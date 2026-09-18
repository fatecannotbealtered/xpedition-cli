"""Declarative metadata inventory entry, not an application-operation registry."""

from __future__ import annotations

from typing import Any

OUTPUT_SCHEMA = {
    "shape": "object",
    "fields": [
        "library",
        "selected_type",
        "scope",
        "items",
        "count",
        "offset",
        "next_offset",
        "has_more",
        "total",
        "complete_in_scope",
        "issue_count",
        "issues",
        "issues_truncated",
        "execution",
        "semantic_validation",
        "source",
        "_untrusted",
    ],
    "untrusted_fields": ["library", "selected_type", "items", "issues", "source"],
}


def command() -> dict[str, Any]:
    return {
        "path": "system api-inventory",
        "type": "query",
        "permission_tier": "read",
        "description": (
            "Inspect trusted standalone COM type-library metadata without activating EDA"
        ),
        "output_schema": "api_inventory",
        "blast_radius": "none; metadata inspection only",
        "examples": [
            'xpedition-cli system api-inventory --input "C:/trusted/product.tlb" --compact',
            'xpedition-cli system api-inventory --input "C:/trusted/product.tlb" '
            "--name IExample --limit 20 --compact",
        ],
        "params": [
            {
                "name": "input",
                "type": "path",
                "required": True,
                "multiple": False,
                "description": "Trusted local standalone .tlb/.olb only; 32 MiB maximum",
            },
            {
                "name": "name",
                "type": "string",
                "required": False,
                "multiple": False,
                "description": (
                    "Exact unique type name; select member metadata rather than type headers"
                ),
            },
            {
                "name": "limit",
                "type": "integer",
                "required": False,
                "multiple": False,
                "default": 100,
                "minimum": 1,
                "maximum": 200,
            },
            {
                "name": "offset",
                "type": "integer",
                "required": False,
                "multiple": False,
                "default": 0,
                "minimum": 0,
            },
        ],
        "requirements": ["Windows", "pywin32", "caller-trusted standalone type library"],
        "execution_support": "type_library_metadata_only",
        "native_application_activation": False,
        "registration_requested": False,
        "target_xpedition_validation": "not_performed",
        "metadata_contract": {
            "defaults_and_constants": "not returned",
            "type_descriptors": "raw numeric descriptors, not inferred JSON schemas",
            "completeness": "all type headers or selected member page; see has_more separately",
            "semantic_validation": "never inferred from member names or metadata flags",
            "identity": "file SHA-256, library GUID/LCID/version and type GUID",
        },
    }
