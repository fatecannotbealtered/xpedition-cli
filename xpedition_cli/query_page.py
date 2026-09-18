"""Bound local query work to a page plus one matching lookahead record.

This does not push a query into Xpedition or cache native snapshots. It preserves
legacy substring matching, result order, clamped offsets and zero-limit behavior.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any

from .errors import CLIError


def _result(rows: list[Any], offset: int, has_more: bool) -> dict[str, Any]:
    return {
        "items": rows,
        "count": len(rows),
        "offset": offset,
        "next_offset": offset + len(rows) if has_more else None,
        "has_more": has_more,
    }


def query_page(
    items: Iterable[Any],
    *,
    query: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict[str, Any]:
    for name, value in (("limit", limit), ("offset", offset)):
        if name == "limit" and value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise CLIError("E_VALIDATION", f"--{name} must be a non-negative integer")
    needle = str(query).casefold() if query else None
    if needle is None and isinstance(items, Sequence):
        start = min(offset, len(items))
        end = len(items) if limit is None else min(len(items), start + limit)
        return _result(list(items[start:end]), start, end < len(items))
    rows: list[Any] = []
    skipped = 0
    for item in items:
        if (
            needle is not None
            and needle not in json.dumps(item, ensure_ascii=False, sort_keys=True).casefold()
        ):
            continue
        if skipped < offset:
            skipped += 1
            continue
        if limit is not None and len(rows) == limit:
            return _result(rows, skipped, True)
        rows.append(item)
    return _result(rows, skipped, False)
