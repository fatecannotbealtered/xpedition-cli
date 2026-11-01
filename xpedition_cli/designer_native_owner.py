"""Source-only dedicated Designer owner: private measurement entry, no takeover.

The child, rather than a bounded CLI observer, owns the original launch lease,
startup files, MTA broker and STA Application. This module never enables the
public native gate. Native connection evidence is produced by the child, not by
a caller-supplied qualified flag. Measurement commands retain their read/lifecycle
allowlist. A separate public intent serially dispatches the adapter after host,
scope and source verification.

No imported code activates COM. ``launch_measurement_owner`` is an explicit
experimental entry for the reviewed dedicated launcher, not a production grant.
Windows job termination/logoff can still lose a child; that remains unknown.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import hmac
import json
import ntpath
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xpedition_cli import execution_lifecycle as L
from xpedition_cli.designer_owner_transport import (
    ExpectedGeneration,
    TransportError,
    create_native_runtime,
)

COMPONENT = "designer-dedicated-native-owner-measurement-v1"
COMMANDS = ("identity", "read_project", "save_close_project", "quit_empty")
DOCS_IID = "{BD2EDF77-7E28-11CE-822D-00001B4D36B5}"
MAX_COMMANDS = 8
PUBLIC_MODE = "confirmed-public-owner-v1"
MAX_PUBLIC_COMMANDS = 128
MAX_PUBLIC_BYTES = 128 * 1024 * 1024
MAX_COMMAND_RECORD_BYTES = 1024 * 1024
VERIFICATION_COMMAND_RESERVE = 8
CHECKPOINT_COMMAND_RESERVE = 8
QUIT_COMMAND_RESERVE = 8
CAPACITY_SCHEME = "designer-owner-capacity-v1"
# request and terminal observation are independently capped at 1MiB. The
# initial record and two fixed phase records are bounded separately below.
MAX_COMMAND_AUX_BYTES = 8192
MAX_COMMAND_SLOT_BYTES = 2 * MAX_COMMAND_RECORD_BYTES + MAX_COMMAND_AUX_BYTES
PDF_PRODUCER_JSON = frozenset(
    {"pdf-producer-initial.json", "pdf-producer-launched.json", "pdf-producer-returned.json"}
)
PDF_PRODUCER_LOGS = ("pdf-producer-stdout.bin", "pdf-producer-stderr.bin")
MAX_PDF_PRODUCER_LOG_BYTES = 1024 * 1024
MAX_PDF_COMMAND_SLOT_BYTES = (
    MAX_COMMAND_SLOT_BYTES
    + len(PDF_PRODUCER_JSON) * MAX_COMMAND_RECORD_BYTES
    + len(PDF_PRODUCER_LOGS) * MAX_PDF_PRODUCER_LOG_BYTES
)
MAX_NATIVE_BOUNDARY_BYTES = 16 * 1024 * 1024
MAX_OBSERVER_SECONDS = 2.0
LAUNCH_COMPONENT_STAGES = frozenset(
    {
        "startup.preflight",
        "startup.prepare_project",
        "startup.create_inputs",
        "startup.verify_inputs",
        "startup.spawn",
        "startup.pin_launcher",
    }
)
LAUNCH_COMPONENT_REASONS = frozenset(
    {
        "startup_input_contract_incomplete",
        "startup_input_access_mask_mismatch",
        "startup_input_identity_mismatch",
        "startup_input_readback_mismatch",
        "startup_input_security_mismatch",
        "startup_input_resource_limit",
        "startup_input_native_api_failed",
        "startup_input_handle_release_failed",
        "existing_designer_blocks_dedicated_empty_launch",
        "dedicated_native_launch_incomplete",
    }
)
SNAPSHOT_CALLPOINTS = frozenset(
    {
        "CreateFileW",
        "GetFileInformationByHandle",
        "SetFileInformationByHandle",
        "NtSetInformationFile",
        "CloseHandle",
    }
)


class OwnerError(ValueError):
    """Safe fixed diagnostic reason, with no endpoint or external exception."""


class OwnerCommandNotPublished(OwnerError):
    """A proved admission refusal before any new command directory is created."""

    def __init__(self, reason, capacity=None, *, code="E_CONFLICT"):
        super().__init__(reason)
        self.capacity = copy.deepcopy(capacity)
        self.code = code


class _PublicCommandLock:
    """Lock admission is no-publication; lock release retains the actual phase."""

    def __init__(self, root, publication):
        self.manager = L._launch_lock(root)
        self.publication = publication

    def __enter__(self):
        try:
            return self.manager.__enter__()
        except (OSError, ValueError, KeyError, TypeError):
            raise OwnerCommandNotPublished("public_owner_admission_lock_busy") from None

    def __exit__(self, *arguments):
        try:
            return self.manager.__exit__(*arguments)
        except (OSError, ValueError, KeyError, TypeError) as error:
            if self.publication["started"]:
                error.command_id = self.publication["command_id"]
                error.owner_request_published = self.publication["published"]
                raise
            raise OwnerCommandNotPublished("public_owner_admission_lock_incomplete") from None


def _capacity_owner_snapshot(application, epoch, generation, execution_index, *, uncertain=False):
    """Owner-authored side metadata only; it never substitutes for native gates."""
    empty = getattr(application, "_capacity_document_empty", None)
    return {
        "scheme": "designer-owner-capacity-admission-v1",
        "epoch": epoch,
        "execution_index": execution_index,
        "project": application.project,
        "document_empty_observed": empty if type(empty) is bool and not uncertain else None,
        "generation": {
            name: generation[name] for name in ("pid", "birth_filetime_100ns", "executable")
        },
    }


def capacity_contract():
    """Static resource policy, not session evidence or permission to execute."""
    return {
        "scheme": CAPACITY_SCHEME,
        "maximum_commands": MAX_PUBLIC_COMMANDS,
        "maximum_bytes": MAX_PUBLIC_BYTES,
        "command_record_bytes": MAX_COMMAND_RECORD_BYTES,
        "command_slot_worst_case_bytes": MAX_COMMAND_SLOT_BYTES,
        "method_slot_worst_case_bytes": {"export_pdf": MAX_PDF_COMMAND_SLOT_BYTES},
        "pdf_producer_records": {
            "method": "export_pdf",
            "metadata_records": len(PDF_PRODUCER_JSON),
            "metadata_record_bytes": MAX_COMMAND_RECORD_BYTES,
            "log_records": len(PDF_PRODUCER_LOGS),
            "log_bytes_each": MAX_PDF_PRODUCER_LOG_BYTES,
            "completed_records": "exact method/command/epoch MAC metadata and log digests",
        },
        "reserves": {
            "verification": VERIFICATION_COMMAND_RESERVE,
            "closed_checkpoint": CHECKPOINT_COMMAND_RESERVE,
            "empty_quit": QUIT_COMMAND_RESERVE,
        },
        "ordinary_command_slot_ceiling": MAX_PUBLIC_COMMANDS
        - VERIFICATION_COMMAND_RESERVE
        - CHECKPOINT_COMMAND_RESERVE
        - QUIT_COMMAND_RESERVE,
        "capacity_is_authorization": False,
        "byte_limit_can_reduce_available_slots": True,
        "on_ordinary_exhaustion": "complete the original write verification; use a fresh "
        "explicit leave-closed checkpoint, then normally Quit the exact owned empty "
        "generation; start a new dedicated session and use the original successful "
        "checkpoint to reopen. Never reset state, remove command records or replay writes.",
    }


class _BoundaryTrace:
    """STA-only fixed private file; instrumented boundaries, not all COM calls."""

    def __init__(self, root):
        self.thread, self.ident = threading.current_thread(), threading.get_ident()
        self.stream = (L._plain_path(root) / "native-boundary.ndjson").open("xb")
        self.bytes = self.events = 0
        self.failed = False
        self.closed = self.close_attempted = False
        self.command_id = None
        self.last_step = self.last_event = self.attempted_step = None
        self.started = time.monotonic()

    def _owner(self):
        if threading.current_thread() is not self.thread or threading.get_ident() != self.ident:
            raise OwnerError("native_boundary_original_owner_thread_required")

    def select_command(self, command_id):
        self._owner()
        if command_id is not None and (
            not isinstance(command_id, str)
            or not re.fullmatch(r"owner-command-[a-f0-9]{32}", command_id)
        ):
            raise OwnerError("native_boundary_command_binding_invalid")
        self.command_id = command_id

    @staticmethod
    def _safe_fields(fields):
        safe = {}
        for key, value in fields.items():
            if key == "result":
                if value is None or type(value) in (bool, int, float):
                    safe[key] = value
                elif type(value) is dict and set(value) <= {"type", "count", "length", "sha256"}:
                    item = {}
                    for name, scalar in value.items():
                        if name in {"count", "length"} and type(scalar) is int and scalar >= 0:
                            item[name] = scalar
                        elif (
                            name == "sha256"
                            and isinstance(scalar, str)
                            and re.fullmatch(r"[a-f0-9]{64}", scalar)
                        ):
                            item[name] = scalar
                        elif (
                            name == "type"
                            and isinstance(scalar, str)
                            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", scalar)
                        ):
                            item[name] = scalar
                        else:
                            raise OwnerError("native_boundary_field_contract_invalid")
                    safe[key] = item
                else:
                    raise OwnerError("native_boundary_field_contract_invalid")
            elif key == "error" and type(value) is dict:
                name, code, hr = value.get("type"), value.get("code"), value.get("hresult")
                safe[key] = {
                    "type": name
                    if isinstance(name, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name)
                    else "ExternalError",
                    "code": code
                    if isinstance(code, str) and re.fullmatch(r"E_[A-Z0-9_]{1,63}", code)
                    else None,
                    "hresult": hr if type(hr) is int else None,
                }
            elif (
                key == "wrapper"
                and type(value) is dict
                and set(value)
                == {
                    "module",
                    "class",
                    "new_project_signature_observed",
                    "new_project_declaration_observation",
                }
            ):
                if (
                    any(
                        not isinstance(value[name], str)
                        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,180}", value[name])
                        for name in ("module", "class")
                    )
                    or type(value["new_project_signature_observed"]) is not bool
                    or value["new_project_declaration_observation"]
                    not in {"observed_matching", "observed_incompatible", "unobserved"}
                ):
                    raise OwnerError("native_boundary_field_contract_invalid")
                safe[key] = dict(value)
            elif key == "declaration_basis" and value in {
                "actual_generated_wrapper_code",
                "current_role_151_and_native_member_251",
            }:
                safe[key] = value
            elif key in {"verified", "canonical_stable", "begin_missing"} and (
                value is None or type(value) is bool
            ):
                safe[key] = value
            elif key == "declaration" and value == {
                "dispid": 251,
                "invoke_kind": 1,
                "return_vartype": 11,
                "argument_vartypes": [8, 8, 8, 8],
            }:
                safe[key] = value
            elif key == "last_error":
                from .designer_owner_dispatch import validate_last_error_summary

                safe[key] = validate_last_error_summary(value)
            elif (
                key == "lifecycle"
                and type(value) is dict
                and set(value)
                == {
                    "save_call_attempted",
                    "save_call_returned",
                    "save_persistence_verified",
                    "close_call_attempted",
                    "close_call_returned",
                    "close_result_bool",
                    "post_close_empty_observed",
                }
                and all(item is None or type(item) is bool for item in value.values())
            ):
                safe[key] = dict(value)
            else:
                raise OwnerError("native_boundary_field_contract_invalid")
        return safe

    def retain_last_error_text(self, text, *, operation="new_project"):
        """Raw external text stays only in this fixed private, CREATE_NEW file."""
        self._owner()
        if (
            operation not in {"new_project", "open_project"}
            or self.command_id is None
            or not isinstance(text, str)
            or len(text) > 8192
        ):
            raise OwnerError("native_last_error_retention_contract_invalid")
        encoded = text.encode("utf-8")
        if len(encoded) > 8192:
            raise OwnerError("native_last_error_text_byte_limit")
        payload = json.dumps(
            {
                "owner_command_id": self.command_id,
                "operation": operation,
                "text": text,
                "_untrusted": ["text"],
                "meaning": "server_last_error_observation_not_causal_attribution",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > 65536:
            raise OwnerError("native_last_error_retention_byte_limit")
        filename = (
            "native-new-project-last-error.json"
            if operation == "new_project"
            else "native-open-project-last-error.json"
        )
        path = Path(self.stream.name).with_name(filename)
        with path.open("xb") as stream:
            if stream.write(payload) != len(payload):
                raise OwnerError("native_last_error_retention_short_write")
            stream.flush()
            os.fsync(stream.fileno())

    def emit(self, step, event, **fields):
        self._owner()
        if self.failed or self.closed or self.close_attempted:
            raise OwnerError("native_boundary_trace_incomplete")
        if (
            not isinstance(step, str)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]{0,99}", step)
            or event not in {"begin", "end", "error", "observed"}
        ):
            raise OwnerError("native_boundary_stage_contract_invalid")
        self.attempted_step = step
        try:
            row = {
                "sequence": self.events + 1,
                "owner_command_id": self.command_id,
                "step": step,
                "event": event,
                "at": datetime.now(UTC).isoformat(),
                "elapsed_ms": round((time.monotonic() - self.started) * 1000, 3),
                **self._safe_fields(fields),
            }
            raw = (json.dumps(row, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
            if len(raw) > 4096 or self.bytes + len(raw) > MAX_NATIVE_BOUNDARY_BYTES:
                raise OwnerError("native_boundary_trace_byte_limit")
            written = self.stream.write(raw)
            if written != len(raw):
                raise OwnerError("native_boundary_trace_short_write")
            self.stream.flush()
            os.fsync(self.stream.fileno())
            self.bytes += len(raw)
            self.events += 1
            self.last_step, self.last_event = step, event
        except Exception:
            self.failed = True
            raise

    def snapshot(self):
        self._owner()
        return {
            "bytes_written": None if self.failed else self.bytes,
            "events_written": None if self.failed else self.events,
            "verified_flushed_prefix_bytes": self.bytes,
            "verified_flushed_prefix_events": self.events,
            "incomplete": self.failed,
            "last_persisted_step": self.last_step,
            "last_persisted_event": self.last_event,
            "last_attempted_step": self.attempted_step,
            "closed": None if self.close_attempted and not self.closed else self.closed,
        }

    def require_complete(self):
        self._owner()
        if self.failed:
            raise OwnerError("native_boundary_trace_incomplete")

    def close(self):
        self._owner()
        if self.closed:
            return
        if self.close_attempted:
            raise OwnerError("native_boundary_close_outcome_unknown")
        self.close_attempted = True
        try:
            self.stream.close()
            self.closed = True
        except Exception:
            self.failed = True
            raise


@dataclass(frozen=True, repr=False)
class OwnerTicket:
    root: Path = field(repr=False)
    epoch: str = field(repr=False)


def native_owner_enabled():
    return False


def _failure_facts(error, stage, dispatched):
    from xpedition_cli.designer_native_launch import LaunchError
    from xpedition_cli.designer_owner_dispatch import GRANT_CALLPOINTS, DispatchError

    facts = {"stage": stage, "dispatched": dispatched}
    if isinstance(error, (OwnerError, TransportError, LaunchError, DispatchError)):
        reason = str(error)
        facts["reason"] = (
            reason if re.fullmatch(r"[a-z][a-z0-9_]{0,120}", reason) else "owned_component_failure"
        )
    else:
        facts["reason"] = "external_native_failure"
    name = type(error).__name__
    facts["error_type"] = (
        name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name) else "ExternalError"
    )
    for key in ("hresult", "errno", "winerror"):
        value = getattr(error, key, None)
        facts[key] = value if type(value) is int else None
    callpoint = getattr(error, "snapshot_callpoint", None)
    facts["snapshot_callpoint"] = (
        callpoint if isinstance(callpoint, str) and callpoint in SNAPSHOT_CALLPOINTS else None
    )
    grant_point = getattr(error, "grant_callpoint", None)
    facts["grant_callpoint"] = (
        grant_point if isinstance(grant_point, str) and grant_point in GRANT_CALLPOINTS else None
    )
    returned = getattr(error, "native_call_returned", None)
    facts["native_call_returned"] = returned if type(returned) is bool else None
    try:
        facts["native_result_summary"] = (
            _BoundaryTrace._safe_fields({"result": getattr(error, "native_result_summary", None)})[
                "result"
            ]
            if returned is True
            else None
        )
    except OwnerError:
        facts["native_result_summary"] = None
    if isinstance(error, LaunchError):
        for key, allowed in (
            ("component_stage", LAUNCH_COMPONENT_STAGES),
            ("component_reason", LAUNCH_COMPONENT_REASONS),
        ):
            value = getattr(error, key, None)
            facts[key] = (
                value
                if isinstance(value, str)
                and value.isascii()
                and re.fullmatch(r"[a-z][a-z0-9_.]{0,120}", value)
                and value in allowed
                else None
            )
    return facts


def _native_edit_failure_details(error):
    """Bound native batch evidence; an omitted observation is never complete."""
    from . import native_com_adapter as A

    if type(error) is not A.AdapterError or type(error.details) is not dict:
        return {}
    result, omitted, used = {}, {}, 0
    if type(error.details.get("applied")) is list:
        result["applied_count"] = len(error.details["applied"])
    core = (
        "write_attempted",
        "saved",
        "save_acknowledged",
        "persistence_verified",
        "stage",
        "orphan_placed",
        "orphan_library",
        "orphan_device",
        "orphan_symbol",
        "orphan_x",
        "orphan_y",
        "requested_refdes",
        "requested_position",
        "applied",
        "sheets_saved",
        "lifecycle",
        "protection",
        "edit_protection",
        "verification",
    )
    for name in (*core, "orphan_actual", "observed", "read_back_after_reopen"):
        if name not in error.details:
            continue
        if name == "stage" and "display_failure" in error.details:
            checked = _native_display_failure_details(error)
            if checked.get("native_display_failure_details_complete") is not True:
                continue
        try:
            value = (
                checked["display_failure"]["stage"]
                if name == "stage" and "display_failure" in error.details
                else error.details[name]
            )
            raw = json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError):
            omitted[name] = {"status": "unserializable", "observation_complete": False}
            continue
        limit = MAX_COMMAND_RECORD_BYTES // 2
        if name not in core:
            limit = min(limit, used + 128 * 1024)
        if used + len(raw) > limit:
            omitted[name] = {
                "status": "omitted",
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "observation_complete": False,
            }
            continue
        result[name] = copy.deepcopy(value)
        used += len(raw)
    if result or omitted:
        result["native_edit_failure_details_complete"] = not omitted
    if omitted:
        result["omitted_native_edit_details"] = omitted
    return result


def _native_display_failure_details(error):
    """Only fixed display stages and observed control-flow facts leave the owner."""
    from . import native_com_adapter as A

    if type(error) is not A.AdapterError or type(error.details) is not dict:
        return {}
    if "display_failure" not in error.details:
        return {}
    value = error.details["display_failure"]
    preflight = {
        "display_owner_preflight",
        "display_snapshot_preflight",
        "display_identity_preflight",
        "display_model_preflight",
        "display_metadata_preflight",
        "display_protection_preflight",
        "display_target_preflight",
    }
    applying = {
        "display_apply",
        "display_before_save_observation",
        "display_before_save_verification",
    }
    persistence = {
        "display_save_close_reopen",
        "display_reopened_identity",
        "display_reopened_observation",
        "display_reopened_verification",
    }
    setter = {
        "display_setter_fields_attempted": None,
        "display_setter_fields_observed": False,
        "display_setter_last_attempted_callpoint": None,
    }
    invalid = {"native_display_failure_details_complete": False, **setter}
    if type(value) is not dict or set(value) != {
        "stage",
        "reason",
        "operations_requested",
        "operations_returned",
        "operation_index",
        "operation_call_attempted",
    }:
        return invalid
    stage, reason = value["stage"], value["reason"]
    if (
        type(stage) is not str
        or type(reason) is not str
        or not (
            (stage in preflight and reason == "display_preflight_refused")
            or (stage in applying and reason == "display_apply_or_verification_failed")
            or (stage in persistence and reason == "display_persistence_incomplete")
        )
    ):
        return invalid
    requested, returned, index, attempted = (
        value[name]
        for name in (
            "operations_requested",
            "operations_returned",
            "operation_index",
            "operation_call_attempted",
        )
    )
    if (
        type(requested) is not int
        or not 1 <= requested <= 4096
        or type(returned) is not int
        or not 0 <= returned <= requested
        or (index is not None and (type(index) is not int or not 0 <= index < requested))
        or type(attempted) is not bool
        or (stage in preflight and (returned != 0 or index is not None or attempted))
    ):
        return invalid
    attempted_fields = error.details.get("attempted_fields")
    prefix = ["Value", "SetLocation", "Visible", "Size"]
    if (
        stage == "display_apply"
        and attempted is True
        and index is not None
        and type(attempted_fields) is list
        and 1 <= len(attempted_fields) <= 4
        and all(type(field) is str for field in attempted_fields)
        and attempted_fields == prefix[: len(attempted_fields)]
    ):
        setter = {
            "display_setter_fields_attempted": list(attempted_fields),
            "display_setter_fields_observed": True,
            "display_setter_last_attempted_callpoint": attempted_fields[-1],
        }
    return {
        "native_display_failure_details_complete": True,
        "display_failure": copy.deepcopy(value),
        **setter,
    }


def _native_connection_failure_details(error):
    """Only fixed call points, numeric native codes and bounded progress escape.

    EXCEPINFO source/description can contain paths or arbitrary server text.
    Preserve its presence, not those strings. This evidence never changes the
    original dispatch/unknown classification or grants a retry.
    """
    from . import native_com_adapter as A

    if type(error) is not A.AdapterError or type(error.details) is not dict:
        return {}
    action = error.details.get("action")
    if action not in {
        "find_component_pin",
        "pin_location",
        "add_schematic_net",
        "label_schematic_net",
    }:
        return {}
    result = {"action": action}
    native = error.details.get("native")
    if type(native) is dict:
        clean = {
            key: native.get(key)
            if type(native.get(key)) is int and -(2**31) <= native[key] < 2**32
            else None
            for key in ("hresult", "product_code", "scode")
        }
        for key in ("hresult", "scode"):
            clean[key + "_hex"] = (
                f"0x{clean[key] & 0xFFFFFFFF:08X}" if clean[key] is not None else None
            )
        clean.update(
            source=None,
            source_present=native.get("source") is not None,
            source_redacted=native.get("source") is not None,
        )
        result["native"] = clean
    progress = error.details.get("schematic_connect_progress")
    if type(progress) is dict:
        phase = progress.get("phase")
        clean_progress = {"phase": phase}
        if phase in {"wire_constant", "first_pin_location", "second_pin_location", "add_net"}:
            counts = (
                "segments_planned",
                "segments_attempted",
                "segments_returned",
                "last_segment_index",
            )
            for key in counts:
                value = progress.get(key)
                if value is not None and (type(value) is not int or not 0 <= value <= 66):
                    return {**result, "schematic_connect_progress_observed": False}
                clean_progress[key] = value
            key = "last_returned_nonnull"
            if progress.get(key) is not None and type(progress[key]) is not bool:
                return {**result, "schematic_connect_progress_observed": False}
            clean_progress[key] = progress.get(key)
        elif phase in {"label_segment", "add_label"}:
            for key in (
                "segment_observed",
                "label_attempted",
                "label_returned",
                "label_returned_nonnull",
            ):
                if progress.get(key) is not None and type(progress[key]) is not bool:
                    return {**result, "schematic_connect_progress_observed": False}
                clean_progress[key] = progress.get(key)
        else:
            return result
        result["schematic_connect_progress"] = clean_progress
    return result


def _adapter_failure(error):
    code = getattr(error, "code", None)
    if not isinstance(code, str) or not re.fullmatch(r"E_[A-Z0-9_]{1,63}", code):
        code = "E_CONFLICT" if isinstance(error, ValueError) else "E_OUTCOME_UNKNOWN"
    reason = str(error) if type(error).__name__ == "DispatchError" else None
    if not isinstance(reason, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,120}", reason):
        reason = "owner_adapter_call_incomplete"
    started = getattr(error, "dispatch_started", None)
    details = {
        "reason": reason,
        "native_request_dispatched": started,
        "native_call_unknown": started is not False,
        "automatic_replay": False,
        "native_call_returned": getattr(error, "native_call_returned", None)
        if type(getattr(error, "native_call_returned", None)) is bool
        else None,
    }
    if getattr(error, "native_boundary_trace_incomplete", False) is True or (
        type(error) is OwnerError and str(error) == "native_boundary_trace_incomplete"
    ):
        # The owner command was received, and diagnostic failure cannot turn
        # its native entries into "Nothing was sent" or a state-drift claim.
        code = "E_OUTCOME_UNKNOWN"
        details.update(
            reason="native_boundary_trace_incomplete",
            owner_command_received=True,
            native_request_dispatched=True,
            native_call_unknown=True,
            diagnostic_incomplete=True,
        )
        for name in (
            "engineering_write_attempted",
            "fixed_document_member_attempted",
            "fixed_document_cleanup_observed",
        ):
            value = getattr(error, name, None)
            details[name] = value if type(value) is bool else None
        point = getattr(error, "fixed_document_failed_callpoint", None)
        if isinstance(point, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]{0,99}", point):
            details["metadata_api_point"] = point
            details["native_metadata_call_entered"] = (
                True if details["native_call_returned"] is True else None
            )
        summary = getattr(error, "native_result_summary", None)
        try:
            cleaned = _BoundaryTrace._safe_fields({"result": summary})["result"]
            json.dumps(cleaned, allow_nan=False)
        except (TypeError, ValueError):
            cleaned = None
        details["native_result_summary"] = cleaned
    if started is False:
        details["write_attempted"] = False
    from .designer_owner_dispatch import first_open_failure_observation_report

    first_open_report = first_open_failure_observation_report(error)
    if first_open_report is not None:
        details["first_open_observation_report"] = first_open_report
    for key in ("hresult", "winerror", "errno"):
        value = getattr(error, key, None)
        details[key] = value if type(value) is int else None
    external_details = getattr(error, "details", None)
    if type(external_details) is dict:
        fixed_reason = external_details.get("reason")
        display_details = _native_display_failure_details(error)
        if "display_failure" in external_details:
            if display_details.get("native_display_failure_details_complete") is True:
                details["reason"] = display_details["display_failure"]["reason"]
        elif isinstance(fixed_reason, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,120}", fixed_reason):
            details["reason"] = fixed_reason
    if type(external_details) is dict and external_details.get("open_acknowledgement") is False:
        details["open_acknowledgement"] = False
    details.update(_native_edit_failure_details(error))
    details.update(_native_display_failure_details(error))
    details.update(_native_connection_failure_details(error))
    for diagnostic_key in ("new_project_last_error", "open_project_last_error"):
        if type(external_details) is not dict or diagnostic_key not in external_details:
            continue
        from .designer_owner_dispatch import validate_last_error_summary

        try:
            details[diagnostic_key] = validate_last_error_summary(external_details[diagnostic_key])
        except ValueError:
            details[diagnostic_key] = None
    return {
        "code": code,
        "message": "Dedicated owner adapter operation did not complete",
        "details": details,
    }


def _source_binding():
    if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
        from .designer_frozen_binding import frozen_source_binding

        return frozen_source_binding()
    values = {}
    for name in (
        "designer_native_owner.py",
        "designer_owner_transport.py",
        "designer_native_launch.py",
        "designer_startup.py",
        "designer_host_bootstrap.py",
        "startup_input_seal.py",
        "execution_lifecycle.py",
        "designer_owner_dispatch.py",
        "native_com_adapter.py",
        "designer_source_binding.py",
        "designer_first_open.py",
        "schematic_edit_protection.py",
        "designer_dual_invoke.py",
    ):
        path = Path(__file__).with_name(name)
        if not path.is_file():
            raise OwnerError("dedicated_native_launcher_not_integrated")
        raw = path.read_bytes()
        if len(raw) > 1024 * 1024:
            raise OwnerError("owner_source_byte_limit")
        values[name] = hashlib.sha256(raw).hexdigest()
    return values


def _context(ticket):
    if type(ticket) is not OwnerTicket or not re.fullmatch(r"[a-f0-9]{32}", ticket.epoch):
        raise OwnerError("new_native_owner_ticket_required")
    root = L._plain_path(ticket.root)
    key_path = L._plain_path(root / "integrity-key.bin")
    with key_path.open("rb") as stream:
        key = stream.read(33)
    if len(key) != 32:
        raise OwnerError("owner_key_incomplete")
    intent, _ = L._read(root / "intent.json", key)
    if (
        intent.get("component") != COMPONENT
        or intent.get("execution_epoch") != ticket.epoch
        or intent.get("locator_sha256") != L._locator(root)
        or not re.fullmatch(r"designer-owner-[a-f0-9]{32}", str(intent.get("execution_id")))
    ):
        raise OwnerError("dedicated_owner_binding_invalid")
    return root, key, intent


def _budget(value):
    if type(value) not in (int, float) or not 0 <= value <= MAX_OBSERVER_SECONDS:
        raise OwnerError("bounded_owner_observation_required")
    return float(value)


def _launch_evidence(root, key, expected, lease):
    """Retain exact new-launch facts privately; expose only their binding."""
    inputs = getattr(lease, "inputs", None)
    private = {
        "expected_native_generation": {
            "pid": expected.pid,
            "birth_filetime_100ns": expected.birth_100ns,
            "executable": expected.executable,
        },
        "startup_provenance": lease.startup_provenance,
        "startup_seed": lease.startup_provenance,
        "startup_provenance_kind": "captured_startup_seed_not_validated_public_provenance",
        "startup_input_protection": inputs.private_binding() if inputs is not None else None,
        "startup_working_directory": lease.private_work_binding()
        if callable(getattr(lease, "private_work_binding", None))
        else None,
        "authorized_project": None
        if getattr(lease, "project_path", None) is None
        else str(lease.project_path),
    }
    raw = L._pack(private, key)
    L._write_new(root / "native-launch-evidence.json", raw)
    return {
        "pid": expected.pid,
        "birth_filetime_100ns": expected.birth_100ns,
        "executable_sha256": hashlib.sha256(expected.executable.encode("utf-8")).hexdigest(),
        "private_launch_evidence_sha256": hashlib.sha256(raw).hexdigest(),
    }


def launch_measurement_owner(root, plan, *, observation_seconds=0.5):
    return _launch_owner(root, plan, observation_seconds=observation_seconds)


def launch_public_owner(root, plan, editing_scope, *, observation_seconds=0.5):
    """Create a fresh dedicated empty owner; the scope is re-read server-side."""
    from xpedition_cli.designer_native_launch import SCHEME
    from xpedition_cli.designer_owner_dispatch import validate_editing_scope

    if type(plan) is not dict or plan.get("scheme") != SCHEME:
        raise OwnerError("public_owner_requires_new_empty_startup")
    validate_editing_scope(editing_scope).require(effects=["session"])
    return _launch_owner(
        root, plan, observation_seconds=observation_seconds, editing_scope=editing_scope
    )


def _launch_owner(root, plan, *, observation_seconds=0.5, editing_scope=None):
    """Start one reviewed owner child, which alone calls the fixed CAD launcher.

    This private-measurement API grants no engineering-write authority. The
    public native gate remains false, regardless of the supplied plan contents.
    """
    budget = _budget(observation_seconds)
    frozen = bool(getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"))
    root = L._plain_path(root)
    if root.exists() or not root.parent.is_dir():
        raise OwnerError("new_dedicated_runtime_root_required")
    # Validate the launch plan before creating any owner/runtime. The launcher
    # owns this schema; arbitrary executable commands/factories are unsupported.
    from xpedition_cli.designer_native_launch import constrain_plan_to_owner

    plan = constrain_plan_to_owner(plan, root)
    source = _source_binding()
    L._private_mkdir(root)
    key = secrets.token_bytes(32)
    L._write_new(root / "integrity-key.bin", key)
    epoch = secrets.token_hex(16)
    intent = {
        "component": COMPONENT,
        "execution_id": "designer-owner-" + secrets.token_hex(16),
        "execution_epoch": epoch,
        "locator_sha256": L._locator(root),
        "source_sha256": source,
        "plan": plan,
    }
    if editing_scope is not None:
        intent.update(mode=PUBLIC_MODE, editing_scope=dict(editing_scope))
    L._write_new(root / "intent.json", L._pack(intent, key))
    L._private_mkdir(root / "events")
    L._private_mkdir(root / "commands")
    ticket = OwnerTicket(root, epoch)
    arguments = (
        [sys.executable, "--internal-designer-owner"]
        if frozen
        else [sys.executable, "-X", "utf8", "-B", str(Path(__file__).resolve()), "--owned-child"]
    ) + ["--root", str(root), "--epoch", epoch]
    # Preserve the caller's existing legal vendor licensing environment. Remove
    # interpreter/old CLI selectors; the fixed launcher supplies SDK and WDIR.
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.upper().startswith(("PYTHON", "XPEDITION_"))
    }
    environment.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    if frozen:
        # A fresh top-level onedir instance owns stable collected files after
        # the caller exits. Never reuse onefile's temporary extraction lifetime.
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    with (
        (root / "owner-stdout.log").open("xb") as out,
        (root / "owner-stderr.log").open("xb") as err,
    ):
        process = L._DetachedChild(
            arguments,
            cwd=str(root),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    try:
        identity = L._identity(int(process._handle))
        if identity["alive"] is not True:
            raise OwnerError("owner_child_exited_before_registration")
        L._write_new(root / "owner.json", L._pack({"epoch": epoch, "owner": identity}, key))
    finally:
        process.close_parent_handle()
    return {"ticket": ticket, "observation": observe_owner(ticket, timeout_seconds=budget)}


def _registration(root, key, intent):
    registration, _ = L._read(root / "owner.json", key)
    if registration.get("epoch") != intent["execution_epoch"]:
        raise OwnerError("owner_registration_epoch_mismatch")
    return registration["owner"]


def observe_owner(ticket, *, timeout_seconds=0):
    deadline = time.monotonic() + _budget(timeout_seconds)
    result = {
        "status": "unknown",
        "native_writer_admitted": False,
        "owner_state": "unobservable",
        "cleanup_verified": None,
        "connection_observation": "unobserved",
        "phase": "unobserved",
    }
    while True:
        try:
            root, key, intent = _context(ticket)
            expected = _registration(root, key, intent)
            values = L._events(root, key, intent)
            owner_state, _ = L._fresh_owner(expected)
            # A partial concurrent event read is a past observation, not a
            # permanent reason on the next complete synchronized snapshot.
            result.pop("reason", None)
            result.update(owner_state=owner_state, event_count=len(values))
            if intent.get("mode") == PUBLIC_MODE:
                result["command_capacity"] = observe_owner_capacity(ticket)
            if values:
                result["phase"] = values[-1]["phase"]
                result["latest_observation"] = values[-1]["details"]
                ready = any(value["phase"] == "native_connection_observed" for value in values)
                result["connection_observation"] = (
                    "session_specific_native" if ready else "incomplete"
                )
                if (
                    values[-1]["phase"] == "owner_cleanup_complete"
                    and owner_state == "verified_exited"
                ):
                    result.update(status="completed", cleanup_verified=True)
                elif values[-1]["phase"] == "owner_cleanup_failed":
                    result["cleanup_verified"] = False
            if values or time.monotonic() >= deadline:
                return result
        except (OSError, ValueError, KeyError, TypeError):
            result["reason"] = "owner_observation_incomplete"
            if time.monotonic() >= deadline:
                return result
        time.sleep(min(0.02, max(0, deadline - time.monotonic())))


def submit_owner_command(ticket, command, *, observation_seconds=0.25):
    budget = _budget(observation_seconds)
    if command not in COMMANDS:
        raise OwnerError("fixed_owner_command_required")
    root, key, intent = _context(ticket)
    if intent.get("mode") == PUBLIC_MODE:
        raise OwnerError("public_owner_requires_confirmed_adapter_command")
    if intent["source_sha256"] != _source_binding():
        raise OwnerError("owner_command_source_binding_changed")
    expected = _registration(root, key, intent)
    if L._fresh_owner(expected)[0] != "verified_alive":
        raise OwnerError("live_original_owner_required")
    with L._launch_lock(root):
        directories = list((root / "commands").iterdir())
        if len(directories) >= MAX_COMMANDS:
            raise OwnerError("owner_command_limit")
        command_id = "owner-command-" + secrets.token_hex(16)
        directory = root / "commands" / command_id
        L._private_mkdir(directory)
        request = {"command_id": command_id, "epoch": ticket.epoch, "command": command}
        # The immutable unknown exists before request publication; a timeout
        # cannot turn a dispatched write into an automatic replay.
        L._write_new(
            directory / "initial.json",
            L._pack(
                {
                    "command_id": command_id,
                    "epoch": ticket.epoch,
                    "original_outcome": "unknown",
                    "immutable": True,
                },
                key,
            ),
        )
        L._write_new(directory / "request.json", L._pack(request, key))
    return observe_owner_command(ticket, command_id, timeout_seconds=budget)


def _command_write(path, payload, key):
    raw = L._pack(payload, key)
    if len(raw) > MAX_COMMAND_RECORD_BYTES:
        raise OwnerError("public_owner_command_record_limit")
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return hashlib.sha256(raw).hexdigest()


def _command_read(path, key):
    L._plain_path(path)
    if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
        raise OwnerError("public_owner_command_reparse_refused")
    with path.open("rb") as stream:
        raw = stream.read(MAX_COMMAND_RECORD_BYTES + 1)
    if len(raw) > MAX_COMMAND_RECORD_BYTES:
        raise OwnerError("public_owner_command_record_limit")
    try:
        packet = json.loads(raw)
        if set(packet) != {"payload", "mac"} or type(packet["payload"]) is not dict:
            raise ValueError
        mac = hmac.new(key, L._canonical(packet["payload"]), hashlib.sha256).hexdigest()
        if not isinstance(packet["mac"], str) or not hmac.compare_digest(packet["mac"], mac):
            raise ValueError
        return packet["payload"], hashlib.sha256(raw).hexdigest()
    except (ValueError, TypeError, KeyError):
        raise OwnerError("public_owner_command_integrity_incomplete") from None


class _CommandJournal:
    """One bounded append-only chain per public command, never a mutable result."""

    def __init__(self, directory, key, request):
        self.directory, self.key, self.request = directory, key, request
        self.sequence, self.previous = 0, None

    def append(self, phase, **details):
        if (
            phase
            not in {"command_received", "command_started", "command_returned", "command_failed"}
            or self.sequence >= 3
        ):
            raise OwnerError("fixed_public_command_phase_required")
        sequence = self.sequence + 1
        payload = {
            "command_id": self.request["command_id"],
            "epoch": self.request["epoch"],
            "sdk_operation_id": self.request["sdk_operation_id"],
            "request_sha256": hashlib.sha256(L._canonical(self.request)).hexdigest(),
            "sequence": sequence,
            "previous_sha256": self.previous,
            "phase": phase,
            "details": {"command_id": self.request["command_id"], **details},
        }
        digest = _command_write(
            self.directory / f"observation-{sequence:02d}.json", payload, self.key
        )
        self.sequence, self.previous = sequence, digest


def _public_command_observations(directory, key, request):
    result, previous = [], None
    for index in range(1, 4):
        path = directory / f"observation-{index:02d}.json"
        if not path.exists():
            break
        event, digest = _command_read(path, key)
        if (
            event.get("command_id") != request["command_id"]
            or event.get("epoch") != request["epoch"]
            or event.get("sdk_operation_id") != request["sdk_operation_id"]
            or event.get("request_sha256") != hashlib.sha256(L._canonical(request)).hexdigest()
            or event.get("sequence") != index
            or event.get("previous_sha256") != previous
            or event.get("phase")
            not in {"command_received", "command_started", "command_returned", "command_failed"}
        ):
            raise OwnerError("public_owner_command_chain_incomplete")
        result.append(event)
        previous = digest
    if result and (
        [e["phase"] for e in result[:2]]
        != ["command_received", "command_started"][: min(len(result), 2)]
        or (len(result) == 3 and result[2]["phase"] not in {"command_returned", "command_failed"})
    ):
        raise OwnerError("public_owner_command_chain_incomplete")
    return result


def _validate_adapter_request(request, command_id, epoch):
    from xpedition_cli.designer_owner_dispatch import METHODS

    if (
        type(request) is not dict
        or set(request) - {"capacity"}
        != {
            "command_id",
            "epoch",
            "command",
            "method",
            "params",
            "sdk_operation_id",
            "authorization_binding",
        }
        or request["command_id"] != command_id
        or request["epoch"] != epoch
        or request["command"] != "adapter"
        or request["method"] not in METHODS
        or type(request["params"]) is not dict
        or type(request["authorization_binding"]) is not dict
        or not isinstance(request["sdk_operation_id"], str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", request["sdk_operation_id"])
    ):
        raise OwnerError("public_owner_adapter_request_invalid")
    allocation = request.get("capacity")
    if "capacity" in request and (
        type(allocation) is not dict
        or set(allocation) != {"scheme", "pool", "new_claim"}
        or allocation.get("scheme") != CAPACITY_SCHEME
        or allocation.get("pool")
        not in {"ordinary", "verification", "closed_checkpoint", "empty_quit"}
        or type(allocation.get("new_claim")) is not bool
        or (allocation["pool"] == "ordinary" and allocation["new_claim"])
    ):
        raise OwnerError("public_owner_capacity_binding_invalid")


def _capacity_pools():
    return {
        "verification": VERIFICATION_COMMAND_RESERVE,
        "closed_checkpoint": CHECKPOINT_COMMAND_RESERVE,
        "empty_quit": QUIT_COMMAND_RESERVE,
    }


def _command_worst_case_bytes(method):
    return MAX_PDF_COMMAND_SLOT_BYTES if method == "export_pdf" else MAX_COMMAND_SLOT_BYTES


def _capacity_pdf_expected(request):
    """The source-bound PDF plan is private request evidence, not CAD authority."""
    expected = request["authorization_binding"].get("pdf_export")
    if (
        request["method"] != "export_pdf"
        or type(expected) is not dict
        or request["authorization_binding"].get("command") != "schematic export"
        or expected.get("generation_id") != request["sdk_operation_id"]
        or request["params"].get("artifact_generation") != request["sdk_operation_id"]
        or L._canonical(expected) != L._canonical(request["params"].get("_pdf_expectation"))
    ):
        raise OwnerError("public_owner_pdf_records_invalid")
    producer = expected.get("producer")
    if (
        type(producer) is not dict
        or not isinstance(producer.get("path"), str)
        or not ntpath.isabs(producer["path"])
        or type(producer.get("bytes")) is not int
        or not 0 <= producer["bytes"] <= 128 * 1024 * 1024
        or not isinstance(producer.get("sha256"), str)
        or re.fullmatch(r"[a-f0-9]{64}", producer["sha256"]) is None
    ):
        raise OwnerError("public_owner_pdf_records_invalid")
    return producer


def _capacity_pdf_bytes(path, maximum, before, *, log=False):
    """Bound the read and bind its opened file identity to the full census."""
    if before[2] > maximum:
        raise OwnerError(
            "public_owner_pdf_log_limit" if log else "public_owner_pdf_records_invalid"
        )
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(maximum + 1)
        ended = os.fstat(stream.fileno())
    identities = [
        (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns) for item in (opened, ended)
    ]
    if any(identity != before for identity in identities):
        raise OwnerError("public_owner_pdf_record_unstable")
    if len(raw) > maximum:
        raise OwnerError(
            "public_owner_pdf_log_limit" if log else "public_owner_pdf_records_invalid"
        )
    return raw


def _capacity_pdf_record(directory, name, key, before):
    path = directory / name
    raw = _capacity_pdf_bytes(path, MAX_COMMAND_RECORD_BYTES, before[name])
    try:
        value, digest = _command_read(path, key)
    except (ValueError, TypeError, KeyError, RecursionError):
        raise OwnerError("public_owner_pdf_records_invalid") from None
    if digest != hashlib.sha256(raw).hexdigest():
        raise OwnerError("public_owner_pdf_record_unstable")
    return value


def _capacity_pdf_records(directory, key, request, request_sha256, before, events):
    """Validate only the five fixed records produced by this owned PDF method."""
    names = set(before) & (PDF_PRODUCER_JSON | set(PDF_PRODUCER_LOGS))
    if not names:
        if (
            len(events) == 3
            and events[-1]["phase"] == "command_returned"
            and events[-1]["details"].get("result", {}).get("exported") is True
        ):
            raise OwnerError("public_owner_pdf_records_invalid")
        return False
    producer = _capacity_pdf_expected(request)
    ordered = [
        *PDF_PRODUCER_LOGS,
        "pdf-producer-initial.json",
        "pdf-producer-launched.json",
        "pdf-producer-returned.json",
    ]
    if (
        names not in [set(ordered[:count]) for count in range(1, len(ordered) + 1)]
        or len(events) < 2
    ):
        raise OwnerError("public_owner_pdf_records_invalid")
    for name in PDF_PRODUCER_LOGS:
        if name in before and before[name][2] > MAX_PDF_PRODUCER_LOG_BYTES:
            raise OwnerError("public_owner_pdf_log_limit")
    if "pdf-producer-initial.json" not in names:
        return False  # The synchronous producer has not published its initial MAC yet.
    initial = _capacity_pdf_record(directory, "pdf-producer-initial.json", key, before)
    expected_initial = {
        "command_id": request["command_id"],
        "epoch": request["epoch"],
        "original_outcome": "unknown",
        "immutable": True,
        "expected_producer_path": ntpath.normcase(ntpath.normpath(producer["path"])),
        "request_sha256": request_sha256,
    }
    if initial != expected_initial:
        raise OwnerError("public_owner_pdf_records_invalid")
    if "pdf-producer-launched.json" not in names:
        return False
    launched = _capacity_pdf_record(directory, "pdf-producer-launched.json", key, before)
    process = launched.get("process")
    if (
        set(launched) != {"command_id", "epoch", "process", "automatic_replay"}
        or launched.get("command_id") != request["command_id"]
        or launched.get("epoch") != request["epoch"]
        or launched.get("automatic_replay") is not False
        or type(process) is not dict
        or set(process) != {"pid", "birth_filetime_100ns", "executable", "alive"}
        or type(process.get("pid")) is not int
        or not 0 < process["pid"] < 2**32
        or not isinstance(process.get("birth_filetime_100ns"), str)
        or not process["birth_filetime_100ns"].isascii()
        or not process["birth_filetime_100ns"].isdecimal()
        or int(process["birth_filetime_100ns"]) <= 0
        or type(process.get("alive")) is not bool
    ):
        raise OwnerError("public_owner_pdf_records_invalid")
    image = process.get("executable")
    if image is not None and (
        not isinstance(image, str)
        or not ntpath.isabs(image)
        or ntpath.normcase(ntpath.normpath(image))
        != ntpath.normcase(ntpath.normpath(producer["path"]))
    ):
        raise OwnerError("public_owner_pdf_records_invalid")
    if "pdf-producer-returned.json" not in names:
        if image is None:
            raise OwnerError("public_owner_pdf_records_invalid")
        return False
    returned = _capacity_pdf_record(directory, "pdf-producer-returned.json", key, before)
    if (
        set(returned)
        != {
            "command_id",
            "epoch",
            "process_returned",
            "exit_code",
            "resource_cleanup_complete",
            "stdout_bytes",
            "stderr_bytes",
            "stdout_sha256",
            "stderr_sha256",
        }
        or returned.get("command_id") != request["command_id"]
        or returned.get("epoch") != request["epoch"]
        or returned.get("process_returned") is not True
        or returned.get("resource_cleanup_complete") is not True
        or type(returned.get("exit_code")) is not int
        or not -(2**31) <= returned["exit_code"] < 2**32
        or (image is None and process["alive"] is not False)
    ):
        raise OwnerError("public_owner_pdf_records_invalid")
    for stream_name, file_name in zip(("stdout", "stderr"), PDF_PRODUCER_LOGS, strict=True):
        count, digest = returned.get(stream_name + "_bytes"), returned.get(stream_name + "_sha256")
        if (
            type(count) is not int
            or not 0 <= count <= MAX_PDF_PRODUCER_LOG_BYTES
            or not isinstance(digest, str)
            or re.fullmatch(r"[a-f0-9]{64}", digest) is None
        ):
            raise OwnerError("public_owner_pdf_records_invalid")
        raw = _capacity_pdf_bytes(
            directory / file_name, MAX_PDF_PRODUCER_LOG_BYTES, before[file_name], log=True
        )
        if count != len(raw) or digest != hashlib.sha256(raw).hexdigest():
            raise OwnerError("public_owner_pdf_records_invalid")
    return True  # Resource completion only. A null image remains unobserved.


def _capacity_inventory(root, key, epoch):
    """Read authenticated immutable commands and charge incomplete output in full."""
    requests, claims, allocated = [], {}, {pool: 0 for pool in _capacity_pools()}
    used = charged = 0
    directories = list((root / "commands").iterdir())
    if len(directories) > MAX_PUBLIC_COMMANDS:
        raise OwnerError("public_owner_capacity_incomplete")
    for directory in directories:
        L._plain_path(directory)
        files = list(directory.iterdir())
        ordinary_files = {
            "initial.json",
            "request.json",
            *(f"observation-{n:02d}.json" for n in (1, 2, 3)),
        }
        if len(files) > 10 or {p.name for p in files} - (
            ordinary_files | PDF_PRODUCER_JSON | set(PDF_PRODUCER_LOGS)
        ):
            raise OwnerError("public_owner_pdf_records_invalid")
        before = {}
        for path in files:
            L._plain_path(path)
            stat = path.lstat()
            if (
                not path.is_file()
                or path.is_symlink()
                or getattr(stat, "st_file_attributes", 0) & 0x400
            ):
                raise OwnerError("public_owner_command_reparse_refused")
            before[path.name] = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        request, request_sha256 = _command_read(directory / "request.json", key)
        _validate_adapter_request(request, directory.name, epoch)
        if request["method"] != "export_pdf" and (len(files) > 5 or set(before) - ordinary_files):
            raise OwnerError("public_owner_pdf_records_invalid")
        auxiliary_bytes = sum(
            value[2]
            for name, value in before.items()
            if name in {"initial.json", "observation-01.json", "observation-02.json"}
        )
        if auxiliary_bytes > MAX_COMMAND_AUX_BYTES:
            raise OwnerError(
                "public_owner_pdf_records_invalid"
                if request["method"] == "export_pdf"
                else "public_owner_capacity_incomplete"
            )
        initial, _ = _command_read(directory / "initial.json", key)
        if initial != {
            "command_id": directory.name,
            "epoch": epoch,
            "original_outcome": "unknown",
            "immutable": True,
        }:
            raise OwnerError("public_owner_capacity_incomplete")
        events = _public_command_observations(directory, key, request)
        for event in events[:2]:
            if event["details"] != {"command_id": directory.name, "method": request["method"]}:
                raise OwnerError("public_owner_capacity_incomplete")
        pdf_complete = (
            _capacity_pdf_records(directory, key, request, request_sha256, before, events)
            if request["method"] == "export_pdf"
            else True
        )
        after = {
            p.name: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
            for p in directory.iterdir()
            for s in (p.lstat(),)
        }
        if before != after:
            raise OwnerError(
                "public_owner_pdf_record_unstable"
                if request["method"] == "export_pdf"
                else "public_owner_capacity_incomplete"
            )
        actual = sum(row[2] for row in after.values())
        used += actual
        terminal = len(events) == 3
        charged += (
            actual
            if terminal and pdf_complete
            else max(actual, _command_worst_case_bytes(request["method"]))
        )
        request["_owner_admission"] = (
            events[-1]["details"].get("owner_admission") if terminal else None
        )
        request["_owner_terminal"] = terminal
        request["_owner_terminal_phase"] = events[-1]["phase"] if terminal else None
        requests.append(request)
        allocation = request.get("capacity") or {}
        pool = allocation.get("pool", "ordinary")
        if pool != "ordinary":
            allocated[pool] += 1
            if allocation["new_claim"]:
                if pool in claims:
                    raise OwnerError("public_owner_capacity_binding_invalid")
                claims[pool] = request["sdk_operation_id"]
    for pool, count in allocated.items():
        if count > _capacity_pools()[pool] or (count and pool not in claims):
            raise OwnerError("public_owner_capacity_binding_invalid")
        if any(
            row["sdk_operation_id"] != claims[pool]
            for row in requests
            if (row.get("capacity") or {}).get("pool") == pool
        ):
            raise OwnerError("public_owner_capacity_binding_invalid")
    from xpedition_cli.designer_owner_dispatch import WRITE_METHODS, lifecycle_capacity_intent

    for row in requests:
        pool = (row.get("capacity") or {}).get("pool", "ordinary")
        if pool in {"closed_checkpoint", "empty_quit"}:
            if lifecycle_capacity_intent(row["authorization_binding"], row["method"]) != pool:
                raise OwnerError("public_owner_capacity_binding_invalid")
        elif pool == "verification" and (
            row["method"]
            not in {"identity", "snapshot", "read_project", "release_project_preflight"}
            or not any(
                prior["sdk_operation_id"] == row["sdk_operation_id"]
                and prior["method"] in WRITE_METHODS
                for prior in requests
            )
        ):
            raise OwnerError("public_owner_capacity_binding_invalid")
    future = sum(_capacity_pools()[pool] - count for pool, count in allocated.items())
    if used > MAX_PUBLIC_BYTES or charged > MAX_PUBLIC_BYTES:
        raise OwnerError("public_owner_capacity_incomplete")
    slots = max(0, MAX_PUBLIC_COMMANDS - len(directories) - future)
    byte_slots = max(0, (MAX_PUBLIC_BYTES - charged) // MAX_COMMAND_SLOT_BYTES - future)
    report = {
        "scheme": CAPACITY_SCHEME,
        "status": "observed",
        "slots": {
            "limit": MAX_PUBLIC_COMMANDS,
            "used": len(directories),
            "remaining": MAX_PUBLIC_COMMANDS - len(directories),
        },
        "bytes": {
            "limit": MAX_PUBLIC_BYTES,
            "used": used,
            "committed_including_pending": charged,
            "remaining_after_pending": MAX_PUBLIC_BYTES - charged,
        },
        "ordinary_commands_available": min(slots, byte_slots),
        "reserved_commands_remaining": {
            pool: _capacity_pools()[pool] - count for pool, count in allocated.items()
        },
        "reserve_claimed": {pool: pool in claims for pool in _capacity_pools()},
        "authorization_granted": False,
        "native_observation_performed": False,
    }
    return report, requests, claims, allocated


def observe_owner_capacity(ticket):
    """No CAD call and no command publication; unavailable counts remain null."""
    try:
        root, key, intent = _context(ticket)
        if intent.get("mode") != PUBLIC_MODE:
            raise OwnerError("public_owner_capacity_incomplete")
        with L._launch_lock(root):
            return _capacity_inventory(root, key, ticket.epoch)[0]
    except (OSError, ValueError, TypeError, KeyError):
        return {
            "scheme": CAPACITY_SCHEME,
            "status": "unobserved",
            "slots": None,
            "bytes": None,
            "ordinary_commands_available": None,
            "reserved_commands_remaining": None,
            "reserve_claimed": None,
            "authorization_granted": False,
            "native_observation_performed": False,
        }


def _capacity_admission_snapshot(root, key, intent, epoch, requests):
    """The owner authors this side channel after its actual probe, never a caller."""
    events = L._events(root, key, intent)
    values = [
        event["details"].get("owner_admission")
        for event in events
        if event["phase"] == "public_dispatch_ready"
    ]
    values += [row.get("_owner_admission") for row in requests if row["_owner_terminal"]]
    if not values or any(type(value) is not dict for value in values):
        raise OwnerError("public_owner_capacity_admission_unobserved")
    indexes = [value.get("execution_index") for value in values]
    if any(
        type(index) is not int or not 0 <= index <= MAX_PUBLIC_COMMANDS for index in indexes
    ) or len(set(indexes)) != len(indexes):
        raise OwnerError("public_owner_capacity_admission_unobserved")
    newest = max(values, key=lambda value: value["execution_index"])
    launched, _ = L._read(root / "native-launch-evidence.json", key)
    expected = launched.get("expected_native_generation")
    if (
        newest.get("scheme") != "designer-owner-capacity-admission-v1"
        or newest.get("epoch") != epoch
        or type(expected) is not dict
        or newest.get("generation") != expected
        or (
            newest.get("document_empty_observed") is not None
            and type(newest.get("document_empty_observed")) is not bool
        )
        or any(
            not row["_owner_terminal"]
            and row["method"]
            not in {"identity", "snapshot", "read_project", "release_project_preflight"}
            for row in requests
        )
    ):
        raise OwnerError("public_owner_capacity_admission_unobserved")
    return newest


def _require_capacity_lifecycle(
    root, key, intent, epoch, requests, method, params, operation_id, binding, pool
):
    from xpedition_cli._vendor.tool_driver_sdk.models import DriverError
    from xpedition_cli.designer_owner_dispatch import _project_key, capacity_authorization_scope

    scope = capacity_authorization_scope(
        intent["editing_scope"], method, params, operation_id, binding
    )
    observed = _capacity_admission_snapshot(root, key, intent, epoch, requests)
    wanted = binding["lifecycle_intent"]
    if pool == "empty_quit":
        if (
            wanted["pid"] != observed["generation"].get("pid")
            or observed["document_empty_observed"] is not True
        ):
            raise OwnerError("public_owner_capacity_exact_empty_target_required")
        effects, paths = ["observe", "session"], []
    else:
        from xpedition_cli.cli.project import _archive_outside
        from xpedition_cli.errors import CLIError

        project = observed.get("project")
        requested = params.get("project") or params.get("path")
        if (
            not isinstance(project, str)
            or _project_key(wanted["project"]) != _project_key(project)
            or (requested is not None and _project_key(requested) != _project_key(project))
        ):
            raise OwnerError("public_owner_capacity_exact_project_required")
        archive = Path(wanted["output"])
        after_release_observation = method in {
            "identity",
            "snapshot",
            "release_project_preflight",
        } and any(
            row["method"] == "release_project"
            and row["sdk_operation_id"] == operation_id
            and row["_owner_terminal"] is True
            and row["_owner_terminal_phase"] == "command_returned"
            and (row.get("_owner_admission") or {}).get("document_empty_observed") is True
            and row["authorization_binding"] == binding
            for row in requests
        )
        try:
            if archive.suffix.lower() != ".zip" or (
                archive.exists() and not after_release_observation
            ):
                raise ValueError
            _archive_outside(Path(project), archive)
        except (ValueError, CLIError):
            raise OwnerCommandNotPublished(
                "public_owner_capacity_explicit_archive_required", code="E_VALIDATION"
            ) from None
        effects, paths = (
            ["observe", "session", "persist", "artifact"],
            [(project, "persist"), (str(archive), "artifact")],
        )
    try:
        scope.require(effects=effects, paths=paths)
    except DriverError:
        raise OwnerCommandNotPublished(
            "public_owner_capacity_scope_denied", code="E_FORBIDDEN"
        ) from None


def _capacity_allocation(
    report, requests, claims, allocated, method, params, operation_id, binding
):
    from xpedition_cli.designer_owner_dispatch import WRITE_METHODS, lifecycle_capacity_intent

    pool = lifecycle_capacity_intent(binding, method)
    available_bytes = (
        report["bytes"]["remaining_after_pending"]
        - sum(report["reserved_commands_remaining"].values()) * MAX_COMMAND_SLOT_BYTES
    )
    if report["ordinary_commands_available"] > 0 and available_bytes >= _command_worst_case_bytes(
        method
    ):
        return {"scheme": CAPACITY_SCHEME, "pool": "ordinary", "new_claim": False}
    if (
        method in {"snapshot", "release_project_preflight"}
        and params.get("observe_only") is not True
    ):
        raise OwnerCommandNotPublished("public_owner_capacity_readonly_selector_required", report)
    if pool is None and method in {
        "identity",
        "snapshot",
        "read_project",
        "release_project_preflight",
    }:
        if any(
            row["sdk_operation_id"] == operation_id
            and row["method"] in WRITE_METHODS
            and all(
                row["authorization_binding"].get(name) == binding.get(name)
                for name in ("command", "scope_id", "scope_sha256", "source_operation_id")
            )
            for row in requests
        ):
            pool = "verification"
    if (
        pool is None
        or (pool in claims and claims[pool] != operation_id)
        or allocated[pool] >= _capacity_pools()[pool]
        or report["slots"]["remaining"] < 1
        or report["bytes"]["remaining_after_pending"] < MAX_COMMAND_SLOT_BYTES
        or report["slots"]["remaining"] < sum(report["reserved_commands_remaining"].values())
        or report["bytes"]["remaining_after_pending"]
        < sum(report["reserved_commands_remaining"].values()) * MAX_COMMAND_SLOT_BYTES
    ):
        raise OwnerCommandNotPublished("public_owner_verification_budget_reserved", report)
    return {"scheme": CAPACITY_SCHEME, "pool": pool, "new_claim": pool not in claims}


def submit_owner_adapter_command(
    ticket, method, params, *, sdk_operation_id, authorization_binding, observation_seconds=0.25
):
    """Publish once; only typed admission failures establish no new publication."""
    from xpedition_cli.designer_owner_dispatch import WRITE_METHODS, validate_editing_scope

    capacity = None
    try:
        budget = _budget(observation_seconds)
        root, key, intent = _context(ticket)
        if intent.get("mode") != PUBLIC_MODE:
            raise OwnerError("measurement_owner_cannot_accept_public_editing")
        validate_editing_scope(intent.get("editing_scope"))
        if intent["source_sha256"] != _source_binding():
            raise OwnerError("owner_command_source_binding_changed")
        if L._fresh_owner(_registration(root, key, intent))[0] != "verified_alive":
            raise OwnerError("live_original_owner_required")
    except (OSError, ValueError, TypeError, KeyError) as error:
        reason = str(error) if type(error) is OwnerError else "public_owner_admission_incomplete"
        raise OwnerCommandNotPublished(reason) from None
    publication = {"started": False, "command_id": None, "published": None}
    with _PublicCommandLock(root, publication):
        try:
            service_events = L._events(root, key, intent)
            if not any(
                event["phase"] == "public_dispatch_ready" for event in service_events
            ) or any(
                event["phase"]
                in {
                    "owner_unresolved",
                    "owner_cleanup_failed",
                    "owner_cleanup_complete",
                    "native_process_exit_observed",
                    "public_dispatch_stopping",
                }
                for event in service_events
            ):
                raise OwnerError("public_owner_not_currently_serviceable")
            if any(event["phase"] == "public_readonly_quarantine" for event in service_events) and (
                method not in {"identity", "read_project", "snapshot", "close"}
                or (method == "snapshot" and params.get("observe_only") is not True)
            ):
                raise OwnerError("readonly_quarantine_blocks_engineering_write")
            capacity, previous, claims, allocated = _capacity_inventory(root, key, ticket.epoch)
            if any(row["method"] == "close" for row in previous):
                raise OwnerError("public_owner_closure_already_queued")
            if method in WRITE_METHODS:
                wanted = hashlib.sha256(
                    L._canonical({"method": method, "params": params})
                ).hexdigest()
                for old in previous:
                    if old["sdk_operation_id"] == sdk_operation_id and old["method"] == method:
                        original = hashlib.sha256(
                            L._canonical({"method": old["method"], "params": old["params"]})
                        ).hexdigest()
                        if original == wanted:
                            raise OwnerError("original_write_command_already_published_no_replay")
                        raise OwnerError("original_write_command_payload_changed_no_replay")
            from xpedition_cli.designer_owner_dispatch import lifecycle_capacity_intent

            lifecycle_pool = lifecycle_capacity_intent(authorization_binding, method)
            if lifecycle_pool is not None:
                _require_capacity_lifecycle(
                    root,
                    key,
                    intent,
                    ticket.epoch,
                    previous,
                    method,
                    params,
                    sdk_operation_id,
                    authorization_binding,
                    lifecycle_pool,
                )
            allocation = _capacity_allocation(
                capacity,
                previous,
                claims,
                allocated,
                method,
                params,
                sdk_operation_id,
                authorization_binding,
            )
            command_id = "owner-command-" + secrets.token_hex(16)
            request = {
                "command_id": command_id,
                "epoch": ticket.epoch,
                "command": "adapter",
                "method": method,
                "params": params,
                "sdk_operation_id": sdk_operation_id,
                "authorization_binding": authorization_binding,
                "capacity": allocation,
            }
            _validate_adapter_request(request, command_id, ticket.epoch)
            if len(L._pack(request, key)) > MAX_COMMAND_RECORD_BYTES:
                raise OwnerError("public_owner_command_record_limit")
        except OwnerCommandNotPublished:
            raise
        except (OSError, ValueError, TypeError, KeyError) as error:
            reason = (
                str(error) if type(error) is OwnerError else "public_owner_admission_incomplete"
            )
            raise OwnerCommandNotPublished(reason, capacity) from None
        # From this boundary, even an incomplete file publication remains an
        # immutable unknown. The original ID is retained for read-only lookup.
        published = None
        try:
            directory = root / "commands" / command_id
            publication.update(started=True, command_id=command_id)
            L._private_mkdir(directory)
            L._write_new(
                directory / "initial.json",
                L._pack(
                    {
                        "command_id": command_id,
                        "epoch": ticket.epoch,
                        "original_outcome": "unknown",
                        "immutable": True,
                    },
                    key,
                ),
            )
            _command_write(directory / "request.json", request, key)
            published = True
            publication["published"] = True
        except (OSError, ValueError, TypeError, KeyError) as error:
            error.command_id = command_id
            error.owner_request_published = published
            raise
    try:
        return observe_owner_command(ticket, command_id, timeout_seconds=budget)
    except (OSError, ValueError, TypeError, KeyError) as error:
        error.command_id = command_id
        error.owner_request_published = True
        raise


def observe_owner_command(ticket, command_id, *, timeout_seconds=0):
    if not isinstance(command_id, str) or not re.fullmatch(
        r"owner-command-[a-f0-9]{32}", command_id
    ):
        return {
            "status": "legacy_unmanaged_unknown",
            "original_outcome": "unknown",
            "adopted": False,
        }
    deadline = time.monotonic() + _budget(timeout_seconds)
    result = {
        "command_id": command_id,
        "status": "unknown",
        "observation": "unobserved",
        "initial_observation": None,
        "native_writer_admitted": False,
        "observations": [],
        "observations_current": False,
        "request_replayed": False,
        "owner": {"status": "unknown", "owner_state": "unobservable"},
    }
    while True:
        try:
            root, key, intent = _context(ticket)
            directory = root / "commands" / command_id
            initial, _ = L._read(directory / "initial.json", key)
            if initial != {
                "command_id": command_id,
                "epoch": ticket.epoch,
                "original_outcome": "unknown",
                "immutable": True,
            }:
                raise OwnerError("initial_owner_unknown_binding_invalid")
            result["initial_observation"] = initial
            if intent.get("mode") == PUBLIC_MODE:
                request, _ = _command_read(directory / "request.json", key)
                _validate_adapter_request(request, command_id, ticket.epoch)
                observations = _public_command_observations(directory, key, request)
            else:
                values = L._events(root, key, intent)
                observations = [
                    value for value in values if value["details"].get("command_id") == command_id
                ]
            result.pop("reason", None)
            result.update(
                observation="snapshot_complete",
                observations=observations,
                observations_current=True,
                owner=observe_owner(ticket),
            )
            if (
                observations and observations[-1]["phase"] in ("command_returned", "command_failed")
            ) or time.monotonic() >= deadline:
                return result
        except (OSError, ValueError, KeyError, TypeError):
            result.update(
                observation="unobserved",
                observations_current=False,
                reason="owner_command_observation_incomplete",
            )
            if time.monotonic() >= deadline:
                return result
        time.sleep(min(0.02, max(0, deadline - time.monotonic())))


def request_stop_owner(ticket):
    """Cooperative boundary only. Does not cancel COM or silently issue Quit."""
    root, key, _ = _context(ticket)
    path = root / "stop.json"
    if not path.exists():
        L._write_new(
            path, L._pack({"epoch": ticket.epoch, "request": "stop_at_safe_boundary"}, key)
        )
    return {"accepted": True, "native_call_interrupted": False, "quit_dispatched": False}


def _retain_launch_resources(lease, dispatched, pump, *, on_failure=None):
    """No null/failed observation qualifies a launched process's exit boundary.

    A cleanup failure is not retried blindly. The child remains the explicit
    unresolved owner. This function does not cancel, terminate or replay CAD.
    """
    close_failed = False
    while True:
        safe = dispatched is False
        if not safe and not close_failed:
            try:
                state = lease.snapshot()
                safe = (
                    dispatched is True
                    and state.get("worker_generation_observed") is True
                    and state.get("alive") is False
                    and state.get("launcher_alive") is False
                )
            except BaseException:
                safe = False
        if safe and not close_failed:
            try:
                lease.close()
                return
            except BaseException as error:
                # A failed native close is not an instruction to retry it or
                # release the Python lease with uncertain outstanding handles.
                close_failed = True
                if on_failure is not None:
                    try:
                        on_failure(error)
                    except BaseException:
                        pass
        try:
            pump()
        except BaseException:
            pass  # A failed pump cannot discard the outstanding launch lease.
        time.sleep(0.02)


def _join_broker(broker, pump, shared, writer):
    """Best-effort telemetry/pumping cannot bypass the following lease hold."""
    reported = False
    while broker.is_alive():
        if shared.get("broker_cleanup_failed") and not reported:
            reported = True
            try:
                writer.append("owner_cleanup_failed", failure=shared["broker_error"])
            except BaseException:
                pass
        try:
            pump()
        except BaseException:
            pass
        broker.join(0.02)


def _document_dispatch_roles(application, com, budget):
    """Observe default dispatch and requested-IID roles without invoking members.

    QI/canonical equality identifies the object, not its default IDispatch name
    table. These facts deliberately grant no authority to invoke either Open.
    """
    from xpedition_cli import designer_host_bootstrap as B

    points = {}
    callpoint = "not_started"

    def read(point, operation):
        nonlocal callpoint
        callpoint = point
        return budget.read(point, operation)

    report = {
        "scheme": "designer-document-dispatch-roles-v1",
        "diagnostic_only": True,
        "native_writer_admitted": False,
        "collection_member_invocations": 0,
        "requested_interfaces": {
            "documents": DOCS_IID,
            "sheet_documents": "{608CA7F4-A52A-4206-9580-3BEB17CE0073}",
        },
        "observations": points,
        "_untrusted": [
            "observations.*.role.value.interface_name",
            "observations.*.role.value.library_name",
        ],
    }

    def observe(point, operation, *, object_value=False):
        try:
            value = operation()
            points[point] = {
                "status": "observed_null" if value is None else "observed",
                "value": None if object_value else value,
                "hresult": None,
            }
            if object_value:
                points[point]["object_present"] = value is not None
            return value
        except Exception as error:
            hr = getattr(error, "hresult", None)
            points[point] = {
                "status": "unsupported"
                if hr in {-2147352573, -2147352570, -2147467263, -2147467262}
                else "error",
                "value": None,
                "hresult": hr if type(hr) is int else None,
                "reason": "native_document_role_observation_failed",
                "failed_callpoint": callpoint,
            }
            return None

    def text(value):
        if type(value) is not str or len(value) > 256:
            raise OwnerError("native_document_role_text_invalid")
        return value

    def role(info):
        attrs = read("document_role.interface_attributes", info.GetTypeAttr)
        name = read("document_role.interface_name", lambda: info.GetDocumentation(-1)[0])
        library, _ = read("document_role.typelib", info.GetContainingTypeLib)
        try:
            libattrs = read("document_role.library_attributes", library.GetLibAttr)
            libname = read("document_role.library_name", lambda: library.GetDocumentation(-1)[0])
            integers = (attrs.typekind, attrs.wTypeFlags, libattrs[1], libattrs[3], libattrs[4])
            if any(type(value) is not int for value in integers):
                raise OwnerError("native_document_role_attributes_invalid")
            return {
                "interface_iid": B.W._guid(attrs.iid),
                "interface_name": text(name),
                "interface_typekind": attrs.typekind,
                "interface_typeflags": attrs.wTypeFlags,
                "library_guid": B.W._guid(libattrs[0]),
                "library_name": text(libname),
                "library_lcid": libattrs[1],
                "library_major": libattrs[3],
                "library_minor": libattrs[4],
                "current_viewdraw_151": (
                    B.W._guid(libattrs[0]) == B.W.VIEWDRAW_GUID
                    and libname == "ViewDraw"
                    and libattrs[3:5] == (151, 0)
                ),
            }
        finally:
            library = None

    def raw_role(raw):
        info = read("document_role.typeinfo", lambda: raw.GetTypeInfo(0, 0))
        try:
            return role(info)
        finally:
            info = None

    def declared_role(raw, iid):
        info = library = declared = None
        try:
            info = read("document_role.declaration_source", lambda: raw.GetTypeInfo(0, 0))
            library, _ = read("document_role.declaration_library", info.GetContainingTypeLib)
            declared = read(
                "document_role.declared_interface", lambda: library.GetTypeInfoOfGuid(iid)
            )
            return role(declared)
        finally:
            info = library = declared = None

    def member(raw):
        value = read("document_role.Open_name", lambda: raw.GetIDsOfNames(0, "Open"))
        if type(value) is not int:
            raise OwnerError("native_document_role_member_not_integer")
        return value

    def getter(name, dispid, kind):
        actual = read("document_role.getter_member", lambda: application.GetIDsOfNames(0, name))
        if type(actual) is not int or actual != dispid:
            raise OwnerError("native_document_role_getter_mapping_differs")
        value = read(
            "document_role.getter_" + name,
            lambda: application.InvokeTypes(dispid, 0, kind, (com.VT_DISPATCH, 0), ()),
        )
        return None if value is None else B._raw(value, com)

    def collection(label, name, dispid, kind, iid):
        returned = queried = original = repeated = None
        for suffix in (
            "getter",
            "returned.role",
            "returned.Open_member",
            "requested_interface",
            "queried.role",
            "queried.Open_member",
            "same_canonical",
            "declared.role",
            "application_declared.role",
        ):
            points[label + "." + suffix] = {
                "status": "not_attempted",
                "value": None,
                "hresult": None,
            }
        try:
            observe(label + ".application_declared.role", lambda: declared_role(application, iid))
            returned = observe(
                label + ".getter", lambda: getter(name, dispid, kind), object_value=True
            )
            if returned is None:
                return
            observe(label + ".returned.role", lambda: raw_role(returned))
            observe(label + ".returned.Open_member", lambda: member(returned))
            observe(label + ".declared.role", lambda: declared_role(returned, iid))
            queried = observe(
                label + ".requested_interface",
                lambda: read(
                    "document_role.query_requested_interface",
                    lambda: returned.QueryInterface(iid, com.IID_IDispatch),
                ),
                object_value=True,
            )
            if queried is None:
                return
            observe(label + ".queried.role", lambda: raw_role(queried))
            observe(label + ".queried.Open_member", lambda: member(queried))

            def same_canonical():
                nonlocal original, repeated
                original = read(
                    "document_role.original_canonical",
                    lambda: returned.QueryInterface(com.IID_IUnknown),
                )
                repeated = read(
                    "document_role.queried_canonical",
                    lambda: queried.QueryInterface(com.IID_IUnknown),
                )
                if original is None or repeated is None:
                    raise OwnerError("native_document_role_canonical_missing")
                value = original == repeated
                if type(value) is not bool:
                    raise OwnerError("native_document_role_canonical_comparison_invalid")
                return value

            observe(label + ".same_canonical", same_canonical)
        finally:
            returned = queried = original = repeated = None

    for label, name, dispid, kind, iid in (
        ("documents", "Documents", 1, 2, DOCS_IID),
        (
            "sheet_documents",
            "SchematicSheetDocuments",
            212,
            1,
            "{608CA7F4-A52A-4206-9580-3BEB17CE0073}",
        ),
    ):
        collection(label, name, dispid, kind, iid)
    report["metadata_observation_complete"] = all(
        point["status"] == "observed" for point in points.values()
    )
    return report


class NativeApplication:
    """STA-only interface consumed from the authenticated handoff, never ROT."""

    def __init__(self, stream, com, expected, lease, project):
        self._thread = threading.current_thread()
        self._ident = threading.get_ident()
        self.com, self.expected, self.lease, self.project = com, expected, lease, project
        self.runtime_root = None
        self.project_origin = None
        self.project_open_verified = False
        self._capacity_document_empty = None
        self.readonly_quarantine = None
        self._pdf_producer = None
        self._dual_count_probe = None
        self._dual_document_call = None
        self._dual_document_preflight = None
        self._document_write_budget = None
        self.quit_attempted = False
        self.quit_returned = None
        self._save_close_started = False
        self._lifecycle_origin_command_id = None
        self.boundary = None
        self._automation_started = False
        self._automation = {
            "silent_mode_before": None,
            "before_observation": "unobserved",
            "set_attempted": False,
            "set_returned": None,
            "silent_mode_after": None,
            "after_observation": "unobserved",
            "verified": None,
        }
        self._lifecycle = {
            "save_call_attempted": False,
            "save_call_returned": None,
            "save_persistence_verified": None,
            "close_call_attempted": False,
            "close_call_returned": None,
            "close_result_bool": None,
            "post_close_empty_observed": None,
        }
        self.raw = com.CoGetInterfaceAndReleaseStream(stream, com.IID_IDispatch)
        self.unknown = self.raw.QueryInterface(com.IID_IUnknown)

    @classmethod
    def from_handoff(cls, handoff, com, expected, lease, project):
        from xpedition_cli.designer_owner_transport import Handoff

        if (
            type(handoff) is not Handoff
            or handoff._expected is not expected
            or handoff._pin is not lease
        ):
            raise OwnerError("same_authenticated_handoff_required")
        application = cls(handoff.take_stream(), com, expected, lease, project)
        application._origin_handoff = handoff
        return application

    def _owner(self):
        if threading.current_thread() is not self._thread or threading.get_ident() != self._ident:
            raise OwnerError("native_application_owner_sta_required")

    def _require_dual_count_released(self):
        self._owner()
        pending = self._dual_count_probe
        if pending is not None and pending.resources_released is False:
            raise OwnerError("native_dual_count_cleanup_pending")
        document_call = self._dual_document_call
        if document_call is not None and document_call.resources_released is False:
            raise OwnerError("native_dual_document_cleanup_pending")

    def _begin_document_command(self):
        self._owner()
        self._capacity_document_empty = None
        self._require_dual_count_released()
        if self._document_write_budget is not None:
            raise OwnerError("native_document_command_already_active")
        owner = self

        class CardinalityBudget:
            # Public operation/backend observation deadlines remain unchanged.
            # No new15s deadline may truncate a legitimate writing call. A
            # blocked call stays here on this owner after its client times out.
            def __init__(self):
                self.calls = 0

            def read(self, point, operation):
                owner._owner()
                if self.calls >= 4096:
                    raise OwnerError("native_document_metadata_call_limit")
                self.calls += 1
                return owner._boundary_call("fixed_document." + point, operation)

        self._document_write_budget = CardinalityBudget()

    def _end_document_command(self):
        self._owner()
        self._document_write_budget = None

    def _require_exact_open_document_project(self):
        """Observe only; never change project admission or open a document."""
        self._owner()
        observed = self.probe()
        if (
            observed["native_project_open"] is not True
            or observed["open_project_matches_scope"] is not True
        ):
            raise OwnerError("native_document_exact_open_project_required")
        return observed

    def _invoke_fixed_document(
        self, operation=None, argument=None, *, on_dispatch=None, on_return=None
    ):
        self._owner()
        self._require_dual_count_released()
        from . import designer_owner_dispatch as D
        from .designer_dual_invoke import FixedDocumentCall

        D.require_current_document_owner(self)
        if self._document_write_budget is None:
            raise OwnerError("native_document_current_command_budget_required")
        before = self.probe()
        if operation is not None and (
            before["native_project_open"] is not True
            or before["open_project_matches_scope"] is not True
        ):
            raise OwnerError("native_document_exact_open_project_required")
        if operation == "open_design":
            from .native_com_adapter import _designer_declared_call, _designer_design_names

            active = _designer_declared_call(self.raw, "GetActiveDesign", 229, 8)
            if (
                type(active) is not str
                or active != argument
                or argument not in _designer_design_names(self.raw)
            ):
                raise OwnerError("native_document_fresh_active_design_required")
        call = FixedDocumentCall(
            self.raw,
            self.com,
            self._document_write_budget,
            owner_thread=self._thread,
            owner_ident=self._ident,
            installation_root=getattr(self.lease, "home", None),
            cad_executable=self.expected.executable,
            source_authorizer=self._authorize_dual_typelib_source,
            operation=operation,
            argument=argument,
        )
        self._dual_document_call = call  # Own before any acquisition or invocation.
        project_check = (
            self.probe if operation is None else self._require_exact_open_document_project
        )
        facts = call.run(
            on_dispatch=on_dispatch,
            on_return=on_return,
            precheck=project_check,
            postcheck=project_check,
        )
        if operation is None:
            self._dual_document_preflight = facts
            return facts
        if operation == "open_design":
            return call.take_document()
        return None

    def _read_fixed_wire_constant(self):
        self._owner()
        self._require_dual_count_released()
        from . import designer_owner_dispatch as D
        from .designer_dual_invoke import FixedWireConstantProbe

        D.require_current_document_owner(self)
        if self._document_write_budget is None:
            raise OwnerError("native_document_current_command_budget_required")
        self._require_exact_open_document_project()
        probe = FixedWireConstantProbe(
            self.raw,
            self.com,
            self._document_write_budget,
            owner_thread=self._thread,
            owner_ident=self._ident,
            installation_root=getattr(self.lease, "home", None),
            cad_executable=self.expected.executable,
            source_authorizer=self._authorize_dual_typelib_source,
        )
        self._dual_document_call = probe
        facts = probe.resolve()
        self._require_exact_open_document_project()
        if (
            facts.get("status") != "observed"
            or type(facts.get("value")) is not int
            or facts["value"] != 0
        ):
            raise OwnerError("native_fixed_wire_value_not_observed")
        return facts["value"]

    def trusted_config_root(self):
        """Derive the existing public SDK root from our fixed runtime layout."""
        self._owner()
        if self.runtime_root is None:
            raise OwnerError("public_owner_runtime_binding_required")
        root = L._plain_path(self.runtime_root)
        suffix = "-designer-runtime"
        if not re.fullmatch(r"owner-[a-f0-9]{32}", root.name) or not root.parent.name.endswith(
            suffix
        ):
            raise OwnerError("public_owner_runtime_binding_required")
        name = root.parent.name[: -len(suffix)]
        if not name or name in {".", ".."}:
            raise OwnerError("public_owner_runtime_binding_required")
        return L._plain_path(root.parent.parent / name)

    def _authorize_dual_typelib_source(self, path):
        """Read only the fixed SDK dependency under the original signed scope."""
        self._owner()
        if self.runtime_root is None or self._origin_handoff is None:
            raise OwnerError("native_dual_count_public_scope_required")
        from .designer_owner_dispatch import validate_editing_scope

        _, _, intent = _context(OwnerTicket(self.runtime_root, self._origin_handoff._epoch))
        if intent.get("mode") != PUBLIC_MODE:
            raise OwnerError("native_dual_count_public_scope_required")
        scope = validate_editing_scope(intent["editing_scope"])
        scope.require(effects=["observe"], paths=[(str(path), "observe")])

    def own_generation_reader(self):
        """Original pinned handles, never peer-reported PID or a new lookup."""
        self._owner()
        snapshot = self.lease.snapshot()
        if not self.expected.matches(snapshot):
            raise OwnerError("original_generation_unavailable_for_project_writer_check")
        launcher = getattr(self.lease, "launcher_pin", None)
        if launcher is None:
            raise OwnerError("original_launcher_handle_required")
        identity = launcher.identity
        return [
            {
                "role": "worker",
                "domain": "schematic",
                "process_identity": {
                    "pid": self.expected.pid,
                    "executable": self.expected.executable,
                    "creation_time_100ns": self.expected.birth_100ns,
                },
                "alive": True,
            },
            {
                "role": "launcher",
                "domain": "schematic",
                "process_identity": {
                    key: identity[key] for key in ("pid", "executable", "creation_time_100ns")
                },
                "alive": not launcher.exited(),
            },
        ]

    def require_empty_project(self):
        self._owner()
        if self.probe()["native_project_open"] is not False:
            raise OwnerError("fresh_empty_owner_project_required")
        member = self._boundary_call(
            "project.empty.ActiveDocument.member",
            lambda: self.raw.GetIDsOfNames(0, "ActiveDocument"),
        )
        if type(member) is not int or member != 208:
            raise OwnerError("native_active_document_member_mapping_differs")
        document = self._boundary_call(
            "project.empty.ActiveDocument208",
            lambda: self.raw.InvokeTypes(208, 0, 2, (self.com.VT_DISPATCH, 0), ()),
        )
        if document is not None:
            document = None
            raise OwnerError("fresh_empty_owner_document_required")

    def confirm_project_open(self):
        self._owner()
        self._require_dual_count_released()
        observed = self.probe()
        if (
            self.project is None
            or observed["native_project_open"] is not True
            or observed["open_project_matches_scope"] is not True
        ):
            raise OwnerError("exact_native_project_open_not_verified")
        if self._save_close_started:
            if (
                self._lifecycle["close_result_bool"] is not True
                or self._lifecycle["post_close_empty_observed"] is not True
            ):
                raise OwnerError("incomplete_close_cannot_reset_project_lifecycle")
            self._save_close_started = False
            self._lifecycle = dict.fromkeys(self._lifecycle)
            self._lifecycle["save_call_attempted"] = self._lifecycle["close_call_attempted"] = False
        self.project_open_verified = True
        return observed

    def require_reopen_allowed(self, *, require_completed_close=False):
        self._owner()
        self._require_dual_count_released()
        if require_completed_close and not self._save_close_started:
            raise OwnerError("completed_owner_close_required_before_reopen")
        if self._save_close_started and (
            self._lifecycle["close_result_bool"] is not True
            or self._lifecycle["post_close_empty_observed"] is not True
        ):
            raise OwnerError("incomplete_owner_close_forbids_reopen")

    def probe(self):
        from xpedition_cli import designer_host_bootstrap as B

        self._owner()
        if not self.expected.matches(self.lease.snapshot()):
            raise OwnerError("native_generation_before_probe_mismatch")
        observation = B.HostObservation()
        budget = B.Budget(observation.result, time.monotonic, observation=observation)
        if self.raw.QueryInterface(self.com.IID_IUnknown) != self.unknown:
            raise OwnerError("retained_application_canonical_changed")
        role = B._role(self.raw, self.com, budget)
        project = B._native_project(self.raw, self.com, budget)
        if project["open"]:
            if self.project is None:
                raise OwnerError("dedicated_empty_session_has_open_project")
            if B.W._path(self.project) != project["path"]:
                raise OwnerError("native_project_outside_authorized_scope")
        if B._native_project(self.raw, self.com, budget) != project:
            raise OwnerError("native_project_changed_during_probe")
        if self.raw.QueryInterface(self.com.IID_IUnknown) != self.unknown:
            raise OwnerError("retained_application_canonical_changed")
        if not self.expected.matches(self.lease.snapshot()):
            raise OwnerError("native_generation_after_probe_mismatch")
        self._capacity_document_empty = (
            not project["open"] if type(project["open"]) is bool else None
        )
        return {
            "role": role,
            "canonical_stable": True,
            "native_project_open": project["open"],
            "open_project_matches_scope": True if project["open"] else None,
            "read_operations": budget.calls,
            "last_read_phase": observation.snapshot()["phase"],
        }

    def quit_empty(self):
        self._owner()
        self._require_dual_count_released()
        if self.quit_attempted:
            raise OwnerError("native_quit_already_dispatched")
        if self.probe()["native_project_open"] is not False:
            raise OwnerError("fresh_empty_project_required_before_quit")
        active_id = self._boundary_call(
            "lifecycle.ActiveDocument.member", lambda: self.raw.GetIDsOfNames(0, "ActiveDocument")
        )
        if type(active_id) is not int or active_id != 208:
            raise OwnerError("native_active_document_member_mapping_differs")
        active = self._boundary_call(
            "lifecycle.ActiveDocument208",
            lambda: self.raw.InvokeTypes(208, 0, 2, (self.com.VT_DISPATCH, 0), ()),
        )
        if active is not None:
            active = None
            raise OwnerError("fresh_empty_active_document_required_before_quit")
        member = self._boundary_call(
            "lifecycle.Quit.member", lambda: self.raw.GetIDsOfNames(0, "Quit")
        )
        if type(member) is not int or member != 226:
            raise OwnerError("native_quit_member_unavailable")

        def quit_once():
            self.quit_attempted = True
            self.raw.InvokeTypes(member, 0, 1, (self.com.VT_EMPTY, 0), ())
            self.quit_returned = True

        self._boundary_call("lifecycle.Quit226", quit_once)
        return {"quit_reply_received": True, "process_exit_verified": None}

    def _boundary_call(self, step, operation):
        if self.boundary is None:
            return operation()
        from xpedition_cli import native_com_adapter as A

        with A._owner_boundary_trace(self.boundary):
            return A._native_call(step, operation)

    def automation_snapshot(self):
        self._owner()
        return dict(self._automation)

    def configure_silent_mode(self):
        """One exact 151 SilentMode250 GET/PUT/GET on this retained object.

        Called only after the public grant and current session scope have been
        checked. A void setter reply is not acceptance; the after-get proves 1.
        """
        from xpedition_cli import native_com_adapter as A

        self._owner()
        self._require_dual_count_released()
        if self._automation_started:
            raise OwnerError("native_automation_configuration_already_started_no_replay")
        self._automation_started = True
        member = A._native_call(
            "automation.SilentMode.member", lambda: self.raw.GetIDsOfNames(0, "SilentMode")
        )
        if type(member) is not int or member != 250:
            raise OwnerError("native_silent_mode_member_mapping_differs")

        def read_mode(step, value_key, observation_key):
            try:
                return A._native_call(
                    step, lambda: self.raw.InvokeTypes(250, 0, 2, (self.com.VT_I4, 0), ())
                )
            except Exception as error:
                observed = getattr(error, "native_result_summary", None)
                if getattr(error, "native_call_returned", None) is True and type(observed) is int:
                    self._automation[value_key] = observed
                    self._automation[observation_key] = "observed_boundary_incomplete"
                raise

        before = read_mode(
            "automation.SilentMode.before", "silent_mode_before", "before_observation"
        )
        self._automation["before_observation"] = "observed"
        self._automation["silent_mode_before"] = before if type(before) is int else None
        if type(before) is not int or before not in (0, 1):
            raise OwnerError("native_silent_mode_before_not_declared_enum")

        def set_one():
            self._automation["set_attempted"] = True
            self.raw.InvokeTypes(250, 0, 4, (self.com.VT_EMPTY, 0), ((self.com.VT_I4, 1),), 1)
            self._automation["set_returned"] = True

        A._native_call("automation.SilentMode.set_one", set_one)
        after = read_mode("automation.SilentMode.after", "silent_mode_after", "after_observation")
        self._automation["after_observation"] = "observed"
        self._automation["silent_mode_after"] = after if type(after) is int else None
        self._automation["verified"] = type(after) is int and after == 1
        if self._automation["verified"] is not True:
            raise OwnerError("native_silent_mode_readback_differs")
        return self.automation_snapshot()

    def read_project(self):
        """Independent creation-state observations; flag295 never hides data228.

        These are readonly fields, not a synthesized empty/open verdict. No
        project path is opened as a file or used to activate a sheet.
        """
        import ntpath

        from xpedition_cli import designer_host_bootstrap as B

        self._owner()
        result = {
            "project": {"open": None, "path": None, "status": "independent_observations"},
            "icdb_designs": None,
            "available_schematics": None,
            "available_sheet_count": None,
            "open_sheet_document_count": None,
            "lists_observation": "incomplete",
            "native_writer_admitted": False,
            "observations": {},
            "integrity": {
                "original_generation_before": None,
                "canonical_before": None,
                "original_generation_after": None,
                "canonical_after": None,
            },
            "_untrusted": ["project.path", "icdb_designs", "available_schematics"],
        }
        fields = result["observations"]
        for key in (
            "application_role",
            "project_open_flag",
            "project_data",
            "project_path",
            "icdb_designs",
            "sheet_documents",
            "open_sheet_document_count",
            "available_schematics",
            "available_sheets",
            "document_dispatch_roles",
            "dual_document_counts",
        ):
            fields[key] = {"status": "not_attempted", "value": None, "hresult": None}

        class ReadBudget:
            def __init__(self):
                self.started, self.calls, self.point = time.monotonic(), 0, "initial"

            def read(self, point, operation):
                self.point = point
                if self.calls >= 1024 or time.monotonic() - self.started > 15:
                    raise OwnerError("native_read_project_budget_exhausted")
                self.calls += 1
                value = operation()
                if time.monotonic() - self.started > 15:
                    raise OwnerError("native_read_project_budget_exhausted")
                return value

        budget, com = ReadBudget(), self.com

        def invoke(raw, name, dispid, result_type, arguments=(), values=(), kind=1):
            actual = budget.read("member_" + name, lambda: raw.GetIDsOfNames(0, name))
            if type(actual) is not int or actual != dispid:
                raise OwnerError("native_read_project_declared_member_mismatch")
            return budget.read(
                "invoke_" + name,
                lambda: raw.InvokeTypes(dispid, 0, kind, (result_type, 0), arguments, *values),
            )

        def typed(value, iid):
            if value is None:
                return None
            raw = B._raw(value, com)
            if (
                budget.read(
                    "interface", lambda: raw.QueryInterface("{" + iid + "}", com.IID_IUnknown)
                )
                is None
            ):
                raise OwnerError("native_read_project_declared_interface_missing")
            return raw

        def observe(key, operation, *, object_value=False):
            try:
                value = operation()
                fields[key] = {
                    "status": "observed_null" if value is None else "observed",
                    "value": None if object_value else value,
                    "hresult": None,
                }
                if object_value:
                    fields[key]["object_present"] = value is not None
                return value
            except Exception as error:
                hr = getattr(error, "hresult", None)
                reason = str(error) if type(error) is OwnerError else "native_getter_exception"
                fields[key] = {
                    "status": "unsupported"
                    if reason == "native_read_project_declared_member_mismatch"
                    or hr in {-2147352573, -2147352570, -2147467263}
                    else "error",
                    "value": None,
                    "hresult": hr if type(hr) is int else None,
                    "reason": reason
                    if reason.startswith("native_read_project_")
                    else "native_getter_exception",
                }
                return None

        def text(value):
            if value is None:
                return None
            if not isinstance(value, str) or len(value) > 4096:
                raise OwnerError("native_read_project_text_invalid_or_over_limit")
            return value

        def strings(value, limit):
            raw = typed(value, "3566b3fb-97c7-4f45-b1a6-e5469271f9ac")
            if raw is None:
                return None
            count = invoke(raw, "GetCount", 1, com.VT_I4)
            if type(count) is not int or not 0 <= count <= limit:
                raise OwnerError("native_read_project_list_count_invalid_or_over_limit")
            items = []
            for index in range(1, count + 1):
                item = text(invoke(raw, "GetItem", 2, com.VT_BSTR, ((com.VT_I4, 1),), (index,)))
                if item is None:
                    raise OwnerError("native_read_project_list_item_null")
                items.append(item)
            if invoke(raw, "GetCount", 1, com.VT_I4) != count:
                raise OwnerError("native_read_project_string_list_changed")
            return items

        def integrity(where):
            generation = self.expected.matches(self.lease.snapshot())
            canonical = self.raw.QueryInterface(com.IID_IUnknown) == self.unknown
            result["integrity"]["original_generation_" + where] = generation
            result["integrity"]["canonical_" + where] = canonical
            if generation is not True or canonical is not True:
                raise OwnerError("native_read_project_identity_" + where + "_mismatch")

        data = docs = docs_app = None
        integrity("before")
        try:
            if observe("application_role", lambda: B._role(self.raw, com, budget)) is None:
                return result
            observe(
                "document_dispatch_roles",
                lambda: _document_dispatch_roles(self.raw, com, budget),
            )

            def dual_counts():
                from .designer_dual_invoke import ReadonlyCountProbe

                previous = self._dual_count_probe
                if previous is not None and previous.resources_released is not True:
                    raise OwnerError("native_dual_count_resources_still_pending")
                self._dual_count_probe = ReadonlyCountProbe(
                    self.raw,
                    com,
                    budget,
                    owner_thread=self._thread,
                    owner_ident=self._ident,
                    installation_root=getattr(self.lease, "home", None),
                    cad_executable=self.expected.executable,
                    source_authorizer=self._authorize_dual_typelib_source,
                )
                return self._dual_count_probe.observe()

            observe("dual_document_counts", dual_counts)
            opened = observe(
                "project_open_flag", lambda: invoke(self.raw, "IsProjectOpened", 295, com.VT_BOOL)
            )
            if type(opened) is bool:
                result["project"]["open"] = opened
            elif opened is not None:
                fields["project_open_flag"] = {
                    "status": "error",
                    "value": None,
                    "hresult": None,
                    "reason": "native_project_flag_not_boolean",
                }
            data = observe(
                "project_data",
                lambda: typed(
                    invoke(self.raw, "GetProjectData", 228, com.VT_DISPATCH), B.PROJECT_IID
                ),
                object_value=True,
            )
            if data is not None:
                path = observe(
                    "project_path", lambda: text(invoke(data, "GetProjectFilePath", 3, com.VT_BSTR))
                )
                if path is not None:
                    matches = bool(self.project) and ntpath.normcase(
                        ntpath.normpath(path)
                    ) == ntpath.normcase(ntpath.normpath(str(self.project)))
                    if path and not matches:
                        fields["project_path"].update(
                            value=None,
                            redacted=True,
                            outside_bound_project=True,
                            characters=len(path),
                            sha256=hashlib.sha256(path.encode("utf-8")).hexdigest(),
                        )
                        result["project"]["path_outside_bound_project"] = True
                    else:
                        result["project"]["path"] = path
                result["icdb_designs"] = observe(
                    "icdb_designs",
                    lambda: strings(invoke(data, "GetiCDBDesigns", 9, com.VT_DISPATCH), 64),
                )

            def documents():
                nonlocal docs_app
                value = typed(
                    invoke(self.raw, "SchematicSheetDocuments", 212, com.VT_DISPATCH),
                    "608ca7f4-a52a-4206-9580-3beb17ce0073",
                )
                if value is None:
                    return None
                docs_app = B._raw(invoke(value, "Application", 102, com.VT_DISPATCH, kind=2), com)
                if (
                    budget.read(
                        "documents_application_canonical",
                        lambda: docs_app.QueryInterface(com.IID_IUnknown),
                    )
                    != self.unknown
                ):
                    raise OwnerError("native_read_project_documents_application_mismatch")
                return value

            docs = observe("sheet_documents", documents, object_value=True)
            if docs is not None:

                def count():
                    value = invoke(docs, "Count", 101, com.VT_I4, kind=2)
                    if type(value) is not int or not 0 <= value <= 64:
                        raise OwnerError("native_read_project_open_document_count_invalid")
                    return value

                result["open_sheet_document_count"] = observe("open_sheet_document_count", count)
                schematics = observe(
                    "available_schematics",
                    lambda: strings(
                        invoke(docs, "GetAvailableSchematics", 108, com.VT_DISPATCH), 64
                    ),
                )
                if schematics is not None:

                    def sheets():
                        values, total = [], 0
                        for schematic in schematics:
                            names = strings(
                                invoke(
                                    docs,
                                    "GetAvailableSheets",
                                    109,
                                    com.VT_DISPATCH,
                                    ((com.VT_BSTR, 1),),
                                    (schematic,),
                                ),
                                64,
                            )
                            if names is None:
                                raise OwnerError("native_read_project_sheet_list_null")
                            total += len(names)
                            if total > 128:
                                raise OwnerError("native_read_project_total_sheet_limit")
                            values.append({"schematic": schematic, "sheets": names})
                        return values

                    result["available_schematics"] = observe("available_sheets", sheets)
                    if result["available_schematics"] is not None:
                        result["available_sheet_count"] = sum(
                            len(row["sheets"]) for row in result["available_schematics"]
                        )
            result["lists_observation"] = (
                "complete"
                if all(
                    fields[key]["status"] == "observed"
                    for key in ("icdb_designs", "available_schematics", "available_sheets")
                )
                else "incomplete"
            )
            return result
        finally:
            data = docs = docs_app = None
            integrity("after")
            result["read_operations"], result["last_read_phase"] = budget.calls, budget.point
            result["canonical_stable"] = True
            result["open_documents_scope"] = (
                "currently_open_native_SheetDocuments_not_available_sheets"
            )

    def lifecycle_snapshot(self):
        return dict(self._lifecycle)

    def save_close_project(self, progress=None):
        """One SaveAll/CloseProject chain on the fresh exact authorized project.

        A void SaveAll reply is recorded as a returned call, never persistence
        verification. Failed/unknown stages cannot be replayed on this object.
        ``progress`` is only the owner's private event sink, never CLI input.
        """

        self._owner()
        self._require_dual_count_released()
        if self._save_close_started:
            raise OwnerError("native_save_close_already_started_no_replay")
        if self.project is None:
            raise OwnerError("authorized_project_required_before_save_close")
        before = self.probe()
        if (
            before["native_project_open"] is not True
            or before["open_project_matches_scope"] is not True
        ):
            raise OwnerError("fresh_exact_open_project_required_before_save_close")

        def member(raw, name, expected):
            value = raw.GetIDsOfNames(0, name)
            if type(value) is not int or value != expected:
                raise OwnerError("declared_native_lifecycle_member_mapping_differs")
            return value

        def publish(phase):
            if progress is not None:
                progress(phase, self.lifecycle_snapshot())

        self._save_close_started = True
        self.project_open_verified = False
        self._lifecycle_origin_command_id = (
            self.boundary.command_id if self.boundary is not None else None
        )
        try:
            close_id = member(self.raw, "CloseProject", 274)
            publish("save_call_intent_recorded")
            self._invoke_fixed_document(
                "save_all",
                on_dispatch=lambda: self._lifecycle.__setitem__("save_call_attempted", True),
                on_return=lambda: self._lifecycle.__setitem__("save_call_returned", True),
            )
            publish("save_call_returned")
            # A project switch during SaveAll must stop CloseProject.
            saved = self.probe()
            if (
                saved["native_project_open"] is not True
                or saved["open_project_matches_scope"] is not True
            ):
                raise OwnerError("native_project_changed_after_save_close_stopped")
            publish("close_call_intent_recorded")
            self._lifecycle["close_call_attempted"] = True
            closed = self.raw.InvokeTypes(close_id, 0, 1, (self.com.VT_BOOL, 0), ())
            self._lifecycle["close_call_returned"] = True
            self._lifecycle["close_result_bool"] = closed if type(closed) is bool else None
            publish("close_call_returned")
            if type(closed) is not bool:
                raise OwnerError("native_close_project_reply_not_boolean")
            after = self.probe()
            self._lifecycle["post_close_empty_observed"] = after["native_project_open"] is False
            publish("post_close_probe_returned")
            if closed is not True or after["native_project_open"] is not False:
                raise OwnerError("native_project_normal_close_not_confirmed")
            return self.lifecycle_snapshot()
        finally:
            # FixedDocumentCall retains uncertain native arguments/results on
            # this owner; no local finally discards that graph.
            pass

    def run_pdf_producer(self, arguments, cwd):
        """Own one fixed console producer until process and local resources end.

        The client deadline never cancels this call. The original process object
        and private streams remain on this owner; no daemon, replay, GUI or kill
        is used. A launch/cleanup whose outcome cannot be observed stays owned.
        """
        self._owner()
        self._require_dual_count_released()
        if self._pdf_producer is not None or self.boundary is None:
            raise OwnerError("owned_pdf_producer_context_required")
        command_id = self.boundary.command_id
        if not re.fullmatch(r"owner-command-[a-f0-9]{32}", str(command_id)):
            raise OwnerError("original_pdf_command_id_required")
        root, key, _intent = _context(OwnerTicket(self.runtime_root, self._origin_handoff._epoch))
        directory = root / "commands" / command_id
        expected = Path(self.lease.home) / "common/win64/bin/sch2pdf.exe"
        if (
            not arguments
            or Path(arguments[0]).resolve() != expected.resolve()
            or Path(cwd).resolve() != expected.parent.resolve()
        ):
            raise OwnerError("fixed_owned_pdf_producer_required")
        request, request_sha256 = _command_read(directory / "request.json", key)
        _validate_adapter_request(request, command_id, self._origin_handoff._epoch)
        producer = _capacity_pdf_expected(request)
        expected_path = ntpath.normcase(ntpath.normpath(str(expected.resolve())))
        if expected_path != ntpath.normcase(ntpath.normpath(producer["path"])):
            raise OwnerError("public_owner_pdf_records_invalid")
        state = {
            "process": None,
            "streams": [],
            "attempted": False,
            "returned": False,
            "cleanup_complete": False,
        }
        self._pdf_producer = state  # Adopt partial resources before any I/O/spawn.
        failure = None
        try:
            for name in ("pdf-producer-stdout.bin", "pdf-producer-stderr.bin"):
                state["streams"].append((directory / name).open("xb"))
            L._write_new(
                directory / "pdf-producer-initial.json",
                L._pack(
                    {
                        "command_id": command_id,
                        "epoch": self._origin_handoff._epoch,
                        "original_outcome": "unknown",
                        "immutable": True,
                        "expected_producer_path": expected_path,
                        "request_sha256": request_sha256,
                    },
                    key,
                ),
            )
            from .designer_frozen_binding import (
                external_application_dll_search,
                external_child_environment,
            )

            environment = external_child_environment(dict(os.environ))
            with external_application_dll_search():
                state["attempted"] = True
                state["process"] = subprocess.Popen(
                    arguments,
                    cwd=str(cwd),
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=state["streams"][0],
                    stderr=state["streams"][1],
                    close_fds=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
        except BaseException as error:
            failure = error
        process = state["process"]
        if process is None and state["attempted"]:
            self._retain_pdf_uncertainty()  # Popen outcome unknown; no second launch.
        if process is not None:
            try:
                identity = L._identity(int(process._handle))
                L._write_new(
                    directory / "pdf-producer-launched.json",
                    L._pack(
                        {
                            "command_id": command_id,
                            "epoch": self._origin_handoff._epoch,
                            "process": identity,
                            "automatic_replay": False,
                        },
                        key,
                    ),
                )
            except BaseException as error:
                failure = failure or error
            while not state["returned"]:
                try:
                    returned = process.wait()  # Owner wait, without a cancellation deadline.
                    if type(returned) is not int:
                        raise OwnerError("owned_pdf_exit_code_unobserved")
                    state["returned"] = True
                except BaseException as error:
                    failure = failure or error
                    time.sleep(0.02)  # Observe this same original child only.
        for stream in reversed(state["streams"]):
            try:
                stream.close()
            except BaseException:
                self._retain_pdf_uncertainty()  # Do not retry a close of unknown outcome.
        if process is not None:
            try:
                process._handle.Close()
                process._handle = None
            except BaseException:
                self._retain_pdf_uncertainty()
        state["cleanup_complete"] = True
        self._pdf_producer = None
        if failure is not None:
            raise OwnerError("owned_pdf_producer_observation_incomplete") from None
        output = []
        for name in ("pdf-producer-stdout.bin", "pdf-producer-stderr.bin"):
            with (directory / name).open("rb") as stream:
                raw = stream.read(MAX_PDF_PRODUCER_LOG_BYTES + 1)
            if len(raw) > MAX_PDF_PRODUCER_LOG_BYTES:
                raise OwnerError("owned_pdf_producer_log_limit")
            output.append(raw)
        L._write_new(
            directory / "pdf-producer-returned.json",
            L._pack(
                {
                    "command_id": command_id,
                    "epoch": self._origin_handoff._epoch,
                    "process_returned": True,
                    "exit_code": returned,
                    "resource_cleanup_complete": True,
                    "stdout_bytes": len(output[0]),
                    "stderr_bytes": len(output[1]),
                    "stdout_sha256": hashlib.sha256(output[0]).hexdigest(),
                    "stderr_sha256": hashlib.sha256(output[1]).hexdigest(),
                },
                key,
            ),
        )
        return {
            "returncode": returned,
            "stdout": output[0],
            "stderr": output[1],
            "owned_producer_terminal": True,
        }

    def _retain_pdf_uncertainty(self):
        while True:
            time.sleep(0.02)  # Same owner/resources retained; no false cleanup.

    def release(self):
        self._owner()
        if self._pdf_producer is not None:
            raise OwnerError("owned_pdf_resources_still_pending")
        if self._dual_count_probe is not None:
            while self._dual_count_probe.resources_released is not True:
                # A failed/unknown native revoke is not replayed or discarded.
                # This owner remains responsible for the retained COM graph.
                try:
                    self.com.PumpWaitingMessages()
                except BaseException:
                    pass
                time.sleep(0.02)
            self._dual_count_probe = None
        if self._dual_document_call is not None:
            while self._dual_document_call.resources_released is not True:
                try:
                    self.com.PumpWaitingMessages()
                except BaseException:
                    pass
                time.sleep(0.02)
            self._dual_document_call = None
        self.raw = self.unknown = None


def _run_owned_child(ticket):
    """Native code is reachable only through this explicit private child entry."""
    root, key, intent = _context(ticket)
    if intent["source_sha256"] != _source_binding():
        raise OwnerError("owner_source_binding_changed")
    # A parent lost before registration must not leave an undiscoverable CAD
    # launch. Only this new helper's own HANDLE identity is inspected here.
    deadline = time.monotonic() + 2.0
    while True:
        try:
            registered = _registration(root, key, intent)
            current = L._identity(L._kernel().GetCurrentProcess())
            if registered != current or current["alive"] is not True:
                raise OwnerError("owner_original_registration_mismatch")
            break
        except FileNotFoundError:
            if time.monotonic() >= deadline:
                raise OwnerError("owner_abandoned_before_registration") from None
            time.sleep(0.01)
    # Import on this STA before creating the MTA thread: comtypes import has its
    # own initialization and must not choose an accidental apartment there.
    writer = L.EventWriter(root, key, intent)
    writer.append("owner_com_setup_started", native_writer_admitted=False)
    sys.coinit_flags = 2
    import comtypes  # noqa: F401
    import pythoncom

    from xpedition_cli.designer_native_launch import (
        LaunchError,
        constrain_plan_to_owner,
        prepare_and_launch,
    )

    intent["plan"] = constrain_plan_to_owner(intent["plan"], root)

    pythoncom.CoInitializeEx(2)
    shared, runtime_ready, stop_broker = {}, threading.Event(), threading.Event()

    def broker_worker():
        runtime = None
        initialized = False
        try:
            pythoncom.CoInitializeEx(0)
            initialized = True
            runtime = create_native_runtime(ticket.epoch)
            shared["runtime"] = runtime
            runtime_ready.set()
            stop_broker.wait()
            while not runtime.close():
                runtime.handoff.changed.wait(0.05)
                runtime.handoff.changed.clear()
            shared["broker_cleanup_complete"] = True
        except BaseException as error:
            shared["broker_error"] = _failure_facts(error, "owner.broker_mta", None)
            runtime_ready.set()
            if runtime is not None:
                # The apartment must outlive uncertain references. Do not
                # uninitialize or discard a failed cleanup's retained graph.
                shared["broker_cleanup_failed"] = True
                threading.Event().wait()
        finally:
            runtime = None
            if initialized:
                pythoncom.CoUninitialize()

    broker = threading.Thread(target=broker_worker, name="DesignerHostBrokerMTA")

    application = lease = grant = boundary = None
    close_result_pending = None
    unresolved = False
    dispatched = False
    stage = "owner.broker_start"
    broker.start()
    try:
        writer.append("owner_started", native_writer_admitted=False)
        if intent.get("mode") == PUBLIC_MODE:
            boundary = _BoundaryTrace(root)
        deadline = time.monotonic() + 10
        while not runtime_ready.is_set() and time.monotonic() < deadline:
            pythoncom.PumpWaitingMessages()
            time.sleep(0.01)
        if "runtime" not in shared:
            raise OwnerError("native_broker_start_incomplete")
        runtime = shared["runtime"]
        if intent.get("mode") == PUBLIC_MODE:
            from xpedition_cli.designer_owner_dispatch import validate_editing_scope

            validate_editing_scope(intent.get("editing_scope")).require(effects=["session"])
        # The dedicated launcher owns all startup resources and returns the
        # original process HANDLE lineage; no caller-reported PID can arm it.
        try:
            stage = "owner.dedicated_launch"
            writer.append("native_launch_started", dispatched=None)
            lease = prepare_and_launch(intent["plan"], runtime.endpoint, ticket.epoch)
            dispatched = True
        except LaunchError as error:
            lease = getattr(error, "lease", None)
            dispatched = getattr(error, "dispatched", None)
            writer.append(
                "native_launch_incomplete",
                dispatched=dispatched,
                original_launch_lease_retained=lease is not None,
            )
            if lease is None and dispatched is not False:
                # A trustworthy launcher must expose its partial lease. With no
                # lease there is no safe cleanup claim; keep this owner alive.
                while True:
                    pythoncom.PumpWaitingMessages()
                    time.sleep(0.05)
            raise OwnerError("dedicated_native_launch_incomplete") from None
        deadline = time.monotonic() + 10.0
        stage = "owner.original_worker_generation"
        writer.append("worker_generation_resolution_started")
        while True:
            try:
                expected_value = lease.resolve_generation()
                break
            except LaunchError as error:
                if (
                    str(error) != "fresh_worker_generation_not_yet_unambiguous"
                    or time.monotonic() >= deadline
                ):
                    raise OwnerError("original_worker_generation_incomplete") from None
                pythoncom.PumpWaitingMessages()
                time.sleep(0.02)
        expected = ExpectedGeneration(
            expected_value["pid"],
            expected_value["birth_filetime_100ns"],
            expected_value["executable"],
        )
        runtime.handoff.arm(expected, lease)
        generation_receipt = _launch_evidence(root, key, expected, lease)
        writer.append(
            "native_launch_owned",
            generation=generation_receipt,
            original_handle_pinned=True,
            startup_resources_owned=True,
        )
        stage = "owner.host_handoff"
        writer.append("handoff_wait_started", handoff=runtime.handoff.snapshot())
        handoff_deadline, wait_reported = time.monotonic() + 15.0, False
        quit_requested = False
        processed = set()
        handoff_reported = False
        while True:
            pythoncom.PumpWaitingMessages()
            generation = lease.snapshot()
            if not expected.matches(generation, require_alive=False):
                raise OwnerError("native_original_generation_changed")
            if generation["alive"] is False:
                writer.append(
                    "native_process_exit_observed",
                    original_handle_signalled=True,
                    generation=generation_receipt,
                    launcher_exit_observed=generation.get("launcher_alive") is False
                    if type(generation.get("launcher_alive")) is bool
                    else None,
                )
                break
            if application is None and not quit_requested:
                stats = runtime.handoff.snapshot()
                if stats["accepted"] == 1 and stats["inflight"] == 0:
                    stage = "owner.sta_materialization"
                    writer.append("owner_materialization_started")
                    if boundary is not None:
                        boundary.emit("owner.materialization", "begin")
                    application = NativeApplication.from_handoff(
                        runtime.handoff,
                        pythoncom,
                        expected,
                        lease,
                        None
                        if getattr(lease, "project_path", None) is None
                        else str(lease.project_path),
                    )
                    application.boundary = boundary
                    application.runtime_root = root
                    if boundary is not None:
                        boundary.emit("owner.materialization", "end", verified=True)
                    stage = "owner.initial_native_probe"
                    writer.append("native_probe_started")
                    if boundary is not None:
                        boundary.emit("owner.initial_probe", "begin")
                    observed = application.probe()
                    if boundary is not None:
                        boundary.emit("owner.initial_probe", "end", canonical_stable=True)
                    writer.append(
                        "native_connection_observed",
                        **observed,
                        source="fixed_host_Application_and_authenticated_ncalrpc",
                        native_writer_admitted=False,
                    )
                    if intent.get("mode") == PUBLIC_MODE:
                        from xpedition_cli import designer_owner_dispatch as D

                        stage = "owner.bridge_scope_validation"
                        D.validate_editing_scope(intent.get("editing_scope"))
                        stage = "owner.bridge_grant_admission"
                        grant = D.issue_from_owner(application, runtime.handoff, ticket.epoch)
                        stage = "owner.automation_configuration"
                        writer.append(
                            "native_automation_configuration_started",
                            automation=application.automation_snapshot(),
                        )
                        configured = D.configure_automation(grant, intent["editing_scope"])
                        writer.append(
                            "native_automation_configuration_observed", automation=configured
                        )
                        writer.append(
                            "public_dispatch_ready",
                            same_held_application=True,
                            owner_admission=_capacity_owner_snapshot(
                                application, ticket.epoch, generation, 0
                            ),
                        )
                    stage = "owner.ready"
                elif stats["rejected"] and not handoff_reported:
                    writer.append("native_handoff_incomplete", handoff=stats)
                    handoff_reported = True
                if (
                    application is None
                    and time.monotonic() >= handoff_deadline
                    and not wait_reported
                ):
                    writer.append(
                        "handoff_wait_budget_observed",
                        handoff=stats,
                        action="continue_same_owner_no_replay",
                        call_cancelled=False,
                    )
                    wait_reported = True
            stop_path = root / "stop.json"
            stop = False
            if stop_path.exists():
                request, _ = L._read(stop_path, key)
                if request != {"epoch": ticket.epoch, "request": "stop_at_safe_boundary"}:
                    raise OwnerError("owner_stop_epoch_mismatch")
                stop = True
            if application is not None and not stop and not quit_requested:
                directories = sorted((root / "commands").iterdir())
                public = intent.get("mode") == PUBLIC_MODE
                if len(directories) > (MAX_PUBLIC_COMMANDS if public else MAX_COMMANDS):
                    raise OwnerError("owner_command_limit")
                for directory in directories:
                    if directory.name in processed or not (directory / "request.json").exists():
                        continue
                    request, _ = (
                        _command_read(directory / "request.json", key)
                        if public
                        else L._read(directory / "request.json", key)
                    )
                    if public:
                        _validate_adapter_request(request, directory.name, ticket.epoch)
                        processed.add(directory.name)
                        command_writer = _CommandJournal(directory, key, request)
                        command_writer.append("command_received", method=request["method"])
                        command_writer.append("command_started", method=request["method"])
                        stage = "owner.adapter." + request["method"]
                        try:
                            if boundary is not None:
                                boundary.select_command(directory.name)
                            if request["method"] == "close":
                                D.authorize(
                                    application,
                                    intent["editing_scope"],
                                    "close",
                                    request["params"],
                                    request["sdk_operation_id"],
                                    request["authorization_binding"],
                                )
                                if application.readonly_quarantine is not None:
                                    try:
                                        application.require_empty_project()
                                    except Exception:
                                        raise D.DispatchError(
                                            "readonly_quarantine_requires_empty_close"
                                        ) from None
                                answer = application.quit_empty()
                                writer.append(
                                    "public_dispatch_stopping",
                                    reason="original_empty_quit_returned",
                                )
                                close_result_pending = (
                                    command_writer,
                                    answer,
                                    dict(generation),
                                    _capacity_owner_snapshot(
                                        application, ticket.epoch, generation, len(processed)
                                    ),
                                )
                            else:
                                answer = D.dispatch(
                                    grant,
                                    request["method"],
                                    request["params"],
                                    editing_scope=intent["editing_scope"],
                                    sdk_operation_id=request["sdk_operation_id"],
                                    authorization_binding=request["authorization_binding"],
                                )
                                command_writer.append(
                                    "command_returned",
                                    result=answer,
                                    owner_admission=_capacity_owner_snapshot(
                                        application, ticket.epoch, generation, len(processed)
                                    ),
                                )
                        except BaseException as error:
                            adapter_error = _adapter_failure(error)
                            if application._lifecycle_origin_command_id == directory.name:
                                lifecycle = application.lifecycle_snapshot()
                                adapter_error["details"]["release_progress"] = {
                                    "designer": {
                                        "save_count": int(lifecycle["save_call_attempted"]),
                                        "close_count": int(lifecycle["close_call_attempted"]),
                                        "lifecycle": lifecycle,
                                    },
                                    "layout": {"save_count": 0, "close_count": 0},
                                }
                            command_writer.append(
                                "command_failed",
                                adapter_error=adapter_error,
                                automatic_replay=False,
                                owner_admission=_capacity_owner_snapshot(
                                    application,
                                    ticket.epoch,
                                    generation,
                                    len(processed),
                                    uncertain=getattr(error, "dispatch_started", True) is not False,
                                ),
                            )
                            # A rejected preflight did not alter the connection.
                            # Any uncertain native dispatch ends normal service.
                            if not getattr(error, "dispatch_started", True):
                                continue
                            if D.continue_quarantined_observation_rejection(grant, error):
                                # The original command_failed receipt is intact.
                                # No new global event is needed: the existing
                                # quarantine and all write restrictions remain.
                                continue
                            quarantine = D.quarantine_rejected_creation(
                                grant,
                                error,
                                sdk_operation_id=request["sdk_operation_id"],
                                command_id=directory.name,
                            )
                            if quarantine is not None:
                                writer.append("public_readonly_quarantine", quarantine=quarantine)
                                continue
                            raise OwnerError("public_owner_adapter_command_incomplete") from None
                        if request["method"] == "close":
                            quit_requested = True
                            D.close(grant)
                            grant = None
                            application.release()
                            application = None
                            stop_broker.set()
                            break
                        continue
                    if (
                        request
                        != {
                            "command_id": directory.name,
                            "epoch": ticket.epoch,
                            "command": request.get("command"),
                        }
                        or request.get("command") not in COMMANDS
                    ):
                        raise OwnerError("fixed_owner_command_binding_invalid")
                    processed.add(directory.name)
                    writer.append(
                        "command_received", command_id=directory.name, command=request["command"]
                    )
                    writer.append(
                        "command_started", command_id=directory.name, command=request["command"]
                    )
                    stage = "owner.command." + request["command"]
                    try:
                        if request["command"] == "quit_empty":
                            answer = application.quit_empty()
                        elif request["command"] == "save_close_project":
                            answer = application.save_close_project(
                                lambda phase, facts, command_id=directory.name: writer.append(
                                    phase, command_id=command_id, lifecycle=facts
                                )
                            )
                        elif request["command"] == "read_project":
                            answer = application.read_project()
                        else:
                            answer = application.probe()
                        writer.append(
                            "command_returned",
                            command_id=directory.name,
                            command=request["command"],
                            result=answer,
                        )
                    except BaseException as error:
                        writer.append(
                            "command_failed",
                            command_id=directory.name,
                            reason="native_command_incomplete",
                            quit_dispatched=application.quit_attempted,
                            quit_reply_received=application.quit_returned,
                            lifecycle=application.lifecycle_snapshot(),
                            automatic_replay=False,
                            failure=_failure_facts(error, stage, dispatched),
                        )
                        raise OwnerError("native_command_incomplete") from None
                    if request["command"] == "quit_empty":
                        quit_requested = True
                        application.release()
                        application = None
                        stop_broker.set()
                        break
            time.sleep(0.02)
        stage = "owner.reference_cleanup"
        if grant is not None:
            from xpedition_cli import designer_owner_dispatch as D

            D.close(grant)
            grant = None
        if application is not None:
            application.release()
            application = None
        stop_broker.set()
        _join_broker(broker, pythoncom.PumpWaitingMessages, shared, writer)
        if shared.get("broker_cleanup_complete") is not True:
            raise OwnerError("native_broker_cleanup_incomplete")
        if boundary is not None:
            boundary.close()
        stage = "owner.launch_resource_cleanup"
        _retain_launch_resources(
            lease,
            dispatched,
            pythoncom.PumpWaitingMessages,
            on_failure=lambda error: writer.append(
                "owner_cleanup_failed", failure=_failure_facts(error, stage, dispatched)
            ),
        )
        lease = None
        if close_result_pending is not None:
            command_writer, answer, original_generation, admission = close_result_pending
            command_writer.append(
                "command_returned",
                owner_admission=admission,
                result={
                    "closed": True,
                    "domain": "schematic",
                    "pid": original_generation["pid"],
                    "process_identity": {
                        "pid": original_generation["pid"],
                        "creation_time_100ns": original_generation["birth_filetime_100ns"],
                        "executable": original_generation["executable"],
                    },
                    "process_absence_observed": True,
                    "owned_launcher_exit_observed": True,
                    "command_returned": True,
                    "normal_exit_observed": True,
                    "lifecycle": {
                        "empty_document_observed": True,
                        "quit_count": 1,
                        "quit_dispatched": True,
                        "quit_reply_received": True,
                        "save_count": 0,
                        "close_count": 0,
                    },
                    "startup_resources_released": True,
                    **answer,
                    "process_exit_verified": True,
                },
            )
        writer.append(
            "owner_cleanup_complete",
            native_exit_verified=True,
            reference_slots_cleared=True,
            startup_resources_released=True,
        )
        return 0
    except BaseException as error:
        unresolved = True  # Telemetry I/O must never disable native retention.
        try:
            writer.append(
                "owner_unresolved",
                failure=_failure_facts(error, stage, dispatched),
                native_boundary=boundary.snapshot() if boundary is not None else None,
                automation=application.automation_snapshot() if application is not None else None,
                automatic_replay=False,
            )
        except BaseException:
            pass
    finally:
        # This block runs after the exception handler is left. An exception
        # traceback must not retain App references throughout the exit wait.
        if unresolved:
            if dispatched is not False and lease is None:
                # Missing provenance is not proof of no dispatch. Do not exit
                # merely because an incomplete launch report could not persist.
                while True:
                    try:
                        pythoncom.PumpWaitingMessages()
                    except BaseException:
                        pass
                    time.sleep(0.05)
            if grant is not None:
                from xpedition_cli import designer_owner_dispatch as D

                D.close(grant)
                grant = None
            if application is not None:
                application.release()
                application = None
            stop_broker.set()
            _join_broker(broker, pythoncom.PumpWaitingMessages, shared, writer)
            # Do not abandon a live launched process. Source files and original
            # HANDLE remain owned even after all application references drop.
            if lease is not None:
                _retain_launch_resources(
                    lease,
                    dispatched,
                    pythoncom.PumpWaitingMessages,
                    on_failure=lambda error: writer.append(
                        "owner_cleanup_failed",
                        failure=_failure_facts(
                            error, "owner.unresolved_launch_resource_cleanup", dispatched
                        ),
                    ),
                )
        if boundary is not None and not boundary.close_attempted:
            try:
                boundary.close()
            except BaseException:
                pass  # This path already has an unresolved/failed owner outcome.
        application = lease = grant = boundary = None
        pythoncom.CoUninitialize()
    return 21


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--owned-child", action="store_true")
    parser.add_argument("--root", required=True)
    parser.add_argument("--epoch", required=True)
    arguments = parser.parse_args(argv)
    if not arguments.owned_child:
        return 21
    try:
        return _run_owned_child(OwnerTicket(Path(arguments.root), arguments.epoch))
    except BaseException:
        print("designer_native_owner_incomplete", file=sys.stderr)
        return 21


if __name__ == "__main__":
    # Source child execution must use the canonical package's classes. Running
    # this file creates __main__.NativeApplication, which is a distinct type
    # from the exact package type required by the dispatch admission boundary.
    from xpedition_cli.designer_native_owner import main as canonical_main

    raise SystemExit(canonical_main())
