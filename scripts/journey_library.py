"""Build a realistic journey-test library for FaceFrame E2E runs.

Creates ~28 photos + 3 videos across 5 day-folders spanning two months,
with EXIF (camera, capture time, GPS), a panorama, screenshots, an exact
duplicate, two motion-still pairs (JPG + same-stem MJPG AVI) and two photos
dated exactly three years ago for the on-this-day memory.

Usage:
    ./venv/Scripts/python.exe scripts/journey_library.py [dest]
"""

import calendar
import io
import os
import shutil
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import piexif

ROOT = Path(__file__).resolve().parent.parent
DEST = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "test-data" / "journey-library"

# Today is assumed to be around build time; the "years ago" date is
# EXACTLY 3 years before today so the on-this-day memory fires.
TODAY = time.gmtime()
YEARS_AGO = (TODAY.tm_year - 3, TODAY.tm_mon, TODAY.tm_mday)

PARIS = (48.8566, 2.3522)
BERLIN = (52.5200, 13.4050)

CAMERA = ("FaceFrame Labs", "FF-One X100")


# ----------------------------------------------------------------- helpers

def gradient(w, h, top, bottom):
    """Vertical two-color gradient as a BGR image."""
    t = np.linspace(0.0, 1.0, h, dtype=np.float32).reshape(h, 1, 1)
    top = np.array(top, dtype=np.float32).reshape(1, 3)  # RGB
    bottom = np.array(bottom, dtype=np.float32).reshape(1, 3)
    rgb = (top * (1.0 - t) + bottom * t)  # h x 1 x 3
    img = np.repeat(rgb, w, axis=1)  # h x w x 3
    return np.ascontiguousarray(img.astype(np.uint8)[:, :, ::-1])  # to BGR


def add_sun(img, cx, cy, r, color=(90, 200, 255)):  # BGR warm
    cv2.circle(img, (cx, cy), r, color, -1)
    cv2.circle(img, (cx, cy), r + 12, color, 6)
    return img


def add_horizon(img, y, color):
    cv2.rectangle(img, (0, y), (img.shape[1], img.shape[0]), color, -1)
    return img


def add_face(img, cx, cy, scale=1.0):
    """Stylized frontal face — skin oval, eyes, brows, mouth, hair. Gives
    the detector *something* plausible on synthetic scenery."""
    s = lambda v: max(1, int(v * scale))
    skin = (140, 190, 235)  # BGR
    hair = (30, 30, 60)
    eye = (20, 20, 20)
    cv2.ellipse(img, (cx, cy), (s(46), s(60)), 0, 0, 360, skin, -1)
    cv2.ellipse(img, (cx, cy - s(52)), (s(50), s(30)), 0, 0, 180, hair, -1)
    cv2.rectangle(img, (cx - s(50), cy - s(52)), (cx + s(50), cy - s(34)), hair, -1)
    cv2.circle(img, (cx - s(18), cy - s(8)), s(7), eye, -1)
    cv2.circle(img, (cx + s(18), cy - s(8)), s(7), eye, -1)
    cv2.line(img, (cx - s(28), cy - s(24)), (cx - s(8), cy - s(24)), eye, s(4))
    cv2.line(img, (cx + s(8), cy - s(24)), (cx + s(28), cy - s(24)), eye, s(4))
    cv2.ellipse(img, (cx, cy + s(26)), (s(20), s(10)), 0, 0, 180, (60, 60, 200), -1)
    return img


def write_jpg(path, img, ts=None, gps=None, camera=CAMERA, iso=100,
              f_number=(28, 10), exposure=(1, 250), focal=(45, 10)):
    """Encode with OpenCV, stamp EXIF via piexif, write bytes."""
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        raise RuntimeError(f"encode failed for {path}")
    data = buf.tobytes()
    exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}}
    if camera:
        make, model = camera
        if make:
            exif_dict["0th"][piexif.ImageIFD.Make] = make
        if model:
            exif_dict["0th"][piexif.ImageIFD.Model] = model
    if ts:
        tstruct = time.gmtime(ts)
        stamp = time.strftime("%Y:%m:%d %H:%M:%S", tstruct).encode()
        exif_dict["0th"][piexif.ImageIFD.DateTime] = stamp
        exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = stamp
        exif_dict["Exif"][piexif.ExifIFD.ISOSpeedRatings] = iso
        exif_dict["Exif"][piexif.ExifIFD.FNumber] = f_number
        exif_dict["Exif"][piexif.ExifIFD.ExposureTime] = exposure
        exif_dict["Exif"][piexif.ExifIFD.FocalLength] = focal
    if gps:
        lat, lon = gps

        def dms(value):
            deg = int(value)
            minute_full = (value - deg) * 60
            minute = int(minute_full)
            sec = int(round((minute_full - minute) * 60 * 100))
            if sec >= 6000:
                sec -= 6000
                minute += 1
            return ((deg, 1), (minute, 1), (sec, 100))

        gps_ifd = {
            piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S",
            piexif.GPSIFD.GPSLatitude: dms(abs(lat)),
            piexif.GPSIFD.GPSLongitudeRef: b"E" if lon >= 0 else b"W",
            piexif.GPSIFD.GPSLongitude: dms(abs(lon)),
        }
        exif_dict["GPS"] = gps_ifd
    out = io.BytesIO()
    piexif.insert(piexif.dump(exif_dict), data, out)
    data = out.getvalue()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if ts:
        os.utime(path, (ts, ts))


def write_png(path, img, ts=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img)
    if ts:
        os.utime(path, (ts, ts))


def write_video(path, frames, fps, fourcc="MJPG"):
    path.parent.mkdir(parents=True, exist_ok=True)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*fourcc), fps,
                         (frames[0].shape[1], frames[0].shape[0]))
    for f in frames:
        vw.write(f)
    vw.release()


def epoch(y, mo, d, h=12, mi=0, s=0):
    return calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0))


# ------------------------------------------------------------------ build

def main():
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)

    jan, feb = 2026, 2026

    # -- Folder 1 · 2026-01-05 · screenshots + the two 3-years-ago photos --
    f1 = DEST / "2026-01-05 Everyday"
    shot_ts = epoch(jan, 1, 5, 19, 30, 0)
    for i, (w, h) in enumerate([(1080, 2400), (1200, 900)]):
        img = np.full((h, w, 3), 245, np.uint8)  # white-ish content
        cv2.rectangle(img, (0, 0), (w, 90), (60, 60, 60), -1)
        for k in range(6):
            y = 200 + k * 160
            cv2.rectangle(img, (40, y), (w - 40, y + 90), (210, 210, 210), -1)
        write_png(f1 / f"Screenshot_2026-01-05-19-{30 + i:02d}-00.png", img,
                  ts=shot_ts + i * 600)
    # Exactly three years ago today -> on-this-day memory.
    for name, hh, hue in [("vacation_2023_morning.jpg", 10, (60, 160, 240)),
                          ("vacation_2023_evening.jpg", 17, (150, 120, 40))]:
        img = gradient(1200, 800, hue, (230, 235, 245))
        add_sun(img, 950, 160, 60)
        add_face(img, 420, 380, 1.2)
        write_jpg(f1 / name, img, ts=epoch(*YEARS_AGO, hh, 15), gps=None)

    # -- Folder 2 · 2026-01-10 · beach day near Paris (+ motion pair 0) ----
    f2 = DEST / "2026-01-10 Beach day"
    beach_day = epoch(jan, 1, 10, 10, 0, 0)
    for i in range(6):
        img = gradient(1200, 800, (250, 190, 120), (60, 130, 200))  # sky->sea
        add_horizon(img, 430, (70, 160, 210))
        add_horizon(img, 600, (150, 200, 240))  # sand (BGR sand ~ blue-ish?)
        add_sun(img, 200 + i * 130, 120, 55)
        if i < 3:
            add_face(img, 600 + (i - 1) * 60, 520, 1.1)
        write_jpg(f2 / f"beach{i}.jpg", img, ts=beach_day + i * 1500,
                  gps=(PARIS[0] + i * 0.004, PARIS[1] + i * 0.003),
                  iso=100 + i * 20)
    # Motion pair 0: beach0.jpg + beach0.avi (20 MJPG frames).
    avi_frames = []
    for k in range(20):
        img = gradient(640, 480, (250, 190, 120), (60, 130, 200))
        add_horizon(img, 300, (70, 160, 210))
        add_sun(img, 120 + k * 20, 90, 40)
        write_to = img.copy()
        avi_frames.append(write_to)
    write_video(f2 / "beach0.avi", avi_frames, 20, "MJPG")
    os.utime(f2 / "beach0.avi", (beach_day, beach_day))

    # -- Folder 3 · 2026-01-24 · winter hike near Berlin (+ motion pair 1) -
    f3 = DEST / "2026-01-24 Winter hike"
    hike_day = epoch(jan, 1, 24, 9, 30, 0)
    for i in range(6):
        w, h = (800, 1200) if i % 2 else (1200, 800)
        img = gradient(w, h, (120, 180, 230), (30, 90, 40))  # sky->forest
        cv2.rectangle(img, (0, int(h * 0.62)), (w, h), (40, 80, 30), -1)
        for k in range(5):  # tree trunks
            x = 90 + k * (w // 5)
            cv2.rectangle(img, (x, int(h * 0.35)), (x + 24, int(h * 0.8)), (20, 40, 15), -1)
        if i < 3:
            add_face(img, w // 2, int(h * 0.55), 1.0)
        write_jpg(f3 / f"hiking{i}.jpg", img, ts=hike_day + i * 1800,
                  gps=(BERLIN[0] + i * 0.005, BERLIN[1] - i * 0.004))
    avi_frames = []
    for k in range(20):
        img = gradient(640, 480, (120, 180, 230), (30, 90, 40))
        cv2.rectangle(img, (0, 300), (640, 480), (40, 80, 30), -1)
        avi_frames.append(img)
    write_video(f3 / "hiking0.avi", avi_frames, 20, "MJPG")
    os.utime(f3 / "hiking0.avi", (hike_day, hike_day))

    # -- Folder 4 · 2026-02-07 · night city (+ the standalone video) -------
    f4 = DEST / "2026-02-07 City night"
    night_ts = epoch(feb, 2, 7, 21, 30, 0)
    rng = np.random.default_rng(7)
    for i in range(5):
        img = gradient(1200, 800, (15, 15, 30), (60, 40, 20))
        cv2.circle(img, (1000, 130), 45, (200, 220, 255), -1)  # moon
        for _ in range(140):  # window lights
            x, y = int(rng.integers(0, 1200)), int(rng.integers(400, 780))
            cv2.rectangle(img, (x, y), (x + 10, y + 14), (60, 200, 240), -1)
        add_sun_marker = None  # keep linter quiet
        del add_sun_marker
        if i < 2:
            add_face(img, 350, 560, 0.9)
        if i == 4:  # one without any EXIF at all
            write_jpg(f4 / f"night{i}.jpg", img, ts=None, camera=None)
            os.utime(f4 / f"night{i}.jpg", (night_ts + i * 900,) * 2)
        else:
            write_jpg(f4 / f"night{i}.jpg", img, ts=night_ts + i * 900,
                      iso=1600, exposure=(1, 15), f_number=(18, 10),
                      camera=CAMERA)
    clip = []
    for k in range(48):
        img = gradient(640, 360, (15, 15, 30), (60, 40, 20))
        cv2.circle(img, (520, 70), 30, (200, 220, 255), -1)
        for _ in range(30):
            x, y = int(rng.integers(0, 640)), int(rng.integers(180, 350))
            cv2.rectangle(img, (x, y), (x + 6, y + 8), (60, 200, 240), -1)
        clip.append(img)
    write_video(f4 / "city_night_clip.mp4", clip, 24, "mp4v")
    os.utime(f4 / "city_night_clip.mp4", (night_ts, night_ts))

    # -- Folder 5 · 2026-02-20 · pano, exact duplicate, mixed shots ---------
    f5 = DEST / "2026-02-20 Around town"
    pano_ts = epoch(feb, 2, 20, 12, 0, 0)
    pano = gradient(3000, 1000, (170, 210, 250), (80, 140, 90))
    add_horizon(pano, 620, (90, 160, 110))
    cv2.circle(pano, (2400, 220), 90, (90, 200, 255), -1)
    write_jpg(f5 / "pano_bay.jpg", pano, ts=None, camera=None)  # no EXIF
    os.utime(f5 / "pano_bay.jpg", (pano_ts, pano_ts))
    # Exact byte duplicate of a beach photo in ANOTHER folder.
    shutil.copyfile(f2 / "beach3.jpg", f5 / "beach3_copy.jpg")
    os.utime(f5 / "beach3_copy.jpg", (pano_ts, pano_ts))
    mix_hues = [((40, 80, 160), (220, 230, 240)), ((30, 40, 60), (200, 180, 160)),
                ((90, 60, 20), (240, 200, 150)), ((0, 0, 0), (120, 120, 130)),
                ((160, 120, 40), (240, 240, 220))]
    for i, (top, bot) in enumerate(mix_hues):
        img = gradient(1200, 800 if i % 2 == 0 else 900, top, bot)
        add_sun(img, 300 + i * 120, 150, 50)
        write_jpg(f5 / f"mix{i}.jpg", img, ts=pano_ts + 3600 + i * 1200,
                  gps=(48.86 + i * 0.002, 2.35 + i * 0.0015))

    total = sum(1 for p in DEST.rglob("*") if p.is_file())
    photos = sum(1 for p in DEST.rglob("*")
                 if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    videos = total - photos
    print(f"Journey library ready: {DEST}")
    print(f"  files={total} photos={photos} videos={videos} folders={len(list(DEST.iterdir()))}")
    print(f"  years-ago date: {YEARS_AGO[0]}-{YEARS_AGO[1]:02d}-{YEARS_AGO[2]:02d}")


if __name__ == "__main__":
    main()
