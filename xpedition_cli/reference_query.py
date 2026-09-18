"""Select a self-contained slice of the live reference, never a second catalog."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from .errors import CLIError

SELECTORS = {
    "command": "Exact command path from reference.commands (for example: pcb trace)",
    "domain": "One top-level command domain (for example: schematic)",
    "schema": "One existing output-schema name from reference.schemas",
}
SELECTOR_FLAGS = frozenset(f"--{name}" for name in SELECTORS)


def selector_params() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "type": "string",
            "required": False,
            "multiple": False,
            "description": description,
            "mutually_exclusive_with": [key for key in SELECTORS if key != name],
        }
        for name, description in SELECTORS.items()
    ]


def validate_reference_options(
    positionals: list[str], options: Mapping[str, Any]
) -> tuple[str, str] | None:
    supplied = [name for name in SELECTORS if name in options and options[name] is not None]
    if not supplied:
        return None
    if positionals != ["reference"]:
        raise CLIError(
            "E_USAGE", "reference selectors are only valid on reference", {"options": supplied}
        )
    if len(supplied) != 1:
        raise CLIError(
            "E_USAGE", "choose only one of --command, --domain or --schema", {"options": supplied}
        )
    name = supplied[0]
    raw = options[name]
    if not isinstance(raw, str) or not raw.strip():
        raise CLIError("E_VALIDATION", f"--{name} must be a non-empty string", {"option": name})
    value = " ".join(raw.split())
    if name in {"domain", "schema"} and " " in value:
        raise CLIError("E_VALIDATION", f"--{name} must be one catalog identifier", {"option": name})
    return name, value


def select_reference(
    catalog: dict[str, Any],
    *,
    command: str | None = None,
    domain: str | None = None,
    schema: str | None = None,
) -> dict[str, Any]:
    selector = validate_reference_options(
        ["reference"], {"command": command, "domain": domain, "schema": schema}
    )
    if selector is None:
        return catalog
    kind, value = selector
    if kind == "schema":
        selected = []
        names = {value}
        exists = value in catalog["schemas"]
    else:
        selected = [
            item
            for item in catalog["commands"]
            if (item["path"] == value if kind == "command" else item["path"].split()[0] == value)
        ]
        exists = bool(selected)
        names = {
            item[key]
            for item in selected
            for key in ("output_schema", "dry_run_output_schema")
            if key in item
        }
    if not exists:
        raise CLIError(
            "E_NOT_FOUND",
            f"reference {kind} was not found",
            {
                "selector": kind,
                "value": value,
                "hint": "run reference without a selector to inspect the live catalog",
                "_untrusted": ["value"],
            },
        )
    missing = sorted(names - catalog["schemas"].keys())
    if missing:
        raise CLIError(
            "E_CONFIG", "command refers to an undeclared output schema", {"schemas": missing}
        )
    result = {
        key: copy.deepcopy(
            selected
            if key == "commands"
            else {name: item for name, item in payload.items() if name in names}
            if key == "schemas"
            else payload
        )
        for key, payload in catalog.items()
    }
    result["selection"] = {
        "kind": kind,
        "value": value,
        "command_count": len(selected),
        "schema_count": len(names),
    }
    return result
