"""session start | status | stop: the Designer and Layout processes this tool drives.

Xpedition has no notion of a session; this is the tool's own layer over starting,
attaching to and quitting the two applications, with what it did recorded in the
config directory so a later command can explain a crash or a timed-out call.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..backends import NativeBackend
from ..errors import CLIError
from ..native_process import (
    EXIT_OBSERVATION_SECONDS,
    ProcessHandle,
    observe_exit,
    owned_startup_launchers,
    process_identity,
    processes,
    resolve_startup_process,
    same_identity,
)
from ..session import (
    clear_native,
    read_state,
    record_native_attach,
    record_native_closing,
    record_native_failure,
    record_native_start,
    record_native_start_incomplete,
)
from ..session import status as recorded_status
from .common import check_gate, confirmed, native, previewed, text

APPLICATIONS = {"schematic": "Xpedition Designer", "pcb": "Xpedition Layout"}
_HEALTH_KEYS = {"schematic": "designer_application_running", "pcb": "application_running"}
# how long a freshly started application may take to accept automation
STARTUP_SECONDS = 120.0


@contextmanager
def _bound_instance(domain, pid):
    values = {"XPEDITION_EXPECTED_PID": str(pid), "XPEDITION_EXPECTED_DOMAIN": domain}
    old = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _same_application_process(backend, domain, identity):
    observed = backend.invoke("identity", {"domain": domain}, timeout_seconds=60.0)
    application = observed.get("application") or {}
    if application.get("pid") != identity["pid"] or not same_identity(
        process_identity(domain, identity["pid"]), identity
    ):
        raise CLIError("E_CONFLICT", "Actual COM process changed during session setup")
    image = (application.get("public_fields") or {}).get("FullName")
    if (
        not isinstance(image, str)
        or Path(image).resolve() != Path(identity["executable"]).resolve()
    ):
        raise CLIError("E_CONFLICT", "COM executable differs from the pinned process")
    return observed


def _verify_requested_document(application, project, domain):
    if not project:
        return
    if domain == "pcb":
        require_layout_switch_ready(application, project)
    document = application.get("document") or {}
    if document.get("open") is not True or not isinstance(document.get("path"), str):
        raise CLIError("E_VERIFY_INCOMPLETE", "Requested session document is not observed open")
    if domain == "schematic" and Path(document["path"]).resolve() != Path(project).resolve():
        raise CLIError("E_CONFLICT", "Actual Designer project differs from the requested project")


def _observe_owned_launchers(recorded, actual_result, *, wait_seconds=0.0):
    launchers = owned_startup_launchers(
        recorded.get("startup_provenance"), recorded.get("process_identity") or {}
    )
    observations = []
    started = time.monotonic()
    for identity in launchers:
        remaining = max(0.0, wait_seconds - (time.monotonic() - started))
        observation = observe_exit(
            recorded["domain"], str(identity["pid"]), timeout=remaining, expected_identity=identity
        )
        observations.append({"process_identity": identity, "observation": observation})
    complete = all(
        row["observation"].get("process_absence_observed") is True for row in observations
    )
    return {
        "scope": "actual application plus proven original launcher"
        if launchers
        else "actual application only; external launcher ownership unknown",
        "associated_processes": observations,
        "all_owned_launchers_absent": complete,
        "normal_exit_complete": actual_result.get("process_absence_observed") is True and complete,
    }


def _require_no_pending_close() -> None:
    recorded = read_state()
    if recorded.get("state") == "closing":
        raise CLIError(
            "E_CONFLICT",
            "The original close is pending; preserve its session and do not repeat Quit",
            {
                "write_attempted": False,
                "operation_id": recorded.get("closing_operation_id"),
                "next_action": "Use operation show and session inspect for read-only observations",
            },
        )


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
    health = backend.invoke("health", {"domain": domain}, timeout_seconds=60.0)
    return bool(health.get(_HEALTH_KEYS[domain]))


def _dedicated_empty_application(observed, actual):
    """Check the public readback of the already-authenticated owner connection."""
    application = observed.get("application") if isinstance(observed, dict) else None
    if not isinstance(application, dict):
        raise CLIError("E_VERIFY_INCOMPLETE", "Dedicated application readback is incomplete")
    public = application.get("public_fields")
    public = public if isinstance(public, dict) else {}
    document = application.get("document")
    document = document if isinstance(document, dict) else {}
    image = public.get("FullName")
    if (
        application.get("pid") != actual.get("pid")
        or not isinstance(image, str)
        or Path(image).resolve() != Path(actual["executable"]).resolve()
        or document.get("status") != "observed"
        or document.get("open") is not False
        or document.get("path") is not None
    ):
        raise CLIError(
            "E_CONFLICT", "The dedicated Designer owner did not retain the same empty application"
        )
    return application


def _start_dedicated_designer(options):
    """Consume the SDK-bound empty-start route, without discovering another COM app."""
    if text(options, "project"):
        raise CLIError(
            "E_UNSUPPORTED",
            "Dedicated Designer startup accepts an empty session only; open the authorized "
            "project through the same owner afterwards",
            {"write_attempted": False},
        )
    previous = read_state()
    previous_observation = None
    if (
        previous.get("owner_ref")
        or previous.get("state") == "startup_incomplete"
        or options.get("_previous_start_observation") is not None
    ):
        from ..designer_owner_recovery import RecoveryObservation

        observation = options.get("_previous_start_observation")
        if type(observation) is not RecoveryObservation or not observation.matches(previous):
            raise CLIError(
                "E_CONFLICT",
                "A recorded launch already owns this session; inspect its original operation "
                "instead of starting another",
                {"write_attempted": False, "operation_id": previous.get("startup_operation_id")},
            )
        previous_observation = observation.safe_summary
        if previous_observation.get("original_generations_absent") is not True:
            raise CLIError(
                "E_CONFLICT",
                "The original launch generation is not observed closed",
                {"write_attempted": False},
            )
    if not (
        previous.get("state") == "closing"
        and previous_observation is not None
        and previous_observation.get("protocol") == "designer-owned-close-ended-v1"
        and previous_observation.get("original_close_operation_id")
        == previous.get("closing_operation_id")
        and previous.get("closing_operation_id") is not None
    ):
        _require_no_pending_close()
    operation_id = options.get("_operation_id")
    if not isinstance(operation_id, str) or not operation_id:
        raise CLIError(
            "E_CONFIG",
            "Dedicated Designer startup requires the confirmed SDK operation",
            {"write_attempted": False},
        )
    from ..designer_owner_client import validate_owner_ref

    backend = native()
    launched = {
        "domain": "schematic",
        "started": None,
        "attached": False,
        "pid": None,
        "requested_project": None,
        "startup_operation_id": operation_id,
    }
    if previous_observation is not None:
        association = (
            "previous_failed_start"
            if previous_observation.get("protocol") == "designer-failed-start-closed-v1"
            else "previous_session_generation"
        )
        launched[association] = previous_observation
    stage = "dedicated_start_dispatch"
    try:
        reply = backend.invoke(
            "start", {"domain": "schematic", "visible": True}, timeout_seconds=180.0
        )
        stage = "dedicated_start_response"
        if not isinstance(reply, dict):
            raise CLIError("E_VERIFY_INCOMPLETE", "Dedicated startup response is incomplete")
        # The backend owns the private locator/ticket. No endpoint or capability
        # is accepted from CLI options or copied into the public session record.
        fields = (
            "domain",
            "started",
            "attached",
            "pid",
            "executable",
            "launcher_pid",
            "launcher_identity",
            "process_identity",
            "startup_seed",
            "startup_provenance",
            "automation_ready",
            "application_identity",
            "adapter_identity",
            "application_document",
        )
        launched.update({key: reply[key] for key in fields if key in reply})
        launched["owner_ref"] = validate_owner_ref(reply.get("owner_ref"))
        stage = "dedicated_start_identity"
        actual = launched.get("process_identity") or {}
        seed, provenance = (
            launched.get("startup_seed") or {},
            launched.get("startup_provenance") or {},
        )
        if not all(isinstance(value, dict) for value in (actual, seed, provenance)):
            raise CLIError("E_VERIFY_INCOMPLETE", "Dedicated startup ownership is incomplete")
        if (
            launched.get("domain") != "schematic"
            or launched.get("started") is not True
            or launched.get("attached") is not True
            or launched.get("automation_ready") is not True
            or not same_identity(actual, actual)
            or launched.get("pid") != actual.get("pid")
            or launched.get("executable") != actual.get("executable")
            or seed.get("domain") != "schematic"
            or provenance.get("domain") != "schematic"
            or seed.get("version") != provenance.get("version")
            or launched.get("launcher_pid") != (seed.get("launcher_identity") or {}).get("pid")
            or not same_identity(
                seed.get("launcher_identity") or {}, provenance.get("launcher_identity") or {}
            )
        ):
            raise CLIError("E_VERIFY_INCOMPLETE", "Dedicated startup ownership is incomplete")
        owned_startup_launchers(provenance, actual)
        _dedicated_empty_application({"application": launched.get("application_identity")}, actual)
        stable = _dedicated_empty_application(
            backend.invoke("identity", {"domain": "schematic"}, timeout_seconds=60.0), actual
        )
        launched.update(
            application_document=stable["document"], application=APPLICATIONS["schematic"]
        )
        stage = "dedicated_start_record_and_recheck"
        record_native_start(launched)
        stored = read_state()
        if (
            stored.get("state") != "started"
            or stored.get("startup_operation_id") != operation_id
            or stored.get("owner_ref") != launched["owner_ref"]
            or not same_identity(stored.get("process_identity") or {}, actual)
            or stored.get("startup_provenance") != provenance
        ):
            raise CLIError("E_IO", "The dedicated owner session could not be retained")
        final = _dedicated_empty_application(
            backend.invoke("identity", {"domain": "schematic"}, timeout_seconds=60.0), actual
        )
        if final.get("document") != stable.get("document"):
            raise CLIError("E_CONFLICT", "The dedicated application changed after recording")
        launched["_untrusted"] = [
            "application_identity",
            "startup_provenance",
            "application_document",
        ]
        return launched
    except (CLIError, OSError, ValueError, KeyError, TypeError) as error:
        details = (error.details or {}) if isinstance(error, CLIError) else {}
        if stage == "dedicated_start_dispatch" and details.get("write_attempted") is False:
            raise
        if stage == "dedicated_start_dispatch":
            result = details.get("native_result") or details
            for key in (
                "process_identity",
                "startup_seed",
                "startup_provenance",
                "launcher_pid",
                "pid",
            ):
                if key in result:
                    launched[key] = result[key]
            if result.get("owner_ref") is not None:
                try:
                    launched["owner_ref"] = validate_owner_ref(result["owner_ref"])
                except CLIError:
                    pass  # Never echo a malformed locator or its private fields.
        launched["attached"] = False
        record_native_start_incomplete(
            launched, operation_id=operation_id, cause=getattr(error, "code", type(error).__name__)
        )
        raise CLIError(
            getattr(error, "code", "E_VERIFY_INCOMPLETE"),
            "The dedicated launch has no complete verified session; preserve its original ID",
            {
                "write_attempted": True,
                "stage": stage,
                "native_result": launched,
                "next_action": "Query the original operation; do not launch or replay it again",
            },
        ) from error


def start(options: dict[str, Any]) -> dict[str, Any]:
    domain = _kind(options)
    if domain is None:
        raise CLIError("E_USAGE", "session start requires --kind schematic or --kind pcb")
    if domain == "schematic":
        return _start_dedicated_designer(options)
    _require_no_pending_close()
    project = _project_for(options, domain)
    backend = native()
    stale = read_state().get("state") == "stale"
    running = _running(backend, domain)
    if running and stale:
        raise CLIError(
            "E_CONFLICT",
            "the session is stale after a timed-out call and the application is still running",
            {"hint": f"session close --kind {domain}, then session start"},
        )
    if running:
        raise CLIError(
            "E_CONFLICT",
            "the application is already running",
            {
                "next_action": (
                    "use session attach with its explicit native --pid; start never "
                    "attaches implicitly"
                )
            },
        )
    else:
        pending = processes(domain)
        if pending:
            raise CLIError(
                "E_HUMAN_REQUIRED",
                "A native process is already present but its COM application is not ready.",
                {
                    "processes": pending,
                    "action": (
                        "Inspect the existing licensing/startup window; do not launch another "
                        "instance."
                    ),
                    "resume": "session inspect",
                },
            )
        clear_native()
        launched = {"domain": domain, "started": None, "pid": None, "requested_project": project}
        try:
            reply = backend.invoke(
                "start", {"domain": domain, "visible": True}, timeout_seconds=180.0
            )
            launched.update(reply)
        except CLIError as error:
            details = error.details or {}
            if details.get("write_attempted") is False:
                record_native_failure({"code": error.code, "details": details})
                raise
            launched.update(details.get("native_result") or {})
            record_native_start_incomplete(
                launched, operation_id=options.get("_operation_id"), cause=error.code
            )
            raise CLIError(
                error.code,
                error.message,
                {**details, "write_attempted": True, "native_result": launched},
            ) from error
        stage = "startup_readiness"
        try:
            ready = bool(launched.get("automation_ready"))
            deadline = time.monotonic() + STARTUP_SECONDS
            while not ready and time.monotonic() < deadline:
                time.sleep(min(0.5, max(0, deadline - time.monotonic())))
                ready = _running(backend, domain)
            if not ready:
                raise CLIError(
                    "E_TIMEOUT", f"{APPLICATIONS[domain]} did not accept automation in time"
                )
            stage = "startup_actual_process_binding"
            observed = backend.invoke("identity", {"domain": domain}, timeout_seconds=60.0)
            application = observed.get("application") or {}
            binding = resolve_startup_process(
                domain, launched.get("startup_seed") or {}, application
            )
            actual = binding["process_identity"]
            launched.update(
                binding, pid=actual["pid"], executable=actual["executable"], attached=False
            )
            with (
                ProcessHandle(domain, actual["pid"], actual) as pinned,
                _bound_instance(domain, actual["pid"]),
            ):
                if pinned.exited():
                    raise CLIError("E_CONFLICT", "Actual COM process ended before session opening")
                before_open = _same_application_process(backend, domain, actual)["application"]
                if domain == "pcb" and project:
                    require_layout_switch_ready(before_open, project)
                stage = "startup_open_owned_project"
                opened = (
                    backend.invoke(
                        "open",
                        {
                            "project": project,
                            "domain": domain,
                            "start": False,
                            "pid": actual["pid"],
                        },
                        timeout_seconds=420.0,
                    )
                    if project
                    else {}
                )
                stable = _same_application_process(backend, domain, actual)["application"]
                _verify_requested_document(stable, project, domain)
                launched.update(
                    application_document=stable.get("document"),
                    requested_project=project,
                    project=opened.get("project"),
                    prompts=opened.get("prompts") or [],
                    application=APPLICATIONS[domain],
                    started=True,
                    attached=True,
                    startup_operation_id=options.get("_operation_id"),
                )
                stage = "startup_record_and_recheck"
                record_native_start(launched)
                stored = read_state()
                if (
                    not same_identity(stored.get("process_identity") or {}, actual)
                    or stored.get("startup_provenance") != binding["startup_provenance"]
                ):
                    raise CLIError("E_IO", "Actual native startup ownership could not be retained")
                final = _same_application_process(backend, domain, actual)["application"]
                _verify_requested_document(final, project, domain)
                if final.get("document") != stable.get("document") or pinned.exited():
                    raise CLIError(
                        "E_CONFLICT", "COM document/generation changed after startup recording"
                    )
            launched["_untrusted"] = [
                "project",
                "prompts",
                "startup_provenance",
                "application_document",
            ]
            return launched
        except (CLIError, OSError, ValueError, KeyError, TypeError) as error:
            launched["attached"] = False
            record_native_start_incomplete(
                launched,
                operation_id=options.get("_operation_id"),
                cause=getattr(error, "code", type(error).__name__),
            )
            raise CLIError(
                getattr(error, "code", "E_VERIFY_INCOMPLETE"),
                "The application was launched but session ownership/opening was not fully verified",
                {
                    "write_attempted": True,
                    "stage": stage,
                    "native_result": launched,
                    "next_action": "Query the original ID and inspect identities; do not relaunch",
                },
            ) from error


def require_layout_switch_ready(application: dict, project: str) -> None:
    """A session attachment cannot implicitly save/close a different board."""
    from .. import project_file as prj

    requested = Path(project).resolve()
    if requested.suffix.lower() == ".prj":
        design = prj.board_design(prj.designs(requested.read_text(encoding="utf-8")))
        if design is None or not design.pcb_path:
            raise CLIError(
                "E_NOT_FOUND",
                "The requested project has no linked PCB",
                {"write_attempted": False},
            )
        requested = prj.local_path(requested.parent, design.pcb_path).resolve()
    document = application.get("document") or {}
    current = document.get("path")
    if document.get("open") is False:
        return
    if document.get("open") is True and current and Path(current).resolve() == requested:
        return
    raise CLIError(
        "E_CONFLICT",
        "Layout must have no board open, or already have the requested board open",
        {
            "current_board": current,
            "requested_board": str(requested),
            "write_attempted": False,
            "next_action": "Save and normally close the current owned board first, for example "
            "with project backup on that project. Do not close another task's board.",
            "_untrusted": ["current_board", "requested_board"],
        },
    )


def attach(options: dict[str, Any]) -> dict[str, Any]:
    if options.get("project_source_operation_id"):
        from ..designer_project_admission import open_registered_project

        return open_registered_project(options)
    if _kind(options) == "schematic":
        from ..designer_startup import require_dedicated_designer

        require_dedicated_designer()
    if options.get("host_bootstrap"):
        validate_host_bootstrap_options(options)
        from ..backends.native_xpedition import require_designer_host_execution

        require_designer_host_execution()
    _require_no_pending_close()
    if options.get("host_bootstrap"):
        from ..backends.native_xpedition import selected_host_bootstrap_ownership

        packet = selected_host_bootstrap_ownership()
        if not isinstance(packet, dict):
            raise CLIError(
                "E_CONFIG",
                "Use the confirmed public SDK fixed host-bootstrap experiment",
                {"write_attempted": False},
            )
        result = native().invoke(
            "designer_host_bootstrap", {"domain": "schematic"}, timeout_seconds=60.0
        )
        dispatch = result.get("dispatch") or {}
        callbacks = result.get("callbacks") or {}
        cleanup = result.get("cleanup") or {}
        completed = callbacks.get("completed")
        script_completion_unproven = dispatch.get("attempted") is True and (
            dispatch.get("returned") is not True
            or (
                dispatch.get("result_bool") is not False
                and (type(completed) is not int or completed <= 0)
            )
        )
        reply = {
            "domain": "schematic",
            "pid": int(options["pid"]),
            "startup_operation_id": options["startup_operation_id"],
            "host_bootstrap": result,
            "started": False,
            "attached": False,
            "authority_granted": False,
            "native_attach_dispatched": False,
            "native_open_dispatched": False,
            "active_session_context_changed": False,
            "script_completion_unproven": script_completion_unproven,
        }
        if dispatch.get("attempted") is True and (
            cleanup.get("worker_completed") is not True
            or cleanup.get("callback_references_quiescent") is not True
            or type(cleanup.get("inflight_callbacks_at_close")) is not int
            or cleanup.get("inflight_callbacks_at_close") != 0
            or cleanup.get("mta_uninitialized") is not True
            or script_completion_unproven
        ):
            raise CLIError(
                "E_OUTCOME_UNKNOWN",
                "The fixed host bootstrap is pending; do not replay",
                {
                    "write_attempted": True,
                    "native_result": reply,
                    "original_session_metadata_preserved": True,
                },
            )
        return reply
    domain = _kind(options)
    if domain is None:
        raise CLIError("E_USAGE", "session attach requires --kind")
    pid = int(options.get("pid") or 0)
    if pid <= 0:
        raise CLIError("E_VALIDATION", "--pid must be a positive native process ID")
    if options.get("startup_operation_id"):
        return _restore_startup_ownership(options, domain, pid)
    project = _project_for(options, domain)
    backend = native()
    identity = backend.invoke("identity", {"domain": domain}, timeout_seconds=60.0)
    observed = identity.get("application") or {}
    if observed.get("pid") != pid:
        raise CLIError(
            "E_CONFLICT",
            "the active COM application does not match the requested process",
            {
                "requested_pid": pid,
                "observed_application": observed,
                "next_action": (
                    "inspect the application's identity; no attachment or launch was performed"
                ),
            },
        )
    if domain == "pcb" and project:
        require_layout_switch_ready(observed, project)
    try:
        owned_process = process_identity(domain, pid)
        if options.get("_expected_process_identity") is not None and not same_identity(
            owned_process, options["_expected_process_identity"]
        ):
            raise ValueError("The preview's actual process generation changed")
        pinned = ProcessHandle(domain, pid, owned_process)
    except (OSError, ValueError) as error:
        raise CLIError(
            "E_CONFLICT",
            "Cannot pin the requested actual process generation",
            {"write_attempted": False},
        ) from error
    dispatched = False
    try:
        with pinned, _bound_instance(domain, pid):
            observed = _same_application_process(backend, domain, owned_process)["application"]
            if domain == "pcb" and project:
                require_layout_switch_ready(observed, project)
            dispatched = True
            result = backend.invoke("attach", {"domain": domain, "pid": pid})
            if result.get("attached") is not True:
                raise CLIError("E_NATIVE_FAILURE", "Native adapter did not acknowledge attachment")
            result.update(
                domain=domain,
                application=APPLICATIONS[domain],
                started=False,
                pid=pid,
                identity=identity,
                process_identity=owned_process,
            )
            if project:
                result["project"] = backend.invoke(
                    "open",
                    {"domain": domain, "project": project, "start": False, "pid": pid},
                    timeout_seconds=420.0,
                )
            stable = _same_application_process(backend, domain, owned_process)["application"]
            _verify_requested_document(stable, project, domain)
            record_native_attach(
                {
                    **result,
                    "requested_project": project,
                    "application_document": stable.get("document"),
                }
            )
            if not same_identity(read_state().get("process_identity") or {}, owned_process):
                raise CLIError("E_IO", "Native attachment ownership could not be retained")
            final = _same_application_process(backend, domain, owned_process)["application"]
            _verify_requested_document(final, project, domain)
            if final.get("document") != stable.get("document") or pinned.exited():
                raise CLIError(
                    "E_CONFLICT", "Actual document/generation changed after attachment recording"
                )
            result["launcher_ownership"] = read_state().get("launcher_ownership")
            return result
    except (CLIError, OSError, ValueError) as error:
        if dispatched:
            record_native_attach(
                {
                    "domain": domain,
                    "pid": pid,
                    "identity": identity,
                    "process_identity": owned_process,
                    "state": "attachment_incomplete",
                    "requested_project": project,
                }
            )
        raise CLIError(
            getattr(error, "code", "E_VERIFY_INCOMPLETE"),
            "The actual attachment/document could not be fully verified",
            {"write_attempted": dispatched, "native_query_only": not dispatched},
        ) from error


def validate_host_bootstrap_options(options):
    """Reject widened bootstrap requests before COM, process inspection or helpers."""
    if (
        options.get("host_bootstrap") != "identity"
        or options.get("kind") != "schematic"
        or not options.get("startup_operation_id")
        or options.get("project")
        or type(options.get("pid")) not in (int, str)
    ):
        raise CLIError(
            "E_USAGE",
            "--host-bootstrap identity requires --kind schematic, --pid and "
            "--startup-operation-id, and excludes --project",
            {"write_attempted": False},
        )
    try:
        valid = int(options["pid"]) > 0 and str(options["pid"]).isdecimal()
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise CLIError("E_VALIDATION", "--pid must be a positive native process ID")


def _restore_startup_ownership(options, domain, pid):
    """Restore an original receipt pointer without opening or activating CAD."""
    from ..session_ownership import register

    if options.get("project"):
        raise CLIError(
            "E_USAGE",
            "--startup-operation-id cannot be combined with --project",
            {"write_attempted": False},
        )
    resolver = options.get("_ownership_resolver")
    if not callable(resolver):
        raise CLIError(
            "E_CONFIG",
            "Original startup recovery requires the public SDK ledger reader",
            {"write_attempted": False},
        )
    backend = native()
    actual = process_identity(domain, pid)
    expected = options.get("_expected_process_identity")
    if expected is not None and not same_identity(actual, expected):
        raise CLIError("E_CONFLICT", "The planned native process generation changed")
    with ProcessHandle(domain, pid, actual) as pinned, _bound_instance(domain, pid):
        before = _same_application_process(backend, domain, actual)["application"]
        context, pointer = resolver(domain, actual, options["startup_operation_id"])

        def document_matches(application):
            document = application.get("document") or {}
            if document.get("status") != "observed":
                raise CLIError(
                    "E_CONFLICT",
                    "The original instance's current document is unobserved",
                    {"write_attempted": False},
                )
            if document.get("open") is False and not document.get("path"):
                return
            _verify_requested_document(application, context.get("requested_project"), domain)
            if not context.get("requested_project"):
                raise CLIError(
                    "E_CONFLICT",
                    "An open document has no original startup source binding",
                    {"write_attempted": False},
                )

        document_matches(before)
        after = _same_application_process(backend, domain, actual)["application"]
        document_matches(after)
        if before.get("document") != after.get("document") or pinned.exited():
            raise CLIError(
                "E_CONFLICT",
                "The native document or generation changed during recovery",
                {"write_attempted": False},
            )
        _fresh, final_pointer = resolver(domain, actual, options["startup_operation_id"])
        if final_pointer != pointer:
            raise CLIError(
                "E_CONFLICT",
                "The original startup record changed during recovery",
                {"write_attempted": False},
            )
        register(pointer)
        return {
            "domain": domain,
            "pid": pid,
            "started": False,
            "attached": True,
            "ownership_recovered": True,
            "startup_operation_id": options["startup_operation_id"],
            "process_identity": actual,
            "application_document": after.get("document"),
            "launcher_ownership": "proven_this_start",
            "native_attach_dispatched": False,
            "native_open_dispatched": False,
            "active_session_context_changed": False,
        }


def _dedicated_owner_status(state):
    """Observe the recorded owner only; an invalid source never triggers discovery."""
    result = {
        "xpedition": {"ready": None, "reason": None},
        "designer": {"running": None},
        "layout": {"running": None, "com_probe_skipped": "dedicated Designer owner only"},
        "recorded": {
            "state": state.get("state"),
            "domain": "schematic",
            "pid": state.get("pid"),
            "startup_operation_id": state.get("startup_operation_id"),
            "timed_out_method": state.get("timed_out_method"),
        },
        "stale": state.get("state") == "stale",
        "_untrusted": ["recorded", "designer.identity"],
    }
    try:
        from ..designer_owner_client import validate_owner_ref

        validate_owner_ref(state.get("owner_ref"))
        actual = state.get("process_identity") or {}
        if state.get("domain") != "schematic" or not same_identity(actual, actual):
            raise CLIError("E_CONFLICT", "Recorded Designer owner process binding is incomplete")
        if state.get("state") == "closed":
            result["designer"].update(
                running=False,
                com_probe_skipped="recorded ended generation; no connection reacquired",
            )
            return result
        if state.get("state") == "closing":
            from ..designer_owner_client import validate_routed_owner

            validate_routed_owner(state["owner_ref"])
            observed = observe_exit(
                "schematic",
                str(state.get("pid")),
                timeout=0,
                expected_identity=state.get("process_identity"),
            )
            observed["owned_launch_chain"] = _observe_owned_launchers(state, observed)
            result["closure_observation"] = {
                **observed,
                "original_operation_id": state.get("closing_operation_id"),
                "historical_receipt_changed": False,
                "quit_replayed": False,
            }
            result["designer"]["running"] = (
                False if observed.get("process_absence_observed") is True else None
            )
            result["designer"]["com_probe_skipped"] = "original close pending; OS observation only"
            return result
        identity = native().invoke("identity", {"domain": "schematic"}, timeout_seconds=60.0)
        application = identity.get("application") if isinstance(identity, dict) else None
        if not isinstance(application, dict):
            raise CLIError("E_VERIFY_INCOMPLETE", "Dedicated application readback is incomplete")
        public = application.get("public_fields")
        image = public.get("FullName") if isinstance(public, dict) else None
        if (
            not same_identity(actual, actual)
            or application.get("pid") != actual.get("pid")
            or not isinstance(image, str)
            or Path(image).resolve() != Path(actual["executable"]).resolve()
        ):
            raise CLIError("E_CONFLICT", "Recorded Designer owner identity changed")
        result["designer"].update(running=True, identity=identity)
        result["xpedition"]["ready"] = True
        try:
            result["designer"]["project_state"] = native().invoke(
                "read_project", {"domain": "schematic"}, timeout_seconds=60.0
            )
            result["_untrusted"].append("designer.project_state")
        except (CLIError, OSError, ValueError, KeyError, TypeError) as error:
            result["designer"]["project_state"] = None
            result["designer"]["project_state_error"] = {
                "code": getattr(error, "code", "E_VERIFY_INCOMPLETE"),
                "observation": "unobserved",
            }
    except (CLIError, OSError, ValueError, KeyError, TypeError) as error:
        result["designer"]["identity_error"] = {
            "code": getattr(error, "code", "E_VERIFY_INCOMPLETE"),
            "message": "The recorded Designer owner could not be verified; discovery was not used",
        }
    return result


def status(options: dict[str, Any]) -> dict[str, Any]:
    probe = text(options, "designer_identity_probe")
    kind = text(options, "kind")
    if bool(probe) != bool(kind) or probe and (probe != "application-proxy" or kind != "schematic"):
        raise CLIError(
            "E_USAGE",
            "Designer proxy diagnostics require --kind schematic and "
            "--designer-identity-probe application-proxy together",
        )
    if probe:
        from ..backends.native_xpedition import selected_designer_identity_probe

        if selected_designer_identity_probe() != probe:
            raise CLIError("E_CONFIG", "Use the scoped public session inspect diagnostic selector")
    state = read_state()
    if state.get("domain") == "schematic" or state.get("owner_ref") is not None:
        if probe:
            raise CLIError("E_UNSUPPORTED", "A dedicated Designer session uses its owner identity")
        return _dedicated_owner_status(state)
    backend = NativeBackend()
    adapter = backend.status()
    recorded = recorded_status()
    if probe and state.get("state") == "closing":
        raise CLIError(
            "E_CONFLICT",
            "The explicit Designer diagnostic cannot inspect a pending close",
            {"write_attempted": False, "state_write_attempted": False, "native_query_only": True},
        )
    exit_observation = None
    if state.get("state") == "closing" and state.get("process_identity"):
        try:
            exit_observation = observe_exit(
                state["domain"],
                str(state["pid"]),
                timeout=0,
                expected_identity=state["process_identity"],
            )
            chain = _observe_owned_launchers(state, exit_observation)
            exit_observation["owned_launch_chain"] = chain
            if chain["normal_exit_complete"]:
                clear_native()
        except (OSError, ValueError) as error:
            exit_observation = {"observation_complete": False, "cause_type": type(error).__name__}
    result: dict[str, Any] = {
        "xpedition": {"ready": bool(adapter.get("available")), "reason": adapter.get("reason")},
        "designer": {"running": None},
        "layout": {"running": None},
        "recorded": {
            "state": recorded.get("state"),
            "domain": state.get("domain"),
            "pid": recorded.get("pid"),
            "timed_out_method": state.get("timed_out_method"),
            "startup_operation_id": state.get("startup_operation_id"),
            "launcher_ownership": state.get("launcher_ownership"),
        },
        "stale": state.get("state") == "stale",
        "_untrusted": ["xpedition.reason", "recorded"],
    }
    if exit_observation is not None:
        result["closure_observation"] = {
            **exit_observation,
            "original_operation_id": state.get("closing_operation_id"),
            "historical_receipt_changed": False,
            "quit_replayed": False,
        }
        # Do not reacquire a quitting COM server merely to explain its status.
        key = "layout" if state["domain"] == "pcb" else "designer"
        result[key]["running"] = (
            False if exit_observation.get("process_absence_observed") is True else None
        )
        result[key]["com_probe_skipped"] = "original close is pending; OS observation only"
        return result
    if adapter.get("available"):
        if probe:
            result["identity_probe"] = {
                "kind": "schematic",
                "selector": probe,
                "debugging_only": True,
                "authority_granted": False,
            }
            result["layout"]["com_probe_skipped"] = "explicit Designer-only diagnostic"
            try:
                result["designer"]["identity"] = backend.invoke(
                    "identity", {"domain": "schematic"}, timeout_seconds=60.0
                )
                result["designer"]["running"] = True
            except CLIError as error:
                result["designer"]["identity_error"] = {
                    "code": error.code,
                    "message": error.message,
                }
            return result
        try:
            health = backend.invoke("health", {"domain": "pcb"}, timeout_seconds=60.0)
        except CLIError as error:
            result["probe_error"] = {"code": error.code, "message": error.message}
        else:
            result["designer"]["com_probe_skipped"] = "no recorded dedicated Designer owner"
            result["layout"]["running"] = bool(health.get("application_running"))
            result["adapter_identity"] = health.get("adapter_identity")
            result["registered_progids"] = health.get("registered_progids")
            for domain, key in (("pcb", "layout"),):
                if result[key]["running"]:
                    try:
                        result[key]["identity"] = backend.invoke(
                            "identity", {"domain": domain}, timeout_seconds=60.0
                        )
                    except CLIError as error:
                        result[key]["identity_error"] = {
                            "code": error.code,
                            "message": error.message,
                        }
                else:
                    result[key]["pending_processes"] = processes(domain)
    return result


def _stop_domain(options: dict[str, Any]) -> str:
    """Resolve one explicitly owned application; never guess between two CAD apps."""
    requested = _kind(options)
    state = read_state()
    if requested == "schematic":
        from ..designer_owner_client import validate_owner_ref

        if state.get("domain") != "schematic":
            raise CLIError("E_CONFLICT", "No dedicated Designer owner is recorded")
        validate_owner_ref(state.get("owner_ref"))
        return requested
    if requested is None and state.get("domain") == "schematic":
        raise CLIError("E_USAGE", "Dedicated Designer close requires --kind schematic")
    try:
        # A Layout choice must not inspect or acquire an arbitrary Designer.
        layout_running = _running(native(), "pcb")
    except CLIError:
        if requested is None:
            raise CLIError(
                "E_USAGE",
                "could not probe which application is running; name one with --kind",
            ) from None
        return requested
    if layout_running:
        return "pcb"
    raise CLIError("E_NOT_FOUND", "Layout is not running; no recorded Designer was selected")


def stop(options: dict[str, Any]) -> dict[str, Any]:
    check_gate(options, "session close")
    _kind(options)  # a malformed --kind is a usage error before anything is probed
    preparation_preview = False
    if options.get("dry_run") and read_state().get("state") == "closing":
        from ..designer_driver_routing import current_prepared_close
        from ..session_ownership import PreparedCloseObservation

        observed = current_prepared_close()
        preparation_preview = type(observed) is PreparedCloseObservation and observed.matches(
            read_state()
        )
    if not preparation_preview:
        _require_no_pending_close()
    backend = native()
    try:
        domain = _stop_domain(options)
    except CLIError as error:
        raise CLIError(
            error.code, error.message, {**(error.details or {}), "write_attempted": False}
        ) from error
    pid = int(options.get("pid") or 0)
    if pid <= 0:
        raise CLIError("E_USAGE", "session close requires the explicit owned --pid")
    try:
        observed = (
            backend.invoke("identity", {"domain": domain}, timeout_seconds=60.0).get("application")
            or {}
        )
        actual_process = process_identity(domain, pid)
    except (CLIError, OSError, ValueError) as error:
        raise CLIError(
            "E_CONFLICT",
            "The owned native process identity could not be established",
            {"write_attempted": False, "cause_type": type(error).__name__},
        ) from error
    expected = options.get("_expected_process_identity") or actual_process
    resolver = options.get("_ownership_resolver")
    if not callable(resolver):
        from ..session_ownership import select

        def resolver(selected_domain, actual):
            return select(selected_domain, actual)

    recorded, _pointer = resolver(domain, actual_process)
    if (
        observed.get("pid") != pid
        or recorded.get("domain") != domain
        or recorded.get("pid") != pid
        or not same_identity(actual_process, recorded.get("process_identity") or {})
        or not same_identity(actual_process, expected)
    ):
        raise CLIError(
            "E_CONFLICT",
            "Close requires this task's attached process with an unchanged creation time",
            {
                "application": observed,
                "write_attempted": False,
                "next_action": "Explicitly attach the owned PID first",
            },
        )
    project = None
    if options.get("save_project"):
        project = _project_for({"project": options["save_project"]}, domain)
        if domain != "pcb":
            raise CLIError(
                "E_UNSUPPORTED",
                "--save-project currently supports Layout only",
                {"write_attempted": False},
            )
        require_layout_switch_ready(observed, project)
    else:
        document = observed.get("document") or {}
        if document.get("open") is not False or document.get("status") != "observed":
            raise CLIError(
                "E_CONFLICT",
                "Session-only close requires an observed empty document; "
                "use --save-project for explicit save/close",
                {"document": document, "write_attempted": False},
            )
    scope = {
        "operation": "session_stop",
        "domain": domain,
        "pid": pid,
        "process_identity": actual_process,
        "save_project": project,
        "owned_launcher_identities": owned_startup_launchers(
            recorded.get("startup_provenance"), actual_process
        ),
    }
    if options.get("dry_run"):
        preview = {
            "domain": domain,
            "application": APPLICATIONS[domain],
            "process_identity": actual_process,
            "owned_process": True,
            "save_project": project,
            "document": observed.get("document"),
            "owned_launcher_identities": scope["owned_launcher_identities"],
            "changes": ([{"action": "save_close_project", "project": project}] if project else [])
            + [{"action": "quit_application_once", "domain": domain}],
            "risk": {
                "tier": "T1",
                "blast_radius": f"{APPLICATIONS[domain]} exits only after "
                "its document is confirmed closed",
            },
        }
        return previewed(preview, scope)
    confirmed(options, scope)
    operation_id = options.get("_operation_id")
    record_native_closing(
        domain, actual_process, operation_id=operation_id, ownership_record=recorded
    )
    try:
        result = backend.invoke(
            "close",
            {
                "domain": domain,
                "pid": pid,
                "expected_process_identity": actual_process,
                "save_project": project,
                "exit_wait_seconds": EXIT_OBSERVATION_SECONDS,
            },
            timeout_seconds=180.0,
        )
    except CLIError as error:
        record_native_closing(
            domain,
            actual_process,
            operation_id=operation_id,
            outcome={"code": error.code, "details": error.details or {}},
        )
        raise
    if result.get("process_absence_observed") is True and result.get("closed") is True:
        elapsed = (result.get("exit_observation") or {}).get("elapsed_seconds", 0.0)
        remaining = max(0.0, EXIT_OBSERVATION_SECONDS - float(elapsed))
        try:
            chain = _observe_owned_launchers(recorded, result, wait_seconds=remaining)
        except (OSError, ValueError) as error:
            chain = {
                "normal_exit_complete": False,
                "observation_complete": False,
                "cause_type": type(error).__name__,
            }
        result["application_process_absence_observed"] = True
        result["owned_launch_chain"] = chain
        if chain.get("normal_exit_complete") is True:
            clear_native(closed_observation=result)
        else:
            result.update(closed=False, closing_pending=True)
            record_native_closing(domain, actual_process, operation_id=operation_id, outcome=result)
            raise CLIError(
                "E_HUMAN_REQUIRED",
                "Actual COM application exited but its owned launcher chain remains unclosed",
                {
                    "write_attempted": True,
                    "native_result": result,
                    "next_action": "Observe the original close ID; never repeat Quit",
                },
            )
    else:
        record_native_closing(domain, actual_process, operation_id=operation_id, outcome=result)
    result["application"] = APPLICATIONS[domain]
    return result
