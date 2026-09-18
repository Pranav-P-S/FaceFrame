"""EXIF extraction: capture time, camera info, GPS.

Time convention (important for day grouping): ``capture_time`` is the *naive
wall-clock* epoch — calendar.timegm of the EXIF local time, timezone not
applied — with the raw offset string stored alongside. A photo taken
2021-06-15 10:30 in Berlin groups under June 15 everywhere on earth, which is
what a photo manager owes its user. Without EXIF the file mtime stands in.
"""

import calendar
import math
import time

DATETIME_ORIGINAL = 0x9003
OFFSET_ORIGINAL = 0x9011
EXIF_IFD = 0x8769
GPS_IFD = 0x8825


def extract(path: str) -> dict:
    """Best-effort metadata for one file. Never raises."""
    info = {
        "capture_time": None,
        "tz_offset": None,
        "exif": {},
    }
    try:
        from PIL import Image

        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return info
            camera = " ".join(
                part
                for part in (exif.get(0x010F), exif.get(0x0110))
                if part
            ).strip()
            ifd = exif.get_ifd(EXIF_IFD)
            gps_ifd = exif.get_ifd(GPS_IFD)

            info["capture_time"] = _parse_exif_time(ifd, exif)
            info["tz_offset"] = _clean_str(ifd.get(OFFSET_ORIGINAL))
            lens = _clean_str(ifd.get(0xA434))
            info["exif"] = {
                "camera": camera or None,
                "lens": lens or None,
                "iso": _int_or_none(ifd.get(0x8827)),
                "f_number": _rational_or_none(ifd.get(0x829D)),
                "exposure": _rational_or_none(ifd.get(0x829A)),
                "focal_length": _rational_or_none(ifd.get(0x920A)),
                "gps": _parse_gps(gps_ifd),
            }
            # Drop empty keys so the stored JSON stays readable.
            info["exif"] = {k: v for k, v in info["exif"].items() if v is not None}
    except Exception:
        return info
    return info


def _parse_exif_time(ifd, exif):
    raw = _clean_str(ifd.get(DATETIME_ORIGINAL)) or _clean_str(exif.get(0x0132))
    if not raw:
        return None
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            struct = time.strptime(raw[:19], fmt)
            return calendar.timegm(struct)
        except ValueError:
            continue
    return None


def _parse_gps(gps_ifd) -> list | None:
    try:
        lat = _dms(gps_ifd.get(0x0002))
        lon = _dms(gps_ifd.get(0x0004))
        if lat is None or lon is None:
            return None
        lat_ref = (_clean_str(gps_ifd.get(0x0001)) or "N").upper()
        lon_ref = (_clean_str(gps_ifd.get(0x0003)) or "E").upper()
        if lat_ref == "S":
            lat = -lat
        if lon_ref == "W":
            lon = -lon
        return [lat, lon]
    except Exception:
        return None


def _dms(value):
    if not value:
        return None
    try:
        parts = []
        for component in value[:3]:
            # Pillow may resolve rationals to floats or keep (num, den) pairs.
            if isinstance(component, tuple):
                num, den = component[0], component[1]
                parts.append(float(num) / float(den) if den else 0.0)
            else:
                parts.append(float(component))
        degrees, minutes, seconds = parts
        result = degrees + minutes / 60.0 + seconds / 3600.0
        # A zero denominator can surface as nan/inf; that is no coordinate.
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, ZeroDivisionError, IndexError):
        return None


def _clean_str(value):
    if value is None:
        return None
    text = str(value).strip().strip("\x00")
    return text or None


def _int_or_none(value):
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result


def _rational_or_none(value):
    try:
        if isinstance(value, tuple):
            num, den = value[0], value[1]
            result = float(num) / float(den) if den else None
        else:
            result = float(value)
    except (TypeError, ValueError, ZeroDivisionError, IndexError):
        return None
    # Guard nan/inf so no non-finite value reaches the stored exif JSON.
    return result if result is not None and math.isfinite(result) else None
