"""Slice 3 — library state: favorites, archive, trash + purge, locked folder,
captions, date overrides, albums, storage stats."""

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import write_jpeg


@pytest.fixture
def lib_with_items(tmp_path):
    """A scanned library (no faces/labels) with stable hash handles."""
    from scan import ScanPipeline

    root = tmp_path / "library"
    for i in range(4):
        write_jpeg(root / f"p{i}.jpg", color=(10 * i + 40, 80, 160))
    pipeline = ScanPipeline(
        str(root / ".faceframe" / "index.db"), str(root)
    )
    pipeline.run()
    with pipeline.store.connect() as conn:
        pairs = conn.execute(
            "SELECT f.path, f.content_hash FROM files f ORDER BY f.path"
        ).fetchall()
    return root, pipeline.store, {r["path"]: r["content_hash"] for r in pairs}


@pytest.fixture
def svc(lib_with_items):
    from library import LibraryService

    root, store, hashes = lib_with_items
    return LibraryService(store, str(root)), root, hashes


# ---------------------------------------------------------------------------
# favorites / archive / caption / date override (media-scoped user state)
# ---------------------------------------------------------------------------

def test_favorite_and_caption_survive_rescan(lib_with_items):
    from scan import ScanPipeline

    root, store, hashes = lib_with_items
    from library import LibraryService

    svc = LibraryService(store, str(root))
    svc.set_favorite([hashes["p0.jpg"]], True)
    svc.set_caption(hashes["p0.jpg"], "sunset at the lake")

    ScanPipeline(str(root / ".faceframe" / "index.db"), str(root)).run()

    media = store.get_media(hashes["p0.jpg"])
    assert media["favorite"] == 1
    assert media["caption"] == "sunset at the lake"


def test_date_override_and_effective_capture_time(svc):
    library, root, hashes = svc
    library.set_date_override(hashes["p1.jpg"], 1600000000.0)
    assert library.effective_capture_time(library.get_media(hashes["p1.jpg"])) == 1600000000.0
    library.set_date_override(hashes["p1.jpg"], None)
    media = library.get_media(hashes["p1.jpg"])
    assert library.effective_capture_time(media) == pytest.approx(media["capture_time"])


# ---------------------------------------------------------------------------
# trash lifecycle
# ---------------------------------------------------------------------------

def test_trash_restore_and_visibility(svc):
    library, root, hashes = svc
    library.set_trashed([hashes["p0.jpg"]], True)
    visible = {r["path"] for r in library.visible_items()}
    assert "p0.jpg" not in visible and "p1.jpg" in visible
    trashed = {r["path"] for r in library.trashed_items()}
    assert trashed == {"p0.jpg"}

    library.set_trashed([hashes["p0.jpg"]], False)
    assert {r["path"] for r in library.visible_items()} >= {"p0.jpg"}


def test_purge_sends_expired_files_to_os_trash(svc, monkeypatch):
    library, root, hashes = svc
    sent = []
    monkeypatch.setattr(
        "library.send2trash", lambda p: sent.append(p)
    )
    library.set_trashed([hashes["p0.jpg"]], True)
    with library.store.connect() as conn:
        conn.execute(
            "UPDATE files SET trashed_at=? WHERE path='p0.jpg'",
            (time.time() - 61 * 86400,),
        )
    removed = library.purge_expired_trash(max_age_days=60)
    assert removed == 1
    assert len(sent) == 1 and sent[0].endswith("p0.jpg")
    with library.store.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM files WHERE path='p0.jpg'"
        ).fetchone()[0] == 0
    assert (root / "p0.jpg").exists()  # OS trash, never a hard delete


def test_delete_from_disk_uses_os_trash(svc, monkeypatch):
    library, root, hashes = svc
    sent = []
    monkeypatch.setattr("library.send2trash", lambda p: sent.append(p))
    # Security contract: only trashed, indexed files may be deleted from disk.
    library.set_trashed(paths=["p2.jpg"], trashed=True)
    library.delete_from_disk(["p2.jpg"])
    assert sent and sent[0].endswith("p2.jpg")
    assert (root / "p2.jpg").exists()
    with library.store.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM files WHERE path='p2.jpg'"
        ).fetchone()[0] == 0


def test_delete_from_disk_refuses_untrashed_paths(svc, monkeypatch):
    library, root, hashes = svc
    sent = []
    monkeypatch.setattr("library.send2trash", lambda p: sent.append(p))
    library.delete_from_disk(["p3.jpg"])  # indexed but not trashed
    assert sent == []
    with pytest.raises(ValueError):
        library.delete_from_disk(["../../outside.jpg"])  # path escape refused
    with pytest.raises(ValueError):
        library.delete_from_disk([str(root.parent / "elsewhere.jpg")])  # absolute outside


# ---------------------------------------------------------------------------
# locked folder
# ---------------------------------------------------------------------------

def test_locked_folder_gate(svc):
    library, root, hashes = svc
    library.set_locked_passcode("1234")
    assert library.verify_locked_passcode("1234") is True
    assert library.verify_locked_passcode("9999") is False

    library.set_locked([hashes["p1.jpg"]], True)
    assert "p1.jpg" not in {r["path"] for r in library.visible_items()}
    locked = {r["path"] for r in library.locked_items()}
    assert locked == {"p1.jpg"}

    library.remove_locked_passcode("1234")
    assert library.verify_locked_passcode("1234") is False
    with pytest.raises(ValueError):
        library.set_locked_passcode("   ")  # empty/whitespace passcode refused


def test_locked_requires_passcode_set(svc):
    library, root, hashes = svc
    with pytest.raises(ValueError):
        library.set_locked([hashes["p0.jpg"]], True)


# ---------------------------------------------------------------------------
# albums
# ---------------------------------------------------------------------------

def test_album_lifecycle_and_ordering(svc):
    library, root, hashes = svc
    album_id = library.create_album("Trip", description="Summer")
    library.add_album_items(album_id, [hashes["p0.jpg"], hashes["p1.jpg"]])
    library.add_album_items(album_id, [hashes["p2.jpg"]])

    albums = library.list_albums()
    assert albums[0]["name"] == "Trip" and albums[0]["count"] == 3
    assert albums[0]["description"] == "Summer"

    items = library.album_items(album_id)
    assert [i["content_hash"] for i in items] == [
        hashes["p0.jpg"], hashes["p1.jpg"], hashes["p2.jpg"],
    ]

    # Reorder is authoritative: positions rewritten 0..n-1
    library.reorder_album(
        album_id, [hashes["p2.jpg"], hashes["p0.jpg"], hashes["p1.jpg"]]
    )
    items = library.album_items(album_id)
    assert [i["content_hash"] for i in items] == [
        hashes["p2.jpg"], hashes["p0.jpg"], hashes["p1.jpg"],
    ]
    assert [i["position"] for i in items] == [0, 1, 2]

    # Removal keeps the media (never deletes content)
    library.remove_album_items(album_id, [hashes["p0.jpg"]])
    assert library.store.media_count() == 4
    assert len(library.album_items(album_id)) == 2

    # Cover + sort + rename + delete
    library.set_album_cover(album_id, hashes["p2.jpg"])
    library.set_album_sort(album_id, "captured")
    library.rename_album(album_id, "Trip 2026")
    albums = library.list_albums()
    assert albums[0]["name"] == "Trip 2026"
    assert albums[0]["cover_hash"] == hashes["p2.jpg"]
    assert albums[0]["sort_key"] == "captured"
    library.delete_album(album_id)
    assert library.list_albums() == []
    assert library.store.media_count() == 4


def test_album_items_survive_trash_of_member(svc):
    """Trashing an item hides it from the album view but the membership row
    stays: restoring the item brings it back."""
    library, root, hashes = svc
    album_id = library.create_album("Keep")
    library.add_album_items(album_id, [hashes["p3.jpg"]])
    library.set_trashed([hashes["p3.jpg"]], True)
    assert library.album_items(album_id, include_trashed=False) == []
    assert len(library.album_items(album_id, include_trashed=True)) == 1


# ---------------------------------------------------------------------------
# storage stats
# ---------------------------------------------------------------------------

def test_storage_stats(svc):
    library, root, hashes = svc
    stats = library.storage_stats()
    assert stats["items"]["count"] == 4
    assert stats["items"]["bytes"] > 0
    assert stats["index_bytes"] > 0
    assert set(stats["caches"]) >= {"thumbnails", "previews", "posters"}
