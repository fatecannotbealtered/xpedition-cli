"""Placement raw-witness resource bounds are checked before native enumeration."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest
from test_placement import material_state, observations
from test_placement_text_witness import semantics

from xpedition_cli.errors import CLIError
from xpedition_cli.placement import MAX_PROTECTION_BYTES, canonical_state
from xpedition_cli.placement_text_witness import (
    MAX_RAW_POINTS_TOTAL,
    MAX_TEXT_POINTS,
    MAX_TEXTS,
    capture,
    contract,
    projection,
    validate,
)


def raw_values(points):
    values = [0.127]
    for index in range(points):
        values.extend([1.2345, 2.3456, 0 if index == 0 else 2 if index == points - 1 else 1])
    return values


def witness_state(point_counts):
    state = {"model": {"texts": []}, "text_native_witness": [], "text_semantic_witness": []}
    for index, points in enumerate(point_counts):
        row = {
            "uid": str(index + 1),
            "refdes": None,
            "extents_mm": [1.0, 2.0, 3.0, 4.0],
            "stroke_array_mm": raw_values(points),
        }
        state["text_native_witness"].append(row)
        text = {
            "uid": row["uid"],
            "refdes": None,
            "text": "BOARD",
            "x": 1.0,
            "y": 2.0,
            "height": 1.0,
            "orientation": 0.0,
            "mirrored": False,
            "side": "top",
            "kind": "refdes",
            **projection(row),
        }
        state["model"]["texts"].append(text)
        state["text_semantic_witness"].append(semantics(text))
    return state


@pytest.mark.parametrize("count", [None, True, 1.0, "1", -1, MAX_TEXTS + 1])
def test_invalid_native_count_refuses_before_enumeration_or_geometry(count):
    def unexpected(*args, **kwargs):
        pytest.fail("invalid Count must be rejected before enumeration or native geometry")

    bridge = SimpleNamespace(_items=unexpected)
    doc = SimpleNamespace(FabricationLayerTexts=SimpleNamespace(Count=count))
    with pytest.raises(CLIError, match="count is invalid or exceeds"):
        capture(doc, bridge, {"model": {"texts": []}})


@pytest.mark.parametrize("count", [0, MAX_TEXTS])
def test_exact_native_count_limit_is_accepted(count):
    bridge = SimpleNamespace(
        FAB_GFX_SILKSCREEN=2,
        _items=lambda value, strict: (SimpleNamespace(Type=3) for _ in range(count)),
    )
    doc = SimpleNamespace(FabricationLayerTexts=SimpleNamespace(Count=count))
    assert capture(doc, bridge, {"model": {"texts": []}})["text_native_witness"] == []


def test_validate_exact_text_count_limit_and_one_above():
    state = witness_state([1] * MAX_TEXTS)
    assert len(validate(state)) == MAX_TEXTS
    extra = copy.deepcopy(state["text_native_witness"][0])
    extra["uid"] = str(MAX_TEXTS + 1)
    state["text_native_witness"].append(extra)
    state["model"]["texts"].append({"uid": extra["uid"], "refdes": None, **projection(extra)})
    with pytest.raises(CLIError, match="complete native text witnesses"):
        validate(state)


def test_validate_exact_per_text_point_limit_and_one_above():
    state = witness_state([MAX_TEXT_POINTS])
    assert len(validate(state)) == 1
    state["text_native_witness"][0]["stroke_array_mm"].extend([1.2345, 2.3456, 2])
    with pytest.raises(CLIError, match="per-text capacity"):
        validate(state)


def test_validate_exact_total_raw_point_limit_and_one_above():
    state = witness_state([MAX_TEXT_POINTS] * (MAX_RAW_POINTS_TOTAL // MAX_TEXT_POINTS))
    assert len(validate(state)) == 4
    extra = witness_state([1])
    extra["text_native_witness"][0]["uid"] = extra["model"]["texts"][0]["uid"] = "extra"
    state["text_native_witness"].extend(extra["text_native_witness"])
    state["model"]["texts"].extend(extra["model"]["texts"])
    with pytest.raises(CLIError, match="full-board native text raw points exceed"):
        validate(state)


@pytest.mark.parametrize("counts", [[MAX_TEXT_POINTS], [MAX_TEXT_POINTS] * 4])
def test_native_capture_accepts_exact_per_text_and_whole_board_point_limits(counts, monkeypatch):
    from xpedition_cli import board_labels

    state = witness_state(counts)
    rows = state["text_native_witness"]
    by_uid = {row["uid"]: row for row in state["text_semantic_witness"]}

    def read_semantics(text, board, bridge, *, canonical):
        raw = text.row
        return {
            **copy.deepcopy(by_uid[raw["uid"]]),
            "extents": [],
            "strokes": [],
            "stroke_pen": raw["stroke_array_mm"][0],
            "native_geometry_witness": {
                "extents_mm": [repr(v) for v in raw["extents_mm"]],
                "stroke_array_mm": [repr(v) for v in raw["stroke_array_mm"]],
            },
        }

    monkeypatch.setattr(board_labels, "_text", read_semantics)
    bridge = SimpleNamespace(
        UNIT_MM=4,
        FAB_GFX_SILKSCREEN=2,
        _items=lambda value, strict: (
            SimpleNamespace(
                Type=2,
                UniqueId=row["uid"],
                row=row,
                StrokeText=lambda unit, row=row: row["stroke_array_mm"],
            )
            for row in rows
        ),
        _fabrication_owner=lambda text, strict: (None, True),
        _extrema_mm=lambda text: text.row["extents_mm"],
    )
    protected = {"model": state["model"]}
    captured = capture(
        SimpleNamespace(
            FabricationLayerTexts=SimpleNamespace(Count=len(rows)), FullName="test.pcb"
        ),
        bridge,
        protected,
    )
    assert captured == state


def test_native_capture_rejects_per_text_overflow_before_extrema_or_next_text():
    values = raw_values(MAX_TEXT_POINTS + 1)
    text = SimpleNamespace(Type=2, UniqueId=1, StrokeText=lambda unit: values)
    bridge = SimpleNamespace(
        UNIT_MM=4,
        FAB_GFX_SILKSCREEN=2,
        _items=lambda value, strict: iter([text]),
        _fabrication_owner=lambda text, strict: (None, True),
    )
    with pytest.raises(CLIError, match="per-text capacity"):
        capture(
            SimpleNamespace(FabricationLayerTexts=SimpleNamespace(Count=1)),
            bridge,
            {"model": {"texts": []}},
        )


def test_capture_checks_full_board_points_as_each_native_text_is_collected(monkeypatch):
    from xpedition_cli import board_labels

    values = raw_values(MAX_TEXT_POINTS)
    calls = []

    monkeypatch.setattr(
        board_labels,
        "_text",
        lambda text, board, bridge, canonical: {
            "uid": str(text.UniqueId),
            "extents": [],
            "strokes": [],
            "stroke_pen": values[0],
            "native_geometry_witness": {
                "extents_mm": [repr(v) for v in [1.0, 2.0, 3.0, 4.0]],
                "stroke_array_mm": [repr(v) for v in values],
            },
        },
    )

    def items(collection, *, strict):
        for index in range(6):
            calls.append(("item", index))

            def strokes(unit, index=index):
                assert unit == 4
                calls.append(("strokes", index))
                return values if index < 4 else raw_values(1)

            yield SimpleNamespace(Type=2, UniqueId=index + 1, StrokeText=strokes)

    def extrema(text):
        calls.append(("extrema", text.UniqueId - 1))
        return [1.0, 2.0, 3.0, 4.0]

    bridge = SimpleNamespace(
        UNIT_MM=4,
        FAB_GFX_SILKSCREEN=2,
        _items=items,
        _fabrication_owner=lambda text, strict: (None, True),
        _extrema_mm=extrema,
    )
    with pytest.raises(CLIError, match="full-board native text raw points exceed"):
        capture(
            SimpleNamespace(FabricationLayerTexts=SimpleNamespace(Count=6), FullName="test.pcb"),
            bridge,
            {"model": {"texts": []}},
        )
    assert [kind for kind, _ in calls].count("strokes") == 5
    assert [kind for kind, _ in calls].count("extrema") == 4
    assert ("item", 5) not in calls


@pytest.mark.parametrize("uid", [0, "0", " 0 "])
def test_native_uid_zero_refuses_before_reading_raw_geometry(uid):
    text = SimpleNamespace(Type=2, UniqueId=uid)
    bridge = SimpleNamespace(
        FAB_GFX_SILKSCREEN=2,
        _items=lambda value, strict: iter([text]),
        _fabrication_owner=lambda text, strict: (None, True),
    )
    with pytest.raises(CLIError, match="UID is unavailable"):
        capture(
            SimpleNamespace(FabricationLayerTexts=SimpleNamespace(Count=1)),
            bridge,
            {"model": {"texts": []}},
        )
    state = witness_state([1])
    state["text_native_witness"][0]["uid"] = state["model"]["texts"][0]["uid"] = str(uid)
    with pytest.raises(CLIError, match="identity is missing or ambiguous"):
        validate(state)


def test_whole_material_byte_limit_includes_raw_witness(monkeypatch):
    from xpedition_cli import placement

    raw = witness_state([100])
    state = material_state(observations())
    state["model"]["texts"] = raw["model"]["texts"]
    state["text_native_witness"] = raw["text_native_witness"]
    state["text_semantic_witness"] = raw["text_semantic_witness"]
    total_bytes = len(json.dumps(state, allow_nan=False, ensure_ascii=False).encode("utf-8"))
    monkeypatch.setattr(placement, "MAX_PROTECTION_BYTES", total_bytes)
    assert canonical_state(state)["text_native_witness"] == state["text_native_witness"]
    monkeypatch.setattr(placement, "MAX_PROTECTION_BYTES", total_bytes - 1)
    with pytest.raises(CLIError, match="byte budget"):
        canonical_state(state)


def test_read_only_contract_is_bounded_explicit_and_returns_independent_values():
    value = contract()
    assert value["limits"] == {
        "max_texts": 16384,
        "max_points_per_text": 16384,
        "max_raw_points_total": 65536,
        "max_full_material_bytes_including_witness": MAX_PROTECTION_BYTES,
    }
    assert value["target_raw_coordinate_arithmetic_tolerance_mm"] == 1e-9
    assert value["non_target_raw_geometry"] == "exact"
    assert "unique nonzero" in value["uid_binding"] and "one-to-one" in value["uid_binding"]
    assert "none" in value["cross_phase_cache"]
    value["limits"]["max_texts"] = 0
    assert contract()["limits"]["max_texts"] == MAX_TEXTS


def test_native_and_sdk_count_preflight_refuse_before_first_full_material_capture(monkeypatch):
    from xpedition_cli import cell_refresh, placement_text_witness
    from xpedition_cli.native_placement import LayoutPlacementDriver

    doc = SimpleNamespace(FabricationLayerTexts=SimpleNamespace(Count=MAX_TEXTS + 1))

    def unexpected(*args, **kwargs):
        pytest.fail("oversized text Count must precede the first full material capture")

    monkeypatch.setattr(cell_refresh, "capture", unexpected)
    native = SimpleNamespace(doc=doc, bridge=object())
    with pytest.raises(CLIError, match="count is invalid or exceeds"):
        LayoutPlacementDriver.capture_state(native)
    monkeypatch.setattr(cell_refresh, "active", lambda *args, **kwargs: (None, None, None, doc))
    with pytest.raises(CLIError, match="count is invalid or exceeds"):
        placement_text_witness.observe({"strict": True, "observe_only": True}, None, object())
