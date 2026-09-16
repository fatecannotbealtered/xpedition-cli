from __future__ import annotations

import csv
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from ..errors import CLIError
from ..models import normalise_project


class ExchangeBackend:
    """Safe parser boundary for supported exported Xpedition exchange files."""

    name = "exchange_files"
    formats = ("json", "csv", "bom", "ipc2581", "pdf", "edn", "odbpp")

    @staticmethod
    def _format(path: Path) -> str:
        suffix = path.suffix.casefold()
        if suffix == ".json":
            return "json"
        if suffix == ".csv":
            return "csv"
        if suffix == ".bom":
            return "bom"
        if suffix in {".ipc", ".ipc2581"}:
            return "ipc2581"
        if suffix == ".xml":
            return "ipc2581"
        return suffix.removeprefix(".") or "unknown"

    @staticmethod
    def _rows_to_project(rows: list[dict[str, Any]], source: Path) -> dict[str, Any]:
        components = []
        for row in rows:
            refdes = row.get("refdes") or row.get("reference") or row.get("designator")
            if not refdes:
                continue
            components.append(
                {
                    "refdes": str(refdes),
                    "internal_part_no": row.get("internal_part_no")
                    or row.get("part_number")
                    or row.get("part_no")
                    or row.get("mpn")
                    or "",
                    "mpn": row.get("mpn")
                    or row.get("manufacturer_part_number")
                    or row.get("part_number")
                    or "",
                    "manufacturer": row.get("manufacturer") or "",
                    "description": row.get("description") or "",
                    "value": row.get("value") or "",
                    "package": row.get("package") or row.get("footprint") or "",
                    "variant": row.get("variant"),
                    "dnp": str(row.get("dnp", "")).casefold() in {"1", "true", "yes"},
                }
            )
        return normalise_project(
            {
                "project": source.stem,
                "revision": "R00",
                "components": components,
                "metadata": {
                    "format_version": "1",
                    "backend": "exchange_files",
                    "source": str(source),
                },
            }
        )

    @staticmethod
    def _parse_ipc2581(source: Path) -> dict[str, Any]:
        try:
            root = ET.parse(source).getroot()
        except (OSError, ET.ParseError) as exc:
            raise CLIError(
                "E_PROJECT_INVALID", f"cannot parse IPC-2581 XML: {exc}", {"path": str(source)}
            ) from exc

        def local_name(tag: str) -> str:
            return tag.rsplit("}", 1)[-1].casefold()

        def attributes(element: ET.Element) -> dict[str, str]:
            return {local_name(key): str(value) for key, value in element.attrib.items()}

        if "ipc" not in local_name(root.tag):
            raise CLIError(
                "E_PROJECT_INVALID",
                "XML input is not identified as IPC-2581",
                {"path": str(source)},
            )

        rows: list[dict[str, Any]] = []
        nets: list[dict[str, Any]] = []
        connections: dict[str, list[str]] = {}
        for element in root.iter():
            tag = local_name(element.tag)
            attrs = attributes(element)
            if tag in {"component", "bomitem"}:
                rows.append(
                    {
                        "refdes": attrs.get("refdes") or attrs.get("designator"),
                        "part_number": attrs.get("partname")
                        or attrs.get("partnumber")
                        or attrs.get("part_no"),
                        "mpn": attrs.get("manufacturerpartnumber") or attrs.get("mpn"),
                        "manufacturer": attrs.get("manufacturer"),
                        "description": attrs.get("description"),
                        "value": attrs.get("value"),
                        "package": attrs.get("package") or attrs.get("footprint"),
                    }
                )
            elif tag == "net":
                name = attrs.get("name") or attrs.get("netname")
                if name:
                    if not any(str(item.get("name")) == str(name) for item in nets):
                        nets.append({"name": name, "type": attrs.get("type", "signal")})
                    connections.setdefault(name, [])
                    for child in element.iter():
                        child_tag = local_name(child.tag)
                        if child_tag not in {"pin", "node"}:
                            continue
                        child_attrs = attributes(child)
                        refdes = (
                            child_attrs.get("componentref")
                            or child_attrs.get("component")
                            or child_attrs.get("refdes")
                        )
                        pin_number = (
                            child_attrs.get("pin")
                            or child_attrs.get("pinnumber")
                            or child_attrs.get("number")
                        )
                        if refdes and pin_number:
                            pin_id = f"{refdes}.{pin_number}"
                            if pin_id not in connections[name]:
                                connections[name].append(pin_id)
            elif tag in {"pin", "node"}:
                net_name = attrs.get("net") or attrs.get("netname")
                refdes = attrs.get("componentref") or attrs.get("component") or attrs.get("refdes")
                pin_number = attrs.get("pin") or attrs.get("pinnumber") or attrs.get("number")
                if net_name and refdes and pin_number:
                    connections.setdefault(net_name, []).append(f"{refdes}.{pin_number}")
        project = ExchangeBackend._rows_to_project(rows, source)
        project["nets"] = nets
        project["connections"] = [
            {"net": name, "pins": pins} for name, pins in connections.items() if pins
        ]
        return normalise_project(project)

    def parse(self, input_path: str) -> tuple[dict[str, Any], Path, str]:
        source = Path(input_path).expanduser().resolve()
        if not source.exists():
            raise CLIError(
                "E_NOT_FOUND", "exchange input file was not found", {"path": str(source)}
            )
        format_name = self._format(source)
        if format_name not in {"json", "csv", "bom", "ipc2581"}:
            raise CLIError(
                "E_BACKEND_UNAVAILABLE",
                f"ExchangeBackend does not parse {format_name!r} in this phase",
                {"format": format_name, "supported_formats": ["json", "csv", "bom", "ipc2581"]},
            )
        try:
            if format_name == "json":
                value = json.loads(source.read_text(encoding="utf-8"))
                if isinstance(value, dict) and isinstance(value.get("components"), list):
                    project = normalise_project(value)
                elif isinstance(value, dict) and isinstance(value.get("items"), list):
                    project = self._rows_to_project(value["items"], source)
                elif isinstance(value, list):
                    project = self._rows_to_project(value, source)
                else:
                    raise CLIError(
                        "E_PROJECT_INVALID", "exchange JSON must contain components or BOM rows"
                    )
            elif format_name == "ipc2581":
                project = self._parse_ipc2581(source)
            else:
                with source.open("r", encoding="utf-8-sig", newline="") as handle:
                    rows = [dict(row) for row in csv.DictReader(handle)]
                project = self._rows_to_project(rows, source)
            project["metadata"]["backend"] = self.name
            project["metadata"]["source"] = str(source)
        except CLIError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, csv.Error) as exc:
            raise CLIError(
                "E_PROJECT_INVALID", f"cannot parse exchange input: {exc}", {"path": str(source)}
            ) from exc
        return project, source, format_name

    def status(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "available": True,
            "formats": list(self.formats),
            "supported_formats": ["json", "csv", "bom", "ipc2581"],
            "reason": (
                "JSON/CSV/BOM/IPC-2581 exchange parsing is available; PDF/EDN/ODB++ remain planned"
            ),
        }

    def require_implemented(self) -> None:
        """Compatibility hook; format support is checked by :meth:`parse`."""
        return None
