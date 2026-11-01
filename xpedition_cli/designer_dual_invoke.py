"""Fixed ViewDraw151 Count probes and three document members; no generic API.

GIT carries canonical IUnknown only. Its comtypes recipient explicitly QIs the
requested interface. ITypeInfo.Invoke uses that exact interface and the formal
dual declaration from the held Application's verified library, never a child's
default IDispatch name table. Importing this module does not initialize COM.
"""

from __future__ import annotations

import copy
import hashlib
import os
import stat
import threading
import uuid
from pathlib import Path

DOCS_IID = "{BD2EDF77-7E28-11CE-822D-00001B4D36B5}"
DOCUMENT_IID = "{4ADEF4E2-690A-11CE-9261-0020C5E26659}"
SHEETS_IID = "{608CA7F4-A52A-4206-9580-3BEB17CE0073}"
LIBRARY_IID = "{00020402-0000-0000-C000-000000000046}"
_TARGETS = (
    ("documents", "Documents", 1, 2, DOCS_IID, 1),
    ("sheet_documents", "SchematicSheetDocuments", 212, 1, SHEETS_IID, 101),
)
MAX_TYPELIB_BYTES = 16 * 1024 * 1024
_DOCUMENT_MEMBERS = {
    "open_design": (DOCS_IID, "Open", 7, 0, True),
    "save_all": (DOCS_IID, "SaveAll", 10, 0, False),
    "save_document": (DOCUMENT_IID, "Save", 12, 64, False),
}


class DualInvokeError(ValueError):
    """Fixed safe reason; no external message, object address or cookie."""


def _guid(value):
    return str(uuid.UUID(str(value)))


def _same(left, right):
    value = left == right
    if left is None or right is None or type(value) is not bool or value is not True:
        raise DualInvokeError("dual_count_canonical_mismatch")


class _NativeBridge:
    """Inert until prepare(); every acquired native reference stays owned."""

    def __init__(self, probe):
        self.probe, self.refs, self.entries = probe, [], []
        self.native_git = self.typed_git = None
        self.uncertain = False

    def hold(self, point, operation):
        def acquire():
            value = operation()
            self.refs.append(value)  # Own before the soft post-call budget check.
            return value

        return self.probe.read(point, acquire)

    def prepare(self):
        import ctypes
        from ctypes.wintypes import DWORD

        if ctypes.sizeof(ctypes.c_void_p) != 8:
            raise DualInvokeError("dual_count_64_bit_process_required")

        import comtypes as C
        from comtypes import GUID, HRESULT, STDMETHOD, IUnknown
        from comtypes.automation import IDispatch
        from comtypes.typeinfo import ITypeLib

        class GIT(IUnknown):
            _iid_ = GUID("{00000146-0000-0000-C000-000000000046}")
            _methods_ = [
                STDMETHOD(
                    HRESULT,
                    "RegisterInterfaceInGlobal",
                    [ctypes.POINTER(IUnknown), ctypes.POINTER(GUID), ctypes.POINTER(DWORD)],
                ),
                STDMETHOD(HRESULT, "RevokeInterfaceFromGlobal", [DWORD]),
                STDMETHOD(
                    HRESULT,
                    "GetInterfaceFromGlobal",
                    [DWORD, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.POINTER(IUnknown))],
                ),
            ]

        class Documents(IDispatch):
            _iid_ = GUID(DOCS_IID)
            _methods_ = []  # No guessed business-method vtable declaration.

        class Sheets(IDispatch):
            _iid_ = GUID(SHEETS_IID)
            _methods_ = []

        class Document(IDispatch):
            _iid_ = GUID(DOCUMENT_IID)
            _methods_ = []

        self.ctypes, self.C, self.IUnknown, self.guid = ctypes, C, IUnknown, GUID
        self.interfaces = {
            DOCS_IID: Documents,
            SHEETS_IID: Sheets,
            DOCUMENT_IID: Document,
            LIBRARY_IID: ITypeLib,
        }
        com = self.probe.com
        self.native_git = self.hold(
            "git.create_pywin_table",
            lambda: com.CoCreateInstance(
                com.CLSID_StdGlobalInterfaceTable,
                None,
                com.CLSCTX_INPROC_SERVER,
                com.IID_IGlobalInterfaceTable,
            ),
        )
        self.typed_git = self.hold(
            "git.create_comtypes_table",
            lambda: C.CoCreateInstance(
                GUID("{00000323-0000-0000-C000-000000000046}"),
                interface=GIT,
                clsctx=C.CLSCTX_INPROC_SERVER,
            ),
        )

    def exact(self, raw, iid):
        com, ctypes, interface = self.probe.com, self.ctypes, self.interfaces[iid]
        original = self.hold(
            "bridge.source_canonical", lambda: raw.QueryInterface(com.IID_IUnknown)
        )
        # Verify support before GIT. Pywin32's GIT wrapper itself converts its
        # input to IUnknown, so registering a different IID here is unsafe.
        required = self.hold(
            "bridge.source_exact_qi", lambda: raw.QueryInterface(iid, com.IID_IUnknown)
        )
        _same(
            original,
            self.hold(
                "bridge.source_exact_canonical", lambda: required.QueryInterface(com.IID_IUnknown)
            ),
        )
        entry = {
            "cookie": None,
            "register_attempted": False,
            "register_returned": None,
            "revoke_attempted": False,
            "revoke_returned": None,
        }
        self.entries.append(entry)

        def register():
            entry["register_attempted"] = True
            try:
                entry["cookie"] = self.native_git.RegisterInterfaceInGlobal(
                    original, com.IID_IUnknown
                )
            except BaseException:
                # No cookie is not proof that no registration took effect.
                self.uncertain = True
                raise
            entry["register_returned"] = True
            if type(entry["cookie"]) is not int or not 0 < entry["cookie"] < 2**32:
                self.uncertain = True
                raise DualInvokeError("dual_count_git_cookie_invalid")
            return entry["cookie"]

        cookie = self.probe.read("git.register_canonical_unknown", register)
        unknown = ctypes.POINTER(self.IUnknown)()
        self.refs.append(unknown)

        def retrieve():
            try:
                self.typed_git.GetInterfaceFromGlobal(
                    cookie, ctypes.byref(self.IUnknown._iid_), ctypes.byref(unknown)
                )
            except Exception:
                if unknown:
                    self.uncertain = True  # Failed API with a nonnull out pointer.
                raise
            return unknown

        self.probe.read("git.retrieve_canonical_unknown", retrieve)
        if not unknown:
            raise DualInvokeError("dual_count_git_null_interface")
        typed = self.hold("bridge.recipient_exact_qi", lambda: unknown.QueryInterface(interface))
        address = ctypes.cast(typed, ctypes.c_void_p)
        if not address.value:
            raise DualInvokeError("dual_count_exact_interface_null")
        # Public interface-pointer API, not an address extracted from PyObject.
        wrapper_iid = com.IID_ITypeLib if iid == LIBRARY_IID else com.IID_IDispatch
        wrapper = self.hold(
            "bridge.public_pointer_roundtrip",
            lambda: com.ObjectFromAddress(address.value, wrapper_iid),
        )
        repeated = self.hold(
            "bridge.recipient_canonical", lambda: wrapper.QueryInterface(com.IID_IUnknown)
        )
        _same(original, repeated)
        return typed, wrapper, original

    def invoke_count(self, info, interface, dispid, observation):
        from ctypes.wintypes import UINT

        from comtypes.automation import DISPPARAMS, VARIANT

        ctypes = self.ctypes
        parameters, output, argerr = DISPPARAMS(), VARIANT(), UINT(0)
        self.refs.extend((parameters, output, argerr))
        address = ctypes.cast(interface, ctypes.c_void_p)

        # No parameters, no named arguments, and only PROPERTYGET is allowed.
        def invoke():
            # The budget's pre-call guard must run before dispatch is recorded.
            # Keep a normal native return even if its post-call guard expires.
            observation["invoke_attempted"] = True
            value = info.Invoke(
                address,
                dispid,
                2,
                ctypes.byref(parameters),
                ctypes.byref(output),
                None,
                ctypes.byref(argerr),
            )
            observation["invoke_returned"] = True
            return value

        self.probe.read("count.ITypeInfo_Invoke", invoke)
        vartype = int(output.vt)
        # Do not wrap or inspect an unexpected COM interface as a Count result.
        return vartype, output.value if vartype in (3, 22) else None

    def invoke_document(
        self,
        info,
        interface,
        dispid,
        argument,
        returns_document,
        observation,
        on_dispatch=None,
        on_return=None,
    ):
        """Owned ABI storage; caller selects only an already checked fixed spec."""
        from ctypes.wintypes import UINT

        from comtypes.automation import DISPPARAMS, VARIANT

        ctypes = self.ctypes
        parameters, output, argerr = DISPPARAMS(), VARIANT(), UINT(0)
        self.refs.extend((parameters, output, argerr))
        if argument is not None:
            arguments = (VARIANT * 1)()
            self.refs.append(arguments)
            arguments[0].value = argument
            if arguments[0].vt != 8:
                raise DualInvokeError("fixed_document_bstr_argument_required")
            parameters.rgvarg, parameters.cArgs = arguments, 1
        address = ctypes.cast(interface, ctypes.c_void_p)

        def invoke():
            if on_dispatch is not None:
                on_dispatch()  # Private owner facts only; no native action.
            observation["invoke_attempted"] = True
            value = info.Invoke(
                address,
                dispid,
                1,
                ctypes.byref(parameters),
                ctypes.byref(output) if returns_document else None,
                None,
                ctypes.byref(argerr),
            )
            observation["invoke_returned"] = True
            if on_return is not None:
                on_return()
            return value

        self.probe.read("document.ITypeInfo_Invoke", invoke)
        if not returns_document:
            return None  # VOID is a returned call, never persistence evidence.
        observation["return_vartype"] = int(output.vt)
        return self.independent_dispatch_return(output, DOCUMENT_IID)

    def independent_dispatch_return(self, output, iid):
        """Own a tagged native return before releasing its VARIANT storage."""
        if output.vt != 9 or not output._.c_void_p:
            raise DualInvokeError("fixed_document_nonnull_dispatch_return_required")
        # Read the documented tagged VARIANT interface field. No dynamic
        # GetBestInterface, global cache, repr parsing, or PyObject memory.
        returned = self.hold(
            "document.independent_return_reference",
            lambda: self.probe.com.ObjectFromAddress(
                output._.c_void_p, self.probe.com.IID_IDispatch
            ),
        )
        self.exact(returned, iid)
        return returned

    def release(self):
        self.probe.owner()
        failed = self.uncertain
        for entry in reversed(self.entries):
            if entry["cookie"] is None:
                continue
            if type(entry["cookie"]) is not int or not 0 < entry["cookie"] < 2**32:
                failed = True
                continue
            if entry["revoke_attempted"]:
                failed = failed or entry["revoke_returned"] is not True
                continue  # Unknown native cleanup is never replayed.
            entry["revoke_attempted"] = True
            try:
                self.native_git.RevokeInterfaceFromGlobal(entry["cookie"])
                entry["revoke_returned"] = True
            except Exception as error:
                entry["revoke_returned"] = False
                self.probe.cleanup_error(error, "git.RevokeInterfaceFromGlobal")
                failed = True
        if failed:
            return False  # Keep the entire owned graph, including failed cookie.
        self.refs.clear()
        self.native_git = self.typed_git = None
        return True


class ReadonlyCountProbe:
    """Same-owner one-shot observation. There is no caller-selected member."""

    def __init__(
        self,
        application,
        com,
        budget,
        *,
        owner_thread,
        owner_ident,
        installation_root=None,
        cad_executable=None,
        source_authorizer=None,
    ):
        self.application, self.com, self.budget = application, com, budget
        self.thread, self.ident = owner_thread, owner_ident
        self.bridge = None
        self.point = "not_started"
        self.started = False
        self.resources_released = None
        self.installation_root = installation_root
        self.cad_executable = cad_executable
        self.source_authorizer = source_authorizer
        self._local_pin = None
        self.result = {
            "scheme": "designer-fixed-dual-count-v1",
            "diagnostic_only": True,
            "native_writer_admitted": False,
            "status": "not_attempted",
            "counts": {},
            "type_library_source": {"status": "not_attempted", "pin": None, "regkind": None},
            "cleanup": {
                "status": "unobserved",
                "resources_released": None,
                "scope": "own_git_cookies_and_reference_slots_only",
                "native_release_acknowledged": None,
                "hresult": None,
                "failed_callpoint": None,
            },
        }

    def owner(self):
        if threading.current_thread() is not self.thread or threading.get_ident() != self.ident:
            raise DualInvokeError("dual_count_original_owner_sta_required")

    def read(self, point, operation):
        self.owner()
        self.point = point
        return self.budget.read("dual_count." + point, operation)

    def cleanup_error(self, error, point):
        hr = getattr(error, "hresult", None)
        self.result["cleanup"].update(
            status="error",
            resources_released=False,
            hresult=hr if type(hr) is int else None,
            failed_callpoint=point,
        )

    def _hold(self, point, operation):
        return self.bridge.hold(point, operation)

    def _canonical(self, raw):
        return self._hold(
            "application.canonical", lambda: raw.QueryInterface(self.com.IID_IUnknown)
        )

    def _registered_local_path(self, lcid):
        from . import designer_window_identity as W

        if (
            self.installation_root is None
            or self.cad_executable is None
            or not callable(self.source_authorizer)
        ):
            raise DualInvokeError("dual_count_local_source_scope_required")
        if (
            not Path(str(self.installation_root)).is_absolute()
            or not Path(str(self.cad_executable)).is_absolute()
        ):
            raise DualInvokeError("dual_count_absolute_installation_paths_required")
        home = Path(os.path.abspath(str(self.installation_root)))
        executable = Path(os.path.abspath(str(self.cad_executable)))
        expected_executable = home / "wv" / "win64" / "bin" / "viewdraw.exe"

        def key(path):
            return os.path.normcase(os.path.normpath(str(path)))

        if key(executable) != key(expected_executable):
            raise DualInvokeError("dual_count_original_installation_path_mismatch")
        path = executable.with_name("viewdraw.tlb")
        self.read("source.require_observe_scope", lambda: self.source_authorizer(path))
        registered = self.read(
            "source.QueryPathOfRegTypeLib",
            lambda: self.com.QueryPathOfRegTypeLib(
                self.com.MakeIID("{" + W.VIEWDRAW_GUID + "}"), 151, 0, lcid
            ),
        )
        if type(registered) is not str or not registered or any(ord(c) < 32 for c in registered):
            raise DualInvokeError("dual_count_registered_typelib_path_invalid")
        if key(Path(os.path.abspath(registered))) != key(path):
            raise DualInvokeError("dual_count_registered_typelib_outside_original_installation")
        if key(path.resolve(strict=True)) != key(path):
            raise DualInvokeError("dual_count_typelib_path_alias_refused")
        return path

    def _pin_typelib(self, path):
        def fingerprint(value):
            if (
                not stat.S_ISREG(value.st_mode)
                or value.st_nlink != 1
                or getattr(value, "st_file_attributes", 0) & 0x400
                or not 0 < value.st_size <= MAX_TYPELIB_BYTES
            ):
                raise DualInvokeError("dual_count_typelib_file_not_regular_bounded_single_link")
            return (
                value.st_dev,
                value.st_ino,
                value.st_size,
                value.st_mtime_ns,
                value.st_ctime_ns,
                getattr(value, "st_birthtime_ns", None),
            )

        before = fingerprint(path.stat(follow_symlinks=False))
        digest, count = hashlib.sha256(), 0
        with path.open("rb") as stream:
            opened = fingerprint(os.fstat(stream.fileno()))
            # Windows3.12 stat.ctime is the compatibility creation time while
            # fstat.ctime may be the change time. Keep both observations; only
            # compare equivalent identity/data fields across the two APIs.
            if opened[:4] != before[:4] or opened[5] != before[5]:
                raise DualInvokeError("dual_count_typelib_identity_changed_before_read")
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                count += len(block)
                if count > MAX_TYPELIB_BYTES:
                    raise DualInvokeError("dual_count_typelib_read_bound_exceeded")
                digest.update(block)
            if fingerprint(os.fstat(stream.fileno())) != opened:
                raise DualInvokeError("dual_count_typelib_changed_during_read")
        if fingerprint(path.stat(follow_symlinks=False)) != before or count != before[2]:
            raise DualInvokeError("dual_count_typelib_changed_after_read")
        return {
            "path": str(path),
            "device": before[0],
            "file_id": str(before[1]),
            "bytes": count,
            "mtime_ns": before[3],
            "ctime_ns": before[4],
            "handle_ctime_ns": opened[4],
            "birthtime_ns": before[5],
            "sha256": digest.hexdigest(),
        }

    def _verify_local_source(self):
        if self._local_pin is None:
            raise DualInvokeError("dual_count_local_typelib_not_bound")
        path = self._registered_local_path(self.result["library"]["lcid"])
        actual = self.read("source.stable_file_pin", lambda: self._pin_typelib(path))
        if actual != self._local_pin:
            raise DualInvokeError("dual_count_local_typelib_changed")

    def _load_local_library(self, remote_attributes):
        from comtypes.typeinfo import REGKIND_NONE, LoadTypeLibEx

        if REGKIND_NONE != 2:
            raise DualInvokeError("dual_count_no_registration_loader_contract_changed")
        path = self._registered_local_path(remote_attributes[1])
        initial = self.read("source.initial_file_pin", lambda: self._pin_typelib(path))
        self._local_pin = initial
        local = self._hold(
            "source.LoadTypeLibEx_REGKIND_NONE", lambda: LoadTypeLibEx(str(path), REGKIND_NONE)
        )
        attrs = self.read("source.local_GetLibAttr", local.GetLibAttr)
        if _guid(attrs.guid) != _guid(remote_attributes[0]) or (
            attrs.lcid,
            attrs.syskind,
            attrs.wMajorVerNum,
            attrs.wMinorVerNum,
        ) != tuple(remote_attributes[1:5]):
            raise DualInvokeError("dual_count_local_and_remote_library_mismatch")
        self.result["type_library_source"].update(
            status="observed", pin=copy.deepcopy(initial), regkind=REGKIND_NONE
        )
        self._verify_local_source()
        return local

    def _library(self):
        from . import designer_window_identity as W

        info = self._hold("application.GetTypeInfo", lambda: self.application.GetTypeInfo(0, 0))
        attr = self.read("application.GetTypeAttr", info.GetTypeAttr)
        if _guid(attr.iid) not in {
            "ea4abd71-84b0-11ce-8237-00001b4d36b5",
            "8e0609dd-208c-42b6-a1b0-00daa3e45423",
        }:
            raise DualInvokeError("dual_count_application_role_mismatch")
        library, _ = self._hold("application.GetContainingTypeLib", info.GetContainingTypeLib)
        attrs = self.read("application.GetLibAttr", library.GetLibAttr)
        name = self.read("application.library_name", lambda: library.GetDocumentation(-1)[0])
        if (
            _guid(attrs[0]) != W.VIEWDRAW_GUID
            or any(type(value) is not int for value in attrs[1:5])
            or attrs[2] != 3
            or attrs[3:5] != (151, 0)
            or name != "ViewDraw"
        ):
            raise DualInvokeError("dual_count_current_viewdraw_151_required")
        self.bridge.prepare()
        typed, _wrapper, _canonical = self.bridge.exact(library, LIBRARY_IID)
        actual = self.read("library.recipient_GetLibAttr", typed.GetLibAttr)
        if (
            _guid(actual.guid) != W.VIEWDRAW_GUID
            or any(
                type(value) is not int
                for value in (actual.syskind, actual.wMajorVerNum, actual.wMinorVerNum, actual.lcid)
            )
            or actual.syskind != 3
            or (actual.wMajorVerNum, actual.wMinorVerNum) != (151, 0)
            or actual.lcid != attrs[1]
        ):
            raise DualInvokeError("dual_count_recipient_library_mismatch")
        self.result["library"] = {
            "guid": W.VIEWDRAW_GUID,
            "major": 151,
            "minor": 0,
            "lcid": attrs[1],
        }
        return self._load_local_library(attrs), typed

    def _declaration(self, library, iid, dispid):
        info = self._hold(
            "declaration.GetTypeInfoOfGuid",
            lambda: library.GetTypeInfoOfGuid(self.bridge.guid(iid)),
        )
        attr = self.read("declaration.dispatch_GetTypeAttr", info.GetTypeAttr)
        if _guid(attr.guid) != _guid(iid) or attr.typekind != 4 or not (attr.wTypeFlags & 0x40):
            raise DualInvokeError("dual_count_formal_interface_mismatch")
        reference = self.read(
            "declaration.GetRefTypeOfImplType", lambda: info.GetRefTypeOfImplType(-1)
        )
        twin = self._hold("declaration.GetRefTypeInfo", lambda: info.GetRefTypeInfo(reference))
        attrs = self.read("declaration.dual_GetTypeAttr", twin.GetTypeAttr)
        if _guid(attrs.guid) != _guid(iid) or attrs.typekind != 3 or not 0 <= attrs.cFuncs <= 64:
            raise DualInvokeError("dual_count_formal_dual_mismatch")
        found = None
        for index in range(attrs.cFuncs):
            fd = self.read("declaration.GetFuncDesc", lambda index=index: twin.GetFuncDesc(index))
            names = self.read("declaration.GetNames", lambda fd=fd: twin.GetNames(fd.memid, 1))
            if names != ["Count"]:
                continue
            if (
                found is not None
                or fd.memid != dispid
                or fd.invkind != 2
                or fd.funckind != 1
                or fd.cParams != 1
                or fd.cParamsOpt != 0
                or fd.elemdescFunc.tdesc.vt != 25
            ):
                raise DualInvokeError("dual_count_count_signature_mismatch")
            param = fd.lprgelemdescParam[0]
            if (
                param._.paramdesc.wParamFlags != 10
                or param.tdesc.vt != 26
                or param.tdesc.lptdesc.contents.vt != 22
            ):
                raise DualInvokeError("dual_count_count_retval_mismatch")
            found = {
                "interface_iid": _guid(iid),
                "dispid": dispid,
                "invkind": 2,
                "funckind": fd.funckind,
                "function_flags": fd.wFuncFlags,
                "callconv": fd.callconv,
                "scode_count": fd.cScodes,
                "vtable_offset": fd.oVft,
                "parameter_count": fd.cParams,
                "optional_parameters": fd.cParamsOpt,
                "parameter_flags": param._.paramdesc.wParamFlags,
                "formal_result_vartype": fd.elemdescFunc.tdesc.vt,
                "input_parameters": 0,
                "formal_retval_vartype": 22,
                "normalized_return_vartype": 3,
            }
        repeated = self.read("declaration.final_GetTypeAttr", twin.GetTypeAttr)
        if found is None or repeated.cFuncs != attrs.cFuncs or _guid(repeated.guid) != _guid(iid):
            raise DualInvokeError("dual_count_count_declaration_incomplete")
        return twin, found

    def observe(self):
        self.owner()
        if self.started:
            raise DualInvokeError("dual_count_probe_already_attempted")
        self.started = True
        self.result["status"] = "incomplete"
        self.bridge = _NativeBridge(self)  # Retained before any native acquisition.
        canonical = library = remote_library = declaration = None
        raw = typed = wrapper = original = count = _remote_declaration = None
        try:
            canonical = self._canonical(self.application)
            library, remote_library = self._library()
            for label, name, getter_id, getter_kind, iid, count_id in _TARGETS:
                item = {
                    "status": "not_attempted",
                    "value": None,
                    "hresult": None,
                    "failed_callpoint": None,
                    "same_canonical": None,
                    "invoke_attempted": False,
                    "invoke_returned": None,
                    "return_vartype": None,
                }
                self.result["counts"][label] = item
                try:
                    declaration, contract = self._declaration(library, iid, count_id)
                    _remote_declaration, remote_contract = self._declaration(
                        remote_library, iid, count_id
                    )
                    if contract != remote_contract:
                        item["different_contract_fields"] = sorted(
                            key
                            for key in set(contract) | set(remote_contract)
                            if contract.get(key) != remote_contract.get(key)
                        )
                        raise DualInvokeError("dual_count_local_and_remote_count_contract_mismatch")
                    member = self.read(
                        "getter." + name + ".member",
                        lambda name=name: self.application.GetIDsOfNames(0, name),
                    )
                    if type(member) is not int or member != getter_id:
                        raise DualInvokeError("dual_count_application_getter_mismatch")
                    raw = self._hold(
                        "getter." + name,
                        lambda getter_id=getter_id, getter_kind=getter_kind: (
                            self.application.InvokeTypes(
                                getter_id, 0, getter_kind, (self.com.VT_DISPATCH, 0), ()
                            )
                        ),
                    )
                    if raw is None:
                        item["status"] = "observed_null"
                        continue
                    typed, wrapper, original = self.bridge.exact(raw, iid)
                    item.update(same_canonical=True, declaration=contract)
                    _same(canonical, self._canonical(self.application))
                    self._verify_local_source()
                    vt, count = self.bridge.invoke_count(declaration, typed, count_id, item)
                    item.update(return_vartype=vt)
                    if (
                        type(vt) is not int
                        or vt not in (3, 22)
                        or type(count) is not int
                        or not 0 <= count <= 64
                    ):
                        raise DualInvokeError("dual_count_result_invalid_or_over_limit")
                    _same(original, self._canonical(wrapper))
                    _same(canonical, self._canonical(self.application))
                    self._verify_local_source()
                    item.update(status="observed", value=count)
                except Exception as error:
                    hr = getattr(error, "hresult", None)
                    item.update(
                        status="error",
                        hresult=hr if type(hr) is int else None,
                        failed_callpoint=self.point,
                        reason=str(error)
                        if type(error) is DualInvokeError
                        else "dual_count_native_exception",
                    )
            self.result["status"] = (
                "observed"
                if all(x["status"] == "observed" for x in self.result["counts"].values())
                else "incomplete"
            )
        except Exception as error:
            hr = getattr(error, "hresult", None)
            self.result.update(
                status="incomplete",
                hresult=hr if type(hr) is int else None,
                failed_callpoint=self.point,
                reason=str(error)
                if type(error) is DualInvokeError
                else "dual_count_native_exception",
            )
        finally:
            canonical = library = remote_library = declaration = None
            raw = typed = wrapper = original = count = _remote_declaration = None
            try:
                self.resources_released = self.bridge.release()
            except Exception as error:
                self.cleanup_error(error, "bridge.release_owned_references")
                self.resources_released = False
            if self.resources_released:
                self.result["cleanup"].update(status="observed", resources_released=True)
            else:
                self.result["status"] = "cleanup_incomplete"
                self.result["cleanup"].update(resources_released=False)
                if self.result["cleanup"]["status"] == "unobserved":
                    self.result["cleanup"]["status"] = "incomplete"
            self.result["cleanup"].update(
                registration_attempts=sum(e["register_attempted"] for e in self.bridge.entries),
                registration_returned_count=sum(
                    e["register_returned"] is True for e in self.bridge.entries
                ),
                known_registered_cookie_count=sum(
                    type(e["cookie"]) is int and 0 < e["cookie"] < 2**32
                    for e in self.bridge.entries
                ),
                registered_cookie_count=(
                    None
                    if any(
                        e["register_attempted"]
                        and (
                            e["register_returned"] is not True
                            or type(e["cookie"]) is not int
                            or not 0 < e["cookie"] < 2**32
                        )
                        for e in self.bridge.entries
                    )
                    else sum(
                        type(e["cookie"]) is int and 0 < e["cookie"] < 2**32
                        for e in self.bridge.entries
                    )
                ),
                revoke_attempts=sum(e["revoke_attempted"] for e in self.bridge.entries),
                revoke_returned_count=sum(
                    e["revoke_returned"] is True for e in self.bridge.entries
                ),
            )
            # Keep the held app reference until normal cleanup is complete.
            if self.resources_released:
                self.application = None
        return copy.deepcopy(self.result)


class FixedDocumentCall(ReadonlyCountProbe):
    """Three private fixed members; an omitted member means metadata-only preflight.

    The actual NativeApplication installs this object before run(). Dispatched
    failure retains its complete parameter/output graph. Success transfers only
    an independently AddRef'd document to the same owner adapter call stack.
    """

    def __init__(self, *args, operation=None, argument=None, **kwargs):
        if operation is not None and operation not in _DOCUMENT_MEMBERS:
            raise DualInvokeError("fixed_document_member_required")
        if operation == "open_design":
            if (
                type(argument) is not str
                or not argument
                or len(argument) > 4096
                or any(ord(c) < 32 for c in argument)
            ):
                raise DualInvokeError("fixed_document_observed_design_required")
        elif argument is not None:
            raise DualInvokeError("fixed_document_void_member_has_no_arguments")
        super().__init__(*args, **kwargs)
        self.operation, self.argument, self.returned_document = operation, argument, None
        self.result.update(
            scheme="designer-fixed-document-call-v1",
            diagnostic_only=operation is None,
            operation=operation or "preflight",
            phase="not_started",
            invoke_attempted=False,
            invoke_returned=None,
            return_vartype=None,
            persistence_verified=False,
            return_reference_transferred=False,
        )
        self.result.pop("counts")
        if operation is not None:
            # Admission belongs to the existing public SDK/opaque owner, not
            # this transport. Avoid misreporting a performed write as disabled.
            self.result.pop("native_writer_admitted")
            self.result["authority_source"] = "current_public_scope_and_opaque_owner"
        self.result["cleanup"]["scope"] = "temporary_git_metadata_arguments_and_output_storage"

    def read(self, point, operation):
        self.owner()
        self.point = point
        self.result["phase"] = "invoke" if point == "document.ITypeInfo_Invoke" else "metadata"
        return self.budget.read("dual_document." + point, operation)

    def _shape(self, info, desc, depth=0):
        if depth > 4:
            raise DualInvokeError("fixed_document_type_description_depth")
        kind = int(desc.vt)
        if kind == 26:
            return [kind, self._shape(info, desc.lptdesc.contents, depth + 1)]
        if kind == 29:
            referenced = self._hold(
                "declaration.return_GetRefTypeInfo", lambda: info.GetRefTypeInfo(desc.hreftype)
            )
            attr = self.read("declaration.return_GetTypeAttr", referenced.GetTypeAttr)
            return [kind, _guid(attr.guid)]
        return [kind]

    def _document_declaration(self, library, operation):
        iid, name, member, flags, returns_document = _DOCUMENT_MEMBERS[operation]
        dispatch = self._hold(
            "document.GetTypeInfoOfGuid", lambda: library.GetTypeInfoOfGuid(self.bridge.guid(iid))
        )
        attr = self.read("document.dispatch_GetTypeAttr", dispatch.GetTypeAttr)
        if _guid(attr.guid) != _guid(iid) or attr.typekind != 4 or not attr.wTypeFlags & 64:
            raise DualInvokeError("fixed_document_formal_dispatch_required")
        reference = self.read(
            "document.GetRefTypeOfImplType", lambda: dispatch.GetRefTypeOfImplType(-1)
        )
        twin = self._hold("document.GetRefTypeInfo", lambda: dispatch.GetRefTypeInfo(reference))
        contracts = {}
        doc = [26, [29, _guid(DOCUMENT_IID)]]
        for label, info, typekind, funckind in (("dispatch", dispatch, 4, 4), ("dual", twin, 3, 1)):
            attrs = self.read("document.role_GetTypeAttr", info.GetTypeAttr)
            if (
                _guid(attrs.guid) != _guid(iid)
                or attrs.typekind != typekind
                or not 0 <= attrs.cFuncs <= 128
            ):
                raise DualInvokeError("fixed_document_formal_role_mismatch")
            rows = []
            for index in range(attrs.cFuncs):
                fd = self.read(
                    "document.GetFuncDesc", lambda index=index, info=info: info.GetFuncDesc(index)
                )
                if fd.memid != member:
                    continue
                names = self.read(
                    "document.GetNames",
                    lambda info=info, fd=fd: info.GetNames(member, fd.cParams + 1),
                )
                if (
                    not names
                    or names[0] != name
                    or fd.invkind != 1
                    or fd.funckind != funckind
                    or fd.cParamsOpt != 0
                    or fd.wFuncFlags != flags
                    or fd.callconv != 4
                ):
                    raise DualInvokeError("fixed_document_member_signature_mismatch")
                parameters = [
                    {
                        "flags": fd.lprgelemdescParam[i]._.paramdesc.wParamFlags,
                        "shape": self._shape(info, fd.lprgelemdescParam[i].tdesc),
                    }
                    for i in range(fd.cParams)
                ]
                expected_parameters = []
                if returns_document:
                    expected_parameters = [{"flags": 1, "shape": [8]}]
                    if label == "dual":
                        expected_parameters += [{"flags": 10, "shape": [26, doc]}]
                result = self._shape(info, fd.elemdescFunc.tdesc)
                expected_result = (
                    doc
                    if returns_document and label == "dispatch"
                    else [24 if label == "dispatch" else 25]
                )
                if parameters != expected_parameters or result != expected_result:
                    raise DualInvokeError("fixed_document_parameter_or_return_iid_mismatch")
                rows.append(
                    {
                        "iid": _guid(iid),
                        "names": names,
                        "member": member,
                        "typekind": typekind,
                        "function_kind": fd.funckind,
                        "invoke_kind": fd.invkind,
                        "optional": fd.cParamsOpt,
                        "flags": fd.wFuncFlags,
                        "callconv": fd.callconv,
                        "vtable_offset": fd.oVft,
                        "parameters": parameters,
                        "return": result,
                    }
                )
            if len(rows) != 1:
                raise DualInvokeError("fixed_document_unique_member_declaration_required")
            contracts[label] = rows[0]
        return twin, contracts

    def run(self, *, on_dispatch=None, on_return=None, precheck=None, postcheck=None):
        self.owner()
        if self.started:
            raise DualInvokeError("fixed_document_call_already_attempted")
        self.started = True
        self.bridge = _NativeBridge(self)
        canonical = local = remote = declaration = _remote_declaration = receiver = typed = (
            wrapper
        ) = identity = None
        failure = None
        try:
            canonical = self._canonical(self.application)
            local, remote = self._library()
            names = tuple(_DOCUMENT_MEMBERS) if self.operation is None else (self.operation,)
            declarations, contracts = {}, {}
            for operation in names:
                declaration, contract = self._document_declaration(local, operation)
                _remote_declaration, remote_contract = self._document_declaration(remote, operation)
                if contract != remote_contract:
                    raise DualInvokeError("fixed_document_local_remote_contract_mismatch")
                declarations[operation], contracts[operation] = declaration, contract
            self.result["contracts"] = contracts
            self._verify_local_source()
            if self.operation is not None:
                iid, _name, member, _flags, returned_doc = _DOCUMENT_MEMBERS[self.operation]
                getter, label = (
                    (208, "ActiveDocument")
                    if self.operation == "save_document"
                    else (1, "Documents")
                )
                actual = self.read(
                    "receiver.member", lambda: self.application.GetIDsOfNames(0, label)
                )
                if type(actual) is not int or actual != getter:
                    raise DualInvokeError("fixed_document_receiver_getter_mismatch")
                receiver = self._hold(
                    "receiver.get",
                    lambda: self.application.InvokeTypes(
                        getter, 0, 2, (self.com.VT_DISPATCH, 0), ()
                    ),
                )
                if receiver is None:
                    raise DualInvokeError("fixed_document_receiver_unavailable")
                typed, wrapper, identity = self.bridge.exact(receiver, iid)
                _same(canonical, self._canonical(self.application))
                self._verify_local_source()
                if precheck is not None:
                    precheck()
                self.returned_document = self.bridge.invoke_document(
                    declarations[self.operation],
                    typed,
                    member,
                    self.argument,
                    returned_doc,
                    self.result,
                    on_dispatch,
                    on_return,
                )
                _same(identity, self._canonical(wrapper))
                _same(canonical, self._canonical(self.application))
                self._verify_local_source()
            if postcheck is not None:
                postcheck()
            self.result["status"] = "metadata_verified" if self.operation is None else "returned"
        except BaseException as error:
            failure = error
            hr = getattr(error, "hresult", None)
            self.result.update(
                status="incomplete",
                failed_callpoint=self.point,
                hresult=hr if type(hr) is int else None,
                reason=str(error)
                if type(error) is DualInvokeError
                else "fixed_document_native_exception",
            )
        finally:
            canonical = local = remote = declaration = _remote_declaration = receiver = typed = (
                wrapper
            ) = identity = None
            declarations = None
            self.result["phase"] = "cleanup"
            if failure is not None and self.result["invoke_attempted"]:
                # Parameters and an unvalidated return remain on the real owner.
                self.resources_released = False
                self.result["cleanup"].update(
                    status="retained_after_dispatched_failure", resources_released=False
                )
            else:
                try:
                    self.resources_released = self.bridge.release()
                except BaseException as error:
                    self.cleanup_error(error, "document.release_owned_references")
                    self.resources_released = False
                    failure = failure or error
                self.result["cleanup"].update(
                    status="observed" if self.resources_released else "incomplete",
                    resources_released=self.resources_released,
                )
            if self.resources_released:
                self.application = None
            elif failure is None:
                failure = DualInvokeError("fixed_document_cleanup_incomplete")
            if not self.resources_released:
                self.result["status"] = "cleanup_incomplete"
            self.result["cleanup"].update(
                known_registered_cookie_count=sum(
                    type(e["cookie"]) is int and 0 < e["cookie"] < 2**32
                    for e in self.bridge.entries
                ),
                revoke_attempts=sum(e["revoke_attempted"] for e in self.bridge.entries),
                revoke_returned_count=sum(
                    e["revoke_returned"] is True for e in self.bridge.entries
                ),
            )
        if failure is not None:
            error = DualInvokeError("fixed_document_call_incomplete")
            # Only the metadata-only preflight is known to precede the entire
            # creation write. A later member's metadata rejection cannot erase
            # an earlier NewProject/write in the same adapter command.
            diagnostic_failed = getattr(failure, "native_boundary_trace_incomplete", False) is True
            error.dispatch_started = self.operation is not None or diagnostic_failed
            error.hresult = self.result.get("hresult")
            normal_return = getattr(failure, "native_call_returned", None)
            if type(normal_return) is bool:
                error.native_call_returned = normal_return
            if hasattr(failure, "native_result_summary"):
                error.native_result_summary = copy.deepcopy(failure.native_result_summary)
            if diagnostic_failed:
                error.code = "E_OUTCOME_UNKNOWN"
                error.native_boundary_trace_incomplete = True
                error.engineering_write_attempted = False if self.operation is None else None
                error.fixed_document_member_attempted = self.result["invoke_attempted"]
                error.fixed_document_cleanup_observed = self.resources_released
                error.fixed_document_failed_callpoint = self.result.get("failed_callpoint")
            raise error from None
        return copy.deepcopy(self.result)

    def take_document(self):
        self.owner()
        if (
            self.operation != "open_design"
            or self.resources_released is not True
            or self.returned_document is None
        ):
            raise DualInvokeError("fixed_document_independent_return_not_available")
        value, self.returned_document = self.returned_document, None
        self.result["return_reference_transferred"] = True
        return value


class FixedWireConstantProbe(ReadonlyCountProbe):
    """Observe only current151 VdBusOrWire.VD_WIRE; no caller-selected enum."""

    def read(self, point, operation):
        self.owner()
        self.point = point
        return self.budget.read("fixed_wire." + point, operation)

    def _wire_declaration(self, library):
        count = self.read("enum.GetTypeInfoCount", library.GetTypeInfoCount)
        if type(count) is not int or not 0 < count <= 1024:
            raise DualInvokeError("fixed_wire_typeinfo_count_invalid")
        matches = []
        for index in range(count):
            kind = self.read(
                "enum.GetTypeInfoType", lambda index=index: library.GetTypeInfoType(index)
            )
            if type(kind) is not int:
                raise DualInvokeError("fixed_wire_typekind_invalid")
            if kind != 0:
                continue
            name = self.read(
                "enum.GetDocumentation", lambda index=index: library.GetDocumentation(index)[0]
            )
            if name != "VdBusOrWire":
                continue
            info = self._hold("enum.GetTypeInfo", lambda index=index: library.GetTypeInfo(index))
            attrs = self.read("enum.GetTypeAttr", info.GetTypeAttr)
            if (
                type(attrs.typekind) is not int
                or type(attrs.cVars) is not int
                or attrs.typekind != 0
                or _guid(attrs.guid) != "00000000-0000-0000-0000-000000000000"
                or not 0 <= attrs.cVars <= 256
            ):
                raise DualInvokeError("fixed_wire_enum_declaration_invalid")
            members = []
            for member in range(attrs.cVars):
                var = self._hold(
                    "enum.GetVarDesc", lambda member=member, info=info: info.GetVarDesc(member)
                )
                names = self.read(
                    "enum.GetNames", lambda var=var, info=info: info.GetNames(var.memid, 1)
                )
                if names != ["VD_WIRE"]:
                    continue
                if (
                    type(var.memid) is not int
                    or type(var.varkind) is not int
                    or type(var.elemdescVar.tdesc.vt) is not int
                    or var.varkind != 2
                    or var.elemdescVar.tdesc.vt != 22
                    or not var._.lpvarValue
                ):
                    raise DualInvokeError("fixed_wire_constant_signature_invalid")
                value = var._.lpvarValue.contents
                if (
                    type(value.vt) is not int
                    or value.vt != 3
                    or type(value._.VT_I4) is not int
                    or value._.VT_I4 != 0
                ):
                    raise DualInvokeError("fixed_wire_constant_value_not_observed_zero")
                members.append(
                    {
                        "enum": "VdBusOrWire",
                        "name": "VD_WIRE",
                        "typekind": 0,
                        "member_id": var.memid,
                        "var_kind": 2,
                        "declared_vartype": 22,
                        "value_vartype": 3,
                        "value": value._.VT_I4,
                    }
                )
            repeated = self.read("enum.final_GetTypeAttr", info.GetTypeAttr)
            if repeated.cVars != attrs.cVars or len(members) != 1:
                raise DualInvokeError("fixed_wire_unique_constant_required")
            matches.append(members[0])
        repeated = self.read("enum.final_GetTypeInfoCount", library.GetTypeInfoCount)
        if repeated != count or len(matches) != 1:
            raise DualInvokeError("fixed_wire_unique_enum_required")
        return matches[0]

    def resolve(self):
        self.owner()
        if self.started:
            raise DualInvokeError("fixed_wire_probe_already_attempted")
        self.started = True
        self.bridge = _NativeBridge(self)
        self.result.pop("counts")
        self.result.update(scheme="designer-fixed-wire-enum-v1", status="incomplete", value=None)
        canonical = local = remote = None
        failure = None
        try:
            canonical = self._canonical(self.application)
            local, remote = self._library()
            declared = self._wire_declaration(local)
            if declared != self._wire_declaration(remote):
                raise DualInvokeError("fixed_wire_local_remote_enum_mismatch")
            self._verify_local_source()
            _same(canonical, self._canonical(self.application))
            self.result.update(status="observed", value=declared["value"], declaration=declared)
        except BaseException as error:
            failure = error
            hr = getattr(error, "hresult", None)
            self.result.update(
                hresult=hr if type(hr) is int else None,
                failed_callpoint=self.point,
                reason=str(error)
                if type(error) is DualInvokeError
                else "fixed_wire_metadata_exception",
            )
        finally:
            canonical = local = remote = None
            try:
                self.resources_released = self.bridge.release()
            except BaseException as error:
                self.cleanup_error(error, "fixed_wire.release_owned_references")
                self.resources_released = False
                failure = failure or error
            self.result["cleanup"].update(
                status="observed" if self.resources_released else "incomplete",
                resources_released=self.resources_released,
            )
            if self.resources_released:
                self.application = None
            elif failure is None:
                failure = DualInvokeError("fixed_wire_cleanup_incomplete")
            if self.resources_released is not True:
                self.result["status"] = "cleanup_incomplete"
        if failure is not None:
            error = DualInvokeError("fixed_wire_constant_incomplete")
            error.dispatch_started = True  # This macro may have earlier writes.
            error.hresult = self.result.get("hresult")
            for attr in (
                "native_boundary_trace_incomplete",
                "native_call_returned",
                "native_result_summary",
            ):
                if hasattr(failure, attr):
                    setattr(error, attr, copy.deepcopy(getattr(failure, attr)))
            raise error from None
        return copy.deepcopy(self.result)
