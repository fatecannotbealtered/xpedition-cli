"""Synthetic capture and window witnesses; these tests never call native CAD."""

from __future__ import annotations

import copy
import hashlib
from types import SimpleNamespace

import pytest
from PIL import Image
from test_driver_v2 import _scope, _worker_fixture

from xpedition_cli.driver import Adapter, runtime
from xpedition_cli.schematic_view_artifacts import expectation, verify


def _application(project):
    return {
        "pid": 4242,
        "pid_source": {"hwnd": 101, "property": "ActiveViewHandle"},
        "public_fields": {
            "FullName": str(project.parent / "viewdraw.exe"),
            "ActiveViewHandle": 101,
        },
        "document": {"open": True, "path": str(project)},
        "active_project": str(project),
    }


def _capture(path, project):
    Image.new("RGB", (24, 16), "green").save(path)
    screenshot = {
        "path": str(path),
        "width": 24,
        "height": 16,
        "bytes": path.stat().st_size,
        "window_hwnd": 100,
        "window_pid": 4242,
        "bounds_px": [0, 0, 24, 16],
        "coordinate_space": "physical_pixels",
        "bounds_verified": True,
    }
    context = {
        "project": str(project),
        "sheet": 1,
        "pid": 4242,
        "executable": str(project.parent / "viewdraw.exe"),
        "application_hwnd": 101,
        "active_view_hwnd": 101,
        "window_hwnd": 100,
        "same_root_verified": True,
        "foreground_verified": True,
    }
    return {"sheet": 1, "foreground": True, "screenshot": screenshot, "capture_context": context}


def _observation(path):
    return [
        {
            "path": str(path),
            "exists": True,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size": path.stat().st_size,
        }
    ]


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "wrong-pid",
        "wrong-project",
        "wrong-after-project",
        "wrong-exe",
        "wrong-sheet",
        "wrong-path",
        "wrong-png",
        "wrong-size",
        "wrong-bounds",
        "wrong-bytes",
        "wrong-window",
        "context-missing",
        "source-missing",
    ],
)
def test_schematic_capture_requires_independent_current_window_and_bytes(tmp_path, fault):
    project = tmp_path / "test.prj"
    project.write_text("fixture")
    output = tmp_path / "schematic.png"
    application = _application(project)
    options = {"project": str(project), "sheet": 1, "output": str(output)}
    expected = expectation(options, application)
    result = _capture(output, project)
    after = {
        "application": copy.deepcopy(application),
        "artifact_observations": _observation(output),
    }

    def same_root(left, right):
        if fault == "wrong-window":
            return False
        if fault == "source-missing":
            return None
        return left == 101 and right == 100

    if fault == "wrong-pid":
        result["screenshot"]["window_pid"] = 999
    elif fault == "wrong-project":
        result["capture_context"]["project"] = str(tmp_path / "foreign.prj")
    elif fault == "wrong-after-project":
        after["application"]["document"]["path"] = str(tmp_path / "foreign.prj")
    elif fault == "wrong-exe":
        after["application"]["public_fields"]["FullName"] = str(tmp_path / "foreign.exe")
    elif fault == "wrong-sheet":
        result["capture_context"]["sheet"] = 2
    elif fault == "wrong-path":
        result["screenshot"]["path"] = str(tmp_path / "foreign.png")
    elif fault == "wrong-png":
        output.write_bytes(b"not png")
        result["screenshot"]["bytes"] = output.stat().st_size
        after["artifact_observations"] = _observation(output)
    elif fault == "wrong-size":
        result["screenshot"]["width"] = 23
    elif fault == "wrong-bounds":
        result["screenshot"]["bounds_verified"] = False
    elif fault == "wrong-bytes":
        result["screenshot"]["bytes"] += 1
    elif fault == "context-missing":
        result.pop("capture_context")
    report = verify(options, result, expected, {}, after, same_root=same_root)
    assert report["valid"] is (fault is None), report
    assert report["status"] == (
        "passed"
        if fault is None
        else "incomplete"
        if fault in {"context-missing", "source-missing"}
        else "failed"
    ), report
    assert "native ERC" in report["not_checked"]


@pytest.mark.parametrize("output_requested", [True, False])
def test_public_view_capture_receipt_is_bounded_and_no_output_stays_incomplete(
    tmp_path, monkeypatch, output_requested
):
    from xpedition_cli import win_dialogs
    from xpedition_cli.cli import common, schematic

    _scope(tmp_path, monkeypatch)
    project, output = tmp_path / "test.prj", tmp_path / "schematic.png"
    project.write_text("fixture")
    application = _application(project)
    model = {"components": [], "sheets": [{"number": 1}], "nets": [], "connections": []}
    captured = []

    class Backend:
        def invoke(self, method, params, **_):
            if method == "identity":
                return {"application": copy.deepcopy(application), "adapter": _worker_fixture()}
            if method == "snapshot":
                return copy.deepcopy(model)
            assert method == "show"
            captured.append(method)
            return (
                _capture(output, project)
                if params.get("output")
                else {"sheet": 1, "foreground": True}
            )

    backend = Backend()
    monkeypatch.setattr(schematic, "native", lambda: backend)
    monkeypatch.setattr(
        common, "reader", lambda _: SimpleNamespace(load=lambda *a, **k: (model, None))
    )
    monkeypatch.setattr(
        win_dialogs, "same_root_window", lambda left, right: left == 101 and right == 100
    )
    args = ["schematic", "view", "--project", str(project), "--sheet", "1"]
    if output_requested:
        args += ["--output", str(output)]
    with runtime(Adapter(backend), state_dir=tmp_path / "state") as cli:
        planned = cli.run([*args, "--dry-run"])
        assert planned.envelope["ok"], planned.envelope
        assert not captured
        result = cli.run([*args, "--confirm", planned.envelope["data"]["confirm_token"]])
    assert captured == ["show"]
    if not output_requested:
        assert result.envelope["error"]["code"] == "E_VERIFY_INCOMPLETE", result.envelope
        assert not output.exists()
        return
    assert result.envelope["ok"], result.envelope
    receipt = result.envelope["data"]["receipt"]
    assert receipt["outcome"]["verification"]["status"] == "passed"
    assert receipt["outcome"]["persistence"] == "unchanged"
    assert receipt["extensions"]["file_readback"]["valid"]
    assert all(not item["required"] for item in receipt["outcome"]["verification"]["not_checked"])
    assert receipt["extensions"]["persistence_independently_reopened"] is False
