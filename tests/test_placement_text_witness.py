"""Pure regression coverage for complete-board native text rounding witnesses."""

from __future__ import annotations

import copy
import math
import random
from decimal import Decimal
from types import SimpleNamespace

import pytest
from test_batch_material_validation import RepresentativeDriver, representative_board
from test_placement import guarded_plan, material_state, observations

from xpedition_cli import placement_text_witness as text_witness
from xpedition_cli.errors import CLIError
from xpedition_cli.placement import (
    canonical_state,
    execute_placement,
    expected_state,
    plan_placement,
    verify_batch_state,
)
from xpedition_cli.placement_text_witness import capture, projection


def semantics(text, component=None):
    side = text["side"]
    code = 1 if side == "top" else 512
    owner = (
        {"uid": str(component["uid"]), "refdes": component["refdes"], "placed": True}
        if component
        else None
    )
    return {
        "uid": text["uid"],
        "native_name": text["text"],
        "object_class": 2048,
        "original_layer": -99,
        "is_dimension_text": False,
        "is_flex_text_valid": True,
        "text": text["text"],
        "type": 2,
        "text_type": 1,
        "side": side,
        "side_code": code,
        "side_name": side.title(),
        "layer": -99,
        "layer_name": "",
        "fabrication_identity": {"type": 2, "side": code},
        "stackup_layer_association": {"applicability": "not_applicable_to_assembly_or_silkscreen"},
        "owner": owner,
        "parent": (
            {"object_class": 512, "uid": owner["uid"], "refdes": owner["refdes"]}
            if owner
            else {"object_class": 1, "document": "test.pcb"}
        ),
        "position": [text["x"], text["y"]],
        "format": {
            "Font": "vf_std",
            "Bold": False,
            "Italic": False,
            "Underline": False,
            "Mirrored": text["mirrored"],
            "HorizontalJust": 0,
            "VerticalJust": 0,
            "AspectRatio": 1.0,
            "Attributes": 0,
            "LineSpacing": 1.7,
            "height": text["height"],
            "pen": text["pen"],
            "orientation": text["orientation"],
            "valid": True,
            "is_vector_font": True,
        },
        "fix_lock": 0,
        "display_condition": 1,
        "reserved": False,
        "reserved_by": "",
    }


def text_board():
    rows, state = representative_board()
    names = ["U9001", "U9002", "U9003", "U9004", "U9005", "U9006"]
    renames = {row["refdes"]: names[i] for i, row in enumerate(rows[:6])}
    for row in [*rows, *state["components"], *state["model"]["pads"]]:
        row["refdes"] = renames.get(row["refdes"], row["refdes"])
    for i, comp in enumerate(state["components"]):
        raw = 3.80005 if i == 1 else comp["x"] + 0.2672500004
        values = [0.127]
        for stroke in range(5):
            for point in range(6):
                values.extend(
                    [
                        raw + (stroke - 4) * 0.01 + (point - 5) * 0.001,
                        comp["y"] + point * 0.02,
                        0 if point == 0 else 2 if point == 5 else 1,
                    ]
                )
        witness = {
            "uid": "synthetic-text-1" if i == 1 else str(1000 + i),
            "refdes": comp["refdes"],
            "extents_mm": [raw - 0.2, comp["y"] - 0.2, raw + 0.3, comp["y"] + 0.3],
            "stroke_array_mm": values,
        }
        state["text_native_witness"].append(witness)
        state["model"]["texts"].append(
            {
                "uid": witness["uid"],
                "refdes": comp["refdes"],
                "x": comp["x"],
                "y": comp["y"],
                "height": 1.0,
                "orientation": 0.0,
                "text": comp["refdes"],
                "kind": "refdes",
                "mirrored": False,
                "side": comp["side"],
                **projection(witness),
            }
        )
        state["text_semantic_witness"].append(semantics(state["model"]["texts"][-1], comp))
    free = {
        "uid": "free-board-text",
        "refdes": None,
        "extents_mm": [90.1234500004, 90.0, 91.1234500004, 91.0],
        "stroke_array_mm": [0.127, 90.1234500004, 90.0, 0, 91.1234500004, 91.0, 2],
    }
    state["text_native_witness"].append(free)
    state["model"]["texts"].append(
        {
            "uid": free["uid"],
            "refdes": None,
            "text": "BOARD",
            "height": 1.0,
            "x": 90.0,
            "y": 90.0,
            "orientation": 0.0,
            "mirrored": False,
            "side": "top",
            "kind": "refdes",
            **projection(free),
        }
    )
    state["text_semantic_witness"].append(semantics(state["model"]["texts"][-1]))
    request = {
        "schema_version": "1.0",
        "unit": "mm",
        "selection": names,
        "steps": [{"op": "translate", "dx": 0.2, "dy": 0}],
    }
    return rows, state, request


def test_synthetic_large_board_transforms_raw_before_reader_rounding():
    rows, state, request = text_board()
    plan = plan_placement(request, rows, protection_state=state)
    actual = expected_state(plan["protection_before"], plan["results"])
    text = next(row for row in actual["model"]["texts"] if row["uid"] == "synthetic-text-1")
    assert round(3.80005, 4) == 3.8001
    assert round(round(3.80005, 4) + 0.2, 4) == 4.0001
    assert text["strokes"][4][5][0] == round(3.80005 + 0.2, 4) == 4.0
    assert len(actual["components"]) == 127
    assert sum(len(c["pins"]) for c in actual["components"]) == 467
    assert sum(p["net"] == "(Net0)" for c in actual["components"] for p in c["pins"]) == 50
    assert len(actual["text_native_witness"]) == len(actual["model"]["texts"]) == 128
    assert verify_batch_state(plan, actual)["valid"] is True
    assert (
        state["text_native_witness"][0]["stroke_array_mm"][1]
        == plan["protection_before"]["text_native_witness"][0]["stroke_array_mm"][1]
    )
    untouched = {
        r["uid"]: r
        for r in actual["text_native_witness"]
        if r["refdes"] not in request["selection"]
    }
    assert untouched == {
        r["uid"]: r for r in state["text_native_witness"] if r["refdes"] not in request["selection"]
    }


@pytest.mark.parametrize("native_noise,valid", [(1e-14, True), (2e-9, False)])
def test_half_rounding_side_change_requires_verified_after_raw_geometry(native_noise, valid):
    rows, state, request = text_board()
    plan = plan_placement(request, rows, protection_state=state)
    observed = expected_state(plan["protection_before"], plan["results"])
    raw = next(row for row in observed["text_native_witness"] if row["uid"] == "synthetic-text-1")
    raw["stroke_array_mm"][-3] += native_noise
    text = next(row for row in observed["model"]["texts"] if row["uid"] == "synthetic-text-1")
    assert text["strokes"][4][5][0] == 4.0
    text.update(projection(raw))
    assert text["strokes"][4][5][0] == 4.0001
    assert verify_batch_state(plan, observed)["valid"] is valid


def test_synthetic_two_nanometre_target_stroke_difference_is_not_silently_accepted():
    # Synthetic coordinates exercise the same strict 2 nm distinction.
    original = {
        "uid": "synthetic-text-1",
        "refdes": "R1",
        "extents_mm": [0.0, 0.0, 2.0, 1.0],
        "stroke_array_mm": [0.12, 0.0, 0.0, 0, 2.0, 1.0, 2],
    }
    before = {"x": 0.0, "y": 0.0, "rotation": 0.0}
    target = {"x": 1.0, "y": 1.0, "rotation": 0.0}
    expected = text_witness.transform(original, before, target)
    actual = copy.deepcopy(expected)
    actual["stroke_array_mm"][1] += 2e-6
    assert text_witness.difference([expected], [expected], {"R1"}) is None
    assert text_witness.difference([expected], [actual], {"R1"}) == {
        "path": "text_native_witness[uid=synthetic-text-1].stroke_array_mm[1]",
        "expected": expected["stroke_array_mm"][1],
        "actual": actual["stroke_array_mm"][1],
    }


@pytest.mark.parametrize("turns", [0, 1, 2, 3])
def test_anchor_accepts_independent_decimal_rigid_result_for_every_orthogonal_turn(turns):
    before = {
        "uid": "text-1",
        "owner": {"refdes": "R1"},
        "position": [0.1, 0.1],
        "format": {"Font": "vf_std", "HorizontalJust": 0, "orientation": 0},
    }
    old = {"x": 0.2, "y": 0.2, "rotation": 0}
    target = {"x": 0.3, "y": 0.3, "rotation": turns * 90}
    expected = text_witness.transform_semantics(before, old, target)
    actual = copy.deepcopy(expected)
    # This oracle is independent of the binary-float prediction under test.
    dx, dy = Decimal("0.1") - Decimal("0.2"), Decimal("0.1") - Decimal("0.2")
    cosine, sine = ((1, 0), (0, 1), (-1, 0), (0, -1))[turns]
    actual["position"] = [
        float(Decimal("0.3") + dx * cosine - dy * sine),
        float(Decimal("0.3") + dx * sine + dy * cosine),
    ]
    movement = {"id": "R1", "changed": True, "before": old, "target": target}
    if turns == 0:
        assert expected["position"][0] == 0.19999999999999998
        assert actual["position"][0] == 0.2
    assert text_witness.semantic_difference([expected], [actual], [before], [movement]) is None


@pytest.mark.parametrize("fault", ["anchor", "font", "justification", "orientation", "non_target"])
def test_anchor_arithmetic_equivalence_does_not_hide_other_changes(fault):
    before = {
        "uid": "text-1",
        "owner": {"refdes": "R1"},
        "position": [0.1, 0.1],
        "format": {"Font": "vf_std", "HorizontalJust": 0, "orientation": 0},
    }
    old = {"x": 0.2, "y": 0.2, "rotation": 0}
    target = {"x": 0.3, "y": 0.3, "rotation": 0}
    expected = text_witness.transform_semantics(before, old, target)
    actual = copy.deepcopy(expected)
    actual["position"] = [0.2, 0.2]  # Independently evaluated decimal result.
    movement = {"id": "R1", "changed": True, "before": old, "target": target}
    if fault == "anchor":
        actual["position"][0] = math.nextafter(0.2, math.inf)
    elif fault == "font":
        actual["format"]["Font"] = "another_font"
    elif fault == "justification":
        actual["format"]["HorizontalJust"] = 1
    elif fault == "orientation":
        actual["format"]["orientation"] = 1e-10
    else:
        movement["id"] = "R2"  # R1 is not moved and must stay bit-for-bit exact.
    assert text_witness.semantic_difference([expected], [actual], [before], [movement]) is not None


def test_bounded_anchor_candidates_never_admit_a_two_nm_shift():
    generator = random.Random(20261030)
    samples = [
        (100_000.0, 100_000.0, -100_000.0),
        (-100_000.0, -100_000.0, 100_000.0),
        (0.3, 0.1, 0.2),
        *[tuple(generator.uniform(-100_000, 100_000) for _ in range(3)) for _ in range(1000)],
    ]
    roundoff_bound = math.ulp(200_001.0) + math.ulp(300_001.0)
    for target, anchor, origin in samples:
        for sign in (-1, 1):
            candidates = text_witness._anchor_coordinate_candidates(target, anchor, origin, sign)
            assert max(candidates) - min(candidates) <= roundoff_bound < 1e-9
            assert all(value + 0.000002 not in candidates for value in candidates)


def test_anchor_candidate_generation_rejects_out_of_contract_coordinates():
    with pytest.raises(ValueError, match="bounded coordinate"):
        text_witness._anchor_coordinate_candidates(0.0, 100_001.0, 0.0, 1)


def test_every_predicted_orthogonal_anchor_is_in_its_finite_candidate_set():
    generator = random.Random(20261030)
    for turns in range(4):
        for _ in range(200):
            position = [generator.uniform(-100_000, 100_000) for _ in range(2)]
            old = {
                "x": generator.uniform(-100_000, 100_000),
                "y": generator.uniform(-100_000, 100_000),
                "rotation": 0,
            }
            target = {
                "x": generator.uniform(-100_000, 100_000),
                "y": generator.uniform(-100_000, 100_000),
                "rotation": turns * 90,
            }
            row = {"position": position, "format": {"orientation": 0}}
            predicted = text_witness.transform_semantics(row, old, target)["position"]
            allowed = text_witness._rigid_anchor_candidates(position, old, target)
            assert all(value in choices for value, choices in zip(predicted, allowed, strict=True))


def test_full_guard_accepts_independent_anchor_arithmetic_but_not_two_nm_raw_change():
    rows = observations()
    rows[0]["x"] = 0.2
    state = material_state(rows)
    witness = {
        "uid": "text-1",
        "refdes": "R1",
        "extents_mm": [0.1, 0.0, 0.2, 0.2],
        "stroke_array_mm": [0.1, 0.1, 0.1, 0, 0.2, 0.1, 2],
    }
    text = {
        "uid": "text-1",
        "refdes": "R1",
        "text": "R1",
        "x": 0.1,
        "y": 0.1,
        "height": 1.0,
        "orientation": 0.0,
        "mirrored": False,
        "side": "top",
        "kind": "refdes",
        **projection(witness),
    }
    state["model"]["texts"].append(text)
    state["text_native_witness"].append(witness)
    state["text_semantic_witness"].append(semantics(text, state["components"][0]))
    request = {
        "schema_version": "1.0",
        "unit": "mm",
        "selection": ["R1"],
        "steps": [{"op": "translate", "dx": 0.1, "dy": 0}],
    }
    plan = plan_placement(request, rows, protection_state=state)
    observed = expected_state(plan["protection_before"], plan["results"])
    assert observed["text_semantic_witness"][0]["position"][0] == 0.19999999999999998
    observed["text_semantic_witness"][0]["position"][0] = float(
        Decimal("0.3") + Decimal("0.1") - Decimal("0.2")
    )
    assert verify_batch_state(plan, observed)["valid"] is True
    observed["text_native_witness"][0]["stroke_array_mm"][1] += 0.000002
    observed["model"]["texts"][0].update(projection(observed["text_native_witness"][0]))
    result = verify_batch_state(plan, observed)
    assert result["valid"] is False
    assert result["difference"]["path"].startswith("text_native_witness")


@pytest.mark.parametrize(
    "fault",
    [
        "font",
        "horizontal",
        "vertical",
        "bold",
        "italic",
        "underline",
        "aspect",
        "attributes",
        "spacing",
        "format_pen",
        "height",
        "text",
        "position",
        "subnanometre_position",
        "orientation",
        "display",
        "lock",
        "reservation",
        "parent",
        "native_name",
        "object_class",
        "original_layer",
        "dimension",
        "flex",
        "vector_font",
        "non_target_font",
        "missing_semantics",
    ],
)
def test_semantic_guard_rejects_actual_target_and_non_target_text_changes(fault):
    rows, state, request = text_board()
    plan = plan_placement(request, rows, protection_state=state)
    observed = expected_state(plan["protection_before"], plan["results"])
    semantic = next(
        s
        for s in observed["text_semantic_witness"]
        if s["owner"] and s["owner"]["refdes"] == "U9001"
    )
    model = next(t for t in observed["model"]["texts"] if t["uid"] == semantic["uid"])
    fmt = semantic["format"]
    if fault == "font":
        fmt["Font"] = "another_font"
    elif fault == "horizontal":
        fmt["HorizontalJust"] = 1
    elif fault == "vertical":
        fmt["VerticalJust"] = 1
    elif fault == "bold":
        fmt["Bold"] = True
    elif fault == "italic":
        fmt["Italic"] = True
    elif fault == "underline":
        fmt["Underline"] = True
    elif fault == "aspect":
        fmt["AspectRatio"] += 1e-10
    elif fault == "attributes":
        fmt["Attributes"] = 1
    elif fault == "spacing":
        fmt["LineSpacing"] += 1e-10
    elif fault == "format_pen":
        fmt["pen"] += 1e-10
    elif fault == "height":
        fmt["height"] += 1e-7
        model["height"] += 1e-7
    elif fault == "text":
        semantic["text"] = model["text"] = "REPLACED"
    elif fault == "position":
        semantic["position"][0] += 1e-7
        model["x"] += 1e-7
    elif fault == "subnanometre_position":
        semantic["position"][0] += 1e-10
    elif fault == "orientation":
        fmt["orientation"] += 1e-7
        model["orientation"] += 1e-7
    elif fault == "display":
        semantic["display_condition"] += 1
    elif fault == "lock":
        semantic["fix_lock"] += 1
    elif fault == "reservation":
        semantic["reserved"] = True
        semantic["reserved_by"] = "holder"
    elif fault == "parent":
        semantic["parent"]["object_class"] += 1
    elif fault == "native_name":
        semantic["native_name"] = "replacement"
    elif fault == "object_class":
        semantic["object_class"] += 1
    elif fault == "original_layer":
        semantic["original_layer"] += 1
    elif fault == "dimension":
        semantic["is_dimension_text"] = True
    elif fault == "flex":
        semantic["is_flex_text_valid"] = False
    elif fault == "vector_font":
        fmt["is_vector_font"] = False
    elif fault == "non_target_font":
        observed["text_semantic_witness"][-1]["format"]["Font"] = "replacement"
    else:
        observed.pop("text_semantic_witness")
    assert verify_batch_state(plan, observed)["valid"] is False


def test_missing_semantics_refuses_before_dispatch_but_old_snapshot_stays_readable():
    driver = RepresentativeDriver()
    driver.rows, driver.material, request = text_board()
    del driver.material["text_semantic_witness"]
    assert "text_semantic_witness" not in canonical_state(driver.material)
    with pytest.raises(CLIError) as error:
        execute_placement(request, "a" * 64, driver)
    assert error.value.details["write_attempted"] is False
    assert not driver.calls


def test_existing_orthogonal_text_rotation_remains_supported():
    rows, state, request = text_board()
    request["steps"] = [{"op": "rotate", "angle": 90, "origin": [0, 0]}]
    plan = plan_placement(request, rows, protection_state=state)
    actual = expected_state(plan["protection_before"], plan["results"])
    assert verify_batch_state(plan, actual)["valid"]
    original = next(r for r in state["text_native_witness"] if r["uid"] == "synthetic-text-1")
    rotated = next(r for r in actual["text_native_witness"] if r["uid"] == "synthetic-text-1")
    assert rotated["stroke_array_mm"][1:3] == [
        -original["stroke_array_mm"][2],
        original["stroke_array_mm"][1],
    ]


@pytest.mark.parametrize(
    "fault",
    [
        "selected_sub_projection_geometry",
        "non_target_raw_geometry",
        "free_text_raw_geometry",
        "text_uid",
        "format",
        "raw_pen",
        "stroke_code",
        "missing_witness",
        "duplicate_witness",
        "duplicate_text_uid",
        "wrong_owner",
        "raw_projection_mismatch",
    ],
)
def test_full_text_guard_rejects_actual_micro_geometry_identity_and_format_changes(fault):
    rows, state, request = text_board()
    plan = plan_placement(request, rows, protection_state=state)
    observed = expected_state(plan["protection_before"], plan["results"])
    text = next(row for row in observed["model"]["texts"] if row["refdes"] == "U9001")
    raw = next(row for row in observed["text_native_witness"] if row["uid"] == text["uid"])
    if fault == "selected_sub_projection_geometry":
        raw["stroke_array_mm"][1] += 1e-7
        text.update(projection(raw))
    elif fault == "non_target_raw_geometry":
        raw = next(r for r in observed["text_native_witness"] if r["refdes"] == rows[-1]["refdes"])
        raw["stroke_array_mm"][1] += 1e-10
    elif fault == "free_text_raw_geometry":
        observed["text_native_witness"][-1]["extents_mm"][0] += 1e-10
    elif fault == "text_uid":
        text["uid"] = raw["uid"] = "replacement"
    elif fault == "format":
        text["height"] += 1e-7
    elif fault == "raw_pen":
        raw["stroke_array_mm"][0] += 1e-8
    elif fault == "stroke_code":
        raw["stroke_array_mm"][3] = 1
    elif fault == "missing_witness":
        observed["text_native_witness"].pop()
    elif fault == "duplicate_witness":
        observed["text_native_witness"][1] = copy.deepcopy(raw)
    elif fault == "duplicate_text_uid":
        observed["model"]["texts"][1]["uid"] = text["uid"]
    elif fault == "wrong_owner":
        raw["refdes"] = "U9002"
    elif fault == "raw_projection_mismatch":
        text["strokes"][0][0][0] += 0.0001
    result = verify_batch_state(plan, observed)
    assert result["valid"] is False
    assert result.get("difference") or result.get("issue")


@pytest.mark.parametrize("fault", ["missing", "duplicate", "missing_field"])
def test_bad_or_missing_raw_capture_is_refused_before_drc_or_component_dispatch(fault):
    driver = RepresentativeDriver()
    driver.rows, driver.material, request = text_board()
    if fault == "missing":
        driver.material["text_native_witness"].pop()
    elif fault == "duplicate":
        driver.material["text_native_witness"][1] = copy.deepcopy(
            driver.material["text_native_witness"][0]
        )
    else:
        driver.material.pop("text_native_witness")
    with pytest.raises(CLIError) as error:
        execute_placement(request, "a" * 64, driver)
    assert error.value.details["write_attempted"] is False
    assert not driver.calls


@pytest.mark.parametrize("fault", ["micro_text", "non_target", "old_copper", "format"])
def test_full_board_protection_failure_retains_difference_and_zero_save_close_open(fault):
    driver = RepresentativeDriver()
    driver.rows, driver.material, request = text_board()
    old_move = driver.move

    def move(target):
        old_move(target)
        if target["refdes"] != request["selection"][-1]:
            return
        if fault == "micro_text":
            raw = next(r for r in driver.material["text_native_witness"] if r["refdes"] == "U9001")
            raw["stroke_array_mm"][1] += 1e-7
            next(r for r in driver.material["model"]["texts"] if r["uid"] == raw["uid"]).update(
                projection(raw)
            )
        elif fault == "non_target":
            driver.material["components"][-1]["part_number"] = "CHANGED"
        elif fault == "old_copper":
            driver.material["model"]["traces"][0]["width"] += 1e-8
        else:
            driver.material["model"]["texts"][0]["mirrored"] = True

    driver.move = move
    report = execute_placement(request, guarded_plan(request, driver)["state_digest"], driver)
    assert report["outcome"] == "partial_failure"
    assert report["saved"] is False and report["drc_restored"] is True
    assert report["lifecycle"] == {"save_count": 0, "close_count": 0, "open_count": 0}
    assert len(report["summary"]["applied"]) == 6
    assert "save" not in driver.calls
    issue = report["issues"][0]
    assert issue["message"] == "full-board placement protection failed"
    assert issue["details"]["verification"]["difference"]["path"]


def test_independent_native_inventory_is_bound_to_every_material_text_uid(monkeypatch):
    from xpedition_cli import board_labels

    _, state, _ = text_board()
    rows = copy.deepcopy(state["text_native_witness"])
    by_uid = {row["uid"]: row for row in state["text_semantic_witness"]}

    def read_semantics(text, board, bridge, *, canonical):
        assert canonical is False
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

    class Collection:
        Count = len(rows)

    collection = Collection()
    bridge = SimpleNamespace(
        UNIT_MM=4,
        FAB_GFX_SILKSCREEN=2,
        _items=lambda value, strict: [
            SimpleNamespace(
                Type=2, UniqueId=r["uid"], row=r, StrokeText=lambda unit, r=r: r["stroke_array_mm"]
            )
            for r in rows
        ],
        _fabrication_owner=lambda text, strict: (text.row["refdes"], True),
        _extrema_mm=lambda text: text.row["extents_mm"],
    )
    protected = copy.deepcopy(state)
    protected.pop("text_native_witness")
    protected.pop("text_semantic_witness")
    doc = SimpleNamespace(FabricationLayerTexts=collection, FullName="test.pcb")
    captured = capture(doc, bridge, protected)
    assert canonical_state(captured) == canonical_state(state)
    rows[-1]["uid"] = rows[0]["uid"]
    with pytest.raises(CLIError, match="ambiguous"):
        capture(doc, bridge, protected)
