"""Slice 5 — search-views: query parser, feed grouping, places, memories."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import write_jpeg


# ---------------------------------------------------------------------------
# query parser
# ---------------------------------------------------------------------------

def test_parser_plain_text():
    from query import parse

    q = parse("beach sunset")
    assert q.text == ["beach", "sunset"]
    assert q.filters == {}


def test_parser_keyed_filters():
    from query import parse

    q = parse("dog person:Alice type:video is:favorite folder:trip before:2024-01-01 after:2023-01-01")
    assert q.text == ["dog"]
    assert q.filters == {
        "person": ["Alice"],
        "type": ["video"],
        "is": ["favorite"],
        "folder": ["trip"],
        "before": ["2024-01-01"],
        "after": ["2023-01-01"],
    }


def test_parser_repeated_keys_and_quotes():
    from query import parse

    q = parse('is:favorite is:archived place:"new york"')
    assert q.filters["is"] == ["favorite", "archived"]
    assert q.filters["place"] == ["new york"]


def test_parser_date_helpers():
    from query import parse

    q = parse("year:2023")
    assert q.filters["after"] == ["2023-01-01"]
    assert q.filters["before"] == ["2024-01-01"]


# ---------------------------------------------------------------------------
# feed grouping
# ---------------------------------------------------------------------------

@pytest.fixture
def dated_lib(tmp_path):
    """Photos on distinct days, one favorite, one archived, one trashed,
    one locked, one motion pair, one duplicate copy."""
    from scan import ScanPipeline
    from library import LibraryService

    root = tmp_path / "library"
    # 2024-03-10 12:00 UTC-epoch etc.
    days = ["2024-03-10", "2024-03-10", "2024-03-11", "2024-04-01", "2023-07-15"]
    hashes = {}
    for i, day in enumerate(days):
        path = write_jpeg(root / f"p{i}.jpg", color=(15 * i + 30, 60, 180))
        import os

        ts = _epoch(day)
        os.utime(path, (ts, ts))
    pipeline = ScanPipeline(str(root / ".faceframe" / "index.db"), str(root))
    pipeline.run()
    store = pipeline.store
    with store.connect() as conn:
        rows = conn.execute("SELECT path, content_hash FROM files ORDER BY path").fetchall()
    hashes = {r["path"]: r["content_hash"] for r in rows}

    lib = LibraryService(store, str(root))
    lib.set_favorite([hashes["p0.jpg"]], True)
    lib.set_archived([hashes["p3.jpg"]], True)
    lib.set_trashed(paths=["p4.jpg"], trashed=True)
    return root, store, lib, hashes


def _epoch(day: str) -> float:
    import calendar
    import time

    return calendar.timegm(time.strptime(day, "%Y-%m-%d"))


def test_feed_groups_by_day_excludes_hidden(dated_lib):
    root, store, lib, hashes = dated_lib
    from views import feed_groups

    groups = feed_groups(store, view="days")
    # Trashed p4 excluded; archived p3 still in the feed (GP keeps archived in
    # the feed? No: GP hides archived from the main feed).
    paths = [i["path"] for g in groups for i in g["items"]]
    assert "p4.jpg" not in paths and "p3.jpg" not in paths
    assert set(paths) == {"p0.jpg", "p1.jpg", "p2.jpg"}
    assert groups[0]["items"][0]["path"] in {"p0.jpg", "p1.jpg", "p2.jpg"}
    # Day keys are ISO dates derived from naive wall time
    assert any(g["key"].startswith("2024-03-10") for g in groups)
    assert any(g["key"].startswith("2024-03-11") for g in groups)


def test_feed_favorite_filter(dated_lib):
    root, store, lib, hashes = dated_lib
    from views import feed_groups

    groups = feed_groups(store, view="days", favorite=True)
    paths = [i["path"] for g in groups for i in g["items"]]
    assert paths == ["p0.jpg"]


def test_feed_include_archived_flag(dated_lib):
    root, store, lib, hashes = dated_lib
    from views import feed_groups

    groups = feed_groups(store, view="days", include_archived=True)
    paths = [i["path"] for g in groups for i in g["items"]]
    assert "p3.jpg" in paths


def test_months_and_years_views(dated_lib):
    root, store, lib, hashes = dated_lib
    from views import feed_groups

    months = feed_groups(store, view="months")
    keys = [g["key"] for g in months]
    assert keys == ["2024-03"]  # April is archived: hidden from the feed
    years = feed_groups(store, view="years")
    assert [g["key"] for g in years] == ["2024"]  # 2023 fully trashed


def test_search_executes_query(dated_lib):
    root, store, lib, hashes = dated_lib
    from library import LibraryService

    lib.set_caption(hashes["p0.jpg"], "golden retriever on the beach")
    from views import search_items

    result = search_items(store, "beach")
    assert [i["path"] for i in result["items"]] == ["p0.jpg"]

    result = search_items(store, "type:photo is:favorite")
    assert [i["path"] for i in result["items"]] == ["p0.jpg"]

    result = search_items(store, "year:2024 after:2024-03-11")
    # Archived items leave the grid but remain searchable (GP behavior).
    assert {i["path"] for i in result["items"]} == {"p2.jpg", "p3.jpg"}


def test_search_by_person(dated_lib):
    root, store, lib, hashes = dated_lib
    with store.connect() as conn:
        cur = conn.execute(
            "INSERT INTO persons (name, created_at) VALUES ('Alice', 0)"
        )
        pid = cur.lastrowid
        conn.execute(
            """INSERT INTO faces (content_hash, face_index, bbox, embedding, det_score, person_id)
               VALUES (?, 0, '[1,1,2,2]', '[]', 0.9, ?)""",
            (hashes["p1.jpg"], pid),
        )
    from views import search_items

    result = search_items(store, "person:Alice")
    assert [i["path"] for i in result["items"]] == ["p1.jpg"]


def test_item_detail_shape(dated_lib):
    root, store, lib, hashes = dated_lib
    from views import item_detail

    detail = item_detail(store, "p0.jpg")
    assert detail["path"] == "p0.jpg"
    assert detail["media"]["favorite"] == 1
    assert detail["media"]["content_hash"] == hashes["p0.jpg"]
    assert "exif" in detail["media"]


def test_search_by_label_filter(dated_lib):
    root, store, lib, hashes = dated_lib
    # Give two items labels via the store (as the labeler would).
    for path in ("p0.jpg", "p2.jpg"):
        with store.connect() as conn:
            h = conn.execute(
                "SELECT content_hash FROM files WHERE path=?", (path,)
            ).fetchone()[0]
        store.upsert_media({"content_hash": h, "labels": '["golden retriever"]'})
    store.sync_fts()  # production syncs FTS at scan end
    from views import search_items

    result = search_items(store, "label:retriever")
    assert {i["path"] for i in result["items"]} == {"p0.jpg", "p2.jpg"}
    # Free text still finds labels too.
    result = search_items(store, "retriever")
    assert len(result["items"]) == 2


# ---------------------------------------------------------------------------
# category query expansion ("dog" finds "golden retriever")
# ---------------------------------------------------------------------------

def _fake_class_names():
    """A minimal stand-in for imagenet_classes.txt: distinct breed names on
    the fixed dog indices 151-268, five cat names on 281-285, filler elsewhere."""
    classes = ["unrelated"] * 300
    for i in range(151, 269):
        classes[i] = f"breed {i}"
    classes[151] = "golden retriever"
    classes[152] = "toy poodle"
    for i, name in enumerate(
        ("Persian cat", "Siamese cat", "tabby", "tiger cat", "Egyptian cat")
    ):
        classes[281 + i] = name
    return classes


def test_expand_search_terms_maps_category_words():
    from labels import expand_search_terms

    classes = _fake_class_names()
    dogs = expand_search_terms(["dog"], class_names=classes)[0]
    assert dogs[0] == "dog"
    assert {"golden retriever", "toy poodle"} <= set(dogs)
    assert "Egyptian cat" not in dogs
    # Plurals and diminutives hit the same category.
    puppies = expand_search_terms(["puppies"], class_names=classes)[0]
    assert {"golden retriever", "toy poodle"} <= set(puppies)
    # Case-insensitive, and cats never leak into the dog group.
    cats = expand_search_terms(["Cat"], class_names=classes)[0]
    assert cats[0] == "Cat"
    assert {"Egyptian cat", "Persian cat"} <= set(cats)
    assert "golden retriever" not in cats
    # Unknown words pass through untouched; an explicitly empty class list
    # means no expansion (the default None reads the on-disk classes file).
    assert expand_search_terms(["beach"], class_names=classes) == [["beach"]]
    assert expand_search_terms(["dog"], class_names=[]) == [["dog"]]


def test_search_expands_dog_to_breed_labels(dated_lib, monkeypatch):
    root, store, lib, hashes = dated_lib
    import labels as labels_mod
    from views import search_items

    monkeypatch.setattr(
        labels_mod, "load_class_names", lambda: _fake_class_names()
    )

    def hash_of(path):
        with store.connect() as conn:
            return conn.execute(
                "SELECT content_hash FROM files WHERE path=?", (path,)
            ).fetchone()[0]

    store.upsert_media(
        {"content_hash": hash_of("p0.jpg"), "labels": '["golden retriever"]'}
    )
    store.upsert_media(
        {"content_hash": hash_of("p2.jpg"), "labels": '["Egyptian cat"]'}
    )
    store.sync_fts()

    # The category word reaches breed-labeled photos; the other species stays out.
    dogs = search_items(store, "dog")
    assert {i["path"] for i in dogs["items"]} == {"p0.jpg"}
    cats = search_items(store, "cats")
    assert {i["path"] for i in cats["items"]} == {"p2.jpg"}
    # The breed name itself still works as a plain token.
    assert {i["path"] for i in search_items(store, "retriever")["items"]} == {"p0.jpg"}
