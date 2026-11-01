"""Independent checks of recorded getters against the unchanged QA requirements."""

from __future__ import annotations

import math
import re
from pathlib import Path


class Stop(RuntimeError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code, self.details = code, details or {}


def require(value, code, message, details=None):
    if not value:
        raise Stop(code, message, details)


def within(path: Path, root: Path):
    resolved, base = path.resolve(), root.resolve()
    require(
        resolved == base or base in resolved.parents,
        "E_SCOPE",
        "Path escapes the fixed root",
        {"path": str(path)},
    )
    for item in (path, *path.parents):
        if item.exists():
            require(
                not item.is_symlink()
                and not (getattr(item.stat(), "st_file_attributes", 0) & 1024),
                "E_SCOPE",
                "Reparse points are not accepted",
                {"path": str(item)},
            )
        if item == root:
            break
    return resolved


def number(value, label):
    require(
        type(value) in (int, float) and math.isfinite(value),
        "E_READBACK",
        "Required getter is absent or nonnumeric",
        {"getter": label, "actual": value},
    )
    return float(value)


def close(actual, expected, tolerance, label):
    actual = number(actual, label)
    require(
        abs(actual - expected) <= tolerance,
        "E_READBACK",
        "Native value differs from frozen expectation",
        {"getter": label, "actual": actual, "expected": expected, "tolerance": tolerance},
    )


def positive_ccw(documents):
    """Require a statement about positive angles, not a stray mention of CCW."""
    found = False
    for doc in documents:
        text = " ".join(doc.get("documentation", "").lower().split())
        if re.search(r"positive[^.;]{0,100}(?<!counter )(?<!counter-)(?<!counter)clockwise", text):
            return False
        found = found or bool(re.search(r"positive[^.;]{0,100}counter[ -]?clockwise", text))
    return found


def schematic(snapshot, expected):
    rows = snapshot.get("components", [])
    require(
        len(rows) == 2 and {r.get("refdes") for r in rows} == {"J1", "R1"},
        "E_READBACK",
        "Schematic instance set differs from frozen requirements",
        {"actual_refdes": [r.get("refdes") for r in rows]},
    )
    require(
        snapshot.get("project") == expected["planned_native_binding"]["design"],
        "E_READBACK",
        "Native design identity does not match the predeclared binding",
        {"actual": snapshot.get("project")},
    )
    require(
        len(snapshot.get("sheets", [])) == 1 and snapshot["sheets"][0].get("number") == 1,
        "E_READBACK",
        "Expected exactly one native sheet",
    )
    pins, nets, uid = set(), {}, set()
    for row in rows:
        require(
            row.get("uid") and row["uid"] not in uid and row.get("sheet") == 1,
            "E_READBACK",
            "Missing/duplicate native UID or wrong sheet",
            {"component": row},
        )
        uid.add(row["uid"])
        require(
            len(row.get("pins", [])) == 2,
            "E_READBACK",
            "Expected two observed native pin records",
            {"refdes": row["refdes"]},
        )
        for pin in row["pins"]:
            identity = (
                f"{expected['logical_root']}/{expected['logical_page']}/"
                f"{row['refdes']}.{pin.get('number')}"
            )
            require(
                identity not in pins and pin.get("net"),
                "E_READBACK",
                "Missing connection or duplicate pin",
                {"pin": identity},
            )
            pins.add(identity)
            nets.setdefault(pin["net"], set()).add(identity)
    expected_sets = {name: set(members) for name, members in expected["nets"].items()}
    require(
        nets == expected_sets,
        "E_READBACK",
        "Native pin-to-net sets differ from the independent frozen file",
        {
            "actual": {k: sorted(v) for k, v in nets.items()},
            "expected": {k: sorted(v) for k, v in expected_sets.items()},
        },
    )
    return {
        "status": "passed",
        "source": "native component UID, sheet and GetConnections getters",
        "logical_binding": {
            "root": expected["logical_root"],
            "page": expected["logical_page"],
            "native_design": snapshot["project"],
            "native_sheet": snapshot["sheets"][0]["name"],
        },
        "native_uids": sorted(uid),
        "nets": {k: sorted(v) for k, v in nets.items()},
    }


def library(record, input_part):
    part, cell = record.get("part") or {}, record.get("cell") or {}
    require(
        part.get("number") == input_part["number"] and cell and record.get("symbols"),
        "E_READBACK",
        "Native library export is missing a part/cell/symbol",
        {"part": input_part["number"]},
    )
    require(
        not any(f.get("severity") == "high" for f in record.get("findings", [])),
        "E_READBACK",
        "Native library readback has high-severity findings",
        {"findings": record.get("findings")},
    )
    actual_map = {pin.get("name"): pin.get("number") for pin in part.get("pins", [])}
    require(
        actual_map == input_part["pinmap"],
        "E_READBACK",
        "Exported symbol-to-cell pin map differs from the frozen part file",
        {"actual": actual_map, "expected": input_part["pinmap"]},
    )
    return {
        "part": part,
        "cell": cell,
        "symbols": record["symbols"],
        "padstacks": record.get("padstacks"),
        "source": "stock database exporters and symbol files; fresh CLI library inspect",
    }


def pcb(record, geometry, connections, library_records):
    rows = record.get("components", [])
    require(
        len(rows) == 2 and {r.get("refdes") for r in rows} == {"J1", "R1"},
        "E_READBACK",
        "PCB must contain exactly the two frozen instances",
        {"actual": rows},
    )
    actual_nets = {}
    for row in rows:
        name = row["refdes"]
        goal = geometry["placements"][name]
        lib = library_records["QA_J1X02_P254" if name == "J1" else "QA_R0805_10K"]
        require(
            row.get("cell") == lib["part"]["cell"],
            "E_READBACK",
            "Library and PCB instance cell identities differ",
            {"actual": row.get("cell"), "library_cell": lib["part"]["cell"]},
        )
        require(
            row.get("placed") is True and row.get("bottom") is False,
            "E_READBACK",
            "Native placed/top-side getter is absent or false",
            {"actual": row},
        )
        tolerance = geometry["tolerances"]["position_mm"]
        close(row.get("x"), goal["origin_mm"][0], tolerance, name + ".x")
        close(row.get("y"), goal["origin_mm"][1], tolerance, name + ".y")
        close(
            row.get("rotation"),
            goal["rotation_deg"],
            geometry["tolerances"]["angle_deg"],
            name + ".rotation",
        )
        require(
            len(row.get("pins", [])) == 2,
            "E_READBACK",
            "Missing or extra PCB pin records",
            {"refdes": name},
        )
        for pin in row["pins"]:
            label = str(pin.get("number"))
            require(
                label in goal["pins_mm"] and pin.get("net"),
                "E_READBACK",
                "PCB pin identity/connection is missing",
                {"pin": pin},
            )
            close(pin.get("x"), goal["pins_mm"][label][0], tolerance, name + "." + label + ".x")
            close(pin.get("y"), goal["pins_mm"][label][1], tolerance, name + "." + label + ".y")
            actual_nets.setdefault(pin["net"], set()).add(
                f"{connections['logical_root']}/{connections['logical_page']}/{name}.{label}"
            )
    require(
        actual_nets == {k: set(v) for k, v in connections["nets"].items()},
        "E_READBACK",
        "PCB native connectivity differs from frozen requirements",
        {"actual": {k: sorted(v) for k, v in actual_nets.items()}},
    )
    return {
        "status": "passed",
        "source": "native component/pin getters in explicit mm/degrees",
        "nets": {k: sorted(v) for k, v in actual_nets.items()},
        "scope": (
            "two top-side instances, pin mapping, pin centers, origin and orientation; "
            "pad shape/drill/layer correspondence is a separate required observation"
        ),
    }


def pad_correspondence(record, expected):
    require(
        record.get("layer_count") == 4,
        "E_PAD_COVERAGE",
        "Expected four observed board copper layers",
        {"actual": record.get("layer_count")},
    )
    checked = []
    for row in record["components"]:
        refdes = row["refdes"]
        goal = expected["placements"][refdes]
        for pin in row["pins"]:
            pin_id = pin["number"]
            copper, fabrication = [], set()
            for pad in pin.get("pads", []):
                meanings = " ".join(pad.get("type_enum_names", [])).lower()
                if "mask" in meanings:
                    kind = "mask"
                elif "paste" in meanings:
                    kind = "paste"
                elif "copper" in meanings or "signal" in meanings:
                    kind = "copper"
                else:
                    raise Stop(
                        "E_PAD_LAYER_CONTRACT",
                        "Current pad getter/type enum does not establish copper/mask/paste meaning",
                        {"refdes": refdes, "pin": pin_id, "actual_pad": pad},
                    )
                fabrication.add(kind)
                if kind != "copper":
                    require(
                        pad.get("layer") == 1
                        or any("top" in name.lower() for name in pad.get("type_enum_names", [])),
                        "E_PAD_COVERAGE",
                        "SMD fabrication pad top-side identity is unavailable",
                        {"pad": pad},
                    )
                    continue
                copper.append(pad["layer"])
                require(
                    pad.get("geometries"),
                    "E_PAD_COVERAGE",
                    "Native copper pad geometries are missing",
                )
                for shape in pad["geometries"]:
                    require(
                        shape is not None, "E_PAD_COVERAGE", "Native geometry could not be read"
                    )
                    position = goal["pins_mm"][pin_id]
                    tolerance = expected["tolerances"]["position_mm"]
                    if goal["pad_shapes"][pin_id] == "round":
                        circle = shape.get("circle")
                        require(
                            isinstance(circle, list) and len(circle) == 3,
                            "E_PAD_COVERAGE",
                            "Expected an observed circular copper pad",
                            {"shape": shape},
                        )
                        close(circle[0], position[0], tolerance, "pad.circle.x")
                        close(circle[1], position[1], tolerance, "pad.circle.y")
                        close(circle[2] * 2, 1.7, tolerance, "pad.circle.diameter")
                    else:
                        path = shape.get("path")
                        require(
                            isinstance(path, list)
                            and len(path) >= 4
                            and all(len(p) == 3 and p[2] == 0 for p in path),
                            "E_PAD_COVERAGE",
                            "Expected an observed rectangle without rounded corners",
                            {"shape": shape},
                        )
                        xs, ys = (
                            [number(p[0], "pad.x") for p in path],
                            [number(p[1], "pad.y") for p in path],
                        )
                        width, height = (1.7, 1.7) if refdes == "J1" else goal["pad_size_mm"]
                        close(max(xs) - min(xs), width, tolerance, "pad.width")
                        close(max(ys) - min(ys), height, tolerance, "pad.height")
                        close((max(xs) + min(xs)) / 2, position[0], tolerance, "pad.center.x")
                        close((max(ys) + min(ys)) / 2, position[1], tolerance, "pad.center.y")
                        require(
                            all(
                                min(abs(x - min(xs)), abs(x - max(xs))) <= tolerance
                                and min(abs(y - min(ys)), abs(y - max(ys))) <= tolerance
                                for x, y, _ in path
                            ),
                            "E_PAD_COVERAGE",
                            "Path is not the required rectangle",
                        )
            if refdes == "J1":
                require(
                    set(copper) == {1, 2, 3, 4},
                    "E_PAD_COVERAGE",
                    "Header copper pads do not cover all four board layers",
                    {"layers": copper},
                )
                require(
                    len(pin.get("holes", [])) == 1,
                    "E_PAD_COVERAGE",
                    "Header requires one actual drill per pin",
                )
                hole = pin["holes"][0]
                close(
                    hole.get("drill_mm"), 1.0, expected["tolerances"]["position_mm"], "header.drill"
                )
                require(
                    hole.get("plated") is True,
                    "E_PAD_COVERAGE",
                    "Required plated-hole getter is unavailable or false",
                    {"hole": hole},
                )
            else:
                require(
                    set(copper) == {1}
                    and fabrication == {"copper", "mask", "paste"}
                    and pin.get("holes") == [],
                    "E_PAD_COVERAGE",
                    "SMD copper/mask/paste or no-drill observation is incomplete",
                    {"layers": copper, "types": sorted(fabrication), "holes": pin.get("holes")},
                )
            checked.append(
                {
                    "refdes": refdes,
                    "pin": pin_id,
                    "copper_layers": sorted(set(copper)),
                    "fabrication_types": sorted(fabrication),
                }
            )
    return {
        "status": "passed",
        "source": "native Pads/Geometries/Holes getters plus live type-library enum meaning",
        "checked": checked,
    }


def orphan(before, after, error, trace):
    def records(snapshot):
        rows = snapshot.get("components", []) + snapshot.get("unnamed_components", [])
        require(
            all(r.get("uid") for r in rows),
            "E_READBACK",
            "Cannot enumerate all baseline/native orphan IDs",
        )
        require(len({r["uid"] for r in rows}) == len(rows), "E_READBACK", "Native UID collision")
        return {r["uid"]: r for r in rows}

    old, new = records(before), records(after)
    added = set(new) - set(old)
    require(
        len(added) == 1 and set(old) <= set(new),
        "E_READBACK",
        "Expected exactly one added native object and no baseline removal",
        {"added": sorted(added), "removed": sorted(set(old) - set(new))},
    )
    require(
        all(old[key] == new[key] for key in old), "E_READBACK", "A baseline native object changed"
    )
    actual = new[next(iter(added))]
    details = error.get("details") or {}
    require(
        trace.get("real_add_calls") == 1 and trace.get("setter_faults") == 1,
        "E_READBACK",
        "Fault was not injected once after one real placement",
    )
    require(
        details.get("orphan_placed") is True
        and (details.get("orphan_actual") or {}).get("uid") == actual["uid"],
        "E_READBACK",
        "Adapter error does not identify the independently enumerated orphan",
    )
    require(
        actual.get("refdes") != "J_ORPHAN",
        "E_READBACK",
        "The injected Refdes assignment unexpectedly applied",
    )
    close(actual.get("x"), 500, 0, "orphan.native_x")
    close(actual.get("y"), 400, 0, "orphan.native_y")
    require(
        before.get("connections") == after.get("connections"),
        "E_READBACK",
        "Baseline connections changed",
    )
    require(
        all(not pin.get("net") for pin in actual.get("pins", [])),
        "E_READBACK",
        "New orphan unexpectedly joined a baseline net",
    )
    return {
        "failure_handling_verdict": "passed",
        "engineering_task_outcome": "partial",
        "fault_mode": "fault_injected_after_real_AddPartInstance_before_Refdes_setter",
        "actual": actual,
        "cleanup": "not_attempted; observed orphan retained in owned scratch window",
        "persistence": "unknown; no save performed after injection",
        "automatic_replay": False,
    }
