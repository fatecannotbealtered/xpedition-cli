from __future__ import annotations

import copy
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .audit import config_dir


def _state_path() -> Path:
    return config_dir() / "session.json"


def _write_state(state: dict[str, Any]) -> None:
    path = _state_path()
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def record_native_start(result: dict[str, Any]) -> None:
    owner = _owner_fields(result)
    _write_state(
        {
            "backend": "native_xpedition",
            "state": "started",
            "session_id": f"native-{result.get('pid')}" if result.get("pid") else None,
            "pid": result.get("pid"),
            "executable": result.get("executable"),
            "domain": result.get("domain"),
            "process_identity": result.get("process_identity"),
            "startup_provenance": result.get("startup_provenance"),
            "startup_operation_id": result.get("startup_operation_id"),
            "requested_project": result.get("requested_project"),
            "application_document": result.get("application_document"),
            "xpedition_process": True,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **owner,
        }
    )


def _owner_fields(result: dict[str, Any], *, operation_id=None) -> dict[str, Any]:
    """Keep a public locator and peer context, never the private owner capability."""
    reference = result.get("owner_ref")
    previous = read_state()
    source_id = operation_id or result.get("startup_operation_id")
    if reference is not None:
        from .designer_owner_client import validate_owner_ref

        reference = validate_owner_ref(reference)
    if reference is None and result.get("domain") != "schematic":
        from .designer_owner_recovery import schematic_context

        ended = schematic_context(previous)
        if ended.get("state") == "closed" and ended.get("owner_ref") is not None:
            return {"retained_peer_context": copy.deepcopy(ended)}
        return {}
    same_start = source_id and previous.get("startup_operation_id") == source_id
    if (
        same_start
        and reference is not None
        and previous.get("owner_ref") is not None
        and previous["owner_ref"] != reference
    ):
        from .errors import CLIError

        raise CLIError("E_CONFLICT", "The original startup owner locator changed")
    fields = {"owner_ref": reference} if reference is not None else {}
    association_name = (
        "previous_session_generation"
        if result.get("previous_session_generation") is not None
        else "previous_failed_start"
    )
    association = result.get(association_name)
    if same_start:
        for name in (
            "previous_failed_start",
            "previous_failed_start_context",
            "previous_session_generation",
            "previous_session_generation_context",
            "previous_close_preparation",
            "previous_close_preparation_context",
            "active_bound_project",
            "project_source_operation_id",
            "project_admission_operation_id",
            "project_admission_binding",
            "project_admission_observation",
        ):
            if name in previous:
                fields[name] = copy.deepcopy(previous[name])
    elif association is not None:
        from .designer_owner_recovery import schematic_context

        source = schematic_context(previous)
        ended_close = (
            isinstance(association, dict)
            and association_name == "previous_session_generation"
            and association.get("protocol") == "designer-owned-close-ended-v1"
            and source.get("state") == "closing"
            and source.get("closing_operation_id") is not None
            and association.get("original_close_operation_id") == source.get("closing_operation_id")
        )
        if (
            not isinstance(association, dict)
            or association.get("protocol")
            not in (
                {"designer-failed-start-closed-v1"}
                if association_name == "previous_failed_start"
                else {"designer-owned-close-ended-v1"}
                if ended_close
                else {"designer-owned-session-ended-v1"}
            )
            or association.get("original_startup_operation_id")
            != source.get("startup_operation_id")
            or association.get("original_owner_ref") != source.get("owner_ref")
            or source.get("state")
            not in (
                {"startup_incomplete"}
                if association_name == "previous_failed_start"
                else {"closing"}
                if ended_close
                else {"started", "stale", "closed"}
            )
        ):
            from .errors import CLIError

            raise CLIError("E_CONFLICT", "The previous failed-start association changed")
        fields[association_name] = copy.deepcopy(association)
        fields[association_name + "_context"] = copy.deepcopy(previous)
    retained = (
        previous.get("retained_peer_context")
        if same_start or (association is not None and previous.get("domain") == "schematic")
        else previous
        if previous
        else None
    )
    if retained:
        fields["retained_peer_context"] = retained
    return fields


def record_native_attach(result: dict[str, Any]) -> None:
    from .native_process import same_identity

    previous = read_state()
    retained_start = (
        previous.get("startup_provenance")
        if (
            same_identity(
                result.get("process_identity") or {}, previous.get("process_identity") or {}
            )
            and isinstance(previous.get("startup_provenance"), dict)
            and previous["startup_provenance"].get("ownership") == "proven_this_start"
        )
        else None
    )
    owner = {}
    if (
        retained_start
        and result.get("domain") == previous.get("domain")
        and previous.get("owner_ref") is not None
    ):
        from .designer_owner_client import validate_owner_ref

        owner = {
            "owner_ref": validate_owner_ref(previous["owner_ref"]),
            "startup_operation_id": previous.get("startup_operation_id"),
        }
        if previous.get("retained_peer_context"):
            owner["retained_peer_context"] = previous["retained_peer_context"]
        for name in (
            "previous_failed_start",
            "previous_failed_start_context",
            "previous_session_generation",
            "previous_session_generation_context",
        ):
            if name in previous:
                owner[name] = copy.deepcopy(previous[name])
    _write_state(
        {
            "backend": "native_xpedition",
            "state": result.get("state", "attached"),
            "session_id": f"native-{result['pid']}" if result.get("pid") else None,
            "pid": result.get("pid"),
            "domain": result.get("domain"),
            "identity": result.get("identity"),
            "process_identity": result.get("process_identity"),
            "startup_provenance": retained_start,
            "launcher_ownership": "proven_this_start"
            if retained_start
            else "unknown_external_launcher",
            "requested_project": result.get("requested_project"),
            "application_document": result.get("application_document"),
            "xpedition_process": result.get("state", "attached") == "attached",
            "attached_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **owner,
        }
    )


def record_native_start_incomplete(
    result: dict[str, Any], *, operation_id=None, cause=None
) -> None:
    """Retain a dispatched launch without claiming the actual COM app is owned."""
    owner = _owner_fields(result, operation_id=operation_id)
    _write_state(
        {
            "backend": "native_xpedition",
            "state": "startup_incomplete",
            "domain": result.get("domain"),
            "pid": result.get("pid"),
            "launcher_pid": result.get("launcher_pid"),
            "launcher_identity": result.get("launcher_identity"),
            "startup_seed": result.get("startup_seed"),
            "process_identity": result.get("process_identity"),
            "startup_provenance": result.get("startup_provenance"),
            "requested_project": result.get("requested_project"),
            "xpedition_process": False,
            "startup_operation_id": operation_id,
            "cause": cause,
            **owner,
        }
    )


def read_state() -> dict[str, Any]:
    """The recorded session, or an empty mapping when there is none to read."""
    try:
        value = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def record_native_timeout(method: str) -> None:
    """Mark the session suspect after a native call timed out.

    A timed-out call leaves Designer in a state its own `IsProjectOpened()` then
    misreports, and the next command fails on something unrelated -- asking
    Designer to open a project it already has, which it refuses with a message
    about scripts and GUIs. Recording the timeout lets the next command say what
    actually happened instead.
    """
    state = read_state()
    _write_state(
        {
            **state,
            "backend": "native_xpedition",
            "state": "stale",
            "timed_out_method": str(method),
            "timed_out_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )


def record_native_closing(
    domain: str,
    identity: dict,
    *,
    operation_id: str | None = None,
    outcome: dict | None = None,
    ownership_record: dict | None = None,
) -> None:
    """Retain the original Quit attempt until OS exit has independently verified."""
    state = read_state()
    retained = state.get("retained_peer_context")
    if ownership_record is not None:
        from .native_process import same_identity

        owner = {}
        if ownership_record.get("owner_ref") is not None:
            from .designer_owner_client import validate_owner_ref

            owner["owner_ref"] = validate_owner_ref(ownership_record["owner_ref"])
        if (
            state.get("owner_ref") is not None
            and state.get("domain") == domain == ownership_record.get("domain")
            and same_identity(state.get("process_identity") or {}, identity)
            and state.get("startup_operation_id") == ownership_record.get("startup_operation_id")
        ):
            from .designer_owner_client import validate_owner_ref

            reference = validate_owner_ref(state["owner_ref"])
            if owner.get("owner_ref", reference) != reference:
                from .errors import CLIError

                raise CLIError("E_CONFLICT", "The original startup owner locator changed")
            owner["owner_ref"] = reference
            for name in (
                "previous_failed_start",
                "previous_failed_start_context",
                "previous_session_generation",
                "previous_session_generation_context",
                "previous_close_preparation",
                "previous_close_preparation_context",
                "active_bound_project",
                "project_source_operation_id",
                "project_admission_operation_id",
                "project_admission_binding",
                "project_admission_observation",
            ):
                if name in state:
                    owner[name] = copy.deepcopy(state[name])
        if state.get("domain") != domain or not same_identity(
            state.get("process_identity") or {}, identity
        ):
            retained = state if state else None
        state = {**ownership_record, **owner, "retained_peer_context": retained}
    _write_state(
        {
            **state,
            "backend": "native_xpedition",
            "state": "closing",
            "domain": domain,
            "pid": identity["pid"],
            "process_identity": identity,
            "closing_operation_id": operation_id or state.get("closing_operation_id"),
            "closing_started_at": state.get("closing_started_at")
            or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "close_outcome": outcome,
        }
    )
    written = read_state()
    if (
        written.get("state") != "closing"
        or written.get("process_identity") != identity
        or (operation_id is not None and written.get("closing_operation_id") != operation_id)
    ):
        from .errors import CLIError

        raise CLIError("E_IO", "Cannot retain the original close attempt; no Quit may be replayed")


def record_native_failure(error: dict[str, Any]) -> None:
    _write_state(
        {
            "backend": "native_xpedition",
            "state": "crashed",
            "session_id": None,
            "pid": error.get("pid"),
            "executable": error.get("executable"),
            "xpedition_process": False,
            "error_code": error.get("code"),
            "error_details": error.get("details", {}),
            "failed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )


def record_close_prepared_not_dispatched(observation) -> None:
    """Append a proved metadata preparation fact; never resolve/replay a CAD call."""
    from .errors import CLIError
    from .session_ownership import PreparedCloseObservation

    previous = read_state()
    if type(observation) is not PreparedCloseObservation or not observation.matches(previous):
        raise CLIError(
            "E_CONFLICT",
            "The original close preparation changed",
            {
                "write_attempted": False,
                "state_write_attempted": False,
                "native_request_dispatched": False,
            },
        )
    state = {
        **previous,
        "state": "close_prepared_not_dispatched",
        "previous_close_preparation": observation.safe_summary,
        "previous_close_preparation_context": copy.deepcopy(previous),
    }
    _write_state(state)
    if read_state() != state:
        raise CLIError(
            "E_IO",
            "The close preparation observation could not be retained",
            {
                "write_attempted": False,
                "state_write_attempted": True,
                "native_request_dispatched": False,
            },
        )


def clear_native(*, closed_observation=None) -> None:
    state = read_state()
    identity, domain = state.get("process_identity"), state.get("domain")
    if domain == "schematic" and state.get("owner_ref") is not None:
        from .designer_owner_client import validate_owner_ref
        from .errors import CLIError
        from .native_process import owned_startup_launchers, same_identity

        validate_owner_ref(state["owner_ref"])
        if state.get("state") == "closed" and closed_observation is None:
            return  # Retain the ended source; another application's start cannot erase it.
        outcome = closed_observation if isinstance(closed_observation, dict) else {}
        lifecycle = outcome.get("lifecycle") or {}
        if (
            state.get("state") != "closing"
            or not re.fullmatch(r"op-[A-Za-z0-9_-]{1,100}", str(state.get("startup_operation_id")))
            or not re.fullmatch(r"op-[A-Za-z0-9_-]{1,100}", str(state.get("closing_operation_id")))
            or not same_identity(identity or {}, outcome.get("process_identity") or {})
            or not isinstance(state.get("startup_provenance"), dict)
            or state["startup_provenance"].get("ownership") != "proven_this_start"
            or outcome.get("closed") is not True
            or outcome.get("process_absence_observed") is not True
            or (outcome.get("owned_launch_chain") or {}).get("normal_exit_complete") is not True
            or lifecycle.get("empty_document_observed") is not True
            or type(lifecycle.get("quit_count")) is not int
            or lifecycle["quit_count"] != 1
            or lifecycle.get("quit_dispatched") is not True
            or lifecycle.get("quit_reply_received") is not True
        ):
            raise CLIError(
                "E_CONFLICT",
                "A dedicated owner requires its complete terminal close observation",
                {"write_attempted": False, "state_write_attempted": False},
            )
        try:
            owned_startup_launchers(state["startup_provenance"], identity)
        except (ValueError, KeyError, TypeError):
            raise CLIError(
                "E_CONFLICT", "The terminal owner startup provenance is incomplete"
            ) from None
        ended = {
            **state,
            "state": "closed",
            "xpedition_process": False,
            "closed_observation": copy.deepcopy(outcome),
            "sdk_close_receipt_status": "not_checked_by_metadata_transition",
        }
        _write_state(ended)
        if read_state() != ended:
            raise CLIError("E_IO", "The ended owner context could not be retained")
        return
    if identity is not None and domain is not None:
        from .session_ownership import remove

        remove(domain, identity)
    peer = state.get("retained_peer_context")
    if isinstance(peer, dict) and peer:
        _write_state(peer)
        if read_state() != peer:
            from .errors import CLIError

            raise CLIError("E_IO", "The retained peer session context could not be restored")
        return
    try:
        _state_path().unlink(missing_ok=True)
    except OSError:
        pass


def _pid_alive(pid: Any) -> bool:
    if pid is None:
        return False
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    if os.name == "nt":
        return _pid_alive_windows(value)
    try:
        os.kill(value, 0)
        return True
    except OSError:
        return False


def _pid_alive_windows(pid: int) -> bool:
    """Probe liveness through the Win32 API.

    ``os.kill(pid, 0)`` is not a liveness probe on Windows: ``signal.CTRL_C_EVENT``
    is 0, so CPython routes signal 0 to ``GenerateConsoleCtrlEvent``, which takes a
    process *group* id, delivers a console Ctrl+C when one matches, and fails with
    ERROR_INVALID_PARAMETER for an ordinary pid — reporting every GUI process, including
    a running Xpedition, as dead.
    """
    import ctypes
    from ctypes import wintypes

    SYNCHRONIZE = 0x00100000
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    WAIT_TIMEOUT = 0x00000102

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    handle = kernel32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # A live process owned by another integrity level denies SYNCHRONIZE but
        # still answers a query-only open; only a missing pid fails both.
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


def status() -> dict[str, Any]:
    """The session this tool recorded, with a started process checked for being alive."""
    state = read_state()
    state_name = str(state.get("state", "not_started"))
    pid = state.get("pid")
    generation_matches = None
    if state_name in {"started", "attached", "running"} and state.get("process_identity"):
        from .native_process import process_identity, same_identity

        try:
            generation_matches = same_identity(
                process_identity(state.get("domain"), pid), state["process_identity"]
            )
        except (OSError, ValueError, KeyError, TypeError):
            generation_matches = False
        if generation_matches is False:
            state_name = "identity_changed"
    if state_name in {"started", "attached", "running"} and pid is not None and not _pid_alive(pid):
        state_name = "crashed"
    return {
        "state": state_name,
        "session_id": str(state["session_id"]) if state.get("session_id") is not None else None,
        "pid": pid,
        "xpedition_process": bool(state.get("xpedition_process", False))
        and generation_matches is not False
        and (pid is None or _pid_alive(pid)),
        "reason": (
            (
                "recorded native startup failed"
                if state.get("error_code")
                else "recorded native process is no longer running"
            )
            if state_name == "crashed"
            else "recorded native process generation no longer matches"
            if state_name == "identity_changed"
            else None
        ),
        "_untrusted": ["session_id", "pid", "reason"],
    }
