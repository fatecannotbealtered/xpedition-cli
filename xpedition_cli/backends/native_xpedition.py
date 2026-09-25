from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..contract_gen import CODES
from ..errors import CLIError
from ..models import normalise_project, snapshot
from ..session import read_state, record_native_timeout

# `--quiet` is a global flag, and the adapter runs as a subprocess several call
# layers below where options are parsed, so the setting lives here rather than
# being threaded through every backend construction.
_PROGRESS_SUPPRESSED = False


def suppress_progress(suppressed: bool) -> None:
    """Capture the adapter's stderr instead of letting it stream to ours."""
    global _PROGRESS_SUPPRESSED
    _PROGRESS_SUPPRESSED = bool(suppressed)


def progress_suppressed() -> bool:
    return _PROGRESS_SUPPRESSED


# These have to keep working while the session is stale: they are how a caller
# inspects it and how it gets restarted.
_SESSION_RECOVERY_METHODS = frozenset({"health", "start", "close"})


def _refuse_a_stale_session(method: str) -> None:
    """Stop a task command running against a session a timed-out call left mid-operation.

    After a timeout Designer misreports its own state -- `IsProjectOpened()` went
    false with the project still open -- and the next command failed while asking
    Designer to open a project it already had, refused with a message about
    scripts and GUIs that pointed nowhere near the timeout that caused it.
    """
    if method in _SESSION_RECOVERY_METHODS:
        return
    state = read_state()
    if state.get("state") != "stale":
        return
    raise CLIError(
        "E_CONFLICT",
        "the native session is stale after a timed-out call; restart it before continuing",
        {
            "timed_out_method": state.get("timed_out_method"),
            "timed_out_at": state.get("timed_out_at"),
            "hint": "session stop, then session start",
        },
    )


class NativeBackend:
    """Boundary for a licensed Xpedition automation adapter.

    The prototype refuses to pretend that a native adapter exists. A future
    implementation can call the configured, audited automation entry point.
    """

    name = "native_xpedition"
    # The snapshot behind every native read. 30 s was too short for a 41-part,
    # 5-sheet design with an 8 MB central library, and a timeout costs a session
    # restart, so the default errs long; `--timeout` sets it per command.
    read_timeout_seconds: float = 120.0

    def _command_path(self) -> Path | None:
        command = os.environ.get("XPEDITION_NATIVE_COMMAND")
        if command:
            return Path(command).expanduser().resolve()
        discovered = shutil.which("xpedition-native-adapter")
        return Path(discovered).resolve() if discovered else None

    @staticmethod
    def _com_registered() -> bool:
        if os.name != "nt":
            return False
        try:
            import winreg

            for progid in (
                "MGCPCB.Application",
                "MGCPCB.Application.60",
                "MGCPCB.ExpeditionPCBApplication",
                "MGCPCB.ExpeditionPCBApplication.60",
                "Viewdraw.Application",
                "Viewdraw.Application.60",
            ):
                try:
                    with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid):
                        return True
                except OSError:
                    continue
        except ImportError:
            return False
        return False

    def status(self) -> dict[str, Any]:
        command = self._command_path()
        explicit = os.environ.get("XPEDITION_NATIVE_COMMAND")
        registered = self._com_registered()
        try:
            from ..native_com_adapter import _sdd_home, _viewdraw_registered

            sdd_home = _sdd_home()
            designer_registered = _viewdraw_registered()
        except Exception:
            sdd_home = None
            designer_registered = False
        available = bool(command and command.is_file() and registered and sdd_home)
        reason = None
        if command is None:
            reason = "native COM adapter is not installed"
        elif not command.is_file():
            reason = "configured native COM adapter was not found"
        elif sdd_home is None:
            reason = "Xpedition SDD_HOME could not be discovered"
        elif not registered:
            reason = "Xpedition COM automation is not registered"
        elif not available:
            reason = "native COM adapter is unavailable"
        return {
            "backend": self.name,
            "available": available,
            "licensed": "unknown",
            "automation_command_configured": bool(command),
            "automation_command_explicit": bool(explicit),
            "automation_command": str(command) if command else None,
            "sdd_home": str(sdd_home) if sdd_home else None,
            "com_registered": registered,
            "designer_com_registered": designer_registered,
            "reason": reason,
        }

    def require_implemented(self) -> None:
        status = self.status()
        if status["available"]:
            return
        raise CLIError(
            "E_BACKEND_UNAVAILABLE",
            "NativeBackend is not ready in this environment",
            {
                "backend": self.name,
                "reason": status["reason"],
                "adapter": status["automation_command"],
                "hint": (
                    "register Xpedition COM automation as Administrator, then retry"
                    if status["automation_command"]
                    else "install the native adapter and register Xpedition COM automation"
                ),
                "_untrusted": ["adapter", "reason"],
            },
        )

    def load(
        self, project_path: str | None, domain: str | None = None
    ) -> tuple[dict[str, Any], Path | None]:
        self.require_implemented()
        params = {"project": str(Path(project_path).expanduser().resolve())} if project_path else {}
        if domain:
            params["domain"] = str(domain)
        value = self.invoke("snapshot", params, timeout_seconds=self.read_timeout_seconds)
        return (
            normalise_project(value, observed=True),
            Path(project_path).expanduser().resolve() if project_path else None,
        )

    def snapshot(self, project: dict[str, Any]) -> dict[str, Any]:
        return snapshot(project)

    def capabilities(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "available": bool(self.status()["available"]),
            "licensed": "unknown",
            "operations": [
                "snapshot",
                "start",
                "attach",
                "open",
                "save",
                "close",
                "place_component",
                "place_pcb_component",
                "move_component",
                "move_pcb_component",
                "create_net",
                "connect",
                "draw",
                "show",
                "verify",
                "export_pdf",
                "package",
                "clone_project",
                "library_import",
                "kicad_import",
                "pcb_create",
                "forward_annotate",
                "arrange_components",
                "placement_batch",
                "show_board",
                "board_outline",
                "mounting_holes",
                "manufacturing_output",
                "net_rules",
                "render_board",
                "hand_route",
                "unroute_nets",
                "move_component",
                "board_geometry",
                "tidy_labels",
                "plane_pour",
                "route_board",
                "batch_drc",
            ],
        }

    def invoke(
        self, method: str, params: dict[str, Any] | None = None, timeout_seconds: float = 30.0
    ) -> dict[str, Any]:
        """Invoke an explicitly configured adapter without a shell.

        The adapter receives one JSON request on stdin and must return exactly
        one JSON object on stdout. This protocol is deliberately separate from
        the Xpedition product API; the adapter owns all licensed automation.
        """
        command_path = self._command_path()
        if command_path is None:
            raise CLIError("E_BACKEND_UNAVAILABLE", "XPEDITION_NATIVE_COMMAND is not configured")
        if not command_path.is_file():
            raise CLIError(
                "E_CONFIG",
                "configured NativeBackend adapter was not found",
                {"path": str(command_path)},
            )
        request = {"method": str(method), "params": params or {}}
        _refuse_a_stale_session(str(method))
        try:
            completed = subprocess.run(
                [str(command_path)],
                input=json.dumps(request, ensure_ascii=False),
                text=True,
                # The adapter always speaks UTF-8; the console code page (GBK on a
                # Chinese Windows) is not what either side writes.
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                # stderr is the progress side channel (CLI-SPEC §4). Inheriting
                # it streams the adapter's progress as it happens, which is the
                # only way a draw that runs for minutes is observable; capturing
                # it would hold every line until the call returned. `--quiet`
                # suppresses non-error stderr, so it captures instead.
                stderr=subprocess.PIPE if progress_suppressed() else None,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            # The adapter was killed mid-call, so Designer is left mid-operation.
            # Record that, or the next command fails somewhere unrelated with no
            # way to connect it back to this timeout.
            record_native_timeout(str(method))
            hint = "session stop, then session start, before the next native command"
            if method == "snapshot":
                # A read that runs out of time is a large design, not a fault:
                # say how to give the next one room.
                hint += f"; then retry with a --timeout above {timeout_seconds:g} seconds"
            raise CLIError(
                "E_TIMEOUT",
                "NativeBackend adapter timed out; the session is now stale",
                {"method": str(method), "seconds": timeout_seconds, "hint": hint},
            ) from exc
        except OSError as exc:
            raise CLIError(
                "E_IO", f"cannot start NativeBackend adapter: {exc}", {"path": str(command_path)}
            ) from exc
        if completed.returncode != 0:
            raise CLIError(
                "E_SERVER",
                "NativeBackend adapter returned a non-zero status",
                {"method": str(method), "exit_code": completed.returncode},
            )
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise CLIError(
                "E_SERVER", "NativeBackend adapter did not return one JSON object"
            ) from exc
        if not isinstance(value, dict):
            raise CLIError("E_SERVER", "NativeBackend adapter response must be a JSON object")
        if value.get("ok") is False:
            error = value.get("error")
            if not isinstance(error, dict) or not error.get("code"):
                raise CLIError("E_SERVER", "NativeBackend adapter returned an invalid error")
            if str(error["code"]) not in CODES:
                raise CLIError(
                    "E_SERVER", "NativeBackend adapter returned an unsupported error code"
                )
            raise CLIError(
                str(error["code"]),
                str(error.get("message", "NativeBackend adapter failed")),
                error.get("details") if isinstance(error.get("details"), dict) else {},
            )
        if value.get("ok") is not True or not isinstance(value.get("data"), dict):
            raise CLIError("E_SERVER", "NativeBackend adapter response is missing ok/data")
        return value["data"]
