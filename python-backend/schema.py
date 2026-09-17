"""Schema v3 and the migration chain.

The index is content-addressed: ``media`` holds one row per unique byte
sequence and owns every expensive derived artifact (faces, labels, exif,
poster, user edits); ``files`` maps real paths onto media rows. A v1/v2
database (path-addressed, FaceFrame <= 0.2) migrates in place: existing face
embeddings are carried over by re-hashing the indexed files, so no face
inference ever re-runs and person names/assignments survive.

Migration contract: index state may lag disk, never contradict it. Files that
cannot be hashed during migration become ``missing`` rows instead of being
dropped.
"""

import logging
import sqlite3
from pathlib import Path

import pathio

logger = logging.getLogger("FaceFrame.Schema")

SCHEMA_VERSION = 3

V3_DDL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- One row per unique content (bytes). Expensive work happens once per row.
CREATE TABLE IF NOT EXISTS media (
    content_hash TEXT PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT 'photo',      -- photo | video
    width INTEGER,
    height INTEGER,
    duration REAL,                            -- seconds, videos
    capture_time REAL,                        -- epoch seconds (EXIF or mtime)
    tz_offset TEXT,                           -- "+02:00" style, or NULL
    exif TEXT,                                -- JSON camera/lens/gps blob
    phash TEXT,                               -- 16 hex chars, photos
    labels TEXT,                              -- JSON array of strings
    caption TEXT,
    edit TEXT,                                -- JSON editor params
    date_override REAL,                       -- user-corrected capture time
    favorite INTEGER NOT NULL DEFAULT 0,
    locked INTEGER NOT NULL DEFAULT 0,
    poster_path TEXT,                         -- relative cache path
    analysis_state TEXT NOT NULL DEFAULT 'none',  -- none|hashed|analyzed
    analyzed_at REAL
);

-- One row per real file. Anchors every item query: an item exists in a view
-- iff it has a file row here (and not trashed, per query filters).
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,                    -- library-relative posix
    content_hash TEXT REFERENCES media(content_hash) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'photo',       -- photo | video | creation
    size INTEGER NOT NULL DEFAULT 0,
    mtime REAL NOT NULL DEFAULT 0,
    missing INTEGER NOT NULL DEFAULT 0,
    last_seen REAL,
    added_at REAL NOT NULL DEFAULT 0,
    trashed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_files_hash ON files(content_hash);
CREATE INDEX IF NOT EXISTS idx_files_capture ON files(added_at);
CREATE INDEX IF NOT EXISTS idx_files_trashed ON files(trashed_at);

CREATE TABLE IF NOT EXISTS persons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    thumbnail_path TEXT,
    hidden INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS faces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash TEXT NOT NULL REFERENCES media(content_hash) ON DELETE CASCADE,
    face_index INTEGER NOT NULL,
    bbox TEXT NOT NULL,
    embedding TEXT NOT NULL,
    det_score REAL,
    thumbnail_path TEXT,
    person_id INTEGER REFERENCES persons(id) ON DELETE SET NULL,
    hidden INTEGER NOT NULL DEFAULT 0,
    UNIQUE(content_hash, face_index)
);
CREATE INDEX IF NOT EXISTS idx_faces_person ON faces(person_id);

CREATE TABLE IF NOT EXISTS albums (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    cover_hash TEXT REFERENCES media(content_hash) ON DELETE SET NULL,
    sort_key TEXT NOT NULL DEFAULT 'added',   -- added | captured | filename
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS album_items (
    album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    content_hash TEXT NOT NULL REFERENCES media(content_hash) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    added_at REAL NOT NULL,
    UNIQUE(album_id, content_hash)
);

CREATE TABLE IF NOT EXISTS geonames (
    geohash TEXT PRIMARY KEY,
    name TEXT,
    fetched_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS creations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                       -- collage | animation | export
    path TEXT NOT NULL,                       -- library-relative
    content_hash TEXT REFERENCES media(content_hash) ON DELETE CASCADE,
    params TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    state TEXT NOT NULL,                      -- running|done|cancelled|error
    stats TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS media_fts USING fts5(
    content_hash UNINDEXED,
    text,
    tokenize='unicode61 remove_diacritics 2'
);
"""


def ensure_schema(conn: sqlite3.Connection, library_root: str | None = None):
    """Bring a database to SCHEMA_VERSION, creating or migrating as needed."""
    conn.execute("PRAGMA foreign_keys=ON")
    version = _detect_version(conn)
    if version == SCHEMA_VERSION:
        return
    if version == 0:
        conn.executescript(V3_DDL)
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        return
    if version in (1, 2):
        _migrate_v1_v2_to_v3(conn, library_root)
        return
    raise RuntimeError(f"Unknown index schema version: {version}")


def _detect_version(conn: sqlite3.Connection) -> int:
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "meta" not in tables:
        # A pre-v3 database has files/persons/faces but no meta table.
        if "files" in tables and "faces" in tables:
            return 1
        return 0
    row = conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()
    return int(row[0]) if row else 1


def _migrate_v1_v2_to_v3(conn: sqlite3.Connection, library_root: str | None):
    logger.info("Migrating index schema v1/v2 -> v3")
    for old in ("files", "faces", "persons"):
        conn.execute(f"ALTER TABLE {old} RENAME TO _v2_{old}")
    # DROP the v1 indexes: they belong to the renamed tables and would clash
    # with the fresh names.
    for idx in ("idx_faces_person", "idx_faces_file"):
        conn.execute(f"DROP INDEX IF EXISTS {idx}")

    conn.executescript(V3_DDL)

    conn.execute(
        """INSERT INTO persons (id, name, thumbnail_path, hidden, created_at)
           SELECT id, name, thumbnail_path, 0, created_at FROM _v2_persons"""
    )

    hashed: dict[str, str | None] = {}  # old rel path -> content hash (None=missing)
    for (rel_path,) in conn.execute("SELECT path FROM _v2_files"):
        hashed[rel_path] = _hash_if_present(rel_path, library_root)

    faces_by_file: dict[str, list[sqlite3.Row]] = {}
    for row in conn.execute(
        """SELECT id, file_path, embedding, bbox, det_score, thumbnail_path, person_id
           FROM _v2_faces ORDER BY id"""
    ):
        faces_by_file.setdefault(row["file_path"], []).append(row)

    media_seen: set[str] = set()
    for rel_path, hash_hex in hashed.items():
        stat = conn.execute(
            "SELECT modified_time, size, scanned_at FROM _v2_files WHERE path=?",
            (rel_path,),
        ).fetchone()
        if hash_hex is not None:
            if hash_hex not in media_seen:
                conn.execute(
                    """INSERT INTO media (content_hash, kind, analysis_state, analyzed_at)
                       VALUES (?, 'photo', 'analyzed', ?)""",
                    (hash_hex, stat["scanned_at"]),
                )
                media_seen.add(hash_hex)
            conn.execute(
                """INSERT INTO files (path, content_hash, kind, size, mtime,
                                      missing, last_seen, added_at)
                   VALUES (?, ?, 'photo', ?, ?, 0, ?, ?)""",
                (rel_path, hash_hex, stat["size"], stat["modified_time"],
                 stat["scanned_at"], stat["scanned_at"]),
            )
            for face_index, face in enumerate(faces_by_file.get(rel_path, [])):
                conn.execute(
                    """INSERT INTO faces (content_hash, face_index, bbox, embedding,
                                          det_score, thumbnail_path, person_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (hash_hex, face_index, face["bbox"], face["embedding"],
                     face["det_score"], face["thumbnail_path"], face["person_id"]),
                )
        else:
            # Unhashable (file gone or path predates relative keys): keep the
            # row as missing so the user can resolve it; faces cannot survive
            # without content to anchor them.
            conn.execute(
                """INSERT INTO files (path, content_hash, kind, size, mtime,
                                      missing, last_seen, added_at)
                   VALUES (?, NULL, 'photo', ?, ?, 1, ?, ?)""",
                (rel_path, stat["size"], stat["modified_time"],
                 stat["scanned_at"], stat["scanned_at"]),
            )

    conn.execute(
        "UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),)
    )
    conn.execute("DROP TABLE _v2_faces")
    conn.execute("DROP TABLE _v2_files")
    conn.execute("DROP TABLE _v2_persons")
    logger.info(
        "Migration done: %d media, %d files, %d faces",
        len(media_seen),
        len(hashed),
        conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0],
    )


def _hash_if_present(rel_path: str, library_root: str | None) -> str | None:
    if not library_root:
        return None
    from hashing import content_hash

    absolute = pathio.to_absolute(rel_path, library_root)
    try:
        if Path(absolute).is_file():
            return content_hash(absolute)
    except OSError:
        pass
    return None
