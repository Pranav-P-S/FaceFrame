"""Slice 3 — creations: collages, animations, and their lifecycle."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import write_jpeg


@pytest.fixture
def scanned_lib(tmp_path):
    from scan import ScanPipeline

    root = tmp_path / "library"
    for i in range(4):
        write_jpeg(root / f"p{i}.jpg", color=(10 * i + 40, 80, 160))
    pipeline = ScanPipeline(str(root / ".faceframe" / "index.db"), str(root))
    pipeline.run()
    with pipeline.store.connect() as conn:
        hashes = [
            r[0]
            for r in conn.execute("SELECT content_hash FROM files ORDER BY path")
        ]
    return root, pipeline.store, hashes


def test_collage_is_created_indexed_and_deletable(scanned_lib):
    from creations import make_collage

    root, store, hashes = scanned_lib
    creation = make_collage(store, str(root), hashes[:4])
    collage_path = root / creation["path"]
    assert collage_path.is_file() and collage_path.stat().st_size > 0

    # Registered as a first-class item
    assert store.get_media(creation["content_hash"]) is not None
    with store.connect() as conn:
        row = conn.execute(
            "SELECT kind FROM files WHERE content_hash=?",
            (creation["content_hash"],),
        ).fetchone()
    assert row["kind"] == "creation"

    # A rescan neither removes nor duplicates it (creations live outside the walk)
    from scan import ScanPipeline

    ScanPipeline(str(root / ".faceframe" / "index.db"), str(root)).run()
    with store.connect() as conn:
        missing = conn.execute(
            "SELECT missing FROM files WHERE content_hash=?",
            (creation["content_hash"],),
        ).fetchone()[0]
        count = conn.execute(
            "SELECT COUNT(*) FROM files WHERE content_hash=?",
            (creation["content_hash"],),
        ).fetchone()[0]
    assert missing == 0 and count == 1

    from creations import delete_creation

    delete_creation(store, str(root), creation["id"])
    assert not collage_path.exists()
    assert store.get_media(creation["content_hash"]) is None


def test_animation_gif_from_photos(scanned_lib):
    from creations import make_animation

    root, store, hashes = scanned_lib
    creation = make_animation(store, str(root), hashes, frame_duration_ms=120)
    gif = root / creation["path"]
    assert gif.is_file() and gif.suffix == ".gif"
    from PIL import Image

    with Image.open(gif) as im:
        assert getattr(im, "n_frames", 1) == 4
    delete_ok = True
    from creations import delete_creation

    delete_creation(store, str(root), creation["id"])
    assert not gif.exists()
