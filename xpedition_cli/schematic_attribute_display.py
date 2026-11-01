"""Bounded attribute display edits and independent observed-state verification.

Display coordinates and Size use Designer sheet units (one unit = 10 mil).
This deliberately does not estimate font extents or accept unobserved defaults.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from typing import Any

from .errors import CLIError

DISPLAY_NAMES = ("Name", "Value", "Part Label", "Part Name", "Part Number", "Ref Designator")
VALUE_VISIBLE = 3  # VdVisibilityFlag.VDVALUEVISIBLE, installed Viewdraw 151.0 typelib
NOT_CHECKED = [
    "native_font_family_and_exact_text_envelope",
    "other_free_text_and_symbol_graphics",
    "visual_collision_sheet_boundary_and_direction_acceptance",
    "native_erc",
    "independent_reopen_persistence",
]


def canonical_name(name: Any) -> str:
    return "".join(str(name).split()).casefold()


def is_display_only_operations(operations: list[dict[str, Any]]) -> bool:
    return bool(operations) and all(
        isinstance(op, dict) and op.get("type") == "set_property" and "display" in op
        for op in operations
    )


def validate_display_operation(operation: dict[str, Any]) -> None:
    if "display" not in operation:
        return
    display = operation["display"]
    if "sheet" in operation and (type(operation["sheet"]) is not int or operation["sheet"] < 1):
        raise CLIError("E_VALIDATION", "display sheet is a positive integer")
    if operation.get("type") != "set_property" or canonical_name(operation.get("name")) not in {
        "value",
        "partlabel",
    }:
        raise CLIError(
            "E_VALIDATION", "display is limited to existing Value or Part Label attributes"
        )
    if not isinstance(operation.get("value"), str):
        raise CLIError("E_VALIDATION", "a display edit's value must be a string")
    if (
        not isinstance(display, dict)
        or set(display) - {"x", "y", "visible", "size"}
        or not {"x", "y", "visible"} <= set(display)
        or display.get("visible") is not True
        or any(type(display.get(key)) is not int for key in ("x", "y"))
        or any(not -(2**31) <= display[key] < 2**31 for key in ("x", "y"))
        or (
            "size" in display
            and (type(display["size"]) is not int or not 5 <= display["size"] <= 30)
        )
    ):
        raise CLIError(
            "E_VALIDATION",
            "display requires integer LONG x/y sheet coordinates and visible:true; "
            "optional size is an integer from 5 to 30 sheet units",
        )


def target_record(component: dict[str, Any], name: str) -> dict[str, Any]:
    coverage = component.get("attribute_display_coverage") or {}
    records = [
        row
        for row in component.get("attribute_display", [])
        if isinstance(row, dict) and canonical_name(row.get("name")) == canonical_name(name)
    ]
    if coverage.get("enumeration_complete") is not True or len(records) != 1:
        raise CLIError(
            "E_VALIDATION",
            "display needs one uniquely observed existing attribute",
            {
                "refdes": component.get("refdes"),
                "name": name,
                "matches": len(records),
                "coverage": coverage,
            },
        )
    row = records[0]
    display = row.get("display") or {}
    if (
        row.get("getter_errors")
        or not isinstance(row.get("value"), str)
        or not all(
            type(display.get(k)) is int
            for k in ("x", "y", "visible", "origin", "orientation", "size")
        )
        or display.get("units") != "sheet_unit"
        or display.get("orientation") != 0
        or display.get("visible") not in {0, 1, 2, 3, 4}
        or display.get("size", 0) <= 0
        or not component.get("uid")
        or type(component.get("sheet")) is not int
        or component["sheet"] < 1
        or row.get("owner")
        != {
            "refdes": component.get("refdes"),
            "uid": component.get("uid"),
            "sheet": component.get("sheet"),
        }
    ):
        raise CLIError(
            "E_VALIDATION",
            "display requires actual complete getters and horizontal orientation 0",
            {"refdes": component.get("refdes"), "name": name, "observed": row},
        )
    return row


def project_display(component: dict[str, Any], operation: dict[str, Any]) -> tuple[dict, dict]:
    validate_display_operation(operation)
    record = target_record(component, str(operation["name"]))
    if operation.get("sheet", component.get("sheet")) != component.get("sheet"):
        raise CLIError("E_VALIDATION", "display sheet must match the observed instance sheet")
    before = copy.deepcopy(record)
    record["value"] = operation["value"]
    record["display"].update(
        x=operation["display"]["x"], y=operation["display"]["y"], visible=VALUE_VISIBLE
    )
    if "size" in operation["display"]:
        record["display"]["size"] = operation["display"]["size"]
    attributes = component.get("attributes")
    if not isinstance(attributes, dict) or before["name"] not in attributes:
        raise CLIError("E_VALIDATION", "display attribute value was not independently enumerated")
    attributes[before["name"]] = operation["value"]
    # This summary is derived by the native reader from Value, never Part Label.
    if canonical_name(before["name"]) == "value":
        component["value"] = (
            attributes.get("VALUE")
            or attributes.get("Value")
            or component.get("internal_part_no", "")
        )
    return before, copy.deepcopy(record)


def _native_border_datetime(value: Any) -> bool:
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{2}/[0-9]{2}/[0-9]{4}:[0-9]{2}:[0-9]{2}", value
    ):
        return False
    try:
        datetime.strptime(value, "%d/%m/%Y:%H:%M")
    except ValueError:
        return False
    return True


def freeze_save_metadata(before: dict) -> dict | None:
    """Bind one observed border time; the edit/save phase was not isolated."""
    if (
        not isinstance(before, dict)
        or not isinstance(before.get("components"), list)
        or not isinstance(before.get("unnamed_components"), list)
    ):
        raise CLIError("E_VALIDATION", "native save metadata requires actual component collections")
    records = [*before.get("components", []), *before.get("unnamed_components", [])]
    if any(
        not isinstance(row, dict) or not isinstance(row.get("attributes"), dict) for row in records
    ):
        raise CLIError("E_VALIDATION", "native save metadata attribute observations are incomplete")
    dated = [row for row in records if "@DATETIME" in (row.get("attributes") or {})]
    if not dated:
        return None
    sheet_numbers = []
    for sheet_record in before.get("sheets", []):
        number = sheet_record.get("number")
        # The CLI normalises identifier keys to strings; the native observer is raw.
        if isinstance(number, str) and re.fullmatch(r"[1-9][0-9]*", number):
            try:
                number = int(number)
            except ValueError:
                number = None
        sheet_numbers.append(number)
    known_sheets = (
        bool(sheet_numbers)
        and all(type(number) is int and number > 0 for number in sheet_numbers)
        and len(set(sheet_numbers)) == len(sheet_numbers)
    )
    candidates = []
    for row in before.get("unnamed_components", []):
        attributes = row.get("attributes") or {}
        uid, sheet = row.get("uid"), row.get("sheet")
        if (
            row.get("refdes") == ""
            and row.get("pins") == []
            and isinstance(uid, str)
            and uid
            and sum(record.get("uid") == uid for record in records) == 1
            and type(sheet) is int
            and known_sheets
            and sheet in sheet_numbers
            and isinstance(before.get("project"), str)
            and before["project"]
            and attributes.get("@NAME") == before["project"]
            and attributes.get("@Sheet") == str(sheet)
            and attributes.get("@SHEETTOTAL") == str(len(sheet_numbers))
            and _native_border_datetime(attributes.get("@DATETIME"))
            and (row.get("identity_observation_coverage") or {}).get("complete") is True
            and (row.get("pin_observation_coverage") or {}).get("complete") is True
            and (row.get("attribute_display_coverage") or {}).get("enumeration_complete") is True
        ):
            candidates.append(row)
    if len(candidates) != 1:
        raise CLIError(
            "E_VALIDATION",
            "native save timestamp needs one uniquely observed pinless border owner",
            {"matches": len(candidates), "dated_owners": [row.get("uid") for row in dated]},
        )
    row = candidates[0]
    return {
        "owner": {"refdes": "", "uid": row["uid"], "sheet": row["sheet"]},
        "field": "@DATETIME",
        "before": row["attributes"]["@DATETIME"],
        "role_markers": {key: row["attributes"][key] for key in ("@NAME", "@Sheet", "@SHEETTOTAL")},
        "phase_scope": "edit_save_reopen",
        "phase_evidence": "edit_plus_save_bundle; individual_phase_not_isolated",
    }


def same_save_metadata(expected: dict | None, supplied: Any) -> bool:
    """A bool/float sheet cannot substitute for the frozen integer owner sheet."""
    try:
        return json.dumps(expected, sort_keys=True, allow_nan=False) == json.dumps(
            supplied, sort_keys=True, allow_nan=False
        )
    except (TypeError, ValueError):
        return False


def verify_display_changes(
    before: dict, after: dict, operations: list[dict], *, save_metadata: dict | None = None
) -> dict:
    """Compare fresh snapshots, including every non-target record and pin/net.

    `complete` describes observation availability; `valid` describes equality.
    Only the explicitly bound border time may change during edit/save/reopen;
    the evidence did not isolate the individual phase. All other observations stay
    protected, and calls without this binding (including later reads) stay strict.
    """
    issues: list[dict] = []
    complete = True
    metadata_changes: list[dict] = []
    expected = copy.deepcopy(before)
    try:
        if not is_display_only_operations(operations):
            raise CLIError("E_VALIDATION", "verification requires display-only set_property edits")
        for snapshot in (before, after):
            if not all(
                isinstance(snapshot.get(key), list)
                for key in ("components", "nets", "connections", "sheets", "unnamed_components")
            ):
                raise CLIError(
                    "E_VALIDATION", "required native protection collections were not read"
                )
            components = snapshot["components"]
            records = [*components, *snapshot["unnamed_components"]]
            refs = [row.get("refdes") for row in components]
            uids = [row.get("uid") for row in records]
            if (
                any(not isinstance(ref, str) or not ref for ref in refs)
                or len(set(refs)) != len(refs)
                or any(not isinstance(uid, str) or not uid for uid in uids)
                or len(set(uids)) != len(uids)
                or any(row.get("refdes") != "" for row in snapshot["unnamed_components"])
            ):
                raise CLIError("E_VALIDATION", "native component identity is missing or ambiguous")
            if any(
                not isinstance(row.get("pins"), list)
                or not isinstance(row.get("attributes"), dict)
                or not isinstance(row.get("attribute_display"), list)
                for row in records
            ):
                raise CLIError(
                    "E_VALIDATION", "component pins or attribute values were not observed"
                )
            for row in records:
                names = [attribute.get("name") for attribute in row["attribute_display"]]
                if any(not isinstance(name, str) or not name for name in names) or len(
                    set(names)
                ) != len(names):
                    raise CLIError(
                        "E_VALIDATION", "attribute display names are missing or duplicated"
                    )
            if any(
                (row.get("pin_observation_coverage") or {}).get("complete") is not True
                or (row.get("identity_observation_coverage") or {}).get("complete") is not True
                or (row.get("attribute_display_coverage") or {}).get("enumeration_complete")
                is not True
                for row in records
            ):
                raise CLIError(
                    "E_VALIDATION", "component pin/net or attribute enumeration is incomplete"
                )
        for op in operations:
            ref = str(op["refdes"])
            wanted = [row for row in expected["components"] if str(row.get("refdes")) == ref]
            got = [row for row in after["components"] if str(row.get("refdes")) == ref]
            if len(wanted) != 1 or len(got) != 1:
                raise CLIError("E_VALIDATION", "display target instance is missing or ambiguous")
            target_record(got[0], str(op["name"]))
            project_display(wanted[0], op)
        if save_metadata is not None:
            if not same_save_metadata(freeze_save_metadata(before), save_metadata):
                raise CLIError(
                    "E_VALIDATION", "save metadata differs from the frozen native border scope"
                )
            owner = save_metadata["owner"]
            wanted_border = next(
                row for row in expected["unnamed_components"] if row.get("uid") == owner["uid"]
            )
            observed_border = [
                row
                for row in after["unnamed_components"]
                if {key: row.get(key) for key in ("refdes", "uid", "sheet")} == owner
            ]
            if len(observed_border) != 1:
                raise CLIError(
                    "E_VALIDATION", "the bound native save metadata owner is missing or changed"
                )
            value = observed_border[0]["attributes"].get("@DATETIME")
            if not _native_border_datetime(value):
                raise CLIError(
                    "E_VALIDATION",
                    "the native border timestamp is not a valid DD/MM/YYYY:HH:MM value",
                )
            if value != save_metadata["before"]:
                wanted_border["attributes"]["@DATETIME"] = value
                metadata_changes.append(
                    {
                        "owner": copy.deepcopy(owner),
                        "field": "@DATETIME",
                        "before": save_metadata["before"],
                        "after": value,
                        "source": "native_save_metadata",
                    }
                )
    except (CLIError, KeyError, TypeError, AttributeError, ValueError) as error:
        complete = False
        issues.append(
            {
                "kind": "required_observation_unavailable",
                "message": str(error),
                "details": getattr(error, "details", {}),
            }
        )
    # Refdes keyed sorting makes collection order irrelevant, while preserving duplicates.
    for key in sorted((set(expected) | set(after)) - {"revision", "metadata"}):
        left, right = expected.get(key), after.get(key)
        if key in {"components", "unnamed_components"}:
            # IVdComp.Attributes enumeration can change order after normal reopen.
            # Compare complete records as a multiset, without changing raw snapshots,
            # merging names, removing duplicates, or normalising other collections.
            def component_records(rows):
                if not isinstance(rows, list):
                    return rows
                return [
                    {
                        **row,
                        "attribute_display": sorted(
                            json.dumps(attribute, sort_keys=True)
                            for attribute in row["attribute_display"]
                        ),
                    }
                    if isinstance(row, dict) and isinstance(row.get("attribute_display"), list)
                    else row
                    for row in rows
                ]

            left, right = component_records(left), component_records(right)
        if (
            key in {"components", "unnamed_components", "nets", "connections", "sheets"}
            and isinstance(left, list)
            and isinstance(right, list)
        ):
            left = sorted(left, key=lambda row: json.dumps(row, sort_keys=True))
            right = sorted(right, key=lambda row: json.dumps(row, sort_keys=True))
        if left != right:
            issues.append(
                {"kind": "protected_record_or_requested_display_mismatch", "collection": key}
            )
    valid = complete and not issues
    return {
        "valid": valid,
        "complete": complete,
        "display_verified": valid,
        "protection_verified": valid,
        "persistence_verified": False,
        "issues": issues,
        "metadata_changes": metadata_changes,
        "scope": "display postconditions and all observed native engineering records; "
        "only explicitly bound native save time is reported separately",
        "not_checked": list(NOT_CHECKED),
    }
