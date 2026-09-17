"""Apply a source-pinned confirmation patch on its isolated review branch only."""
from pathlib import Path
import hashlib
import os

BRANCH = "codex/confirmation-concurrency-20260918"
if os.environ.get("GITHUB_REF") != f"refs/heads/{BRANCH}":
    raise SystemExit("This bootstrap only runs on its dedicated work branch")
EXPECTED = {
    "xpedition_cli/confirm.py": "f1e0b1d4b874a6dd4819e7bb21e903ce48a2ee76",
    "CHANGELOG.md": "de4e0cacf51a63edd19e6c5d6b51fbda6f0e14e2",
    "skills/xpedition-cli/SKILL.md": "515b8cff14f50af6d16ddc5862345c79b786751f",
    "xpedition_cli/confirmation_store.py": "a14d05c79b0743f1c9518d5d96db04cf26e99cf8",
    "tests/test_confirm_concurrency.py": "b898802606d7436d2f9b4c6b172bbed1d5279f3c",
    "tests/test_confirm_cli.py": "64ce9f4e48d5b994aa9cfaa153792180760811a9",
    "skills/xpedition-cli/reference/confirmation-safety.md": "6fbe132f7c581578f30fc3ea18c9f03e8bd4a155",
}
for name, expected in EXPECTED.items():
    raw = Path(name).read_bytes()
    actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if actual != expected:
        raise SystemExit(f"Refusing to replace changed source: {name} ({actual})")

Path("xpedition_cli/confirm.py").write_text('''from __future__ import annotations

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
''', encoding="utf-8")
raw = Path("xpedition_cli/confirm.py").read_bytes()
actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
if actual != "91a2e548b7f136a8cc09bcb37cbb825ea9d31b39":
    raise SystemExit(f"Patched confirmation source differs from local tested source ({actual})")

def replace_once(path, old, new):
    source = Path(path).read_text(encoding="utf-8")
    if source.count(old) != 1:
        raise SystemExit(f"Expected one anchor in {path}")
    Path(path).write_text(source.replace(old, new), encoding="utf-8")

replace_once("CHANGELOG.md", "## [Unreleased]\n", """## [Unreleased]

### Fixed

- Confirmation consumption now serializes ledger check/update across cooperating
  processes and threads, atomically replaces the ledger and rechecks expiry after
  locking. Concurrent first-use secret creation no longer races; corrupt secrets
  are rejected rather than silently reused or rotated.
- Reject alternate base64 token representations that could evade a consumed-token
  fingerprint, non-ASCII signatures and nonfinite or malformed signed expiry data.
- Preserve the pinned spec's storage-failure degradation, with explicit secret-free
  stderr warnings. Failed replacement preserves the old ledger; unavailable lock
  storage never triggers an unlocked ledger rewrite. Lock contention is a conflict,
  not degradation. This is not a project write lock or exactly-once native execution.
""")
replace_once("skills/xpedition-cli/SKILL.md", "## Agent Defaults\n", """## Agent Defaults

Read `reference/confirmation-safety.md` for confirmation concurrency boundaries.
Storage-degradation warnings mean replay protection is not guaranteed; stop
automatic retries and inspect the environment and observed project state.
""")
print("Applied confirmation patch; canonical contracts and native operations unchanged.")
