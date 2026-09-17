"""Slice 2 — the optional labeler ("things" search). Offline-deterministic:
the ONNX session is injected; download paths are only exercised for URL
constants and graceful failure."""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class FakeSession:
    def __init__(self, probs):
        self.probs = np.asarray(probs, dtype=np.float32)
        self.inputs = None

    def run(self, _outputs, inputs):
        self.inputs = inputs
        batch = inputs[self.get_input_name()]
        return [np.tile(self.probs, (batch.shape[0], 1))]

    def get_input_name(self):
        return "input"


def make_labeler(probs, n_classes=1000):
    from labels import ImageLabeler

    session = FakeSession(probs)
    classes = [f"class_{i}" for i in range(n_classes)]
    return ImageLabeler(session=session, class_names=classes), session


def test_label_maps_top_predictions_above_threshold():
    # Sessions emit logits; build them so softmax recovers the intended
    # distribution exactly.
    probs = np.full(1000, 1e-45, dtype=np.float64)
    probs[207] = 0.70
    probs[340] = 0.20
    probs[12] = 0.05  # below threshold -> dropped
    probs /= probs.sum()
    logits = np.log(probs).astype(np.float32)
    labeler, session = make_labeler(logits)
    img = np.zeros((48, 64, 3), dtype=np.uint8)
    labels = labeler.label(img)
    assert labels == ["class_207", "class_340"]
    batch = session.inputs["input"]
    assert batch.shape == (1, 3, 224, 224)
    assert -3.0 < float(batch.min()) and float(batch.max()) < 3.0  # normalized


def test_label_always_includes_top_even_if_low():
    probs = np.full(1000, 0.001, dtype=np.float32)
    probs[5] = 0.02
    labeler, _ = make_labeler(probs)
    img = np.zeros((48, 64, 3), dtype=np.uint8)
    assert labeler.label(img) == ["class_5"]


def test_session_construction_failure_is_graceful(tmp_path):
    from labels import ImageLabeler

    # No model file, downloads disabled: the labeler builds to a no-op.
    labeler = ImageLabeler(
        cache_dir=str(tmp_path), auto_download=False, class_names=["a"]
    )
    img = np.zeros((48, 64, 3), dtype=np.uint8)
    assert labeler.label(img) == []


def test_model_urls_are_https():
    from labels import MODEL_URL, CLASSES_URL

    assert MODEL_URL.startswith("https://")
    assert CLASSES_URL.startswith("https://")
