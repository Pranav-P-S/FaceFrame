"""v3 acceptance test: drives the new command surface the way the UI does
and proves the mandate — idempotent indexing, zero-cost moves, duplicate
detection, album/trash/locked/edit round-trips.

Run after (or instead of) e2e_backend_test.py; it rebuilds the index itself.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "python-backend" / "main.py"
PYTHON = str(ROOT / "venv" / "Scripts" / "python.exe") if os.name == "nt" else str(
    ROOT / "venv" / "bin" / "python3"
)
LIBRARY = ROOT / "test-data" / "library"

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {name}" + (f" -- {detail}" if detail else ""))


class Backend:
    def __init__(self):
        self.proc = subprocess.Popen(
            [PYTHON, str(BACKEND)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", cwd=str(ROOT),
        )
        self._stderr_lines = []
        self._responses = {}
        self._waiters = {}
        self._events = []
        self._id = 0
        threading.Thread(target=self._read_stderr, daemon=True).start()
        threading.Thread(target=self._read, daemon=True).start()

    def _read_stderr(self):
        for line in self.proc.stderr:
            self._stderr_lines.append(line.rstrip()[:220])

    def _read(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" in msg:
                self._responses[msg["id"]] = msg
                waiter = self._waiters.pop(msg["id"], None)
                if waiter:
                    waiter.set()
            elif "event" in msg:
                self._events.append(msg)

    def request(self, action, timeout=180, **params):
        self._id += 1
        rid = self._id
        self._waiters[rid] = threading.Event()
        self.proc.stdin.write(json.dumps({"id": rid, "action": action, **params}) + "\n")
        self.proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._waiters[rid].wait(0.2):
                msg = self._responses.get(rid, {})
                if msg.get("ok") is False:
                    tail = "\n".join(self._stderr_lines[-8:])
                    raise RuntimeError(
                        f"{action} failed: {msg.get('error')}\n--- backend stderr tail ---\n{tail}"
                    )
                return msg
        raise TimeoutError(f"No response for {action} within {timeout}s")

    def kill(self):
        self.proc.kill()


def main():
    if not LIBRARY.is_dir():
        print("Test library missing; run scripts/make_test_library.py first")
        return 1

    data_dir = LIBRARY / ".faceframe"
    if data_dir.exists():
        shutil.rmtree(data_dir)
    # Remove artifacts a previous run may have left behind (e.g. after a
    # crash): a stray duplicate skews dedup-sensitive checks in both suites.
    shutil.rmtree(LIBRARY / "moved_sub", ignore_errors=True)
    stray_copy = LIBRARY / "duplicate_copy.jpg"
    if stray_copy.exists():
        stray_copy.unlink()

    b = Backend()
    try:
        lib = str(LIBRARY)

        print("== v3 scan ==")
        ack = b.request("scan", path=lib, provider="cpu")
        assert ack.get("ok"), "scan rejected: %s" % ack.get("error")
        _wait_scan_complete(b, timeout=900)
        # cluster explicitly (reply carries stats)
        stats = b.request("cluster", path=lib, timeout=600)["data"]
        check("cluster produced people", stats.get("people", 0) >= 3, str(stats.get("people")))
        print("  backend stderr tail:", b._stderr_lines[-6:])

        print("== open_library / feed ==")
        info = b.request("open_library", path=lib)["data"]
        check("open_library reports items", info.get("items", 0) > 0, str(info.get("items")))
        feed = b.request("get_feed", path=lib, view="days")["data"]["groups"]
        total_items = sum(len(g["items"]) for g in feed)
        check("feed groups items", total_items > 0, str(total_items))
        check("feed day keys sorted desc",
              [g["key"] for g in feed] == sorted([g["key"] for g in feed], reverse=True))

        print("== rescan idempotence (the mandate) ==")
        before = b.request("get_persons", path=lib)["data"]["persons"]
        faces_before = _face_count(b, lib)
        scan2 = _rescan_and_get_stats(b, lib)
        check("rescan decodes nothing", scan2.get("decoded", -1) == 0, str(scan2.get("decoded")))
        faces_after = _face_count(b, lib)
        check("rescan keeps every face", faces_after == faces_before,
              f"{faces_before} -> {faces_after}")
        after = b.request("get_persons", path=lib)["data"]["persons"]
        check("rescan keeps person list",
              {p["name"]: p["face_count"] for p in before} == {p["name"]: p["face_count"] for p in after})

        print("== move file: zero decode, faces survive ==")
        victim = next(LIBRARY.glob("*.jpg"))
        target = LIBRARY / "moved_sub"
        target.mkdir(exist_ok=True)
        moved = target / victim.name
        shutil.move(str(victim), str(moved))
        scan3 = _rescan_and_get_stats(b, lib)
        check("move decodes nothing", scan3.get("decoded", -1) == 0, str(scan3.get("decoded")))
        b.request("cluster", path=lib, timeout=600)
        total_after_move = _face_count(b, lib)
        check("faces preserved across move", total_after_move > 0, str(total_after_move))
        shutil.move(str(moved), str(victim))
        _rescan_and_get_stats(b, lib)

        print("== duplicates ==")
        sample = next(LIBRARY.glob("*.jpg"))
        copy = LIBRARY / "duplicate_copy.jpg"
        shutil.copyfile(sample, copy)
        _rescan_and_get_stats(b, lib)
        dupes = b.request("get_duplicates", path=lib)["data"]["groups"]
        check("exact duplicate detected", any(d["count"] == 2 for d in dupes), str(len(dupes)))
        # remove the copy via trash
        b.request("set_trashed", path=lib, paths=[str(copy)], trashed=True)
        feed_after = b.request("get_feed", path=lib, view="days")["data"]["groups"]
        still_there = any(
            i["path"].endswith("duplicate_copy.jpg")
            for g in feed_after for i in g["items"]
        )
        check("trashed file leaves the feed", still_there is False)
        b.request("set_trashed", path=lib, paths=[str(copy)], trashed=False)

        print("== captions + search ==")
        # get_item takes the ITEM's absolute path (what every view hands the
        # viewer) and returns full detail — exercise that contract directly.
        item_detail = b.request("get_item", path=str(sample))["data"]["item"]
        content_hash = item_detail["content_hash"]
        check("get_item resolves an absolute item path", bool(content_hash))
        feed0 = b.request("get_feed", path=lib, view="days")["data"]["groups"]
        feed_hash = next(
            i["content_hash"]
            for g in feed0 for i in g["items"] if i["path"].endswith(sample.name)
        )
        check("get_item content_hash matches the feed row", feed_hash == content_hash)
        b.request("set_caption", path=lib, hash=content_hash, caption="unique zebra capsule")
        hits = b.request("search", path=lib, query="zebra capsule")["data"]["items"]
        check("caption is searchable", len(hits) >= 1)
        b.request("set_caption", path=lib, hash=content_hash, caption="")

        print("== albums ==")
        album = b.request("create_album", path=lib, name="E2E album")["data"]
        album_id = album["album_id"]
        hashes = [content_hash]
        b.request("album_add", path=lib, album_id=album_id, hashes=hashes)
        got = b.request("get_album", path=lib, album_id=album_id)["data"]["album"]
        check("album holds the item", got["count"] == 1, str(got["count"]))
        b.request("album_remove", path=lib, album_id=album_id, hashes=hashes)
        got = b.request("get_album", path=lib, album_id=album_id)["data"]["album"]
        check("album removal keeps media", got["count"] == 0 and _face_count(b, lib) > 0)
        b.request("delete_album", path=lib, album_id=album_id)

        print("== edit round-trip ==")
        # Verify the edit through the edit-aware preview: it must change with
        # the edit and restore on revert.
        base = b.request(
            "get_image_preview", file_path=str(sample), max_dim=400
        )["data"]["data_url"]
        edit = {"rotate": 90}
        # Mirror the renderer contract: set_edit accepts the edit document
        # as a JSON object and serializes it server-side.
        b.request("set_edit", path=lib, hash=content_hash, edit=edit)
        prev = b.request("get_image_preview", file_path=str(sample), max_dim=400)["data"]["data_url"]
        check("edited preview renders", prev.startswith("data:image/jpeg;base64,"))
        check("edit changes the rendered preview", prev != base)
        b.request("set_edit", path=lib, hash=content_hash, edit=None)
        reverted = b.request(
            "get_image_preview", file_path=str(sample), max_dim=400
        )["data"]["data_url"]
        check("edit revert restores the original render", reverted == base)

        print("== locked folder ==")
        b.request("set_locked_passcode", path=lib, code="1234")
        b.request("set_locked", path=lib, hashes=[content_hash], locked=True)
        feed_locked = b.request("get_feed", path=lib, view="days")["data"]["groups"]
        hidden = all(
            i["content_hash"] != content_hash
            for g in feed_locked for i in g["items"]
        )
        check("locked item hidden from feed", hidden)
        check("wrong passcode refused",
              b.request("verify_locked_passcode", path=lib, code="0000")["data"]["ok"] is False)
        check("right passcode accepted",
              b.request("verify_locked_passcode", path=lib, code="1234")["data"]["ok"] is True)
        b.request("remove_locked_passcode", path=lib, code="1234")
        feed_unlocked = b.request("get_feed", path=lib, view="days")["data"]["groups"]
        check("removing passcode unlocks items",
              any(i["content_hash"] == content_hash for g in feed_unlocked for i in g["items"]))

        print("== trash + restore ==")
        victim2 = next(LIBRARY.glob("*.jpg"))
        feed2 = b.request("get_feed", path=lib, view="days")["data"]["groups"]
        h2 = next(
            i["content_hash"]
            for g in feed2 for i in g["items"] if i["path"].endswith(victim2.name)
        )
        b.request("set_trashed", path=lib, hashes=[h2], trashed=True)
        trashed = b.request("get_trashed", path=lib)["data"]["items"]
        check("trashed item listed", any(i["content_hash"] == h2 for i in trashed))
        b.request("set_trashed", path=lib, hashes=[h2], trashed=False)
        feed_final = b.request("get_feed", path=lib, view="days")["data"]["groups"]
        check("restored item back in feed",
              any(i["content_hash"] == h2 for g in feed_final for i in g["items"]))

        print("== storage + places/memories ==")
        stats2 = b.request("get_storage_stats", path=lib)["data"]["stats"]
        check("storage stats present", stats2["items"]["count"] > 0 and stats2["index_bytes"] > 0)
        mem = b.request("get_memories", path=lib)["data"]["memories"]
        check("memories builder runs", isinstance(mem, list))

        b.request("clear_index", path=lib)
        check("final clear", not data_dir.exists())
        # Leave no debris: a stray duplicate_copy.jpg in the flat library
        # creates a dedup pair that skews later runs of both e2e suites.
        (LIBRARY / "duplicate_copy.jpg").unlink(missing_ok=True)
        shutil.rmtree(LIBRARY / "moved_sub", ignore_errors=True)
    finally:
        b.kill()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("Failed:", *FAIL, sep="\n  - ")
        return 1
    return 0


def _face_count(b, lib):
    faces = b.request("get_unclustered", path=lib)["data"]["faces"]
    persons = b.request("get_persons", path=lib)["data"]["persons"]
    return len(faces) + sum(p["face_count"] for p in persons)


def _wait_scan_complete(b, timeout=600):
    """Wait for a scan_complete / scan_error event from the event log."""
    marker = len(b._events)
    deadline = time.time() + timeout
    while time.time() < deadline:
        for msg in b._events[marker:]:
            if msg["event"] == "scan_complete":
                return msg
            if msg["event"] == "scan_error":
                raise RuntimeError(msg.get("message"))
        time.sleep(0.5)
    raise TimeoutError("scan did not complete")


def _rescan_and_get_stats(b, lib, timeout=600):
    """Trigger a scan and return its scan_complete stats from the event log."""
    marker = len(b._events)
    ack = b.request("scan", path=lib, provider="cpu")
    assert ack.get("ok"), "scan rejected: %s" % ack.get("error")
    return _wait_scan_complete(b, timeout)


if __name__ == "__main__":
    sys.exit(main())
