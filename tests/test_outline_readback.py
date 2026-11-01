"""Complete contour/lifecycle safety checks through native-shaped offline inputs."""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from xpedition_cli import cell_refresh
from xpedition_cli import native_com_adapter as A
from xpedition_cli import outline_readback as O


class Collection(list):
    @property
    def Count(self):
        return len(self)

    def Item(self, number):
        return self[number - 1]


def contour(points):
    return {
        "points_mil": [[v / O.UNIT_SCALE_MM for v in row] for row in points],
        "points_mm": points,
        "cutout_count": 0,
        "closed": True,
        "source": "native synthetic getter",
    }


def contours(width=68, height=149, radius=3):
    seed = {
        "complete": True,
        "objects": {key: contour(O._rounded(0, 0, 10, 20, 0)) for key in O.OUTLINE_MEMBERS},
        "errors": [],
    }
    expected = O.expectation({"width": width, "height": height, "radius": radius}, seed)
    return {
        "complete": True,
        "objects": {
            key: contour(copy.deepcopy(shape["points_mm"]))
            for key, shape in expected["target"].items()
        },
        "errors": [],
    }


def material(outlines):
    components = [
        {
            "refdes": "U19",
            "uid": 73,
            "part_number": "DEMO",
            "footprint": "DEMO_GEOM",
            "pins": [{"number": "1", "net": "SIGNAL", "x": 4.0, "y": 5.0}],
            "placed": True,
            "x": 4.0,
            "y": 5.0,
        },
        {"refdes": "J8", "uid": 91, "pins": [{"number": "1", "net": "SIGNAL", "x": 6.0, "y": 5.0}]},
    ]
    for comp in components:
        comp.update(
            part_number="DEMO",
            footprint="DEMO_GEOM",
            placed=True,
            x=4.0 if comp["refdes"] == "U19" else 6.0,
            y=5.0,
            rotation=0.0,
            side="top",
            anchor=0,
            fix_lock=0,
            placement_outlines=[],
            insertion_outlines=[],
            geometry=[
                {
                    "number": pin["number"],
                    "net": pin["net"],
                    "center_mm": [pin["x"], pin["y"]],
                    "copper": [],
                    "fabrication": [],
                    "holes": [],
                }
                for pin in comp["pins"]
            ],
        )
    model = {
        key: []
        for key in (
            "outline",
            "pads",
            "vias",
            "traces",
            "planes",
            "holes",
            "via_holes",
            "slots",
            "silk",
            "texts",
        )
    }
    model.update(
        layers=4,
        outline=[
            {
                "kind": "outline",
                "path": [
                    list(row)
                    for row in zip(*outlines["objects"]["board_outline"]["points_mm"], strict=True)
                ],
            }
        ],
        traces=[
            {
                "uid": "318",
                "net": "SIGNAL",
                "layer": 1,
                "width": 0.254,
                "path": [[4, 5, 0], [6, 5, 0]],
            }
        ],
    )
    return {
        "components": components,
        "model": model,
        "stackup": [{"board_layer": 1, "kind": "conductor"}],
    }


def facts(outlines, board="D:/authorized/board.pcb"):
    return {
        "identity": {"pid": 1203, "pcb": str(Path(board).resolve())},
        "outlines": copy.deepcopy(outlines),
        "protection": material(outlines),
        "nets": [{"name": "SIGNAL", "uid": "11"}],
        "stackup": [{"type": 0, "board_layer": 1, "thickness_mm": 0.035}],
    }


def receipt(before):
    return {
        "write_attempted": True,
        "native_calls_returned": [
            {"call": name, "returned": True}
            for name in ("PutBoardOutline", "PutRouteBorder", "PutManufacturingOutline")
        ],
        "lifecycle": {
            "save_count": 1,
            "close_count": 1,
            "open_count": 1,
            "save_reply_received": True,
            "save_returned": True,
            "save_return_type": "bool",
            "save_acknowledged": True,
            "close_reply_received": True,
            "close_returned": True,
            "close_return_type": "bool",
            "close_acknowledged": True,
            "closed_observed": True,
            "open_reply_received": True,
            "reopen_verified": True,
            "pid_before": 1203,
            "pid_after": 1203,
            "board": before["identity"]["pcb"],
            "reopened_path": before["identity"]["pcb"],
        },
    }


def acceptance():
    before = facts(contours())
    expected = O.expectation({"width": 70, "height": 150, "radius": 4}, before["outlines"])
    after = facts(contours(70, 150, 4))
    return before, after, receipt(before), expected


def test_complete_r4_is_accepted_with_signed_quarter_arcs_and_full_protection():
    before, after, result, expected = acceptance()
    original = copy.deepcopy((before, after, result, expected))
    verified = O.verify(before, after, result, expected)
    assert verified["valid"] and verified["persistence_verified"]
    assert (before, after, result, expected) == original


@pytest.mark.parametrize("key", list(O.OUTLINE_MEMBERS))
@pytest.mark.parametrize(
    "change",
    ["same_bbox_wrong_radius", "positive_arc", "open", "interior_drift", "missing_raw", "cutout"],
)
def test_whole_shape_and_companion_defects_fail_even_with_same_extrema(key, change):
    before, after, result, expected = acceptance()
    record = after["outlines"]["objects"][key]
    if change == "same_bbox_wrong_radius":
        points = O._rounded(0, 0, 70, 150, 3)
        after["outlines"]["objects"][key] = contour(points)
    elif change == "positive_arc":
        record["points_mm"][2][1] *= -1
        record["points_mil"][2][1] *= -1
    elif change == "open":
        record["points_mm"][0][-1] += 1
        record["points_mil"][0][-1] += 1 / O.UNIT_SCALE_MM
    elif change == "interior_drift":
        record["points_mm"][0][1] += 0.1
        record["points_mil"][0][1] += 0.1 / O.UNIT_SCALE_MM
    elif change == "missing_raw":
        record.pop("points_mil")
    else:
        record["cutout_count"] = 1
    assert not O.verify(before, after, result, expected)["valid"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("save_returned", False),
        ("save_returned", None),
        ("save_returned", 1),
        ("close_returned", False),
        ("close_returned", None),
        ("close_returned", 1),
        ("closed_observed", False),
        ("pid_after", 1204),
        ("reopened_path", "D:/foreign.pcb"),
        ("open_count", 2),
        ("save_count", 0),
        ("open_reply_received", False),
    ],
)
def test_incomplete_or_false_lifecycle_never_passes(field, value):
    before, after, result, expected = acceptance()
    result["lifecycle"][field] = value
    assert not O.verify(before, after, result, expected)["valid"]


@pytest.mark.parametrize(
    "change",
    [
        "component",
        "pin_net",
        "old_copper",
        "net",
        "stackup",
        "duplicate_uid",
        "duplicate_ref",
        "missing_uid",
        "duplicate_pin",
        "missing_component",
        "missing_model_field",
    ],
)
def test_full_non_target_drift_and_ambiguous_identity_fail(change):
    before, after, result, expected = acceptance()
    if change == "component":
        after["protection"]["components"][0]["x"] += 1
    elif change == "pin_net":
        after["protection"]["components"][0]["pins"][0]["net"] = "OTHER"
    elif change == "old_copper":
        after["protection"]["model"]["traces"][0]["width"] = 0.5
    elif change == "net":
        after["nets"][0]["uid"] = "12"
    elif change == "stackup":
        after["stackup"][0]["thickness_mm"] += 0.1
    elif change == "duplicate_uid":
        after["protection"]["components"][1]["uid"] = 73
    elif change == "duplicate_ref":
        after["protection"]["components"][1]["refdes"] = "U19"
    elif change == "missing_uid":
        after["protection"]["components"][0].pop("uid")
    elif change == "duplicate_pin":
        after["protection"]["components"][0]["pins"] *= 2
    elif change == "missing_component":
        after["protection"]["components"].pop()
    else:
        after["protection"]["model"].pop("planes")
    assert not O.verify(before, after, result, expected)["valid"]


def test_reopen_enumeration_order_does_not_change_stable_identity_protection():
    before, after, result, expected = acceptance()
    after["protection"]["components"].reverse()
    assert O.verify(before, after, result, expected)["valid"]


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), None, "70"])
def test_invalid_numeric_request_fails(value):
    with pytest.raises(ValueError):
        O.expectation({"width": value, "height": 150, "radius": 4}, contours())


@pytest.mark.parametrize("radius", [False, True, float("nan"), float("inf"), "4"])
def test_invalid_radius_never_coerces_to_legal_zero(radius):
    with pytest.raises(ValueError):
        O.expectation({"width": 70, "height": 150, "radius": radius}, contours())


def test_noop_is_all_three_exact_shapes_and_dispatches_nothing():
    before = facts(contours(70, 150, 4))
    expected = O.expectation({"width": 70, "height": 150, "radius": 4}, before["outlines"])
    assert expected["no_op"]
    result = {
        "applied": False,
        "saved": False,
        "write_attempted": False,
        "change_status": "not_applied",
        "persistence_state": "unchanged",
        "native_calls_returned": [],
        "lifecycle": {},
    }
    verified = O.verify(before, copy.deepcopy(before), result, expected)
    assert verified["valid"] and not verified["persistence_verified"]
    result["lifecycle"] = {"save_count": 1}
    assert not O.verify(before, before, result, expected)["valid"]


def test_same_main_shape_but_old_companion_is_not_noop():
    observed = contours(70, 150, 4)
    observed["objects"]["route_border"] = contours()["objects"]["route_border"]
    assert not O.expectation({"width": 70, "height": 150, "radius": 4}, observed)["no_op"]


@pytest.fixture
def native_case(monkeypatch, tmp_path):
    board = (tmp_path / "authorized.pcb").resolve()
    app = NS(ActiveDocument=None)
    case = NS(
        board=board,
        calls=[],
        save_reply=True,
        close_reply=True,
        pid=1203,
        reopened_path=str(board),
        drift=None,
    )

    def shape(points):
        return NS(
            Geometry=NS(
                PointsArray=[[v / O.UNIT_SCALE_MM for v in row] for row in points],
                Cutouts=Collection(),
            )
        )

    class Doc:
        FullName = str(board)

        def __init__(self):
            for key, member in O.OUTLINE_MEMBERS.items():
                setattr(self, member, shape(contours()["objects"][key]["points_mm"]))
            self.Nets = Collection([NS(Name="SIGNAL", UniqueId=11)])

        def PutBoardOutline(self, count, rows, width, unit):
            assert count == len(rows[0]) and width == 0.15 and unit == A.UNIT_MM
            case.calls.append("PutBoardOutline")
            self.BoardOutline = shape(rows)

        def PutRouteBorder(self, count, rows, width, unit):
            case.calls.append("PutRouteBorder")
            if case.drift == "route_setter_error":
                raise RuntimeError("synthetic setter failed")
            self.RouteBorder = shape(rows)
            if case.drift == "route_setter_wrong":
                self.RouteBorder.Geometry.PointsArray[2][1] *= -1

        def PutManufacturingOutline(self, count, rows, unit):
            case.calls.append("PutManufacturingOutline")
            self.ManufacturingOutline = shape(rows)

        def Save(self):
            case.calls.append("Save")
            return case.save_reply

        def Close(self, saved):
            assert saved is True
            case.calls.append("Close")
            if case.close_reply is True:
                app.ActiveDocument = None
            return case.close_reply

    doc = Doc()
    app.ActiveDocument = doc
    case.doc, case.app = doc, app
    monkeypatch.setattr(
        cell_refresh, "active", lambda *args, **kwargs: (board, board, app, app.ActiveDocument)
    )
    monkeypatch.setattr(O, "_identity", lambda app, board: {"pid": case.pid, "pcb": str(board)})
    monkeypatch.setattr(A, "_licensed_document", lambda app: app.ActiveDocument)
    monkeypatch.setattr(A, "_native_call", lambda name, fun: fun())
    monkeypatch.setattr(A, "_settle", lambda duration: None)

    def read_material(doc, a, strict=False):
        assert strict is True
        state = material(O.outlines(doc))
        if case.drift == "non_target" and "PutManufacturingOutline" in case.calls:
            state["components"][0]["pins"][0]["net"] = "OTHER"
        return state

    monkeypatch.setattr(cell_refresh, "capture", read_material)
    monkeypatch.setattr(O, "_strict_stackup", lambda doc, a: [{"type": 0, "thickness_mm": 0.035}])

    def reopen(app, board):
        assert app.ActiveDocument is None
        case.calls.append("Open")
        doc.FullName = case.reopened_path
        app.ActiveDocument = doc
        if case.drift == "pid_reopen":
            case.pid += 1
        return doc, []

    monkeypatch.setattr(A, "_open_layout_document", reopen)
    case.params = {
        "project": str(board),
        "width": 70.0,
        "height": 150.0,
        "radius": 4.0,
        "apply": False,
    }
    return case


def run_case(case):
    preview = O.execute(case.params, None, A)
    return O.execute(
        {
            **case.params,
            "apply": True,
            "state_guard": preview["state_guard"],
            "state_digest": preview["state_digest"],
        },
        None,
        A,
    )


def test_native_shaped_full_set_save_close_empty_open_and_fresh_acceptance(native_case):
    result = run_case(native_case)
    assert native_case.calls == [
        "PutBoardOutline",
        "PutRouteBorder",
        "PutManufacturingOutline",
        "Save",
        "Close",
        "Open",
    ]
    assert result["applied"] and result["saved"] and result["persistence_verified"]
    assert result["verification"]["valid"]


@pytest.mark.parametrize(
    "field,value,expected_calls",
    [
        ("save_reply", False, ["Save"]),
        ("save_reply", None, ["Save"]),
        ("close_reply", False, ["Save", "Close"]),
        ("close_reply", None, ["Save", "Close"]),
    ],
)
def test_native_false_none_lifecycle_stops_without_replay(
    native_case, field, value, expected_calls
):
    setattr(native_case, field, value)
    with pytest.raises(A.AdapterError) as caught:
        run_case(native_case)
    assert native_case.calls[3:] == expected_calls
    assert caught.value.details["write_attempted"] is True
    assert caught.value.details["result"]["persistence_verified"] is False


@pytest.mark.parametrize(
    "drift", ["route_setter_error", "route_setter_wrong", "non_target", "pid_reopen"]
)
def test_partial_native_failure_and_drift_are_reported_without_extra_save(native_case, drift):
    native_case.drift = drift
    with pytest.raises(A.AdapterError) as caught:
        run_case(native_case)
    result = caught.value.details["result"]
    assert not result["persistence_verified"]
    assert native_case.calls.count("PutBoardOutline") == 1
    if drift != "pid_reopen":
        assert "Save" not in native_case.calls


def test_wrong_reopen_path_stops_without_reading_or_replaying(native_case):
    native_case.reopened_path = str(native_case.board.with_name("foreign.pcb"))
    with pytest.raises(A.AdapterError) as caught:
        run_case(native_case)
    assert caught.value.details["stage"] == "outline.save_close_reopen"
    assert native_case.calls.count("Open") == 1


def test_missing_companion_getter_refuses_before_any_write(native_case):
    del native_case.doc.RouteBorder
    observed = O.outlines(native_case.doc)
    assert observed["complete"] is False
    assert "route_border" not in observed["objects"]
    assert observed["errors"] == [
        {"object": "route_border", "getter": "RouteBorder", "observation_error": "AttributeError"}
    ]
    with pytest.raises(A.AdapterError) as caught:
        O.execute(native_case.params, None, A)
    assert caught.value.details["write_attempted"] is False
    assert native_case.calls == []


def test_material_drift_after_preview_refuses_before_any_setter(native_case):
    preview = O.execute(native_case.params, None, A)
    native_case.doc.Nets[0].Name = "OTHER"
    with pytest.raises(A.AdapterError) as caught:
        O.execute(
            {
                **native_case.params,
                "apply": True,
                "state_guard": O.GUARD,
                "state_digest": preview["state_digest"],
            },
            None,
            A,
        )
    assert caught.value.code == "E_CONFLICT"
    assert caught.value.details["write_attempted"] is False
    assert native_case.calls == []


@pytest.mark.parametrize(
    "invalid",
    [
        {},
        {"observe_only": True},
        {"observe_only": True, "start": False, "pid": True},
        {"observe_only": True, "start": True, "pid": 1203},
    ],
)
def test_readonly_observation_rejects_implicit_activation(native_case, invalid):
    with pytest.raises(A.AdapterError):
        O.observe({**native_case.params, **invalid}, None, A)
    assert native_case.calls == []


def test_readonly_observation_keeps_missing_getter_as_unsupported(native_case):
    del native_case.doc.ManufacturingOutline
    result = O.observe(
        {**native_case.params, "observe_only": True, "start": False, "pid": 1203}, None, A
    )
    assert not result["complete"] and not result["write_attempted"]
    assert result["protection"] is None
    assert result["outlines"]["objects"]["board_outline"]
    assert native_case.calls == []


def test_native_noop_keeps_all_setter_and_lifecycle_call_counts_zero(native_case):
    native_case.params.update(width=68, height=149, radius=3)
    result = run_case(native_case)
    assert result["outline_plan"]["no_op"] is True
    assert result["verification"]["valid"] is True
    assert result["verification"]["persistence_verified"] is False
    assert result["persistence_state"] == "unchanged"
    assert native_case.calls == []


def test_readonly_bound_pid_mismatch_refuses_without_side_effect(native_case):
    with pytest.raises(A.AdapterError) as caught:
        O.observe(
            {**native_case.params, "observe_only": True, "start": False, "pid": 1204},
            None,
            A,
        )
    assert caught.value.code == "E_CONFLICT"
    assert native_case.calls == []


def test_bad_native_cutout_getter_preserves_already_read_raw_points(native_case):
    native_case.doc.RouteBorder.Geometry.Cutouts = NS(Count=None)
    observed = O.outlines(native_case.doc)
    assert observed["complete"] is False
    raw = observed["objects"]["route_border"]
    assert raw["points_mil"] and raw["points_mm"]
    assert raw["cutout_count"] is None
    with pytest.raises(ValueError):
        O.expectation(native_case.params, observed)
    assert native_case.calls == []


def test_over_budget_contour_refuses_before_write_and_preserves_raw(native_case):
    raw = [[float(index) for index in range(129)], [0.0] * 129, [0.0] * 129]
    native_case.doc.BoardOutline.Geometry.PointsArray = raw
    observed = O.outlines(native_case.doc)
    assert observed["complete"] is False
    assert observed["objects"]["board_outline"]["points_mil"] == raw
    with pytest.raises(A.AdapterError) as caught:
        O.execute(native_case.params, None, A)
    assert caught.value.details["write_attempted"] is False
    assert native_case.calls == []


def test_strict_stackup_getters_reject_missing_data_instead_of_empty_defaults(monkeypatch):
    props = NS(StackupLayerName="TOP", Description="", LayerUsage=1)
    layer = NS(Type=0, BoardLayer=1, ConductorLayer=1, LayerProperties=props)

    def propget(owner, key, *args):
        if owner is props:
            assert key == A.LAYER_PROPERTIES_THICKNESS
            return 0.035
        assert key == A.LAYOUT_LAYER_STACK
        return Collection([layer])

    monkeypatch.setattr(A, "_propget", propget)
    observed = O._strict_stackup(object(), A)
    assert observed[0]["thickness_mm"] == 0.035
    del props.Description
    with pytest.raises(AttributeError):
        O._strict_stackup(object(), A)


def test_strict_stackup_missing_native_layer_collection_is_not_empty_success(monkeypatch):
    monkeypatch.setattr(A, "_propget", lambda *args: Collection())
    with pytest.raises(ValueError):
        O._strict_stackup(object(), A)


@pytest.mark.parametrize("value", [None, True, ""])
def test_missing_or_boolean_trace_identity_fails_even_if_unchanged(value):
    before, after, result, expected = acceptance()
    before["protection"]["model"]["traces"][0]["uid"] = value
    after["protection"]["model"]["traces"][0]["uid"] = value
    assert not O.verify(before, after, result, expected)["valid"]


def test_duplicate_net_identity_fails_even_if_unchanged():
    before, after, result, expected = acceptance()
    before["nets"] *= 2
    after["nets"] *= 2
    assert not O.verify(before, after, result, expected)["valid"]


def test_boolean_lifecycle_count_is_not_native_dispatch_count():
    before, after, result, expected = acceptance()
    result["lifecycle"]["save_count"] = True
    assert not O.verify(before, after, result, expected)["valid"]


def test_incomplete_malformed_facts_return_failed_checks_without_guessing():
    verified = O.verify(None, [], None, None)
    assert not verified["valid"]
    assert not verified["persistence_verified"]


@pytest.mark.parametrize(
    "missing",
    [
        "geometry",
        "footprint",
        "part_number",
        "anchor",
        "fix_lock",
        "rotation",
        "side",
        "placement_outlines",
        "insertion_outlines",
    ],
)
def test_same_incomplete_component_material_does_not_pass_protection(missing):
    before, after, result, expected = acceptance()
    before["protection"]["components"][0].pop(missing)
    after["protection"]["components"][0].pop(missing)
    assert not O.verify(before, after, result, expected)["valid"]


def test_missing_physical_pin_or_missing_net_record_never_means_empty():
    before, after, result, expected = acceptance()
    before["protection"]["components"][0]["geometry"] = []
    after["protection"]["components"][0]["geometry"] = []
    assert not O.verify(before, after, result, expected)["valid"]
