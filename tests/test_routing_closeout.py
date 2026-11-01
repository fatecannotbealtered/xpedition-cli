"""Regressions discovered by the independent QA closeout consumer."""

from types import SimpleNamespace

import pytest

from xpedition_cli import native_com_adapter as A
from xpedition_cli.task_readback import verify_task


def test_clearance_query_uses_actual_nets_explicit_layers_and_units(monkeypatch):
    nets = {n: object() for n in ("SIGNAL", "RETURN")}
    calls = []

    def read(*args):
        calls.append(args)
        return 0.254

    doc = SimpleNamespace(LayerCount=4, Clearance=SimpleNamespace(GetNetClearanceRuleByType=read))
    monkeypatch.setattr(A, "_find_net", lambda d, name: nets[name])
    result = A._selected_clearances(doc, ["SIGNAL", "RETURN"], [1])
    assert result["unit"] == "mm" and result["scheme"] == "(Master)"
    assert result["active_scheme_verified"] is False
    assert len(result["rules"]) == 6
    assert {c[1] for c in calls} == set(nets.values())
    assert all(c[2] == c[5] == 1 and c[6:] == ("(Master)", A.UNIT_MM) for c in calls)
    assert {c[3] for c in calls} == {1, 2, 5}


@pytest.mark.parametrize("value", [None, float("nan"), -1])
def test_clearance_unavailable_is_not_zero(monkeypatch, value):
    doc = SimpleNamespace(
        LayerCount=4,
        Clearance=SimpleNamespace(GetNetClearanceRuleByType=lambda *a: value),
    )
    monkeypatch.setattr(A, "_find_net", lambda *a: object())
    with pytest.raises(A.AdapterError):
        A._selected_clearances(doc, ["A", "B"], [1])


def test_public_inspect_selected_clearances(cli, adapter, board):
    adapter.on("board_info", {"clearances": {"rules": [{"clearance_mm": 0.254}]}})
    code, payload = cli("pcb", "inspect", "--project", str(board), "--nets", "A,B", "--layers", "1")
    assert code == 0, payload
    assert adapter.last("board_info")["nets"] == ["A", "B"]
    assert adapter.last("board_info")["layers"] == [1]
    assert payload["data"]["clearances"]["rules"][0]["clearance_mm"] == 0.254


def test_public_label_selector_reaches_preview(cli, adapter, board):
    adapter.on(
        "tidy_labels",
        {
            "state_guard": "labels_input_sha256_v1",
            "state_digest": "a" * 64,
            "labels": [],
            "selected_refdes": ["J1"],
            "protected_refdes": ["R1"],
        },
    )
    code, payload = cli(
        "pcb", "silkscreen", "labels", "--project", str(board), "--refdes", "J1", "--dry-run"
    )
    assert code == 0, payload
    assert adapter.last("tidy_labels")["refdes"] == ["J1"]
    assert payload["data"]["preview"]["protected_refdes"] == ["R1"]


@pytest.fixture
def board(tmp_path):
    path = tmp_path / "board.pcb"
    path.write_bytes(b"synthetic")
    return path


def edit_fixture():
    import copy

    geometry = {
        "components": [{"uid": "67", "refdes": "J1", "pins": [{"net": "A"}]}],
        "stackup": [1, 2, 3, 4],
        "model": {
            "traces": [],
            "vias": [],
            "unrouted": [],
            "connectivity": {
                "complete": True,
                "nets": [
                    {"net": "A", "uid": "nA", "routed": True, "opens": 0},
                    {"net": "B", "uid": "nB", "routed": True, "opens": 0},
                ],
            },
            "holes": ["protected"],
            "texts": [
                {
                    "kind": "refdes",
                    "refdes": "J1",
                    "text": "J1",
                    "x": 1,
                    "y": 2,
                    "height": 0.8,
                    "pen": 0.12,
                    "orientation": 90,
                },
                {"kind": "refdes", "refdes": "R1", "x": 5, "y": 5},
            ],
        },
    }

    def snapshot(g):
        return {"extensions": {"facts": {"cell_refresh": {"geometry": g}}}}

    return geometry, copy.deepcopy(geometry), snapshot


@pytest.mark.parametrize("corrupt", [None, "protected", "wrong_layer", "reopen_missing"])
def test_route_receipt_requires_independent_geometry_and_protection(corrupt):
    old, new, snap = edit_fixture()
    new["model"]["traces"] = [{"net": "A", "layer": 1, "width": 0.254, "path": [[0, 0], [1, 1]]}]
    if corrupt == "protected":
        new["model"]["holes"] = []
    if corrupt == "wrong_layer":
        new["model"]["traces"][0]["layer"] = 4
    result = {
        "saved": True,
        "persistence_verified": corrupt != "reopen_missing",
        "geometry_after": new,
    }
    check = verify_task(
        "pcb routing auto", {"nets": "A,B", "layers": "1"}, result, snap(old), snap(new), {}
    )
    assert check["valid"] is (corrupt is None)


@pytest.mark.parametrize(
    "corrupt", [None, "other_label", "identity", "orientation", "target_mismatch"]
)
def test_selected_label_receipt_keeps_every_other_field(corrupt):
    old, new, snap = edit_fixture()
    new["model"]["texts"][0]["x"] = 3
    if corrupt == "other_label":
        new["model"]["texts"][1]["x"] = 6
    if corrupt == "identity":
        new["components"][0]["uid"] = "99"
    if corrupt == "orientation":
        new["model"]["texts"][0]["orientation"] = 0
    if corrupt == "target_mismatch":
        new["model"]["texts"][0]["x"] = 3.1
    result = {"saved": True, "persistence_verified": True, "geometry_after": new}
    plan = {
        "extensions": {
            "legacy_preview": {
                "labels": [{"refdes": "J1", "x": 3, "y": 2, "text_height": 0.8, "pen": 0.12}]
            }
        }
    }
    check = verify_task("pcb silkscreen labels", {}, result, snap(old), snap(new), plan)
    assert check["valid"] is (corrupt is None)


def test_post_save_observer_failure_preserves_applied_and_saved(monkeypatch, tmp_path):
    from xpedition_cli import cell_refresh

    def unavailable(*a, **k):
        raise RuntimeError("private diagnostic")

    monkeypatch.setattr(cell_refresh, "capture", unavailable)
    result = {"applied": True, "saved": True}
    A._verify_board_edit_persistence(result, object(), object(), tmp_path / "board.pcb")
    assert result["change_status"] == "applied"
    assert result["persistence_state"] == "saved"
    assert result["persistence_verified"] is False
    assert "private diagnostic" not in str(result)


@pytest.mark.parametrize("command", ["pcb routing auto", "pcb silkscreen labels"])
def test_outer_edit_observation_uses_unrounded_strict_pin_getters(monkeypatch, tmp_path, command):
    from xpedition_cli import cell_refresh, native_identity
    from xpedition_cli.driver import Adapter

    board = tmp_path / "board.pcb"
    board.write_bytes(b"fixture")
    doc = SimpleNamespace(FullName=str(board.resolve()))
    app = SimpleNamespace(ActiveDocument=doc)
    monkeypatch.setattr(cell_refresh, "active", lambda *a, **k: (None, board, app, doc))

    def identity(observed, domain):
        assert observed is app and domain == "pcb"
        return {"pid": 42}

    monkeypatch.setattr(native_identity, "application_identity", identity)
    monkeypatch.setattr(A, "_licensed_document", lambda observed: observed.ActiveDocument)
    modes = []

    def capture(doc, adapter, *, strict=False):
        modes.append(strict)
        return {"pin_x_mm": 29.0875 if strict else 29.087}

    monkeypatch.setattr(cell_refresh, "capture", capture)

    class Backend:
        def invoke(self, method, params, **kwargs):
            if method == "snapshot":
                return {"fixture": True}
            assert method == "cell_refresh_state"
            return cell_refresh.observe(params, None, A)

    adapter = Adapter(Backend())
    monkeypatch.setattr(adapter, "_identity", lambda domain: {"application": {"pid": 42}})
    target = {
        "mode": "session",
        "instance_id": "42",
        "extensions": {
            "domain": "pcb",
            "project": str(board),
            "command": command,
        },
    }
    snapshot = adapter._observe(target)
    assert modes == [True]
    assert snapshot["extensions"]["facts"]["cell_refresh"]["geometry"]["pin_x_mm"] == 29.0875
