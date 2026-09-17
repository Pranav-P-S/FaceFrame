"""People: face clustering and person management on the content-keyed index.

The clustering algorithm carries over from v0.2 (it is correct and proven):
full DBSCAN per run, then clusters matched back to existing persons by shared
face count so renamed people keep their identities. Faces are keyed by
content hash now, so a re-cluster only ever needs the stored embeddings —
no re-inference, and assignments survive file moves automatically.

DBSCAN/sklearn is imported at module scope on purpose: the backend imports
this module on its main thread at startup, and loading scipy's extension
DLLs from a worker thread deadlocks on Windows (see main._warm_heavy_imports).
"""

import json
import logging
import re
import time

import numpy as np
from sklearn.cluster import DBSCAN

logger = logging.getLogger("FaceFrame.People")

EPS = 0.55
MIN_SAMPLES = 2
AUTO_NAME_RE = re.compile(r"^Person \d+$")


class PeopleService:
    def __init__(self, store):
        self.store = store

    # ------------------------------------------------------------ clustering

    def cluster(self, eps: float = EPS, min_samples: int = MIN_SAMPLES) -> dict:
        rows = self.store.all_faces_for_clustering()
        face_ids, valid_rows, embeddings, previous = [], [], [], {}
        for row in rows:
            emb = _parse_embedding(row["embedding"])
            if emb is None:
                logger.warning("Skipping face %s: unreadable embedding", row["id"])
                continue
            face_ids.append(row["id"])
            valid_rows.append(row)
            embeddings.append(emb)
            previous[row["id"]] = row["person_id"]

        total = len(face_ids)
        if total < 2:
            return {"people": 0, "new_people": 0, "assigned": 0, "unclustered": total}

        matrix = np.asarray(embeddings, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        matrix /= norms

        labels = DBSCAN(eps=eps, min_samples=min_samples, metric="cosine").fit_predict(matrix)

        clusters: dict[int, list[int]] = {}
        for idx, label in enumerate(labels):
            if label != -1:
                clusters.setdefault(int(label), []).append(idx)

        by_size = sorted(clusters.items(), key=lambda kv: -len(kv[1]))
        custom = self._custom_named_persons()
        claimed: set[int] = set()
        label_to_person: dict[int, int] = {}

        # Renamed people always keep their cluster.
        for label, members in by_size:
            best = _best_overlap(members, previous, face_ids, custom, claimed)
            if best is not None:
                label_to_person[label] = best
                claimed.add(best)

        # Other clusters reuse unclaimed auto persons, else get a new one.
        new_people = 0
        for label, members in by_size:
            if label in label_to_person:
                continue
            best = _best_overlap(members, previous, face_ids, None, claimed)
            if best is not None:
                label_to_person[label] = best
                claimed.add(best)
            else:
                label_to_person[label] = self._create_auto_person()
                new_people += 1

        assigned = 0
        for label, members in clusters.items():
            ids = [face_ids[i] for i in members]
            self.store.set_faces_person(ids, label_to_person[label])
            assigned += len(ids)
            self._refresh_thumbnail(label_to_person[label], ids)

        noise = [face_ids[i] for i, label in enumerate(labels) if label == -1]
        if noise:
            self.store.set_faces_person(noise, None)

        self.store.delete_empty_persons()
        people_count = len(label_to_person)
        logger.info(
            "Clustering: %d people (%d new), %d assigned, %d unclustered",
            people_count, new_people, assigned, len(noise),
        )
        return {
            "people": people_count,
            "new_people": new_people,
            "assigned": assigned,
            "unclustered": len(noise),
        }

    def _custom_named_persons(self) -> set:
        return {
            pid
            for pid, name in self.store.person_names()
            if name and not AUTO_NAME_RE.match(name)
        }

    def _create_auto_person(self) -> int:
        return self.store.create_person(self._next_auto_name())

    def _next_auto_name(self) -> str:
        return self.store.next_auto_person_name()

    def _refresh_thumbnail(self, person_id: int, face_ids: list):
        best_path, best_area = None, -1
        for row in self.store.faces_by_ids(face_ids):
            try:
                x1, y1, x2, y2 = json.loads(row["bbox"])
            except (TypeError, ValueError):
                continue
            area = max(0, x2 - x1) * max(0, y2 - y1)
            if row["thumbnail_path"] and area > best_area:
                best_area, best_path = area, row["thumbnail_path"]
        if best_path:
            self.store.update_person_thumbnail(person_id, best_path)

    # ---------------------------------------------------------------- queries

    def list_persons(self, include_hidden: bool = False) -> list:
        return self.store.persons_with_counts(include_hidden)

    def get_person(self, person_id: int):
        return self.store.get_person(person_id)

    def person_photos(self, person_id: int) -> list:
        return self.store.person_photos(person_id)

    def person_faces(self, person_id: int) -> list:
        return self.store.person_faces(person_id)

    def unclustered_faces(self) -> list:
        return self.store.unclustered_faces(limit=500)

    # ------------------------------------------------------------- management

    def rename_person(self, person_id: int, name: str):
        self.store.rename_person(person_id, name)

    def merge_persons(self, keep_id: int, merge_id: int):
        """All faces of merge_id move under keep_id; merge_id is removed.
        The losing thumbnail file is cleaned up by the caller via the
        returned relative path (regenerable data, unlink directly)."""
        stale = self.store.merge_persons(keep_id, merge_id)
        return stale

    def split_person(self, person_id: int, face_ids: list, new_name: str | None = None) -> int:
        if not face_ids:
            raise ValueError("Select faces to split off")
        new_id = self.store.create_person(new_name or self._next_auto_name())
        self.store.set_faces_person(face_ids, new_id)
        self._refresh_thumbnail(new_id, face_ids)
        self._refresh_thumbnail(person_id, self.store.person_face_ids(person_id))
        self.store.delete_empty_persons()
        return new_id

    def assign_faces(self, face_ids: list, person_id: int | None):
        if person_id is not None and not self.store.get_person(person_id):
            raise ValueError("No such person")
        self.store.set_faces_person(face_ids, person_id)

    def set_person_hidden(self, person_id: int, hidden: bool):
        self.store.set_person_hidden(person_id, hidden)

    def set_person_thumbnail(self, person_id: int, thumbnail_path: str):
        self.store.update_person_thumbnail(person_id, thumbnail_path)


def _best_overlap(members, previous, face_ids, allowed, claimed):
    overlap: dict[int, int] = {}
    for idx in members:
        pid = previous[face_ids[idx]]
        if pid is None or pid in claimed:
            continue
        if allowed is not None and pid not in allowed:
            continue
        overlap[pid] = overlap.get(pid, 0) + 1
    return max(overlap, key=overlap.get) if overlap else None


def _parse_embedding(raw):
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    try:
        values = json.loads(raw)
        arr = np.asarray(values, dtype=np.float32)
        return arr if arr.size else None
    except (ValueError, TypeError):
        return None
