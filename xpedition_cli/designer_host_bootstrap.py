"""Bounded, diagnostic-only Designer host Application handoff.

The only script is our immutable template. The installed host's Application
named-item contract, not an arbitrary caller-supplied PID, establishes its source.
RPC call origin is independently checked against a retained, proven startup
generation. Host getters run after the command returns, in the same MTA as the
retained host object. No COM reference, capability, runtime path or script hash
leaves this helper. No writer admission, state update or CAD exit is performed.

This does not isolate against arbitrary malicious code already executing inside
the trusted CAD process. Such code could supply another application's object.
The fixed script cannot substitute that object or acquire another CAD instance.
"""

from __future__ import annotations

import copy
import hashlib
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import designer_window_identity as W

TEMPLATE_RESOURCE = "xpedition_cli/resources/designer-host-identity.vbs"
TEMPLATE_SHA256 = "112ec1a31bb974889ace484a2782b6c1c974352552a2841bdfb737679a582b88"
PLACEHOLDER = b"__BROKER_OBJREF__"
APP_IID = "ea4abd71-84b0-11ce-8237-00001b4d36b5"
APP_PROXY_IID = "8e0609dd-208c-42b6-a1b0-00daa3e45423"
COMMANDS_IID = "aa7f70a6-2f0e-417c-a1d2-c1621d728eb0"
PROJECT_IID = "956dd308-d5ed-40a1-83f2-84113b8937c1"
MAX_READ_OPERATIONS = 64
MAX_SOFT_SECONDS = 25.0
MAX_CALLBACK_SECONDS = 5.0
MAX_SCRIPT_BYTES = 65536


class HostObservation:
    """One synchronized, detached observation; null means no observation yet.

    This object owns telemetry only. It neither owns nor cancels a COM call.
    A qualified operation executor must own the execution and its cleanup.
    """

    def __init__(self):
        self.lock = threading.RLock()
        self.revision = 0
        self.result = {
            "status": "incomplete",
            "authority": False,
            "authority_granted": False,
            "source": "fixed_host_Application_named_item_and_verified_ncalrpc_origin",
            "phase": "bootstrap.setup",
            "reason": None,
            "dispatch": {"attempted": False, "returned": None, "result_bool": None},
            "candidate_lookups": 0,
            "read_operations": 0,
            "process_inventories": [],
            "callbacks": dict.fromkeys(("received", "completed", "accepted", "rejected")),
            "callback_observation": "unobserved",
            "callback_rpc": None,
            "host_object": None,
            "script_integrity": None,
            "cleanup": dict.fromkeys(
                (
                    "closed_to_new_calls",
                    "reference_variables_cleared",
                    "files_removed",
                    "worker_completed",
                    "mta_uninitialized",
                    "callback_references_quiescent",
                    "inflight_callbacks_at_close",
                )
            ),
            "cleanup_observation": "unobserved",
            "runtime_capability_or_path_exported": False,
            "engineering_write_attempted": False,
        }

    @contextmanager
    def edit(self):
        with self.lock:
            try:
                yield self.result
            finally:
                self.revision += 1

    def snapshot(self):
        with self.lock:
            value = copy.deepcopy(self.result)
            value["snapshot"] = {"revision": self.revision, "consistency": "synchronized"}
            return value


class BootstrapGap(ValueError):
    """Only fixed local reasons may be exposed, never external exception text."""


def _template_bytes():
    value = (
        Path(__file__).with_name("resources").joinpath("designer-host-identity.vbs").read_bytes()
    )
    if (
        hashlib.sha256(value).hexdigest() != TEMPLATE_SHA256
        or value.count(PLACEHOLDER) != 1
        or not value.isascii()
    ):
        raise BootstrapGap("bootstrap_template_integrity_failed")
    return value


def template_identity():
    """Preview reads only the packaged template; no COM, runtime file or token."""
    value = _template_bytes()
    return {
        "status": "verified",
        "resource": TEMPLATE_RESOURCE,
        "sha256": TEMPLATE_SHA256,
        "bytes": len(value),
        "placeholder_count": 1,
        "engineering_writes": False,
    }


class Budget:
    def __init__(self, result, clock, *, observation=None):
        self.result, self.clock, self.started = result, clock, clock()
        self.calls = 0
        self.observation = observation
        self.lock = observation.lock if observation is not None else threading.RLock()
        self.cancelled = threading.Event()

    @contextmanager
    def edit(self):
        with self.lock:
            try:
                yield self.result
            finally:
                if self.observation is not None:
                    self.observation.revision += 1

    def read(self, point, operation):
        with self.edit():
            self.result["phase"] = point
            if self.cancelled.is_set():
                raise BootstrapGap("bootstrap_observer_cancelled_after_total_budget")
            if self.calls >= MAX_READ_OPERATIONS:
                raise BootstrapGap("bootstrap_read_budget_exhausted")
            if self.clock() - self.started > MAX_SOFT_SECONDS:
                raise BootstrapGap("bootstrap_soft_budget_exhausted")
            self.calls += 1
            self.result["read_operations"] = self.calls
        value = operation()
        if self.clock() - self.started > MAX_SOFT_SECONDS:
            raise BootstrapGap("bootstrap_soft_budget_exhausted")
        return value


def _header(packet, expected_pid):
    if (
        not isinstance(packet, dict)
        or packet.get("status") != "verified"
        or packet.get("domain") != "schematic"
        or packet.get("authority_granted") is not False
        or packet.get("project_scope_verified") is not True
        or not isinstance(packet.get("scope_id"), str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", str(packet.get("scope_id")))
        or packet.get("verification_basis") != "original_sdk_start_receipt_and_registry"
        or not re.fullmatch(r"op-[A-Za-z0-9_-]{1,80}", str(packet.get("startup_operation_id")))
        or not re.fullmatch(r"[a-f0-9]{64}", str(packet.get("source_sha256")))
    ):
        raise BootstrapGap("bootstrap_trusted_original_start_packet_required")
    rows = packet.get("generations")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 2:
        raise BootstrapGap("bootstrap_original_generation_set_invalid")
    if any(not isinstance(row, dict) for row in rows):
        raise BootstrapGap("bootstrap_original_generation_row_invalid")
    if [row.get("role") for row in rows].count("application") != 1 or any(
        row.get("role") not in ("application", "launcher") for row in rows
    ):
        raise BootstrapGap("bootstrap_original_generation_roles_invalid")
    for row in rows:
        birth = row.get("creation_time_100ns")
        if (
            type(row.get("pid")) is not int
            or not 0 < row["pid"] < 2**32
            or not isinstance(birth, str)
            or not birth.isascii()
            or not birth.isdecimal()
            or int(birth) <= 0
        ):
            raise BootstrapGap("bootstrap_original_generation_incomplete")
        W._path(row.get("executable"))
    if len({row["pid"] for row in rows}) != len(rows):
        raise BootstrapGap("bootstrap_original_generation_duplicate")
    worker = next(row for row in rows if row["role"] == "application")
    if type(expected_pid) is not int or expected_pid != worker["pid"]:
        raise BootstrapGap("bootstrap_requested_pid_differs_from_original_worker")
    if Path(worker["executable"]).name.lower() != "viewdraw.exe":
        raise BootstrapGap("bootstrap_expected_designer_image_required")
    return dict(worker), W._path(packet.get("project"))


def _matches(expected, actual):
    try:
        return (
            isinstance(actual, dict)
            and type(actual.get("pid")) is int
            and actual["pid"] == expected["pid"]
            and actual.get("alive") is True
            and actual.get("creation_time_100ns") == expected["creation_time_100ns"]
            and W._path(actual.get("executable")) == W._path(expected["executable"])
        )
    except (ValueError, TypeError):
        return False


def _designer_inventory():
    from .native_process import _startup_snapshot

    return _startup_snapshot("schematic")


def _check_inventory(packet, expected, reader, budget, phase):
    """Require a complete bounded OS census before selecting or using a candidate.

    These observations constrain discovery; they do not prove which process
    implements an arbitrary COM object. The later RPC/host-script handoff remains
    necessary. A process may appear after a census; dispatched facts are never
    changed back to 'not dispatched' because of a later failure.
    """
    from .native_process import MAX_STARTUP_PROCESSES

    report = {
        "phase": phase,
        "snapshot_reader_calls": 0,
        "observation": "inflight",
        "complete": None,
        "current_process_count": None,
        "original_allowed_role_count": len(packet["generations"]),
        "original_worker_present": None,
        "matched_current_generation_count": None,
        "all_current_generations_allowed": None,
        "maximum_process_count": MAX_STARTUP_PROCESSES,
    }
    with budget.edit() as result:
        index = len(result["process_inventories"])
        result["process_inventories"].append(copy.deepcopy(report))

    def collect():
        report["snapshot_reader_calls"] += 1
        with budget.edit() as result:
            result["process_inventories"][index] = copy.deepcopy(report)
        return reader()

    try:
        current = budget.read("bootstrap.inventory." + phase, collect)
    except BootstrapGap:
        report["observation"] = "unobserved"
        with budget.edit() as result:
            result["process_inventories"][index] = copy.deepcopy(report)
        raise
    except Exception as exc:
        report["observation"] = "unavailable"
        with budget.edit() as result:
            result["process_inventories"][index] = copy.deepcopy(report)
        raise BootstrapGap("bootstrap_current_viewdraw_inventory_unavailable") from exc
    try:
        return _validate_inventory(current, report, packet, expected)
    finally:
        report["observation"] = "observed"
        with budget.edit() as result:
            result["process_inventories"][index] = copy.deepcopy(report)


def _validate_inventory(current, report, packet, expected):
    from .native_process import MAX_STARTUP_PROCESSES

    report["complete"] = False
    if not isinstance(current, dict):
        raise BootstrapGap("bootstrap_current_viewdraw_inventory_not_complete_mapping")
    report["current_process_count"] = len(current)
    if len(current) > MAX_STARTUP_PROCESSES:
        raise BootstrapGap("bootstrap_current_viewdraw_inventory_exceeds_limit")
    seen = set()
    for pid, row in current.items():
        if (
            type(pid) is not int
            or not 0 < pid < 2**32
            or not isinstance(row, dict)
            or set(row) != {"identity", "parent_pid"}
            or type(row.get("parent_pid")) is not int
            or row["parent_pid"] < 0
        ):
            raise BootstrapGap("bootstrap_current_viewdraw_inventory_row_incomplete")
        identity = row["identity"]
        if not isinstance(identity, dict) or set(identity) != {
            "pid",
            "executable",
            "creation_time_100ns",
        }:
            raise BootstrapGap("bootstrap_current_viewdraw_inventory_generation_incomplete")
        birth = identity.get("creation_time_100ns")
        if (
            type(identity.get("pid")) is not int
            or identity["pid"] != pid
            or identity["pid"] in seen
            or not isinstance(birth, str)
            or not birth.isascii()
            or not birth.isdecimal()
            or int(birth) <= 0
        ):
            raise BootstrapGap("bootstrap_current_viewdraw_inventory_pid_or_birth_invalid")
        try:
            W._path(identity.get("executable"))
        except (ValueError, TypeError) as exc:
            raise BootstrapGap("bootstrap_current_viewdraw_inventory_image_unavailable") from exc
        seen.add(identity["pid"])
    report["complete"] = True
    allowed = {row["pid"]: row for row in packet["generations"]}
    report["matched_current_generation_count"] = 0
    report["all_current_generations_allowed"] = False
    report["original_worker_present"] = expected["pid"] in current
    if expected["pid"] not in current:
        raise BootstrapGap("bootstrap_original_worker_missing_from_complete_inventory")
    for pid, row in current.items():
        permitted = allowed.get(pid)
        if permitted is None or not _matches(permitted, {**row["identity"], "alive": True}):
            raise BootstrapGap("bootstrap_current_viewdraw_generation_not_originally_owned")
        report["matched_current_generation_count"] += 1
    report["original_roles_not_current_count"] = len(set(allowed) - set(current))
    report["all_current_generations_allowed"] = True
    return report


@contextmanager
def _process_pin(expected):
    import ctypes
    from ctypes import wintypes as w

    from .native_process import ProcessHandle

    with ProcessHandle("schematic", expected["pid"], expected) as pinned:

        def read():
            born, exited = pinned._times()
            size, image = w.DWORD(32768), ctypes.create_unicode_buffer(32768)
            if not pinned.api.QueryFullProcessImageNameW(
                pinned.handle, 0, image, ctypes.byref(size)
            ):
                raise BootstrapGap("bootstrap_retained_process_image_unavailable")
            return {
                "pid": pinned.identity["pid"],
                "creation_time_100ns": str(born),
                "executable": image.value,
                "alive": exited == 0 and not pinned.exited(),
            }

        yield read


def _same(left, right, reason):
    value = left == right
    if type(value) is not bool or value is not True:
        raise BootstrapGap(reason)


def _raw(value, com):
    raw = getattr(value, "_oleobj_", value)
    if type(raw) is not com.TypeIIDs[com.IID_IDispatch]:
        raise BootstrapGap("bootstrap_native_dispatch_type_unqualified")
    return raw


def _role(raw, com, budget, *, candidate=False):
    info = budget.read("host.role.typeinfo", lambda: raw.GetTypeInfo(0, 0))
    library, _ = budget.read("host.role.typelib", info.GetContainingTypeLib)
    attrs = budget.read("host.role.library_attributes", library.GetLibAttr)
    library_name = budget.read("host.role.library_name", lambda: library.GetDocumentation(-1)[0])
    iid = W._guid(budget.read("host.role.interface_attributes", info.GetTypeAttr).iid)
    if (
        W._guid(attrs[0]) != W.VIEWDRAW_GUID
        or library_name != "ViewDraw"
        or attrs[3:5] != (151, 0)
        or iid not in (APP_IID, APP_PROXY_IID)
    ):
        raise BootstrapGap("bootstrap_current_viewdraw_151_role_required")
    return {
        "library_guid": W._guid(attrs[0]),
        "library_name": library_name,
        "library_major": attrs[3],
        "library_minor": attrs[4],
        "library_lcid": attrs[1],
        "interface_iid": iid,
    }


def _invoke(raw, com, budget, name, dispid, result_type, arguments=(), values=()):
    actual = budget.read("host.member." + name, lambda: raw.GetIDsOfNames(0, name))
    if type(actual) is not int or actual != dispid:
        raise BootstrapGap("bootstrap_declared_member_mapping_differs")
    return budget.read(
        "host.invoke." + name,
        lambda: raw.InvokeTypes(dispid, 0, 1, (result_type, 0), arguments, *values),
    )


def _native_project(raw, com, budget):
    opened = _invoke(raw, com, budget, "IsProjectOpened", 295, com.VT_BOOL)
    if type(opened) is not bool:
        raise BootstrapGap("bootstrap_native_project_open_not_boolean")
    path = None
    if opened:
        data = _raw(_invoke(raw, com, budget, "GetProjectData", 228, com.VT_DISPATCH), com)
        budget.read(
            "host.project.role",
            lambda: data.QueryInterface("{" + PROJECT_IID + "}", com.IID_IUnknown),
        )
        path = W._path(_invoke(data, com, budget, "GetProjectFilePath", 3, com.VT_BSTR))
    return {"open": opened, "path": path}


class HostBinding:
    """Pure protocol core: callbacks cannot invoke a CAD business getter."""

    def __init__(self, expected, generation_reader, com, budget, rpc_reader, resolver):
        self.expected, self.generation_reader, self.com, self.budget = (
            expected,
            generation_reader,
            com,
            budget,
        )
        self.rpc_reader, self.resolver = rpc_reader, resolver
        self.raw = self.unknown = self.dispatch_owner = None
        self.armed = self.closed = False
        self.received = self.completed = self.accepted = self.rejected = 0
        self.event = threading.Event()
        self.error = None
        self.rpc = {
            "api_attempts": 0,
            "api_returns": 0,
            "status": None,
            "protocol_sequence": None,
            "client_pid": None,
        }
        self.state_lock = budget.lock
        self.epoch = object()
        self.inflight = 0
        self.inflight_at_close = 0
        self.cleanup_complete = False
        with self.state_lock:
            self._publish_locked()

    def _publish_locked(self):
        self.budget.result["callbacks"] = {
            name: getattr(self, name) for name in ("received", "completed", "accepted", "rejected")
        }
        self.budget.result["callback_observation"] = "synchronized_live"
        rpc = copy.deepcopy(self.rpc)
        rpc["observation"] = (
            "returned"
            if rpc["api_returns"]
            else "inflight"
            if rpc["api_attempts"]
            else "not_called"
        )
        self.budget.result["callback_rpc"] = rpc
        if self.budget.observation is not None:
            self.budget.observation.revision += 1

    def snapshot(self):
        with self.state_lock:
            return {
                "counters": {
                    name: getattr(self, name)
                    for name in ("received", "completed", "accepted", "rejected")
                },
                "rpc": copy.deepcopy(self.rpc),
                "inflight": self.inflight,
                "closed": self.closed,
            }

    def _require_lease(self, epoch):
        with self.state_lock:
            self._publish_locked()
            if (
                self.closed
                or not self.armed
                or self.budget.cancelled.is_set()
                or self.epoch is not epoch
                or self.received != 1
            ):
                raise BootstrapGap("bootstrap_callback_lease_expired")

    def accept(self, incoming):
        leased = False
        try:
            with self.state_lock:
                self.received += 1
                if (
                    self.closed
                    or self.budget.cancelled.is_set()
                    or not self.armed
                    or self.received != 1
                ):
                    raise BootstrapGap("bootstrap_callback_not_armed_or_duplicate")
                epoch = self.epoch
                self.inflight += 1
                leased = True
                self.event.clear()
                self._publish_locked()
            peer = self.budget.read("callback.rpc_origin", lambda: self.rpc_reader(self.rpc))
            self._require_lease(epoch)
            if (
                not isinstance(peer, dict)
                or peer.get("status") != 0
                or peer.get("protocol_sequence") != 3
                or type(peer.get("client_pid")) is not int
                or peer["client_pid"] != self.expected["pid"]
            ):
                raise BootstrapGap("bootstrap_callback_origin_not_expected_native_process")
            before = self.budget.read("callback.generation.before", self.generation_reader)
            self._require_lease(epoch)
            if not _matches(self.expected, before):
                raise BootstrapGap("bootstrap_callback_generation_before_mismatch")
            owner, raw = self.budget.read(
                "callback.owned_dispatch", lambda: self.resolver(incoming)
            )
            self._require_lease(epoch)
            raw = _raw(raw, self.com)
            unknown = self.budget.read(
                "callback.canonical_iunknown", lambda: raw.QueryInterface(self.com.IID_IUnknown)
            )
            self._require_lease(epoch)
            after = self.budget.read("callback.generation.after", self.generation_reader)
            if not _matches(self.expected, after):
                raise BootstrapGap("bootstrap_callback_generation_after_mismatch")
            with self.state_lock:
                self._require_lease(epoch)
                self.dispatch_owner, self.raw, self.unknown = owner, raw, unknown
                self.accepted += 1
                self._publish_locked()
            return True
        except BootstrapGap as exc:
            with self.state_lock:
                if self.error is None:
                    self.error = str(exc)
                self.rejected += 1
                self._publish_locked()
            return False
        except Exception:
            with self.state_lock:
                if self.error is None:
                    self.error = "bootstrap_callback_observation_failed"
                self.rejected += 1
                self._publish_locked()
            return False
        finally:
            with self.state_lock:
                self.completed += 1
                if leased:
                    self.inflight -= 1
                if self.inflight == 0:
                    self.event.set()
                self._publish_locked()

    def readback(self, project, candidate_unknown=None):
        if self.closed or self.accepted != 1 or self.rejected or self.received != 1:
            raise BootstrapGap("bootstrap_host_binding_not_complete")
        before = self.budget.read("host.generation.before", self.generation_reader)
        if not _matches(self.expected, before):
            raise BootstrapGap("bootstrap_host_generation_before_mismatch")
        _same(
            self.unknown,
            self.budget.read(
                "host.canonical.before", lambda: self.raw.QueryInterface(self.com.IID_IUnknown)
            ),
            "bootstrap_retained_host_canonical_changed",
        )
        role = _role(self.raw, self.com, self.budget)
        document = _native_project(self.raw, self.com, self.budget)
        if document["open"] and document["path"] != project:
            raise BootstrapGap("bootstrap_host_project_differs_from_authorized_project")
        repeated = _native_project(self.raw, self.com, self.budget)
        if document != repeated:
            raise BootstrapGap("bootstrap_host_native_project_changed_during_read")
        _same(
            self.unknown,
            self.budget.read(
                "host.canonical.after", lambda: self.raw.QueryInterface(self.com.IID_IUnknown)
            ),
            "bootstrap_retained_host_canonical_changed",
        )
        after = self.budget.read("host.generation.after", self.generation_reader)
        if not _matches(self.expected, after):
            raise BootstrapGap("bootstrap_host_generation_after_mismatch")
        compared = None
        if candidate_unknown is not None:
            value = self.unknown == candidate_unknown
            compared = value if type(value) is bool else None
        return {
            "role": role,
            "canonical_before_after_equal": True,
            "candidate_canonical_equal": compared,
            "candidate_is_only_bootstrap_transport": True,
            "native_project_open": document["open"],
            "native_project_matches_authorized": True if document["open"] else None,
            "native_project_path_exported": False,
            "host_execution_generation": {
                "pid": after["pid"],
                "birth_filetime_100ns": after["creation_time_100ns"],
                "alive_rechecked": after["alive"],
                "image_matches_original_start": True,
            },
            "application_server_pid_observed": False,
        }

    def close(self):
        with self.state_lock:
            already_closed = self.closed
            self.closed, self.armed = True, False
            self.epoch = object()
            if not already_closed:
                self.inflight_at_close = self.inflight
                self.cleanup_complete = self.inflight == 0
            if self.inflight and self.error is None:
                self.error = "bootstrap_callback_inflight_during_cleanup"
            # Keep temporary Python owners while clearing slots atomically, so
            # COM Release/destructors run outside the state lock.
            retired = self.raw, self.unknown, self.dispatch_owner
            self.raw = self.unknown = self.dispatch_owner = None
            self._publish_locked()
            self.budget.result.setdefault("cleanup", {})["closed_to_new_calls"] = True
            self.budget.result["cleanup_observation"] = "in_progress"
        del retired


def _native_runtime(expected, generation_reader, com, budget):
    import ctypes
    from ctypes import wintypes as w

    import comtypes
    from comtypes import GUID, IUnknown, hresult
    from comtypes.automation import VARIANT, VT_BYREF, VT_DISPATCH, VT_VARIANT, IDispatch

    class Attributes(ctypes.Structure):
        _fields_ = [
            ("Version", w.UINT),
            ("Flags", w.ULONG),
            ("ServerLength", w.ULONG),
            ("ServerName", ctypes.c_void_p),
            ("ClientLength", w.ULONG),
            ("ClientName", ctypes.c_void_p),
            ("AuthenticationLevel", w.ULONG),
            ("AuthenticationService", w.ULONG),
            ("NullSession", w.BOOL),
            ("KernelModeCaller", w.BOOL),
            ("ProtocolSequence", w.ULONG),
            ("Locality", w.ULONG),
            ("ClientPID", w.HANDLE),
            ("CallStatus", w.ULONG),
            ("CallType", w.ULONG),
            ("LocalAddress", ctypes.c_void_p),
            ("OpNum", w.USHORT),
            ("InterfaceUuid", GUID),
        ]

    if ctypes.sizeof(Attributes) != 112:
        raise BootstrapGap("bootstrap_rpc_attributes_abi_unqualified")

    def rpc(progress):
        api = ctypes.WinDLL("rpcrt4").RpcServerInqCallAttributesW
        api.argtypes, api.restype = (ctypes.c_void_p, ctypes.c_void_p), w.ULONG
        value = Attributes()
        value.Version, value.Flags = 2, 0x10
        with budget.edit() as result:
            progress["api_attempts"] += 1
            result["callback_rpc"] = copy.deepcopy(progress)
            result["callback_rpc"]["observation"] = "inflight"
        status = int(api(None, ctypes.byref(value)))
        with budget.edit() as result:
            progress["api_returns"] += 1
            progress["status"] = status
            progress["protocol_sequence"] = int(value.ProtocolSequence) if status == 0 else None
            progress["client_pid"] = int(value.ClientPID or 0) if status == 0 else None
            result["callback_rpc"] = copy.deepcopy(progress)
            result["callback_rpc"]["observation"] = "returned"
        return {
            "status": status,
            "protocol_sequence": int(value.ProtocolSequence) if status == 0 else None,
            "client_pid": int(value.ClientPID or 0) if status == 0 else None,
        }

    def resolve(incoming):
        if type(incoming) is not VARIANT or incoming.vt not in (VT_DISPATCH, VT_BYREF | VT_VARIANT):
            raise BootstrapGap("bootstrap_host_argument_type_invalid")
        owned = VARIANT()
        try:
            api = ctypes.OleDLL("oleaut32").VariantCopyInd
            api.argtypes, api.restype = (
                (ctypes.POINTER(VARIANT), ctypes.POINTER(VARIANT)),
                ctypes.HRESULT,
            )
            api(ctypes.byref(owned), ctypes.byref(incoming))
            if owned.vt != VT_DISPATCH:
                raise BootstrapGap("bootstrap_host_argument_not_dispatch")
            dispatch = owned.value
            if not isinstance(dispatch, ctypes.POINTER(IDispatch)) or not dispatch:
                raise BootstrapGap("bootstrap_host_dispatch_null_or_untyped")
            raw = com.ObjectFromAddress(
                ctypes.cast(dispatch, ctypes.c_void_p).value, com.IID_IDispatch
            )
            return dispatch, raw
        finally:
            owned.value = None

    binding = HostBinding(expected, generation_reader, com, budget, rpc, resolve)
    callback_finished = threading.Event()

    class Broker(comtypes.COMObject):
        _com_interfaces_ = [IDispatch]

        def IDispatch_GetIDsOfNames(self, this, riid, names, count, lcid, ids):
            if count == 1 and names[0].casefold() == "bind":
                ids[0] = 1
                return hresult.S_OK
            return hresult.DISP_E_UNKNOWNNAME

        def IDispatch_Invoke(self, this, member, riid, lcid, flags, params, output, excep, argerr):
            if member != 1 or params[0].cArgs != 1 or params[0].cNamedArgs:
                return hresult.E_INVALIDARG
            try:
                accepted = binding.accept(params[0].rgvarg[0])
                if output:
                    output[0].value = accepted
                # Bind is a BOOL acknowledgement. Authentication refusal is a
                # false result, not a script-host COM exception/dialog. The
                # diagnostic still rejects its recorded binding.error.
                return hresult.S_OK
            finally:
                # Signal after the handler has finished its response work; no
                # CAD getter runs on this incoming callback thread.
                callback_finished.set()

    class Runtime:
        def __init__(self):
            self.binding = binding
            self.callback_finished = callback_finished
            self.broker = Broker()
            self.pointer = self.broker.QueryInterface(IDispatch)
            api = ctypes.OleDLL("ole32").CreateObjrefMoniker
            api.argtypes, api.restype = (
                (ctypes.POINTER(IUnknown), ctypes.POINTER(ctypes.POINTER(IUnknown))),
                ctypes.HRESULT,
            )
            self.moniker_pointer = ctypes.POINTER(IUnknown)()
            api(self.pointer, ctypes.byref(self.moniker_pointer))
            self.moniker = com.ObjectFromAddress(
                ctypes.cast(self.moniker_pointer, ctypes.c_void_p).value, com.IID_IMoniker
            )
            self.endpoint = self.moniker.GetDisplayName(com.CreateBindCtx(0), None)

        def close(self):
            binding.close()
            self.endpoint = None
            self.moniker = self.moniker_pointer = self.pointer = self.broker = None

    return Runtime()


class LockedScript:
    """Unique script held against write/delete, with pinned non-reparse ancestors."""

    def __init__(self, root, payload):
        self.root, self.payload = Path(root), payload
        self.payload_digest = hashlib.sha256(payload).digest()
        self.path = self.directory = None
        self.handles, self.file_handle = [], None
        self.child_handle = None
        self.created_file_id = None
        self.facts = {"locked": False, "rechecked": False, "same_file": False, "bytes_match": False}
        self.removed = False

    def __enter__(self):
        import ctypes
        from ctypes import wintypes as w

        self.ctypes, self.w = ctypes, w
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)

        class Information(ctypes.Structure):
            _fields_ = [
                ("attributes", w.DWORD),
                ("created", w.FILETIME),
                ("accessed", w.FILETIME),
                ("written", w.FILETIME),
                ("volume", w.DWORD),
                ("size_high", w.DWORD),
                ("size_low", w.DWORD),
                ("links", w.DWORD),
                ("index_high", w.DWORD),
                ("index_low", w.DWORD),
            ]

        self.Information = Information
        for name, args, result in (
            (
                "CreateFileW",
                (w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE),
                w.HANDLE,
            ),
            ("CloseHandle", (w.HANDLE,), w.BOOL),
            ("GetFileInformationByHandle", (w.HANDLE, ctypes.POINTER(Information)), w.BOOL),
            (
                "WriteFile",
                (w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p),
                w.BOOL,
            ),
            (
                "ReadFile",
                (w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p),
                w.BOOL,
            ),
            ("FlushFileBuffers", (w.HANDLE,), w.BOOL),
            ("SetFilePointerEx", (w.HANDLE, ctypes.c_longlong, ctypes.c_void_p, w.DWORD), w.BOOL),
            (
                "SetFileInformationByHandle",
                (w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD),
                w.BOOL,
            ),
        ):
            method = getattr(self.api, name)
            method.argtypes, method.restype = args, result
        if not self.root.is_absolute() or not re.fullmatch(
            r"[A-Za-z]:[\\/][A-Za-z0-9_./\\-]+", str(self.root)
        ):
            raise BootstrapGap("bootstrap_runtime_root_must_be_absolute_plain_ascii")
        if len(self.payload) > MAX_SCRIPT_BYTES or not self.payload.isascii():
            raise BootstrapGap("bootstrap_runtime_script_size_or_encoding_invalid")
        try:
            for parent in reversed((self.root, *self.root.parents)):
                self.handles.append(self._open(parent, 0x80, 1, 3, 0x02200000))
                if self._information(self.handles[-1]).attributes & 0x400:
                    raise BootstrapGap("bootstrap_runtime_ancestor_is_reparse_point")
            self.directory = Path(tempfile.mkdtemp(prefix="designer-host-", dir=self.root))
            self.child_handle = self._open(self.directory, 0x10080, 1, 3, 0x02200000)
            self.handles.append(self.child_handle)
            self.path = self.directory / "identity.vbs"
            if len(str(self.path)) > 240:
                raise BootstrapGap("bootstrap_runtime_script_path_too_long")
            writer = self._open(self.path, 0xC0000000, 1, 1, 0x00200000)
            try:
                self.created_file_id = self._file_id(self._information(writer))
                written = w.DWORD()
                if (
                    not self.api.WriteFile(
                        writer, self.payload, len(self.payload), ctypes.byref(written), None
                    )
                    or written.value != len(self.payload)
                    or not self.api.FlushFileBuffers(writer)
                ):
                    raise BootstrapGap("bootstrap_runtime_script_write_failed")
            finally:
                self.api.CloseHandle(writer)
            self.file_handle = self._open(self.path, 0x80000000, 1, 3, 0x00200000)
            self.recheck()
            self.facts["locked"] = True
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def _open(self, path, access, sharing, creation, flags):
        handle = self.api.CreateFileW(str(path), access, sharing, None, creation, flags, None)
        if handle in (None, self.ctypes.c_void_p(-1).value):
            raise BootstrapGap("bootstrap_runtime_file_or_directory_lock_failed")
        return handle

    def _information(self, handle):
        info = self.Information()
        if not self.api.GetFileInformationByHandle(handle, self.ctypes.byref(info)):
            raise BootstrapGap("bootstrap_runtime_file_information_unavailable")
        return info

    @staticmethod
    def _file_id(info):
        return info.volume, info.index_high, info.index_low

    def recheck(self):
        info = self._information(self.file_handle)
        if (
            info.attributes & 0x400
            or info.links != 1
            or self._file_id(info) != self.created_file_id
        ):
            raise BootstrapGap("bootstrap_runtime_script_identity_changed")
        if (info.size_high << 32) | info.size_low != len(self.payload):
            raise BootstrapGap("bootstrap_runtime_script_size_changed")
        if not self.api.SetFilePointerEx(self.file_handle, 0, None, 0):
            raise BootstrapGap("bootstrap_runtime_script_seek_failed")
        buffer, read = self.ctypes.create_string_buffer(len(self.payload)), self.w.DWORD()
        if (
            not self.api.ReadFile(
                self.file_handle, buffer, len(self.payload), self.ctypes.byref(read), None
            )
            or read.value != len(self.payload)
            or buffer.raw != self.payload
            or hashlib.sha256(buffer.raw).digest() != self.payload_digest
        ):
            raise BootstrapGap("bootstrap_runtime_script_bytes_changed")
        self.facts.update(rechecked=True, same_file=True, bytes_match=True)

    def _dispose(self, handle):
        value = self.w.BOOL(True)
        if not self.api.SetFileInformationByHandle(
            handle, 4, self.ctypes.byref(value), self.ctypes.sizeof(value)
        ):
            raise BootstrapGap("bootstrap_runtime_owned_file_cleanup_failed")

    def __exit__(self, *args):
        failure = None
        try:
            if self.file_handle:
                if not self.api.CloseHandle(self.file_handle):
                    raise BootstrapGap("bootstrap_runtime_script_handle_close_failed")
                self.file_handle = None
            if self.created_file_id is not None:
                deleter = self._open(self.path, 0x10080, 7, 3, 0x00200000)
                try:
                    if self._file_id(self._information(deleter)) != self.created_file_id:
                        raise BootstrapGap("bootstrap_runtime_cleanup_target_changed")
                    self._dispose(deleter)
                finally:
                    self.api.CloseHandle(deleter)
            if self.child_handle is not None:
                self._dispose(self.child_handle)
            elif self.directory is not None:
                raise BootstrapGap("bootstrap_runtime_child_directory_not_pinned")
            self.removed = True
        except Exception as exc:
            failure = exc
        finally:
            for handle in reversed(self.handles):
                if not self.api.CloseHandle(handle):
                    failure = BootstrapGap("bootstrap_runtime_directory_handle_close_failed")
            self.handles.clear()
        if failure is not None:
            raise BootstrapGap("bootstrap_runtime_cleanup_incomplete") from failure


def _preload_com():
    # Frozen hooks may have initialized the main thread as STA. Loading bindings
    # here prevents comtypes' first-import initializer from changing the worker.
    import comtypes  # noqa: F401
    import pythoncom

    return pythoncom


def observe(
    *args,
    **kwargs,
):
    """Refuse the former one-shot/daemon execution path without doing any IO.

    The new execution owner is a non-CAD prototype, not a qualified adapter for
    this call. Returning a bounded response must never destroy a live owner.
    """
    observation = HostObservation()
    with observation.edit() as result:
        result["reason"] = "bootstrap_execution_owner_required"
        result["execution_owner"] = "unqualified"
    return observation.snapshot()


def _execute_owned(
    ownership,
    runtime_dir,
    *,
    expected_pid,
    candidate_factory,
    com_module=None,
    pin_factory=None,
    runtime_factory=None,
    script_guard_factory=None,
    inventory_reader=None,
    clock=time.monotonic,
    observation=None,
):
    """Synchronous execution core for an operation owner, never the CLI observer.

    This deliberately has no thread, join deadline or process-exit fallback. A
    blocking call blocks its owner. Native dispatch remains independently gated
    off; this core's in-process tests do not qualify the new subprocess owner.
    """
    observation = observation if observation is not None else HostObservation()
    result = observation.result
    budget = Budget(result, clock, observation=observation)
    try:
        expected, project = _header(ownership, expected_pid)
        template = template_identity()
        with observation.edit():
            result["template"] = template
        if not callable(candidate_factory):
            raise BootstrapGap("bootstrap_candidate_factory_required")
        com = com_module if com_module is not None else _preload_com()
    except BootstrapGap as exc:
        with observation.edit():
            result["reason"] = str(exc)
        return observation.snapshot()
    except Exception:
        with observation.edit():
            result["reason"] = "bootstrap_setup_failed"
        return observation.snapshot()

    def worker():
        runtime = script = candidate = candidate_unknown = raw = manager = None
        initialized = False
        mta_can_uninitialize = True
        try:
            com.CoInitializeEx(0)
            initialized = True
            with (pin_factory or _process_pin)(expected) as generation:
                if not _matches(expected, budget.read("bootstrap.generation.before", generation)):
                    raise BootstrapGap("bootstrap_generation_before_dispatch_mismatch")
                _check_inventory(
                    ownership,
                    expected,
                    inventory_reader or _designer_inventory,
                    budget,
                    "before_candidate_lookup",
                )
                with observation.edit():
                    result["candidate_lookups"] += 1
                candidate = budget.read("bootstrap.candidate_lookup", candidate_factory)
                raw = _raw(candidate, com)
                _role(raw, com, budget, candidate=True)
                candidate_unknown = budget.read(
                    "bootstrap.candidate_iunknown", lambda: raw.QueryInterface(com.IID_IUnknown)
                )
                manager = _raw(
                    _invoke(raw, com, budget, "CommandsManager", 220, com.VT_DISPATCH), com
                )
                budget.read(
                    "bootstrap.command_manager_role",
                    lambda: manager.QueryInterface("{" + COMMANDS_IID + "}", com.IID_IUnknown),
                )
                runtime = (runtime_factory or _native_runtime)(expected, generation, com, budget)
                endpoint = runtime.endpoint
                if (
                    not isinstance(endpoint, str)
                    or not re.fullmatch(r"(?i)objref:[A-Za-z0-9+/=]+:", endpoint)
                    or len(endpoint) > MAX_SCRIPT_BYTES // 2
                ):
                    raise BootstrapGap("bootstrap_runtime_endpoint_shape_invalid")
                payload = _template_bytes().replace(PLACEHOLDER, endpoint.encode("ascii"))
                with (script_guard_factory or LockedScript)(runtime_dir, payload) as script:
                    runtime.binding.armed = True
                    script.recheck()
                    if not _matches(
                        expected, budget.read("bootstrap.generation.pre_dispatch", generation)
                    ):
                        raise BootstrapGap("bootstrap_generation_before_dispatch_mismatch")
                    actual = budget.read(
                        "bootstrap.execute_member",
                        lambda: manager.GetIDsOfNames(0, "ExecuteCommand"),
                    )
                    if type(actual) is not int or actual != 327:
                        raise BootstrapGap("bootstrap_execute_member_mapping_differs")
                    _check_inventory(
                        ownership,
                        expected,
                        inventory_reader or _designer_inventory,
                        budget,
                        "before_script_dispatch",
                    )

                    def dispatch():
                        with observation.edit():
                            result["dispatch"]["attempted"] = True
                        value = manager.InvokeTypes(
                            327,
                            0,
                            1,
                            (com.VT_BOOL, 0),
                            ((com.VT_BSTR, 1),),
                            "run " + str(script.path),
                        )
                        with observation.edit():
                            result["dispatch"].update(
                                returned=True, result_bool=value if type(value) is bool else None
                            )
                        return value

                    answer = budget.read("bootstrap.execute_fixed_script", dispatch)
                    if type(answer) is not bool or answer is not True:
                        raise BootstrapGap("bootstrap_execute_did_not_return_true")
                    if not getattr(runtime, "callback_finished", runtime.binding.event).wait(
                        MAX_CALLBACK_SECONDS
                    ):
                        raise BootstrapGap("bootstrap_host_callback_not_completed")
                    if runtime.binding.error:
                        raise BootstrapGap(runtime.binding.error)
                    host = runtime.binding.readback(project, candidate_unknown)
                    with observation.edit():
                        result["host_object"] = host
                    candidate_unknown = candidate = raw = manager = None
                    script.recheck()
                    with observation.edit():
                        result["script_integrity"] = dict(script.facts)
                with observation.edit():
                    result["cleanup"]["files_removed"] = script.removed
                    result.update(
                        status="observed",
                        reason="host_application_relation_observed_diagnostic_only",
                    )
        except BootstrapGap as exc:
            with observation.edit():
                result["status"], result["reason"] = "incomplete", str(exc)
        except Exception as exc:
            with observation.edit():
                result["status"], result["reason"] = (
                    "incomplete",
                    "bootstrap_native_or_runtime_observation_failed",
                )
                hr = getattr(exc, "hresult", None)
                if type(hr) is int:
                    result["hresult"] = f"0x{hr & 0xFFFFFFFF:08x}"
        finally:
            if runtime is not None:
                binding_snapshot = runtime.binding.snapshot()
                with observation.edit():
                    result["callbacks"] = binding_snapshot["counters"]
                    result["callback_rpc"] = binding_snapshot["rpc"]
                    result["cleanup_observation"] = "in_progress"
                try:
                    runtime.close()
                    with runtime.binding.state_lock:
                        closed = runtime.binding.closed is True
                        cleared = all(
                            getattr(runtime.binding, name) is None
                            for name in ("raw", "unknown", "dispatch_owner")
                        )
                        quiescent = runtime.binding.cleanup_complete is True
                        with observation.edit():
                            result["cleanup"].update(
                                callback_references_quiescent=quiescent,
                                inflight_callbacks_at_close=runtime.binding.inflight_at_close,
                            )
                    if not closed or not cleared:
                        raise BootstrapGap("bootstrap_runtime_reference_cleanup_failed")
                    with observation.edit():
                        result["cleanup"].update(
                            closed_to_new_calls=closed,
                            reference_variables_cleared=cleared and quiescent,
                            native_reference_release_result="not_observable_by_bindings",
                        )
                    if not quiescent:
                        mta_can_uninitialize = False
                        with observation.edit():
                            result["status"], result["reason"] = (
                                "incomplete",
                                "bootstrap_callback_inflight_during_cleanup",
                            )
                except Exception:
                    mta_can_uninitialize = False
                    with observation.edit():
                        result["status"], result["reason"] = (
                            "incomplete",
                            "bootstrap_runtime_reference_cleanup_failed",
                        )
            # These are all candidate-owned COM references, including paths that
            # failed before readback. Release in this MTA before CoUninitialize.
            candidate = candidate_unknown = raw = manager = None
            if initialized and mta_can_uninitialize:
                try:
                    com.CoUninitialize()
                    with observation.edit():
                        result["cleanup"]["mta_uninitialized"] = True
                except Exception:
                    with observation.edit():
                        result["status"], result["reason"] = (
                            "incomplete",
                            "bootstrap_mta_cleanup_failed",
                        )
            with observation.edit():
                result["cleanup"]["worker_completed"] = True
                result["cleanup_observation"] = "worker_finalized"

    worker()
    with observation.edit():
        result["read_operations"] = budget.calls
        result["elapsed_ms"] = max(0, round((clock() - budget.started) * 1000))
    return observation.snapshot()
