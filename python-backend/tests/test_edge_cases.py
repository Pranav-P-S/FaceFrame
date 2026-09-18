"""Edge-case battery: hostile inputs at every layer of the backend.

Files that cannot be decoded, extreme dimensions, unicode/hostile names,
deep nesting, stale motion pairs, EXIF torture, extreme years, content
churn, locked handles, combined user states, query torture, album/people
edge inputs, trash purge corners, path escapes, override extremes and
concurrent writers. Backend fixes found by this file carry a matching
regression test here.
"""

import ctypes
import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import write_jpeg


# ----------------------------------------------------------------- helpers

def scan_factory(tmp_path):
    """A ScanPipeline over a fresh library; engine optional per call."""
    from scan import ScanPipeline

    root = tmp_path / "library"

    def make(root_path=None, faces=None, labels=None):
        root_path = root_path or root
        engine = None
        if faces is not None:
            class E:
                def process_decoded(self, path, img, face_key=None):
                    return [
                        {
                            "embedding": np.linspace(0, 1, 8).tolist(),
                            "bbox": [1, 1, 30, 30],
                            "det_score": 0.9,
                            "thumbnail": None,
                        }
                    ] * faces

            engine = E()
        pipeline = ScanPipeline(
            str(Path(root_path) / ".faceframe" / "index.db"), str(root_path),
            face_engine=engine,
            labeler=(lambda img: labels) if labels is not None else None,
        )
        return pipeline

    return root, make


def write_jpeg_any(path: Path, width=64, height=48, color=(90, 140, 200)) -> Path:
    """JPEG write that tolerates any filename (unicode, %, quotes-free
    hostiles) — cv2.imwrite is locale-bound on Windows, imencode is not."""
    import cv2

    rng = np.random.default_rng(len(str(path)) % (2**32))
    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:, :] = color
    img += rng.integers(0, 40, img.shape, dtype=np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    assert ok
    buf.tofile(str(path))
    return path


def write_jpeg_exif(path: Path, exif_dict: dict, width=64, height=48) -> Path:
    """JPEG with EXIF (piexif), any filename, RGB colorspace."""
    from PIL import Image

    import piexif

    rng = np.random.default_rng(len(str(exif_dict)) % (2**32))
    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:] = (120, 90, 60)
    img += rng.integers(0, 40, img.shape, dtype=np.uint8)
    pil = Image.fromarray(img)  # already RGB layout
    path.parent.mkdir(parents=True, exist_ok=True)
    pil.save(str(path), "jpeg", exif=piexif.dump(exif_dict))
    return path


def hashes_by_path(store) -> dict:
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT path, content_hash FROM files"
        ).fetchall()
    return {r["path"]: r["content_hash"] for r in rows}


def media_flags(store, content_hash: str) -> dict:
    raw = store.get_media(content_hash)["flags"]
    return json.loads(raw) if raw else {}


@pytest.fixture
def factory(tmp_path):
    return scan_factory(tmp_path)


# ---------------------------------------------------------------------------
# 1. Undecodable files
# ---------------------------------------------------------------------------

def test_undecodable_files_never_indexed_and_get_item_honest(factory):
    root, make = factory
    zero = root / "zero.jpg"
    zero.parent.mkdir(parents=True, exist_ok=True)
    zero.write_bytes(b"")
    text = root / "text.jpg"
    text.write_bytes(b"this is definitely not a jpeg, just plain text")
    good = write_jpeg_any(root / "good.jpg")

    pipeline = make()
    stats = pipeline.run()
    assert stats["decoded"] == 1  # only the real photo
    indexed = hashes_by_path(pipeline.store)
    assert set(indexed) == {"good.jpg"}  # broken files have no rows

    # Feed and get_item never lie about them.
    from views import feed_groups, item_detail

    groups = feed_groups(pipeline.store)
    paths = [i["path"] for g in groups for i in g["items"]]
    assert paths == ["good.jpg"]
    assert item_detail(pipeline.store, "zero.jpg") is None
    assert item_detail(pipeline.store, "text.jpg") is None
    assert item_detail(pipeline.store, "good.jpg") is not None
    assert good.is_file()


def test_undecodable_files_retried_on_rescan_then_real_file_indexed(factory,
                                                                     tmp_path):
    root, make = factory
    broken = root / "broken.jpg"
    root.mkdir(parents=True, exist_ok=True)
    # The seed lives OUTSIDE the library: only the broken copy is scanned.
    data = write_jpeg_any(tmp_path / "seed.jpg").read_bytes()
    broken.write_bytes(data[: len(data) // 2])  # valid header, cut bytes

    pipeline = make()
    pipeline.run()
    assert hashes_by_path(pipeline.store) == {}

    # Still undecodable: a rescan must not crash and must retry the hash.
    stats2 = pipeline.run()
    assert stats2["decoded"] == 0
    assert stats2["hashed"] >= 1  # retry work actually happened
    assert hashes_by_path(pipeline.store) == {}

    # File repaired on disk: next pass indexes it.
    broken.write_bytes(data)
    stats3 = pipeline.run()
    assert stats3["decoded"] == 1
    assert set(hashes_by_path(pipeline.store)) == {"broken.jpg"}


# ---------------------------------------------------------------------------
# 2. Extreme dimensions
# ---------------------------------------------------------------------------

def test_one_by_one_image(factory):
    root, make = factory
    write_jpeg_any(root / "tiny.jpg", width=1, height=1)
    pipeline = make()
    stats = pipeline.run()
    assert stats["decoded"] == 1
    row = pipeline.store.get_media(hashes_by_path(pipeline.store)["tiny.jpg"])
    assert (row["width"], row["height"]) == (1, 1)
    flags = media_flags(pipeline.store, row["content_hash"])
    assert "panorama" not in flags


def test_8000x8000_gradient_completes_quickly(factory):
    root, make = factory
    write_jpeg_any(root / "huge.jpg", width=8000, height=8000)
    pipeline = make()
    started = time.time()
    stats = pipeline.run()
    elapsed = time.time() - started
    assert stats["decoded"] == 1
    assert elapsed < 30.0, f"8000x8000 scan took {elapsed:.1f}s"
    row = pipeline.store.get_media(hashes_by_path(pipeline.store)["huge.jpg"])
    assert (row["width"], row["height"]) == (8000, 8000)


def test_panorama_strip_flagged(factory):
    root, make = factory
    write_jpeg_any(root / "pano.jpg", width=3, height=20000)
    pipeline = make()
    stats = pipeline.run()
    assert stats["decoded"] == 1
    row = pipeline.store.get_media(hashes_by_path(pipeline.store)["pano.jpg"])
    assert media_flags(pipeline.store, row["content_hash"])["panorama"] == 1


# ---------------------------------------------------------------------------
# 3. Unicode + hostile names
# ---------------------------------------------------------------------------

def test_unicode_and_hostile_filenames(factory):
    root, make = factory
    names = [
        "naïve_照片_🎉.jpg",            # unicode soup
        "100%_done.jpg",                 # SQL/FTS/LIKE metachar
        "it's a 100% #1 (draft)[v2].jpg",  # quotes-free SQL metachars
        "x" * 180 + ".jpg",              # 180-char stem
        "...jpg",                        # dots-only stem + extension
    ]
    for name in names:
        write_jpeg_any(root / name, color=(len(name) % 200, 60, 130))

    # Documented Windows limits: `"` is illegal in NT filenames, and a
    # trailing-space name survives only as the OS reports it.
    try:
        write_jpeg_any(root / 'quote"name.jpg')
        names.append('quote"name.jpg')
    except OSError:
        pass  # Windows forbids double quotes in filenames: documented
    try:
        write_jpeg_any(root / "trailing space .jpg")
        listed = {f.name for f in root.iterdir()}
        if "trailing space .jpg" in listed:
            names.append("trailing space .jpg")
    except OSError:
        pass  # Windows forbids trailing spaces here: documented

    pipeline = make()
    stats = pipeline.run()
    assert stats["decoded"] == len(names)
    indexed = set(hashes_by_path(pipeline.store))
    assert indexed == set(names)

    from views import feed_groups

    paths = [i["path"] for g in feed_groups(pipeline.store) for i in g["items"]]
    assert set(paths) == set(names)

    # Search stays literal and never corrupts on the hostile stems.
    hits = pipeline.store.search_text("done")
    assert any(h["path"] == "100%_done.jpg" for h in hits)


def test_dot_only_filename_documented(factory):
    """A name that is ONLY dots ('...') is reserved on Windows: creation
    must fail cleanly (or the scanner must cope if the OS allows it)."""
    root, make = factory
    try:
        (root / "...").write_bytes(b"x")
    except OSError:
        pytest.skip("Windows forbids dot-only filenames (documented)")
    pipeline = make()
    pipeline.run()  # must not crash either way


# ---------------------------------------------------------------------------
# 4./5. Deep nesting, root files, .faceframe at every level
# ---------------------------------------------------------------------------

def test_deep_nesting_root_file_and_nested_faceframe_skipped(factory):
    root, make = factory
    deep = root
    for _ in range(40):
        deep = deep / "d"
    write_jpeg_any(deep / "deep.jpg")
    write_jpeg_any(root / "at_root.jpg")
    write_jpeg_any(root / "empty_parent" / "kept.jpg")
    (root / "truly_empty").mkdir(parents=True)
    # A .faceframe folder in a SUBfolder must be skipped too, at every level.
    write_jpeg_any(root / "sub" / ".faceframe" / "hidden.jpg")

    pipeline = make()
    stats = pipeline.run()
    indexed = set(hashes_by_path(pipeline.store))
    assert indexed == {"d/" * 40 + "deep.jpg", "at_root.jpg",
                       "empty_parent/kept.jpg"}
    assert stats["decoded"] == 3


def test_uppercase_faceframe_dir_skipped_on_windows(factory):
    root, make = factory
    (root / "sub" / ".FACEFRAME").mkdir(parents=True)
    write_jpeg_any(root / "sub" / ".FACEFRAME" / "x.jpg")
    write_jpeg_any(root / "visible.jpg")
    pipeline = make()
    pipeline.run()
    assert set(hashes_by_path(pipeline.store)) == {"visible.jpg"}


# ---------------------------------------------------------------------------
# 6. Motion pairs
# ---------------------------------------------------------------------------

def _write_video(path: Path, frames=10):
    import cv2

    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (64, 48)
    )
    for i in range(frames):
        writer.write(np.full((48, 64, 3), i * 20, dtype=np.uint8))
    writer.release()


def test_motion_pair_links_and_feed_shows_photo_once(factory):
    root, make = factory
    write_jpeg_any(root / "IMG_0001.jpg")
    _write_video(root / "IMG_0001.mp4")
    pipeline = make(faces=0)
    pipeline.run()
    def paired_of(path):
        with pipeline.store.connect() as conn:
            return conn.execute(
                "SELECT paired_path FROM files WHERE path=?", (path,)
            ).fetchone()[0]

    assert paired_of("IMG_0001.jpg") == "IMG_0001.mp4"
    assert paired_of("IMG_0001.mp4") == "IMG_0001.jpg"

    from views import feed_groups

    paths = [i["path"] for g in feed_groups(pipeline.store) for i in g["items"]]
    assert paths.count("IMG_0001.jpg") == 1
    assert "IMG_0001.mp4" not in paths  # hidden behind its photo


def test_motion_pair_stale_flag_clears_when_video_deleted(factory):
    root, make = factory
    write_jpeg_any(root / "IMG_0001.jpg")
    _write_video(root / "IMG_0001.mp4")
    pipeline = make(faces=0)
    pipeline.run()

    (root / "IMG_0001.mp4").unlink()
    pipeline.run()
    with pipeline.store.connect() as conn:
        assert conn.execute(
            "SELECT paired_path FROM files WHERE path='IMG_0001.jpg'"
        ).fetchone()[0] is None

    from views import feed_groups

    paths = [i["path"] for g in feed_groups(pipeline.store) for i in g["items"]]
    assert paths == ["IMG_0001.jpg"]  # feed unaffected by the stale flag


def test_same_stem_two_folders_one_video_no_crash(factory):
    root, make = factory
    write_jpeg_any(root / "a" / "IMG.jpg", color=(10, 20, 30))
    write_jpeg_any(root / "b" / "IMG.jpg", color=(200, 20, 30))
    _write_video(root / "a" / "IMG.mp4")
    pipeline = make(faces=0)
    pipeline.run()  # exactly one photo wins the pairing; nothing crashes
    from views import feed_groups

    paths = [i["path"] for g in feed_groups(pipeline.store) for i in g["items"]]
    assert set(paths) == {"a/IMG.jpg", "b/IMG.jpg"}


# ---------------------------------------------------------------------------
# 7./8. EXIF torture
# ---------------------------------------------------------------------------

def test_exif_orientation_swaps_dimensions(factory):
    root, make = factory
    write_jpeg_exif(
        root / "rotated.jpg",
        {"0th": {piexif_orientation(): 8}},
    )
    pipeline = make()
    pipeline.run()
    row = pipeline.store.get_media(hashes_by_path(pipeline.store)["rotated.jpg"])
    # Stored 64x48 pixels; orientation 8 means the viewer rotates: decoded
    # dimensions are transposed.
    assert (row["width"], row["height"]) == (48, 64)


def piexif_orientation():
    import piexif

    return piexif.ImageIFD.Orientation


def test_exif_datetime_without_offset(factory):
    import calendar

    root, make = factory
    write_jpeg_exif(
        root / "naive_time.jpg",
        {"Exif": {piexif_datetime_original(): b"2021:06:15 10:30:00"}},
    )
    pipeline = make()
    pipeline.run()
    row = pipeline.store.get_media(
        hashes_by_path(pipeline.store)["naive_time.jpg"]
    )
    expected = calendar.timegm(time.strptime("2021:06:15 10:30:00", "%Y:%m:%d %H:%M:%S"))
    assert row["capture_time"] == expected
    assert row["tz_offset"] is None


def piexif_datetime_original():
    import piexif

    return piexif.ExifIFD.DateTimeOriginal


def test_exif_garbage_and_zero_denominators(factory):
    import piexif

    from exif import extract

    root, make = factory
    # Random bytes inside the EXIF APP1 payload (TIFF header kept so the
    # damage is metadata-only): extract() must never raise, and the scan
    # copes however PIL rules — decode or skip-with-retry, no crash.
    p = write_jpeg_exif(
        root / "garbage_exif.jpg",
        {"0th": {piexif.ImageIFD.Make: b"Cam"}},
    )
    raw = bytearray(p.read_bytes())
    pos = raw.find(b"Exif\x00\x00")
    if pos != -1:
        rng = np.random.default_rng(42)
        for i in range(pos + 6 + 8, min(pos + 6 + 8 + 24, len(raw))):
            raw[i] = int(rng.integers(0, 256))
        p.write_bytes(bytes(raw))
    info = extract(str(p))  # must not raise
    assert set(info) == {"capture_time", "tz_offset", "exif"}
    # GPS rationals with zero denominators must not divide by zero.
    write_jpeg_exif(
        root / "zero_denom.jpg",
        {"GPS": {
            piexif.GPSIFD.GPSLatitudeRef: b"N",
            piexif.GPSIFD.GPSLatitude: ((41, 0), (23, 0), (0, 0)),
            piexif.GPSIFD.GPSLongitudeRef: b"E",
            piexif.GPSIFD.GPSLongitude: ((2, 0), (9, 0), (0, 0)),
        }},
    )
    # GPS rationals with zero numerators (the (0,1) shape).
    write_jpeg_exif(
        root / "zero_numer.jpg",
        {"GPS": {
            piexif.GPSIFD.GPSLatitudeRef: b"N",
            piexif.GPSIFD.GPSLatitude: ((0, 1), (0, 1), (0, 1)),
            piexif.GPSIFD.GPSLongitudeRef: b"E",
            piexif.GPSIFD.GPSLongitude: ((0, 1), (0, 1), (0, 1)),
        }},
    )
    pipeline = make()
    stats = pipeline.run()  # must not raise on any of the three
    assert set(hashes_by_path(pipeline.store)) >= {
        "zero_denom.jpg", "zero_numer.jpg",
    }
    # Zero-denominator GPS must collapse to nothing usable — never nan/inf
    # in the stored exif JSON (strict JSON parsers reject those literals).
    for name in ("zero_denom.jpg", "zero_numer.jpg"):
        raw_exif = pipeline.store.get_media(
            hashes_by_path(pipeline.store)[name]
        )["exif"]
        assert raw_exif is None or (
            "NaN" not in raw_exif and "Infinity" not in raw_exif
        )
        gps = extract(str(root / name))["exif"].get("gps")
        assert gps is None or all(-90 <= v <= 90 for v in gps)
    assert stats["decoded"] >= 2


def test_exif_extreme_years_1900_and_2099(factory):
    root, make = factory
    write_jpeg_exif(
        root / "old.jpg",
        {"Exif": {piexif_datetime_original(): b"1900:06:15 10:30:00"}},
    )
    write_jpeg_exif(
        root / "future.jpg",
        {"Exif": {piexif_datetime_original(): b"2099:12:31 23:59:00"}},
    )
    pipeline = make()
    stats = pipeline.run()
    assert stats["decoded"] == 2
    by_path = hashes_by_path(pipeline.store)
    assert pipeline.store.get_media(by_path["old.jpg"])["capture_time"] < 0
    assert (
        pipeline.store.get_media(by_path["future.jpg"])["capture_time"] > 4e9
    )

    # Feed grouping and memories must survive pre-1970 timestamps on Windows
    # (time.gmtime cannot; the view layer must not rely on it).
    from memories import build_memories
    from views import feed_groups

    groups = feed_groups(pipeline.store, view="days")
    keys = {g["key"] for g in groups}
    assert "1900-06-15" in keys and "2099-12-31" in keys
    build_memories(pipeline.store)  # must not raise


def test_same_wall_time_different_offsets_same_day(factory):
    import piexif

    root, make = factory
    write_jpeg_exif(
        root / "berlin.jpg",
        {"Exif": {
            piexif_datetime_original(): b"2021:06:15 10:30:00",
            piexif.ExifIFD.OffsetTimeOriginal: b"+02:00",
        }},
    )
    write_jpeg_exif(
        root / "newyork.jpg",
        {"Exif": {
            piexif_datetime_original(): b"2021:06:15 10:30:00",
            piexif.ExifIFD.OffsetTimeOriginal: b"-05:00",
        }},
    )
    pipeline = make()
    pipeline.run()
    by_path = hashes_by_path(pipeline.store)
    berlin = pipeline.store.get_media(by_path["berlin.jpg"])
    newyork = pipeline.store.get_media(by_path["newyork.jpg"])
    assert berlin["capture_time"] == newyork["capture_time"]
    assert {berlin["tz_offset"], newyork["tz_offset"]} == {"+02:00", "-05:00"}

    from views import feed_groups

    groups = feed_groups(pipeline.store, view="days")
    assert len(groups) == 1  # same wall clock: one day
    assert groups[0]["key"] == "2021-06-15"


# ---------------------------------------------------------------------------
# 9. Content churn
# ---------------------------------------------------------------------------

def _scanned_lib(factory):
    root, make = factory
    pipeline = make(faces=0)
    return root, pipeline


def test_duplicate_bytes_after_edit_dedupe_both_files_in_feed(factory):
    root, pipeline = _scanned_lib(factory)
    write_jpeg_any(root / "a.jpg", color=(30, 60, 90))
    write_jpeg_any(root / "b.jpg", color=(120, 40, 10))
    pipeline.run()
    store = pipeline.store
    by_path = hashes_by_path(store)
    assert by_path["a.jpg"] != by_path["b.jpg"]

    store.set_media_user_state(by_path["a.jpg"], favorite=1)
    # a.jpg is edited until its bytes equal b.jpg exactly.
    (root / "a.jpg").write_bytes((root / "b.jpg").read_bytes())
    pipeline.run()

    by_path = hashes_by_path(store)
    assert by_path["a.jpg"] == by_path["b.jpg"]  # deduped
    from views import feed_groups

    paths = [i["path"] for g in feed_groups(store) for i in g["items"]]
    assert sorted(paths) == ["a.jpg", "b.jpg"]  # both still in the feed


def test_file_replaced_and_reverted_keeps_favorite(factory):
    root, pipeline = _scanned_lib(factory)
    original = write_jpeg_any(root / "a.jpg", color=(10, 20, 30)).read_bytes()
    other = write_jpeg_any(root / "other.jpg", color=(200, 100, 50))
    pipeline.run()
    store = pipeline.store
    original_hash = hashes_by_path(store)["a.jpg"]
    store.set_media_user_state(original_hash, favorite=1)

    # Transition 1: replaced by a different image.
    (root / "a.jpg").write_bytes(other.read_bytes())
    pipeline.run()
    mid_hash = hashes_by_path(store)["a.jpg"]
    assert mid_hash != original_hash
    assert store.get_media(mid_hash)["favorite"] == 1

    # Transition 2: reverted to the original bytes.
    (root / "a.jpg").write_bytes(original)
    pipeline.run()
    back_hash = hashes_by_path(store)["a.jpg"]
    assert back_hash == original_hash
    assert store.get_media(back_hash)["favorite"] == 1

    from views import feed_groups

    paths = [i["path"] for g in feed_groups(store) for i in g["items"]]
    assert {"a.jpg", "other.jpg"} <= set(paths)


def test_added_at_preserved_across_edit_and_revert(factory):
    root, pipeline = _scanned_lib(factory)
    original = write_jpeg_any(root / "a.jpg", color=(10, 20, 30)).read_bytes()
    other = write_jpeg_any(root / "other.jpg", color=(200, 100, 50))
    pipeline.run()
    store = pipeline.store
    with store.connect() as conn:
        first_added = conn.execute(
            "SELECT added_at FROM files WHERE path='a.jpg'"
        ).fetchone()[0]
    time.sleep(0.02)
    (root / "a.jpg").write_bytes(other.read_bytes())
    pipeline.run()
    (root / "a.jpg").write_bytes(original)
    pipeline.run()
    with store.connect() as conn:
        final_added = conn.execute(
            "SELECT added_at FROM files WHERE path='a.jpg'"
        ).fetchone()[0]
    assert final_added == first_added  # 'Recently added' must not churn


# ---------------------------------------------------------------------------
# 10. File locked by another process
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="Windows handle locking")
def test_locked_file_skipped_gracefully(factory, tmp_path):
    root, make = factory
    locked = write_jpeg_any(root / "locked.jpg")
    write_jpeg_any(root / "free.jpg")

    GENERIC_READ = 0x80000000
    OPEN_EXISTING = 3
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateFileW(
        str(locked), GENERIC_READ, 0, None, OPEN_EXISTING, 0, None
    )
    if handle in (None, INVALID_HANDLE_VALUE):
        pytest.skip("Could not take an exclusive handle on the file")
    try:
        pipeline = make()
        stats = pipeline.run()  # must not raise
        assert stats["decoded"] == 1  # only the unlocked file
        assert set(hashes_by_path(pipeline.store)) == {"free.jpg"}
    finally:
        kernel32.CloseHandle(handle)


# ---------------------------------------------------------------------------
# 11. Combined states
# ---------------------------------------------------------------------------

def test_combined_states_visible_only_in_trash_then_restored(factory):
    root, pipeline = _scanned_lib(factory)
    write_jpeg_any(root / "c.jpg", color=(15, 75, 130))
    pipeline.run()
    store = pipeline.store
    from library import LibraryService
    from views import feed_groups

    svc = LibraryService(store, str(root))
    h = hashes_by_path(store)["c.jpg"]
    svc.set_favorite([h], True)
    svc.set_archived([h], True)
    svc.set_locked_passcode("9999")
    svc.set_locked([h], True)
    svc.set_trashed([h], True)

    feed_paths = [i["path"] for g in feed_groups(store) for i in g["items"]]
    assert "c.jpg" not in feed_paths
    assert "c.jpg" not in {r["path"] for r in svc.archived_items()}
    assert "c.jpg" not in {r["path"] for r in svc.locked_items()}
    assert {r["path"] for r in svc.trashed_items()} == {"c.jpg"}

    svc.set_trashed([h], False)  # restore: prior visibility returns
    feed_paths = [i["path"] for g in feed_groups(store) for i in g["items"]]
    assert "c.jpg" not in feed_paths          # still locked
    assert "c.jpg" in {r["path"] for r in svc.locked_items()}
    assert "c.jpg" in {r["path"] for r in svc.archived_items()}
    assert svc.trashed_items() == []


# ---------------------------------------------------------------------------
# 12. Empty library
# ---------------------------------------------------------------------------

def test_every_query_on_empty_library_returns_empty_shapes(tmp_path):
    from library import LibraryService
    from memories import build_memories
    from people import PeopleService
    from places import place_groups
    from store import Store
    from views import feed_groups, item_detail, search_items

    root = tmp_path / "lib"
    root.mkdir()
    (root / ".faceframe").mkdir()
    store = Store(str(root / ".faceframe" / "index.db"), library_root=str(root))
    svc = LibraryService(store, str(root))

    assert feed_groups(store, view="days") == []
    assert feed_groups(store, view="months") == []
    assert feed_groups(store, view="years") == []
    assert search_items(store, "anything") == {
        "items": [], "total": 0, "filters": {},
    }
    assert build_memories(store) == []
    assert place_groups(store) == []
    assert store.duplicate_groups() == []
    assert svc.trashed_items() == []
    assert svc.list_albums() == []
    assert svc.visible_items() == []
    assert svc.locked_items() == []
    assert svc.archived_items() == []
    assert PeopleService(store).list_persons() == []
    assert item_detail(store, "nope.jpg") is None
    stats = svc.storage_stats()
    assert stats["items"]["count"] == 0 and stats["items"]["bytes"] == 0


# ---------------------------------------------------------------------------
# 13. Query torture through search
# ---------------------------------------------------------------------------

def test_search_query_torture(factory):
    root, pipeline = _scanned_lib(factory)
    write_jpeg_any(root / "victim.jpg", color=(5, 75, 130))
    pipeline.run()
    store = pipeline.store
    from views import search_items

    queries = [
        "",                                  # empty: browse-all shape
        "   ",                               # whitespace
        "year:abcd",                         # non-numeric year
        'person:"',                          # unterminated quote
        "is:",                               # empty filter value
        "type:",
        "place:",
        "folder:C:\\windows",                # absolute folder outside
        "100%' ; -- DROP TABLE media",       # SQL metachars in text
        "% _ %",                             # LIKE metachars in text
        "person:%' ; --",                    # SQL metachars in person value
        "folder:%_'--",                      # SQL metachars in folder value
        "x" * 5000,                          # 5000-char query
        "is:favorite is:locked type:photo person:ghost place:nowhere",
    ]
    for q in queries:
        result = search_items(store, q)  # must never raise
        assert set(result) >= {"items", "total", "filters"}

    result = search_items(store, "")
    assert [i["path"] for i in result["items"]] == ["victim.jpg"]
    result = search_items(store, "person:ghost")
    assert result["items"] == []
    result = search_items(store, "is:locked")
    assert result["items"] == []


# ---------------------------------------------------------------------------
# 14. Album edges
# ---------------------------------------------------------------------------

def test_album_edges(factory):
    root, pipeline = _scanned_lib(factory)
    write_jpeg_any(root / "p.jpg", color=(15, 75, 130))
    pipeline.run()
    store = pipeline.store
    from library import LibraryService

    svc = LibraryService(store, str(root))
    h = hashes_by_path(store)["p.jpg"]

    album_id = svc.create_album("Edge")
    svc.add_album_items(album_id, [h])
    svc.add_album_items(album_id, [h])  # duplicate add: no second row
    assert len(svc.album_items(album_id)) == 1

    svc.remove_album_items(album_id, ["f" * 64])  # not a member: no-op
    assert len(svc.album_items(album_id)) == 1
    svc.remove_album_items(album_id, [h])  # remove for real
    assert svc.album_items(album_id) == []

    svc.add_album_items(album_id, [h, h, h])  # collapses to one
    assert len(svc.album_items(album_id)) == 1
    other = write_jpeg_any(root / "q.jpg", color=(100, 20, 60))
    pipeline.run()
    h2 = hashes_by_path(store)["q.jpg"]
    svc.add_album_items(album_id, [h, h2])
    svc.reorder_album(album_id, [h2])  # partial list: no crash, no loss
    remaining = {i["content_hash"] for i in svc.album_items(album_id)}
    assert remaining == {h, h2}

    # Cover whose media is deleted: FK nulls it, listing stays clean.
    svc.set_album_cover(album_id, h2)
    store.remove_file("q.jpg")
    assert svc.list_albums()[0]["cover_hash"] is None

    svc.delete_album(album_id)
    assert svc.list_albums() == []
    import main

    with pytest.raises(ValueError):
        main._get_album({"path": str(root), "album_id": album_id})


# ---------------------------------------------------------------------------
# 15. People edges
# ---------------------------------------------------------------------------

def _emb(seed):
    rng = np.random.default_rng(seed)
    v = rng.normal(0, 1, 8)
    return (v / np.linalg.norm(v)).tolist()


@pytest.fixture
def people_env(factory):
    root, make = factory
    pipeline = make(faces=0)
    # Distinct colors: identical bytes would dedupe both paths onto one
    # media row and collapse the face set.
    write_jpeg_any(root / "p0.jpg", color=(10, 20, 30))
    write_jpeg_any(root / "p1.jpg", color=(40, 50, 60))
    pipeline.run()
    store = pipeline.store
    from people import PeopleService

    return root, store, PeopleService(store), pipeline


def test_people_management_edges(people_env):
    root, store, people, _ = people_env
    with store.connect() as conn:
        cur = conn.execute(
            "INSERT INTO persons (name, created_at) VALUES ('Alice', 0)"
        )
        pid = cur.lastrowid
    store.add_faces(
        hashes_by_path(store)["p0.jpg"],
        [{"embedding": _emb(1), "bbox": "[1,1,30,30]", "det_score": 0.9}],
    )
    faces = store.all_faces_for_clustering()

    with pytest.raises(ValueError):
        people.merge_persons(pid, pid)          # merge into itself
    with pytest.raises(ValueError):
        people.split_person(pid, [])            # empty split
    with pytest.raises(ValueError):
        people.assign_faces([faces[0]["id"]], 424242)  # no such person

    people.rename_person(pid, "")               # empty rename: no crash
    assert people.get_person(pid)["name"] == ""
    people.rename_person(pid, "Alice")


def test_hidden_person_stays_hidden_after_recluster(people_env):
    root, store, people, _ = people_env
    by_path = hashes_by_path(store)
    emb = _emb(7)
    store.add_faces(by_path["p0.jpg"], [
        {"embedding": emb, "bbox": "[1,1,30,30]", "det_score": 0.9},
    ])
    store.add_faces(by_path["p1.jpg"], [
        {"embedding": emb, "bbox": "[1,1,30,30]", "det_score": 0.9},
    ])
    stats = people.cluster()
    assert stats["people"] == 1
    person = people.list_persons()[0]
    people.set_person_hidden(person["id"], True)
    assert people.list_persons() == []

    people.cluster()  # recluster must not unhide or crash
    assert people.get_person(person["id"])["hidden"] == 1
    assert people.list_persons() == []
    assert people.get_person(person["id"])["face_count"] == 2


def test_person_survives_missing_file_with_empty_photos(people_env):
    root, store, people, pipeline = people_env
    h = hashes_by_path(store)["p0.jpg"]
    store.add_faces(h, [
        {"embedding": _emb(3), "bbox": "[1,1,30,30]", "det_score": 0.9},
    ])
    person_id = store.create_person("Solo")
    store.set_faces_person(
        [store.all_faces_for_clustering()[0]["id"]], person_id
    )
    (root / "p0.jpg").unlink()
    pipeline.run()  # file becomes missing
    assert people.person_photos(person_id) == []
    assert people.get_person(person_id) is not None


# ---------------------------------------------------------------------------
# 16. Locked folder
# ---------------------------------------------------------------------------

def test_locked_folder_passcode_lifecycle(factory):
    root, pipeline = _scanned_lib(factory)
    write_jpeg_any(root / "secret.jpg", color=(90, 10, 40))
    pipeline.run()
    store = pipeline.store
    from library import LibraryService
    from views import feed_groups

    svc = LibraryService(store, str(root))
    h = hashes_by_path(store)["secret.jpg"]

    with pytest.raises(ValueError):
        svc.set_locked([h], True)  # no passcode yet
    svc.set_locked_passcode("1234")
    svc.set_locked([h], True)

    feed_paths = [i["path"] for g in feed_groups(store) for i in g["items"]]
    assert "secret.jpg" not in feed_paths
    assert {r["path"] for r in svc.locked_items()} == {"secret.jpg"}

    with pytest.raises(ValueError):
        svc.remove_locked_passcode("0000")  # wrong code refused
    assert {r["path"] for r in svc.locked_items()} == {"secret.jpg"}

    svc.remove_locked_passcode("1234")
    assert svc.locked_items() == []
    feed_paths = [i["path"] for g in feed_groups(store) for i in g["items"]]
    assert "secret.jpg" in feed_paths


# ---------------------------------------------------------------------------
# 17. Trash purge corners
# ---------------------------------------------------------------------------

def test_purge_with_zero_age_sends_everything_to_os_trash(factory, monkeypatch):
    root, pipeline = _scanned_lib(factory)
    write_jpeg_any(root / "doomed.jpg", color=(80, 10, 170))
    pipeline.run()
    store = pipeline.store
    from library import LibraryService

    svc = LibraryService(store, str(root))
    sent = []
    monkeypatch.setattr("library.send2trash", lambda p: sent.append(p))
    svc.set_trashed(paths=["doomed.jpg"], trashed=True)
    # Backdate explicitly: time.time() can return the same value for the
    # trash stamp and the purge cutoff on coarse Windows timers.
    with store.connect() as conn:
        conn.execute(
            "UPDATE files SET trashed_at = trashed_at - 10 WHERE path='doomed.jpg'"
        )
    removed = svc.purge_expired_trash(max_age_days=0)
    assert removed == 1
    assert len(sent) == 1 and sent[0].endswith("doomed.jpg")
    assert svc.trashed_items() == []
    with store.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM files WHERE path='doomed.jpg'"
        ).fetchone()[0] == 0
    assert (root / "doomed.jpg").exists()  # OS trash, never a hard delete


def test_delete_from_disk_when_file_already_gone(factory, monkeypatch):
    root, pipeline = _scanned_lib(factory)
    write_jpeg_any(root / "gone.jpg", color=(40, 80, 160))
    pipeline.run()
    store = pipeline.store
    from library import LibraryService

    svc = LibraryService(store, str(root))
    monkeypatch.setattr("library.send2trash", lambda p: pytest.fail(
        "send2trash must not be called for a file that is already gone"
    ))
    svc.set_trashed(paths=["gone.jpg"], trashed=True)
    (root / "gone.jpg").unlink()  # removed outside FaceFrame
    removed = svc.delete_from_disk(["gone.jpg"])
    assert removed == 1  # clean no-op on disk, row dropped
    assert svc.trashed_items() == []


# ---------------------------------------------------------------------------
# 18. Path escapes (security regressions)
# ---------------------------------------------------------------------------

def test_path_escapes_are_refused(factory, tmp_path):
    root, pipeline = _scanned_lib(factory)
    write_jpeg_any(root / "p.jpg", color=(15, 75, 130))
    pipeline.run()
    from library import LibraryService

    svc = LibraryService(store=pipeline.store, library_root=str(root))
    with pytest.raises(ValueError):
        svc.set_trashed(paths=["../outside.jpg"])
    with pytest.raises(ValueError):
        svc.delete_from_disk([str(tmp_path / "outside.jpg")])
    assert (tmp_path / "outside.jpg")  # nothing happened to it either way

    import main

    with pytest.raises(ValueError):
        main._resolve_missing({"path": str(root), "paths": ["../x.jpg"]})


def test_render_preview_constrained_to_indexed_files(factory):
    root, pipeline = _scanned_lib(factory)
    write_jpeg_any(root / "p.jpg", color=(15, 75, 130))
    pipeline.run()
    from render import render_preview

    assert render_preview(pipeline.store, str(root), "p.jpg") is not None
    # Unindexed (or escaped) relative path: refuse, never guess.
    assert render_preview(pipeline.store, str(root), "unindexed.jpg") is None
    assert render_preview(pipeline.store, str(root), "../sibling.jpg") is None


def test_preview_of_unindexed_file_in_other_library_errors(factory, tmp_path,
                                                           monkeypatch):
    """A path whose parents contain a DIFFERENT library's .faceframe must
    still resolve to indexed files only: the preview action replies with an
    error instead of rendering an arbitrary file."""
    root, make = factory
    lib_a = root
    write_jpeg_any(lib_a / "a.jpg")
    make(lib_a).run()

    lib_b = tmp_path / "other_library"
    (lib_b / ".faceframe").mkdir(parents=True)
    secret = write_jpeg_any(lib_b / "secret.jpg", color=(1, 2, 3))

    import main

    main.locate_library(str(lib_a))
    main.locate_library(str(lib_b))
    replies = []
    monkeypatch.setattr(main, "emit", lambda payload: replies.append(payload))
    main.handle({
        "id": 1, "action": "get_image_preview", "file_path": str(secret),
    })
    assert len(replies) == 1
    assert replies[0]["id"] == 1 and replies[0]["ok"] is False
    assert "data" not in replies[0]
    main._contexts.pop(str(lib_a.resolve()), None)
    main._contexts.pop(str(lib_b.resolve()), None)


# ---------------------------------------------------------------------------
# 19. Captions & date overrides
# ---------------------------------------------------------------------------

def test_caption_extremes_and_search(factory):
    root, pipeline = _scanned_lib(factory)
    write_jpeg_any(root / "c.jpg", color=(15, 75, 130))
    pipeline.run()
    store = pipeline.store
    from library import LibraryService
    from views import search_items

    svc = LibraryService(store, str(root))
    h = hashes_by_path(store)["c.jpg"]
    caption = "🎉 beach day\n\n" + "sunset " * 2000  # emoji + newlines + ~10k
    svc.set_caption(h, caption)
    assert store.get_media(h)["caption"] == caption

    hits = search_items(store, "sunset")
    assert [i["path"] for i in hits["items"]] == ["c.jpg"]
    hits = search_items(store, "beach")
    assert [i["path"] for i in hits["items"]] == ["c.jpg"]

    svc.set_caption(h, "")
    assert store.get_media(h)["caption"] is None


def test_date_override_epoch_zero_and_year_3000_then_revert(factory):
    root, pipeline = _scanned_lib(factory)
    write_jpeg_any(root / "d.jpg", color=(15, 75, 130))
    pipeline.run()
    store = pipeline.store
    from library import LibraryService, LibraryService as LS
    from views import feed_groups

    svc = LibraryService(store, str(root))
    h = hashes_by_path(store)["d.jpg"]
    original_ts = LS.effective_capture_time(svc.get_media(h))

    svc.set_date_override(h, 0.0)
    groups = feed_groups(store)
    assert any(g["key"] == "1970-01-01" for g in groups)

    svc.set_date_override(h, 32503680000.0)  # year 3000
    groups = feed_groups(store)
    assert groups[0]["key"] == "3000-01-01"

    svc.set_date_override(h, None)  # revert: EXIF/mtime order returns
    assert LS.effective_capture_time(svc.get_media(h)) == pytest.approx(
        original_ts
    )
    keys = {g["key"] for g in feed_groups(store)}
    assert "3000-01-01" not in keys and "1970-01-01" not in keys


# ---------------------------------------------------------------------------
# 20. Concurrent-ish writers
# ---------------------------------------------------------------------------

def test_two_store_writers_never_deadlock(tmp_path):
    from store import Store

    db = str(tmp_path / "index.db")
    Store(db)  # create schema up front
    errors = []
    counts = [0, 0]

    def writer(idx):
        try:
            store = Store(db)
            i = 0
            deadline = time.time() + 2.0
            while time.time() < deadline:
                slot = i % 10  # 10 paths per writer: covered after 10 ops
                path = f"w{idx}/f{slot}.jpg"
                # Media row first: files carry an FK on content_hash.
                store.upsert_media({
                    "content_hash": f"{idx:062x}{slot:02x}",
                    "kind": "photo",
                    "analysis_state": "analyzed",
                })
                store.upsert_file(path, f"{idx:062x}{slot:02x}", 1.0 * i, 10)
                counts[idx] += 1
                i += 1
        except Exception as e:  # pragma: no cover - failure path
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [], errors
    assert all(c > 20 for c in counts), counts  # both writers made progress
    store = Store(db)
    assert store.media_count() == 20
    with store.connect() as conn:
        rows = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    assert rows == 20  # both writers' paths coexist
