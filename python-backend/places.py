"""Places: geohash-based photo clustering with optional online geocoding.

The offline-first contract: coordinates and geohash clusters work with zero
network; place names come from a local cache (geonames table) that is filled
by an optional, rate-limited reverse-geocoding call when the user has online
lookups enabled.
"""

import json
import logging
import time
import urllib.parse
import urllib.request

logger = logging.getLogger("FaceFrame.Places")

GEOHASH_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat={lat}&lon={lon}"
USER_AGENT = "FaceFrame/1.0 (local photo manager)"
GEOCODE_INTERVAL = 1.1  # seconds between requests, per the service's policy


def geohash(lat: float, lon: float, precision: int = 5) -> str:
    lat_lo, lat_hi = -90.0, 90.0
    lon_lo, lon_hi = -180.0, 180.0
    bits = [16, 8, 4, 2, 1]
    hash_value = []
    ch = 0
    bit = 0
    even = True
    while len(hash_value) < precision:
        if even:
            mid = (lon_lo + lon_hi) / 2
            if lon >= mid:
                ch |= bits[bit]
                lon_lo = mid
            else:
                lon_hi = mid
        else:
            mid = (lat_lo + lat_hi) / 2
            if lat >= mid:
                ch |= bits[bit]
                lat_lo = mid
            else:
                lat_hi = mid
        even = not even
        if bit < 4:
            bit += 1
        else:
            hash_value.append(GEOHASH_BASE32[ch])
            ch = 0
            bit = 0
    return "".join(hash_value)


def place_groups(store, precision: int = 4, geocode=None) -> list:
    """Cluster GPS-tagged items by geohash. ``geocode`` is an optional
    callable (geohash, lat, lon) -> name|None used to fill the cache."""
    with store.connect() as conn:
        rows = conn.execute(
            """SELECT f.path, f.content_hash, m.exif
               FROM files f JOIN media m ON m.content_hash=f.content_hash
               WHERE f.missing=0 AND f.trashed_at IS NULL
                 AND COALESCE(m.locked,0)=0
                 AND m.exif LIKE '%"gps"%'"""
        ).fetchall()

    clusters: dict[str, dict] = {}
    for row in rows:
        try:
            gps = json.loads(row["exif"]).get("gps")
        except (TypeError, ValueError):
            gps = None
        if not gps or len(gps) != 2:
            continue
        cell = geohash(gps[0], gps[1], precision)
        group = clusters.setdefault(
            cell,
            {
                "geohash": cell,
                "count": 0,
                "items": [],
                "cover_hash": row["content_hash"],
                "lat_sum": 0.0,
                "lon_sum": 0.0,
            },
        )
        group["count"] += 1
        group["items"].append(row["path"])
        group["lat_sum"] += gps[0]
        group["lon_sum"] += gps[1]

    results = []
    for cell, group in clusters.items():
        lat = group["lat_sum"] / group["count"]
        lon = group["lon_sum"] / group["count"]
        name = get_geoname(store, cell)
        if name is None and geocode is not None:
            name = geocode(cell, lat, lon)
            if name:
                set_geoname(store, cell, name)
        results.append(
            {
                "geohash": cell,
                "count": group["count"],
                "items": group["items"],
                "cover_hash": group["cover_hash"],
                "lat": lat,
                "lon": lon,
                "name": name,
            }
        )
    results.sort(key=lambda g: -g["count"])
    return results


def reverse_geocode(lat: float, lon: float) -> str | None:
    """One polite online lookup; returns a display name or None. Never
    raises — offline machines just keep coordinate labels."""
    try:
        url = NOMINATIM_URL.format(lat=lat, lon=lon)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))
        address = data.get("address", {})
        for key in ("city", "town", "village", "county", "state", "country"):
            if address.get(key):
                label = address[key]
                country = address.get("country")
                return f"{label}, {country}" if country and country != label else label
        return data.get("name")
    except Exception as e:
        logger.info("Reverse geocoding unavailable: %s", e)
        return None


def get_geoname(store, geohash_value: str):
    with store.connect() as conn:
        row = conn.execute(
            "SELECT name FROM geonames WHERE geohash=?", (geohash_value,)
        ).fetchone()
    return row[0] if row else None


def set_geoname(store, geohash_value: str, name: str):
    with store.connect() as conn:
        conn.execute(
            """INSERT INTO geonames (geohash, name, fetched_at) VALUES (?, ?, ?)
               ON CONFLICT(geohash) DO UPDATE SET name=excluded.name""",
            (geohash_value, name, time.time()),
        )
