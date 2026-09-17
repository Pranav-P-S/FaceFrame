"""Shared fixtures: tiny synthetic photo libraries on tmp paths."""

import json
import sys
from pathlib import Path

import numpy as np
import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))


def write_jpeg(path: Path, width=64, height=48, color=(200, 120, 40)) -> Path:
    import cv2

    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(abs(hash(str(color))) % (2**32))
    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:, :] = color
    # Texture (noise + gradient) so perceptual hashes have bits to work with,
    # the way real photos do; uniform swatches would collapse dHash.
    img += rng.integers(0, 40, img.shape, dtype=np.uint8)
    img += np.tile(np.linspace(0, 48, width, dtype=np.uint8), (height, 1))[..., None]
    cv2.imwrite(str(path), img)
    return path


@pytest.fixture
def lib(tmp_path):
    """A library root with a few photos and one subfolder."""

    def make(n=3, sub=True):
        root = tmp_path / "library"
        for i in range(n):
            write_jpeg(
                root / f"photo_{i:02d}.jpg",
                color=(30 * i + 60, 100, 180),
            )
        if sub:
            write_jpeg(root / "sub" / "nested.jpg", color=(10, 200, 90))
        return root

    return make


@pytest.fixture
def db(tmp_path):
    from store import Store

    return Store(str(tmp_path / "index.db"))


def face_row(embedding=None, bbox="[10,10,50,50]", det=0.9, thumb=None):
    return {
        "embedding": embedding if embedding is not None else np.random.rand(512).tolist(),
        "bbox": bbox,
        "det_score": det,
        "thumbnail": thumb,
    }


def media_row(hash_hex, kind="photo", w=64, h=48):
    return {
        "content_hash": hash_hex,
        "kind": kind,
        "width": w,
        "height": h,
        "duration": None,
        "capture_time": 1700000000.0,
        "tz_offset": None,
        "exif": json.dumps({"camera": "TestCam"}),
        "phash": "0" * 16,
        "labels": json.dumps([]),
    }
