import json
import logging
import sqlite3
import time
from contextlib import contextmanager

logger = logging.getLogger("FaceFrame.Database")

SCHEMA_VERSION = 1


class Database:
    """SQLite storage for one photo library.

    The database lives inside the library's ``.faceframe`` folder, so each
    scanned folder is fully self-contained: delete that folder and every
    trace of FaceFrame is gone.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _connect(self):
        """Yield a connection inside a transaction, always closing it.

        Connections must not outlive a call: on Windows an open handle
        would block deleting the library's .faceframe folder.
        """
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        try:
            # WAL lets the scan thread write while the UI reads without
            # tripping over database locks.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.row_factory = sqlite3.Row
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );

                CREATE TABLE IF NOT EXISTS files (
                    path TEXT PRIMARY KEY,
                    modified_time REAL NOT NULL,
                    size INTEGER NOT NULL,
                    scanned_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS persons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    thumbnail_path TEXT,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS faces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT NOT NULL,
                    embedding TEXT NOT NULL,
                    bbox TEXT NOT NULL,
                    det_score REAL,
                    thumbnail_path TEXT,
                    person_id INTEGER REFERENCES persons(id) ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_faces_person ON faces(person_id);
                CREATE INDEX IF NOT EXISTS idx_faces_file ON faces(file_path);
                """
            )
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )

    # -- files ----------------------------------------------------------

    def get_file(self, path: str):
        with self._connect() as conn:
            return conn.execute(
                "SELECT path, modified_time, size FROM files WHERE path = ?", (path,)
            ).fetchone()

    def upsert_file(self, path: str, modified_time: float, size: int):
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO files (path, modified_time, size, scanned_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    modified_time = excluded.modified_time,
                    size = excluded.size,
                    scanned_at = excluded.scanned_at
                """,
                (path, modified_time, size, time.time()),
            )

    def all_file_paths(self):
        with self._connect() as conn:
            return [r[0] for r in conn.execute("SELECT path FROM files")]

    def remove_file(self, path: str):
        with self._connect() as conn:
            conn.execute("DELETE FROM faces WHERE file_path = ?", (path,))
            conn.execute("DELETE FROM files WHERE path = ?", (path,))

    # -- faces ----------------------------------------------------------

    def add_faces(self, file_path: str, faces: list):
        rows = [
            (
                file_path,
                json.dumps(f["embedding"]),
                json.dumps(f["bbox"]),
                f.get("det_score"),
                f.get("thumbnail"),
            )
            for f in faces
        ]
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO faces (file_path, embedding, bbox, det_score, thumbnail_path)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )

    def remove_faces_for_file(self, path: str):
        """Drop face rows for one image; returns their thumbnail paths (relative)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT thumbnail_path FROM faces WHERE file_path = ? AND thumbnail_path IS NOT NULL",
                (path,),
            ).fetchall()
            conn.execute("DELETE FROM faces WHERE file_path = ?", (path,))
        return [r[0] for r in rows]

    def all_faces(self):
        """Every face with its embedding, for clustering."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT id, embedding, bbox, person_id, thumbnail_path FROM faces"
            ).fetchall()

    def unclustered_faces(self):
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT id, file_path, bbox, thumbnail_path
                FROM faces
                WHERE person_id IS NULL
                ORDER BY id
                """
            ).fetchall()

    def set_faces_person(self, face_ids, person_id):
        with self._connect() as conn:
            conn.executemany(
                "UPDATE faces SET person_id = ? WHERE id = ?",
                [(person_id, fid) for fid in face_ids],
            )

    def set_faces_unassigned(self, face_ids):
        self.set_faces_person(face_ids, None)

    # -- persons ----------------------------------------------------------

    def persons_with_counts(self):
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT p.id, p.name, p.thumbnail_path, COUNT(f.id) AS face_count
                FROM persons p
                LEFT JOIN faces f ON f.person_id = p.id
                GROUP BY p.id
                HAVING face_count > 0
                ORDER BY face_count DESC, p.id
                """
            ).fetchall()

    def person_names(self):
        with self._connect() as conn:
            return conn.execute("SELECT id, name FROM persons").fetchall()

    def update_person_thumbnail(self, person_id: int, thumbnail_path: str):
        with self._connect() as conn:
            conn.execute(
                "UPDATE persons SET thumbnail_path = ? WHERE id = ?",
                (thumbnail_path, person_id),
            )

    def create_person(self, name=None) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO persons (name, created_at) VALUES (?, ?)",
                (name, time.time()),
            )
            return cur.lastrowid

    def rename_person(self, person_id: int, new_name: str):
        with self._connect() as conn:
            conn.execute(
                "UPDATE persons SET name = ? WHERE id = ?", (new_name, person_id)
            )

    def merge_persons(self, keep_id: int, merge_id: int):
        """Move every face of merge_id under keep_id; returns the removed
        person's thumbnail path (relative) so the caller can clean up."""
        with self._connect() as conn:
            stale_thumb = conn.execute(
                "SELECT thumbnail_path FROM persons WHERE id = ?", (merge_id,)
            ).fetchone()
            keep_thumb = conn.execute(
                "SELECT thumbnail_path FROM persons WHERE id = ?", (keep_id,)
            ).fetchone()
            conn.execute(
                "UPDATE faces SET person_id = ? WHERE person_id = ?",
                (keep_id, merge_id),
            )
            conn.execute("DELETE FROM persons WHERE id = ?", (merge_id,))
        if (
            stale_thumb
            and stale_thumb[0]
            and stale_thumb[0] != (keep_thumb[0] if keep_thumb else None)
        ):
            return stale_thumb[0]
        return None

    def delete_empty_persons(self) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                DELETE FROM persons
                WHERE id NOT IN (SELECT DISTINCT person_id FROM faces WHERE person_id IS NOT NULL)
                """
            )
            return cur.rowcount

    def person_photos(self, person_id: int):
        """Distinct images that contain this person, newest scan first."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT f.file_path, COUNT(f.id) AS face_count
                FROM faces f
                WHERE f.person_id = ?
                GROUP BY f.file_path
                ORDER BY f.file_path
                """,
                (person_id,),
            ).fetchall()
        return [{"path": r[0], "face_count": r[1]} for r in rows]

    def next_auto_person_name(self) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT name FROM persons WHERE name GLOB 'Person [0-9]*'"
            ).fetchall()
        highest = 0
        for (name,) in row:
            try:
                highest = max(highest, int(name.split()[-1]))
            except ValueError:
                continue
        return f"Person {highest + 1}"
