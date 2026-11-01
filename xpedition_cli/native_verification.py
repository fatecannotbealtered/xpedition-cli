"""Verify requested NativeBackend postconditions against an observed snapshot.

This is NOT a durability test or an ERC/DRC substitute. It checks the final
state of affected objects, so successive moves/properties are not mistaken for
failed intermediate states. Missing observations and unsupported operations
are never reported as verified.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

# Serialization round-off in native coordinates, not a manufacturing tolerance.
_COORDINATE_ABS_TOLERANCE = 1e-6
_MISSING = object()


def _at(value: Any, path: tuple[str, ...]) -> Any:
    for key in path:
        if not isinstance(value, Mapping) or key not in value:
            return _MISSING
        value = value[key]
    return value


def _same(expected: Any, observed: Any, *, coordinate: bool = False) -> bool:
    if observed is _MISSING:
        return False
    if coordinate:
        if isinstance(expected, bool) or isinstance(observed, bool):
            return False
        if not isinstance(expected, (int, float)) or not isinstance(observed, (int, float)):
            return False
        return (
            math.isfinite(expected)
            and math.isfinite(observed)
            and math.isclose(expected, observed, rel_tol=0.0, abs_tol=_COORDINATE_ABS_TOLERANCE)
        )
    if isinstance(expected, dict):
        return isinstance(observed, Mapping) and all(
            _same(item, observed.get(key, _MISSING)) for key, item in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(observed, list)
            and len(expected) == len(observed)
            and all(_same(left, right) for left, right in zip(expected, observed, strict=True))
        )
    return type(expected) is type(observed) and expected == observed


def _index(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if isinstance(row, dict) and row.get(key) is not None:
            result[str(row[key])].append(row)
    return result


def verify_native_changes(
    expected: dict[str, Any], observed: dict[str, Any], operations: list[dict[str, Any]]
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    targets: dict[tuple[str, str], set[tuple[str, ...]]] = defaultdict(set)
    net_fields: dict[str, set[str]] = defaultdict(set)
    connected_nets: set[str] = set()
    disconnected: set[str] = set()
    renamed: dict[str, str] = {}
    for index, operation in enumerate(operations):
        kind = operation["type"]
        if kind in {
            "place_component",
            "move_component",
            "delete_component",
            "set_property",
            "place_pcb_component",
            "move_pcb_component",
        }:
            section = (
                "pcb" if kind in {"place_pcb_component", "move_pcb_component"} else "schematic"
            )
            fields = targets[(section, str(operation["refdes"]))]
            if kind.startswith("move_"):
                fields.update({("x",), ("y",)})
            elif kind == "set_property":
                fields.add(("attributes", str(operation["name"])))
            elif kind.startswith("place_"):
                mapping = (
                    {"part_number": "internal_part_no", "footprint": "package"}
                    if section == "schematic"
                    else {}
                )
                keys = (
                    (
                        "part_number",
                        "mpn",
                        "manufacturer",
                        "description",
                        "value",
                        "footprint",
                        "package",
                        "x",
                        "y",
                        "properties",
                        "pins",
                    )
                    if section == "schematic"
                    else ("footprint", "part_number", "x", "y", "rotation", "side")
                )
                fields.update((mapping.get(key, key),) for key in keys if key in operation)
        elif kind == "create_net":
            fields = net_fields[str(operation["name"])]
            if "net_type" in operation:
                fields.add("type")
            if "class" in operation:
                fields.add("class")
        elif kind == "connect":
            connected_nets.add(str(operation["net"]))
        elif kind == "disconnect":
            disconnected.add(str(operation["pin"]))
        elif kind == "rename_net":
            renamed[str(operation["net"])] = str(operation["name"])
            connected_nets.add(str(operation["name"]))
        else:
            issues.append({"kind": "unverified_operation", "index": index, "operation": kind})

    for section in ("schematic", "pcb"):
        desired = expected if section == "schematic" else expected.get("pcb", {})
        actual = observed if section == "schematic" else observed.get("pcb", {})
        desired_index = _index(desired.get("components", []), "refdes")
        actual_index = _index(actual.get("components", []), "refdes")
        for (domain, refdes), fields in targets.items():
            if domain != section:
                continue
            want, got = desired_index.get(refdes, []), actual_index.get(refdes, [])
            if not want:
                if got:
                    issues.append(
                        {"kind": "component_not_deleted", "domain": domain, "refdes": refdes}
                    )
                if domain == "schematic" and any(
                    str(pin).startswith(refdes + ".")
                    for connection in observed.get("connections", [])
                    for pin in connection.get("pins", [])
                ):
                    issues.append({"kind": "deleted_component_still_connected", "refdes": refdes})
                continue
            if len(want) != 1 or len(got) != 1:
                issues.append(
                    {
                        "kind": "component_count_mismatch",
                        "domain": domain,
                        "refdes": refdes,
                        "expected": len(want),
                        "observed": len(got),
                    }
                )
                continue
            for path in sorted(fields):
                expected_value, observed_value = _at(want[0], path), _at(got[0], path)
                if expected_value is _MISSING:
                    continue  # An earlier operation was superseded by delete/re-place.
                if not _same(
                    expected_value,
                    observed_value,
                    coordinate=path in {("x",), ("y",), ("rotation",)},
                ):
                    issues.append(
                        {
                            "kind": "field_mismatch",
                            "domain": domain,
                            "refdes": refdes,
                            "field": ".".join(path),
                            "expected": expected_value,
                            "observed": None if observed_value is _MISSING else observed_value,
                            "observed_present": observed_value is not _MISSING,
                        }
                    )

    desired_nets = _index(expected.get("nets", []), "name")
    actual_nets = _index(observed.get("nets", []), "name")
    for name, fields in net_fields.items():
        want, got = desired_nets.get(name, []), actual_nets.get(name, [])
        if len(want) != 1 or len(got) != 1:
            issues.append(
                {
                    "kind": "net_count_mismatch",
                    "net": name,
                    "expected": len(want),
                    "observed": len(got),
                }
            )
            continue
        for key in sorted(fields):
            if not _same(want[0].get(key, _MISSING), got[0].get(key, _MISSING)):
                issues.append({"kind": "net_field_mismatch", "net": name, "field": key})

    def pins_by_net(project: dict[str, Any]) -> dict[str, set[str]]:
        result: dict[str, set[str]] = defaultdict(set)
        for connection in project.get("connections", []):
            result[str(connection.get("net"))].update(
                str(pin) for pin in connection.get("pins", [])
            )
        return result

    desired_pins, actual_pins = pins_by_net(expected), pins_by_net(observed)
    for name in sorted(connected_nets):
        want, got = desired_pins.get(name, set()), actual_pins.get(name, set())
        if want != got or len(actual_nets.get(name, [])) != 1:
            issues.append(
                {
                    "kind": "connectivity_mismatch",
                    "net": name,
                    "missing_pins": sorted(want - got),
                    "unexpected_pins": sorted(got - want),
                }
            )
        other_nets = sorted(net for net, pins in actual_pins.items() if net != name and want & pins)
        if other_nets:
            issues.append({"kind": "pins_on_other_nets", "net": name, "other_nets": other_nets})
    for old in sorted(renamed):
        if old in actual_nets:
            issues.append({"kind": "net_not_renamed", "net": old, "name": renamed[old]})
    observed_pins = {
        f"{component.get('refdes')}.{row.get('number')}": row.get("net")
        for component in observed.get("components", [])
        for row in component.get("pins") or []
    }
    for pin in sorted(disconnected):
        still = observed_pins.get(pin, _MISSING)
        if still is _MISSING:
            issues.append({"kind": "pin_missing", "pin": pin})
        elif still:
            issues.append({"kind": "pin_still_connected", "pin": pin, "net": still})
    return {
        "valid": not issues,
        "status": "verified" if not issues else "failed",
        "scope": "requested_postconditions",
        "checked_operations": len(operations),
        "component_count": len(observed.get("components", [])),
        "net_count": len(observed.get("nets", [])),
        "issues": issues,
        "_untrusted": ["issues"],
    }


def verify_batch_drc_report(
    report: dict[str, Any], *, project: str, run_requested: bool
) -> dict[str, Any]:
    """A zero hazard count is meaningful only for a completed, bound current run.

    This validates adapter evidence, not the application's implementation. The
    engine path independently validates native files and reopened hazards;
    legacy adapters without these observations fail closed.
    """

    def mapping(value: Any) -> Mapping:
        return value if isinstance(value, Mapping) else {}

    def known(value: Any) -> bool:
        return isinstance(value, str) and bool(value.strip()) and value.strip().lower() != "unknown"

    def sha256(value: Any) -> bool:
        return (
            isinstance(value, str)
            and len(value) == 64
            and all(c in "0123456789abcdef" for c in value.lower())
        )

    source = mapping(report.get("source"))
    completion = mapping(report.get("completion"))
    inputs = mapping(source.get("input"))
    coverage = mapping(source.get("coverage"))
    rules = coverage.get("rules_checked")
    excluded = coverage.get("excluded_rules")
    checks = {
        "run_requested": run_requested,
        "run_confirmed": report.get("ran") is True,
        "completion_confirmed": completion.get("status") == "completed",
        "not_timed_out": completion.get("timed_out") is False,
        "source_complete": source.get("complete") is True,
        "native_source": source.get("kind") == "native_checker",
        "current_run_results": source.get("freshness") == "current_run",
        "application_version_known": known(source.get("application_version")),
        "checker_version_known": known(source.get("checker_version")),
        "input_target_matches": inputs.get("requested_project") == project,
        "observed_target_known": known(report.get("pcb"))
        and inputs.get("observed_pcb") == report.get("pcb"),
        "input_snapshot_known": sha256(inputs.get("snapshot_sha256")),
        "rule_config_known": sha256(coverage.get("rule_config_sha256")),
        "rules_known": isinstance(rules, list) and bool(rules) and all(known(r) for r in rules),
        "exclusions_known": isinstance(excluded, list) and all(known(r) for r in excluded),
        "selected_rule_execution_confirmed": mapping(
            coverage.get("rule_execution_verification")
        ).get("selected_rules_verified")
        is True,
    }
    missing = [name for name, valid in checks.items() if not valid]
    valid = not missing and report.get("passes") is True
    return {
        "valid": valid,
        "evidence_complete": not missing,
        "status": ("incomplete" if missing else ("passed" if valid else "failed"))
        if run_requested
        else "not_run",
        "scope": "native_batch_drc_current_run",
        "missing_evidence": missing,
    }
