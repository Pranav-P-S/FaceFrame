"""View layer: the feed (day/month/year groupings) and search execution.

Every query anchors on ``files`` — an item appears at most once per view.
Item visibility rules, in one place: not trashed, not missing, not locked
(unless the session unlocked), archived excluded from the main feed but
includable, motion-pair videos hidden behind their photo.
"""

import calendar
import datetime
import json
import time

import query as query_mod


# --------------------------------------------------------------------- feed

def feed_groups(
    store,
    view: str = "days",
    include_locked: bool = False,
    include_archived: bool = False,
    favorite: bool | None = None,
    archived_only: bool = False,
    limit: int = 4000,
) -> list:
    items = _base_items(
        store,
        include_locked=include_locked,
        include_archived=include_archived,
        favorite=favorite,
        archived_only=archived_only,
        limit=limit,
    )
    keyfn = {"days": _day_key, "months": _month_key, "years": _year_key}[view]
    groups: dict[str, list] = {}
    for item in items:
        groups.setdefault(keyfn(item["ts"]), []).append(item)
    return [
        {"key": key, "items": members}
        for key, members in sorted(groups.items(), reverse=True)
    ]


def _base_items(
    store,
    include_locked: bool = False,
    include_archived: bool = False,
    favorite: bool | None = None,
    archived_only: bool = False,
    limit: int = 4000,
    extra_where: str = "",
    extra_params: tuple = (),
) -> list:
    sql = """
        SELECT f.path, f.content_hash, f.kind, f.added_at,
               f.paired_path AS motion,
               COALESCE(m.date_override, m.capture_time, f.mtime) AS ts,
               m.kind AS media_kind, m.width, m.height, m.duration,
               m.favorite, m.archived, m.locked, m.caption, m.flags,
               m.labels
        FROM files f JOIN media m ON m.content_hash = f.content_hash
        WHERE f.missing=0 AND f.trashed_at IS NULL
    """
    params: list = []
    if not include_locked:
        sql += " AND COALESCE(m.locked,0)=0"
    if not include_archived:
        sql += " AND COALESCE(m.archived,0)=0"
    if favorite is not None:
        sql += " AND m.favorite=?"
        params.append(1 if favorite else 0)
    if archived_only:
        sql += " AND COALESCE(m.archived,0)=1"
    # Motion-pair videos hide behind their photo; photos keep showing
    # (with the paired video reachable for playback).
    sql += " AND NOT (m.kind='video' AND f.paired_path IS NOT NULL)"
    sql += extra_where
    sql += " ORDER BY ts DESC, f.path LIMIT ?"
    params.append(int(limit))
    with store.connect() as conn:
        rows = conn.execute(sql, (*params, *extra_params)).fetchall()
    items = [dict(r) for r in rows]
    for item in items:
        item["flags"] = _flags(item.get("flags"))
        item["labels"] = _labels(item.get("labels"))
    return items


def _labels(raw):
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return []


def _flags(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def _day_key(ts: float) -> str:
    return _utc(ts).strftime("%Y-%m-%d")


def _month_key(ts: float) -> str:
    return _utc(ts).strftime("%Y-%m")


def _year_key(ts: float) -> str:
    return _utc(ts).strftime("%Y")


_EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)


def _utc(ts: float):
    """Epoch -> aware UTC datetime via pure arithmetic: both time.gmtime and
    datetime.fromtimestamp refuse pre-1970 EXIF years (e.g. 1900) on
    Windows, and a 1900 photo must still land in a day group."""
    return _EPOCH + datetime.timedelta(seconds=ts)


# ------------------------------------------------------------------- search

def search_items(store, raw_query: str, include_locked: bool = False) -> dict:
    parsed = query_mod.parse(raw_query)
    where, params = [], []

    text_hashes = None
    if parsed.text:
        text_hashes = {
            hit["content_hash"] for hit in store.search_text(" ".join(parsed.text))
        }
        if not text_hashes:
            return {"items": [], "total": 0, "filters": parsed.filters}

    include_trashed = "trashed" in parsed.get("is")
    base_sql = """
        SELECT f.path, f.content_hash, f.kind,
               f.paired_path AS motion,
               COALESCE(m.date_override, m.capture_time, f.mtime) AS ts,
               m.kind AS media_kind, m.width, m.height, m.duration,
               m.favorite, m.archived, m.locked, m.caption, m.flags,
               m.labels
        FROM files f JOIN media m ON m.content_hash = f.content_hash
        WHERE f.missing=0
    """
    if include_trashed:
        base_sql += " AND f.trashed_at IS NOT NULL"
    else:
        base_sql += " AND f.trashed_at IS NULL"
    if not include_locked and "locked" not in parsed.get("is"):
        base_sql += " AND COALESCE(m.locked,0)=0"

    for value in parsed.get("is"):
        if value == "favorite":
            where.append("m.favorite=1")
        elif value == "archived":
            where.append("COALESCE(m.archived,0)=1")
        elif value == "locked":
            where.append("COALESCE(m.locked,0)=1")
        elif value in ("screenshot", "panorama"):
            where.append("m.flags LIKE ?")
            params.append(f'%"{value}"%')
        elif value == "video":
            where.append("m.kind='video'")
        elif value == "photo":
            where.append("m.kind='photo'")

    for value in parsed.get("type"):
        if value in ("photo", "video"):
            where.append("m.kind=?", )
            params.append(value)
        else:
            where.append("m.flags LIKE ?")
            params.append(f'%"{value}"%')

    for value in parsed.get("person"):
        where.append(
            """EXISTS (SELECT 1 FROM faces fa JOIN persons p ON p.id=fa.person_id
                       WHERE fa.content_hash=f.content_hash AND p.name=?)"""
        )
        params.append(value)

    for value in parsed.get("label"):
        # Substring match: label:retriever hits "golden retriever".
        where.append("m.labels LIKE ?")
        params.append(f"%{value}%")

    place_values = parsed.get("place")
    if place_values:
        # Resolve geoname strings to geohash cells, then match the geohash
        # the scan stored in media.flags.
        cells = store.geohashes_for_names(place_values)
        if cells:
            clause = " OR ".join("m.flags LIKE ?" for _ in cells)
            where.append(f"({clause})")
            params.extend([f'%"geohash":"{cell}"%' for cell in cells])
        else:
            where.append("0=1")

    for value in parsed.get("folder"):
        # Match the folder boundary: folder:sub must not hit submissions/.
        folder = value.strip("/").replace(chr(92), "/")
        where.append("(f.path LIKE ? OR f.path LIKE ?)")
        params.extend([f"{folder}/%", f"{folder} %"])

    for value in parsed.get("after"):
        epoch = _date_to_epoch(value, start=True)
        if epoch is not None:
            where.append("COALESCE(m.date_override, m.capture_time) >= ?")
            params.append(epoch)

    for value in parsed.get("before"):
        epoch = _date_to_epoch(value, start=False)
        if epoch is not None:
            where.append("COALESCE(m.date_override, m.capture_time) < ?")
            params.append(epoch)

    sql = base_sql
    for clause in where:
        sql += f" AND {clause}"
    sql += " AND NOT (m.kind='video' AND f.paired_path IS NOT NULL)"
    if text_hashes is not None:
        if include_trashed:
            # FTS only indexes live rows; for trash searches fall back to a
            # direct LIKE over caption/path so is:trashed + text still works.
            terms = parsed.text
            clause = " OR ".join(
                "(COALESCE(m.caption,'') LIKE ? OR f.path LIKE ?)" for _ in terms
            )
            sql += f" AND ({clause})"
            like = [f"%{t}%" for t in terms for _ in range(2)]
            params.extend(like)
        else:
            sql += f" AND f.content_hash IN ({','.join('?' * len(text_hashes))})"
            params.extend(text_hashes)
    sql += " ORDER BY ts DESC, f.path LIMIT 5000"

    with store.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    items = [dict(r) for r in rows]
    for item in items:
        item["flags"] = _flags(item.get("flags"))
        item["labels"] = _labels(item.get("labels"))
    return {"items": items, "total": len(items), "filters": parsed.filters}


def _date_to_epoch(value: str, start: bool):
    value = value.strip()
    for fmt, length in (("%Y-%m-%d", 10), ("%Y-%m", 7), ("%Y", 4)):
        if len(value) >= length:
            try:
                struct = time.strptime(value[:length], fmt)
                epoch = calendar.timegm(struct)
                return epoch
            except ValueError:
                continue
    return None


def item_detail(store, path: str):
    with store.connect() as conn:
        file_row = conn.execute(
            """SELECT f.path, f.content_hash, f.kind, f.size, f.mtime, f.added_at,
                      f.missing, f.trashed_at,
                      COALESCE(m.date_override, m.capture_time, f.mtime) AS ts
               FROM files f JOIN media m ON m.content_hash = f.content_hash
               WHERE f.path=?""",
            (path,),
        ).fetchone()
        if not file_row:
            return None
        media_row = conn.execute(
            "SELECT * FROM media WHERE content_hash=?",
            (file_row["content_hash"],),
        ).fetchone()
        faces = conn.execute(
            """SELECT fa.id, fa.bbox, fa.person_id, fa.thumbnail_path,
                      p.name AS person_name
               FROM faces fa LEFT JOIN persons p ON p.id=fa.person_id
               WHERE fa.content_hash=?""",
            (file_row["content_hash"],),
        ).fetchall()
        albums = conn.execute(
            """SELECT a.id, a.name FROM albums a
               JOIN album_items ai ON ai.album_id=a.id
               WHERE ai.content_hash=?""",
            (file_row["content_hash"],),
        ).fetchall()
        duplicate_paths = conn.execute(
            """SELECT path FROM files
               WHERE content_hash=? AND path != ? AND missing=0
                 AND trashed_at IS NULL""",
            (file_row["content_hash"], path),
        ).fetchall()
    detail = dict(file_row)
    detail["media"] = dict(media_row) if media_row else None
    detail["faces"] = [dict(f) for f in faces]
    detail["albums"] = [dict(a) for a in albums]
    detail["duplicate_paths"] = [d["path"] for d in duplicate_paths]
    return detail
