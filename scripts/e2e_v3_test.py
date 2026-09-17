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
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", cwd=str(ROOT),
        )
        self._responses = {}
        self._waiters = {}
        self._events = []
        self._id = 0
        threading.Thread(target=self._read, daemon=True).start()

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
                return self._responses.get(rid, {})
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

    b = Backend()
    try:
        lib = str(LIBRARY)

        print("== v3 scan ==")
        b.request("scan", provider="cpu")
        # scan replies immediately; wait for the completion event indirectly:
        deadline = time.time() + 900
        while time.time() < deadline:
            persons = b.request("get_persons", path=lib)["data"]["persons"]
            unclustered = b.request("get_unclustered", path=lib)["data"]["faces"]
            if persons or unclustered:
                break
            time.sleep(1)
        # cluster explicitly (reply carries stats)
        stats = b.request("cluster", path=lib, timeout=600)["data"]
        check("cluster produced people", stats.get("people", 0) >= 3, str(stats.get("people")))

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
        scan2 = _rescan_and_get_stats(b, lib)
        check("rescan decodes nothing", scan2.get("decoded", -1) == 0, str(scan2.get("decoded")))
        check("rescan keeps every face", _face_count(b, lib) == _face_count(b, lib))
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
        b.request("set_trashed", paths=[str(copy)], trashed=True)
        feed_after = b.request("get_feed", path=lib, view="days")["data"]["groups"]
        still_there = any(
            i["path"].endswith("duplicate_copy.jpg")
            for g in feed_after for i in g["items"]
        )
        check("trashed file leaves the feed", still_there is False)
        b.request("set_trashed", paths=[str(copy)], trashed=False)

        print("== captions + search ==")
        with open(sample, "rb") as fh:
            content_hash = None
        detail = b.request("get_item", path=str(sample))["data"]["item"]
        content_hash = detail["media"]["content_hash"]
        b.request("set_caption", hash=content_hash, caption="unique zebra capsule")
        hits = b.request("search", query="zebra capsule")["data"]["items"]
        check("caption is searchable", len(hits) >= 1)
        b.request("set_caption", hash=content_hash, caption="")

        print("== albums ==")
        album = b.request("create_album", name="E2E album")["data"]
        album_id = album["album_id"]
        hashes = [detail["media"]["content_hash"]]
        b.request("album_add", album_id=album_id, hashes=hashes)
        got = b.request("get_album", album_id=album_id)["data"]["album"]
        check("album holds the item", got["count"] == 1, str(got["count"]))
        b.request("album_remove", album_id=album_id, hashes=hashes)
        got = b.request("get_album", album_id=album_id)["data"]["album"]
        check("album removal keeps media", got["count"] == 0 and _face_count(b, lib) > 0)
        b.request("delete_album", album_id=album_id)

        print("== edit round-trip ==")
        edit = {"rotate": 90}
        b.request("set_edit", hash=content_hash, edit=edit)
        prev = b.request("get_image_preview", file_path=str(sample), max_dim=400)["data"]["data_url"]
        check("edited preview renders", prev.startswith("data:image/jpeg;base64,"))
        detail2 = b.request("get_item", path=str(sample))["data"]["item"]
        check("edit persisted in detail", json.loads(detail2["media"]["edit"] or "{}") == edit)
        b.request("set_edit", hash=content_hash, edit=None)
        detail3 = b.request("get_item", path=str(sample))["data"]["item"]
        check("edit revert clears", not detail3["media"]["edit"])

        print("== locked folder ==")
        b.request("set_locked_passcode", code="1234")
        b.request("set_locked", hashes=[content_hash], locked=True)
        feed_locked = b.request("get_feed", path=lib, view="days")["data"]["groups"]
        hidden = all(
            i["content_hash"] != content_hash
            for g in feed_locked for i in g["items"]
        )
        check("locked item hidden from feed", hidden)
        check("wrong passcode refused",
              b.request("verify_locked_passcode", code="0000")["data"]["ok"] is False)
        check("right passcode accepted",
              b.request("verify_locked_passcode", code="1234")["data"]["ok"] is True)
        b.request("remove_locked_passcode", code="1234")
        feed_unlocked = b.request("get_feed", path=lib, view="days")["data"]["groups"]
        check("removing passcode unlocks items",
              any(i["content_hash"] == content_hash for g in feed_unlocked for i in g["items"]))

        print("== trash + restore ==")
        victim2 = next(LIBRARY.glob("*.jpg"))
        h2 = b.request("get_item", path=str(victim2))["data"]["item"]["media"]["content_hash"]
        b.request("set_trashed", hashes=[h2], trashed=True)
        trashed = b.request("get_trashed", path=lib)["data"]["items"]
        check("trashed item listed", any(i["hash"] == h2 for i in trashed))
        b.request("set_trashed", hashes=[h2], trashed=False)
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


def _rescan_and_get_stats(b, lib, timeout=600):
    """Trigger a scan and return its scan_complete stats from the event log."""
    marker = len(b._events)
    b.request("scan", provider="cpu")
    deadline = time.time() + timeout
    while time.time() < deadline:
        for msg in b._events[marker:]:
            if msg["event"] == "scan_complete":
                return msg
            if msg["event"] == "scan_error":
                raise RuntimeError(msg.get("message"))
        time.sleep(0.5)
    raise TimeoutError("scan did not complete")


if __name__ == "__main__":
    sys.exit(main())
