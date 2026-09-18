# FaceFrame Photos

A fully local photo manager with the Google Photos experience — an infinite
day-grouped feed, search across people, places and things, albums, memories,
a non-destructive editor, videos, favorites, archive, trash and a locked
folder. No account, no backup, no uploads: your photos never leave this
machine, and the entire index lives in a hidden `.faceframe` folder inside
the library you choose. Delete that folder and every trace of FaceFrame is
gone.

![License](https://img.shields.io/badge/license-MIT-blue.svg)

## What it does

- **Feed** — reverse-chronological, day/month/year grouped, justified and
  virtualized (smooth at tens of thousands of items), with a density slider,
  a memories carousel ("1 year ago", highlights), and multi-select with bulk
  favorite / archive / trash / add-to-album.
- **Viewer** — full-screen zoomable photos and streamed videos (with
  scrubber), EXIF info panel, editable description and date, face chips,
  undo toasts everywhere.
- **Editor** — crop & rotate, straighten, 14 filter presets, light
  adjustments (brightness, contrast, saturation, warmth, highlights, shadows,
  sharpen, vignette), and a magic eraser (object removal via inpainting).
  Edits are stored transforms — originals are never modified; "Revert"
  removes them.
- **People** — faces detected and grouped locally (InsightFace), name /
  merge / split / hide people, search by name.
- **Search** — one box for everything: captions, filenames, people
  (`person:Alice`), labels ("things": `dog`, `beach`), places
  (`place:Paris`), types (`type:video`, `is:favorite`, `year:2024`), with
  filter chips.
- **Places & memories** — GPS clustering into places (optional, cached,
  toggleable reverse geocoding), "on this day", trips and highlights builders.
- **Library care** — albums (cover, sort, reorder), trash with 60-day
  retention that ends in the OS Recycle Bin (never a hard delete), archive,
  a passcode-gated locked folder, exact-duplicate review, missing-file
  tracking, storage stats and cache cleanup.
- **Watch mode** (optional) — rescans while running so new photos appear
  automatically.

## Requirements

- Node.js 18+
- Python 3.10–3.12 (3.11 recommended; newer Pythons have no prebuilt
  insightface wheels yet)
- Windows, Linux, or macOS

The face models (~350 MB) download once — at first launch of the photo
engine — into `~/.insightface`; the optional "things" labeler adds a one-time
~14 MB model. On slow connections the first launch waits for the download
(the app tells you it is preparing the models). Everything after that is
fully offline.

## Setup

```bash
git clone https://github.com/Pranav-P-S/FaceFrame.git
cd FaceFrame

npm install

python -m venv venv
# Windows:
venv\Scripts\pip install -r python-backend\requirements.txt
# Linux / macOS:
venv/bin/pip install -r python-backend/requirements.txt
```

### Optional: GPU acceleration

Inference runs on CPU through ONNX Runtime by default. With NVIDIA CUDA 12.x
+ cuDNN installed, add the GPU runtime and pick it in Settings:

```bash
venv\Scripts\pip install onnxruntime-gpu
```

Then pick **GPU (CUDA)** under Settings → Processing → Detection engine. If
the CUDA libraries are missing at runtime, the app quietly falls back to
CPU.

## Running

```bash
npm run electron:dev
```

This starts the Vite dev server and the Electron shell, which spawns the
Python backend from `venv`. For a production bundle:

```bash
npm run electron:build   # installer in release/
```

Opening the UI in a plain browser (`npm run dev`) works too, against a
deterministic mock backend (`?mock=1` forces it even inside Electron) —
handy for development and demos with no Python at all.

## Tests

```bash
# backend units (fast: schema, hashing, scan idempotency, search, places…)
venv/Scripts/python -m pytest python-backend/tests -q

# backend end-to-end, spoken to exactly like the Electron shell does
npm test

# v3 acceptance suite: idempotent rescans, zero-cost moves, duplicates,
# albums, trash, locked folder, edits — the feature contract
venv/Scripts/python scripts/e2e_v3_test.py

# frontend units (justified layout, query parser, routing, formatting)
npm run test:unit

# frontend typecheck + build
npm run build
```

The clustering quality gate runs against the LFW-based test library
(`venv/Scripts/python scripts/make_test_library.py` builds it) and scores
1.00 purity on the 6-person set.

## Project layout

```
electron/            Electron main + preload (backend spawn, IPC, app://
                     bundle protocol, media:// range streaming)
python-backend/      Local photo service (stdin/stdout JSON protocol)
  main.py            command registry (~55 actions), scan/cluster jobs
  schema.py          content-addressed schema v3 + migration from v0.2
  store.py           SQLite (WAL) query layer
  scan.py            idempotent scan pipeline (hash → process → reconcile → GC)
  hashing.py         sha256 content hashes + dHash perceptual hashes
  exif.py            capture time (naive wall clock), camera info, GPS
  labels.py          optional MobileNet "things" labels
  processor.py       InsightFace wrapper (detection + recognition)
  people.py          DBSCAN clustering, split/merge/assign/hide
  library.py         favorites, archive, trash + 60-day purge, locked folder
  views.py           feed + search execution, item detail
  query.py           search query parser
  places.py          geohash clustering, optional reverse geocoding
  memories.py        on-this-day / trips / highlights builders
  render.py          cached preview pipeline with stored edit transforms
  creations.py       collages + animations
  watcher.py         watch mode
src/                 React UI (GP-style shell, feed, viewer, editor, pages)
scripts/             test-library builder, e2e suites, CDP capture
docs/                feature analysis, architecture critique, spec
```

## Design notes

- The Python backend is a plain subprocess speaking newline-delimited JSON on
  stdin/stdout. Electron correlates requests by id and broadcasts events; a
  dead backend restarts with backoff before the UI gives up and says so.
- The index is **content-addressed**: one `media` row per unique byte
  sequence owns every expensive artifact (faces, labels, EXIF, posters,
  edits). Re-scans of unchanged content decode nothing; moving or renaming a
  file costs a rehash, never a re-inference, and keeps its people
  assignments; exact duplicates are found by grouping hashes. Cache files are
  named by content and swept when unreferenced.
- Photos are never mutated. Favorites, captions, dates, edits — all live in
  the index; "Revert" is deleting JSON. Deletes go to the OS Recycle Bin,
  always, and only files that are both indexed and trashed can be deleted.
- Videos stream through a `media://` protocol with HTTP range support,
  restricted to backend-verified library roots and real media extensions.
- The heavy inference chain is imported (and the models constructed) on the
  backend's main thread at startup: loading ONNX sessions inside worker
  threads deadlocks nondeterministically on Windows.
- Each library gets its own `.faceframe/` (SQLite index + caches). Deleting
  it removes every trace of FaceFrame for that library.

## Honest limitations

- No RAW decoding, no video trimming/re-encoding, no auto-movies, no cloud
  anything (backup, sharing links, comments) — the app is local by design.
- The locked folder hides items from FaceFrame's views behind a passcode; it
  does not encrypt files on disk (the UI says so).
- Pets are not detected (the face model is human-only). "Things" labels come
  from an ImageNet classifier — broad categories, not fine-grained scenes.
- Very small faces (under ~28 px) are skipped by the detector; abstract
  wallpapers can produce confident false positives.
- The packaged build does not bundle Python: the venv step is not optional.
- Clustering re-runs over all faces and reassigns noise on every run; heavy
  manual face curation can be reshuffled (renamed people always keep their
  cluster).

## Contributing

Issues and pull requests are welcome. If you touch the backend, run
`npm test`, the pytest suite, and `scripts/e2e_v3_test.py` — together they
cover the whole command surface and the content-addressing contract.

## License

MIT — see [LICENSE](LICENSE).
