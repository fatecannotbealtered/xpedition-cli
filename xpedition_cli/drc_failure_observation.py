"""Bound, file-only DRC failure evidence plus existing-process identity getters.

No Open, Close, Save, engine dispatch, retry, or journal resolution is available
here. A closed board cannot provide geometry or reopened-hazard certification.
"""

from __future__ import annotations

import copy
import os
import re
from pathlib import Path

from . import drc_engine as drc
from .operation_budget import checkpoint

MAX_FILES = 256
MAX_BYTES = 64 * 1024 * 1024


def _path(value):
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ValueError("DRC observation requires an absolute bound path")
    return str(drc._plain_path(value))


def _candidate(value):
    if (
        not isinstance(value, dict)
        or not value.get("version")
        or not re.fullmatch(r"[0-9a-f]{64}", str(value.get("build_sha256", "")))
    ):
        raise ValueError("DRC observation requires the original candidate identity")
    return copy.deepcopy(value)


def _generation(application, process):
    if (
        type(application.get("pid")) is not int
        or application["pid"] <= 0
        or process.get("pid") != application["pid"]
        or not str(process.get("creation_time_100ns", "")).isdigit()
        or not application.get("product_version")
        or not application.get("type_library")
    ):
        raise ValueError("DRC observation requires a complete original process generation")
    public = application.get("public_fields") or {}
    if _path(public.get("FullName")) != _path(process.get("executable")):
        raise ValueError("DRC application and OS executable identities differ")
    return {
        "process": copy.deepcopy(process),
        "pid": application["pid"],
        "product_version": application["product_version"],
        "type_library": copy.deepcopy(application["type_library"]),
        "public_fields": {k: public.get(k) for k in ("FullName", "Version", "InstanceGuid")},
    }


def binding(project, board, application, worker, controller, process, side_effects):
    board, project = _path(str(board)), _path(str(project))
    document = application.get("document") or {}
    if (
        document.get("status") != "observed"
        or document.get("open") is not True
        or _path(document.get("path")) != board
    ):
        raise ValueError("DRC must bind the actual open board before dispatch")
    expected = drc.planned_side_effects(Path(board))
    if side_effects != expected:
        raise ValueError("DRC observation must bind exactly the ten declared side effects")
    return {
        "contract": "drc-closed-failure-observation-v1",
        "project": project,
        "board": board,
        "generation": _generation(application, process),
        "worker": _candidate(worker),
        "controller": _candidate(controller),
        "side_effects": copy.deepcopy(expected),
        "reopen_authorized": False,
    }


def require_identity(bound, identity, controller, process, *, closed, candidate_match=None):
    candidate_match = candidate_match or (lambda first, second: first == second)
    if (
        bound.get("contract") != "drc-closed-failure-observation-v1"
        or bound.get("reopen_authorized") is not False
        or bound["side_effects"] != drc.planned_side_effects(Path(_path(bound["board"])))
        or not candidate_match(_candidate(controller), bound["controller"])
        or not candidate_match(_candidate(identity.get("adapter")), bound["worker"])
        or _generation(identity.get("application") or {}, process) != bound["generation"]
    ):
        raise ValueError("DRC original board, candidate or process binding changed")
    document = identity["application"].get("document") or {}
    if document.get("status") != "observed" or type(document.get("open")) is not bool:
        raise ValueError("DRC active-document state is unknown")
    if closed:
        if document["open"] is not False or document.get("path") is not None:
            raise ValueError("DRC failure observer requires an independently observed closed board")
    elif document["open"] is not True or _path(document.get("path")) != bound["board"]:
        raise ValueError("DRC original open board changed before dispatch")


def _error(error):
    return {"type": type(error).__name__, "message": str(error)}


def _capture(bound):
    budget = {"files_attempted": 0, "bytes_reserved": 0, "byte_budget_exhausted": False}

    def attempt():
        if budget["files_attempted"] >= MAX_FILES:
            raise ValueError("DRC failure observation file-attempt limit exceeded")
        budget["files_attempted"] += 1

    def file(path):
        checkpoint()
        attempt()
        if budget["byte_budget_exhausted"]:
            raise ValueError("DRC failure observation byte budget exhausted")
        path = drc._plain_path(path)

        def reserve(size):
            if size > MAX_BYTES - budget["bytes_reserved"]:
                budget["byte_budget_exhausted"] = True
                raise ValueError("DRC failure observation byte budget exceeded")
            budget["bytes_reserved"] += size

        try:
            return drc._read_contents(path, reserve=reserve).record
        except FileNotFoundError:
            return {"path": str(path), "exists": False}

    rows = {}
    for role, path in [("project", bound["project"]), ("board", bound["board"])] + [
        (r["role"], r["path"]) for r in bound["side_effects"]
    ]:
        partial_files = None
        try:
            path = drc._plain_path(path)
            if role == "staged_inputs" and path.exists():
                files = []
                partial_files = files
                pending = [path]
                while pending:
                    checkpoint()
                    attempt()
                    directory = drc._plain_path(pending.pop())
                    # Bound traversal and failures before attempting further entries.
                    with os.scandir(directory) as entries:
                        for entry in entries:
                            attempt()
                            child = drc._plain_path(entry.path)
                            if child.is_dir():
                                pending.append(child)
                            else:
                                files.append(file(child))
                rows[role] = {
                    "path": str(path),
                    "exists": True,
                    "files": sorted(files, key=lambda r: r["path"]),
                }
            else:
                rows[role] = file(path)
        except (OSError, ValueError) as error:
            rows[role] = {"path": str(path), "read_error": _error(error)}
            if partial_files is not None:
                rows[role].update(exists=True, files=sorted(partial_files, key=lambda r: r["path"]))
    return {"files": rows, "budget": budget}


def native_terminal_observed(result):
    """Returned stage evidence only; an enclosing timeout always takes precedence."""
    if not isinstance(result, dict) or not isinstance(result.get("native_stages"), dict):
        return False
    stages = result["native_stages"]
    return (
        stages.get("go") == "not_started" and result.get("native_go_dispatched") is not True
    ) or (
        result.get("native_go_dispatched") is True
        and result.get("native_go_returned") is True
        and stages.get("go") == "returned"
    )


def observe(bound, result, *, identity_reader, process_reader, controller_reader):
    """Read fresh evidence after a returned failed handler, never recover its board."""
    if not isinstance(bound, dict) or bound.get("contract") != "drc-closed-failure-observation-v1":
        raise ValueError("A fresh DRC plan with a closed-state binding is required")
    if (
        _path(result.get("pcb")) != bound["board"]
        or _path((result.get("source", {}).get("input") or {}).get("requested_project"))
        != bound["project"]
        or bound["side_effects"] != drc.planned_side_effects(Path(bound["board"]))
        or _candidate(controller_reader()) != bound["controller"]
    ):
        raise ValueError("DRC handler result differs from the original confirmed target")
    terminal = native_terminal_observed(result)
    issues, identities = [], []
    if (result.get("source", {}).get("input") or {}).get("snapshot_sha256") != bound.get(
        "geometry_before_sha256"
    ) or not bound.get("geometry_before_sha256"):
        issues.append({"reason": "handler_source_geometry_differs_from_confirmed_snapshot"})

    def identity():
        if not terminal:
            issues.append({"reason": "original_native_call_terminal_unconfirmed"})
            return
        try:
            value = identity_reader()
            process = process_reader(bound["generation"]["pid"])
            require_identity(bound, value, controller_reader(), process, closed=True)
            identities.append({"application": value["application"], "process": process})
        except Exception as error:
            issues.append({"reason": "closed_identity_unverified", "error": _error(error)})

    identity()
    first, second = _capture(bound), _capture(bound)
    identity()
    files = second["files"]
    for role, row in files.items():
        if row.get("read_error") or first["files"][role].get("read_error"):
            issues.append({"reason": "file_read_failed", "role": role})
        elif row != first["files"][role]:
            issues.append({"reason": "file_set_changed", "role": role})
        elif role in {"board", "project"} and row.get("exists") is not True:
            issues.append({"reason": "bound_source_missing", "role": role})
        elif (
            role in result.get("unqualified_report_observation", {}).get("files", {})
            and row.get("exists") is not True
        ):
            issues.append({"reason": "consumed_output_missing", "role": role})
        elif role in result.get("unqualified_report_observation", {}).get("files", {}):
            if row != result["unqualified_report_observation"]["files"][role]:
                issues.append({"reason": "consumed_output_changed", "role": role})
    closed_reported = result.get("board_lifecycle", {}).get("closed_observed") is True
    if not closed_reported:
        issues.append({"reason": "handler_closure_unconfirmed"})
    return {
        "contract": bound["contract"],
        "binding": copy.deepcopy(bound),
        "complete": not issues and len(identities) == 2,
        "scope": "original closed board/candidate identity and ten declared file effects only",
        "original_call_terminal_observed": terminal,
        "identities": identities,
        "first": first,
        "second": second,
        "issues": issues,
        "execution_qualified": False,
        "persistence_verified": False,
        "reopen": {
            "attempted": False,
            "authorized": False,
            "requires": [
                "separate explicit authorization and a fresh preview/confirmation",
                "original call terminal, no in-flight engine or unresolved ownership ambiguity",
                "same candidate, board path, source files and owned process generation",
                "fresh independent geometry/GetHazards reads; never repeat Go",
            ],
        },
        "not_checked": [
            "save/reopen persistence",
            "geometry protection",
            "hazard matching",
            "unit contract",
        ],
    }
