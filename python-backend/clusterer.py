import json
import logging
import re

import numpy as np
from sklearn.cluster import DBSCAN

from database import Database

logger = logging.getLogger("FaceFrame.Clusterer")

# DBSCAN with the cosine metric: two faces end up in the same cluster when
# cosine similarity >= 1 - EPS. InsightFace embeddings for the same person
# typically land around 0.5-0.8 similarity, different people below ~0.4.
EPS = 0.55
MIN_SAMPLES = 2

AUTO_NAME_RE = re.compile(r"^Person \d+$")


class Clusterer:
    """Groups faces into people.

    Re-clustering runs over every face each time, which keeps results
    consistent as the library grows. Person rows stay stable across runs:
    clusters are matched back to existing people by how many faces they
    share, so renamed people never lose their names.
    """

    def __init__(self, db_path: str):
        self.db = Database(db_path)

    def run_clustering(self, eps: float = EPS, min_samples: int = MIN_SAMPLES):
        rows = self.db.all_faces()
        face_ids, valid_rows, embeddings, previous_persons = [], [], [], {}
        for row in rows:
            emb = _parse_embedding(row["embedding"])
            if emb is None:
                logger.warning("Skipping face %s: unreadable embedding", row["id"])
                continue
            face_ids.append(row["id"])
            valid_rows.append(row)
            embeddings.append(emb)
            previous_persons[row["id"]] = row["person_id"]

        total_faces = len(face_ids)
        if total_faces < 2:
            logger.info("Not enough faces to cluster (have %d)", total_faces)
            return {"people": 0, "assigned": 0, "unclustered": total_faces}

        matrix = np.asarray(embeddings, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        matrix /= norms

        labels = DBSCAN(
            eps=eps, min_samples=min_samples, metric="cosine"
        ).fit_predict(matrix)

        clusters = {}
        for idx, label in enumerate(labels):
            if label != -1:
                clusters.setdefault(int(label), []).append(idx)

        custom_names = self._custom_named_persons()
        claimed_persons = set()
        label_to_person = {}

        # Renamed people always keep their cluster, even if it shrank or
        # merged with an auto-generated one.
        for label, member_idx in sorted(
            clusters.items(), key=lambda kv: -len(kv[1])
        ):
            overlap = {}
            for idx in member_idx:
                pid = previous_persons[face_ids[idx]]
                if pid is not None and pid in custom_names:
                    overlap[pid] = overlap.get(pid, 0) + 1
            best = max(overlap, key=overlap.get) if overlap else None
            if best is not None:
                label_to_person[label] = best
                claimed_persons.add(best)

        # Remaining clusters reuse unclaimed auto-generated persons when
        # they overlap (stable ids), otherwise become a new person.
        new_people = 0
        for label, member_idx in sorted(
            clusters.items(), key=lambda kv: -len(kv[1])
        ):
            if label in label_to_person:
                continue
            overlap = {}
            for idx in member_idx:
                pid = previous_persons[face_ids[idx]]
                if pid is not None and pid not in claimed_persons:
                    overlap[pid] = overlap.get(pid, 0) + 1
            best = max(overlap, key=overlap.get) if overlap else None
            if best is not None:
                label_to_person[label] = best
                claimed_persons.add(best)
            else:
                person_id = self.db.create_person(
                    self.db.next_auto_person_name()
                )
                label_to_person[label] = person_id
                claimed_persons.add(person_id)
                new_people += 1

        assigned = 0
        unclustered = 0
        for label, member_idx in clusters.items():
            face_rows = [(face_ids[i], valid_rows[i]) for i in member_idx]
            self.db.set_faces_person(
                [fid for fid, _ in face_rows], label_to_person[label]
            )
            assigned += len(face_rows)
            self._refresh_thumbnail(label_to_person[label], face_rows)

        noise = [face_ids[i] for i, label in enumerate(labels) if label == -1]
        if noise:
            self.db.set_faces_unassigned(noise)
            unclustered = len(noise)

        removed = self.db.delete_empty_persons()
        if removed:
            logger.info("Removed %d empty person(s)", removed)

        people_count = len(label_to_person)
        logger.info(
            "Clustering done: %d people (%d new), %d assigned, %d unclustered",
            people_count,
            new_people,
            assigned,
            unclustered,
        )
        return {
            "people": people_count,
            "new_people": new_people,
            "assigned": assigned,
            "unclustered": unclustered,
        }

    def _custom_named_persons(self):
        return {
            pid
            for pid, name in self.db.person_names()
            if name and not AUTO_NAME_RE.match(name)
        }

    def _refresh_thumbnail(self, person_id, face_rows):
        """Pick the largest face crop as the person's portrait."""
        best_path = None
        best_area = -1
        for _, row in face_rows:
            try:
                x1, y1, x2, y2 = json.loads(row["bbox"])
            except (TypeError, ValueError):
                continue
            area = max(0, x2 - x1) * max(0, y2 - y1)
            if row["thumbnail_path"] and area > best_area:
                best_area = area
                best_path = row["thumbnail_path"]
        if best_path:
            self.db.update_person_thumbnail(person_id, best_path)


def _parse_embedding(raw):
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    try:
        values = json.loads(raw)
        arr = np.asarray(values, dtype=np.float32)
        return arr if arr.size else None
    except (ValueError, TypeError):
        return None
