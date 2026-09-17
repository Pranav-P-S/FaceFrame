"""Creations: collages and animations built from library items.

Creation files live in ``.faceframe/creations`` so the scanner never walks
them (no re-index churn), while files/media/creations rows make them
first-class items in every view. Deleting a creation is the one delete that
unlinks directly — it only ever touches FaceFrame's own generated files.
"""

import logging
import time
from pathlib import Path

import pathio
from hashing import content_hash
from store import Store

logger = logging.getLogger("FaceFrame.Creations")

COLLAGE_TEMPLATES = {
    1: [(0, 0, 1, 1)],
    2: [(0, 0, 0.5, 1), (0.5, 0, 0.5, 1)],
    3: [(0, 0, 0.5, 1), (0.5, 0, 0.5, 0.5), (0.5, 0.5, 0.5, 0.5)],
    4: [(0, 0, 0.5, 0.5), (0.5, 0, 0.5, 0.5), (0, 0.5, 0.5, 0.5), (0.5, 0.5, 0.5, 0.5)],
}
COLLAGE_SIZE = 1600
CELL_GAP = 8


def make_collage(store: Store, library_root: str, content_hashes: list) -> dict:
    import cv2
    import numpy as np

    if not 1 <= len(content_hashes) <= 4:
        raise ValueError("A collage takes 1 to 4 photos")

    cells = COLLAGE_TEMPLATES[len(content_hashes)]
    canvas = np.full((COLLAGE_SIZE, COLLAGE_SIZE, 3), 255, dtype=np.uint8)
    for cell, content_hash in zip(cells, content_hashes):
        x, y, w, h = cell
        img = _load_bgr(store, library_root, content_hash)
        if img is None:
            continue
        cell_w = int(COLLAGE_SIZE * w) - CELL_GAP
        cell_h = int(COLLAGE_SIZE * h) - CELL_GAP
        img = _fit_cover(img, cell_w, cell_h)
        px = int(COLLAGE_SIZE * x) + CELL_GAP // 2
        py = int(COLLAGE_SIZE * y) + CELL_GAP // 2
        canvas[py : py + img.shape[0], px : px + img.shape[1]] = img

    rel_path = _register_creation(
        store, library_root, canvas, "collage", ".jpg",
        params={"sources": list(content_hashes)},
    )
    return rel_path


def make_animation(
    store: Store, library_root: str, content_hashes: list, frame_duration_ms: int = 500
) -> dict:
    from PIL import Image

    if len(content_hashes) < 2:
        raise ValueError("An animation takes at least 2 photos")
    frames = []
    for content_hash in content_hashes:
        img = _load_bgr(store, library_root, content_hash)
        if img is None:
            continue
        img = _fit_cover(img, 640, 640)
        frames.append(Image.fromarray(img[:, :, ::-1]))
    if len(frames) < 2:
        raise ValueError("Not enough readable photos for an animation")

    def _emit(canvas):
        return canvas

    # PIL needs the output path up front; create the file, then register.
    creations_dir = Path(library_root) / ".faceframe" / "creations"
    creations_dir.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time() * 1000)
    rel_path = f".faceframe/creations/anim_{stamp}.gif"
    out_path = Path(library_root) / rel_path
    frames[0].save(
        str(out_path),
        save_all=True,
        append_images=frames[1:],
        duration=frame_duration_ms,
        loop=0,
    )
    return _register_creation_rows(
        store, str(out_path), rel_path, "animation",
        params={"sources": list(content_hashes), "duration": frame_duration_ms},
    )


def delete_creation(store: Store, library_root: str, creation_id: int):
    with store.connect() as conn:
        row = conn.execute(
            "SELECT path, content_hash FROM creations WHERE id=?", (creation_id,)
        ).fetchone()
        conn.execute("DELETE FROM creations WHERE id=?", (creation_id,))
    if not row:
        return
    absolute = Path(library_root) / row["path"]
    try:
        if absolute.is_file():
            absolute.unlink()
    except OSError as e:
        logger.warning("Could not unlink creation file: %s", e)
    store.remove_file(row["path"])


# ---------------------------------------------------------------- helpers

def _register_creation(store: Store, library_root: str, canvas, kind: str, suffix: str, params: dict) -> dict:
    import cv2

    creations_dir = Path(library_root) / ".faceframe" / "creations"
    creations_dir.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time() * 1000)
    rel_path = f".faceframe/creations/{kind}_{stamp}{suffix}"
    out_path = Path(library_root) / rel_path
    ok, buf = cv2.imencode(suffix, canvas, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        raise RuntimeError("Could not encode creation")
    buf.tofile(str(out_path))
    return _register_creation_rows(store, str(out_path), rel_path, kind, params)


def _register_creation_rows(store: Store, out_path: str, rel_path: str, kind: str, params: dict) -> dict:
    hash_hex = content_hash(out_path)
    size = Path(out_path).stat().st_size
    now = time.time()
    store.upsert_media(
        {
            "content_hash": hash_hex,
            "kind": "photo",
            "capture_time": now,
            "analysis_state": "analyzed",
            "analyzed_at": now,
            "exif": _dumps({"creation": kind}),
            "labels": _dumps([]),
        }
    )
    store.upsert_file(rel_path, hash_hex, mtime=now, size=size, kind="creation")
    with store.connect() as conn:
        cur = conn.execute(
            """INSERT INTO creations (kind, path, content_hash, params, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (kind, rel_path, hash_hex, _dumps(params), now),
        )
        creation_id = cur.lastrowid
    return {"id": creation_id, "path": rel_path, "content_hash": hash_hex}


def _load_bgr(store: Store, library_root: str, content_hash: str):
    """Best-effort decode of a member item's first file."""
    import cv2

    with store.connect() as conn:
        row = conn.execute(
            "SELECT path FROM files WHERE content_hash=? AND missing=0 ORDER BY path",
            (content_hash,),
        ).fetchone()
    if not row:
        return None
    from scan import read_image_bgr

    return read_image_bgr(str(Path(library_root) / row["path"]))


def _fit_cover(img, target_w: int, target_h: int):
    import cv2

    height, width = img.shape[:2]
    scale = max(target_w / width, target_h / height)
    resized = cv2.resize(
        img,
        (max(1, round(width * scale)), max(1, round(height * scale))),
        interpolation=cv2.INTER_AREA,
    )
    y0 = (resized.shape[0] - target_h) // 2
    x0 = (resized.shape[1] - target_w) // 2
    return resized[y0 : y0 + target_h, x0 : x0 + target_w]


def _dumps(value) -> str:
    import json

    return value if isinstance(value, str) else json.dumps(value)
