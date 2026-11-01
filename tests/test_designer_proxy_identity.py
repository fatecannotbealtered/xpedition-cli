"""Held-interface diagnostics with fake bindings and hard native-call stops."""

import json
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest
from designer_identity_fakes import hard_no_native  # noqa: F401

from xpedition_cli import designer_proxy_identity as P


class Raw:
    def __init__(self, scene, name):
        self.scene, self.name, self.queries = scene, name, 0

    def QueryInterface(self, iid):
        assert iid == "unknown"
        self.queries += 1
        if self.name == "bridge" and self.scene.wrong_canonical:
            return self.scene.foreign
        if self.queries > 1 and self.scene.drift == self.name:
            return self.scene.foreign
        return self.scene.canonical


class Scene:
    def __init__(self, tmp_path, monkeypatch):
        self.pid, self.cookie = 401, 529
        self.secret_pointer = 0x8765432112345678
        self.exe, self.project = str(tmp_path / "wv/viewdraw.exe"), str(tmp_path / "private.prj")
        self.canonical, self.foreign = object(), object()
        self.wrong_canonical, self.drift = False, None
        self.calls, self.revokes, self.generation_calls, self.doc_calls = [], [], 0, 0
        self.closed, self.revoke_fails, self.get_fails = 0, False, False
        self.clock_value = 0.0
        self.register_advance = self.decode_advance = 0
        self.get_pointer = self.secret_pointer
        self.held, self.bridge = Raw(self, "held"), Raw(self, "bridge")
        self.app = SimpleNamespace(_oleobj_=self.held)
        self.document = {"status": "observed", "open": True, "path": self.project}
        self.identity = {
            "domain": "schematic",
            "public_fields": {"FullName": self.exe, "ActiveViewHandle": 0},
            "document": self.document,
        }
        self.ownership = {
            "status": "verified",
            "domain": "schematic",
            "startup_operation_id": "op-fixture-only",
            "source_sha256": "a" * 64,
            "project": self.project,
            "generations": [
                {
                    "role": "application",
                    "pid": self.pid,
                    "creation_time_100ns": "112233445566",
                    "executable": self.exe,
                }
            ],
        }
        self.generation = {
            "pid": self.pid,
            "alive": True,
            "birth_filetime_100ns": "112233445566",
            "executable": self.exe,
        }
        self.after_generation = deepcopy(self.generation)
        self.decoded = {
            "hresult": 0,
            "server_pid": self.pid,
            "server_tid": 0,
            "server_address_present": True,
        }
        self.com = SimpleNamespace(
            IID_IUnknown="unknown",
            IID_IDispatch="dispatch",
            IID_IGlobalInterfaceTable="git",
            CLSID_StdGlobalInterfaceTable=P.STD_GIT_CLSID,
            CLSCTX_INPROC_SERVER=1,
            TypeIIDs={"dispatch": Raw},
            CoCreateInstance=self.create_git,
            ObjectFromAddress=self.from_address,
        )
        monkeypatch.setattr(P, "_typed_git", lambda: self.get_global)
        monkeypatch.setattr(P, "_pointer_value", lambda typed: typed)

        def forbidden(*args, **kwargs):
            pytest.fail("A default native or COM operation was attempted")

        monkeypatch.setattr(P, "_decode", forbidden)
        monkeypatch.setattr(P, "_process_pin", forbidden)

    def create_git(self, clsid, outer, clsctx, iid):
        assert (clsid, outer, clsctx, iid) == (P.STD_GIT_CLSID, None, 1, "git")
        self.calls.append("standard_git")
        return SimpleNamespace(
            RegisterInterfaceInGlobal=self.register, RevokeInterfaceFromGlobal=self.revoke
        )

    def register(self, raw, iid):
        assert raw is self.held and iid == "dispatch"
        self.calls.append("register_held")
        self.clock_value += self.register_advance
        return self.cookie

    def get_global(self, cookie):
        assert cookie == self.cookie
        self.calls.append("get_registered")
        if self.get_fails:
            raise ValueError("private-path/secret/address")
        return self.get_pointer

    def revoke(self, cookie):
        self.revokes.append(cookie)
        if self.revoke_fails:
            raise ValueError("private-cookie-and-address")

    def from_address(self, address, iid):
        assert address == self.secret_pointer and iid == "dispatch"
        self.calls.append("bridge_exact_dispatch")
        return self.bridge

    @contextmanager
    def pin(self, worker):
        assert worker == self.ownership["generations"][0]
        self.calls.append("pin_worker_handle")

        def read():
            self.generation_calls += 1
            return deepcopy(
                self.generation if self.generation_calls == 1 else self.after_generation
            )

        try:
            yield read
        finally:
            self.closed += 1

    def read_document(self, app):
        assert app is self.app
        self.doc_calls += 1
        if self.drift == "document" and self.doc_calls > 1:
            return {**self.document, "path": str(self.project) + "-other.prj"}
        return deepcopy(self.document)

    def decode(self, pointer, progress):
        assert self.generation_calls == 1 and self.closed == 0
        assert pointer == self.secret_pointer
        self.calls.append("decode_held_proxy_once")
        progress["api_available"] = True
        progress["api_stage"] = "call"
        progress["decode_attempts"] += 1
        self.clock_value += self.decode_advance
        progress["decode_returns"] += 1
        progress["api_stage"] = "returned"
        progress["hresult"] = f"0x{self.decoded['hresult']:08x}"
        return deepcopy(self.decoded)

    def run(self, **overrides):
        arguments = dict(
            com_module=self.com,
            process_pin_factory=self.pin,
            decoder=self.decode,
            document_reader=self.read_document,
            clock=lambda: self.clock_value,
        )
        arguments.update(overrides)
        return P.observe(self.app, self.identity, self.ownership, **arguments)


@pytest.fixture
def scene(tmp_path, monkeypatch):
    return Scene(tmp_path, monkeypatch)


def test_one_actual_typed_held_proxy_relation_is_diagnostic_only(scene):
    result = scene.run()
    assert result["status"] == "observed"
    assert result["formal_relation_observed"] is True and result["authority"] is False
    assert result["decode_attempts"] == result["decode_returns"] == 1
    assert result["read_operations"] == 14
    assert scene.generation_calls == scene.doc_calls == 2
    assert scene.revokes == [scene.cookie] and scene.closed == 1
    assert result["git"] == {
        "registered": True,
        "typed_dispatch_present": True,
        "revoke_attempted": True,
        "revoked": True,
    }
    assert scene.calls.index("pin_worker_handle") < scene.calls.index("decode_held_proxy_once")
    text = json.dumps(result)
    assert str(scene.secret_pointer) not in text and hex(scene.secret_pointer) not in text
    assert scene.exe not in text and scene.project not in text and str(scene.cookie) not in text


@pytest.mark.parametrize("tid", [0, 0xFFFF, 9876])
def test_documented_mta_na_and_sta_thread_identifiers_are_not_pid_sources(scene, tid):
    scene.decoded["server_tid"] = tid
    assert scene.run()["status"] == "observed"


def test_wrong_canonical_is_rejected_before_decode_and_cookie_is_revoked(scene):
    scene.wrong_canonical = True
    result = scene.run()
    assert result["reason"] == "proxy_bridge_canonical_mismatch"
    assert result["decode_attempts"] == 0
    assert scene.revokes == [scene.cookie] and scene.closed == 1


@pytest.mark.parametrize("pointer", [None, 0, -1, True, 2**64, "0x123", 4.5])
def test_invalid_typed_return_pointer_never_reaches_objectfromaddress(scene, pointer):
    scene.get_pointer = pointer
    result = scene.run()
    assert result["reason"] == "proxy_git_dispatch_pointer_invalid"
    assert "bridge_exact_dispatch" not in scene.calls
    assert result["decode_attempts"] == 0 and scene.revokes == [scene.cookie]


@pytest.mark.parametrize("cookie", [None, 0, -1, True, 2**32, "529"])
def test_invalid_registration_cookie_never_reaches_get_or_guessed_revoke(scene, cookie):
    scene.cookie = cookie
    result = scene.run()
    assert result["reason"] == "proxy_git_cookie_invalid"
    assert scene.revokes == [] and "get_registered" not in scene.calls


@pytest.mark.parametrize(
    "hr,reason",
    [
        (0x80070005, "proxy_decode_access_denied"),
        (0x80010113, "proxy_decode_invalid_ipid"),
        (0x80004005, "proxy_decode_native_failure"),
    ],
)
def test_actual_decode_hresult_does_not_become_success_or_auto_retry(scene, hr, reason):
    scene.decoded.update(hresult=hr, server_pid=9999, server_tid=9999, server_address_present=True)
    result = scene.run()
    assert result["reason"] == reason and result["status"] == "incomplete"
    assert "server_pid" not in result and result["formal_relation_observed"] is False
    assert result["decode_attempts"] == result["decode_returns"] == 1
    assert scene.revokes == [scene.cookie]


@pytest.mark.parametrize(
    "field,value",
    [
        ("server_pid", 888),
        ("server_pid", True),
        ("server_pid", 0),
        ("server_tid", -1),
        ("server_tid", True),
        ("server_address_present", False),
        ("server_address_present", 1),
    ],
)
def test_actual_server_fields_must_name_the_retained_owned_generation(scene, field, value):
    scene.decoded[field] = value
    result = scene.run()
    assert result["status"] == "incomplete" and result["formal_relation_observed"] is False
    assert result["decode_attempts"] == 1 and scene.revokes == [scene.cookie]


@pytest.mark.parametrize("phase", ["before", "after"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("pid", 999),
        ("alive", False),
        ("birth_filetime_100ns", "112233445567"),
        ("executable", "/foreign/viewdraw.exe"),
    ],
)
def test_same_pid_reuse_dead_or_different_image_cannot_qualify(scene, phase, field, value):
    target = scene.generation if phase == "before" else scene.after_generation
    target[field] = value
    result = scene.run()
    assert result["reason"] == f"proxy_generation_{phase}_mismatch"
    assert result["decode_attempts"] == (0 if phase == "before" else 1)
    assert scene.closed == 1


@pytest.mark.parametrize(
    "drift,reason",
    [
        ("held", "proxy_held_canonical_drift"),
        ("bridge", "proxy_bridge_canonical_drift"),
        ("document", "proxy_document_drift"),
    ],
)
def test_after_decode_object_or_native_document_drift_invalidates_relation(scene, drift, reason):
    scene.drift = drift
    result = scene.run()
    assert result["reason"] == reason and result["formal_relation_observed"] is False
    assert scene.revokes == [scene.cookie] and scene.closed == 1


def test_typed_get_failure_has_one_cookie_cleanup_without_raw_error(scene):
    scene.get_fails = True
    result = scene.run()
    assert result["reason"] == "proxy_observation_failed"
    assert scene.revokes == [scene.cookie] and scene.closed == 1
    assert "private-path" not in json.dumps(result) and result["decode_attempts"] == 0


def test_cleanup_failure_is_not_accepted_as_complete_observation(scene):
    scene.revoke_fails = True
    result = scene.run()
    assert result["reason"] == "proxy_git_registration_cleanup_failed"
    assert result["formal_relation_observed"] is False
    assert scene.revokes == [scene.cookie] and scene.closed == 1
    assert "private-cookie" not in json.dumps(result)


def test_expiry_during_registration_still_revokes_the_created_cookie_once(scene):
    scene.register_advance = P.MAX_WALL_SECONDS + 1
    result = scene.run()
    assert result["reason"] == "proxy_wall_limit" and result["decode_attempts"] == 0
    assert result["git"]["registered"] is True and result["git"]["revoked"] is True
    assert scene.revokes == [scene.cookie] and scene.closed == 1


def test_expiry_during_decode_preserves_actual_attempt_and_return_counts(scene):
    scene.decode_advance = P.MAX_WALL_SECONDS + 1
    result = scene.run()
    assert result["reason"] == "proxy_wall_limit"
    assert result["decode_attempts"] == result["decode_returns"] == 1
    assert result["hresult"] == "0x00000000" and result["api_stage"] == "returned"
    assert result["formal_relation_observed"] is False and scene.revokes == [scene.cookie]


@pytest.mark.parametrize("ownership", [None, {}, {"status": "unverified"}])
def test_missing_ownership_makes_no_native_or_bridge_call(scene, ownership):
    scene.ownership = ownership
    result = scene.run()
    assert result["reason"] == "proxy_original_start_ownership_missing"
    assert scene.calls == [] and result["decode_attempts"] == 0


def test_matching_path_and_startup_are_never_sufficient_without_decoded_relation(scene):
    scene.decoded["server_pid"] = 202
    result = scene.run()
    assert result["reason"] == "proxy_decode_server_differs_from_owned_worker"
    assert result["authority"] is False and result["formal_relation_observed"] is False


def test_malformed_native_return_is_rejected_without_address_leak(scene):
    scene.decoded["server_address"] = scene.secret_pointer
    result = scene.run()
    assert result["reason"] == "proxy_decode_result_shape_invalid"
    assert str(scene.secret_pointer) not in json.dumps(result)


def test_absent_api_is_not_counted_as_dispatched_native_call(scene):
    def unavailable(pointer, progress):
        progress["api_stage"] = "resolve"
        raise P.ObservationGap("proxy_decode_api_unavailable")

    result = scene.run(decoder=unavailable)
    assert result["reason"] == "proxy_decode_api_unavailable"
    assert result["api_available"] is False and result["api_stage"] == "resolve"
    assert result["decode_attempts"] == result["decode_returns"] == 0
    assert scene.revokes == [scene.cookie] and scene.closed == 1


def test_decoder_abi_uses_actual_client_and_u64_interface_value(monkeypatch):
    import ctypes

    class API:
        def __call__(self, pid, pointer, output):
            assert pid == P.os.getpid() and pointer == 0x1234567890ABCDEF
            assert self.argtypes[0] is ctypes.wintypes.DWORD
            assert self.argtypes[1] is ctypes.c_uint64
            assert ctypes.sizeof(output._obj) == 16
            output._obj.pid, output._obj.tid, output._obj.address = 44, 0xFFFF, 0xABCDEF01
            return 0

    api = API()

    def dll(name, **kwargs):
        assert name == "combase" and kwargs == {"use_last_error": True}
        return SimpleNamespace(CoDecodeProxy=api)

    monkeypatch.setattr(ctypes, "WinDLL", dll)
    progress = {"decode_attempts": 0, "decode_returns": 0}
    result = P._decode(0x1234567890ABCDEF, progress)
    assert result == {
        "hresult": 0,
        "server_pid": 44,
        "server_tid": 0xFFFF,
        "server_address_present": True,
    }
    assert progress == {
        "decode_attempts": 1,
        "decode_returns": 1,
        "api_available": True,
        "api_stage": "returned",
        "hresult": "0x00000000",
    }
    assert "abcdef" not in json.dumps(result).lower()


def test_missing_native_entrypoint_preserves_zero_actual_api_attempts(monkeypatch):
    import ctypes

    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: SimpleNamespace())
    progress = {"decode_attempts": 0, "decode_returns": 0}
    with pytest.raises(P.ObservationGap, match="proxy_decode_api_unavailable"):
        P._decode(123, progress)
    assert progress["api_stage"] == "resolve"
    assert progress["decode_attempts"] == progress["decode_returns"] == 0
