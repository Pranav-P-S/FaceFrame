# Architecture Critique — FaceFrame v0.2 (pre-clone)

> The user-mandated review axis: **robustness**, **ability to handle change**,
> and **prevention of double counting / double computation**. Each finding cites
> the code it applies to and the fix the v3 design adopts.

## 1. Double computation — the index is *path-addressed*, not *content-addressed*

This is the root finding; most others descend from it.

**F1. Identity = `(rel_path, mtime, size)`.** `database.py:52-57` keys the
`files` table on path and `scanner.py:66-69` uses `(mtime, size)` as the change
signal. Consequences:

- **Moving or renaming a file is indistinguishable from delete + create.** The
  old row is pruned (faces, thumbnails discarded) and the file is fully
  re-decoded and re-embedded. For a 50k library being reorganized, that is a
  full-face-inference redo of every moved file.
- **Face→person assignments die on move.** After a rename, every face of every
  moved photo becomes unclustered again until the user re-runs clustering.
- **mtime is a weak change signal.** A copy operation with `cp -p`/robocopy
  `/copyall` preserves mtime, so an edited file can be skipped (stale index);
  conversely any touch of the file forces a full redo.

**F2. Per-path thumbnails.** `processor.py:118-124` names face thumbnails by
`sha1(abspath#idx)`. Any path change orphans every thumbnail file forever (no
GC exists), and moved files get *duplicate* thumbnail bytes stored under new
names.

**F3. Per-(path, mtime, size) previews with no lifecycle.**
`processor.py:179-205` caches previews keyed by digest of
`(abspath, mtime, size, max_dim)`. Correct for invalidation — but nothing ever
deletes superseded entries: every in-place edit leaves the old preview on disk
permanently. The cache grows without bound across the library's lifetime; same
for orphaned thumbnails above. On a 200k-photo library this becomes tens of GB
of dead cache.

**F4. Content-identical copies are computed N times.** Duplicate copies of a
photo (the most common form of library bloat) are each decoded, embedded, and
thumbnailed independently. Nothing notices they are the same bytes.

**Fix (adopted in v3):** a two-layer index —
`files(path → content_hash, size, mtime)` and `media(content_hash → exif,
faces, labels, phash, user state)`. The content hash is the unit of expensive
work: decode/embed/thumbnail/label happen **once per content**, ever. Path
changes are a hash hit (zero compute, state preserved). Duplicate detection is
a `GROUP BY content_hash`. Media caches are named by content hash (no digest
drift), and a GC sweep deletes any cache file whose hash is no longer indexed.

## 2. Robustness

**F5. Schema versioning is decorative.** `database.py:9,80-87` writes
`schema_version = 1` and never reads it for migration. Any future column is a
breaking change for existing libraries. **Fix:** real migration chain
(v1/v2 → v3) that re-links existing faces by content hash and preserves
persons/embeddings without re-running inference.

**F6. Unbounded event stream.** `main.py:114-122` emits one
`scan_progress` JSON line per file. At 100k files that is 100k messages through
a JSON pipe + Electron broadcast + React setState each — the UI spends its time
re-rendering a counter. **Fix:** coalesced progress events (≥250 ms interval or
1% change), carried in a job object.

**F7. Scan and model loading share one global gate.** `main.py:172-186` permits
a single busy worker for *everything*: a scan blocks clustering blocks
previews… except previews, which run on their own pool but are refused while
`locate_library` says the library is fine. Reads are fine, but two libraries
cannot be scanned sequentially without user-visible failure states, and any
crash inside a thread leaves `_scan_thread` set until the `finally` runs (OK)
— but a hard kill (power loss) mid-scan leaves **no job record**; the next
launch cannot know a scan was interrupted (acceptable for v2, not for a
library app). **Fix:** jobs table; crash-interrupted jobs are detected and
self-heal on next scan (their partially-written rows are simply superseded
because files are keyed by content+path and writes are transactional).

**F8. Deleted-file pruning can misfire on flaky mounts.**
`scanner.py:148-158` deletes index rows for any path whose `isfile()` is false.
A network drive that drops for a second wipes assignments for the whole
library. **Fix:** v3 marks files *missing* (with last-seen timestamp) instead
of pruning; a "Missing files" utility resolves them (file came back → relink
by hash, truly gone → remove). Prune-by-silence is never automatic.

**F9. No fsync / checkpoint strategy.** WAL mode is set per connection
(`database.py:35`) — good — but a power cut mid-scan leaves a DB with no
record of what completed. Because v3 writes file rows only after media data
(committed atomically with the faces), interruption is always recoverable:
unchanged files are re-derived from `files` on the next pass. Documented
invariant: **the index may lag reality, never contradict it.**

**F10. `clear_index` and cluster are mutually exclusive with scan but not with
each other across processes.** Two FaceFrame instances on the same library
(single-instance lock exists in Electron — `main.cjs:467` — but the Python
backend is a public process). Acceptable: SQLite's busy_timeout + WAL cover
concurrent readers; writers are app-serialized. Documented.

**F11. Preview base64 over IPC.** Every grid tile ships as a base64 data URL
through IPC (`main.cjs:403-416`). At 640 px this is fine for a virtualized
grid (≈30–60 KB/tile) and avoids all URL-encoding pitfalls (a deliberate v0.2
design note that remains correct). Kept, with smaller square thumbnails
(≈384 px cover-crop) to cut payload ~4×, plus renderer LRU + in-flight
dedupe (already present in `src/images.ts`).

**F12. Videos absent.** The scan only accepts images (`scanner.py:12`); a
photo library in 2026 contains videos. The viewer/grid silently pretend they
don't exist. **Fix:** video rows (poster frame via `cv2.VideoCapture`,
duration, playback via a range-capable `media://` protocol in Electron).

## 3. Ability to handle change (the "handle change" axis)

**F13. Domain logic has no seams.** `main.py` contains orchestration + HTTP-ish
dispatch + thumbnail plumbing in one 385-line file; adding albums/search/editor
means touching the dispatcher for every feature forever. **Fix:** command
registry — each feature module registers its actions (`@action("album_create")`),
main.py is a dumb loop. New features cannot grow the dispatcher.

**F14. The frontend has no routing and one 529-line App component holding all
state (`src/App.tsx`).** Ten more screens would be unmanageable. **Fix:** hash
router + a small store (zustand); pages own their slices.

**F15. Data model can't express anything but faces.** No captions, no albums,
no user state, no date overrides, no type flags. Adding columns ad hoc is how
schemas rot (see F5). **Fix:** v3 schema designed for the full feature matrix
up front (docs/SPEC.md §schema), with the migration chain established *before*
the features land.

**F16. The e2e suite is the only test layer.** `scripts/e2e_backend_test.py`
covers the command surface end-to-end but is slow and coarse; refactors have no
fast safety net. **Fix:** pytest unit layer for every pure/logic module
(query parser, migration, hashing pipeline, geohash, memories, layout math) +
the e2e suite kept as the acceptance gate, extended for idempotency proofs.

**F17. Time handling is mtime-only.** Photo managers live and die by capture
time (EXIF DateTimeOriginal with timezone offset). v2 has none of it; sorting
by mtime reorders a library after any careless copy. **Fix:** capture-time
chain: EXIF → (videos: container/creation hints unavailable →) mtime; user
date overrides stored per media and never written into files.

**F18. State that must survive restart lives in `localStorage` only**
(`App.tsx:37`): the chosen folder. Per-library settings (density, watch mode,
clustering) belong in the library's `meta` table so they travel with the
`.faceframe` folder; app-level prefs (theme) may stay in localStorage. v3
splits them explicitly.

## 4. Double *counting*

**F19. `persons_with_counts` (LEFT JOIN + HAVING) and `person_photos`
(GROUP BY file_path) are correct** — no inflation found. But the *grid* the
clone needs (all items, grouped by day) has no query at all, and naive joins
of `files × faces × albums` would multiply rows; v3 keeps item queries anchored
on `files` with EXISTS subqueries for flags, so an item appears exactly once
per view regardless of how many faces/albums/labels reference it. Duplicates
(surface-identical items) are *by design* shown once per path, with duplicate
groups surfaced in Utilities instead of silently hidden — GP hides exact
duplicates; locally the user owns the disk, so we surface rather than decide.

## 5. What survives review as-is

Credit where due — these v0.2 decisions are correct and carry forward:

- NDJSON stdin/stdout protocol with per-action timeouts and crash-restart
  backoff in Electron (`main.cjs:220-248`).
- Library-relative POSIX paths (`pathio.py`) — libraries survive moves between
  machines/folders.
- Per-library self-contained `.faceframe/` (index + caches): delete one folder
  to erase every trace. (Trivially compatible with the new schema.)
- WAL + short-lived connections (Windows file-handle constraints).
- The preview-first memory bound (no full-res image ever crosses IPC) and the
  EXIF-orientation-honoring decoder (`processor.py:152-166`).
- Cancel events checked at every file boundary; scan writes ordered so a kill
  mid-file self-heals (`scanner.py:103-115`).
- Stable person identity across re-clusters (`clusterer.py:64-107`).
