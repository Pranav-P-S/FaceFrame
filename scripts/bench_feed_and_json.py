"""Ground-truth numbers for the perf critique: feed sort cost and
json.dumps cost at 20k items. Run: venv python scripts/bench_feed_and_json.py"""

import json
import sqlite3
import sys
import time
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python-backend"))

N = 20_000
tmp = Path(tempfile.mkdtemp())
db = tmp / "index.db"

conn = sqlite3.connect(db)
import schema  # noqa: E402

schema.ensure_schema(conn)
rows = []
for i in range(N):
    h = f"{i:064x}"
    rows.append(
        (h, "photo", 1600, 1067, None, 1_700_000_000 + i * 37, None, None, None, None, "none", None)
    )
conn.executemany(
    """INSERT INTO media (content_hash, kind, width, height, duration,
       capture_time, tz_offset, exif, phash, labels, analysis_state, analyzed_at)
       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
    rows,
)
conn.executemany(
    """INSERT INTO files (path, content_hash, kind, size, mtime, missing, added_at)
       VALUES (?,?,?,?,?,0,?)""",
    [(f"IMG_{i:05d}.jpg", f"{i:064x}", "photo", 500_000, 1_700_000_000 + i * 37, 1_700_000_000)
     for i in range(N)],
)
conn.commit()

FEED_SQL = """
    SELECT f.path, f.content_hash, f.kind, f.added_at, f.paired_path AS motion,
           COALESCE(m.date_override, m.capture_time, f.mtime) AS ts,
           m.kind AS media_kind, m.width, m.height, m.duration,
           m.favorite, m.archived, m.locked, m.caption, m.flags, m.labels
    FROM files f JOIN media m ON m.content_hash = f.content_hash
    WHERE f.missing=0 AND f.trashed_at IS NULL
      AND COALESCE(m.locked,0)=0 AND COALESCE(m.archived,0)=0
      AND NOT (m.kind='video' AND f.paired_path IS NOT NULL)
    ORDER BY ts DESC, f.path LIMIT 4000
"""

print("query plan:")
for row in conn.execute("EXPLAIN QUERY PLAN " + FEED_SQL):
    print("  ", row[-1])

best = min(
    (lambda t0: (conn.execute(FEED_SQL).fetchall(), time.perf_counter() - t0)[1])(time.perf_counter())
    for _ in range(5)
)
print(f"feed query (20k files, limit 4000): {best*1000:.1f} ms")

feed_rows = conn.execute("SELECT * FROM files LIMIT 4000").fetchall()
payload = {"groups": [{"key": "2024-01-01", "items": [dict(zip(
    ("path", "content_hash", "kind", "size", "mtime", "missing", "added_at"), r
)) for r in feed_rows]}], "total": len(feed_rows)}
t0 = time.perf_counter()
line = json.dumps(payload)
dt = time.perf_counter() - t0
print(f"json.dumps of {len(feed_rows)}-item feed payload ({len(line)/1e6:.1f} MB): {dt*1000:.1f} ms")
