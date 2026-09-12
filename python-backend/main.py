import base64
import json
import logging
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pathio
from clusterer import Clusterer
from database import Database
from scanner import DATA_DIR_NAME, Scanner, VALID_EXTENSIONS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("FaceFrame.Backend")

_write_lock = threading.Lock()
_lifecycle_lock = threading.Lock()
_scan_thread = None
_cluster_thread = None
_abort_scan = threading.Event()
_preview_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="preview")


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


def locate_library(path: str):
    """Resolve a library root; raises if the folder is gone.

    Unlike the scan path, reads never re-create folders that vanished --
    a stale path in the UI should surface, not silently resurrect.
    """
    library = Path(path)
    if not library.is_dir():
        raise NotADirectoryError(f"Folder not found: {path}")
    return library, library / DATA_DIR_NAME


def ensure_library(path: str):
    library = Path(path)
    if not library.is_dir():
        raise NotADirectoryError(f"Folder not found: {path}")
    data_dir = library / DATA_DIR_NAME
    (data_dir / "thumbnails").mkdir(parents=True, exist_ok=True)
    (data_dir / "previews").mkdir(parents=True, exist_ok=True)
    return library, data_dir


def busy_worker():
    return (_scan_thread and _scan_thread.is_alive()) or (
        _cluster_thread and _cluster_thread.is_alive()
    )


def get_processor(provider):
    from processor import FaceProcessor

    import onnxruntime

    choice = (provider or "auto").lower()
    if choice == "auto":
        # Try the GPU when the runtime ships with a CUDA provider; the
        # processor itself falls back to CPU if the CUDA libraries are
        # missing at load time.
        use_gpu = "CUDAExecutionProvider" in onnxruntime.get_available_providers()
    else:
        use_gpu = "cuda" in choice
    return FaceProcessor(use_gpu=use_gpu, thumbnail_dir=None)


def run_scan(path, provider):
    global _scan_thread
    try:
        library, data_dir = ensure_library(path)
        root = str(library)

        if _abort_scan.is_set():
            emit({"event": "scan_cancelled", "path": root})
            return

        emit({"event": "model_status", "state": "loading"})
        processor = get_processor(provider)
        processor.thumbnail_dir = str(data_dir / "thumbnails")
        emit({"event": "model_status", "state": "ready"})

        if _abort_scan.is_set():
            # The model load can take minutes on first run; respect a
            # cancel that arrived during it.
            emit({"event": "scan_cancelled", "path": root})
            return

        scanner = Scanner(str(data_dir / "index.db"), processor)

        def on_progress(current, total, filename, **extra):
            emit(
                {
                    "event": "scan_progress",
                    "current": current,
                    "total": total,
                    "file": filename,
                }
            )

        emit({"event": "scan_started", "path": root})
        stats = scanner.scan_directory(
            root,
            progress_callback=on_progress,
            abort_check=_abort_scan.is_set,
        )

        if stats.get("cancelled"):
            emit({"event": "scan_cancelled", "path": root})
        else:
            emit({"event": "scan_complete", "path": root, **stats})
    except Exception as e:
        logger.exception("Scan failed")
        emit({"event": "scan_error", "message": str(e)})
    finally:
        with _lifecycle_lock:
            _scan_thread = None


def run_cluster(path, req_id):
    global _cluster_thread
    try:
        _library, data_dir = locate_library(path)
        stats = Clusterer(str(data_dir / "index.db")).run_clustering()
        reply(req_id, True, stats)
        emit({"event": "cluster_done", **stats})
    except Exception as e:
        logger.exception("Clustering failed")
        reply(req_id, False, error=str(e))
    finally:
        with _lifecycle_lock:
            _cluster_thread = None


def handle(req):
    global _scan_thread, _cluster_thread
    action = req.get("action")
    req_id = req.get("id")
    path = req.get("path")

    if action == "ping":
        reply(req_id, True, {"version": 2})

    elif action == "get_providers":
        from processor import FaceProcessor

        reply(req_id, True, {"compute": FaceProcessor.compute_info()})

    elif action == "scan":
        if not path:
            reply(req_id, False, error="Missing path")
            return
        with _lifecycle_lock:
            if busy_worker():
                reply(req_id, False, error="Another operation is still running")
                return
            _abort_scan.clear()
            _scan_thread = threading.Thread(
                target=run_scan,
                args=(path, req.get("provider")),
                daemon=True,
            )
            _scan_thread.start()
        reply(req_id, True, {"started": True})

    elif action == "cancel_scan":
        _abort_scan.set()
        reply(req_id, True, {})

    elif action == "cluster":
        if not path:
            reply(req_id, False, error="Missing path")
            return
        with _lifecycle_lock:
            if busy_worker():
                reply(req_id, False, error="Another operation is still running")
                return
            _cluster_thread = threading.Thread(
                target=run_cluster, args=(path, req_id), daemon=True
            )
            _cluster_thread.start()

    elif action == "get_persons":
        library, data_dir = locate_library(path)
        if not (data_dir / "index.db").exists():
            reply(req_id, True, {"persons": []})
            return
        db = Database(str(data_dir / "index.db"))
        persons = [
            {
                "id": row["id"],
                "name": row["name"] or f"Person {row['id']}",
                "thumbnail": _abs_or_none(row["thumbnail_path"], library),
                "face_count": row["face_count"],
            }
            for row in db.persons_with_counts()
        ]
        reply(req_id, True, {"persons": persons})

    elif action == "get_unclustered":
        library, data_dir = locate_library(path)
        if not (data_dir / "index.db").exists():
            reply(req_id, True, {"faces": []})
            return
        db = Database(str(data_dir / "index.db"))
        faces = [
            {
                "id": row["id"],
                "file_path": pathio.to_absolute(row["file_path"], str(library)),
                "bbox": json.loads(row["bbox"]) if row["bbox"] else [0, 0, 0, 0],
                "thumbnail": _abs_or_none(row["thumbnail_path"], library),
            }
            for row in db.unclustered_faces()
        ]
        reply(req_id, True, {"faces": faces})

    elif action == "get_photos_by_person":
        person_id = req.get("person_id")
        if person_id is None:
            reply(req_id, False, error="Missing person_id")
            return
        library, data_dir = locate_library(path)
        if not (data_dir / "index.db").exists():
            reply(req_id, True, {"photos": []})
            return
        db = Database(str(data_dir / "index.db"))
        photos = [
            {
                "path": pathio.to_absolute(row["path"], str(library)),
                "face_count": row["face_count"],
            }
            for row in db.person_photos(person_id)
        ]
        reply(req_id, True, {"photos": photos})

    elif action == "rename_person":
        person_id, new_name = req.get("person_id"), (req.get("new_name") or "").strip()
        if not path or person_id is None or not new_name:
            reply(req_id, False, error="Missing path, person_id or name")
            return
        _library, data_dir = locate_library(path)
        Database(str(data_dir / "index.db")).rename_person(person_id, new_name)
        reply(req_id, True, {})

    elif action == "merge_persons":
        keep_id, merge_id = req.get("keep_id"), req.get("merge_id")
        if not path or keep_id is None or merge_id is None:
            reply(req_id, False, error="Missing path, keep_id or merge_id")
            return
        library, data_dir = locate_library(path)
        stale_thumb = Database(str(data_dir / "index.db")).merge_persons(
            keep_id, merge_id
        )
        if stale_thumb:
            _unlink_quietly(pathio.to_absolute(stale_thumb, str(library)))
        reply(req_id, True, {})

    elif action == "clear_index":
        library, data_dir = locate_library(path)
        with _lifecycle_lock:
            if busy_worker():
                reply(req_id, False, error="Another operation is still running")
                return
            if data_dir.exists():
                shutil.rmtree(data_dir, ignore_errors=True)
        reply(req_id, True, {})
        emit({"event": "index_cleared", "path": str(library)})

    elif action == "get_image_preview":
        file_path = req.get("file_path")
        max_dim = min(int(req.get("max_dim", 640)), 4096)
        if not file_path:
            reply(req_id, False, error="Missing file_path")
            return
        if Path(file_path).suffix.lower() not in VALID_EXTENSIONS:
            reply(req_id, False, error="Not a readable image")
            return
        previews_dir = _previews_dir_for(file_path)
        if previews_dir is None:
            reply(req_id, False, error="File is not part of an indexed library")
            return
        # First-time decodes of large images can take a while; keep them
        # off the stdin reader thread so commands stay responsive.
        _preview_pool.submit(
            _serve_preview, req_id, file_path, previews_dir, max_dim
        )

    else:
        reply(req_id, False, error=f"Unknown action: {action}")


def _serve_preview(req_id, file_path, previews_dir, max_dim):
    from processor import generate_preview

    try:
        preview = generate_preview(file_path, previews_dir, max_dim=max_dim)
        if preview is None:
            reply(req_id, False, error="Could not read image")
            return
        data = base64.b64encode(Path(preview).read_bytes()).decode("ascii")
        reply(req_id, True, {"data_url": f"data:image/jpeg;base64,{data}"})
    except Exception as e:
        logger.warning("Preview failed for %s: %s", file_path, e)
        reply(req_id, False, error="Could not read image")


def _abs_or_none(rel, library: Path):
    return str(pathio.to_absolute(rel, str(library))) if rel else None


def _previews_dir_for(file_path: str):
    """Preview cache lives inside the library's .faceframe folder. Files
    outside any indexed library are refused -- the UI only ever shows
    indexed paths, and this keeps us from writing anywhere unexpected."""
    directory = Path(file_path).parent
    for parent in [directory, *directory.parents]:
        data_dir = parent / DATA_DIR_NAME
        if data_dir.is_dir():
            previews = data_dir / "previews"
            try:
                previews.mkdir(parents=True, exist_ok=True)
            except OSError:
                return None
            return str(previews)
    return None


def _unlink_quietly(path):
    try:
        if path and Path(path).is_file():
            Path(path).unlink()
    except OSError:
        pass


def main():
    logger.info("Backend started")
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
