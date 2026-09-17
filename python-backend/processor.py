import hashlib
import logging
import os

import cv2
import numpy as np
from PIL import Image, ImageOps

logger = logging.getLogger("FaceFrame.Processor")

# Faces smaller than this (pixels) rarely produce a usable embedding and
# mostly turn into cluster noise.
MIN_FACE_SIZE = 28
THUMBNAIL_SIZE = 96


class ModelNotAvailable(Exception):
    """Raised when the InsightFace model pack cannot be loaded."""


class FaceProcessor:
    """Detects faces and computes recognition embeddings for one image.

    Detection + recognition run through InsightFace's buffalo_l pack on
    ONNX Runtime. When CUDA is requested but unusable, initialization
    falls back to CPU and reports what actually happened.
    """

    def __init__(self, use_gpu: bool = False, thumbnail_dir: str | None = None):
        try:
            from insightface.app import FaceAnalysis
        except ImportError as e:
            raise ModelNotAvailable(
                "The insightface package is not installed. "
                "Run: pip install -r python-backend/requirements.txt"
            ) from e

        self.thumbnail_dir = thumbnail_dir
        if thumbnail_dir:
            os.makedirs(thumbnail_dir, exist_ok=True)

        requested = (
            ["CUDAExecutionProvider", "CPUExecutionProvider"]
            if use_gpu
            else ["CPUExecutionProvider"]
        )
        self.providers = self._init_with_fallback(FaceAnalysis, requested)
        logger.info("FaceProcessor ready (providers: %s)", ", ".join(self.providers))

    def _init_with_fallback(self, face_analysis_cls, requested):
        try:
            self.app = face_analysis_cls(
                name="buffalo_l",
                providers=requested,
                allowed_modules=["detection", "recognition"],
            )
            self.app.prepare(ctx_id=0, det_size=(640, 640))
            return requested
        except Exception as e:
            if requested == ["CPUExecutionProvider"]:
                raise ModelNotAvailable(f"Could not load the face model: {e}") from e
            logger.warning("GPU initialization failed (%s); falling back to CPU.", e)
            return self._init_with_fallback(
                face_analysis_cls, ["CPUExecutionProvider"]
            )

    def process_image(self, image_path: str):
        """Return a list of face dicts for one image (empty if no faces)."""
        img = read_image_bgr(image_path)
        if img is None:
            return []
        return self.process_decoded(image_path, img)

    def process_decoded(self, image_path: str, img: np.ndarray, face_key: str | None = None):
        """Same as process_image, for callers that already decoded the file.

        Returns None when detection itself failed (caller should retry the
        file on a later scan); an empty list means the image simply has no
        faces. ``face_key`` names thumbnails by content hash instead of path,
        so moves/renames never orphan or duplicate face crops.
        """
        try:
            faces = self.app.get(img)
        except Exception as e:
            logger.error("Detection failed for %s: %s", image_path, e)
            return None

        height, width = img.shape[:2]
        results = []
        for idx, face in enumerate(faces):
            x1, y1, x2, y2 = (int(v) for v in face.bbox)
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(width, x2), min(height, y2)
            if x2 - x1 < MIN_FACE_SIZE or y2 - y1 < MIN_FACE_SIZE:
                continue

            face_crop = img[y1:y2, x1:x2]
            embedding = getattr(face, "normed_embedding", None)
            if embedding is None:
                embedding = face.embedding
            if embedding is None:
                continue

            results.append(
                {
                    "embedding": np.asarray(embedding, dtype=np.float32).tolist(),
                    "bbox": [x1, y1, x2, y2],
                    "det_score": float(face.det_score),
                    "thumbnail": self._save_thumbnail(
                        face_key if face_key else image_path, idx, face_crop
                    ),
                }
            )

        if results:
            logger.info(
                "Found %d face(s) in %s", len(results), os.path.basename(image_path)
            )
        return results

    def _save_thumbnail(self, key: str, idx: int, face_crop: np.ndarray):
        if not self.thumbnail_dir or face_crop.size == 0:
            return None
        digest = hashlib.sha1(
            f"{key}#{idx}".encode("utf-8", "surrogateescape")
        ).hexdigest()[:64]
        thumb_path = os.path.join(self.thumbnail_dir, f"{digest}.jpg")
        try:
            # The dir is normally created by the pipeline; a stale index
            # cleared underneath us must not lose the face over it.
            os.makedirs(self.thumbnail_dir, exist_ok=True)
            thumb = cv2.resize(
                face_crop,
                (THUMBNAIL_SIZE, THUMBNAIL_SIZE),
                interpolation=cv2.INTER_AREA,
            )
            return _write_jpeg(thumb_path, thumb, quality=88)
        except Exception as e:
            logger.warning("Thumbnail save failed for %s: %s", key, e)
            return None

    @staticmethod
    def compute_info():
        """What hardware can actually be used, as far as we can tell."""
        import onnxruntime

        providers = onnxruntime.get_available_providers()
        return {
            "providers": providers,
            "cuda_listed": "CUDAExecutionProvider" in providers,
            # A CUDA provider being listed does not guarantee the CUDA /
            # cuDNN runtime libs are present; initialization falls back to
            # CPU automatically when they are missing.
            "device_label": _device_label(providers),
        }


def read_image_bgr(image_path: str):
    """Decode an image honoring its EXIF orientation, as BGR. None on failure."""
    try:
        with Image.open(image_path) as pil_img:
            pil_img = ImageOps.exif_transpose(pil_img)
            if pil_img.mode != "RGB":
                pil_img = pil_img.convert("RGB")
            rgb = np.asarray(pil_img)
    except FileNotFoundError:
        logger.warning("File vanished during scan: %s", image_path)
        return None
    except Exception as e:
        logger.warning("Could not decode %s: %s", image_path, e)
        return None
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _write_jpeg(path: str, img: np.ndarray, quality: int):
    """Encode + write a JPEG, surviving non-ANSI paths on Windows
    (cv2.imwrite uses ANSI fopen there). Returns the path or None."""
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        return None
    buf.tofile(path)
    return path


def generate_preview(
    image_path: str, previews_dir: str, max_dim: int = 640
):
    """Return a cached, downscaled JPEG for display in the UI.

    Every image the app shows flows through here, which bounds memory (no
    full-resolution photos travel to the renderer) and makes formats
    Chromium cannot display natively (TIFF) viewable anyway. The cache key
    includes the source's mtime and size, so editing a photo in place
    invalidates its preview on the next view.
    """
    try:
        stat = os.stat(image_path)
        stamp = f"{stat.st_mtime:.0f}#{stat.st_size}"
    except OSError:
        return None
    digest = hashlib.sha1(
        f"{os.path.abspath(image_path)}#{stamp}#{max_dim}".encode(
            "utf-8", "surrogateescape"
        )
    ).hexdigest()[:16]
    preview_path = os.path.join(previews_dir, f"{digest}.jpg")
    if os.path.isfile(preview_path):
        return preview_path

    img = read_image_bgr(image_path)
    if img is None:
        return None
    height, width = img.shape[:2]
    scale = max_dim / max(height, width)
    if scale < 1.0:
        img = cv2.resize(
            img,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    try:
        os.makedirs(previews_dir, exist_ok=True)
        return _write_jpeg(preview_path, img, quality=82)
    except OSError as e:
        logger.warning("Preview save failed for %s: %s", image_path, e)
        return None


def _device_label(providers):
    if "CUDAExecutionProvider" in providers:
        return "NVIDIA GPU (CUDA)"
    if "DmlExecutionProvider" in providers:
        return "DirectML"
    if "CoreMLExecutionProvider" in providers:
        return "Apple CoreML"
    return "CPU"
