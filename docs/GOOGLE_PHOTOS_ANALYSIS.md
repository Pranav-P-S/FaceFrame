# Google Photos — Comprehensive Feature & UX Analysis

> Purpose: the complete feature inventory and UX model of Google Photos (as of
> 2025–2026), analyzed for a **fully local** clone. Every feature is mapped to a
> local-feasibility decision. This document is the source for the capability map
> in `SPEC.md`.

## 1. Product model

Google Photos is **not** a file browser. Its core mental model:

1. **Time is the primary axis.** The home screen is an infinite reverse-chronological
   feed of *everything*, grouped by day. There are no folders-first views.
2. **Everything else is a projection.** People, places, things, albums, search
   results are all *views over the same item stream*, never separate libraries.
3. **The viewer is the hub.** From any view you tap an item and enter one shared
   full-screen viewer; all item actions (favorite, edit, info, delete, archive,
   add to album) live there.
4. **Zero-cost capture → zero-effort recall.** Auto-backup, auto-organization
   (people/places/things), and auto-creations (memories, collages) mean the user
   never organizes manually unless they want to.
5. **Nothing is ever lost silently.** Trash (60-day grace), archive (hidden but
   searchable), "remove from album" (≠ delete), edits that keep originals
   ("Revert to original"), stacking (bursts collapsed, not hidden).

A local clone drops the network half of #4 (backup/sharing) and replaces it
with **watch folders + on-device intelligence**. Everything else carries over.

## 2. Feature inventory

Legend: **Local** = fully feasible offline in this repo. **Adapted** = feasible
with a changed mechanism. **Out** = dropped (cloud/AI-service dependent) or
deferred (cost), with the reason.

### 2.1 Library / main feed

| # | Feature | Verdict | Local mechanism |
|---|---------|---------|-----------------|
| 1 | Reverse-chronological grid, day-grouped ("Today", "Yesterday", dates) | Local | SQL over capture date (EXIF DateTimeOriginal → fallback file mtime), day headers |
| 2 | Infinite scroll, virtualized | Local | Windowed justified-row layout, only visible rows rendered |
| 3 | Grid density zoom (4 levels, pinch/slider) | Local | Row-height slider; re-justifies rows |
| 4 | Months view (compact month grids) | Local | Second layout mode grouped by month |
| 5 | Years view (tiny thumbnails per year) | Local | Third layout mode |
| 6 | Multi-select (click, drag, shift-range, select-all-in-view) | Local | Selection mode with top action bar |
| 7 | Item count chip + bulk actions | Local | Bulk favorite/archive/trash/album |
| 8 | Recently added | Local | Sort by indexed_at |
| 9 | Videos inline in the feed with duration badge & poster | Local | cv2 grabs a poster frame at scan; playback via streaming protocol |
| 10 | Motion photos / Live Photos (jpg+mp4 pair) | Adapted | Detect adjacent `.mp4` with matching basename; "play" button plays the video |
| 11 | Panoramas displayed full-width | Local | aspect-ratio-aware layout |
| 12 | Screenshots / selfies auto-identified | Adapted | Heuristics: filename (`screenshot*`, `screen_shot*`), front-camera EXIF, portrait aspect + single face |
| 13 | RAW support | Out | No offline RAW decoder bundled (rawpy too heavy / platform wheels); JPEG/PNG/WebP/BMP/TIFF + common videos only. Documented limitation. |
| 14 | Stacking of bursts | Adapted | Same-second + perceptual-hash-similar groups; stack can be expanded |
| 15 | Jump-to-date / scrubber | Local | "Jump to date" input in the feed |

### 2.2 Viewer

| # | Feature | Verdict | Local mechanism |
|---|---------|---------|-----------------|
| 16 | Full-screen viewer, arrow/swipe navigation | Local | Existing viewer, rebuilt to GP layout |
| 17 | Pinch / double-tap / wheel zoom, pan | Local | Canvas zoom transform |
| 18 | Info panel: EXIF (camera, lens, settings, size, resolution), file info | Local | EXIF parsed at scan (Pillow), stored in index |
| 19 | Editable description/caption | Local | Stored in index (not written into user files) |
| 20 | Editable date & time | Adapted | Date override stored in index; original EXIF untouched, reversible |
| 21 | Location line + mini-map in info panel | Adapted | GPS coords from EXIF; map via Leaflet when online, coordinate text offline |
| 22 | Faces on the photo + chips to jump to a person | Local | bboxes already stored; overlay chips |
| 23 | Favorite / archive / trash / add-to-album from viewer | Local | Index mutations |
| 24 | Slideshow mode | Local | Timed advance |
| 25 | Video playback with scrubber, mute, fullscreen | Local | `media://` streaming protocol with HTTP range support in the Electron main process |
| 26 | Delete with undo snackbar | Local | Trash + timed undo toast |
| 27 | "Set as wallpaper" | Out | OS-dependent, low value |
| 28 | Print | Out | Deferred |

### 2.3 Editor (photos)

| # | Feature | Verdict | Local mechanism |
|---|---------|---------|-----------------|
| 29 | Crop with aspect presets, rotate 90°, flip, straighten | Local | Canvas editor; transform JSON stored per item |
| 30 | 12+ filter presets | Local | Parameterized filter chains (PIL/cv2 at render time) |
| 31 | Adjust: brightness, contrast, saturation, warmth, tint, highlights, shadows, vignette, sharpen, grain | Local | Same pipeline |
| 32 | Auto-enhance (one click) | Adapted | Histogram stretch + saturation/sharpen heuristics |
| 33 | Magic Eraser (remove object) | Adapted | User paints a mask → OpenCV `inpaint` (Telea) on the render |
| 34 | Portrait blur / Magic Editor generative AI | Out | Requires segmentation/generative models; documented limitation |
| 35 | Edits never destroy the original; "Revert to original" | Local | Originals untouched; edits are stored transforms; export bakes them |
| 36 | "Save a copy" / export edited | Local | Bake transforms to a new file (into library folder or chosen dir) |
| 37 | Video trim | Out | No guaranteed ffmpeg; cv2 cannot re-mux audio cleanly. Documented limitation. |

### 2.4 Search

| # | Feature | Verdict | Local mechanism |
|---|---------|---------|-----------------|
| 38 | One search box, structured filters | Local | Query parser: free text + filter tokens |
| 39 | People search (named persons) | Local | persons table |
| 40 | Places | Adapted | GPS geohash clusters; optional online reverse-geocoding (cached, toggleable) |
| 41 | Things ("dog", "beach", "food") | Adapted | MobileNetV2 (ONNX, ~14 MB, one-time download like the face models) classifies each photo; top-k labels become searchable "things". Offline-degradable. |
| 42 | Media types (photo/video/selfie/screenshot/panorama) | Local | Type flags from scan |
| 43 | Date filters ("June 2024", years) | Local | SQL date ranges |
| 44 | Folders view | Local | Folder column already stored |
| 45 | Recent searches | Local | localStorage |
| 46 | Natural-language "Ask Photos" (Gemini) | Out | Cloud LLM. Out by definition. |

### 2.5 Albums & creations

| # | Feature | Verdict | Local mechanism |
|---|---------|---------|-----------------|
| 47 | Create album, add/remove items (removal ≠ deletion) | Local | album_items table |
| 48 | Album cover choice, rename, reorder items | Local | cover_media + position column; drag reorder |
| 49 | Sort album by date captured / date added / filename | Local | sort_key column |
| 50 | Auto albums: per-person albums | Local | Derived view over faces |
| 51 | Collages | Local | Template-based composition in PIL; saved into a `creations/` area and indexed |
| 52 | Animations (GIF from a selection) | Local | PIL GIF writer |
| 53 | Movies (auto highlights reel w/ music) | Out | Needs ffmpeg + music licensing; deferred |
| 54 | Memory rewind / year recap video | Out | Same |

### 2.6 People & pets

| # | Feature | Verdict | Local mechanism |
|---|---------|---------|-----------------|
| 55 | Auto face grouping (existing DBSCAN) | Local | Keep, move to automatic pipeline |
| 56 | Name people; search by name | Local | Exists |
| 57 | Merge people | Local | Exists |
| 58 | Split a person (pick faces → new person) | Local | New: move face rows to a new person |
| 59 | Manually add/remove a face to a person | Local | New: per-face assignment UI |
| 60 | Change a person's feature photo | Local | New: pick thumbnail |
| 61 | Hide a person from the people grid | Local | New: hidden flag |
| 62 | Pets | Out | buffalo_l is human-only; documented limitation |
| 63 | Face grouping on/off + re-cluster | Local | Settings toggle |

### 2.7 Memories

| # | Feature | Verdict | Local mechanism |
|---|---------|---------|-----------------|
| 64 | "On this day" / X years ago | Local | Date matching |
| 65 | Trips & places memories | Adapted | Time+geohash clustering heuristics |
| 66 | Recent highlights | Local | Recency + favorites + face presence |
| 67 | Story player (progress bars, tap through) | Local | Fullscreen player component |
| 68 | Set memory music | Out | Licensing; deferred |

### 2.8 Places

| # | Feature | Verdict | Local mechanism |
|---|---------|---------|-----------------|
| 69 | Map with photo pins, cluster counts | Adapted | Leaflet + OSM tiles when online; graceful coordinate-scatter fallback offline |
| 70 | Places grid grouped by location name | Adapted | Geohash clusters; names via optional cached reverse-geocoding |
| 71 | "No location" group | Local | Items without GPS |

### 2.9 Library management (the "Library" tab)

| # | Feature | Verdict | Local mechanism |
|---|---------|---------|-----------------|
| 72 | Trash with 60-day retention, restore, empty | Local | Soft-delete state; auto-purge to OS Recycle Bin after 60 days (send2trash), never `rm` |
| 73 | Delete from device = delete from disk | Local | "Delete from disk" does exactly that (via OS trash) |
| 74 | Archive | Local | archived flag; excluded from feed, kept in search/albums |
| 75 | Locked folder | Adapted | Passcode-gated view (PBKDF2-stored); items hidden from all views while locked. Files stay on disk (not encrypted) — honest UI note. |
| 76 | Favorites | Local | starred flag |
| 77 | Albums list, People list, Archive entry points | Local | Rail navigation |
| 78 | Utilities: duplicates, missing files, storage usage, clear caches | Local | See 2.11 |
| 79 | Import / watch folders | Adapted | Watch mode: background re-scan every N seconds while running (toggle) |
| 80 | Free up space | Out | Meaningless without cloud backup |

### 2.11 Duplicates & integrity (local-specific, user-mandated)

| # | Feature | Verdict | Local mechanism |
|---|---------|---------|-----------------|
| 81 | Exact-duplicate detection (same content, different path/name) | Local | SHA-256 content hash at scan; duplicates listed by hash group |
| 82 | Near-duplicate / burst detection | Local | 64-bit perceptual hash (dHash) + Hamming distance |
| 83 | Review UI: keep one, remove the rest from library/disk | Local | Bulk actions per group |
| 84 | Idempotent indexing — re-scan never recomputes unchanged content | Local | Content-hash addressable index (see ARCHITECTURE_CRITIQUE §4) |
| 85 | Move/rename a file: no recompute, no lost assignments | Local | Faces/labels keyed by content hash, not path |
| 86 | Cache GC: no orphaned thumbnails/previews ever accumulate | Local | Orphan sweep after every scan; creations excluded |

### 2.12 App shell & settings

| # | Feature | Verdict | Local mechanism |
|---|---------|---------|-----------------|
| 87 | Left rail: Photos / Explore / Library (+ Sharing → replaced by Export) | Local | GP layout |
| 88 | Search bar in the top bar | Local | — |
| 89 | Account avatar | Adapted | No accounts; shows app menu (settings, about) |
| 90 | Light/dark theme + system | Local | CSS variables; GP Material-3 palette |
| 91 | Keyboard shortcuts (+ shortcut help overlay) | Local | Full set: arrows, space, f, i, Del, Esc, Ctrl+A, /, g then p/e/l… |
| 92 | Settings: theme, grid density, watch mode, clustering sensitivity, engine (CPU/GPU), geocoding toggle, cache mgmt, about | Local | Settings page persisted per library + app-level |
| 93 | Empty states everywhere | Local | GP-style illustrations/messages |
| 94 | Toasts/snackbars with undo | Local | Existing toast, upgraded with action button |
| 95 | Onboarding (choose library, explain what happens) | Local | Welcome screen, rebuilt |
| 96 | LAN sharing (share an album over HTTP on the local network) | Deferred | Feasible (backend HTTP server) but large; not in v1 scope. Listed as future work. |
| 97 | Backup & sync, shared albums, partner sharing, comments, print fulfillment, photo books | Out | Cloud features by definition — explicitly excluded by the user (local-only, no backup) |

## 3. UX analysis — what makes Google Photos feel the way it feels

### 3.1 Visual system
- **Light-first, low-chrome.** White surfaces, near-black text (#202124),
  gray-600 secondary (#5f6368), one accent (Google Blue #0b57d0). Color comes
  almost exclusively from the photos.
- **Type hierarchy without boxes.** Day headers are small, medium-weight gray
  labels; section titles ("People", "Albums") are 22–24px regular; the app is
  border-free — separators are whitespace, not lines.
- **Density is the user's dial.** The same feed scales from "Comfortable" to
  "Years view" without changing screens — one continuous zoom semantic.
- **Dark mode** mirrors the same palette (surface #131314, cards #1e1f20).

### 3.2 Interaction grammar
- **Progressive disclosure.** Grid → tap → viewer → (i) info / (slider) edit.
  Nothing dense on first sight; power is one tap away.
- **Selection is a mode, not a checkbox grid.** Long-press (or click in select
  mode) flips the top bar into a contextual action bar; the rail hides. Esc or
  ✕ exits.
- **Undo over confirm.** GP rarely asks "are you sure?" — it acts and offers
  "Move to trash — UNDO" for a few seconds. Destructive-beyond-undo actions
  (empty trash, delete from disk) do confirm.
- **Optimistic UI.** Favorite/archive/trash apply instantly; failure rolls back
  with a toast.
- **Consistent item menu.** Every item (grid tile, viewer, album item) has the
  same action set in the same order: Add to album, Archive, Move to trash,
  Favorite, Edit, Download, Info.
- **Keyboard parity.** Full shortcut set; `/` focuses search; `Esc` closes
  layer by layer (viewer → selection → page).
- **Motion.** Grid tiles fade-in on load; viewer opens with a shared-element
  scale transition; toasts slide from the bottom. ~200ms, ease-out. Nothing
  bounces.

### 3.3 Signature patterns to reproduce
1. **Sticky date headers** that collapse into a floating pill when scrolled past.
2. **The density slider** in the top bar of the feed.
3. **The viewer's bottom action row** (favorite · edit · info · add · trash) —
   identical across photo and video.
4. **Faces strip** in the viewer: face chips → tap → that person's page.
5. **Memories carousel** above the feed (rounded-2xl cards, subtle zoom on the
   active memory).
6. **Search filter chips** below the search bar after a query.

### 3.4 Where a local clone should *diverge* from GP
- **No backup/status chips** ("Waiting for Wi-Fi", storage %) — replaced by
  **index status chip** (scan state, items, pending analysis).
- **Delete needs clarity**: locally, "trash" is an index-level state while the
  file stays on disk. The UI must say so. "Delete from disk" is a separate,
  confirm-guarded action (goes to OS Recycle Bin).
- **Locked folder honesty**: files are hidden from FaceFrame's views, not
  encrypted. Stated on the lock screen.
- **First run**: instead of sign-in, the welcome screen explains the local
  model: "Your photos never leave this machine. The index lives in a hidden
  `.faceframe` folder inside your library."

## 4. Success criteria (extracted)

1. Opening the app lands on a day-grouped, virtualized feed of the whole
   library that scrolls smoothly at 50k+ items.
2. Any item can be favorited, archived, trashed (+undo), added to albums,
   captioned, date-corrected, edited (crop/adjust/filter/erase), and all of it
   is reflected consistently in every view — with zero file mutations.
3. Faces are found, named, merged, split, hidden, and searched by name.
4. Search finds items by text (caption/filename/person/place/label), type,
   date, folder — with filter chips.
5. Re-scanning the same library processes 0 files the second time (proven in
   e2e); moving a file costs 0 recompute and keeps its person assignments
   (proven in e2e); exact duplicates are detected and surfaced.
6. Trash restores; 60-day purge goes to the OS Recycle Bin; nothing ever
   hard-deletes user data without an explicit confirm.
7. Light/dark themes, keyboard shortcuts, empty states, undo toasts, settings —
   all present.
