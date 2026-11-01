"""Independent package integrity consumer; does not import the export producer."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..errors import CLIError


def verify(options: dict[str, Any]) -> dict[str, Any]:
    suite = str(options.get("suite") or "integrity")
    if suite != "integrity":
        raise CLIError(
            "E_UNSUPPORTED",
            "This consumer implements package byte integrity only.",
            {"suite": suite, "supported": ["integrity"], "manufacturing_sign_off": False},
        )
    root = Path(str(options.get("package") or "")).expanduser().resolve()
    manifest = root / "manifest.json"
    if not manifest.is_file():
        raise CLIError("E_NOT_FOUND", "package manifest.json was not found", {"package": str(root)})
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CLIError("E_VALIDATION", "package manifest is not readable JSON") from error
    entries = data.get("integrity")
    if not isinstance(entries, list) or not entries:
        return {
            "suite": "integrity",
            "status": "incomplete",
            "valid": False,
            "items": [],
            "package": str(root),
            "reason": "This manifest contains no export-time sizes and hashes.",
            "manufacturing_sign_off": False,
            "coverage": ["manifest-readability"],
            "uncovered": ["cam-geometry", "dfm", "fabricator-acceptance"],
            "_untrusted": ["package"],
        }
    items = []
    names = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise CLIError("E_VALIDATION", "manifest integrity items must be objects")
        relative = str(entry.get("path") or "")
        path = (root / relative).resolve()
        if (
            not relative
            or Path(relative).is_absolute()
            or not path.is_relative_to(root)
            or relative in names
        ):
            raise CLIError(
                "E_VALIDATION",
                "manifest contains an escaping, empty or repeated path",
                {"path": relative},
            )
        names.add(relative)
        if not path.is_file():
            items.append({"path": relative, "valid": False, "reason": "missing"})
            continue
        observed = {
            "size": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        expected = {key: entry.get(key) for key in ("size", "sha256")}
        items.append(
            {
                "path": relative,
                "expected": expected,
                "observed": observed,
                "valid": observed == expected,
            }
        )
    valid = all(item["valid"] for item in items)
    return {
        "suite": "integrity",
        "status": "passed" if valid else "failed",
        "valid": valid,
        "package": str(root),
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "items": items,
        "count": len(items),
        "manufacturing_sign_off": False,
        "coverage": ["manifest-path-containment", "file-presence", "file-size", "file-sha256"],
        "uncovered": ["cam-geometry", "dfm", "fabricator-acceptance"],
        "_untrusted": ["package", "items"],
    }
