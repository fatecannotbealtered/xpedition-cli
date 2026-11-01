"""Read-only NativeOM candidate ownership from the original SDK startup record.

This context authorizes no writer. A process ID or the active-session file is
never sufficient: the bounded registry pointer and original startup receipt are
revalidated by the runtime's existing ownership selector before any candidates
are supplied to the native observer.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

from .errors import CLIError
from .native_process import ProcessHandle, owned_startup_launchers, process_identity, same_identity
from .session import read_state


def _unavailable(reason: str) -> dict:
    return {
        "status": "unavailable",
        "domain": "schematic",
        "reason": reason,
        "authority_granted": False,
        "generations": [],
    }


def _launcher_observation(expected: dict) -> dict:
    with ProcessHandle("schematic", expected["pid"], check_name=False) as pinned:
        return {"identity": copy.deepcopy(pinned.identity), "alive": not pinned.exited()}


def collect(
    resolve, require_observe, *, state_reader=None, process_reader=None, launcher_reader=None
):
    """Return only exact live generations validated through one original start.

    All readers are injectable for fake-only tests. No COM, window inspection,
    state mutation, new startup, history scan or fallback to another domain is
    performed here.
    """
    if not callable(resolve) or not callable(require_observe):
        return _unavailable("original_scope_or_reader_missing")
    state_reader = state_reader or read_state
    process_reader = process_reader or process_identity
    launcher_reader = launcher_reader or _launcher_observation
    try:
        state = state_reader()
        if (
            not isinstance(state, dict)
            or state.get("state") not in {"attached", "started"}
            or state.get("domain") != "schematic"
            or type(state.get("pid")) is not int
            or state["pid"] <= 0
        ):
            return _unavailable("active_schematic_generation_missing")
        actual = process_reader("schematic", state["pid"])
        if not same_identity(actual, state.get("process_identity") or {}):
            return _unavailable("active_generation_changed")
        context, pointer = resolve("schematic", actual)
        if not isinstance(pointer, dict):
            return _unavailable("original_start_pointer_recovery_required")
        if (
            pointer.get("domain") != "schematic"
            or not same_identity(pointer.get("process_identity") or {}, actual)
            or context.get("domain") != "schematic"
            or not same_identity(context.get("process_identity") or {}, actual)
            or context.get("startup_operation_id") != pointer.get("startup_operation_id")
            or not re.fullmatch(r"op-[a-zA-Z0-9_-]{1,80}", str(pointer.get("startup_operation_id")))
            or not re.fullmatch(r"[0-9a-f]{64}", str(pointer.get("source_sha256")))
        ):
            return _unavailable("original_start_context_mismatch")
        project = context.get("requested_project")
        if not isinstance(project, str) or not Path(project).is_absolute():
            return _unavailable("original_start_project_missing")
        require_observe(project)
        launchers = owned_startup_launchers(context.get("startup_provenance"), actual)
        if len(launchers) > 1:
            return _unavailable("original_start_generation_bound_exceeded")
        generations = [{**copy.deepcopy(actual), "role": "application"}]
        ended = 0
        for expected in launchers:
            try:
                observed = launcher_reader(expected)
            except OSError as error:
                if getattr(error, "winerror", None) == 87 or getattr(error, "errno", None) == 87:
                    ended += 1
                    continue
                raise
            if not isinstance(observed, dict) or type(observed.get("alive")) is not bool:
                return _unavailable("original_launcher_observation_incomplete")
            if not observed["alive"] or not same_identity(observed.get("identity") or {}, expected):
                ended += 1  # An ended/reused historical generation supplies no window candidate.
                continue
            generations.append({**copy.deepcopy(expected), "role": "launcher"})
        return {
            "status": "verified",
            "domain": "schematic",
            "authority_granted": False,
            "startup_operation_id": pointer["startup_operation_id"],
            "source_sha256": pointer["source_sha256"],
            "verification_basis": "original_sdk_start_receipt_and_registry",
            "project": project,
            "generations": generations,
            "ended_original_launcher_generations": ended,
        }
    except CLIError as error:
        reason = ((error.details or {}).get("lifecycle_diagnostic") or {}).get("reason")
        return _unavailable(
            reason
            if isinstance(reason, str) and re.fullmatch(r"[a-z0-9_]{1,100}", reason)
            else "original_start_or_scope_validation_failed"
        )
    except Exception:
        return _unavailable("original_start_ownership_read_failed")
