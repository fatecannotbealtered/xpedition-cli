"""Pure fake boundary tests for retained inputs; no native API is executed."""

from __future__ import annotations

import copy
import ctypes
import hashlib
import json
import threading
from types import SimpleNamespace

import pytest

from xpedition_cli import startup_input_seal as S


class FakeApi:
    def __init__(self):
        self.nodes, self.files, self.events, self.closed = {}, {}, [], []
        self.next = 10
        self.fail = None

    def _opened(self, kind, obj, disposition):
        self.next += 1
        self.nodes[self.next] = {"kind": kind, "object": obj}
        return {"handle": self.next, "disposition": disposition, "ntstatus": 0}

    def require_local_fixed_drive(self, drive):
        self.events.append(("local_drive", drive))

    def require_ntfs(self, handle):
        self.events.append(("ntfs", handle))
        if self.fail == "filesystem":
            raise S.SealError("startup_input_contract_incomplete")

    def directory_open(self, name, root, *, create):
        self.events.append(("directory_create" if create else "ancestor_open", name, root))
        if create and self.fail == "partial_ntstatus":
            opened = self._opened("workspace", self._info(0x10), 0)
            opened.update(ntstatus=-1, winerror=5)
            return opened
        return self._opened(
            "workspace" if create else "ancestor", self._info(0x10), 2 if create else 1
        )

    def workspace_reopen(self, name, root):
        assert set(self.files) == S.INPUT_NAMES
        assert all(
            handle in self.closed
            for handle, node in self.nodes.items()
            if node["kind"] == "creator"
        ), "the empty or still-writable workspace must not be reopened share3"
        self.events.append(("workspace_reopen_share3", name, root))
        obj = next(node["object"] for node in self.nodes.values() if node["kind"] == "workspace")
        if self.fail == "replacement_identity":
            obj = {**obj, "file_id": (99, 99)}
        opened = self._opened("workspace", obj, 1)
        self.replacement = opened["handle"]
        if self.fail == "replacement_partial_ntstatus":
            opened.update(ntstatus=-1, winerror=5)
        if self.fail == "replacement_file_drift":
            self.files["identity.vbs"]["last_write_100ns"] += 1
        return opened

    def _info(self, attributes):
        return {
            "file_id": (1, self.next + 100),
            "attributes": attributes,
            "bytes": 0,
            "links": 1,
            "last_write_100ns": 0,
        }

    def file_create(self, name, root):
        self.events.append(("file_create", name, root))
        info = self._info(0x20)
        self.files[name] = info
        return self._opened("creator", info, 2)

    def metadata_open(self, name, root):
        self.events.append(("metadata_open", name, root))
        return self._opened("metadata", self.files[name], 1)

    def final_dos_path(self, handle):
        return (
            r"\\?\C:\synthetic\attempt\inputs"
            if self.fail != "canonical_alias"
            else r"\\?\C:\other\inputs"
        )

    def win32_metadata_open(self, path, *, directory):
        if directory:
            obj = next(
                node["object"] for node in self.nodes.values() if node["kind"] == "workspace"
            )
        else:
            obj = self.files[path.name]
        if self.fail == "interop_identity":
            obj = {**obj, "file_id": (99, 99)}
        return self._opened("interop", obj, 1)

    def access(self, handle):
        kind = self.nodes[handle]["kind"]
        if self.fail == "replacement_access" and handle == getattr(self, "replacement", None):
            return S.DIRECTORY_ACCESS | 2
        if self.fail == "ancestor_mask" and kind == "ancestor":
            return S.METADATA_ACCESS
        if self.fail == "metadata_execute" and kind == "metadata":
            return S.METADATA_ACCESS | 0x20
        return {
            "ancestor": S.DIRECTORY_ACCESS,
            "workspace": S.DIRECTORY_ACCESS,
            "creator": S.CREATOR_MASK,
            "metadata": S.METADATA_ACCESS,
            "interop": S.METADATA_ACCESS,
        }[kind]

    def info(self, handle):
        return copy.deepcopy(self.nodes[handle]["object"])

    def security_exact(self, handle, mask):
        kind = self.nodes[handle]["kind"]
        if self.fail == "replacement_security" and handle == getattr(self, "replacement", None):
            return False
        if self.fail == "security" and kind in ("workspace", "metadata"):
            return False
        if self.fail == "birth_security" and kind == "creator":
            return False
        return mask == (S.DIRECTORY_MASK if kind == "workspace" else S.FILE_MASK)

    def write_read_hash(self, handle, payload):
        self.events.append(("actual_write_flush_readback", handle))
        self.nodes[handle]["object"].update(bytes=len(payload), last_write_100ns=100)
        return "0" * 64 if self.fail == "hash" else hashlib.sha256(payload).hexdigest()

    def close(self, handle):
        self.events.append(("close", handle))
        original = next((h for h, node in self.nodes.items() if node["kind"] == "workspace"), None)
        if self.fail == "workspace_close" and handle == original:
            raise OSError(5, "private original workspace close failure")
        if self.fail == "post_transition_drift" and handle == original:
            self.files["identity.vbs"]["last_write_100ns"] += 1
        if self.fail == "close" and self.nodes[handle]["kind"] in ("creator", "metadata"):
            raise OSError(5, "private close failure")
        if self.fail == "interop_close" and self.nodes[handle]["kind"] == "interop":
            raise OSError(5, "private transient close failure")
        assert handle not in self.closed, "no duplicate/unknown close retry"
        if self.nodes[handle]["kind"] == "creator":
            self.nodes[handle]["object"]["last_write_100ns"] += 1
        self.closed.append(handle)


PAYLOADS = {"identity.vbs": b"private-capability-fixture", "scripts.ini": b"fixed-fixture"}


def _lease(monkeypatch, failure=None):
    api = FakeApi()
    api.fail = failure
    monkeypatch.setattr(S, "_make_api", lambda: api)
    return S.InputsLease(r"C:\synthetic\attempt\inputs"), api


def test_constructor_inert_then_prepare_pins_and_verifies_retained_metadata(monkeypatch):
    lease, api = _lease(monkeypatch)
    assert api.events == [] and lease.directory is None
    assert lease.facts()["immutable_files_retained"] is None
    assert lease.prepare(PAYLOADS) is lease
    facts = lease.verify_prelaunch()
    assert facts["input_contract_verified"] is True
    assert facts["native_seal_enabled"] is False
    assert facts["file_count"] == 2
    assert facts["verification"] == "metadata_and_dacl_only"
    assert facts["scheme"] == "startup-inputs-retain-only-v2"
    assert facts["pin_policy_version"] == "prepared_nonempty_workspace_share3_v1"
    assert facts["workspace_pin_share"] == 3
    assert facts["workspace_pin_active"] is True
    assert facts["workspace_pin_transition_complete"] is True
    assert facts["ancestor_pin_share_policy"] == 1
    assert next(i for i, row in enumerate(api.events) if row[0] == "ntfs") < next(
        i for i, row in enumerate(api.events) if row[0] == "directory_create"
    )
    assert len([row for row in api.events if row[0] == "actual_write_flush_readback"]) == 2
    assert all(
        node["kind"] != "creator" for handle, node in api.nodes.items() if handle not in api.closed
    )
    assert "private-capability" not in json.dumps(facts)
    binding = lease.private_binding()
    assert (
        binding["files"]["identity.vbs"]["sha256"]
        == hashlib.sha256(PAYLOADS["identity.vbs"]).hexdigest()
    )
    assert "handle" not in json.dumps(binding)
    released = lease.terminal_release()
    assert released["resources_released"] is True and released["files_removed"] is False
    assert released["immutable_files_retained"] is True
    assert len(api.closed) == len(api.nodes)
    assert released["input_contract_verified"] is False
    assert released["workspace_pin_active"] is False


def test_workspace_share3_opens_only_after_sealed_children_and_retires_only_old_workspace(
    monkeypatch,
):
    lease, api = _lease(monkeypatch)
    assert lease.facts()["workspace_pin_share"] is None
    lease.prepare(PAYLOADS)
    retired = [h for h in lease._holders if h["kind"] == "retired_workspace"]
    workspace = [h for h in lease._holders if h["kind"] == "workspace"]
    assert len(retired) == len(workspace) == 1
    assert retired[0]["state"] == "closed" and workspace[0]["state"] == "open"
    assert retired[0]["identity"]["file_id"] == workspace[0]["identity"]["file_id"]
    reopened = next(i for i, row in enumerate(api.events) if row[0] == "workspace_reopen_share3")
    original_handle = next(h for h, node in api.nodes.items() if node["kind"] == "workspace")
    retired_close = api.events.index(("close", original_handle))
    assert reopened < retired_close
    assert all(h["state"] == "open" for h in lease._holders if h["kind"] == "ancestor")
    assert not any(
        ("close", h["handle"]) in api.events for h in lease._holders if h["kind"] == "ancestor"
    )
    lease.terminal_release()


@pytest.mark.parametrize(
    "failure,category",
    [
        ("replacement_identity", "identity_mismatch"),
        ("replacement_security", "security_mismatch"),
        ("replacement_access", "access_mask_mismatch"),
        ("replacement_file_drift", "identity_mismatch"),
    ],
)
def test_invalid_workspace_replacement_keeps_original_pin_and_never_claims_transition(
    monkeypatch, failure, category
):
    lease, api = _lease(monkeypatch, failure)
    with pytest.raises(S.SealError) as caught:
        lease.prepare(PAYLOADS)
    assert caught.value.category == "startup_input_" + category
    original = next(h for h in lease._holders if h["kind"] == "workspace")
    assert original["state"] == "open"
    facts = lease.facts()
    assert facts["input_contract_verified"] is False
    assert facts["workspace_pin_transition_complete"] is False
    assert facts["workspace_pin_share"] == 1
    api.fail = None
    assert lease.terminal_release()["resources_released"] is True


@pytest.mark.parametrize("failure", ["workspace_close", "replacement_partial_ntstatus"])
def test_unknown_pin_transition_retains_all_handle_responsibility_without_retry(
    monkeypatch, failure
):
    lease, api = _lease(monkeypatch, failure)
    with pytest.raises(S.SealError):
        lease.prepare(PAYLOADS)
    assert lease.facts()["workspace_pin_transition_complete"] is False
    assert lease.facts()["input_contract_verified"] is False
    unknown = next(h for h in lease._holders if h["state"] == "close_unknown")
    attempts = api.events.count(("close", unknown["handle"]))
    with pytest.raises(S.SealError, match="startup_input_handle_release_failed"):
        lease.terminal_release()
    assert api.events.count(("close", unknown["handle"])) == attempts
    assert lease.facts()["resources_released"] is not True


def test_drift_after_original_pin_retirement_is_not_verified_or_successful_transition(monkeypatch):
    lease, api = _lease(monkeypatch, "post_transition_drift")
    with pytest.raises(S.SealError, match="startup_input_identity_mismatch"):
        lease.prepare(PAYLOADS)
    assert lease.facts()["workspace_pin_share"] == 3
    assert lease.facts()["workspace_pin_transition_complete"] is False
    assert lease.facts()["input_contract_verified"] is False
    assert any(h["kind"] == "workspace" and h["state"] == "open" for h in lease._holders)
    api.fail = None
    assert lease.terminal_release()["resources_released"] is True


def test_no_unprepared_lease_can_request_workspace_share3(monkeypatch):
    lease, api = _lease(monkeypatch)
    with pytest.raises(S.SealError, match="startup_input_contract_incomplete"):
        lease._replace_prepared_workspace_pin({}, {}, "inputs")
    assert api.events == []


@pytest.mark.parametrize(
    "payloads",
    [
        {},
        {"other": b"x"},
        {"identity.vbs": b"x", "scripts.ini": bytearray(b"x")},
        {"identity.vbs": b"x" * 65536, "scripts.ini": b"x"},
    ],
)
def test_contract_and_size_limits_refuse_before_native_api(monkeypatch, payloads):
    monkeypatch.setattr(S, "_make_api", lambda: pytest.fail("no native call"))
    with pytest.raises(S.SealError):
        S.InputsLease(r"C:\synthetic\inputs").prepare(payloads)


@pytest.mark.parametrize(
    "workspace",
    [
        r"\\server\share\inputs",
        "relative",
        r"C:\one\..\inputs",
        r"C:\one.\inputs",
        r"C:\NUL.txt\inputs",
        r"C:\COM1\inputs",
    ],
)
def test_ambiguous_or_nonlocal_workspace_refuses_before_native_api(monkeypatch, workspace):
    monkeypatch.setattr(S, "_make_api", lambda: pytest.fail("no native call"))
    with pytest.raises(S.SealError):
        S.InputsLease(workspace).prepare(PAYLOADS)


@pytest.mark.parametrize(
    "failure,category",
    [
        ("filesystem", "contract_incomplete"),
        ("ancestor_mask", "access_mask_mismatch"),
        ("metadata_execute", "access_mask_mismatch"),
        ("security", "security_mismatch"),
        ("hash", "readback_mismatch"),
    ],
)
def test_partial_failure_retains_handles_until_explicit_terminal_release(
    monkeypatch, failure, category
):
    lease, api = _lease(monkeypatch, failure)
    with pytest.raises(S.SealError) as caught:
        lease.prepare(PAYLOADS)
    assert caught.value.category == "startup_input_" + category
    assert lease.facts()["input_contract_verified"] is False
    assert len(lease._holders) == len(api.nodes)
    api.fail = None
    assert lease.terminal_release()["resources_released"] is True
    assert len(api.closed) == len(api.nodes)


@pytest.mark.parametrize("change", ["last_write_100ns", "links", "attributes"])
def test_file_metadata_drift_is_not_noise(monkeypatch, change):
    lease, api = _lease(monkeypatch)
    lease.prepare(PAYLOADS)
    api.files["identity.vbs"][change] += 1
    with pytest.raises(S.SealError, match="startup_input_identity_mismatch"):
        lease.verify_prelaunch()
    assert lease.facts()["input_contract_verified"] is False
    lease.terminal_release()


def test_partial_nt_handle_is_retained_and_never_guessed_closed(monkeypatch):
    lease, api = _lease(monkeypatch, "partial_ntstatus")
    with pytest.raises(S.SealError, match="startup_input_native_api_failed"):
        lease.prepare(PAYLOADS)
    uncertain = lease._holders[-1]
    assert uncertain["state"] == "close_unknown" and uncertain["handle"] in api.nodes
    with pytest.raises(S.SealError, match="startup_input_handle_release_failed"):
        lease.terminal_release()
    assert api.closed == []


def test_terminal_failure_revokes_verified_bit_before_any_close_and_cannot_retry(monkeypatch):
    lease, api = _lease(monkeypatch)
    lease.prepare(PAYLOADS)
    api.fail = "close"
    with pytest.raises(S.SealError, match="startup_input_handle_release_failed"):
        lease.terminal_release()
    facts = lease.facts()
    assert facts["state"] == "release_failed" and facts["input_contract_verified"] is False
    attempted = [row for row in api.events if row[0] == "close"]
    with pytest.raises(S.SealError, match="startup_input_handle_release_failed"):
        lease.terminal_release()
    for row in attempted:
        assert api.events.count(row) == 1


def test_unknown_birth_security_retains_protection_and_never_claims_readonly(monkeypatch):
    lease, api = _lease(monkeypatch, "birth_security")
    with pytest.raises(S.SealError, match="startup_input_security_mismatch"):
        lease.prepare(PAYLOADS)
    assert lease.facts()["file_count"] == 1
    assert lease.facts()["birth_read_only_verified_count"] == 0
    assert not any(row[0] == "actual_write_flush_readback" for row in api.events)
    with pytest.raises(S.SealError, match="startup_input_security_mismatch"):
        lease.terminal_release()
    assert lease.facts()["immutable_files_retained"] is None
    assert api.closed == []


@pytest.mark.parametrize("failure", ["canonical_alias", "interop_identity", "interop_close"])
def test_canonical_and_ordinary_path_failures_never_qualify_input_contract(monkeypatch, failure):
    lease, api = _lease(monkeypatch, failure)
    with pytest.raises(S.SealError):
        lease.prepare(PAYLOADS)
    assert lease.facts()["input_contract_verified"] is False
    calls = list(api.events)
    with pytest.raises(S.SealError):
        lease.verify_prelaunch()
    assert api.events == calls
    if failure == "interop_close":
        with pytest.raises(S.SealError, match="startup_input_handle_release_failed"):
            lease.terminal_release()
        assert api.events == calls
    else:
        assert lease.terminal_release()["resources_released"] is True


def test_facts_and_private_binding_require_exact_owner_thread(monkeypatch):
    lease, _ = _lease(monkeypatch)
    errors = []

    def observer():
        for operation in (lease.facts, lease.private_binding):
            try:
                operation()
            except S.SealError as error:
                errors.append(error.category)

    worker = threading.Thread(target=observer)
    worker.start()
    worker.join(1)
    assert not worker.is_alive()
    assert errors == ["startup_input_contract_incomplete"] * 2


def test_sealerror_rejects_arbitrary_nonstring_category():
    assert S.SealError({"private": "text"}).category == "startup_input_contract_incomplete"


def test_native_open_binding_keeps_root_single_component_and_original_returned_handle():
    observed = {}

    def create(
        handle,
        access,
        attributes,
        io,
        allocation,
        file_attrs,
        share,
        disposition,
        options,
        ea,
        ea_bytes,
    ):
        attrs = ctypes.cast(attributes, ctypes.POINTER(S._Attributes)).contents
        observed.update(
            root=attrs.RootDirectory,
            name=attrs.ObjectName.contents.Buffer,
            flags=attrs.Attributes,
            share=share,
            disposition=disposition,
            options=options,
            access=access,
        )
        ctypes.cast(handle, ctypes.POINTER(S.w.HANDLE)).contents.value = 345
        ctypes.cast(io, ctypes.POINTER(S._IoStatus)).contents.Information = 2
        return 0

    api = S._WindowsApi.__new__(S._WindowsApi)
    api.n = SimpleNamespace(NtCreateFile=create, RtlNtStatusToDosError=lambda value: 5)
    api.k = SimpleNamespace(LocalFree=lambda value: pytest.fail("no descriptor allocated"))
    value = api._open("inputs", 123, S.DIRECTORY_ACCESS, 1, 2, 0x21)
    assert value == {"handle": 345, "disposition": 2, "ntstatus": 0, "winerror": None}
    assert observed == {
        "root": 123,
        "name": "inputs",
        "flags": 0x1040,
        "share": 1,
        "disposition": 2,
        "options": 0x21,
        "access": S.DIRECTORY_ACCESS,
    }


def test_native_workspace_reopen_is_open_only_share3_while_all_ancestor_opens_stay_share1():
    calls = []
    api = S._WindowsApi.__new__(S._WindowsApi)
    api._open = lambda *args: calls.append(args) or {"fixture": True}
    api.directory_open("ancestor", 123, create=False)
    api.directory_open("inputs", 456, create=True)
    api.workspace_reopen("inputs", 456)
    assert calls == [
        ("ancestor", 123, S.DIRECTORY_ACCESS, 1, 1, 0x21, None),
        ("inputs", 456, S.DIRECTORY_ACCESS, 1, 2, 0x21, S.DIRECTORY_SDDL),
        ("inputs", 456, S.DIRECTORY_ACCESS, 3, 1, 0x21),
    ]


def test_true_short_write_does_not_export_stale_last_error(monkeypatch):
    def write(handle, payload, size, written, overlapped):
        ctypes.cast(written, ctypes.POINTER(S.w.DWORD)).contents.value = 1
        return True

    api = S._WindowsApi.__new__(S._WindowsApi)
    api.k = SimpleNamespace(WriteFile=write)
    monkeypatch.setattr(
        S.ctypes, "get_last_error", lambda: pytest.fail("success has no qualified last error")
    )
    with pytest.raises(S.SealError) as caught:
        api.write_read_hash(123, b"payload")
    assert caught.value.category == "startup_input_readback_mismatch"
    assert caught.value.winerror is None


def test_false_write_exports_its_numeric_native_error(monkeypatch):
    api = S._WindowsApi.__new__(S._WindowsApi)
    api.k = SimpleNamespace(WriteFile=lambda *args: False)
    monkeypatch.setattr(S.ctypes, "get_last_error", lambda: 112)
    with pytest.raises(S.SealError) as caught:
        api.write_read_hash(123, b"payload")
    assert caught.value.category == "startup_input_native_api_failed"
    assert caught.value.winerror == 112
