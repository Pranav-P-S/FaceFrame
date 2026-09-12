import logging
import os
import queue
import threading
from pathlib import Path

import pathio
from database import Database

logger = logging.getLogger("FaceFrame.Scanner")

VALID_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
DATA_DIR_NAME = ".faceframe"


class Scanner:
    """Walks a photo library and keeps the face index in sync with disk.

    Re-scans are incremental: the mtime/size of every file is checked
    against the index first, and only new or modified files are decoded.
    Deleted files are pruned, and the thumbnails folder is never indexed
    as photo content.
    """

    def __init__(self, db_path: str, processor):
        self.db = Database(db_path)
        self.processor = processor

    def scan_directory(self, root_path: str, progress_callback=None, abort_check=None):
        root = str(Path(root_path).resolve())
        if not os.path.isdir(root):
            raise NotADirectoryError(f"Not a folder: {root_path}")

        logger.info("Scanning %s", root)
        image_files = _list_images(root)
        total = len(image_files)
        logger.info("Found %d image(s)", total)

        def report(current, filename, **extra):
            if progress_callback:
                progress_callback(current, total, filename, **extra)

        # Phase 1: cheap stat pass decides what actually needs decoding.
        # (Decoding first would make every rescan a full re-decode.)
        pending = []
        skipped_dirs = 0
        faces_found = 0
        for index, full_path in enumerate(image_files):
            if abort_check and abort_check():
                logger.info("Scan cancelled by user")
                return {
                    "cancelled": True,
                    "processed": index,
                    "faces": faces_found,
                    "skipped_dirs": skipped_dirs,
                }
            report(index, os.path.basename(full_path))

            try:
                stat = os.stat(full_path)
                mtime, size = stat.st_mtime, stat.st_size
            except OSError as e:
                logger.warning("Cannot stat %s: %s", full_path, e)
                continue

            existing = self.db.get_file(pathio.to_relative(full_path, root))
            if existing and existing[1] == mtime and existing[2] == size:
                continue
            pending.append((full_path, mtime, size))

        # Phase 2: decode + recognize only what changed, with I/O
        # overlapped against inference.
        decoder = _PrefetchDecoder([p for p, _, _ in pending])
        decoder.start()
        done_before = total - len(pending)
        try:
            for offset, (full_path, mtime, size) in enumerate(pending):
                if abort_check and abort_check():
                    logger.info("Scan cancelled by user")
                    return {
                        "cancelled": True,
                        "processed": done_before + offset,
                        "faces": faces_found,
                        "skipped_dirs": skipped_dirs,
                    }

                rel_path = pathio.to_relative(full_path, root)
                report(done_before + offset, os.path.basename(full_path))

                img = decoder.get(offset)
                if img is _PrefetchDecoder.FAILED:
                    continue

                try:
                    faces = self.processor.process_decoded(full_path, img)
                except Exception:
                    logger.exception("Processing failed for %s", full_path)
                    continue
                if faces is None:
                    # Detection itself failed (not "no faces"): leave the
                    # index untouched so a later scan retries the file.
                    continue

                # Only touch the index once detection succeeded. Ordering
                # here makes interruptions self-healing: if the app dies
                # before the file row is written, the stale mtime makes the
                # next scan pick the file up again.
                self._drop_faces(rel_path, root)
                for face in faces:
                    if face.get("thumbnail"):
                        face["thumbnail"] = pathio.to_relative(
                            face["thumbnail"], root
                        )
                self.db.add_faces(rel_path, faces)
                self.db.upsert_file(rel_path, mtime, size)
                faces_found += len(faces)

            report(total, "Done")
        finally:
            decoder.stop()

        removed = self._prune_deleted(root)
        if removed:
            logger.info("Pruned %d deleted file(s) from the index", removed)
            self.db.delete_empty_persons()

        logger.info(
            "Scan finished: %d file(s), %d new face(s)", total, faces_found
        )
        return {
            "cancelled": False,
            "processed": total,
            "faces": faces_found,
            "skipped_dirs": skipped_dirs,
        }

    def _drop_faces(self, rel_path: str, root: str):
        for thumb_rel in self.db.remove_faces_for_file(rel_path):
            # Legacy rows may hold absolute paths; pathio.to_absolute would
            # mangle those on POSIX, so pass them through untouched.
            abs_thumb = (
                thumb_rel
                if os.path.isabs(thumb_rel)
                else pathio.to_absolute(thumb_rel, root)
            )
            _unlink(abs_thumb)

    def _prune_deleted(self, root: str) -> int:
        removed = 0
        for rel_path in self.db.all_file_paths():
            # Rows written by pre-0.2 builds hold absolute paths; they can
            # never match a relative key again, so retire them.
            if os.path.isabs(rel_path) or not os.path.isfile(
                pathio.to_absolute(rel_path, root)
            ):
                self._drop_faces(rel_path, root)
                self.db.remove_file(rel_path)
                removed += 1
        return removed


class _PrefetchDecoder:
    """Decodes upcoming images on side threads so disk I/O overlaps inference."""

    # Identity sentinel, never equality: decoded images are numpy arrays
    # and `==` on them would broadcast instead of comparing.
    FAILED = object()

    def __init__(self, paths, workers: int = 2):
        self._paths = paths
        self._pending = {}
        self._ready = queue.Queue(maxsize=4)
        self._next_to_submit = 0
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._threads = [
            threading.Thread(target=self._work, daemon=True, name=f"decode-{i}")
            for i in range(max(1, workers))
        ]

    def start(self):
        for t in self._threads:
            t.start()

    def stop(self):
        self._stop_event.set()
        # Unblock the consumer if it is waiting on an empty queue.
        for _ in self._threads:
            try:
                self._ready.put_nowait(None)
            except queue.Full:
                pass

    def get(self, index: int):
        """Return the decoded BGR image for this exact index, or FAILED."""
        if index in self._pending:
            return self._pending.pop(index)
        while True:
            item = self._ready.get()
            if item is None:
                return self.FAILED
            done_index, image = item
            if done_index == index:
                return image
            self._pending[done_index] = image

    def _work(self):
        from processor import read_image_bgr

        while not self._stop_event.is_set():
            with self._lock:
                index = self._next_to_submit
                if index >= len(self._paths):
                    return
                self._next_to_submit += 1
            path = self._paths[index]
            try:
                image = read_image_bgr(path)
            except Exception:
                logger.exception("Decode failed for %s", path)
                image = None
            if self._stop_event.is_set():
                return
            item = (index, image if image is not None else self.FAILED)
            while not self._stop_event.is_set():
                try:
                    self._ready.put(item, timeout=0.2)
                    break
                except queue.Full:
                    continue


def _list_images(root: str):
    """All indexable image paths under root, skipping our own data folder."""
    images = []
    scan_errors = []

    def on_error(err):
        scan_errors.append(err)

    for dirpath, dirnames, filenames in os.walk(root, onerror=on_error):
        # Never index our own metadata (thumbnails are JPEGs too).
        dirnames[:] = [d for d in dirnames if d != DATA_DIR_NAME]
        for name in filenames:
            if Path(name).suffix.lower() in VALID_EXTENSIONS:
                images.append(os.path.join(dirpath, name))
    for err in scan_errors:
        logger.warning("Directory not readable during scan: %s", err)
    images.sort()
    return images


def _unlink(path):
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass
