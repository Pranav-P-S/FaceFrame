"""Search query parser.

Grammar: free-text words plus ``key:value`` tokens (value may be quoted).
``year:`` is sugar for an after/before pair. Keys repeat (list semantics).
This parser is mirrored in TypeScript for the frontend's filter chips — keep
the two in sync.
"""

import shlex
from dataclasses import dataclass, field

KNOWN_KEYS = {
    "person", "people", "place", "type", "is", "folder", "in",
    "after", "before", "on", "year", "label",
}


@dataclass
class Query:
    text: list = field(default_factory=list)
    filters: dict = field(default_factory=dict)

    def get(self, key: str) -> list:
        return self.filters.get(key, [])


def parse(raw: str) -> Query:
    query = Query()
    try:
        tokens = shlex.split(raw or "")
    except ValueError:
        tokens = (raw or "").split()
    for token in tokens:
        if ":" in token:
            key, _, value = token.partition(":")
            key = key.lower()
            value = value.strip().strip('"')
            if key == "year" and value.isdigit() and len(value) == 4:
                query.filters.setdefault("after", []).append(f"{value}-01-01")
                query.filters.setdefault("before", []).append(f"{int(value) + 1}-01-01")
                continue
            if key in KNOWN_KEYS and value:
                if key in ("person", "people"):
                    key = "person"
                elif key in ("place", "in"):
                    key = "place"
                query.filters.setdefault(key, []).append(value)
                continue
        query.text.append(token)
    return query
