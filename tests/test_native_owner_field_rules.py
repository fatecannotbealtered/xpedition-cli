"""Real Layout-fingerprint-shaped finite-field protection, entirely offline."""

from __future__ import annotations

from copy import deepcopy

import pytest

from scripts.native_acceptance import owner_field_rules as rules


@pytest.fixture
def baseline():
    geometry = {
        "components": [{"refdes": "J1", "uid": "67", "unit": "mm", "x": 20.0, "y": 15.0}],
        "model": {"pads": [{"net": "QA_SIGNAL", "refdes": "J1", "pin": "1"}], "traces": []},
        "stackup": [
            {
                "kind": "conductor",
                "layer": layer,
                "name": f"SIGNAL_{layer}",
                "board_layer": layer * 2,
            }
            for layer in range(1, 5)
        ],
    }
    classes = {
        "name": "(Default)",
        "observation_index": 0,
        "nets": [{"name": "QA_SIGNAL", "uid": "123"}],
    }
    for scheme, field in rules.SCHEMES.items():
        classes[field] = [
            {
                "layer": layer,
                "scheme": scheme,
                "unit": rules.MM_SOURCE,
                "MinTraceWidth": 0.254,
                "TypicalTraceWidth": 0.254,
                "ExpansionTraceWidth": 0.3048 if layer == 1 else 0.254,
            }
            for layer in range(1, 5)
        ]
    return {
        "widths": {
            "complete": True,
            "layer_count": 4,
            "constraints_uid_mapping": "ConstraintsAuto provides no persistent UID",
            "components": [{"name": "J1", "uid": "67", "pins": [{"name": "1", "uid": "70"}]}],
            "nets": [{"name": "(Net0)", "net_class_observation": {"complete": False}}],
            "classes": [classes],
            "class_membership_complete": False,
            "class_membership_unreviewed": [{"name": "(Net0)"}],
            "named_minimum_observation": {
                "complete": True,
                "scheme": "(Minimum)",
                "unit": rules.MM_SOURCE,
                "class_count": 1,
                "layer_count": 4,
                "derived_or_effective_rule_semantics_proven": False,
            },
        },
        "raw_model": {
            "layers": 4,
            "traces": [],
            "pads": [{"kind": "pad", "net": "QA_SIGNAL"}],
            "layout_observation": {
                "units": "mm",
                "unreadable_objects": [],
                "layer_count": {
                    "observed": True,
                    "value": 4,
                    "source": "native_document.LayerCount",
                    "renderer_value": 4,
                    "renderer_source": "native_document.LayerCount",
                },
            },
        },
        "geometry": geometry,
        "planes": {
            "geometry": deepcopy(geometry),
            "plane_state": {
                "assignments": [{"net": "QA_RETURN", "mode": "Dynamic"}],
                "generated": [{"uid": "756", "net": "QA_RETURN", "layer": 1}],
                "shapes": [],
                "classes": [],
            },
        },
        "ties": {
            "source": rules.TIE_SOURCE,
            "units": "mm",
            "complete": False,
            "errors": [{"field": "collection.items.uid", "cause": "duplicate_uid"}],
            "collection": {"count": 1, "items": [{"uid": "0", "net": "QA_RETURN"}]},
        },
        "limits": "Bounded outputs; Net0/membership and UID0/thermal limits remain",
    }


def changed(baseline, *, minimum=0.3048):
    result = deepcopy(baseline)
    target = result["widths"]["classes"][0]
    target["named_master_widths"][0]["ExpansionTraceWidth"] = 0.3556
    target["named_minimum_widths"][0]["ExpansionTraceWidth"] = minimum
    return result


@pytest.mark.parametrize("minimum", [0.3048, 0.3556])
def test_exact_consumed_target_and_only_discrete_minimum_outcome(baseline, minimum):
    original = deepcopy(baseline)
    assert rules.validate_baseline(baseline)["named_widths"] == 24
    after = changed(baseline, minimum=minimum)
    report = rules.compare_consumed(baseline, after)
    assert report["consumed"] is True
    assert report["protected_named_widths"] == 22
    assert report["full_remaining_fingerprint_equal"] is True
    assert report["minimum_mm"] == str(minimum)
    assert report["complete_thermal_material_proven"] is False
    assert baseline == original
    assert baseline["widths"]["class_membership_complete"] is False
    assert after["ties"]["complete"] is False


def test_release_that_layout_still_reads_twelve_is_not_consumed(baseline):
    with pytest.raises(ValueError, match="declared discrete outcomes"):
        rules.compare_consumed(baseline, deepcopy(baseline))


@pytest.mark.parametrize("minimum", [0.3302, 0.355600000000001, "0.3556"])
def test_minimum_near_or_other_values_are_not_tolerance_equivalent(baseline, minimum):
    with pytest.raises(ValueError):
        rules.compare_consumed(baseline, changed(baseline, minimum=minimum))


@pytest.mark.parametrize(
    "case", ["wrong_class", "duplicate_class", "wrong_layer", "duplicate_layer", "wrong_full_name"]
)
def test_wrong_or_ambiguous_target_identity_is_rejected(baseline, case):
    after = changed(baseline)
    cls = after["widths"]["classes"][0]
    if case == "wrong_class":
        cls["name"] = "other"
    elif case == "duplicate_class":
        after["widths"]["classes"].append(deepcopy(cls))
    elif case == "wrong_layer":
        cls["named_master_widths"][0]["ExpansionTraceWidth"] = 0.3048
        cls["named_master_widths"][1]["ExpansionTraceWidth"] = 0.3556
    elif case == "duplicate_layer":
        cls["named_master_widths"][1]["layer"] = 1
    else:
        after["geometry"]["stackup"][0]["name"] = "SIGNAL_1_truncated_or_other"
    with pytest.raises(ValueError):
        rules.compare_consumed(baseline, after)


@pytest.mark.parametrize("case", ["named_unit", "minimum_unit", "source", "tie_source"])
def test_actual_units_and_sources_cannot_be_replaced_by_display_fallback(baseline, case):
    after = changed(baseline)
    if case == "named_unit":
        after["widths"]["classes"][0]["named_master_widths"][0]["unit"] = "mm"
    elif case == "minimum_unit":
        after["widths"]["named_minimum_observation"]["unit"] = "th"
    elif case == "source":
        after["raw_model"]["layout_observation"]["layer_count"]["source"] = "renderer_fallback"
    else:
        after["ties"]["source"] = "PlaneShape hidden getter"
    with pytest.raises(ValueError):
        rules.compare_consumed(baseline, after)


@pytest.mark.parametrize(
    "field,layer,key",
    [
        ("named_master_widths", 0, "MinTraceWidth"),
        ("named_master_widths", 0, "TypicalTraceWidth"),
        ("named_master_widths", 1, "ExpansionTraceWidth"),
        ("named_minimum_widths", 2, "TypicalTraceWidth"),
    ],
)
def test_other_ct_scheme_or_layer_change_is_not_ignored(baseline, field, layer, key):
    after = changed(baseline)
    after["widths"]["classes"][0][field][layer][key] = 0.2794
    with pytest.raises(ValueError, match="protected_fields"):
        rules.compare_consumed(baseline, after)


@pytest.mark.parametrize(
    "case", ["component", "net", "raw_model", "geometry", "plane", "tie", "unknown_field"]
)
def test_all_other_observed_material_objects_and_unknown_fields_remain_protected(baseline, case):
    after = changed(baseline)
    if case == "component":
        after["widths"]["components"][0]["uid"] = "wrong"
    elif case == "net":
        after["widths"]["nets"][0]["name"] = "wrong"
    elif case == "raw_model":
        after["raw_model"]["pads"][0]["net"] = "QA_RETURN"
    elif case == "geometry":
        after["geometry"]["components"][0]["x"] = 20.00000000001
        after["planes"]["geometry"] = deepcopy(after["geometry"])
    elif case == "plane":
        after["planes"]["plane_state"]["generated"][0]["uid"] = "other"
    elif case == "tie":
        after["ties"]["collection"]["items"][0]["net"] = "QA_SIGNAL"
    else:
        after["new_unreviewed_field"] = "must not be ignored"
    with pytest.raises(ValueError, match="protected_fields"):
        rules.compare_consumed(baseline, after)


def test_save_and_reopen_compare_every_field_against_consumed_state(baseline):
    consumed = changed(baseline, minimum=0.3556)
    assert (
        rules.compare_persisted(consumed, deepcopy(consumed))["persistence_fingerprint_equal"]
        is True
    )
    drifted = deepcopy(consumed)
    drifted["widths"]["classes"][0]["named_minimum_widths"][0]["ExpansionTraceWidth"] = 0.3048
    with pytest.raises(ValueError, match="fingerprint.persisted"):
        rules.compare_persisted(consumed, drifted)
