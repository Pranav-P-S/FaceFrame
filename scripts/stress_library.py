"""Generate a synthetic stress library for FaceFrame.

Layout produced at the target path:
  - N photo FILES total (default 3000, duplicates included):
      ~5% exact byte-identical duplicate copies (same sha256, different
      name + folder), spread over ~120 day folders spanning 2 years.
      Dimensions rotate through 640x480 ... 4000x3000; JPEG quality varies.
      ~60% carry EXIF DateTimeOriginal + GPS (piexif); file mtime is set to
      the EXIF capture time.
  - 30 short MJPG .avi videos (64x48, 30 frames).
  - Adversarial names: unicode, spaces, percent, apostrophes, a 180-char
  filename, and files nested 40 levels deep.

A manifest (stress_manifest.json) with every file's sha256, mtime, capture
time and duplicate lineage is written next to the library so the e2e
harness can verify counts and relinks without re-hashing.

Usage:
    venv/Scripts/python.exe scripts/stress_library.py \
        [--out test-data/stress-library-3000] [--photos 3000]
        [--videos 30] [--seed 42] [--force]
"""

import argparse
import calendar
import hashlib
import json
import os
import random
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
import piexif

ROOT = Path(__file__).resolve().parent.parent

# (w, h, weight) - varied dimensions from 640x480 up to 4000x3000
DIMENSIONS = [
    (640, 480, 15),
    (1024, 768, 20),
    (1280, 960, 22),
    (1600, 1200, 25),
    (2560, 1920, 12),
    (4000, 3000, 6),
]
_DIM_POP = []
for i, (_, _, w) in enumerate(DIMENSIONS):
    _DIM_POP.extend([i] * w)

THEMES = [
    "beach", "hike", "birthday", "roadtrip", "gardening", "ski", "concert",
    "picnic", "citywalk", "kitchen", "sunset", "museum", "sports", "pets",
]

GPS_SPOTS = [
    (48.8584, 2.2945),    # Paris
    (40.7484, -73.9857),  # NYC
    (35.6586, 139.7454),  # Tokyo
    (52.5163, 13.3777),   # Berlin
    (-33.8568, 151.2153), # Sydney
    (41.8902, 12.4922),   # Rome
    (55.7539, 37.6208),   # Moscow
    (-23.5558, -46.6396), # Sao Paulo
]

BASE_DATE = datetime(2026, 9, 18)


def build_exif(capture_epoch: float, lat: float, lon: float, idx: int) -> dict:
    # exif.py parses DTO with calendar.timegm (naive wall clock), so render
    # the stamp in UTC to keep mtime == parsed capture_time.
    dt = datetime.utcfromtimestamp(capture_epoch)
    exif_dict = {
        "0th": {
            piexif.ImageIFD.Make: b"StressCam",
            piexif.ImageIFD.Model: f"Model-{idx % 7}".encode(),
        },
        "Exif": {
            piexif.ExifIFD.DateTimeOriginal: dt.strftime("%Y:%m:%d %H:%M:%S").encode(),
            piexif.ExifIFD.ISOSpeed: int(random.choice((100, 200, 400, 800))),
        },
        "GPS": {
            piexif.GPSIFD.GPSVersionID: (2, 3, 0, 0),
            piexif.GPSIFD.GPSLatitudeRef: (b"N" if lat >= 0 else b"S"),
            piexif.GPSIFD.GPSLongitudeRef: (b"E" if lon >= 0 else b"W"),
        },
        "1st": {},
    }
    for key, value in ((piexif.GPSIFD.GPSLatitude, abs(lat)),
                       (piexif.GPSIFD.GPSLongitude, abs(lon))):
        deg = int(value)
        minutes_full = (value - deg) * 60
        minutes = int(minutes_full)
        seconds = int(round((minutes_full - minutes) * 3600 * 100))
        exif_dict["GPS"][key] = ((deg, 1), (minutes, 1), (seconds, 100))
    return exif_dict


def make_photo(rng: np.random.Generator, w: int, h: int) -> np.ndarray:
    """Gradient background + mild luma noise + a few random shapes."""
    x = np.linspace(0.0, 1.0, w, dtype=np.float32)
    y = np.linspace(0.0, 1.0, h, dtype=np.float32)
    gx = (x * (1.0 - y[:, None])).astype(np.float32)  # cheap diagonal ramp
    ramp = gx[:, :, None]
    c1 = rng.integers(30, 220, 3, dtype=np.uint8)
    c2 = rng.integers(30, 220, 3, dtype=np.uint8)
    img = (c1[None, None, :] * (1.0 - ramp) + c2[None, None, :] * ramp)
    # Luma noise at quarter resolution, upscaled: keeps generation cheap.
    nh, nw = max(2, h // 4), max(2, w // 4)
    noise = rng.normal(0, 7, (nh, nw)).astype(np.float32)
    noise = cv2.resize(noise, (w, h), interpolation=cv2.INTER_LINEAR)
    img = np.clip(img + noise[:, :, None], 0, 255).astype(np.uint8)
    for _ in range(rng.integers(3, 8)):
        color = tuple(int(v) for v in rng.integers(0, 255, 3))
        kind = rng.integers(0, 3)
        thickness = max(1, min(w, h) // 120)
        if kind == 0:
            p1 = (int(rng.integers(0, w * 3 // 4)), int(rng.integers(0, h * 3 // 4)))
            p2 = (int(rng.integers(p1[0] + 8, w)), int(rng.integers(p1[1] + 8, h)))
            cv2.rectangle(img, p1, p2, color, thickness)
        elif kind == 1:
            center = (int(rng.integers(0, w)), int(rng.integers(0, h)))
            axes = (int(rng.integers(10, w // 3)), int(rng.integers(10, h // 3)))
            cv2.ellipse(img, center, axes, float(rng.integers(0, 180)), 0, 360,
                        color, thickness)
        else:
            center = (int(rng.integers(0, w)), int(rng.integers(0, h)))
            radius = int(rng.integers(8, min(w, h) // 4))
            cv2.circle(img, center, radius, color, thickness)
    cv2.putText(img, f"FaceFrame {rng.integers(0, 9999)}",
                (10, max(24, h - 20)), cv2.FONT_HERSHEY_SIMPLEX,
                max(0.6, min(w, h) / 800), (255, 255, 255), 2, cv2.LINE_AA)
    return img


def encode_jpeg(img: np.ndarray, quality: int, exif: dict | None) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("jpeg encode failed")
    data = buf.tobytes()
    if exif is not None:
        # piexif on this venv requires an output sink for in-memory inserts.
        import io

        sink = io.BytesIO()
        piexif.insert(piexif.dump(exif), data, sink)
        data = sink.getvalue()
    return data


def make_video_bytes(idx: int) -> bytes:
    """A 64x48 MJPG AVI, 30 frames, moving pattern so posters differ."""
    import tempfile

    tmp = Path(tempfile.gettempdir()) / f"ff_stress_clip_{idx}_{os.getpid()}.avi"
    writer = cv2.VideoWriter(
        str(tmp), cv2.VideoWriter_fourcc(*"MJPG"), 30, (64, 48)
    )
    if not writer.isOpened():
        raise RuntimeError("VideoWriter failed to open (MJPG/AVI)")
    base = np.zeros((48, 64, 3), np.uint8)
    try:
        for frame in range(30):
            base[:] = (frame * 8 % 255, 40 + idx * 5 % 200, 120 - frame * 3 % 100)
            x = int(frame * 2 % 64)
            cv2.circle(base, (x, 24), 6, (0, 255, 255), -1)
            cv2.rectangle(base, (63 - x, 10), (63 - x + 8, 30), (255, 0, 0), 1)
            writer.write(base)
    finally:
        writer.release()
    data = tmp.read_bytes()
    tmp.unlink(missing_ok=True)
    return data


def day_folder_name(rng_day: int, rng: random.Random) -> str:
    date = BASE_DATE - timedelta(days=int(rng_day))
    return f"{date:%Y-%m-%d}_{rng.choice(THEMES)}"


def long_name_180() -> str:
    alphabet = "abcdefghijkmnpqrstuvwxyz023456789"
    stem = "".join(alphabet[i % len(alphabet)] for i in range(180))
    return stem[:86] + "_" + stem[86:172] + "_" + stem[172:180]  # keep 180 chars


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(ROOT / "test-data" / "stress-library-3000"))
    ap.add_argument("--photos", type=int, default=3000,
                    help="total photo FILES including duplicates")
    ap.add_argument("--dup-pct", type=float, default=5.0)
    ap.add_argument("--videos", type=int, default=30)
    ap.add_argument("--folders", type=int, default=120)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--force", action="store_true", help="wipe target first")
    args = ap.parse_args()

    out = Path(args.out)
    if out.exists() and args.force:
        print(f"Wiping {out}")
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    py_rng = random.Random(args.seed)
    random.seed(args.seed)
    rng = np.random.default_rng(args.seed)

    dup_count = int(round(args.photos * args.dup_pct / 100.0))
    unique_count = args.photos - dup_count

    day_offsets = py_rng.sample(range(730), min(args.folders, 730))
    folders = [day_folder_name(d, py_rng) for d in day_offsets]
    for f in folders + ["special_names", "long_names", "photo_dump",
                        "clips_summer", "clips_winter"]:
        (out / f).mkdir(parents=True, exist_ok=True)

    files: dict[str, dict] = {}

    # --- adversarial names (counted inside the unique photo budget) --------
    deep_rel = "deep/" + "d/" * 40 + "deep_totem.jpg"
    specials = [
        "special_names/na\u00efve_\u7167\u7247_\U0001F389.jpg",
        "special_names/100%_done.jpg",
        "special_names/O'Brien's summer snapshot.jpg",
        "special_names/backup & restore, 2nd try.jpg",
        "long_names/" + long_name_180() + ".jpg",
        deep_rel,
        "photo_dump/beach photo reference.jpg",
    ]

    started = time.time()
    print(f"Generating {unique_count} unique photos "
          f"({dup_count} duplicate copies on top -> {args.photos} photo files) "
          f"+ {args.videos} videos in {out}")

    names = iter(specials)
    for i in range(unique_count):
        folder_idx = int(rng.integers(0, len(folders)))
        day = int(day_offsets[folder_idx])
        capture_epoch = calendar.timegm(
            (BASE_DATE - timedelta(days=day)).timetuple()
        ) + int(rng.integers(0, 86400))
        has_exif = bool(rng.random() < 0.60)
        lat, lon = GPS_SPOTS[int(rng.integers(0, len(GPS_SPOTS)))]
        lat += float(rng.normal(0, 0.01))
        lon += float(rng.normal(0, 0.01))
        if has_exif:
            mtime = float(capture_epoch)
            exif_epoch = float(capture_epoch)
        else:
            mtime = float(calendar.timegm((BASE_DATE - timedelta(days=day)).timetuple())
                          + int(rng.integers(0, 86400)))
            exif_epoch = 0.0

        dim = DIMENSIONS[_DIM_POP[int(rng.integers(0, len(_DIM_POP)))]]
        quality = int(rng.integers(60, 97))
        img = make_photo(rng, dim[0], dim[1])
        exif = build_exif(capture_epoch, lat, lon, i) if has_exif else None
        data = encode_jpeg(img, quality, exif)
        sha = hashlib.sha256(data).hexdigest()

        # Name: mostly camera-style, every ~50th gets a quirky name.
        if i < len(specials):
            rel = next(names)
        else:
            style = i % 3
            if style == 0:
                fname = f"IMG_{i:04d}.jpg"
            elif style == 1:
                fname = f"DSC_{i:05d}.jpg"
            else:
                fname = f"snapshot_{i}_frame.jpg"
            rel = f"{folders[folder_idx]}/{fname}"
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        os.utime(path, (mtime, mtime))
        files[rel] = {
            "hash": sha, "size": len(data), "mtime": mtime,
            "exif_time": exif_epoch if has_exif else None,
            "kind": "photo", "dup_of": None,
        }
        if i % 250 == 0:
            rate = (i + 1) / max(1e-9, time.time() - started)
            print(f"  photos {i + 1}/{unique_count}  ({rate:.0f} files/s)")

    # --- exact duplicates: same bytes, new name + different folder --------
    dup_sources = py_rng.sample(
        [r for r, meta in files.items()], min(dup_count, len(files))
    )
    folder_index = {f: i for i, f in enumerate(folders)}
    for j, src_rel in enumerate(dup_sources):
        src = files[src_rel]
        src_folder = src_rel.split("/", 1)[0]
        alt = folders[(folder_index.get(src_folder, 0) + 57) % len(folders)]
        stem = Path(src_rel).stem
        rel = f"{alt}/dupcopy_{j:04d}_{stem}.jpg"
        data = (out / src_rel).read_bytes()
        (out / rel).write_bytes(data)
        os.utime(out / rel, (src["mtime"], src["mtime"]))
        files[rel] = {**src, "dup_of": src_rel}

    # --- videos ------------------------------------------------------------
    per_clip_folder = ["clips_summer", "clips_winter"]
    for v in range(args.videos):
        folder = per_clip_folder[v % len(per_clip_folder)]
        rel = f"{folder}/vidclip_{v:03d}.avi"
        data = make_video_bytes(v)
        (out / rel).write_bytes(data)
        day = int(day_offsets[(v * 7) % len(day_offsets)])
        mtime = float(calendar.timegm((BASE_DATE - timedelta(days=day)).timetuple()))
        os.utime(out / rel, (mtime, mtime))
        files[rel] = {
            "hash": hashlib.sha256(data).hexdigest(), "size": len(data),
            "mtime": mtime, "exif_time": None, "kind": "video", "dup_of": None,
        }

    dup_hashes = sorted({m["hash"] for m in files.values() if m["dup_of"]})
    single_hashes = sorted({
        m["hash"] for m in files.values()
        if not m["dup_of"]
        and sum(1 for o in files.values() if o["hash"] == m["hash"]) == 1
    })
    manifest = {
        "library": str(out),
        "seed": args.seed,
        "photos_total": args.photos,
        "unique_photos": unique_count,
        "duplicates": dup_count,
        "videos": args.videos,
        "expected_feed_items": args.photos + args.videos,
        "dup_hashes": dup_hashes,
        "single_hashes_sample": single_hashes[:1000],
        "files": files,
    }
    manifest_path = out / "stress_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, indent=0))
    total_bytes = sum(m["size"] for m in files.values())
    print(f"Done in {time.time() - started:.0f}s: {len(files)} media files, "
          f"{total_bytes / 1e6:.0f} MB, {len(dup_hashes)} duplicate hash groups")
    print(f"Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
