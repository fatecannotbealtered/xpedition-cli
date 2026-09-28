"""Schematic operations projected onto a design as read, before Designer is asked.

`schematic edit` projects every operation here first: a missing part, pin or net,
or a part that exists already, is refused before anything is written, and the
projection is what the design read back afterwards is verified against.
"""

from __future__ import annotations

import copy
from typing import Any

from .errors import CLIError

SUPPORTED_OPERATIONS = {
    "place_component",
    "create_net",
    "connect",
    "move_component",
    "delete_component",
    "set_property",
    "disconnect",
    "rename_net",
}
# attributes an edit must not set: the reference designator is the part's identity
PROTECTED_ATTRIBUTES = {"ref designator", "refdes"}


def validate_operation(operation: Any, index: int = 0) -> None:
    if not isinstance(operation, dict):
        raise CLIError("E_CHANGESET_INVALID", "operation must be an object", {"index": index})
    operation_type = operation.get("type")
    if operation_type not in SUPPORTED_OPERATIONS:
        raise CLIError(
            "E_CHANGESET_INVALID",
            f"unsupported operation type: {operation_type!r}",
            {"index": index, "supported": sorted(SUPPORTED_OPERATIONS)},
        )
    required: dict[str, tuple[str, ...]] = {
        "place_component": ("refdes", "part_number"),
        "create_net": ("name",),
        "connect": ("net", "pins"),
        "move_component": ("refdes", "x", "y"),
        "delete_component": ("refdes",),
        "set_property": ("refdes", "name", "value"),
        "disconnect": ("pin",),
        "rename_net": ("net", "name"),
    }
    missing = [key for key in required[operation_type] if key not in operation]
    if missing:
        raise CLIError(
            "E_CHANGESET_INVALID",
            "operation is missing required fields",
            {"index": index, "missing": missing},
        )
    if operation_type == "connect" and (
        not isinstance(operation["pins"], list) or len(operation["pins"]) < 2
    ):
        raise CLIError(
            "E_CHANGESET_INVALID",
            "connect.pins must contain at least two pin IDs",
            {"index": index},
        )


def _find_component(project: dict[str, Any], refdes: str) -> dict[str, Any]:
    for component in project["components"]:
        if str(component.get("refdes")) == str(refdes):
            return component
    raise CLIError("E_NOT_FOUND", f"component {refdes!r} was not found", {"refdes": str(refdes)})


def _find_net(project: dict[str, Any], name: str) -> dict[str, Any]:
    for net in project["nets"]:
        if str(net.get("name")) == str(name):
            return net
    raise CLIError("E_NOT_FOUND", f"net {name!r} was not found", {"net": str(name)})


def _validate_pin(project: dict[str, Any], pin: str) -> None:
    refdes, separator, pin_number = str(pin).partition(".")
    if not separator or not refdes or not pin_number:
        raise CLIError("E_VALIDATION", f"pin ID must use <refdes>.<pin>: {pin!r}")
    component = _find_component(project, refdes)
    declared_pins = component.get("pins") or []
    if declared_pins and not any(
        str(item.get("number")) == pin_number or str(item.get("name")) == pin_number
        for item in declared_pins
        if isinstance(item, dict)
    ):
        raise CLIError("E_NOT_FOUND", f"pin {pin!r} was not found on component", {"pin": str(pin)})


def _next_revision(revision: str) -> str:
    try:
        number = int(str(revision).lstrip("Rr"))
        return f"R{number + 1:02d}"
    except ValueError:
        return "R01"


def apply_operations(
    project: dict[str, Any], operations: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result = copy.deepcopy(project)
    changes: list[dict[str, Any]] = []
    for index, operation in enumerate(operations):
        validate_operation(operation, index)
        operation_type = operation["type"]
        if operation_type == "place_component":
            refdes = str(operation["refdes"])
            if any(str(item.get("refdes")) == refdes for item in result["components"]):
                raise CLIError(
                    "E_CONFLICT", f"component {refdes!r} already exists", {"refdes": refdes}
                )
            component = {
                "refdes": refdes,
                "internal_part_no": str(operation["part_number"]),
                "mpn": str(operation.get("mpn", operation["part_number"])),
                "manufacturer": str(operation.get("manufacturer", "")),
                "description": str(operation.get("description", "")),
                "value": str(operation.get("value", "")),
                "package": str(operation.get("footprint", operation.get("package", ""))),
                "x": operation.get("x"),
                "y": operation.get("y"),
                "pins": copy.deepcopy(operation.get("pins", [])),
                "properties": copy.deepcopy(operation.get("properties", {})),
            }
            result["components"].append(component)
            changes.append(
                {
                    "action": "place_component",
                    "resource": "component",
                    "id": refdes,
                    "before": None,
                    "after": component,
                }
            )
        elif operation_type == "create_net":
            name = str(operation["name"])
            if any(str(item.get("name")) == name for item in result["nets"]):
                raise CLIError("E_CONFLICT", f"net {name!r} already exists", {"net": name})
            net = {
                "name": name,
                "type": str(operation.get("net_type", "signal")),
                "class": operation.get("class"),
            }
            result["nets"].append(net)
            changes.append(
                {
                    "action": "create_net",
                    "resource": "net",
                    "id": name,
                    "before": None,
                    "after": net,
                }
            )
        elif operation_type == "connect":
            net_name = str(operation["net"])
            _find_net(result, net_name)
            pins = [str(pin) for pin in operation["pins"]]
            for pin in pins:
                _validate_pin(result, pin)
            connection = {"net": net_name, "pins": pins}
            if connection in result["connections"]:
                raise CLIError("E_CONFLICT", "connection already exists", connection)
            result["connections"].append(connection)
            changes.append(
                {
                    "action": "connect",
                    "resource": "connection",
                    "id": net_name,
                    "before": None,
                    "after": connection,
                }
            )
        elif operation_type == "move_component":
            refdes = str(operation["refdes"])
            component = _find_component(result, refdes)
            before = {"x": component.get("x"), "y": component.get("y")}
            component["x"] = operation["x"]
            component["y"] = operation["y"]
            changes.append(
                {
                    "action": "move_component",
                    "resource": "component",
                    "id": refdes,
                    "before": before,
                    "after": {"x": component["x"], "y": component["y"]},
                }
            )
        elif operation_type == "delete_component":
            refdes = str(operation["refdes"])
            component = _find_component(result, refdes)
            result["components"] = [
                item for item in result["components"] if str(item.get("refdes")) != refdes
            ]
            # the part's pins leave their nets; a net keeps the pins it has left
            for connection in result["connections"]:
                connection["pins"] = [
                    pin
                    for pin in connection.get("pins", [])
                    if not str(pin).startswith(refdes + ".")
                ]
            result["connections"] = [c for c in result["connections"] if len(c["pins"]) >= 2]
            changes.append(
                {
                    "action": "delete_component",
                    "resource": "component",
                    "id": refdes,
                    "before": component,
                    "after": None,
                }
            )
        elif operation_type == "set_property":
            refdes = str(operation["refdes"])
            component = _find_component(result, refdes)
            name, value = str(operation["name"]), str(operation["value"])
            if name.strip().casefold() in PROTECTED_ATTRIBUTES or not name.strip():
                raise CLIError(
                    "E_VALIDATION",
                    f"{name!r} is not a property an edit may set",
                    {"index": index, "name": name},
                )
            if any(ord(ch) < 32 for ch in name + value) or "=" in name:
                raise CLIError(
                    "E_VALIDATION",
                    "a property name or value may hold no line breaks, and a name no '='",
                    {"index": index, "name": name[:80]},
                )
            # Designer reads the value back among the part's attributes
            attributes = component.setdefault("attributes", {})
            before = attributes.get(name)
            attributes[name] = value
            changes.append(
                {
                    "action": "set_property",
                    "resource": "component",
                    "id": refdes,
                    "before": {name: before},
                    "after": {name: value},
                }
            )
        elif operation_type == "disconnect":
            pin = str(operation["pin"])
            _validate_pin(result, pin)
            refdes, _, number = pin.partition(".")
            component = _find_component(result, refdes)
            nets = [
                str(row.get("net"))
                for row in component.get("pins") or []
                if str(row.get("number")) == number and row.get("net")
            ]
            if not nets:
                raise CLIError(
                    "E_CONFLICT", f"pin {pin} is not connected", {"index": index, "pin": pin}
                )
            for row in component.get("pins") or []:
                if str(row.get("number")) == number:
                    row["net"] = None
            for connection in result["connections"]:
                connection["pins"] = [p for p in connection.get("pins", []) if str(p) != pin]
            result["connections"] = [c for c in result["connections"] if len(c["pins"]) >= 2]
            changes.append(
                {
                    "action": "disconnect",
                    "resource": "pin",
                    "id": pin,
                    "before": {"net": nets[0]},
                    "after": {"net": None},
                }
            )
        elif operation_type == "rename_net":
            old, new = str(operation["net"]), str(operation["name"]).strip()
            _find_net(result, old)
            from .review_engine import is_ground_net, is_power_net

            if is_power_net(old) or is_ground_net(old):
                raise CLIError(
                    "E_VALIDATION",
                    f"net {old!r} is a power or ground net, which its symbols name; change "
                    "it in the design and draw it again",
                    {"index": index, "net": old},
                )
            if not new or any(ord(ch) < 33 for ch in new):
                raise CLIError(
                    "E_VALIDATION",
                    "a net name is one word with no spaces or line breaks",
                    {"index": index, "name": new[:80]},
                )
            if any(str(item.get("name")) == new for item in result["nets"]):
                raise CLIError(
                    "E_CONFLICT",
                    f"net {new!r} exists; renaming {old!r} to it would merge the two nets",
                    {"index": index, "net": old, "name": new},
                )
            for item in result["nets"]:
                if str(item.get("name")) == old:
                    item["name"] = new
            for connection in result["connections"]:
                if str(connection.get("net")) == old:
                    connection["net"] = new
            for component in result["components"]:
                for row in component.get("pins") or []:
                    if str(row.get("net")) == old:
                        row["net"] = new
            changes.append(
                {
                    "action": "rename_net",
                    "resource": "net",
                    "id": old,
                    "before": {"name": old},
                    "after": {"name": new},
                }
            )
    result["revision"] = _next_revision(str(project.get("revision", "R00")))
    return result, changes


def verify_project(project: dict[str, Any]) -> dict[str, Any]:
    refs = [str(item.get("refdes")) for item in project["components"]]
    nets = [str(item.get("name")) for item in project["nets"]]
    duplicate_refs = sorted({item for item in refs if refs.count(item) > 1})
    duplicate_nets = sorted({item for item in nets if nets.count(item) > 1})
    missing_nets = sorted(
        {
            str(item.get("net"))
            for item in project["connections"]
            if str(item.get("net")) not in nets
        }
    )
    return {
        "valid": not duplicate_refs and not duplicate_nets and not missing_nets,
        "component_count": len(refs),
        "net_count": len(nets),
        "connection_count": len(project["connections"]),
        "duplicate_refdes": duplicate_refs,
        "duplicate_nets": duplicate_nets,
        "connections_with_missing_nets": missing_nets,
    }


def bom_rows(project: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for component in project["components"]:
        rows.append(
            {
                "refdes": str(component.get("refdes", "")),
                "internal_part_no": str(component.get("internal_part_no", "")),
                "manufacturer": str(component.get("manufacturer", "")),
                "mpn": str(component.get("mpn", "")),
                "description": str(component.get("description", "")),
                "value": str(component.get("value", "")),
                "package": str(component.get("package", "")),
                "quantity": 1,
                "variant": component.get("variant"),
                "dnp": bool(component.get("dnp", False)),
                "lifecycle": component.get("lifecycle"),
                "datasheet": component.get("datasheet"),
            }
        )
    return rows
