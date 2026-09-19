# Changelog

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
