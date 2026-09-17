"""Slice 6 — render pipeline: cached previews with non-destructive edits."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import write_jpeg


@pytest.fixture
def photo_lib(tmp_path):
    from scan import ScanPipeline

    root = tmp_path / "library"
    path = write_jpeg(root / "photo.jpg", width=320, height=200, color=(120, 90, 60))
    pipeline = ScanPipeline(str(root / ".faceframe" / "index.db"), str(root))
    pipeline.run()
    return root, pipeline.store, path


def test_basic_preview_cached_by_content(photo_lib):
    from render import render_preview

    root, store, path = photo_lib
    out1 = render_preview(store, str(root), "photo.jpg", max_dim=256)
    out2 = render_preview(store, str(root), "photo.jpg", max_dim=256)
    assert out1 == out2 and Path(out1).is_file()
    import cv2

    img = cv2.imread(out1)
    assert max(img.shape[:2]) <= 256


def test_square_crop_thumb(photo_lib):
    from render import render_preview
    import cv2

    root, store, path = photo_lib
    out = render_preview(store, str(root), "photo.jpg", max_dim=128, square=True)
    img = cv2.imread(out)
    assert img.shape[0] == img.shape[1] == 128


def test_edit_transforms_change_output_and_invalidate_cache(photo_lib):
    from render import render_preview

    root, store, path = photo_lib
    base = render_preview(store, str(root), "photo.jpg", max_dim=256)
    edited = render_preview(
        store, str(root), "photo.jpg", max_dim=256,
        edit={"rotate": 90, "adjust": {"brightness": 1.5}},
    )
    assert edited != base and Path(edited).is_file()
    # Re-render with the same edit: same cache file
    again = render_preview(
        store, str(root), "photo.jpg", max_dim=256,
        edit={"adjust": {"brightness": 1.5}, "rotate": 90},
    )
    assert again == edited


def test_crop_semantics(photo_lib):
    from render import render_preview, _apply_edit
    import cv2

    root, store, path = photo_lib
    img = cv2.imread(str(path))
    # Center half crop, normalized
    out = _apply_edit(
        img,
        {"crop": [0.25, 0.25, 0.5, 0.5]},
    )
    assert out.shape[0] == 100 and out.shape[1] == 160


def test_rotate90_swaps_dimensions(photo_lib):
    from render import _apply_edit

    img = np.zeros((200, 320, 3), dtype=np.uint8)
    out = _apply_edit(img, {"rotate": 90})
    assert out.shape[0] == 320 and out.shape[1] == 200


def test_adjust_acts(photo_lib):
    from render import _apply_edit

    img = np.full((40, 40, 3), 100, dtype=np.uint8)
    brighter = _apply_edit(img, {"adjust": {"brightness": 1.5}})
    assert brighter.mean() > img.mean()
    mono = _apply_edit(img, {"adjust": {"saturation": 0.0}})
    assert mono.std() < img.std() or mono.shape[-1] == 3  # gray-ish


def test_filter_presets_exist_and_differ(photo_lib):
    from render import FILTERS, _apply_edit

    assert len(FILTERS) >= 12
    img = np.zeros((40, 40, 3), dtype=np.uint8)
    img[:, :] = (120, 90, 60)
    outputs = {
        name: _apply_edit(img, {"filter": name}).tobytes()
        for name in ("vivid", "mono", "warm")
    }
    assert len(set(outputs.values())) == 3


def test_magic_eraser_removes_region(photo_lib):
    from render import magic_eraser_preview
    import cv2

    root, store, path = photo_lib
    # Paint a bright square to erase
    img = cv2.imread(str(path))
    img[80:120, 130:190] = 255
    cv2.imwrite(str(path), img)
    # Re-index the new content
    from scan import ScanPipeline

    ScanPipeline(str(root / ".faceframe" / "index.db"), str(root)).run()

    out = magic_eraser_preview(
        store, str(root), "photo.jpg", rects=[[130 / 320, 80 / 200, 60 / 320, 40 / 200]],
        max_dim=320,
    )
    assert Path(out).is_file()
    rendered = cv2.imread(out)
    # The bright square should be gone (replaced by inpainted surroundings)
    region = rendered[80:120, 130:190]
    assert region.mean() < 240


def test_export_bakes_edits(photo_lib, tmp_path):
    from render import export_baked

    root, store, path = photo_lib
    dest = tmp_path / "export"
    dest.mkdir()
    out = export_baked(
        store, str(root), "photo.jpg", str(dest),
        edit={"rotate": 90}, original=False,
    )
    assert Path(out).is_file()
    import cv2

    img = cv2.imread(out)
    assert img.shape[0] == 320 and img.shape[1] == 200
