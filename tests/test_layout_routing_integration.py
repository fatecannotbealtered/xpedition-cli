"""Real reader/capture/identity integration over native-shaped owned objects, no CAD."""

import copy
from types import SimpleNamespace as NS

from test_unroute_native import Collection

from xpedition_cli import board_trace, board_unroute, cell_refresh, layout_reminders
from xpedition_cli import native_com_adapter as A


def native_document(monkeypatch):
    net = NS(Name="A", UniqueId=11, NumberOfOpens=0)
    trace = NS(
        UniqueId=318,
        Net=net,
        Layer=1,
        Geometry=NS(
            IsCircle=lambda: False,
            PointsArray=((785.1, 1145.17716535433), (590.55118110236,) * 2, (0.0, 0.0)),
            LineWidth=10.0,
            Cutouts=Collection(),
        ),
    )
    text = NS(
        PositionX=800,
        PositionY=600,
        TextString="R1",
        Type=2,
        TextType=1,
        UniqueId=501,
        Component=None,
        SideName="Top",
        Format=NS(Height=40, Orientation=0, Mirrored=False, PenWidth=4),
        Extrema=NS(
            GetMinX=lambda unit: 20.0,
            GetMinY=lambda unit: 14.8,
            GetMaxX=lambda unit: 22.0,
            GetMaxY=lambda unit: 15.8,
        ),
        StrokeText=lambda unit: [0.1, 20.1, 14.9, 0, 21.9, 15.7, 2],
    )
    doc = NS(
        CurrentUnit=2,
        LayerCount=2,
        BoardOutline=None,
        Components=Collection(),
        Traces=Collection([trace]),
        Vias=Collection(),
        MountingHoles=Collection(),
        PlaneShapes=Collection(),
        FabricationLayerGfxs=Collection(),
        FabricationLayerTexts=Collection([text]),
        Nets=Collection([net]),
    )
    net.IsRouted = lambda: bool(doc.Traces)

    def property_collection(owner, key, *args):
        assert owner is doc
        assert key in {A.LAYOUT_LAYER_STACK, A.LAYOUT_OBSTRUCTS, A.LAYOUT_PLACEMENT_OBSTRUCTS}
        return Collection()

    # Only absent optional layer/obstruct collections need a COM-property shim.
    # _board_model, capture, _route_status, geometry conversion and UID reads run normally.
    monkeypatch.setattr(A, "_propget", property_collection)
    return doc, trace, net


def test_native_trace_observation_reminders_and_exact_removal_share_physical_geometry(monkeypatch):
    doc, trace, net = native_document(monkeypatch)
    model = A._board_model(doc, strict=True)
    original = copy.deepcopy(model)
    assert "width_source" not in model["traces"][0]
    metadata = model["layout_observation"]["trace_envelopes"]
    assert metadata == [
        {
            "model_index": 0,
            "observation_index": 0,
            "uid": "318",
            "geometry_sha256": layout_reminders.trace_geometry_binding(model["traces"][0]),
            "width_source": "native_geometry_mils",
        }
    ]
    report = layout_reminders.report(model)
    warning = next(
        w for w in report["layout_reminders"] if w["kind"] == "projected_text_trace_overlap"
    )
    assert warning["objects"][1]["uid"] == "318"
    assert model == original  # local enrichment never mutates the physical dictionary

    before = board_unroute.observe(doc, A)  # real strict capture, not a supplied/mocked snapshot
    assert "layout_observation" not in before["geometry"]["model"]
    assert before["routing_objects"][0]["geometry"] == before["geometry"]["model"]["traces"][0]
    bound = {
        "before": before,
        "nets": ["A"],
        "selected_objects": before["routing_objects"],
        "expected_connectivity": [{"net": "A", "uid": "11", "opens": 1, "routed": False}],
    }
    doc.Traces.clear()
    net.NumberOfOpens = 1
    after = board_unroute.observe(doc, A)
    assert board_unroute.compare(bound, after)["valid"] is True
    assert A._board_model(doc, strict=True)["layout_observation"]["trace_envelopes"] == []

    doc.Traces.append(trace)
    trace.Geometry.LineWidth = 10.1
    net.NumberOfOpens = 0
    drift = board_unroute.observe(doc, A)
    result = board_unroute.compare(bound, drift)
    assert result["protection_valid"] is False
    assert any(
        r["name"] == "remaining routing identities and geometry unchanged" and not r["passed"]
        for r in result["checks"]
    )


def test_native_trace_addition_does_not_create_advisory_collection_count_protection_drift(
    monkeypatch,
):
    doc, trace, net = native_document(monkeypatch)
    doc.Traces.clear()
    net.NumberOfOpens = 1

    def state():
        return {
            **board_unroute.observe(doc, A),
            "plane_inventory": {"shapes": [], "generated": [], "assignments": []},
            "rules": {"fixture": "unchanged"},
            "pin_identities": [],
        }

    before = state()
    doc.Traces.append(trace)
    net.NumberOfOpens = 0
    after = state()
    shape = after["geometry"]["model"]["traces"][0]
    plan = {
        "before": before,
        "target": {"net": "A", "layer": 1},
        "request": {"width": shape["width"], "points": [p[:2] for p in shape["path"]]},
        "authorized_regeneration": [],
    }
    comparison = board_trace.compare(plan, after, "318")
    assert comparison["protection_valid"] is True
    assert comparison["completion_valid"] is False
    assert comparison["valid"] is False


def test_parallel_trace_provenance_rejects_shape_or_index_drift(monkeypatch):
    doc, _, _ = native_document(monkeypatch)
    model = A._board_model(doc, strict=True)
    model["traces"][0]["width"] += 0.001
    report = layout_reminders.report(model)
    assert not [
        w for w in report["layout_reminders"] if w["kind"] == "projected_text_trace_overlap"
    ]
    assert any(
        "trace_stroke_width_unobserved" in row["reasons"]
        for row in report["layout_reminder_coverage"]["unreviewed_objects"]
    )


def test_capture_excludes_only_named_advisory_sidecar():
    model = {
        "traces": [{"width": 0.254}],
        "layout_observation": {"analysis": True},
        "unknown_physical_field": {"keep_exact": True},
    }
    original = copy.deepcopy(model)
    result = cell_refresh.physical_model(model)
    assert result == {key: value for key, value in model.items() if key != "layout_observation"}
    assert result["unknown_physical_field"] == {"keep_exact": True}
    assert model == original
