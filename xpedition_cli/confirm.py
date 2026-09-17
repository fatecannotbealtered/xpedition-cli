from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import secrets
import time
from pathlib import Path
from typing import Any

from .audit import config_dir
from .confirmation_store import claim, machine_secret
from .errors import CLIError

TOKEN_TTL_SECONDS = 900


def _secret_path() -> Path:
    return config_dir() / "confirm.secret"


def _consumed_path() -> Path:
    return config_dir() / "confirm-consumed.json"


def _machine_secret() -> bytes:
    try:
        return machine_secret(_secret_path())
    except OSError as exc:
        raise CLIError(
            "E_IO",
            "cannot initialize local confirmation secret",
            {"stage": "confirmation_secret", "write_attempted": False},
        ) from exc


def _encode(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    signature = hmac.new(_machine_secret(), raw, hashlib.sha256).hexdigest()
    return f"ct_{body}.{signature}"


def _scope_hash(scope: dict[str, Any]) -> str:
    canonical = json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(canonical).hexdigest()


def _decode(token: str) -> dict[str, Any]:
    try:
        body, signature = token[3:].split(".", 1)
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        # compare_digest rejects non-ASCII text with TypeError, not a CLI error.
        signature_bytes = signature.encode("ascii")
    except (ValueError, IndexError, binascii.Error, UnicodeError) as exc:
        raise CLIError("E_CONFLICT", "invalid confirmation token") from exc
    if body != base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii"):
        raise CLIError("E_CONFLICT", "non-canonical confirmation token")
    expected = hmac.new(_machine_secret(), raw, hashlib.sha256).hexdigest().encode("ascii")
    if not hmac.compare_digest(signature_bytes, expected):
        raise CLIError("E_CONFLICT", "invalid confirmation token")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CLIError("E_CONFLICT", "invalid confirmation token") from exc
    if not isinstance(payload, dict):
        raise CLIError("E_CONFLICT", "invalid confirmation token")
    return payload


def issue(scope: dict[str, Any]) -> tuple[str, str]:
    expires_at = int(time.time()) + TOKEN_TTL_SECONDS
    payload = {
        "scope_hash": _scope_hash(scope),
        "expires_at": expires_at,
        "nonce": secrets.token_hex(8),
    }
    token = _encode(payload)
    return token, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expires_at))


def consume(token: str, scope: dict[str, Any]) -> None:
    if not token or not token.startswith("ct_"):
        raise CLIError(
            "E_CONFIRMATION_REQUIRED", "a confirmation token is required; run --dry-run first"
        )
    payload = _decode(token)
    try:
        expires_at = float(payload.get("expires_at", 0))
    except (TypeError, ValueError, OverflowError) as exc:
        raise CLIError("E_CONFLICT", "invalid confirmation token expiry") from exc
    if not math.isfinite(expires_at) or isinstance(payload.get("expires_at"), bool):
        raise CLIError("E_CONFLICT", "invalid confirmation token expiry")
    if expires_at <= time.time():
        raise CLIError("E_CONFLICT", "confirmation token has expired; run --dry-run again")
    if payload.get("scope_hash") != _scope_hash(scope):
        raise CLIError("E_CONFLICT", "confirmation token does not match this operation")
    fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()
    claim(_consumed_path(), fingerprint, expires_at)
