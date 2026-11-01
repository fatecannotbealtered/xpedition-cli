"""Supported LibraryManager attachment and LMC partition observations.

The installed file launcher establishes the release environment. COM activation
of LocalServer32 is deliberately not attempted. Queries never launch or switch
a library; an explicitly confirmed library write may launch an owned manager.
"""

from __future__ import annotations

import hashlib
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from .native_identity import application_identity, application_progids
from .native_process import processes

# LibraryManager.tlb MGCLMObjectType, distinct from LibraryServices' enum.
PARTITION_TYPES = {"parts": 1, "symbols": 2, "cells": 3}


def _error(code, message, details=None):
    from .native_com_adapter import AdapterError

    return AdapterError(code, message, details)


def active(client):
    """Attach only to registered identities; never Dispatch a guessed version."""
    causes = []
    for progid in application_progids("library"):
        try:
            return client.GetActiveObject(progid), progid
        except Exception as exc:
            causes.append({"progid": progid, "hresult": getattr(exc, "hresult", None)})
    raise _error(
        "E_NOT_FOUND", "No active LibraryManager automation object was found", {"causes": causes}
    )


def search_schemes(library):
    """Read bounded native scheme membership; never infer it from registration."""
    from .native_com_adapter import AdapterError, _com_error

    try:
        collection = library.SchemeList
        count = int(collection.Count)
        if not 0 <= count <= 100:
            raise _error("E_CONFLICT", "Native search-scheme count cannot be safely enumerated")
        partitions = {
            kind: list(library.Partitions(value)) for kind, value in PARTITION_TYPES.items()
        }
        rows = []
        for index in range(1, count + 1):
            scheme = collection.Item(index)
            rows.append(
                {
                    "name": str(scheme.Name),
                    "partitions": {
                        kind: [
                            {
                                "name": str(partition.Name),
                                "included": bool(scheme.IsPartitionInScheme(partition)),
                            }
                            for partition in items
                        ]
                        for kind, items in partitions.items()
                    },
                }
            )
        return {
            "status": "observed",
            "items": rows,
            "source": "SchemeList.Count/Item and Scheme.IsPartitionInScheme native getters",
        }
    except Exception as exc:
        error = exc if isinstance(exc, AdapterError) else _com_error(exc, "read_search_schemes")
        return {
            "status": "not_observed",
            "items": None,
            "diagnostics": [
                {"code": error.code, "message": error.message, "details": error.details}
            ],
        }


def snapshot(app, lmc, progid):
    """Read the actual central-library registry, not filenames in PartsDBLibs."""
    target = Path(lmc).resolve()
    library = app.ActiveLibrary
    actual = str(library.FullName) if library is not None else None
    if actual is None or Path(actual).resolve() != target:
        raise _error(
            "E_CONFLICT",
            "LibraryManager is attached to a different library; it was not switched",
            {"expected_library": str(target), "observed_library": actual},
        )
    partitions = {}
    for kind, value in PARTITION_TYPES.items():
        collection = library.Partitions(value)
        rows = [
            {"name": str(item.Name), "reservation": str(item.Reservation), "count": int(item.Count)}
            for item in collection
        ]
        if len(rows) != int(collection.Count):
            raise _error("E_CONFLICT", "The native partition enumeration was incomplete")
        partitions[kind] = rows
    return {
        "status": "observed",
        "library": actual,
        "lmc_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "automation_progid": progid,
        "application": application_identity(app, "library"),
        "partitions": partitions,
        "search_schemes": search_schemes(library),
        "source": "LibraryManager ActiveLibrary.FullName/Partitions "
        "MGCLMObjectType/Count/Name/Reservation",
        "not_checked": [
            "partition reservation acquisition",
            "packager consumption",
            "PCB instances",
        ],
    }


def observe(client, lmc):
    """A read-only optional diagnostic. Missing or wrong instances remain unknown."""
    from .native_com_adapter import AdapterError, _com_error

    try:
        app, progid = active(client)
        return snapshot(app, lmc, progid)
    except Exception as exc:
        error = exc if isinstance(exc, AdapterError) else _com_error(exc, "read_lmc_partitions")
        return {
            "status": "not_observed",
            "library": str(lmc),
            "error": {"code": error.code, "message": error.message, "details": error.details},
            "source": "LibraryManager attachment only; no activation or library switch",
        }


def _cleanup_owned(app, pid, report):
    """Attempt each owned cleanup step independently; retain exit uncertainty."""
    from .native_com_adapter import _com_error

    report.update(owned=True, pid=pid, steps=[], quit_requested=False)

    def attempt(action, function):
        try:
            value = function()
            report["steps"].append({"action": action, "status": "completed"})
            return value
        except Exception as exc:
            error = _com_error(exc, action)
            report["steps"].append(
                {
                    "action": action,
                    "status": "failed",
                    "code": error.code,
                    "message": error.message,
                    "details": error.details,
                }
            )
            return None

    library = attempt("ActiveLibrary", lambda: app.ActiveLibrary)
    if library is not None:
        attempt("Close", lambda: library.Close())
    else:
        report["steps"].append(
            {"action": "Close", "status": "not_attempted", "reason": "No library handle available"}
        )
    attempt("Quit", lambda: app.Quit())
    report["quit_requested"] = report["steps"][-1]["status"] == "completed"
    try:
        alive = any(row["pid"] == pid for row in processes("library"))
        report["process_exit"] = {
            "status": "running" if alive else "exited",
            "source": "Toolhelp32 process observation after shutdown request",
            "reason": "COM references can retain the process until adapter release"
            if alive
            else None,
        }
    except Exception as exc:
        report["process_exit"] = {"status": "not_observed", "error_type": type(exc).__name__}
    report["status"] = (
        "incomplete"
        if any(row["status"] == "failed" for row in report["steps"])
        or report["process_exit"]["status"] != "exited"
        else "completed"
    )


@contextmanager
def connected(client, lmc, *, startup_seconds=30.0, cleanup_report=None):
    """For a confirmed write: reuse an exact target or start one owned launcher.

    A pending process prevents duplicate startup. Only a manager whose native
    process is a child of this invocation's launcher may be closed on exit.
    """
    from .native_com_adapter import AdapterError, _native_executable

    if client is None:
        raise _error("E_BACKEND_UNAVAILABLE", "A native COM client is required for LibraryManager")
    owned = False
    owned_pid = None
    primary_error = None
    cleanup = cleanup_report if cleanup_report is not None else {}
    app = None
    launcher = None
    try:
        try:
            app, progid = active(client)
        except AdapterError as exc:
            if exc.code != "E_NOT_FOUND":
                raise
            pending = processes("library")
            if pending:
                raise _error(
                    "E_HUMAN_REQUIRED",
                    "LibraryManager is already starting or unavailable for attachment",
                    {"processes": pending, "action": "Inspect the existing LibraryManager window"},
                ) from exc
            executable, directory = _native_executable("library")
            launcher = subprocess.Popen(
                [str(executable), str(Path(lmc).resolve())],
                cwd=directory,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            deadline = time.monotonic() + startup_seconds
            while time.monotonic() < deadline:
                try:
                    app, progid = active(client)
                    break
                except AdapterError:
                    if launcher.poll() is not None:
                        break
                    time.sleep(0.25)
            if app is None:
                raise _error(
                    "E_HUMAN_REQUIRED",
                    "The supported LibraryManager launcher did not become attachable in time",
                    {
                        "launcher_pid": launcher.pid,
                        "launcher": str(executable),
                        "launcher_exit": launcher.poll(),
                        "action": "Inspect startup/licensing UI; do not relaunch",
                    },
                ) from exc
            identity = application_identity(app, "library")
            matches = [p for p in processes("library") if p["pid"] == identity.get("pid")]
            if len(matches) != 1 or matches[0]["parent_pid"] != launcher.pid:
                raise _error(
                    "E_CONFLICT",
                    "LibraryManager ownership could not be established",
                    {"launcher_pid": launcher.pid, "application": identity, "processes": matches},
                ) from exc
            owned = True
            owned_pid = identity["pid"]
            if app.ActiveLibrary is None:
                app.OpenLibrary(str(Path(lmc).resolve()))
        # An already active manager may be observed, but never switched or closed.
        snapshot(app, lmc, progid)
        yield app, progid
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if owned and app is not None:
            _cleanup_owned(app, owned_pid, cleanup)
        else:
            cleanup.update(
                owned=False,
                status="not_attempted",
                steps=[],
                reason="Existing or unverified instance was not closed",
            )
        if primary_error is not None:
            if isinstance(primary_error, AdapterError):
                primary_error.details["cleanup"] = cleanup
            else:
                primary_error.cleanup_diagnostics = cleanup
                primary_error.add_note("LibraryManager cleanup diagnostics retained separately")
        elif any(row["status"] == "failed" for row in cleanup["steps"]):
            raise _error(
                "E_HUMAN_REQUIRED",
                "The operation completed but owned LibraryManager cleanup failed",
                {"cleanup": cleanup, "main_operation_completed": True},
            )


def require_partitions(client, lmc, units, required_partitions=None):
    """Stop before converters/symbol writes when an LMC partition is missing."""
    required = {
        (kind, str(unit["partition"]))
        for unit in units
        for kind in PARTITION_TYPES
        if unit.get(kind) or (kind == "symbols" and unit.get("symbol_files"))
    }
    if required_partitions is not None:
        if (
            not isinstance(required_partitions, dict)
            or required_partitions.keys() - PARTITION_TYPES.keys()
        ):
            raise _error("E_USAGE", "Required partitions must map parts/symbols/cells to names")
        from .native_com_adapter import PARTITION_NAME

        for kind, names in required_partitions.items():
            if not isinstance(names, list) or any(
                not isinstance(name, str) or not PARTITION_NAME.fullmatch(name) for name in names
            ):
                raise _error(
                    "E_USAGE", "Required partition names must be a list of plain identifiers"
                )
            required.update((kind, name) for name in names)
    cleanup = {}
    with connected(client, lmc, cleanup_report=cleanup) as (app, progid):
        observed = snapshot(app, lmc, progid)
        observed["manager_cleanup"] = cleanup
        missing = [
            {"kind": kind, "name": name}
            for kind, name in sorted(required)
            if name not in {row["name"] for row in observed["partitions"][kind]}
        ]
        if missing:
            raise _error(
                "E_HUMAN_REQUIRED",
                "The central library has not registered the requested partitions",
                {
                    "registration": observed,
                    "missing": missing,
                    "native_definition_write_started": False,
                    "action": "In this exact library, Edit > Partition Editor: add each missing "
                    "partition on its kind tab, Apply, close/reopen, then read back "
                    "before a new preview",
                    "warning": "PDB files and project LIST PDBs are not LMC registration; "
                    "reservation is a separate lifecycle",
                    "_untrusted": ["registration", "missing"],
                },
            )
        return observed
