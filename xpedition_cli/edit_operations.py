"""The operations `schematic edit` takes, and their published input schema.

An operations file is `{"operations": [...]}`. Each operation names its `type`;
the ones Designer's automation carries out are listed in SUPPORTED. The dry run
projects every operation onto the design as read (`changeset.apply_operations`),
so a missing part, pin or net is refused before anything is written.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .changeset import validate_operation
from .errors import CLIError

MAX_OPERATIONS = 200
SUPPORTED = ("place_component", "move_component", "create_net", "connect")

_FIELDS: dict[str, dict[str, Any]] = {
    "place_component": {
        "required": ["refdes", "library", "part_number", "x", "y"],
        "properties": {
            "refdes": {"type": "string", "description": "the new part's reference designator"},
            "library": {
                "type": "string",
                "description": "the central-library partition its symbol is in",
            },
            "part_number": {"type": "string", "description": "the part (device) to place"},
            "symbol_name": {
                "type": "string",
                "description": "the symbol, when it is not named like the part",
            },
            "sheet": {"type": "integer", "minimum": 1, "description": "the sheet to place on"},
            "x": {"type": "integer", "description": "sheet units"},
            "y": {"type": "integer", "description": "sheet units"},
        },
    },
    "move_component": {
        "required": ["refdes", "x", "y"],
        "properties": {
            "refdes": {"type": "string"},
            "sheet": {"type": "integer", "minimum": 1},
            "x": {"type": "integer", "description": "sheet units"},
            "y": {"type": "integer", "description": "sheet units"},
        },
    },
    "create_net": {
        "required": ["name"],
        "properties": {
            "name": {
                "type": "string",
                "description": "the new net; it is drawn by the connect that names it",
            }
        },
    },
    "connect": {
        "required": ["net", "pins"],
        "properties": {
            "net": {"type": "string", "description": "the net the wire gets as its label"},
            "pins": {
                "type": "array",
                "minItems": 2,
                "maxItems": 2,
                "items": {"type": "string", "pattern": "^[^.]+\\..+$"},
                "description": "two pins as <refdes>.<pin number>; a wire joins them",
            },
            "points": {
                "type": "array",
                "items": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "integer"},
                },
                "description": "corners the wire passes through, sheet units; one horizontal "
                "and one vertical segment by default",
            },
            "x": {
                "type": "integer",
                "description": "the net label's position (beside the first pin)",
            },
            "y": {"type": "integer"},
            "sheet": {"type": "integer", "minimum": 1},
        },
    },
}


def input_schema() -> dict[str, Any]:
    variants = []
    for kind in SUPPORTED:
        spec = _FIELDS[kind]
        variants.append(
            {
                "type": "object",
                "required": ["type", *spec["required"]],
                "properties": {"type": {"const": kind}, **spec["properties"]},
            }
        )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["operations"],
        "properties": {
            "operations": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_OPERATIONS,
                "items": {"oneOf": variants},
            }
        },
    }


def load(path: Path) -> list[dict[str, Any]]:
    """The operations in an operations file, each checked for its type and fields."""
    import json

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CLIError(
            "E_NOT_FOUND", f"operations file cannot be read: {exc}", {"path": str(path)}
        ) from exc
    except json.JSONDecodeError as exc:
        raise CLIError(
            "E_CHANGESET_INVALID", f"operations file is not valid JSON: {exc}", {"path": str(path)}
        ) from exc
    operations = raw.get("operations") if isinstance(raw, dict) else None
    if not isinstance(operations, list) or not operations:
        raise CLIError(
            "E_CHANGESET_INVALID",
            "the operations file needs a non-empty operations array",
            {"path": str(path)},
        )
    if len(operations) > MAX_OPERATIONS:
        raise CLIError(
            "E_CHANGESET_INVALID",
            f"at most {MAX_OPERATIONS} operations a file",
            {"count": len(operations)},
        )
    for index, operation in enumerate(operations):
        kind = operation.get("type") if isinstance(operation, dict) else None
        if kind not in SUPPORTED:
            raise CLIError(
                "E_CHANGESET_INVALID",
                f"operation {index} has an unsupported type: {kind!r}",
                {"index": index, "supported": list(SUPPORTED)},
            )
        missing = [key for key in _FIELDS[kind]["required"] if key not in operation]
        if missing:
            raise CLIError(
                "E_CHANGESET_INVALID",
                f"operation {index} ({kind}) is missing {', '.join(missing)}",
                {"index": index, "missing": missing},
            )
        validate_operation(operation, index)
        if kind == "connect" and len(operation["pins"]) != 2:
            raise CLIError(
                "E_CHANGESET_INVALID",
                f"operation {index}: connect joins exactly two pins",
                {"index": index},
            )
        points = operation.get("points")
        if points is not None and (
            not isinstance(points, list)
            or not all(
                isinstance(point, list)
                and len(point) == 2
                and all(isinstance(value, int) and not isinstance(value, bool) for value in point)
                for point in points
            )
        ):
            raise CLIError(
                "E_CHANGESET_INVALID",
                f"operation {index}: points is a list of [x, y] integer pairs",
                {"index": index},
            )
        for key in ("x", "y", "sheet"):
            if key in operation and (
                isinstance(operation[key], bool) or not isinstance(operation[key], int)
            ):
                raise CLIError(
                    "E_CHANGESET_INVALID",
                    f"operation {index}: {key} is an integer",
                    {"index": index, "field": key},
                )
    return operations
