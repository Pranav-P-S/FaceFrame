"""Slice 4 — people: clustering over content-keyed faces, split/merge/
assign/hide, person views."""

import json
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import write_jpeg


def _emb(center, jitter=0.02, seed=0):
    rng = np.random.default_rng(seed)
    v = np.asarray(center, dtype=np.float32) + rng.normal(0, jitter, len(center))
    return (v / max(np.linalg.norm(v), 1e-6)).tolist()


@pytest.fixture
def people_lib(tmp_path):
    """Library with 3 tight clusters of faces + 2 noise faces."""
    from scan import ScanPipeline

    root = tmp_path / "library"
    clusters = {
        "a": ([1, 0, 0, 0], 3),   # 3 files, 1 face each
        "b": ([0, 1, 0, 0], 3),
        "c": ([0, 0, 1, 0], 2),
    }
    idx = 0
    embeddings = {}
    for name, (center, n) in clusters.items():
        for i in range(n):
            path = write_jpeg(root / f"{name}{i}.jpg", color=(40 + 20 * idx, 90, 150))
            embeddings[f"{name}{i}.jpg"] = _emb(center, seed=idx)
            idx += 1
    write_jpeg(root / "noise0.jpg", color=(200, 10, 10))
    write_jpeg(root / "noise1.jpg", color=(10, 200, 10))
    # Orthogonal to all three cluster centers and to each other (cos = -1),
    # so both sit beyond DBSCAN's eps from every cluster.
    embeddings["noise0.jpg"] = _emb([0, 0, 0, 1], jitter=0.0)
    embeddings["noise1.jpg"] = _emb([0, 0, 0, -1], jitter=0.0)

    pipeline = ScanPipeline(str(root / ".faceframe" / "index.db"), str(root))

    # Inject a face engine that returns the precomputed embedding per file.
    class E:
        def process_decoded(self, path, img, face_key=None):
            from pathlib import Path as P

            return [
                {
                    "embedding": embeddings[P(path).name],
                    "bbox": [1, 1, 30, 30],
                    "det_score": 0.9,
                    "thumbnail": None,
                }
            ]

    pipeline.face_engine = E()
    pipeline.run()

    from people import PeopleService

    people = PeopleService(pipeline.store)
    return root, pipeline.store, people, embeddings


def test_clustering_finds_three_people(people_lib):
    root, store, people, _ = people_lib
    stats = people.cluster()
    assert stats["people"] == 3
    assert stats["unclustered"] == 2
    persons = people.list_persons()
    assert len(persons) == 3
    assert all(p["face_count"] >= 2 for p in persons)


def test_renamed_person_survives_reclustering(people_lib):
    root, store, people, _ = people_lib
    people.cluster()
    persons = people.list_persons()
    target = persons[0]
    people.rename_person(target["id"], "Alice")
    people.cluster()
    names = {p["name"] for p in people.list_persons()}
    assert "Alice" in names
    # Alice still exists and still has her faces
    alice = [p for p in people.list_persons() if p["name"] == "Alice"][0]
    assert alice["face_count"] >= 2


def test_person_photos_exclude_trashed_and_missing(people_lib):
    root, store, people, _ = people_lib
    people.cluster()
    person = people.list_persons()[0]
    face_path = people.person_photos(person["id"])[0]["path"]
    from library import LibraryService

    lib = LibraryService(store, str(root))
    lib.set_trashed(paths=[face_path], trashed=True, content_hashes=None)
    photos = people.person_photos(person["id"])
    assert face_path not in [p["path"] for p in photos]


def test_split_person(people_lib):
    root, store, people, _ = people_lib
    people.cluster()
    person = people.list_persons()[0]
    faces = people.person_faces(person["id"])
    assert len(faces) >= 2
    new_id = people.split_person(person["id"], [faces[0]["id"]], "Bob")
    assert new_id != person["id"]
    assert people.get_person(new_id)["name"] == "Bob"
    assert len(people.person_faces(new_id)) == 1
    assert len(people.person_faces(person["id"])) == len(faces) - 1


def test_merge_persons(people_lib):
    root, store, people, _ = people_lib
    people.cluster()
    first, second = people.list_persons()[:2]
    total = first["face_count"] + second["face_count"]
    people.merge_persons(first["id"], second["id"])
    remaining = {p["id"] for p in people.list_persons()}
    assert second["id"] not in remaining
    assert people.get_person(first["id"])["face_count"] == total


def test_assign_and_unassign_faces(people_lib):
    root, store, people, _ = people_lib
    people.cluster()
    stray = people.unclustered_faces()
    assert len(stray) == 2
    target = people.list_persons()[0]
    people.assign_faces([stray[0]["id"]], target["id"])
    assert len(people.unclustered_faces()) == 1
    assert len(people.person_faces(target["id"])) >= 3
    people.assign_faces([stray[0]["id"]], None)
    assert len(people.unclustered_faces()) == 2


def test_hide_person(people_lib):
    root, store, people, _ = people_lib
    people.cluster()
    person = people.list_persons()[0]
    people.set_person_hidden(person["id"], True)
    assert person["id"] not in {p["id"] for p in people.list_persons()}
    assert person["id"] in {
        p["id"] for p in people.list_persons(include_hidden=True)
    }
    people.set_person_hidden(person["id"], False)
    assert person["id"] in {p["id"] for p in people.list_persons()}


def test_feature_photo_choice(people_lib):
    root, store, people, _ = people_lib
    people.cluster()
    person = people.list_persons()[0]
    faces = people.person_faces(person["id"])
    faces[0]["thumbnail_path"] = ".faceframe/thumbnails/x.jpg"
    people.set_person_thumbnail(person["id"], faces[0]["thumbnail_path"])
    assert people.get_person(person["id"])["thumbnail_path"] == faces[0]["thumbnail_path"]
