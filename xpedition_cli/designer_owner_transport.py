"""Private host-origin transport for a dedicated Designer owner process.

Importing this module initializes no COM and queries no process. The only native
entry builds our in-memory broker, never acquires/starts a CAD application. The
host supplies Application. An original duplicated process HANDLE, active ncalrpc
origin and the fixed startup-script contract are separate parts of its source.
RPC client PID alone does not identify an arbitrary Application's server.

The MTA callback authenticates and marshals only. The receiving owner STA must
consume the standard in-process stream and independently validate the object.
No object, endpoint, stream, raw address or HANDLE belongs in public telemetry.
"""

from __future__ import annotations

import copy
import ntpath
import threading
from dataclasses import dataclass, field


class TransportError(ValueError):
    """Fixed safe reason, never raw native exceptions or capabilities."""


_ENTRY_COUNTERS = (
    "names_query_count",
    "names_query_rejected",
    "names_query_failed",
    "names_query_inflight",
    "invoke_entry_count",
    "invoke_shape_rejected",
    "invoke_read_failed",
    "invoke_inflight",
    "recognized_offer_dispatched",
)


def _unobserved_entries():
    return {
        "status": "unobserved",
        "scope": "IDispatch.GetIDsOfNames_and_Invoke_only",
        "offer_counts_cover_all_com_entries": False,
        **dict.fromkeys(_ENTRY_COUNTERS),
    }


class BrokerDispatch:
    """Pure shape checks; synchronized counters never retain names or arguments.

    The native Runtime constructs this before exposing its broker. Merely
    constructing a Handoff leaves these observations unobserved/null. Other COM
    entries, including QueryInterface and AddRef, are outside this scope.
    """

    def __init__(self, handoff):
        self._handoff = handoff
        with handoff._lock:
            if handoff._broker_entries is not None:
                raise TransportError("broker_entry_monitor_already_constructed")
            handoff._broker_entries = {
                **_unobserved_entries(),
                "status": "synchronized_live",
                **dict.fromkeys(_ENTRY_COUNTERS, 0),
            }

    def _add(self, **increments):
        with self._handoff._lock:
            for key, value in increments.items():
                self._handoff._broker_entries[key] += value

    def names_query(self, count, name_reader):
        self._add(names_query_count=1, names_query_inflight=1)
        try:
            matched = count == 1 and name_reader().casefold() == "offerstartupapplication"
            if not matched:
                self._add(names_query_rejected=1)
            return matched
        except BaseException:
            self._add(names_query_failed=1)
            raise
        finally:
            self._add(names_query_inflight=-1)

    def invoke(self, member, count_reader, named_reader, arguments_reader):
        self._add(invoke_entry_count=1, invoke_inflight=1)
        try:
            # Preserve the existing rejection order: a wrong member does not
            # inspect DISPPARAMS, and an invalid shape never reads VARIANT slots.
            if member != 1:
                self._add(invoke_shape_rejected=1)
                return False, None
            if count_reader() != 2 or named_reader():
                self._add(invoke_shape_rejected=1)
                return False, None
            incoming, epoch_argument = arguments_reader()
            self._add(recognized_offer_dispatched=1)
            return True, self._handoff.offer(incoming, epoch_argument)
        except BaseException:
            self._add(invoke_read_failed=1)
            raise
        finally:
            self._add(invoke_inflight=-1)


@dataclass(frozen=True)
class ExpectedGeneration:
    pid: int
    birth_100ns: str
    executable: str = field(repr=False)

    def __post_init__(self):
        if (
            type(self.pid) is not int
            or not 0 < self.pid < 2**32
            or not isinstance(self.birth_100ns, str)
            or not self.birth_100ns.isascii()
            or not self.birth_100ns.isdecimal()
            or int(self.birth_100ns) <= 0
            or not isinstance(self.executable, str)
            or not ntpath.isabs(self.executable)
        ):
            raise TransportError("complete_expected_generation_required")

    def matches(self, actual, *, require_alive=True):
        return (
            isinstance(actual, dict)
            and actual.get("pid") == self.pid
            and actual.get("birth_filetime_100ns") == self.birth_100ns
            and isinstance(actual.get("executable"), str)
            and ntpath.normcase(ntpath.normpath(actual["executable"]))
            == ntpath.normcase(ntpath.normpath(self.executable))
            and (not require_alive or actual.get("alive") is True)
        )


class Handoff:
    """One authenticated offer; no application business getter is called here."""

    def __init__(self, epoch, rpc_reader, epoch_reader, marshal, discard, *, arm_wait_seconds=0):
        self._epoch = epoch
        self._rpc_reader, self._epoch_reader = rpc_reader, epoch_reader
        self._marshal, self._discard = marshal, discard
        self._lock = threading.Lock()
        self._lease = object()
        self._expected = self._pin = self._stream = None
        self._closed = False
        self._armed = threading.Event()
        self._arm_wait_seconds = arm_wait_seconds
        self._received = self._completed = self._accepted = self._rejected = self._inflight = 0
        self._reason = None
        self._failed_streams = []
        self._broker_entries = None
        self.changed = threading.Event()

    def arm(self, expected, pin):
        if type(expected) is not ExpectedGeneration or not expected.matches(pin.snapshot()):
            raise TransportError("original_handle_generation_mismatch")
        with self._lock:
            if self._closed or self._expected is not None or self._received > 1 or self._rejected:
                raise TransportError("host_offer_cannot_be_rearmed")
            self._expected, self._pin = expected, pin
            self._armed.set()

    def _valid(self, lease):
        return not self._closed and self._lease is lease

    def offer(self, incoming, epoch_argument):
        stream = None
        leased = False
        try:
            with self._lock:
                self._received += 1
                if self._closed or self._received != 1:
                    raise TransportError("host_offer_unarmed_closed_or_duplicate")
                lease = self._lease
                self._inflight += 1
                leased = True
            self.changed.set()
            # No argument is unwrapped while the owner resolves the original
            # spawn's finite lineage. A callback's own PID cannot fill this gap.
            if not self._armed.wait(self._arm_wait_seconds):
                raise TransportError("host_offer_unarmed_closed_or_duplicate")
            with self._lock:
                if not self._valid(lease) or self._expected is None:
                    raise TransportError("host_offer_cancelled_before_authentication")
                expected, pin = self._expected, self._pin
            # The same original HANDLE remains pinned across call attribution.
            if not expected.matches(pin.snapshot()):
                raise TransportError("host_generation_before_mismatch")
            peer = self._rpc_reader()
            if (
                not isinstance(peer, dict)
                or peer.get("status") != 0
                or peer.get("protocol_sequence") != 3
                or peer.get("client_pid") != expected.pid
            ):
                raise TransportError("active_ncalrpc_origin_mismatch")
            if self._epoch_reader(epoch_argument) != self._epoch:
                raise TransportError("host_offer_epoch_mismatch")
            with self._lock:
                if not self._valid(lease):
                    raise TransportError("host_offer_cancelled_before_materialization")
            stream = self._marshal(incoming)
            if stream is None:
                raise TransportError("host_application_marshal_missing")
            if not expected.matches(pin.snapshot()):
                raise TransportError("host_generation_after_mismatch")
            with self._lock:
                if not self._valid(lease):
                    raise TransportError("host_offer_cancelled_after_materialization")
                self._stream, stream = stream, None
                self._accepted += 1
            return True
        except TransportError as error:
            with self._lock:
                self._reason = self._reason or str(error)
                self._rejected += 1
            return False
        except BaseException:
            with self._lock:
                self._reason = self._reason or "host_offer_native_failure"
                self._rejected += 1
            return False
        finally:
            # Native releases are deliberately outside the observer lock.
            if stream is not None:
                try:
                    self._discard(stream)
                except BaseException:
                    with self._lock:
                        self._failed_streams.append(stream)
                        self._reason = self._reason or "unconsumed_marshal_release_failed"
            with self._lock:
                self._completed += 1
                if leased:
                    self._inflight -= 1
            self.changed.set()

    def take_stream(self):
        with self._lock:
            if (
                self._closed
                or self._inflight
                or self._received != 1
                or self._accepted != 1
                or self._rejected
                or self._stream is None
            ):
                raise TransportError("host_handoff_not_complete")
            stream, self._stream = self._stream, None
            return stream

    def close(self):
        with self._lock:
            self._closed = True
            self._lease = object()
            self._armed.set()
            retired, self._stream = self._stream, None
            inflight = self._inflight
        if retired is not None:
            try:
                self._discard(retired)
            except BaseException:
                with self._lock:
                    self._failed_streams.append(retired)
                    self._reason = self._reason or "unconsumed_marshal_release_failed"
        with self._lock:
            return inflight == 0 and not self._failed_streams

    def snapshot(self):
        with self._lock:
            return {
                "received": self._received,
                "callback_body_completed": self._completed,
                "accepted": self._accepted,
                "rejected": self._rejected,
                "inflight": self._inflight,
                "pending_original_launch_anchor": self._inflight > 0 and self._expected is None,
                "closed_to_new_calls": self._closed,
                "reason": self._reason,
                "marshal_cleanup_observation": "failed" if self._failed_streams else "unobserved",
                "startup_script_returned": None,
                "application_server_pid_observed": False,
                "offer_census_scope": "Handoff.offer_only_not_all_COM_entries",
                "broker_entries": copy.deepcopy(self._broker_entries)
                if self._broker_entries is not None
                else _unobserved_entries(),
            }


class OriginalHandlePin:
    """Owns only a duplicated original process HANDLE, never OpenProcess(PID)."""

    def __init__(self, handle, expected):
        from . import execution_lifecycle as L

        if type(handle) is not int or handle <= 0:
            raise TransportError("original_duplicated_handle_required")
        self._handle, self._lifecycle = handle, L
        self.expected = expected
        try:
            if not expected.matches(self.snapshot()):
                raise TransportError("original_duplicated_handle_generation_mismatch")
        except BaseException:
            self.close()
            raise

    def snapshot(self):
        if self._handle is None:
            raise TransportError("original_process_handle_closed")
        return self._lifecycle._identity(self._handle)

    def close(self):
        if self._handle is not None:
            handle, self._handle = self._handle, None
            if not self._lifecycle._kernel().CloseHandle(handle):
                raise TransportError("original_process_handle_close_failed")


def create_native_runtime(epoch):
    """Called inside the owner MTA only. Never runs at module import/preview."""
    import ctypes
    from ctypes import wintypes as w

    import comtypes
    import pythoncom
    from comtypes import GUID, IUnknown, hresult
    from comtypes.automation import VARIANT, VT_BSTR, VT_DISPATCH, IDispatch

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
        raise TransportError("rpc_attributes_v2_abi_unqualified")
    api = ctypes.WinDLL("rpcrt4").RpcServerInqCallAttributesW
    api.argtypes, api.restype = (ctypes.c_void_p, ctypes.c_void_p), w.ULONG
    copy_ind = ctypes.OleDLL("oleaut32").VariantCopyInd
    copy_ind.argtypes, copy_ind.restype = (
        (ctypes.POINTER(VARIANT), ctypes.POINTER(VARIANT)),
        ctypes.HRESULT,
    )

    def rpc():
        value = Attributes()
        value.Version, value.Flags = 2, 0x10
        status = int(api(None, ctypes.byref(value)))
        return {
            "status": status,
            "protocol_sequence": int(value.ProtocolSequence) if status == 0 else None,
            "client_pid": int(value.ClientPID or 0) if status == 0 else None,
        }

    def epoch_reader(incoming):
        if type(incoming) is not VARIANT:
            raise TransportError("host_epoch_variant_required")
        owned = VARIANT()
        try:
            copy_ind(ctypes.byref(owned), ctypes.byref(incoming))
            if owned.vt != VT_BSTR or not isinstance(owned.value, str):
                raise TransportError("host_epoch_bstr_required")
            return owned.value
        finally:
            owned.value = None

    def marshal(incoming):
        if type(incoming) is not VARIANT:
            raise TransportError("host_application_variant_required")
        owned = VARIANT()
        dispatch = raw = None
        try:
            copy_ind(ctypes.byref(owned), ctypes.byref(incoming))
            if owned.vt != VT_DISPATCH:
                raise TransportError("host_application_dispatch_required")
            dispatch = owned.value
            if not isinstance(dispatch, ctypes.POINTER(IDispatch)) or not dispatch:
                raise TransportError("host_application_typed_dispatch_required")
            raw = pythoncom.ObjectFromAddress(
                ctypes.cast(dispatch, ctypes.c_void_p).value, pythoncom.IID_IDispatch
            )
            return pythoncom.CoMarshalInterThreadInterfaceInStream(pythoncom.IID_IDispatch, raw)
        finally:
            raw = dispatch = None
            owned.value = None

    def discard(stream):
        # Unconsumed packets must release their marshal reference, not merely
        # drop the IStream Python wrapper. This runs in the source MTA.
        stream.Seek(0, 0)
        pythoncom.CoReleaseMarshalData(stream)

    handoff = Handoff(epoch, rpc, epoch_reader, marshal, discard, arm_wait_seconds=10.0)
    entries = None

    class Broker(comtypes.COMObject):
        _com_interfaces_ = [IDispatch]

        def IDispatch_GetIDsOfNames(self, this, riid, names, count, lcid, ids):
            if entries.names_query(count, lambda: names[0]):
                ids[0] = 1
                return hresult.S_OK
            return hresult.DISP_E_UNKNOWNNAME

        def IDispatch_Invoke(self, this, member, riid, lcid, flags, params, output, excep, argerr):
            recognized, accepted = entries.invoke(
                member,
                lambda: params[0].cArgs,
                lambda: params[0].cNamedArgs,
                lambda: (params[0].rgvarg[1], params[0].rgvarg[0]),
            )
            if not recognized:
                return hresult.E_INVALIDARG
            if output:
                output[0].value = accepted
            return hresult.S_OK

    class Runtime:
        def __init__(self):
            nonlocal entries
            self.handoff = handoff
            entries = BrokerDispatch(handoff)
            self.broker = Broker()
            self.pointer = self.broker.QueryInterface(IDispatch)
            create = ctypes.OleDLL("ole32").CreateObjrefMoniker
            create.argtypes, create.restype = (
                (ctypes.POINTER(IUnknown), ctypes.POINTER(ctypes.POINTER(IUnknown))),
                ctypes.HRESULT,
            )
            self.moniker_pointer = ctypes.POINTER(IUnknown)()
            create(self.pointer, ctypes.byref(self.moniker_pointer))
            self.moniker = pythoncom.ObjectFromAddress(
                ctypes.cast(self.moniker_pointer, ctypes.c_void_p).value, pythoncom.IID_IMoniker
            )
            self.endpoint = self.moniker.GetDisplayName(pythoncom.CreateBindCtx(0), None)

        def close(self):
            quiet = handoff.close()
            if not quiet:
                return False
            self.endpoint = None
            self.moniker = self.moniker_pointer = self.pointer = self.broker = None
            return True

    return Runtime()


def transport_contract():
    """Pure safe description, never an enable flag or native observation."""
    return copy.deepcopy(
        {
            "native_default": "deny",
            "host_entry": "fixed_startup_Application_named_item",
            "broker_apartment": "MTA",
            "application_owner_apartment": "STA",
            "argument_transfer": "CoMarshalInterThreadInterfaceInStream",
            "peer_authentication": "active_ncalrpc_and_original_process_handle_generation",
            "native_qualification": "requires_session_specific_observation",
            "running_instance_lookup": False,
        }
    )
