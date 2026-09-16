from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path
from typing import Any

from .audit import config_dir
from .errors import CLIError

TOKEN_TTL_SECONDS = 900


def _secret_path() -> Path:
    return config_dir() / "confirm.secret"


def _consumed_path() -> Path:
    return config_dir() / "confirm-consumed.json"


def _machine_secret() -> bytes:
    path = _secret_path()
    try:
        if path.exists():
            return path.read_bytes()
        value = secrets.token_bytes(32)
        path.write_bytes(value)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return value
    except OSError as exc:
        raise CLIError("E_IO", f"cannot initialize local confirmation secret: {exc}") from exc


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
    except (ValueError, IndexError, binascii.Error) as exc:
        raise CLIError("E_CONFLICT", "invalid confirmation token") from exc
    expected = hmac.new(_machine_secret(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise CLIError("E_CONFLICT", "invalid confirmation token")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CLIError("E_CONFLICT", "invalid confirmation token") from exc
    if not isinstance(payload, dict):
        raise CLIError("E_CONFLICT", "invalid confirmation token")
    return payload


def _prune_consumed() -> dict[str, float]:
    path = _consumed_path()
    try:
        values = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        values = {}
    now = time.time()
    retained: dict[str, float] = {}
    for key, value in values.items():
        try:
            expiry = float(value)
        except (TypeError, ValueError):
            continue
        if expiry > now:
            retained[str(key)] = expiry
    return retained


def _mark_consumed(fingerprint: str, expires_at: float) -> None:
    path = _consumed_path()
    values = _prune_consumed()
    values[fingerprint] = expires_at
    try:
        path.write_text(json.dumps(values, sort_keys=True), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except OSError:
        # The spec permits graceful degradation when the consumed-token ledger
        # cannot be written; the operation still remains HMAC protected.
        return


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
    expires_at = float(payload.get("expires_at", 0))
    if expires_at <= time.time():
        raise CLIError("E_CONFLICT", "confirmation token has expired; run --dry-run again")
    if payload.get("scope_hash") != _scope_hash(scope):
        raise CLIError("E_CONFLICT", "confirmation token does not match this operation")
    fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()
    consumed = _prune_consumed()
    if fingerprint in consumed:
        raise CLIError("E_CONFLICT", "confirmation token already used; run --dry-run again")
    _mark_consumed(fingerprint, expires_at)
