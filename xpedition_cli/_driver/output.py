"""The envelope on stdout (DCLI-001 to DCLI-008, DCLI-010 to DCLI-014).

One JSON envelope per call, compact unless ``--pretty``. ``meta`` holds ``duration_ms`` and,
when not empty, ``notices`` and ``untrusted``. Envelopes keep within the size limits of the
contract: a list page is shortened and says ``has_more``; an oversized error keeps its
codes and drops detail the Agent can fetch with ``operation get``.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .contract import Contract
from .errors import DriverError


@dataclass
class Result:
    """What a command returns. ``untrusted`` holds paths below ``$.data``, such as ``.items[*].title``."""

    data: Any
    untrusted: list[str] = field(default_factory=list)
    offset: int | None = None  # the offset of a list page, so the page can be shortened
    raw: bytes | None = None  # the bytes of a --format raw result


def page(items: list[Any], offset: int, limit: int, total: int | None = None) -> tuple[dict[str, Any], int]:
    """One page of a list (DCLI-006): ``items``, ``count``, ``has_more``, ``total``, ``next_offset``."""
    chunk = items[offset:offset + limit]
    known = len(items) if total is None else total
    out: dict[str, Any] = {"items": chunk, "count": len(chunk), "has_more": offset + len(chunk) < known, "total": known}
    if out["has_more"]:
        out["next_offset"] = offset + len(chunk)
    return out, offset


def dumps(value: Any, pretty: bool = False) -> str:
    if pretty:
        return json.dumps(value, ensure_ascii=False, indent=2)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Envelopes:
    def __init__(self, contract: Contract) -> None:
        self.contract = contract
        self.limits = contract["envelope"]["limits"]

    def success(self, result: Result, notices: list[dict[str, str]], duration_ms: int) -> dict[str, Any]:
        env = {"ok": True, "schema_version": self.contract.schema_version, "data": result.data,
               "meta": self._meta(duration_ms, notices, ["$.data" + p for p in result.untrusted])}
        _resolve_untrusted(env)
        if len(dumps(env)) > self.limits["success_chars"]:
            env = self._shrink_success(env, result)
        return env

    def failure(self, err: DriverError, notices: list[dict[str, str]], duration_ms: int) -> dict[str, Any]:
        spec = self.contract.errors[err.code]
        error = {"code": err.code, "message": err.message, "retryable": spec["retryable"], "details": dict(err.details)}
        env = {"ok": False, "schema_version": self.contract.schema_version, "error": error,
               "meta": self._meta(duration_ms, notices, ["$.error." + p.lstrip(".") for p in err.untrusted])}
        _resolve_untrusted(env)
        limit = self.limits["confirmation_required_chars" if err.code == "E_CONFIRMATION_REQUIRED" else "error_chars"]
        if len(dumps(env)) > limit:
            _shrink_error(env, limit)
            _resolve_untrusted(env)
        return env

    def exit_code(self, env: dict[str, Any]) -> int:
        return 0 if env["ok"] else self.contract.errors[env["error"]["code"]]["exit"]

    @staticmethod
    def _meta(duration_ms: int, notices: list[dict[str, str]], untrusted: list[str]) -> dict[str, Any]:
        meta: dict[str, Any] = {"duration_ms": max(0, int(duration_ms))}
        if notices:
            meta["notices"] = notices
        if untrusted:
            meta["untrusted"] = sorted(set(untrusted))
        return meta

    def _shrink_success(self, env: dict[str, Any], result: Result) -> dict[str, Any]:
        data = env["data"]
        if isinstance(data, dict) and isinstance(data.get("items"), list) and result.offset is not None:
            while data["items"] and len(dumps(env)) > self.limits["success_chars"]:
                data["items"].pop()
                data["count"] = len(data["items"])
                data["has_more"] = True
                data["next_offset"] = result.offset + data["count"]
            _resolve_untrusted(env)
            if data["items"]:
                return env
        raise DriverError(
            "E_USAGE", f"The result is longer than the {self.limits['success_chars']}-character limit of one response.",
            problem="The result is too long for one response.",
            next_action="Ask for fewer fields with --fields, a smaller page with --limit, or the full result in a file with --output.",
        )


def _shrink_error(env: dict[str, Any], limit: int) -> None:
    """Keep the codes and drop the detail that can be fetched again (DCLI-008)."""
    error, details = env["error"], env["error"]["details"]

    def fits() -> bool:
        return len(dumps(env)) <= limit

    preview = details.get("preview")
    if isinstance(preview, dict):
        while preview.get("changes") and not fits():
            preview["changes"].pop()
            preview["changes_truncated"] = True
    for text in ("native_message", "problem"):
        if not fits() and isinstance(details.get(text), str) and len(details[text]) > 300:
            details[text] = details[text][:300] + "…"
    receipt = details.get("receipt")
    if isinstance(receipt, dict) and not fits():
        receipt.pop("notes", None)
        mismatched = set(receipt.get("mismatched", []))
        observed = receipt.get("observed", {})
        if isinstance(observed, dict):
            receipt["observed"] = {k: v for k, v in observed.items() if k in mismatched or any(m.startswith(k) for m in mismatched)}
    batch = details.get("batch")
    if isinstance(batch, dict) and not fits():
        keep = [i for i in batch["items"] if not i.get("ok")] or batch["items"][:1]
        while len(keep) > 1 and not fits():
            keep.pop()
            batch["items"] = keep
        batch["items"] = keep
    if isinstance(details.get("candidates"), list):
        while len(details["candidates"]) > 2 and not fits():
            details["candidates"].pop()
    if not fits() and len(error["message"]) > 300:
        error["message"] = error["message"][:300] + "…"


_STEP = re.compile(r"\.([a-z0-9_]+)|\[([0-9]+|\*)\]")


def _resolve_untrusted(env: dict[str, Any]) -> None:
    """Keep only the untrusted paths that point at something in the envelope."""
    paths = env["meta"].get("untrusted")
    if not paths:
        env["meta"].pop("untrusted", None)
        return
    kept = [p for p in paths if _exists(env, p)]
    if kept:
        env["meta"]["untrusted"] = sorted(set(kept))
    else:
        env["meta"].pop("untrusted", None)


def _exists(root: Any, path: str) -> bool:
    nodes = [root]
    for name, index in _STEP.findall(path[1:]):
        nxt = []
        for node in nodes:
            if name and isinstance(node, dict) and name in node:
                nxt.append(node[name])
            elif index == "*" and isinstance(node, list):
                nxt.extend(node)
            elif index and index != "*" and isinstance(node, list) and int(index) < len(node):
                nxt.append(node[int(index)])
        nodes = nxt
        if not nodes:
            return False
    return True


def render_text(env: dict[str, Any]) -> str:
    """The ``--format text`` form, for people only; it may change between versions (DCLI-002)."""
    lines: list[str] = []
    if env["ok"]:
        _text(env["data"], 0, lines)
    else:
        err = env["error"]
        lines.append(f"error {err['code']}: {err['message']}")
        lines.append(f"next: {err['details'].get('next_action', '')}")
        rest = {k: v for k, v in err["details"].items() if k not in ("next_action",)}
        _text(rest, 1, lines)
    for notice in env["meta"].get("notices", []):
        lines.append(f"[{notice['severity']}] {notice['message']}")
    return "\n".join(lines)


def _text(value: Any, depth: int, lines: list[str]) -> None:
    pad = "  " * depth
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)) and item:
                lines.append(f"{pad}{key}:")
                _text(item, depth + 1, lines)
            else:
                lines.append(f"{pad}{key}: {_scalar(item)}")
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)) and item:
                lines.append(f"{pad}-")
                _text(item, depth + 1, lines)
            else:
                lines.append(f"{pad}- {_scalar(item)}")
    else:
        lines.append(f"{pad}{_scalar(value)}")


def _scalar(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (dict, list)):
        return dumps(value)
    return str(value)
