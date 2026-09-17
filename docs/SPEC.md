# Spec: FaceFrame v3 — a local Google Photos

## Objective

Turn FaceFrame from a face-browsing tool into a complete, local, offline-first
photo manager with Google Photos' feature set and UX (see
`docs/GOOGLE_PHOTOS_ANALYSIS.md`): a time-first infinite feed, a universal
viewer, a non-destructive editor, search across people/places/things, albums,
memories, favorites/archive/trash/locked-folder, duplicates and integrity
tooling, videos, and watch-mode library sync. Zero cloud dependencies; photo
files are **never mutated** — all state lives in the library's `.faceframe/`
folder.

The user is asleep: **decisions default to maximum feature coverage** where
feasible, per explicit instruction. Gated reviews execute as documented
self-reviews plus dispatched critic agents.

## Capability map

| Module id | Responsibility | Depends on |
|---|---|---|
| `index-core` | Schema v3, migrations, content hashing, media/file tables, GC | — |
| `scan-pipeline` | Discovery, hashing, processing (faces/exif/labels/phash/posters), jobs+events, missing-file handling | index-core |
| `library-state` | Favorites, archive, trash (+purge), locked folder, captions, date overrides, type flags | index-core |
| `collections` | Albums CRUD+ordering, duplicates utility, creations (collage/animation/export) | index-core, library-state |
| `people` | Clustering, naming, merge/split/assign/hide, person views | index-core |
| `search-views` | Query parser, search executor, feed queries (day/month/year), places (geohash+optional geocoding), memories builders | index-core, library-state, people |
| `render-pipeline` | Thumbnail/preview generation with stored transforms, editor params, magic eraser, video posters | index-core |
| `app-shell` | Protocol dispatch (command registry), settings, storage stats | all |
| `electron-host` | media:// streaming, IPC surface, backend lifecycle | app-shell |
| `web-ui` | GP-style React app: shell, feed, viewer, editor, all pages, theming, a11y, shortcuts | electron-host |

Build order: index-core → scan-pipeline → library-state → collections → people
→ search-views → render-pipeline → app-shell → electron-host → web-ui.
(people and collections can interleave; web-ui slices vertical through
everything.)

## Tech stack

- **Backend**: Python 3.11, SQLite (WAL), OpenCV, Pillow, numpy, scikit-learn
  (DBSCAN), InsightFace buffalo_l (existing), onnxruntime; optional
  MobileNetV2 ONNX for labels; send2trash for OS-trash deletes.
- **Host**: Electron 39, custom `app://` (bundle) + `media://` (range-streamed
  originals) protocols, Python subprocess over NDJSON.
- **Frontend**: React 19 + TypeScript + Vite 7, zustand (store), Leaflet
  (map, npm-bundled), no UI framework — hand-built GP-style CSS with light/dark
  variables.
- **Tests**: pytest (backend units), extended NDJSON e2e suite, vitest
  (frontend pure logic: query parser, justified layout), tsc strict.

## Schema v3 (one per library, in `.faceframe/index.db`)

```
meta(key PK, value)                          -- schema_version, settings, lock hash
files(                                       -- one row per real file on disk
  path PK,                -- library-relative posix
  content_hash,           -- sha256 hex, indexed
  kind,                   -- photo|video|creation
  size, mtime,            -- stat mirror (display + change hints)
  missing INTEGER DEFAULT 0, last_seen REAL,
  added_at REAL,          -- when indexed (Recently added)
  trashed_at REAL,        -- NULL = not trashed
  archived INTEGER DEFAULT 0,
  FOREIGN KEY(content_hash) REFERENCES media(content_hash))
media(                                       -- one row per unique content
  content_hash PK,
  kind,                     -- photo|video
  width, height, duration REAL,
  capture_time REAL,        -- EXIF epoch; mtime fallback
  tz_offset TEXT,           -- EXIF offset "+02:00" or NULL
  exif TEXT,                -- JSON {camera, lens, iso, fnum, exposure, fl, gps[]}
  phash TEXT,               -- 64-bit hex perceptual hash (photos)
  labels TEXT,              -- JSON ["dog", "grass", ...]
  caption TEXT,
  edit TEXT,                -- JSON edit params (crop/adjust/filter/eraser)
  date_override REAL,       -- user-corrected capture time
  favorite INTEGER DEFAULT 0,
  locked INTEGER DEFAULT 0,
  poster_path,              -- relative cache path (video poster / photo thumb)
  analysis_state,           -- none|hashed|analyzed (job resume)
  analyzed_at REAL)
faces(                                       -- keyed by content now
  id PK, content_hash REFERENCES media, face_index INTEGER,
  bbox TEXT, embedding TEXT, det_score REAL,
  thumbnail_path TEXT, person_id REFERENCES persons,
  hidden INTEGER DEFAULT 0,
  UNIQUE(content_hash, face_index))
persons(id PK, name, thumbnail_path, hidden DEFAULT 0, created_at)
albums(id PK, name, description, cover_hash, sort_key, created_at)
album_items(album_id, content_hash, position, added_at,
  UNIQUE(album_id, content_hash))
places(content_hash PK, geohash TEXT, lat REAL, lon REAL)
  -- folded into media.exif too; places table only for geocoded names
geonames(geohash PK, name TEXT, fetched_at REAL)   -- reverse-geocode cache
creations(id PK, kind, path, content_hash, created_at, params TEXT)
jobs(id PK, kind, state, stats TEXT, created_at, updated_at)
FTS: media_fts(content_hash UNINDEXED, caption, labels, path-ish via files join)
```

Invariants (the anti-double-count/count/compute contract):
1. `media` rows are created once per unique content hash — all expensive
   derived data hangs off it.
2. A file row is written **only after** its media row is committed (write
   order makes any interruption self-healing).
3. Every item query anchors on `files` (path-existence) with EXISTS flags —
   an item appears at most once per view.
4. Cache filenames are content-derived; a GC sweep removes cache files with no
   index row. Re-running GC is a no-op (idempotent).
5. Re-scan of an unchanged library performs **zero decodes** (hash check by
   size+mtime first, verified hash only on mismatch).
6. User state (favorite/trash/album/…) lives on `media`/`files` rows only —
   never on disk; "Revert" is deleting JSON.

## Commands (the full executable surface)

```
Backend tests (units):  venv/Scripts/python -m pytest python-backend/tests -q
Backend tests (e2e):    npm test                       # scripts/run-backend-e2e.mjs
Frontend typecheck:     npm run build                  # tsc && vite build
Frontend unit tests:    npm run test:unit              # vitest run
Dev:                    npm run electron:dev
Package:                npm run electron:build
```

## Project structure (additions)

```
python-backend/
  main.py            # slim loop + command registry
  actions_*.py       # feature modules registering commands
  schema.py          # v3 schema + migration chain
  hashing.py         # content/perceptual hashing
  exif.py            # capture-time + EXIF extraction
  labels.py          # optional MobileNet labeling
  geoutil.py         # geohash + trip clustering
  memories.py        # memory builders
  render.py          # preview/thumb pipeline + transforms + eraser
  creations.py       # collage/animation/export
  watcher.py         # watch mode
tests/               # pytest units
src/
  app/               # store, router, theme, shortcuts
  components/...     # shell, feed, viewer, editor, pages
  lib/               # api, query, layout, format
  mock/              # browser-dev fake backend (also feeds visual review)
tasks/               # plan.md, todo.md
docs/                # analysis, critique, spec, decisions
```

## Code style

Match the existing repo: 4-space Python, double quotes, module docstrings
explaining *why*; TypeScript strict, function components + hooks, no default
exports for pages, CSS in App.css (GP-palette variables). Comments only for
constraints the code cannot express.

## Testing strategy

- **Units (fast, every logic module)**: pytest with tmp-path libraries built
  by fixture generators (tiny synthetic JPEGs). Red-green-refactor per module.
- **e2e (acceptance)**: the NDJSON suite extended to assert the mandate: second
  scan processes 0 files; moved file keeps person + costs 0 decodes; duplicate
  detection; trash→restore; album→search→edit round-trips.
- **Frontend**: vitest for query parser + justified-layout + date grouping;
  tsc strict as the static gate; visual review via judge on rendered screens.
- Coverage bar: every new backend module has a test file; every success
  criterion in the analysis doc has an owning test.

## Boundaries

- **Always**: prove with a failing test first; keep `npm test` green; commit
  per vertical slice; never mutate user photo files; keep schema writes
  transactional.
- **Ask first** *(moot — user asleep; defaults chosen and documented)*: new
  dependencies (chosen: zustand, leaflet, vitest, send2trash — justified in
  plan), schema changes beyond v3 (documented in decisions).
- **Never**: hard-delete user files (OS trash only, behind confirm); weaken
  tests to pass; put user state on disk; ship features not in this spec
  without recording the decision.

## Success criteria

Tracked as checklist in `tasks/plan.md`; the binding list is §4 of
`docs/GOOGLE_PHOTOS_ANALYSIS.md` plus the e2e idempotency proofs.

## Open questions

None blocking — user delegated all decisions ("choose the most features in
case of doubt"). Deferred items recorded: RAW decode, video trim, movies,
LAN sharing, portrait blur (documented in analysis §2 as Out/Deferred).
