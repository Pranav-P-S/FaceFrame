"""Memories: automatic recollections — "on this day", trips, highlights.

Builders are deterministic and cheap (SQL + light clustering). The UI owns
presentation (story player); the backend only decides what exists and what's
in it.
"""

import json
import time

# A trip is a place cluster with at least this many photos spread over at
# least this many days, inside a bounded time window.
TRIP_MIN_ITEMS = 4
TRIP_MIN_DAYS = 2
TRIP_WINDOW_DAYS = 30
HIGHLIGHTS_COUNT = 12


def build_memories(
    store,
    today_month_day: tuple | None = None,
    now_epoch: float | None = None,
    on_this_day: bool = True,
    trips: bool = True,
    highlights: bool = True,
) -> list:
    now_epoch = now_epoch if now_epoch is not None else time.time()
    memories: list = []
    if on_this_day:
        memories.extend(_on_this_day(store, today_month_day, now_epoch))
    if trips:
        memories.extend(_trips(store, now_epoch))
    if highlights:
        memories.extend(_highlights(store, now_epoch))
    return memories


def _on_this_day(store, today_month_day, now_epoch) -> list:
    if not today_month_day:
        return []
    month, day = today_month_day
    current_year = time.gmtime(now_epoch).tm_year
    with store.connect() as conn:
        rows = conn.execute(
            """SELECT f.path, f.content_hash,
                      COALESCE(m.date_override, m.capture_time) AS ts
               FROM files f JOIN media m ON m.content_hash=f.content_hash
               WHERE f.missing=0 AND f.trashed_at IS NULL
                 AND COALESCE(m.locked,0)=0
                 AND CAST(strftime('%m', ts, 'unixepoch') AS INTEGER)=?
                 AND CAST(strftime('%d', ts, 'unixepoch') AS INTEGER)=?
               ORDER BY ts DESC
               LIMIT 100""",
            (month, day),
        ).fetchall()

    by_year: dict[int, list] = {}
    for row in rows:
        item_year = time.gmtime(row["ts"]).tm_year
        if item_year < current_year:
            by_year.setdefault(item_year, []).append(row)

    memories = []
    for year, items in sorted(by_year.items(), reverse=True):
        if not items:
            continue
        memories.append(
            {
                "type": "on_this_day",
                "title": f"{current_year - year} years ago",
                "years_ago": current_year - year,
                "date": f"{current_year - year}-{month:02d}-{day:02d}",
                "items": [dict(r) for r in items],
                "cover_hash": items[0]["content_hash"],
            }
        )
    return memories


def _trips(store, now_epoch) -> list:
    """Photos clustered by geohash and time: same area, >= TRIP_MIN_DAYS
    span, >= TRIP_MIN_ITEMS, inside one TRIP_WINDOW window."""
    try:
        from places import geohash, get_geoname
    except ImportError:
        return []

    with store.connect() as conn:
        rows = conn.execute(
            """SELECT f.path, f.content_hash, m.exif,
                      COALESCE(m.date_override, m.capture_time) AS ts
               FROM files f JOIN media m ON m.content_hash=f.content_hash
               WHERE f.missing=0 AND f.trashed_at IS NULL
                 AND COALESCE(m.locked,0)=0
                 AND m.exif LIKE '%"gps"%' AND ts >= ?
               ORDER BY ts""",
            (now_epoch - 3 * 365 * 86400,),
        ).fetchall()

    by_cell: dict[str, list] = {}
    for row in rows:
        try:
            gps = json.loads(row["exif"]).get("gps")
        except (TypeError, ValueError):
            gps = None
        if not gps or len(gps) != 2:
            continue
        cell = geohash(gps[0], gps[1], 3)
        by_cell.setdefault(cell, []).append(row)

    memories = []
    for cell, items in by_cell.items():
        items.sort(key=lambda r: r["ts"])
        span_days = (
            (items[-1]["ts"] - items[0]["ts"]) / 86400 if len(items) > 1 else 0
        )
        if len(items) >= TRIP_MIN_ITEMS and TRIP_MIN_DAYS <= span_days <= TRIP_WINDOW_DAYS:
            name = get_geoname(store, cell) or cell
            memories.append(
                {
                    "type": "trip",
                    "title": f"Trip to {name}",
                    "place": name,
                    "start": items[0]["ts"],
                    "end": items[-1]["ts"],
                    "items": [dict(r) for r in items],
                    "cover_hash": items[0]["content_hash"],
                }
            )
    return memories


def _highlights(store, now_epoch) -> list:
    with store.connect() as conn:
        rows = conn.execute(
            """SELECT f.path, f.content_hash,
                      COALESCE(m.date_override, m.capture_time) AS ts,
                      m.favorite
               FROM files f JOIN media m ON m.content_hash=f.content_hash
               WHERE f.missing=0 AND f.trashed_at IS NULL
                 AND COALESCE(m.locked,0)=0
               ORDER BY m.favorite DESC, ts DESC
               LIMIT ?""",
            (HIGHLIGHTS_COUNT,),
        ).fetchall()
    if not rows:
        return []
    return [
        {
            "type": "highlights",
            "title": "Recent highlights",
            "items": [dict(r) for r in rows],
            "cover_hash": rows[0]["content_hash"],
        }
    ]
