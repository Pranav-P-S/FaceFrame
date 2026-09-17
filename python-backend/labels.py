"""Optional image labeling for "things" search, Google-Photos style.

A MobileNetV2 (ImageNet-1000) ONNX model — ~14 MB, downloaded once on first
scan exactly like the face model pack — classifies each photo; top labels are
stored per media row and become searchable text. Everything degrades
gracefully: no model, no network, or no onnxruntime simply means no labels.
"""

import logging
import os
import urllib.request
from pathlib import Path

import numpy as np

logger = logging.getLogger("FaceFrame.Labels")

MODEL_URL = (
    "https://github.com/onnx/models/raw/main/validated/vision/classification/"
    "mobilenet/model/mobilenetv2-12.onnx"
)
CLASSES_URL = (
    "https://raw.githubusercontent.com/pytorch/hub/master/imagenet_classes.txt"
)

# A label is kept when it carries at least this softmax share; the single top
# label is always kept so every photo is findable by "what does this show".
LABEL_THRESHOLD = 0.10
INPUT_SIZE = 224
IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class ImageLabeler:
    def __init__(
        self,
        cache_dir: str | None = None,
        auto_download: bool = True,
        session=None,
        class_names: list | None = None,
    ):
        self._session = session
        self._class_names = class_names
        self._input_name = None
        self._dead = False
        if self._session is not None:
            try:
                self._input_name = self._session.get_inputs()[0].name
            except AttributeError:  # test doubles
                self._input_name = self._session.get_input_name()
        if self._session is None:
            cache = Path(
                cache_dir
                or os.environ.get("FACEFRAME_MODELS", str(Path.home() / ".cache" / "faceframe"))
            )
            model_path = cache / "mobilenetv2-12.onnx"
            classes_path = cache / "imagenet_classes.txt"
            try:
                if auto_download:
                    _ensure_model(model_path, classes_path)
                if model_path.is_file() and classes_path.is_file():
                    import onnxruntime

                    self._session = onnxruntime.InferenceSession(
                        str(model_path), providers=["CPUExecutionProvider"]
                    )
                    self._input_name = self._session.get_inputs()[0].name
                    self._class_names = classes_path.read_text(
                        encoding="utf-8"
                    ).splitlines()
            except Exception as e:
                logger.warning("Labeling unavailable: %s", e)
                self._dead = True

    def label(self, img_bgr) -> list:
        """Class labels for one BGR image; [] when labeling is unavailable."""
        if self._session is None or self._dead or self._class_names is None:
            return []
        try:
            batch = _preprocess(img_bgr)
            outputs = self._session.run(None, {self._input_name: batch})
            probs = _softmax(np.asarray(outputs[0][0], dtype=np.float32))
            order = np.argsort(probs)[::-1]
            names = self._class_names
            picked = []
            for rank, idx in enumerate(order[:5]):
                prob = float(probs[idx])
                if rank > 0 and prob < LABEL_THRESHOLD:
                    break
                name = names[int(idx)].strip() if int(idx) < len(names) else None
                if name:
                    picked.append(name)
            return picked
        except Exception as e:
            logger.warning("Labeling failed: %s", e)
            self._dead = True
            return []


def _preprocess(img_bgr) -> np.ndarray:
    import cv2

    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    rgb = cv2.resize(rgb, (INPUT_SIZE, INPUT_SIZE), interpolation=cv2.INTER_AREA)
    arr = rgb.astype(np.float32) / 255.0
    arr = (arr - IMAGENET_MEAN) / IMAGENET_STD
    return arr.transpose(2, 0, 1)[None].astype(np.float32)


def _softmax(values: np.ndarray) -> np.ndarray:
    shifted = values - values.max()
    exp = np.exp(shifted)
    return exp / exp.sum()


def model_ready(cache_dir: str | None = None) -> bool:
    """True when the model files are already on disk (no download implied)."""
    cache = Path(
        cache_dir
        or os.environ.get("FACEFRAME_MODELS", str(Path.home() / ".cache" / "faceframe"))
    )
    return (
        (cache / "mobilenetv2-12.onnx").is_file()
        and (cache / "imagenet_classes.txt").is_file()
    )


def _ensure_model(model_path: Path, classes_path: Path, timeout: int = 90):
    for path, url in ((model_path, MODEL_URL), (classes_path, CLASSES_URL)):
        if path.is_file() and path.stat().st_size > 0:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        logger.info("Downloading %s", url)
        with urllib.request.urlopen(url, timeout=timeout) as response, open(tmp, "wb") as out:
            while True:
                block = response.read(1 << 20)
                if not block:
                    break
                out.write(block)
        tmp.replace(path)
