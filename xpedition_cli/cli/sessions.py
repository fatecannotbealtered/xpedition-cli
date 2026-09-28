"""session start | status | stop: the Designer and Layout processes this tool drives.

Xpedition has no notion of a session; this is the tool's own layer over starting,
attaching to and quitting the two applications, with what it did recorded in the
config directory so a later command can explain a crash or a timed-out call.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..backends import NativeBackend
from ..errors import CLIError
from ..session import (
    clear_native,
    read_state,
    record_native_attach,
    record_native_failure,
    record_native_start,
)
from ..session import status as recorded_status
from .common import check_gate, confirmed, native, previewed, text
from .environment import running_applications

APPLICATIONS = {"schematic": "Xpedition Designer", "pcb": "Xpedition Layout"}
_HEALTH_KEYS = {"schematic": "designer_application_running", "pcb": "application_running"}
# how long a freshly started application may take to accept automation
STARTUP_SECONDS = 120.0


def _kind(options: dict[str, Any]) -> str | None:
    value = text(options, "kind").lower()
    if not value:
        return None
    if value not in APPLICATIONS:
        raise CLIError(
            "E_VALIDATION",
            "--kind is schematic or pcb",
            {"kind": value, "choices": ["schematic", "pcb"]},
        )
    return value


def _project_for(options: dict[str, Any], domain: str) -> str | None:
    raw = text(options, "project")
    if not raw:
        return None
    path = Path(raw).expanduser().resolve()
    allowed = {".prj"} if domain == "schematic" else {".prj", ".pcb"}
    if path.suffix.lower() not in allowed:
        raise CLIError(
            "E_VALIDATION",
            f"--project for {APPLICATIONS[domain]} is a {' or '.join(sorted(allowed))} file",
            {"path": str(path)},
        )
    if not path.is_file():
        raise CLIError("E_NOT_FOUND", "project file was not found", {"path": str(path)})
    return str(path)


def _running(backend: NativeBackend, domain: str) -> bool:
    health = backend.invoke("health", {}, timeout_seconds=60.0)
    return bool(health.get(_HEALTH_KEYS[domain]))


def start(options: dict[str, Any]) -> dict[str, Any]:
    domain = _kind(options)
    if domain is None:
        raise CLIError("E_USAGE", "session start requires --kind schematic or --kind pcb")
    project = _project_for(options, domain)
    backend = native()
    stale = read_state().get("state") == "stale"
    running = _running(backend, domain)
    if running and stale:
        raise CLIError(
            "E_CONFLICT",
            "the session is stale after a timed-out call and the application is still running",
            {"hint": f"session stop --kind {domain}, then session start"},
        )
    result: dict[str, Any] = {"domain": domain, "application": APPLICATIONS[domain]}
    if running:
        backend.invoke("attach", {"domain": domain})
        record_native_attach({"domain": domain})
        result.update({"started": False, "attached": True, "pid": None})
    else:
        clear_native()
        try:
            launched = backend.invoke(
                "start", {"domain": domain, "visible": True}, timeout_seconds=180.0
            )
        except CLIError as error:
            record_native_failure({"code": error.code, "details": error.details or {}})
            raise
        record_native_start(launched)
        ready = bool(launched.get("automation_ready"))
        deadline = time.monotonic() + STARTUP_SECONDS
        while not ready and time.monotonic() < deadline:
            time.sleep(3.0)
            ready = _running(backend, domain)
        if not ready:
            raise CLIError(
                "E_TIMEOUT",
                f"{APPLICATIONS[domain]} started but did not accept automation in time",
                {"pid": launched.get("pid"), "hint": "a start-up dialog may be waiting"},
            )
        result.update({"started": True, "attached": True, "pid": launched.get("pid")})
    result["project"] = None
    result["prompts"] = []
    if project:
        opened = backend.invoke(
            "open", {"project": project, "domain": domain, "start": False}, timeout_seconds=420.0
        )
        result["project"] = opened.get("project")
        result["prompts"] = opened.get("prompts") or []
    result["_untrusted"] = ["project", "prompts"]
    return result


def status(options: dict[str, Any]) -> dict[str, Any]:
    backend = NativeBackend()
    adapter = backend.status()
    recorded = recorded_status()
    state = read_state()
    result: dict[str, Any] = {
        "xpedition": {"ready": bool(adapter.get("available")), "reason": adapter.get("reason")},
        "designer": {"running": None},
        "layout": {"running": None},
        "recorded": {
            "state": recorded.get("state"),
            "domain": state.get("domain"),
            "pid": recorded.get("pid"),
            "timed_out_method": state.get("timed_out_method"),
        },
        "stale": state.get("state") == "stale",
        "_untrusted": ["xpedition.reason", "recorded"],
    }
    if adapter.get("available"):
        try:
            health = backend.invoke("health", {}, timeout_seconds=60.0)
        except CLIError as error:
            result["probe_error"] = {"code": error.code, "message": error.message}
        else:
            result["designer"]["running"] = bool(health.get("designer_application_running"))
            result["layout"]["running"] = bool(health.get("application_running"))
    return result


def _stop_domain(options: dict[str, Any]) -> str:
    """The application `session stop` quits, resolved against what is running.

    Quitting discards unsaved work, so it never guesses: with both running and no
    --kind it refuses rather than quit whichever a default happened to name.
    """
    requested = _kind(options)
    live = running_applications(True)
    if not live["probed"]:
        if requested is None:
            raise CLIError(
                "E_USAGE",
                "could not probe which application is running; name one with --kind",
                {"reason": live["reason"]},
            )
        return requested
    running = {"pcb": live["layout"], "schematic": live["designer"]}
    if requested is not None:
        if not running[requested]:
            raise CLIError(
                "E_NOT_FOUND",
                f"{APPLICATIONS[requested]} is not running; nothing to stop",
                {"running": [name for name, on in running.items() if on]},
            )
        return requested
    names = [name for name, on in running.items() if on]
    if len(names) == 1:
        return names[0]
    if not names:
        raise CLIError("E_NOT_FOUND", "neither Designer nor Layout is running; nothing to stop")
    raise CLIError(
        "E_USAGE",
        "Designer and Layout are both running; name one with --kind schematic or --kind pcb",
        {"running": names},
    )


def stop(options: dict[str, Any]) -> dict[str, Any]:
    check_gate(options, "session stop")
    _kind(options)  # a malformed --kind is a usage error before anything is probed
    backend = native()
    domain = _stop_domain(options)
    scope = {"operation": "session_stop", "domain": domain}
    if options.get("dry_run"):
        preview = {
            "domain": domain,
            "application": APPLICATIONS[domain],
            "changes": [{"action": "quit_application", "domain": domain}],
            "risk": {
                "tier": "T1",
                "blast_radius": f"{APPLICATIONS[domain]} quits; unsaved design work in it is lost",
            },
        }
        return previewed(preview, scope)
    confirmed(options, scope)
    result = backend.invoke("close", {"domain": domain})
    clear_native()
    result["application"] = APPLICATIONS[domain]
    return result
