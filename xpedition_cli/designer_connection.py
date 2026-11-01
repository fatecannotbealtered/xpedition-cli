"""Pure offline model of a dedicated Designer connection owner.

There is no COM, transport, marshaller, process query or native admission here.
The default verifier is deliberately unqualified. Private fixture evidence can
exercise the state machine, but can only produce ``offline_ready``. A launch
anchor constrains evidence; PID, path, scope and random strings are not proof.

Callback capture performs no application getters. Startup return and owner-side
materialization are separate fixture barriers, not a qualified Designer startup
contract. The creating thread owns the retained opaque object and commands.
Observation cannot dispatch, cancel, replay, or revise the first unknown receipt.
"""

from __future__ import annotations

import copy
import math
import ntpath
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

MAX_OPERATIONS = 64
MAX_OBSERVATION_SECONDS = 1.0


class ConnectionError(ValueError):
    """Fixed safe reason; never includes object contents or external errors."""


def native_connection_enabled() -> bool:
    """No production host-origin transport has been qualified for this model."""
    return False


@dataclass(frozen=True)
class ProcessGeneration:
    pid: int
    birth_100ns: int
    image: str = field(repr=False)

    def __post_init__(self):
        if (
            type(self.pid) is not int
            or self.pid <= 0
            or type(self.birth_100ns) is not int
            or self.birth_100ns <= 0
            or not isinstance(self.image, str)
            or not ntpath.isabs(self.image)
        ):
            raise ConnectionError("invalid_launch_generation")

    def matches(self, other: ProcessGeneration) -> bool:
        return (
            type(other) is ProcessGeneration
            and self.pid == other.pid
            and self.birth_100ns == other.birth_100ns
            and ntpath.normcase(ntpath.normpath(self.image))
            == ntpath.normcase(ntpath.normpath(other.image))
        )


@dataclass(frozen=True)
class LaunchAnchor:
    launch_intent_id: str = field(repr=False)
    session_epoch: str = field(repr=False)
    expected_peer: ProcessGeneration
    project_scope_id: str = field(repr=False)
    template_sha256: str

    def __post_init__(self):
        if (
            any(
                not isinstance(value, str) or not value
                for value in (self.launch_intent_id, self.session_epoch, self.project_scope_id)
            )
            or type(self.expected_peer) is not ProcessGeneration
            or not isinstance(self.template_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", self.template_sha256)
        ):
            raise ConnectionError("invalid_launch_anchor")


@dataclass(frozen=True, repr=False)
class SessionHandle:
    """Only the issuing session recognizes this in-memory capability."""

    _token: object


@dataclass(frozen=True, repr=False)
class _Capture:
    peer: ProcessGeneration
    anchor: LaunchAnchor
    application: object
    source: str


@dataclass(frozen=True, repr=False)
class _FixtureCommand:
    token: object


class UnqualifiedTransportVerifier:
    """The sole production-facing verifier: no evidence can admit a connection."""

    def capture(self, anchor, message):
        raise ConnectionError("native_transport_unqualified")

    def startup_returned(self, anchor, capture, proof):
        raise ConnectionError("native_transport_unqualified")

    def command(self, command):
        raise ConnectionError("native_transport_unqualified")


class _FixtureTransportVerifier:
    """Test-only object capabilities; no claim of OS or CAD authentication.

    This private fixture issuer never turns a caller's ``qualified=True`` into
    authority. Native callers must use the unqualified default. Python-private
    names are not a security boundary against hostile code in this interpreter.
    """

    def __init__(self, anchor, host_application):
        self._anchor = anchor
        self._host_application = host_application
        self._messages = {}
        self._barriers = {}
        self._commands = {}
        self._used = set()

    def issue_capture(
        self,
        *,
        peer=None,
        anchor=None,
        application=None,
        source="host_supplied_application",
    ):
        token = object()
        self._messages[token] = _Capture(
            peer if peer is not None else self._anchor.expected_peer,
            anchor if anchor is not None else self._anchor,
            application if application is not None else self._host_application,
            source,
        )
        return token

    def capture(self, anchor, message):
        try:
            captured = self._messages[message]
        except (KeyError, TypeError):
            raise ConnectionError("host_origin_proof_missing") from None
        if message in self._used:
            raise ConnectionError("host_origin_proof_reused")
        if captured.anchor != anchor or anchor != self._anchor:
            raise ConnectionError("launch_epoch_or_scope_mismatch")
        if not anchor.expected_peer.matches(captured.peer):
            raise ConnectionError("host_peer_generation_mismatch")
        if (
            captured.source != "host_supplied_application"
            or captured.application is not self._host_application
        ):
            raise ConnectionError("host_application_provenance_mismatch")
        self._used.add(message)
        return captured

    def issue_startup_return(self, message):
        token = object()
        self._barriers[token] = message
        return token

    def startup_returned(self, anchor, capture, proof):
        try:
            message = self._barriers[proof]
            original = self._messages[message]
        except (KeyError, TypeError):
            raise ConnectionError("startup_return_proof_missing") from None
        if original is not capture or original.anchor != anchor:
            raise ConnectionError("startup_return_proof_mismatch")

    def issue_command(self, function: Callable[[object], object], *, requires_save=False):
        if not callable(function) or type(requires_save) is not bool:
            raise ConnectionError("invalid_fixture_command")
        command = _FixtureCommand(object())
        self._commands[command.token] = (function, requires_save)
        return command

    def command(self, command):
        if type(command) is not _FixtureCommand or command.token not in self._commands:
            raise ConnectionError("offline_fixture_command_required")
        return self._commands[command.token]

    def retire_owned_references(self):
        # The caller holds the connection lock. Keep every replaced graph alive
        # until that caller drops this tuple outside the observer's lock.
        retired = (
            self._host_application,
            self._messages,
            self._barriers,
            self._commands,
            self._used,
        )
        self._host_application = None
        self._messages, self._barriers, self._commands, self._used = {}, {}, {}, set()
        return retired


class ConnectionSession:
    """One epoch and one owner thread; disconnect never creates a replacement.

    The owner executor supplies this model's lifetime. This class starts no
    thread or process, persists nothing and exports no application reference.
    The private fixture command injection is not a public native dispatch API.
    """

    def __init__(self, anchor: LaunchAnchor, *, verifier=None):
        if type(anchor) is not LaunchAnchor:
            raise ConnectionError("launch_anchor_required")
        self._verifier = verifier if verifier is not None else UnqualifiedTransportVerifier()
        if type(self._verifier) not in (UnqualifiedTransportVerifier, _FixtureTransportVerifier):
            raise ConnectionError("unsupported_transport_verifier")
        self._anchor = anchor
        self._owner_thread = threading.current_thread()
        self._owner_ident = threading.get_ident()
        self._condition = threading.Condition()
        self._state = "awaiting_host"
        self._capture = None
        self._application = None
        self._host_captured = False
        self._handle = None
        self._startup_returned = False
        self._materialized = False
        self._inflight = 0
        self._operations = {}
        self._cleanup_verified = None
        self._cleanup_attempted = False
        self._fixture_release_completed = None
        self._owner_exited = None

    def _require_owner(self):
        if (
            threading.current_thread() is not self._owner_thread
            or threading.get_ident() != self._owner_ident
        ):
            raise ConnectionError("owner_thread_required")

    def _require_handle(self, handle):
        if (
            self._state != "offline_ready"
            or self._handle is None
            or handle is not self._handle
            or not self._materialized
        ):
            raise ConnectionError("connection_handle_expired_or_unrecognized")

    def capture_host_application(self, message):
        """Callback stage: authenticate and retain only, with no app getters."""
        with self._condition:
            if self._state != "awaiting_host":
                raise ConnectionError("connection_not_accepting_host")
            captured = self._verifier.capture(self._anchor, message)
            self._capture = captured
            self._application = captured.application
            self._host_captured = True
            self._state = "host_captured"
            self._condition.notify_all()
            return self._snapshot()

    def mark_startup_returned(self, proof):
        """A fixture barrier, not proof of any installed Designer callback API."""
        self._require_owner()
        with self._condition:
            if self._state != "host_captured":
                raise ConnectionError("host_capture_required")
            self._verifier.startup_returned(self._anchor, self._capture, proof)
            self._startup_returned = True
            self._condition.notify_all()

    def materialize(self):
        """Model owner-thread materialization; no COM marshalling is performed."""
        self._require_owner()
        with self._condition:
            if self._state != "host_captured" or not self._startup_returned:
                raise ConnectionError("startup_return_barrier_required")
            if self._capture is None or self._application is not self._capture.application:
                raise ConnectionError("held_application_identity_mismatch")
            self._materialized = True
            self._handle = SessionHandle(object())
            self._state = "offline_ready"
            self._condition.notify_all()
            return self._handle

    def execute(self, handle, operation_id, command):
        """Execute an issued fake command once on the owner-held fixture object."""
        self._require_owner()
        with self._condition:
            self._require_handle(handle)
            if self._inflight:
                raise ConnectionError("owner_command_already_inflight")
            if not isinstance(operation_id, str) or not re.fullmatch(
                r"fixture-operation-[A-Za-z0-9_-]{1,64}", operation_id
            ):
                raise ConnectionError("new_fixture_operation_id_required")
            if operation_id in self._operations:
                raise ConnectionError("operation_already_received_no_replay")
            if len(self._operations) >= MAX_OPERATIONS:
                raise ConnectionError("offline_operation_limit")
            function, requires_save = self._verifier.command(command)
            record = {
                "initial_observation": {
                    "operation_id": operation_id,
                    "outcome": "unknown",
                    "native_admitted": False,
                },
                "received": True,
                "execution_started": True,
                "execution_completed": None,
                "execution_completion_basis": "fixture_callable_return_only",
                "save_required": requires_save,
                "save_completed": None,
                "connection_valid_at_return": None,
                "failure_stage": None,
            }
            self._operations[operation_id] = record
            application = self._application
            issued_handle = self._handle
            self._inflight += 1
            self._condition.notify_all()
        failed = False
        try:
            function(application)
        except BaseException:
            failed = True
        finally:
            application = None
            with self._condition:
                self._inflight -= 1
                record["execution_completed"] = not failed
                record["connection_valid_at_return"] = (
                    self._state == "offline_ready" and self._handle is issued_handle
                )
                if failed:
                    record["failure_stage"] = "execution"
                elif not record["connection_valid_at_return"]:
                    record["failure_stage"] = "connection_invalidated_during_execution"
                self._condition.notify_all()
        if failed:
            raise ConnectionError("fixture_command_failed")
        return self.observe(operation_id)

    def record_save_result(self, handle, operation_id, *, saved):
        self._require_owner()
        with self._condition:
            self._require_handle(handle)
            record = self._operations.get(operation_id)
            if record is None or record["execution_completed"] is not True:
                raise ConnectionError("completed_execution_required_before_save_result")
            if not record["save_required"] or type(saved) is not bool:
                raise ConnectionError("explicit_required_save_result_required")
            if record["save_completed"] is not None:
                raise ConnectionError("save_result_already_recorded")
            record["save_completed"] = saved
            if not saved:
                record["failure_stage"] = "save"
            self._condition.notify_all()

    def _snapshot(self):
        return {
            "mode": "offline_fixture_model",
            "state": self._state,
            "native_admitted": False,
            "host_origin_observation": "fixture_only" if self._host_captured else "unobserved",
            "startup_returned": True if self._startup_returned else None,
            "startup_return_observation": "fixture_only"
            if self._startup_returned
            else "unobserved",
            "owner_materialized": self._materialized,
            "inflight_commands": self._inflight,
            "cleanup_verified": self._cleanup_verified,
            "cleanup_scope": "offline_connection_slots_and_fixture_release_only",
            "fixture_release_completed": self._fixture_release_completed,
            "native_reference_release": None,
            "owner_exited": self._owner_exited,
            "owner_exit_basis": "fixture_declaration" if self._owner_exited else "unobserved",
        }

    def status(self):
        with self._condition:
            return self._snapshot()

    def observe(self, operation_id, *, timeout_seconds=0.0):
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0 <= timeout_seconds <= MAX_OBSERVATION_SECONDS
        ):
            raise ConnectionError("bounded_observation_budget_required")
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            record = self._operations.get(operation_id)
            if record is None:
                raise ConnectionError("operation_not_owned")
            while record["execution_completed"] is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            return {**self._snapshot(), "operation": copy.deepcopy(record)}

    def disconnect(self):
        """Invalidate all handles; does not stop a call or manufacture cleanup."""
        with self._condition:
            if self._state not in ("closed", "owner_exited"):
                self._state = "disconnected"
                self._handle = None
            self._condition.notify_all()
            return self._snapshot()

    def cleanup(self, release=None):
        """Owner-side fixture cleanup; a failed release stays failed/unresolved."""
        self._require_owner()
        with self._condition:
            if self._inflight:
                raise ConnectionError("cleanup_waits_for_actual_command_return")
            if self._cleanup_attempted:
                raise ConnectionError("cleanup_already_attempted")
            if release is not None and type(self._verifier) is not _FixtureTransportVerifier:
                raise ConnectionError("offline_fixture_release_required")
            self._state = "cleanup_pending"
            self._cleanup_attempted = True
            self._handle = None
            retired = (self._application, self._capture)
            if type(self._verifier) is _FixtureTransportVerifier:
                retired += self._verifier.retire_owned_references()
            self._application = self._capture = None
            self._materialized = False
            self._condition.notify_all()
        application = retired[0]
        failed = False
        release_attempted = False
        try:
            if release is not None and application is not None:
                release_attempted = True
                release(application)
        except BaseException:
            failed = True
        finally:
            # A destructor or fixture closure release may block. During that
            # time observers retain access to cleanup_pending / null evidence.
            application = release = retired = None
            with self._condition:
                self._fixture_release_completed = not failed if release_attempted else None
                self._cleanup_verified = not failed
                self._state = "cleanup_failed" if failed else "closed"
                self._condition.notify_all()
        return self.status()

    def mark_owner_exited(self):
        """Fixture lifetime fact only; no OS exit/generation assertion is made."""
        self._require_owner()
        with self._condition:
            if self._cleanup_verified is not True or self._state != "closed":
                raise ConnectionError("successful_cleanup_required_before_model_exit")
            self._owner_exited = True
            self._state = "owner_exited"
            self._condition.notify_all()
            return self._snapshot()
