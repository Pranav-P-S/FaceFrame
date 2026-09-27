# Changelog

## Unreleased

### Added
- Albums whose cover photo is trashed, deleted or missing now serve the
  album's first live item as the cover (albums without an explicit cover do
  the same) instead of a dead gray tile. The stored `cover_hash` is
  untouched — restoring the original photo brings the user's choice back.
  Covered end-to-end, along with the ghost-trash behavior below.
- Pet search without a rescan: free-text `dog`, `cat` (plus `puppy`,
  `kitten` and plurals) expand at query time to the ImageNet breed class
  names the labeler stores ("golden retriever", "Egyptian cat", …).
  Expansion reads the already-downloaded classes file and never blocks on
  the network.

### Changed
- "Locked folder" is now the **Hidden folder** — the old name implied an
  encryption the feature never provided. Same passcode gate (PBKDF2, salted,
  constant-time), honest name; the unlock screen, Settings and this README
  all state that files stay unencrypted on disk. Backend action names and
  the `#/locked` route are unchanged.
- Setup collapses to one command: `npm run setup` finds a suitable Python,
  creates the venv and installs the backend deps, cross-platform. The
  in-app "Python was not found" banner now points at it.

### Fixed
- Full codebase audit (review/CODE_AUDIT.md) — all 16 P1 findings fixed and
  suites-verified:
  - `set_person_thumbnail` crashed on every call (`os` was never imported);
    the path is now contained for relative values too.
  - `restore_index`'s busy-check + index swap are atomic under the lifecycle
    lock (a scan could previously start mid-swap and write into the orphaned
    old index); the pool epoch bump happens inside the pool lock.
  - `export_items` refuses destinations inside the library, and `export_baked`
    no longer overwrites unrelated files: an existing file is kept only when
    byte-identical (sha256), anything else bumps to "name (1).ext".
  - `place:` search never matched anything — the LIKE pattern lacked the
    space `json.dumps` writes after the colon.
  - Locked photos no longer leak through People pages (`person_photos`,
    `person_faces`, `unclustered_faces` now enforce the hidden-folder gate).
  - Re-clustering no longer deletes user-named persons: faces of custom-named
    persons survive a noise round (single-face persons always landed in noise
    at MIN_SAMPLES=2 and were silently destroyed).
  - Straighten over-cropped non-square photos by up to ~40% (swapped cover-
    zoom denominators in the rotation math).
  - One poison video with NaN stream properties aborted every future scan
    pass at the same file; properties are sanitized and per-file processing
    failures are contained (counted as `failed`, progress still completes).
  - The viewer was unreachable by mouse from search/album/person/archive
    grids (select-mode was route-forced); plain click opens there now, with
    shift-click selecting as on the feed.
  - Editor aspect presets computed the reciprocal crop at 90°/270° rotation
    ("1:1" on a rotated photo saved a 9:16 crop).
  - Editor saves now refresh already-mounted grid tiles (the image-cache
    version is subscribed by Thumb/useImage instead of only clearing caches).
  - Panoramas no longer overflow their grid row (the flush pass used the
    unclamped aspect while the row sum used the clamped one).
  - The backend child process has an `error` listener: a spawn failure can no
    longer crash the whole app or wedge the restart supervisor (taskkill too).
  - Holding Delete in the viewer no longer trashes a run of photos (key-
    repeat guarded; arrows still repeat), and modifier chords are ignored.
  - Person/album detail pages show a not-found state on stale or malformed
    ids (back-after-merge was an in-app trigger) instead of spinning forever,
    and person state no longer leaks across ids via back/forward.
- The browser-dev mock answers `set_album_sort`, the one action the UI could
  call that the mock still faked with a silent `ok()`.
- Two regressions from the performance pass: the trash index is swapped for
  its partial replacement exactly once (keyed on the stored definition)
  instead of being rebuilt on every open, and scan progress reports
  "hashing" while files are actually hashing (plus a `failed` bucket so the
  progress bar completes when files are left for a later pass).
- The watcher thread no longer holds a pooled index.db handle at rest
  (defeating release-at-rest and silently blocking clear/restore on Windows);
  the scan thread releases its context-store handle too, and a non-dict
  NDJSON request answers an error instead of killing the backend process.

### Verified already-correct
- Label-model downloads already write to `*.part` and atomically rename
  after SHA-256 verification — a killed daemon thread cannot leave a
  corrupt ONNX.
- `empty_trash` already clears files that were deleted outside the app
  (missing file → no recycle-bin call → DB row removed). Now proven by an
  e2e check so it stays that way.

### Performance
- **Backend**: one pooled SQLite connection per worker thread instead of
  open+PRAGMA+close on every call (the pytest suite alone dropped ~24s →
  ~12s). The command thread releases its connection after every request, so
  the library folder carries no open handle at rest and Explorer
  renames/moves keep working; scan/cluster/watch jobs keep theirs for the
  job and release it at exit, and `clear_index`/`restore_index` close all
  handles before replacing the index file (Windows).
- **Feed/search**: the sort key is materialized in `files.sort_time`
  (maintained by `upsert_file` and date-override writes, backfilled on
  upgrade) with a partial index matching the live-rows filter — the feed
  query walks the index in order instead of temp-B-tree sorting the whole
  library on every load (measured 38 ms → 28 ms at 20k files including
  Python grouping, and the SQL side no longer scales a sort with library
  size). `album_items(content_hash)` indexed: `item_detail` joins and
  media-delete cascades stop being full scans.
- **Scan**: hashing runs on a 4-thread pool kept 8 files ahead of the
  decode/inference loop, so sha256 + disk reads overlap the AI engine
  instead of serializing behind it. Stats, progress events, abort checks
  and the per-file state machine are unchanged (still single-threaded by
  design — see D4).
- **Electron**: backend NDJSON parsed with `readline` instead of manual
  `Buffer.concat` per chunk (quadratic copying on multi-MB scan/feed
  responses); `media://` handler is async (`fs.promises.stat`) with
  pre-normalized cached library roots instead of per-request
  normalize+lowercase over every root; media-root persistence and the venv
  probe are async too.
- **UI**: the Viewer is permanently mounted and self-subscribing — swiping
  to the next photo re-renders only the viewer, not the whole app with the
  feed grid behind it. Grid tiles are memoized with per-path selection
  subscriptions and stable per-index callbacks: a selection click
  re-renders only the flipped tiles, and scrolling no longer re-renders
  mounted tiles (the redundant per-tile IntersectionObserver is gone —
  virtualization already buffers 800 px). Vendor JS split into its own
  chunk (app chunk 280 KB → 87 KB).

### Rejected (measured, not vibes)
- orjson over stdlib `json.dumps`: a 4000-item feed payload serializes in
  ~6 ms with the C-accelerated stdlib — not worth a compiled dependency.
- SWC for Babel: full build is ~0.9 s and `tsc` dominates typechecking.
- cv2.imread over PIL: PIL's decompression-bomb guard is a documented
  contract (undecodable flag), and the GIL argument doesn't apply to the
  prefetch design.
- Explicit ONNX `SessionOptions`: InsightFace constructs its own sessions,
  and ORT already defaults intra-op threads to the physical core count.
- Per-library scan/cluster locks: the UI binds one library at a time, and
  all libraries share one preloaded ONNX engine (D4) — a TaskManager keyed
  by root would add races for zero user-facing parallelism. Revisit only
  with a multi-library UI.
- Incremental `item_added`/`item_removed` feed patching: a full feed refetch
  on the rare `scan_complete` costs tens of milliseconds (index-driven query
  + 0.8 MB JSON); maintaining justified-layout invariants under granular
  patches would fork the data flow used by every other mutation.
- Tightening the date-override bound to 8.64e10: JS `Date` spans ±8.64e15
  **ms** = ±8.64e12 **s** — the existing Python bound (3.5e11 s ≈ year
  12000) is already inside it. The claim's arithmetic was off by 100×.

## 1.0.1 — the pass nobody asked for (so we ran it)

A full double-check of every layer found real holes: features that were
implemented in the backend and promised in the docs but never reachable from
the UI, plus a handful of genuine bugs the earlier rounds missed.

### Fixed
- The Archive page listed every *unarchived* photo: the `archived_only`
  filter never made it from the request into the feed query.
- Face thumbnails could never render — the only image channel refused paths
  without a file row, which thumbnails never have. People pages were blank.
- `on:2024-06-01` in search was parsed and then silently ignored.
- A malicious index backup could write files outside the library on restore
  (zip-slip), and the docstring claimed a containment check that didn't exist.
- The watcher pass toasted "Indexed undefined file(s)" — it re-emitted
  scan_complete after the pipeline had already sent it.
- Emptying the backup snapshot could fail on Windows with the database
  connection still open; merge could delete a face crop another face still
  referenced; a bad settings key half-applied the rest; deleted files kept
  stale full-text rows; photos past PIL's decompression-bomb limit retried
  on every scan forever.
- Electron: a multi-byte character split across a stdout chunk boundary came
  through corrupted; suffix HTTP ranges (`bytes=-N`) served the wrong bytes;
  error toasts blamed "still starting" on a backend that had died for good.
- The viewer's info panel could copy the previous photo's caption and date
  onto the next one. Trash/archive/favorite now refresh the grid, selection
  is dropped when the page changes, and eraser marks are drawn on the
  original image — the same space the backend inpaints in.
- `window.prompt` throws in Electron, which quietly killed add-to-album,
  export and index backup in the real app. All replaced with an in-app
  dialog.

### Added
- People: merge, split, hide/show, manual face assignment and feature-photo
  choice — the backend always supported all of it, the UI never did.
- Albums: rename, delete, set cover from a selection, and manual ordering.
- Collage and animation buttons in the selection bar; delete creations from
  the viewer; restore index from a backup in Utilities; remove the locked
  folder passcode in Settings.

### Changed
- The browser-dev mock implements every action the UI can call instead of
  silently answering `ok()`.
- `npm run test:py` works on all platforms; the stress harness refuses a
  library mutated by a previous run and its expectations account for
  duplicate twins; installers ship a real app icon.

## 1.0.0 — the Google Photos pivot

FaceFrame grows from a face browser into a complete local photo manager.

### Added
- Content-addressed index (schema v3) with automatic migration from v0.2:
  faces, people and embeddings survive the upgrade without re-inference.
- Day/month/year grouped, virtualized, justified photo feed with a density
  slider, memories carousel, and multi-select bulk actions.
- Universal viewer: zoom, video streaming (media:// with range support),
  EXIF info panel, editable captions and dates, face chips, undo toasts.
- Non-destructive editor: crop/rotate/flip/straighten, 14 filter presets,
  light adjustments, magic eraser (inpainting); originals never modified.
- People management: clustering on content-keyed faces, rename, merge,
  split, manual assignment, hide, feature-photo choice.
- Search: text (captions/filenames/labels via FTS) + structured filters
  (person, place, type, folder, dates) with filter chips.
- Albums with covers, sorting and reorder; collages and animations.
- Places (geohash clustering, optional cached reverse geocoding), memories
  (on this day, trips, highlights) with a story player.
- Favorites, archive, trash with 60-day retention ending in the OS Recycle
  Bin, passcode-gated locked folder.
- Duplicates review (exact matches via sha256; perceptual dHashes are stored
  as the foundation for near-duplicate/burst grouping), missing-file
  tracking with automatic relink, storage stats, cache cleanup.
- Watch mode for automatic re-scans; light/dark themes; keyboard shortcuts;
  deterministic mock backend for browser-only development (`?mock=1`).

### Changed
- Scans are idempotent by construction: unchanged content costs nothing,
  moves/reimages cost a rehash, and user state survives rescans.
- All deletes go to the OS Recycle Bin; only indexed, trashed files are
  deletable from disk.
- Heavy model chain is imported and constructed on the backend's main thread
  (worker-thread ONNX loading deadlocks on Windows).

### Removed
- The v0.2 path-addressed scanner/database/clusterer modules (superseded by
  the v3 pipeline and people service).
