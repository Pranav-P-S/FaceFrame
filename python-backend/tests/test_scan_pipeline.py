"""Slice 2 — scan pipeline: idempotent, content-addressed, videos, exif, GC.

The face engine is injected (a stub) so unit tests never load real models.
"""

import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import face_row, write_jpeg


class StubFaceEngine:
    """Deterministic stand-in for the InsightFace processor."""

    def __init__(self, faces_per_call=1):
        self.calls = 0
        self.faces_per_call = faces_per_call
        self.thumb_dir = None

    def process_decoded(self, path, img, face_key=None):
        self.calls += 1
        faces = []
        for idx in range(self.faces_per_call):
            thumb = None
            if self.thumb_dir and face_key:
                thumb_path = os.path.join(self.thumb_dir, f"{face_key}#{idx}.jpg")
                Path(thumb_path).write_bytes(b"fake-thumb")
                thumb = thumb_path
            faces.append(
                {
                    "embedding": np.linspace(0, 1, 8).tolist(),
                    "bbox": [1, 1, 30, 30],
                    "det_score": 0.9,
                    "thumbnail": thumb,
                }
            )
        return faces


class StubLabeler:
    def __init__(self, labels=None):
        self.labels = labels or []
        self.calls = 0

    def label(self, img):
        self.calls += 1
        return list(self.labels)


@pytest.fixture
def scan(lib, tmp_path):
    def make(root=None, faces_per_file=1, labels=None, enable_faces=True):
        from scan import ScanPipeline

        root = root or lib()
        engine = StubFaceEngine(faces_per_file)
        engine.thumb_dir = str(root / ".faceframe" / "thumbnails")
        engine.thumb_dir and os.makedirs(engine.thumb_dir, exist_ok=True)
        labeler = StubLabeler(labels)
        pipeline = ScanPipeline(
            str(root / ".faceframe" / "index.db"),
            str(root),
            face_engine=engine if enable_faces else None,
            labeler=labeler,
        )
        return pipeline, root, engine

    return make


def test_fresh_scan_indexes_everything(scan):
    pipeline, root, engine = scan()
    stats = pipeline.run()
    assert stats["files_total"] == 4  # 3 photos + 1 nested
    assert stats["decoded"] == 4
    assert stats["faces"] == 4
    assert stats["videos"] == 0
    assert engine.calls == 4
    with pipeline.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 4
        assert conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0] == 4


def test_rescan_is_idempotent_zero_decodes(scan):
    """The user-mandated proof: scanning an unchanged library decodes nothing."""
    pipeline, root, engine = scan()
    pipeline.run()
    calls_after_first = engine.calls
    stats2 = pipeline.run()
    assert stats2["decoded"] == 0
    assert stats2["hashed"] == 0
    assert engine.calls == calls_after_first
    assert stats2["skipped_unchanged"] == 4


def test_touch_updates_row_without_recompute(scan):
    pipeline, root, engine = scan()
    pipeline.run()
    target = root / "photo_00.jpg"
    stat = os.stat(target)
    os.utime(target, (stat.st_atime, stat.st_mtime + 100))
    stats = pipeline.run()
    assert stats["decoded"] == 0
    assert stats["hashed"] == 1  # stat changed -> verify content hash
    assert stats["content_unchanged"] == 1  # bytes identical: no decode


def test_move_file_keeps_faces_zero_decodes(scan):
    """Rename/move: hash hit -> zero decode, faces and assignments survive."""
    pipeline, root, engine = scan(faces_per_file=1)
    pipeline.run()
    with pipeline.store.connect() as conn:
        cur = conn.execute(
            "INSERT INTO persons (name, created_at) VALUES ('Alice', ?)", (time.time(),)
        )
        person_id = cur.lastrowid
        conn.execute("UPDATE faces SET person_id=?", (person_id,))
    (root / "photo_00.jpg").rename(root / "renamed.jpg")
    stats = pipeline.run()
    assert stats["decoded"] == 0
    with pipeline.store.connect() as conn:
        row = conn.execute(
            """SELECT f.path, m.analysis_state FROM files f
               JOIN media m ON m.content_hash = f.content_hash
               JOIN faces fa ON fa.content_hash = f.content_hash
               WHERE fa.person_id = ? AND f.missing = 0""",
            (person_id,),
        ).fetchall()
    # Every surviving file kept its assignment; the renamed file is among
    # them and the vanished path is not.
    paths = {r["path"] for r in row}
    assert "renamed.jpg" in paths and "photo_00.jpg" not in paths
    assert all(r["analysis_state"] == "analyzed" for r in row)
    # The face thumbnail (keyed by content) still exists on disk.
    with pipeline.store.connect() as conn:
        thumb = conn.execute("SELECT thumbnail_path FROM faces").fetchone()[0]
    assert thumb and (root / thumb).is_file()


def test_edited_file_reprocesses_and_old_media_gcs(scan):
    pipeline, root, engine = scan()
    pipeline.run()
    before = pipeline.store.media_count()
    write_jpeg(root / "photo_00.jpg", color=(1, 2, 3))  # new bytes, same path
    stats = pipeline.run()
    assert stats["decoded"] == 1
    assert pipeline.store.media_count() == before  # old media row replaced+GC'd


def test_deleted_file_becomes_missing_then_relinks(scan):
    pipeline, root, engine = scan()
    pipeline.run()
    victim = root / "photo_01.jpg"
    victim.unlink()
    stats = pipeline.run()
    assert stats["missing_now"] == 1
    with pipeline.store.connect() as conn:
        missing = conn.execute(
            "SELECT missing FROM files WHERE path='photo_01.jpg'"
        ).fetchone()[0]
    assert missing == 1
    # File comes back (restore from recycle bin) with genuinely new bytes.
    write_jpeg(victim, color=(5, 60, 130))
    stats = pipeline.run()
    assert stats["decoded"] == 1
    with pipeline.store.connect() as conn:
        missing = conn.execute(
            "SELECT missing FROM files WHERE path='photo_01.jpg'"
        ).fetchone()[0]
    assert missing == 0


def test_video_gets_poster_and_duration(scan):
    import cv2

    pipeline, root, engine = scan(faces_per_file=0)
    video_path = root / "clip.avi"
    writer = cv2.VideoWriter(
        str(video_path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (64, 48)
    )
    for i in range(20):
        writer.write(np.full((48, 64, 3), i * 10, dtype=np.uint8))
    writer.release()
    assert video_path.stat().st_size > 0

    stats = pipeline.run()
    assert stats["videos"] == 1
    assert stats["decoded"] == 5  # 4 photos + 1 video, first sight
    with pipeline.store.connect() as conn:
        row = conn.execute(
            "SELECT kind, duration, poster_path FROM media WHERE kind='video'"
        ).fetchone()
    assert row is not None
    assert row["duration"] and abs(row["duration"] - 2.0) < 0.5
    assert row["poster_path"] and (root / row["poster_path"]).is_file()


def test_labels_stored_and_fts_indexed(scan):
    pipeline, root, engine = scan(labels=["golden retriever", "grass"])
    pipeline.run()
    hits = pipeline.store.search_text("retriever")
    assert len(hits) == 4  # every photo got the same labels
    hits = pipeline.store.search_text("grass photo_00")
    assert len(hits) == 1 and hits[0]["path"] == "photo_00.jpg"


def test_cache_gc_removes_orphans_only(scan):
    pipeline, root, engine = scan(faces_per_file=1)
    pipeline.run()
    thumb_dir = root / ".faceframe" / "thumbnails"
    orphan = thumb_dir / "deadbeef#0.jpg"
    orphan.write_bytes(b"stale")
    posters = root / ".faceframe" / "posters"
    posters.mkdir(exist_ok=True)
    orphan_poster = posters / "deadbeef_p.jpg"
    orphan_poster.write_bytes(b"stale")
    stats = pipeline.run()  # idempotent pass also sweeps
    assert not orphan.exists()
    assert not orphan_poster.exists()
    with pipeline.store.connect() as conn:
        thumbs = [r[0] for r in conn.execute(
            "SELECT thumbnail_path FROM faces WHERE thumbnail_path IS NOT NULL"
        )]
    assert thumbs and all((root / t).is_file() for t in thumbs)


def test_cancellation_stops_between_files(scan):
    pipeline, root, engine = scan()
    seen = {"n": 0}

    def abort():
        seen["n"] += 1
        return seen["n"] > 2

    pipeline.abort_check = abort
    stats = pipeline.run()
    assert stats["cancelled"] is True
    assert stats["decoded"] <= 2


def test_capture_time_from_exif(scan, tmp_path):
    import piexif
    from PIL import Image

    pipeline, root, engine = scan(enable_faces=False)
    p = root / "with_exif.jpg"
    img = np.zeros((48, 64, 3), dtype=np.uint8)
    img[:, :] = (90, 140, 200)
    pil = Image.fromarray(img[:, :, ::-1])
    exif_dict = {
        "0th": {piexif.ImageIFD.Make: b"TestCam", piexif.ImageIFD.Model: b"X100"},
        "Exif": {
            piexif.ExifIFD.DateTimeOriginal: b"2021:06:15 10:30:00",
            piexif.ExifIFD.OffsetTimeOriginal: b"+02:00",
        },
        "GPS": {
            piexif.GPSIFD.GPSLatitudeRef: b"N",
            piexif.GPSIFD.GPSLatitude: ((41, 1), (23, 1), (0, 1)),
            piexif.GPSIFD.GPSLongitudeRef: b"E",
            piexif.GPSIFD.GPSLongitude: ((2, 1), (9, 1), (0, 1)),
        },
    }
    pil.save(str(p), "jpeg", exif=piexif.dump(exif_dict))
    pipeline.run()
    with pipeline.store.connect() as conn:
        row = conn.execute(
            "SELECT capture_time, tz_offset, exif FROM media m "
            "JOIN files f ON f.content_hash=m.content_hash WHERE f.path='with_exif.jpg'"
        ).fetchone()
    import calendar

    naive_epoch = calendar.timegm(time.strptime("2021:06:15 10:30:00", "%Y:%m:%d %H:%M:%S"))
    assert row["capture_time"] == naive_epoch  # naive wall time, tz kept aside
    assert row["tz_offset"] == "+02:00"
    exif = json.loads(row["exif"])
    assert exif["camera"] == "TestCam X100"
    assert abs(exif["gps"][0] - 41.383333) < 0.001
    assert abs(exif["gps"][1] - 2.15) < 0.001


def test_move_does_not_create_missing_ghosts(scan):
    """Stress finding: moving files used to leave permanent missing=1 ghosts
    even though the content was cleanly relinked at the new path."""
    pipeline, root, engine = scan()
    pipeline.run()
    (root / "sub_move").mkdir()
    moved = 0
    for p in sorted(root.glob("photo_*.jpg")):
        p.rename(root / "sub_move" / p.name)
        moved += 1
    stats = pipeline.run()
    assert stats["missing_now"] == 0, stats
    assert stats["decoded"] == 0
    with pipeline.store.connect() as conn:
        missing = conn.execute(
            "SELECT COUNT(*) FROM files WHERE missing=1"
        ).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    assert missing == 0 and total == moved + 1  # + nested.jpg
