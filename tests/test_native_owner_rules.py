"""Synthetic fingerprint checks only; no native rule or Load identity proof."""

from copy import deepcopy
from decimal import Decimal

import pytest

from scripts.native_acceptance.owner_rules import compare_loaded


def fingerprints():
    layout = {
        "complete": True,
        "layer_count": 4,
        "named_minimum_observation": {"complete": True},
        "class_membership_complete": False,
        "class_membership_unreviewed": [{"net": {"name": "(Net0)"}, "reason": "unassigned"}],
        "classes": [{"name": "(Default)", "nets": [{"name": "QA_RETURN"}, {"name": "QA_SIGNAL"}]}],
        "components": [],
        "nets": [{"name": "(Net0)", "pins": [], "net_class": None}],
    }
    constraints = {
        "complete": True,
        "linear_unit_enum": 1,
        "linear_unit": "th",
        "linear_to_mm": 0.0254,
        "net_count": 3,
        "component_count": 2,
        "net_class_count": 1,
        "layer_count": 9,
        "scheme_count": 2,
        "layers": [],
        "classes": [
            {
                "name": "(Default)",
                "full_name": "(Default)",
                "net_count": 3,
                "nets": [
                    {"name": "QA_RETURN^^^", "object_type": 47},
                    {"name": "QA_RETURN", "object_type": 49},
                    {"name": "QA_SIGNAL", "object_type": 49},
                ],
            }
        ],
        "nets": [],
        "components": [],
        "schemes": [],
        "unreviewed": ["local and child effective rule precedence"],
    }
    for index in range(1, 10):
        constraints["layers"].append(
            {
                "Index": index,
                "MetallicIndex": index // 2 if index % 2 == 0 else 0,
                "name": f"SIGNAL_{index // 2}" if index % 2 == 0 else f"DIELECTRIC_{index}",
            }
        )
    for name, field in (("(Master)", "named_master_widths"), ("(Minimum)", "named_minimum_widths")):
        scheme_class = {"full_name": "(Default)", "layer_count": 4, "layers": []}
        constraints["schemes"].append(
            {"name": name, "net_class_count": 1, "classes": [scheme_class]}
        )
        layout["classes"][0][field] = []
        for layer in range(1, 5):
            selected = [
                {
                    "constraint_type": kind,
                    "raw_value": 12.0 if kind == 183 and layer == 1 else 10.0,
                    "raw_unit_enum": 1,
                    "raw_unit": "th",
                    "numeric_conversion_complete": True,
                    "value_mm": 0.30479999999999996 if kind == 183 and layer == 1 else 0.254,
                }
                for kind in (183, 283, 439)
            ]
            scheme_class["layers"].append(
                {
                    "name": f"SIGNAL_{layer}",
                    "constraint_count": 4,
                    "constraint_type_inventory": [{"constraint_type": 144}] + deepcopy(selected),
                    "selected_widths": selected,
                }
            )
            layout["classes"][0][field].append(
                {
                    "layer": layer,
                    "scheme": name,
                    "unit": "explicit Layout epcbUnitMM4",
                    "ExpansionTraceWidth": 0.3048 if layer == 1 else 0.254,
                    "MinTraceWidth": 0.254,
                    "TypicalTraceWidth": 0.254,
                }
            )
    for target in ("QA_RETURN^^^", "QA_RETURN", "QA_SIGNAL"):
        constraints["nets"].append(
            {
                "name": target,
                "object_type": 47 if target.endswith("^^^") else 49,
                "net_class": {"name": "(Default)", "full_name": "(Default)"},
            }
        )
    for component in ("J1", "R1"):
        lpins, cpins = [], []
        for pin, net in (("1", "QA_SIGNAL"), ("2", "QA_RETURN")):
            lpins.append(
                {
                    "name": pin,
                    "uid": f"{component}/{pin}",
                    "component": {"name": component},
                    "net": {"name": net},
                }
            )
            cpins.append(
                {
                    "name": f"{component}-{pin}",
                    "part_pin": {"name": pin},
                    "parent": {"name": component},
                    "net": {"name": net},
                }
            )
        layout["components"].append({"name": component, "pins": lpins})
        constraints["components"].append({"name": component, "pin_count": 2, "pins": cpins})
    for target, number in (("QA_RETURN", "2"), ("QA_SIGNAL", "1")):
        layout["nets"].append(
            {
                "name": target,
                "net_class": {"name": "(Default)"},
                "net_class_observation": {"complete": True, "matches": [{"name": "(Default)"}]},
                "pins": [
                    {
                        "name": number,
                        "uid": f"{component}/{number}",
                        "component": {"name": component},
                    }
                    for component in ("J1", "R1")
                ],
            }
        )
    return layout, constraints


def test_finite_consistency_maps_four_metallic_layers_in_nine_stackup_rows():
    layout, constraints = fingerprints()
    result = compare_loaded(layout, constraints)
    assert result["consistency_pass"]
    assert result["checks"]["layout_metallic_layer_count"] == 4
    assert result["checks"]["constraints_total_stackup_count"] == 9
    assert len(result["checks"]["named_widths"]) == 24
    assert result["limits"]["layout_class_membership_complete"] is False
    assert result["limits"]["layout_net0"] == [layout["nets"][0]]
    assert result["limits"]["finite_effective_rules_proven"] is False
    assert result["limits"]["new_Load_target_binding_proven"] is False
    assert result["limits"]["production_can_apply"] is False


@pytest.mark.parametrize(
    "unit,name,factor",
    [
        (0, "in", "25.4"),
        (1, "th", "0.0254"),
        (2, "mm", "1"),
        (3, "um", "0.001"),
        (4, "nm", "0.000001"),
    ],
)
def test_actual_settings_unit_converts_raw_values_exactly_without_cached_float(unit, name, factor):
    layout, constraints = fingerprints()
    constraints.update(linear_unit_enum=unit, linear_unit=name, linear_to_mm=float(factor))
    for scheme in constraints["schemes"]:
        for layer in scheme["classes"][0]["layers"]:
            for collection in ("constraint_type_inventory", "selected_widths"):
                for row in layer[collection]:
                    if row["constraint_type"] in (183, 283, 439):
                        mm = (
                            Decimal("0.3048")
                            if row["constraint_type"] == 183 and layer["name"] == "SIGNAL_1"
                            else Decimal("0.254")
                        )
                        row.update(
                            raw_value=str(mm / Decimal(factor)), raw_unit_enum=unit, raw_unit=name
                        )
    # In-inch 0.254mm/25.4 and 0.3048mm/25.4 are terminating Decimals.
    assert compare_loaded(layout, constraints)["consistency_pass"]


def test_wrong_rule_value_is_rejected_despite_matching_names_and_cached_mm():
    layout, constraints = fingerprints()
    rule = constraints["schemes"][0]["classes"][0]["layers"][0]
    rule["selected_widths"][0]["raw_value"] = 14
    rule["constraint_type_inventory"][1]["raw_value"] = 14
    with pytest.raises(ValueError, match=r"CT183: named width differs"):
        compare_loaded(layout, constraints)


def test_decimal_conversion_does_not_round_away_an_observed_difference():
    layout, constraints = fingerprints()
    rule = constraints["schemes"][0]["classes"][0]["layers"][0]
    changed = "12.000000000000000000000000000000000001"
    rule["selected_widths"][0]["raw_value"] = changed
    rule["constraint_type_inventory"][1]["raw_value"] = changed
    with pytest.raises(ValueError, match=r"CT183: named width differs"):
        compare_loaded(layout, constraints)


@pytest.mark.parametrize(
    "field,value",
    [
        ("linear_unit_enum", True),
        ("linear_unit_enum", 5),
        ("linear_unit", "mm"),
        ("linear_to_mm", 1),
    ],
)
def test_invalid_or_conflicting_actual_units_are_rejected(field, value):
    layout, constraints = fingerprints()
    constraints[field] = value
    with pytest.raises(ValueError, match=r"constraints.linear_"):
        compare_loaded(layout, constraints)


def test_rule_layer_name_must_map_to_metallic_index_not_collection_position():
    layout, constraints = fingerprints()
    constraints["schemes"][0]["classes"][0]["layers"][0]["name"] = "DIELECTRIC_1"
    with pytest.raises(ValueError, match="rule-layer names"):
        compare_loaded(layout, constraints)


@pytest.mark.parametrize("kind", ["metallic_index", "class", "selected_type", "inventory_type"])
def test_duplicate_identity_or_constraint_type_is_rejected(kind):
    layout, constraints = fingerprints()
    rule = constraints["schemes"][0]["classes"][0]["layers"][0]
    if kind == "metallic_index":
        constraints["layers"][3]["MetallicIndex"] = 1
    elif kind == "class":
        layout["classes"].append(deepcopy(layout["classes"][0]))
    elif kind == "selected_type":
        rule["selected_widths"].append(deepcopy(rule["selected_widths"][0]))
    else:
        rule["constraint_type_inventory"][-1] = deepcopy(rule["constraint_type_inventory"][1])
    with pytest.raises(ValueError, match="duplicate"):
        compare_loaded(layout, constraints)


@pytest.mark.parametrize(
    "missing", ["width", "layer", "scheme", "target", "count", "minimum_complete"]
)
def test_missing_observation_never_means_empty_or_matching(missing):
    layout, constraints = fingerprints()
    if missing == "width":
        constraints["schemes"][0]["classes"][0]["layers"][0]["selected_widths"].pop()
    elif missing == "layer":
        layout["classes"][0]["named_master_widths"].pop()
    elif missing == "scheme":
        constraints["schemes"].pop()
        constraints["scheme_count"] = 1
    elif missing == "target":
        constraints["nets"].pop()
        constraints["net_count"] = 2
    elif missing == "count":
        constraints.pop("layer_count")
    else:
        layout["named_minimum_observation"]["complete"] = False
    with pytest.raises(ValueError):
        compare_loaded(layout, constraints)


@pytest.mark.parametrize(
    "fault",
    ["wrong_parent", "wrong_pin", "wrong_net", "net_component_disagree", "wrong_net_pin_uid"],
)
def test_target_component_pin_net_ownership_is_checked_both_directions(fault):
    layout, constraints = fingerprints()
    pin = constraints["components"][0]["pins"][0]
    if fault == "wrong_parent":
        pin["parent"]["name"] = "R1"
    elif fault == "wrong_pin":
        pin["part_pin"]["name"] = "wrong-contact"
    elif fault == "wrong_net":
        pin["net"]["name"] = "QA_RETURN"
    elif fault == "net_component_disagree":
        layout["nets"][2]["pins"][0]["name"] = "2"
    else:
        layout["nets"][2]["pins"][0]["uid"] = "wrong-entity"
    with pytest.raises(ValueError):
        compare_loaded(layout, constraints)


def test_target_membership_incomplete_is_rejected_without_erasing_net0_boundary():
    layout, constraints = fingerprints()
    layout["nets"][1]["net_class_observation"]["complete"] = False
    with pytest.raises(ValueError, match="target Layout class membership is incomplete"):
        compare_loaded(layout, constraints)


def test_selected_constraint_must_agree_with_complete_inventory_and_actual_unit():
    layout, constraints = fingerprints()
    constraints["schemes"][0]["classes"][0]["layers"][0]["selected_widths"][0]["raw_value"] = 14
    with pytest.raises(ValueError, match="conflicts with inventory"):
        compare_loaded(layout, constraints)
    layout, constraints = fingerprints()
    constraints["schemes"][0]["classes"][0]["layers"][0]["selected_widths"][0]["raw_unit_enum"] = 2
    with pytest.raises(ValueError, match="raw unit differs"):
        compare_loaded(layout, constraints)
