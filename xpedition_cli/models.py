from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any

from .errors import CLIError

EMPTY_PROJECT: dict[str, Any] = {
    "project": "mock-project",
    "revision": "R00",
    "sheets": [],
    "components": [],
    "nets": [],
    "connections": [],
    "interfaces": [],
    "bom": [],
    "constraints": [],
    "pcb": {
        "components": [],
        "footprints": [],
        "nets": [],
        "layers": [],
        "stackup": [],
        "tracks": [],
        "vias": [],
        "zones": [],
        "keepouts": [],
    },
    "library": {
        "parts": [],
        "symbols": [],
        "footprints": [],
        "padstacks": [],
        "models": [],
    },
    "analysis": {"erc": [], "drc": [], "dfm": [], "results": []},
    "manufacturing": {"artifacts": []},
    "metadata": {"format_version": "1", "backend": "mock"},
}


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


def _normalise(project: dict[str, Any], observed: bool = False) -> dict[str, Any]:
    value = copy.deepcopy(EMPTY_PROJECT)
    value.update(project)
    for key in (
        "sheets",
        "components",
        "nets",
        "connections",
        "interfaces",
        "bom",
        "constraints",
    ):
        if not isinstance(value.get(key), list):
            raise CLIError(
                "E_PROJECT_INVALID", f"project field {key!r} must be an array", {"field": key}
            )
    if not isinstance(value.get("metadata"), dict):
        raise CLIError("E_PROJECT_INVALID", "project metadata must be an object")
    for key in ("pcb", "library", "analysis", "manufacturing"):
        if not isinstance(value.get(key), dict):
            raise CLIError("E_PROJECT_INVALID", f"project field {key!r} must be an object")
        defaults = EMPTY_PROJECT[key]
        merged = copy.deepcopy(defaults)
        merged.update(value[key])
        value[key] = merged
    for key in (
        "components",
        "footprints",
        "nets",
        "layers",
        "stackup",
        "tracks",
        "vias",
        "zones",
        "keepouts",
    ):
        if not isinstance(value["pcb"].get(key), list):
            raise CLIError("E_PROJECT_INVALID", f"pcb field {key!r} must be an array")
    for key in ("parts", "symbols", "footprints", "padstacks", "models"):
        if not isinstance(value["library"].get(key), list):
            raise CLIError("E_PROJECT_INVALID", f"library field {key!r} must be an array")
    for key in ("erc", "drc", "dfm", "results"):
        if not isinstance(value["analysis"].get(key), list):
            raise CLIError("E_PROJECT_INVALID", f"analysis field {key!r} must be an array")
    if not isinstance(value["manufacturing"].get("artifacts"), list):
        raise CLIError("E_PROJECT_INVALID", "manufacturing.artifacts must be an array")
    value["project"] = str(value.get("project") or "mock-project")
    value["revision"] = str(value.get("revision") or "R00")
    # An authored project file missing a refdes is a defect worth rejecting. Observed
    # upstream data is different: a real schematic always carries symbols that have no
    # reference designator — ground, power, ports, borders, title blocks — and wires
    # that were never named. Failing the whole read over those would make every live
    # design unreadable, so separate them out and keep a count instead.
    named_components = []
    unnamed_components = 0
    for component in value["components"]:
        if not isinstance(component, dict) or not component.get("refdes"):
            if not observed:
                raise CLIError("E_PROJECT_INVALID", "each component needs a refdes")
            unnamed_components += 1
            continue
        component["refdes"] = str(component["refdes"])
        named_components.append(component)
    value["components"] = named_components

    named_nets = []
    unnamed_nets = 0
    for net in value["nets"]:
        if not isinstance(net, dict) or not net.get("name"):
            if not observed:
                raise CLIError("E_PROJECT_INVALID", "each net needs a name")
            unnamed_nets += 1
            continue
        net["name"] = str(net["name"])
        named_nets.append(net)
    value["nets"] = named_nets

    if observed and (unnamed_components or unnamed_nets):
        metadata = value.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            value["metadata"] = metadata
        metadata["unnamed"] = {
            "components": unnamed_components,
            "nets": unnamed_nets,
            "note": (
                "symbols without a reference designator and unnamed wires were read "
                "from the design but are not reported as components or nets"
            ),
        }
    return _stringify_identifiers(value)


def normalise_project(project: dict[str, Any], *, observed: bool = False) -> dict[str, Any]:
    """Public normalization entry point for exchange-file adapters.

    Set ``observed`` for data read back from a live tool rather than authored by hand:
    it keeps the read working when the design legitimately contains symbols without a
    reference designator or wires without a name, instead of rejecting the whole project.
    """
    return _normalise(project, observed=observed)


def load_project(path: str | None) -> tuple[dict[str, Any], Path | None]:
    if not path:
        return copy.deepcopy(EMPTY_PROJECT), None
    project_path = Path(path).expanduser().resolve()
    if not project_path.exists():
        return copy.deepcopy(EMPTY_PROJECT), project_path
    try:
        with project_path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise CLIError("E_PROJECT_INVALID", f"cannot read project file: {exc}") from exc
    if not isinstance(raw, dict):
        raise CLIError("E_PROJECT_INVALID", "project file must contain a JSON object")
    return _normalise(raw), project_path


def save_project(project: dict[str, Any], path: Path, backup: bool = False) -> str | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path: str | None = None
    if backup and path.exists():
        backup_file = path.with_suffix(path.suffix + ".bak")
        backup_file.write_bytes(path.read_bytes())
        backup_path = str(backup_file)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(_normalise(project), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise CLIError("E_IO", f"cannot save project file: {exc}", {"path": str(path)}) from exc
    return backup_path


def history_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".history.jsonl")


def append_history(path: Path, entry: dict[str, Any]) -> None:
    record = {"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **entry}
    try:
        with history_path(path).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        try:
            history_path(path).chmod(0o600)
        except OSError:
            pass
    except OSError:
        # History is an audit aid; failure to append must not turn a verified
        # atomic project write into a partial command failure.
        return


def load_history(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not history_path(path).exists():
        return []
    entries: list[dict[str, Any]] = []
    try:
        lines = history_path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            entries.append(value)
    return entries


def restore_backup(path: Path) -> dict[str, Any]:
    backup_file = path.with_suffix(path.suffix + ".bak")
    if not backup_file.exists():
        raise CLIError(
            "E_NOT_FOUND", "no project backup is available for rollback", {"path": str(backup_file)}
        )
    backup_project, _ = load_project(str(backup_file))
    temporary = path.with_suffix(path.suffix + ".rollback.tmp")
    try:
        temporary.write_text(
            json.dumps(_normalise(backup_project), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise CLIError(
            "E_IO", f"cannot restore project backup: {exc}", {"path": str(path)}
        ) from exc
    return backup_project


def snapshot(project: dict[str, Any]) -> dict[str, Any]:
    data = copy.deepcopy(project)
    data["_untrusted"] = [
        "project",
        "sheets",
        "components",
        "nets",
        "connections",
        "interfaces",
        "bom",
        "constraints",
        "pcb",
        "library",
        "analysis",
        "manufacturing",
        "metadata",
    ]
    return data
