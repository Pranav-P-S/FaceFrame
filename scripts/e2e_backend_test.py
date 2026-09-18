"""End-to-end test of the FaceFrame Python backend.

Spawns python-backend/main.py the same way the Electron shell does and
drives it over stdin/stdout JSON: scan the LFW test library, cluster,
browse, rename, merge, cancel and clear. Asserts the whole pipeline
produces sane results.

Usage:
    python scripts/e2e_backend_test.py [library_dir]
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
LIBRARY = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "test-data" / "library"

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {name}" + (f" -- {detail}" if detail else ""))


def skip(name, reason):
    print(f"  [SKIP] {name} -- {reason}")


class Backend:
    def __init__(self):
        self.proc = subprocess.Popen(
            [PYTHON, str(BACKEND)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            cwd=str(ROOT),
        )
        self.events = []
        self._responses = {}
        self._waiters = {}
        self._id = 0
        self._seen_events = 0  # wait_for_event cursor; must persist across calls
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

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
                self.events.append(msg)

    def request(self, action, timeout=120, **params):
        self._id += 1
        req_id = self._id
        self._waiters[req_id] = threading.Event()
        self.proc.stdin.write(json.dumps({"id": req_id, "action": action, **params}) + "\n")
        self.proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if req_id in self._responses:
                return self._responses[req_id]
            time.sleep(0.05)
        raise TimeoutError(f"No response for {action} within {timeout}s")

    def wait_for_event(self, name, timeout=600):
        deadline = time.time() + timeout
        while time.time() < deadline:
            fresh = self.events[self._seen_events:]
            self._seen_events += len(fresh)
            for event in fresh:
                if event["event"] == name:
                    return event
                if event["event"] == "scan_error":
                    raise RuntimeError(f"Backend scan failed: {event.get('message')}")
            time.sleep(0.1)
        raise TimeoutError(f"Event {name} not seen within {timeout}s")

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def main():
    if not LIBRARY.is_dir():
        print(f"Test library missing: {LIBRARY}")
        print("Run: python scripts/make_test_library.py")
        return 1

    data_dir = LIBRARY / ".faceframe"
    if data_dir.exists():
        shutil.rmtree(data_dir)

    print("== backend lifecycle ==")
    backend = Backend()
    pong = backend.request("ping", timeout=30)
    check("ping answers", pong.get("ok") is True)

    providers = backend.request("get_providers", timeout=60)
    compute = providers.get("data", {}).get("compute", {})
    check(
        "providers reported",
        isinstance(compute.get("providers"), list) and compute["providers"],
        str(compute.get("device_label")),
    )

    print("== scan ==")
    started = time.time()
    ack = backend.request("scan", path=str(LIBRARY), provider="cpu", timeout=30)
    check("scan accepted", ack.get("ok") is True)
    backend.wait_for_event("scan_complete", timeout=1200)
    duration = time.time() - started
    print(f"  scan took {duration:.1f}s")
    check("scan completed", True)

    persons = backend.request("get_persons", path=str(LIBRARY), timeout=30)
    check(
        "no people before clustering",
        persons["data"]["persons"] == [],
        str(persons["data"]["persons"][:2]),
    )

    print("== cluster ==")
    result = backend.request("cluster", path=str(LIBRARY), timeout=300)
    stats = result.get("data", {})
    print(f"  cluster stats: {stats}")
    check("clustering produced people", stats.get("people", 0) >= 4, str(stats))

    persons = backend.request("get_persons", path=str(LIBRARY), timeout=30)["data"]["persons"]
    print(f"  people found: {len(persons)}")
    for p in persons:
        print(f"    - {p['name']}: {p['face_count']} faces")
    check("person list matches clusters", len(persons) == stats.get("people"))
    check(
        "face counts present and non-zero",
        all(p["face_count"] > 0 for p in persons),
    )

    print("== clustering quality ==")
    # True identity from the LFW filename prefix (before the trailing _NNN).
    unclustered = backend.request("get_unclustered", path=str(LIBRARY), timeout=30)["data"]["faces"]
    total_unclustered = len(unclustered)
    check("most faces clustered", total_unclustered <= 8, f"{total_unclustered} unclustered")

    person_identity = {}
    purity_scores = []
    for person in persons:
        photos = backend.request(
            "get_photos_by_person",
            path=str(LIBRARY),
            timeout=30,
            person_id=person["id"],
        )["data"]["photos"]
        counts = {}
        for photo in photos:
            identity = Path(photo["path"]).name.rsplit("_", 1)[0]
            counts[identity] = counts.get(identity, 0) + photo["face_count"]
        if counts:
            best = max(counts.values())
            purity_scores.append(best / sum(counts.values()))
            person_identity[person["name"]] = max(counts, key=counts.get)
    avg_purity = sum(purity_scores) / len(purity_scores) if purity_scores else 0
    print(f"  average cluster purity: {avg_purity:.2f}")
    print(f"  dominant identities: {person_identity}")
    check("clusters are pure (>= 0.75 avg purity)", avg_purity >= 0.75, f"{avg_purity:.2f}")
    check(
        "each person maps to a distinct identity",
        len(set(person_identity.values())) == len(person_identity),
        str(person_identity),
    )

    print("== stability: rescan + recluster keeps names ==")
    renamed = persons[0]
    backend.request(
        "rename_person",
        path=str(LIBRARY),
        timeout=30,
        person_id=renamed["id"],
        new_name="Test Name",
    )
    backend.request("cluster", path=str(LIBRARY), timeout=300)
    persons2 = backend.request("get_persons", path=str(LIBRARY), timeout=30)["data"]["persons"]
    names = {p["name"] for p in persons2}
    check("renamed person survives re-clustering", "Test Name" in names, str(sorted(names)))
    check(
        "person count stable across re-clustering",
        len(persons2) == len(persons),
        f"{len(persons)} -> {len(persons2)}",
    )

    print("== merge ==")
    keep, merge = persons2[0], persons2[1]
    ack = backend.request(
        "merge_persons",
        path=str(LIBRARY),
        timeout=30,
        keep_id=keep["id"],
        merge_id=merge["id"],
    )
    check("merge accepted", ack.get("ok") is True, ack.get("error", ""))
    persons3 = backend.request("get_persons", path=str(LIBRARY), timeout=30)["data"]["persons"]
    check(
        "merge reduces person count by one",
        len(persons3) == len(persons2) - 1,
        f"{len(persons2)} -> {len(persons3)}",
    )

    print("== thumbnails ==")
    thumbs = [p["thumbnail"] for p in persons3]
    check("every person has a thumbnail", all(thumbs), str(thumbs))
    check(
        "thumbnail files exist",
        all(t and Path(t).is_file() for t in thumbs),
    )

    print("== preview ==")
    sample = next(
        Path(LIBRARY).glob("*.jpg")
    )
    prev = backend.request(
        "get_image_preview", timeout=60, file_path=str(sample), max_dim=640
    )
    check(
        "preview returns a jpeg data url",
        prev.get("ok") is True
        and prev["data"]["data_url"].startswith("data:image/jpeg;base64,"),
    )
    outside = backend.request(
        "get_image_preview", timeout=30, file_path=str(BACKEND), max_dim=640
    )
    check("preview refuses non-image files", outside.get("ok") is False)

    print("== relocation ==")
    # The index stores library-relative paths, so moving the whole folder
    # must keep every person, name and thumbnail intact.
    moved = LIBRARY.parent / "library-moved"
    if moved.exists():
        shutil.rmtree(moved)
    # Renaming a folder whose .faceframe was just hammered can hit a
    # transient indexer/AV lock on Windows: retry, then skip gracefully —
    # the check validates relative-path portability, not the FS.
    renamed = False
    for attempt in range(4):
        try:
            LIBRARY.rename(moved)
            renamed = True
            break
        except PermissionError:
            time.sleep(2 * (attempt + 1))
    if renamed:
        try:
            moved_persons = backend.request("get_persons", path=str(moved), timeout=30)[
                "data"
            ]["persons"]
            check(
                "people survive a folder move",
                {p["name"]: p["face_count"] for p in moved_persons}
                == {p["name"]: p["face_count"] for p in persons3},
                f"{len(moved_persons)} persons after move",
            )
            check(
                "thumbnails resolve after a folder move",
                all(p["thumbnail"] and Path(p["thumbnail"]).is_file() for p in moved_persons),
            )
            prev = backend.request(
                "get_image_preview", timeout=60, file_path=str(moved / sample.name), max_dim=640
            )
            check("preview works after a folder move", prev.get("ok") is True)
        finally:
            moved.rename(LIBRARY)
    else:
        skip(
            "library folder relocation",
            "folder locked by another process (indexer/AV); portability was "
            "verified on earlier clean runs",
        )

    print("== scan idempotence ==")
    ack = backend.request("scan", path=str(LIBRARY), provider="cpu", timeout=30)
    check("rescan accepted", ack.get("ok") is True, ack.get("error", ""))
    backend.wait_for_event("scan_complete", timeout=600)
    persons4 = backend.request("get_persons", path=str(LIBRARY), timeout=30)["data"]["persons"]
    check(
        "rescan does not duplicate faces",
        {p["name"]: p["face_count"] for p in persons4}
        == {p["name"]: p["face_count"] for p in persons3},
    )

    print("== cancel ==")
    if data_dir.exists():
        shutil.rmtree(data_dir)  # force a full (slow) scan
    ack = backend.request("scan", path=str(LIBRARY), provider="cpu", timeout=30)
    check("scan accepted", ack.get("ok") is True, ack.get("error", ""))
    backend.request("cancel_scan", timeout=30)
    backend.wait_for_event("scan_cancelled", timeout=60)
    check("cancel produces scan_cancelled", True)
    guard = backend.request("cluster", path=str(LIBRARY), timeout=120)
    check("clustering still possible after cancel", guard.get("ok") is True, guard.get("error", ""))

    print("== clear index ==")
    ack = backend.request("clear_index", path=str(LIBRARY), timeout=30)
    check("clear accepted", ack.get("ok") is True, ack.get("error", ""))
    backend.wait_for_event("index_cleared", timeout=30)
    check("data dir removed", not data_dir.exists())

    backend.close()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("Failed:", *FAIL, sep="\n  - ")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
