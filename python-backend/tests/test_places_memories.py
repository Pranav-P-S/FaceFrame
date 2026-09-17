"""Slice 5 — places (geohash clusters, optional geocoding) and memories."""

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import write_jpeg


def _epoch(dt: str) -> float:
    import calendar

    return calendar.timegm(time.strptime(dt, "%Y-%m-%d %H:%M:%S"))


@pytest.fixture
def geo_lib(tmp_path):
    """Six photos at two places (Berlin ~52.5, 13.4; Paris ~48.85, 2.35)
    plus two without GPS, all on different days."""
    import piexif
    from PIL import Image

    import numpy as np

    from scan import ScanPipeline

    root = tmp_path / "library"
    places = {
        "berlin%d.jpg": (52.52, 13.40),
        "berlin%d.jpg_": None,
    }
    specs = [
        ("berlin0.jpg", 52.52, 13.40, "2024-05-01 10:00:00"),
        ("berlin1.jpg", 52.51, 13.42, "2024-05-02 11:00:00"),
        ("paris0.jpg", 48.85, 2.35, "2024-06-01 09:00:00"),
        ("paris1.jpg", 48.86, 2.34, "2024-06-02 09:30:00"),
        ("nogps0.jpg", None, None, "2024-06-03 09:30:00"),
        ("nogps1.jpg", None, None, "2024-06-04 09:30:00"),
    ]
    for i, (name, lat, lon, dt) in enumerate(specs):
        path = write_jpeg(root / name, color=(20 * i + 30, 70, 170))
        img = np.zeros((48, 64, 3), dtype=np.uint8)
        if lat is not None:
            exif_dict = {
                "GPS": {
                    piexif.GPSIFD.GPSLatitudeRef: b"N",
                    piexif.GPSIFD.GPSLatitude: _dms(lat),
                    piexif.GPSIFD.GPSLongitudeRef: b"E",
                    piexif.GPSIFD.GPSLongitude: _dms(lon),
                }
            }
            piexif.insert(piexif.dump(exif_dict), str(path))
        ts = _epoch(dt)
        import os

        os.utime(path, (ts, ts))
    pipeline = ScanPipeline(str(root / ".faceframe" / "index.db"), str(root))
    pipeline.run()
    return root, pipeline.store


def _dms(value):
    positive = abs(value)
    degrees = int(positive)
    minutes_full = (positive - degrees) * 60
    minutes = int(minutes_full)
    seconds = int((minutes_full - minutes) * 60)
    ref = b"N" if value >= 0 else b"S"
    return ((degrees, 1), (minutes, 1), (seconds, 1))


def test_geohash_basics():
    from places import geohash

    berlin = geohash(52.52, 13.40, precision=8)
    also_berlin = geohash(52.53, 13.41, precision=8)
    paris = geohash(48.85, 2.35, precision=8)
    assert berlin[:4] == also_berlin[:4]
    assert berlin[:4] != paris[:4]
    assert len(berlin) == 8


def test_place_groups_cluster_photos(geo_lib):
    root, store = geo_lib
    from places import place_groups

    groups = place_groups(store, precision=3)
    named = {g["geohash"]: g for g in groups}
    assert len(named) == 2  # two clusters; no-GPS items excluded
    counts = sorted(g["count"] for g in groups)
    assert counts == [2, 2]
    for g in groups:
        assert g["items"] and g["cover_hash"]


def test_geoname_cache_roundtrip(geo_lib):
    root, store = geo_lib
    from places import set_geoname, get_geoname

    set_geoname(store, "u33", "Berlin, Germany")
    assert get_geoname(store, "u33") == "Berlin, Germany"
    assert get_geoname(store, "u00") is None
    set_geoname(store, "u33", "Berlin")  # update path
    assert get_geoname(store, "u33") == "Berlin"


# ---------------------------------------------------------------------------
# memories
# ---------------------------------------------------------------------------

@pytest.fixture
def memory_lib(tmp_path):
    from scan import ScanPipeline

    root = tmp_path / "library"
    specs = [
        # five years ago, same day: an "on this day" memory
        ("past0.jpg", "2021-09-18 10:00:00", (10, 20, 30)),
        ("past1.jpg", "2021-09-18 11:00:00", (40, 20, 30)),
        # a trip: 6 photos around one place within 3 days this year
        ("trip0.jpg", "2026-08-01 10:00:00", (50, 60, 70)),
        ("trip1.jpg", "2026-08-02 10:00:00", (80, 60, 70)),
        ("trip2.jpg", "2026-08-03 10:00:00", (110, 60, 70)),
        # favorites for highlights
        ("fav0.jpg", "2026-09-01 10:00:00", (140, 60, 70)),
    ]
    for name, dt, color in specs:
        path = write_jpeg(root / name, color=color)
        ts = _epoch(dt)
        import os

        os.utime(path, (ts, ts))
    pipeline = ScanPipeline(str(root / ".faceframe" / "index.db"), str(root))
    pipeline.run()
    with pipeline.store.connect() as conn:
        row = conn.execute(
            "SELECT content_hash FROM files WHERE path='fav0.jpg'"
        ).fetchone()
    from library import LibraryService

    LibraryService(pipeline.store, str(root)).set_favorite([row[0]], True)
    return root, pipeline.store


def test_on_this_day_memory(memory_lib):
    root, store = memory_lib
    from memories import build_memories

    today = (9, 18)  # Sept 18 (see currentDate context)
    memories = build_memories(store, today_month_day=today, now_epoch=time.time())
    kinds = [m["type"] for m in memories]
    assert "on_this_day" in kinds
    otd = [m for m in memories if m["type"] == "on_this_day"][0]
    assert otd["years_ago"] == 5
    assert len(otd["items"]) == 2


def test_trip_memory(memory_lib):
    root, store = memory_lib
    from memories import build_memories

    memories = build_memories(
        store, today_month_day=(9, 18), now_epoch=time.time(), trips=False
    )
    assert all(m["type"] != "trip" for m in memories)
    # With geohashes absent (no GPS in this fixture), trips never appear.


def test_highlights_memory(memory_lib):
    root, store = memory_lib
    from memories import build_memories

    memories = build_memories(
        store, today_month_day=(9, 18), now_epoch=time.time(), on_this_day=False
    )
    highlights = [m for m in memories if m["type"] == "highlights"]
    assert highlights and highlights[0]["items"]
    fav_paths = {i["path"] for i in highlights[0]["items"]}
    assert "fav0.jpg" in fav_paths
