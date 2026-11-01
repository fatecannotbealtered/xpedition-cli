"""Exact finite Layout checks for one external CT183 diagnostic experiment.

These functions consume observed fingerprints only. They do not select a COM
object, authorize writes, read files, resolve effective rules, or prove unknown
impedance/thermal semantics. Every field outside two uniquely located named
observations remains protected, including partial-read and source metadata.
"""

from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal

MM_SOURCE = "explicit Layout epcbUnitMM4"
TIE_SOURCE = "IMGCPCBDocument.TieLegs DISPID154 PROPERTYGET(0,0)"
BASE_MM, TARGET_MM = Decimal("0.3048"), Decimal("0.3556")
FIELDS = {183: "ExpansionTraceWidth", 283: "MinTraceWidth", 439: "TypicalTraceWidth"}
SCHEMES = {"(Master)": "named_master_widths", "(Minimum)": "named_minimum_widths"}


def _require(condition, path, message):
    if not condition:
        raise ValueError(path + ": " + message)


def _dict(value, path):
    _require(isinstance(value, dict), path, "required observed object missing")
    return value


def _rows(value, path):
    _require(isinstance(value, list), path, "required observed collection missing")
    for index, row in enumerate(value):
        _dict(row, f"{path}[{index}]")
    return value


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _number(value, path):
    _require(type(value) in (int, float), path, "actual numeric Layout value required")
    result = Decimal(str(value))
    _require(result.is_finite() and result > 0, path, "finite positive value required")
    return result


def _snapshot(fp, master_allowed, minimum_allowed):
    root = _dict(fp, "fingerprint")
    for key in ("widths", "raw_model", "geometry", "planes", "ties"):
        _dict(root.get(key), key)
    _require("limits" in root, "limits", "declared read boundary missing")
    _canonical(root)
    widths, raw, geometry = root["widths"], root["raw_model"], root["geometry"]
    _require(widths.get("complete") is True, "widths.complete", "named read incomplete")
    _require(
        type(widths.get("layer_count")) is int and widths["layer_count"] == 4,
        "widths.layer_count",
        "this experiment requires four actually observed signal layers",
    )
    _require(
        type(raw.get("layers")) is int and raw["layers"] == 4,
        "raw_model.layers",
        "actual layer count conflicts with named observation",
    )
    observation = _dict(raw.get("layout_observation"), "raw_model.layout_observation")
    _require(
        observation.get("units") == "mm",
        "raw_model.layout_observation.units",
        "actual unit differs",
    )
    layer_observation = _dict(
        observation.get("layer_count"), "raw_model.layout_observation.layer_count"
    )
    _require(
        layer_observation.get("observed") is True
        and type(layer_observation.get("value")) is int
        and layer_observation["value"] == 4
        and layer_observation.get("source") == "native_document.LayerCount",
        "raw_model.layout_observation.layer_count",
        "native layer count/source not established",
    )
    stackup = _rows(geometry.get("stackup"), "geometry.stackup")
    signals = [row for row in stackup if row.get("kind") == "conductor"]
    _require(len(signals) == 4, "geometry.stackup", "four conductor layer identities required")
    indices, names = set(), set()
    for row in signals:
        index, name = row.get("layer"), row.get("name")
        _require(
            type(index) is int and index in range(1, 5),
            "geometry.stackup.layer",
            "invalid signal index",
        )
        _require(
            isinstance(name, str) and bool(name),
            "geometry.stackup.name",
            "missing full signal name",
        )
        _require(
            index not in indices and name not in names,
            "geometry.stackup",
            "ambiguous signal layer identity",
        )
        indices.add(index)
        names.add(name)
    _require(
        any(row["layer"] == 1 and row["name"] == "SIGNAL_1" for row in signals),
        "geometry.stackup",
        "SIGNAL_1 does not uniquely map to observed layer 1",
    )
    for index, component in enumerate(_rows(geometry.get("components"), "geometry.components")):
        _require(
            component.get("unit") == "mm",
            f"geometry.components[{index}].unit",
            "actual unit differs",
        )
    _require(
        _canonical(root["planes"].get("geometry")) == _canonical(geometry),
        "planes.geometry",
        "independent geometry observations disagree",
    )
    _require(
        root["ties"].get("units") == "mm" and root["ties"].get("source") == TIE_SOURCE,
        "ties",
        "supported observed source/unit not established",
    )
    classes = _rows(widths.get("classes"), "widths.classes")
    _require(
        len(classes) == 1 and classes[0].get("name") == "(Default)",
        "widths.classes",
        "unique actual (Default) class required for this finite experiment",
    )
    minimum = _dict(widths.get("named_minimum_observation"), "widths.named_minimum_observation")
    _require(
        minimum.get("complete") is True
        and minimum.get("scheme") == "(Minimum)"
        and minimum.get("unit") == MM_SOURCE
        and type(minimum.get("class_count")) is int
        and minimum["class_count"] == 1
        and type(minimum.get("layer_count")) is int
        and minimum["layer_count"] == 4,
        "widths.named_minimum_observation",
        "secondary named observation/source is incomplete or conflicting",
    )
    targets = {}
    for scheme, field in SCHEMES.items():
        path = "widths.classes[0]." + field
        rows = _rows(classes[0].get(field), path)
        _require(len(rows) == 4, path, "exactly four named layer observations required")
        seen = set()
        for index, row in enumerate(rows):
            row_path = f"{path}[{index}]"
            layer = row.get("layer")
            _require(
                type(layer) is int and layer in range(1, 5) and layer not in seen,
                row_path + ".layer",
                "missing or ambiguous actual layer identity",
            )
            seen.add(layer)
            _require(
                row.get("scheme") == scheme and row.get("unit") == MM_SOURCE,
                row_path,
                "named scheme or explicit Layout unit/source differs",
            )
            for width_field in FIELDS.values():
                _number(row.get(width_field), row_path + "." + width_field)
            if layer == 1:
                targets[scheme] = (field, index, row)
        allowed = master_allowed if scheme == "(Master)" else minimum_allowed
        _require(
            _number(targets[scheme][2]["ExpansionTraceWidth"], path) in allowed,
            path + ".SIGNAL_1.ExpansionTraceWidth",
            "value is outside the exact declared discrete outcomes",
        )
    return targets


def validate_baseline(fp):
    """Require the actual independent 12th/12th baseline; do not infer overrides."""
    _snapshot(fp, {BASE_MM}, {BASE_MM})
    return {
        "baseline_valid": True,
        "named_widths": 24,
        "class": "(Default)",
        "layer": "SIGNAL_1",
        "layer_index": 1,
        "constraint_type": 183,
        "Layout_field": "ExpansionTraceWidth",
        "unit_source": MM_SOURCE,
        "baseline_mm": "0.3048",
        "baseline_th": "12",
    }


def compare_consumed(before, after):
    """Permit exactly Master CT183=14th and Minimum CT183 in {12th,14th}."""
    validate_baseline(before)
    targets = _snapshot(after, {TARGET_MM}, {BASE_MM, TARGET_MM})
    expected = deepcopy(before)
    before_targets = _snapshot(expected, {BASE_MM}, {BASE_MM})
    for scheme, (_, _, actual_row) in targets.items():
        before_targets[scheme][2]["ExpansionTraceWidth"] = actual_row["ExpansionTraceWidth"]
    _require(
        _canonical(expected) == _canonical(after),
        "fingerprint.protected_fields",
        "a field outside the two declared named CT183 observations changed",
    )
    minimum = targets["(Minimum)"][2]["ExpansionTraceWidth"]
    return {
        "consumed": True,
        "master_mm": "0.3556",
        "master_th": "14",
        "minimum_mm": str(Decimal(str(minimum))),
        "protected_named_widths": 22,
        "allowed_observations": 2,
        "full_remaining_fingerprint_equal": True,
        "effective_rules_or_impedance_proven": False,
        "complete_thermal_material_proven": False,
    }


def compare_persisted(consumed, observed):
    """Require exact full equality after Save or reopen, including both widths."""
    _snapshot(consumed, {TARGET_MM}, {BASE_MM, TARGET_MM})
    _snapshot(observed, {TARGET_MM}, {BASE_MM, TARGET_MM})
    _require(
        _canonical(consumed) == _canonical(observed),
        "fingerprint.persisted",
        "full independent fingerprint differs from the consumed state",
    )
    return {
        "persistence_fingerprint_equal": True,
        "full_fingerprint_compared": True,
        "effective_rules_or_impedance_proven": False,
        "complete_thermal_material_proven": False,
    }
