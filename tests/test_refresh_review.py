"""Public SDK review regressions. Native values are explicit synthetic fixtures."""

import copy
from types import SimpleNamespace

import pytest
from test_cell_refresh import fixture_plan
from test_driver_v2 import _scope, _worker_fixture

from xpedition_cli import cell_refresh as R
from xpedition_cli import native_com_adapter as N
from xpedition_cli.cli import pcb
from xpedition_cli.driver import Adapter, runtime
from xpedition_cli.errors import CLIError


def test_read_only_geometry_state_accepts_a_board_but_refresh_requires_project(
    tmp_path, monkeypatch
):
    board = tmp_path / "board.pcb"
    doc = SimpleNamespace(FullName=str(board))
    adapter = SimpleNamespace(
        _layout_board_path=lambda p: board,
        _application=lambda *a, **k: object(),
        _licensed_document=lambda app: doc,
        AdapterError=N.AdapterError,
    )
    monkeypatch.setattr(R, "capture", lambda *args: {"fixture": True})
    assert R.observe({"project": str(board)}, None, adapter)["geometry"] == {"fixture": True}
    with pytest.raises(N.AdapterError):
        R.active({"project": str(board)}, None, adapter)


@pytest.mark.parametrize("extra", ["..OFFSET (1, 0)", "..CUSTOM\n...WIDTH 1\n...HEIGHT 1"])
def test_raw_native_pad_attributes_cannot_hide_behind_a_box(extra):
    text = '.PAD "P"\n..RECTANGLE\n...WIDTH 1\n...HEIGHT 1\n..PAD_OPTIONS USER_GENERATED_NAME\n'
    shape = R._pads(text + extra)["pads"]["P"]
    assert not R._supported_shape(shape)


def test_affected_instance_still_protects_non_target_fabrication():
    plan, after = fixture_plan()
    extra = {"type": 3, "side": 512, "layer": -1, "geometry": [], "name": "protected"}
    plan["before"]["components"][0]["geometry"][0]["fabrication"].append(copy.deepcopy(extra))
    after["components"][0]["geometry"][0]["fabrication"].append({**extra, "name": "changed"})
    result = R.compare(plan, after, plan["definitions"]["central"])
    assert not next(c for c in result["checks"] if c["id"] == "protected-objects")["passed"]


def prepare_project(project):
    library = project.parent / "Lib/Lib.lmc"
    library.parent.mkdir()
    library.write_bytes(b"synthetic library")
    project.write_text(
        project.read_text().replace('KEY PCBDesignPath ""', 'KEY PCBDesignPath "PCB/board.pcb"')
    )


def definitions_fixture():
    plan, after = fixture_plan()
    state = plan["before"]
    state["components"][0].update(placed=True, side="top")
    new = plan["definitions"]["central"]["R0805"]
    new["cell"].update(name="R0805", holes=[], silkscreen=[], assembly=[], placement=[], height=1)
    new["cell"]["pins"][0].update(x=0, y=0)
    new["padstacks"]["SMD"].update(type="PIN_SMD", hole=None)
    old = copy.deepcopy(new)
    old["padstacks"]["SMD"]["pad"]["width"] = 1.025
    defs = {
        "central": {"R0805": new},
        "local": {"R0805": old},
        "local_catalog": {"cells": [old["cell"]], "padstacks": {}},
        "central_library": "fixture.lmc",
    }
    return state, defs


@pytest.mark.parametrize(
    "fault", ["copper_oblong", "mask_oblong", "paste_oblong", "pin_45", "silkscreen_only", "height"]
)
def test_public_preview_refuses_unverifiable_delta(tmp_path, monkeypatch, project, fault):
    _scope(tmp_path, monkeypatch)
    prepare_project(project)
    state, defs = definitions_fixture()
    new, old = defs["central"]["R0805"], defs["local"]["R0805"]
    if fault.endswith("oblong"):
        key = {"copper_oblong": "pad", "mask_oblong": "mask", "paste_oblong": "paste"}[fault]
        new["padstacks"]["SMD"][key]["shape"] = "OBLONG"
    elif fault == "pin_45":
        new["cell"]["pins"][0]["rotation"] = old["cell"]["pins"][0]["rotation"] = 45
    elif fault == "silkscreen_only":
        defs["local"]["R0805"] = copy.deepcopy(new)
        new["cell"]["silkscreen"] = [{"kind": "path", "points": [[0, 0], [1, 0]], "width": 0.12}]
    else:
        new["cell"]["height"] = 2
    app = SimpleNamespace(
        Gui=SimpleNamespace(ProcessCommand=lambda *a: pytest.fail("native write"))
    )
    doc = SimpleNamespace(
        ProjectIntegration=SimpleNamespace(ForwardAnnotationStatus=N.PRJINT_IN_SYNCH)
    )
    monkeypatch.setattr(R, "active", lambda *a: (project, project.with_suffix(".pcb"), app, doc))
    monkeypatch.setattr(R, "capture", lambda *a: copy.deepcopy(state))
    monkeypatch.setattr(R, "definitions", lambda *a: copy.deepcopy(defs))

    class Backend:
        def invoke(self, method, params, **kwargs):
            if method == "identity":
                return {"application": {"pid": 4242}, "adapter": _worker_fixture()}
            if method == "snapshot":
                return {"components": [{"refdes": "R1", "uid": "62"}], "fixture": True}
            if method == "cell_refresh_state":
                return {"geometry": copy.deepcopy(state)}
            assert method == "refresh_cells" and not params.get("apply")
            try:
                return R.execute(params, None, N)
            except N.AdapterError as error:
                raise CLIError(error.code, error.message, error.details) from error

    backend = Backend()
    monkeypatch.setattr(pcb, "native", lambda: backend)
    with runtime(Adapter(backend), state_dir=tmp_path / "state") as cli:
        result = cli.run(
            [
                "pcb",
                "update",
                "--project",
                str(project),
                "--refresh-cells",
                "--dangerous",
                "--dry-run",
            ]
        )
    assert not result.envelope["ok"]
    assert result.envelope["error"]["code"] == "E_UNSUPPORTED", result.envelope
    assert result.envelope["error"]["details"]["dispatch_state"] == "not_started"


@pytest.mark.parametrize("kind", ["pad", "mask", "paste"])
def test_equal_bounds_do_not_prove_oblong_is_rectangle(kind):
    plan, state = fixture_plan()
    plan["definitions"]["central"]["R0805"]["padstacks"]["SMD"][kind]["shape"] = "OBLONG"
    assert not R.compare(plan, state, plan["definitions"]["central"])["valid"]


def test_affected_instance_keeps_its_silkscreen_and_texts():
    plan, state = fixture_plan()
    row = {"refdes": "R1", "path": [[0, 0, 0], [1, 0, 0]], "kind": "silk"}
    plan["before"]["model"]["silk"] = [row]
    state["model"]["silk"] = []
    assert not R.compare(plan, state, plan["definitions"]["central"])["valid"]


def test_updated_local_definition_with_stale_instance_silk_is_not_synced():
    plan, state = fixture_plan()
    plan["definitions"]["central"]["R0805"]["cell"]["silkscreen"] = [
        {"kind": "path", "points": [[0, 0], [1, 0]], "width": 0.12}
    ]
    state["model"]["silk"] = []
    assert not R.compare(plan, state, plan["definitions"]["central"])["valid"]


@pytest.mark.parametrize("component", [0, 90, 180, 270])
@pytest.mark.parametrize("pad", [0, 90, 180, 270])
def test_supported_orthogonal_rotation_combinations(component, pad):
    state, defs = definitions_fixture()
    comp = state["components"][0]
    comp["rotation"] = component
    for level in ["central", "local"]:
        defs[level]["R0805"]["cell"]["pins"][0]["rotation"] = pad
    assert R._supported_delta(comp, defs["central"]["R0805"], defs["local"]["R0805"])


def test_rectangle_with_crossed_edges_is_not_equal_contour():
    plan, state = fixture_plan()
    points = state["components"][0]["geometry"][0]["copper"][0]["geometry"][0]["points_mil"]
    for axis in points:
        axis[1], axis[2] = axis[2], axis[1]
    assert not R.compare(plan, state, plan["definitions"]["central"])["valid"]


@pytest.mark.parametrize("observe_error", ["E_NOT_FOUND", "E_TIMEOUT"])
@pytest.mark.parametrize("saved", [False, True])
def test_public_receipt_keeps_partial_when_following_observer_fails(
    tmp_path, monkeypatch, project, saved, observe_error
):
    _scope(tmp_path, monkeypatch)
    prepare_project(project)
    plan, state = fixture_plan()
    calls = []
    known = {
        "applied": True,
        "saved": saved,
        "persistence_verified": saved,
        "change_status": "partial",
        "persistence_state": "saved" if saved else "partial",
        "stages": {
            "local_definition": "changed",
            "instances": "mismatch",
            "save_reopen": "verified" if saved else "not_started",
        },
    }

    class Backend:
        def invoke(self, method, params, **kwargs):
            if calls and method == "identity":
                raise CLIError(observe_error, "Synthetic independent observer unavailable")
            if method == "identity":
                return {"application": {"pid": 4242}, "adapter": _worker_fixture()}
            if method == "snapshot":
                return {"components": [{"refdes": "R1", "uid": "62"}], "fixture": True}
            if method == "cell_refresh_state":
                return {"geometry": copy.deepcopy(plan["before"])}
            assert method == "refresh_cells"
            if not params.get("apply"):
                return copy.deepcopy(plan)
            calls.append("apply")
            raise CLIError(
                "E_NATIVE_FAILURE", "Synthetic partial refresh", {"native_result": known}
            )

    backend = Backend()
    monkeypatch.setattr(pcb, "native", lambda: backend)
    args = ["pcb", "update", "--project", str(project), "--refresh-cells", "--dangerous"]
    with runtime(Adapter(backend), state_dir=tmp_path / "state") as cli:
        preview = cli.run([*args, "--dry-run"])
        assert preview.envelope["ok"], preview.envelope
        result = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert not result.envelope["ok"]
    receipt = result.envelope["error"]["details"]["receipt"]
    assert receipt["outcome"]["change"] == "partial"
    assert receipt["outcome"]["persistence"] == ("saved" if saved else "partial")
    assert receipt["outcome"]["verification"]["status"] == "incomplete"
    assert receipt["extensions"]["result"]["stages"] == known["stages"]
    assert calls == ["apply"]
