"""Candidate scratch-mask qualification with explicit synthetic native cell replies."""

import copy
from types import SimpleNamespace as NS

import pytest
from test_components_public_observation import native_component_case
from test_native_pad_mask import Collection, add_graph_trace, array, cells, contour, material

from xpedition_cli import board_components as C
from xpedition_cli import board_routepass as R
from xpedition_cli import native_com_adapter as A
from xpedition_cli import native_component_candidate as Q
from xpedition_cli.cell_refresh import digest


def candidate_case(tmp_path, monkeypatch, *, points=None, fault=None):
    case = native_component_case(tmp_path, monkeypatch, pins=4, traces=0)
    raw = array(-5, -5, 80, 80)
    geometry = NS(
        IsValid=lambda: True,
        IsCircle=lambda: False,
        IsArced=lambda: fault == "outline_arc",
        IsClosed=lambda: True,
        values={1: raw},
    )
    cutouts = Collection([object()] if fault == "outline_cutout" else [])
    case.doc.BoardOutline = NS(Geometry=geometry)
    old_propget = A._propget

    def propget(owner, dispid, *args):
        if owner is geometry and dispid == 22:
            assert args == ()
            return cutouts
        return old_propget(owner, dispid, *args)

    monkeypatch.setattr(A, "_propget", propget)
    case.state["geometry"]["model"]["outline"] = [
        {"kind": "outline", "path": [list(p) for p in zip(*raw, strict=True)]}
    ]
    case.engine.filled[digest(raw)] = cells(-5, -5, 80, 80)
    case.engine.filled[digest(array(-6, -6, 81, 81))] = cells(-6, -6, 81, 81)
    if fault == "same_component_regions":
        pin = case.doc.Components[0].Pins[0]
        other_region = array(-1, 1, 2, 4)
        case.engine.filled[digest(other_region)] = cells(-1, 1, 2, 4)
        pad = pin.material["pads"][0]
        pad["bounds_mm"] = [-1, -1, 2, 4]
        pad["geometry"].append({"filled": True, "outer": contour(other_region), "cutouts": []})
    if fault in {"drill", "cutout"}:
        pin = case.doc.Components[0].Pins[0]
        if fault == "drill":
            pin.material["holes"] = [{"center_mm": [0, 0], "diameter_mm": 1, "plated": True}]
            case.state["geometry"]["model"]["holes"] = [[0, 0, 1]]
        else:
            cut = array(-0.5, -0.5, 0.5, 0.5)
            case.engine.filled[digest(cut)] = cells(0, 0, 1, 1)
            pin.material["pads"][0]["geometry"][0]["cutouts"] = [contour(cut)]
    if fault == "foreign":
        other = array(4, -1, 7, 2)
        case.engine.filled[digest(other)] = cells(4, -1, 7, 2)
        pin = NS(
            UniqueId="foreign-pin",
            Name="9",
            Net=NS(Name="other-net", UniqueId="other-uid"),
            GetPositionX=lambda unit: 5,
            GetPositionY=lambda unit: 0,
            material=material(contour(other)),
        )
        case.doc.Components.append(
            NS(UniqueId="foreign-owner", RefDes="NATIVE", Pins=Collection([pin]))
        )
        case.state["pin_identities"].append(
            {
                "component_uid": "foreign-owner",
                "refdes": "NATIVE",
                "pin": "9",
                "pin_uid": "foreign-pin",
                "net": "other-net",
                "net_uid": "other-uid",
            }
        )
        case.state["geometry"]["model"]["connectivity"]["nets"].append(
            {"net": "other-net", "uid": "other-uid", "opens": 0, "routed": True}
        )
    state = R.observe_components(case.doc, A, case.selector)
    observed = Q.M.observe_components(state, case.request)
    snapshot = C.build_snapshot(
        state,
        observed,
        board_id=str(case.board),
        net_id=case.request["net_uid"],
        layer=1,
    )
    assert snapshot["valid"], snapshot
    nodes = {row["pin_uid"]: row["node_id"] for row in snapshot["nodes"]}
    connection = C.plan_connection(
        snapshot,
        first=nodes["actual-pin-0"],
        second=nodes["actual-pin-1"],
        points=points or [[0, 0], [10, 0]],
        width_mm=0.254,
    )
    path = connection["points"]
    path_raw = [[p[i] for p in path] for i in (0, 1)] + [[0.0] * len(path)]
    case.engine.paths[(digest(path_raw), 0.254)] = cells(
        min(p[0] for p in path),
        min(p[1] for p in path),
        max(p[0] for p in path) + 1,
        max(p[1] for p in path) + 1,
    )
    return case, state, snapshot, connection


def test_candidate_uses_native_full_material_and_individually_releases_every_scratch_mask(
    tmp_path, monkeypatch
):
    case, state, snapshot, connection = candidate_case(tmp_path, monkeypatch)
    prior = case.engine.masks.Add()
    result = Q.evaluate(case.doc, A, state, snapshot, connection)
    assert result["valid"] and result["complete"], result
    assert Q.verify(state, snapshot, connection, result)["valid"]
    assert case.engine.masks == [prior] and not prior.deleted
    assert result["temporary_masks_count"] == {"before": 1, "after": 1}
    touched = [r for r in result["target_intersections"] if r["area_mm2"] > 0]
    assert len(touched) == 2
    assert all(row["delete_returned"] for row in result["cleanup"])
    assert not any(
        row[0] in {"PutTrace", "TransactionStart", "TransactionEnd", "Save", "Close", "Open"}
        for row in case.engine.calls
    )


@pytest.mark.parametrize("fault", ["cutout", "foreign", "outline_arc", "outline_cutout"])
def test_unproved_outline_and_actual_void_or_foreign_contact_block_candidate(
    tmp_path, monkeypatch, fault
):
    case, state, snapshot, connection = candidate_case(tmp_path, monkeypatch, fault=fault)
    result = Q.evaluate(case.doc, A, state, snapshot, connection)
    assert not result["valid"], result
    assert case.engine.masks == []


def test_native_material_contact_with_third_component_is_not_authorized(tmp_path, monkeypatch):
    case, state, snapshot, connection = candidate_case(
        tmp_path,
        monkeypatch,
        points=[[0, 0], [0, 5], [20, 5], [20, 0], [10, 0]],
    )
    result = Q.evaluate(case.doc, A, state, snapshot, connection)
    assert not result["valid"] and "exactly" in result["reason"], result


def copper_candidate_case(tmp_path, monkeypatch, *, through=False, anchor=10):
    case, _, _, _ = candidate_case(tmp_path, monkeypatch, fault="drill" if through else None)
    raw = [[10, 12, 14], [0, 0, 0], [0, 0, 0]]
    add_graph_trace(
        case.doc,
        case.state,
        case.engine,
        "existing-terminal-trace",
        raw,
        cells(10, 0, 15, 1),
        [9.873, -0.127, 14.127, 0.127],
        net=case.request["net"],
    )
    case.doc.Traces[-1].Net.UniqueId = case.request["net_uid"]
    case.state["geometry"]["model"]["traces"] = [
        copy.deepcopy(row["geometry"]) for row in case.state["routing_objects"]
    ]
    state = R.observe_components(case.doc, A, case.selector)
    observed = Q.M.observe_components(state, case.request)
    snapshot = C.build_snapshot(
        state,
        observed,
        board_id=str(case.board),
        net_id=case.request["net_uid"],
        layer=1,
    )
    assert snapshot["valid"], snapshot
    target = next(
        row["node_id"] for row in snapshot["nodes"] if row.get("pin_uid") == "actual-pin-0"
    )
    connection = C.plan_connection(
        snapshot,
        first={"node_id": "trace:existing-terminal-trace"},
        second={"node_id": target},
        points=[[anchor, 0], [0, 0]],
        width_mm=0.254,
    )
    candidate_raw = [[anchor, 0], [0, 0], [0.0, 0.0]]
    case.engine.paths[(digest(candidate_raw), 0.254)] = cells(0, 0, int(anchor) + 1, 1)
    return case, state, snapshot, connection


@pytest.mark.parametrize("through", [False, True])
@pytest.mark.parametrize("anchor", [10, 14])
def test_existing_terminal_copper_to_actual_smd_or_round_throughhole_pad(
    tmp_path, monkeypatch, through, anchor
):
    case, state, snapshot, connection = copper_candidate_case(
        tmp_path, monkeypatch, through=through, anchor=anchor
    )
    result = Q.evaluate(case.doc, A, state, snapshot, connection)
    assert result["valid"], result
    assert result["binding"]["anchors"][0]["kind"] == "trace"
    assert result["binding"]["anchors"][1]["kind"] == "pin"
    if through:
        row = result["void_intersections"][0]
        assert row["raw_intersection"]["area_mm2"] > 0 and row["area_mm2"] == 0
        assert row["terminal_crop"]["end_index"] == 1
        assert 0 < result["candidate_area"]["area_mm2"] < result["candidate_raw_area"]["area_mm2"]
        assert result["candidate_area"]["shape_count"] == 1
        assert Q.verify(state, snapshot, connection, result)["valid"]


@pytest.mark.parametrize("anchor", [11, 12])
def test_existing_trace_interior_vertex_is_refused_before_scratch_allocation(
    tmp_path, monkeypatch, anchor
):
    case, state, snapshot, connection = copper_candidate_case(tmp_path, monkeypatch, anchor=anchor)
    count = len(case.engine.calls)
    result = Q.evaluate(case.doc, A, state, snapshot, connection)
    assert not result["valid"] and "first/last vertex" in result["errors"][0]["reason"]
    assert len(case.engine.calls) == count


def test_new_coordinates_beyond_four_decimal_mm_are_refused_without_rounding(tmp_path, monkeypatch):
    case, state, snapshot, connection = copper_candidate_case(
        tmp_path, monkeypatch, anchor=10.00001
    )
    count = len(case.engine.calls)
    result = Q.evaluate(case.doc, A, state, snapshot, connection)
    assert not result["valid"] and "four-decimal-mm grid" in result["errors"][0]["reason"]
    assert len(case.engine.calls) == count


def test_only_drill_void_intersection_without_annular_material_never_qualifies(
    tmp_path, monkeypatch
):
    case, state, snapshot, connection = copper_candidate_case(tmp_path, monkeypatch, through=True)
    raw = [[10, 0], [0, 0], [0.0, 0.0]]
    case.engine.paths[(digest(raw), 0.254)] = cells(0, 0, 1, 1)
    result = Q.evaluate(case.doc, A, state, snapshot, connection)
    assert not result["valid"] and result["candidate_area"]["area_mm2"] == 0


def test_terminal_drill_cannot_be_used_for_an_internal_reentry(tmp_path, monkeypatch):
    points = [[0, 0], [5, 0], [5, 5], [-5, 5], [-5, -5], [0, -0.2], [10, -0.2], [10, 0]]
    case, state, snapshot, connection = candidate_case(
        tmp_path, monkeypatch, points=points, fault="drill"
    )
    result = Q.evaluate(case.doc, A, state, snapshot, connection)
    assert not result["valid"] and "re-enters" in result["errors"][0]["reason"], result


@pytest.mark.parametrize("fault", ["missing_crop", "wrong_owner", "missing_pair", "split_physical"])
def test_terminal_drill_receipts_cannot_hide_ownership_crop_or_material_split(
    tmp_path, monkeypatch, fault
):
    case, state, snapshot, connection = copper_candidate_case(tmp_path, monkeypatch, through=True)
    result = Q.evaluate(case.doc, A, state, snapshot, connection)
    assert result["valid"], result
    if fault == "missing_crop":
        result["void_intersections"][0]["terminal_crop"] = None
    elif fault == "wrong_owner":
        result["void_intersections"][0]["owners"][0]["pin_uid"] = "unselected-owner"
    elif fault == "missing_pair":
        result["terminal_crop_pairs"] = None
    else:
        result["candidate_area"]["shape_count"] = 2
    result["valid"] = True
    assert not Q.verify(state, snapshot, connection, result)["valid"]


def test_endpoint_coordinates_cannot_replace_actual_positive_mask_contact(tmp_path, monkeypatch):
    case, state, snapshot, connection = candidate_case(tmp_path, monkeypatch)
    raw = [[0, 10], [0, 0], [0.0, 0.0]]
    case.engine.paths[(digest(raw), 0.254)] = cells(0, 0, 5, 1)
    result = Q.evaluate(case.doc, A, state, snapshot, connection)
    assert not result["valid"] and "exactly" in result["reason"], result


def test_unreadable_native_touch_is_incomplete_even_when_native_and_is_empty(tmp_path, monkeypatch):
    case, state, snapshot, connection = candidate_case(tmp_path, monkeypatch)
    case.engine.touch_result = "not a native BOOL"
    result = Q.evaluate(case.doc, A, state, snapshot, connection)
    assert not result["valid"] and result["complete"] is False
    assert case.engine.masks == []


def test_contact_with_another_node_in_same_component_cannot_replace_explicit_node(
    tmp_path, monkeypatch
):
    case, state, snapshot, connection = candidate_case(
        tmp_path, monkeypatch, fault="same_component_regions"
    )
    nodes = {
        row["region_index"]: row["node_id"]
        for row in snapshot["nodes"]
        if row.get("pin_uid") == "actual-pin-0"
    }
    wrong = C.plan_connection(
        snapshot,
        first={"node_id": nodes[1]},
        second=connection["second"],
        points=connection["points"],
        width_mm=connection["width_mm"],
    )
    result = Q.evaluate(case.doc, A, state, snapshot, wrong)
    assert not result["valid"] and "explicitly selected node" in result["reason"], result
    by_node = {row["node_id"]: row for row in result["target_intersections"]}
    assert by_node[nodes[0]]["area_mm2"] > 0 and by_node[nodes[1]]["area_mm2"] == 0
    unpinned = C.plan_connection(
        snapshot,
        first={"component_id": wrong["first"]["component_id"]},
        second=connection["second"],
        points=connection["points"],
        width_mm=connection["width_mm"],
    )
    result = Q.evaluate(case.doc, A, state, snapshot, unpinned)
    assert result["valid"], result


def test_candidate_full_width_outside_board_is_rejected_even_when_endpoints_contact(
    tmp_path, monkeypatch
):
    case, state, snapshot, connection = candidate_case(tmp_path, monkeypatch)
    raw = connection["points"]
    key = digest([[p[i] for p in raw] for i in (0, 1)] + [[0.0] * len(raw)])
    case.engine.paths[(key, 0.254)] |= {(-7, 0)}
    result = Q.evaluate(case.doc, A, state, snapshot, connection)
    assert not result["valid"] and "board" in result["reason"], result


def test_cleanup_failure_and_unknown_touch_never_become_qualified(tmp_path, monkeypatch):
    case, state, snapshot, connection = candidate_case(tmp_path, monkeypatch)
    case.engine.delete_failure = True
    result = Q.evaluate(case.doc, A, state, snapshot, connection)
    assert not result["valid"] and "cleanup" in result["reason"], result


@pytest.mark.parametrize(
    "fault", ["omit_target", "omit_void", "wrong_path", "fake_complete", "after_cleanup"]
)
def test_pure_verifier_rejects_incomplete_or_substituted_candidate_receipt(
    tmp_path, monkeypatch, fault
):
    case, state, snapshot, connection = candidate_case(
        tmp_path, monkeypatch, fault="drill" if fault == "omit_void" else None
    )
    result = Q.evaluate(case.doc, A, state, snapshot, connection)
    if fault == "omit_target":
        result["target_intersections"].pop()
    elif fault == "omit_void":
        result["void_intersections"] = []
    elif fault == "wrong_path":
        result["binding"]["candidate"]["points_array_mm"][0][0] += 0.1
    elif fault == "fake_complete":
        result["complete"] = False
    else:
        result["after_cleanup_inputs"]["raw_trace_sha256"] = "0" * 64
    result["valid"] = True
    assert not Q.verify(state, snapshot, connection, result)["valid"]


def test_fingerprint_excludes_only_scratch_collection_lifecycle(tmp_path, monkeypatch):
    case, state, snapshot, connection = candidate_case(tmp_path, monkeypatch)
    first = Q.evaluate(case.doc, A, state, snapshot, connection)
    case.engine.masks.Add()
    second = Q.evaluate(case.doc, A, state, snapshot, connection)
    assert first["valid"] and second["valid"], (first, second)
    assert Q.fingerprint_basis(first) == Q.fingerprint_basis(second)
    changed = copy.deepcopy(second)
    changed["candidate_area"]["area_mm2"] += 1
    assert Q.fingerprint_basis(first) != Q.fingerprint_basis(changed)
