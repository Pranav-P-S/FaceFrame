"""Render pipeline: every pixel the UI (or an export) sees flows through
here, with the item's stored edit applied. Originals are never touched — an
edit is a JSON document; rendering is a pure function of (content, edit),
and the cache filename is a digest of exactly that pair.
"""

import hashlib
import logging
import os
from pathlib import Path

import numpy as np

import pathio

logger = logging.getLogger("FaceFrame.Render")

PREVIEW_DIR = "previews"

# Named filter presets: adjust overrides applied on top of the user's.
FILTERS = {
    "none": {},
    "auto": {"contrast": 1.08, "saturation": 1.12, "sharpen": 0.4},
    "vivid": {"saturation": 1.35, "contrast": 1.15},
    "natural": {"saturation": 1.05, "contrast": 1.02},
    "warm": {"warmth": 0.18, "saturation": 1.1},
    "cool": {"warmth": -0.18},
    "golden": {"warmth": 0.3, "vignette": 0.25},
    "mono": {"saturation": 0.0, "contrast": 1.1},
    "mono_high": {"saturation": 0.0, "contrast": 1.35},
    "sepia": {"saturation": 0.0, "warmth": 0.35, "vignette": 0.2},
    "fade": {"contrast": 0.85, "brightness": 1.06, "saturation": 0.85},
    "cinema": {"contrast": 1.2, "saturation": 0.85, "warmth": -0.08,
               "vignette": 0.3},
    "dreamy": {"saturation": 1.15, "brightness": 1.08, "sharpen": -0.3,
               "vignette": 0.15},
    "noir": {"saturation": 0.0, "contrast": 1.5, "vignette": 0.4},
}


def render_preview(
    store,
    library_root: str,
    rel_path: str,
    max_dim: int = 640,
    square: bool = False,
    edit: dict | None = None,
    quality: int = 84,
):
    """Return a cached JPEG path for (content, edit, size). Raises nothing:
    returns None when the source cannot be read."""
    import cv2

    state = store.get_file_state(rel_path)
    content_hash = state["content_hash"] if state else None
    if not content_hash:
        # Unindexed path: refuse rather than guess — the UI only shows indexed items.
        return None

    edit = edit or {}
    key_source = f"{content_hash}#{max_dim}#{int(square)}#{_edit_digest(edit)}"
    # Full 64-char content hash: the scan-time GC recognizes cached files by
    # exact hash membership, so the name must carry the whole digest.
    cache_name = f"{content_hash}_{_digest16(key_source)}.jpg"
    previews_dir = Path(library_root) / ".faceframe" / PREVIEW_DIR
    cache_path = previews_dir / cache_name
    if cache_path.is_file():
        return str(cache_path)

    img = _load_image(store, library_root, rel_path, content_hash)
    if img is None:
        return None

    if edit:
        img = _apply_edit(img, edit)

    height, width = img.shape[:2]
    if square:
        img = _cover_square(img, max_dim)
    else:
        scale = max_dim / max(height, width)
        if scale < 1.0:
            img = cv2.resize(
                img,
                (max(1, round(width * scale)), max(1, round(height * scale))),
                interpolation=cv2.INTER_AREA,
            )

    previews_dir.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        return None
    buf.tofile(str(cache_path))
    return str(cache_path)


def magic_eraser_preview(
    store, library_root: str, rel_path: str, rects: list, max_dim: int = 1600
):
    """One-off inpaint preview (not a saved edit). Rects are normalized
    [x, y, w, h] in image coordinates."""
    import cv2

    state = store.get_file_state(rel_path)
    if not state or not state["content_hash"]:
        return None
    img = _load_image(store, library_root, rel_path, state["content_hash"])
    if img is None:
        return None
    height, width = img.shape[:2]
    scale = min(1.0, max_dim / max(height, width))
    if scale < 1.0:
        img = cv2.resize(
            img,
            (round(width * scale), round(height * scale)),
            interpolation=cv2.INTER_AREA,
        )
    img = _inpaint_rects(img, rects)
    previews_dir = Path(library_root) / ".faceframe" / PREVIEW_DIR
    previews_dir.mkdir(parents=True, exist_ok=True)
    digest = _digest16(f"eraser#{state['content_hash']}#{rects}#{max_dim}")
    out = previews_dir / f"tmp_era_{digest}.jpg"
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        return None
    buf.tofile(str(out))
    return str(out)


def export_baked(
    store, library_root: str, rel_path: str, dest_dir: str,
    edit: dict | None = None, original: bool = False,
):
    """Write the item's current pixels (or its untouched original) into
    dest_dir under its own filename."""
    import cv2

    state = store.get_file_state(rel_path)
    if not state or not state["content_hash"]:
        raise FileNotFoundError(rel_path)
    source_name = Path(rel_path).name
    dest = Path(dest_dir) / source_name
    if original or not edit or not _edit_digest(edit):
        import shutil

        shutil.copyfile(str(Path(library_root) / rel_path), str(dest))
        return str(dest)
    img = _load_image(store, library_root, rel_path, state["content_hash"])
    if img is None:
        raise ValueError("Undecodable source")
    img = _apply_edit(img, edit)
    params = [cv2.IMWRITE_JPEG_QUALITY, 92]
    ok, buf = cv2.imencode(".jpg", img, params)
    if not ok:
        raise ValueError("Encode failed")
    buf.tofile(str(dest))
    return str(dest)


# ------------------------------------------------------------------ edits

def _apply_edit(img, edit: dict):
    import cv2
    import numpy as np

    # Eraser rects are normalized to the full image: inpaint before any
    # geometry so saved erasures render identically in previews and exports.
    if edit.get("eraser"):
        img = _inpaint_rects(img, edit["eraser"])

    filter_name = edit.get("filter")
    adjust = dict(edit.get("adjust") or {})
    if filter_name and filter_name in FILTERS:
        adjust = {**FILTERS[filter_name], **adjust}

    img = _apply_geometry(img, edit)

    brightness = float(adjust.get("brightness", 1.0))
    contrast = float(adjust.get("contrast", 1.0))
    saturation = float(adjust.get("saturation", 1.0))
    warmth = float(adjust.get("warmth", 0.0))
    vignette = float(adjust.get("vignette", 0.0))
    sharpen = float(adjust.get("sharpen", 0.0))
    highlights = float(adjust.get("highlights", 0.0))
    shadows = float(adjust.get("shadows", 0.0))

    if highlights != 0.0 or shadows != 0.0:
        img = _apply_tone(img, highlights, shadows)
    if saturation != 1.0:
        img = _saturation(img, saturation)
    if warmth != 0.0:
        img = _warmth(img, warmth)
    if contrast != 1.0:
        img = cv2.convertScaleAbs(img, alpha=contrast, beta=0)
    if brightness != 1.0:
        img = cv2.convertScaleAbs(img, alpha=brightness, beta=0)
    if sharpen != 0.0:
        img = _sharpen(img, sharpen)
    if vignette > 0.0:
        img = _vignette(img, vignette)
    return img


def _apply_geometry(img, edit: dict):
    import cv2

    crop = edit.get("crop")
    if crop:
        x, y, w, h = (float(v) for v in crop)
        height, width = img.shape[:2]
        x0, y0 = int(x * width), int(y * height)
        x1, y1 = int((x + w) * width), int((y + h) * height)
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(width, x1), min(height, y1)
        if x1 - x0 > 0 and y1 - y0 > 0:
            img = img[y0:y1, x0:x1]

    straighten = float(edit.get("straighten", 0.0) or 0.0)
    if straighten:
        img = _straighten(img, straighten)

    rotate = int(edit.get("rotate", 0) or 0) % 360
    if rotate == 90:
        img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    elif rotate == 180:
        img = cv2.rotate(img, cv2.ROTATE_180)
    elif rotate == 270:
        img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)

    if edit.get("flip_h"):
        img = cv2.flip(img, 1)
    if edit.get("flip_v"):
        img = cv2.flip(img, 0)
    return img


def _straighten(img, degrees: float):
    import cv2

    height, width = img.shape[:2]
    center = (width // 2, height // 2)
    matrix = cv2.getRotationMatrix2D(center, degrees, 1.0)
    # Zoom in enough that corners stay covered (cos/sin bound).
    cos, sin = abs(matrix[0, 0]), abs(matrix[0, 1])
    scale = max(1.0, (width * sin + height * cos) / width,
                (width * cos + height * sin) / height)
    matrix[0, 2] += (scale - 1.0) * width / 2
    matrix[1, 2] += (scale - 1.0) * height / 2
    return cv2.warpAffine(img, matrix, (width, height),
                          flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)


def _saturation(img, factor: float):
    import cv2

    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[..., 1] *= factor
    hsv[..., 1] = np.clip(hsv[..., 1], 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


def _warmth(img, amount: float):
    """Positive warms (r+ / b-), negative cools."""
    warmed = img.astype(np.int16)
    warmed[..., 2] = np.clip(warmed[..., 2] + amount * 60, 0, 255)
    warmed[..., 0] = np.clip(warmed[..., 0] - amount * 60, 0, 255)
    return warmed.astype(np.uint8)


def _apply_tone(img, highlights: float, shadows: float):
    """Approximate highlight/shadow recovery via luma gamma masks."""
    import cv2

    luma = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    result = img.astype(np.float32)
    if highlights != 0.0:
        mask = np.clip((luma - 0.5) * 2.0, 0, 1)[..., None]
        result += mask * highlights * 80.0
    if shadows != 0.0:
        mask = np.clip((0.5 - luma) * 2.0, 0, 1)[..., None]
        result += mask * shadows * 80.0
    return np.clip(result, 0, 255).astype(np.uint8)


def _sharpen(img, amount: float):
    import cv2

    if amount == 0.0:
        return img
    blur = cv2.GaussianBlur(img, (0, 0), 2.0)
    # Negative amounts approximate softening.
    return cv2.addWeighted(img, 1.0 + amount, blur, -amount, 0)


def _vignette(img, strength: float):
    import cv2

    height, width = img.shape[:2]
    y, x = np.mgrid[0:height, 0:width]
    cx, cy = width / 2, height / 2
    radius = np.sqrt(((x - cx) / cx) ** 2 + ((y - cy) / cy) ** 2)
    mask = 1.0 - strength * np.clip(radius - 0.4, 0, 1.2) ** 2
    return np.clip(img.astype(np.float32) * mask[..., None], 0, 255).astype(np.uint8)


def _inpaint_rects(img, rects: list):
    import cv2
    import numpy as np

    if not rects:
        return img
    height, width = img.shape[:2]
    mask = np.zeros((height, width), dtype=np.uint8)
    for rect in rects:
        x, y, w, h = (float(v) for v in rect[:4])
        x0, y0 = max(0, int(x * width)), max(0, int(y * height))
        x1, y1 = min(width, int((x + w) * width)), min(height, int((y + h) * height))
        if x1 > x0 and y1 > y0:
            mask[y0:y1, x0:x1] = 255
    if not mask.any():
        return img
    return cv2.inpaint(img, mask, 5, cv2.INPAINT_TELEA)


def _cover_square(img, size: int):
    import cv2

    height, width = img.shape[:2]
    side = min(height, width)
    y0 = (height - side) // 2
    x0 = (width - side) // 2
    img = img[y0 : y0 + side, x0 : x0 + side]
    return cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)


def _load_image(store, library_root: str, rel_path: str, content_hash: str):
    """Photos decode directly; videos prefer their scan-time poster frame
    (decoding the video per preview was the worst first-scroll jank)."""
    import cv2

    from scan import read_image_bgr

    state = store.get_file_state(rel_path)
    if state and state["kind"] == "video":
        media = store.get_media(content_hash)
        poster = media["poster_path"] if media else None
        if poster:
            poster_abs = Path(library_root) / poster
            if poster_abs.is_file():
                return cv2.imread(str(poster_abs))
        return _video_poster(str(Path(library_root) / rel_path), content_hash)
    absolute = str(Path(library_root) / rel_path)
    return read_image_bgr(absolute)


def _video_poster(absolute_path: str, content_hash: str):
    import cv2

    capture = cv2.VideoCapture(absolute_path)
    try:
        if not capture.isOpened():
            return None
        ok, frame = capture.read()
        return frame if ok else None
    finally:
        capture.release()


# ------------------------------------------------------------------ utils

def _edit_digest(edit: dict) -> str:
    import json

    if not edit:
        return "0"
    return hashlib.sha256(
        json.dumps(edit, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def _digest16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
