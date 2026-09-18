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
    "exif", "phash", "labels", "flags", "poster_path", "analysis_state",
    "analyzed_at",
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
        archived=...,
        locked=...,
    ):
        """Update user-owned columns; sentinel ``...`` means leave unchanged."""
        fields = {
            "caption": caption,
            "edit": edit,
            "date_override": date_override,
            "favorite": favorite,
            "archived": archived,
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
            # A live row for content H means any missing ghost of H was a
            # move, not a deletion: the old path row must not linger in the
            # missing list forever.
            conn.execute(
                """DELETE FROM files
                   WHERE content_hash=? AND missing=1 AND path != ?""",
                (content_hash, path),
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

    def all_file_states(self) -> dict:
        """path -> state row, loaded once per scan pass (the discovery loop
        would otherwise open two connections per file)."""
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT f.path, f.content_hash, f.kind, f.size, f.mtime,
                          f.missing, f.added_at, f.paired_path,
                          m.analysis_state
                   FROM files f LEFT JOIN media m
                     ON m.content_hash = f.content_hash"""
            ).fetchall()
        return {r["path"]: r for r in rows}

    def get_file_state(self, path: str):
        with self.connect() as conn:
            return conn.execute(
                """SELECT path, content_hash, kind, size, mtime, missing, added_at,
                          trashed_at, paired_path
                   FROM files WHERE path=?""",
                (path,),
            ).fetchone()

    def geohashes_for_names(self, names: list) -> list:
        """Geohash cells whose cached place name matches any given string."""
        if not names:
            return []
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT geohash FROM geonames WHERE name IN (%s)"
                % ",".join("?" for _ in names),
                names,
            ).fetchall()
        return [r[0] for r in rows]

    def get_media(self, content_hash: str):
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM media WHERE content_hash=?", (content_hash,)
            ).fetchone()

    def migrate_media_state(self, old_hash: str, new_hash: str):
        """A file's content changed in place (edited). Carry user state and
        album membership to the new content so the user's intent follows the
        logical item, then drop the old media row if it is now unreferenced."""
        if old_hash == new_hash:
            return
        with self.connect() as conn:
            # The new content usually has no media row yet (it is about to be
            # processed): seed it with the old row's user-owned columns or
            # the UPDATE below is a no-op and favorite/caption are lost.
            # analysis_state is deliberately NOT carried — the new bytes
            # still need a full decode.
            conn.execute(
                """INSERT INTO media (content_hash, caption, edit, date_override,
                                      favorite, archived, locked)
                   SELECT ?, caption, edit, date_override, favorite, archived, locked
                   FROM media WHERE content_hash=?
                   ON CONFLICT(content_hash) DO NOTHING""",
                (new_hash, old_hash),
            )
            conn.execute(
                """UPDATE media SET
                       caption=(SELECT caption FROM media WHERE content_hash=?),
                       edit=(SELECT edit FROM media WHERE content_hash=?),
                       date_override=(SELECT date_override FROM media WHERE content_hash=?),
                       favorite=COALESCE((SELECT favorite FROM media WHERE content_hash=?), 0),
                       archived=COALESCE((SELECT archived FROM media WHERE content_hash=?), 0),
                       locked=COALESCE((SELECT locked FROM media WHERE content_hash=?), 0)
                   WHERE content_hash=?""",
                (old_hash, old_hash, old_hash, old_hash, old_hash, old_hash, new_hash),
            )
            conn.execute(
                """UPDATE OR IGNORE album_items SET content_hash=?
                   WHERE content_hash=?""",
                (new_hash, old_hash),
            )
            conn.execute(
                "DELETE FROM album_items WHERE content_hash=?", (old_hash,)
            )
        self.prune_orphan_media([old_hash])

    def prune_orphan_media(self, only_hashes: list | None = None) -> int:
        """Delete media rows no file references anymore (faces cascade)."""
        with self.connect() as conn:
            if only_hashes:
                removed = 0
                for h in only_hashes:
                    refs = conn.execute(
                        "SELECT COUNT(*) FROM files WHERE content_hash=?", (h,)
                    ).fetchone()[0]
                    if refs == 0:
                        removed += conn.execute(
                            "DELETE FROM media WHERE content_hash=?", (h,)
                        ).rowcount
                return removed
            cur = conn.execute(
                """DELETE FROM media WHERE content_hash NOT IN
                   (SELECT DISTINCT content_hash FROM files
                    WHERE content_hash IS NOT NULL)"""
            )
            return cur.rowcount

    def known_hashes(self) -> set:
        with self.connect() as conn:
            return {
                r[0]
                for r in conn.execute("SELECT content_hash FROM media")
            }

    def referenced_face_thumbs(self) -> list:
        with self.connect() as conn:
            return [
                r[0]
                for r in conn.execute(
                    """SELECT DISTINCT thumbnail_path FROM faces
                       WHERE thumbnail_path IS NOT NULL"""
                )
            ]

    def set_motion_pairs(self, updates: list, seen_paths: set | None = None):
        """Write (path, paired_path) rows for this pass's motion pairs and
        clear pairings whose sibling vanished (recomputed from scratch every
        pass, so a stale pair never survives its sibling)."""
        with self.connect() as conn:
            conn.executemany(
                "UPDATE files SET paired_path=? WHERE path=?",
                [(pair, path) for path, pair in updates],
            )
            if seen_paths:
                update_paths = {path for path, _ in updates}
                stale = [
                    (path,)
                    for (path,) in conn.execute(
                        """SELECT path FROM files
                           WHERE paired_path IS NOT NULL"""
                    )
                    if path in seen_paths and path not in update_paths
                ]
                conn.executemany(
                    "UPDATE files SET paired_path=NULL WHERE path=?", stale
                )

    def mark_missing(self, seen_paths: set) -> int:
        """Flag indexed files not seen in this pass; never auto-delete.
        A vanished path whose content is still alive at another path is a
        MOVE: its ghost row is removed instead of being flagged missing.
        Creations live inside .faceframe (outside the walk) by design."""
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT path, content_hash FROM files
                   WHERE missing=0 AND kind != 'creation'"""
            ).fetchall()
            gone = [r["path"] for r in rows if r["path"] not in seen_paths]
            hash_by_path = {r["path"]: r["content_hash"] for r in rows}
            moved = []
            really_gone = []
            for path in gone:
                content_hash = hash_by_path.get(path)
                if content_hash:
                    live_elsewhere = conn.execute(
                        """SELECT COUNT(*) FROM files
                           WHERE content_hash=? AND missing=0 AND path != ?""",
                        (content_hash, path),
                    ).fetchone()[0]
                    if live_elsewhere:
                        moved.append(path)
                        continue
                really_gone.append(path)
            if moved:
                conn.executemany(
                    "DELETE FROM files WHERE path=?", [(p,) for p in moved]
                )
            if really_gone:
                conn.executemany(
                    "UPDATE files SET missing=1 WHERE path=?",
                    [(p,) for p in really_gone],
                )
            return len(really_gone)

    # -- full-text ---------------------------------------------------------

    def sync_fts(self, changed_paths: list | None = None):
        """Reindex full-text rows. With no argument, reindexes everything
        (used at scan end); otherwise only the given file paths."""
        with self.connect() as conn:
            if changed_paths is None:
                conn.execute("DELETE FROM fts_map")
                conn.execute("DELETE FROM media_fts")
                rows = conn.execute(
                    """SELECT f.path, f.content_hash, m.caption, m.labels
                       FROM files f JOIN media m ON m.content_hash=f.content_hash
                       WHERE f.missing=0 AND f.trashed_at IS NULL"""
                ).fetchall()
            else:
                rows = []
                for path in changed_paths:
                    old = conn.execute(
                        "SELECT rowid FROM fts_map WHERE path=?", (path,)
                    ).fetchone()
                    if old:
                        conn.execute("DELETE FROM media_fts WHERE rowid=?", (old[0],))
                    conn.execute("DELETE FROM fts_map WHERE path=?", (path,))
                    row = conn.execute(
                        """SELECT f.path, f.content_hash, m.caption, m.labels
                           FROM files f JOIN media m ON m.content_hash=f.content_hash
                           WHERE f.path=? AND f.missing=0 AND f.trashed_at IS NULL""",
                        (path,),
                    ).fetchone()
                    if row:
                        rows.append(row)
            for row in rows:
                text = _fts_text(row["path"], row["caption"], row["labels"])
                cur = conn.execute(
                    "INSERT INTO fts_map (path, content_hash) VALUES (?, ?)",
                    (row["path"], row["content_hash"]),
                )
                conn.execute(
                    "INSERT INTO media_fts (rowid, text) VALUES (?, ?)",
                    (cur.lastrowid, text),
                )

    def search_text(self, query: str) -> list:
        """FTS lookup over captions, labels and filenames; returns file hits."""
        terms = [t for t in query.replace('"', " ").split() if t]
        if not terms:
            return []
        match = " AND ".join(f'"{t}"' for t in terms)
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT m.path, m.content_hash
                   FROM media_fts fts
                   JOIN fts_map m ON m.rowid = fts.rowid
                   WHERE media_fts MATCH ?
                   LIMIT 500""",
                (match,),
            ).fetchall()
        return [{"path": r["path"], "content_hash": r["content_hash"]} for r in rows]

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

    # -- persons / faces (v3) ---------------------------------------------

    def all_faces_for_clustering(self):
        with self.connect() as conn:
            return conn.execute(
                """SELECT id, content_hash, embedding, bbox, person_id,
                          thumbnail_path
                   FROM faces"""
            ).fetchall()

    def faces_by_ids(self, face_ids: list):
        with self.connect() as conn:
            return conn.execute(
                """SELECT id, bbox, thumbnail_path FROM faces
                   WHERE id IN (%s)""" % ",".join("?" for _ in face_ids),
                face_ids,
            ).fetchall()

    def person_face_ids(self, person_id: int) -> list:
        with self.connect() as conn:
            return [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM faces WHERE person_id=?", (person_id,)
                )
            ]

    def set_faces_person(self, face_ids: list, person_id: int | None):
        if not face_ids:
            return
        with self.connect() as conn:
            conn.executemany(
                "UPDATE faces SET person_id=? WHERE id=?",
                [(person_id, fid) for fid in face_ids],
            )

    def persons_with_counts(self, include_hidden: bool = False) -> list:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT p.id, p.name, p.thumbnail_path, p.hidden,
                          COUNT(f.id) AS face_count
                   FROM persons p
                   JOIN faces f ON f.person_id = p.id
                   GROUP BY p.id
                   ORDER BY face_count DESC, p.id"""
            ).fetchall()
        return [
            dict(r)
            for r in rows
            if include_hidden or not r["hidden"]
        ]

    def get_person(self, person_id: int):
        with self.connect() as conn:
            row = conn.execute(
                """SELECT p.id, p.name, p.thumbnail_path, p.hidden,
                          COUNT(f.id) AS face_count
                   FROM persons p LEFT JOIN faces f ON f.person_id = p.id
                   WHERE p.id=? GROUP BY p.id""",
                (person_id,),
            ).fetchone()
        return dict(row) if row else None

    def person_names(self):
        with self.connect() as conn:
            return conn.execute("SELECT id, name FROM persons").fetchall()

    def create_person(self, name: str | None = None) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO persons (name, created_at) VALUES (?, ?)",
                (name, time.time()),
            )
            return cur.lastrowid

    def rename_person(self, person_id: int, name: str):
        with self.connect() as conn:
            conn.execute(
                "UPDATE persons SET name=? WHERE id=?", (name, person_id)
            )

    def set_person_hidden(self, person_id: int, hidden: bool):
        with self.connect() as conn:
            conn.execute(
                "UPDATE persons SET hidden=? WHERE id=?",
                (1 if hidden else 0, person_id),
            )

    def update_person_thumbnail(self, person_id: int, thumbnail_path: str):
        with self.connect() as conn:
            conn.execute(
                "UPDATE persons SET thumbnail_path=? WHERE id=?",
                (thumbnail_path, person_id),
            )

    def merge_persons(self, keep_id: int, merge_id: int):
        """Faces move to keep_id, merge_id is removed. Returns the removed
        person's thumbnail path (regenerable cache data) for cleanup."""
        with self.connect() as conn:
            stale = conn.execute(
                "SELECT thumbnail_path FROM persons WHERE id=?", (merge_id,)
            ).fetchone()
            keep = conn.execute(
                "SELECT thumbnail_path FROM persons WHERE id=?", (keep_id,)
            ).fetchone()
            conn.execute(
                "UPDATE faces SET person_id=? WHERE person_id=?",
                (keep_id, merge_id),
            )
            conn.execute("DELETE FROM persons WHERE id=?", (merge_id,))
        stale_path = stale[0] if stale else None
        keep_path = keep[0] if keep else None
        return stale_path if stale_path and stale_path != keep_path else None

    def delete_empty_persons(self) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """DELETE FROM persons
                   WHERE id NOT IN
                     (SELECT DISTINCT person_id FROM faces
                      WHERE person_id IS NOT NULL)"""
            )
            return cur.rowcount

    def person_photos(self, person_id: int) -> list:
        """Distinct visible images containing this person, newest first."""
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT f.path, f.content_hash, COUNT(fa.id) AS face_count,
                          COALESCE(m.date_override, m.capture_time) AS ts
                   FROM faces fa
                   JOIN files f ON f.content_hash = fa.content_hash
                   JOIN media m ON m.content_hash = fa.content_hash
                   WHERE fa.person_id = ?
                     AND f.missing=0 AND f.trashed_at IS NULL
                   GROUP BY f.path
                   ORDER BY ts DESC""",
                (person_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def person_faces(self, person_id: int) -> list:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT fa.id, fa.content_hash, fa.bbox, fa.thumbnail_path
                   FROM faces fa
                   WHERE fa.person_id=?
                   ORDER BY fa.id""",
                (person_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def unclustered_faces(self, limit: int = 500) -> list:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT fa.id, fa.content_hash, fa.bbox, fa.thumbnail_path,
                          MIN(f.path) AS file_path
                   FROM faces fa
                   LEFT JOIN files f ON f.content_hash = fa.content_hash
                   WHERE fa.person_id IS NULL
                   GROUP BY fa.id
                   ORDER BY fa.id
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def next_auto_person_name(self) -> str:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT name FROM persons WHERE name GLOB 'Person [0-9]*'"
            ).fetchall()
        highest = 0
        for (name,) in rows:
            try:
                highest = max(highest, int(name.split()[-1]))
            except ValueError:
                continue
        return f"Person {highest + 1}"

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


def _fts_text(path: str, caption: str | None, labels: str | None) -> str:
    """One FTS document per file: caption + labels + its path components."""
    import json
    from pathlib import PurePosixPath

    parts = []
    if caption:
        parts.append(caption)
    if labels:
        try:
            parts.extend(json.loads(labels))
        except (TypeError, ValueError):
            pass
    parts.append(path.replace("/", " "))
    stem = PurePosixPath(path).stem.replace("_", " ").replace("-", " ")
    parts.append(stem)
    return " ".join(parts)
