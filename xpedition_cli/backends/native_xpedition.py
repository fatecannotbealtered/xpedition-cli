from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from ..error_contract import CODES
from ..errors import TRACE_RETURNED_WARNING, CLIError
from ..models import normalise_project
from ..operation_budget import current_budget, native_call_budget
from ..session import read_state, record_native_timeout

# `--quiet` is a global flag, and the adapter runs as a subprocess several call
# layers below where options are parsed, so the setting lives here rather than
# being threaded through every backend construction.
_PROGRESS_SUPPRESSED = False
_IDENTITY_OWNERSHIP = ContextVar("xpedition_identity_ownership", default=None)
_DESIGNER_IDENTITY_PROBE = ContextVar("xpedition_designer_identity_probe", default=None)
_HOST_BOOTSTRAP_OWNERSHIP = ContextVar("xpedition_host_bootstrap_ownership", default=None)
_UNCERTAIN_EXIT_WORKERS = []


def _run_nonterminating_exit_worker(argv, *, input, timeout, stderr, **_kwargs):
    """Never terminate a possibly in-flight diagnostic-exit worker on timeout."""
    end = time.monotonic() + timeout
    request = json.loads(input)
    method = request["method"]
    request.setdefault("params", {})["_diagnostic_exit_deadline"] = end - min(0.25, timeout * 0.05)
    with tempfile.TemporaryFile() as source:
        source.write(json.dumps(request, ensure_ascii=False).encode("utf-8"))
        source.seek(0)
        worker = subprocess.Popen(
            argv,
            stdin=source,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            stdout, error_output = worker.communicate(timeout=max(0.0, end - time.monotonic()))
        except subprocess.TimeoutExpired as error:
            _UNCERTAIN_EXIT_WORKERS.append(worker)
            try:
                record_native_timeout(method)
            except OSError:
                pass
            raise CLIError(
                "E_TIMEOUT",
                "The diagnostic exit worker did not return; do not retry or query CAD",
                {
                    "write_attempted": method == "diagnostic_exit_quit",
                    "native_request_dispatched": True,
                    "worker_pid": worker.pid,
                    "worker_terminated": False,
                    "replay_allowed": False,
                },
            ) from error
    return subprocess.CompletedProcess(argv, worker.returncode, stdout, error_output)


def require_designer_host_execution():
    """Fail before state/OS/COM while only the non-CAD executor is implemented."""
    try:
        from ..execution_lifecycle import native_execution_enabled
    except ImportError:
        enabled = False
    else:
        enabled = native_execution_enabled()
    if enabled is True:
        return
    raise CLIError(
        "E_HUMAN_REQUIRED",
        "The retained runtime host-bootstrap experiment is disabled; "
        "Designer uses a separate, unqualified dedicated-startup design",
        {
            "action": "other",
            "resume": 'xpedition-cli reference --command "session attach"',
            "next_action": "Use reference for the dedicated-startup readiness gaps. "
            "Runtime Run is historical, not a prerequisite or fallback for that design. "
            "The execution-owner prototype remains non-CAD only.",
            "write_attempted": False,
            "state_write_attempted": False,
            "native_request_dispatched": False,
            "native_execution_enabled": False,
        },
    )


@contextmanager
def host_bootstrap_ownership(packet):
    """Supply an original-start target only during the confirmed fixed experiment."""
    token = _HOST_BOOTSTRAP_OWNERSHIP.set(packet)
    try:
        yield
    finally:
        _HOST_BOOTSTRAP_OWNERSHIP.reset(token)


def selected_host_bootstrap_ownership():
    return _HOST_BOOTSTRAP_OWNERSHIP.get()


@contextmanager
def identity_ownership(provider, *, designer_probe=None):
    """Limit original-start metadata injection to the active public read call."""
    token = _IDENTITY_OWNERSHIP.set(provider)
    probe_token = _DESIGNER_IDENTITY_PROBE.set(designer_probe)
    try:
        yield
    finally:
        _DESIGNER_IDENTITY_PROBE.reset(probe_token)
        _IDENTITY_OWNERSHIP.reset(token)


def selected_designer_identity_probe():
    return _DESIGNER_IDENTITY_PROBE.get()


def _identity_params(method, params):
    result = dict(params or {})
    result.pop("_nativeom_ownership", None)  # Caller data cannot claim process ownership.
    result.pop("_designer_identity_probe", None)
    result.pop("_host_bootstrap_ownership", None)
    if method == "designer_host_bootstrap" and result.get("domain") == "schematic":
        packet = _HOST_BOOTSTRAP_OWNERSHIP.get()
        if isinstance(packet, dict):
            result["_host_bootstrap_ownership"] = packet
    provider = _IDENTITY_OWNERSHIP.get()
    if method == "identity" and result.get("domain") == "schematic" and callable(provider):
        result["_nativeom_ownership"] = provider()
        if _DESIGNER_IDENTITY_PROBE.get() == "application-proxy":
            result["_designer_identity_probe"] = "application-proxy"
    return result


def suppress_progress(suppressed: bool) -> None:
    """Capture the adapter's stderr instead of letting it stream to ours."""
    global _PROGRESS_SUPPRESSED
    _PROGRESS_SUPPRESSED = bool(suppressed)


def progress_suppressed() -> bool:
    return _PROGRESS_SUPPRESSED


# These have to keep working while the session is stale: they are how a caller
# inspects it and how it gets restarted.
_SESSION_RECOVERY_METHODS = frozenset({"health", "identity", "start", "close"})


def _attached_geometry_read(method: str, params: dict[str, Any] | None) -> bool:
    params = params or {}
    return (
        method == "board_info"
        and params.get("geometry") is True
        and params.get("start") is False
        and type(params.get("pid")) is int
        and params["pid"] > 0
    )


def _bound_observation_read(method: str, params: dict[str, Any] | None) -> bool:
    """Only the explicit existing-document, same-process form is read-only.

    A method name alone cannot establish this: geometry also has an artifact /
    start route, and snapshots historically could attach/open a document.
    """
    params = params or {}
    expected = os.environ.get("XPEDITION_EXPECTED_PID", "")
    return (
        method in {"snapshot", "board_geometry", "outline_state"}
        and os.environ.get("XPEDITION_OBSERVE_ONLY") == "1"
        and params.get("observe_only") is True
        and params.get("start") is False
        and type(params.get("pid")) is int
        and params["pid"] > 0
        and expected == str(params["pid"])
        and os.environ.get("XPEDITION_REQUIRE_EXISTING") == "1"
        and bool(params.get("project"))
        and not params.get("output")
    )


def _refuse_a_stale_session(method: str, params: dict[str, Any] | None = None) -> None:
    """Stop a task command running against a session a timed-out call left mid-operation.

    After a timeout Designer misreports its own state -- `IsProjectOpened()` went
    false with the project still open -- and the next command failed while asking
    Designer to open a project it already had, refused with a message about
    scripts and GUIs that pointed nowhere near the timeout that caused it.
    """
    if method in _SESSION_RECOVERY_METHODS:
        return
    if os.environ.get("XPEDITION_OBSERVE_ONLY") == "1" and (
        method
        in {"identity", "cell_refresh_state", "silk_environment_state", "release_project_preflight"}
        or _attached_geometry_read(method, params)
        or _bound_observation_read(method, params)
    ):
        # Reconciliation reads attach to the existing instance; they cannot open or launch it.
        return
    state = read_state()
    if state.get("timed_out_method") in {"health", "identity"} and method in {"identity", "attach"}:
        # A confirmed attachment can recover an earlier pure observation timeout.
        # A timed-out write/snapshot keeps its independent reconciliation guard.
        return
    if state.get("state") != "stale":
        return
    raise CLIError(
        "E_CONFLICT",
        "the native session is stale after a timed-out call; reconcile it before another write",
        {
            "timed_out_method": state.get("timed_out_method"),
            "timed_out_at": state.get("timed_out_at"),
            "hint": (
                "session inspect and read-only snapshot; reconcile the unknown operation. "
                "Use explicit session close/start only if inspection requires a restart."
            ),
        },
    )


def native_fix(status: dict[str, Any]) -> str:
    """What to do about an unavailable NativeBackend, by the reason `status` gives."""
    reason = status.get("reason")
    if not status.get("automation_command_configured"):
        return (
            'install the native COM adapter: python -m pip install "xpedition-cli[native] @ '
            'git+https://github.com/fatecannotbealtered/xpedition-cli"'
        )
    if reason == "configured native COM adapter was not found":
        return (
            "point XPEDITION_NATIVE_COMMAND at the adapter executable, or unset it to use "
            "the installed xpedition-native-adapter"
        )
    if reason == "Xpedition SDD_HOME could not be discovered":
        return "install Xpedition, or set SDD_HOME to its SDD_HOME folder"
    return (
        "run scripts/register-xpedition-user.ps1 from a clone of the repository (or the "
        "official Administrator registration)"
    )


class NativeBackend:
    """The boundary to the Xpedition automation adapter.

    Every command reaches Designer or Layout through one subprocess call to the
    adapter: a JSON request on stdin, one JSON object on stdout.
    """

    name = "native_xpedition"
    # The snapshot behind every native read. 30 s was too short for a 41-part,
    # 5-sheet design with an 8 MB central library, and a timeout costs a session
    # restart, so the default errs long; `--timeout` sets it per command.
    read_timeout_seconds: float = 300.0

    def _command_path(self) -> Path | None:
        command = os.environ.get("XPEDITION_NATIVE_COMMAND")
        if command:
            return Path(command).expanduser().resolve()
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve()
        discovered = shutil.which("xpedition-native-adapter")
        return Path(discovered).resolve() if discovered else None

    @staticmethod
    def _com_registered() -> bool:
        if os.name != "nt":
            return False
        try:
            import winreg

            from ..native_identity import application_progids

            for progid in application_progids("pcb") + application_progids("schematic"):
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
                "hint": native_fix(status),
                "_untrusted": ["adapter", "reason"],
            },
        )

    def load(
        self, project_path: str | None, domain: str | None = None
    ) -> tuple[dict[str, Any], Path | None]:
        """Observe the existing native document without launching or opening it."""
        self.require_implemented()
        params: dict[str, Any] = {"observe_only": True}
        if project_path:
            params["project"] = str(Path(project_path).expanduser().resolve())
        if domain:
            params["domain"] = str(domain)
        value = self.invoke("snapshot", params, timeout_seconds=self.read_timeout_seconds)
        return (
            normalise_project(value, observed=True),
            Path(project_path).expanduser().resolve() if project_path else None,
        )

    def invoke(
        self, method: str, params: dict[str, Any] | None = None, timeout_seconds: float = 30.0
    ) -> dict[str, Any]:
        """Invoke an explicitly configured adapter without a shell.

        The adapter receives one JSON request on stdin and must return exactly
        one JSON object on stdout. This protocol is deliberately separate from
        the Xpedition product API; the adapter owns all licensed automation.
        """
        from ..designer_owner_client import owner_route_available, route_invocation

        if owner_route_available(method, params):
            with native_call_budget(str(method), timeout_seconds) as allocated:
                handled, owned_result = route_invocation(method, params, allocated)
            if handled:
                return owned_result
        if method == "open":
            selection = params or {}
            domain = str(selection.get("domain") or selection.get("kind") or "").lower()
            path = str(selection.get("project") or selection.get("path") or "").lower()
            if domain in {"schematic", "designer"} or (
                domain not in {"pcb", "layout"} and path.endswith((".prj", ".dproj"))
            ):
                from ..designer_startup import require_dedicated_designer

                require_dedicated_designer()
        if method in {"start", "attach"}:
            selection = params or {}
            domain = str(selection.get("domain") or selection.get("kind") or "").lower()
            if domain not in {"schematic", "designer", "pcb", "layout"}:
                raise CLIError(
                    "E_USAGE",
                    "Native session start/attach requires an explicit domain or kind",
                    {"write_attempted": False, "native_request_dispatched": False},
                )
            if domain in {"schematic", "designer"}:
                from ..designer_startup import require_dedicated_designer

                require_dedicated_designer()
        if method == "designer_host_bootstrap":
            require_designer_host_execution()
        from ..native_com_adapter import _native_step

        command_path = self._command_path()
        if command_path is None:
            raise CLIError("E_BACKEND_UNAVAILABLE", "XPEDITION_NATIVE_COMMAND is not configured")
        if not command_path.is_file():
            raise CLIError(
                "E_CONFIG",
                "configured NativeBackend adapter was not found",
                {"path": str(command_path)},
            )
        request = {"method": str(method), "params": _identity_params(str(method), params)}
        if (
            method == "designer_host_bootstrap"
            and "_host_bootstrap_ownership" not in request["params"]
        ):
            raise CLIError(
                "E_CONFIG",
                "The fixed host experiment requires its confirmed SDK context",
                {"write_attempted": False},
            )
        _refuse_a_stale_session(str(method), params)
        try:
            argv = [str(command_path)]
            if getattr(sys, "frozen", False) and not os.environ.get("XPEDITION_NATIVE_COMMAND"):
                argv.append("--internal-native-adapter")
            with (
                native_call_budget(str(method), timeout_seconds) as allocated,
                _native_step(f"worker.{method}") as trace,
            ):
                timeout_seconds = allocated
                budget = current_budget()
                if method == "export_pdf":
                    reserve = (
                        budget.profile["native_return_reserve_seconds"]["export_pdf"]
                        if budget is not None
                        else 15.0
                    )
                    if allocated <= reserve:
                        raise CLIError(
                            "E_TIMEOUT",
                            "No PDF child-process budget remains before dispatch.",
                            {
                                "write_attempted": False,
                                "native_query_only": True,
                                "failed_stage": "pdf_before_dispatch",
                                "native_request_dispatched": False,
                                "parent_limit_seconds": allocated,
                                "return_reserve_seconds": reserve,
                            },
                        )
                    request["params"] = {
                        **request["params"],
                        "_native_process_timeout_seconds": allocated - reserve,
                        "_native_budget_profile_id": budget.profile["id"]
                        if budget
                        else "standalone-native-v1",
                    }
                trace["timeout_seconds"] = timeout_seconds
                runner = (
                    _run_nonterminating_exit_worker
                    if method in {"diagnostic_exit_state", "diagnostic_exit_quit"}
                    else subprocess.run
                )
                completed = runner(
                    argv,
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
                    stderr=subprocess.PIPE
                    if progress_suppressed() or (budget is not None and budget.quiet)
                    else None,
                    timeout=timeout_seconds,
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            if method == "designer_host_bootstrap":
                # The one-shot experiment can already have run its fixed script.
                # Its SDK operation owns that uncertainty; never overwrite the
                # original native session merely because our helper timed out.
                raise CLIError(
                    "E_TIMEOUT",
                    "The fixed host-bootstrap result is unknown; do not replay",
                    {
                        "method": method,
                        "seconds": timeout_seconds,
                        "write_attempted": True,
                        "native_query_only": False,
                        "original_session_metadata_preserved": True,
                    },
                ) from exc
            # The adapter was killed mid-call, so Designer is left mid-operation.
            # Record that, or the next command fails somewhere unrelated with no
            # way to connect it back to this timeout.
            read_only = (
                method
                in {
                    "health",
                    "identity",
                    "cell_refresh_state",
                    "silk_environment_state",
                    "board_view_state",
                    "release_project_preflight",
                }
                or (method == "refresh_cells" and not (params or {}).get("apply"))
                or _attached_geometry_read(method, params)
                or _bound_observation_read(method, params)
            )
            if not read_only:
                record_native_timeout(str(method))
            hint = (
                "inspect the operation and actual target; reconcile before another write; "
                "retain the existing application unless inspection establishes "
                "a restart is necessary"
            )
            budget = current_budget()
            if read_only:
                hint += "; retain original operation history; a later read is supplemental evidence"
            raise CLIError(
                "E_TIMEOUT",
                "Native read timed out; no write was dispatched by this query"
                if read_only
                else "NativeBackend adapter timed out; the session is now stale",
                {
                    "method": str(method),
                    "seconds": timeout_seconds,
                    "hint": hint,
                    "native_query_only": read_only,
                    **({"write_attempted": False} if read_only else {}),
                    **({"operation_budget": budget.report()} if budget is not None else {}),
                },
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
        if TRACE_RETURNED_WARNING in (completed.stderr or "").splitlines():
            # Relay only this fixed advisory; never echo arbitrary native stderr.
            try:
                sys.stderr.write(TRACE_RETURNED_WARNING + "\n")
            except OSError:
                pass  # Diagnostics cannot replace the adapter's returned result.
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
