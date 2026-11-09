"""The bill of materials of a schematic: its rows, its checks, its CSV file and the comparison with
an earlier BOM.

A component is ``{refdes, part_number, value, sheet, description}``, the description its own
attributes give; ``descriptions`` maps a part number to the central library's description of
it, which comes first.
"""
from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from typing import Any

from . import PlanError

GROUPED_COLUMNS = ["Item", "Quantity", "Reference", "Part Number", "Value", "Description"]
COMPONENT_COLUMNS = ["Item", "Reference", "Part Number", "Value", "Description", "Sheet"]
# The headers a BOM from another tool may give these columns, compared in lower case.
REFERENCE_HEADERS = ("reference", "references", "reference designator", "reference designators", "refdes", "ref des",
                     "designator", "designators", "ref", "位号")
PART_HEADERS = ("part number", "part no", "part no.", "partnumber", "part", "pn", "料号", "物料编码", "物料号")
VALUE_HEADERS = ("value", "值", "参数")


def natural(text: str) -> list[Any]:
    """A sort key that puts R2 before R10."""
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", text or "")]


# ------------------------------------------------------------------ rows


def rows(components: list[dict[str, Any]], descriptions: dict[str, str], *, per_component: bool = False) -> list[dict[str, Any]]:
    """The BOM's rows, in reference order. Grouped, one row per part number and value; a component
    without a part number keeps a row of its own, since nothing says it is the same part as another."""
    ordered = sorted(components, key=lambda c: natural(c["refdes"]))
    if per_component:
        return [{"Item": i, "Reference": c["refdes"], "Part Number": c["part_number"], "Value": c["value"],
                 "Description": _description(c, descriptions), "Sheet": c.get("sheet")}
                for i, c in enumerate(ordered, 1)]
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for component in ordered:
        key = (component["part_number"], component["value"]) if component["part_number"] else ("", f"\0{component['refdes']}")
        groups.setdefault(key, []).append(component)
    out = []
    for i, members in enumerate(groups.values(), 1):
        first = members[0]
        out.append({"Item": i, "Quantity": len(members), "Reference": ", ".join(c["refdes"] for c in members),
                    "Part Number": first["part_number"], "Value": first["value"],
                    "Description": _description(first, descriptions)})
    return out


def _description(component: dict[str, Any], descriptions: dict[str, str]) -> str:
    return descriptions.get(component["part_number"]) or component.get("description", "")


def csv_text(table: list[dict[str, Any]], *, per_component: bool = False) -> str:
    """The rows as CSV text with a header line."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COMPONENT_COLUMNS if per_component else GROUPED_COLUMNS, lineterminator="\r\n")
    writer.writeheader()
    for row in table:
        writer.writerow({k: "" if v is None else v for k, v in row.items()})
    return buffer.getvalue()


# ------------------------------------------------------------------ checks


def check(components: list[dict[str, Any]], library_numbers: set[str] | None) -> list[dict[str, Any]]:
    """The BOM's problems: a component without a part number, a reference designator used twice,
    a part number with several values, and, when ``library_numbers`` is given, a part number the
    central library does not hold."""
    findings: list[dict[str, Any]] = []

    def add(severity: str, rule: str, message: str, objects: list[str]) -> None:
        findings.append({"id": f"{rule}-{len(findings) + 1}", "severity": severity, "rule": rule, "message": message,
                         "objects": objects})

    ordered = sorted(components, key=lambda c: natural(c["refdes"]))
    missing = [c["refdes"] for c in ordered if not c["part_number"]]
    if missing:
        add("error", "missing_part_number", f"{len(missing)} component(s) have no part number: {_names(missing)}.", missing)
    by_refdes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for component in ordered:
        by_refdes[component["refdes"]].append(component)
    for refdes, same in by_refdes.items():
        if len(same) > 1:
            sheets = ", ".join(str(c.get("sheet")) for c in same)
            add("error", "duplicate_refdes", f"{len(same)} components are named {refdes}, on sheets {sheets}.", [refdes])
    by_part: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for component in ordered:
        if component["part_number"]:
            by_part[component["part_number"]][component["value"]].append(component["refdes"])
    for number, values in sorted(by_part.items()):
        if len(values) > 1:
            said = "; ".join(f"{value or '(no value)'} on {_names(refs)}" for value, refs in values.items())
            add("warning", "value_mismatch", f"Part {number} carries {len(values)} different values: {said}.",
                [r for refs in values.values() for r in refs])
    if library_numbers is not None:
        for number, values in sorted(by_part.items()):
            if number not in library_numbers:
                refs = [r for found in values.values() for r in found]
                add("error", "not_in_library", f"The central library holds no part {number}, used by {_names(refs)}.", refs)
    return findings


def _names(refs: list[str], most: int = 12) -> str:
    return ", ".join(refs[:most]) + (f" and {len(refs) - most} more" if len(refs) > most else "")


# ------------------------------------------------------------------ comparison with an earlier BOM


def read_baseline(data: bytes) -> dict[str, dict[str, str]]:
    """An earlier BOM in CSV, as ``{refdes: {part_number, value}}``. It may come from this tool or
    another: the header line is the first one that names a reference column and a part number
    column; a reference cell may list several designators and ranges such as ``R1-R4``. Excel
    writes CSV in the system's code page, so GBK is tried after UTF-8."""
    for encoding in ("utf-8-sig", "gbk"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise PlanError("E_VALIDATION", "The baseline is neither UTF-8 nor GBK text; save it as CSV.")
    lines = list(csv.reader(io.StringIO(text)))
    for index, header in enumerate(lines[:20]):
        names = [cell.strip().casefold() for cell in header]
        ref = next((i for i, n in enumerate(names) if n in REFERENCE_HEADERS), None)
        part = next((i for i, n in enumerate(names) if n in PART_HEADERS), None)
        if ref is not None and part is not None:
            value = next((i for i, n in enumerate(names) if n in VALUE_HEADERS), None)
            break
    else:
        raise PlanError("E_VALIDATION", "The baseline has no header line with a reference column and a part number column, "
                        "such as Reference and Part Number.")
    found: dict[str, dict[str, str]] = {}
    for line in lines[index + 1:]:
        if len(line) <= max(ref, part):
            continue
        for refdes in designators(line[ref]):
            found[refdes] = {"part_number": line[part].strip(),
                             "value": line[value].strip() if value is not None and value < len(line) else ""}
    return found


def designators(cell: str) -> list[str]:
    """``"R1, R2 R5;R7-R9"`` as R1, R2, R5, R7, R8, R9. Chinese commas and ``、`` separate as commas
    do, and a range may be written ``R7~R9`` or ``R7 - R9``."""
    out: list[str] = []
    cell = re.sub(r"\s*[-~～–—]\s*", "-", cell.strip())
    for token in re.split(r"[,;，；、\s]+", cell):
        span = re.fullmatch(r"([A-Za-z_]+)(\d+)-(?:\1)?(\d+)", token)
        if span and int(span.group(3)) >= int(span.group(2)) and int(span.group(3)) - int(span.group(2)) <= 1000:
            out += [f"{span.group(1)}{n}" for n in range(int(span.group(2)), int(span.group(3)) + 1)]
        elif token:
            out.append(token)
    return out


def compare(components: list[dict[str, Any]], baseline: dict[str, dict[str, str]], *, values: bool) -> dict[str, Any]:
    """What changed since the baseline, by reference designator: added, removed, and changed in
    part number or, when the baseline has a value column, in value."""
    now = {c["refdes"]: c for c in components}
    added = [{"refdes": r, "part_number": now[r]["part_number"]} for r in sorted(set(now) - set(baseline), key=natural)]
    removed = [{"refdes": r, "part_number": baseline[r]["part_number"]} for r in sorted(set(baseline) - set(now), key=natural)]
    changed = []
    for refdes in sorted(set(now) & set(baseline), key=natural):
        fields = [("part_number", "part_number")] + ([("value", "value")] if values else [])
        for field, key in fields:
            before, after = baseline[refdes][key], now[refdes][field]
            if before != after:
                changed.append({"refdes": refdes, "field": field, "before": before, "after": after})
    return {"added": added, "removed": removed, "changed": changed}


def baseline_has_values(baseline: dict[str, dict[str, str]]) -> bool:
    return any(entry["value"] for entry in baseline.values())
