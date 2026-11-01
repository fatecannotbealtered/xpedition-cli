"""Public SDK runtime. The native adapter, not the SDK, establishes application facts."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import stat
import sys
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path, PureWindowsPath
from typing import Any

from . import __version__, resources
from ._vendor.tool_driver_sdk import DriverCLI
from ._vendor.tool_driver_sdk import DriverError as SDKDriverError
from ._vendor.tool_driver_sdk.runtime import Output
from ._vendor.tool_driver_sdk.validation import ContractValidator
from .audit import config_dir
from .backends import NativeBackend
from .cli.registry import _handler_module, by_path
from .designer_driver_routing import (
    current_prepared_close,
    previous_start_observation,
)
from .designer_driver_routing import (
    routed as _designer_routed,
)
from .errors import CLIError
from .native_identity import adapter_identity, diagnostic_content_request
from .operation_budget import (
    checkpoint,
    current_budget,
    operation_request,
    operation_stage,
    profile_for,
)
from .pdf_source_identity import same_project_path as _pdf_same_project_path

VENDOR = Path(__file__).resolve().parent / "_vendor/spec"
_PCB_REQUEST = ContextVar("xpedition_pcb_request", default=None)
HUMAN_ACTIONS = json.loads((VENDOR / "schemas/common.schema.json").read_text(encoding="utf-8"))[
    "$defs"
]["human_action"]["enum"]


class DriverError(SDKDriverError):
    """Add product state-conflict semantics required by the pinned error schema."""

    def __init__(self, code, message, *, details=None, **kwargs):
        details = dict(details or {})
        if code == "E_CONFLICT":
            details.setdefault("conflict", "state_drift")
        if code in {"E_USAGE", "E_VALIDATION"}:
            details.setdefault("problem", "invalid_value")
        if code != "E_HUMAN_REQUIRED" and "action" in details:
            # Native action names are COM evidence, not human-action enum values.
            details["native_action"] = details.pop("action")
        if code == "E_HUMAN_REQUIRED" and details.get("action") not in (
            None,
            *HUMAN_ACTIONS,
        ):
            # Native handlers historically used action for prose. Preserve that
            # evidence while the public field uses the pinned schema vocabulary.
            details["action_instructions"] = details["action"]
            details["action"] = "other"
            details.setdefault("next_action", details["action_instructions"])
            details["_untrusted"] = [
                *details.get("_untrusted", []),
                "action_instructions",
            ]
        if code == "E_HUMAN_REQUIRED":
            details.setdefault("action", "other")
            details.setdefault("resume", "xpedition-cli session inspect")
            details.setdefault(
                "next_action", "Complete the human step, then inspect; do not replay."
            )
        super().__init__(code, message, details=details, **kwargs)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _disk(path: Path, *, allow_unreadable: bool = False) -> dict[str, Any]:
    checkpoint()
    if not path.exists():
        return {"path": str(path), "exists": False}
    if path.is_symlink() or getattr(path.stat(), "st_file_attributes", 0) & getattr(
        stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
    ):
        raise DriverError(
            "E_VALIDATION",
            "State observation does not follow reparse points.",
            dispatch_state="not_started",
        )
    if path.is_file():
        digest = hashlib.sha256()
        try:
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    checkpoint()
                    digest.update(chunk)
        except PermissionError as error:
            if not allow_unreadable:
                raise
            info = path.stat()
            return {
                "path": str(path),
                "exists": True,
                "sha256": None,
                "size": info.st_size,
                "mtime_ns": info.st_mtime_ns,
                "read_error": type(error).__name__,
                "content_observed": False,
            }
        return {
            "path": str(path),
            "exists": True,
            "sha256": digest.hexdigest(),
            "size": path.stat().st_size,
        }
    entries = []
    for current, folders, files in os.walk(path, followlinks=False):
        checkpoint()
        for name in folders:
            child = Path(current) / name
            if child.is_symlink() or getattr(child.stat(), "st_file_attributes", 0) & getattr(
                stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
            ):
                raise DriverError(
                    "E_VALIDATION",
                    "State observation does not follow directory junctions.",
                    dispatch_state="not_started",
                )
        entries.extend(
            _disk(Path(current) / name, allow_unreadable=allow_unreadable) for name in sorted(files)
        )
        if len(entries) > 10000:
            raise DriverError(
                "E_VALIDATION",
                "Target observation exceeds the 10000-file bound; narrow the target.",
                dispatch_state="not_started",
            )
    return {
        "path": str(path),
        "exists": True,
        "files": [
            {**item, "path": str(Path(item["path"]).relative_to(path))}
            for item in sorted(entries, key=lambda item: item["path"])
        ],
    }


def _stable(value: Any, *, after_dispatch: bool = False) -> Any:
    if isinstance(value, dict):
        from .native_snapshot_revision import canonical, recognized

        if recognized(value):
            try:
                value = canonical(value)
            except (KeyError, TypeError, ValueError) as error:
                raise DriverError(
                    "E_NATIVE_FAILURE",
                    "Native snapshot stable identities are missing or ambiguous.",
                    details={
                        "cause_code": "E_VERIFY_INCOMPLETE",
                        "native_snapshot_identity_incomplete": True,
                        "reason": str(error),
                        **(
                            {}
                            if after_dispatch
                            else {"write_attempted": False, "native_query_only": True}
                        ),
                    },
                    dispatch_state="dispatched" if after_dispatch else "not_started",
                ) from error
        transient = {"observed_at", "snapshot_id", "duration_ms"}
        if value.get("identity_basis") == "frozen_executable_bytes":
            # One-file extraction directories vary per call; executable path/hash bind the build.
            transient.add("module_path")
        if "all_pin_pad_material" in value and "native_trace_material" in value:
            # Scratch-mask allocation/cleanup receipts are derived evidence,
            # independently validated on fresh reads. Exact raw source geometry
            # remains in the revision; temporary mask names are not design IDs.
            transient.add("native_mask_proof")
        return {
            key: _stable(item, after_dispatch=after_dispatch)
            for key, item in value.items()
            if key not in transient
        }
    if isinstance(value, list):
        return [_stable(item, after_dispatch=after_dispatch) for item in value]
    return value


_DIFF_SCHEMA_KEYS = frozenset(
    "adapter inputs application worker_adapter native project_directory artifact_observations "
    "components unnamed_components pins nets connections sheets metadata attributes "
    "attribute_display attribute_display_coverage display name value uid refdes number net "
    "x y font size origin visible "
    "rotation height width path files sha256 exists bytes project revision backend source unit "
    "enumeration_complete errors truncated attribute_count sheet side color style".split()
)
_DIFF_NUMERIC_KEYS = frozenset("x y rotation height width size origin visible font color".split())
_DIFF_SECRET_PARTS = (
    "scope",
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "epoch",
    "objref",
    "journal",
    "runtime",
    "license",
    "authorization",
    "handoff",
    "key",
    "owner_ref",
    "capsule",
    "capability",
    "binding",
)


def _revision_field_diagnostic(before, current, *, max_paths=64, max_nodes=10000):
    """Bounded diagnostic copy only; never change revision material or permission."""
    missing = object()
    nodes, redacted = 0, []

    def secret(key):
        return any(part in key.casefold() for part in _DIFF_SECRET_PARTS)

    def segment(key):
        return (
            key
            if key in _DIFF_SCHEMA_KEYS
            else "field#" + hashlib.sha256(key.encode("utf-8", "surrogatepass")).hexdigest()[:12]
        )

    def copy_safe(value, path):
        nonlocal nodes
        nodes += 1
        if nodes > max_nodes:
            raise OverflowError
        if isinstance(value, dict):
            result = {}
            if len(value) > max_nodes - nodes:
                raise OverflowError
            for key, item in value.items():
                if type(key) is not str:
                    raise ValueError
                if secret(key):
                    redacted.append(path + ".<redacted_binding>")
                    continue
                child = path + "." + segment(key)
                if (
                    key == "attribute_display"
                    and isinstance(item, list)
                    and any(
                        isinstance(row, dict)
                        and type(row.get("name")) is str
                        and secret(row["name"])
                        for row in item
                    )
                ):
                    redacted.append(child)
                    continue
                result[key] = copy_safe(item, child)
            return result
        if isinstance(value, list):
            if len(value) > max_nodes - nodes:
                raise OverflowError
            return [copy_safe(item, path + "[]") for item in value]
        if value is None or type(value) in (str, int, float, bool):
            if type(value) is float and not math.isfinite(value):
                raise ValueError("nonfinite_diagnostic_value")
            return value
        raise ValueError

    def summary(value, field):
        if value is missing:
            return {"type": "missing"}
        result = {"type": type(value).__name__}
        if isinstance(value, (dict, list, str)):
            result["length"] = len(value)
        if type(value) in (int, float, bool) and field in _DIFF_NUMERIC_KEYS:
            result["value"] = value
        elif type(value) is str:
            result["sha256"] = hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()
        elif value is not None and type(value) in (int, float, bool):
            result["sha256"] = _digest(value)
        return result

    rows, limited = [], False

    def changed(a, b, path, field):
        nonlocal limited
        if len(rows) >= max_paths:
            limited = True
            return
        rows.append({"path": path, "before": summary(a, field), "current": summary(b, field)})

    def walk(a, b, path="$", field=""):
        nonlocal nodes, limited
        nodes += 1
        if nodes > max_nodes or len(rows) >= max_paths:
            limited = True
            return
        if type(a) is not type(b):
            changed(a, b, path, field)
        elif isinstance(a, dict):
            for key in sorted(set(a) | set(b)):
                walk(
                    a.get(key, missing),
                    b.get(key, missing),
                    path + "." + segment(key),
                    key,
                )
                if limited:
                    break
        elif isinstance(a, list):
            identity = {
                "components": "uid",
                "unnamed_components": "uid",
                "pins": "number",
                "attribute_display": "name",
                "nets": "name",
                "sheets": "number",
                "connections": "net",
                "files": "path",
                "inputs": "path",
            }.get(field)
            if identity and all(isinstance(row, dict) and identity in row for row in [*a, *b]):
                first = {str(row[identity]): row for row in a}
                second = {str(row[identity]): row for row in b}
                if len(first) != len(a) or len(second) != len(b):
                    raise ValueError
                for key in sorted(set(first) | set(second)):
                    label = (
                        "id#"
                        + hashlib.sha256(key.encode("utf-8", "surrogatepass")).hexdigest()[:12]
                    )
                    walk(
                        first.get(key, missing),
                        second.get(key, missing),
                        path + "[" + label + "]",
                        "",
                    )
                    if limited:
                        break
            else:
                for index in range(max(len(a), len(b))):
                    walk(
                        a[index] if index < len(a) else missing,
                        b[index] if index < len(b) else missing,
                        path + "[" + str(index) + "]",
                        "",
                    )
                    if limited:
                        break
        elif a != b:
            changed(a, b, path, field)

    try:
        first = _stable(copy_safe({k: v for k, v in before.items() if k != "native_logs"}, "$"))
        second = _stable(copy_safe({k: v for k, v in current.items() if k != "native_logs"}, "$"))
        walk(first, second)
        return {
            "status": "truncated" if limited else "available",
            "changed_paths": rows,
            "redacted_bindings": sorted(set(redacted))[:64],
            "nodes_examined": min(nodes, max_nodes),
            "limits": {"paths": max_paths, "nodes": max_nodes},
        }
    except OverflowError:
        return {
            "status": "truncated",
            "changed_paths": rows,
            "redacted_bindings": sorted(set(redacted))[:64],
            "nodes_examined": max_nodes,
            "limits": {"paths": max_paths, "nodes": max_nodes},
        }


def _matched_revision_diagnostic(witness, record, details, request_token):
    if (
        not isinstance(witness, dict)
        or witness.get("request_token") is not request_token
        or request_token is None
    ):
        raise ValueError("revision_witness_unavailable")
    if not isinstance(record, dict):
        raise ValueError("revision_plan_unavailable")
    plan = record.get("plan") or {}
    source = witness.get("source_operation_id")
    snapshot = witness.get("snapshot") or {}
    expected = (details.get("expected") or {}).get("revision")
    if (
        not isinstance(source, str)
        or not source.startswith("op-")
        or not isinstance(expected, str)
        or not re.fullmatch(r"[a-f0-9]{64}", expected)
        or not isinstance(snapshot.get("revision"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", snapshot["revision"])
        or record.get("status") != "planned"
        or record.get("operation_id") != source
        or plan.get("operation_id") != source
        or plan.get("plan_id") != details.get("plan_id")
        or _digest(plan.get("target")) != witness.get("target_digest")
        or (plan.get("base_snapshot") or {}).get("revision") != expected
        or snapshot.get("revision") == expected
    ):
        raise ValueError("revision_witness_does_not_match_plan")
    result = _revision_field_diagnostic(
        plan["base_snapshot"]["extensions"]["facts"], snapshot["extensions"]["facts"]
    )
    result.update(
        expected_revision=expected,
        current_revision=snapshot["revision"],
        source="already_captured_confirm_observation",
    )
    return result


def _project_files(path: Path, state_dirs=None) -> dict[str, Any]:
    """Native logs are observed separately from the design revision guard."""
    data = _disk(path, allow_unreadable=True)
    own_states = []
    for directory in state_dirs or [config_dir()]:
        try:
            relative = Path(directory).resolve().relative_to(path.resolve()).parts
            if relative:
                own_states.append(tuple(part.lower() for part in relative))
        except ValueError:
            pass
    logs, design = [], []
    for item in data.get("files", []):
        parts = tuple(part.lower() for part in PureWindowsPath(item["path"]).parts)
        transient = (parts and parts[0] in {"logfiles", "projectbackup", "thumbnail"}) or (
            len(parts) >= 3 and parts[:3] == ("database", "cdbsvr", "log")
        )
        transient = transient or any(parts[: len(prefix)] == prefix for prefix in own_states)
        (logs if transient else design).append(item)
    return {**data, "files": design, "native_logs": logs}


@contextmanager
def _observe_only():
    previous = os.environ.get("XPEDITION_OBSERVE_ONLY")
    os.environ["XPEDITION_OBSERVE_ONLY"] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("XPEDITION_OBSERVE_ONLY", None)
        else:
            os.environ["XPEDITION_OBSERVE_ONLY"] = previous


@contextmanager
def _native_binding(target: dict):
    extension = target.get("extensions") or {}
    bootstrap = bool(extension.get("host_bootstrap_request"))
    application = extension.get("application") or {}
    values = {
        "XPEDITION_EXPECTED_PID": str(application["pid"]) if application.get("pid") else None,
        "XPEDITION_EXPECTED_DOMAIN": None if bootstrap else extension.get("domain"),
        "XPEDITION_REQUIRE_EXISTING": "1" if application or bootstrap else None,
        "XPEDITION_EXPECTED_ADAPTER_BUILD": (
            (target.get("extensions") or {}).get("worker_adapter") or {}
        ).get("build_sha256"),
        **{
            f"XPEDITION_EXPECTED_{domain.upper()}_PID": str(identity["pid"])
            for domain, identity in target.get("extensions", {})
            .get("secondary_applications", {})
            .items()
        },
    }
    saved = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is not None:
                os.environ[key] = value
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _options(call) -> dict[str, Any]:
    options = {
        **{
            key.replace("-", "_"): ",".join(map(str, value)) if isinstance(value, list) else value
            for key, value in call.params.items()
        },
        "dangerous": call.dangerous,
        "_command": call.command,
        "_check_domain": call.command.split()[0],
        "quiet": True,
    }
    if call.command == "session close" and options.get("save_project"):
        # The public flag explicitly adds persist; one canonical project path
        # must also participate in target binding and source observation.
        options["project"] = options["save_project"]
    if call.command == "schematic export" and options.get("project") and not options.get("output"):
        options["output"] = str(
            Path(str(options["project"])).expanduser().resolve().with_suffix(".pdf")
        )
    return options


def _pdf_source_complete(snapshot: dict, project: str) -> bool:
    """Require complete captured identities/pins, without claiming design correctness."""
    facts = snapshot.get("extensions", {}).get("facts", {})
    application, native = facts.get("application") or {}, facts.get("native")
    if not isinstance(application, dict):
        return False
    document = application.get("document") or {}
    if not isinstance(document, dict):
        return False
    metadata = native.get("metadata") if isinstance(native, dict) else None
    if (
        type(application.get("pid")) is not int
        or application["pid"] <= 0
        or document.get("open") is not True
        or document.get("status") != "observed"
        or not _pdf_same_project_path(document.get("path"), project)
        or not isinstance(native, dict)
        or not isinstance(metadata, dict)
        or metadata.get("backend") != "native_xpedition"
        or metadata.get("source") != "Viewdraw.Application"
        or any(
            not isinstance(native.get(key), list)
            for key in (
                "components",
                "unnamed_components",
                "sheets",
                "nets",
                "connections",
            )
        )
        or not native["sheets"]
        or metadata.get("truncated") is True
        or metadata.get("complete") is False
    ):
        return False
    identities = set()
    for record in [*native["components"], *native["unnamed_components"]]:
        if not isinstance(record, dict):
            return False
        uid = record.get("uid")
        if (
            not isinstance(uid, str)
            or not uid
            or uid in identities
            or not isinstance(record.get("pins"), list)
        ):
            return False
        identities.add(uid)
        for key in ("identity_observation_coverage", "pin_observation_coverage"):
            coverage = record.get(key) or {}
            if coverage.get("complete") is not True or coverage.get("errors") != []:
                return False
    return True


def _owned_process_identity(domain: str, pid: str) -> dict:
    from .native_process import process_identity

    try:
        return process_identity(domain, pid)
    except (OSError, ValueError) as error:
        raise DriverError(
            "E_CONFLICT",
            "The exact native process generation could not be independently identified.",
            details={
                "write_attempted": False,
                "native_query_only": True,
                "identity_error": type(error).__name__,
            },
            dispatch_state="not_started",
        ) from error


def _budgeted(mode):
    """Direct adapter callers and the public SDK share identical finite budgets."""

    def decorate(function):
        @wraps(function)
        def wrapped(self, call, *args, **kwargs):
            command = (
                call.get("extensions", {}).get("command", "other")
                if isinstance(call, dict)
                else call.command
            )
            with operation_request(command, mode) as budget:
                try:
                    result = function(self, call, *args, **kwargs)
                except SDKDriverError as error:
                    error.details["operation_budget"] = budget.report()
                    operation_id = (
                        call.get("extensions", {}).get("source_operation_id")
                        if isinstance(call, dict)
                        else call.operation_id
                    )
                    if operation_id and mode == "confirm":
                        error.details.setdefault("operation_id", operation_id)
                        error.details.setdefault(
                            "lookup_argv", ["operation", "show", "--id", operation_id]
                        )
                    if isinstance(error.receipt, dict):
                        error.receipt.setdefault("extensions", {})["operation_budget"] = (
                            budget.report()
                        )
                    raise
                except CLIError as error:
                    raise DriverError(
                        error.code,
                        error.message,
                        details={
                            **(error.details or {}),
                            "operation_budget": budget.report(),
                        },
                        dispatch_state="dispatched" if budget.dispatched else "not_started",
                    ) from error
                if isinstance(result, dict):
                    result.setdefault("extensions", {})["operation_budget"] = budget.report()
                return result

        return wrapped

    return decorate


def _backup_release_progress(result, cause):
    """Keep real release stages even when no archive/result could be returned."""
    details = (cause.details or {}) if cause else {}
    backup = details.get("backup") if isinstance(details.get("backup"), dict) else {}
    release = (result or {}).get("release") or details.get("release") or backup.get("release") or {}
    return details.get("release_progress") or release.get("release_progress") or {}


def _save_acknowledged(command, known):
    """A received Save fact, independent of persistence/reopen qualification."""
    if command == "pcb checks run":
        return (
            known.get("board_save_acknowledged") is True
            or (known.get("board_lifecycle") or {}).get("save_acknowledged") is True
        )
    return known.get("saved") is True


def _drc_failure_evidence(error):
    """Keep received facts through bounded CLI cause wrappers, without granting authority."""
    node = {"code": error.code, "details": error.details}
    result, uncertain = None, False
    for _ in range(4):
        uncertain = uncertain or node.get("code") in {"E_TIMEOUT", "E_OUTCOME_UNKNOWN"}
        details = node.get("details") or {}
        if not isinstance(details, dict):
            return result, True
        if result is None and isinstance(details.get("native_result"), dict):
            result = details["native_result"]
        cause = details.get("cause")
        if cause is None:
            return result, uncertain
        if not isinstance(cause, dict) or not isinstance(cause.get("code"), str):
            return result, True
        node = cause
    return result, True  # An incomplete error chain is not positive terminal evidence.


def _unobserved_receipt(call, result, cause, error, *, persistence_unverified=False):
    """Retain returned native facts when the *subsequent* observer is unavailable.

    This is not a substitute for task verification. A Save acknowledgement alone
    remains unknown persistence; a native save/reopen result is kept separately
    from the independent observer which failed. No native request is repeated.
    """
    known = result if isinstance(result, dict) else {}
    persisted = "persist" in call.effects and known.get("persistence_verified") is True
    applied = "change" in call.effects and (
        known.get("applied") is True or known.get("outcome") in {"annotated", "annotated_on_open"}
    )
    change = known.get("change_status")
    if change not in {"not_applied", "applied", "partial", "unknown"}:
        change = "applied" if applied else "unknown"
    persistence = known.get("persistence_state")
    if persistence not in {"unchanged", "unsaved", "saved", "partial", "unknown"}:
        persistence = (
            "saved" if persisted else "unknown" if "persist" in call.effects else "unchanged"
        )
    if persistence_unverified and "persist" in call.effects:
        persistence = "unknown"
    release_progress = (
        _backup_release_progress(known, cause) if call.command == "project backup" else {}
    )
    release_saves = [
        item
        for item in release_progress.values()
        if isinstance(item, dict) and item.get("save_count")
    ]
    if release_saves:
        persistence = "unknown"
    reason = (
        "Independent observation failed after the native handler; inspect before further writes."
    )
    snapshot = None
    if known:
        revision = _digest(known)
        snapshot = {
            "id": "snapshot-" + revision[:24],
            "source": "live",
            "revision": revision,
            "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "dependencies": [],
            "extensions": {
                "native_handler_result": known,
                "scope": "returned native getters; subsequent independent readback unavailable",
            },
        }
    return {
        "snapshot_after": snapshot,
        "outcome": {
            "execution": "completed"
            if result is not None and not cause
            else "failed"
            if result is not None
            else "unknown",
            "change": change,
            "persistence": persistence,
            "delivery": "not_applicable",
            "verification": {
                "status": "incomplete",
                "scope": "post-dispatch independent target observation",
                "snapshot_id": None,
                "coverage": "none",
                "checks": [],
                "not_checked": [{"id": "target-readback", "required": True, "reason": reason}],
                "evidence_refs": [],
                "reason": reason,
            },
        },
        "failed_stage": "verify",
        "changed_items": [],
        "artifacts": [],
        "extensions": {
            "result": result,
            "handler_error": {"code": cause.code, "details": cause.details} if cause else None,
            "readback_error": {
                "type": type(error).__name__,
                "code": getattr(error, "code", None),
            },
            "save_acknowledged": (
                any(item.get("save_acknowledged") is True for item in release_saves)
                if release_saves
                else _save_acknowledged(call.command, known)
            ),
            **({"project_release_progress": release_progress} if release_progress else {}),
            "native_reopen_reported": known.get("persistence_verified") is True,
            "independent_readback_completed": False,
        },
        "recovery": {"action": "inspect", "supported": True, "reason": reason},
        "_untrusted": ["extensions"],
    }


def _owner_terminal_not_observed(error, operation_id):
    """Only suppress a new CAD read; this predicate grants no native authority."""
    if (
        not isinstance(operation_id, str)
        or not re.fullmatch(r"op-[A-Za-z0-9_-]{1,100}", operation_id)
        or not isinstance(error, CLIError)
        or error.code != "E_OUTCOME_UNKNOWN"
        or error.message
        != "The original owner command has no observed terminal result; do not replay"
        or type(error.details) is not dict
    ):
        return False
    details = error.details
    if (
        details.get("sdk_operation_id") != operation_id
        or not re.fullmatch(r"owner-command-[a-f0-9]{32}", str(details.get("owner_command_id")))
        or details.get("request_replayed") is not False
        or details.get("write_attempted") is not True
        or "native_request_dispatched" not in details
        or details.get("native_request_dispatched") is not None
        or "owner_command_terminal_phase" in details
        or "native_failure" in details
    ):
        return False
    try:
        from .designer_owner_client import validate_owner_ref

        validate_owner_ref(details.get("owner_ref"))
    except (CLIError, ValueError, TypeError):
        return False
    return True


def _handler(command):
    module, _, function = command.handler.partition(":")
    return getattr(_handler_module(module), function)


def _require_designer_host_execution():
    from .backends.native_xpedition import require_designer_host_execution

    try:
        require_designer_host_execution()
    except CLIError as error:
        raise DriverError(
            error.code,
            error.message,
            details=error.details,
            dispatch_state="not_started",
        ) from error


DESIGNER_START_ROUTE = "owned-empty-host-handoff-v1"


def _require_dedicated_designer(command=None, options=None, target=None):
    """Only the new empty-session intent may reach the confirmed owner route.

    This is intent selection, not native eligibility. The confirmed handler
    still requires a typed SDK producer, scope, a fresh owner and host handoff.
    Old signed plans and running-instance attachment retain their refusal.
    """
    options = options or {}
    extension = (target or {}).get("extensions") or {}
    selected = command or extension.get("command")
    domain = options.get("kind") or options.get("domain") or extension.get("domain")
    if selected == "session attach" and (
        options.get("project_source_operation_id")
        or options.get("project-source-operation-id")
        or extension.get("designer_project_source")
    ):
        from .designer_project_admission import validate_intent

        validate_intent(options, target)
        return
    if (
        selected == "session start"
        and domain == "schematic"
        and not options.get("project")
        and not extension.get("project")
        and (
            target is None
            or (
                extension.get("designer_startup_route") == DESIGNER_START_ROUTE
                and isinstance(extension.get("designer_startup_generation"), str)
                and extension["designer_startup_generation"] == extension.get("source_operation_id")
            )
        )
    ):
        return
    from .designer_startup import require_dedicated_designer

    try:
        require_dedicated_designer()
    except CLIError as error:
        raise DriverError(
            error.code,
            error.message,
            details=error.details,
            dispatch_state="not_started",
        ) from error


def _dedicated_designer_request(command, options=None, target=None):
    """Recognize old start/attach intents without observing their native target."""
    options, target = options or {}, target or {}
    extension = target.get("extensions") or {}
    return command in {"session start", "session attach"} and (
        options.get("kind") == "schematic"
        or options.get("domain") == "schematic"
        or extension.get("domain") == "schematic"
        or target.get("engine_id") == "xpedition-schematic"
    )


def _project_creation_readback(result, options, plan, after):
    """Native reopen, fresh identity getters and the project file must agree."""
    from .project_file import template_state

    path = Path(str(options["project"])).resolve()
    template = plan["extensions"]["legacy_preview"].get("template_state") or {}
    expected_designs = (
        (template.get("designs") or [])
        if template.get("initialized")
        else [str(options.get("design") or "Board1")]
    )
    actual = after["extensions"]["facts"].get("application", {}).get("document") or {}
    disk = template_state(path) if path.is_file() else {"initialized": False, "designs": []}
    source = (result or {}).get("verification") or {}
    source_before = (
        ((plan.get("base_snapshot") or {}).get("extensions") or {}).get("facts") or {}
    ).get("designer_setup_source")
    source_after = (after.get("extensions", {}).get("facts") or {}).get("designer_setup_source")
    source_unchanged = source_after == source_before if source_before is not None else None
    valid = bool(
        (result or {}).get("persistence_verified") is True
        and source.get("source") == "native_getters_after_save_close_open"
        and actual.get("open") is True
        and actual.get("path")
        and Path(actual["path"]).resolve() == path
        and actual.get("designs") == expected_designs
        and disk["initialized"]
        and disk["designs"] == expected_designs
        and source_unchanged is not False
    )
    return {
        "valid": valid,
        "expected": {"path": str(path), "designs": expected_designs},
        "observed": {"native": actual, "file": disk},
        "source": "fresh native identity getters and project file after adapter save/close/open",
        "setup_source_unchanged": source_unchanged,
        "not_checked": ["full_template_object_equivalence"],
    }


def _schematic_build_readback(result, plan, after):
    from .native_com_adapter import _compare_netlist

    expected = plan["extensions"]["legacy_preview"].get("verification_input")
    actual = after["extensions"]["facts"].get("native") or {}
    comparison = _compare_netlist(actual, expected) if expected else {"matches": False}
    present = [str(row.get("refdes")) for row in actual.get("components", [])]
    valid = bool(
        expected
        and result.get("persistence_verified") is True
        and result.get("verification_source") == "native_getters_after_sheet_save_close_open"
        and comparison["matches"]
        and sorted(present) == sorted(expected["parts"])
        and all(
            row.get("internal_part_no") == expected.get("part_numbers", {}).get(row["refdes"])
            for row in actual.get("components", [])
            if row["refdes"] in expected.get("part_numbers", {})
        )
        and not any(row.get("internal_part_no") for row in actual.get("unnamed_components", []))
    )
    return {
        "valid": valid,
        "expected": expected,
        "observed": comparison,
        "source": "fresh component/pin/net getters after independently saved/reopened sheets",
        "not_checked": ["visual layout", "native ERC", "PCB synchronization"],
    }


def _schematic_save_metadata_binding(native, preview):
    """Accept only the preview's exact independently observed metadata allowance."""
    from .schematic_attribute_display import freeze_save_metadata

    try:
        expected = freeze_save_metadata(native)
    except CLIError as error:
        raise DriverError(
            error.code,
            error.message,
            details={**(error.details or {}), "write_attempted": False},
            dispatch_state="not_started",
        ) from error
    actual = preview.get("save_metadata")
    if _digest(actual) != _digest(expected):
        raise DriverError(
            "E_CONFLICT",
            "The preview's native metadata binding differs from the independently observed design.",
            details={
                "write_attempted": False,
                "expected_save_metadata": expected,
                "preview_save_metadata": actual,
                "_untrusted": ["expected_save_metadata", "preview_save_metadata"],
            },
            dispatch_state="not_started",
        )
    return expected


def _schematic_display_persistence_readback(
    result, options, before, after, operations, *, save_metadata=None
):
    """Corroborate native lifecycle counts with independently observed project getters."""
    from .native_process import same_identity
    from .schematic_attribute_display import verify_display_changes

    known = result if isinstance(result, dict) else {}
    lifecycle = known.get("lifecycle")
    lifecycle = lifecycle if isinstance(lifecycle, dict) else {}
    path = Path(str(options["project"])).resolve()
    host_source = "dedicated_startup_host_Application_and_authenticated_ncalrpc"

    def mapping(value):
        return value if isinstance(value, dict) else {}

    applications = [
        mapping(snapshot["extensions"]["facts"].get("application")) for snapshot in (before, after)
    ]
    raw_documents = [
        *(application.get("document") for application in applications),
        lifecycle.get("before_identity"),
        lifecycle.get("reopened_identity"),
    ]
    dedicated = any(mapping(value).get("source") == host_source for value in raw_documents) or any(
        "dedicated_startup_binding" in application
        or mapping(application.get("pid_source")).get("property") == host_source
        for application in applications
    )
    identity_source = host_source if dedicated else "IsProjectOpened/GetProjectData public getters"
    bindings = [
        mapping(application.get("dedicated_startup_binding")) for application in applications
    ]
    pid_sources = [mapping(application.get("pid_source")) for application in applications]
    generations = [mapping(source.get("matching_process")) for source in pid_sources]
    host_complete = all(
        binding.get("source") == host_source
        and source.get("property") == host_source
        and type(binding.get("canonical_stable")) is bool
        and type(binding.get("original_handle_rechecked")) is bool
        and isinstance(binding.get("connection_epoch_sha256"), str)
        and re.fullmatch(r"[a-f0-9]{64}", binding["connection_epoch_sha256"]) is not None
        for binding, source in zip(bindings, pid_sources, strict=True)
    )
    host_verified = bool(
        host_complete
        and all(
            binding["canonical_stable"] is True and binding["original_handle_rechecked"] is True
            for binding in bindings
        )
        and bindings[0]["connection_epoch_sha256"] == bindings[1]["connection_epoch_sha256"]
    )
    generation_complete = all(
        type(generation.get("pid")) is int
        and generation["pid"] > 0
        and type(generation.get("alive")) is bool
        and type(generation.get("birth_filetime_100ns")) is str
        and generation["birth_filetime_100ns"].isdecimal()
        and int(generation["birth_filetime_100ns"]) > 0
        and isinstance(generation.get("executable"), str)
        and bool(generation["executable"])
        and (
            PureWindowsPath(generation["executable"]).is_absolute()
            or Path(generation["executable"]).is_absolute()
        )
        for generation in generations
    )
    generation_identities = [
        {
            "pid": generation.get("pid"),
            "executable": generation.get("executable"),
            "creation_time_100ns": generation.get("birth_filetime_100ns"),
        }
        for generation in generations
    ]
    generation_verified = bool(
        generation_complete
        and all(generation["alive"] is True for generation in generations)
        and same_identity(*generation_identities)
        and all(
            generation["pid"] == application.get("pid")
            for generation, application in zip(generations, applications, strict=True)
        )
    )

    def document(value):
        if not isinstance(value, dict):
            return None
        designs = value.get("designs")
        if (
            value.get("open") is not True
            or value.get("status") != "observed"
            or value.get("source") != identity_source
            or not isinstance(value.get("path"), str)
            or not value["path"]
            or not isinstance(designs, list)
            or not designs
            or not all(isinstance(name, str) and name for name in designs)
            or len(set(designs)) != len(designs)
        ):
            return None
        try:
            same_path = _pdf_same_project_path(value["path"], str(path))
        except (OSError, RuntimeError, ValueError):
            return None
        if not same_path:
            return None
        return {"path": str(path), "designs": sorted(designs)}

    documents = [document(value) for value in raw_documents]
    pids = [
        *(application.get("pid") for application in applications),
        lifecycle.get("before_pid"),
        lifecycle.get("reopened_pid"),
    ]
    identity_complete = (
        all(item is not None for item in documents)
        and all(type(pid) is int and pid > 0 for pid in pids)
        and (not dedicated or (host_complete and generation_complete))
    )
    identity_verified = bool(
        identity_complete
        and all(item == documents[0] for item in documents)
        and Path(documents[0]["path"]) == path
        and len(set(pids)) == 1
        and (not dedicated or (host_verified and generation_verified))
    )
    counter_names = ("save_count", "close_count", "reopen_count")
    lifecycle_complete = bool(
        lifecycle.get("source") == "designer_save_close_reopen"
        and all(type(lifecycle.get(key)) is int for key in counter_names)
        and all(
            type(lifecycle.get(key)) is bool
            for key in ("save_acknowledged", "closed_observed", "close_returned")
        )
        and type(known.get("save_acknowledged")) is bool
    )
    lifecycle_verified = bool(
        lifecycle_complete
        and all(lifecycle[key] == 1 for key in counter_names)
        and all(
            lifecycle[key] is True
            for key in ("save_acknowledged", "closed_observed", "close_returned")
        )
        and known["save_acknowledged"] is True
    )
    reopened = known.get("read_back_after_reopen")
    reopened = reopened if isinstance(reopened, dict) else {}
    reopened_changes = verify_display_changes(
        before["extensions"]["facts"].get("native", {}),
        reopened,
        operations,
        save_metadata=save_metadata,
    )
    # No writes are authorized between reopening and this independent observation.
    reopened_current = verify_display_changes(
        reopened, after["extensions"]["facts"].get("native", {}), operations
    )
    reopened_complete = all(
        item.get("complete") is True for item in (reopened_changes, reopened_current)
    )
    reopened_verified = all(
        item.get("valid") is True for item in (reopened_changes, reopened_current)
    )
    comparison_keys = (
        "valid",
        "complete",
        "display_verified",
        "protection_verified",
        "issues",
        "scope",
        "metadata_changes",
    )
    valid = identity_verified and lifecycle_verified and reopened_verified
    return {
        "valid": valid,
        "complete": identity_complete and lifecycle_complete and reopened_complete,
        "identity_verified": identity_verified,
        "identity_mode": "dedicated_startup_owner" if dedicated else "legacy_public_getters",
        "host_object_binding_verified": host_verified if dedicated else None,
        "original_generation_verified": generation_verified if dedicated else None,
        "lifecycle_verified": lifecycle_verified,
        "reopened_snapshot_verified": reopened_verified,
        "reopened_changes": {key: reopened_changes[key] for key in comparison_keys},
        "reopened_current": {key: reopened_current[key] for key in comparison_keys},
        "source": "native lifecycle counts and independent before/after project identity getters",
        "expected_project": str(path),
        "observed_documents": documents,
        "observed_pids": pids,
    }


def _schematic_ordinary_persistence_readback(
    result, options, before, after, operations, *, save_metadata=None
):
    """Use the Driver's fresh observations, not the handler's success flags."""
    from .cli.schematic import verify_ordinary_edit_durability
    from .native_process import same_identity

    known = result if isinstance(result, dict) else {}
    old = before["extensions"]["facts"]
    new = after["extensions"]["facts"]
    evidence = verify_ordinary_edit_durability(
        options["project"],
        old.get("native", {}),
        new.get("native", {}),
        operations,
        known,
        save_metadata=save_metadata,
    )
    lifecycle = known.get("lifecycle") or {}

    def document(value):
        if not isinstance(value, dict):
            return None
        names = value.get("designs")
        if (
            value.get("status") != "observed"
            or value.get("open") is not True
            or not isinstance(value.get("path"), str)
            or not value["path"]
            or not isinstance(names, list)
            or not names
            or any(not isinstance(name, str) or not name for name in names)
            or len(names) != len(set(names))
        ):
            return None
        try:
            return (Path(value["path"]).resolve(), tuple(sorted(names)))
        except (OSError, RuntimeError, ValueError):
            return None

    apps = [old.get("application") or {}, new.get("application") or {}]
    documents = [document(app.get("document")) for app in apps]
    documents += [document(lifecycle.get(key)) for key in ("before_identity", "reopened_identity")]
    pids = [app.get("pid") for app in apps]
    pids += [lifecycle.get(key) for key in ("before_pid", "reopened_pid")]
    generations = [(app.get("pid_source") or {}).get("matching_process") or {} for app in apps]
    identities = [
        {
            "pid": row.get("pid"),
            "executable": row.get("executable"),
            "creation_time_100ns": row.get("birth_filetime_100ns"),
        }
        for row in generations
    ]
    host_source = "dedicated_startup_host_Application_and_authenticated_ncalrpc"
    bindings = [app.get("dedicated_startup_binding") or {} for app in apps]
    host_identity = bool(
        all(
            binding.get("source") == host_source
            and binding.get("canonical_stable") is True
            and binding.get("original_handle_rechecked") is True
            and isinstance(binding.get("connection_epoch_sha256"), str)
            and len(binding["connection_epoch_sha256"]) == 64
            for binding in bindings
        )
        and bindings[0].get("connection_epoch_sha256") == bindings[1].get("connection_epoch_sha256")
    )
    complete = all(row is not None for row in documents) and all(
        type(pid) is int and pid > 0 for pid in pids
    )
    identity = bool(
        complete
        and host_identity
        and len(set(pids)) == 1
        and all(row == documents[0] for row in documents)
        and documents[0][0] == Path(options["project"]).resolve()
        and identities[0].get("pid") == pids[0]
        and same_identity(identities[0], identities[1])
        and all(row.get("alive") is True for row in generations)
    )
    return {
        **evidence,
        "valid": evidence.get("valid") is True and identity,
        "complete": evidence.get("complete") is True and complete,
        "identity_verified": identity,
        "host_object_binding_verified": host_identity,
        "persistence_verified": evidence.get("valid") is True and identity,
        "source": "driver.before/after native snapshots and original-generation identity getters",
        "snapshot_before_id": before["id"],
        "snapshot_after_id": after["id"],
    }


def _query_result(result, options, command):
    """Adapt legacy local pages to the pinned SDK query-page profile."""
    data = {
        "items": result["items"],
        "count": len(result["items"]),
        "has_more": bool(result.get("has_more")),
        "truncated": bool(result.get("truncated", False)),
        "offset": int(result.get("offset") or 0),
        "scope": {
            "description": command.path,
            "selector": {
                key: options[key] for key in ("query", "project", "library") if options.get(key)
            },
        },
        "snapshot": None,
        "snapshot_unavailable_reason": "Each page is observed afresh; "
        "no retained cross-page snapshot exists.",
        "sort": [],
        "consistency": {
            "mode": "best_effort",
            "drift_policy": "report",
            "limitations": ["Native/file data may change between separate page calls."],
        },
        "limits": {
            "requested": options.get("limit"),
            "effective": options.get("limit"),
            "truncation_reason": None,
        },
        "extensions": {
            key: value
            for key, value in result.items()
            if key
            not in {
                "items",
                "count",
                "has_more",
                "offset",
                "next_offset",
                "_untrusted",
                "truncated",
            }
        },
        "_untrusted": ["items", "extensions"],
    }
    if data["has_more"]:
        data["next_offset"] = int(result.get("next_offset") or data["offset"] + data["count"])
    return data


class XpeditionDriverCLI(DriverCLI):
    """Add group help from the canonical contract without changing pinned SDK gates."""

    def _context(self):
        from .knowledge_base import for_context

        data = super()._context()
        # This is a local association list only. No document is read/fetched and
        # its untrusted contents cannot extend the SDK authorization summary.
        data["knowledge_base"] = for_context()
        data["_untrusted"] = [*data.get("_untrusted", []), "knowledge_base"]
        return data

    def _write(self, command, params, flags):
        diagnostic_exit_id = params.get("diagnostic-operation-id")
        if params.get("diagnostic-exit-token") and not diagnostic_exit_id:
            raise DriverError(
                "E_CONFLICT",
                "An exit token requires its diagnostic operation ID",
                details={"write_attempted": False},
                dispatch_state="not_started",
            )
        if diagnostic_exit_id and (
            command["path"] != "session close"
            or params.get("kind") != "pcb"
            or params.get("save-project")
            or params.get("save_project")
            or not isinstance(self.adapter, Adapter)
        ):
            raise DriverError(
                "E_CONFLICT",
                "A diagnostic-bound exit requires PCB and no save-project",
                details={"write_attempted": False},
                dispatch_state="not_started",
            )
        if _dedicated_designer_request(command["path"], params):
            _require_dedicated_designer(command["path"], params)
        if command["path"] == "session attach" and (
            params.get("host-bootstrap") or params.get("host_bootstrap")
        ):
            _require_designer_host_execution()
        # The public declaration is the maximum effect set. Empty-document Quit
        # never acquires persistence authorization through an unrelated project.
        if command["path"] == "session close" and not (
            params.get("save-project") or params.get("save_project")
        ):
            command = copy.deepcopy(command)
            command["driver"]["effects"] = ["session"]
        if command["path"] == "session attach" and isinstance(command.get("driver"), dict):
            # Opening an owned project is a session transition. The first-open
            # producer still requires mutable-project scope, but sends no Save
            # and cannot manufacture persistence qualification from Open.
            command = copy.deepcopy(command)
            command["driver"]["effects"] = ["session"]
        from .pcb_document_guard import (
            before_native_write,
            ensure_qualification_store,
            exclusive_pcb_write,
            require_shared_journal,
            requires_pcb_exclusion,
        )

        path = command["path"]
        if not isinstance(self.adapter, Adapter) or not requires_pcb_exclusion(command, params):
            return super()._write(command, params, flags)
        request = _PCB_REQUEST.get()
        if request is None or request["cli"] is not self:
            raise DriverError(
                "E_CONFLICT",
                "PCB writes require their public request context",
                details={"write_attempted": False},
                dispatch_state="not_started",
            )
        store = self._store_open()
        try:
            request["stack"].enter_context(exclusive_pcb_write(store))
            if not diagnostic_exit_id:
                admission = None
                if (
                    path == "session close"
                    and params.get("kind") == "pcb"
                    and not (params.get("save-project") or params.get("save_project"))
                ):
                    from .empty_close_admission import prepare

                    admission = prepare(self.adapter, store, params, self._scope(), self.validator)
                request["empty_close_admission"] = admission
                if admission is not None:
                    # Bind the already authenticated generation/build before
                    # even the first ordinary identity request reaches COM.
                    request["stack"].enter_context(
                        _native_binding(
                            {
                                "extensions": {
                                    "domain": "pcb",
                                    "application": {"pid": admission["current_process"]["pid"]},
                                    "worker_adapter": admission["worker"],
                                }
                            }
                        )
                    )
                before_native_write(store, path, params, empty_close_admission=admission)
            binding = config_dir() / "pcb-journal-binding-v1.json"
            try:
                binding.lstat()
                bound = True
            except FileNotFoundError:
                bound = False
            if bound:
                require_shared_journal(store, adapter_identity())
                ensure_qualification_store(store, can_initialize=False)
            elif path == "pcb diagnostics close" or diagnostic_exit_id:
                raise CLIError(
                    "E_CONFLICT",
                    "Closed diagnostic copy eligibility is unavailable",
                    {
                        "write_attempted": False,
                        "native_write_attempted": False,
                        "pcb_document_guard": {"reason": "copy_qualification_unavailable"},
                    },
                )
            if (
                path == "pcb diagnostics close"
                and flags.get("confirm")
                and not flags.get("dry_run")
            ):
                from ._vendor.tool_driver_sdk.scope import forbidden
                from .diagnostic_exit import ensure_table, require_capacity

                scope = self._scope()
                missing = scope.missing(
                    effects=command["driver"]["effects"],
                    dangerous=bool(command.get("dangerous")),
                )
                if missing:
                    raise forbidden(scope, missing)
                ensure_table(store)
                require_capacity(store)
            if diagnostic_exit_id:
                from .diagnostic_exit import authority

                proof, owner = authority(
                    self.adapter,
                    store,
                    diagnostic_exit_id,
                    self._scope(),
                    params.get("pid"),
                    qualification_token=params.get("diagnostic-exit-token"),
                )
                request.update(diagnostic_exit_proof=proof, diagnostic_exit_owner=owner)
            request.update(
                store=store,
                guarded=True,
                qualification_bound=bound,
                command=path,
                params=params,
                closed_copy=path == "project create"
                and bool(params.get("closed-copy") or params.get("closed_copy")),
            )
        except CLIError as error:
            raise DriverError(
                error.code,
                error.message,
                details=dict(error.details or {}),
                dispatch_state="not_started",
            ) from error
        return super()._write(command, params, flags)

    def _dispatch(self, store, record, claims, token, **kwargs):
        """Persist compact copy use after SDK admission and before any handler."""
        request = _PCB_REQUEST.get()
        reserved = bool(
            request
            and request.get("cli") is self
            and request.get("command") == "pcb diagnostics close"
        )
        if (
            request
            and request.get("cli") is self
            and request.get("command") == "session start"
            and request.get("qualification_bound")
        ):
            from .closed_copy_startup import prepare

            try:
                request["copy_startup_preparation"] = prepare(
                    store, record, request["params"], kwargs["scope"], self.validator
                )
            except Exception as error:
                raise DriverError(
                    getattr(error, "code", "E_CONFLICT"),
                    "The first closed-copy startup preparation could not be established",
                    details={"write_attempted": False, "native_write_attempted": False},
                    dispatch_state="not_started",
                ) from error
        if reserved:
            from .diagnostic_exit import reserve

            try:
                reserve(store, record["operation_id"], kwargs["scope"])
            except Exception as error:
                raise DriverError(
                    "E_IO",
                    "The diagnostic exit qualification could not be reserved",
                    details={"write_attempted": False, "native_write_attempted": False},
                    dispatch_state="not_started",
                ) from error
            request["diagnostic_reservation"] = record["operation_id"]
        refused = super()._dispatch(store, record, claims, token, **kwargs)
        if refused is not None and request and request.get("copy_startup_preparation"):
            from .closed_copy_startup import release_undispatched

            # Only an explicit SDK refusal plus a fresh never-dispatched plan
            # permits this exact CAS cleanup. Unknown dispatch is never undone.
            release_undispatched(store, record["operation_id"], request["copy_startup_preparation"])
        if refused is not None and reserved:
            from .diagnostic_exit import unreserve_undispatched

            unreserve_undispatched(store, record["operation_id"])
        if refused is not None or not request or request.get("cli") is not self:
            return refused
        if not request.get("qualification_bound"):
            return None
        try:
            from .pcb_document_guard import mark_dispatched_copy_use

            mark_dispatched_copy_use(store, record, request["params"])
            if request.get("diagnostic_exit_proof"):
                from .diagnostic_exit import claim

                proof = request["diagnostic_exit_proof"]
                claim(store, proof["diagnostic_id"], record["operation_id"], proof)
        except Exception as error:
            # The SDK dispatch is committed but no handler has run. Restore the
            # plan only if the SDK can prove that reversal succeeded; otherwise
            # leave its in-flight ID for the SDK's unknown settlement.
            from ._vendor.tool_driver_sdk.tokens import fingerprint

            try:
                store.undispatch(
                    record["operation_id"],
                    fingerprint(token),
                    {
                        "last_refusal": {
                            "code": "E_IO",
                            "reason": "copy_use_record_failed",
                        }
                    },
                )
                restored = store.get(record["operation_id"])
                if not isinstance(restored, dict) or restored.get("status") != "planned":
                    raise ValueError("qualification_refusal_not_reverted")
                if reserved:
                    from .diagnostic_exit import unreserve_undispatched

                    unreserve_undispatched(store, record["operation_id"])
                self._in_flight = None
            except Exception:
                raise DriverError(
                    "E_IO",
                    "Copy eligibility could not be recorded before native dispatch",
                    details={"native_write_attempted": False},
                    dispatch_state="unknown",
                ) from error
            raise DriverError(
                "E_IO",
                "Copy eligibility could not be recorded before native dispatch",
                details={
                    "write_attempted": False,
                    "state_write_attempted": True,
                    "native_write_attempted": False,
                    "pcb_document_guard": {"reason": "copy_use_record_failed"},
                },
                dispatch_state="not_started",
            ) from error
        return None

    def run(self, argv):
        # This supplement is request-local and query-only. Resolve/list never
        # reuse a preceding show request, even on the same CLI object.
        self._late_owner_operation_id = None
        if list(argv[:2]) == ["operation", "show"]:
            supplied = []
            for index, word in enumerate(argv):
                if word == "--id" and index + 1 < len(argv):
                    supplied.append(argv[index + 1])
                elif word.startswith("--id="):
                    supplied.append(word.partition("=")[2])
            if len(supplied) == 1:
                self._late_owner_operation_id = supplied[0]
        try:
            self.adapter._revision_request_token = object()
            self.adapter._revision_witness = None
        except AttributeError:
            pass  # A custom immutable adapter simply has no optional witness.
        words = [word for word in argv if word != "--help"]
        if "--help" in argv and words and not any(word.startswith("-") for word in words):
            prefix = " ".join(words)
            matched = [
                item for item in self.reference["commands"] if item["path"].startswith(prefix + " ")
            ]
            if matched:
                lines = [
                    f"usage: xpedition-cli {prefix} <command> [flags]",
                    "",
                    "commands:",
                ]
                lines.extend("  " + item["path"] + "  " + item["description"] for item in matched)
                lines.extend(["", "Read reference --index for the machine contract.", ""])
                return Output("\n".join(lines).encode("utf-8"), 0)
        words = []
        for word in argv:
            if word.startswith("-"):
                break
            words.append(word)
        command = " ".join(words)
        budget_flags = {
            word.partition("=")[0]: word.partition("=")[2] if "=" in word else "true"
            for word in argv
            if word.startswith("--")
        }
        mode = (
            "preview"
            if budget_flags.get("--dry-run") == "true"
            else "confirm"
            if "--confirm" in budget_flags
            else "query"
        )
        with (
            operation_request(command, mode, quiet=budget_flags.get("--quiet") == "true"),
            diagnostic_content_request(),
            ExitStack() as stack,
        ):
            token = _PCB_REQUEST.set({"cli": self, "stack": stack})
            try:
                # The cooperative PCB lock is released only after the SDK's
                # response validation and any resulting ledger demotion.
                return super().run(argv)
            finally:
                _PCB_REQUEST.reset(token)

    def _finish(self, envelope, started, flags, dispatched):
        if envelope.get("ok") and getattr(self, "_late_owner_operation_id", None) is not None:
            status = (envelope.get("data") or {}).get("operation_status")
            if (
                type(status) is dict
                and status.get("operation_id") == self._late_owner_operation_id
                and status.get("status") == "unknown"
            ):
                from .designer_late_observation import observe_record

                try:
                    if self._store is None:
                        raise ValueError("original_sdk_store_not_open")
                    record = self._store.get(self._late_owner_operation_id)
                    observation = observe_record(
                        record,
                        scope=self._scope(),
                        validator=self.validator,
                        config_root=Path(self.state_dir).parent,
                        get_record=self._store.get,
                    )
                except Exception:
                    observation = {
                        "scheme": "designer-original-command-observation-v1",
                        "status": "unavailable",
                        "reason": "original_authenticated_evidence_unavailable",
                        "original_outcome_unchanged": True,
                        "native_writer_admitted": False,
                        "cad_requests_queued": False,
                        "automatic_replay": False,
                    }
                envelope["data"]["native_observation"] = observation
        if not envelope.get("ok") and envelope.get("error", {}).get("code") == "E_CONFLICT":
            details = envelope["error"].get("details") or {}
            if (
                details.get("conflict") == "state_drift"
                and details.get("plan_id")
                and details.get("expected")
            ):
                try:
                    witness = getattr(self.adapter, "_revision_witness", None)
                    if not isinstance(witness, dict) or self._store is None:
                        raise ValueError("no_request_local_revision_witness")
                    # The existing SDK store is already open. get is SELECT
                    # only; do not open/create/prune a store for diagnostics.
                    record = self._store.get(witness.get("source_operation_id"))
                    details["revision_diagnostic"] = _matched_revision_diagnostic(
                        witness,
                        record,
                        details,
                        getattr(self.adapter, "_revision_request_token", None),
                    )
                except Exception:
                    details["revision_diagnostic"] = {
                        "status": "unavailable",
                        "reason": "no_matching_captured_observation",
                    }
                finally:
                    try:
                        self.adapter._revision_witness = None
                    except AttributeError:
                        pass
        receipt = (envelope.get("data") or {}).get("receipt") if envelope.get("ok") else None
        if (
            isinstance(receipt, dict)
            and (receipt.get("target", {}).get("extensions") or {}).get("command")
            == "session start"
            and receipt.get("outcome", {}).get("verification", {}).get("status") == "passed"
        ):
            # The SDK has now independently validated and stored this receipt.
            # The registry is only a pointer to that exact original record.
            try:
                from .native_process import process_identity
                from .session_ownership import original_start, register

                result = receipt["extensions"]["result"]
                domain = result["domain"]
                actual = process_identity(domain, result["pid"])
                _context, pointer = original_start(
                    domain,
                    actual,
                    receipt["operation_id"],
                    self._store_open().get,
                    self.validator,
                    receipt["authorization_scope_id"],
                )
                register(pointer)
                envelope["data"].setdefault("extensions", {})["ownership_registration"] = {
                    "status": "stored",
                    "startup_operation_id": receipt["operation_id"],
                }
            except (CLIError, OSError, ValueError, KeyError, TypeError):
                # Preserve the already verified/stored native result. A failure
                # to retain its pointer is a current storage failure, never a
                # reason to relaunch or rewrite its successful original receipt.
                envelope["data"].setdefault("extensions", {})["ownership_registration"] = {
                    "status": "failed",
                    "reason": "ownership_pointer_not_retained",
                    "startup_operation_id": receipt["operation_id"],
                    "recovery_required": True,
                }
                self._warnings.append(
                    "Native start succeeded and its original receipt was stored, but the "
                    "ownership pointer was not retained. Use explicit recovery with that "
                    "original startup operation ID; do not relaunch or repeat native work."
                )
        budget = current_budget()
        if budget is not None and (budget.stages or budget.calls):
            if envelope.get("ok"):
                data = envelope.get("data", {})
                if isinstance(data, dict) and (
                    not flags.get("fields") or {"preview", "receipt"} & data.keys()
                ):
                    # The SDK has already sealed/stored preview and receipt. The
                    # final request wall clock is separate response evidence;
                    # modifying those objects here breaks producer provenance.
                    data.setdefault("extensions", {})["operation_budget"] = budget.report()
            else:
                details = envelope["error"].setdefault("details", {})
                details["operation_budget"] = budget.report()
        output = super()._finish(envelope, started, flags, dispatched)
        request = _PCB_REQUEST.get()
        if (
            request
            and request.get("cli") is self
            and request.get("copy_startup_preparation")
            and dispatched
            and isinstance(output.envelope, dict)
            and output.envelope.get("ok") is True
        ):
            try:
                from .closed_copy_startup import finalize as finalize_copy_startup

                qualification = finalize_copy_startup(
                    self.adapter,
                    request["store"],
                    dispatched,
                    request["copy_startup_preparation"],
                    output,
                    self.validator,
                    self._scope(),
                )
                if not qualification:
                    raise ValueError("post_open_qualification_unavailable")
                enriched = copy.deepcopy(output.envelope)
                extension = enriched["data"].setdefault("extensions", {})
                extension["closed_copy_startup"] = qualification
                # This is the same total public startup budget. Never publish a
                # certificate from a late COMMIT, signer or final response build.
                for _ in range(2):
                    checkpoint()
                    extension["operation_budget"] = budget.report()
                    enriched["meta"]["duration_ms"] = extension["operation_budget"]["wall_ms"]
                    self.validator.validate("response", enriched)
                    rendered = self._render(enriched, flags)
                    checkpoint()
                output = Output(rendered, output.exit_code, enriched, output.stderr)
            except Exception:
                # Preserve the actual startup result. A retained preparation
                # without a delivered token cannot fall back to legacy genesis.
                fallback = copy.deepcopy(output.envelope)
                extension = fallback["data"].setdefault("extensions", {})
                extension.pop("closed_copy_startup", None)
                if budget is not None:
                    extension["operation_budget"] = budget.report()
                    fallback["meta"]["duration_ms"] = extension["operation_budget"]["wall_ms"]
                output = Output(
                    self._render(fallback, flags),
                    output.exit_code,
                    fallback,
                    output.stderr + "warning: closed-copy post-Open qualification unavailable\n",
                )
        if (
            request
            and request.get("cli") is self
            and dispatched
            and request.get("diagnostic_reservation") == dispatched
        ):
            try:
                from .diagnostic_exit import finalize

                qualification_token = finalize(
                    request["store"], dispatched, output, self.validator, self._scope()
                )
                details = ((output.envelope or {}).get("error") or {}).get("details") or {}
                if not qualification_token and details.get("diagnostic_complete") is True:
                    raise ValueError("diagnostic_exit_qualification_not_sealed")
                if qualification_token:
                    enriched = copy.deepcopy(output.envelope)
                    final_details = enriched["error"]["details"]
                    final_details["diagnostic_exit_token"] = qualification_token
                    # The certificate binds this already-validated stage evidence;
                    # operation_budget below is the separate final request timing.
                    final_details["diagnostic_exit_validated_budget"] = copy.deepcopy(
                        details["operation_budget"]
                    )
                    # No more durable writes: a late COMMIT or failed final response
                    # can leave a ready candidate, but can never disclose authority.
                    # Validate/render the complete token-bearing response, then sample
                    # tail timing and validate/render the final bounded result once.
                    for _ in range(2):
                        checkpoint()
                        final_details["operation_budget"] = budget.report()
                        enriched["meta"]["duration_ms"] = final_details["operation_budget"][
                            "wall_ms"
                        ]
                        self.validator.validate("response", enriched)
                        rendered = self._render(enriched, flags)
                        checkpoint()
                    output = Output(rendered, output.exit_code, enriched, output.stderr)
            except Exception:
                # Preserve the original unknown receipt/resolution byte-for-byte.
                # Never return a certificate from a failed or over-budget finish.
                fallback = copy.deepcopy(output.envelope)
                details = ((fallback or {}).get("error") or {}).get("details")
                if isinstance(details, dict):
                    details.pop("diagnostic_exit_token", None)
                    if budget is not None:
                        details["operation_budget"] = budget.report()
                        fallback["meta"]["duration_ms"] = details["operation_budget"]["wall_ms"]
                output = Output(
                    self._render(fallback, flags),
                    output.exit_code,
                    fallback,
                    output.stderr
                    + "warning: diagnostic-bound empty exit qualification unavailable\n",
                )
        if (
            request
            and request.get("cli") is self
            and request.get("diagnostic_exit_proof")
            and dispatched
            and isinstance(output.envelope, dict)
            and output.envelope.get("ok") is True
        ):
            try:
                from .diagnostic_exit import release_completed

                released = release_completed(
                    request["store"],
                    request["diagnostic_exit_proof"],
                    dispatched,
                    output,
                    self.validator,
                )
                if not released:
                    raise ValueError("completed_exit_not_released")
            except Exception:
                # An uncertain COMMIT can leave the claimed row or remove it.
                # Neither state can regrant: the SDK diagnostic is not planned.
                output = Output(
                    output.stdout,
                    output.exit_code,
                    output.envelope,
                    output.stderr + "warning: completed exit qualification release uncertain\n",
                )
        if (
            request
            and request.get("cli") is self
            and request.get("closed_copy")
            and dispatched
            and isinstance(output.envelope, dict)
            and output.envelope.get("ok") is True
        ):
            # The copy remains a valid ordinary file operation if optional
            # diagnostic eligibility cannot be recorded. A later diagnostic
            # then refuses explicitly; no unrelated write is held hostage.
            try:
                from .pcb_document_guard import (
                    ensure_qualification_store,
                    register_copy_qualification,
                    require_shared_journal,
                )

                store = request["store"]
                if request.get("qualification_bound"):
                    ensure_qualification_store(store, can_initialize=False)
                    require_shared_journal(store, adapter_identity())
                else:
                    # Establish the optional table before publishing the root
                    # binding. A schema write failure cannot strand unrelated
                    # legacy PCB commands behind a half-created binding.
                    ensure_qualification_store(store, can_initialize=True)
                    require_shared_journal(store, adapter_identity())
                register_copy_qualification(store, output.envelope["data"]["receipt"])
            except Exception as error:
                guard = (getattr(error, "details", None) or {}).get("pcb_document_guard") or {}
                reason = guard.get("reason") if isinstance(guard, dict) else None
                if reason not in {
                    "candidate_identity_unavailable",
                    "journal_already_bound_elsewhere",
                    "journal_binding_unavailable",
                    "journal_identity_unavailable",
                    "journal_or_candidate_mismatch",
                    "prior_pcb_ownership_without_journal_binding",
                    "qualification_capacity_reached",
                    "qualification_registration_failed",
                    "qualification_store_missing_or_changed",
                }:
                    reason = "qualification_record_failed"
                enriched = copy.deepcopy(output.envelope)
                enriched["data"].setdefault("extensions", {})["close_diagnostic_qualification"] = {
                    "status": "unavailable",
                    "reason": reason,
                }
                try:
                    self.validator.validate("response", enriched)
                    output = Output(
                        self._render(enriched, flags),
                        output.exit_code,
                        enriched,
                        output.stderr,
                    )
                except Exception:
                    if not flags.get("quiet"):
                        output = Output(
                            output.stdout,
                            output.exit_code,
                            output.envelope,
                            output.stderr
                            + "warning: this copy has no closed-diagnostic eligibility record\n",
                        )
        return output


class Adapter:
    """Reuse task handlers while binding plans and receipts to observed source/state."""

    def __init__(self, backend=None):
        self.backend = backend or NativeBackend()
        self._revision_witness = None
        self._revision_request_token = object()

    def _ownership(self, domain, actual, *, source_id=None, scope_id=None):
        from .session_ownership import select

        if scope_id is None and callable(getattr(self, "ownership_scope_id", None)):
            scope_id = self.ownership_scope_id()
        return select(
            domain,
            actual,
            get_record=getattr(self, "ownership_record_get", None),
            validator=getattr(self, "ownership_validator", None),
            scope_id=scope_id,
            source_id=source_id,
        )

    def _ownership_options(self, call, options):
        if call.command in {"session attach", "session close"}:
            scope = call.extras.get("scope")

            def resolve(domain, actual, source_id=None):
                context, pointer = self._ownership(
                    domain,
                    actual,
                    source_id=source_id,
                    scope_id=getattr(scope, "scope_id", None),
                )
                if source_id and context.get("requested_project"):
                    scope.require(
                        effects=["observe"],
                        paths=[(context["requested_project"], "observe")],
                    )
                return context, pointer

            options["_ownership_resolver"] = resolve
        return options

    def _nativeom_ownership(self, scope):
        from .nativeom_ownership import collect

        def resolve(domain, actual):
            return self._ownership(domain, actual, scope_id=getattr(scope, "scope_id", None))

        def require_observe(project):
            if scope is None:
                raise CLIError("E_FORBIDDEN", "Original task scope is unavailable")
            scope.require(effects=["observe"], paths=[(project, "observe")])

        return collect(resolve, require_observe)

    def _host_bootstrap_material(self, options, scope=None):
        """Bind a requested original generation without inspecting any CAD object."""
        _require_designer_host_execution()
        from .cli.sessions import validate_host_bootstrap_options
        from .designer_host_bootstrap import template_identity
        from .session import read_state
        from .session_ownership import pointer_for

        validate_host_bootstrap_options(options)
        if read_state().get("state") in {"closing", "stale"}:
            raise CLIError(
                "E_CONFLICT",
                "The fixed host experiment refuses a closing or stale session",
                {"write_attempted": False},
            )
        actual = _owned_process_identity("schematic", str(options["pid"]))
        context, pointer = self._ownership(
            "schematic",
            actual,
            source_id=options["startup_operation_id"],
            scope_id=getattr(scope, "scope_id", None),
        )
        if pointer_for("schematic", actual) != pointer:
            raise CLIError(
                "E_CONFLICT",
                "Recover the original startup pointer before this experiment",
                {"write_attempted": False},
            )
        generations = self._host_bootstrap_generations(context, actual)
        project = context.get("requested_project")
        if not isinstance(project, str) or not Path(project).is_absolute():
            raise CLIError("E_CONFLICT", "The original startup has no project observation scope")
        if scope is not None:
            scope.require(effects=["observe"], paths=[(project, "observe")])
        scope_id = getattr(scope, "scope_id", None)
        if scope_id is None and callable(getattr(self, "ownership_scope_id", None)):
            scope_id = self.ownership_scope_id()
        if not isinstance(scope_id, str) or not scope_id:
            raise CLIError("E_FORBIDDEN", "The fixed host experiment requires its original scope")
        template = template_identity()
        if template.get("status") != "verified" or template.get("engineering_writes") is not False:
            raise CLIError("E_CONFIG", "The fixed host-bootstrap template is unverified")
        return (
            {
                "status": "verified",
                "domain": "schematic",
                "authority_granted": False,
                "verification_basis": "original_sdk_start_receipt_and_registry",
                "startup_operation_id": pointer["startup_operation_id"],
                "source_sha256": pointer["source_sha256"],
                "project": project,
                "scope_id": scope_id,
                "project_scope_verified": True,
                "generations": generations,
            },
            pointer,
            template,
        )

    @staticmethod
    def _host_bootstrap_generations(context, actual):
        """Require a complete current Designer census within the original own chain."""
        from .native_process import (
            _startup_snapshot,
            owned_startup_launchers,
            same_identity,
        )

        try:
            launchers = owned_startup_launchers(context.get("startup_provenance"), actual)
            allowed = {row["pid"]: row for row in [actual, *launchers]}
            observed = _startup_snapshot("schematic")
            if actual["pid"] not in observed or any(
                pid not in allowed or not same_identity(row["identity"], allowed[pid])
                for pid, row in observed.items()
            ):
                raise ValueError("unowned_or_missing_generation")
            return [
                {**copy.deepcopy(actual), "role": "application"},
                *[
                    {**copy.deepcopy(allowed[pid]), "role": "launcher"}
                    for pid in sorted(observed)
                    if pid != actual["pid"]
                ],
            ]
        except Exception as error:
            raise CLIError(
                "E_CONFLICT",
                "Current Designer candidates are not the complete original owned set",
                {
                    "write_attempted": False,
                    "reason": "host_bootstrap_native_census_unverified",
                },
            ) from error

    def _observe_host_bootstrap(self, target):
        _require_designer_host_execution()
        from .session_ownership import registry_path

        extension = target["extensions"]
        packet, pointer, template = self._host_bootstrap_material(
            {
                "host_bootstrap": "identity",
                "kind": "schematic",
                "pid": target["instance_id"],
                "startup_operation_id": extension["ownership_pointer"]["startup_operation_id"],
            }
        )
        if (
            pointer != extension.get("ownership_pointer")
            or template != extension["host_bootstrap_request"]["template"]
            or packet != extension.get("host_bootstrap_target")
        ):
            raise CLIError(
                "E_CONFLICT",
                "The fixed bootstrap target, scope or template changed",
                {"write_attempted": False},
            )
        facts = {
            "adapter": adapter_identity(),
            "inputs": [],
            "host_bootstrap_target": packet,
            "template": template,
            "ownership_registry_pointer": pointer,
            "os_process_identity": packet["generations"][0],
            "session_record": _disk(config_dir() / "session.json"),
            "ownership_registry": _disk(registry_path()),
        }
        revision = _digest(_stable(facts))
        return {
            "id": "snapshot-" + revision[:24],
            "source": "disk",
            "revision": revision,
            "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "dependencies": [],
            "extensions": {"facts": facts},
        }

    def _engineering_guard(self, target, application, *, exclude_operation_id=None):
        from .pcb_document_guard import applies, check

        capabilities = getattr(self, "engineering_capabilities", {})
        if not applies(target, capabilities):
            return
        reader = getattr(self, "engineering_active_records", None)
        validator = getattr(self, "ownership_validator", None)
        if not callable(reader) or validator is None:
            raise DriverError(
                "E_CONFIG",
                "Engineering persistence requires the original active-operation ledger reader",
                details={"write_attempted": False},
                dispatch_state="not_started",
            )
        check(
            target,
            application,
            reader(exclude_operation_id),
            validator,
            capabilities,
            exclude_operation_id=exclude_operation_id,
        )

    def _identity(self, domain: str) -> dict:
        observed = self.backend.invoke("identity", {"domain": domain}, timeout_seconds=60.0)
        worker = observed.get("adapter") or {}
        request = _PCB_REQUEST.get()
        admission = (
            request.get("empty_close_admission")
            if request and getattr(request.get("cli"), "adapter", None) is self
            else None
        )
        if admission is not None:
            from .native_identity import same_diagnostic_candidate

            if (
                domain != "pcb"
                or (observed.get("application") or {}).get("pid")
                != admission["current_process"]["pid"]
                or not same_diagnostic_candidate(worker, admission["worker"])
            ):
                raise DriverError(
                    "E_CONFLICT",
                    "The empty-close worker or native generation changed",
                    details={"write_attempted": False},
                    dispatch_state="not_started",
                )
        if worker.get("version") != __version__ or not worker.get("build_sha256"):
            raise DriverError(
                "E_BACKEND_UNAVAILABLE",
                "The loaded native worker is not the candidate adapter",
                details={
                    "expected_version": __version__,
                    "observed_adapter": worker,
                    "next_action": (
                        "Point XPEDITION_NATIVE_COMMAND at the matching candidate worker."
                    ),
                },
                dispatch_state="not_started",
            )
        return observed

    def environment(self) -> dict:
        identity = adapter_identity()
        return {
            "components": [
                {
                    "name": "Xpedition native COM adapter",
                    "role": "adapter",
                    "version": __version__,
                    "build_id": identity["build_sha256"],
                    "location": "disk",
                    "identity_status": "observed",
                }
            ],
            "platform": "windows" if os.name == "nt" else sys.platform,
            "arch": "amd64"
            if os.name == "nt" and adapter_identity()["client_bitness"] == 64
            else "unknown",
            "connection": {
                "status": "not_connected",
                "instance_id": None,
                "generation": None,
                "ownership": None,
                "reason": (
                    "Use session inspect and explicit session attach; installation "
                    "registration alone does not establish a live licensed instance."
                ),
            },
        }

    def doctor_checks(self) -> list[dict]:
        status = self.backend.status()
        checks = [
            {
                "check": "native_backend",
                "status": "pass" if status.get("available") else "fail",
                "fix": None
                if status.get("available")
                else (
                    "Configure the existing licensed Xpedition installation and candidate adapter."
                ),
                "message": (
                    "Adapter/configuration available; native license and current "
                    "application readiness are unproven."
                )
                if status.get("available")
                else str(status.get("reason")),
                "details": {"native_task_acceptance": "not_run"},
            }
        ]
        if status.get("available"):
            try:
                health = self.backend.invoke("health", {}, timeout_seconds=60.0)
                ready = bool(
                    health.get("application_running") or health.get("designer_application_running")
                )
                checks.append(
                    {
                        "check": "live_automation",
                        "status": "pass" if ready else "fail",
                        "message": (
                            "A live COM application is reachable; task acceptance and license "
                            "expiry remain unproven."
                        )
                        if ready
                        else "No Designer/Layout COM application is reachable.",
                        "fix": None
                        if ready
                        else (
                            "Inspect the attachment diagnostics and existing application window, "
                            "then run session inspect. An attachment error does not establish "
                            "process absence or a licensing cause; do not launch a duplicate."
                        ),
                        "details": {
                            "health": health,
                            "license_expiry": "unknown",
                            "native_task_acceptance": "not_run",
                        },
                    }
                )
            except CLIError as error:
                checks.append(
                    {
                        "check": "live_automation",
                        "status": "fail",
                        "message": error.message,
                        "fix": "Inspect the existing native session and run session inspect.",
                        "details": {"code": error.code},
                    }
                )
        return checks

    @_budgeted("query")
    @_designer_routed("query")
    def run(self, call) -> dict:
        command = by_path()[call.command]
        with _observe_only():
            try:
                options = _options(call)
                if call.command == "pcb inspect" and options.get("live_geometry"):
                    from .cli.common import integer

                    pid = integer(options, "pid")
                    if pid is None or pid <= 0:
                        raise CLIError("E_VALIDATION", "Geometry observation requires --pid")
                    path = Path(str(options["project"])).expanduser().resolve()
                    # Reuse the existing task scope, without reserving, resolving,
                    # or modifying any operation or loosening a write/artifact gate.
                    call.extras["scope"].require(
                        effects=["observe"],
                        paths=[(str(path), "observe")],
                        target={
                            "mode": "session",
                            "engine_id": "xpedition-pcb",
                            "instance_id": str(pid),
                            "object_id": str(path),
                            "locator": path.as_uri(),
                        },
                    )
                    from . import native_com_adapter

                    try:
                        board = native_com_adapter._layout_board_path({"project": str(path)})
                    except native_com_adapter.AdapterError as error:
                        raise CLIError(error.code, str(error), error.details) from error
                    call.extras["scope"].require(
                        effects=["observe"], paths=[(str(board), "observe")]
                    )
                from .backends.native_xpedition import identity_ownership

                provider = (
                    (lambda: self._nativeom_ownership(call.extras.get("scope")))
                    if call.command == "session inspect"
                    else None
                )
                with (
                    operation_stage("query"),
                    identity_ownership(
                        provider,
                        designer_probe=options.get("designer_identity_probe")
                        if call.command == "session inspect"
                        else None,
                    ),
                ):
                    result = _handler(command)(options)
                return (
                    _query_result(result, options, command)
                    if command.output_schema == "list_result"
                    or (
                        isinstance(result, dict)
                        and isinstance(result.get("items"), list)
                        and {"count", "has_more"} <= result.keys()
                    )
                    else result
                )
            except CLIError as error:
                details = dict(error.details or {})
                if error.code == "E_NATIVE_FAILURE" and details.get("native_query_only") is True:
                    details.setdefault(
                        "next_action",
                        "This read is incomplete. Inspect its retained native state and failure "
                        "details. It makes no conclusion about changes or persistence from "
                        "earlier actions; do not replay those actions.",
                    )
                raise DriverError(
                    error.code,
                    error.message,
                    details=details,
                    dispatch_state="not_started",
                ) from error

    def _revalidate_empty_close(self, target=None, *, operation_id=None):
        """Request-local exception, never a transferable engineering authority."""
        request = _PCB_REQUEST.get()
        promised = (target or {}).get("extensions", {}).get("empty_close_admission")
        if not request or getattr(request.get("cli"), "adapter", None) is not self:
            if promised is not None:
                raise CLIError("E_CONFLICT", "Empty-close request binding is unavailable")
            return None
        proof = request.get("empty_close_admission")
        if target is not None and promised != proof:
            raise CLIError("E_CONFLICT", "Empty-close admission changed since preview")
        if proof is None:
            return None
        from .empty_close_admission import revalidate

        cli = request["cli"]
        revalidate(
            self,
            request["store"],
            request["params"],
            cli._scope(),
            cli.validator,
            proof,
            exclude_operation_id=operation_id,
        )
        return copy.deepcopy(proof)

    def _target(self, call, command, options: dict) -> dict:
        if call.command == "pcb diagnostics close":
            from .close_diagnostic import target

            return target(self, call, options)
        if _dedicated_designer_request(call.command, options) or _dedicated_designer_request(
            call.command, getattr(call, "params", {})
        ):
            _require_dedicated_designer(call.command, options)
        if call.command == "session attach" and options.get("host_bootstrap"):
            _require_designer_host_execution()
            packet, pointer, template = self._host_bootstrap_material(
                options, call.extras.get("scope")
            )
            return {
                "mode": "session",
                "engine_id": "xpedition-schematic",
                "instance_id": str(options["pid"]),
                "object_id": "schematic",
                "locator": f"xpedition://schematic/{options['pid']}",
                "extensions": {
                    "domain": "schematic",
                    "command": call.command,
                    "project": None,
                    "input_paths": [],
                    "worker_adapter": adapter_identity(),
                    "process_identity": packet["generations"][0],
                    "ownership_pointer": pointer,
                    "host_bootstrap_target": packet,
                    "host_bootstrap_request": {
                        "kind": "identity",
                        "template": template,
                    },
                },
            }
        closed_copy = call.command == "project create" and options.get("closed_copy") is True
        file_native = {
            "project create",
            "library create",
            "library import",
            "library materialize",
            "pcb create",
        }
        domain = options.get("kind") or (
            "schematic" if command.needs == "designer" or call.command in file_native else "pcb"
        )
        native = (
            command.needs in {"designer", "layout"} and call.command not in file_native
        ) or call.command in {"session attach", "session close"}
        if native:
            empty_close_admission = self._revalidate_empty_close(operation_id=call.operation_id)
            identity = self._identity(domain)
            application = identity.get("application") or {}
            instance = application.get("pid") or application.get("public_fields", {}).get(
                "InstanceGuid"
            )
            if not instance:
                raise DriverError(
                    "E_BACKEND_UNAVAILABLE",
                    "The active native application has no verified instance identity.",
                    details={
                        "application": application,
                        "next_action": (
                            "Complete licensing, then inspect and attach to the explicit process."
                        ),
                    },
                    dispatch_state="not_started",
                )
            if options.get("pid") and application.get("pid") != int(options["pid"]):
                raise DriverError(
                    "E_CONFLICT",
                    "The active native instance does not match --pid.",
                    dispatch_state="not_started",
                )
            raw = options.get("project")
            if call.command == "session attach" and domain == "pcb" and raw:
                from .cli.sessions import require_layout_switch_ready

                try:
                    require_layout_switch_ready(application, str(raw))
                except CLIError as error:
                    raise DriverError(
                        error.code,
                        error.message,
                        details=error.details,
                        dispatch_state="not_started",
                    ) from error
            locator = (
                Path(str(raw)).expanduser().resolve().as_uri()
                if raw
                else f"xpedition://{domain}/{instance}"
            )
            extra = {}
            if call.command in {"session close", "session attach"}:
                try:
                    extra["process_identity"] = _owned_process_identity(domain, str(instance))
                except DriverError as error:
                    if call.command != "session close":
                        raise
                    details = dict(error.details or {})
                    details["lifecycle_diagnostic"] = {
                        "stage": "setup",
                        "callsite": "driver._target.process_identity",
                        "reason": "process_identity_unavailable",
                    }
                    raise DriverError(
                        error.code,
                        error.message,
                        details=details,
                        dispatch_state="not_started",
                    ) from error
                if call.command == "session close":
                    from .native_process import (
                        StartupOwnershipError,
                        owned_startup_launchers,
                    )
                    from .session import read_state

                    recorded = read_state()
                    preparation = current_prepared_close()
                    if preparation is not None:
                        extra["previous_close_preparation"] = preparation.safe_summary
                    try:
                        selected, pointer = self._ownership(domain, extra["process_identity"])
                        extra["owned_launcher_identities"] = owned_startup_launchers(
                            selected.get("startup_provenance"),
                            extra["process_identity"],
                        )
                        extra["ownership_pointer"] = pointer
                    except (ValueError, KeyError, TypeError) as error:
                        domains = {"pcb", "schematic", "library"}
                        raise DriverError(
                            "E_CONFLICT",
                            "Recorded startup ownership could not be verified; "
                            "nothing was dispatched.",
                            details={
                                "write_attempted": False,
                                "lifecycle_diagnostic": {
                                    "stage": "setup",
                                    "callsite": "driver._target.owned_startup_launchers",
                                    "reason": error.reason
                                    if isinstance(error, StartupOwnershipError)
                                    else "startup_provenance_validation_failed",
                                    "requested_domain": domain if domain in domains else "unknown",
                                    "recorded_domain": recorded.get("domain")
                                    if recorded.get("domain") in domains
                                    else "unknown",
                                },
                            },
                            dispatch_state="not_started",
                        ) from error
                if call.command == "session attach" and options.get("startup_operation_id"):
                    if options.get("project"):
                        raise DriverError(
                            "E_USAGE",
                            "--startup-operation-id cannot be combined with --project",
                            details={"write_attempted": False},
                            dispatch_state="not_started",
                        )
                    _selected, pointer = self._ownership(
                        domain,
                        extra["process_identity"],
                        source_id=options["startup_operation_id"],
                    )
                    extra["ownership_pointer"] = pointer
                    extra["ownership_recovery"] = True
                if call.command == "session attach" and options.get("project_source_operation_id"):
                    from .designer_project_admission import ROUTE, build_target_binding

                    _selected, pointer = self._ownership(
                        domain,
                        extra["process_identity"],
                        scope_id=getattr(call.extras.get("scope"), "scope_id", None),
                    )
                    document = application.get("document") or {}
                    if (
                        document.get("status") != "observed"
                        or document.get("open") is not False
                        or document.get("path")
                    ):
                        raise CLIError(
                            "E_CONFLICT",
                            "First project admission requires the original empty owner",
                        )
                    extra["ownership_pointer"] = pointer
                    extra["designer_project_open_route"] = ROUTE
                    extra["designer_project_source"] = build_target_binding(
                        extra["process_identity"]
                    )
            if options.get("project") and set(call.effects) & {"change", "persist"}:
                extra["project_directory"] = str(Path(str(options["project"])).resolve().parent)
            if call.command == "pcb update" and not options.get("refresh_cells"):
                extra["secondary_applications"] = {
                    "schematic": self._identity("schematic")["application"]
                }
            if call.command == "pcb update" and options.get("refresh_cells"):
                from .project_file import central_library

                library = central_library(Path(str(options["project"])))
                if library:
                    extra["library_directory"] = str(library.parent.resolve())
                extra["cell_refresh"] = True
            if call.command == "pcb silkscreen labels" and options.get("file"):
                extra["label_objects"] = True
            if call.command == "pcb placement move" and options.get("file"):
                extra["placement_batch"] = True
            if call.command == "pcb outline set":
                extra["outline_readback"] = True
            if call.command == "pcb routing trace" and options.get("mode") == "component-connect":
                from .cli.pcb import component_connect_options

                extra["component_connect_request"] = component_connect_options(
                    options, str(Path(str(options["project"])).resolve())
                )["component_connect_request"]
            if call.command == "pcb routing auto" and options.get("mode") == "bounded-native":
                from .cli.pcb import native_route_options

                extra["native_routepass_request"] = native_route_options(options)
                extra["source_backup"] = str(Path(str(options["source_backup"])).resolve())
            if call.command == "pcb routing auto" and options.get("mode") == "bounded-general":
                from .board_general import validate
                from .cli.common import input_file, read_json_file

                plan_file = input_file(
                    options, "file", call.command, "the bounded General Route plan"
                )
                try:
                    extra["native_general_request"] = validate(
                        read_json_file(plan_file, "the bounded General Route plan")
                    )
                except ValueError as error:
                    raise CLIError(
                        "E_VALIDATION", str(error), {"write_attempted": False}
                    ) from error
                extra["source_backup"] = str(Path(str(options["source_backup"])).resolve())
            return {
                "mode": "session",
                "engine_id": "xpedition-" + domain,
                "instance_id": str(instance),
                "object_id": str(Path(str(raw)).expanduser().resolve()) if raw else domain,
                "locator": locator,
                "extensions": {
                    "domain": domain,
                    "command": call.command,
                    "project": str(Path(str(raw)).expanduser().resolve()) if raw else None,
                    "application": application,
                    "worker_adapter": identity["adapter"],
                    "input_paths": self._input_paths(options),
                    **extra,
                    **(
                        {"empty_close_admission": empty_close_admission}
                        if empty_close_admission is not None
                        else {}
                    ),
                },
            }
        raw = (
            options.get("project")
            or options.get("output")
            or options.get("file")
            or options.get("design")
        )
        if call.command.startswith("kb "):
            raw = config_dir() / "knowledge-base.json"
        startup_recovery = None
        if call.command == "session start":
            from .session_ownership import require_registration_capacity

            require_registration_capacity()
            # Before launch, the target is the CLI-owned launch intent, not an invented native PID.
            raw = config_dir() / "launch-intents" / (str(domain) + ".json")
            if domain == "schematic":
                # Each future owned process has its own prospective intent.
                # A failed current binding must first prove all original
                # generations gone; its unknown row is never resolved/reused.
                startup_recovery = previous_start_observation(self, call.extras.get("scope"))
                raw = config_dir() / "launch-intents" / "schematic" / (call.operation_id + ".json")
        path = Path(str(raw or config_dir() / "local-target.json")).expanduser().resolve()
        extension = {
            "path": str(path),
            "domain": domain,
            "command": call.command,
            "input_paths": self._input_paths(options),
        }
        if call.command == "project backup":
            extension["designer_leave_closed_requested"] = options.get("leave_closed") is True
            extension["designer_lifecycle_output"] = (
                str(Path(str(options["output"])).resolve()) if options.get("output") else None
            )
        if call.command == "project create" and not closed_copy:
            extension["designer_setup_template"] = str(Path(str(options["template"])).resolve())
        if call.command == "session start" and domain == "schematic":
            extension["designer_startup_route"] = DESIGNER_START_ROUTE
            extension["designer_startup_generation"] = call.operation_id
            if startup_recovery is not None:
                extension["previous_start_observation"] = startup_recovery.safe_summary
        if call.command in file_native and not closed_copy:
            identity = self._identity("schematic")
            application = identity.get("application") or {}
            if not application.get("pid"):
                raise DriverError(
                    "E_BACKEND_UNAVAILABLE",
                    "This operation requires an identified existing Designer session.",
                    dispatch_state="not_started",
                )
            extension["application"] = application
            extension["worker_adapter"] = identity["adapter"]
            extension["project_directory"] = str(path.parent)
            if call.command == "pcb create":
                extension["secondary_applications"] = {"pcb": self._identity("pcb")["application"]}
        elif (
            options.get("project")
            and path.suffix.lower() == ".prj"
            and set(call.effects) & {"change", "persist"}
        ):
            extension["project_directory"] = str(path.parent)
        if options.get("project") and call.command.startswith("library "):
            from .project_file import central_library

            library = central_library(Path(str(options["project"])))
            if library:
                extension["library_directory"] = str(library.parent.resolve())
        return {
            "mode": "file",
            "engine_id": "xpedition-cli-files",
            "instance_id": None,
            "object_id": str(path),
            "locator": path.as_uri(),
            "extensions": extension,
        }

    @staticmethod
    def _input_paths(options) -> list[str]:
        return [
            str(Path(str(options[key])).expanduser().resolve())
            for key in (
                "project",
                "file",
                "design",
                "geometry",
                "rules",
                "elements",
                "baseline",
                "backup",
                "source_backup",
                "component_query",
                "isolated_origin",
                "template",
                "library",
                "from",
            )
            if options.get(key) and Path(str(options[key])).expanduser().exists()
        ]

    def _observe(
        self,
        target: dict,
        *,
        after_dispatch: bool = False,
        start_generation_witness=None,
    ) -> dict:
        if not after_dispatch:
            self._revalidate_empty_close(
                target,
                operation_id=(target.get("extensions") or {}).get("source_operation_id"),
            )
        if (target.get("extensions") or {}).get("command") == "pcb diagnostics close":
            from .close_diagnostic import before

            if after_dispatch:
                raise CLIError("E_CONFLICT", "Diagnostic forbids generic post-dispatch reads")
            return before(self, target)
        if start_generation_witness is not None:
            from .designer_owner_recovery import SameRequestStartWitness

            if (
                type(start_generation_witness) is not SameRequestStartWitness
                or not after_dispatch
                or (target.get("extensions") or {}).get("command") != "session start"
                or (target.get("extensions") or {}).get("domain") != "schematic"
            ):
                raise CLIError(
                    "E_CONFLICT",
                    "A terminal-generation witness is limited to its start readback",
                )
        extension = target.get("extensions") or {}
        if _dedicated_designer_request(extension.get("command"), target=target):
            _require_dedicated_designer(target=target)
        if extension.get("host_bootstrap_request"):
            _require_designer_host_execution()
            return self._observe_host_bootstrap(target)
        facts = {
            "adapter": adapter_identity(),
            "inputs": [_disk(Path(path)) for path in extension.get("input_paths", [])],
        }
        if target["mode"] == "session":
            domain, project = extension["domain"], extension.get("project")
            try:
                if after_dispatch and extension.get("command") == "session close":
                    from .native_process import EXIT_OBSERVATION_SECONDS, observe_exit

                    # COM can unregister before the OS process finishes exiting.
                    # Wait only for the bound PID; another instance is unrelated.
                    budget = current_budget()
                    seconds = (
                        min(
                            EXIT_OBSERVATION_SECONDS,
                            budget.profile["os_wait_seconds"]["exit_observation"],
                            budget.remaining_seconds(),
                        )
                        if budget
                        else EXIT_OBSERVATION_SECONDS
                    )
                    identity = {
                        "application": observe_exit(
                            domain,
                            target["instance_id"],
                            timeout=seconds,
                            expected_identity=extension.get("process_identity"),
                        )
                    }
                    associated = []
                    for launcher in extension.get("owned_launcher_identities", []):
                        associated.append(
                            {
                                "process_identity": launcher,
                                "observation": observe_exit(
                                    domain,
                                    str(launcher["pid"]),
                                    timeout=0,
                                    expected_identity=launcher,
                                ),
                            }
                        )
                    facts["owned_launch_chain"] = {
                        "associated_processes": associated,
                        "normal_exit_complete": identity["application"].get(
                            "process_absence_observed"
                        )
                        is True
                        and all(
                            row["observation"].get("process_absence_observed") is True
                            for row in associated
                        ),
                        "scope": "actual_application_and_proven_launchers"
                        if associated
                        else "actual_application_only",
                    }
                else:
                    identity = self._identity(domain)
            except CLIError:
                from .native_process import processes

                if after_dispatch and extension.get("command") == "session close":
                    raise  # The bound OS observer cannot be replaced by a PID-only fallback.
                if extension.get("command") != "session close" or any(
                    str(item["pid"]) == target["instance_id"] for item in processes(domain)
                ):
                    raise
                identity = {"application": {"pid": None, "process_absence_observed": True}}
            application = identity.get("application") or {}
            instance = application.get("pid") or application.get("public_fields", {}).get(
                "InstanceGuid"
            )
            if str(instance) != target["instance_id"] and not application.get(
                "process_absence_observed"
            ):
                raise DriverError(
                    "E_CONFLICT",
                    "The native application instance changed.",
                    dispatch_state="not_started",
                )
            facts["application"] = application
            facts["worker_adapter"] = identity.get("adapter")
            if extension.get("drc_failure_binding"):
                from .drc_failure_observation import require_identity

                try:
                    require_identity(
                        extension["drc_failure_binding"],
                        identity,
                        facts["adapter"],
                        _owned_process_identity("pcb", str(application["pid"])),
                        closed=False,
                    )
                except ValueError as error:
                    raise DriverError(
                        "E_VERIFY_INCOMPLETE" if after_dispatch else "E_CONFLICT",
                        str(error),
                        dispatch_state="dispatched" if after_dispatch else "not_started",
                    ) from error
            if extension.get("designer_project_source") and not after_dispatch:
                from .designer_project_admission import build_target_binding

                fresh_source = build_target_binding(extension["process_identity"])
                if fresh_source != extension["designer_project_source"]:
                    raise CLIError("E_CONFLICT", "The confirmed first-open source changed")
                facts["designer_project_source"] = fresh_source
            if not after_dispatch:
                self._engineering_guard(
                    target,
                    application,
                    exclude_operation_id=extension.get("source_operation_id"),
                )
            if extension.get("command") in {"session attach", "session close"}:
                from .session_ownership import pointer_for

                expected_process = extension.get("process_identity") or {}
                facts["ownership_registry_pointer"] = pointer_for(domain, expected_process)
                bound_pointer = extension.get("ownership_pointer")
                if bound_pointer is not None and (
                    not after_dispatch or extension.get("ownership_recovery") is True
                ):
                    _context, observed_pointer = self._ownership(
                        domain,
                        expected_process,
                        source_id=bound_pointer["startup_operation_id"],
                    )
                    if observed_pointer != bound_pointer:
                        raise DriverError(
                            "E_CONFLICT",
                            "The original startup ownership record changed since planning",
                            details={"write_attempted": False},
                            dispatch_state="not_started",
                        )
            if (
                extension.get("command") in {"session close", "session attach"}
                and not after_dispatch
            ):
                actual_process = _owned_process_identity(domain, target["instance_id"])
                if actual_process != extension.get("process_identity"):
                    raise DriverError(
                        "E_CONFLICT",
                        "The owned native process identity changed before close.",
                        details={"write_attempted": False},
                        dispatch_state="not_started",
                    )
                facts["os_process_identity"] = actual_process
            if extension.get("command") == "session attach" and after_dispatch:
                facts["os_process_identity"] = _owned_process_identity(domain, application["pid"])
            if (
                project
                and not application.get("process_absence_observed")
                and not (after_dispatch and extension.get("command") == "session close")
                and (extension.get("command") != "session attach" or after_dispatch)
            ):
                with _native_binding(target):
                    if not extension.get("component_connect_request"):
                        facts["native"] = self.backend.invoke(
                            "snapshot",
                            {
                                "domain": domain,
                                "project": project,
                                "observe_only": True,
                                "start": False,
                                "pid": application.get("pid"),
                            },
                            timeout_seconds=profile_for(extension.get("command", "other"))[
                                "native_call_seconds"
                            ]["snapshot"],
                        )
                    if extension.get("native_routepass_request"):
                        facts["routepass_state"] = self.backend.invoke(
                            "routepass_state",
                            {
                                "project": project,
                                "routepass_request": extension["native_routepass_request"],
                            },
                            timeout_seconds=600.0,
                        )
                    if extension.get("native_general_request"):
                        from .board_general import material_request

                        facts["general_route_state"] = self.backend.invoke(
                            "routepass_state",
                            {
                                "project": project,
                                "routepass_request": material_request(
                                    extension["native_general_request"]
                                ),
                            },
                            timeout_seconds=600.0,
                        )
                    if extension.get("component_connect_request"):
                        request = extension["component_connect_request"]
                        observed = self.backend.invoke(
                            "board_info",
                            {
                                "project": project,
                                "start": False,
                                "geometry": True,
                                "pid": application["pid"],
                                "component_request": {
                                    "net": request["net"],
                                    "layer": request["layer"],
                                },
                            },
                            timeout_seconds=profile_for(extension.get("command", "other"))[
                                "native_call_seconds"
                            ]["board_info"],
                        )["observation"]
                        # Derived mask receipts and query digests are validated on each read;
                        # only their actual material inputs participate in drift revision.
                        facts["component_state"] = {
                            "board": observed["connectivity_components"]["board_id"],
                            "state": observed["native_routepass_state"],
                            "identity": observed["native_routepass_identity"],
                        }
                    if not (
                        extension.get("native_routepass_request")
                        or extension.get("native_general_request")
                        or extension.get("component_connect_request")
                    ) and (
                        extension.get("cell_refresh")
                        or extension.get("placement_batch")
                        or extension.get("command")
                        in {
                            "pcb checks run",
                            "pcb routing auto",
                            "pcb routing trace",
                            "pcb routing unroute",
                            "pcb copper pour",
                            "pcb silkscreen labels",
                        }
                    ):
                        facts["cell_refresh"] = self.backend.invoke(
                            "cell_refresh_state",
                            {
                                "project": project,
                                "observe_only": True,
                                "start": False,
                                "pid": application.get("pid"),
                                **(
                                    {"placement_text_witness": True}
                                    if extension.get("placement_batch")
                                    else {}
                                ),
                                **(
                                    {"strict": True}
                                    if extension.get("placement_batch")
                                    or extension.get("command")
                                    in {
                                        "pcb routing auto",
                                        "pcb routing trace",
                                        "pcb routing unroute",
                                        "pcb copper pour",
                                        "pcb silkscreen labels",
                                    }
                                    else {}
                                ),
                                **(
                                    {"routing_objects": True}
                                    if extension.get("command")
                                    in {"pcb routing unroute", "pcb routing trace"}
                                    else {}
                                ),
                                **(
                                    {"plane_objects": True}
                                    if extension.get("command") == "pcb copper pour"
                                    else {}
                                ),
                                **(
                                    {"label_objects": True}
                                    if extension.get("label_objects") is True
                                    else {}
                                ),
                            },
                            # Label protection performs two complete material
                            # reads plus native text envelopes/strokes. The RC
                            # two-read control already took 76.29 seconds.
                            timeout_seconds=600.0
                            if extension.get("placement_batch")
                            or extension.get("command") == "pcb checks run"
                            else 180.0
                            if extension.get("label_objects") is True
                            else 60.0,
                        )
                    if extension.get("outline_readback"):
                        facts["outline_state"] = self.backend.invoke(
                            "outline_state",
                            {
                                "project": project,
                                "start": False,
                                "observe_only": True,
                                "pid": application.get("pid"),
                            },
                            timeout_seconds=300.0,
                        )
                    if extension.get("command") in {"pcb snapshot", "pcb render"}:
                        facts["board_artifact_source"] = self.backend.invoke(
                            "board_geometry",
                            {
                                "project": project,
                                "start": False,
                                "observe_only": True,
                                "pid": application.get("pid"),
                                **extension.get("artifact_filters", {}),
                            },
                            timeout_seconds=profile_for(extension.get("command", "other"))[
                                "native_call_seconds"
                            ]["board_geometry"],
                        )
                    if extension.get("command") == "pcb view":
                        facts["board_view"] = self.backend.invoke(
                            "board_view_state",
                            {
                                "project": project,
                                **extension["view_options"],
                                "allow_unverified_scheme": after_dispatch,
                            },
                            timeout_seconds=60.0,
                        )
            source = "live"
        else:
            facts["target"] = _disk(Path(extension["path"]))
            source = "disk"
            if extension.get("designer_setup_template"):
                from .designer_source_binding import setup_source_binding

                scope_getter = getattr(self, "ownership_scope", None)
                if callable(scope_getter):
                    scope_getter().require(
                        effects=["observe"],
                        paths=[
                            (
                                str(Path(extension["designer_setup_template"]).parent),
                                "observe",
                            )
                        ],
                    )
                facts["designer_setup_source"] = setup_source_binding(
                    extension["designer_setup_template"]
                )
                if extension.get("command") == "project create":
                    from .project_create_budget import require_cost

                    facts["project_create_cost"] = require_cost(
                        facts["designer_setup_source"],
                        extension.get("project_create_cost"),
                    )
            if extension.get("command") == "project backup":
                from .cli.project import release_preflight

                # Freeze all live release identities in the SDK snapshot as
                # well as the handler token. Drift must refuse before dispatch.
                facts["release_preflight"] = release_preflight(Path(extension["path"]))
            if extension.get("command") == "session start":
                from .native_process import processes

                facts["native_processes"] = processes(extension["domain"])
                if extension["domain"] == "schematic":
                    recovery = (
                        start_generation_witness.observe_after(
                            self,
                            target,
                            self.ownership_scope(),
                            getattr(self, "ownership_record_get", None),
                            getattr(self, "ownership_validator", None),
                            config_dir(),
                        )
                        if start_generation_witness is not None
                        else previous_start_observation(
                            self,
                            self.ownership_scope(),
                            expected_summary=extension.get("previous_start_observation"),
                        )
                        if not after_dispatch or extension.get("previous_start_observation")
                        else None
                    )
                    summary = recovery.safe_summary if recovery is not None else None
                    if summary != extension.get("previous_start_observation"):
                        raise DriverError(
                            "E_CONFLICT",
                            "The original failed-start recovery evidence changed",
                            dispatch_state="not_started",
                        )
                    facts["previous_start_observation"] = summary
                    if not after_dispatch and facts["native_processes"]:
                        raise DriverError(
                            "E_CONFLICT",
                            "An existing Designer prevents a new dedicated generation",
                            dispatch_state="not_started",
                        )
                if after_dispatch:
                    try:
                        identity = self._identity(extension["domain"])
                        facts["application"] = identity.get("application")
                        facts["worker_adapter"] = identity["adapter"]
                        actual = facts["application"] or {}
                        if type(actual.get("pid")) is int and actual["pid"] > 0:
                            from .session import read_state

                            facts["os_process_identity"] = _owned_process_identity(
                                extension["domain"], actual["pid"]
                            )
                            stored = read_state()
                            facts["startup_provenance"] = stored.get("startup_provenance")
                            facts["recorded_process_identity"] = stored.get("process_identity")
                    except (CLIError, DriverError) as error:
                        facts["automation_unavailable"] = {"code": error.code}
            if extension.get("application"):
                identity = self._identity(extension["domain"])
                application = identity.get("application") or {}
                if application.get("pid") != extension["application"].get("pid"):
                    raise DriverError(
                        "E_CONFLICT",
                        "The bound native process changed.",
                        dispatch_state="not_started",
                    )
                facts["application"] = application
                facts["worker_adapter"] = identity["adapter"]
        for domain, expected in extension.get("secondary_applications", {}).items():
            actual = self._identity(domain)["application"]
            if not expected.get("pid") or actual.get("pid") != expected["pid"]:
                raise DriverError(
                    "E_CONFLICT",
                    "A required secondary application changed",
                    dispatch_state="not_started",
                )
            facts.setdefault("secondary_applications", {})[domain] = actual
        if extension.get("command") in {
            "session start",
            "session attach",
            "session close",
        }:
            facts["session_record"] = _disk(config_dir() / "session.json")
        for key in ("project_directory", "library_directory"):
            if extension.get(key):
                files = _project_files(Path(extension[key]), getattr(self, "state_dirs", None))
                facts[key] = {k: v for k, v in files.items() if k != "native_logs"}
                facts.setdefault("native_logs", []).extend(files["native_logs"])
        facts["artifact_observations"] = self._artifact_observations(
            extension.get("artifact_outputs", [])
        )
        if facts.get("board_view"):
            scheme = facts["board_view"]["scheme_plan"]
            facts["artifact_observations"] += self._artifact_observations(
                [
                    {"path": scheme[key]["path"]}
                    for key in ("target", "provenance")
                    if scheme.get(key)
                ]
                + [
                    {"path": str(Path(scheme["config_directory"]) / item["relative_path"])}
                    for item in scheme.get("native_files", [])
                    if item["relative_path"] != Path(scheme["target"]["path"]).name
                ]
            )
        revision = _digest(
            _stable(
                {k: v for k, v in facts.items() if k != "native_logs"},
                after_dispatch=after_dispatch,
            )
        )
        return {
            "id": "snapshot-" + revision[:24],
            "source": source,
            "revision": revision,
            "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "dependencies": [
                {"identity": item["path"], "revision": _digest(item)} for item in facts["inputs"]
            ],
            "extensions": {"facts": facts},
        }

    @_budgeted("confirm")
    @_designer_routed("revision")
    def revision(self, target: dict) -> str:
        self._revision_witness = None
        extension = target.get("extensions") or {}
        if _dedicated_designer_request(extension.get("command"), target=target):
            _require_dedicated_designer(target=target)
        if extension.get("host_bootstrap_request"):
            _require_designer_host_execution()
        if extension.get("command") == "pcb routing auto" and not (
            extension.get("native_routepass_request") or extension.get("native_general_request")
        ):
            raise DriverError(
                "E_UNSUPPORTED",
                "Legacy autorouting confirmations remain disabled before native observation.",
                details={"write_attempted": False},
                dispatch_state="not_started",
            )
        if (target.get("extensions") or {}).get("command") == "pcb constraints widths":
            raise DriverError(
                "E_UNSUPPORTED",
                "Widths confirmations remain disabled, including legacy eligible session plans; "
                "the complete native command contract is unverified.",
                details={
                    "write_attempted": False,
                    "can_apply": False,
                    "native_state": "not_observed",
                },
                dispatch_state="not_started",
            )
        expected = profile_for(extension.get("command", "other"))
        if extension.get("operation_budget_profile") != expected:
            raise DriverError(
                "E_CONFLICT",
                "The signed operation budget profile is missing or changed; make a fresh preview.",
                details={"write_attempted": False, "expected_budget_profile": expected},
                dispatch_state="not_started",
            )
        if extension.get("command") == "session close" and extension.get("diagnostic_exit_proof"):
            from .diagnostic_exit import before

            return before(self, target, stage="observe_confirm")["revision"]
        if extension.get("command") == "project create" and extension.get(
            "designer_setup_template"
        ):
            if type(extension.get("project_create_cost")) is not dict:
                raise DriverError(
                    "E_CONFLICT",
                    "The signed creation resource budget is missing",
                    details={"write_attempted": False},
                    dispatch_state="not_started",
                )
        from .native_com_adapter import _native_step

        try:
            # Reject already-unresolved related writes using the signed preview
            # identity before requesting another native observation.
            self._engineering_guard(
                target,
                extension.get("application") or {},
                exclude_operation_id=extension.get("source_operation_id"),
            )
            with (
                _observe_only(),
                _native_step("driver.confirm_observation"),
                operation_stage("observe_confirm"),
            ):
                observation = self._observe(target)
                self._revision_witness = {
                    "request_token": self._revision_request_token,
                    "source_operation_id": extension.get("source_operation_id"),
                    "target_digest": _digest(target),
                    "snapshot": observation,
                }
                return observation["revision"]
        except CLIError as error:
            raise DriverError(
                error.code,
                error.message,
                details=error.details or {},
                dispatch_state="not_started",
            ) from error

    @staticmethod
    def _artifact_observations(outputs: list[dict]) -> list[dict]:
        observed = {}
        for output in outputs:
            path = Path(output["path"])
            paths = [path]
            if output.get("siblings"):
                paths.extend(path.parent.glob(path.stem + "-*" + path.suffix))
            for candidate in paths:
                record = _disk(candidate)
                if record.get("files") is not None:
                    for entry in record["files"]:
                        full = str(candidate / entry["path"])
                        observed[full] = {**entry, "path": full}
                else:
                    observed[str(candidate)] = record
        return [observed[key] for key in sorted(observed)]

    @_budgeted("preview")
    @_designer_routed("preview")
    def plan(self, call) -> dict:
        command, options = by_path()[call.command], _options(call)
        if call.command == "session close" and options.get("diagnostic_operation_id"):
            from .diagnostic_exit import plan

            return plan(self, call, options)
        if _dedicated_designer_request(call.command, options):
            _require_dedicated_designer(call.command, options)
        options = self._ownership_options(call, options)
        pdf_expectation = None
        with _observe_only():
            try:
                if call.command == "pcb routing auto" and options.get("mode") != "bounded-general":
                    from .cli.pcb import native_route_options

                    native_route_options(options)
                with operation_stage("setup"):
                    target = self._target(call, command, options)
                    if call.command == "schematic export":
                        home = self.backend.status().get("sdd_home")
                        if not home or not Path(str(home)).is_absolute():
                            raise CLIError(
                                "E_BACKEND_UNAVAILABLE",
                                "PDF export requires the discovered absolute Xpedition SDD_HOME.",
                            )
                        producer = Path(str(home)).resolve() / "common/win64/bin/sch2pdf.exe"
                        target["extensions"]["pdf_producer_executable"] = str(producer)
                        target["extensions"]["input_paths"] = sorted(
                            set(
                                [
                                    *target["extensions"].get("input_paths", []),
                                    str(Path(str(options["project"])).expanduser().resolve()),
                                    str(producer),
                                ]
                            )
                        )
                target["extensions"]["operation_budget_profile"] = profile_for(call.command)
                target["extensions"]["source_operation_id"] = call.operation_id
                if call.command == "pcb diagnostics close":
                    options["_close_diagnostic_binding"] = target["extensions"][
                        "close_diagnostic_binding"
                    ]
                if call.command == "pcb view":
                    from .cli.common import comma_list

                    target["extensions"]["view_options"] = {
                        "scheme": options.get("scheme"),
                        "top_view": options.get("top_view"),
                        "refdes": comma_list(options, "refdes"),
                    }
                if call.command == "pcb snapshot":
                    from .cli.common import comma_list

                    target["extensions"]["artifact_filters"] = {
                        k: comma_list(options, k) for k in ("refdes", "nets")
                    }
                if options.get("output"):
                    target["extensions"]["artifact_outputs"] = [
                        {
                            "path": str(Path(str(options["output"])).expanduser().resolve()),
                            "siblings": call.command
                            in {"library preview", "schematic plan preview"},
                        }
                    ]
                    if call.command == "schematic view":
                        from .schematic_view_artifacts import expectation

                        target["extensions"]["schematic_view_artifact"] = expectation(
                            options, target["extensions"].get("application", {})
                        )
                if (
                    call.command == "pcb routing trace"
                    and options.get("mode") == "component-connect"
                    and options.get("recovery_backup")
                    and target["extensions"]["component_connect_request"]["first"]["component_id"]
                    != target["extensions"]["component_connect_request"]["second"]["component_id"]
                ):
                    target["extensions"]["artifact_outputs"] = [
                        {"path": str(Path(str(options["recovery_backup"])).expanduser().resolve())}
                    ]
                if call.command in {"pcb checks run", "schematic checks run"}:
                    target["extensions"]["artifact_outputs"] = [
                        {"path": str(config_dir() / "checks")}
                    ]
                if call.command == "pcb checks run":
                    from . import drc_engine, native_com_adapter

                    try:
                        board = native_com_adapter._layout_board_path(options)
                    except (
                        ValueError,
                        OSError,
                        native_com_adapter.AdapterError,
                    ) as error:
                        raise CLIError(
                            "E_VALIDATION",
                            "Cannot identify the checker output paths",
                            {"write_attempted": False},
                        ) from error
                    side_effects = drc_engine.planned_side_effects(board)
                    target["extensions"]["drc_side_effects"] = side_effects
                    from .drc_failure_observation import binding

                    try:
                        target["extensions"]["drc_failure_binding"] = binding(
                            target["extensions"]["project"],
                            board,
                            target["extensions"]["application"],
                            target["extensions"]["worker_adapter"],
                            adapter_identity(),
                            _owned_process_identity("pcb", target["instance_id"]),
                            side_effects,
                        )
                    except ValueError as error:
                        raise CLIError(
                            "E_CONFLICT",
                            str(error),
                            {"write_attempted": False},
                        ) from error
                    target["extensions"]["artifact_outputs"].extend(
                        {"path": row["path"]} for row in side_effects if row["observe_bytes"]
                    )
                with operation_stage("observe_plan"):
                    before = self._observe(target)
                    if call.command == "pcb checks run":
                        from .cell_refresh import digest

                        geometry = before["extensions"]["facts"]["cell_refresh"]["geometry"]
                        target["extensions"]["drc_failure_binding"].update(
                            geometry_before_sha256=digest(geometry),
                            source_before_snapshot=before["id"],
                        )
                    if call.command == "project create" and target["extensions"].get(
                        "designer_setup_template"
                    ):
                        facts = before["extensions"]["facts"]
                        target["extensions"]["project_create_cost"] = copy.deepcopy(
                            facts["project_create_cost"]
                        )
                        options["_project_create_cost"] = copy.deepcopy(
                            facts["project_create_cost"]
                        )
                        options["_project_create_source"] = copy.deepcopy(
                            facts["designer_setup_source"]
                        )
                    if call.command == "schematic export":
                        from .schematic_pdf_artifacts import expectation

                        project_path = str(Path(str(options["project"])).expanduser().resolve())
                        if not _pdf_source_complete(before, project_path):
                            raise CLIError(
                                "E_NATIVE_FAILURE",
                                "PDF export lacks a complete same-project "
                                "native source observation.",
                                {"write_attempted": False, "native_query_only": True},
                            )
                        if Path(str(options["output"])).suffix.lower() != ".pdf":
                            raise CLIError(
                                "E_VALIDATION",
                                "schematic export output must end in .pdf",
                            )
                        if options.get("color") is not None and int(options["color"]) not in range(
                            5
                        ):
                            raise CLIError("E_VALIDATION", "schematic export color must be 0 to 4")
                        if Path(str(options["output"])).exists() and not options.get("replace"):
                            raise CLIError(
                                "E_CONFLICT",
                                "PDF output already exists; use a new path "
                                "or an explicit replace preview.",
                            )
                        try:
                            pdf_expectation = expectation(
                                {**options, "_artifact_generation": call.operation_id},
                                before,
                                producer_executable=target["extensions"]["pdf_producer_executable"],
                            )
                        except (OSError, ValueError, TypeError) as error:
                            raise CLIError(
                                "E_VALIDATION",
                                "PDF export inputs could not be pinned completely.",
                                {
                                    "cause_type": type(error).__name__,
                                    "write_attempted": False,
                                },
                            ) from error
                with _native_binding(target), operation_stage("handler_preview"):
                    if command.extra.get("generic_gate"):
                        from .cli.effect_gate import invoke

                        planned = invoke(command, _handler(command), {**options, "dry_run": True})
                    else:
                        planned = _handler(command)({**options, "dry_run": True})
            except CLIError as error:
                code = error.code
                details = dict(error.details or {})
                if (
                    call.command == "pcb routing trace"
                    and options.get("mode") == "component-connect"
                    and code in {"E_VERIFY_FAILED", "E_VERIFY_INCOMPLETE"}
                ):
                    # A failed preview never dispatched engineering work. The
                    # applied-verification error schema requires a receipt and
                    # would otherwise hide this read's original diagnostics.
                    details.update(cause_code=code, write_attempted=False, native_query_only=True)
                    code = "E_NATIVE_FAILURE"
                raise DriverError(
                    code,
                    error.message,
                    details=details,
                    dispatch_state="not_started",
                ) from error
        preview = planned.get("preview") or {}
        if call.command == "pcb outline set":
            from .outline_readback import expectation

            try:
                expected_outline = expectation(
                    options, before["extensions"]["facts"]["outline_state"]["outlines"]
                )
            except (ValueError, KeyError, TypeError) as error:
                raise DriverError(
                    "E_NATIVE_FAILURE",
                    "Complete outline observation is required.",
                    details={"write_attempted": False},
                    dispatch_state="not_started",
                ) from error
            if expected_outline != preview.get("outline_plan"):
                raise DriverError(
                    "E_CONFLICT",
                    "Native outline preview disagrees with independent shape expectation.",
                    details={"write_attempted": False},
                    dispatch_state="not_started",
                )
        if call.command == "project backup" and _digest(
            before["extensions"]["facts"].get("release_preflight")
        ) != _digest(preview.get("release_plan")):
            raise DriverError(
                "E_CONFLICT",
                "Project release identities changed while preparing the backup preview.",
                details={"write_attempted": False},
                dispatch_state="not_started",
            )
        if call.command == "schematic edit":
            _schematic_save_metadata_binding(
                before["extensions"]["facts"].get("native", {}), preview
            )
        preview_artifacts = None
        if call.command in {"library preview", "schematic plan preview"}:
            from .preview_artifacts import expectation

            try:
                preview_artifacts = expectation(call.command, options, preview)
            except (CLIError, OSError, ValueError) as error:
                raise DriverError(
                    error.code if isinstance(error, CLIError) else "E_VALIDATION",
                    error.message if isinstance(error, CLIError) else str(error),
                    dispatch_state="not_started",
                ) from error
        if call.command in {"pcb snapshot", "pcb render", "pcb view"}:
            from .layout_reminders import report

            facts = before["extensions"]["facts"]
            source = (
                facts.get("board_view")
                if call.command == "pcb view"
                else facts.get("board_artifact_source")
            )
            preview.update(report((source or {}).get("model") or {}))
        token_folder = config_dir() / "handler-plans"
        token_folder.mkdir(parents=True, exist_ok=True)
        (token_folder / (call.operation_id + ".json")).write_text(
            json.dumps({"token": planned.get("confirm_token")}), encoding="utf-8"
        )
        changes = [
            {
                "action": str(item.get("action") or "execute"),
                "resource": target["locator"],
                "summary": json.dumps(item, ensure_ascii=False),
            }
            for item in preview.get("changes", [])
            if isinstance(item, dict)
        ]
        outputs = []
        if call.command == "pcb view":
            binding = before["extensions"]["facts"]["board_view"]["scheme_plan"]
            outputs.extend(
                {"path": p, "effect": "artifact", "exists": False} for p in binding["new_files"]
            )
            if binding["new_files"]:
                # Native SaveScheme also creates profile copies under this bound
                # directory. The preview names known candidates and the basename;
                # registration refuses every pre-existing same-name copy.
                outputs.append(
                    {
                        "path": binding["config_directory"],
                        "effect": "artifact",
                        "exists": True,
                    }
                )
        if options.get("output"):
            path = Path(str(options["output"])).expanduser().resolve()
            outputs.append({"path": str(path), "effect": "artifact", "exists": path.exists()})
            if call.command in {"library preview", "schematic plan preview"}:
                outputs.append(
                    {
                        "path": str(path.parent),
                        "effect": "artifact",
                        "exists": path.parent.exists(),
                    }
                )
        if (
            call.command == "pcb routing trace"
            and options.get("mode") == "component-connect"
            and options.get("recovery_backup")
            and preview.get("no_op") is not True
        ):
            recovery = Path(str(options["recovery_backup"])).expanduser().resolve()
            outputs.append(
                {
                    "path": str(recovery),
                    "effect": "artifact",
                    "exists": recovery.exists(),
                }
            )
        if call.command in {"pcb checks run", "schematic checks run"}:
            outputs.append(
                {
                    "path": str(config_dir() / "checks"),
                    "effect": "artifact",
                    "exists": (config_dir() / "checks").exists(),
                }
            )
        if call.command == "pcb checks run":
            outputs.extend(
                {
                    "path": row["path"],
                    "effect": row["effect"],
                    "exists": Path(row["path"]).exists(),
                }
                for row in target["extensions"]["drc_side_effects"]
            )
        if "persist" in call.effects or "change" in call.effects:
            if options.get("project"):
                path = Path(str(options["project"])).expanduser().resolve().parent
                # Designer checks write LogFiles reports under the project, not
                # an engineering-document Save. Keep that real output covered by
                # the checker command's already-declared artifact effect.
                project_effect = "artifact" if call.command == "schematic checks run" else "persist"
                outputs.append(
                    {
                        "path": str(path),
                        "effect": project_effect,
                        "exists": path.exists(),
                    }
                )
                if (
                    call.command.startswith("library ")
                    or call.command == "schematic build"
                    or (call.command == "pcb update" and options.get("refresh_cells"))
                ):
                    from .project_file import central_library

                    library = central_library(Path(str(options["project"])))
                    if library:
                        outputs.append(
                            {
                                "path": str(library.parent.resolve()),
                                "effect": "persist",
                                "exists": library.parent.exists(),
                            }
                        )
        return {
            "target": target,
            "base_snapshot": before,
            "level": "plan",
            "changes": changes,
            "outputs": outputs,
            "unknowns": [
                "Native persistence and independent checks remain unproven until observed.",
                *(
                    [
                        *preview.get("unknowns", []),
                        *[r["code"] for r in preview.get("blockers", [])],
                    ]
                    if call.command == "pcb constraints widths"
                    else []
                ),
                *(
                    [row["code"] for row in preview.get("trace", {}).get("blockers", [])]
                    if call.command == "pcb routing trace"
                    and options.get("mode") != "component-connect"
                    else []
                ),
            ],
            "constraints": [
                (
                    "A native read/plan cannot launch, open or switch a project. Unknown "
                    "dispatched operations must be reconciled before another write."
                ),
                *(
                    preview.get("constraints", [])
                    if call.command == "pcb constraints widths"
                    else []
                ),
                *(
                    [
                        "Current production trace is ineligible: do not retry the same blocked "
                        "configuration or use dangerous flags to bypass missing impact/rule proof. "
                        "A can_apply=false confirmation refusal is not evidence of geometry drift."
                    ]
                    if call.command == "pcb routing trace"
                    and options.get("mode") != "component-connect"
                    else []
                ),
            ],
            "trial_snapshot": None,
            "can_apply": preview.get("can_apply") is not False
            or (
                call.command == "pcb routing trace"
                and options.get("mode") == "component-connect"
                and preview.get("no_op") is True
                and preview.get("blockers") == []
            ),
            "extensions": {
                "legacy_preview": preview,
                "handler_plan_id": call.operation_id,
                **({"preview_artifacts": preview_artifacts} if preview_artifacts else {}),
                **({"schematic_pdf_artifact": pdf_expectation} if pdf_expectation else {}),
                **(
                    {"outline_expectation": expected_outline}
                    if call.command == "pcb outline set"
                    else {}
                ),
            },
            "_untrusted": [
                "extensions.legacy_preview",
                *(["extensions.preview_artifacts.images"] if preview_artifacts else []),
            ],
        }

    @_budgeted("confirm")
    @_designer_routed("confirm")
    def apply(self, call, plan) -> dict:
        target = plan.get("target") or {}
        if call.command == "session close" and call.params.get("diagnostic-operation-id"):
            from .diagnostic_exit import apply

            proof = (target.get("extensions") or {}).get("diagnostic_exit_proof") or {}
            if proof.get("diagnostic_id") != call.params["diagnostic-operation-id"]:
                raise DriverError(
                    "E_CONFLICT",
                    "The diagnostic exit selector changed",
                    details={"write_attempted": False},
                    dispatch_state="not_started",
                )
            return apply(self, call, plan)
        if call.command == "session close" and current_prepared_close() is not None:
            from .session import record_close_prepared_not_dispatched

            preparation = current_prepared_close()
            if preparation.safe_summary != target.get("extensions", {}).get(
                "previous_close_preparation"
            ):
                raise DriverError(
                    "E_CONFLICT",
                    "The original close preparation evidence changed",
                    dispatch_state="not_started",
                )
            record_close_prepared_not_dispatched(preparation)
        if _dedicated_designer_request(call.command, call.params, target) or (
            _dedicated_designer_request(
                (target.get("extensions") or {}).get("command"), target=target
            )
        ):
            _require_dedicated_designer(call.command, call.params, target)
        if (plan.get("target", {}).get("extensions") or {}).get("host_bootstrap_request") or (
            call.command == "session attach"
            and (call.params.get("host-bootstrap") or call.params.get("host_bootstrap"))
        ):
            _require_designer_host_execution()
        self._revalidate_empty_close(target, operation_id=call.operation_id)
        from .native_com_adapter import _native_step

        command, options = by_path()[call.command], _options(call)
        options = self._ownership_options(call, options)
        if call.command == "pcb diagnostics close":
            options["_close_diagnostic_binding"] = target["extensions"]["close_diagnostic_binding"]
        if call.command == "project create" and target.get("extensions", {}).get(
            "designer_setup_template"
        ):
            from .project_create_budget import require_cost

            facts = plan.get("base_snapshot", {}).get("extensions", {}).get("facts", {})
            promised = target["extensions"].get("project_create_cost")
            if type(promised) is not dict or facts.get("project_create_cost") != promised:
                raise DriverError(
                    "E_CONFLICT",
                    "The original creation resource budget is missing or changed",
                    details={"write_attempted": False},
                    dispatch_state="not_started",
                )
            options["_project_create_cost"] = require_cost(
                facts.get("designer_setup_source"), promised
            )
            options["_project_create_source"] = copy.deepcopy(facts["designer_setup_source"])
        from .pcb_document_guard import applies as engineering_guard_applies

        if engineering_guard_applies(plan["target"], getattr(self, "engineering_capabilities", {})):
            try:
                if call.command == "pcb diagnostics close":
                    # No COM request precedes this post-dispatch check. The
                    # cooperative PCB lock is held from before revision until
                    # this operation has been settled in the original ledger.
                    fresh = target["extensions"].get("application") or {}
                else:
                    fresh = self._identity("pcb").get("application") or {}
                self._engineering_guard(
                    plan["target"], fresh, exclude_operation_id=call.operation_id
                )
                if call.command == "pcb diagnostics close":
                    from .close_diagnostic import before_apply

                    before_apply(self, target, call.operation_id)
            except CLIError as error:
                raise DriverError(
                    error.code,
                    error.message,
                    details=error.details or {},
                    dispatch_state="not_started",
                ) from error
        token_record = config_dir() / "handler-plans" / (call.operation_id + ".json")
        if not token_record.is_file():
            raise DriverError(
                "E_CONFIG",
                "The handler's bound plan record is missing.",
                dispatch_state="not_started",
            )
        options["confirm"] = json.loads(token_record.read_text(encoding="utf-8"))["token"]
        options["_artifact_generation"] = call.operation_id
        if call.command == "schematic export":
            expected_pdf = plan.get("extensions", {}).get("schematic_pdf_artifact")
            if (
                not isinstance(expected_pdf, dict)
                or expected_pdf.get("generation_id") != call.operation_id
            ):
                raise DriverError(
                    "E_CONFLICT",
                    "The PDF plan lacks its original generation and source binding; "
                    "make a fresh preview.",
                    details={"write_attempted": False},
                    dispatch_state="not_started",
                )
            options["_pdf_expectation"] = expected_pdf
        if call.command in {"session close", "session attach"}:
            options["_expected_process_identity"] = plan["target"]["extensions"].get(
                "process_identity"
            )
        if call.command in {"session start", "session close", "session attach"}:
            options["_operation_id"] = call.operation_id
        start_generation_witness = None
        if call.command == "session start" and options.get("kind") == "schematic":
            from .designer_owner_recovery import observe_confirmed_start

            if plan.get("plan_id") != call.plan_id:
                raise DriverError(
                    "E_CONFLICT",
                    "The confirmed startup plan changed",
                    dispatch_state="not_started",
                )
            recovery, start_generation_witness = observe_confirmed_start(
                self,
                call.operation_id,
                plan,
                call.extras.get("scope"),
                getattr(self, "ownership_record_get", None),
                getattr(self, "ownership_validator", None),
                config_dir(),
            )
            if (recovery.safe_summary if recovery is not None else None) != target.get(
                "extensions", {}
            ).get("previous_start_observation"):
                raise DriverError(
                    "E_CONFLICT",
                    "The original failed-start recovery evidence changed",
                    dispatch_state="not_started",
                )
            if recovery is not None:
                options["_previous_start_observation"] = recovery
        before = plan["base_snapshot"]
        if call.command == "project backup":
            release_plan = plan.get("extensions", {}).get("legacy_preview", {}).get("release_plan")
            if not isinstance(release_plan, dict):
                raise DriverError(
                    "E_UNSUPPORTED",
                    "This backup plan lacks the required read-only release identities; replan.",
                    details={"write_attempted": False},
                    dispatch_state="not_started",
                )
            options["_expected_release_preflight"] = release_plan
        schematic_display = False
        schematic_ordinary = False
        display_operations = []
        display_save_metadata = None
        if call.command == "schematic edit":
            from .schematic_attribute_display import is_display_only_operations

            display_operations = (
                plan.get("extensions", {}).get("legacy_preview", {}).get("steps") or []
            )
            schematic_display = is_display_only_operations(display_operations)
            schematic_ordinary = bool(display_operations) and not schematic_display
            display_save_metadata = _schematic_save_metadata_binding(
                before["extensions"]["facts"].get("native", {}),
                plan["extensions"]["legacy_preview"],
            )
        result, cause = None, None
        drc_terminal_uncertain = False
        try:
            from .backends.native_xpedition import host_bootstrap_ownership

            host_packet = None
            if plan["target"]["extensions"].get("host_bootstrap_request"):
                host_packet, _pointer, _template = self._host_bootstrap_material(
                    options, call.extras.get("scope")
                )
                if host_packet != plan["target"]["extensions"]["host_bootstrap_target"]:
                    raise CLIError(
                        "E_CONFLICT",
                        "Original host-bootstrap scope or generation changed",
                        {"write_attempted": False},
                    )
            with (
                _native_binding(plan["target"]),
                host_bootstrap_ownership(host_packet),
                _native_step("driver.handler"),
                operation_stage("dispatch"),
            ):
                current_budget().dispatched = True
                result = _handler(command)(options)
        except CLIError as error:
            cause = error
            if call.command in {"pcb checks run", "pcb diagnostics close"}:
                result, drc_terminal_uncertain = _drc_failure_evidence(error)
            elif isinstance((error.details or {}).get("native_result"), dict):
                result = error.details["native_result"]
            if (error.details or {}).get("write_attempted") is False:
                raise DriverError(
                    error.code,
                    error.message,
                    details=error.details or {},
                    dispatch_state="not_started",
                ) from error
        if call.command == "pcb diagnostics close":
            from .close_diagnostic import finish

            return finish(self, call, plan, result, cause, drc_terminal_uncertain)
        if (
            not cause
            and isinstance(result, dict)
            and (
                any(result.get(key) is False for key in ("ok", "packaged"))
                or (result.get("applied") is False and result.get("no_change") is not True)
            )
        ):
            cause = CLIError(
                "E_NATIVE_FAILURE",
                "The native handler reported that the operation did not complete.",
                {"reported_result": result},
            )
        if call.command == "pcb checks run" and cause:
            from .drc_failure_observation import native_terminal_observed

            if drc_terminal_uncertain or not native_terminal_observed(result):
                reason = (
                    "Original DRC call terminal unobserved; no post-dispatch native read was "
                    "dispatched. Read operation show; do not replay, reopen or query CAD."
                )
                receipt = _unobserved_receipt(
                    call, result, cause, None, persistence_unverified=True
                )
                receipt["failed_stage"] = "apply"
                receipt["outcome"].update(execution="unknown", change="unknown")
                receipt["extensions"]["readback_error"] = None
                receipt["extensions"]["independent_readback"] = {
                    "status": "unobserved",
                    "native_request_dispatched": False,
                    "original_call_terminal_observed": False,
                    "reason": "original_drc_call_terminal_unobserved",
                }
                receipt["outcome"]["verification"]["reason"] = reason
                receipt["outcome"]["verification"]["not_checked"][0]["reason"] = reason
                receipt["recovery"]["reason"] = reason
                raise DriverError(
                    "E_OUTCOME_UNKNOWN",
                    reason,
                    receipt=receipt,
                    details={
                        "failed_stage": "dispatch",
                        "handler_error": cause.code,
                        "independent_readback": receipt["extensions"]["independent_readback"],
                    },
                    dispatch_state="dispatched",
                ) from cause
        if _owner_terminal_not_observed(cause, call.operation_id):
            reason = (
                "Independent CAD readback was not dispatched while "
                "the original terminal was unobserved"
            )
            receipt = _unobserved_receipt(call, result, cause, None)
            receipt["failed_stage"] = "apply"
            receipt["extensions"]["readback_error"] = None
            receipt["outcome"]["verification"]["reason"] = reason
            receipt["outcome"]["verification"]["not_checked"][0]["reason"] = reason
            receipt["recovery"]["reason"] = reason
            receipt["extensions"]["independent_readback"] = {
                "status": "unobserved",
                "native_request_dispatched": False,
                "reason": "original_owner_command_terminal_unobserved",
            }
            raise DriverError(
                cause.code,
                cause.message,
                receipt=receipt,
                details={
                    **cause.details,
                    "failed_stage": "dispatch",
                    "independent_readback": receipt["extensions"]["independent_readback"],
                },
                dispatch_state="dispatched",
            ) from cause
        try:
            with (
                _observe_only(),
                _native_step("driver.after_observation"),
                operation_stage("observe_after"),
            ):
                if call.command == "pcb checks run" and cause and isinstance(result, dict):
                    from .drc_failure_observation import observe

                    with _native_binding(plan["target"]):
                        observation = observe(
                            plan["target"]["extensions"].get("drc_failure_binding"),
                            result,
                            identity_reader=lambda: self._identity("pcb"),
                            process_reader=lambda pid: _owned_process_identity("pcb", str(pid)),
                            controller_reader=adapter_identity,
                        )
                    revision = _digest(observation)
                    after = {
                        "id": "snapshot-" + revision[:24],
                        "source": "live",
                        "revision": revision,
                        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                        "dependencies": [],
                        "extensions": {
                            "facts": {
                                "drc_failure_observation": observation,
                                "artifact_observations": [
                                    entry
                                    for row in observation["binding"]["side_effects"]
                                    for entry in observation["second"]["files"][row["role"]].get(
                                        "files",
                                        [observation["second"]["files"][row["role"]]],
                                    )
                                    if entry.get("sha256")
                                ],
                            }
                        },
                    }
                else:
                    after = (
                        self._observe(
                            plan["target"],
                            after_dispatch=True,
                            start_generation_witness=start_generation_witness,
                        )
                        if start_generation_witness is not None
                        else self._observe(plan["target"], after_dispatch=True)
                    )
        except Exception as error:
            raise DriverError(
                "E_OUTCOME_UNKNOWN",
                "The result could not be read back after dispatch.",
                receipt=_unobserved_receipt(
                    call, result, cause, error, persistence_unverified=schematic_display
                ),
                details={
                    "readback_error": type(error).__name__,
                    "handler_error": cause.code if cause else None,
                    "native_handler_returned": result is not None and cause is None,
                    "failed_stage": "observe_after",
                    "readback_details": getattr(error, "details", None),
                },
                dispatch_state="dispatched",
            ) from error

        with operation_stage("verify"):

            def observed_target(snapshot):
                return {
                    key: value
                    for key, value in snapshot["extensions"]["facts"].items()
                    if key not in {"adapter", "inputs", "artifact_observations", "native_logs"}
                }

            try:
                changed = _digest(_stable(observed_target(after), after_dispatch=True)) != _digest(
                    _stable(observed_target(before), after_dispatch=True)
                )
            except Exception as error:
                raise DriverError(
                    "E_OUTCOME_UNKNOWN",
                    "The returned native facts could not be independently verified after dispatch.",
                    receipt=_unobserved_receipt(
                        call,
                        result,
                        cause,
                        error,
                        persistence_unverified=schematic_display,
                    ),
                    details={
                        "readback_error": type(error).__name__,
                        "handler_error": cause.code if cause else None,
                        "native_handler_returned": result is not None and cause is None,
                        "failed_stage": "verify",
                        "readback_details": getattr(error, "details", None),
                    },
                    dispatch_state="dispatched",
                ) from error
            artifacts = []
            before_artifacts = {
                item["path"]: item
                for item in before["extensions"]["facts"].get("artifact_observations", [])
            }
            for observation in after["extensions"]["facts"].get("artifact_observations", []):
                path = Path(observation["path"])
                if observation.get("exists") and observation != before_artifacts.get(str(path)):
                    artifacts.append(
                        {
                            "id": "artifact-" + hashlib.sha256(str(path).encode()).hexdigest()[:20],
                            "path": str(path),
                            "sha256": observation["sha256"],
                            "source_snapshot_id": after["id"],
                        }
                    )
            artifact_preview = call.command in {
                "library preview",
                "schematic plan preview",
            }
            schematic_capture = call.command == "schematic view" and bool(options.get("output"))
            schematic_pdf = call.command == "schematic export"
            closed_copy = call.command == "project create" and options.get("closed_copy") is True
            native = (
                command.needs in {"designer", "layout", "xpedition"}
                and not artifact_preview
                and not closed_copy
            )
            modifies = bool(
                set(call.effects) & {"change", "persist", "configure", "deliver", "physical"}
            )
            change = (
                "partial"
                if cause and changed and modifies
                else "applied"
                if changed and modifies
                else "unknown"
                if native and "change" in call.effects
                else "not_applied"
            )
            if (
                cause
                and "session" in call.effects
                and not (modifies and changed)
                and (cause.details or {}).get("write_attempted") is not False
            ):
                # A later observation can fail after launch/attachment already ran.
                # Session effects are not engineering mutations in the pinned spec;
                # preserve uncertainty rather than claiming nothing was dispatched.
                change = "unknown"
            persistence = (
                "unknown"
                if native and "persist" in call.effects
                else "saved"
                if changed and "persist" in call.effects
                else "unchanged"
            )
            if call.command == "project backup" and cause:
                progress = _backup_release_progress(result, cause)
                if (cause.details or {}).get("release_outcome_unverified") is True or any(
                    isinstance(item, dict) and item.get("save_count") for item in progress.values()
                ):
                    # A returned Save/Close stage is retained in handler_error.
                    # A lost release reply can lack progress even if identical
                    # snapshots are readable; that is not proof no Save occurred.
                    persistence = "unknown"
            if isinstance(result, dict) and result.get("persistence_verified") is True:
                if "persist" in call.effects:
                    persistence = "saved"
                if "change" in call.effects and (
                    result.get("outcome") == "annotated" or result.get("applied") is True
                ):
                    change = "applied"
            if result and (
                (call.command == "pcb update" and options.get("refresh_cells"))
                or (
                    call.command == "pcb routing trace"
                    and options.get("mode") == "component-connect"
                )
                or call.command
                in {
                    "pcb checks run",
                    "pcb routing auto",
                    "pcb routing unroute",
                    "pcb copper pour",
                    "pcb silkscreen labels",
                }
            ):
                if result.get("change_status") in {
                    "not_applied",
                    "applied",
                    "partial",
                    "unknown",
                }:
                    change = result["change_status"]
                if result.get("persistence_state") in {
                    "unchanged",
                    "unsaved",
                    "saved",
                    "partial",
                    "unknown",
                }:
                    persistence = result["persistence_state"]
            # Native artifact/session work still has applicable verification even
            # when no engineering bytes change. Do not contradict required checks.
            status = "incomplete" if native else "not_applicable"
            verification = (result or {}).get("verification") or {}
            # Retain handler booleans as evidence; they do not establish native verification.
            if native and verification.get("status") in {"failed", "incomplete"}:
                status = verification["status"]
            checks = []
            file_readback = None
            session_verified = False
            domain_verified = False
            verification_scope = "canonical handler observation"
            if schematic_display:
                from .schematic_attribute_display import verify_display_changes

                # Read independently after the handler's own readback. Neither its
                # echoed operations nor its verification booleans establish these facts.
                file_readback = {
                    **verify_display_changes(
                        before["extensions"]["facts"].get("native", {}),
                        after["extensions"]["facts"].get("native", {}),
                        display_operations,
                        save_metadata=display_save_metadata,
                    ),
                    "source": "driver.after_observation.snapshot",
                    "snapshot_before_id": before["id"],
                    "snapshot_after_id": after["id"],
                    "persistence_verified": False,
                }
                persistence_readback = _schematic_display_persistence_readback(
                    result,
                    options,
                    before,
                    after,
                    display_operations,
                    save_metadata=display_save_metadata,
                )
                display_valid = all(
                    file_readback.get(key) is True
                    for key in (
                        "valid",
                        "complete",
                        "display_verified",
                        "protection_verified",
                    )
                )
                file_readback["persistence_readback"] = persistence_readback
                file_readback["persistence_verified"] = (
                    not cause and display_valid and persistence_readback["valid"] is True
                )
                file_readback["valid"] = file_readback["persistence_verified"]
                file_readback["complete"] = (
                    file_readback["complete"] and persistence_readback["complete"]
                )
                file_readback["scope"] += " after the declared edit/save/reopen lifecycle"
                if file_readback["persistence_verified"]:
                    file_readback["not_checked"] = [
                        item
                        for item in file_readback.get("not_checked", [])
                        if item != "independent_reopen_persistence"
                    ]
                complete = file_readback.get("complete") is True
                domain_verified = all(
                    file_readback.get(key) is True
                    for key in (
                        "valid",
                        "complete",
                        "display_verified",
                        "protection_verified",
                    )
                )
                status = "passed" if domain_verified else "failed" if complete else "incomplete"
                verification_scope = file_readback["scope"]
                persistence = (
                    "saved"
                    if file_readback["persistence_verified"]
                    else "unknown"
                    if "persist" in call.effects
                    else "unchanged"
                )
                checks = [
                    {
                        "id": identifier,
                        "status": "passed"
                        if file_readback.get(key) is True
                        else "failed"
                        if complete
                        else "incomplete",
                        "required": True,
                        "evidence_ref": after["id"],
                    }
                    for identifier, key in (
                        ("schematic-attribute-display-completeness", "complete"),
                        ("schematic-attribute-display-readback", "display_verified"),
                        (
                            "schematic-attribute-display-protection",
                            "protection_verified",
                        ),
                        ("schematic-attribute-display-reopen", "persistence_verified"),
                    )
                ]
            if schematic_ordinary:
                file_readback = _schematic_ordinary_persistence_readback(
                    result,
                    options,
                    before,
                    after,
                    display_operations,
                    save_metadata=display_save_metadata,
                )
                complete = file_readback.get("complete") is True
                domain_verified = not cause and all(
                    file_readback.get(key) is True
                    for key in (
                        "valid",
                        "complete",
                        "protection_verified",
                        "identity_verified",
                        "lifecycle_verified",
                    )
                )
                file_readback["persistence_verified"] = domain_verified
                status = "passed" if domain_verified else "failed" if complete else "incomplete"
                verification_scope = file_readback.get("scope") or (
                    "requested schematic edits, protected native records and normal save/reopen"
                )
                persistence = "saved" if domain_verified else "unknown"
                checks = [
                    {
                        "id": identifier,
                        "status": "passed"
                        if file_readback.get(key) is True and not cause
                        else "failed"
                        if complete
                        else "incomplete",
                        "required": True,
                        "evidence_ref": after["id"],
                    }
                    for identifier, key in (
                        ("schematic-edit-protected-records", "protection_verified"),
                        ("schematic-edit-host-and-document", "identity_verified"),
                        ("schematic-edit-save-close-open", "lifecycle_verified"),
                        ("schematic-edit-reopened-records", "valid"),
                    )
                ]
            if artifact_preview and not cause:
                from .preview_artifacts import verify

                file_readback = verify(
                    options,
                    result,
                    plan.get("extensions", {}).get("preview_artifacts"),
                    before["extensions"]["facts"].get("artifact_observations", []),
                    after["extensions"]["facts"].get("artifact_observations", []),
                )
                domain_verified = file_readback["valid"]
                status = "passed" if domain_verified else "failed"
                verification_scope = file_readback["scope"]
                checks = [
                    {
                        "id": "preview-image-readback",
                        "status": status,
                        "required": True,
                        "evidence_ref": after["id"],
                    }
                ]
            if schematic_capture and not cause:
                from . import win_dialogs
                from .schematic_view_artifacts import verify

                with _native_binding(plan["target"]):
                    file_readback = verify(
                        options,
                        result,
                        plan["target"]["extensions"].get("schematic_view_artifact"),
                        before["extensions"]["facts"],
                        after["extensions"]["facts"],
                        same_root=win_dialogs.same_root_window,
                    )
                domain_verified = file_readback["valid"]
                status = file_readback["status"]
                verification_scope = file_readback["scope"]
                checks = [
                    {
                        "id": "schematic-capture-readback",
                        "status": status,
                        "required": True,
                        "evidence_ref": after["id"],
                    }
                ]
            if schematic_pdf and not cause:
                from .schematic_pdf_artifacts import verify

                expected_pdf = plan["extensions"]["schematic_pdf_artifact"]
                file_readback = verify(before, after, result, expected_pdf)
                source_complete = all(
                    _pdf_source_complete(item, expected_pdf["project"]["path"])
                    for item in (before, after)
                )
                file_readback["checks"].append(
                    {
                        "id": "complete-native-source-observation",
                        "passed": source_complete,
                    }
                )
                file_readback["valid"] = bool(
                    file_readback["valid"]
                    and source_complete
                    and file_readback["native_protection"] == "passed"
                )
                domain_verified = file_readback["valid"]
                status = "passed" if domain_verified else "failed"
                verification_scope = (
                    "PDF artifact generation, producer/input hashes and page census; "
                    "unchanged complete captured native source"
                )
                checks = [
                    {
                        "id": item["id"],
                        "status": "passed" if item["passed"] else "failed",
                        "required": True,
                        "evidence_ref": after["id"],
                    }
                    for item in file_readback["checks"]
                ]
            if call.command == "pcb outline set" and not cause:
                from .outline_readback import verify

                file_readback = verify(
                    before["extensions"]["facts"],
                    after["extensions"]["facts"],
                    result,
                    plan["extensions"].get("outline_expectation"),
                )
                domain_verified = file_readback["valid"]
                status = "passed" if domain_verified else "incomplete"
                verification_scope = (
                    "complete signed native contours, full board material and separate "
                    "save-close-reopen evidence"
                )
                if domain_verified:
                    no_op = plan["extensions"]["outline_expectation"]["no_op"]
                    change = "not_applied" if no_op else "applied"
                    persistence = "unchanged" if no_op else "saved"
                checks = [
                    {
                        "id": "complete-native-outline-readback",
                        "status": status,
                        "required": True,
                        "evidence_ref": after["id"],
                    }
                ]
            if call.command == "project create" and not cause:
                if closed_copy:
                    from .project_isolated_copy import verify_copy

                    file_readback = verify_copy(result)
                else:
                    file_readback = _project_creation_readback(result, options, plan, after)
                domain_verified = file_readback["valid"]
                verification_scope = (
                    "complete closed-copy bytes, public library relocation and source protection; "
                    "native opening remains unproved"
                    if closed_copy
                    else "new project identity/database/design names after save/reopen"
                )
                if domain_verified:
                    persistence = "saved"
                    status = "passed"
                elif closed_copy:
                    status = "failed"
                checks = [
                    {
                        "id": "complete-closed-copy-bytes"
                        if closed_copy
                        else "project-reopen-readback",
                        "status": status,
                        "required": True,
                        "evidence_ref": after["id"],
                    }
                ]
            if call.command in {"library create", "library import"} and not cause:
                from .library_readback import verify_definitions

                try:
                    with _native_binding(plan["target"]), _observe_only():
                        file_readback = verify_definitions(call.command, options)
                    domain_verified = file_readback["valid"]
                    status = "passed" if domain_verified else "failed"
                    if domain_verified:
                        persistence = "saved"
                except (CLIError, OSError, ValueError) as error:
                    file_readback = {"valid": False, "read_error": str(error)}
                    status = "incomplete"
                verification_scope = (
                    "requested library definitions reopened from native database files"
                )
                checks = [
                    {
                        "id": "library-definition-readback",
                        "status": status,
                        "required": True,
                        "evidence_ref": after["id"],
                    }
                ]
            if call.command == "schematic build" and not cause:
                file_readback = _schematic_build_readback(result, plan, after)
                domain_verified = file_readback["valid"]
                status = "passed" if domain_verified else "failed"
                if domain_verified:
                    persistence = "saved"
                verification_scope = (
                    "planned component set and net connectivity after sheet save/reopen"
                )
                checks = [
                    {
                        "id": "schematic-netlist-readback",
                        "status": status,
                        "required": True,
                        "evidence_ref": after["id"],
                    }
                ]
            if call.command in {"pcb snapshot", "pcb render"} and not cause:
                from .board_artifacts import verify

                file_readback = verify(
                    call.command,
                    options,
                    result,
                    after["extensions"]["facts"].get("board_artifact_source", {}),
                    call.operation_id,
                )
                domain_verified = file_readback["valid"]
                status = "passed" if domain_verified else "failed"
                verification_scope = file_readback["scope"]
                checks = [
                    {
                        "id": "board-artifact-readback",
                        "status": status,
                        "required": True,
                        "evidence_ref": after["id"],
                    }
                ]
            if call.command == "pcb view" and not cause:
                from .board_view import verify

                file_readback = verify(
                    options,
                    result,
                    before["extensions"]["facts"].get("board_view", {}),
                    after["extensions"]["facts"].get("board_view", {}),
                    call.operation_id,
                )
                domain_verified = file_readback["valid"]
                status = "passed" if domain_verified else "incomplete"
                verification_scope = file_readback["scope"]
                checks = [
                    {
                        "id": "native-view-readback",
                        "status": status,
                        "required": True,
                        "evidence_ref": after["id"],
                    }
                ]
            if call.command == "pcb checks run" and cause:
                file_readback = after["extensions"]["facts"].get("drc_failure_observation")
                verification_scope = (
                    "closed-board failure evidence; no reopened engineering acceptance"
                )
                checks = [
                    {
                        "id": "drc-bound-closed-file-observation",
                        "status": "passed"
                        if (file_readback or {}).get("complete")
                        else "incomplete",
                        "required": True,
                        "evidence_ref": after["id"],
                    },
                    {
                        "id": "native-check-report-readback",
                        "status": "incomplete",
                        "required": True,
                    },
                ]
            if call.command == "pcb checks run" and not cause:
                from .drc_engine import outputs, read_reports

                try:
                    fresh = read_reports(
                        outputs(Path(result["pcb"])),
                        result["report_before"],
                        result["report_started_ns"],
                        setup_started_ns=result.get("setup_export_started_ns"),
                        selected=result.get("staged_input_configurations"),
                        layer_count=result.get("geometry_before", {})
                        .get("model", {})
                        .get("layers"),
                        engine_version=result.get("source", {}).get("checker_version"),
                        expected_board=Path(result["pcb"]),
                    )
                    source = after["extensions"]["facts"].get("cell_refresh", {}).get("geometry")
                    matched = bool(
                        result.get("verification", {}).get("evidence_complete")
                        and fresh.get("configuration_binding", {}).get("verified") is True
                        and result.get("reopened_hazards_match")
                        and fresh == result.get("native_reports")
                        and source == result.get("geometry_before")
                    )
                    file_readback = {
                        "valid": matched,
                        "native_reports": fresh,
                        "geometry_unchanged": source == result.get("geometry_before"),
                        "engineering_clean": result.get("clean"),
                    }
                except (OSError, ValueError, KeyError, TypeError) as error:
                    file_readback = {"valid": False, "read_error": type(error).__name__}
                domain_verified = file_readback["valid"]
                status = "passed" if domain_verified else "incomplete"
                verification_scope = (
                    "current native check completion and report integrity; "
                    "violations are findings, not a clean-design claim"
                )
                if domain_verified:
                    persistence = "saved"
                checks = [
                    {
                        "id": "native-check-report-readback",
                        "status": status,
                        "required": True,
                        "evidence_ref": after["id"],
                    }
                ]
            if (
                call.command
                in {
                    "schematic package",
                    "pcb create",
                    "pcb update",
                    "pcb placement move",
                    "pcb routing auto",
                    "pcb routing unroute",
                    "pcb copper pour",
                    "pcb silkscreen labels",
                }
                or (
                    call.command == "pcb routing trace"
                    and options.get("mode") == "component-connect"
                )
            ) and (
                not cause or (call.command in {"pcb routing unroute", "pcb copper pour"} and result)
            ):
                from .task_readback import verify_task

                file_readback = verify_task(call.command, options, result, before, after, plan)
                domain_verified = file_readback["valid"]
                status = "passed" if domain_verified else "incomplete"
                if (
                    options.get("refresh_cells")
                    and not domain_verified
                    and file_readback.get("checks")
                ):
                    status = "failed"
                if domain_verified:
                    persistence = "saved"
                    if change == "unknown":
                        change = "applied"
                    if call.command == "pcb update" and result.get("outcome") == "in_synch":
                        change = "not_applied"
                    if call.command == "pcb update" and result.get("no_change") is True:
                        change, persistence = "not_applied", "unchanged"
                    if call.command == "pcb routing unroute" and result.get("no_change") is True:
                        change, persistence = "not_applied", "unchanged"
                    if call.command == "pcb routing trace" and result.get("no_change") is True:
                        change, persistence = "not_applied", "unchanged"
                    if (
                        call.command == "pcb placement move"
                        and options.get("file")
                        and file_readback.get("no_change") is True
                    ):
                        change, persistence = "not_applied", "unchanged"
                verification_scope = file_readback["scope"]
                checks = [
                    {
                        "id": "native-task-readback",
                        "status": status,
                        "required": True,
                        "evidence_ref": after["id"],
                    }
                ]
            if options.get("host_bootstrap") and not cause:
                host_result = result.get("host_bootstrap") or {}
                dispatch = host_result.get("dispatch") or {}
                cleanup = host_result.get("cleanup") or {}
                matched = (
                    result.get("started") is False
                    and result.get("attached") is False
                    and result.get("authority_granted") is False
                    and result.get("native_attach_dispatched") is False
                    and result.get("native_open_dispatched") is False
                    and result.get("active_session_context_changed") is False
                    and result.get("script_completion_unproven") is False
                    and str(result.get("pid")) == plan["target"]["instance_id"]
                    and result.get("startup_operation_id")
                    == plan["target"]["extensions"]["ownership_pointer"]["startup_operation_id"]
                    and host_result.get("authority_granted") is False
                    and host_result.get("status") in {"observed", "incomplete"}
                    and host_result.get("template")
                    == plan["target"]["extensions"]["host_bootstrap_request"]["template"]
                    and host_result.get("runtime_capability_or_path_exported") is False
                    and host_result.get("engineering_write_attempted") is False
                    and type(dispatch.get("attempted")) is bool
                    and type(dispatch.get("returned")) is bool
                    and cleanup.get("worker_completed") is True
                    and cleanup.get("callback_references_quiescent") is True
                    and type(cleanup.get("inflight_callbacks_at_close")) is int
                    and cleanup.get("inflight_callbacks_at_close") == 0
                    and cleanup.get("mta_uninitialized") is True
                    and (
                        dispatch["attempted"] is False
                        or all(
                            cleanup.get(key) is True
                            for key in (
                                "closed_to_new_calls",
                                "reference_variables_cleared",
                                "files_removed",
                            )
                        )
                    )
                    and _stable(before["extensions"]["facts"])
                    == _stable(after["extensions"]["facts"], after_dispatch=True)
                )
                domain_verified = bool(matched)
                status = "passed" if matched else "incomplete"
                verification_scope = (
                    "fixed host-bootstrap diagnostic collection and unchanged original metadata; "
                    "no Designer admission or engineering acceptance"
                )
                checks = [
                    {
                        "id": "fixed-host-bootstrap-diagnostic-and-metadata-preservation",
                        "status": status,
                        "required": True,
                        "evidence_ref": after["id"],
                    }
                ]
            if (
                call.command in {"session start", "session attach", "session close"}
                and not options.get("host_bootstrap")
                and not cause
            ):
                from .cli.sessions import _verify_requested_document
                from .native_process import owned_startup_launchers, same_identity

                actual = after["extensions"]["facts"].get("application") or {}
                session_facts = after["extensions"]["facts"]
                expected = plan["target"]["extensions"].get("application") or {}
                if call.command == "session close":
                    lifecycle = result.get("lifecycle") or {}
                    matched = (
                        actual.get("process_absence_observed") is True
                        and result.get("closed") is True
                        and same_identity(
                            result.get("process_identity") or {},
                            plan["target"]["extensions"].get("process_identity") or {},
                        )
                        and lifecycle.get("empty_document_observed") is True
                        and type(lifecycle.get("quit_count")) is int
                        and lifecycle["quit_count"] == 1
                        and lifecycle.get("quit_dispatched") is True
                        and lifecycle.get("quit_reply_received") is True
                        and session_facts.get("owned_launch_chain", {}).get("normal_exit_complete")
                        is True
                        and (
                            not result.get("owned_launch_chain")
                            or result["owned_launch_chain"].get("normal_exit_complete") is True
                        )
                    )
                    if options.get("save_project"):
                        matched = matched and (
                            lifecycle.get("save_returned") is True
                            and lifecycle.get("save_return_type") == "bool"
                            and lifecycle.get("save_acknowledged") is True
                            and lifecycle.get("close_returned") is True
                            and lifecycle.get("close_return_type") == "bool"
                            and lifecycle.get("close_acknowledged") is True
                            and lifecycle.get("closed_observed") is True
                            and lifecycle.get("save_count") == lifecycle.get("close_count") == 1
                        )
                elif call.command == "session start":
                    process = session_facts.get("os_process_identity") or {}
                    provenance = result.get("startup_provenance") or {}
                    seed = result.get("startup_seed") or {}
                    matched = (
                        result.get("started") is True
                        and result.get("attached") is True
                        and result.get("pid") == actual.get("pid") == process.get("pid")
                        and same_identity(result.get("process_identity") or {}, process)
                        and same_identity(
                            session_facts.get("recorded_process_identity") or {},
                            process,
                        )
                        and provenance == session_facts.get("startup_provenance")
                        and provenance.get("ownership") == "proven_this_start"
                        and provenance.get("domain")
                        == seed.get("domain")
                        == plan["target"]["extensions"]["domain"]
                        and provenance.get("version") == seed.get("version")
                        and result.get("launcher_pid")
                        == (seed.get("launcher_identity") or {}).get("pid")
                        and same_identity(
                            provenance.get("launcher_identity") or {},
                            seed.get("launcher_identity") or {},
                        )
                        and isinstance((actual.get("public_fields") or {}).get("FullName"), str)
                        and Path(actual["public_fields"]["FullName"]).resolve()
                        == Path(str(process.get("executable", ""))).resolve()
                    )
                    try:
                        owned_startup_launchers(provenance, process)
                        _verify_requested_document(
                            actual,
                            options.get("project"),
                            plan["target"]["extensions"]["domain"],
                        )
                    except (ValueError, KeyError, TypeError, CLIError):
                        matched = False
                else:
                    process = session_facts.get("os_process_identity") or {}
                    matched = (
                        actual.get("pid") == expected.get("pid") == result.get("pid")
                        and bool(actual.get("pid"))
                        and same_identity(
                            process,
                            plan["target"]["extensions"].get("process_identity") or {},
                        )
                        and same_identity(process, result.get("process_identity") or {})
                    )
                    if options.get("project"):
                        # observe_only snapshot already checks that the requested document is open.
                        matched = matched and "native" in after["extensions"]["facts"]
                    if options.get("startup_operation_id"):
                        bound_pointer = plan["target"]["extensions"].get("ownership_pointer")
                        base_document = (
                            before["extensions"]["facts"].get("application") or {}
                        ).get("document")
                        matched = matched and (
                            isinstance(bound_pointer, dict)
                            and isinstance(base_document, dict)
                            and base_document.get("status") == "observed"
                            and type(base_document.get("open")) is bool
                            and base_document == result.get("application_document")
                            and base_document == actual.get("document")
                            and result.get("ownership_recovered") is True
                            and result.get("native_attach_dispatched") is False
                            and result.get("native_open_dispatched") is False
                            and result.get("active_session_context_changed") is False
                            and result.get("startup_operation_id")
                            == bound_pointer["startup_operation_id"]
                            and session_facts.get("ownership_registry_pointer") == bound_pointer
                            and session_facts.get("session_record")
                            == before["extensions"]["facts"].get("session_record")
                        )
                session_verified = bool(matched)
                status = "passed" if session_verified else "incomplete"
                checks = [
                    {
                        "id": "session-context-readback",
                        "status": status,
                        "required": True,
                        "evidence_ref": after["id"],
                    }
                ]
                if options.get("startup_operation_id"):
                    checks.append(
                        {
                            "id": "original-start-ownership-recovery",
                            "status": status,
                            "required": True,
                            "evidence_ref": after["id"],
                        }
                    )
            if call.command in {"kb bind", "kb unbind"} and not cause:
                # Reopen the bytes, rather than trusting knowledge_base.apply's acknowledgement.
                record = json.loads(
                    Path(plan["target"]["extensions"]["path"]).read_text(encoding="utf-8")
                )
                name = str(options["name"])
                actual = record["documents"].get(name)
                expected = plan["extensions"]["legacy_preview"]["changes"][0]["after"]
                matched = (
                    actual is None
                    if expected is None
                    else isinstance(actual, dict)
                    and all(actual.get(key) == expected[key] for key in ("url", "about"))
                )
                status = "passed" if matched else "failed"
                file_readback = {
                    "actual": actual,
                    "expected": expected,
                    "source": "reopened_configuration_file",
                }
                checks = [
                    {
                        "id": "configuration-entry-readback",
                        "status": status,
                        "required": True,
                        "evidence_ref": after["id"],
                    }
                ]
            if plan["target"]["extensions"].get("artifact_outputs") and not artifacts:
                status = "incomplete"
                checks.append(
                    {
                        "id": "artifact-change-readback",
                        "status": "incomplete",
                        "required": True,
                        "reason": (
                            "No new or changed output bytes were observed; existing files are not "
                            "proof of this operation."
                        ),
                    }
                )
            for check in checks:
                if check["status"] != "passed":
                    check.setdefault(
                        "reason",
                        "The required native/file readback was not established.",
                    )
            fields = {
                "snapshot_after": after,
                "outcome": {
                    "execution": "failed" if cause else "completed",
                    "change": change,
                    "persistence": persistence,
                    "delivery": "not_applicable",
                    "verification": {
                        "status": status,
                        "scope": "session process/project context readback"
                        if session_verified
                        else verification_scope
                        if domain_verified
                        or schematic_capture
                        or schematic_display
                        or schematic_ordinary
                        or (call.command == "pcb checks run" and cause)
                        else "canonical handler observation",
                        "snapshot_id": after["id"],
                        "coverage": "complete"
                        if session_verified or domain_verified
                        else "partial"
                        if native
                        else "complete"
                        if checks
                        else "none",
                        "checks": checks,
                        "not_checked": [
                            {
                                "id": "native-independent-acceptance",
                                "required": not (session_verified or domain_verified),
                                "reason": (
                                    "Independent native task acceptance has not been established."
                                ),
                            }
                        ]
                        if native and not (schematic_capture or schematic_display or schematic_pdf)
                        else [
                            {"id": limitation, "required": False, "reason": limitation}
                            for limitation in (file_readback or {}).get("not_checked", [])
                        ]
                        if artifact_preview
                        or schematic_capture
                        or schematic_display
                        or schematic_pdf
                        else [],
                        "evidence_refs": [after["id"]],
                        "reason": (
                            "Only the process/project context was checked; CAD task acceptance "
                            "and license expiry are outside this session check."
                        )
                        if session_verified
                        else (
                            "The declared scope was checked against fresh native/file observations."
                        )
                        if domain_verified
                        else (
                            "Requested image delivery or capture-context readback "
                            "was not established."
                        )
                        if schematic_capture
                        else "Native verification remains incomplete."
                        if native
                        else (
                            "Only the declared file helper was checked; CAD/native acceptance is "
                            "outside this check."
                        ),
                    },
                },
                "changed_items": [
                    {
                        "id": plan["target"]["object_id"],
                        "change": "updated",
                        "evidence_ref": after["id"],
                    }
                ]
                if changed and modifies
                else [],
                "artifacts": artifacts,
                "failed_stage": "apply"
                if cause
                else "verify"
                if status in {"failed", "incomplete"}
                else None,
                "recovery": {
                    "action": "inspect",
                    "supported": True,
                    "reason": (
                        "Read the actual target and reconcile partial/unknown operations; "
                        "never replay the entire request."
                    ),
                },
                "extensions": {
                    "result": result,
                    **(
                        {"save_acknowledged": _save_acknowledged(call.command, result or {})}
                        if call.command == "pcb checks run"
                        else {}
                    ),
                    "handler_error": {
                        "code": cause.code,
                        "message": cause.message,
                        "details": cause.details,
                    }
                    if cause
                    else None,
                    "file_readback": file_readback,
                    "context_or_file_state_changed": changed,
                    "persistence_independently_reopened": bool(
                        (file_readback or {}).get("persistence_verified")
                    )
                    if schematic_display
                    or schematic_ordinary
                    or call.command == "pcb outline set"
                    or (call.command == "pcb placement move" and options.get("file"))
                    else bool((file_readback or {}).get("persistence_valid"))
                    if call.command in {"pcb routing unroute", "pcb copper pour"}
                    else False
                    if closed_copy
                    else domain_verified is True
                    and (file_readback or {}).get("persistence_verified") is True
                    if call.command == "pcb routing trace"
                    and options.get("mode") == "component-connect"
                    else False,
                },
                "_untrusted": ["extensions", "snapshot_after.extensions"],
            }
            if cause:
                details = dict(cause.details or {})
                if cause.code == "E_HUMAN_REQUIRED":
                    details.setdefault(
                        "resume",
                        f"xpedition-cli operation show --id {call.operation_id}",
                    )
                    details.setdefault(
                        "resume_argv", ["operation", "show", "--id", call.operation_id]
                    )
                raise DriverError(
                    cause.code,
                    cause.message,
                    receipt=fields,
                    details=details,
                    dispatch_state="dispatched",
                ) from cause
            return fields


def runtime(adapter=None, **options) -> DriverCLI:
    lock = json.loads((VENDOR.parent / "vendor-lock.json").read_text(encoding="utf-8"))
    for relative, expected in lock["files"].items():
        path = VENDOR.parent / relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Pinned SDK bytes changed: " + relative)
    path = resources.locate("contract/driver-v2/product_contract.json")
    if path is None:
        raise ValueError("Generated product contract is missing")
    contract = json.loads(path.read_text(encoding="utf-8"))
    adapter = adapter or Adapter()
    state_dir = options.pop("state_dir", config_dir() / "driver-v2")
    if isinstance(adapter, Adapter):
        adapter.state_dirs = [config_dir(), state_dir]
    cli = XpeditionDriverCLI(
        contract,
        adapter,
        validator=ContractValidator(VENDOR),
        state_dir=state_dir,
        **options,
    )
    if isinstance(adapter, Adapter):
        adapter.ownership_record_get = lambda operation_id: cli._store_open().get(operation_id)
        adapter.ownership_validator = cli.validator
        adapter.ownership_scope_id = lambda: cli._scope().scope_id
        adapter.ownership_scope = cli._scope
        from .pcb_document_guard import read_active_records

        adapter.engineering_active_records = lambda exclude=None: read_active_records(
            cli._store_open(), exclude_operation_id=exclude
        )
        # Diagnostic copy provenance, history and the engineering guard all use
        # this exact SDK-owned store, not a separately reconstructed path.
        adapter.diagnostic_store = cli._store_open
        adapter.engineering_capabilities = {
            item["path"]: item["driver"] for item in contract["reference"]["commands"]
        }
    return cli


def main(argv=None) -> int:
    try:
        cli = runtime()
    except (OSError, ValueError) as error:
        import time

        from .output import emit, failure

        problem = CLIError(
            "E_CONFIG",
            "The pinned driver runtime cannot be loaded",
            {
                "cause": type(error).__name__,
                "dispatch_state": "not_started",
                "next_action": "Inspect the pinned runtime files; no native work was dispatched.",
            },
        )
        emit(failure(problem, time.perf_counter()), "json", False)
        return problem.exit_code
    with cli:
        output = cli.run(list(sys.argv[1:] if argv is None else argv))
    try:
        return output.write()
    except OSError:
        # The SDK has already persisted the operation. A lost stdout consumer is
        # a transport failure, not a new configuration or native execution error.
        try:
            sys.stderr.write(
                "Reply delivery failed. Use operation show with the preview's operation ID; "
                "do not repeat the write.\n"
            )
        except OSError:
            pass
        return 1
