"""The shape a design read back from Xpedition is normalised to."""

from __future__ import annotations

import copy
from typing import Any

from .errors import CLIError

EMPTY_DESIGN: dict[str, Any] = {
    "project": "",
    "revision": "native",
    "sheets": [],
    "components": [],
    "nets": [],
    "connections": [],
    "pcb": {"components": [], "footprints": [], "nets": [], "tracks": [], "vias": []},
    "metadata": {},
}

_LISTS = ("sheets", "components", "nets", "connections")
_PCB_LISTS = ("components", "footprints", "nets", "tracks", "vias")


def _stringify_identifiers(value: Any) -> Any:
    if isinstance(value, dict):
        result = {key: _stringify_identifiers(item) for key, item in value.items()}
        for key in ("id", "refdes", "name", "net", "layer", "part_number", "number"):
            if result.get(key) is not None:
                result[key] = str(result[key])
        return result
    if isinstance(value, list):
        return [_stringify_identifiers(item) for item in value]
    return value


def normalise_project(project: dict[str, Any], *, observed: bool = True) -> dict[str, Any]:
    """A design as the adapter read it, in the shape every command reads.

    A real schematic always carries symbols without a reference designator --
    ground, power, ports, borders, title blocks -- and wires nobody named. They
    are counted, not reported as components or nets, instead of failing the read.
    """
    if not isinstance(project, dict):
        raise CLIError("E_SERVER", "the adapter's design snapshot is not an object")
    value = copy.deepcopy(EMPTY_DESIGN)
    value.update(project)
    for key in _LISTS:
        if not isinstance(value.get(key), list):
            raise CLIError("E_SERVER", f"the design snapshot's {key} is not a list", {"field": key})
    pcb = value.get("pcb") if isinstance(value.get("pcb"), dict) else {}
    value["pcb"] = {key: list(pcb.get(key) or []) for key in _PCB_LISTS}
    if not isinstance(value.get("metadata"), dict):
        value["metadata"] = {}
    value["project"] = str(value.get("project") or "")
    value["revision"] = str(value.get("revision") or "native")
    named_components, unnamed_components = [], 0
    for component in value["components"]:
        if not isinstance(component, dict) or not component.get("refdes"):
            if not observed:
                raise CLIError("E_PROJECT_INVALID", "each component needs a refdes")
            unnamed_components += 1
            continue
        named_components.append(component)
    value["components"] = named_components
    named_nets, unnamed_nets = [], 0
    for net in value["nets"]:
        if not isinstance(net, dict) or not net.get("name"):
            if not observed:
                raise CLIError("E_PROJECT_INVALID", "each net needs a name")
            unnamed_nets += 1
            continue
        named_nets.append(net)
    value["nets"] = named_nets
    if unnamed_components or unnamed_nets:
        value["metadata"]["unnamed"] = {
            "components": unnamed_components,
            "nets": unnamed_nets,
            "note": "symbols without a reference designator and unnamed wires were read but "
            "are not reported as components or nets",
        }
    return _stringify_identifiers(value)
