# Changelog

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
- Duplicates review (exact via sha256; near/burst via dHash), missing-file
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
