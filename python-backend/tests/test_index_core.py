"""Slice 1 — index-core: schema v3, migrations, content hashing."""

import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import face_row, media_row, write_jpeg


# ---------------------------------------------------------------------------
# hashing
# ---------------------------------------------------------------------------

def test_content_hash_is_sha256_and_stable(lib, tmp_path):
    from hashing import content_hash

    root = lib()
    p = root / "photo_00.jpg"
    expect = hashlib.sha256(p.read_bytes()).hexdigest()
    assert content_hash(str(p)) == expect
    assert content_hash(str(p)) == content_hash(str(p))
    other = tmp_path / "copy.jpg"
    other.write_bytes(p.read_bytes())
    assert content_hash(str(other)) == expect


def test_content_hash_distinguishes_content(lib):
    from hashing import content_hash

    root = lib()
    hashes = {
        content_hash(str(p)) for p in root.rglob("*.jpg")
    }
    assert len(hashes) >= 4  # 3 top-level + 1 nested, all different content


def test_phash_hamming(lib):
    from hashing import perceptual_hash, hamming
    import cv2

    root = lib()
    img_a = cv2.imread(str(root / "photo_00.jpg"))
    h1 = perceptual_hash(img_a)
    h2 = perceptual_hash(img_a)
    assert h1 == h2 and len(h1) == 16
    img_b = cv2.imread(str(root / "photo_01.jpg"))
    assert 0 < hamming(h1, perceptual_hash(img_b)) <= 64


# ---------------------------------------------------------------------------
# store: schema v3 fresh open
# ---------------------------------------------------------------------------

def test_fresh_store_creates_v3_schema(tmp_path):
    from store import Store
    from schema import SCHEMA_VERSION

    s = Store(str(tmp_path / "index.db"))
    with s.connect() as conn:
        version = conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()[0]
    assert int(version) == SCHEMA_VERSION == 3
    tables = {
        "files", "media", "faces", "persons", "albums", "album_items",
        "geonames", "creations", "jobs", "meta", "media_fts",
    }
    with s.connect() as conn:
        present = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            )
        }
    assert tables <= present


def test_meta_roundtrip_and_defaults(tmp_path):
    from store import Store

    s = Store(str(tmp_path / "index.db"))
    assert s.get_meta("nothing") is None
    s.set_meta("k", "v")
    assert s.get_meta("k") == "v"
    s.set_meta("k", "v2")
    assert s.get_meta("k") == "v2"


# ---------------------------------------------------------------------------
# store: file+media upserts, the invariants
# ---------------------------------------------------------------------------

def test_upsert_file_creates_media_once(db, tmp_path):
    p = write_jpeg(tmp_path / "a.jpg")
    from hashing import content_hash

    h = content_hash(str(p))
    db.upsert_media(media_row(h))
    db.upsert_media(media_row(h))  # twice must not duplicate
    db.upsert_file("a.jpg", h, mtime=1.0, size=p.stat().st_size)
    db.upsert_file("a.jpg", h, mtime=2.0, size=p.stat().st_size)

    assert db.media_count() == 1
    with db.connect() as conn:
        rows = [tuple(r) for r in conn.execute(
            "SELECT path, content_hash FROM files"
        )]
    assert rows == [("a.jpg", h)]


def test_two_paths_same_content_share_media(db, tmp_path):
    p = write_jpeg(tmp_path / "a.jpg")
    from hashing import content_hash

    h = content_hash(str(p))
    db.upsert_media(media_row(h))
    db.upsert_file("a.jpg", h, 1.0, 10)
    db.upsert_file("copies/a.jpg", h, 1.0, 10)
    dupes = db.duplicate_groups(min_count=2)
    assert len(dupes) == 1 and dupes[0]["hash"] == h
    assert sorted(dupes[0]["paths"]) == ["a.jpg", "copies/a.jpg"]


def test_remove_file_keeps_media_until_last_reference(db, tmp_path):
    p = write_jpeg(tmp_path / "a.jpg")
    from hashing import content_hash

    h = content_hash(str(p))
    db.upsert_media(media_row(h))
    db.upsert_file("a.jpg", h, 1.0, 10)
    db.upsert_file("b.jpg", h, 1.0, 10)
    db.remove_file("a.jpg")
    assert db.media_count() == 1
    db.remove_file("b.jpg")
    assert db.media_count() == 0  # media GC'd with last file


def test_faces_keyed_by_content_and_index(db):
    db.upsert_media(media_row("h1"))
    db.upsert_media(media_row("h2"))
    db.add_faces("h1", [face_row(), face_row()])
    with db.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM faces WHERE content_hash='h1'"
        ).fetchone()[0] == 2
    db.add_faces("h1", [face_row()])  # replace semantics: 2 rows -> 1
    db.add_faces("h2", [face_row()])

    with db.connect() as conn:
        rows = [tuple(r) for r in conn.execute(
            "SELECT content_hash, face_index FROM faces ORDER BY content_hash, face_index"
        )]
    assert rows == [("h1", 0), ("h2", 0)]


# ---------------------------------------------------------------------------
# migration v2 (current app format) -> v3
# ---------------------------------------------------------------------------

def make_v2_db(path: Path, with_faces=True):
    """Create a database shaped exactly like the shipping v0.2 schema."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE files (
            path TEXT PRIMARY KEY, modified_time REAL NOT NULL,
            size INTEGER NOT NULL, scanned_at REAL NOT NULL);
        CREATE TABLE persons (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT,
            thumbnail_path TEXT, created_at REAL NOT NULL);
        CREATE TABLE faces (
            id INTEGER PRIMARY KEY AUTOINCREMENT, file_path TEXT NOT NULL,
            embedding TEXT NOT NULL, bbox TEXT NOT NULL, det_score REAL,
            thumbnail_path TEXT,
            person_id INTEGER REFERENCES persons(id) ON DELETE SET NULL);
        INSERT INTO meta (key, value) VALUES ('schema_version', '1');
        """
    )
    conn.execute(
        "INSERT INTO persons (name, created_at) VALUES ('Alice', ?)", (time.time(),)
    )
    pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    if with_faces:
        emb = np.linspace(0, 1, 512).tolist()
        conn.execute(
            "INSERT INTO files VALUES ('old.jpg', 123.0, 456, 124.0)", ()
        )
        conn.execute(
            "INSERT INTO faces (file_path, embedding, bbox, det_score, person_id)"
            " VALUES ('old.jpg', ?, '[4,4,20,20]', 0.95, ?)",
            (json.dumps(emb), pid),
        )
    conn.commit()
    conn.close()
    return pid


def test_migration_from_v2_preserves_people_and_faces(tmp_path, lib):
    from schema import SCHEMA_VERSION
    from store import Store

    old_path = tmp_path / "index.db"
    pid = make_v2_db(old_path)
    # The migrated file must exist on disk for hashing: build the library and
    # stage a copy of the exact bytes the v2 row pointed at.
    root = lib()
    src = root / "photo_00.jpg"
    (root / "old.jpg").write_bytes(src.read_bytes())

    s = Store(str(old_path), library_root=str(root))
    with s.connect() as conn:
        version = int(
            conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
        )
        assert version == SCHEMA_VERSION
        # Person survived with name
        person = conn.execute("SELECT id, name FROM persons").fetchone()
        assert person[1] == "Alice"
        # Face survived, now keyed by content hash with its embedding intact
        face = conn.execute(
            "SELECT content_hash, embedding, person_id, face_index FROM faces"
        ).fetchone()
    assert face is not None
    expected_hash = hashlib.sha256((root / "old.jpg").read_bytes()).hexdigest()
    assert face[0] == expected_hash
    assert face[2] == pid
    # The embedding was carried over, not recomputed (identical bytes)
    assert json.loads(face[1]) == np.linspace(0, 1, 512).tolist()


def test_migration_tolerates_missing_files(tmp_path, lib):
    from store import Store

    old_path = tmp_path / "index.db"
    make_v2_db(old_path)  # references old.jpg which will not exist
    root = lib()
    s = Store(str(old_path), library_root=str(root))
    with s.connect() as conn:
        faces = conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0]
        missing = conn.execute(
            "SELECT missing FROM files WHERE path='old.jpg'"
        ).fetchone()
    assert faces == 0
    assert missing is not None and missing[0] == 1


def test_migration_is_idempotent(tmp_path, lib):
    from store import Store

    old_path = tmp_path / "index.db"
    make_v2_db(old_path)
    root = lib()
    s1 = Store(str(old_path), library_root=str(root))
    count1 = s1.media_count()
    s2 = Store(str(old_path), library_root=str(root))
    assert s2.media_count() == count1
