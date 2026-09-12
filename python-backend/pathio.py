"""Path conversion between the filesystem and the index database.

The database stores library-relative POSIX-style paths ("sub/img.jpg") so a
library folder stays valid when it is moved or renamed, and so an index is
not bound to one machine's absolute layout. Absolute paths exist only at
the edges: the scanner walks absolute paths, and main.py hands absolute
paths to the UI.
"""

import os


def to_relative(path: str, root: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def to_absolute(path: str, root: str) -> str:
    return os.path.join(root, *path.split("/"))
