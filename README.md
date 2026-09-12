# FaceFrame

FaceFrame scans a folder of photos, finds the faces in them and groups them
by person — all on your machine. Nothing is uploaded, nothing leaves the
device, and there is no account. The whole index lives in a single hidden
folder inside the library you choose, so removing FaceFrame from your life
is a matter of deleting that folder.

![License](https://img.shields.io/badge/license-MIT-blue.svg)

## What it does

1. **Scan** — pick a folder. FaceFrame walks it (skipping its own metadata),
   decodes every JPG/PNG/WebP/BMP/TIFF with correct EXIF orientation, and
   runs detection + recognition through InsightFace's `buffalo_l` models.
2. **Group** — one click runs DBSCAN over the face embeddings and creates a
   card per person, with the largest detected crop as the portrait.
3. **Organize** — click a person to see every photo they appear in, open any
   photo full-screen, rename people, or merge two cards that should be one.

Re-scans are incremental: unchanged files are skipped, edited files have
their old detections replaced, deleted files are pruned from the index.
Re-clustering is stable — people you renamed keep their names.

## Requirements

- Node.js 18+
- Python 3.10–3.12 (3.11 recommended; newer Pythons have no prebuilt
  insightface wheels yet)
- Windows, Linux, or macOS

The face models (~350 MB) are downloaded once, on the first scan, into
`~/.insightface`. Everything after that is fully offline.

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

By default inference runs on CPU through ONNX Runtime, which is fine for
ordinary photo libraries (the LFW test library scans at roughly four
images per second on this laptop's CPU). If you have NVIDIA CUDA 12.x +
cuDNN set up, install the GPU build of the runtime and pick GPU in the
app's engine selector:

```bash
venv\Scripts\pip install onnxruntime-gpu
```

If the CUDA libraries are missing at runtime, the app quietly falls back to
CPU instead of failing.

## Running

```bash
npm run electron:dev
```

This starts the Vite dev server and the Electron shell, which spawns the
Python backend from `venv`. For a production bundle:

```bash
npm run electron:build   # installer in release/
```

The packaged app needs Python with the backend dependencies on the target
machine: a `venv` folder next to the app, a system Python 3.10–3.12 with
the requirements installed, or any interpreter pointed to by the
`FACEFRAME_PYTHON` environment variable.

## Tests

The backend has an end-to-end test that talks to it exactly like the
Electron shell does — scan, cluster, rename, merge, rescan, cancel, clear:

```bash
# one-time: build a small test library from the LFW dataset (~180 MB)
venv/Scripts/python scripts/make_test_library.py   # venv/bin/python on unix

npm test
```

The clustering check requires better-than-chance grouping of the LFW
identities; on the 6-person test library it currently scores 1.00 purity.

## Project layout

```
electron/            Electron main + preload (backend spawn, IPC, window)
python-backend/      Face detection service (stdin/stdout JSON protocol)
  main.py            command loop, scan orchestration
  scanner.py         incremental directory walk, prefetching decoder
  processor.py       InsightFace wrapper, thumbnails, hardware probe
  clusterer.py       DBSCAN clustering with stable person ids
  database.py        SQLite (WAL) index storage
src/                 React UI
scripts/             test-library builder, e2e test, window capture
```

## Design notes

- The Python backend is a plain subprocess speaking newline-delimited JSON
  on stdin/stdout. Electron correlates requests by id and broadcasts events;
  if the backend dies it is restarted a few times with growing delays
  before the UI gives up and says so.
- Photos are shown through IPC reads, not custom file protocols, so path
  encoding never has to survive a URL round trip.
- Each library gets its own `.faceframe/` (SQLite index + face thumbnails).
  Deleting it removes every trace of FaceFrame for that library.
- Clustering runs over all faces every time, then matches clusters back to
  existing people by shared face count. Renamed people keep their names,
  auto-named cards keep their ids, and empty persons are cleaned up.

## Honest limitations

- Detection quality follows InsightFace: strong on clear frontal faces,
  weaker on profiles, heavy occlusion, and very small faces (under ~28 px
  are skipped outright). Abstract wallpapers occasionally produce a
  confident false positive — that is the detector, not a bug in the loop.
- Faces with no cluster stay visible under "Unsorted faces" rather than
  being hidden; they will be picked up the next time clustering runs.
- The packaged build does not bundle Python. That keeps the installer small
  and the setup honest, but it does mean the venv step is not optional.

## Contributing

Issues and pull requests are welcome. If you touch the backend, run
`npm test` — the e2e suite covers the whole command surface.

## License

MIT — see [LICENSE](LICENSE).
