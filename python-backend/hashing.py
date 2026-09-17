"""Content addressing: sha256 file hashes and 64-bit dHash perceptual hashes.

The content hash is the unit of expensive work in the index (decode, embed,
thumbnail, label): it is computed once per unique byte sequence and every
derived artifact hangs off it. The perceptual hash exists for near-duplicate
and burst grouping; it is coarse by design.
"""

import hashlib
import os

CHUNK = 1 << 20


def content_hash(path: str) -> str:
    """SHA-256 hex digest of a file's bytes, read in chunks."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def perceptual_hash(img_bgr) -> str:
    """64-bit difference hash (dHash) of a BGR image, as 16 hex chars.

    Downscale to 9x8 grayscale, compare each pixel to its right neighbor:
    64 comparison bits that survive rescale/recompress noise.
    """
    import cv2
    import numpy as np

    small = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(small, (9, 8), interpolation=cv2.INTER_AREA)
    diff = small[:, 1:] > small[:, :-1]
    value = int(np.packbits(diff.flatten()).tobytes().hex(), 16)
    return f"{value:016x}"


def hamming(hash_a: str, hash_b: str) -> int:
    """Bit distance between two 16-char hex hashes."""
    return bin(int(hash_a, 16) ^ int(hash_b, 16)).count("1")


def similar(hash_a: str, hash_b: str, threshold: int = 8) -> bool:
    return hamming(hash_a, hash_b) <= threshold
