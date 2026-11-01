"""One scope-dispatched, bounded STA owner; no-value Load/read/UnLoad only.

This external acceptance method is not part of the product command tree or EXE.
It never uses GetActiveObject, restores old owners, writes rule Values, or saves.
Original activation/Application and authenticated Document stay in this client
thread. Document.Application is independently observed and never substituted.
"""

from __future__ import annotations

import argparse
import datetime
import gc
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.native_acceptance import owner_rules  # noqa: E402
from scripts.native_acceptance.owner_identity import (  # noqa: E402
    capture_identity,
    compare_identity,
)
from scripts.native_acceptance.owner_phases import run_noedit  # noqa: E402
from scripts.native_acceptance.owner_support import (  # noqa: E402
    WindowsProcessObserver,
    capture_live_log,
    classify_process_roles,
    registered_layout_factory,
    require_callables,
    strict_file_pin,
)

GET, PUT, METHOD = 2, 4, 1
CAD_DISPATCHED = False
LIMITS = dict(
    Activation=1, Open=1, Load=1, UnLoad=1, Close=1, Quit=1, ValuePUT=0, SynchCES=0, Save=0
)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def file_pin(path):
    import hashlib

    p = Path(path).resolve()
    raw = p.read_bytes()
    return dict(path=str(p), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def write_new(path, value):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("x", encoding="utf-8", newline="\n") as out:
        json.dump(value, out, ensure_ascii=False, indent=2, allow_nan=False)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def normalized(path):
    return os.path.normcase(os.path.realpath(path))


class Evidence:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.stream = (self.directory / "events.jsonl").open("x", encoding="utf-8")
        self.secret_values = []
        self.sequence = 0
        self.errors = []

    def clean(self, value):
        if value is None or type(value) in (str, int, float, bool):
            return value
        if isinstance(value, (list, tuple)):
            return [self.clean(x) for x in value]
        if isinstance(value, dict):
            return {str(k): self.clean(v) for k, v in value.items()}
        return {"native_reference_type": type(value).__name__}

    def event(self, event, *, durable=False, **data):
        self.sequence += 1
        row = dict(
            sequence=self.sequence,
            at=datetime.datetime.now(datetime.UTC).isoformat(),
            event=event,
            **self.clean(data),
        )
        self.stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        self.stream.flush()
        if durable:
            os.fsync(self.stream.fileno())

    def safe_event(self, event, **data):
        try:
            self.event(event, **data)
        except Exception as exc:
            self.errors.append(dict(event=event, type=type(exc).__name__, message=str(exc)))

    def file(self, name, value):
        p = self.directory / name
        write_new(p, self.clean(value))
        self.safe_event("evidence_file", reference=file_pin(p))
        return file_pin(p)


class BootstrapLog:
    def __init__(self):
        self.events = []

    def event(self, event, **data):
        self.events.append(dict(event=event, **data))


def import_dependencies(contract):
    strict_file_pin(contract["intake"])
    intake = read_json(contract["intake"]["path"])
    registration = read_json(intake["registration"]["path"])
    strict_file_pin(intake["registration"])
    for field in ("project", "board", "board_name"):
        if contract[field] != registration[field]:
            raise ValueError("Target differs from the single registered owned copy")
    for field in ("environment", "scope", "journal", "baseline_source"):
        if contract[field] != intake[field]:
            raise ValueError("Original environment/scope/journal/source identity changed")
    for ref in contract["source_pins"] + contract["file_pins"]:
        strict_file_pin(ref)
    strict_file_pin(contract["dependency_bundle"])
    deps = read_json(contract["dependency_bundle"]["path"])
    for ref in deps["files"]:
        strict_file_pin(ref)
    minimum = load_module("owned_minimum_readers", deps["minimum_reader"]["path"])
    minimum.FROZEN = Path(contract["baseline_source"]["root"])
    base, layout = minimum.pure_helpers()
    base.REPO = minimum.FROZEN
    support = load_module("owned_formal_activation_support", deps["noedit_support"]["path"])
    return deps, minimum, base, layout, support


def observe_files(root):
    root = Path(root).resolve()
    files, errors = [], []
    try:
        if not root.is_dir():
            raise FileNotFoundError(str(root))
        paths = list(root.rglob("*"))
    except Exception as exc:
        return dict(
            root=str(root),
            files=[],
            errors=[
                dict(
                    path="<enumeration>", type=type(exc).__name__, errno=getattr(exc, "errno", None)
                )
            ],
            complete=False,
        )
    for path in paths:
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        try:
            first = path.stat()
            ref = file_pin(path)
            last = path.stat()
            if (first.st_size, first.st_mtime_ns) != (last.st_size, last.st_mtime_ns):
                raise ValueError("File changed while hashing")
            files.append(dict(path=rel, bytes=ref["bytes"], sha256=ref["sha256"]))
        except Exception as exc:
            errors.append(
                dict(path=rel, type=type(exc).__name__, errno=getattr(exc, "errno", None))
            )
    return dict(
        root=str(root),
        files=sorted(files, key=lambda r: r["path"]),
        errors=sorted(errors, key=lambda r: r["path"]),
        complete=not errors,
    )


def verify_inventory(reference):
    strict_file_pin(reference)
    expected = read_json(reference["path"])
    current = observe_files(expected["root"])
    errors = [
        {k: row.get(k) for k in ("path", "type", "errno")} for row in expected.get("errors", [])
    ]
    if current["files"] != expected["files"] or current["errors"] != errors:
        raise ValueError("Protected readable files/error coverage changed: " + expected["root"])
    return dict(
        reference=reference,
        readable_files=len(current["files"]),
        complete=not current["errors"],
        errors=current["errors"],
    )


def runtime_preflight(contract, minimum, base, layout, support):
    import ctypes
    import inspect
    import struct
    import winreg

    import pythoncom
    import win32com.client

    if os.name != "nt" or struct.calcsize("P") != 8:
        raise ValueError("This declared win64 installation requires a 64-bit Windows owner")
    requirements = [
        (
            pythoncom,
            (
                "MakeIID",
                "CoCreateInstance",
                "CoInitializeEx",
                "CoUninitialize",
                "PumpWaitingMessages",
            ),
        ),
        (win32com.client, ("Dispatch",)),
        (winreg, ("OpenKey", "QueryValueEx")),
        (subprocess, ("run",)),
        (
            minimum,
            ("pure_helpers", "classes", "validated_layout_fingerprint", "client_environment"),
        ),
        (base, ("constraints_fingerprint",)),
        (layout, ("layout_fingerprint",)),
        (support, ("registered_activation_source", "loaded_activation_module")),
        (WindowsProcessObserver, ("observe_window", "observe_pid")),
        (owner_rules, ("compare_loaded",)),
        (
            Owner,
            (
                "baseline_read",
                "load",
                "read_loaded",
                "unload",
                "identity",
                "release_constraints",
                "after_read",
                "close_successful_control",
                "compare_fresh_document",
                "observe_document",
            ),
        ),
    ]
    checked = require_callables(requirements)
    from xpedition_cli import board_copper, cell_refresh, copper_ties
    from xpedition_cli import native_com_adapter as adapter

    if normalized(adapter.__file__) != normalized(
        Path(contract["baseline_source"]["root"]) / "xpedition_cli/native_com_adapter.py"
    ):
        raise ValueError("Geometry adapter imported from an unexpected source")
    checked.extend(
        require_callables(
            [
                (adapter, ("_board_model",)),
                (cell_refresh, ("capture",)),
                (board_copper, ("observe",)),
                (copper_ties, ("observe",)),
            ]
        )
    )
    dispatch_type = pythoncom.TypeIIDs[pythoncom.IID_IDispatch]
    for method in ("Invoke", "GetTypeInfo", "QueryInterface"):
        if not callable(getattr(dispatch_type, method, None)):
            raise ValueError("Actual PyIDispatch method unavailable: " + method)
    with winreg.OpenKey(
        winreg.HKEY_CLASSES_ROOT,
        "MGCPCB.ExpeditionPCBApplication.151\\CLSID",
        0,
        winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
    ) as key:
        value, kind = winreg.QueryValueEx(key, "")
    if kind != winreg.REG_SZ:
        raise ValueError("Versioned Layout CLSID default value is not REG_SZ")
    clsid = registered_layout_factory(pythoncom, value)
    metadata = minimum.classes(base, layout)(None, time.monotonic() + 1, "read-layout").formal
    required = [
        ("MGCPCB", "IMGCPCBApplication", "OpenDocument", METHOD),
        ("MGCPCB", "IMGCPCBApplication", "Quit", METHOD),
        ("MGCPCB", "IMGCPCBDocument", "Close", METHOD),
        ("MGCPCB", "IMGCPCBDocument", "Application", GET),
        ("ConstraintsAuto", "IDesign", "CreateDesignParams", METHOD),
        ("ConstraintsAuto", "IDesign", "Load", METHOD),
        ("ConstraintsAuto", "IDesign", "UnLoad", METHOD),
    ]
    signatures = [
        dict(
            library=a, interface=b, member=c, kind=d, signature=metadata.member(None, a, b, c, d)[0]
        )
        for a, b, c, d in required
    ]
    # Resolve/read-only API exports before activation, not after Open.
    observer = WindowsProcessObserver()
    for method in (
        "GetWindowThreadProcessId",
        "OpenProcess",
        "GetProcessTimes",
        "QueryFullProcessImageNameW",
        "CloseHandle",
    ):
        api = observer.user if method == "GetWindowThreadProcessId" else observer.kernel
        if not callable(getattr(api, method, None)):
            raise ValueError("Windows observer callable unavailable: " + method)
    inspect.signature(capture_identity).bind(
        None,
        None,
        phase="pre_open",
        sample="offline",
        read_member=None,
        describe_type=None,
        observe_process=None,
        context_id="offline",
    )
    return dict(
        callables=checked,
        type_methods=["Invoke", "GetTypeInfo", "QueryInterface"],
        signatures=signatures,
        layout_registration=dict(
            key="HKCR64/MGCPCB.ExpeditionPCBApplication.151/CLSID",
            value=value,
            kind=kind,
            constructed=str(clsid),
        ),
        ctypes_version=str(ctypes.__version__),
        explicit_COM_creates=0,
        connected_COM_objects=0,
        client_bitness=64,
    ), clsid


def process_inventory():
    code = (
        "Get-CimInstance Win32_Process -Filter \"Name = 'ExpeditionPCB.exe'\" | "
        "Select-Object ProcessId,ParentProcessId,ExecutablePath | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", code],
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    rows = json.loads(result.stdout) if result.stdout.strip() else []
    rows = rows if isinstance(rows, list) else [rows]
    observer = WindowsProcessObserver()
    observed = []
    for row in rows:
        actual = observer.observe_pid(row["ProcessId"])
        if normalized(actual["executable"]) != normalized(row["ExecutablePath"]):
            raise ValueError("CIM enumeration and actual process handle disagree")
        observed.append(dict(actual, parent_pid=row["ParentProcessId"]))
    return observed


class Owner:
    def __init__(self, contract, log, minimum, base, layout, support):
        self.contract, self.log = contract, log
        self.minimum, self.base, self.layout, self.support = minimum, base, layout, support
        self.counts = dict.fromkeys(LIMITS, 0)
        self.context_id = f"client-{os.getpid()}-sta-{threading.get_ident()}"
        self.thread = threading.get_ident()
        self.deadline = time.monotonic() + contract.get("native_deadline_seconds", 700)
        self.process = WindowsProcessObserver()
        self.application = self.document = self.auto = self.design = self.params = None
        self.application_unknown = self.document_unknown = None
        self.last_identity = self.identity_expected = None
        self.identity_number = 0
        self.missing_required_evidence = []
        self.requirements = []
        self.baseline = None
        self.reference_after_open = self.reference_after_unload = None
        self.phase_result = None
        self.licensing_calls = 0
        self.cma_activations = 0
        self.stage = "created"
        parent = minimum.classes(base, layout)
        owner = self

        class Native(base.Native):
            def __init__(self):
                super().__init__(log, owner.deadline)
                self.formal = parent(log, owner.deadline, "read-layout").formal

            def call(
                self, obj, library, typ, name, *args, kind=GET, licensing=False, secret_result=False
            ):
                if threading.get_ident() != owner.thread:
                    raise ValueError("COM owner thread changed")
                self.check()
                if len(self.calls) >= 16000:
                    raise ValueError("Bounded native call budget exhausted")
                getters = (
                    library == "MGCPCB"
                    and kind == GET
                    and name in layout.ReadOnlyNative.GETS.get(typ, set())
                )
                getters |= (
                    library == "MGCPCB"
                    and kind == GET
                    and (typ, name)
                    in {("IMGCPCBDocument", "Application"), ("IMGCPCBDocument", "CurrentUnit")}
                )
                getters |= (
                    library == "ConstraintsAuto"
                    and kind == GET
                    and name in parent.CMA_GETS.get(typ, set())
                )
                reads = (
                    library == "MGCPCB"
                    and kind == METHOD
                    and (typ, name) in layout.ReadOnlyNative.READ_METHODS
                    and not args
                )
                auth = (
                    licensing
                    and kind == METHOD
                    and (library, typ, name)
                    in {
                        ("MGCPCB", "IMGCPCBDocument", "Validate"),
                        ("MGCPCBAutomationLicensing", "IApplication", "GetToken"),
                        ("ConstraintsAuto", "IConstraintsAuto", "Validate"),
                    }
                )
                params = (
                    library == "ConstraintsAuto"
                    and typ == "IDesignParams"
                    and kind == PUT
                    and name in {"ProjectFile", "Board", "DesignContext"}
                    and len(args) == 1
                )
                lifecycle = (
                    library == "ConstraintsAuto"
                    and typ == "IDesign"
                    and kind == METHOD
                    and name in {"CreateDesignParams", "Load", "UnLoad"}
                )
                lifecycle |= (
                    library == "MGCPCB"
                    and typ == "IMGCPCBApplication"
                    and kind == METHOD
                    and (
                        name == "OpenDocument"
                        and args == (owner.contract["board"],)
                        or name == "Quit"
                        and not args
                    )
                )
                lifecycle |= (
                    library == "MGCPCB"
                    and typ == "IMGCPCBDocument"
                    and kind == METHOD
                    and name == "Close"
                    and args == (False,)
                )
                if not (getters or reads or auth or params or lifecycle):
                    raise ValueError(
                        f"Finite owner allowlist refused {library}/{typ}.{name}/{kind}"
                    )
                member, source = self.formal.member(obj, library, typ, name, kind)
                raw = getattr(obj, "_oleobj_", obj)
                converted = [getattr(a, "_oleobj_", a) for a in args]
                counted = (
                    name
                    if lifecycle and name in {"Load", "UnLoad", "OpenDocument", "Close", "Quit"}
                    else None
                )
                counted = "Open" if counted == "OpenDocument" else counted
                if counted and owner.counts[counted] >= LIMITS[counted]:
                    raise ValueError("Native action already attempted: " + counted)
                # Read/log observation failures must not turn a still-valid
                # known-loaded owner's authorized UnLoad into an abandoned lock.
                emitter = log.safe_event if counted in (None, "UnLoad") else log.event
                emitter(
                    "native_call_started",
                    durable=bool(counted),
                    member=f"{library}/{typ}.{name}",
                    dispid=member["dispid"],
                    kind=kind,
                    arguments="<licensing redacted>" if licensing else converted,
                    formal_source=source,
                )
                if counted:
                    owner.counts[counted] += 1
                try:
                    value = raw.Invoke(member["dispid"], 0, kind, 1, *converted)
                except Exception as exc:
                    self.calls.append(dict(member=f"{library}/{typ}.{name}", completed=False))
                    log.safe_event(
                        "native_call_failed",
                        member=f"{library}/{typ}.{name}",
                        error_type=type(exc).__name__,
                        hresult=getattr(exc, "hresult", None),
                        message="<licensing redacted>" if licensing else str(exc),
                    )
                    raise
                self.calls.append(dict(member=f"{library}/{typ}.{name}", completed=True))
                log.safe_event(
                    "native_call_returned",
                    member=f"{library}/{typ}.{name}",
                    result="<licensing redacted>" if licensing else value,
                )
                return value

        self.native = Native()

    def protected(self, phase):
        for ref in self.contract["source_pins"]:
            strict_file_pin(ref)
        rows = [verify_inventory(ref) for ref in self.contract["protected_inventories"]]
        self.requirements.append(self.log.file(phase + "-protection.json", rows))
        return True

    def authenticate(self, document):
        import win32com.client

        if document is None:
            raise ValueError("Required actual Document missing")
        n = self.native
        seed = n.call(
            document, "MGCPCB", "IMGCPCBDocument", "Validate", 0, kind=METHOD, licensing=True
        )
        if type(seed) is not int:
            raise ValueError("Document licensing seed is not LONG")
        license_object = win32com.client.Dispatch("MGCPCBAutomationLicensing.Application")
        self.licensing_calls += 1
        token = n.call(
            license_object,
            "MGCPCBAutomationLicensing",
            "IApplication",
            "GetToken",
            seed,
            kind=METHOD,
            licensing=True,
        )
        if type(token) is not int:
            raise ValueError("Licensing token is not LONG")
        n.call(
            document, "MGCPCB", "IMGCPCBDocument", "Validate", token, kind=METHOD, licensing=True
        )
        license_object = seed = token = None
        return document

    @staticmethod
    def describe_type(reference):
        ti = getattr(reference, "_oleobj_", reference).GetTypeInfo()
        lib, _ = ti.GetContainingTypeLib()
        attrs = lib.GetLibAttr()
        return dict(
            library_guid=str(attrs[0]),
            library_name=lib.GetDocumentation(-1)[0],
            library_major=attrs[3],
            library_minor=attrs[4],
            interface_guid=str(ti.GetTypeAttr()[0]),
            interface_name=ti.GetDocumentation(-1)[0],
        )

    def read_member(self, reference, name):
        typ = self.describe_type(reference)["interface_name"]
        value = self.native.call(reference, "MGCPCB", typ, name)
        if name == "ActiveDocument" and value is not None:
            self.authenticate(value)
        return value

    def same_iunknown(self, left, right):
        import pythoncom

        if threading.get_ident() != self.thread:
            raise ValueError("IUnknown comparison left owner COM context")
        a = getattr(left, "_oleobj_", left).QueryInterface(pythoncom.IID_IUnknown)
        b = getattr(right, "_oleobj_", right).QueryInterface(pythoncom.IID_IUnknown)
        equal = a == b
        a = b = None
        if type(equal) is not bool:
            raise ValueError("Canonical IUnknown equality did not yield BOOL")
        return equal

    def identity(self, phase):
        self.stage = phase
        self.identity_number += 1
        readings = []
        for suffix in ("a", "b"):
            row = capture_identity(
                self.application,
                self.document,
                phase=phase,
                sample=f"{self.identity_number}-{suffix}",
                read_member=self.read_member,
                describe_type=self.describe_type,
                observe_process=self.process.observe_window,
                context_id=self.context_id,
                same_iunknown=self.same_iunknown,
            )
            import pythoncom

            current = getattr(self.application, "_oleobj_", self.application).QueryInterface(
                pythoncom.IID_IUnknown
            )
            stable_object = current == self.application_unknown
            current = None
            row["iunknown"]["activation_vs_retained_canonical"] = dict(ok=True, value=stable_object)
            if self.document is not None:
                current_document = getattr(self.document, "_oleobj_", self.document).QueryInterface(
                    pythoncom.IID_IUnknown
                )
                row["iunknown"]["document_vs_retained_canonical"] = dict(
                    ok=True, value=current_document == self.document_unknown
                )
                current_document = None
            name = f"identity-{self.identity_number}-{phase}-{suffix}.json"
            try:
                self.requirements.append(self.log.file(name, row))
            except Exception as exc:
                self.missing_required_evidence.append(
                    dict(name=name, error_type=type(exc).__name__)
                )
            readings.append(row)
        result = compare_identity(
            *readings, expected=self.identity_expected, previous=self.last_identity
        )
        if any(
            r["iunknown"]["activation_vs_retained_canonical"]["value"] is not True for r in readings
        ):
            result["can_continue_original_chain"] = False
            result["errors"].append(dict(field="retained_application.IUnknown", reason="changed"))
        if self.document is not None and any(
            r["iunknown"]["document_vs_retained_canonical"]["value"] is not True for r in readings
        ):
            result["can_continue_original_chain"] = False
            result["errors"].append(dict(field="retained_document.IUnknown", reason="changed"))
        name = f"identity-{self.identity_number}-{phase}-comparison.json"
        try:
            self.requirements.append(self.log.file(name, result))
        except Exception as exc:
            self.missing_required_evidence.append(dict(name=name, error_type=type(exc).__name__))
        if not result["can_continue_original_chain"]:
            raise ValueError(
                "Original activation/document chain not established; see identity comparison"
            )
        self.last_identity = readings[-1]
        return True

    def observe_layout(self, label):
        return self.observe_document(self.document, label)

    def observe_document(self, document, label):
        import win32com.client

        from xpedition_cli import native_com_adapter as adapter
        from xpedition_cli.board_copper import observe as copper
        from xpedition_cli.cell_refresh import capture
        from xpedition_cli.copper_ties import observe as ties

        if self.native.call(document, "MGCPCB", "IMGCPCBDocument", "CurrentUnit") != 2:
            raise ValueError("Unsupported actual CurrentUnit; no unit setter")
        widths = self.minimum.validated_layout_fingerprint(
            self.layout, self.native, document, self.log, label + "-widths.json"
        )
        doc = win32com.client.Dispatch(getattr(document, "_oleobj_", document))
        model = adapter._board_model(doc, strict=True)
        geometry = capture(doc, adapter, strict=True)
        planes = copper(doc, adapter, geometry)
        inventory = ties(doc, adapter)
        row = dict(
            widths=widths,
            raw_model=model,
            geometry=geometry,
            planes=planes,
            ties=inventory,
            limits=("Bounded outputs; Net0/membership and UID0/thermal limits remain"),
        )
        self.requirements.append(self.log.file(label + "-independent-layout.json", row))
        return row

    def compare_fresh_document(self, label, held):
        fresh = self.authenticate(
            self.native.call(self.application, "MGCPCB", "IMGCPCBApplication", "ActiveDocument")
        )
        path = self.native.call(fresh, "MGCPCB", "IMGCPCBDocument", "FullName")
        if normalized(path) != normalized(self.contract["board"]):
            raise ValueError("Fresh original-Application Document has wrong physical path")
        observed = self.observe_document(fresh, label + "-fresh")
        row = dict(
            held_role="original authenticated retained Document",
            fresh_role="new ActiveDocument from original retained Application",
            fresh_type=self.describe_type(fresh),
            fresh_path=path,
            same_IUnknown=self.same_iunknown(fresh, self.document),
            fingerprints_equal=canonical(held) == canonical(observed),
            comparison_scope="bounded same-phase widths/objects/geometry/planes/tie inventory",
            interchangeability_proven=False,
        )
        self.requirements.append(self.log.file(label + "-physical-document-comparison.json", row))
        fresh = None
        if not row["fingerprints_equal"]:
            raise ValueError("Fresh and retained Document physical observations disagree")
        return row

    def baseline_read(self):
        self.protected("before-load")
        self.identity("baseline")
        a = self.observe_layout("baseline-a")
        b = self.observe_layout("baseline-b")
        if canonical(a) != canonical(b):
            raise ValueError("Repeated new baseline outputs differ")
        self.reference_after_open = self.compare_fresh_document("after-open", b)
        self.baseline = b
        return b

    def verify_identity(self):
        return self.identity("before-load" if self.counts["Load"] == 0 else "before-unload")

    def load(self):
        if not self.reference_after_open or not self.baseline:
            raise ValueError("Actual fresh/retained baseline comparison required before Load")
        if self.cma_activations or self.counts["Load"]:
            raise ValueError("This owner already attempted its single no-value CMA lifecycle")
        import pythoncom

        self.stage = "CMA-create"
        registered = self.support.registered_activation_source(self.log)
        self.cma_activations += 1
        self.auto = pythoncom.CoCreateInstance(
            pythoncom.MakeIID("{66D30501-6CCD-1014-8E8D-AEA2E1D98D3C}"),
            None,
            pythoncom.CLSCTX_INPROC_SERVER,
            pythoncom.IID_IDispatch,
        )
        actual = self.describe_type(self.auto)
        if (
            actual["library_major"] != 151
            or actual["library_minor"] != 0
            or actual["interface_guid"].lower() != "{66d30448-6ccd-1014-8e8d-aea2e1d98d3c}"
        ):
            raise ValueError("Live CMA type is not formal151 IConstraintsAuto")
        self.log.file(
            "CMA-type-and-registration.json",
            dict(
                actual=actual,
                registered=registered,
                loaded_module=self.support.loaded_activation_module(registered, self.log),
            ),
        )
        n = self.native
        seed = n.call(
            self.auto,
            "ConstraintsAuto",
            "IConstraintsAuto",
            "Validate",
            0,
            kind=METHOD,
            licensing=True,
        )
        if type(seed) is not int:
            raise ValueError("CMA Validate seed is not LONG")
        n.call(
            self.auto,
            "ConstraintsAuto",
            "IConstraintsAuto",
            "Validate",
            seed,
            kind=METHOD,
            licensing=True,
        )
        seed = None
        self.design = n.call(self.auto, "ConstraintsAuto", "IConstraintsAuto", "Design")
        self.params = n.call(
            self.design, "ConstraintsAuto", "IDesign", "CreateDesignParams", kind=METHOD
        )
        values = dict(
            ProjectFile=self.contract["project"], Board=self.contract["board_name"], DesignContext=2
        )
        for name, value in values.items():
            n.call(self.params, "ConstraintsAuto", "IDesignParams", name, value, kind=PUT)
        echoes = {
            name: n.call(self.params, "ConstraintsAuto", "IDesignParams", name) for name in values
        }
        if echoes != values:
            raise ValueError("Formal explicit Load parameters differ; no Load")
        self.log.file(
            "Load-input-parameters.json",
            dict(
                parameters=values,
                echoes=echoes,
                limitation="Input echoes are not loaded-design identity",
            ),
        )
        self.stage = "Load"
        return n.call(self.design, "ConstraintsAuto", "IDesign", "Load", self.params, kind=METHOD)

    def read_loaded(self):
        from scripts.native_acceptance.owner_rules import compare_loaded

        self.stage = "loaded-read"
        row = self.base.constraints_fingerprint(self.native, self.auto, self.design)
        self.requirements.append(self.log.file("loaded-design-read.json", row))
        compared = compare_loaded(self.baseline["widths"], row)
        self.requirements.append(self.log.file("loaded-layout-consistency.json", compared))
        return dict(inventory=row, consistency=compared)

    def sample_logs(self):
        self.requirements.append(
            self.log.file(
                "F-loaded-readable-files.json",
                observe_files(Path(self.contract["project"]).parent),
            )
        )
        rows = []
        project = Path(self.contract["project"]).parent
        candidates = sorted(
            p
            for p in project.rglob("*")
            if p.is_file() and (p.suffix.lower() == ".log" or "log" in p.name.lower())
        )
        for p in candidates[:32]:
            rows.append(
                capture_live_log(
                    p,
                    self.log.directory / "live-logs" / p.relative_to(project),
                    max_bytes=2 * 1024 * 1024,
                )
            )
        return dict(
            samples=rows,
            complete=False,
            max_files=32,
            unobserved_candidates=max(0, len(candidates) - 32),
        )

    def unload(self):
        self.stage = "UnLoad"
        return self.native.call(self.design, "ConstraintsAuto", "IDesign", "UnLoad", kind=METHOD)

    def release_constraints(self):
        for name in ("params", "design", "auto"):
            setattr(self, name, None)
            self.log.safe_event(
                "local_reference_cleared", name=name, service_lock_release_not_inferred=True
            )
        gc.collect()
        return self.auto is self.design is self.params is None

    def after_read(self):
        self.identity("after-unload")
        value = self.observe_layout("after-unload")
        self.reference_after_unload = self.compare_fresh_document("after-unload", value)
        self.protected("after-unload")
        return value

    @staticmethod
    def compare_readback(before, after):
        return canonical(before) == canonical(after)

    def close_successful_control(self):
        if not self.reference_after_unload:
            raise ValueError("Fresh post-UnLoad Document comparison missing; no Close/Quit")
        if not self.phase_result or self.phase_result["control_complete"] is not True:
            raise ValueError("No-value phase is not complete; no Close/Quit")
        if self.missing_required_evidence:
            raise ValueError("Required identity evidence was not persisted; no Close/Quit")
        self.protected("before-close")
        self.identity("before-close")
        if self.missing_required_evidence:
            raise ValueError("Final identity evidence was not persisted; no Close/Quit")
        self.stage = "Close"
        if (
            self.native.call(
                self.document, "MGCPCB", "IMGCPCBDocument", "Close", False, kind=METHOD
            )
            is not True
        ):
            raise ValueError("Close(False) did not return actual True; no Quit")
        self.document = None
        self.document_unknown = None
        self.identity("post_close")
        if self.missing_required_evidence:
            raise ValueError("Post-close identity evidence was not persisted; no Quit")
        self.stage = "Quit"
        if (
            self.native.call(self.application, "MGCPCB", "IMGCPCBApplication", "Quit", kind=METHOD)
            is not None
        ):
            raise ValueError("Quit did not return formal void")
        self.application = None
        self.application_unknown = None
        gc.collect()


def observe_exit(observer, expected, seconds=10):
    deadline = time.monotonic() + seconds
    samples = []
    while True:
        try:
            actual = observer.observe_pid(expected["pid"])
            if actual["pid"] != expected["pid"]:
                raise ValueError("Process observer returned a different PID")
            ended = actual["birth_filetime_100ns"] != expected["birth_filetime_100ns"]
            samples.append(dict(observed=actual, original_generation_ended=ended))
            if ended:
                return dict(exited=True, pid_reused=True, samples=samples)
            exit_time = actual.get("exit_filetime_100ns")
            if (
                actual.get("alive") is False
                and isinstance(exit_time, str)
                and exit_time.isdecimal()
                and int(exit_time) > 0
            ):
                samples[-1]["original_generation_ended"] = True
                return dict(exited=True, pid_reused=False, samples=samples)
        except OSError as exc:
            absent = getattr(exc, "winerror", None) == 87
            samples.append(dict(type=type(exc).__name__, winerror=getattr(exc, "winerror", None)))
            return dict(exited=absent, pid_reused=False, samples=samples)
        if time.monotonic() >= deadline:
            return dict(exited=False, pid_reused=False, samples=samples)
        time.sleep(0.5)


def files_delta(before, after):
    left = {r["path"]: r for r in before["files"]}
    right = {r["path"]: r for r in after["files"]}
    unreadable = {r["path"] for r in after["errors"]}
    enumeration_failed = "<enumeration>" in unreadable
    return dict(
        added=sorted(set(right) - set(left)),
        removed=[] if enumeration_failed else sorted(set(left) - set(right) - unreadable),
        changed=sorted(k for k in set(left) & set(right) if left[k] != right[k]),
        unobserved=sorted(unreadable),
        complete=before.get("complete", not before.get("errors")) and after.get("complete", False),
        manual_Save_called=False,
        rule_persistence_proven=False,
    )


def finalize_required_evidence(result, owner=None):
    """Independently validate actual artifacts before any final success claim.

    Close/Quit counts and prior stopped-stage facts remain unchanged. This
    validation does not resolve operations, undo effects or infer persistence.
    """
    refs = list(result.get("required_evidence", []))
    missing = list(result.get("missing_required_evidence", []))
    if owner is not None:
        for ref in owner.requirements:
            if ref not in refs:
                refs.append(ref)
        for failure in owner.missing_required_evidence:
            if failure not in missing:
                missing.append(failure)
    result["required_evidence"] = refs
    result["missing_required_evidence"] = missing
    failures = []
    for index, ref in enumerate(refs):
        try:
            strict_file_pin(ref)
        except Exception as exc:
            failures.append(
                dict(index=index, reference=ref, type=type(exc).__name__, message=str(exc))
            )
    # A claimed native success with no evidence is incomplete independently of
    # any upstream flag. Early pre-dispatch failures need not invent artifacts.
    empty_claim = result.get("control_completed") is True and not refs
    complete = not missing and not failures and not empty_claim
    result["required_evidence_validation"] = dict(
        complete=complete,
        checked_pins=len(refs),
        missing_required_evidence=missing,
        pin_failures=failures,
        claimed_success_without_evidence=empty_claim,
    )
    if not complete:
        if result.get("control_completed") is True:
            result["prior_stage"] = result.get("stage")
            result["stage"] = "required_evidence_incomplete"
        result["control_completed"] = False
        result["evidence_complete"] = False
        result["verification"] = dict(
            status="incomplete",
            reason="Required evidence was not persisted or its actual file pin no longer matches",
        )
    return complete


def main():
    global CAD_DISPATCHED
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request")
    parser.add_argument("--request-sha256")
    parser.add_argument("--operation-id")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--contract")
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    if args.preflight:
        contract = read_json(args.contract)
        _, minimum, base, layout, support = import_dependencies(contract)
        bootstrap = BootstrapLog()
        minimum.client_environment(dict(environment=contract["environment"]), bootstrap)
        report, _ = runtime_preflight(contract, minimum, base, layout, support)
        report["child_environment_bootstrap"] = bootstrap.events
        print(json.dumps(report, ensure_ascii=False))
        return 0

    # Pure input errors are caught by the outer zero-CAD marker before an
    # authorized output directory can necessarily be identified.
    ref = file_pin(args.request)
    if ref["sha256"] != args.request_sha256:
        raise ValueError("Dispatched request SHA mismatch")
    request = read_json(args.request)
    if request["operation_id"] != args.operation_id:
        raise ValueError("Dispatched operation mismatch")
    path = Path(request["contract_path"])
    if file_pin(path)["sha256"] != request["contract_sha256"]:
        raise ValueError("Dispatched contract drift")
    contract = read_json(path)
    if request["contract"] != contract or contract["limits"] != LIMITS:
        raise ValueError("Exact frozen no-value contract required")
    output = Path(contract["output"]) / "native"
    log = Evidence(output)
    result = dict(
        schema="owned-native-control-result/v1",
        operation_id=args.operation_id,
        request_sha256=args.request_sha256,
        complete=True,
        control_completed=False,
        evidence_complete=False,
        stage="preflight",
        counts=dict.fromkeys(LIMITS, 0),
        known_effects=[],
        required_evidence=[],
        verification=dict(status="incomplete", reason="No native control completed"),
        rules_saved=False,
        old_C_D_E_recovered=False,
        product_can_apply=False,
        owner_client_pid=os.getpid(),
        COM_thread=threading.get_ident(),
    )
    owner = None
    initialized = False
    process = None
    try:
        _, minimum, base, layout, support = import_dependencies(contract)
        minimum.client_environment(dict(environment=contract["environment"]), log)
        report, clsid = runtime_preflight(contract, minimum, base, layout, support)
        log.file("runtime-preflight.json", report)
        import pythoncom
        import win32com.client

        from xpedition_cli._vendor.tool_driver_sdk.scope import ScopeResolver
        from xpedition_cli._vendor.tool_driver_sdk.store import StateStore, target_key

        # Verify actual dispatch, not only an echoed operation parameter.
        state = StateStore(contract["journal"])
        try:
            actual_record = state.get(args.operation_id)
            if actual_record is None or actual_record["status"] != "dispatched":
                raise ValueError("Original journal has no matching active dispatch")
            if actual_record["request_digest"] != request["contract_sha256"]:
                raise ValueError("Original dispatch digest differs from frozen contract")
        finally:
            state.close()

        owner = Owner(contract, log, minimum, base, layout, support)
        result["counts"] = owner.counts
        owner.protected("before-activation")
        verify_inventory(contract["closed_input_inventory"])
        before = process_inventory()
        log.file("processes-before.json", before)
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        initialized = True
        log.event(
            "owner_STA_initialized",
            durable=True,
            client_pid=os.getpid(),
            thread=threading.get_ident(),
            bounded=True,
        )
        log.event("Layout_activation_started", durable=True, clsid=str(clsid), maximum=1)
        owner.stage = "Activation"
        owner.counts["Activation"] += 1
        CAD_DISPATCHED = True
        raw = pythoncom.CoCreateInstance(
            clsid, None, pythoncom.CLSCTX_LOCAL_SERVER, pythoncom.IID_IDispatch
        )
        owner.application = win32com.client.Dispatch(raw)
        owner.application_unknown = owner.application._oleobj_.QueryInterface(
            pythoncom.IID_IUnknown
        )
        raw = None
        actual_type = owner.describe_type(owner.application)
        gui = owner.native.call(owner.application, "MGCPCB", "IMGCPCBApplication", "Gui")
        hwnd = owner.native.call(gui, "MGCPCB", "IMGCPCBGui", "HWND")
        gui = None
        process = owner.process.observe_window(hwnd)
        after = process_inventory()
        role = classify_process_roles(
            before, after, process, expected_worker=contract["layout_executable"]["path"]
        )
        log.file("new-process-role.json", role)
        owner.identity_expected = dict(
            contract["identity_expected"], forbidden_processes=[dict(pid=r["pid"]) for r in before]
        )
        owner.identity("pre_open")
        original = owner.last_identity["activation_application"]
        owner.identity_expected["activation"] = dict(
            pid=process["pid"],
            birth_filetime_100ns=process["birth_filetime_100ns"],
            hwnd=hwnd,
            executable=process["executable"],
            instance_guid=original["instance_guid"]["value"],
        )

        target = dict(
            mode="session",
            engine_id="xpedition-pcb",
            instance_id=str(process["pid"]),
            object_id=contract["board_name"],
            locator=Path(contract["project"]).as_uri(),
        )
        env = read_json(contract["environment"]["path"])
        ScopeResolver("xpedition-cli", environ=env).resolve().require(
            effects=request["effects"],
            target=target,
            paths=[
                (contract["project"], "observe"),
                (str(Path(contract["project"]).parent), "persist"),
            ],
            outputs=[dict(path=contract["output"], effect="artifact")],
        )
        keys = [
            target_key(dict(target, mode=m, locator=Path(contract[f]).as_uri()))
            for m in ("file", "session")
            for f in ("project", "board")
        ]
        keys.append(target_key(dict(target, object_id="pcb", locator="xpedition://pcb")))
        state = StateStore(contract["journal"])
        try:
            if state.blocking(keys, exclude=()):
                raise ValueError("Actual newly observed PID is blocked; no Open")
        finally:
            state.close()
        log.file(
            "actual-PID-scope.json", dict(target=target, target_keys=keys, conflict_exclude=[])
        )
        owner.protected("before-open")
        verify_inventory(contract["closed_input_inventory"])
        owner.identity("pre_open")
        owner.stage = "Open"
        returned = owner.native.call(
            owner.application,
            "MGCPCB",
            "IMGCPCBApplication",
            "OpenDocument",
            contract["board"],
            kind=METHOD,
        )
        if returned is None:
            raise ValueError("OpenDocument returned null; no retry")
        owner.authenticate(returned)
        owner.document = owner.authenticate(
            owner.native.call(owner.application, "MGCPCB", "IMGCPCBApplication", "ActiveDocument")
        )
        owner.document_unknown = getattr(owner.document, "_oleobj_", owner.document).QueryInterface(
            pythoncom.IID_IUnknown
        )
        returned_path = owner.native.call(returned, "MGCPCB", "IMGCPCBDocument", "FullName")
        same_document = owner.same_iunknown(returned, owner.document)
        log.file(
            "Open-return-observation.json",
            dict(
                type=owner.describe_type(returned),
                path=returned_path,
                same_IUnknown_as_retained=same_document,
                original_application_type=actual_type,
            ),
        )
        if normalized(returned_path) != normalized(contract["board"]):
            raise ValueError("Open returned wrong Document path")
        returned = None
        owner.identity("post_open")
        owner.requirements.append(
            log.file(
                "F-after-open-before-load-files.json",
                observe_files(Path(contract["project"]).parent),
            )
        )
        phase = run_noedit(owner)
        owner.phase_result = phase
        phase_pin = log.file("noedit-phase-result.json", phase)
        result["required_evidence"] = owner.requirements + [phase_pin]
        result["phase"] = phase
        if not phase["control_complete"]:
            raise ValueError("No-value control stopped; exact release facts retained")
        owner.close_successful_control()
        result.update(
            control_completed=True,
            evidence_complete=True,
            stage="control_completed",
            verification=dict(
                status="passed",
                reason=(
                    "Original-chain no-value Load/read/UnLoad and independent Layout passed; "
                    "conditional Close(False)/Quit returned. No rule persistence proof."
                ),
            ),
            actual_process=process,
            known_effects=[
                dict(
                    id="owned-F-session",
                    change="Owned F Open, no-value Load/read/UnLoad and conditional Close/Quit",
                    evidence_ref=str(output / "noedit-phase-result.json"),
                )
            ],
        )
        result["required_evidence"] = owner.requirements + [phase_pin]
    except BaseException as exc:
        result["stage"] = owner.stage if owner else "preflight"
        result["error"] = dict(
            type=type(exc).__name__, message=str(exc), hresult=getattr(exc, "hresult", None)
        )
        result["evidence_complete"] = all(v == 0 for v in result["counts"].values())
        result["verification"] = dict(
            status="failed" if result["evidence_complete"] else "incomplete",
            reason="Exact stopped stage; no retry, guessed Save, Close or Quit",
        )
        log.safe_event("control_stopped", stage=result["stage"], error=result["error"])

    if owner is not None:
        result["native_call_count"] = len(owner.native.calls)
        result["licensing_object_activations"] = owner.licensing_calls
        result["ConstraintsAuto_activations"] = owner.cma_activations
        result["last_identity"] = owner.last_identity
        result["missing_required_evidence"] = owner.missing_required_evidence
        result["phase"] = owner.phase_result
        if not result["control_completed"] and CAD_DISPATCHED:
            hold = min(max(int(contract.get("failure_hold_seconds", 60)), 0), 120)
            result["bounded_failure_owner_hold_seconds"] = hold
            result["owner_reference_expires_at"] = (
                datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=hold)
            ).isoformat()
            log.file("stopped-owner-holding.json", result)
            end = time.monotonic() + hold
            while time.monotonic() < end:
                pythoncom.PumpWaitingMessages()
                time.sleep(0.25)
        owner.params = owner.design = owner.auto = owner.document = owner.application = None
        owner.application_unknown = owner.document_unknown = None
        gc.collect()
        log.safe_event(
            "client_references_ended",
            native_UnLoad_proof=bool(
                owner.phase_result and owner.phase_result["cleanup"]["unload_returned_void"]
            ),
            service_lock_release_not_inferred=True,
        )
    if initialized:
        pythoncom.CoUninitialize()
        result["explicit_CoUninitialize_returned"] = True
    if result["control_completed"]:
        exit_observation = observe_exit(owner.process, result["actual_process"])
        result["OS_exit_observation"] = exit_observation
        result["required_evidence"].append(log.file("owned-process-exit.json", exit_observation))
        after_files = observe_files(Path(contract["project"]).parent)
        result["required_evidence"].append(log.file("F-after-control-files.json", after_files))
        before_files = read_json(contract["closed_input_inventory"]["path"])
        delta = files_delta(before_files, after_files)
        result["required_evidence"].append(log.file("F-disk-delta.json", delta))
        result["disk_before"] = contract["closed_input_inventory"]
        result["disk_after"] = file_pin(output / "F-after-control-files.json")
        changed = bool(delta["added"] or delta["removed"] or delta["changed"])
        result["disk_observation"] = dict(
            before=contract["closed_input_inventory"],
            after=file_pin(output / "F-after-control-files.json"),
            delta=file_pin(output / "F-disk-delta.json"),
            changes_observed=changed,
            coverage_complete=delta["complete"],
        )
        result["native_auto_writes_not_manual_Save"] = dict(
            observed=changed,
            manual_Save_count=0,
            attribution="Session window includes Open/Load/UnLoad/Close; stage lists separate",
        )
        if not exit_observation["exited"]:
            result["control_completed"] = False
            result["stage"] = "process_exit_incomplete"
            result["verification"] = dict(
                status="incomplete",
                reason="No-value control passed; exact process generation exit not established",
            )
    finalize_required_evidence(result, owner)
    result["noncritical_event_errors"] = log.errors
    result["finished_at"] = datetime.datetime.now(datetime.UTC).isoformat()
    log.file("result.json", result)
    log.stream.close()
    return 0 if result["control_completed"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:

        def argument(name):
            return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else None

        marker = dict(
            schema="owned-control-early-failure/v1",
            operation_id=argument("--operation-id"),
            request_sha256=argument("--request-sha256"),
            native_dispatch_state="unknown" if CAD_DISPATCHED else "not_started",
            counts=None if CAD_DISPATCHED else dict.fromkeys(LIMITS, 0),
            error=dict(type=type(exc).__name__, message=str(exc)),
        )
        sys.stderr.write(json.dumps(marker, ensure_ascii=False) + "\n")
        raise SystemExit(2) from None
