"""Quick manual harness: spawn the backend, scan a tiny library, print all
stdout events and the stderr tail. For debugging only."""

import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python-backend" / "tests"))
from conftest import write_jpeg  # noqa: E402


def main():
    root = Path("test-data/library").resolve()
    import shutil as _sh; _sh.rmtree(root / ".faceframe", ignore_errors=True)
    import shutil
    shutil.rmtree(root / ".faceframe", ignore_errors=True)

    proc = subprocess.Popen(
        [str(ROOT / "venv/Scripts/python.exe"), str(ROOT / "python-backend/main.py")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        cwd=str(ROOT),
    )
    out_lines, err_lines = [], []

    def reader(stream, tag, sink):
        for line in stream:
            sink.append((tag, line.rstrip()[:220]))

    threading.Thread(target=reader, args=(proc.stdout, "OUT", out_lines), daemon=True).start()
    threading.Thread(target=reader, args=(proc.stderr, "ERR", err_lines), daemon=True).start()

    def send(obj):
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    send({"action": "ping", "id": 1})
    send({"action": "scan", "id": 2, "path": str(root), "provider": "cpu"})

    deadline = time.time() + 300
    done = False
    while time.time() < deadline:
        time.sleep(1)
        if any("scan_complete" in l or "scan_error" in l for _, l in out_lines):
            done = True
            break

    time.sleep(2)
    print(f"done={done}")
    print("--- STDOUT ---")
    for tag, line in out_lines[-15:]:
        print(tag, line)
    print("--- STDERR tail ---")
    for tag, line in err_lines[-120:]:
        print(tag, line)
    proc.kill()


if __name__ == "__main__":
    main()
