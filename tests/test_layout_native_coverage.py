"""Partial diagnostic reads stay distinct from complete public artifact acceptance."""

import pytest
from layout_native_inputs import bind_source

from xpedition_cli import native_com_adapter as A
from xpedition_cli.layout_reminders import report


def observed(tmp_path, monkeypatch, mode):
    board = tmp_path / "owned.pcb"
    doc = bind_source(monkeypatch, board, mode)
    if mode not in {"board_cutout", "board_cutouts_failed"}:
        with pytest.raises(A.AdapterError) as caught:
            A._board_geometry({"project": str(board), "start": False}, object())
        assert caught.value.code == "E_UNSUPPORTED"
    # This low-level partial model remains useful diagnostic evidence. It cannot
    # qualify a public snapshot/render or any engineering mutation.
    model = A._board_model(doc, strict=False)
    return doc, {"model": model, **report(model)}


@pytest.mark.parametrize(
    "mode,collection,uid,reason,rule",
    [
        ("empty_trace", "traces", "317", "trace_geometry_unrepresentable", "text_trace"),
        (
            "trace_geometry_getter_failed",
            "traces",
            "317",
            "trace_geometry_read_failed",
            "text_trace",
        ),
        (
            "empty_graphic",
            "silk",
            "601",
            "graphic_geometry_unrepresentable",
            "text_component_graphic",
        ),
        (
            "graphic_geometry_getter_failed",
            "silk",
            "601",
            "graphic_geometry_read_failed",
            "text_component_graphic",
        ),
    ],
)
def test_empty_conversion_is_separate_from_complete_native_enumeration(
    tmp_path, monkeypatch, mode, collection, uid, reason, rule
):
    _, result = observed(tmp_path, monkeypatch, mode)
    coverage = result["layout_reminder_coverage"]
    native = coverage["collection_coverage"][collection]
    assert native["enumeration_complete"] is True
    assert native["observed_count"] == 1
    assert native["geometry_complete"] is False
    assert native["geometry_observed_count"] == 0
    assert native["geometry_unreadable_count"] == 1
    assert native["reminder_checkable_count"] == 0
    assert native["reminder_uncheckable_count"] == 1
    assert native["reminder_checkable_complete"] is False
    item = next(item for item in coverage["unreviewed_objects"] if item["object"].get("uid") == uid)
    assert reason in item["reasons"]
    assert item["affected_rules"] == [rule]
    assert coverage["status"] == "partial"


@pytest.mark.parametrize(
    "mode",
    [
        "trace_layer_missing",
        "trace_layer_failed",
        "trace_layer_zero",
        "trace_layer_invalid",
        "trace_layer_large",
    ],
)
def test_native_trace_layer_is_not_invented_by_renderer_default(tmp_path, monkeypatch, mode):
    _, result = observed(tmp_path, monkeypatch, mode)
    coverage = result["layout_reminder_coverage"]
    trace = result["model"]["traces"][0]
    assert trace["layer"] is None
    inventory = coverage["collection_coverage"]["traces"]
    assert inventory["geometry_complete"] is True
    assert inventory["geometry_observed_count"] == 1
    assert inventory["reminder_checkable_count"] == 0
    assert inventory["reminder_uncheckable_count"] == 1
    assert inventory["reminder_checkable_complete"] is False
    item = next(
        item for item in coverage["unreviewed_objects"] if item["object"].get("uid") == "318"
    )
    assert "conductor_layer_unobserved_or_invalid" in item["reasons"]
    check = next(r for r in coverage["checked_rules"] if r["rule"] == "text_trace")
    assert check["pairs_checked"] == 0
    assert check["pairs_excluded"] == 0
    assert check["pairs_unchecked"] == 1


@pytest.mark.parametrize(
    "mode", ["layer_count_missing", "layer_count_failed", "layer_count_zero", "layer_count_invalid"]
)
def test_render_layer_count_fallback_cannot_exclude_opposite_side(tmp_path, monkeypatch, mode):
    _, result = observed(tmp_path, monkeypatch, mode)
    coverage = result["layout_reminder_coverage"]
    assert result["model"]["layers"] == 2  # display fallback only
    assert coverage["layer_count"]["observed"] is False
    assert coverage["layer_count"]["value"] is None
    assert coverage["status"] == "partial"
    check = next(r for r in coverage["checked_rules"] if r["rule"] == "text_trace")
    assert check["pairs_excluded"] == 0
    assert check["pairs_unchecked"] == 1


def test_first_unreadable_trace_cannot_steal_second_geometry_width_or_identity(
    tmp_path, monkeypatch
):
    _, result = observed(tmp_path, monkeypatch, "first_trace_unreadable")
    assert len(result["model"]["traces"]) == 1
    shape = result["model"]["traces"][0]
    assert shape["width"] == pytest.approx(0.254)
    metadata = result["model"]["layout_observation"]["trace_envelopes"][0]
    assert metadata["model_index"] == 0 and metadata["observation_index"] == 1
    assert metadata["uid"] == "318"
    warning = next(
        w for w in result["layout_reminders"] if w["kind"] == "projected_text_trace_overlap"
    )
    assert warning["objects"][1]["uid"] == "318"
    assert warning["objects"][1]["observation_index"] == 1
    assert warning["objects"][1]["model_index"] == 0


@pytest.mark.parametrize(
    "mode,reason",
    [
        ("board_cutout", "board_inner_contours_not_checked"),
        ("board_cutouts_failed", "board_inner_contour_inventory_unobserved"),
    ],
)
def test_rectangular_outer_edge_is_not_complete_internal_board_boundary_coverage(
    tmp_path, monkeypatch, mode, reason
):
    _, result = observed(tmp_path, monkeypatch, mode)
    coverage = result["layout_reminder_coverage"]
    assert coverage["status"] == "partial"
    assert coverage["board_edge_coverage"]["outer_boundary_checked"] is True
    assert coverage["board_edge_coverage"]["complete"] is False
    assert any(
        reason in item["reasons"] and item["affected_rules"] == ["text_board_edge"]
        for item in coverage["unreviewed_objects"]
    )
    check = next(r for r in coverage["checked_rules"] if r["rule"] == "text_board_edge")
    assert check["pairs_checked"] == 4
    assert check["pairs_unchecked"] == 1


def test_actual_render_refuses_source_reader_omissions_without_artifacts(tmp_path, monkeypatch):
    board = tmp_path / "owned.pcb"
    doc = bind_source(monkeypatch, board, "first_trace_unreadable")
    output, model = tmp_path / "image.png", tmp_path / "model.json"
    with pytest.raises(A.AdapterError) as caught:
        A._render_board(
            {
                "project": str(board),
                "output": str(output),
                "model_output": str(model),
                "start": False,
                "scale": 2,
            },
            object(),
        )
    assert caught.value.code == "E_UNSUPPORTED"
    assert not output.exists() and not model.exists()
    partial = A._board_model(doc, strict=False)
    assert (
        report(partial)["layout_reminder_coverage"]["collection_coverage"]["traces"][
            "geometry_complete"
        ]
        is False
    )
    assert partial["layout_observation"]["trace_envelopes"][0]["observation_index"] == 1


@pytest.mark.parametrize(
    "mode",
    [
        "trace_layer_missing",
        "trace_layer_failed",
        "trace_layer_zero",
        "trace_layer_invalid",
        "trace_layer_large",
        "layer_count_missing",
        "layer_count_failed",
        "layer_count_zero",
        "layer_count_invalid",
    ],
)
def test_strict_protection_never_accepts_display_layer_fallback(tmp_path, monkeypatch, mode):
    doc = bind_source(monkeypatch, tmp_path / "owned.pcb", mode)
    with pytest.raises(A.AdapterError) as caught:
        A._board_model(doc, strict=True)
    assert caught.value.code == "E_UNSUPPORTED"
