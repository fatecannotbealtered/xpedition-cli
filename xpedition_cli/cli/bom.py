"""bom export | check: the bill of materials, from the schematic Designer holds."""

from __future__ import annotations

from typing import Any

from ..changeset import bom_rows
from ..errors import CLIError
from .common import input_file, page, project_file, read_json_file, reader, write_json_output

_KEY_FIELDS = ("internal_part_no", "value", "package", "description", "manufacturer", "mpn")


def _rows(options: dict[str, Any], command: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    project = project_file(options, command)
    design, _ = reader(options).load(str(project), domain="schematic")
    rows = [
        {key: value for key, value in row.items() if key not in {"source", "revision"}}
        for row in bom_rows(design)
    ]
    return design, rows


def _grouped(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        part = str(row.get("internal_part_no") or row.get("mpn") or "")
        key = part or f"(no part number) {row.get('value', '')}"
        group = groups.setdefault(
            key,
            {
                "part_number": part or None,
                "quantity": 0,
                "refdes": [],
                **{
                    field: row.get(field)
                    for field in ("value", "package", "description", "manufacturer", "mpn")
                },
            },
        )
        group["quantity"] += int(row.get("quantity") or 1)
        group["refdes"].append(str(row.get("refdes", "")))
    return list(groups.values())


def _changes(rows: list[dict[str, Any]], baseline: list[dict[str, Any]]) -> dict[str, Any]:
    now = {str(row.get("refdes")): row for row in rows}
    before = {str(row.get("refdes")): row for row in baseline if isinstance(row, dict)}
    changed = []
    for refdes in sorted(now.keys() & before.keys()):
        fields = {
            field: {"before": before[refdes].get(field), "after": now[refdes].get(field)}
            for field in _KEY_FIELDS
            if before[refdes].get(field) != now[refdes].get(field)
        }
        if fields:
            changed.append({"refdes": refdes, "fields": fields})
    return {
        "added": sorted(now.keys() - before.keys()),
        "removed": sorted(before.keys() - now.keys()),
        "changed": changed,
    }


def export(options: dict[str, Any]) -> dict[str, Any]:
    design, rows = _rows(options, "bom export")
    baseline_changes = None
    if options.get("baseline") is not None:
        data = read_json_file(
            input_file(options, "baseline", "bom export", "the baseline file"), "the baseline file"
        )
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            data = data["data"]
        previous = data.get("rows") if isinstance(data, dict) else None
        if not isinstance(previous, list):
            raise CLIError(
                "E_VALIDATION", "--baseline is a file an earlier bom export --output wrote"
            )
        baseline_changes = _changes(rows, previous)
    items = _grouped(rows) if options.get("group") else rows
    path = write_json_output(options, "bom export", {"project": design["project"], "rows": rows})
    result = page(design, items, options, ["project", "items", "changes"])
    result["grouped"] = bool(options.get("group"))
    result["parts"] = len(rows)
    if baseline_changes is not None:
        result["changes"] = baseline_changes
    if path:
        result["path"] = path
    return result


def check(options: dict[str, Any]) -> dict[str, Any]:
    design, rows = _rows(options, "bom check")
    issues: list[dict[str, Any]] = []
    for row in rows:
        if not row.get("internal_part_no"):
            issues.append(
                {"kind": "missing_part_number", "severity": "high", "refdes": row.get("refdes")}
            )
    seen: dict[str, int] = {}
    for row in rows:
        seen[str(row.get("refdes"))] = seen.get(str(row.get("refdes")), 0) + 1
    for refdes, count in sorted(seen.items()):
        if count > 1:
            issues.append(
                {"kind": "duplicate_refdes", "severity": "high", "refdes": refdes, "count": count}
            )
    by_part: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("internal_part_no"):
            by_part.setdefault(str(row["internal_part_no"]), []).append(row)
    for part, members in sorted(by_part.items()):
        for field in ("value", "package"):
            values = sorted({str(member.get(field) or "") for member in members})
            if len(values) > 1:
                issues.append(
                    {
                        "kind": f"inconsistent_{field}",
                        "severity": "medium",
                        "part_number": part,
                        "values": values,
                        "refdes": sorted(str(member.get("refdes")) for member in members),
                    }
                )
    return {
        "project": design["project"],
        "parts": len(rows),
        "part_numbers": len(by_part),
        "valid": not issues,
        "issues": issues,
        "_untrusted": ["project", "issues"],
    }
