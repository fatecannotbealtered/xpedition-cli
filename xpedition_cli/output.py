from __future__ import annotations

import json
import re
import sys
import time
from collections.abc import Mapping
from typing import Any

from .contract_gen import SCHEMA_VERSION
from .errors import CLIError
from .field_projection import project_fields as _project_fields

_SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|token|secret|authorization|cookie|api[_-]?key)", re.I
)


def redact(value: Any, key: str = "") -> Any:
    """Redact credential-shaped fields before they cross stdout or stderr."""
    if key != "confirm_token" and _SENSITIVE_KEY.search(key):
        return "<redacted>"
    if isinstance(value, Mapping):
        return {
            str(item_key): redact(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [redact(item, key) for item in value]
    return value


def _duration_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _get_path(value: Any, path: str) -> tuple[bool, Any]:
    current = value
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return False, None
    return True, current


def project_fields(data: Any, fields: str | None) -> Any:
    return _project_fields(data, fields)


def success(data: Any, started: float, fields: str | None = None) -> dict[str, Any]:
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "data": redact(project_fields(data, fields)),
        "meta": {"duration_ms": _duration_ms(started)},
    }


def failure(error: CLIError, started: float) -> dict[str, Any]:
    return {
        "ok": False,
        "schema_version": SCHEMA_VERSION,
        "error": {
            "code": error.code,
            "message": error.message,
            "details": redact(error.details or {}),
            "retryable": error.is_retryable,
        },
        "meta": {"duration_ms": _duration_ms(started)},
    }


def emit(payload: dict[str, Any], output_format: str, compact: bool) -> None:
    if output_format == "json":
        text = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":") if compact else None,
            indent=None if compact else 2,
        )
        sys.stdout.write(text + "\n")
        return
    if output_format == "raw":
        body = payload.get("data") if payload.get("ok") else payload.get("error")
        sys.stdout.write(json.dumps(body, ensure_ascii=False, indent=None if compact else 2) + "\n")
        return
    if payload.get("ok"):
        data = payload.get("data") or {}
        if isinstance(data, dict):
            for key, value in data.items():
                if key == "_untrusted":
                    continue
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False)
                sys.stdout.write(f"{key}: {value}\n")
        else:
            sys.stdout.write(f"{data}\n")
    else:
        error = payload["error"]
        sys.stderr.write(f"{error['code']}: {error['message']}\n")
