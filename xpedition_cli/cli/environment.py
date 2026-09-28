"""context, doctor, reference, changelog, version."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .. import __version__, knowledge_base
from ..audit import config_dir
from ..backends import NativeBackend
from ..backends.native_xpedition import native_fix
from ..changelog import markdown as changelog_markdown
from ..reference_data import reference as full_reference
from ..reference_data import release_readiness
from ..session import read_state
from ..session import status as recorded_session


def _native_summary() -> dict[str, Any]:
    status = NativeBackend().status()
    return {
        "ready": bool(status.get("available")),
        "reason": status.get("reason"),
        "fix": None if status.get("available") else native_fix(status),
        "sdd_home": status.get("sdd_home"),
        "adapter": status.get("automation_command"),
    }


def context(options: dict[str, Any]) -> dict[str, Any]:
    raw = options.get("project")
    path = Path(str(raw)).expanduser().resolve() if raw else None
    return {
        "version": __version__,
        "config": {"directory": str(config_dir())},
        "xpedition": _native_summary(),
        "project": {"path": str(path) if path else None, "exists": bool(path and path.exists())},
        # Xpedition's own licensing; this tool keeps no credentials
        "credentials": {"configured": False, "managed_by": "Xpedition licensing"},
        # the company rules that apply here; the agent reads them with its own tools
        "knowledge_base": knowledge_base.for_context(),
        "notices": [],
        "_untrusted": ["config.directory", "xpedition", "project", "knowledge_base"],
    }


def running_applications(ready: bool) -> dict[str, Any]:
    """Ask the adapter which applications are running; never the reason doctor fails."""
    blank = {"probed": False, "layout": False, "designer": False, "reason": None}
    if not ready:
        return blank
    try:
        health = NativeBackend().invoke("health", {}, timeout_seconds=60.0)
    except Exception as exc:  # a probe must never be the reason doctor fails
        return {**blank, "reason": str(exc)[:200]}
    return {
        "probed": True,
        "layout": bool(health.get("application_running")),
        "designer": bool(health.get("designer_application_running")),
        "reason": None,
    }


def _project_check(raw: str) -> dict[str, Any]:
    from .. import project_file

    path = Path(raw).expanduser().resolve()
    if path.suffix.lower() != ".prj":
        return {
            "check": "project",
            "status": "fail",
            "fix": "pass the project's .prj",
            "message": str(path),
        }
    if not path.is_file():
        return {
            "check": "project",
            "status": "fail",
            "fix": "create it with project create, or correct --project",
            "message": f"{path} does not exist",
        }
    if not str(path).isascii():
        return {
            "check": "project",
            "status": "fail",
            "fix": "move the project to a folder whose path is plain ASCII",
            "message": "Designer cannot load new symbol files from a non-ASCII path",
        }
    designs = project_file.designs(path.read_text(encoding="utf-8", errors="replace"))
    board = project_file.board_design(designs)
    return {
        "check": "project",
        "status": "pass",
        "fix": None,
        "message": f"{path.name}: {len(designs)} designs, "
        + ("a board design" if board else "no board design"),
    }


def doctor(options: dict[str, Any]) -> dict[str, Any]:
    status = NativeBackend().status()
    session = recorded_session()
    recorded = read_state()
    ready = bool(status.get("available"))
    live = running_applications(ready)
    running = [
        name for name, on in (("Layout", live["layout"]), ("Designer", live["designer"])) if on
    ]
    checks: list[dict[str, Any]] = [
        {
            "check": "xpedition",
            "status": "pass" if ready else "fail",
            "fix": None if ready else native_fix(status),
            "message": status.get("reason") or f"adapter ready; SDD_HOME {status.get('sdd_home')}",
        },
        {
            "check": "applications",
            "status": "pass" if len(running) == 2 else "warn",
            "fix": None
            if len(running) == 2
            else "; ".join(
                fix
                for app, fix in (
                    (
                        "Designer",
                        "session start --kind schematic for schematic, library and bom commands",
                    ),
                    ("Layout", "session start --kind pcb for pcb commands"),
                )
                if app not in running
            ),
            "message": (
                f"running: {', '.join(running)}"
                if running
                else (
                    "neither Designer nor Layout is running"
                    if live["probed"]
                    else f"not probed: {live['reason'] or 'the adapter is not ready'}"
                )
            ),
        },
    ]
    # what this tool recorded about the process it drives, when that needs acting on
    if recorded.get("state") == "stale":
        checks.append(
            {
                "check": "session",
                "status": "fail",
                "fix": "session stop, then session start",
                "message": f"a call to {recorded.get('timed_out_method')} timed out; the "
                "application may be mid-operation",
            }
        )
    elif session.get("state") == "crashed":
        kind = "schematic" if recorded.get("domain") == "schematic" else "pcb"
        checks.append(
            {
                "check": "session",
                "status": "warn",
                "fix": f"session start --kind {kind}",
                "message": session.get("reason") or "the recorded process is gone",
            }
        )
    if options.get("project"):
        checks.append(_project_check(str(options["project"])))
    readiness = release_readiness()
    level = str(readiness.get("level", "unknown"))
    checks.append(
        {
            "check": "release_readiness",
            "status": "pass" if level == "stable" else "warn",
            "fix": None
            if level == "stable"
            else "see reference's release_readiness for the evidence stable still needs",
            "message": f"{level}: {readiness.get('reason', '')}",
            "details": {
                "fcc_status": readiness.get("fcc_status"),
                "mock_upstream_status": readiness.get("mock_upstream_status"),
                "live_smoke_status": readiness.get("live_smoke_status"),
            },
        }
    )
    return {
        "checks": checks,
        "version": __version__,
        "_untrusted": ["checks[].message", "checks[].details"],
    }


def reference(options: dict[str, Any]) -> dict[str, Any]:
    return full_reference(
        command=options.get("command"), domain=options.get("domain"), schema=options.get("schema")
    )


def version(options: dict[str, Any]) -> dict[str, Any]:
    return {"version": __version__}


_CHANGE_CATEGORIES = ("added", "changed", "fixed", "deprecated", "removed", "security")


def changelog(options: dict[str, Any]) -> dict[str, Any]:
    since = options.get("since")
    text = changelog_markdown()
    entries: list[dict[str, Any]] = []
    matches = list(
        re.finditer(r"^## \[([^\]]+)\](?: - (\d{4}-\d{2}-\d{2}))?\s*$", text, re.MULTILINE)
    )
    for index, match in enumerate(matches):
        version_name = match.group(1)
        if version_name.lower() == "unreleased":
            continue
        if since and semver_key(version_name) <= semver_key(str(since)):
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        entries.append(
            {
                "version": version_name,
                "date": match.group(2),
                "changes": changelog_changes(text[match.end() : end]),
            }
        )
    result: dict[str, Any] = {"current_version": __version__, "entries": entries}
    if since:
        result["since"] = since
    return result


def changelog_changes(body: str) -> dict[str, list[str]]:
    """Every entry of one release, by category.

    A long release repeats a heading -- three `### Changed` sections, say -- and
    each counts. An entry runs on over its indented lines; a nested bullet joins
    its parent after a semicolon.
    """
    changes: dict[str, list[str]] = {category: [] for category in _CHANGE_CATEGORIES}
    current: list[str] | None = None
    for line in body.splitlines():
        heading = re.match(r"^### (\w+)\s*$", line)
        if heading:
            current = changes.get(heading.group(1).lower())
            continue
        if current is None or not line.strip():
            continue
        if line.startswith("- "):
            current.append(line[2:].strip())
        elif line[0] in " \t" and current:
            stripped = line.strip()
            nested = stripped.startswith("- ")
            joiner = "; " if nested else " "
            current[-1] = f"{current[-1]}{joiner}{stripped[2:] if nested else stripped}"
    return changes


def semver_key(value: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", str(value))
    return tuple(int(part) for part in match.groups()) if match else (0, 0, 0)
