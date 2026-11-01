"""Narrow native producer for the reviewed one-Save/one-Close entrance.

No generic attach/snapshot helpers: they can quiet the GUI, probe another ProgID,
or read CAD after a failed Close. GetActiveObject here has exactly one target.
"""

from __future__ import annotations

import ctypes
import gc
import math
import time

from . import close_diagnostic as D
from . import drc_failure_observation as F
from .cell_refresh import digest
from .errors import CLIError
from .native_identity import (
    adapter_identity,
    diagnostic_content_request,
    invalidate_diagnostic_content,
    same_diagnostic_candidate,
)
from .native_process import process_identity


def _pid(hwnd):
    value = ctypes.c_ulong()
    if type(hwnd) is not int or not hwnd:
        raise ValueError("Diagnostic requires Layout.Gui.HWND")
    window_pid = ctypes.windll.user32.GetWindowThreadProcessId
    window_pid.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
    window_pid.restype = ctypes.c_ulong
    window_pid(hwnd, ctypes.byref(value))
    if value.value <= 0:
        raise ValueError("Layout window has no observed process")
    return value.value


def _exit_checkpoint(params):
    deadline = params.get("_diagnostic_exit_deadline")
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise CLIError("E_VALIDATION", "A bounded diagnostic exit worker deadline is required")

    def checkpoint():
        if time.monotonic() >= deadline:
            raise CLIError(
                "E_TIMEOUT",
                "Diagnostic exit worker deadline expired; no further CAD request",
                {"replay_allowed": False},
            )

    return checkpoint


def read_state(client, params, *, checkpoint=lambda: None):
    """Fixed public getters. No licensing, GUI writes or activation fallback."""
    pid, progid = params.get("pid"), params.get("progid")
    if type(pid) is not int or pid <= 0 or not D.PROGID.fullmatch(str(progid)):
        D.refuse("An explicit owned PID and versioned Layout ProgID are required")
    checkpoint()
    app = client.GetActiveObject(progid)  # One lookup. Never Dispatch Layout.
    checkpoint()
    info = app._oleobj_.GetTypeInfo()
    checkpoint()
    library, _ = info.GetContainingTypeLib()
    checkpoint()
    attrs = library.GetLibAttr()
    checkpoint()
    executable = app.FullName
    checkpoint()
    version = app.Version
    checkpoint()
    gui = app.Gui
    checkpoint()
    hwnd = gui.HWND
    observed_pid = _pid(hwnd)
    if observed_pid != pid:
        D.refuse("The single registered Layout is not the requested process")
    process = process_identity("pcb", pid)
    if F._path(executable) != F._path(process["executable"]):
        D.refuse("Layout and OS executable identities differ")
    checkpoint()
    doc = app.ActiveDocument
    checkpoint()
    path = doc.FullName if doc is not None else None
    checkpoint()
    library_name = library.GetDocumentation(-1)[0]
    checkpoint()
    value = {
        "adapter": adapter_identity(),
        "automation_progid": progid,
        "application": {
            "pid": pid,
            "product_version": version,
            "public_fields": {"FullName": executable, "Version": version},
            "type_library": {
                "guid": str(attrs[0]),
                "lcid": attrs[1],
                "syskind": attrs[2],
                "major": attrs[3],
                "minor": attrs[4],
                "flags": attrs[5],
                "name": library_name,
            },
            "document": {"status": "observed", "open": doc is not None, "path": path},
        },
        "process": process,
    }
    return value, app, doc


@diagnostic_content_request()
def exit_empty(client, params):
    """One Quit of the exact diagnostic-owned empty process, with OS-only wait."""
    from .native_process import EXIT_OBSERVATION_SECONDS, ProcessHandle, same_identity

    checkpoint = _exit_checkpoint(params)
    bound = params.get("binding")
    if (
        not isinstance(bound, dict)
        or bound.get("diagnostic_contract") != D.CONTRACT
        or bound.get("reopen_authorized") is not False
    ):
        D.refuse("A confirmed diagnostic exit binding is required")
    try:
        wait = float(params.get("exit_wait_seconds", EXIT_OBSERVATION_SECONDS))
        if not math.isfinite(wait) or not 0 <= wait <= EXIT_OBSERVATION_SECONDS:
            raise ValueError("exit_wait_outside_existing_bound")
        identity, app, doc = read_state(
            client,
            {"pid": bound["generation"]["pid"], "progid": bound["progid"]},
            checkpoint=checkpoint,
        )
        invalidate_diagnostic_content()
        F.require_identity(
            bound,
            identity,
            adapter_identity(),
            identity["process"],
            closed=True,
            candidate_match=same_diagnostic_candidate,
        )
        expected = bound["generation"]["process"]
        if doc is not None or not same_identity(identity["process"], expected):
            raise ValueError("empty_owned_generation_unavailable")
        pinned = ProcessHandle("pcb", expected["pid"], expected)
    except CLIError:
        raise
    except Exception as error:
        D.refuse("Empty exit preflight failed: " + type(error).__name__)
    lifecycle = {
        "save_count": 0,
        "close_count": 0,
        "quit_count": 0,
        "empty_document_observed": False,
        "quit_dispatched": False,
        "quit_reply_received": False,
    }
    try:
        checkpoint()
        if pinned.exited():
            D.refuse("Owned process ended before the empty exit")
        checkpoint()
        if app.ActiveDocument is not None:
            D.refuse("The original Layout document is no longer empty")
        checkpoint()
        gui = app.Gui
        checkpoint()
        hwnd = gui.HWND
        gui = None
        checkpoint()
        if _pid(hwnd) != expected["pid"] or not same_identity(
            process_identity("pcb", expected["pid"]), expected
        ):
            D.refuse("The original Layout process changed before Quit")
        invalidate_diagnostic_content()
        if not same_diagnostic_candidate(bound["worker"], adapter_identity()):
            D.refuse("The diagnostic worker changed before Quit")
        checkpoint()
        lifecycle.update(empty_document_observed=True, quit_count=1, quit_dispatched=True)
        try:
            app.Quit()
        except Exception as error:
            raise CLIError(
                "E_OUTCOME_UNKNOWN",
                "The single Quit has no observed reply",
                {"native_result": {"lifecycle": lifecycle}, "write_attempted": True},
            ) from error
        lifecycle["quit_reply_received"] = True
        checkpoint()
        app = doc = None
        gc.collect()
        started, checks = time.monotonic(), 0
        while True:
            checkpoint()
            ended = pinned.exited()
            checks += 1
            elapsed = time.monotonic() - started
            if ended or elapsed >= wait:
                break
            time.sleep(min(0.2, wait - elapsed))
        return {
            "contract": "owned-diagnostic-empty-exit-v1",
            "binding_sha256": digest(bound),
            "process_identity": expected,
            "closed": ended,
            "process_absence_observed": ended,
            "closing_pending": not ended,
            "lifecycle": lifecycle,
            "exit_observation": {
                "source": "original pinned Windows process handle/GetProcessTimes",
                "observations": checks,
                "elapsed_seconds": round(elapsed, 3),
                "budget_seconds": wait,
                "com_unregistration_is_exit_evidence": False,
            },
        }
    finally:
        pinned.close()


@diagnostic_content_request()
def execute(client, params):
    bound = params.get("binding")
    if not isinstance(bound, dict) or bound.get("diagnostic_contract") != D.CONTRACT:
        D.refuse("A confirmed diagnostic binding is required")
    if bound.get("reopen_authorized") is not False:
        D.refuse("Diagnostic has no reopen authorization")
    try:
        identity, app, doc = read_state(
            client, {"pid": bound["generation"]["pid"], "progid": bound["progid"]}
        )
        # Do not let the state-read observation cache mask code or resource
        # changes at the worker's independent pre-Save identity boundary.
        invalidate_diagnostic_content()
        F.require_identity(
            bound,
            identity,
            adapter_identity(),
            identity["process"],
            closed=False,
            candidate_match=same_diagnostic_candidate,
        )
        from .closed_copy_startup import worker_verify

        def verify_source():
            # An omitted transition cannot silently choose legacy genesis when
            # the durable row records a new-protocol preparation.
            if not worker_verify(bound):
                for role in ("project", "board"):
                    if (
                        D._record(bound[role])["sha256"]
                        != bound["copy_proof"]["genesis"][role + "_sha256"]
                    ):
                        raise ValueError("Diagnostic source bytes changed before Save")

        verify_source()
        # The only activation is the ordinary document automation license server;
        # never a Layout application or an MGCPCBEngines class.
        key = doc.Validate(0)
        licensing = client.Dispatch("MGCPCBAutomationLicensing.Application")
        token = licensing.GetToken(key)
        doc.Validate(token)
        del key, token, licensing
        active = app.ActiveDocument
        if active is None or F._path(active.FullName) != bound["board"]:
            raise ValueError("The original board ceased to be active before Save")
        if F._path(doc.FullName) != bound["board"]:
            raise ValueError("The licensed document differs from the original board")
        if process_identity("pcb", identity["process"]["pid"]) != identity["process"]:
            raise ValueError("The original process generation changed before Save")
        verify_source()
    except CLIError:
        raise
    except Exception as error:
        D.refuse("Diagnostic pre-Save binding failed: " + type(error).__name__)
    lifecycle = {
        "save_count": 0,
        "close_count": 0,
        "save_acknowledged": False,
        "save_returned": False,
        "close_returned": False,
    }
    result = {
        "contract": D.CONTRACT,
        "binding_sha256": digest(bound),
        "board_lifecycle": lifecycle,
        "terminal_state": "unknown",
        "persistence_verified": False,
        "reopen_attempted": False,
    }

    def unknown(stage, error=None):
        if error is not None:
            # No COM getters here, including error-object property extraction.
            result["failure"] = {"stage": stage, "type": type(error).__name__}
        raise CLIError(
            "E_OUTCOME_UNKNOWN",
            "Diagnostic native terminal is unobserved",
            {"native_result": result, "write_attempted": True},
        ) from error

    lifecycle["save_count"] = 1
    try:
        saved = doc.Save()
    except Exception as error:
        unknown("Save", error)
    lifecycle["save_returned"] = True
    lifecycle["save_result_type"] = type(saved).__name__
    if type(saved) is not bool:
        unknown("Save_untyped_return")
    lifecycle["save_result"] = saved
    lifecycle["save_acknowledged"] = saved
    if saved is False:
        result["terminal_state"] = "returned"
        return result  # A known refusal; no Close is sent.
    lifecycle["close_count"] = 1
    try:
        closed = doc.Close(True)
    except Exception as error:
        unknown("Close", error)
    lifecycle["close_returned"] = True
    lifecycle["close_result_type"] = type(closed).__name__
    if type(closed) is not bool:
        unknown("Close_untyped_return")
    lifecycle["close_result"] = closed
    result["terminal_state"] = "returned"
    return result  # Even BOOL True does not justify a getter in this request.
