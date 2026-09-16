from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .errors import CLIError
from .models import save_project

SUPPORTED_OPERATIONS = {
    "place_component",
    "create_net",
    "connect",
    "move_component",
    "delete_component",
    "set_property",
    "place_pcb_component",
    "move_pcb_component",
    "create_track",
    "create_via",
    "create_zone",
}


def load_changeset(path: str | None) -> tuple[dict[str, Any], Path]:
    if not path:
        raise CLIError("E_USAGE", "--changeset is required")
    changeset_path = Path(path).expanduser().resolve()
    try:
        raw = json.loads(changeset_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CLIError(
            "E_NOT_FOUND", "changeset file was not found", {"path": str(changeset_path)}
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise CLIError(
            "E_CHANGESET_INVALID", f"cannot read changeset: {exc}", {"path": str(changeset_path)}
        ) from exc
    if not isinstance(raw, dict):
        raise CLIError("E_CHANGESET_INVALID", "changeset must be a JSON object")
    operations = raw.get("operations")
    if not isinstance(operations, list) or not operations:
        raise CLIError("E_CHANGESET_INVALID", "changeset.operations must be a non-empty array")
    if not raw.get("project"):
        raise CLIError("E_CHANGESET_INVALID", "changeset.project is required")
    for index, operation in enumerate(operations):
        validate_operation(operation, index)
    return raw, changeset_path


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
        "place_pcb_component": ("refdes", "footprint", "x", "y"),
        "move_pcb_component": ("refdes", "x", "y"),
        "create_track": ("net", "layer", "points"),
        "create_via": ("net", "x", "y"),
        "create_zone": ("name", "layer", "polygon"),
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


def validate_changeset(changeset: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    for index, operation in enumerate(changeset.get("operations", [])):
        try:
            validate_operation(operation, index)
        except CLIError as error:
            issues.append({"index": index, "code": error.code, "message": error.message})
    return {
        "valid": not issues,
        "project": str(changeset.get("project", "")),
        "base_revision": str(changeset.get("base_revision", "")) or None,
        "operation_count": len(changeset.get("operations", [])),
        "operations": [
            str(item.get("type", ""))
            for item in changeset.get("operations", [])
            if isinstance(item, dict)
        ],
        "issues": issues,
        "_untrusted": ["project", "operations", "issues[].message"],
    }


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


def _find_pcb_component(project: dict[str, Any], refdes: str) -> dict[str, Any]:
    for component in project["pcb"]["components"]:
        if str(component.get("refdes")) == str(refdes):
            return component
    raise CLIError(
        "E_NOT_FOUND", f"PCB component {refdes!r} was not found", {"refdes": str(refdes)}
    )


def _find_pcb_net(project: dict[str, Any], name: str) -> dict[str, Any]:
    for net in project["pcb"]["nets"]:
        if str(net.get("name")) == str(name):
            return net
    return _find_net(project, name)


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
            result["connections"] = [
                connection
                for connection in result["connections"]
                if not any(str(pin).startswith(refdes + ".") for pin in connection.get("pins", []))
            ]
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
            properties = component.setdefault("properties", {})
            name = str(operation["name"])
            before = properties.get(name)
            properties[name] = operation["value"]
            changes.append(
                {
                    "action": "set_property",
                    "resource": "component",
                    "id": refdes,
                    "before": {name: before},
                    "after": {name: operation["value"]},
                }
            )
        elif operation_type == "place_pcb_component":
            refdes = str(operation["refdes"])
            if any(str(item.get("refdes")) == refdes for item in result["pcb"]["components"]):
                raise CLIError(
                    "E_CONFLICT", f"PCB component {refdes!r} already exists", {"refdes": refdes}
                )
            component = {
                "refdes": refdes,
                "footprint": str(operation["footprint"]),
                "part_number": str(operation.get("part_number", "")),
                "x": operation["x"],
                "y": operation["y"],
                "rotation": operation.get("rotation", 0),
                "side": str(operation.get("side", "top")),
            }
            result["pcb"]["components"].append(component)
            changes.append(
                {
                    "action": "place_pcb_component",
                    "resource": "pcb_component",
                    "id": refdes,
                    "before": None,
                    "after": component,
                }
            )
        elif operation_type == "move_pcb_component":
            refdes = str(operation["refdes"])
            component = _find_pcb_component(result, refdes)
            before = {"x": component.get("x"), "y": component.get("y")}
            component["x"] = operation["x"]
            component["y"] = operation["y"]
            changes.append(
                {
                    "action": "move_pcb_component",
                    "resource": "pcb_component",
                    "id": refdes,
                    "before": before,
                    "after": {"x": component["x"], "y": component["y"]},
                }
            )
        elif operation_type == "create_track":
            net_name = str(operation["net"])
            _find_pcb_net(result, net_name)
            points = copy.deepcopy(operation["points"])
            if not isinstance(points, list) or len(points) < 2:
                raise CLIError(
                    "E_CHANGESET_INVALID", "track.points must contain at least two points"
                )
            track = {
                "net": net_name,
                "layer": str(operation["layer"]),
                "points": points,
                "width": operation.get("width"),
            }
            result["pcb"]["tracks"].append(track)
            changes.append(
                {
                    "action": "create_track",
                    "resource": "track",
                    "id": net_name,
                    "before": None,
                    "after": track,
                }
            )
        elif operation_type == "create_via":
            net_name = str(operation["net"])
            _find_pcb_net(result, net_name)
            via = {
                "net": net_name,
                "x": operation["x"],
                "y": operation["y"],
                "start_layer": str(operation.get("start_layer", "TOP")),
                "end_layer": str(operation.get("end_layer", "BOTTOM")),
            }
            result["pcb"]["vias"].append(via)
            changes.append(
                {
                    "action": "create_via",
                    "resource": "via",
                    "id": net_name,
                    "before": None,
                    "after": via,
                }
            )
        elif operation_type == "create_zone":
            zone = {
                "name": str(operation["name"]),
                "layer": str(operation["layer"]),
                "polygon": copy.deepcopy(operation["polygon"]),
                "net": operation.get("net"),
            }
            if zone["net"] is not None:
                _find_pcb_net(result, str(zone["net"]))
            if not isinstance(zone["polygon"], list) or len(zone["polygon"]) < 3:
                raise CLIError(
                    "E_CHANGESET_INVALID", "zone.polygon must contain at least three points"
                )
            result["pcb"]["zones"].append(zone)
            changes.append(
                {
                    "action": "create_zone",
                    "resource": "zone",
                    "id": zone["name"],
                    "before": None,
                    "after": zone,
                }
            )
    result["revision"] = _next_revision(str(project.get("revision", "R00")))
    return result, changes


def preview_changes(project: dict[str, Any], changeset: dict[str, Any]) -> dict[str, Any]:
    expected = changeset.get("base_revision")
    current = str(project.get("revision", "R00"))
    if expected and str(expected) != current:
        raise CLIError(
            "E_CONFLICT",
            "changeset base revision does not match project",
            {"expected": str(expected), "current": current},
        )
    projected, changes = apply_operations(project, changeset["operations"])
    return {
        "project": str(changeset["project"]),
        "base_revision": current,
        "result_revision": projected["revision"],
        "operation_count": len(changeset["operations"]),
        "changes": changes,
        "risk": {"tier": "T1", "blast_radius": "local project file selected by --project"},
        "_untrusted": ["project", "changes", "risk.blast_radius"],
    }


def persist_changes(
    project: dict[str, Any], project_path: Path | None, backup: bool
) -> tuple[dict[str, Any], str | None]:
    if project_path is None:
        raise CLIError("E_CONFIG", "change apply requires --project so the result can be persisted")
    backup_path = save_project(project, project_path, backup=backup)
    return project, backup_path


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
                "source": component.get("source", "mock"),
                "revision": component.get("revision"),
            }
        )
    return rows
