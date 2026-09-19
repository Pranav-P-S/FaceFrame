"""Scan pipeline: keep the index in sync with disk, computing each unique
content exactly once.

Order of work per pass:
  1. discover   - walk the library (never entering .faceframe)
  2. change-detect - (size, mtime) short-circuits unchanged files
  3. hash       - sha256 only what changed; a hash hit (file moved/touched)
                  costs a rehash, never a decode
  4. process    - decode + faces + labels + exif + poster, once per content
  5. reconcile  - files that vanished become ``missing`` (never auto-pruned)
  6. gc         - cache files with no index reference are swept

Interruption invariant: a file row is written only after its media row is
committed, so a crash mid-pass always self-heals on the next pass.
"""

import logging
import os
import time
from pathlib import Path

import pathio
from store import Store

logger = logging.getLogger("FaceFrame.Scan")

DATA_DIR_NAME = ".faceframe"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv", ".wmv", ".3gp"}
VALID_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS  # legacy name, kept for imports


class ScanPipeline:
    def __init__(
        self,
        db_path: str,
        library_root: str,
        face_engine=None,
        labeler=None,
        emit=None,
    ):
        self.root = str(Path(library_root).resolve())
        self.data_dir = Path(self.root) / DATA_DIR_NAME
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(db_path, library_root=library_root)
        self.face_engine = face_engine
        self.labeler = labeler
        self.emit = emit or (lambda event, **payload: None)
        self.abort_check = lambda: False

    # ------------------------------------------------------------------ run

    def run(self) -> dict:
        started = time.time()
        stats = {
            "files_total": 0,
            "skipped_unchanged": 0,
            "hashed": 0,
            "content_unchanged": 0,
            "dedup_hits": 0,
            "decoded": 0,
            "faces": 0,
            "videos": 0,
            "missing_now": 0,
            "gc_removed": 0,
            "cancelled": False,
        }

        discovered = self._discover()
        stats["files_total"] = len(discovered)
        self.emit("scan_started", path=self.root, total=len(discovered))

        seen_paths = set()
        pending = []  # (abs_path, rel_path, stat)
        known = self.store.all_file_states()
        for abs_path in discovered:
            if self.abort_check():
                return self._cancelled(stats)
            rel_path = pathio.to_relative(abs_path, self.root)
            seen_paths.add(rel_path)
            try:
                stat = os.stat(abs_path)
            except OSError as e:
                logger.warning("Cannot stat %s: %s", abs_path, e)
                continue
            existing = known.get(rel_path)
            if (
                existing
                and existing["content_hash"]
                and not existing["missing"]
                and existing["size"] == stat.st_size
                and existing["mtime"] == stat.st_mtime
                # Content indexed by a pass without face models stays
                # 'hashed': it must not be short-circuited here or it would
                # never receive faces.
                and existing["analysis_state"] == "analyzed"
            ):
                stats["skipped_unchanged"] += 1
                continue
            pending.append((abs_path, rel_path, stat))

        # Hash + process with decode overlapped against hashing of the next
        # candidates.
        for abs_path, rel_path, stat in pending:
            if self.abort_check():
                return self._cancelled(stats)
            try:
                hash_hex = self._hash(abs_path, stats)
            except OSError as e:
                logger.warning("Cannot read %s: %s", abs_path, e)
                continue
            existing = known.get(rel_path)
            if (
                existing
                and existing["content_hash"] == hash_hex
                # Byte-identical AND fully analyzed: refresh the stat mirror
                # only. A 'hashed' row (a previous pass without face models)
                # must fall through to processing or it would never receive
                # faces.
                and existing["analysis_state"] == "analyzed"
            ):
                # Touched but byte-identical: refresh stat mirror only.
                self.store.upsert_file(
                    rel_path, hash_hex, stat.st_mtime, stat.st_size,
                    kind=existing["kind"], added_at=existing["added_at"],
                )
                stats["content_unchanged"] += 1
                continue
            if existing and existing["content_hash"]:
                # Edited in place: user state follows the item to its new
                # content, and the orphaned old media row is GC'd.
                self.store.migrate_media_state(existing["content_hash"], hash_hex)
            media = self.store.get_media(hash_hex)
            if media is not None and media["analysis_state"] == "analyzed":
                # Same bytes, new path: the expensive work already exists.
                self.store.upsert_file(
                    rel_path, hash_hex, stat.st_mtime, stat.st_size,
                    kind=media["kind"],
                    added_at=existing["added_at"] if existing else None,
                )
                stats["dedup_hits"] += 1
                self._progress(stats, rel_path)
                continue
            self._process(abs_path, rel_path, hash_hex, stat, stats,
                          added_at=existing["added_at"] if existing else None)
            self._progress(stats, rel_path)

        if self.abort_check():
            return self._cancelled(stats)

        stats["missing_now"] = self._mark_missing(seen_paths)
        self._link_motion_pairs(seen_paths)
        self.store.prune_orphan_media()
        stats["gc_removed"] = self.gc_caches()
        self.store.sync_fts()
        stats["elapsed"] = round(time.time() - started, 2)
        self.emit("scan_complete", **stats)
        logger.info(
            "Scan done: %d file(s), %d decoded, %d new face(s), %d dedup hit(s)",
            stats["files_total"], stats["decoded"], stats["faces"],
            stats["dedup_hits"],
        )
        return stats

    # -------------------------------------------------------------- phases

    def _discover(self) -> list:
        images = []
        scan_errors = []

        def on_error(err):
            scan_errors.append(err)

        for dirpath, dirnames, filenames in os.walk(self.root, onerror=on_error):
            # Case-insensitive: .FaceFrame must never be indexed on a
            # filesystem that treats it as our metadata folder.
            dirnames[:] = [d for d in dirnames if d.lower() != DATA_DIR_NAME.lower()]
            for name in filenames:
                if Path(name).suffix.lower() in VALID_EXTENSIONS:
                    images.append(os.path.join(dirpath, name))
        for err in scan_errors:
            logger.warning("Directory not readable during scan: %s", err)
        images.sort()
        return images

    def _hash(self, abs_path: str, stats: dict) -> str:
        from hashing import content_hash

        stats["hashed"] += 1
        self._progress(stats, os.path.basename(abs_path), hashing=True)
        return content_hash(abs_path)

    def _process(self, abs_path, rel_path, hash_hex, stat, stats,
                 added_at=None):
        suffix = Path(abs_path).suffix.lower()
        kind = "video" if suffix in VIDEO_EXTENSIONS else "photo"
        meta = {
            "content_hash": hash_hex,
            "kind": kind,
            "capture_time": stat.st_mtime,
            "tz_offset": None,
            "exif": None,
            # 'hashed' until faces complete; the dedup gate only skips
            # 'analyzed' media, so a pass without engines leaves the content
            # for the next engine-bearing pass to analyze fully.
            "analysis_state": "hashed",
            "analyzed_at": time.time(),
        }

        if kind == "photo":
            try:
                img = read_image_bgr(abs_path)
            except UndecodableImage as e:
                # Permanently undecodable (decompression bomb): index it
                # without a render instead of re-failing every scan forever.
                # The tile shows a broken image; the row exists so the user
                # can act on it. Same bytes always fail the same way.
                logger.warning("Undecodable photo, indexing without a render: %s (%s)", abs_path, e)
                meta["analysis_state"] = "analyzed"
                meta["flags"] = _dumps({"undecodable": True})
                self.store.upsert_media(meta)
                self.store.upsert_file(
                    rel_path, hash_hex, stat.st_mtime, stat.st_size, kind=kind,
                    added_at=added_at,
                )
                return
            if img is None:
                # Transient (vanished mid-pass, IO contention, a cloud
                # placeholder still materializing): retry on a later pass.
                logger.warning("Undecodable, left for a later pass: %s", abs_path)
                return
            height, width = img.shape[:2]
            meta["width"], meta["height"] = int(width), int(height)
            from hashing import perceptual_hash

            meta["phash"] = perceptual_hash(img)
            exif_info = exif_extract(abs_path)
            meta["capture_time"] = (
                exif_info["capture_time"] if exif_info["capture_time"] is not None
                else stat.st_mtime
            )
            meta["tz_offset"] = exif_info["tz_offset"]
            meta["exif"] = _dumps(exif_info["exif"]) if exif_info["exif"] else None
            flags = _photo_flags(rel_path, width, height)
            gps = exif_info["exif"].get("gps") if exif_info["exif"] else None
            if gps and len(gps) == 2:
                try:
                    from places import geohash

                    flags["geohash"] = geohash(gps[0], gps[1], 4)
                except Exception:
                    pass
            meta["flags"] = _dumps(flags)
            if self.labeler is not None:
                try:
                    meta["labels"] = _dumps(self.labeler.label(img))
                except Exception as e:
                    logger.warning("Labeling failed for %s: %s", abs_path, e)
                    meta["labels"] = _dumps([])
        else:
            video_meta = self._process_video(abs_path, hash_hex)
            if video_meta is None:
                logger.warning("Unreadable video, left for a later pass: %s", abs_path)
                return
            meta.update(video_meta)
            stats["videos"] += 1

        # Media row first (files reference it), then the file row: a crash
        # between the two simply reprocesses this file next pass.
        self.store.upsert_media(meta)
        self.store.upsert_file(
            rel_path, hash_hex, stat.st_mtime, stat.st_size, kind=kind,
            added_at=added_at,
        )
        stats["decoded"] += 1

        analyzed = kind == "video"
        if kind == "photo" and self.face_engine is not None:
            faces = self._safe_faces(abs_path, img, hash_hex)
            if faces is not None:
                for face in faces:
                    if face.get("thumbnail"):
                        face["thumbnail"] = pathio.to_relative(
                            face["thumbnail"], self.root
                        )
                self.store.add_faces(hash_hex, faces)
                stats["faces"] += len(faces)
                analyzed = True
        elif kind == "photo":
            # No engine this pass: faces stay empty and the media row stays
            # 'hashed', so a later engine-bearing pass reprocesses it.
            self.store.add_faces(hash_hex, [])

        if analyzed:
            self.store.upsert_media(
                {"content_hash": hash_hex, "analysis_state": "analyzed"}
            )

    def _safe_faces(self, abs_path, img, hash_hex):
        try:
            return self.face_engine.process_decoded(
                abs_path, img, face_key=hash_hex
            )
        except Exception:
            logger.exception("Face processing failed for %s", abs_path)
            return None

    def _process_video(self, abs_path: str, hash_hex: str):
        """Duration + dimensions + a poster frame; None when unreadable."""
        import cv2

        capture = cv2.VideoCapture(abs_path)
        try:
            if not capture.isOpened():
                logger.warning("Unreadable video, left for a later pass: %s", abs_path)
                return None
            fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
            frames = capture.get(cv2.CAP_PROP_FRAME_COUNT)
            width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
            duration = frames / fps if frames and frames > 0 else None
            poster_rel = self._save_poster(capture, hash_hex, frames, fps)
        finally:
            capture.release()
        return {
            "width": width,
            "height": height,
            "duration": duration,
            "poster_path": poster_rel,
        }

    def _save_poster(self, capture, hash_hex: str, frames, fps) -> str | None:
        import cv2
        import cv2 as _cv

        target = int((frames or 0) * 0.1)
        if target > 0:
            capture.set(_cv.CAP_PROP_POS_FRAMES, min(target, int(frames) - 1))
        ok, frame = capture.read()
        if not ok or frame is None:
            return None
        height, width = frame.shape[:2]
        max_dim = 640
        scale = max_dim / max(height, width)
        if scale < 1.0:
            frame = _cv.resize(
                frame,
                (max(1, round(width * scale)), max(1, round(height * scale))),
                interpolation=_cv.INTER_AREA,
            )
        posters_dir = self.data_dir / "posters"
        posters_dir.mkdir(parents=True, exist_ok=True)
        poster_path = posters_dir / f"{hash_hex}_p.jpg"
        ok, buf = _cv.imencode(".jpg", frame, [_cv.IMWRITE_JPEG_QUALITY, 82])
        if not ok:
            return None
        buf.tofile(str(poster_path))
        return pathio.to_relative(str(poster_path), self.root)

    def _mark_missing(self, seen_paths: set) -> int:
        return self.store.mark_missing(seen_paths)

    def _link_motion_pairs(self, seen_paths: set):
        """Motion photos: a video sharing a photo's basename IN THE SAME
        FOLDER (IMG_0123.jpg + IMG_0123.mp4). Pairing is a property of the
        PATH, so it lives on the files row (paired_path) and is recomputed
        deterministically every pass — same-content copies elsewhere never
        inherit or wipe a pairing, and a deleted sibling clears the survivor
        because the pairing is rebuilt from scratch."""
        groups: dict[tuple[str, str], dict[str, str]] = {}
        for rel_path in seen_paths:
            rel = Path(rel_path)
            suffix = rel.suffix.lower()
            key = (str(rel.parent).lower(), rel.stem.lower())
            entry = groups.setdefault(key, {"photo": "", "video": ""})
            if suffix in IMAGE_EXTENSIONS:
                entry["photo"] = entry["photo"] or rel_path
            elif suffix in VIDEO_EXTENSIONS:
                entry["video"] = entry["video"] or rel_path
        updates = []
        for entry in groups.values():
            if entry["photo"] and entry["video"]:
                updates.append((entry["video"], entry["photo"]))
                updates.append((entry["photo"], entry["video"]))
        self.store.set_motion_pairs(updates, seen_paths)

    def gc_caches(self) -> int:
        """Delete cache files the index no longer references. Idempotent."""
        removed = 0
        known = self.store.known_hashes()
        referenced_thumbs = self.store.referenced_face_thumbs()
        root_path = Path(self.root)

        thumbnails = self.data_dir / "thumbnails"
        if thumbnails.is_dir():
            referenced_abs = {
                str(root_path / rel) for rel in referenced_thumbs
            }
            for f in thumbnails.iterdir():
                if f.is_file() and str(f) not in referenced_abs:
                    _unlink(f)
                    removed += 1

        for subdir, suffix_pattern in (("posters", "_p.jpg"), ("previews", None)):
            directory = self.data_dir / subdir
            if not directory.is_dir():
                continue
            for f in directory.iterdir():
                if not f.is_file():
                    continue
                name = f.name
                if suffix_pattern and name.endswith(suffix_pattern):
                    keep = name[: -len(suffix_pattern)] in known
                elif suffix_pattern is None and _preview_kept(name, known):
                    keep = True
                else:
                    keep = False
                if not keep:
                    _unlink(f)
                    removed += 1
        if removed:
            logger.info("Cache GC removed %d file(s)", removed)
        return removed

    # ------------------------------------------------------------- helpers

    def _progress(self, stats: dict, filename: str, hashing: bool = False):
        self.emit(
            "scan_progress",
            processed=stats["skipped_unchanged"]
            + stats["content_unchanged"]
            + stats["dedup_hits"]
            + stats["decoded"],
            total=stats["files_total"],
            file=filename,
            hashing=hashing,
        )

    def _cancelled(self, stats: dict) -> dict:
        stats["cancelled"] = True
        stats["elapsed"] = None
        self.emit("scan_cancelled", **stats)
        return stats


def _preview_kept(name: str, known: set) -> bool:
    """New scheme: ``{sha256}_{dim}.jpg`` kept while content is indexed.
    Legacy v0.2 digests (16 hex chars) are regenerable and never kept."""
    if name.endswith(".jpg") and "_" in name:
        digest = name.rsplit("_", 1)[0]
        return len(digest) == 64 and digest in known
    return False


def _photo_flags(rel_path: str, width, height) -> dict:
    """Cheap type flags derivable at scan time (screenshots, panoramas)."""
    import re

    flags = {}
    stem = Path(rel_path).stem
    if re.search(r"screenshot|screen[_ ]?shot|screen[\s_-]?recording", stem, re.I):
        flags["screenshot"] = 1
    if width and height and max(width, height) / max(1, min(width, height)) >= 2.5:
        flags["panorama"] = 1
    return flags


def _dumps(value) -> str:
    import json

    return value if isinstance(value, str) else json.dumps(value)


def _unlink(path: Path):
    try:
        path.unlink()
    except OSError:
        pass


class UndecodableImage(Exception):
    """Content that can NEVER decode (e.g. past PIL's decompression-bomb
    guard): re-decoding it on a later pass is pointless."""


def read_image_bgr(image_path: str):
    """Decode an image honoring EXIF orientation, as BGR. None on transient
    failure; raises UndecodableImage when the content is permanently
    undecodable."""
    import cv2
    import numpy as np
    from PIL import Image, ImageOps

    try:
        with Image.open(image_path) as pil_img:
            pil_img = ImageOps.exif_transpose(pil_img)
            if pil_img.mode != "RGB":
                pil_img = pil_img.convert("RGB")
            rgb = np.asarray(pil_img)
    except FileNotFoundError:
        logger.warning("File vanished during scan: %s", image_path)
        return None
    except Image.DecompressionBombError as e:
        raise UndecodableImage(str(e)) from e
    except Exception as e:
        logger.warning("Could not decode %s: %s", image_path, e)
        return None
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


# Backwards-compatible alias for modules importing exif extraction from here.
def exif_extract(path: str) -> dict:
    import exif as _exif

    return _exif.extract(path)
