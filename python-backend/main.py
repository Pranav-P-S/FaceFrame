"""FaceFrame backend: command loop over NDJSON stdin/stdout.

Handlers register through @action and receive the raw request dict; anything
they return is the response ``data``. Raising replies with an error. The
dispatcher knows nothing about photos — every feature lives in its module
(store, scan, library, people, views, places, memories, render, creations,
labels, watcher) and plugs in here.
"""

import base64
import json
import logging
import shutil
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pathio
from creations import delete_creation, make_animation, make_collage
from library import LibraryService
from people import PeopleService
from render import magic_eraser_preview, render_preview
from scan import DATA_DIR_NAME, VALID_EXTENSIONS, ScanPipeline
from store import Store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("FaceFrame.Backend")

_write_lock = threading.Lock()
_lifecycle_lock = threading.Lock()
_scan_thread = None
_abort_scan = threading.Event()

ACTIONS = {}


def action(name):
    def register(fn):
        ACTIONS[name] = fn
        return fn

    return register


def emit(payload: dict):
    with _write_lock:
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()


def reply(req_id, ok, data=None, error=None):
    message = {"id": req_id, "ok": ok}
    if ok:
        message["data"] = data or {}
    else:
        message["error"] = error or "Unknown error"
    emit(message)


# ---------------------------------------------------------------------------
# Library contexts (Store init runs migrations; cache so it happens once)
# ---------------------------------------------------------------------------

_contexts: dict[str, dict] = {}
_contexts_lock = threading.Lock()


def context(path: str) -> dict:
    root = str(Path(path).resolve())
    with _contexts_lock:
        ctx = _contexts.get(root)
        data_dir = Path(root) / DATA_DIR_NAME
        # The .faceframe folder can vanish underneath us (clear_index in
        # another process, the user deleting it): rebuild instead of
        # failing every later command with "unable to open database file".
        if ctx is not None and not data_dir.is_dir():
            ctx = None
        if ctx is None:
            data_dir.mkdir(parents=True, exist_ok=True)
            store = Store(str(data_dir / "index.db"), library_root=root)
            ctx = {
                "root": root,
                "store": store,
                "library": LibraryService(store, root),
                "people": PeopleService(store),
            }
            _contexts[root] = ctx
    return ctx


def locate_library(path: str):
    library = Path(path)
    if not library.is_dir():
        raise NotADirectoryError(f"Folder not found: {path}")
    return context(path)


def _abs(ctx, rel):
    return str(pathio.to_absolute(rel, ctx["root"])) if rel else None


# ---------------------------------------------------------------------------
# Scan job (manual or watcher-driven), with coalesced progress
# ---------------------------------------------------------------------------

class ProgressCoalescer:
    """scan_progress at most every ``interval`` seconds; everything else
    passes through instantly. Also enriches scan_complete with the legacy
    protocol's ``processed`` alias and library path."""

    def __init__(self, path: str = "", interval: float = 0.3):
        self.path = path
        self.interval = interval
        self._last = 0.0

    def __call__(self, event: str, **payload):
        if event == "scan_complete" and "processed" not in payload:
            payload["path"] = self.path
            payload["processed"] = payload.get("files_total", 0)
            emit({"event": event, **payload})
            return
        if event != "scan_progress":
            payload.setdefault("path", self.path)
            emit({"event": event, **payload})
            return
        now = time.monotonic()
        total = payload.get("total") or 0
        processed = payload.get("processed") or 0
        final = total and processed >= total
        if final or now - self._last >= self.interval:
            self._last = now
            emit({"event": event, **payload})


def get_processor(provider):
    from processor import FaceProcessor

    import onnxruntime

    choice = (provider or "auto").lower()
    if choice == "auto":
        use_gpu = "CUDAExecutionProvider" in onnxruntime.get_available_providers()
    else:
        use_gpu = "cuda" in choice
    return FaceProcessor(use_gpu=use_gpu, thumbnail_dir=None)


_shared_processors: dict = {}
_shared_labeler = None
_labeler_tried = False


def get_shared_processor(provider):
    """One FaceProcessor, built at startup on the main thread and reused by
    every scan regardless of the requested provider. Model construction in a
    secondary thread deadlocks nondeterministically on Windows (the import
    chain warms in the main thread, but ONNX session creation can still hang
    there). A request for CUDA on a CPU-built instance falls back to the
    shared CPU engine - the same quiet fallback the app already uses when
    CUDA libraries are missing."""
    key = (provider or "auto").lower()
    if "auto" not in _shared_processors:
        _shared_processors["auto"] = get_processor("auto")
    shared = _shared_processors["auto"]
    if "cuda" in key and "CUDAExecutionProvider" not in getattr(shared, "providers", []):
        if key not in _shared_processors:
            logger.warning("Building a CUDA processor on demand (not at startup)")
            _shared_processors[key] = get_processor(provider)
        return _shared_processors[key]
    return shared


def get_shared_labeler():
    global _shared_labeler, _labeler_tried
    if not _labeler_tried:
        _labeler_tried = True
        try:
            from labels import ImageLabeler

            _shared_labeler = ImageLabeler()
        except Exception as e:
            logger.warning("Labeling unavailable: %s", e)
    return _shared_labeler


def run_scan(path, provider):
    global _scan_thread
    try:
        ctx = locate_library(path)
        root = ctx["root"]

        if _abort_scan.is_set():
            emit({"event": "scan_cancelled", "path": root})
            return

        processor = get_shared_processor(provider)
        processor.thumbnail_dir = str(Path(root) / DATA_DIR_NAME / "thumbnails")
        emit({"event": "model_status", "state": "ready"})

        if _abort_scan.is_set():
            emit({"event": "scan_cancelled", "path": root})
            return

        labeler = _make_labeler(ctx)
        pipeline = ScanPipeline(
            str(Path(root) / DATA_DIR_NAME / "index.db"),
            root,
            face_engine=processor,
            labeler=labeler,
            emit=ProgressCoalescer(path=root),
        )
        pipeline.abort_check = _abort_scan.is_set

        stats = pipeline.run()
        ctx["library"].purge_expired_trash()

        if stats.get("cancelled"):
            emit({"event": "scan_cancelled", "path": root})
    except Exception as e:
        logger.exception("Scan failed")
        emit({"event": "scan_error", "message": str(e)})
    finally:
        with _lifecycle_lock:
            _scan_thread = None


def _make_labeler(ctx):
    """The shared labeler when its model is cached; otherwise a background
    download primes the cache and a later pass labels everything."""
    if ctx["store"].get_meta("setting_labels", "1") != "1":
        return None
    labeler = get_shared_labeler()
    if labeler is not None and labeler._session is not None:
        return labeler
    _prime_label_model()
    return None


_label_primed = False
_prime_lock = threading.Lock()


def _prime_label_model():
    """One background download attempt per process."""
    global _label_primed
    with _prime_lock:
        if _label_primed:
            return
        _label_primed = True

    def download():
        try:
            from labels import ImageLabeler

            ImageLabeler(auto_download=True)
            logger.info("Label model cached; labels will apply on the next scan")
        except Exception as e:
            logger.warning("Label model download failed: %s", e)

    threading.Thread(target=download, daemon=True, name="label-prime").start()


def busy_worker():
    # A watcher pass scans the same library the manual scan would: the two
    # must not interleave, or each pass's mark_missing flags the other's
    # newly added files as missing.
    if _scan_thread is not None and _scan_thread.is_alive():
        return True
    return _watcher.is_busy()


# ---------------------------------------------------------------------------
# Handlers — library & setup
# ---------------------------------------------------------------------------

@action("ping")
def _ping(req):
    return {"version": 3}


@action("get_providers")
def _providers(req):
    from processor import FaceProcessor

    return {"compute": FaceProcessor.compute_info()}


@action("open_library")
def _open_library(req):
    import labels as labels_mod

    ctx = locate_library(req["path"])
    store = ctx["store"]
    with store.connect() as conn:
        item_count = conn.execute(
            """SELECT COUNT(*) FROM files f
               JOIN media m ON m.content_hash=f.content_hash
               WHERE f.missing=0 AND f.trashed_at IS NULL"""
        ).fetchone()[0]
        missing = conn.execute(
            "SELECT COUNT(*) FROM files WHERE missing=1"
        ).fetchone()[0]
    return {
        "path": ctx["root"],
        "items": item_count,
        "missing": missing,
        "lock_set": ctx["library"].lock_passcode_set(),
        "labels_enabled": store.get_meta("setting_labels", "1") == "1",
        "labels_ready": labels_mod.model_ready(),
        "watch_enabled": store.get_meta("setting_watch", "0") == "1",
    }


@action("scan")
def _scan(req):
    global _scan_thread
    path = req.get("path")
    if not path:
        raise ValueError("Missing path")
    with _lifecycle_lock:
        if busy_worker():
            raise RuntimeError("Another operation is still running")
        _abort_scan.clear()
        _scan_thread = threading.Thread(
            target=run_scan, args=(path, req.get("provider")), daemon=True
        )
        _scan_thread.start()
    return {"started": True}


@action("cancel_scan")
def _cancel_scan(req):
    _abort_scan.set()
    return {}


@action("cluster")
def _cluster(req):
    """Legacy protocol: the reply itself carries the stats (the caller
    blocks on it) and a cluster_done event follows for broadcast listeners."""
    path = req.get("path")
    if not path:
        raise ValueError("Missing path")
    req_id = req.get("id")

    def run_cluster():
        try:
            ctx = locate_library(path)
            stats = ctx["people"].cluster()
            reply(req_id, True, stats)
            emit({"event": "cluster_done", **stats})
        except Exception as e:
            logger.exception("Clustering failed")
            reply(req_id, False, error=str(e))
        finally:
            with _lifecycle_lock:
                _cluster_thread = None

    global _cluster_thread
    with _lifecycle_lock:
        if busy_worker() or (_cluster_thread and _cluster_thread.is_alive()):
            raise RuntimeError("Another operation is still running")
        _cluster_thread = threading.Thread(target=run_cluster, daemon=True)
        _cluster_thread.start()
    return _RESPONDED


_cluster_thread = None


# ---------------------------------------------------------------------------
# Handlers — views & search
# ---------------------------------------------------------------------------

@action("get_feed")
def _get_feed(req):
    import views

    ctx = locate_library(req["path"])
    groups = views.feed_groups(
        ctx["store"],
        view=req.get("view", "days"),
        include_locked=bool(req.get("include_locked")),
        include_archived=bool(req.get("include_archived")),
        favorite=req.get("favorite"),
        limit=int(req.get("limit", 4000)),
    )
    return {"groups": groups}


@action("search")
def _search(req):
    import views

    ctx = locate_library(req["path"])
    return views.search_items(
        ctx["store"], req.get("query", ""),
        include_locked=bool(req.get("include_locked")),
    )


@action("get_item")
def _get_item(req):
    import views

    # req["path"] is the ITEM's absolute path (what every view hands to the
    # viewer); its library is derived from the indexed context, and the
    # detail lookup uses the library-relative key.
    file_path = req.get("path", "")
    ctx = _context_for_file(file_path)
    if ctx is None:
        raise ValueError("File is not part of an indexed library")
    detail = views.item_detail(
        ctx["store"], pathio.to_relative(file_path, ctx["root"])
    )
    if detail is None:
        raise ValueError("Item not found")
    return {"item": detail}


# ---------------------------------------------------------------------------
# Handlers — user state
# ---------------------------------------------------------------------------

@action("set_favorite")
def _set_favorite(req):
    ctx = locate_library(req["path"])
    ctx["library"].set_favorite(req.get("hashes") or [], bool(req.get("favorite")))
    return {}


@action("set_archived")
def _set_archived(req):
    ctx = locate_library(req["path"])
    ctx["library"].set_archived(req.get("hashes") or [], bool(req.get("archived")))
    return {}


@action("set_locked")
def _set_locked(req):
    ctx = locate_library(req["path"])
    ctx["library"].set_locked(req.get("hashes") or [], bool(req.get("locked")))
    return {}


@action("get_locked_items")
def _get_locked_items(req):
    ctx = locate_library(req["path"])
    items = ctx["library"].locked_items()
    return {"items": [_as_item(ctx, r) for r in items]}


@action("set_trashed")
def _set_trashed(req):
    ctx = locate_library(req["path"])
    ctx["library"].set_trashed(
        content_hashes=req.get("hashes"),
        trashed=bool(req.get("trashed", True)),
        paths=req.get("paths"),
    )
    return {}


@action("get_trashed")
def _get_trashed(req):
    ctx = locate_library(req["path"])
    return {"items": [_as_item(ctx, r) for r in ctx["library"].trashed_items()]}


@action("empty_trash")
def _empty_trash(req):
    """Purge everything in trash (the 60-day policy does it gradually)."""
    ctx = locate_library(req["path"])
    removed = 0
    for row in ctx["library"].trashed_items():
        if ctx["library"]._send_path_to_os_trash(row["path"]):
            ctx["store"].remove_file(row["path"])
            removed += 1
    return {"removed": removed}


@action("delete_from_disk")
def _delete_from_disk(req):
    ctx = locate_library(req["path"])
    return {"removed": ctx["library"].delete_from_disk(req.get("paths") or [])}


@action("set_caption")
def _set_caption(req):
    ctx = locate_library(req["path"])
    ctx["library"].set_caption(req["hash"], req.get("caption") or "")
    return {}


@action("set_date_override")
def _set_date_override(req):
    ctx = locate_library(req["path"])
    epoch = req.get("epoch")
    ctx["library"].set_date_override(
        req["hash"], float(epoch) if epoch is not None else None
    )
    return {}


@action("set_edit")
def _set_edit(req):
    ctx = locate_library(req["path"])
    edit = req.get("edit")
    # The UI sends the edit document as a JSON-able object; the column is TEXT.
    if edit is not None and not isinstance(edit, str):
        edit = json.dumps(edit)
    ctx["store"].set_media_user_state(req["hash"], edit=edit)
    return {}


# ---------------------------------------------------------------------------
# Handlers — locked folder passcode
# ---------------------------------------------------------------------------

@action("set_locked_passcode")
def _set_lock_code(req):
    ctx = locate_library(req["path"])
    ctx["library"].set_locked_passcode(req.get("code") or "")
    return {}


@action("verify_locked_passcode")
def _verify_lock_code(req):
    ctx = locate_library(req["path"])
    return {"ok": ctx["library"].verify_locked_passcode(req.get("code") or "")}


@action("remove_locked_passcode")
def _remove_lock_code(req):
    ctx = locate_library(req["path"])
    ctx["library"].remove_locked_passcode(req.get("code") or "")
    return {}


# ---------------------------------------------------------------------------
# Handlers — albums
# ---------------------------------------------------------------------------

@action("get_albums")
def _get_albums(req):
    ctx = locate_library(req["path"])
    albums = []
    for a in ctx["library"].list_albums():
        a["cover"] = _abs(ctx, _cover_rel(ctx, a.get("cover_hash")))
        albums.append(a)
    return {"albums": albums}


def _cover_rel(ctx, content_hash):
    if not content_hash:
        return None
    with ctx["store"].connect() as conn:
        row = conn.execute(
            "SELECT path FROM files WHERE content_hash=? AND missing=0",
            (content_hash,),
        ).fetchone()
    return row[0] if row else None


@action("create_album")
def _create_album(req):
    ctx = locate_library(req["path"])
    album_id = ctx["library"].create_album(
        req.get("name") or "New album", req.get("description")
    )
    return {"album_id": album_id}


@action("rename_album")
def _rename_album(req):
    ctx = locate_library(req["path"])
    ctx["library"].rename_album(req["album_id"], req["name"])
    return {}


@action("delete_album")
def _delete_album(req):
    ctx = locate_library(req["path"])
    ctx["library"].delete_album(req["album_id"])
    return {}


@action("set_album_cover")
def _set_album_cover(req):
    ctx = locate_library(req["path"])
    ctx["library"].set_album_cover(req["album_id"], req["hash"])
    return {}


@action("set_album_sort")
def _set_album_sort(req):
    ctx = locate_library(req["path"])
    ctx["library"].set_album_sort(req["album_id"], req.get("sort", "added"))
    return {}


@action("album_add")
def _album_add(req):
    ctx = locate_library(req["path"])
    ctx["library"].add_album_items(req["album_id"], req.get("hashes") or [])
    return {}


@action("album_remove")
def _album_remove(req):
    ctx = locate_library(req["path"])
    ctx["library"].remove_album_items(req["album_id"], req.get("hashes") or [])
    return {}


@action("album_reorder")
def _album_reorder(req):
    ctx = locate_library(req["path"])
    ctx["library"].reorder_album(req["album_id"], req.get("hashes") or [])
    return {}


@action("get_album")
def _get_album(req):
    import views

    ctx = locate_library(req["path"])
    meta = next(
        (a for a in ctx["library"].list_albums() if a["id"] == req["album_id"]),
        None,
    )
    if not meta:
        raise ValueError("Album not found")
    rows = ctx["library"].album_items(req["album_id"], include_trashed=False)
    items = []
    for row in rows:
        state = ctx["store"].get_file_state(
            _any_path(ctx, row["content_hash"]) or ""
        )
        if state:
            items.append(_as_item(ctx, {"path": state["path"], "content_hash": row["content_hash"]}))
    meta["cover"] = _abs(ctx, _cover_rel(ctx, meta.get("cover_hash")))
    meta["items"] = items
    return {"album": meta}


def _any_path(ctx, content_hash):
    with ctx["store"].connect() as conn:
        row = conn.execute(
            """SELECT path FROM files
               WHERE content_hash=? AND missing=0 AND trashed_at IS NULL
               ORDER BY path LIMIT 1""",
            (content_hash,),
        ).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Handlers — people
# ---------------------------------------------------------------------------

@action("get_persons")
def _get_persons(req):
    ctx = locate_library(req["path"])
    persons = []
    for row in ctx["people"].list_persons():
        persons.append(
            {
                "id": row["id"],
                "name": row["name"] or f"Person {row['id']}",
                "thumbnail": _abs(ctx, row["thumbnail_path"]),
                "face_count": row["face_count"],
            }
        )
    return {"persons": persons}


@action("get_unclustered")
def _get_unclustered(req):
    ctx = locate_library(req["path"])
    faces = []
    for row in ctx["people"].unclustered_faces():
        faces.append(
            {
                "id": row["id"],
                "file_path": _abs(ctx, _any_path(ctx, row["content_hash"])),
                "bbox": json.loads(row["bbox"]) if row["bbox"] else [0, 0, 0, 0],
                "thumbnail": _abs(ctx, row["thumbnail_path"]),
            }
        )
    return {"faces": faces}


@action("get_photos_by_person")
def _get_photos_by_person(req):
    ctx = locate_library(req["path"])
    photos = [
        {"path": _abs(ctx, row["path"]), "face_count": row["face_count"]}
        for row in ctx["people"].person_photos(req["person_id"])
    ]
    return {"photos": photos}


@action("rename_person")
def _rename_person(req):
    ctx = locate_library(req["path"])
    ctx["people"].rename_person(req["person_id"], (req.get("new_name") or "").strip())
    return {}


@action("merge_persons")
def _merge_persons(req):
    ctx = locate_library(req["path"])
    stale = ctx["people"].merge_persons(req["keep_id"], req["merge_id"])
    if stale:
        _unlink_quietly(_abs(ctx, stale))
    return {}


@action("split_person")
def _split_person(req):
    ctx = locate_library(req["path"])
    new_id = ctx["people"].split_person(
        req["person_id"], req.get("face_ids") or [], req.get("new_name")
    )
    return {"person_id": new_id}


@action("assign_faces")
def _assign_faces(req):
    ctx = locate_library(req["path"])
    ctx["people"].assign_faces(req.get("face_ids") or [], req.get("person_id"))
    return {}


@action("set_person_hidden")
def _set_person_hidden(req):
    ctx = locate_library(req["path"])
    ctx["people"].set_person_hidden(req["person_id"], bool(req.get("hidden")))
    return {}


@action("set_person_thumbnail")
def _set_person_thumbnail(req):
    ctx = locate_library(req["path"])
    ctx["people"].set_person_thumbnail(req["person_id"], req["thumbnail"])
    return {}


# ---------------------------------------------------------------------------
# Handlers — media & render
# ---------------------------------------------------------------------------

@action("get_image_preview")
def _get_image_preview(req):
    file_path = req.get("file_path")
    max_dim = min(int(req.get("max_dim", 640)), 4096)
    if not file_path:
        raise ValueError("Missing file_path")
    ctx = _context_for_file(file_path)
    if ctx is None:
        raise ValueError("File is not part of an indexed library")
    rel = pathio.to_relative(file_path, ctx["root"])

    edit = req.get("edit")
    if edit is None:
        state = ctx["store"].get_file_state(rel)
        if state and state["content_hash"]:
            media = ctx["store"].get_media(state["content_hash"])
            if media and media["edit"]:
                try:
                    edit = json.loads(media["edit"])
                except ValueError:
                    edit = None
    out = render_preview(
        ctx["store"], ctx["root"], rel,
        max_dim=max_dim,
        square=bool(req.get("square")),
        edit=edit if req.get("apply_edit", True) else None,
    )
    if not out:
        raise ValueError("Could not read image")
    data = base64.b64encode(Path(out).read_bytes()).decode("ascii")
    return {"data_url": f"data:image/jpeg;base64,{data}"}


def _context_for_file(file_path: str):
    directory = Path(file_path).parent
    for parent in [directory, *directory.parents]:
        ctx = _contexts.get(str(parent))
        if ctx is not None:
            return ctx
    # Library not opened yet in this process (cold start): scan upwards for
    # a .faceframe folder and open it.
    for parent in [directory, *directory.parents]:
        if (parent / DATA_DIR_NAME).is_dir():
            return context(str(parent))
    return None


@action("magic_eraser")
def _magic_eraser(req):
    file_path = req.get("file_path")
    if not file_path:
        raise ValueError("Missing file_path")
    ctx = _context_for_file(file_path)
    if ctx is None:
        raise ValueError("File is not part of an indexed library")
    out = magic_eraser_preview(
        ctx["store"], ctx["root"],
        pathio.to_relative(file_path, ctx["root"]),
        req.get("rects") or [],
        max_dim=min(int(req.get("max_dim", 1600)), 2600),
    )
    if not out:
        raise ValueError("Could not read image")
    data = base64.b64encode(Path(out).read_bytes()).decode("ascii")
    return {"data_url": f"data:image/jpeg;base64,{data}"}


# ---------------------------------------------------------------------------
# Handlers — places, memories, utilities, creations, settings
# ---------------------------------------------------------------------------

@action("get_places")
def _get_places(req):
    import places as places_mod

    ctx = locate_library(req["path"])
    groups = places_mod.place_groups(ctx["store"], precision=req.get("precision", 4))
    for group in groups:
        group["cover"] = _abs(ctx, _cover_rel(ctx, group["cover_hash"]))
        group["items"] = [_abs(ctx, p) for p in group["items"]]
    # Geocoding must never stall the command loop: cached names come back
    # immediately; uncached cells are filled politely in the background
    # (rate-limited, capped per pass) and announced with an event.
    if req.get("geocode") and ctx["store"].get_meta("setting_geocode", "1") == "1":
        threading.Thread(
            target=_geocode_worker, args=(ctx, groups), daemon=True, name="geocode"
        ).start()
    return {"places": groups}


def _geocode_worker(ctx, groups, cap: int = 5):
    import time as _time

    import places as places_mod

    store = ctx["store"]
    filled = False
    for group in groups:
        if cap <= 0:
            break
        if group.get("name"):
            continue
        try:
            name = places_mod.reverse_geocode(group["lat"], group["lon"])
        except Exception as e:
            logger.warning("Geocode failed: %s", e)
            break
        if name:
            places_mod.set_geoname(store, group["geohash"], name)
            filled = True
        cap -= 1
        _time.sleep(places_mod.GEOCODE_INTERVAL)
    if filled:
        emit({"event": "places_updated", "path": ctx["root"]})


@action("get_memories")
def _get_memories(req):
    import memories as memories_mod

    ctx = locate_library(req["path"])
    raw = memories_mod.build_memories(ctx["store"])
    for memory in raw:
        memory["items"] = [_as_item(ctx, {"path": p, "content_hash": h}) for p, h in
                           ((i["path"], i["content_hash"]) for i in memory["items"])]
        memory["cover"] = _abs(ctx, _cover_rel(ctx, memory.get("cover_hash")))
    return {"memories": raw}


@action("get_duplicates")
def _get_duplicates(req):
    ctx = locate_library(req["path"])
    groups = []
    for group in ctx["store"].duplicate_groups():
        groups.append(
            {
                "hash": group["hash"],
                "paths": [_abs(ctx, p) for p in group["paths"]],
                "count": group["count"],
            }
        )
    return {"groups": groups}


@action("get_missing")
def _get_missing(req):
    ctx = locate_library(req["path"])
    items = []
    for row in ctx["store"].all_files():
        if row["missing"]:
            items.append({"path": _abs(ctx, row["path"]), "kind": row["kind"]})
    return {"items": items}


@action("resolve_missing")
def _resolve_missing(req):
    """User decision on missing files: drop their rows (file is truly gone)
    or leave them for a later rescan (file may come back)."""
    ctx = locate_library(req["path"])
    removed = 0
    for path in req.get("paths") or []:
        rel = pathio.to_relative(path, ctx["root"])
        ctx["store"].remove_file(rel)
        removed += 1
    return {"removed": removed}


@action("get_storage_stats")
def _get_storage_stats(req):
    ctx = locate_library(req["path"])
    return {"stats": ctx["library"].storage_stats()}


@action("clear_caches")
def _clear_caches(req):
    """Regenerable caches only: previews and posters. Face thumbnails are
    NOT regenerable without re-running detection, so they are never cleared
    here."""
    import shutil as _shutil

    ctx = locate_library(req["path"])
    removed = 0
    for name in ("previews", "posters"):
        target = Path(ctx["root"]) / DATA_DIR_NAME / name
        if target.is_dir():
            for child in target.iterdir():
                try:
                    if child.is_file():
                        child.unlink()
                        removed += 1
                except OSError:
                    pass
    return {"removed": removed}


@action("export_items")
def _export_items(req):
    import render as render_mod

    ctx = locate_library(req["path"])
    dest = req.get("dest")
    if not dest:
        raise ValueError("Missing dest")
    Path(dest).mkdir(parents=True, exist_ok=True)
    exported = 0
    errors = []
    for rel in req.get("paths") or []:
        try:
            rel_path = pathio.to_relative(rel, ctx["root"])
            state = ctx["store"].get_file_state(rel_path)
            edit = None
            if state and state["content_hash"] and req.get("edited", True):
                media = ctx["store"].get_media(state["content_hash"])
                if media and media["edit"]:
                    edit = json.loads(media["edit"])
            if state and state["kind"] == "video" and not edit:
                import shutil

                shutil.copyfile(
                    str(Path(ctx["root"]) / rel_path),
                    str(Path(dest) / Path(rel_path).name),
                )
            else:
                render_mod.export_baked(
                    ctx["store"], ctx["root"], rel_path, dest, edit=edit,
                    original=bool(edit is None or not req.get("edited", True)),
                )
            exported += 1
        except Exception as e:
            errors.append(f"{rel}: {e}")
    return {"exported": exported, "errors": errors}


@action("create_collage")
def _create_collage(req):
    ctx = locate_library(req["path"])
    return make_collage(ctx["store"], ctx["root"], req.get("hashes") or [])


@action("create_animation")
def _create_animation(req):
    ctx = locate_library(req["path"])
    return make_animation(
        ctx["store"], ctx["root"], req.get("hashes") or [],
        frame_duration_ms=int(req.get("frame_ms", 500)),
    )


@action("delete_creation")
def _delete_creation(req):
    ctx = locate_library(req["path"])
    delete_creation(ctx["store"], ctx["root"], req["creation_id"])
    return {}


@action("get_settings")
def _get_settings(req):
    ctx = locate_library(req["path"])
    keys = ("setting_labels", "setting_geocode", "setting_watch",
            "setting_watch_interval", "setting_theme")
    return {"settings": {k: ctx["store"].get_meta(k) for k in keys}}


@action("set_settings")
def _set_settings(req):
    ctx = locate_library(req["path"])
    settings = req.get("settings") or {}
    for key, value in settings.items():
        if not key.startswith("setting_"):
            raise ValueError(f"Bad setting key: {key}")
        ctx["store"].set_meta(key, str(value))
    if "setting_watch" in settings:
        _watcher.set_enabled(
            settings["setting_watch"] == "1",
            float(settings.get("setting_watch_interval") or 30),
        )
    return {}


@action("set_watch")
def _set_watch(req):
    ctx = locate_library(req["path"])
    ctx["store"].set_meta("setting_watch", "1" if req.get("enabled") else "0")
    if req.get("interval"):
        ctx["store"].set_meta("setting_watch_interval", str(req["interval"]))
    _watcher.set_enabled(bool(req.get("enabled")), float(req.get("interval") or 30))
    return {"enabled": bool(req.get("enabled"))}


# ---------------------------------------------------------------------------
# Watcher
# ---------------------------------------------------------------------------

def _watch_pass():
    if busy_worker():
        return
    for ctx in list(_contexts.values()):
        if (ctx["store"].get_meta("setting_watch", "0") != "1"):
            continue
        try:
            processor = get_shared_processor("auto")
            processor.thumbnail_dir = str(Path(ctx["root"]) / DATA_DIR_NAME / "thumbnails")
            ScanPipeline(
                str(Path(ctx["root"]) / DATA_DIR_NAME / "index.db"),
                ctx["root"],
                face_engine=processor,
                labeler=_make_labeler(ctx),
                emit=ProgressCoalescer(path=ctx["root"]),
            ).run()
            ctx["library"].purge_expired_trash()
        except Exception:
            logger.exception("Watch pass failed for %s", ctx["root"])


from watcher import Watcher  # noqa: E402  (needs the helpers above)

_watcher = Watcher(_watch_pass)


# ---------------------------------------------------------------------------
# Legacy helpers & loop
# ---------------------------------------------------------------------------

def _as_item(ctx, row):
    """Normalize a files/media row into the wire item shape (absolute path)."""
    row = dict(row)
    return {
        "path": _abs(ctx, row.get("path")),
        "hash": row.get("content_hash"),
        "kind": row.get("kind") or row.get("media_kind") or "photo",
        "ts": row.get("ts") or row.get("trashed_at"),
    }


def _unlink_quietly(path):
    try:
        if path and Path(path).is_file():
            Path(path).unlink()
    except OSError:
        pass


def ensure_library(path: str):
    library = Path(path)
    if not library.is_dir():
        raise NotADirectoryError(f"Folder not found: {path}")
    data_dir = library / DATA_DIR_NAME
    data_dir.mkdir(parents=True, exist_ok=True)
    return library, data_dir


@action("clear_index")
def _clear_index(req):
    path = req.get("path")
    if not path:
        raise ValueError("Missing path")
    library, data_dir = ensure_library(path)
    with _lifecycle_lock:
        if busy_worker():
            raise RuntimeError("Another operation is still running")
        if data_dir.exists():
            shutil.rmtree(data_dir, ignore_errors=True)
        _contexts.pop(str(Path(path).resolve()), None)
    reply(req.get("id"), True, {})
    emit({"event": "index_cleared", "path": str(library)})


_RESPONDED = object()  # sentinel: the handler already replied from a thread


def handle(req):
    req_id = req.get("id")
    handler = ACTIONS.get(req.get("action"))
    if handler is None:
        reply(req_id, False, error=f"Unknown action: {req.get('action')}")
        return
    try:
        data = handler(req)
        if data is not _RESPONDED and req_id is not None:
            reply(req_id, True, data)
    except Exception as e:
        logger.exception("Command failed: %s", req.get("action"))
        if req_id is not None:
            reply(req_id, False, error=str(e))


def _warm_heavy_imports():
    """Import the full inference chain here, in the main thread, before any
    job thread exists. Importing it lazily inside a worker thread deadlocks
    on Windows (the scipy extension modules never finish loading when the
    importing thread is not the primary thread of a piped process); v0.2 got
    this for free because sklearn was imported at startup for clustering.
    Model *weights* still load lazily at scan time — only the module chain
    is warmed here."""
    try:
        import onnxruntime  # noqa: F401

        import insightface.app  # noqa: F401  (drags albumentations -> scipy)

        from processor import FaceProcessor  # noqa: F401
    except Exception as e:
        logger.warning("Model chain not importable at startup: %s", e)


def main():
    logger.info("Backend started (v3)")
    _warm_heavy_imports()
    try:
        # Construct the default model pipeline here, on the main thread:
        # ONNX session creation inside worker threads hangs
        # nondeterministically on this platform.
        get_shared_processor("auto")
        get_shared_labeler()
    except Exception as e:
        logger.warning("Models not ready at startup: %s", e)
    while True:
        try:
            line = sys.stdin.readline()
        except KeyboardInterrupt:
            break
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Ignoring malformed request line")
            continue
        try:
            handle(req)
        except Exception as e:
            logger.exception("Command failed")
            reply(req.get("id"), False, error=str(e))
    logger.info("Backend stopped")


if __name__ == "__main__":
    main()
