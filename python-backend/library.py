"""Library state service: user-owned item state and collection management.

Everything here mutates the index, never the user's files. "Delete" comes in
two flavors with distinct names on purpose:
  - set_trashed: index-level soft state, fully reversible, auto-purged to the
    OS Recycle Bin after 60 days (Google Photos retention, local reality).
  - delete_from_disk: explicit, confirm-guarded in the UI, and still goes to
    the OS Recycle Bin. There is no code path in this app that unlinks a
    user file without send2trash.
"""

import hashlib
import logging
import os
import secrets
import time
from pathlib import Path

from store import Store

logger = logging.getLogger("FaceFrame.Library")

TRASH_RETENTION_DAYS = 60
# 600k follows current OWASP guidance for PBKDF2-HMAC-SHA256.
PBKDF2_ITERATIONS = 600_000

try:
    from send2trash import send2trash
except ImportError:  # pragma: no cover - dependency listed in requirements
    def send2trash(path: str):
        raise RuntimeError("send2trash is not installed")


class LibraryService:
    def __init__(self, store: Store, library_root: str):
        self.store = store
        self.root = str(Path(library_root).resolve())

    def _contained(self, path: str) -> Path:
        """Resolve a user/renderer-supplied path and refuse anything outside
        the library root — absolute paths, '..' escapes, prefix siblings."""
        candidate = Path(path)
        resolved = (Path(self.root) / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
        root = Path(self.root).resolve()
        if os.path.commonpath([str(root), str(resolved)]) != str(root) or resolved == root:
            raise ValueError(f"Path is outside the library: {path}")
        return resolved

    # ---------------------------------------------------------------- media

    def get_media(self, content_hash: str):
        with self.store.connect() as conn:
            return conn.execute(
                "SELECT * FROM media WHERE content_hash=?", (content_hash,)
            ).fetchone()

    # ------------------------------------------------------ user item state

    def set_favorite(self, content_hashes: list, favorite: bool):
        self._set_media_flag(content_hashes, "favorite", 1 if favorite else 0)

    def set_archived(self, content_hashes: list, archived: bool):
        self._set_media_flag(content_hashes, "archived", 1 if archived else 0)

    def set_locked(self, content_hashes: list, locked: bool):
        if locked and not self.lock_passcode_set():
            raise ValueError("Set a passcode for the locked folder first")
        self._set_media_flag(content_hashes, "locked", 1 if locked else 0)

    def set_caption(self, content_hash: str, caption: str):
        self.store.set_media_user_state(content_hash, caption=caption or None)
        with self.store.connect() as conn:
            paths = [
                r[0]
                for r in conn.execute(
                    "SELECT path FROM files WHERE content_hash=?", (content_hash,)
                )
            ]
        self.store.sync_fts(paths)

    def set_date_override(self, content_hash: str, epoch: float | None):
        self.store.set_media_user_state(
            content_hash, date_override=epoch
        )
        with self.store.connect() as conn:
            paths = [
                r[0]
                for r in conn.execute(
                    "SELECT path FROM files WHERE content_hash=?", (content_hash,)
                )
            ]
        self.store.sync_fts(paths)

    @staticmethod
    def effective_capture_time(media) -> float:
        if media["date_override"] is not None:
            return media["date_override"]
        return media["capture_time"] or 0.0

    def _set_media_flag(self, content_hashes: list, column: str, value: int):
        if not content_hashes:
            return
        with self.store.connect() as conn:
            conn.executemany(
                f"UPDATE media SET {column}=? WHERE content_hash=?",
                [(value, h) for h in content_hashes],
            )

    # ---------------------------------------------------------------- trash

    def set_trashed(
        self,
        content_hashes: list | None = None,
        trashed: bool = True,
        paths: list | None = None,
    ):
        """Trash/restore by content hash, or by file paths when given."""
        stamp = time.time() if trashed else None
        rel_paths = [
            self._contained(p).relative_to(Path(self.root).resolve()).as_posix()
            for p in (paths or [])
        ]
        with self.store.connect() as conn:
            if rel_paths:
                conn.executemany(
                    "UPDATE files SET trashed_at=? WHERE path=?",
                    [(stamp, p) for p in rel_paths],
                )
            if content_hashes:
                conn.executemany(
                    "UPDATE files SET trashed_at=? WHERE content_hash=?",
                    [(stamp, h) for h in content_hashes],
                )
                if not rel_paths:
                    rows = conn.execute(
                        "SELECT path FROM files WHERE content_hash IN (%s)"
                        % ",".join("?" for _ in content_hashes),
                        content_hashes,
                    ).fetchall()
                    rel_paths = [r["path"] for r in rows]
        # Keep full-text aligned with visibility in both directions.
        if rel_paths:
            self.store.sync_fts(rel_paths)

    def trashed_items(self):
        with self.store.connect() as conn:
            return conn.execute(
                """SELECT f.path, f.content_hash, f.trashed_at, m.kind,
                          m.width, m.height
                   FROM files f JOIN media m ON m.content_hash=f.content_hash
                   WHERE f.trashed_at IS NOT NULL
                   ORDER BY f.trashed_at DESC"""
            ).fetchall()

    def purge_expired_trash(self, max_age_days: int = TRASH_RETENTION_DAYS) -> int:
        cutoff = time.time() - max_age_days * 86400
        with self.store.connect() as conn:
            rows = conn.execute(
                """SELECT path FROM files
                   WHERE trashed_at IS NOT NULL AND trashed_at < ?""",
                (cutoff,),
            ).fetchall()
        removed = 0
        for (path,) in rows:
            if self._send_path_to_os_trash(path):
                self.store.remove_file(path)
                removed += 1
        return removed


    def delete_from_disk(self, paths: list) -> int:
        """Explicit, confirm-guarded-in-UI deletion. Every path must resolve
        inside the library AND be indexed AND currently trashed — a renderer
        can never turn this into an arbitrary-file delete."""
        removed = 0
        for path in paths:
            resolved = self._contained(path)
            rel = resolved.relative_to(Path(self.root).resolve()).as_posix()
            state = self.store.get_file_state(rel)
            if not state or not state["trashed_at"]:
                logger.warning("Refusing to delete non-trashed or unindexed path: %s", path)
                continue
            if self._send_path_to_os_trash(rel):
                self.store.remove_file(rel)
                removed += 1
        return removed

    def _send_path_to_os_trash(self, rel_path: str) -> bool:
        try:
            absolute = str(self._contained(rel_path))
        except ValueError as e:
            logger.error("Refusing path outside the library: %s", e)
            return False
        try:
            if os.path.isfile(absolute):
                send2trash(absolute)
            return True
        except Exception as e:
            logger.error("Could not move %s to the OS trash: %s", absolute, e)
            return False

    # --------------------------------------------------------------- locked

    def lock_passcode_set(self) -> bool:
        # Empty strings mean "removed" — only a real salt counts.
        return bool(self.store.get_meta("locked_salt"))

    def set_locked_passcode(self, code: str):
        code = (code or "").strip()
        if not code:
            raise ValueError("Passcode must not be empty")
        salt = secrets.token_hex(16)
        digest = _pbkdf2(code, salt)
        self.store.set_meta("locked_salt", salt)
        self.store.set_meta("locked_hash", digest)

    def verify_locked_passcode(self, code: str) -> bool:
        salt = self.store.get_meta("locked_salt")
        digest = self.store.get_meta("locked_hash")
        if not salt or not digest:
            return False
        return secrets.compare_digest(_pbkdf2(code or "", salt), digest)

    def remove_locked_passcode(self, code: str):
        if not self.verify_locked_passcode(code):
            raise ValueError("Wrong passcode")
        self.store.set_meta("locked_salt", "")
        self.store.set_meta("locked_hash", "")
        # A locked folder without a passcode would orphan items invisibly.
        with self.store.connect() as conn:
            conn.execute("UPDATE media SET locked=0 WHERE locked=1")

    # ----------------------------------------------------------------- views

    def visible_items(self, include_locked: bool = False, include_archived: bool = True):
        """The canonical 'exists and browsable' set: not trashed, not missing,
        not locked (unless the session unlocked)."""
        with self.store.connect() as conn:
            sql = """
                SELECT f.path, f.content_hash, f.kind
                FROM files f JOIN media m ON m.content_hash=f.content_hash
                WHERE f.trashed_at IS NULL AND f.missing=0
            """
            params: list = []
            if not include_locked:
                sql += " AND COALESCE(m.locked,0)=0"
            if not include_archived:
                sql += " AND COALESCE(m.archived,0)=0"
            return conn.execute(sql, params).fetchall()

    def archived_items(self):
        with self.store.connect() as conn:
            return conn.execute(
                """SELECT f.path, f.content_hash, m.kind AS media_kind,
                          COALESCE(m.date_override, m.capture_time, f.mtime) AS ts
                   FROM files f
                   JOIN media m ON m.content_hash=f.content_hash
                   WHERE f.trashed_at IS NULL AND f.missing=0
                     AND COALESCE(m.archived,0)=1"""
            ).fetchall()

    def locked_items(self):
        with self.store.connect() as conn:
            return conn.execute(
                """SELECT f.path, f.content_hash, m.kind AS media_kind,
                          m.width, m.height,
                          COALESCE(m.date_override, m.capture_time, f.mtime) AS ts
                   FROM files f
                   JOIN media m ON m.content_hash=f.content_hash
                   WHERE f.trashed_at IS NULL AND f.missing=0
                     AND COALESCE(m.locked,0)=1"""
            ).fetchall()

    # ---------------------------------------------------------------- albums

    def create_album(self, name: str, description: str | None = None) -> int:
        with self.store.connect() as conn:
            cur = conn.execute(
                "INSERT INTO albums (name, description, created_at) VALUES (?, ?, ?)",
                (name, description, time.time()),
            )
            return cur.lastrowid

    def rename_album(self, album_id: int, name: str):
        with self.store.connect() as conn:
            conn.execute("UPDATE albums SET name=? WHERE id=?", (name, album_id))

    def delete_album(self, album_id: int):
        with self.store.connect() as conn:
            conn.execute("DELETE FROM albums WHERE id=?", (album_id,))

    def set_album_cover(self, album_id: int, content_hash: str):
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE albums SET cover_hash=? WHERE id=?",
                (content_hash, album_id),
            )

    def set_album_sort(self, album_id: int, sort_key: str):
        if sort_key not in ("added", "captured", "filename"):
            raise ValueError(f"Unknown album sort: {sort_key}")
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE albums SET sort_key=? WHERE id=?", (sort_key, album_id)
            )

    def list_albums(self) -> list:
        with self.store.connect() as conn:
            rows = conn.execute(
                """SELECT a.id, a.name, a.description, a.cover_hash, a.sort_key,
                          a.created_at, COUNT(ai.content_hash) AS count
                   FROM albums a
                   LEFT JOIN album_items ai ON ai.album_id = a.id
                   GROUP BY a.id
                   ORDER BY a.created_at DESC, a.id DESC"""
            ).fetchall()
        return [dict(r) for r in rows]

    def add_album_items(self, album_id: int, content_hashes: list):
        with self.store.connect() as conn:
            for content_hash in content_hashes:
                exists = conn.execute(
                    """SELECT 1 FROM album_items
                       WHERE album_id=? AND content_hash=?""",
                    (album_id, content_hash),
                ).fetchone()
                if exists:
                    continue
                max_pos = conn.execute(
                    "SELECT COALESCE(MAX(position), -1) FROM album_items WHERE album_id=?",
                    (album_id,),
                ).fetchone()[0]
                conn.execute(
                    """INSERT INTO album_items (album_id, content_hash, position, added_at)
                       VALUES (?, ?, ?, ?)""",
                    (album_id, content_hash, max_pos + 1, time.time()),
                )

    def remove_album_items(self, album_id: int, content_hashes: list):
        """Membership only: media rows are untouched."""
        with self.store.connect() as conn:
            conn.executemany(
                "DELETE FROM album_items WHERE album_id=? AND content_hash=?",
                [(album_id, h) for h in content_hashes],
            )

    def reorder_album(self, album_id: int, ordered_hashes: list):
        with self.store.connect() as conn:
            for position, content_hash in enumerate(ordered_hashes):
                conn.execute(
                    """UPDATE album_items SET position=?
                       WHERE album_id=? AND content_hash=?""",
                    (position, album_id, content_hash),
                )

    def album_items(self, album_id: int, include_trashed: bool = True) -> list:
        joins = "" if include_trashed else (
            """JOIN files f ON f.content_hash=ai.content_hash AND f.trashed_at IS NULL
                   AND f.missing=0"""
        )
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""SELECT ai.content_hash, ai.position, ai.added_at
                    FROM album_items ai {joins}
                    WHERE ai.album_id=?
                    ORDER BY ai.position, ai.added_at""",
                (album_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- stats

    def storage_stats(self) -> dict:
        data_dir = Path(self.root) / ".faceframe"
        with self.store.connect() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS n, COALESCE(SUM(f.size),0) AS bytes
                   FROM files f
                   WHERE f.missing=0 AND f.trashed_at IS NULL
                     AND f.kind != 'creation'"""
            ).fetchone()
        caches = {}
        for name in ("thumbnails", "previews", "posters", "creations"):
            caches[name] = _dir_size(data_dir / name)
        db_path = data_dir / "index.db"
        return {
            "items": {"count": row["n"], "bytes": row["bytes"]},
            "index_bytes": db_path.stat().st_size if db_path.exists() else 0,
            "caches": caches,
        }


def _pbkdf2(code: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", code.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS
    ).hex()


def _dir_size(path: Path) -> int:
    total = 0
    if path.is_dir():
        for f in path.rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
    return total
