"""Machine declarations for the read-only pin workflows, not native capabilities."""
from __future__ import annotations

from typing import Any

from .pin_assignment import DEFAULT_PAGE, MAX_ASSIGNMENTS, MAX_PAGE

OUTPUT_SCHEMA = {
    "shape": "object",
    "fields": ["mode", "scope", "project", "revision", "valid", "matches", "items", "count",
               "offset", "next_offset", "has_more", "summary", "issues", "issues_truncated",
               "execution", "source", "_untrusted"],
    "untrusted_fields": ["project", "revision", "items", "issues", "source"],
}


def commands() -> list[dict[str, Any]]:
    result = []
    for verb, description in (
        ("pin-plan", "Plan explicit pin/net assignments against a supplied snapshot, without writing a design"),
        ("pin-check", "Compare requested pin/net assignments with supplied observations, not live native verification"),
    ):
        result.append({
            "path": f"schematic {verb}", "type": "query", "description": description,
            "output_schema": "pin_assignment", "permission_tier": "read", "blast_radius": "none",
            "examples": [f"xpedition-cli schematic {verb} --input ./snapshot.json --file ./pins.csv --compact"],
            "params": [
                {"name": "input", "type": "path", "required": True, "multiple": False,
                 "description": "Saved snapshot object or successful 1.0 JSON envelope; project, revision, components required. No defaults or native refresh."},
                {"name": "file", "type": "path", "required": True, "multiple": False,
                 "input_contract": {"format": "csv", "encoding": "utf-8 (BOM accepted)",
                    "required_columns": ["refdes", "pin", "net"], "optional_columns": ["expected_net"],
                    "additional_columns": False, "unique_by": ["refdes", "pin"],
                    "identifiers": "exact strings; no integer conversion or whitespace normalization",
                    "maximum_rows": MAX_ASSIGNMENTS,
                    "expected_net": "When column exists, blank means require explicit unconnected state for planning; ignored by pin-check, which checks desired net."}},
                {"name": "limit", "type": "integer", "required": False, "multiple": False,
                 "default": DEFAULT_PAGE, "minimum": 1, "maximum": MAX_PAGE},
                {"name": "offset", "type": "integer", "required": False, "multiple": False,
                 "default": 0, "minimum": 0},
            ],
            "execution_support": "offline_observation_only",
            "native_execution": False, "live_smoke_status": "not_applicable_to_offline_comparison",
            "observation_contract": {
                "pin_identity": "components[].refdes + pins[].number; exact strings, ambiguous targets blocked",
                "connected": "pin.net string and/or positive connections[].net / pins[] evidence",
                "unconnected": "explicit pin.net=null only; absence is unknown",
                "conflicting_evidence": "blocked, never choose the first source",
                "scope": "requested pins only, not whole design or electrical validity",
                "results": "valid=false is an assessment, not a transport error; check matches=null when only unknowns prevent a decision",
            },
        })
    return result
