"""Shape-preserving field selection, including records inside JSON arrays.

Missing selectors keep the legacy omission semantics. Selection happens after
execution, so a bad selector must not turn a completed write into a usage error.
Pagination and untrusted-content annotations are control data, not payload to trim.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_MISSING = object()
_PAGING_KEYS = ("count", "offset", "next_offset", "next_cursor", "has_more", "truncated")


def _selection_tree(fields: str) -> dict[str, Any]:
    tree: dict[str, Any] = {}
    for selector in fields.split(","):
        parts = [part.strip().removesuffix("[]") for part in selector.strip().split(".")]
        if not all(parts):
            continue
        cursor = tree
        for index, part in enumerate(parts):
            if part in cursor and cursor[part] is None:
                break  # A whole-parent selection always wins, in either order.
            if index == len(parts) - 1:
                cursor[part] = None
            else:
                cursor = cursor.setdefault(part, {})
    return tree


def _select(value: Any, tree: dict[str, Any] | None) -> Any:
    if tree is None:
        return value
    if isinstance(value, list):
        # Preserve list length and positions, even for sparse or mixed records.
        selected = []
        for item in value:
            projected = _select(item, tree)
            if projected is _MISSING:
                projected = {} if isinstance(item, Mapping) else None
            selected.append(projected)
        return selected
    if not isinstance(value, Mapping):
        return _MISSING  # A nested field cannot be resolved in a scalar.
    result = {}
    for key, child in tree.items():
        if key in value:
            projected = _select(value[key], child)
            if projected is not _MISSING:
                result[key] = projected
    if result:
        if any(isinstance(item, list) for key, item in result.items() if key != "_untrusted"):
            for key in _PAGING_KEYS:
                if key in value:
                    result[key] = value[key]
        if "_untrusted" in value:
            # Keep conservative source annotations, including dotted/array paths.
            # Never mutate the source or accidentally drop a nested trust boundary.
            result["_untrusted"] = value["_untrusted"]
    return result if result else _MISSING


def project_fields(data: Any, fields: str | None) -> Any:
    if not fields or not isinstance(data, (Mapping, list)):
        return data
    result = _select(data, _selection_tree(fields))
    return {} if result is _MISSING else result
