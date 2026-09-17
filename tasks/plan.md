# Plan — FaceFrame v3 (local Google Photos)

Vertical slices in dependency order. Each slice ends green: units + e2e +
typecheck where applicable, then a commit.

## Slice 1 — index-core (schema v3 + hashing + migration)
- schema.py: v3 DDL, meta helpers, migration chain v1/v2→v3 (re-link faces by
  content hash, preserve persons/embeddings).
- hashing.py: sha256 file hash; 64-bit dHash.
- Tests: fresh v3 library; v1 library migrates (persons/names survive,
  embeddings reused, zero re-encode); hash stability; phash Hamming.

## Slice 2 — scan-pipeline (jobs, events, videos, exif, missing files)
- exif.py: capture time (DateTimeOriginal/offset), camera info, GPS.
- labels.py: optional MobileNet (download → cache → top-k labels); stub-safe.
- scanner rewrite: discover → change-detect → hash → process → commit;
  videos (poster via cv2.VideoCapture); missing-file marking; coalesced
  progress; cancellable; GC sweep at end; creations/ never indexed.
- Tests: idempotent second scan (0 processed); move file (0 decode, person
  kept); video row + poster; missing marking + relink; GC removes orphans.

## Slice 3 — library-state + collections
- favorites/archive/trash/locked (PBKDF2 gate), captions, date overrides,
  albums CRUD + order, duplicates groups, collage/animation/export
  (creations.py), trash purge via send2trash.
- Tests: state transitions visible in feed queries; album reorder stable;
  duplicates grouping; purge to trash; creation files indexed and deletable.

## Slice 4 — people
- port clusterer onto faces-by-hash; split person, assign faces, hide person,
  feature-photo choice; unclustered strip queries.
- Tests: split/merge/assign round-trips; stable ids across re-cluster.

## Slice 5 — search-views
- query.py: token parser (text, person:, place:, type:, before/after/on:,
  folder:, is:favorite/archived/…); feed queries day/month/year; places
  geohash clusters + optional cached reverse geocoding; memories builders
  (on-this-day, trips, highlights).
- Tests: parser round-trips; feed grouping correctness (timezone!); geohash
  clustering; memory builders on synthetic libraries.

## Slice 6 — render-pipeline + app-shell + electron-host
- render.py: square thumbs (cover), preview with edit transforms applied
  (crop/rotate/flip/adjust/filter/eraser), LRU-safe cache naming by
  (hash, transform digest, size), magic-eraser action.
- main.py: slim dispatcher + registry; new actions (≈60); settings + storage
  stats; watcher.py (interval rescan).
- electron: media:// range protocol (allowlist roots), IPC passthrough for all
  new actions.
- Tests: transform determinism, cache hit/miss, eraser, registry dispatch,
  media protocol range requests (node-side test via e2e harness), watcher
  picks up an added file.

## Slice 7 — web-ui
- Shell (rail, topbar, theme), virtualized justified feed w/ day headers +
  density slider + months/years, selection mode + bulk bar, universal viewer
  (+info, faces strip, video), editor (crop/adjust/filters/eraser), albums,
  people, search page + chips, places (Leaflet w/ offline fallback), memories
  player, library pages (trash/archive/locked/utilities/settings), toasts w/
  undo, shortcuts overlay, mock backend for browser dev/visual review.
- Tests: vitest for query parser mirror, layout math, date grouping; tsc
  strict green.

## Slice 8 — acceptance + critics + ship
- Full e2e suite incl. idempotency proofs; browser-driven smoke of the UI
  (mock + live backend); two critic subagents (correctness/architecture;
  security/performance) → fix Criticals; visual judge on rendered screens →
  fix; README rewrite; changelog; version 1.0.0; final commits.
