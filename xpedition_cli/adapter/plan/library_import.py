"""What a library read-back compares: a record's content without where it was read from.

After `part create` writes a symbol, the read-back parses the symbol file the library
holds and compares it with the one the plan wrote. The two differ in where they came
from (partition, file path, file version), never in what they draw.
"""

from __future__ import annotations

from typing import Any

from . import library_read as R


def _without(item: dict[str, Any] | None, *keys: str) -> dict[str, Any] | None:
    """The content of a record as read, without `keys`, its lists in one order."""
    if item is None:
        return None
    return R.canonical({key: value for key, value in item.items() if key not in keys})


def _symbol_content(symbol: dict[str, Any] | None) -> dict[str, Any] | None:
    """A parsed symbol (`library_read.parse_symbol`) without its partition, path and version."""
    return _without(symbol, "partition", "path", "version")
