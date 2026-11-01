"""Whole-project release refuses incomplete/drifted writers before Save/Close."""

import copy
import json
from types import SimpleNamespace

import pytest

from xpedition_cli import native_com_adapter as adapter
from xpedition_cli import native_release_preflight as release


class Scene:
    def __init__(self, tmp_path, monkeypatch):
        self.root = tmp_path / "owned"
        (self.root / "PCB").mkdir(parents=True)
        self.project = self.root / "EXAMPLE_BOARD.prj"
        self.project.write_text("fixture", encoding="utf-8")
        self.board = self.root / "PCB/EXAMPLE_BOARD.pcb"
        self.board.write_bytes(b"fixture board")
        self.calls = []
        self.rows = {domain: [] for domain in release.DOMAINS}
        self.apps = {}
        self.designer_open = True
        self.layout_open = True
        self.add_process("schematic", 10, tmp_path / "XP/wv/win64/bin/viewdraw.exe")
        self.add_process("pcb", 20, tmp_path / "XP/wg/win64/bin/ExpeditionPCB.exe")

        def save_all():
            self.calls.append("designer.SaveAll")

        def close_project():
            self.calls.append("designer.Close")
            self.designer_open = False
            return True

        self.designer_path = self.project
        self.layout_path = self.board
        self.apps["schematic"] = SimpleNamespace(
            IsProjectOpened=lambda: self.designer_open,
            GetProjectData=lambda: SimpleNamespace(
                GetProjectFilePath=lambda: str(self.designer_path)
            ),
            Documents=SimpleNamespace(SaveAll=save_all),
            CloseProject=close_project,
        )

        class Layout:
            @property
            def ActiveDocument(inner):
                return self.document if self.layout_open else None

        def save():
            self.calls.append("layout.Save")
            return True

        def close(*args):
            self.calls.append("layout.Close")
            self.layout_open = False
            return True

        self.document = SimpleNamespace(FullName=str(self.layout_path), Save=save, Close=close)
        self.apps["pcb"] = Layout()

        monkeypatch.setattr(adapter, "processes", lambda domain: self.rows[domain])
        monkeypatch.setattr(release, "process_generation", self.generation)
        monkeypatch.setattr(adapter, "application_identity", self.identity)
        monkeypatch.setattr(
            adapter, "_viewdraw_application", lambda *a, **k: self.apps["schematic"]
        )
        monkeypatch.setattr(adapter, "_application", lambda *a, **k: self.apps["pcb"])
        monkeypatch.setattr(adapter, "_layout_board_path", lambda params: self.board)
        monkeypatch.setattr(adapter, "_licensed_document", lambda app: app.ActiveDocument)
        monkeypatch.setattr(adapter, "_settle", lambda *a: None)

    def add_process(self, domain, pid, executable, *, parent_pid=1, birth="100"):
        self.rows[domain].append(
            {
                "pid": pid,
                "parent_pid": parent_pid,
                "executable": str(executable),
                "birth_filetime_100ns": birth,
            }
        )

    def generation(self, pid):
        row = next(row for rows in self.rows.values() for row in rows if row["pid"] == pid)
        return {**row, "alive": True}

    def identity(self, app, domain):
        pid = {"schematic": 10, "pcb": 20, "library": 30}[domain]
        row = next(row for row in self.rows[domain] if row["pid"] == pid)
        prop = "ActiveViewHandle" if domain == "schematic" else "Gui.HWND"
        public = {
            "Name": "Xpedition " + domain,
            "FullName": row["executable"],
            "Version": "2604.fixture",
        }
        if domain == "pcb":
            public["InstanceGuid"] = "{BBC5A95D-A8EF-44D3-A3F0-B347F8D21DF4}"
        elif domain == "schematic":
            public["ActiveViewHandle"] = pid + 1000
        source = {"property": prop, "hwnd": pid + 1000, "api": "GetWindowThreadProcessId"}
        if domain == "library":
            source = {
                "property": "FullName",
                "matching_process": dict(row),
                "basis": "exact executable path and unique live process; ambiguity is refused",
            }
        guid, name = release.TYPE_LIBRARIES[domain]
        return {
            "domain": domain,
            "pid": pid,
            "pid_source": source,
            "type_library": {
                "guid": "{" + guid + "}",
                "name": name,
                "major": 151,
                "minor": 0,
                "lcid": 0,
                "syskind": 3,
                "flags": 8,
            },
            "public_fields": public,
        }

    def run(self):
        return adapter._let_go_of_project({"project": str(self.project)}, None)


@pytest.fixture
def scene(tmp_path, monkeypatch):
    return Scene(tmp_path, monkeypatch)


def test_matching_designer_does_not_save_before_unreadable_layout(scene, monkeypatch):
    def unavailable(*args, **kwargs):
        raise adapter.AdapterError("E_NOT_FOUND", "Layout COM not ready")

    monkeypatch.setattr(adapter, "_application", unavailable)
    with pytest.raises(adapter.AdapterError) as failure:
        scene.run()
    assert failure.value.details["write_attempted"] is False
    assert scene.calls == []


def test_identified_foreign_layout_is_untouched_while_target_designer_released(scene, tmp_path):
    scene.document.FullName = str(tmp_path / "unrelated/Other.pcb")
    result = scene.run()
    assert result["designer_closed"] is True
    assert result["layout_closed"] is False
    assert result["quiescent"] is True
    assert scene.calls == ["designer.SaveAll", "designer.Close"]


def test_target_process_birth_drift_before_dispatch_saves_nothing(scene, monkeypatch):
    reads = 0

    def changed(pid):
        nonlocal reads
        row = scene.generation(pid)
        if pid == 20:
            reads += 1
            if reads > 2:
                row["birth_filetime_100ns"] = "200"
        return row

    monkeypatch.setattr(release, "process_generation", changed)
    with pytest.raises(adapter.AdapterError) as failure:
        scene.run()
    assert failure.value.details["write_attempted"] is False
    assert scene.calls == []


def test_same_window_but_changed_application_guid_is_rejected_before_save(scene, monkeypatch):
    reads = 0

    def changed(app, domain):
        nonlocal reads
        identity = scene.identity(app, domain)
        if domain == "pcb":
            reads += 1
            if reads > 1:
                identity["public_fields"]["InstanceGuid"] = "different-instance"
        return identity

    monkeypatch.setattr(adapter, "application_identity", changed)
    with pytest.raises(adapter.AdapterError) as failure:
        scene.run()
    assert failure.value.details["write_attempted"] is False
    assert scene.calls == []


@pytest.mark.parametrize(
    "domain,fault,field",
    [
        ("pcb", "only_type_error", "type_library.guid"),
        ("pcb", "type_getter_error", "type_library_error"),
        ("pcb", "missing_type_guid", "type_library.guid"),
        ("pcb", "wrong_type_major", "type_library.major"),
        ("pcb", "wrong_type_minor", "type_library.minor"),
        ("pcb", "missing_pid_source", "pid_source"),
        ("pcb", "missing_hwnd", "pid_source.hwnd"),
        ("pcb", "missing_hwnd_api", "pid_source.api"),
        ("pcb", "missing_instance_guid", "public_fields.InstanceGuid"),
        ("pcb", "malformed_instance_guid", "public_fields.InstanceGuid"),
        ("pcb", "different_image", "public_fields.FullName"),
        ("pcb", "contradictory_pid", "public_fields.ProcessID"),
        ("schematic", "missing_view_handle", "public_fields.ActiveViewHandle"),
    ],
)
def test_incomplete_actual_identity_shape_refuses_before_all_save_close(
    scene, monkeypatch, domain, fault, field
):
    def incomplete(app, actual_domain):
        value = scene.identity(app, actual_domain)
        if domain != actual_domain:
            return value
        if fault == "only_type_error":
            value["type_library"] = {"error": "type getter failed"}
        elif fault == "type_getter_error":
            value["type_library_error"] = "RuntimeError"
        elif fault == "missing_type_guid":
            del value["type_library"]["guid"]
        elif fault == "wrong_type_major":
            value["type_library"]["major"] = 150
        elif fault == "wrong_type_minor":
            value["type_library"]["minor"] = 1
        elif fault == "missing_pid_source":
            del value["pid_source"]
        elif fault == "missing_hwnd":
            value["pid_source"]["hwnd"] = 0
        elif fault == "missing_hwnd_api":
            del value["pid_source"]["api"]
        elif fault == "missing_instance_guid":
            del value["public_fields"]["InstanceGuid"]
        elif fault == "malformed_instance_guid":
            value["public_fields"]["InstanceGuid"] = "GUID getter unavailable"
        elif fault == "different_image":
            value["public_fields"]["FullName"] = str(scene.root / "wrong/ExpeditionPCB.exe")
        elif fault == "contradictory_pid":
            value["public_fields"]["ProcessID"] = 999
        elif fault == "missing_view_handle":
            del value["public_fields"]["ActiveViewHandle"]
        return value

    monkeypatch.setattr(adapter, "application_identity", incomplete)
    with pytest.raises(adapter.AdapterError) as failure:
        scene.run()
    assert field in {gap["field"] for gap in failure.value.details["identity_gaps"]}
    assert failure.value.details["write_attempted"] is False
    assert scene.calls == []


def test_actual_designer_activeview_and_product_identity_are_retained(scene):
    result = scene.run()
    actual = result["release_preflight"]["schematic"]["application"]
    assert actual["public_fields"]["ActiveViewHandle"] == 1010
    assert actual["public_fields"]["FullName"] == scene.rows["schematic"][0]["executable"]
    assert actual["public_fields"]["Name"] == "Xpedition schematic"
    assert actual["public_fields"]["Version"] == "2604.fixture"


@pytest.mark.parametrize("missing_mapping", [False, True])
def test_library_uses_only_actual_unique_fullname_mapping_without_inventing_hwnd_guid(
    scene, monkeypatch, tmp_path, missing_mapping
):
    from xpedition_cli import native_library_manager

    scene.add_process("library", 30, tmp_path / "XP/lm/win64/bin/LibraryManager.exe", parent_pid=29)
    scene.add_process(
        "library", 29, tmp_path / "XP/common/win64/bin/LibraryManager.exe", birth="90"
    )
    app = SimpleNamespace(ActiveLibrary=SimpleNamespace(FullName=str(tmp_path / "outside/Lib.lmc")))
    scene.apps["library"] = app
    monkeypatch.setattr(
        native_library_manager, "active", lambda client: (app, "LibraryManager.Application.151")
    )
    if missing_mapping:

        def incomplete(app, domain):
            value = scene.identity(app, domain)
            if domain == "library":
                del value["pid_source"]["matching_process"]
            return value

        monkeypatch.setattr(adapter, "application_identity", incomplete)
        with pytest.raises(adapter.AdapterError) as failure:
            scene.run()
        assert "pid_source.matching_process" in {
            gap["field"] for gap in failure.value.details["identity_gaps"]
        }
        assert scene.calls == []
    else:
        result = scene.run()
        actual = result["release_preflight"]["library"]["application"]
        assert "no observed formal HWND/InstanceGuid" in actual["identity_boundary"]
        assert "InstanceGuid" not in actual["public_fields"]
        assert "hwnd" not in actual["pid_source"]
        assert actual["process_roles"] == {"worker": 30, "launcher": 29}


def test_document_target_drift_after_preflight_stops_all_saves(scene, tmp_path):
    reads = 0

    def project_path():
        nonlocal reads
        reads += 1
        return str(scene.project if reads == 1 else tmp_path / "foreign/Other.prj")

    scene.apps["schematic"].GetProjectData = lambda: SimpleNamespace(
        GetProjectFilePath=project_path
    )
    with pytest.raises(adapter.AdapterError) as failure:
        scene.run()
    assert failure.value.details["write_attempted"] is False
    assert scene.calls == []


def test_writer_appearing_after_preflight_saves_nothing(scene, monkeypatch, tmp_path):
    reads = 0

    def inventory(domain):
        nonlocal reads
        if domain == "pcb":
            reads += 1
            if reads == 3:
                scene.add_process("pcb", 21, tmp_path / "XP/wg/win64/bin/ExpeditionPCB.exe")
        return scene.rows[domain]

    monkeypatch.setattr(adapter, "processes", inventory)
    with pytest.raises(adapter.AdapterError) as failure:
        scene.run()
    assert failure.value.details["write_attempted"] is False
    assert scene.calls == []


def test_library_writer_appearing_while_layout_observed_stops_all_saves(
    scene, monkeypatch, tmp_path
):
    def changed(app, domain):
        if domain == "pcb":
            scene.add_process("library", 30, tmp_path / "XP/common/win64/bin/LibraryManager.exe")
        return scene.identity(app, domain)

    monkeypatch.setattr(adapter, "application_identity", changed)
    with pytest.raises(adapter.AdapterError) as failure:
        scene.run()
    assert failure.value.details["write_attempted"] is False
    assert scene.calls == []


def test_frozen_matching_documents_save_and_close_once(scene):
    result = scene.run()
    assert result["quiescent"] is True
    assert result["designer_closed"] is result["layout_closed"] is True
    assert scene.calls == ["designer.SaveAll", "designer.Close", "layout.Save", "layout.Close"]


def closed_designer_identity(scene, monkeypatch):
    """Native-shaped 151.0 identity after the closed sheet HWND is destroyed."""
    reference = object()
    monkeypatch.setattr(release, "_canonical_iunknown", lambda app: reference)

    def identity(app, domain):
        value = scene.identity(app, domain)
        if domain == "schematic":
            # Actual application_identity also reads this non-pinned scalar.
            value["public_fields"]["LicenseMode"] = 1
            if not scene.designer_open:
                value["pid_source"] = {
                    "property": "FullName",
                    "matching_process": dict(scene.rows[domain][0]),
                    "basis": "exact executable path and unique live process; ambiguity is refused",
                }
                value["document"] = {
                    "open": False,
                    "path": None,
                    "designs": [],
                    "status": "observed",
                    "source": "IsProjectOpened/GetProjectData public getters",
                }
        return value

    monkeypatch.setattr(adapter, "application_identity", identity)
    return identity


def test_confirmed_closed_designer_accepts_retained_identity_without_inventing_hwnd(
    scene, monkeypatch
):
    closed_designer_identity(scene, monkeypatch)
    result = scene.run()
    assert result["quiescent"] is True
    assert scene.calls == ["designer.SaveAll", "designer.Close", "layout.Save", "layout.Close"]
    evidence = result["post_close_identity"]["schematic"]
    assert evidence["same_iunknown"] is True
    assert evidence["document"] == {"open": False, "path": None, "relation": "empty"}
    assert evidence["identity"]["pid_source"]["property"] == "FullName"
    assert "hwnd" not in evidence["identity"]["pid_source"]
    assert evidence["identity"]["public_fields"]["ActiveViewHandle"] == 1010
    assert evidence["close_progress"]["closed_observed"] is True
    assert json.loads(json.dumps(evidence)) == evidence
    # The original, live pre-dispatch HWND proof remains distinct and intact.
    assert result["release_preflight"]["schematic"]["application"]["pid_source"]["hwnd"] == 1010


@pytest.mark.parametrize(
    "fault",
    [
        "different_iunknown",
        "missing_iunknown",
        "wrong_pid",
        "different_birth",
        "new_writer",
        "unknown_document",
        "other_document",
        "different_type",
        "different_name",
        "contradictory_process_id",
        "different_view_handle",
        "changed_context",
        "contradictory_identity_document",
        "unreadable_identity_document",
    ],
)
def test_closed_designer_missing_or_conflicting_proof_stops_before_layout_save(
    scene, monkeypatch, tmp_path, fault
):
    identity = closed_designer_identity(scene, monkeypatch)
    original_close = scene.apps["schematic"].CloseProject
    original_unknown = release._canonical_iunknown

    def close():
        returned = original_close()
        if fault == "different_birth":
            scene.rows["schematic"][0]["birth_filetime_100ns"] = "200"
        elif fault == "new_writer":
            scene.add_process("schematic", 11, tmp_path / "XP/wv/win64/bin/viewdraw.exe")
        elif fault == "unknown_document":
            scene.apps["schematic"].IsProjectOpened = lambda: None
        elif fault == "other_document":
            scene.apps["schematic"].IsProjectOpened = lambda: True
            scene.designer_path = tmp_path / "foreign/Other.prj"
        return returned

    scene.apps["schematic"].CloseProject = close

    def changed(app, domain):
        value = identity(app, domain)
        if domain == "schematic" and not scene.designer_open:
            if fault == "wrong_pid":
                value["pid"] = 999
            elif fault == "different_type":
                value["type_library"]["major"] = 150
            elif fault == "different_name":
                value["public_fields"]["Name"] = "Different application"
            elif fault == "contradictory_process_id":
                value["public_fields"]["ProcessID"] = 999
            elif fault == "different_view_handle":
                value["public_fields"]["ActiveViewHandle"] = 9999
            elif fault == "contradictory_identity_document":
                value["document"]["open"] = True
            elif fault == "unreadable_identity_document":
                value["document"]["error_type"] = "COMError"
        return value

    monkeypatch.setattr(adapter, "application_identity", changed)
    if fault == "different_iunknown":
        monkeypatch.setattr(
            release,
            "_canonical_iunknown",
            lambda app: original_unknown(app) if scene.designer_open else object(),
        )
    elif fault == "missing_iunknown":

        def unavailable(app):
            raise RuntimeError("QueryInterface unavailable")

        monkeypatch.setattr(release, "_canonical_iunknown", unavailable)
    elif fault == "changed_context":
        original_thread = release.threading.get_ident
        monkeypatch.setattr(
            release.threading,
            "get_ident",
            lambda: original_thread() if scene.designer_open else original_thread() + 1,
        )
    with pytest.raises(adapter.AdapterError) as failure:
        scene.run()
    assert scene.calls == ["designer.SaveAll", "designer.Close"]
    progress = failure.value.details["release_progress"]
    assert progress["designer"]["save_count"] == progress["designer"]["close_count"] == 1
    assert progress["layout"] == {"save_count": 0, "close_count": 0}
    # Unknown/other document breaks the native Close completion observation;
    # the actual Close call is still retained, never retried.
    assert progress["designer_closed"] is (fault not in {"unknown_document", "other_document"})
    assert failure.value.details["write_attempted"] is True


def test_initial_empty_designer_without_live_hwnd_still_refuses_before_any_save(scene, monkeypatch):
    closed_designer_identity(scene, monkeypatch)
    scene.designer_open = False
    with pytest.raises(adapter.AdapterError) as failure:
        scene.run()
    assert failure.value.details["write_attempted"] is False
    assert "pid_source.hwnd" in {gap["field"] for gap in failure.value.details["identity_gaps"]}
    assert scene.calls == []


def test_post_close_fallback_cannot_hide_a_later_live_hwnd_identity_change(scene, monkeypatch):
    identity = closed_designer_identity(scene, monkeypatch)

    def changed(app, domain):
        if domain == "schematic" and not scene.layout_open:
            value = scene.identity(app, domain)
            value["public_fields"]["ActiveViewHandle"] = 9999
            value["pid_source"]["hwnd"] = 9999
            return value
        return identity(app, domain)

    monkeypatch.setattr(adapter, "application_identity", changed)
    with pytest.raises(adapter.AdapterError) as failure:
        scene.run()
    assert scene.calls == ["designer.SaveAll", "designer.Close", "layout.Save", "layout.Close"]
    assert failure.value.details["release_progress"]["layout_closed"] is True
    assert failure.value.details["release_progress"]["designer_closed"] is True


def test_real_worker_with_proven_common_launcher_parent_is_not_extra_writer(scene, tmp_path):
    scene.rows["pcb"][0]["parent_pid"] = 19
    scene.add_process("pcb", 19, tmp_path / "XP/common/win64/bin/ExpeditionPCB.exe", birth="90")
    result = scene.run()
    assert result["release_preflight"]["pcb"]["application"]["process_roles"] == {
        "worker": 20,
        "launcher": 19,
    }
    assert scene.calls == ["designer.SaveAll", "designer.Close", "layout.Save", "layout.Close"]


@pytest.mark.parametrize("fault", ["wrong_parent", "different_install", "reused_parent"])
def test_unproven_same_name_process_is_not_dismissed_as_launcher(scene, tmp_path, fault):
    scene.rows["pcb"][0]["parent_pid"] = 19 if fault != "wrong_parent" else 18
    install = "different" if fault == "different_install" else "XP"
    birth = "200" if fault == "reused_parent" else "90"
    scene.add_process(
        "pcb", 19, tmp_path / install / "common/win64/bin/ExpeditionPCB.exe", birth=birth
    )
    with pytest.raises(adapter.AdapterError):
        scene.run()
    assert scene.calls == []


@pytest.mark.parametrize("fault", ["unreadable_identity", "unknown_document", "other_owned_board"])
def test_other_incomplete_layout_conditions_stop_all_saves(scene, monkeypatch, fault):
    if fault == "unreadable_identity":
        original = scene.identity
        monkeypatch.setattr(
            adapter,
            "application_identity",
            lambda app, domain: (
                {**original(app, domain), "pid": None} if domain == "pcb" else original(app, domain)
            ),
        )
    elif fault == "unknown_document":
        scene.document.FullName = ""
    else:
        scene.document.FullName = str(scene.root / "PCB/Other.pcb")
    with pytest.raises(adapter.AdapterError):
        scene.run()
    assert scene.calls == []


def test_post_close_verification_failure_retains_actual_release_facts(scene, monkeypatch):
    original = release.ReleasePreflight.revalidate
    reads = 0

    def failure(self, **kwargs):
        nonlocal reads
        reads += 1
        if reads == 3:
            raise adapter.AdapterError("E_HUMAN_REQUIRED", "Remaining state became unreadable")
        return original(self, **kwargs)

    monkeypatch.setattr(release.ReleasePreflight, "revalidate", failure)
    with pytest.raises(adapter.AdapterError) as error:
        scene.run()
    assert scene.calls == ["designer.SaveAll", "designer.Close", "layout.Save", "layout.Close"]
    assert error.value.details["release_progress"]["designer_closed"] is True
    assert error.value.details["release_progress"]["layout_closed"] is True
    assert error.value.details["write_attempted"] is True


def test_outside_layout_is_never_reacquired_or_quieted_during_release(scene, monkeypatch, tmp_path):
    scene.document.FullName = str(tmp_path / "outside/Other.pcb")
    attachments = []
    monkeypatch.setattr(adapter, "_quiet_gui", lambda *a: pytest.fail("outside Layout GUI changed"))

    def observed(*args, **kwargs):
        attachments.append(kwargs)
        if kwargs.get("quiet", True):
            adapter._quiet_gui(scene.apps["pcb"], "pcb")
        return scene.apps["pcb"]

    monkeypatch.setattr(adapter, "_application", observed)
    result = scene.run()
    assert result["quiescent"] is True
    assert attachments == [{"attach_only": True, "quiet": False}]
    assert scene.calls == ["designer.SaveAll", "designer.Close"]


def test_layout_authentication_failure_is_not_reported_as_save_dispatch(scene, monkeypatch):
    scene.designer_open = False

    def rejected(*args):
        raise adapter.AdapterError("E_AUTH", "Authentication rejected before document Save")

    monkeypatch.setattr(adapter, "_licensed_document", rejected)
    with pytest.raises(adapter.AdapterError) as failure:
        scene.run()
    progress = failure.value.details["release_progress"]
    assert progress["layout"] == {"save_count": 0, "close_count": 0}
    assert failure.value.details["write_attempted"] is False
    assert scene.calls == []


@pytest.mark.parametrize("returned", [False, None, 0, 1])
def test_release_requires_true_typed_bool_save_before_any_layout_close(scene, returned):
    def save():
        scene.calls.append("layout.Save")
        return returned

    scene.document.Save = save
    with pytest.raises(adapter.AdapterError) as failure:
        scene.run()
    progress = failure.value.details["release_progress"]["layout"]
    assert progress["save_count"] == 1
    assert progress["close_count"] == 0
    assert progress["save_reply_received"] is True
    assert progress["save_returned"] == returned
    assert type(progress["save_returned"]) is type(returned)
    assert progress["save_return_type"] == type(returned).__name__
    assert progress["save_acknowledged"] is False
    assert scene.calls == ["designer.SaveAll", "designer.Close", "layout.Save"]
    assert scene.layout_open is True


def test_release_lost_save_reply_stops_without_close_or_replay(scene):
    def save():
        scene.calls.append("layout.Save")
        raise RuntimeError("Save reply lost after dispatch")

    scene.document.Save = save
    with pytest.raises(adapter.AdapterError) as failure:
        scene.run()
    progress = failure.value.details["release_progress"]["layout"]
    assert progress["save_count"] == 1
    assert progress["close_count"] == 0
    assert progress["save_reply_received"] is False
    assert progress["save_acknowledged"] is None
    assert "save_returned" not in progress
    assert scene.calls == ["designer.SaveAll", "designer.Close", "layout.Save"]


@pytest.mark.parametrize("returned", [False, None, 0, 1])
def test_release_unverified_close_reply_keeps_independent_closed_fact(scene, returned):
    def close(*args):
        scene.calls.append("layout.Close")
        scene.layout_open = False
        return returned

    scene.document.Close = close
    with pytest.raises(adapter.AdapterError) as failure:
        scene.run()
    assert failure.value.code == "E_VERIFY_INCOMPLETE"
    assert failure.value.details["document_closed"] is True
    progress = failure.value.details["release_progress"]["layout"]
    assert progress["save_count"] == progress["close_count"] == 1
    assert progress["save_acknowledged"] is True
    assert progress["close_reply_received"] is True
    assert progress["close_acknowledged"] is False
    assert progress["close_returned"] == returned
    assert type(progress["close_returned"]) is type(returned)
    assert progress["closed_observed"] is True
    assert scene.calls == ["designer.SaveAll", "designer.Close", "layout.Save", "layout.Close"]


@pytest.mark.parametrize("adapter_error", [False, True])
def test_release_lost_close_reply_observes_without_replaying_lifecycle(scene, adapter_error):
    def close(*args):
        scene.calls.append("layout.Close")
        scene.layout_open = False
        if adapter_error:
            raise adapter.AdapterError(
                "E_TIMEOUT", "Close reply lost after dispatch", {"returned": False}
            )
        raise RuntimeError("Close reply lost after dispatch")

    scene.document.Close = close
    with pytest.raises(adapter.AdapterError) as failure:
        scene.run()
    assert failure.value.code == "E_VERIFY_INCOMPLETE"
    assert failure.value.details["document_closed"] is True
    assert "Close reply lost" in failure.value.details["native_error"]["message"]
    progress = failure.value.details["release_progress"]["layout"]
    assert progress["save_count"] == progress["close_count"] == 1
    assert progress["save_acknowledged"] is True
    assert progress["close_reply_received"] is False
    assert progress["close_acknowledged"] is None
    assert "close_returned" not in progress
    assert progress["closed_observed"] is True
    assert scene.calls == ["designer.SaveAll", "designer.Close", "layout.Save", "layout.Close"]


def test_new_writer_after_designer_close_preserves_fact_and_stops_layout_save(scene, tmp_path):
    def close_project():
        scene.calls.append("designer.Close")
        scene.designer_open = False
        scene.add_process("pcb", 21, tmp_path / "XP/wg/win64/bin/ExpeditionPCB.exe")
        return True

    scene.apps["schematic"].CloseProject = close_project
    with pytest.raises(adapter.AdapterError) as failure:
        scene.run()
    assert scene.calls == ["designer.SaveAll", "designer.Close"]
    assert failure.value.details["release_progress"]["designer_closed"] is True
    assert failure.value.details["release_progress"]["layout_closed"] is False
    assert failure.value.details["write_attempted"] is True


def test_readonly_preflight_never_authenticates_or_opens_document(scene, monkeypatch):
    monkeypatch.setattr(adapter, "_licensed_document", lambda *a: pytest.fail("authenticated"))
    plan = release.ReleasePreflight(
        scene.project,
        scene.board,
        process_reader=lambda domain: scene.rows[domain],
        generation_reader=scene.generation,
        application_reader=lambda domain: scene.apps[domain],
        identity_reader=scene.identity,
    )
    plan.revalidate()
    assert scene.calls == []


def test_internal_preflight_returns_serializable_actual_readonly_dto_without_release(
    scene, monkeypatch
):
    monkeypatch.setattr(adapter, "_licensed_document", lambda *a: pytest.fail("authenticated"))
    monkeypatch.setattr(adapter, "_close_if_open", lambda *a, **k: pytest.fail("Designer closed"))
    monkeypatch.setattr(adapter, "_save_close_board", lambda *a, **k: pytest.fail("Layout saved"))
    result = adapter._project_release_preflight({"project": str(scene.project)}, None)
    assert json.loads(json.dumps(result)) == result
    assert result["read_only"] is True
    assert result["save_count"] == result["close_count"] == 0
    assert result["project"] == str(scene.project)
    assert result["board"] == str(scene.board)
    assert result["release_preflight"]["pcb"]["document"]["relation"] == "target"
    assert scene.calls == []


def test_native_dispatch_exposes_only_readonly_preflight_without_release(scene, monkeypatch):
    events = []
    com = SimpleNamespace(
        CoInitialize=lambda: events.append("init"), CoUninitialize=lambda: events.append("uninit")
    )
    monkeypatch.setattr(adapter, "_import_com", lambda: (com, object()))
    monkeypatch.setattr(adapter, "_heal_gen_py_cache", lambda: None)
    result = adapter.dispatch("release_project_preflight", {"project": str(scene.project)})
    assert result["read_only"] is True
    assert result["save_count"] == result["close_count"] == 0
    assert events == ["init", "uninit"]
    assert scene.calls == []


def test_exact_bound_preflight_dto_allows_single_save_close_flow(scene):
    expected = adapter._project_release_preflight({"project": str(scene.project)}, None)
    result = adapter._let_go_of_project(
        {"project": str(scene.project), "expected_release_preflight": expected}, None
    )
    assert result["quiescent"] is True
    assert scene.calls == ["designer.SaveAll", "designer.Close", "layout.Save", "layout.Close"]


@pytest.mark.parametrize(
    "fault", ["process_birth", "new_writer", "board", "read_only_type", "extra_key"]
)
def test_changed_or_inexact_bound_dto_refuses_without_save_close(
    scene, monkeypatch, tmp_path, fault
):
    expected = copy.deepcopy(
        adapter._project_release_preflight({"project": str(scene.project)}, None)
    )
    if fault == "process_birth":
        scene.rows["pcb"][0]["birth_filetime_100ns"] = "200"
    elif fault == "new_writer":
        scene.add_process("pcb", 21, tmp_path / "XP/wg/win64/bin/ExpeditionPCB.exe")
    elif fault == "board":
        expected["board"] = str(tmp_path / "wrong/Other.pcb")
    elif fault == "read_only_type":
        expected["read_only"] = 1
    elif fault == "extra_key":
        expected["verified"] = True
    with pytest.raises(adapter.AdapterError) as failure:
        adapter._let_go_of_project(
            {"project": str(scene.project), "expected_release_preflight": expected}, None
        )
    assert failure.value.code in {"E_CONFLICT", "E_HUMAN_REQUIRED"}
    assert failure.value.details["write_attempted"] is False
    assert scene.calls == []


def test_readonly_preflight_reports_unreadable_layout_without_touching_designer(scene, monkeypatch):
    def unreadable(*args, **kwargs):
        raise adapter.AdapterError("E_NOT_FOUND", "Layout automation unavailable")

    monkeypatch.setattr(adapter, "_application", unreadable)
    with pytest.raises(adapter.AdapterError) as failure:
        adapter._project_release_preflight({"project": str(scene.project)}, None)
    assert failure.value.details["write_attempted"] is False
    assert failure.value.details["domain"] == "pcb"
    assert scene.calls == []
