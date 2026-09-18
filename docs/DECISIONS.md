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

## D6 — Locked folder hides, does not encrypt
Files stay on disk; the passcode (PBKDF2-HMAC-SHA256, 600k iterations) gates
FaceFrame's views only. The UI states this on the lock screen and in
Settings. Rationale: silently implying encryption would be dishonest.

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
