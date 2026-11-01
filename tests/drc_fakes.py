"""Synthetic identity fields for lifecycle-aware DRC tests; no native discovery."""

import json
import os
from pathlib import Path


def write_review_evidence(name, value, fixture_root):
    if not (output := os.environ.get("XPEDITION_DRC_REVIEW_EVIDENCE")):
        return
    text = json.dumps(value, indent=2, ensure_ascii=False)
    text = text.replace(json.dumps(str(fixture_root))[1:-1], "<FIXTURE_ROOT>")
    path = Path(output) / (name + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")


def process_identity(root, *, birth="100000000"):
    return {
        "pid": 4242,
        "executable": str(Path(root) / "fixture-layout.exe"),
        "creation_time_100ns": birth,
    }


def application_identity(board, *, opened=True):
    return {
        "pid": 4242,
        "product_version": "151.0 fixture",
        "type_library": {"guid": "synthetic-only", "major": 151, "minor": 0},
        "public_fields": {
            "FullName": process_identity(Path(board).parent.parent)["executable"],
            "Version": "151.0 fixture",
            "InstanceGuid": "synthetic-generation",
        },
        "document": {"status": "observed", "open": opened, "path": str(board) if opened else None},
    }
