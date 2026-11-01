"""Expose the existing schematic checks' evidence and their unreviewed scope.

This does not infer font extents or collisions from a native value/position read.
Planned findings stay the planner's findings; native edits expose missing inputs.
"""

from __future__ import annotations

from typing import Any

PLAN_RULES = {
    "DS-07": "planned part/symbol and selected text bounds against the planned usable area",
    "DS-08": "planned part bounding-box spacing, with declared neighbours excluded",
    "DS-15": "planned label/ground symbol coverage of another net's free endpoint",
    "DS-16": "estimated top/bottom pin-name width against the planned pin pitch",
    "DS-17": "estimated text boxes against text, modelled lines and label/frame boxes",
}
EDIT_RULES = ["text_text", "text_symbol", "text_wire", "sheet_boundary", "text_direction"]


def _base(basis: str) -> dict[str, Any]:
    return {
        "status": "partial" if basis == "planned_projection" else "not_checked",
        "basis": basis,
        "units": "sheet_units_10_mil" if basis == "planned_projection" else "not_observed",
        "findings": [],
        "checked_rules": [],
        "unreviewed_objects": [],
        "unreviewed_collections": [],
        "empty_findings_mean_layout_clear": False,
        "manual_full_sheet_review_required": True,
        "native_erc_or_manufacturing_check": False,
        "limitations": [
            "Projection advisories and structural edit validation do not establish readable "
            "native drawing, ERC completion or electrical correctness.",
            "Review the actual full sheet; an empty findings list is not layout acceptance.",
        ],
    }


def planned(plan: Any, chosen: list[int] | None = None) -> dict[str, Any]:
    """Describe the exact existing planning checks, without promoting font guesses."""
    from . import schematic_render

    sheets = set(chosen or [int(sheet["number"]) for sheet in plan.sheets])
    report = _base("planned_projection")
    report["findings"] = [item for item in plan.issues if item.get("sheet", 0) in sheets]
    report["checked_rules"] = [
        {"rule": rule, "basis": basis, "native_measured": False}
        for rule, basis in PLAN_RULES.items()
    ]
    report["text_envelope_basis"] = "estimated_character_width_not_native_font_extents"
    report["object_counts"] = {"sheets": len(sheets), "texts": 0, "lines": 0, "boxes": 0}
    for number in sorted(sheets):
        layout = schematic_render.layout_sheet(plan, number, plan.sheet_size)
        for key in ("texts", "lines", "boxes"):
            report["object_counts"][key] += len(getattr(layout, key))
        report["unreviewed_objects"].append(
            {
                "object": {"kind": "sheet", "sheet": number},
                "reasons": ["native_font_extents_not_observed", "native_actual_graphics_not_read"],
                "affected_rules": ["DS-07", "DS-16", "DS-17"],
            }
        )
    current = None
    sources = {**plan.symbols, **plan.library_symbols}
    for index, op in enumerate(plan.ops):
        if op.get("op") == "open_sheet":
            current = int(op["number"])
        if current not in sheets or op.get("op") not in {"place_part", "place_symbol"}:
            continue
        name = str(op.get("symbol") or "")
        identity = {
            "kind": op["op"],
            "operation_index": index,
            "sheet": current,
            "refdes": op.get("refdes"),
            "library": op.get("library"),
            "symbol": name,
        }
        reasons = []
        omitted = []
        source = sources.get(name)
        if source is None:
            reasons.append(
                "stock_symbol_standin_not_actual_native_geometry"
                if (op.get("library"), name) in {("Globals", "gnd"), ("builtin", "No_Connect")}
                else "symbol_graphics_not_available_to_preview"
            )
        else:
            for line_index, line in enumerate(source.splitlines(), start=1):
                fields = line.split()
                if not fields:
                    continue
                kind = fields[0]
                # These are metadata or non-displayed pin records, not parsed graphics.
                if kind in {"V", "K", "F", "D", "Y", "Z", "i", "P", "E", "Q", "b"}:
                    continue
                if kind == "L" and len(fields) >= 7 and fields[6] == "0":
                    continue
                if kind not in {"l", "U", "A"} or (
                    kind in {"U", "A"} and (len(fields) < 8 or "=" not in line)
                ):
                    omitted.append(
                        {"line": line_index, "record": kind, "reason": "record_not_modelled"}
                    )
                elif kind in {"U", "A"} and fields[4] != "0":
                    omitted.append(
                        {
                            "line": line_index,
                            "record": kind,
                            "reason": "attribute_rotation_not_modelled",
                        }
                    )
                elif kind in {"U", "A"} and fields[5] not in {str(n) for n in range(1, 10)}:
                    omitted.append(
                        {
                            "line": line_index,
                            "record": kind,
                            "reason": "attribute_anchor_uses_display_fallback",
                        }
                    )
            if omitted:
                reasons.append("symbol_records_not_fully_modelled")
        if reasons:
            report["unreviewed_objects"].append(
                {
                    "object": identity,
                    "reasons": reasons,
                    "affected_rules": ["DS-07", "DS-08", "DS-15", "DS-17"],
                    "unmodelled_records": omitted,
                }
            )
    report["unreviewed_collections"] = [
        {
            "collection": "actual_native_sheet_graphics",
            "reason": "plan_only_no_native_drawing_read",
            "enumeration_complete": False,
        }
    ]
    report["limitations"] += [
        "Text boxes use the planner's character-width estimate, not the native font, glyph, "
        "visibility or actual rendered envelope.",
        "The symbol preview parses polylines and attribute records only. Stock ground/NC "
        "marks use stand-ins; unmodelled records are listed by placed object and source line.",
    ]
    return report


def planned_params(params: dict, design: dict, parts: dict, chosen: list[int]) -> dict[str, Any]:
    """Use the already generated build plan; never replan or change its operations."""
    from .schematic_layout import Plan

    summary = params["summary"]
    return planned(
        Plan(
            ops=params["ops"],
            symbols=params["symbols"],
            library_symbols={str(part["symbol"]): str(part["text"]) for part in parts.values()},
            sheets=summary["sheets"],
            issues=summary["issues"],
            usable={int(number): bounds for number, bounds in summary["usable"].items()},
            sheet_size=str(design.get("sheet_size", "B")).upper(),
        ),
        chosen,
    )


def _native_anchor_advisories(design: dict[str, Any], report: dict[str, Any]) -> None:
    """Check one exact native anchor condition, without estimating font extents."""
    from .schematic_attribute_display import DISPLAY_NAMES, canonical_name

    rule = "native/visible-attribute-anchor-coincidence"
    components = design.get("components", [])
    checked = 0
    unreviewed = []
    for component in components:
        owner = {key: component.get(key) for key in ("refdes", "uid", "sheet")}
        records = component.get("attribute_display")
        identity_complete = (
            isinstance(owner["refdes"], str)
            and bool(owner["refdes"])
            and isinstance(owner["uid"], str)
            and bool(owner["uid"])
            and type(owner["sheet"]) is int
            and owner["sheet"] > 0
            and sum(row.get("uid") == owner["uid"] for row in components) == 1
            and (component.get("identity_observation_coverage") or {}).get("complete") is True
            and (component.get("attribute_display_coverage") or {}).get("enumeration_complete")
            is True
        )
        names = (
            [row.get("name") for row in records if isinstance(row, dict)]
            if isinstance(records, list)
            else []
        )
        if (
            not identity_complete
            or not isinstance(records, list)
            or any(not isinstance(name, str) or not name for name in names)
            or len(set(names)) != len(names)
        ):
            unreviewed.append(
                {"owner": owner, "reason": "native_owner_or_attribute_enumeration_incomplete"}
            )
            continue
        groups: dict[tuple, list[dict]] = {}
        for record in records:
            name = record.get("name") if isinstance(record, dict) else None
            display = record.get("display") if isinstance(record, dict) else None
            complete = (
                isinstance(display, dict)
                and record.get("owner") == owner
                and type((record.get("owner") or {}).get("sheet")) is int
                and isinstance(record.get("value"), str)
                and isinstance(record.get("getter_errors"), dict)
                and not record["getter_errors"]
                and canonical_name(name) in {canonical_name(item) for item in DISPLAY_NAMES}
                and isinstance(component.get("attributes"), dict)
                and component["attributes"].get(name) == record["value"]
                and all(
                    type(display.get(key)) is int
                    for key in ("x", "y", "visible", "size", "orientation", "origin")
                )
                and display.get("size", 0) > 0
                and display.get("units") == "sheet_unit"
            )
            if not complete:
                unreviewed.append(
                    {
                        "owner": owner,
                        "attribute": name,
                        "reason": "native_attribute_display_getters_incomplete_"
                        "or_outside_named_scope",
                    }
                )
                continue
            checked += 1
            if display["visible"] != 3 or not record["value"].strip():
                continue
            key = tuple(display[key] for key in ("x", "y", "orientation", "origin"))
            groups.setdefault(key, []).append(record)
        for anchor, attributes in sorted(groups.items()):
            if len({row["value"] for row in attributes}) < 2:
                continue
            report["findings"].append(
                {
                    "rule": rule,
                    "severity": "warning",
                    "sheet": owner["sheet"],
                    "object": {"kind": "component", **owner},
                    "anchor": dict(zip(("x", "y", "orientation", "origin"), anchor, strict=True))
                    | {"units": "sheet_unit"},
                    "attributes": [
                        {
                            "name": row["name"],
                            "value": row["value"],
                            "visible": 3,
                            "size": row["display"]["size"],
                        }
                        for row in sorted(attributes, key=lambda row: row["name"])
                    ],
                    "message": "Different non-empty values share one observed visible "
                    "attribute anchor; review the sheet.",
                    "basis": "exact_native_attribute_anchor_equality_only",
                    "exact_collision_verified": False,
                }
            )
    report["native_anchor_check"] = {
        "rule": rule,
        "fully_observed_attributes": checked,
        "unreviewed_attributes": unreviewed,
        "exact_font_envelopes_observed": False,
    }
    if checked:
        report["status"] = "partial"
        report["checked_rules"].append(
            {
                "rule": rule,
                "basis": "complete named native getters; same owner/position/orientation/"
                "origin, visible3 and different non-empty values only",
                "native_measured": True,
                "exact_collision_verified": False,
            }
        )


def native_edit(design: dict[str, Any], operations: list[dict[str, Any]]) -> dict[str, Any]:
    """Native snapshots contain values/positions/pins, but no drawable text envelopes."""
    report = _base("native_snapshot_structural_fields_only")
    components = {str(item.get("refdes")): item for item in design.get("components", [])}
    observations = [
        {
            "refdes": component.get("refdes"),
            "uid": component.get("uid"),
            "sheet": component.get("sheet"),
            "records": component.get("attribute_display", []),
            "coverage": component.get("attribute_display_coverage", {}),
        }
        for component in components.values()
        if "attribute_display" in component
    ]
    if observations:
        report["attribute_display_observations"] = observations
        report["units"] = "sheet_units_10_mil"
    seen = set()
    for index, op in enumerate(operations):
        refs = {str(op["refdes"])} if op.get("refdes") else set()
        refs.update(str(pin).partition(".")[0] for pin in op.get("pins", []))
        if op.get("pin"):
            refs.add(str(op["pin"]).partition(".")[0])
        if op.get("net"):
            for component in components.values():
                if any(pin.get("net") == op["net"] for pin in component.get("pins", [])):
                    refs.add(str(component["refdes"]))
        identities = [
            {
                "kind": "component",
                "refdes": ref,
                "uid": components.get(ref, {}).get("uid"),
                "sheet": op.get("sheet", components.get(ref, {}).get("sheet")),
                "property": op.get("name") if op["type"] == "set_property" else None,
            }
            for ref in sorted(refs)
        ]
        if op.get("net") or op["type"] in {"create_net", "rename_net"}:
            identities.append({"kind": "net", "name": op.get("net", op.get("name"))})
        for identity in identities:
            key = (index, json_identity(identity))
            if key in seen:
                continue
            seen.add(key)
            report["unreviewed_objects"].append(
                {
                    "object": identity,
                    "operation_index": index,
                    "operation_type": op["type"],
                    "reasons": [
                        "attribute_envelope_not_observed",
                        (
                            "attribute_font_and_complete_envelope_not_observed"
                            if observations
                            else "attribute_position_visibility_font_and_direction_not_observed"
                        ),
                        "symbol_graphics_and_wire_paths_not_observed",
                        "actual_sheet_usable_boundary_not_observed",
                    ],
                    "affected_rules": list(EDIT_RULES),
                }
            )
    report["object_counts"] = {
        "snapshot_components": len(components),
        "affected_unreviewed": len(report["unreviewed_objects"]),
    }
    report["unreviewed_collections"] = [
        {"collection": name, "reason": reason, "enumeration_complete": False}
        for name, reason in [
            (
                "text_graphics",
                "limited_named_attribute_getters_do_not_enumerate_all_text_or_exact_envelopes"
                if observations
                else "snapshot_has_attribute_values_without_text_geometry_or_completeness",
            ),
            ("symbol_graphics", "snapshot_has_instance_positions_without_symbol_graphics"),
            ("wire_paths_and_junctions", "snapshot_has_pin_net_membership_without_drawn_paths"),
            ("sheet_boundaries", "snapshot_sheet_inventory_is_not_a_usable_area_measurement"),
        ]
    ]
    report["limitations"].append(
        "Coordinates, orientation codes, attribute strings and pin-net membership cannot "
        "supply missing native text extents or graphics. Limited named display getters, "
        "when returned, report their actual values and failures; arbitrary free text "
        "and exact font envelopes remain uncovered. No collision, boundary "
        "or direction check was performed; other objects on the affected sheet are also unreviewed."
    )
    _native_anchor_advisories(design, report)
    return report


def json_identity(identity: dict) -> tuple:
    return tuple(sorted(identity.items()))


def native_drawing(design: dict[str, Any], sheet: int | None = None) -> dict[str, Any]:
    """Image preflight: actual read identities, with explicit unmeasured graphics."""
    report = native_edit(design, [])
    if sheet is not None:
        report["findings"] = [row for row in report["findings"] if row.get("sheet") == sheet]
    for component in design.get("components", []):
        # A missing sheet association cannot justify excluding a real object.
        if sheet is not None and component.get("sheet") not in {None, sheet}:
            continue
        report["unreviewed_objects"].append(
            {
                "object": {
                    "kind": "component",
                    "refdes": component.get("refdes"),
                    "uid": component.get("uid"),
                    "sheet": component.get("sheet"),
                },
                "reasons": [
                    "attribute_envelope_not_observed",
                    "attribute_position_visibility_font_and_direction_not_observed",
                    "symbol_graphics_and_wire_paths_not_observed",
                    "actual_sheet_usable_boundary_not_observed",
                ],
                "affected_rules": list(EDIT_RULES),
            }
        )
    report["object_counts"]["affected_unreviewed"] = len(report["unreviewed_objects"])
    report["scope"] = {"requested_sheet": sheet, "native_graphic_enumeration_complete": False}
    for item in report["unreviewed_collections"]:
        item["enumeration_complete"] = False
    return report


def unobserved_drawing(reason: str) -> dict[str, Any]:
    """No drawing model was read: do not fabricate an empty native inventory."""
    report = _base("not_observed")
    report["object_counts"] = {"snapshot_components": None, "affected_unreviewed": None}
    report["unreviewed_collections"] = [
        {"collection": name, "reason": reason, "enumeration_complete": False}
        for name in [
            "components",
            "text_graphics",
            "symbol_graphics",
            "wire_paths_and_junctions",
            "sheet_boundaries",
        ]
    ]
    return report
