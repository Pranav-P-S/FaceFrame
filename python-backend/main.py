"""FaceFrame backend: command loop over NDJSON stdin/stdout.

Handlers register through @action and receive the raw request dict; anything
they return is the response ``data``. Raising replies with an error. The
dispatcher knows nothing about photos — every feature lives in its module
(store, scan, library, people, views, places, memories, render, creations,
labels, watcher) and plugs in here.
"""

import base64
import json
import sqlite3
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
from scan import DATA_DIR_NAME, ScanPipeline
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
            try:
                store = Store(str(data_dir / "index.db"), library_root=root)
            except sqlite3.DatabaseError as e:
                raise ValueError(
                    "index_corrupt: the photo index is unreadable. "
                    "Use Settings > Clear index to rebuild it (photos are not touched)."
                ) from e
            except RuntimeError as e:
                raise ValueError(
                    f"index_newer_version: {e}. Install a newer version of FaceFrame."
                ) from e
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
        # ONNX session construction outside the startup path deadlocks
        # nondeterministically on Windows — never build on demand; fall back
        # to the shared engine (the documented CPU-fallback behavior).
        logger.warning("CUDA requested but not preloaded; using the shared engine")
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

        # Purge before the completion event: once scan_complete is out, the
        # busy-guard must genuinely be free, or an immediate follow-up scan
        # races with the cleanup tail.
        if not _abort_scan.is_set():
            ctx["library"].purge_expired_trash()

        stats = pipeline.run()

        # scan.py already emitted scan_complete / scan_cancelled with full
        # stats through the coalescer — no re-emit here, it would arrive
        # twice (and without the processed alias) in the renderer.
        if stats.get("cancelled"):
            logger.info("Scan cancelled: %s", root)
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
        global _labeler_tried
        try:
            from labels import ImageLabeler

            # Files only — never build the ONNX session in this daemon
            # thread. The shared labeler is constructed where a scan runs,
            # exactly like a first scan without a cached model.
            ImageLabeler(auto_download=True, download_only=True)
            _labeler_tried = False
            logger.info("Label model cached; labels will apply on the next scan")
        except Exception as e:
            logger.warning("Label model download failed: %s", e)

    threading.Thread(target=download, daemon=True, name="label-prime").start()


def busy_worker():
    # Cluster, scan and watcher passes all mutate the same tables (faces in
    # particular): none of them may interleave with another.
    if _cluster_thread is not None and _cluster_thread.is_alive():
        return True
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
    archived_only = bool(req.get("archived_only"))
    groups = views.feed_groups(
        ctx["store"],
        view=req.get("view", "days"),
        include_locked=bool(req.get("include_locked")),
        include_archived=bool(req.get("include_archived")) or archived_only,
        archived_only=archived_only,
        favorite=req.get("favorite"),
        limit=max(1, min(int(req.get("limit", 4000)), 20000)),
    )
    root = ctx["root"]
    for group in groups:
        for item in group["items"]:
            item["path"] = _abs(ctx, item["path"])
    return {"groups": groups}


@action("search")
def _search(req):
    import views

    ctx = locate_library(req["path"])
    result = views.search_items(
        ctx["store"], req.get("query", ""),
        include_locked=bool(req.get("include_locked")),
    )
    for item in result["items"]:
        item["path"] = _abs(ctx, item["path"])
    return result


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
    rel = pathio.to_relative(file_path, ctx["root"])
    detail = views.item_detail(ctx["store"], rel)
    if detail is None:
        raise ValueError("Item not found")
    detail["path"] = file_path
    detail["duplicate_paths"] = [_abs(ctx, p) for p in detail["duplicate_paths"]]
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
    failed = 0
    for row in ctx["library"].trashed_items():
        if ctx["library"]._send_path_to_os_trash(row["path"]):
            ctx["store"].remove_file(row["path"])
            removed += 1
        else:
            failed += 1
    if failed:
        logger.error("empty_trash: %d item(s) could not be sent to the OS trash", failed)
    return {"removed": removed, "failed": failed}


@action("delete_from_disk")
def _delete_from_disk(req):
    ctx = locate_library(req["path"])
    paths = req.get("paths") or []
    removed = ctx["library"].delete_from_disk(paths)
    failed = len(paths) - removed
    if failed:
        logger.error("delete_from_disk: %d path(s) refused or failed", failed)
    return {"removed": removed, "failed": failed}


@action("set_caption")
def _set_caption(req):
    ctx = locate_library(req["path"])
    caption = req.get("caption") or ""
    if len(caption) > 4000:
        raise ValueError("Caption too long (max 4000 characters)")
    ctx["library"].set_caption(req["hash"], caption)
    return {}


@action("set_date_override")
def _set_date_override(req):
    ctx = locate_library(req["path"])
    epoch = req.get("epoch")
    if epoch is not None:
        epoch = float(epoch)
        # A poisoned value here lands in every feed row's COALESCE and can
        # overflow the renderer's time conversion — refuse the extremes
        # (covers years ~1000-28000).
        import math

        if not math.isfinite(epoch) or not -3e10 < epoch < 3.5e11:
            raise ValueError("Date out of supported range")
    ctx["library"].set_date_override(req["hash"], epoch)
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
            """SELECT path FROM files
               WHERE content_hash=? AND missing=0 AND trashed_at IS NULL
               ORDER BY path LIMIT 1""",
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
    ctx = locate_library(req["path"])
    meta = next(
        (a for a in ctx["library"].list_albums() if a["id"] == req["album_id"]),
        None,
    )
    if not meta:
        raise ValueError("Album not found")
    # One joined query — an album of 500 items must not cost 1000 round trips.
    with ctx["store"].connect() as conn:
        rows = conn.execute(
            """SELECT ai.content_hash, ai.position, ai.added_at,
                      MIN(f.path) AS rel_path,
                      COALESCE(m.date_override, m.capture_time, f.mtime) AS ts,
                      m.kind AS media_kind, m.width, m.height, m.duration,
                      m.favorite, m.archived, m.locked, m.caption,
                      m.poster_path, m.flags
               FROM album_items ai
               JOIN files f ON f.content_hash = ai.content_hash
                    AND f.missing=0 AND f.trashed_at IS NULL
               JOIN media m ON m.content_hash = ai.content_hash
               WHERE ai.album_id=?
               GROUP BY ai.position, ai.content_hash
               ORDER BY ai.position, ai.added_at""",
            (req["album_id"],),
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["path"] = _abs(ctx, item.pop("rel_path"))
        item["kind"] = item.get("kind") or item.get("media_kind") or "photo"
        try:
            import json as _json

            item["flags"] = _json.loads(item["flags"]) if item.get("flags") else {}
        except ValueError:
            item["flags"] = {}
        items.append(item)
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
    include_hidden = bool(req.get("include_hidden"))
    persons = []
    for row in ctx["people"].list_persons(include_hidden=include_hidden):
        persons.append(
            {
                "id": row["id"],
                "name": row["name"] or f"Person {row['id']}",
                "thumbnail": _abs(ctx, row["thumbnail_path"]),
                "face_count": row["face_count"],
                "hidden": bool(row["hidden"]),
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
        {
            "path": _abs(ctx, row["path"]),
            "content_hash": row["content_hash"],
            "face_count": row["face_count"],
            "kind": row["kind"] or "photo",
            "width": row["width"],
            "height": row["height"],
        }
        for row in ctx["people"].person_photos(req["person_id"])
    ]
    return {"photos": photos}


@action("get_person_faces")
def _get_person_faces(req):
    """Per-face rows for one person: the split and feature-photo flows pick
    from these (photos group multiple faces, faces are the real unit)."""
    ctx = locate_library(req["path"])
    faces = []
    for row in ctx["people"].person_faces(req["person_id"]):
        faces.append(
            {
                "id": row["id"],
                "content_hash": row["content_hash"],
                "thumbnail": _abs(ctx, row["thumbnail_path"]),
            }
        )
    return {"faces": faces}


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
        # The losing person's thumbnail may still be referenced by a surviving
        # face row (person thumbnails are chosen among face crops); only unlink
        # when nothing points at it any more, or check_index would report a
        # missing thumb forever.
        still_referenced = any(
            row["thumbnail_path"] for row in ctx["people"].person_faces(req["keep_id"])
            if row["thumbnail_path"] == stale
        )
        if not still_referenced:
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
    thumb = req["thumbnail"]
    # The renderer only knows absolute paths; store the library-relative form
    # so the library stays movable.
    if os.path.isabs(thumb):
        resolved = ctx["library"]._contained(thumb)
        thumb = pathio.to_relative(str(resolved), ctx["root"])
    ctx["people"].set_person_thumbnail(req["person_id"], thumb.replace("\\", "/"))
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

    # Face thumbnails live under .faceframe/thumbnails, never get a files row
    # (the scanner skips .faceframe), and the media:// protocol refuses that
    # tree — so this is the only channel that can serve them. They are
    # already-encoded JPEGs: hand the bytes straight back.
    norm_rel = rel.replace("\\", "/")
    if norm_rel.startswith(".faceframe/thumbnails/") and norm_rel.endswith(".jpg"):
        thumbs_root = (Path(ctx["root"]) / ".faceframe" / "thumbnails").resolve()
        thumb = (Path(ctx["root"]) / norm_rel).resolve()
        if thumb.parent != thumbs_root or not thumb.is_file():
            raise ValueError("Thumbnail not found")
        data = base64.b64encode(thumb.read_bytes()).decode("ascii")
        return {"data_url": f"data:image/jpeg;base64,{data}"}

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
    try:
        precision = int(req.get("precision", 4))
    except (TypeError, ValueError):
        precision = 4
    precision = max(2, min(precision, 12))  # geohash max meaningful = 12
    groups = places_mod.place_groups(ctx["store"], precision=precision)
    for group in groups:
        group["cover"] = _abs(ctx, _cover_rel(ctx, group["cover_hash"]))
        group["items"] = [_abs(ctx, p) for p in group["items"]]
    # Geocoding must never stall the command loop: cached names come back
    # immediately; uncached cells are filled politely in the background
    # (rate-limited, capped per pass) and announced with an event.
    if req.get("geocode") and ctx["store"].get_meta("setting_geocode", "0") == "1":
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
        items = []
        for p, h in ((i["path"], i["content_hash"]) for i in memory["items"]):
            item = {"path": p, "content_hash": h}
            media = ctx["store"].get_media(h)
            if media:
                item["kind"] = media["kind"] or "photo"
                item["width"] = media["width"]
                item["height"] = media["height"]
            items.append(_as_item(ctx, item))
        memory["items"] = items
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
        # Renderer-supplied: refuse anything outside the library, the same
        # contract every other path-taking action enforces.
        ctx["library"]._contained(path)
        rel = pathio.to_relative(path, ctx["root"])
        ctx["store"].remove_file(rel)
        removed += 1
    return {"removed": removed}


@action("check_index")
def _check_index(req):
    """Integrity self-check: sqlite health, orphans, missing thumbnails,
    full-text drift. repair=true runs the existing self-healing sweeps."""
    ctx = locate_library(req["path"])
    report = {}
    with ctx["store"].connect() as conn:
        report["integrity"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
        report["orphan_media"] = conn.execute(
            """SELECT COUNT(*) FROM media
               WHERE content_hash NOT IN
                 (SELECT DISTINCT content_hash FROM files
                  WHERE content_hash IS NOT NULL)"""
        ).fetchone()[0]
        report["missing_thumbs"] = 0
        thumbs = ctx["store"].referenced_face_thumbs()
        root = Path(ctx["root"])
        for rel in thumbs:
            if not (root / rel).is_file():
                report["missing_thumbs"] += 1
        report["fts_rows"] = conn.execute(
            "SELECT COUNT(*) FROM fts_map"
        ).fetchone()[0]
        report["live_files"] = conn.execute(
            """SELECT COUNT(*) FROM files
               WHERE missing=0 AND trashed_at IS NULL"""
        ).fetchone()[0]
    # Absolute drift: deletions used to leave FTS rows behind, which the old
    # one-directional max(0, …) could never see.
    report["fts_drift"] = abs(report["live_files"] - report["fts_rows"])
    if req.get("repair"):
        ctx["store"].prune_orphan_media()
        ctx["store"].sync_fts()
        report["repaired"] = True
    return {"report": report}


@action("backup_index")
def _backup_index(req):
    """Consistent backup of the irreplaceable parts of the index: the
    database (snapshotted via sqlite's backup API, safe under WAL) and face
    thumbnails (not regenerable without re-inference). Regenerable caches
    (previews/posters/creations) are excluded."""
    import sqlite3 as _sq
    import zipfile

    ctx = locate_library(req["path"])
    dest = req.get("dest")
    if not dest:
        raise ValueError("Missing dest")
    dest_path = Path(dest)
    dest_path.mkdir(parents=True, exist_ok=True)
    data_dir = Path(ctx["root"]) / DATA_DIR_NAME
    stamp = time.strftime("%Y%m%d")
    out = dest_path / f"{Path(ctx['root']).name}-{stamp}.faceframe.zip"

    snapshot = data_dir / "index.snapshot.db"
    # sqlite3's context manager only commits — close explicitly or the
    # snapshot handle stays open and the unlink below fails on Windows.
    src_conn = _sq.connect(str(data_dir / "index.db"))
    dst_conn = _sq.connect(str(snapshot))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()
    try:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(snapshot, "index.db")
            thumbs = data_dir / "thumbnails"
            for f in sorted(thumbs.rglob("*")) if thumbs.is_dir() else []:
                if f.is_file():
                    zf.write(f, f"thumbnails/{f.relative_to(thumbs)}")
            zf.writestr(
                "meta.json",
                json.dumps({
                    "schema_version": 3,
                    "created": time.time(),
                    "library": ctx["root"],
                }),
            )
    finally:
        snapshot.unlink(missing_ok=True)
    return {"path": str(out), "bytes": out.stat().st_size}


@action("restore_index")
def _restore_index(req):
    """Swap in a previously backed-up index. Guarded by the busy check like
    every mutating action; the backup file itself may live anywhere the user
    points at, but archive members may only land inside the library."""
    import zipfile

    ctx = locate_library(req["path"])
    src = Path(req.get("src") or "")
    if not src.is_file():
        raise ValueError("Backup file not found")
    if busy_worker():
        raise RuntimeError("Another operation is still running")
    data_dir = Path(ctx["root"]) / DATA_DIR_NAME
    data_dir.mkdir(parents=True, exist_ok=True)
    data_dir_resolved = data_dir.resolve()
    with zipfile.ZipFile(src) as zf:
        names = zf.namelist()
        if "index.db" not in names:
            raise ValueError("Not a FaceFrame index backup")
        # Replace, don't merge: the backup is a full index snapshot.
        for rel in ("index.db", "index.db-wal", "index.db-shm"):
            target = data_dir / rel
            if target.exists():
                target.unlink()
        zf.extract("index.db", data_dir)
        for name in names:
            if name.startswith("thumbnails/"):
                # Manual extraction gets no zipfile sanitization: refuse any
                # member that would land outside the data dir (zip-slip).
                out = (data_dir / name).resolve()
                if not out.is_relative_to(data_dir_resolved):
                    logger.warning("Backup member escapes the library, skipped: %s", name)
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as fsrc, open(out, "wb") as fdst:
                    fdst.write(fsrc.read())
    _contexts.pop(ctx["root"], None)
    emit({"event": "index_restored", "path": ctx["root"]})
    return {"restored": True}


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
    for key in settings:
        if not key.startswith("setting_"):
            raise ValueError(f"Bad setting key: {key}")
    # Validate everything before writing anything, so a bad key can't leave
    # the request half-applied.
    for key, value in settings.items():
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
    interval = max(5.0, min(float(req.get("interval") or 30), 3600.0))
    ctx["store"].set_meta("setting_watch_interval", str(interval))
    _watcher.set_enabled(bool(req.get("enabled")), interval)
    return {"enabled": bool(req.get("enabled"))}


# ---------------------------------------------------------------------------
# Watcher
# ---------------------------------------------------------------------------

def _watch_pass():
    global _scan_thread
    # Claim the same lifecycle lock the manual scan uses, so a watcher pass
    # can never interleave with a manual scan (or vice versa). Registering
    # this thread as _scan_thread makes busy_worker() cover it everywhere.
    with _lifecycle_lock:
        if busy_worker():
            return
        _scan_thread = threading.current_thread()
    try:
        for ctx in list(_contexts.values()):
            if (ctx["store"].get_meta("setting_watch", "0") != "1"):
                continue
            try:
                processor = get_shared_processor("auto")
                processor.thumbnail_dir = str(Path(ctx["root"]) / DATA_DIR_NAME / "thumbnails")
                pipeline = ScanPipeline(
                    str(Path(ctx["root"]) / DATA_DIR_NAME / "index.db"),
                    ctx["root"],
                    face_engine=processor,
                    labeler=_make_labeler(ctx),
                    emit=ProgressCoalescer(path=ctx["root"]),
                )
                pipeline.abort_check = _abort_scan.is_set
                _abort_scan.clear()
                stats = pipeline.run()
                ctx["library"].purge_expired_trash()
                # scan.py emits scan_complete / scan_cancelled itself.
            except Exception:
                logger.exception("Watch pass failed for %s", ctx["root"])
    finally:
        with _lifecycle_lock:
            _scan_thread = None


from watcher import Watcher  # noqa: E402  (needs the helpers above)

_watcher = Watcher(_watch_pass)


# ---------------------------------------------------------------------------
# Legacy helpers & loop
# ---------------------------------------------------------------------------

def _as_item(ctx, row):
    """Normalize a files/media row into the wire item shape (absolute path).
    The renderer contract is `content_hash` — keep that name, not `hash`."""
    row = dict(row)
    return {
        "path": _abs(ctx, row.get("path")),
        "content_hash": row.get("content_hash"),
        "kind": row.get("kind") or row.get("media_kind") or "photo",
        "ts": row.get("ts") or row.get("trashed_at"),
        "width": row.get("width"),
        "height": row.get("height"),
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
    return _RESPONDED


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
    # Force UTF-8 stdio: default Windows pipe encoding is a legacy code page,
    # which mojibakes every non-ASCII caption/name and can crash readline on
    # undefined bytes (a crash loop, since the host restarts us).
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    logger.info("Backend started (v3)")
    # Announce BEFORE the heavy chain: on first run this imports the models
    # and can download hundreds of MB, so the host must know we are alive
    # (its ping watchdog only sees answers once the loop below starts).
    emit({"event": "model_status", "state": "loading"})
    _warm_heavy_imports()
    try:
        # Construct the default model pipeline here, on the main thread:
        # ONNX session creation inside worker threads hangs
        # nondeterministically on this platform.
        get_shared_processor("auto")
        get_shared_labeler()
    except Exception as e:
        logger.warning("Models not ready at startup: %s", e)
    emit({"event": "model_status", "state": "ready"})
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
