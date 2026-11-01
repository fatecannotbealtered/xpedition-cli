"""Two formal fake object relationships; no CAD, COM instance or process query."""

import pytest
from designer_identity_fakes import FakeComError, Scene, hard_no_native  # noqa: F401

from xpedition_cli import designer_window_identity as W
from xpedition_cli import native_identity as N
from xpedition_cli import native_release_preflight as R


@pytest.mark.parametrize("framework", [False, True])
def test_complete_formal_native_object_binding(tmp_path, framework):
    scene = Scene(tmp_path, framework=framework)
    proof = scene.run()
    assert proof["status"] == "observed"
    assert proof["source"] == (W.SOURCE_FRAMEWORK if framework else W.SOURCE_APP)
    assert scene.window_reads == scene.generation_reads == 2
    assert scene.framework_reads == (2 if framework else 0)
    assert W.validate(scene.identity(proof), scene.rows())
    assert not any(
        word in value for value in scene.calls for word in ("Activate", "Open", "Save", "Tcl")
    )
    assert scene.app_unknown is not scene.framework_unknown


def test_zero_direct_window_can_use_other_formal_provider_without_zero_authority(
    tmp_path,
):
    scene = Scene(tmp_path)
    scene.direct_zero = True
    proof = scene.run()
    assert proof["status"] == "observed" and proof["source"] == W.SOURCE_FRAMEWORK
    assert proof["hwnd"] > 0 and proof["attempts"][0]["observed_hwnd"] == 0
    assert W.validate(scene.identity(proof), scene.rows())


@pytest.mark.parametrize(
    "fault",
    [
        "bool",
        "negative",
        "wrong_canonical",
        "bad_pid",
        "not_window",
        "missing_birth",
        "nonascii_birth",
        "dead",
        "wrong_exe",
        "wrong_doc",
    ],
)
def test_contradiction_does_not_escape_through_second_provider(tmp_path, fault):
    scene = Scene(tmp_path)
    document = scene.document()
    if fault == "bool":
        scene.hwnd = True
    elif fault == "negative":
        scene.hwnd = -1
    elif fault == "wrong_canonical":
        scene.wrong_canonical = True
    elif fault == "bad_pid":
        scene.window["pid"] = 0
    elif fault == "not_window":
        scene.window["is_window"] = False
    elif fault == "missing_birth":
        scene.generation["birth_filetime_100ns"] = ""
    elif fault == "nonascii_birth":
        scene.generation["birth_filetime_100ns"] = "１２３"
    elif fault == "dead":
        scene.generation["alive"] = False
    elif fault == "wrong_exe":
        scene.generation["executable"] = str(tmp_path / "foreign.exe")
    elif fault == "wrong_doc":
        scene.path = str(tmp_path / "other.prj")
    proof = W.observe(
        scene.app,
        document,
        scene.exe,
        com_module=scene.com,
        window_reader=scene.window_reader,
        generation_reader=scene.generation_reader,
    )
    assert proof["status"] != "observed"
    assert scene.framework_reads == 0


@pytest.mark.parametrize(
    "fault",
    ["dispid", "invkind", "args", "major", "return_iid", "drift", "unsupported"],
)
def test_formal_framework_requires_declared_interface_and_stable_own_identity(tmp_path, fault):
    scene = Scene(tmp_path, framework=True)
    if fault == "dispid":
        scene.info.dispid = 241
    elif fault == "invkind":
        scene.info.invkind = 2
    elif fault == "args":
        scene.info.arguments = ((8, 1),)
    elif fault == "major":
        scene.info.major = 150
    elif fault == "return_iid":
        scene.framework_info.return_guid = "00000000-0000-0000-0000-000000000000"
    elif fault == "drift":
        scene.framework_drift = True
    elif fault == "unsupported":
        scene.framework_unsupported = True
    proof = scene.run()
    assert proof["status"] != "observed"
    assert len(proof["attempts"]) == 2
    assert "secret/native" not in str(proof)


@pytest.mark.parametrize(
    "field",
    [
        "source",
        "canonical_iunknown_equal",
        "application_iunknown_stable",
        "provider_iunknown_stable",
        "framework_declaration",
        "framework_observation",
        "rechecked",
        "native_document_binding_sha256",
        "process_generation",
        "window_observation",
        "attempts",
    ],
)
def test_release_does_not_accept_partial_claimed_binding(tmp_path, field):
    scene = Scene(tmp_path, framework=True)
    identity = scene.identity()
    del identity["designer_window_binding"][field]
    assert not W.validate(identity, scene.rows())


def test_true_native_window_mapping_does_not_override_other_domain_or_reused_pid(
    tmp_path,
):
    scene = Scene(tmp_path)
    identity = scene.identity()
    rows = scene.rows()
    rows[0]["birth_filetime_100ns"] = "987654321"
    assert not W.validate(identity, rows)
    assert not W.validate(identity, scene.rows() * 2)
    identity["domain"] = "pcb"
    assert not W.validate(identity, scene.rows())


@pytest.mark.parametrize("framework", [False, True])
def test_actual_application_identity_integration_uses_formal_source(
    monkeypatch, tmp_path, framework
):
    scene = Scene(tmp_path, framework=framework)
    scene.install_dependencies(monkeypatch)
    identity = N.application_identity(scene.app, "schematic")
    assert identity["pid"] == 40
    assert W.validate(identity, scene.rows())
    pin = R._application_pin(identity, "schematic", scene.rows())
    assert pin["designer_native_identity"] == identity


def test_failed_providers_stay_diagnostic_and_cannot_authorize_release(tmp_path):
    scene = Scene(tmp_path, framework=True)
    scene.framework_unsupported = True
    proof = scene.run()
    rejected = {
        "domain": "schematic",
        "pid": 40,
        "type_library": scene.identity(Scene(tmp_path).run())["type_library"],
        "public_fields": {
            "FullName": scene.exe,
            "Name": "Xpedition Designer",
            "Version": "2604.fixture",
            "ActiveViewHandle": 0,
        },
        "document": scene.document(),
        "designer_window_binding": proof,
        "pid_source": {"property": "FullName", "matching_process": scene.rows()[0]},
    }
    with pytest.raises(Exception) as failure:
        R._application_pin(rejected, "schematic", scene.rows())
    assert failure.value.code == "E_HUMAN_REQUIRED"


def test_foreign_external_error_text_not_reported(tmp_path):
    scene = Scene(tmp_path)

    def secret():
        raise ValueError("private path and credential")

    scene.app.IsProjectOpened = secret
    proof = scene.run()
    assert proof["status"] != "observed" and "private path" not in str(proof)


def test_non_interface_hresult_does_not_claim_unsupported_iolewindow(tmp_path):
    scene = Scene(tmp_path)

    def failure(iid):
        raise FakeComError(-2147467259)

    scene.app._oleobj_.QueryInterface = failure
    proof = scene.run()
    assert proof["status"] != "observed" and len(proof["attempts"]) == 1


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("pid", "process_pid_differs_from_window"),
        ("birth", "process_birth_missing_or_invalid"),
        ("alive", "process_not_alive"),
        ("image", "process_image_differs_from_native_executable"),
    ],
)
def test_refusal_identifies_safe_concrete_generation_dimension(tmp_path, fault, reason):
    scene = Scene(tmp_path)
    if fault == "pid":
        scene.generation["pid"] = 999
    elif fault == "birth":
        scene.generation["birth_filetime_100ns"] = ""
    elif fault == "alive":
        scene.generation["alive"] = False
    elif fault == "image":
        scene.generation["executable"] = str(tmp_path / "foreign.exe")
    refusal = scene.run()["attempts"][0]
    assert refusal["reason"] == reason
    assert refusal["stage"] == "process_generation"
    assert refusal["observed_window"]["pid"] == 40
    assert "observed_generation" in refusal
    assert str(tmp_path) not in str(refusal)
    assert "123456789" not in str(refusal)


def test_readonly_unique_executable_fallback_cannot_authorize_writer(monkeypatch, tmp_path):
    from xpedition_cli import native_process as P

    scene = Scene(tmp_path, framework=True)
    scene.framework_unsupported = True
    scene.install_dependencies(monkeypatch)
    monkeypatch.setattr(P, "unique_executable_pid", lambda domain, image: scene.rows()[0])
    identity = N.application_identity(scene.app, "schematic")
    assert identity["pid_source"]["property"] == "FullName"
    assert identity["designer_window_binding"]["status"] == "incomplete"
    with pytest.raises(Exception) as failure:
        R._application_pin(identity, "schematic", scene.rows())
    assert failure.value.code == "E_HUMAN_REQUIRED"


def test_framework_typeattr_uses_real_iid_shape_without_guid(tmp_path):
    scene = Scene(tmp_path, framework=True)
    attr = scene.framework_info.GetTypeAttr()
    assert hasattr(attr, "iid") and not hasattr(attr, "guid")
    with pytest.raises(AttributeError):
        _ = attr.guid  # The C10 implementation's deterministic API mismatch.
    proof = scene.run()
    assert proof["status"] == "observed"
    assert proof["framework_observation"]["typeattr_iid_present"] is True
    assert proof["framework_observation"]["observed_interface_iid"] == W.FRAMEWORK_IID
    assert W.validate(scene.identity(proof), scene.rows())


@pytest.mark.parametrize(
    "fault,point,reason",
    [
        ("getter_missing", "framework.getter_call", "framework_declared_getter_unavailable"),
        ("getter_error", "framework.getter_call", "native_interface_read_failed"),
        ("missing_dispatch", "framework.return_dispatch", "framework_dispatch_wrapper_missing"),
        ("typeinfo_error", "framework.return_typeinfo", "native_interface_read_failed"),
        ("missing_iid", "framework.typeattr_iid", "framework_typeattr_iid_missing"),
    ],
)
def test_framework_prerequisite_refusal_is_precise_and_redacted(tmp_path, fault, point, reason):
    from types import SimpleNamespace

    scene = Scene(tmp_path, framework=True)

    def secret_error():
        raise AttributeError("private path / credential / raw native diagnostic")

    if fault == "getter_missing":
        del scene.app.GetFramework
    elif fault == "getter_error":
        scene.app.GetFramework = secret_error
    elif fault == "missing_dispatch":
        scene.app.GetFramework = lambda: SimpleNamespace()
    elif fault == "typeinfo_error":
        scene.framework._oleobj_.GetTypeInfo = secret_error
    elif fault == "missing_iid":
        scene.framework_info.GetTypeAttr = lambda: SimpleNamespace(guid=W.FRAMEWORK_IID)
    proof = scene.run()
    refusal = proof["attempts"][-1]
    assert refusal["source"] == W.SOURCE_FRAMEWORK
    assert refusal["stage"] == "formal_provider"
    assert refusal["call_point"] == point and refusal["reason"] == reason
    assert "private path" not in str(proof) and "raw native" not in str(proof)
    assert "IOleWindow.GetWindow" not in scene.calls


@pytest.mark.parametrize("fault", ["window", "generation", "document", "app_unknown"])
def test_read_recheck_detects_real_changes(tmp_path, fault):
    scene = Scene(tmp_path)
    original = scene.generation_reader

    if fault == "window":
        original_window = scene.window_reader

        def changed_window(hwnd):
            if scene.window_reads:
                scene.window["thread_id"] = 99
            return original_window(hwnd)

        scene.window_reader = changed_window

    def changed(pid):
        if scene.generation_reads:
            if fault == "generation":
                scene.generation["birth_filetime_100ns"] = "987654321"
            elif fault == "document":
                scene.path = str(tmp_path / "other.prj")
            elif fault == "app_unknown":
                scene.app_unknown = object()
        return original(pid)

    scene.generation_reader = changed
    assert scene.run()["status"] != "observed"
