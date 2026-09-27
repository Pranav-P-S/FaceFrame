# Decision Log — v3 ("local Google Photos") pivot

Companion to `SPEC.md` and `ARCHITECTURE_CRITIQUE.md`. The user delegated
decisions ("choose the most-feature option in case of doubt"); each choice
below was made autonomously and is recorded with its rationale.

## D1 — Content-addressed index (schema v3)
All expensive artifacts (faces, labels, EXIF, posters, edits) hang off a
`media` row keyed by sha256; `files` maps paths onto it. Rationale: the only
way to satisfy "no double computation" and duplicate detection with one
mechanism. Migration from v0.2 re-hashes indexed files once and carries
faces/embeddings over (no re-inference).

## D2 — Photos are never mutated
Edits, captions, date corrections are index-side JSON; "Revert" deletes JSON;
exports bake transforms into NEW files. Rationale: a local manager must be
strictly safer than the cloud original.

## D3 — Deletes go to the OS Recycle Bin, always
`delete_from_disk` requires the file to be indexed AND trashed, and resolves
through a containment check; the 60-day trash purge uses the same path.
Rationale: the user mandate asked for GP semantics; GP's "delete from disk"
locally must never be a hard delete.

## D4 — Heavy model chain is imported and constructed on the main thread
Importing insightface→albumentations→scipy, or creating ONNX sessions, inside
a worker thread deadlocks nondeterministically on Windows (reproduced
minimally; v0.2 avoided it by importing sklearn at startup). The backend
therefore warms the chain and constructs the shared FaceProcessor during
startup, and every scan reuses the singleton. CUDA-on-CPU requests quietly
fall back to the shared instance. Watch passes reuse the same engines so
watch-indexed photos receive faces immediately.

## D5 — "Things" labels via a one-time 14 MB MobileNet download
GP's "search your photos by what's in them" is core UX. A small ONNX
classifier keeps it offline after the first download and degrades silently
when unavailable. Scans never block on the network: the label model is
downloaded in a background thread and labels apply on a later pass.

## D6 — Hidden folder hides, does not encrypt
Files stay on disk; the passcode (PBKDF2-HMAC-SHA256, 600k iterations) gates
FaceFrame's views only. The UI states this on the unlock screen and in
Settings. Rationale: silently implying encryption would be dishonest. The
feature was renamed from "Locked folder" to "Hidden folder" in the UI for
the same reason — "locked" promised more than UI-level hiding delivers —
while backend action names (`set_locked`, …) and the `#/locked` route stay
for contract stability.

## D7 — media:// streaming is allowlisted by verified roots only
The Electron main process registers a media root only after the backend
confirms an open/scan of that folder, serves only known media extensions, and
never serves `.faceframe`. Trade-off accepted: a compromised renderer could
still register an arbitrary existing folder — bounded to media files by the
extension whitelist.

## D8 — Deferred (documented, not forgotten)
RAW decoding, video trim/re-encode, auto-movies, portrait blur, LAN sharing.
All require heavy new dependencies or online services; each is listed in the
analysis doc with its reason. None blocks the core feature set.

## D9 — The double-check wave: wire, don't descope
The first double-check found features the backend fully implemented and the
docs promised but no UI could reach (merge/split/hide/assign people, album
rename/delete/cover/reorder, collages, creations delete, index restore).
Backend + e2e already covered them, so the honest fix was finishing the last
mile in the renderer rather than cutting the docs down.

## D10 — window.prompt is not available in Electron
It throws, and it silently disabled add-to-album, export and backup. The app
uses a small in-app text dialog (src/lib/prompt.ts); window.confirm stays —
Electron implements it.

## D11 — Undecodable is not the same as failed
A decode failure is retried on later passes: cloud-placeholder and
still-copying files decode fine on the next scan. Content PIL refuses
 outright (decompression bomb) is marked analyzed with an `undecodable` flag
 instead, because the same bytes fail identically forever.

## D12 — One-command backend setup, bundling stays a v2 decision
`npm run setup` (scripts/setup-backend.mjs) finds a system Python, creates
the venv and pip-installs the requirements — one cross-platform command
instead of the README's per-platform venv dance. Full backend bundling
(PyInstaller inside electron-builder extraResources) would remove the
terminal entirely but ships ~1.5 GB installers and needs per-OS build
verification; the spawn layer already accepts a packaged venv at
resources/venv, so that step stays open for v2 rather than half-done now.

## D13 — Pet search via query expansion, not stored-label rewrites
The labeler's ImageNet class order is fixed (151-268 dog breeds, 281-285
cats), so free-text category words expand at SEARCH time to those class
names (store.search_groups any-of groups). Rationale: expanding at scan time
would either rewrite stored labels — invisible to existing libraries until a
full re-inference — or fork a label schema version; query expansion applies
to every library on the next search, costs nothing at scan time, and
degrades to the raw token when the classes file is absent. Search never
downloads.

## D14 — Pooled SQLite connections that release at rest
Each worker thread reuses one open connection (PRAGMAs once, no per-call
open/close) instead of the v0.2-era open-per-operation. Two rules make it
safe on Windows, where an open handle blocks any replacement of index.db:
jobs (scan/watch) close their pipeline store in a finally, and
clear_index/restore_index call store.close_all() before touching the file.
AND the command thread releases its connection after every request — a
desktop app that locks the user's library folder at rest would break
Explorer renames of that folder, which the old open/close-per-call behavior
accidentally permitted. Dead job threads' connections are reaped lazily on
the next connect.

## D15 — The feed sort is materialized, not computed
`ORDER BY COALESCE(date_override, capture_time, mtime)` temp-B-tree-sorted
the library on every feed load (38 ms at 20k files, growing with size). The
key now lives in files.sort_time — maintained by upsert_file and
date-override writes, backfilled by the schema upgrade — with a PARTIAL
index on (sort_time DESC, path) WHERE missing=0 AND trashed_at IS NULL,
matching the view filter exactly. The old full index on trashed_at actively
steered the planner into the sort; it is now partial (IS NOT NULL), which is
also precisely the trash page's query. A media-level column would NOT have
worked: the path tiebreak lives on files. Date FILTERS (after:/on:) still
use the live COALESCE so no-EXIF items keep their existing filter semantics.

## D16 — Scan parallelism stops at hashing (and other rejected speedups)
Disk reads + sha256 (hashlib releases the GIL) run 8 files ahead of the
decode/inference loop on a 4-thread pool; decode and inference stay on the
scan thread because the engine contract (D4) and the per-file state machine
are far too subtle to race for a modest win. Rejected with measurements or
contract arguments: orjson (stdlib dumps does a 4000-item feed in ~6 ms),
PyTurboJPEG/cv2 decode (PIL's decompression-bomb guard is load-bearing for
the undecodable flag), explicit ONNX SessionOptions (InsightFace builds its
own sessions; ORT already defaults intra-op threads to physical cores),
@vitejs/plugin-react-swc (0.9 s builds; tsc dominates typechecking).

## D17 — Job serialization stays global until multi-library exists
busy_worker() refuses any second scan/cluster/watcher pass across ALL
libraries. A per-library TaskManager was proposed; rejected because (a) the
UI binds exactly one library at a time, so the global lock is de facto
per-library, and (b) every job shares one preloaded ONNX engine and
thumbnail dir (D4's Windows constraints) — concurrent jobs would contend on
that singleton anyway, so per-library keys would buy complexity and races,
not parallelism. The watcher already coalesces passes across all
_contexts. Revisit only when the product actually holds multiple libraries
open, together with the engine-pooling decision that must precede it.

Related rebuttals from the same review: album covers now fall back to the
album's first live item (implemented — a deleted cover no longer renders a
gray tile; the stored cover_hash is preserved so restoring the photo
restores the choice); ghost-trash (files deleted outside the app are
already cleared by empty_trash — the isfile check skips the recycle-bin
call and removes the DB row) is now pinned by an e2e check; and
incremental item_added/item_removed feed patching stays rejected — tens of
milliseconds per rare scan_complete cannot justify forking the refresh
data flow every mutation shares.
