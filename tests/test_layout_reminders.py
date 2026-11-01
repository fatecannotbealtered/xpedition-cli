"""Projection advisories use observed envelopes, never guessed font dimensions."""

import copy
from types import SimpleNamespace as NS

import pytest

from xpedition_cli import layout_reminders as L
from xpedition_cli import native_com_adapter as A


def text(box=(2, 2, 4, 3), **extra):
    return {
        "uid": "text-1",
        "kind": "refdes",
        "refdes": "R1",
        "text": "R1",
        "height": 1.0,
        "height_source": "native_format_mils",
        "owner_observed": True,
        "extents": list(box),
        "envelope_source": "native_extrema_world_mm",
        "stroke_source": "native_vector_strokes_world_mm",
        "strokes": [[[box[0], box[1]], [box[2], box[3]]]],
        "orientation": 0.0,
        "mirrored": False,
        "side": "top",
        "observed_side": "top",
        "fabrication_layer": "silkscreen",
        **extra,
    }


def model(*texts):
    return {
        "layers": 4,
        "outline": [{"path": [[0, 0, 0], [20, 0, 0], [20, 20, 0], [0, 20, 0], [0, 0, 0]]}],
        "texts": list(texts),
        "pads": [],
        "silk": [],
        "traces": [],
        "layout_observation": {
            "units": "mm",
            "layer_count": {
                "observed": True,
                "value": 4,
                "source": "declared_synthetic_observation",
                "renderer_value": 4,
                "renderer_source": "declared_synthetic_observation",
            },
            "board_outline": {"inner_inventory_observed": True, "inner_contour_count": 0},
            "component_envelopes": [],
            "collections": {
                key: {"complete": True}
                for key in ("texts", "pads", "silk", "traces", "component_envelopes", "outline")
            },
        },
    }


def test_rotated_native_envelope_is_not_rotated_again_or_estimated_from_text_length():
    data = model(text(orientation=270), text((3, 2, 5, 3), uid="text-2", refdes="J1"))
    original = copy.deepcopy(data)
    observed = L.report(data)
    assert data == original
    warning = next(
        w for w in observed["layout_reminders"] if w["kind"] == "projected_text_text_overlap"
    )
    assert warning["envelopes_mm"] == [[2, 2, 4, 3], [3, 2, 5, 3]]
    assert warning["objects"][0]["orientation_deg"] == 270
    data["texts"][0]["text"] = "X" * 1000
    assert L.report(data) == observed
    assert observed["layout_reminder_coverage"]["status"] == "checked_projected_subset"


@pytest.mark.parametrize(
    "extra", [{"observed_side": "bottom", "side": "bottom"}, {"fabrication_layer": "assembly"}]
)
def test_distinct_observed_side_or_layer_is_excluded(extra):
    observed = L.report(model(text(), text(uid="text-2", **extra)))
    assert observed["layout_reminders"] == []
    rule = next(
        r for r in observed["layout_reminder_coverage"]["checked_rules"] if r["rule"] == "text_text"
    )
    assert rule["pairs_excluded"] == 1
    assert rule["pairs_checked"] == 0


def test_disjoint_near_and_overlap_are_different_heuristic_results():
    assert L.report(model(text(), text((5, 2, 7, 3))))["layout_reminders"] == []
    near = L.report(model(text(), text((4.2, 2, 6, 3))))["layout_reminders"]
    assert len(near) == 1 and near[0]["kind"] == "projected_text_text_proximity"
    assert near[0]["projected_distance_mm"] == 0.2
    assert near[0]["advisory_proximity_mm"] == 0.3
    assert "not a native rule" in near[0]["meaning"]


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("extents", None, "native_text_envelope_missing_or_invalid"),
        ("extents", [0, 0, float("nan"), 1], "native_text_envelope_missing_or_invalid"),
        ("envelope_source", None, "text_envelope_provenance_unobserved"),
        ("strokes", [], "font_strokes_unobserved_or_unsupported"),
        ("stroke_source", "fallback_font", "font_strokes_unobserved_or_unsupported"),
        ("orientation", None, "text_orientation_or_mirroring_unobserved"),
        ("orientation", "unsupported-direction", "text_orientation_or_mirroring_unobserved"),
        ("mirrored", None, "text_orientation_or_mirroring_unobserved"),
        ("observed_side", None, "text_side_unobserved"),
        ("fabrication_layer", None, "text_fabrication_layer_unobserved_or_unsupported"),
        ("owner_observed", False, "text_component_or_document_ownership_unobserved"),
        ("height_source", "renderer_fallback", "text_height_observation_unavailable"),
    ],
)
def test_missing_or_unsupported_observation_is_explicit_unreviewed(field, value, reason):
    observed = L.report(model(text(**{field: value})))
    coverage = observed["layout_reminder_coverage"]
    assert observed["layout_reminders"] == []
    assert coverage["status"] == "partial"
    assert reason in coverage["unreviewed_objects"][0]["reasons"]
    assert coverage["empty_warnings_mean_layout_clear"] is False
    assert coverage["manual_visual_review_required"] is True


def test_pads_graphics_component_bounds_and_edges_have_separate_declared_rules():
    data = model(text((0.1, 2, 2.1, 3)))
    data["pads"] = [
        {"circle": [1, 2.5, 0.4], "layer": 1, "refdes": "J1", "pin": "1"},
        {"circle": [1, 2.5, 0.4], "layer": 2, "refdes": "J1", "pin": "1"},
        {"circle": [1, 2.5, 0.4], "layer": 4, "refdes": "J1", "pin": "1"},
    ]
    data["silk"] = [
        {
            "path": [[1, 1, 0], [1, 4, 0]],
            "width": 0.1,
            "width_source": "native_geometry_mils",
            "observed_side": "top",
            "fabrication_layer": "silkscreen",
            "owner_observed": True,
            "refdes": "J1",
        }
    ]
    data["layout_observation"]["component_envelopes"] = [
        {
            "uid": "c-1",
            "refdes": "J1",
            "extents": [0.5, 1, 3, 4],
            "envelope_source": "native_component_extrema_world_mm",
            "observed_side": "top",
        }
    ]
    result = L.report(data)
    kinds = [w["kind"] for w in result["layout_reminders"]]
    assert "projected_text_outer_pad_overlap" in kinds
    assert kinds.count("projected_text_outer_pad_overlap") == 1
    assert "projected_text_component_graphic_overlap" in kinds
    assert "projected_text_component_envelope_overlap" in kinds
    assert "projected_text_board_edge_proximity" in kinds


def test_arcs_nonrectangular_edges_and_missing_collection_provenance_are_not_hidden():
    data = model(text())
    data["silk"] = [
        {
            "path": [[1, 2, 0], [2, 2, 1], [3, 2, 0]],
            "width": 0.1,
            "observed_side": "top",
            "fabrication_layer": "silkscreen",
        }
    ]
    data["outline"][0]["path"][2][0] = 19
    del data["layout_observation"]["collections"]["texts"]
    coverage = L.report(data)["layout_reminder_coverage"]
    assert coverage["status"] == "partial"
    reasons = [r for item in coverage["unreviewed_objects"] for r in item["reasons"]]
    assert "arc_envelope_unobserved" in reasons
    assert "board_edge_requires_one_exact_axis_aligned_rectangle" in reasons
    assert coverage["unreviewed_collections"][0]["collection"] == "texts"


def test_native_reader_marks_world_envelope_vector_source_and_missing_side(monkeypatch):
    native = NS(
        PositionX=100,
        PositionY=200,
        TextString="R1",
        Type=2,
        TextType=1,
        UniqueId=55,
        Component=NS(RefDes="R1"),
        Format=NS(Height=40, Orientation=270, Mirrored=False),
        StrokeText=lambda unit: [0.1, 1, 2, 0, 2, 3, 2],
    )
    monkeypatch.setattr(A, "_extrema_mm", lambda obj: (1, 2, 3, 4))
    record = A._text_record(native, "R1")
    assert record["envelope_source"] == "native_extrema_world_mm"
    assert record["stroke_source"] == "native_vector_strokes_world_mm"
    assert record["uid"] == "55" and record["orientation"] == 270
    assert record["observed_side"] is None
    assert record["side"] == "top"  # historical renderer fallback is explicitly not evidence


def test_legacy_model_does_not_silently_claim_coverage():
    data = {"layers": 4, "texts": [{"extents": [1, 1, 2, 2], "text": "R1"}], "traces": []}
    coverage = L.report(data)["layout_reminder_coverage"]
    assert coverage["status"] == "partial"
    assert coverage["unreviewed_objects"] and coverage["unreviewed_collections"]


@pytest.mark.parametrize("outline", [[[0, 0], [10, 0], [10, 10], [0, 10]], [[0, 0]]])
def test_legacy_point_outline_is_unsupported_coverage_without_breaking_artifact_preview(outline):
    data = {"layers": 4, "pads": [], "components": [], "outline": outline}
    original = copy.deepcopy(data)
    coverage = L.report(data)["layout_reminder_coverage"]
    assert data == original
    assert coverage["status"] == "partial"
    boundary = coverage["board_edge_coverage"]
    assert boundary["outer_boundary_supported"] is False
    assert boundary["outer_boundary_checked"] is False
    assert boundary["inner_inventory_observed"] is False
    assert boundary["inner_contour_count"] is None
    assert boundary["complete"] is False
    reasons = [reason for row in coverage["unreviewed_objects"] for reason in row["reasons"]]
    assert "board_outline_geometry_representation_unsupported" in reasons
    assert "board_inner_contour_inventory_unobserved" in reasons


def test_equal_bounding_box_does_not_make_a_crossed_or_diagonal_outline_rectangular():
    data = model(text())
    data["outline"][0]["path"] = [[0, 0, 0], [20, 20, 0], [20, 0, 0], [0, 20, 0], [0, 0, 0]]
    coverage = L.report(data)["layout_reminder_coverage"]
    assert any(
        "board_edge_requires_one_exact_axis_aligned_rectangle" in r["reasons"]
        for r in coverage["unreviewed_objects"]
    )


@pytest.mark.parametrize(
    "values", [[0.1, 1, 1, 0, 2], [0.1, 1, 1, 7], [0.1, 1, float("nan"), 0], [0, 1, 1, 0]]
)
def test_malformed_native_text_strokes_cannot_be_presented_as_supported_font(values):
    with pytest.raises(A.AdapterError, match="malformed"):
        A.stroke_rows(values)


def test_pair_and_output_budgets_leave_explicit_partial_coverage(monkeypatch):
    monkeypatch.setattr(L, "MAX_PAIRS", 5)
    monkeypatch.setattr(L, "MAX_WARNINGS", 2)
    result = L.report(model(*(text(uid=str(i)) for i in range(20))))
    coverage = result["layout_reminder_coverage"]
    assert len(result["layout_reminders"]) == 2
    assert coverage["budgets"]["pairs_examined"] == 5
    assert coverage["budgets"]["pair_budget_exhausted"] is True
    assert coverage["budgets"]["warnings_omitted"] == 3
    assert coverage["status"] == "partial"
    assert coverage["board_edge_coverage"]["complete"] is False


def test_object_and_unreviewed_output_budgets_are_visible(monkeypatch):
    monkeypatch.setattr(L, "MAX_OBJECTS", 4)
    monkeypatch.setattr(L, "MAX_UNREVIEWED", 2)
    result = L.report(model(*(text(uid=str(i), extents=None) for i in range(10))))
    coverage = result["layout_reminder_coverage"]
    assert coverage["budgets"]["objects_examined"] == 4
    assert coverage["budgets"]["objects_omitted"] == 6
    assert coverage["budgets"]["unreviewed_omitted"] == 2
    assert len(coverage["unreviewed_objects"]) == 2
    assert coverage["status"] == "partial"


def test_renderer_fallback_line_width_does_not_supply_native_graphic_envelope():
    data = model(text())
    data["silk"] = [
        {
            "path": [[2, 2, 0], [2, 3, 0]],
            "width": 0.12,
            "width_source": "renderer_fallback",
            "observed_side": "top",
            "owner_observed": True,
            "fabrication_layer": "silkscreen",
        }
    ]
    coverage = L.report(data)["layout_reminder_coverage"]
    assert "graphic_stroke_width_unobserved" in coverage["unreviewed_objects"][0]["reasons"]


@pytest.mark.parametrize("unit", [1, 4, None])
def test_snapshot_source_refuses_non_mils_without_drawing_or_changing_unit(
    tmp_path, monkeypatch, unit
):
    board = tmp_path / "owned.pcb"
    doc = NS(CurrentUnit=unit)
    monkeypatch.setattr(A, "_layout_board_path", lambda params: board)
    monkeypatch.setattr(A, "_application", lambda *a, **k: object())
    monkeypatch.setattr(A, "_open_layout_document", lambda *a: (doc, []))
    monkeypatch.setattr(A, "_licensed_document", lambda app: doc)
    monkeypatch.setattr(
        A, "_board_model", lambda *a, **k: pytest.fail("unit guard precedes geometry")
    )
    with pytest.raises(A.AdapterError) as caught:
        A._board_geometry({"project": str(board)}, object())
    assert caught.value.code == "E_UNSUPPORTED"
    assert caught.value.details["unit_not_changed"] is True
    assert doc.CurrentUnit == unit


def test_missing_native_fabrication_type_is_recorded_instead_of_silently_excluded(monkeypatch):
    from test_native_com_adapter import _Many

    monkeypatch.setattr(A, "_board_keepouts", lambda *a, **k: [])
    doc = NS(
        CurrentUnit=2,
        LayerCount=2,
        Components=_Many([]),
        FabricationLayerTexts=_Many([NS()]),
        FabricationLayerGfxs=_Many([NS()]),
    )
    data = A._board_model(doc)
    coverage = L.report(data)["layout_reminder_coverage"]
    assert coverage["status"] == "partial"
    reasons = [r for row in coverage["unreviewed_objects"] for r in row["reasons"]]
    assert "text_fabrication_type_unobserved" in reasons
    assert "graphic_fabrication_type_unobserved" in reasons
    assert data["layout_observation"]["collections"]["texts"]["complete"] is False
    assert data["layout_observation"]["collections"]["silk"]["complete"] is False


def test_unknown_component_placement_cannot_be_silently_treated_as_unplaced(monkeypatch):
    from test_native_com_adapter import _Many

    monkeypatch.setattr(A, "_board_keepouts", lambda *a, **k: [])
    unknown = NS(RefDes="R1")
    doc = NS(
        CurrentUnit=2,
        LayerCount=2,
        Components=_Many([unknown]),
        FabricationLayerTexts=_Many([NS(Type=2, Component=unknown)]),
        FabricationLayerGfxs=_Many([NS(Type=2, Component=unknown)]),
    )
    data = A._board_model(doc)
    coverage = L.report(data)["layout_reminder_coverage"]
    reasons = [r for row in coverage["unreviewed_objects"] for r in row["reasons"]]
    assert "component_placement_state_unobserved" in reasons
    assert "text_owner_placement_state_unobserved" in reasons
    assert "graphic_owner_placement_state_unobserved" in reasons
    assert coverage["status"] == "partial"
