# FaceFrame Stress / Scale Report

Date: 2026-09-18 · Scope: backend (`python-backend/main.py`) under a synthetic
library, driven over the real NDJSON stdin/stdout protocol exactly as the
Electron shell does it (subprocess, unbuffered venv python, cwd = repo root,
`provider: "cpu"` on scan requests).

Harness (new files, nothing else modified):

- `scripts/stress_library.py` — synthetic library generator
- `scripts/stress_e2e.py` — 13-item benchmark / correctness harness
- `scripts/run_stress_e2e.sh` — background-run helper

Raw outputs: `test-data/stress-logs/` (run logs, backend stderr logs, per-run
`results_*.json`). Libraries: `test-data/stress-library-3000` (3,030 media
files, 706 MB, 150 planted duplicate hash groups) and
`test-data/stress-library-1000` (1,030 files, 217 MB, 50 dup groups).
Generation per spec: ~120 day folders over 2 years, dimensions 640x480 …
4000x3000, JPEG q60-96, EXIF DateTimeOriginal + GPS on ~60 % with mtime =
EXIF time, ~5 % byte-identical duplicates, 30 MJPG AVI videos, adversarial
names (`naïve_照片_🎉.jpg`, `100%_done.jpg`, apostrophes, spaces, a 180-char
filename, 40-level deep nesting).

Environment: AMD Ryzen 7 6800H (16 threads), 15 GB RAM, Windows
10.0.26100, Python 3.11.9 venv, onnxruntime 1.30.0, insightface 0.7.3,
OpenCV 4.11.0, numpy 1.26.4.

Note on "faces disabled": the backend's public protocol offers no way to run
a scan without a face engine — `run_scan()` always attaches the shared
processor built at startup (`main.py::get_shared_processor`). Detection and
labeling therefore ran on every photo (realistic worst case); `faces=0`
simply reflects that synthetic gradients/shapes contain no faces. The
'hashed' leave-for-later path in `scan.py` exists but was not exercisable
from outside.

## Timing table — 3,000-file library (3,030 items = 3,000 photos incl. 150
duplicate copies + 30 videos)

| # | Scenario | Threshold | Measured | Verdict |
|---|----------|-----------|----------|---------|
| 0 | Backend startup → first ping | ~3–8 s expected | 3.6 s | OK |
| 1 | Cold scan (fresh index) | ≤ 20 min | **415 s (6:55), 7.3 files/s** (3,030 files; decoded 2,880, dedup hits 150, videos 30) | **PASS** |
| 2 | Warm rescan | < 60 s, `decoded: 0` | **8.5 s**, decoded 0, skipped_unchanged 3,030 | **PASS** |
| 3 | get_feed (days) | < 3 s, every file exactly once | **0.06 s**, 3,030 items, path multiset == manifest, no double-count with 150 dup pairs present | **PASS** |
| 4 | get_feed × 10 | < 6 s total | **0.53 s** (max single call 0.08 s) | **PASS** |
| 5 | search `photo` / `type:video` / `is:favorite` / label term | each < 2 s | 0.02 / 0.00 / 0.02 / 0.09 s; hits 1 / 30 / 50 / 535 | **PASS** |
| 6 | get_duplicates | < 2 s, finds planted groups | **0.02 s**, 150/150 groups, each exactly 2 paths | **PASS** |
| 7 | album_add (500 hashes) | < 5 s | **0.02 s** | **PASS** |
| 7 | get_album | < 5 s, 500 items | **1.36 s**, 500 items | **PASS** |
| 8 | Move 200 files + rescan | decoded 0 (hash hits), all 200 present once | decoded 0, dedup_hits 200, feed 3,030 with all new paths exactly once — **but `missing_now: 200`** | **FAIL** (Finding 2) |
| 9 | Delete 300 on disk + rescan | `missing_now: 300`, feed −300, get_missing = 300 | missing_now 300 ✓, feed 2,730 ✓ — **get_missing = 500** | **FAIL** (Finding 2) |
| 10 | Restore 100 identical bytes + rescan | relink, no mass re-decode | missing_now 0 ✓, relinked via `content_unchanged: 100`, **decoded 0** (spec predicted 100; see Finding 4), feed 2,830 ✓ | **PASS** |
| 11 | set_trashed 1,000 hashes | < 5 s, feed adjusts | **0.05 s**; hid 1,134 file rows. Feed showed 1,696 vs harness-predicted 1,693 — harness artifact (Finding 7), backend hid exactly the right rows | PASS (timing) |
| 11 | empty_trash | < 5 s, trash purged | 0.58 s but **removed only 19 of 1,153 rows — `send2trash` is not installed**; 1,134 items stay trashed in DB and on disk | **FAIL** (Finding 1) |
| 12 | Memory: WorkingSet after cold scan / after 50 get_feed | flag growth > 500 MB | 553 MB / 554 MB (**+1 MB**); 559 MB at end of run | **PASS** |
| 13 | Concurrency: ping + get_feed every 1 s during scan of 200 new files | every ping < 2 s | 31/31 pings answered, **max 0.00 s**; 31/31 feeds succeeded mid-scan (0.03–0.25 s); scan decoded 200 | **PASS** |

Totals: **36 PASS / 5 FAIL**, of which 2 FAILs are the real backend findings
below, 3 are consequences/artifacts (1 real + 2 harness bookkeeping, see
Finding 7).

## 1,000-file comparison (same seed recipe, seed 7)

| Metric | 1,030 files | 3,030 files | Ratio | Verdict |
|--------|-------------|-------------|-------|---------|
| Cold scan | 137 s (**7.5 files/s**) | 415 s (**7.3 files/s**) | 3.03× time for 2.94× files | **Linear — no superlinear behavior** |
| Warm rescan | 3.6 s | 8.5 s | 2.4× | Sublinear, fine |
| get_feed | 0.03 s | 0.06 s | 2× | Linear, fine |
| get_album (500 items) | 1.72 s | 1.36 s | ~1× | Item-count dominated (Finding 5) |
| RSS after cold scan | 550 MB | 553 MB | ~1× | Flat |
| RSS after 50 feeds | 543 MB (−7 MB) | 554 MB (+1 MB) | — | No leak |

## Findings

1. **HIGH — `send2trash` is missing from the venv; trash deletion silently
   does nothing.** `pip list` has no Send2Trash although
   `python-backend/requirements.txt` requires it. `library.py` anticipates
   this with a stub that raises `RuntimeError("send2trash is not installed")`
   (line 32), but `_send_path_to_os_trash()` catches everything and returns
   `False`, so: `empty_trash` returned `ok` and removed only 19 rows (those
   whose files were already absent from disk) while logging 1,134
   `Could not move … : send2trash is not installed` ERROR lines
   (`test-data/stress-logs/backend_stress-library-3000.log`); 1,134 trashed
   items remain in the DB **and on disk** (post-run disk walk: 3,030 files,
   all trashed files present). Same failure path applies to
   `delete_from_disk` (always `removed: 0`) and to `purge_expired_trash`
   (the 60-day retention policy can never reclaim anything). The UI gets no
   error — the operation just quietly doesn't happen. Fix is
   `pip install -r python-backend/requirements.txt` (or surface the error to
   the caller).

2. **MEDIUM — file moves are reported as missing and the ghost rows live
   forever.** After os.rename of 200 files + rescan, the new paths indexed
   via hash hits (dedup_hits 200, decoded 0 — content never recomputed,
   correct), but `_mark_missing` flagged the 200 old paths `missing=1`:
   `missing_now: 200` instead of 0. Nothing ever reconciles them, so
   `get_missing` grew to 500 (200 move-ghosts + 300 real deletions), then
   400 after restoring 100 — i.e. the missing-files view conflates "user
   moved a file in Explorer" with "file is gone", permanently, at the rate
   of one ghost row per moved file. Reproduced identically at 1k and 3k.
   Suggested direction: when a new path hashes to a media row whose only
   other reference just went missing in the same pass, retire the old row
   (move instead of delete+add).

3. **LOW — `label:` is parsed but ignored by search.** `query.py` lists
   `label` in `KNOWN_KEYS`, but `views.search_items` never reads
   `parsed.filters["label"]` (zero occurrences of "label" in views.py), so
   `label:sky` silently returns *unfiltered* results. Label matching only
   works as free-text FTS (labels are indexed into `media_fts`), which is
   what the harness used (term `envelope`: 535 hits, 0.09 s).

4. **LOW / spec deviation (benign, better than predicted) — restored
   identical bytes relink with zero decode.** The spec expected
   `decoded == 100`; in reality the 100 recreated files matched their
   existing file rows' content hash, so the scan refreshed the stat mirror
   (`content_unchanged: 100`, `upsert_file` clears `missing`) and decoded
   nothing. `missing_now: 0`, all 100 back in the feed, and the 200
   truly-gone files stayed missing. Correct and cheap — the spec's
   arithmetic just assumed the dedup-media path rather than the
   same-path-restored path.

5. **INFO — get_album is the slowest read path (N+1 queries).** 1.36–1.72 s
   for a 500-item album vs 0.06 s for a 3,030-item feed: `get_album` runs
   `get_file_state` + `_any_path` (2 queries) per item plus cover lookup.
   Passes the 5 s threshold with margin at 500 items but extrapolates past
   it around ~1.5–2 k album items.

6. **INFO — non-JSON line on stdout at startup.** insightface `print`s
   `Applied providers: ['CPUExecutionProvider']...` to the backend's stdout
   (the NDJSON channel). Both the Electron parser and this harness skip
   unparsable lines, so it is benign today, but any strict NDJSON consumer
   would choke; a log-level guard in the import chain would be safer.

7. **INFO — harness artifacts, not backend bugs** (for the record, since
   they appear as FAILs in the raw logs): (a) the 3,000-run item-11 feed
   check predicted 1,693 vs actual 1,696 because the trash-hash list
   contained 3 hashes twice (a dup hash whose twin was deleted landed in
   both selection buckets; the harness double-counted their hidden rows) —
   the backend hid exactly the correct 1,134 rows; fixed in the harness
   afterward (`dict.fromkeys` dedupe). (b) The 1,000-run item-11 could not
   collect 1,000 hashes (only 789 distinct hashes remained after the
   deletions) — a scale artifact of the small library. (c) item-13's
   "expected 1,893 vs 1,896" cascades from (a).

### Non-issues verified

Warm rescans are pure stat checks (0 re-hashes, `hashed: 0`); duplicates are
detected exactly (no double-counting in any feed/search/duplicate view);
unicode/percent/apostrophe/180-char/40-deep paths index, list and trash
without errors (no path-related warnings in either backend log); the
dispatcher stays responsive under a running scan (worst ping 0.00 s, feeds
0.03–0.25 s); memory is flat across scans, 50 feed fetches, and a concurrent
scan (~550 MB, growth ≤ 1 MB).

## Verdict

At 3,000 files the backend is comfortably within every performance budget in
the plan: a full cold ingest — decode, EXIF, perceptual hash, ONNX labeling
and face detection on every image — finishes in under 7 minutes (7.3 files/s,
identical to the 7.5 files/s at 1,000 files, i.e. clean linear scaling with
no superlinear degradation), warm rescans take 8.5 s with zero rework, all
read paths answer in well under a second except the N+1-shaped `get_album`
(1.4 s for 500 items), the process stays responsive to pings and feeds while
a scan runs, and memory is flat at ~550 MB with no leak over 50 feed
fetches. The two things that actually broke were not scale problems: the
venv is missing `send2trash`, which silently neuters empty_trash /
delete_from_disk / trash retention entirely (high severity, one-line fix),
and moved files are permanently misreported as missing, which will flood the
missing-files review for any user who reorganizes folders in Explorer
(medium severity). Neither blocks browsing, search, duplicates, or albums,
which all behaved exactly and correctly at this scale; I would ship the
scale story as-is and treat those two correctness findings as release
blockers on their own merits.
