"""Pure, finite cross-read checks for the bounded native-owner experiment.

Inputs are the existing explicit-document Layout fingerprint and the formal
ConstraintsAuto fingerprint. This module does not read COM, files or a database.
Consistency of selected fields is not unique Load-target identity, effective
local rules, full class membership, conductor protection or production approval.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from decimal import Decimal, DecimalException, InvalidOperation, localcontext
from typing import Any

_UNITS = {
    0: ("in", Decimal("25.4")),
    1: ("th", Decimal("0.0254")),
    2: ("mm", Decimal("1")),
    3: ("um", Decimal("0.001")),
    4: ("nm", Decimal("0.000001")),
}
_WIDTHS = {183: "ExpansionTraceWidth", 283: "MinTraceWidth", 439: "TypicalTraceWidth"}
_SCHEMES = {"(Master)": "named_master_widths", "(Minimum)": "named_minimum_widths"}


def _require(condition: bool, path: str, message: str) -> None:
    if not condition:
        raise ValueError(f"{path}: {message}")


def _mapping(value: Any, path: str) -> Mapping:
    _require(isinstance(value, Mapping), path, "required object is missing or invalid")
    return value


def _rows(value: Any, path: str) -> list[Mapping]:
    _require(isinstance(value, list), path, "required observed collection is missing or invalid")
    for index, row in enumerate(value):
        _mapping(row, f"{path}[{index}]")
    return value


def _text(value: Any, path: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()), path, "required name is missing")
    return value


def _integer(value: Any, path: str, minimum: int = 0) -> int:
    _require(
        type(value) is int and value >= minimum, path, "required integer is missing or invalid"
    )
    return value


def _decimal(value: Any, path: str) -> Decimal:
    _require(type(value) in (int, float, str, Decimal), path, "required numeric scalar is missing")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{path}: invalid Decimal scalar") from exc
    _require(result.is_finite() and result > 0, path, "required finite positive scalar is invalid")
    return result


def _unique(rows: list[Mapping], key: str, path: str, *, numeric: bool = False) -> dict:
    result = {}
    for index, row in enumerate(rows):
        value = row.get(key)
        if numeric:
            _integer(value, f"{path}[{index}].{key}")
        else:
            _text(value, f"{path}[{index}].{key}")
        _require(value not in result, path, f"duplicate {key}={value!s}")
        result[value] = row
    return result


def _count(record: Mapping, key: str, rows: list, path: str) -> None:
    observed = _integer(record.get(key), f"{path}.{key}")
    _require(
        observed == len(rows), f"{path}.{key}", "observed count differs from collection length"
    )


def _name(record: Any, path: str) -> str:
    return _text(_mapping(record, path).get("name"), f"{path}.name")


def compare_loaded(
    layout_fingerprint: Mapping,
    constraints_fingerprint: Mapping,
    required_nets: Sequence[str] = ("QA_RETURN", "QA_SIGNAL"),
) -> dict[str, Any]:
    """Check all selected named widths and the requested nets' pin ownership.

    Raises a field-qualified ValueError on missing, duplicate or conflicting
    observations. Uses exact Decimal conversion of raw values and actual units;
    it never treats the reader's float ``value_mm`` cache as primary evidence.
    Stackup dielectric rows remain in total counts, while positive MetallicIndex
    maps the distinct Layout signal-layer indices to Constraints rule names.
    """
    layout = _mapping(layout_fingerprint, "layout")
    constraints = _mapping(constraints_fingerprint, "constraints")
    _require(layout.get("complete") is True, "layout.complete", "declared read is incomplete")
    _require(
        constraints.get("complete") is True, "constraints.complete", "declared read is incomplete"
    )
    minimum_observation = _mapping(layout.get("named_minimum_observation"), "layout.minimum")
    _require(
        minimum_observation.get("complete") is True,
        "layout.named_minimum_observation.complete",
        "named Minimum read is incomplete",
    )
    _require(
        not isinstance(required_nets, str) and bool(required_nets),
        "required_nets",
        "nonempty sequence of explicit target names is required",
    )
    targets = [_text(name, "required_nets") for name in required_nets]
    _require(len(set(targets)) == len(targets), "required_nets", "duplicate target network")
    unit = _integer(constraints.get("linear_unit_enum"), "constraints.linear_unit_enum")
    _require(unit in _UNITS, "constraints.linear_unit_enum", "unsupported actual unit enumeration")
    unit_name, factor = _UNITS[unit]
    _require(
        constraints.get("linear_unit") == unit_name,
        "constraints.linear_unit",
        "unit name conflicts with enumeration",
    )
    _require(
        _decimal(constraints.get("linear_to_mm"), "constraints.linear_to_mm") == factor,
        "constraints.linear_to_mm",
        "unit factor conflicts with enumeration",
    )
    layer_count = _integer(layout.get("layer_count"), "layout.layer_count", 1)
    layers = _rows(constraints.get("layers"), "constraints.layers")
    _count(constraints, "layer_count", layers, "constraints")
    _unique(layers, "name", "constraints.layers")
    _unique(layers, "Index", "constraints.layers", numeric=True)
    metallic = {}
    for row in layers:
        index = _integer(
            row.get("MetallicIndex"), f"constraints.layers.{row['name']}.MetallicIndex"
        )
        if index:
            _require(
                index not in metallic, "constraints.layers", f"duplicate MetallicIndex={index}"
            )
            metallic[index] = row["name"]
    _require(
        set(metallic) == set(range(1, layer_count + 1)),
        "constraints.layers.MetallicIndex",
        "positive metallic indices do not match Layout layers",
    )
    layout_classes_rows = _rows(layout.get("classes"), "layout.classes")
    layout_classes = _unique(layout_classes_rows, "name", "layout.classes")
    _require("(Default)" in layout_classes, "layout.classes", "required (Default) class is absent")
    layout_default = layout_classes["(Default)"]
    class_rows = _rows(constraints.get("classes"), "constraints.classes")
    _count(constraints, "net_class_count", class_rows, "constraints")
    classes = _unique(class_rows, "full_name", "constraints.classes")
    _unique(class_rows, "name", "constraints.classes")
    _require("(Default)" in classes, "constraints.classes", "required (Default) class is absent")
    schemes_rows = _rows(constraints.get("schemes"), "constraints.schemes")
    _count(constraints, "scheme_count", schemes_rows, "constraints")
    schemes = _unique(schemes_rows, "name", "constraints.schemes")
    width_checks = []
    for scheme_name, layout_field in _SCHEMES.items():
        path = f"constraints.schemes.{scheme_name}"
        _require(scheme_name in schemes, path, "required named scheme is absent")
        scheme = schemes[scheme_name]
        scheme_classes = _rows(scheme.get("classes"), f"{path}.classes")
        _count(scheme, "net_class_count", scheme_classes, path)
        by_class = _unique(scheme_classes, "full_name", f"{path}.classes")
        _require("(Default)" in by_class, path, "required (Default) class is absent")
        scheme_class = by_class["(Default)"]
        rule_layers = _rows(scheme_class.get("layers"), f"{path}.(Default).layers")
        _count(scheme_class, "layer_count", rule_layers, f"{path}.(Default)")
        rule_names = _unique(rule_layers, "name", f"{path}.(Default).layers")
        _require(
            set(rule_names) == set(metallic.values()),
            f"{path}.(Default).layers",
            "rule-layer names do not match metallic stackup names",
        )
        layout_widths = _rows(layout_default.get(layout_field), f"layout.(Default).{layout_field}")
        layout_by_index = _unique(layout_widths, "layer", f"layout.{layout_field}", numeric=True)
        _require(
            set(layout_by_index) == set(metallic),
            f"layout.{layout_field}",
            "missing or extra Layout metallic layer index",
        )
        for index, name in sorted(metallic.items()):
            rule = rule_names[name]
            rule_path = f"{path}.(Default).{name}"
            inventory_rows = _rows(rule.get("constraint_type_inventory"), f"{rule_path}.inventory")
            _count(rule, "constraint_count", inventory_rows, rule_path)
            inventory = _unique(
                inventory_rows, "constraint_type", f"{rule_path}.inventory", numeric=True
            )
            widths = _unique(
                _rows(rule.get("selected_widths"), f"{rule_path}.selected_widths"),
                "constraint_type",
                f"{rule_path}.selected_widths",
                numeric=True,
            )
            _require(
                set(widths) == set(_WIDTHS),
                rule_path,
                "selected width types must be exactly 183/283/439",
            )
            observed_layout = layout_by_index[index]
            _require(
                observed_layout.get("scheme") == scheme_name,
                f"layout.{layout_field}.{index}.scheme",
                "wrong named scheme",
            )
            _require(
                observed_layout.get("unit") == "explicit Layout epcbUnitMM4",
                f"layout.{layout_field}.{index}.unit",
                "Layout value is not explicitly mm",
            )
            for constraint_type, field in _WIDTHS.items():
                value = widths[constraint_type]
                value_path = f"{rule_path}.CT{constraint_type}"
                _require(
                    constraint_type in inventory,
                    value_path,
                    "selected type is absent from full inventory",
                )
                for selected in (value, inventory[constraint_type]):
                    _require(
                        selected.get("numeric_conversion_complete") is True,
                        value_path,
                        "numeric read is incomplete",
                    )
                    _require(
                        type(selected.get("raw_unit_enum")) is int
                        and selected["raw_unit_enum"] == unit,
                        value_path,
                        "raw unit differs from actual Settings unit",
                    )
                    _require(
                        selected.get("raw_unit") == unit_name,
                        value_path,
                        "raw unit name differs from actual Settings unit",
                    )
                raw = _decimal(value.get("raw_value"), f"{value_path}.raw_value")
                _require(
                    raw == _decimal(inventory[constraint_type].get("raw_value"), value_path),
                    value_path,
                    "selected value conflicts with inventory",
                )
                # Decimal's default context can round a long observed scalar.
                # Preserve its full coefficient so exact equality never becomes
                # an implicit numeric tolerance.
                try:
                    with localcontext() as context:
                        context.prec = max(
                            28, len(raw.as_tuple().digits) + len(factor.as_tuple().digits)
                        )
                        actual_mm = raw * factor
                except DecimalException as exc:
                    raise ValueError(f"{value_path}: exact unit conversion failed") from exc
                layout_mm = _decimal(
                    observed_layout.get(field), f"layout.{layout_field}.{index}.{field}"
                )
                _require(
                    actual_mm == layout_mm,
                    value_path,
                    f"named width differs: Constraints {actual_mm} mm, Layout {layout_mm} mm",
                )
                width_checks.append(
                    {
                        "scheme": scheme_name,
                        "class_full_name": "(Default)",
                        "layer_name": name,
                        "metallic_index": index,
                        "constraint_type": constraint_type,
                        "layout_field": field,
                        "raw_value_decimal": str(raw),
                        "actual_unit_enum": unit,
                        "actual_unit": unit_name,
                        "factor_mm_decimal": str(factor),
                        "constraints_mm_decimal": str(actual_mm),
                        "layout_mm_decimal": str(layout_mm),
                        "exact_decimal_equal": True,
                    }
                )

    layout_net_rows = _rows(layout.get("nets"), "layout.nets")
    layout_nets = _unique(layout_net_rows, "name", "layout.nets")
    net_rows = _rows(constraints.get("nets"), "constraints.nets")
    _count(constraints, "net_count", net_rows, "constraints")
    cma_nets = _unique(net_rows, "name", "constraints.nets")
    layout_component_rows = _rows(layout.get("components"), "layout.components")
    _unique(layout_component_rows, "name", "layout.components")
    component_rows = _rows(constraints.get("components"), "constraints.components")
    _count(constraints, "component_count", component_rows, "constraints")
    _unique(component_rows, "name", "constraints.components")
    layout_pins, cma_pins = {}, {}
    for source, components, destination in (
        ("layout", layout_component_rows, layout_pins),
        ("constraints", component_rows, cma_pins),
    ):
        for component in components:
            component_name = component["name"]
            path = f"{source}.components.{component_name}"
            pins = _rows(component.get("pins"), f"{path}.pins")
            _unique(pins, "name", f"{path}.pins")
            if source == "constraints":
                _count(component, "pin_count", pins, path)
            for pin in pins:
                pin_path = f"{path}.pins.{pin['name']}"
                parent = _name(
                    pin.get("parent" if source == "constraints" else "component"), pin_path
                )
                _require(
                    parent == component_name,
                    pin_path,
                    "pin parent differs from containing component",
                )
                pin_name = (
                    _name(pin.get("part_pin"), f"{pin_path}.part_pin")
                    if source == "constraints"
                    else pin["name"]
                )
                key = (component_name, pin_name)
                _require(key not in destination, pin_path, "duplicate component/part-pin identity")
                destination[key] = {"net": _name(pin.get("net"), f"{pin_path}.net"), "row": pin}
    target_checks = []
    for target in targets:
        path = f"target.{target}"
        _require(
            target in layout_nets and target in cma_nets, path, "target net missing from one read"
        )
        _require(
            cma_nets[target].get("object_type") == 49,
            path,
            "target is not the observed physical-net object type",
        )
        observation = _mapping(
            layout_nets[target].get("net_class_observation"), f"{path}.layout_membership"
        )
        _require(
            observation.get("complete") is True,
            path,
            "target Layout class membership is incomplete",
        )
        matches = _rows(observation.get("matches"), f"{path}.layout_membership.matches")
        _require(
            len(matches) == 1 and matches[0].get("name") == "(Default)",
            path,
            "target Layout class membership is not uniquely Default",
        )
        for source, nets, class_key in (
            ("layout", layout_nets, "name"),
            ("constraints", cma_nets, "full_name"),
        ):
            net_class = _mapping(nets[target].get("net_class"), f"{path}.{source}.net_class")
            _require(
                net_class.get(class_key) == "(Default)", path, f"{source} target net class differs"
            )
        for source, class_map in (("layout", layout_classes), ("constraints", classes)):
            owners = []
            for class_name, cls in class_map.items():
                members = _rows(cls.get("nets"), f"{source}.classes.{class_name}.nets")
                if source == "constraints":
                    _count(cls, "net_count", members, f"constraints.classes.{class_name}")
                names = _unique(members, "name", f"{source}.classes.{class_name}.nets")
                if target in names:
                    owners.append(class_name)
                    if source == "constraints":
                        _require(
                            names[target].get("object_type") == 49,
                            path,
                            "class member is not physical net type49",
                        )
            _require(
                owners == ["(Default)"],
                path,
                f"{source} target is absent or multiply assigned to classes",
            )
        expected_pins = {key for key, value in layout_pins.items() if value["net"] == target}
        actual_pins = {key for key, value in cma_pins.items() if value["net"] == target}
        _require(
            bool(expected_pins) and expected_pins == actual_pins,
            path,
            "target component/part-pin/net ownership differs or is empty",
        )
        net_pins = _rows(layout_nets[target].get("pins"), f"{path}.layout.net_pins")
        net_pin_keys = []
        for pin in net_pins:
            key = (
                _name(pin.get("component"), f"{path}.net_pin.component"),
                _text(pin.get("name"), f"{path}.net_pin.name"),
            )
            net_pin_keys.append(key)
            _require(key in layout_pins, path, "net pin has no matching component pin")
            _require(
                pin.get("uid") and pin.get("uid") == layout_pins[key]["row"].get("uid"),
                path,
                "Layout net-pin UID differs from component pin UID",
            )
        _require(len(set(net_pin_keys)) == len(net_pin_keys), path, "duplicate Layout net pin")
        _require(
            set(net_pin_keys) == expected_pins,
            path,
            "Layout net and component pin ownership disagree",
        )
        target_checks.append(
            {
                "name": target,
                "class_full_name": "(Default)",
                "component_part_pins": [list(key) for key in sorted(expected_pins)],
                "limited_membership_equal": True,
            }
        )

    return {
        "complete": True,
        "consistency_pass": True,
        "checks": {
            "layout_metallic_layer_count": layer_count,
            "constraints_total_stackup_count": len(layers),
            "metallic_layer_map": [
                {"metallic_index": index, "name": name} for index, name in sorted(metallic.items())
            ],
            "named_widths": width_checks,
            "required_nets": target_checks,
        },
        "limits": {
            "scope": (
                "Default Master/Minimum named widths and requested net "
                "component/part-pin ownership consistency only"
            ),
            "layout_class_membership_complete": deepcopy(layout.get("class_membership_complete")),
            "layout_class_membership_unreviewed": deepcopy(
                layout.get("class_membership_unreviewed")
            ),
            "layout_net0": deepcopy(
                [row for row in layout_net_rows if row.get("name") == "(Net0)"]
            ),
            "constraints_unreviewed": deepcopy(constraints.get("unreviewed")),
            "constraints_persistent_uid_available": False,
            "independent_Layout_layer_names_available": False,
            "new_Load_target_binding_proven": False,
            "finite_effective_rules_proven": False,
            "local_child_precedence_proven": False,
            "all_network_membership_proven": False,
            "production_can_apply": False,
        },
    }
