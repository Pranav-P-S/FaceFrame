"""Store: the v3 content-addressed query layer.

One Store per operation is fine (connections are opened per call and closed,
a Windows constraint inherited from v0.2). All item queries anchor on
``files`` so an item appears at most once per view; all expensive data hangs
off ``media`` keyed by content hash so it is computed once per content.
"""

import logging
import sqlite3
import time
from contextlib import contextmanager

import schema

logger = logging.getLogger("FaceFrame.Store")

# Columns the scan pipeline may write. User state (caption, edit,
# date_override, favorite, locked) is deliberately outside this set so a
# rescan can never clobber it.
MEDIA_SCAN_FIELDS = (
    "kind", "width", "height", "duration", "capture_time", "tz_offset",
    "exif", "phash", "labels", "poster_path", "analysis_state", "analyzed_at",
)


class Store:
    def __init__(self, db_path: str, library_root: str | None = None):
        self.db_path = db_path
        self.library_root = library_root
        with self.connect() as conn:
            schema.ensure_schema(conn, library_root)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.row_factory = sqlite3.Row
            with conn:
                yield conn
        finally:
            conn.close()

    # -- meta ------------------------------------------------------------

    def get_meta(self, key: str, default=None):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key=?", (key,)
            ).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str):
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO meta (key, value) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (key, str(value)),
            )

    # -- media -----------------------------------------------------------

    def upsert_media(self, row: dict):
        """Insert or scan-update a media row. User-owned columns survive."""
        cols, vals = [], []
        for field in MEDIA_SCAN_FIELDS:
            if field in row:
                cols.append(field)
                vals.append(row[field])
        if not cols:
            return
        if "content_hash" not in row:
            raise ValueError("upsert_media requires content_hash")
        placeholders = ", ".join("?" for _ in cols)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "content_hash")
        with self.connect() as conn:
            conn.execute(
                f"""INSERT INTO media (content_hash, {", ".join(cols)})
                    VALUES (?, {placeholders})
                    ON CONFLICT(content_hash) DO UPDATE SET {updates}""",
                (row["content_hash"], *vals),
            )

    def media_count(self) -> int:
        with self.connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]

    def set_media_user_state(
        self,
        content_hash: str,
        caption=...,
        edit=...,
        date_override=...,
        favorite=...,
        locked=...,
    ):
        """Update user-owned columns; sentinel ``...`` means leave unchanged."""
        fields = {
            "caption": caption,
            "edit": edit,
            "date_override": date_override,
            "favorite": favorite,
            "locked": locked,
        }
        sets, vals = [], []
        for name, value in fields.items():
            if value is not ...:
                sets.append(f"{name}=?")
                vals.append(value)
        if not sets:
            return
        vals.append(content_hash)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE media SET {', '.join(sets)} WHERE content_hash=?", vals
            )

    # -- files -----------------------------------------------------------

    def upsert_file(
        self,
        path: str,
        content_hash: str,
        mtime: float,
        size: int,
        kind: str = "photo",
        added_at: float | None = None,
    ):
        """Register a file. added_at is preserved across rescans so 'Recently
        added' does not churn on every scan."""
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO files (path, content_hash, kind, size, mtime,
                                      missing, last_seen, added_at)
                   VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET
                       content_hash=excluded.content_hash,
                       kind=excluded.kind,
                       size=excluded.size,
                       mtime=excluded.mtime,
                       missing=0,
                       last_seen=excluded.last_seen""",
                (path, content_hash, kind, size, mtime,
                 time.time(), added_at if added_at is not None else time.time()),
            )

    def remove_file(self, path: str):
        """Drop a file row; GC the media row if it lost its last reference."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT content_hash FROM files WHERE path=?", (path,)
            ).fetchone()
            conn.execute("DELETE FROM files WHERE path=?", (path,))
            if row and row[0]:
                refs = conn.execute(
                    "SELECT COUNT(*) FROM files WHERE content_hash=?", (row[0],)
                ).fetchone()[0]
                if refs == 0:
                    conn.execute(
                        "DELETE FROM media WHERE content_hash=?", (row[0],)
                    )

    def all_files(self):
        with self.connect() as conn:
            return conn.execute(
                "SELECT path, content_hash, kind, size, mtime, missing FROM files"
            ).fetchall()

    # -- faces -----------------------------------------------------------

    def add_faces(self, content_hash: str, faces: list):
        """Replace a media's face set atomically. ``faces`` entries use the
        processor shape: embedding, bbox, det_score, thumbnail."""
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM faces WHERE content_hash=?", (content_hash,)
            )
            conn.executemany(
                """INSERT INTO faces (content_hash, face_index, bbox, embedding,
                                      det_score, thumbnail_path)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        content_hash,
                        index,
                        face["bbox"] if isinstance(face["bbox"], str)
                        else _dumps(face["bbox"]),
                        _dumps(face["embedding"]),
                        face.get("det_score"),
                        face.get("thumbnail"),
                    )
                    for index, face in enumerate(faces)
                ],
            )

    # -- duplicates --------------------------------------------------------

    def duplicate_groups(self, min_count: int = 2):
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT content_hash, COUNT(*) AS n, GROUP_CONCAT(path, '\x1f') AS paths
                   FROM files
                   WHERE content_hash IS NOT NULL AND missing=0 AND trashed_at IS NULL
                   GROUP BY content_hash HAVING n >= ?
                   ORDER BY n DESC, content_hash""",
                (min_count,),
            ).fetchall()
        return [
            {"hash": r["content_hash"], "count": r["n"], "paths": r["paths"].split("\x1f")}
            for r in rows
        ]


def _dumps(value) -> str:
    import json

    return value if isinstance(value, str) else json.dumps(value)
