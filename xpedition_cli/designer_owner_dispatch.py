"""Internal same-STA dispatch bridge; not a public native enable switch.

The measurement owner retains its read/empty-Quit allowlist. Public owners bind
NativeApplication._origin_handoff from the same consumed startup stream before
issuance can succeed, and re-read the actual scope per command. Objects here are an
internal capability boundary against JSON callers, not hostile in-process code.
No import initializes COM, queries a process, launches CAD or installs a service.
"""

from __future__ import annotations

import copy
import hashlib
import json
import ntpath
import re
import threading
import time
import types
from contextvars import ContextVar
from pathlib import Path

METHODS = frozenset(
    {
        "identity",
        "clone_project",
        "open",
        "snapshot",
        "apply_changeset",
        "save",
        "close",
        "draw",
        "show",
        "release_project_preflight",
        "release_project",
        "reopen_project",
        "read_project",
        "export_pdf",
    }
)
_CURRENT = ContextVar("designer_owner_dispatch", default=None)
_SEAL = object()
GRANT_CALLPOINTS = frozenset(
    {
        "bridge.native_types",
        "bridge.connection_epoch",
        "bridge.owner_thread",
        "bridge.startup_handoff",
        "bridge.native_probe",
        "bridge.typed_wrapper",
        "bridge.wrapper_canonical",
        "bridge.final_binding",
    }
)
VENDOR = Path(__file__).resolve().parent / "_vendor/spec"
WRITE_METHODS = frozenset(
    {
        "clone_project",
        "open",
        "apply_changeset",
        "save",
        "close",
        "draw",
        "show",
        "release_project",
        "reopen_project",
        "export_pdf",
    }
)
COMMAND_METHODS = {
    "identity": {
        "session start",
        "session attach",
        "session inspect",
        "session close",
        "project create",
        "schematic edit",
        "schematic build",
        "schematic view",
        "schematic export",
        "schematic check",
        "schematic parts",
        "schematic nets",
        "schematic sheets",
        "bom inspect",
        "bom check",
        "project backup",
        "project restore",
    },
    "snapshot": {
        "session start",
        "session attach",
        "session inspect",
        "session close",
        "project create",
        "schematic edit",
        "schematic build",
        "schematic view",
        "schematic export",
        "schematic check",
        "schematic parts",
        "schematic nets",
        "schematic sheets",
        "bom inspect",
        "bom check",
        "project backup",
        "project restore",
    },
    "clone_project": {"project create"},
    "open": {
        "session start",
        "session attach",
        "project create",
        "project backup",
        "project restore",
    },
    "apply_changeset": {"schematic edit"},
    "draw": {"schematic build"},
    "show": {"schematic view"},
    "save": {"schematic edit", "schematic build", "project backup", "project restore"},
    "close": {"session close"},
    "release_project_preflight": {"project backup"},
    "release_project": {"project backup"},
    "reopen_project": {"project backup"},
    "read_project": {"session inspect", "schematic sheets"},
    "export_pdf": {"schematic export"},
}


def lifecycle_capacity_intent(binding, method):
    """A capacity selector from the SDK producer; never native authority."""
    value = binding.get("lifecycle_intent") if type(binding) is dict else None
    if value is None:
        return None
    if (
        type(value) is not dict
        or set(value) not in ({"scheme", "kind", "project", "output"}, {"scheme", "kind", "pid"})
        or value.get("scheme") != "designer-owner-lifecycle-v1"
    ):
        raise DispatchError("fixed_sdk_lifecycle_intent_required")
    if (
        value["kind"] == "leave_closed_checkpoint"
        and isinstance(value.get("project"), str)
        and Path(value["project"]).is_absolute()
        and isinstance(value.get("output"), str)
        and Path(value["output"]).is_absolute()
        and binding.get("command") == "project backup"
        and method in {"identity", "snapshot", "release_project_preflight", "release_project"}
    ):
        return "closed_checkpoint"
    if (
        value["kind"] == "empty_quit"
        and type(value.get("pid")) is int
        and value["pid"] > 0
        and binding.get("command") == "session close"
        and method in {"identity", "snapshot", "close"}
    ):
        return "empty_quit"
    raise DispatchError("fixed_sdk_lifecycle_intent_required")


def validate_editing_scope(value):
    """Read the actual pinned SDK scope; no JSON verified/qualified selector."""
    from ._vendor.tool_driver_sdk.scope import ScopeResolver
    from ._vendor.tool_driver_sdk.validation import ContractValidator

    if type(value) is not dict or set(value) != {"scope_file", "scope_sha256", "scope_id"}:
        raise DispatchError("public_editing_scope_required")
    path = Path(value["scope_file"]) if isinstance(value["scope_file"], str) else None
    if (
        path is None
        or not path.is_absolute()
        or not isinstance(value["scope_sha256"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", value["scope_sha256"])
        or not isinstance(value["scope_id"], str)
        or not value["scope_id"]
    ):
        raise DispatchError("public_editing_scope_required")
    try:
        info = path.lstat()
        if (
            path.is_symlink()
            or getattr(info, "st_file_attributes", 0) & 0x400
            or info.st_size > 65536
        ):
            raise DispatchError("scope_source_binding_changed")
        before = path.read_bytes()
        if hashlib.sha256(before).hexdigest() != value["scope_sha256"]:
            raise DispatchError("scope_source_binding_changed")
        scope = ScopeResolver(
            "xpedition-cli",
            environ={"XPEDITION_CLI_SCOPE_FILE": str(path)},
            validator=ContractValidator(VENDOR),
        ).resolve()
        if not scope.granted or scope.scope_id != value["scope_id"] or path.read_bytes() != before:
            raise DispatchError("current_scope_not_granted_or_changed")
        return scope
    except DispatchError:
        raise
    except Exception:
        raise DispatchError("current_scope_read_incomplete") from None


def authorize(owner, editing_scope, method, params, sdk_operation_id, binding):
    try:
        return _authorize(owner, editing_scope, method, params, sdk_operation_id, binding)
    except Exception as error:
        error.dispatch_started = False
        raise


def capacity_authorization_scope(editing_scope, method, params, sdk_operation_id, binding):
    """Shared pure scope/binding validation; no native object acquisition or query."""
    scope = validate_editing_scope(editing_scope)
    if (
        method not in METHODS
        or type(params) is not dict
        or type(binding) is not dict
        or set(binding)
        - {
            "command",
            "scope_id",
            "scope_sha256",
            "source_operation_id",
            "source_binding",
            "closed_copy_open",
            "pdf_export",
            "lifecycle_intent",
        }
        or not isinstance(sdk_operation_id, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", sdk_operation_id)
        or binding.get("source_operation_id") != sdk_operation_id
        or binding.get("scope_id") != scope.scope_id
        or binding.get("scope_sha256") != editing_scope["scope_sha256"]
        or binding.get("command") not in COMMAND_METHODS[method]
    ):
        raise DispatchError("confirmed_sdk_authorization_binding_required")
    lifecycle_capacity_intent(binding, method)
    if method == "snapshot" and params.get("observe_only") is not True:
        raise DispatchError("read_only_snapshot_selector_required")
    if method == "release_project_preflight" and params.get("observe_only") is not True:
        raise DispatchError("read_only_release_preflight_selector_required")
    return scope


def _authorize(owner, editing_scope, method, params, sdk_operation_id, binding):
    """Dispatch preflight on the owner: same scope, fixed command and exact path."""
    scope = capacity_authorization_scope(editing_scope, method, params, sdk_operation_id, binding)
    require_service_method(owner, method, params)
    if sdk_operation_id.startswith("query-") and method not in {
        "identity",
        "snapshot",
        "release_project_preflight",
        "read_project",
    }:
        raise DispatchError("query_observation_cannot_authorize_writing")
    if method == "snapshot" and params.get("observe_only") is not True:
        raise DispatchError("read_only_snapshot_selector_required")
    if method == "release_project_preflight" and params.get("observe_only") is not True:
        raise DispatchError("read_only_release_preflight_selector_required")
    project = params.get("project") or params.get("path")
    effects = {
        "identity": ["observe"],
        "snapshot": ["observe"],
        "clone_project": ["session", "change", "persist"],
        "open": ["session"],
        "save": ["persist"],
        "close": ["session"],
        "show": ["session", "artifact"],
        "release_project_preflight": ["observe"],
        "read_project": ["observe"],
        "export_pdf": ["observe", "artifact"],
        "release_project": ["session", "persist"],
        "reopen_project": ["session"],
    }.get(method, ["change", "persist"])
    first_open = None
    first_open_report = None
    if method == "open" and owner.project is None:
        if not isinstance(project, str) or type(binding.get("closed_copy_open")) is not dict:
            raise DispatchError("closed_copy_first_open_binding_required")
        scope.require(effects=["session"], paths=[(project, "persist")])
        from . import designer_first_open as F

        owner.require_empty_project()
        try:
            first_open = F.verify_closed_copy_open_binding(
                binding["closed_copy_open"],
                project=project,
                editing_scope=editing_scope,
                sdk_operation_id=sdk_operation_id,
                owner_epoch_sha256=hashlib.sha256(
                    owner._origin_handoff._epoch.encode("ascii")
                ).hexdigest(),
                trusted_config_root=owner.trusted_config_root(),
                own_generation_reader=owner.own_generation_reader,
            )
        except Exception as error:
            error._first_open_report = _FirstOpenReport(_SEAL, _last_first_open_report(F))
            raise
        first_open_report = _last_first_open_report(F)
        if type(first_open) is not F.VerifiedClosedCopyOpen or _project_key(
            first_open.project
        ) != _project_key(project):
            raise DispatchError("verified_closed_copy_first_open_required")
        owner.require_empty_project()
    elif method == "clone_project":
        if owner.project is not None or not isinstance(project, str) or Path(project).exists():
            raise DispatchError("fresh_owner_project_target_required")
        if not isinstance(params.get("template"), str) or not isinstance(
            binding.get("source_binding"), dict
        ):
            raise DispatchError("confirmed_template_source_binding_required")
        scope.require(
            effects=["observe"], paths=[(str(Path(params["template"]).parent), "observe")]
        )
        from .designer_source_binding import setup_source_binding

        if setup_source_binding(params["template"]) != binding["source_binding"]:
            raise DispatchError("confirmed_template_source_binding_changed")
        library = binding["source_binding"].get("library")
        if not isinstance(library, str) or not Path(library).resolve().is_relative_to(
            Path(params["template"]).resolve().parent
        ):
            raise DispatchError("self_contained_template_library_required")
    elif project is not None:
        if owner.project is None or _project_key(project) != _project_key(owner.project):
            raise DispatchError("exact_bound_owner_project_required")
    elif method not in {"identity", "close", "read_project"}:
        raise DispatchError("exact_bound_owner_project_required")
    if binding.get("closed_copy_open") is not None and method == "open" and first_open is None:
        raise DispatchError("closed_copy_first_open_binding_not_reusable")
    if (
        method in {"apply_changeset", "save", "draw", "show", "export_pdf"}
        and not owner.project_open_verified
    ):
        raise DispatchError("verified_owner_project_open_required_before_edit")
    if method == "close":
        expected = params.get("expected_process_identity")
        if (
            str(params.get("pid")) != str(owner.expected.pid)
            or type(expected) is not dict
            or str(expected.get("pid")) != str(owner.expected.pid)
            or str(expected.get("creation_time_100ns")) != str(owner.expected.birth_100ns)
            or ntpath.normcase(ntpath.normpath(str(expected.get("executable"))))
            != ntpath.normcase(ntpath.normpath(owner.expected.executable))
            or params.get("save_project")
            or params.get("document_only")
        ):
            raise DispatchError("exact_empty_original_generation_close_required")
    paths = (
        [
            (
                project,
                "persist"
                if method not in {"identity", "snapshot", "release_project_preflight", "export_pdf"}
                else "observe",
            )
        ]
        if project
        else []
    )
    if method == "clone_project":
        paths.append((params["template"], "observe"))
        library = binding["source_binding"].get("library")
        if not isinstance(library, str):
            raise DispatchError("confirmed_template_source_binding_required")
        paths.append((library, "observe"))
    output = params.get("output") or params.get("png")
    if output:
        paths.append((output, "artifact"))
    if method == "export_pdf":
        expected = binding.get("pdf_export")
        if (
            type(expected) is not dict
            or not _same_pdf_binding(expected, params.get("_pdf_expectation"))
            or type(expected.get("project")) is not dict
            or not isinstance(expected["project"].get("path"), str)
            or type(expected.get("producer")) is not dict
            or expected.get("generation_id") != sdk_operation_id
            or params.get("artifact_generation") != sdk_operation_id
            or expected.get("output") != params.get("output")
            or _project_key((expected.get("project") or {}).get("path")) != _project_key(project)
            or expected.get("color") != params.get("color", 1)
            or expected.get("schematic") != (params.get("schematic") or None)
        ):
            raise DispatchError("confirmed_pdf_export_binding_required")
        producer = (expected.get("producer") or {}).get("path")
        if not isinstance(producer, str) or not producer or not output:
            raise DispatchError("confirmed_pdf_producer_and_output_required")
        paths.append((producer, "observe"))
    scope.require(effects=effects, dangerous=method == "draw", paths=paths)
    return {
        "project": project,
        "effects": effects,
        "first_open": first_open,
        "first_open_observation_report": first_open_report,
    }


def _project_key(value):
    return ntpath.normcase(ntpath.normpath(str(Path(value).resolve())))


def _same_pdf_binding(left, right):
    try:
        return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(
            right, sort_keys=True, allow_nan=False
        )
    except (TypeError, ValueError):
        return False


class DispatchError(ValueError):
    """Fixed reason only; dispatch_started is not a claim of native completion."""

    def __init__(self, reason, *, dispatch_started=False):
        super().__init__(reason)
        self.dispatch_started = dispatch_started


class _Grant:
    __slots__ = ("owner", "handoff", "epoch", "app", "thread", "ident", "closed", "busy", "seal")

    def __init__(self, seal, owner, handoff, epoch, app):
        if seal is not _SEAL:
            raise DispatchError("opaque_owner_grant_required")
        self.seal, self.owner, self.handoff, self.epoch, self.app = seal, owner, handoff, epoch, app
        self.thread, self.ident = threading.current_thread(), threading.get_ident()
        self.closed = self.busy = False

    def __repr__(self):
        return "<DesignerOwnerDispatchGrant>"


class _ReadonlyObservationRejection:
    """Internal provenance for one rejected read, never a caller selector."""

    __slots__ = ("seal", "grant", "quarantine", "method", "consumed")

    def __init__(self, seal, grant, quarantine, method):
        if seal is not _SEAL:
            raise DispatchError("opaque_quarantined_observation_required")
        self.seal, self.grant, self.quarantine = seal, grant, quarantine
        self.method, self.consumed = method, False


class _FirstOpenReport:
    """Diagnostic provenance only; never an opening or writing capability."""

    __slots__ = ("seal", "report")

    def __init__(self, seal, report):
        if seal is not _SEAL:
            raise DispatchError("internal_first_open_report_required")
        self.seal, self.report = seal, report


def _last_first_open_report(module):
    """Read only the helper just invoked, without masking its original result."""
    try:
        report = module.last_observation_report()
        if type(report) is not dict or len(json.dumps(report, allow_nan=False).encode()) > 16384:
            return None
        return copy.deepcopy(report)
    except Exception:
        return None


def first_open_failure_observation_report(error):
    proof = getattr(error, "_first_open_report", None)
    if type(proof) is not _FirstOpenReport or proof.seal is not _SEAL:
        return None
    return copy.deepcopy(proof.report)


def _handoff_ready(owner, handoff, epoch):
    if (
        getattr(owner, "_origin_handoff", None) is not handoff
        or getattr(handoff, "_epoch", None) != epoch
        or getattr(handoff, "_expected", None) is not owner.expected
        or getattr(handoff, "_pin", None) is not owner.lease
        or getattr(handoff, "_stream", None) is not None
    ):
        raise DispatchError("same_consumed_startup_handoff_required")
    state = handoff.snapshot()
    if (
        state.get("received") != 1
        or state.get("callback_body_completed") != 1
        or state.get("accepted") != 1
        or state.get("rejected") != 0
        or state.get("inflight") != 0
        or state.get("closed_to_new_calls") is not False
        or state.get("reason") is not None
    ):
        raise DispatchError("completed_authenticated_startup_handoff_required")


def _canonical(owner, app):
    raw = getattr(app, "_oleobj_", None)
    if raw is None or raw.QueryInterface(owner.com.IID_IUnknown) != owner.unknown:
        raise DispatchError("typed_application_canonical_mismatch")


def _wrap_existing(owner):
    # Dispatch an existing PyIDispatch only; strings/ProgIDs/CLSID activation are
    # impossible on this path. The wrapper owns its own reference on this STA.
    raw_type = owner.com.TypeIIDs.get(owner.com.IID_IDispatch)
    if raw_type is None or type(owner.raw) is not raw_type:
        raise DispatchError("retained_native_idispatch_required")
    import win32com.client

    return win32com.client.Dispatch(owner.raw)


def _trace(owner, step, event, **fields):
    trace = getattr(owner, "boundary", None)
    if trace is not None:
        trace.emit(step, event, **fields)


def _wrapper_observation(app):
    cls = type(app)
    module, name = cls.__module__, cls.__name__
    if any(
        not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,180}", value)
        for value in (module, name)
    ):
        raise DispatchError("wrapper_python_type_identity_incomplete")
    method = next(
        (vars(base).get("NewProject") for base in cls.__mro__[:8] if "NewProject" in vars(base)),
        None,
    )
    signature = False
    if type(method) is types.FunctionType:
        code = method.__code__
        signature = (
            code.co_argcount == 5
            and code.co_kwonlyargcount == 0
            and code.co_varnames[:5]
            == ("self", "ProjectPath", "CentralLibraryPath", "ServerName", "ProjectTemplatePath")
            and set(code.co_names) == {"_oleobj_", "InvokeTypes", "LCID"}
            and method.__globals__.get("LCID") == 0
            and 251 in code.co_consts
            and 1 in code.co_consts
            and (11, 0) in code.co_consts
            and ((8, 1), (8, 1), (8, 1), (8, 1)) in code.co_consts
        )
    return {
        "module": module,
        "class": name,
        "new_project_signature_observed": signature,
        "new_project_declaration_observation": "observed_matching"
        if signature
        else ("observed_incompatible" if type(method) is types.FunctionType else "unobserved"),
    }


def check_new_project_wrapper(app):
    """Inspect the actual selected class, then the same raw native member ID."""
    active = _CURRENT.get()
    if active is None or active[1] != "clone_project":
        raise DispatchError("owner_clone_context_required")
    grant = active[0]
    owner = _check(grant)
    if app is not grant.app:
        raise DispatchError("exact_selected_owner_wrapper_required")
    observation = _wrapper_observation(app)
    _trace(owner, "create.wrapper_selected", "observed", wrapper=observation)
    if observation["new_project_declaration_observation"] == "observed_incompatible":
        raise DispatchError("current_wrapper_new_project_signature_required")
    from . import native_com_adapter as A

    _trace(owner, "create.NewProject.role151", "begin")
    owner.probe()
    _trace(owner, "create.NewProject.role151", "end", canonical_stable=True)
    member = A._native_call(
        "create.NewProject.member", lambda: owner.raw.GetIDsOfNames(0, "NewProject")
    )
    if type(member) is not int or member != 251:
        raise DispatchError("native_new_project_member_mapping_differs")
    _trace(
        owner,
        "create.NewProject.declaration",
        "observed",
        declaration={
            "dispid": 251,
            "invoke_kind": 1,
            "return_vartype": 11,
            "argument_vartypes": [8, 8, 8, 8],
        },
        declaration_basis="current_role_151_and_native_member_251",
    )


class _LastErrorRead:
    __slots__ = ("seal", "grant", "raw", "consumed", "method", "operation")

    def __init__(self, seal, grant, operation="new_project"):
        if seal is not _SEAL:
            raise DispatchError("prepared_last_error_reader_required")
        self.seal, self.grant, self.raw, self.consumed = seal, grant, grant.owner.raw, False
        self.method, self.operation = _CURRENT.get()[1], operation


def prepare_new_project_last_error(app):
    return _prepare_last_error(app, "new_project")


def _prepare_last_error(app, operation):
    """Verify names before the operation, never after its False reply."""
    active = _CURRENT.get()
    if active is None or (operation == "new_project" and active[1] != "clone_project"):
        raise DispatchError("owner_last_error_context_required")
    grant = active[0]
    owner = _check(grant)
    if app is not grant.app:
        raise DispatchError("exact_selected_owner_wrapper_required")
    from . import native_com_adapter as A

    prefix = "create." if operation == "new_project" else "project."
    for name, expected in (("GetLastErrorId", 240), ("GetLastErrorString", 239)):
        member = A._native_call(
            prefix + name + ".member", lambda name=name: owner.raw.GetIDsOfNames(0, name)
        )
        if type(member) is not int or member != expected:
            raise DispatchError("native_last_error_member_mapping_differs")
    return _LastErrorRead(_SEAL, grant, operation)


def _empty_last_error():
    return {
        "new_project_acknowledgement": False,
        "getters_order": [240, 239],
        "last_error_id": None,
        "id_observation": "unobserved",
        "id_hresult": None,
        "text_observation": "unobserved",
        "text_hresult": None,
        "text_characters": None,
        "text_utf8_bytes": None,
        "text_sha256": None,
        "private_text_retained": None,
        "retention_observation": "not_attempted",
        "causal_attribution": "unproven",
    }


def validate_last_error_summary(value):
    """Only fixed observations and safe scalars; the original text never enters."""
    if type(value) is dict and "open_project_acknowledgement" in value:
        if "new_project_acknowledgement" in value:
            raise DispatchError("last_error_summary_contract_invalid")
        normalized = dict(value)
        normalized["new_project_acknowledgement"] = normalized.pop("open_project_acknowledgement")
        validate_last_error_summary(normalized)
        return copy.deepcopy(value)
    if type(value) is not dict or set(value) != set(_empty_last_error()):
        raise DispatchError("last_error_summary_contract_invalid")
    if (
        value["new_project_acknowledgement"] is not False
        or value["getters_order"] != [240, 239]
        or value["causal_attribution"] != "unproven"
    ):
        raise DispatchError("last_error_summary_contract_invalid")
    for key in (
        "last_error_id",
        "id_hresult",
        "text_hresult",
        "text_characters",
        "text_utf8_bytes",
    ):
        if value[key] is not None and type(value[key]) is not int:
            raise DispatchError("last_error_summary_contract_invalid")
    if (
        any(
            not isinstance(value[key], str)
            or value[key]
            not in {
                "unobserved",
                "observed",
                "exception",
                "wrong_type",
                "encoding_error",
                "over_limit",
            }
            for key in ("id_observation", "text_observation")
        )
        or not isinstance(value["retention_observation"], str)
        or value["retention_observation"]
        not in {
            "not_attempted",
            "retained",
            "incomplete",
            "over_limit",
            "encoding_error",
        }
    ):
        raise DispatchError("last_error_summary_contract_invalid")
    if (
        value["private_text_retained"] is not None
        and type(value["private_text_retained"]) is not bool
    ):
        raise DispatchError("last_error_summary_contract_invalid")
    if value["text_sha256"] is not None and (
        not isinstance(value["text_sha256"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", value["text_sha256"])
    ):
        raise DispatchError("last_error_summary_contract_invalid")
    return copy.deepcopy(value)


def capture_new_project_last_error(prepared):
    if type(prepared) is not _LastErrorRead or prepared.operation != "new_project":
        raise DispatchError("same_unused_last_error_reader_required")
    return _capture_last_error(prepared)


def _capture_last_error(prepared):
    """240 then 239 exactly once, same retained raw, no post-False metadata reads."""
    active = _CURRENT.get()
    if (
        type(prepared) is not _LastErrorRead
        or prepared.seal is not _SEAL
        or prepared.consumed
        or active is None
        or active != (prepared.grant, prepared.method)
        or threading.current_thread() is not prepared.grant.thread
        or threading.get_ident() != prepared.grant.ident
        or prepared.grant.owner.raw is not prepared.raw
    ):
        raise DispatchError("same_unused_last_error_reader_required")
    prepared.consumed = True
    owner, result = prepared.grant.owner, _empty_last_error()
    if prepared.operation == "open_project":
        result["open_project_acknowledgement"] = result.pop("new_project_acknowledgement")
    prefix = "create." if prepared.operation == "new_project" else "project."

    def optional_trace(step, event, **fields):
        try:
            _trace(owner, step, event, **fields)
        except Exception:
            pass  # An already known False must remain the primary observation.

    for key, name, member, vartype in (
        ("id", "GetLastErrorId", 240, owner.com.VT_I4),
        ("text", "GetLastErrorString", 239, owner.com.VT_BSTR),
    ):
        step = prefix + name
        optional_trace(step, "begin")
        try:
            value = prepared.raw.InvokeTypes(member, 0, 1, (vartype, 0), ())
        except Exception as error:
            hr = getattr(error, "hresult", None)
            result[key + "_observation"] = "exception"
            result[key + "_hresult"] = hr if type(hr) is int else None
            optional_trace(step, "error", error={"type": type(error).__name__, "hresult": hr})
            continue
        if key == "id":
            result["id_observation"] = "observed" if type(value) is int else "wrong_type"
            result["last_error_id"] = value if type(value) is int else None
            optional_trace(step, "end", result=result["last_error_id"])
        else:
            result["text_observation"] = "observed" if isinstance(value, str) else "wrong_type"
            if isinstance(value, str):
                result["text_characters"] = len(value)
                if len(value) > 8192:
                    result["text_observation"] = result["retention_observation"] = "over_limit"
                    result["private_text_retained"] = False
                else:
                    try:
                        raw = value.encode("utf-8")
                        result["text_utf8_bytes"] = len(raw)
                        if len(raw) > 8192:
                            result["text_observation"] = result["retention_observation"] = (
                                "over_limit"
                            )
                            result["private_text_retained"] = False
                        else:
                            result["text_sha256"] = hashlib.sha256(raw).hexdigest()
                            trace = getattr(owner, "boundary", None)
                            if trace is not None:
                                trace.retain_last_error_text(value, operation=prepared.operation)
                                result["private_text_retained"] = True
                                result["retention_observation"] = "retained"
                    except UnicodeError:
                        result["text_observation"] = result["retention_observation"] = (
                            "encoding_error"
                        )
                    except Exception:
                        result["retention_observation"] = "incomplete"
            optional_trace(
                step,
                "end",
                result={"type": "str", "length": result["text_characters"]}
                if result["text_characters"] is not None
                else None,
            )
    optional_trace(
        "create.NewProject.last_error"
        if prepared.operation == "new_project"
        else "project.OpenProject.last_error",
        "observed",
        last_error=result,
    )
    return validate_last_error_summary(result)


def configure_automation(grant, editing_scope):
    owner = _check(grant)
    validate_editing_scope(editing_scope).require(effects=["session"])
    _trace(owner, "automation.scope_verified", "observed", verified=True)
    _trace(owner, "automation.probe_before", "begin")
    if owner.probe().get("native_project_open") is not False:
        raise DispatchError("fresh_empty_session_required_before_automation_configuration")
    _trace(owner, "automation.probe_before", "end", canonical_stable=True)
    from . import native_com_adapter as A

    with A._owner_boundary_trace(getattr(owner, "boundary", None)):
        result = owner.configure_silent_mode()
    _check(grant)
    _trace(owner, "automation.probe_after", "begin")
    if owner.probe().get("native_project_open") is not False:
        raise DispatchError("empty_session_changed_during_automation_configuration")
    _trace(owner, "automation.probe_after", "end", canonical_stable=True)
    return result


def _project_context(app=None, project=None):
    active = _CURRENT.get()
    if active is None:
        raise DispatchError("same_owner_project_context_required")
    grant = active[0]
    owner = _check(grant)
    if app is not None and app is not grant.app:
        raise DispatchError("exact_selected_owner_wrapper_required")
    if owner.project is None or (
        project is not None and _project_key(owner.project) != _project_key(project)
    ):
        raise DispatchError("exact_bound_owner_project_required")
    return grant, owner


def require_service_method(owner, method, params):
    pending_count = getattr(owner, "_dual_count_probe", None)
    if (
        pending_count is not None
        and pending_count.resources_released is False
        and method in WRITE_METHODS
    ):
        raise DispatchError("dual_count_cleanup_pending_blocks_engineering_write")
    pending_document = getattr(owner, "_dual_document_call", None)
    if (
        pending_document is not None
        and pending_document.resources_released is False
        and method in WRITE_METHODS
    ):
        raise DispatchError("fixed_document_cleanup_pending_blocks_engineering_write")
    if owner.readonly_quarantine is not None and (
        method not in {"identity", "read_project", "snapshot", "close"}
        or (method == "snapshot" and params.get("observe_only") is not True)
    ):
        raise DispatchError("readonly_quarantine_blocks_engineering_write")


def quarantine_rejected_creation(grant, error, *, sdk_operation_id, command_id):
    """A known negative reply grants only further read/empty-Quit observation."""
    from . import native_com_adapter as A

    if (
        type(error) is not A.AdapterError
        or error.code != "E_PROJECT_INVALID"
        or not (
            error.details.get("acknowledgement") is False
            or error.details.get("open_acknowledgement") is False
        )
    ):
        return None
    owner = _check(grant)
    rejected_open = error.details.get("open_acknowledgement") is False
    if owner.project is None or owner.project_origin is None:
        return None
    if not rejected_open and (
        type(owner.project_origin) is not dict
        or owner.project_origin.get("kind") != "new_project"
        or owner.project_origin.get("sdk_operation_id") != sdk_operation_id
    ):
        return None
    observed = owner.probe()
    owner.project_open_verified = False
    owner.readonly_quarantine = {
        "reason": "open_project_returned_false" if rejected_open else "new_project_returned_false",
        "failed_sdk_operation_id": sdk_operation_id,
        "failed_owner_command_id": command_id,
        "native_project_open_observed": observed["native_project_open"],
        "original_failed_result_changed": False,
        "engineering_writes_enabled": False,
    }
    return copy.deepcopy(owner.readonly_quarantine)


def continue_quarantined_observation_rejection(grant, error):
    """Keep only an already quarantined, revalidated connection after a read.

    The marker comes from dispatch's actual read-only adapter branch, not the
    requested method name or exception details. The failed command stays failed;
    this only decides whether the owner can service another read or empty Quit.
    """
    from . import native_com_adapter as A

    proof = getattr(error, "_readonly_observation_rejection", None)
    if (
        type(proof) is not _ReadonlyObservationRejection
        or proof.seal is not _SEAL
        or proof.grant is not grant
        or proof.consumed
        or type(error) is not A.AdapterError
        or error.code not in {"E_CONFLICT", "E_NOT_FOUND"}
    ):
        return False
    proof.consumed = True
    owner = _check(grant)
    if owner.readonly_quarantine is not proof.quarantine:
        raise DispatchError("original_readonly_quarantine_changed")
    owner.probe()
    _check(grant)
    return True


def dispatch_read_project():
    active = _CURRENT.get()
    if active is None or active[1] != "read_project":
        raise DispatchError("same_owner_read_project_context_required")
    return _check(active[0]).read_project()


def open_project227(app, project):
    """Declared 151 call on the held raw object, without cache/ROT selection."""
    _grant, owner = _project_context(app, project)
    if owner.project_origin is None:
        raise DispatchError("native_project_origin_binding_required")
    owner.require_reopen_allowed()
    owner.require_empty_project()
    from . import native_com_adapter as A

    member = A._native_call(
        "project.OpenProject.member", lambda: owner.raw.GetIDsOfNames(0, "OpenProject")
    )
    if type(member) is not int or member != 227:
        raise DispatchError("native_open_project_member_mapping_differs")
    last_error_reader = _prepare_last_error(app, "open_project")
    accepted = A._native_call(
        "project.OpenProject227",
        lambda: owner.raw.InvokeTypes(
            227, 0, 1, (owner.com.VT_BOOL, 0), ((owner.com.VT_BSTR, 1),), str(project)
        ),
    )
    if type(accepted) is bool and accepted is False:
        diagnostic = _capture_last_error(last_error_reader)
        error = A.AdapterError(
            "E_PROJECT_INVALID",
            "Native OpenProject227 returned False",
            {
                "reason": "native_open_project_returned_false",
                "open_acknowledgement": False,
                "open_project_last_error": diagnostic,
            },
        )
        error.dispatch_started = True
        error.native_call_returned = True
        error.native_result_summary = False
        raise error
    if type(accepted) is not bool or accepted is not True:
        error = DispatchError("native_open_project_not_acknowledged", dispatch_started=True)
        error.native_call_returned = True
        raise error
    _check(_grant)
    return {
        "open_acknowledged": True,
        "native_project_open_observed": None,
        "canonical_stable": True,
    }


def _owner_release_preflight(owner):
    from .designer_first_open import observe_owned_designer_scene

    before = observe_owned_designer_scene(own_generation_reader=owner.own_generation_reader)
    observed = owner.probe()
    document = {
        "open": observed["native_project_open"],
        "path": owner.project if observed["native_project_open"] else None,
        "status": "observed",
    }
    if not observed["native_project_open"]:
        owner.require_empty_project()
    after = observe_owned_designer_scene(own_generation_reader=owner.own_generation_reader)
    if before != after or after.get("library") != []:
        raise DispatchError("owned_project_release_scene_changed_or_incomplete")
    return {
        "project": str(owner.project),
        "board": None,
        "library_state": {"status": "no_library_manager_process"},
        "release_preflight": {
            "schematic": {
                "processes": after["schematic"],
                "allowed_live_generations": after["allowed_live_generations"],
                "document": document,
            },
            "pcb": {"processes": after["layout"], "application": None, "document": None},
            "library": {"processes": [], "application": None, "document": None},
        },
        "read_only": True,
        "save_count": 0,
        "close_count": 0,
        "owner_profile": "same_designer_only_no_foreign_application_acquisition",
    }


def dispatch_project_lifecycle(method, params):
    """Backup's existing three methods, limited to this bound Designer only."""
    active = _CURRENT.get()
    if (
        active is None
        or active[1] != method
        or method not in {"release_project_preflight", "release_project", "reopen_project"}
    ):
        raise DispatchError("same_owner_project_lifecycle_context_required")
    grant, owner = _project_context(project=params.get("project"))
    if method == "reopen_project":
        owner.require_reopen_allowed(require_completed_close=True)
        owner.require_empty_project()
        from . import native_com_adapter as A

        A._ensure_project(grant.app, Path(owner.project))
        owner.confirm_project_open()
        return {"project": owner.project, "reopened": True, "canonical_stable": True}
    observed = _owner_release_preflight(owner)
    if method == "release_project_preflight":
        return observed
    expected = params.get("expected_release_preflight")
    if type(expected) is not dict or expected != observed:
        raise DispatchError("owner_project_release_preflight_changed")
    opened = observed["release_preflight"]["schematic"]["document"]["open"]
    lifecycle = None
    if opened:
        lifecycle = owner.save_close_project(
            lambda phase, facts: _trace(owner, "project." + phase, "observed", lifecycle=facts)
        )
    owner.require_empty_project()
    post = _owner_release_preflight(owner)
    return {
        "project": owner.project,
        "designer_closed": opened,
        "layout_closed": False,
        "quiescent": True,
        "library_state": post["library_state"],
        "board": None,
        "release_preflight": observed["release_preflight"],
        "post_close_identity": {"schematic": post["release_preflight"]["schematic"]["document"]},
        "release_progress": {
            "designer": {
                "save_count": 1 if opened else 0,
                "close_count": 1 if opened else 0,
                "lifecycle": lifecycle,
            },
            "layout": {"save_count": 0, "close_count": 0},
        },
    }


def persist_changeset_project(app, project):
    """One save/close/empty/reopen on apply_changeset's original held App."""
    grant, owner = _project_context(app, project)
    if _CURRENT.get()[1] != "apply_changeset":
        raise DispatchError("same_owner_changeset_persistence_context_required")
    before = application_identity(grant.app, "schematic")
    document = before["document"]
    if document.get("open") is not True or not document.get("designs"):
        raise DispatchError("exact_open_changeset_project_required")
    lifecycle = {
        "source": "designer_save_close_reopen",
        "save_count": 0,
        "close_count": 0,
        "reopen_count": 0,
        "save_acknowledged": False,
        "closed_observed": False,
        "close_returned": None,
        "before_identity": document,
        "before_pid": before["pid"],
    }
    saved_close = None
    try:
        saved_close = owner.save_close_project(
            lambda phase, facts: _trace(owner, "project." + phase, "observed", lifecycle=facts)
        )
        owner.require_empty_project()
        _check(grant)
        owner.require_reopen_allowed(require_completed_close=True)
        from . import native_com_adapter as A

        A._ensure_project(grant.app, Path(owner.project), lifecycle=lifecycle)
        after = application_identity(grant.app, "schematic")
        lifecycle.update(reopened_identity=after["document"], reopened_pid=after["pid"])
        if after["pid"] != before["pid"] or after["document"] != document:
            raise DispatchError(
                "reopened_changeset_project_identity_changed", dispatch_started=True
            )
        _check(grant)
    except Exception as error:
        error.lifecycle = lifecycle
        raise
    finally:
        evidence = saved_close if saved_close is not None else owner.lifecycle_snapshot()
        lifecycle.update(
            save_count=int(evidence["save_call_attempted"] is True),
            close_count=int(evidence["close_call_attempted"] is True),
            save_acknowledged=evidence["save_call_returned"],
            closed_observed=evidence["post_close_empty_observed"],
            close_returned=evidence["close_result_bool"],
        )
    return lifecycle


def run_pdf_producer(arguments, cwd):
    active = _CURRENT.get()
    if active is None or active[1] != "export_pdf":
        raise DispatchError("same_owner_pdf_export_context_required")
    grant = active[0]
    owner = _check(grant)
    result = owner.run_pdf_producer(arguments, cwd)
    _check(grant)
    return result


def issue_from_owner(owner, handoff, epoch):
    """Issue only after the same actual handoff and a successful native probe.

    No caller-provided verified flag, PID, dict or public parameter is accepted.
    The measurement command loop never issues this editing capability.
    """
    from .designer_native_owner import NativeApplication
    from .designer_owner_transport import Handoff

    point = "bridge.native_types"
    try:
        if type(owner) is not NativeApplication or type(handoff) is not Handoff:
            raise DispatchError("native_owner_and_handoff_objects_required")
        point = "bridge.connection_epoch"
        if not isinstance(epoch, str) or not re.fullmatch(r"[a-f0-9]{32}", epoch):
            raise DispatchError("private_connection_epoch_required")
        point = "bridge.owner_thread"
        owner._owner()
        point = "bridge.startup_handoff"
        _handoff_ready(owner, handoff, epoch)
        point = "bridge.native_probe"
        _trace(owner, point, "begin")
        proof = owner.probe()
        if proof.get("canonical_stable") is not True:
            raise DispatchError("completed_native_probe_required")
        _trace(owner, point, "end", canonical_stable=True)
        point = "bridge.typed_wrapper"
        _trace(owner, point, "begin")
        app = _wrap_existing(owner)
        wrapper = _wrapper_observation(app)
        _trace(owner, point, "end", wrapper=wrapper)
        if wrapper["new_project_signature_observed"]:
            _trace(
                owner,
                "bridge.wrapper.declaration",
                "observed",
                declaration={
                    "dispid": 251,
                    "invoke_kind": 1,
                    "return_vartype": 11,
                    "argument_vartypes": [8, 8, 8, 8],
                },
                declaration_basis="actual_generated_wrapper_code",
            )
        point = "bridge.wrapper_canonical"
        _canonical(owner, app)
        grant = _Grant(_SEAL, owner, handoff, epoch, app)
        point = "bridge.final_binding"
        _check(grant)
        return grant
    except Exception as error:
        error.grant_callpoint = point
        raise


def _check(grant):
    if type(grant) is not _Grant or grant.seal is not _SEAL or grant.closed:
        raise DispatchError("live_opaque_owner_grant_required")
    if threading.current_thread() is not grant.thread or threading.get_ident() != grant.ident:
        raise DispatchError("original_owner_sta_thread_required")
    owner = grant.owner
    owner._owner()
    _handoff_ready(owner, grant.handoff, grant.epoch)
    if not owner.expected.matches(owner.lease.snapshot()):
        raise DispatchError("original_launch_handle_generation_changed")
    if owner.raw is None or owner.raw.QueryInterface(owner.com.IID_IUnknown) != owner.unknown:
        raise DispatchError("retained_application_canonical_changed")
    _canonical(owner, grant.app)
    return owner


def current_application():
    """Private adapter resolver, returning the exact held typed object or None."""
    active = _CURRENT.get()
    if active is None:
        return None
    grant, _method = active
    _check(grant)
    return grant.app


def check_adapter_dispatch(method):
    active = _CURRENT.get()
    if active is None:
        return False
    grant, selected = active
    _check(grant)
    if method not in METHODS or method != selected:
        raise DispatchError("owner_dispatch_method_changed_or_unsupported")
    return True


def require_current_document_owner(owner=None, app=None):
    active = _CURRENT.get()
    if active is None or active[1] not in WRITE_METHODS:
        raise DispatchError("fixed_document_current_authorized_write_required")
    selected = _check(active[0])
    if (owner is not None and owner is not selected) or (
        app is not None and app is not active[0].app
    ):
        raise DispatchError("fixed_document_exact_current_owner_required")
    require_service_method(selected, active[1], {})
    return selected


def preflight_document_members(app):
    owner = require_current_document_owner(app=app)
    if _CURRENT.get()[1] != "clone_project":
        raise DispatchError("fixed_document_preflight_creation_context_required")
    return owner._invoke_fixed_document()


def invoke_document_member(app, operation, argument=None):
    owner = require_current_document_owner(app=app)
    if operation not in {"open_design", "save_all", "save_document"}:
        raise DispatchError("fixed_document_member_required")
    return owner._invoke_fixed_document(operation, argument)


def fixed_wire_constant(app):
    """Only the observed current151 VD_WIRE, under the existing owner grant."""
    return require_current_document_owner(app=app)._read_fixed_wire_constant()


def _read_identity(owner, app):
    from . import designer_host_bootstrap as B

    observation = B.HostObservation()
    budget = B.Budget(observation.result, time.monotonic, observation=observation)
    role = B._role(owner.raw, owner.com, budget)
    document = B._native_project(owner.raw, owner.com, budget)
    names = []
    if document["open"]:
        if owner.project is None or B.W._path(owner.project) != document["path"]:
            raise DispatchError("native_project_outside_owner_scope")
        from .native_com_adapter import _designer_design_names

        names = _designer_design_names(app)
    fields = {}
    for name in ("Name", "FullName", "Version"):
        value = getattr(app, name)
        if not isinstance(value, str) or len(value) > 4096:
            raise DispatchError("native_public_scalar_identity_incomplete")
        fields[name] = value
    if ntpath.normcase(ntpath.normpath(fields["FullName"])) != ntpath.normcase(
        ntpath.normpath(owner.expected.executable)
    ):
        raise DispatchError("native_image_differs_from_original_launch")
    if B._native_project(owner.raw, owner.com, budget) != document:
        raise DispatchError("native_document_changed_during_identity")
    return role, {**document, "designs": names}, fields


def application_identity(app, domain):
    """Same canonical app and original HANDLE; never window/ROT/PID inference."""
    active = _CURRENT.get()
    if active is None:
        return None
    grant, _method = active
    if domain != "schematic":
        raise DispatchError("owner_context_is_designer_only")
    owner = _check(grant)
    _canonical(owner, app)
    try:
        role, document, fields = _read_identity(owner, app)
    except DispatchError:
        raise
    except Exception:
        raise DispatchError("owner_identity_read_incomplete") from None
    _check(grant)
    generation = owner.lease.snapshot()
    if not owner.expected.matches(generation):
        raise DispatchError("original_launch_handle_generation_changed")
    source = "dedicated_startup_host_Application_and_authenticated_ncalrpc"
    return {
        "domain": "schematic",
        "type_library": {
            "guid": role["library_guid"],
            "name": role["library_name"],
            "major": role["library_major"],
            "minor": role["library_minor"],
            "lcid": role["library_lcid"],
        },
        "public_fields": fields,
        "product_version": fields["Version"],
        "pid": generation["pid"],
        "pid_source": {"property": source, "matching_process": copy.deepcopy(generation)},
        "document": {**document, "status": "observed", "source": source},
        "dedicated_startup_binding": {
            "source": source,
            "connection_epoch_sha256": hashlib.sha256(grant.epoch.encode("ascii")).hexdigest(),
            "original_handle_rechecked": True,
            "canonical_stable": True,
            "application_server_pid_observed": False,
            "native_writer_admitted": False,
        },
        "license_state": "unknown",
        "expiration": None,
    }


def dispatch(
    grant, method, params, *, editing_scope=None, sdk_operation_id=None, authorization_binding=None
):
    """Internal bridge only; no current measurement command reaches this entry."""
    if _CURRENT.get() is not None or (type(grant) is _Grant and grant.busy):
        raise DispatchError("one_owner_command_at_a_time_required")
    if type(grant) is _Grant and grant.seal is _SEAL:
        _trace(grant.owner, "dispatch.owner_binding", "begin")
    _check(grant)
    require_service_method(grant.owner, method, params)
    _trace(grant.owner, "dispatch.owner_binding", "end", canonical_stable=True)
    # Exact current native project must be checked before an adapter helper can
    # attempt Open/Close. A foreign project is never silently switched away.
    _trace(grant.owner, "dispatch.native_probe", "begin")
    grant.owner.probe()
    _trace(grant.owner, "dispatch.native_probe", "end", canonical_stable=True)
    if method not in METHODS or type(params) is not dict:
        raise DispatchError("fixed_owner_adapter_method_required")
    try:
        payload = json.dumps(params, allow_nan=False)
    except (TypeError, ValueError):
        raise DispatchError("bounded_json_owner_parameters_required") from None
    if len(payload.encode("utf-8")) > 1024 * 1024:
        raise DispatchError("bounded_json_owner_parameters_required")
    if any(
        not isinstance(key, str)
        or (key.startswith("_") and not (method == "export_pdf" and key == "_pdf_expectation"))
        for key in params
    ):
        raise DispatchError("private_adapter_selectors_not_accepted")
    if (
        params.get("domain", "schematic") != "schematic"
        or params.get("kind", "schematic") != "schematic"
    ):
        raise DispatchError("owner_context_is_designer_only")
    if grant.busy or _CURRENT.get() is not None:
        raise DispatchError("one_owner_command_at_a_time_required")
    if method in WRITE_METHODS and editing_scope is None:
        raise DispatchError("public_editing_scope_required")
    preflight = None
    if editing_scope is not None:
        _trace(grant.owner, "dispatch.scope_source", "begin")
        preflight = authorize(
            grant.owner, editing_scope, method, params, sdk_operation_id, authorization_binding
        )
        _trace(grant.owner, "dispatch.scope_source", "end", verified=True)
    from . import native_com_adapter as A

    target = None
    origin = None
    if method == "clone_project" or (
        method == "open" and preflight and preflight["first_open"] is not None
    ):
        target = str(Path(params.get("project") or params.get("path")).resolve())
        origin = (
            preflight["first_open"]
            if method == "open"
            else {
                "kind": "new_project",
                "sdk_operation_id": sdk_operation_id,
                "source_binding": copy.deepcopy(authorization_binding["source_binding"]),
            }
        )
    grant.busy = True
    # Bind the new physical target before the first possible native write. A
    # partial/unknown creation retains this path and cannot be changed by alias.
    if method == "clone_project":
        grant.owner.project = target
        grant.owner.project_origin = origin
    elif method == "open" and target is not None:
        grant.owner.project = target
        grant.owner.project_origin = origin
        grant.owner.project_open_verified = False
    token = _CURRENT.set((grant, method))
    quarantine_before_read = grant.owner.readonly_quarantine
    try:
        if method in WRITE_METHODS:
            grant.owner._begin_document_command()
        boundary = getattr(grant.owner, "boundary", None)
        with A._owner_boundary_trace(boundary):
            result = A._native_call(
                "adapter." + method, lambda: A.dispatch(method, copy.deepcopy(params))
            )
        if boundary is not None:
            try:
                boundary.require_complete()
            except Exception as error:
                error.native_call_returned = True
                raise
        if method == "clone_project":
            from .project_file import central_library

            library = central_library(Path(grant.owner.project))
            if library is None or not library.resolve().is_relative_to(
                Path(grant.owner.project).parent
            ):
                raise DispatchError(
                    "created_project_library_outside_owned_target", dispatch_started=True
                )
            grant.owner.confirm_project_open()
        try:
            _check(grant)
            grant.owner.probe()
        except DispatchError as error:
            error.dispatch_started = True
            raise
        if method == "open" and preflight and preflight["first_open"] is not None:
            result = {
                **result,
                "first_open_observation_report": copy.deepcopy(
                    preflight["first_open_observation_report"]
                ),
            }
        return result
    except A.AdapterError as error:
        # These fixed paths only query the same held App: identity/read_project
        # use their read-only resolvers; strict observe_only snapshot sets the
        # observer selector before _open_designer_project's path check, which
        # rejects instead of calling Open/Activate. Do not annotate writes,
        # arbitrary exceptions, COM/RPC failures, or a newly failed open/create.
        if (
            quarantine_before_read is not None
            and grant.owner.readonly_quarantine is quarantine_before_read
            and (
                method in {"identity", "read_project"}
                or (method == "snapshot" and params.get("observe_only") is True)
            )
            and error.code in {"E_CONFLICT", "E_NOT_FOUND"}
        ):
            error._readonly_observation_rejection = _ReadonlyObservationRejection(
                _SEAL, grant, quarantine_before_read, method
            )
        raise
    finally:
        if method in WRITE_METHODS:
            grant.owner._end_document_command()
        _CURRENT.reset(token)
        grant.busy = False


def close(grant):
    """Release this bridge's own reference on its STA, never close CAD or owner."""
    if type(grant) is not _Grant or grant.seal is not _SEAL:
        raise DispatchError("opaque_owner_grant_required")
    if threading.current_thread() is not grant.thread or threading.get_ident() != grant.ident:
        raise DispatchError("original_owner_sta_thread_required")
    if grant.busy:
        raise DispatchError("cannot_release_inflight_owner_command")
    grant.closed = True
    grant.app = None
