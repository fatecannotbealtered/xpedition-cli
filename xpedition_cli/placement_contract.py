"""Discoverable contract for the two placement-task commands."""

from __future__ import annotations

from typing import Any

from .placement import COORDINATE_LIMIT_MM, input_schema

SCHEMAS = {
    "placement_plan": {
        "shape": "object",
        "fields": [
            "schema_version",
            "unit",
            "request",
            "state_digest",
            "results",
            "summary",
            "validation",
            "_untrusted",
        ],
        "untrusted_fields": ["request", "results"],
    },
    "placement_preview": {
        "shape": "object",
        "fields": ["preview", "confirm_token", "expires_at", "_untrusted"],
        "untrusted_fields": ["preview"],
    },
    "placement_result": {
        "shape": "object",
        "fields": [
            "results",
            "outcome",
            "saved",
            "write_attempted",
            "state_digest_before",
            "drc_restored",
            "verification",
            "issues",
            "summary",
            "pcb",
            "prompts",
            "_untrusted",
        ],
        "untrusted_fields": ["results", "issues", "pcb", "prompts"],
    },
}


def observation_schema() -> dict[str, Any]:
    name = input_schema()["properties"]["selection"]["items"]
    numeric = {"type": "number", "minimum": -COORDINATE_LIMIT_MM, "maximum": COORDINATE_LIMIT_MM}
    fields = {
        "refdes": name,
        "object_id": name,
        "x": numeric,
        "y": numeric,
        "rotation": numeric,
        "side": {"enum": ["top", "bottom"]},
        "placed": {"const": True},
        "unit": {"const": "mm"},
        "anchor": {"type": "integer", "enum": [0, 1, 2, 3]},
        "fix_lock": {"type": "integer", "minimum": 0, "maximum": 2147483647},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "components"],
        "properties": {
            "schema_version": {"const": "1.0"},
            "components": {
                "type": "array",
                "items": {"type": "object", "required": list(fields), "properties": fields},
            },
        },
    }


def commands() -> list[dict[str, Any]]:
    result = []
    for verb, write in (("placement-plan", False), ("placement", True)):
        prefix = f"xpedition-cli pcb {verb} --file task.json"
        suffix = (
            " --backend native_xpedition --project board.prj"
            if write
            else " --input observations.json"
        )
        params = [{"name": "file", "type": "path", "required": True, "multiple": False}]
        params += (
            [
                {"name": "project", "type": "path", "required": True, "multiple": False},
                {
                    "name": "backend",
                    "type": "string",
                    "required": True,
                    "multiple": False,
                    "enum": ["native_xpedition"],
                },
                {"name": "dry-run", "type": "boolean", "required": False, "multiple": False},
                {"name": "confirm", "type": "string", "required": False, "multiple": False},
            ]
            if write
            else [{"name": "input", "type": "path", "required": True, "multiple": False}]
        )
        entry = {
            "path": f"pcb {verb}",
            "type": "write" if write else "query",
            "description": "Plan explicit selected-origin translate/rotate/align/distribute tasks"
            + (
                " and apply with read-back (native smoke missing)"
                if write
                else " from supplied observations without COM"
            ),
            "params": params,
            "output_schema": "placement_result" if write else "placement_plan",
            "examples": [
                prefix + suffix + flag + " --compact"
                for flag in ([" --dry-run", " --confirm <confirm_token>"] if write else [""])
            ],
            "permission_tier": "write" if write else "read",
            "blast_radius": "selected placements; board saved only after verified completion"
            if write
            else "none",
            "input_json_schema": input_schema(),
            "input_max_bytes": 1048576,
            "verification": {
                "native_smoke": "missing",
                "scope": "selected_placements",
                "drc": "native placement check only; not full DRC",
            },
            "ordering": "explicit selection order; one final placement per changed component",
        }
        if write:
            entry["dry_run_output_schema"] = "placement_preview"
            entry["mutually_exclusive"] = [["dry-run", "confirm"]]
            entry["preconditions"] = [
                "running licensed Layout session",
                "explicit known side/protection/identity",
                "unchanged preview state",
                "native placement DRC must enable",
            ]
        else:
            entry["observation_json_schema"] = observation_schema()
        result.append(entry)
    return result
