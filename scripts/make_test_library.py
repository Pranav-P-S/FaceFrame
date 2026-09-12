"""Build a small face test library from the LFW dataset.

Downloads the Labeled Faces in the Wild archive (cached under test-data/)
and copies a fixed number of images per person into a flat folder suitable
for an end-to-end FaceFrame scan. People with many photos give clustering
something meaningful to work with.

Usage:
    python scripts/make_test_library.py [images_per_person] [people]
"""

import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_PATH = ROOT / "test-data" / "lfw.tgz"
ARCHIVE_URL = "https://ndownloader.figshare.com/files/5976018"


def ensure_archive():
    if ARCHIVE_PATH.exists() and ARCHIVE_PATH.stat().st_size > 100_000_000:
        return
    ARCHIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading LFW archive to {ARCHIVE_PATH} ...")
    urllib.request.urlretrieve(ARCHIVE_URL, ARCHIVE_PATH)
    print("Download complete.")


def build_library(images_per_person: int, people: int, dest: Path):
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    with tarfile.open(ARCHIVE_PATH, "r:gz") as tar:
        per_person = {}
        for member in tar:
            if not member.isfile():
                continue
            parts = member.name.split("/")
            if len(parts) != 3 or not parts[2].lower().endswith((".jpg", ".jpeg", ".png")):
                continue
            person = parts[1]
            # Skip near-empty identities; a couple of images cannot test
            # clustering.
            count = per_person.get(person, 0)
            if count >= images_per_person:
                continue
            if count == 0 and len(per_person) >= people and person not in per_person:
                continue
            per_person[person] = count + 1
            target = dest / f"{person}_{count + 1:03d}{Path(member.name).suffix}"
            if not target.exists():
                with tar.extractfile(member) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
            copied += 1
            if copied % 25 == 0:
                print(f"  {copied} images copied...")
    print(f"Done: {copied} images from {len(per_person)} people -> {dest}")


if __name__ == "__main__":
    per_person = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    people = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    target_dir = ROOT / "test-data" / "library"
    ensure_archive()
    build_library(per_person, people, target_dir)
