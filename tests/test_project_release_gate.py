"""A declared board's close is not evidence that the whole project is quiescent."""

from types import SimpleNamespace

import pytest

from xpedition_cli import native_com_adapter as bridge
from xpedition_cli import native_process, native_release_preflight, project_backup
from xpedition_cli.backends.native_xpedition import NativeBackend
from xpedition_cli.errors import CLIError


def scene(tmp_path, monkeypatch, state):
    root = tmp_path / "owned"
    (root / "PCB").mkdir(parents=True)
    project = root / "QA.prj"
    project.write_text("fixture", encoding="utf-8")
    declared = root / "PCB/A.pcb"
    declared.write_bytes(b"declared readable board")
    other = root / "PCB/B.pcb"
    other.write_bytes(b"also readable, but active")
    calls = []

    class Layout:
        reads = 0

        @property
        def ActiveDocument(self):
            self.reads += 1
            if state == "unreadable" and self.reads > 1:
                raise RuntimeError("remaining document state unreadable")
            if state in {"none", "unreadable"}:
                return None
            path = other if state == "same_project" else tmp_path / "outside/User.pcb"
            return SimpleNamespace(
                FullName=str(path),
                Save=lambda: calls.append("unexpected_save"),
                Close=lambda *args: calls.append("unexpected_close"),
            )

    layout = Layout()
    executable = str(tmp_path / "XP/wg/win64/bin/ExpeditionPCB.exe")
    process = {"pid": 10, "parent_pid": 1, "executable": executable}
    monkeypatch.setattr(bridge, "processes", lambda domain: [process] if domain == "pcb" else [])
    monkeypatch.setattr(native_process, "processes", bridge.processes)
    monkeypatch.setattr(
        native_release_preflight,
        "process_generation",
        lambda pid: {**process, "birth_filetime_100ns": "100", "alive": True},
    )
    monkeypatch.setattr(
        bridge,
        "application_identity",
        lambda *a: {
            "domain": "pcb",
            "pid": 10,
            "type_library": {
                "guid": "{EDEDED00-D5F6-4B04-8FE7-EDEDEDEDED00}",
                "name": "MGCPCB",
                "major": 151,
                "minor": 0,
                "lcid": 0,
                "syskind": 3,
                "flags": 8,
            },
            "pid_source": {"property": "Gui.HWND", "hwnd": 1010, "api": "GetWindowThreadProcessId"},
            "public_fields": {
                "FullName": executable,
                "Name": "Xpedition Layout",
                "Version": "2604.fixture",
                "InstanceGuid": "{BBC5A95D-A8EF-44D3-A3F0-B347F8D21DF4}",
            },
        },
    )
    monkeypatch.setattr(bridge, "_application", lambda *a, **k: layout)
    monkeypatch.setattr(bridge, "_release_project", lambda *a: (None, False))
    monkeypatch.setattr(bridge, "_layout_board_path", lambda params: declared)
    monkeypatch.setattr(bridge, "_settle", lambda *a: None)
    return project, layout, calls


@pytest.mark.parametrize("state", ["same_project", "unreadable"])
def test_existing_declared_board_does_not_hide_remaining_writer(tmp_path, monkeypatch, state):
    project, layout, calls = scene(tmp_path, monkeypatch, state)
    with pytest.raises(bridge.AdapterError):
        bridge._let_go_of_project({"project": str(project)}, None)
    assert layout.reads >= 1  # same-project extra board is now refused in the first preflight
    assert calls == []


@pytest.mark.parametrize("state", ["none", "outside"])
def test_remaining_absence_or_identified_outside_document_is_quiescent(
    tmp_path, monkeypatch, state
):
    project, layout, calls = scene(tmp_path, monkeypatch, state)
    result = bridge._let_go_of_project({"project": str(project)}, None)
    assert result["quiescent"] is True
    assert layout.reads >= 2
    assert calls == []


@pytest.mark.parametrize("state", ["same_project", "unreadable"])
@pytest.mark.parametrize("operation", ["backup", "restore"])
def test_public_handler_stops_before_archive_or_restore_writes(
    tmp_path, monkeypatch, cli, state, operation
):
    project, _, calls = scene(tmp_path, monkeypatch, state)
    saved = tmp_path / "existing.zip"
    project_backup.create(project, saved)

    def invoke(self, method, params, **kwargs):
        assert method == "release_project"
        try:
            return bridge._let_go_of_project(params, None)
        except bridge.AdapterError as error:
            raise CLIError(error.code, error.message, error.details) from error

    monkeypatch.setattr(NativeBackend, "status", lambda self: {"available": True})
    monkeypatch.setattr(NativeBackend, "invoke", invoke)
    monkeypatch.setattr(project_backup, "create", lambda *a, **k: pytest.fail("archive written"))
    monkeypatch.setattr(project_backup, "restore", lambda *a, **k: pytest.fail("restore written"))
    args = ["project", operation, "--project", str(project)]
    if operation == "backup":
        args += ["--output", str(tmp_path / "new.zip")]
    else:
        args += ["--backup", str(saved), "--dangerous"]
        code, plan = cli(*args, "--dry-run")
        assert code == 0
        args += ["--confirm", plan["data"]["confirm_token"]]
    code, result = cli(*args)
    assert code != 0, result
    assert calls == []
