"""A first placement for a board that follows the connections.

Forward annotation delivers components unplaced, and an unplaced component is
invisible on the board. This lays every part out the way a reviewer expects a
first draft to look: each IC gets a cluster with the parts that connect to it
(decoupling capacitors nearest), the clusters sit in rows by schematic sheet,
connectors stand on the left or right edge next to the cluster they serve,
test points line the bottom edge, and every sheet gets a zone label. Positions
are millimetres, everything goes on the top side, connectors on an edge are
turned by 90° so their pin row runs along the edge. It is a starting point for
a person, not a layout.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Any

MARGIN = 2.0  # mm from the outline to any part (the convention wants ≥ 1 mm)
GAP = 0.6  # mm between neighbouring placement outlines inside a cluster (courtyards already
# carry their own margin; a hand layout puts 0603s about this close)
CLUSTER_GAP = 1.5  # mm between clusters and around edge bands
LABEL_HEIGHT = 1.2  # mm silkscreen text height for zone labels
LABEL_CHAR_WIDTH = 1.18  # mm per character at that height (measured: "TEST ZONE" is 10.6 mm)
MIN_SIZE = 0.5  # mm; a part without a measured footprint still takes a slot
TEXT_ALLOWANCE = 1.2  # mm above a part for its reference designator: 1 mm text plus room
REFDES_CHAR_WIDTH = 0.8  # mm per character of a 1 mm reference designator
REFDES_PADDING = 0.4  # mm kept beside a reference designator so two never touch
GRID = 0.5  # mm; the CLI snaps every centre to this grid, as a hand layout would
ANCHOR_PREFIXES = ("U",)
CONNECTOR_PREFIXES = ("J", "BT", "X", "P")
TESTPOINT_PREFIXES = ("TP", "H")
POWER_NET_PINS = 7  # a net with this many pins or more behaves like a supply
PREFIX_ORDER = ("U", "Q", "J", "BT", "D", "L", "R", "RT", "C", "TP")


@dataclass
class Part:
    refdes: str
    width: float
    height: float
    cell: str = ""
    # the cell origin relative to the centre of the footprint's bounding box
    offset_x: float = 0.0
    offset_y: float = 0.0
    # room kept above the footprint for its reference designator (Layout's extents
    # stop at the placement outline; the text sits above it)
    label: float = TEXT_ALLOWANCE
    # pin name -> offset of the pin from the footprint's centre, unturned (mm)
    pins: dict[str, tuple[float, float]] = field(default_factory=dict)


@dataclass
class Net:
    name: str
    pins: list[tuple[str, str]]  # (refdes, pin)
    power: bool = False


@dataclass
class Placement:
    refdes: str
    cell: str
    group: str
    cluster: str
    x: float
    y: float
    width: float
    height: float
    rotation: float = 0.0
    inside: bool = True
    role: str = "part"

    def as_dict(self) -> dict[str, Any]:
        return {
            "refdes": self.refdes,
            "cell": self.cell,
            "group": self.group,
            "cluster": self.cluster,
            "role": self.role,
            "x": self.x,
            "y": self.y,
            "width": round(self.width, 3),
            "height": round(self.height, 3),
            "rotation": self.rotation,
            "inside": self.inside,
        }


@dataclass
class Label:
    text: str
    x: float
    y: float
    group: str

    def as_dict(self) -> dict[str, Any]:
        return {"text": self.text, "x": self.x, "y": self.y, "group": self.group}


@dataclass
class Plan:
    placements: list[Placement] = field(default_factory=list)
    labels: list[Label] = field(default_factory=list)
    clusters: dict[str, list[str]] = field(default_factory=dict)

    def digest(self) -> str:
        text = "\n".join(
            [f"{p.refdes}:{p.x}:{p.y}:{p.rotation}" for p in self.placements]
            + [f"{lab.text}@{lab.x}:{lab.y}" for lab in self.labels]
        )
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    def summary(self) -> dict[str, Any]:
        groups: dict[str, int] = {}
        for item in self.placements:
            groups[item.group] = groups.get(item.group, 0) + 1
        return {
            "count": len(self.placements),
            "groups": groups,
            "clusters": {key: len(value) for key, value in self.clusters.items()},
            "outside": [item.refdes for item in self.placements if not item.inside],
            "labels": [lab.text for lab in self.labels],
        }


# -- reference designators ---------------------------------------------------------------


def _split(refdes: str) -> tuple[str, int]:
    match = re.match(r"^([A-Za-z]+)(\d+)", refdes)
    if not match:
        return refdes.upper(), 0
    return match.group(1).upper(), int(match.group(2))


def group_of(refdes: str) -> str:
    """The sheet a reference designator encodes: `C201` → `2`; `R5` → `other`."""
    match = re.match(r"^[A-Za-z]+(\d+)", refdes)
    if match and len(match.group(1)) >= 3:
        return match.group(1)[:-2]
    return "other"


def sort_key(refdes: str) -> tuple[int, int, str]:
    prefix, number = _split(refdes)
    order = PREFIX_ORDER.index(prefix) if prefix in PREFIX_ORDER else len(PREFIX_ORDER)
    return order, number, refdes


def role_of(refdes: str) -> str:
    prefix, _number = _split(refdes)
    if prefix in ANCHOR_PREFIXES:
        return "anchor"
    if prefix in CONNECTOR_PREFIXES:
        return "connector"
    if prefix in TESTPOINT_PREFIXES:
        return "testpoint"
    return "part"


def _group_order(key: str) -> tuple[int, str]:
    return (0, key.zfill(4)) if key.isdigit() else (1, key)


def cluster_group(cluster: str) -> str:
    """The sheet of a cluster: its anchor's, or the sheet named by an IC-less `sheet N`."""
    if cluster.startswith("sheet "):
        return cluster[6:]
    return group_of(cluster)


# -- connectivity -----------------------------------------------------------------------


def _weights(
    parts: list[Part], nets: list[Net]
) -> tuple[dict[str, dict[str, int]], dict[str, set[str]]]:
    """Pairwise connection weights over signal nets, and each part's supply nets."""
    known = {part.refdes for part in parts}
    weights: dict[str, dict[str, int]] = {refdes: {} for refdes in known}
    supplies: dict[str, set[str]] = {refdes: set() for refdes in known}
    for net in nets:
        members = sorted({refdes for refdes, _pin in net.pins if refdes in known})
        if net.power or len(members) >= POWER_NET_PINS:
            for refdes in members:
                supplies[refdes].add(net.name)
            continue
        bonus = 2 if len(members) == 2 else 1
        for index, first in enumerate(members):
            for second in members[index + 1 :]:
                weights[first][second] = weights[first].get(second, 0) + bonus
                weights[second][first] = weights[second].get(first, 0) + bonus
    return weights, supplies


def _assign_clusters(parts: list[Part], nets: list[Net]) -> dict[str, str]:
    """Which anchor each part belongs to: the IC it connects to most, a decoupling
    capacitor's supply partner on its own sheet, or else the first IC of its sheet."""
    weights, supplies = _weights(parts, nets)
    anchors = sorted(
        (part.refdes for part in parts if role_of(part.refdes) == "anchor"), key=sort_key
    )
    by_sheet: dict[str, list[str]] = {}
    for anchor in anchors:
        by_sheet.setdefault(group_of(anchor), []).append(anchor)
    assignment: dict[str, str] = {anchor: anchor for anchor in anchors}
    decoupling: dict[str, int] = {anchor: 0 for anchor in anchors}
    for part in sorted(parts, key=lambda item: sort_key(item.refdes)):
        refdes = part.refdes
        if refdes in assignment:
            continue
        sheet = group_of(refdes)
        best: str | None = None
        best_weight = 0
        for anchor in anchors:
            weight = weights[refdes].get(anchor, 0)
            same_sheet_wins = (
                weight == best_weight
                and weight
                and best is not None
                and group_of(anchor) == sheet
                and group_of(best) != sheet
            )
            if weight > best_weight or same_sheet_wins:
                best, best_weight = anchor, weight
        if best is None and supplies[refdes] and _split(refdes)[0] == "C":
            # a decoupling capacitor: spread the sheet's capacitors over the ICs that
            # share its supply, so every IC gets one before any IC gets a second
            candidates = [
                anchor
                for anchor in by_sheet.get(sheet, []) or anchors
                if supplies[anchor] & supplies[refdes]
            ]
            if candidates:
                best = min(candidates, key=lambda anchor: (decoupling[anchor], sort_key(anchor)))
                decoupling[best] += 1
        if best is None:
            same_sheet = by_sheet.get(sheet)
            best = same_sheet[0] if same_sheet else (anchors[0] if anchors else f"sheet {sheet}")
        assignment[refdes] = best
    return assignment


# -- geometry ---------------------------------------------------------------------------


def _label_width(part: Part) -> float:
    return len(part.refdes) * REFDES_CHAR_WIDTH + REFDES_PADDING if part.label else 0.0


def _size(part: Part, rotation: float = 0.0) -> tuple[float, float]:
    """The slot a part takes: its footprint plus the room for its reference designator
    above it (which is often wider than a chip part), turned with the part."""
    width = max(part.width, MIN_SIZE, _label_width(part))
    height = max(part.height, MIN_SIZE) + part.label
    return (height, width) if rotation % 180 == 90 else (width, height)


def _footprint(part: Part, rotation: float = 0.0) -> tuple[float, float]:
    width, height = max(part.width, MIN_SIZE), max(part.height, MIN_SIZE)
    return (height, width) if rotation % 180 == 90 else (width, height)


def _shift(part: Part, rotation: float = 0.0) -> tuple[float, float]:
    """From the slot's centre to the footprint's centre: the text room lies on the
    part's "up" side, which the rotation turns with it (counter-clockwise)."""
    half = part.label / 2
    turn = rotation % 360
    if turn == 90:
        return (half, 0.0)
    if turn == 180:
        return (0.0, half)
    if turn == 270:
        return (-half, 0.0)
    return (0.0, -half)


def _snap(value: float, grid: float, low: float | None = None, high: float | None = None) -> float:
    """`value` on the grid; never below `low` or above `high` (edges stay respected)."""
    if grid <= 0:
        return round(value, 3)
    # half-way values always go up: Python's round() takes them to the even side, and
    # a column on a 2.5 mm step then lands alternately up and down
    snapped = math.floor(value / grid + 0.5) * grid
    if low is not None and snapped < low - 1e-9:
        snapped += grid
    if high is not None and snapped > high + 1e-9:
        snapped -= grid
    return round(snapped, 3)


def _band(queue: list[Part], axis: int, limit: float, gap: float) -> list[Part]:
    """The longest prefix of `queue` that fits in `limit` when spaced on one pitch
    (the first part always fits, like a single part beside a small anchor)."""
    chosen: list[Part] = []
    for part in queue:
        candidate = chosen + [part]
        pitch = max(_size(p)[axis] for p in candidate)
        if chosen and len(candidate) * pitch + (len(candidate) - 1) * gap > limit + 1e-6:
            break
        chosen = candidate
    return chosen


def _cluster_layout(
    anchor: Part | None, satellites: list[Part], gap: float, grid: float = 0.0
) -> dict[str, tuple[float, float]]:
    """Relative centres of a cluster: the anchor at the origin, satellites in rings
    around it — right column, left column, top row, bottom row, then further out."""
    positions: dict[str, tuple[float, float]] = {}
    if anchor is not None:
        core_w, core_h = _size(anchor)
        positions[anchor.refdes] = (0.0, 0.0)
    else:
        core_w = core_h = 0.0
    queue = list(satellites)
    ring = 0
    ring_w = ring_h = 0.0  # how far the previous rings reach beyond the core
    while queue:
        band_w = max(_size(p)[0] for p in queue)
        band_h = max(_size(p)[1] for p in queue)
        reach_x = core_w / 2 + ring_w + gap + band_w / 2
        reach_y = core_h / 2 + ring_h + gap + band_h / 2
        limit_h = max(core_h + 2 * ring_h, band_h)
        limit_w = max(core_w + 2 * ring_w, band_w) + 2 * (gap + band_w)
        # right and left columns, then top and bottom rows: each band takes as many
        # parts as fit, and spaces them on one pitch (the largest of them plus the
        # gap), so a column or row of passives reads as a row, not a staircase
        for axis, signs, limit in ((1, (1, -1), limit_h), (0, (1, -1), limit_w)):
            for sign in signs:
                chosen = _band(queue, axis, limit, gap)
                if not chosen:
                    continue
                pitch = max(_size(p)[axis] for p in chosen)
                step = pitch + gap
                if grid > 0:
                    # a step on the grid keeps the pitch equal after every centre snaps
                    step = math.ceil(step / grid - 1e-9) * grid
                total = pitch + (len(chosen) - 1) * step
                slots = [total / 2 - pitch / 2 - i * step for i in range(len(chosen))]
                # the first (highest-priority) parts take the slots nearest the anchor
                nearest = sorted(range(len(slots)), key=lambda i: (abs(slots[i]), -slots[i]))
                for part, slot in zip(chosen, nearest, strict=True):
                    along = slots[slot]
                    if axis == 1:
                        positions[part.refdes] = (round(sign * reach_x, 3), round(along, 3))
                    else:
                        positions[part.refdes] = (round(-along, 3), round(sign * reach_y, 3))
                del queue[: len(chosen)]
        ring += 1
        ring_w += band_w + gap
        ring_h += band_h + gap
        if ring > 8:
            break
    for part in queue:  # safety: anything left goes below the cluster
        positions[part.refdes] = (0.0, -(core_h / 2 + ring_h + gap))
    return positions


# -- reference designator labels ---------------------------------------------------------

Rect = tuple[float, float, float, float]
LABEL_GAP = 0.3  # mm between a label and what it must not touch
LABEL_STEPS = (0.0, 0.5, 1.0, 1.6, 2.4, 3.2)  # extra distances tried away from the part


def _overlaps(a: Rect, b: Rect, gap: float) -> bool:
    return a[0] < b[2] + gap and b[0] < a[2] + gap and a[1] < b[3] + gap and b[1] < a[3] + gap


def place_labels(
    labels: list[dict[str, Any]],
    obstacles: list[Rect],
    board: Rect,
    gap: float = LABEL_GAP,
    margin: float = 1.0,
) -> list[dict[str, Any]]:
    """Where each part's designator goes: `labels` items have `refdes`, `width`,
    `height` (the text box, mm), `part` (the part's rectangle) and `x`, `y` (where the
    label stands now). Positions above, below, left and right of the part, then its
    corners, then further out are tried; the first that clears every obstacle, every
    other part and every label placed before it wins. A label that fits nowhere keeps
    its place and is reported with `placed` false."""
    taken: list[Rect] = list(obstacles)
    parts = {item["refdes"]: tuple(item["part"]) for item in labels}
    results: list[dict[str, Any]] = []
    for item in sorted(labels, key=lambda it: (-(it["part"][2] - it["part"][0]), it["refdes"])):
        w, h = float(item["width"]), float(item["height"])
        x0, y0, x1, y1 = parts[item["refdes"]]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        others = [rect for ref, rect in parts.items() if ref != item["refdes"]]
        chosen: tuple[float, float] | None = None
        for extra in LABEL_STEPS:
            candidates = [
                (cx, y1 + gap + extra + h / 2),
                (cx, y0 - gap - extra - h / 2),
                (x0 - gap - extra - w / 2, cy),
                (x1 + gap + extra + w / 2, cy),
                (x0 + w / 2, y1 + gap + extra + h / 2),
                (x1 - w / 2, y1 + gap + extra + h / 2),
                (x0 + w / 2, y0 - gap - extra - h / 2),
                (x1 - w / 2, y0 - gap - extra - h / 2),
            ]
            for lx, ly in candidates:
                box = (lx - w / 2, ly - h / 2, lx + w / 2, ly + h / 2)
                if (
                    box[0] < board[0] + margin
                    or box[1] < board[1] + margin
                    or box[2] > board[2] - margin
                    or box[3] > board[3] - margin
                ):
                    continue
                if any(_overlaps(box, rect, gap) for rect in taken):
                    continue
                if any(_overlaps(box, rect, gap) for rect in others):
                    continue
                chosen = (round(lx, 3), round(ly, 3))
                break
            if chosen:
                break
        if chosen is None:
            chosen = (float(item["x"]), float(item["y"]))
            placed = False
        else:
            placed = True
        taken.append((chosen[0] - w / 2, chosen[1] - h / 2, chosen[0] + w / 2, chosen[1] + h / 2))
        results.append(
            {
                "refdes": item["refdes"],
                "x": chosen[0],
                "y": chosen[1],
                "placed": placed,
                "moved": abs(chosen[0] - float(item["x"])) > 1e-6
                or abs(chosen[1] - float(item["y"])) > 1e-6,
            }
        )
    return results


# -- pin-aware clusters -----------------------------------------------------------------

SIDES = ("right", "left", "top", "bottom")
OVERHANG = 4 * REFDES_CHAR_WIDTH + REFDES_PADDING  # a row may pass the IC's edge by a label
SIDE_TOWARD = {"right": (-1.0, 0.0), "left": (1.0, 0.0), "top": (0.0, -1.0), "bottom": (0.0, 1.0)}


def _turn(point: tuple[float, float], rotation: float) -> tuple[float, float]:
    """`point` turned counter-clockwise by `rotation` (a multiple of 90°)."""
    x, y = point
    turn = rotation % 360
    if turn == 90:
        return (-y, x)
    if turn == 180:
        return (-x, -y)
    if turn == 270:
        return (y, -x)
    return (x, y)


PIN_ROW_TOLERANCE = 0.3  # mm; pins this close in x (or y) stand in one column (row)


def _pin_side(anchor: Part, pin: str) -> tuple[str, float] | None:
    """Which side of the anchor a pin sits on, and where along that side: the side
    whose row of pins it stands in (a corner pin of a wide SOIC is nearer the bottom
    edge than the left one, but it stands in the left column), else the nearer edge."""
    offset = anchor.pins.get(pin)
    if offset is None:
        return None
    px, py = offset
    column = sum(1 for qx, _qy in anchor.pins.values() if abs(qx - px) < PIN_ROW_TOLERANCE)
    row = sum(1 for _qx, qy in anchor.pins.values() if abs(qy - py) < PIN_ROW_TOLERANCE)
    if column > row:
        return ("right" if px >= 0 else "left", py)
    if row > column:
        return ("top" if py >= 0 else "bottom", px)
    width, height = max(anchor.width, MIN_SIZE), max(anchor.height, MIN_SIZE)
    if abs(px) * height >= abs(py) * width:
        return ("right" if px >= 0 else "left", py)
    return ("top" if py >= 0 else "bottom", px)


def _attachments(
    anchor: Part, satellites: list[Part], nets: list[Net]
) -> dict[str, tuple[str, str, float]]:
    """For each satellite the anchor pin it should sit beside: (anchor pin, own pin,
    weight). Signal nets between two parts weigh most, supplies least, so a series
    resistor sits at its signal pin and a decoupling capacitor at the supply pin."""
    known = {part.refdes for part in satellites}
    best: dict[str, tuple[str, str, float]] = {}
    for net in nets:
        members = {refdes for refdes, _pin in net.pins}
        anchor_pins = [pin for refdes, pin in net.pins if refdes == anchor.refdes]
        if not anchor_pins:
            continue
        if net.power or len(members) >= POWER_NET_PINS:
            # a decoupling capacitor goes to the supply pin, not the ground pin
            weight = 0.4 if "GND" in net.name.upper() else 0.5
        else:
            weight = 3.0 if len(members) == 2 else 2.0 / (len(members) - 1)
        for refdes, pin in net.pins:
            if refdes not in known:
                continue
            anchor_pin = next(
                (p for p in anchor_pins if _pin_side(anchor, p) is not None), anchor_pins[0]
            )
            current = best.get(refdes)
            if current is None or weight > current[2]:
                best[refdes] = (anchor_pin, pin, weight)
    return best


def _facing_rotation(part: Part, pin: str, side: str) -> float:
    """The rotation that turns `pin` of `part` toward the anchor on `side`; a chip part
    with two pads ends up with one pad facing the pin it connects to."""
    offset = part.pins.get(pin)
    if offset is None or (abs(offset[0]) < 1e-6 and abs(offset[1]) < 1e-6):
        if len(part.pins) > 2 or part.width <= part.height:
            return 0.0
        return 90.0 if side in ("top", "bottom") else 0.0
    toward = SIDE_TOWARD[side]
    candidates = (0.0, 90.0, 180.0, 270.0) if len(part.pins) <= 2 else (0.0,)
    scores = []
    for rotation in candidates:
        x, y = _turn(offset, rotation)
        scores.append((x * toward[0] + y * toward[1], -rotation, rotation))
    return max(scores)[2]


def _pack(items: list[tuple[str, float, float]], gap: float, grid: float) -> dict[str, float]:
    """Centres along a side for (refdes, target, size) items: in target order, never
    overlapping, pulled back toward the targets as a group, on the grid."""
    if not items:
        return {}
    ordered = sorted(items, key=lambda item: (item[1], item[0]))
    step = gap if grid <= 0 else math.ceil(gap / grid - 1e-9) * grid
    positions: list[float] = []
    for index, (_refdes, target, size) in enumerate(ordered):
        if index == 0:
            positions.append(target)
            continue
        previous = positions[-1] + ordered[index - 1][2] / 2 + step + size / 2
        positions.append(max(target, previous))
    drift = sum(pos - target for pos, (_r, target, _s) in zip(positions, ordered, strict=True))
    drift /= len(ordered)
    positions = [pos - drift for pos in positions]
    if grid > 0:
        positions = [round(math.floor(pos / grid + 0.5) * grid, 3) for pos in positions]
        for index in range(1, len(positions)):
            least = positions[index - 1] + ordered[index - 1][2] / 2 + step + ordered[index][2] / 2
            if positions[index] < least - 1e-9:
                positions[index] = round(math.ceil(least / grid - 1e-9) * grid, 3)
    return {refdes: pos for (refdes, _t, _s), pos in zip(ordered, positions, strict=True)}


def _pin_cluster_layout(
    anchor: Part, satellites: list[Part], nets: list[Net], gap: float, grid: float = 0.0
) -> tuple[dict[str, tuple[float, float]], dict[str, float]]:
    """Relative centres and rotations of a cluster whose anchor knows its pin
    positions: every satellite stands on the side of the IC where the pin it connects
    to is, level with that pin, turned so its own connecting pad faces the pin. A row
    may stick out past the IC's edge by about one label; a side that still fills up
    continues in a second row further out, and a row above or below the IC steps
    past a column that reaches the corner."""
    positions: dict[str, tuple[float, float]] = {anchor.refdes: (0.0, 0.0)}
    rotations: dict[str, float] = {anchor.refdes: 0.0}
    attachments = _attachments(anchor, satellites, nets)
    by_refdes = {part.refdes: part for part in satellites}
    placed: dict[str, tuple[str, float]] = {}  # refdes -> (side, target along)
    for part in satellites:
        attachment = attachments.get(part.refdes)
        if attachment is None:
            continue
        side_at = _pin_side(anchor, attachment[0])
        if side_at is None:
            continue
        placed[part.refdes] = side_at
    # a part that does not touch the anchor follows the satellite it connects to most
    weights, _supplies = _weights(satellites, nets)
    for part in satellites:
        if part.refdes in placed:
            continue
        partners = sorted(
            ((w, r) for r, w in weights[part.refdes].items() if r in placed), reverse=True
        )
        if partners:
            placed[part.refdes] = placed[partners[0][1]]
    load = {side: 0 for side in SIDES}
    for side, _along in placed.values():
        load[side] += 1
    for part in satellites:
        if part.refdes not in placed:
            side = min(SIDES, key=lambda s: (load[s], SIDES.index(s)))
            load[side] += 1
            placed[part.refdes] = (side, 0.0)
    core_w, core_h = _size(anchor)
    slots: list[tuple[float, float, float, float]] = [(0.0, 0.0, core_w, core_h)]
    for side in SIDES:
        members = [r for r in placed if placed[r][0] == side]
        if not members:
            continue
        along_axis = 1 if side in ("right", "left") else 0
        across_axis = 1 - along_axis
        core_along = core_h if along_axis == 1 else core_w
        core_across = core_w if along_axis == 1 else core_h
        limit = core_along + 2 * (gap + OVERHANG)
        queue = sorted(members, key=lambda r: (placed[r][1], sort_key(r)))
        reach = core_across / 2 + gap
        sign = 1.0 if side in ("right", "top") else -1.0
        while queue:
            # as many parts as fit along the side in one row, nearest targets first
            row: list[str] = []
            used = 0.0
            for refdes in queue:
                part = by_refdes[refdes]
                pin = attachments.get(refdes, ("", "", 0.0))[1]
                size = _size(part, _facing_rotation(part, pin, side))[along_axis]
                if row and used + gap + size > limit + 1e-6:
                    continue
                row.append(refdes)
                used += size + (gap if used else 0.0)
            queue = [r for r in queue if r not in row]
            items = []
            extents: dict[str, tuple[float, float]] = {}
            band = 0.0
            for refdes in row:
                part = by_refdes[refdes]
                pin = attachments.get(refdes, ("", "", 0.0))[1]
                rotation = _facing_rotation(part, pin, side)
                rotations[refdes] = rotation
                extents[refdes] = _size(part, rotation)
                items.append((refdes, placed[refdes][1], extents[refdes][along_axis]))
                band = max(band, extents[refdes][across_axis])
            packed = _pack(items, gap, grid)
            low = min(packed[r] - extents[r][along_axis] / 2 for r in row)
            high = max(packed[r] + extents[r][along_axis] / 2 for r in row)
            # the row stands clear of whatever already reaches into its span on this
            # side (the IC itself, an earlier row, a column past the IC's corner)
            needed = reach
            for sx, sy, sw, sh in slots:
                s_along, s_size_along = (sy, sh) if along_axis == 1 else (sx, sw)
                s_across, s_size_across = (sx, sw) if across_axis == 0 else (sy, sh)
                if (
                    s_along - s_size_along / 2 < high - 1e-6
                    and low < s_along + s_size_along / 2 - 1e-6
                ):
                    needed = max(needed, sign * s_across + s_size_across / 2 + gap)
            centre = needed + band / 2
            for refdes, along in packed.items():
                if along_axis == 1:
                    position = (round(sign * centre, 3), round(along, 3))
                else:
                    position = (round(along, 3), round(sign * centre, 3))
                positions[refdes] = position
                slots.append((position[0], position[1], extents[refdes][0], extents[refdes][1]))
            reach = needed + band + gap
    return positions, rotations


def _bbox(
    parts: dict[str, Part],
    positions: dict[str, tuple[float, float]],
    rotations: dict[str, float] | None = None,
) -> tuple[float, float, float, float]:
    xs_min = []
    xs_max = []
    ys_min = []
    ys_max = []
    for refdes, (x, y) in positions.items():
        w, h = _size(parts[refdes], (rotations or {}).get(refdes, 0.0))
        xs_min.append(x - w / 2)
        xs_max.append(x + w / 2)
        ys_min.append(y - h / 2)
        ys_max.append(y + h / 2)
    if not xs_min:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs_min), min(ys_min), max(xs_max), max(ys_max))


# -- the plan ---------------------------------------------------------------------------


def arrange(
    parts: list[Part],
    board: tuple[float, float, float, float],
    nets: list[Net] | None = None,
    zones: dict[str, str] | None = None,
    margin: float = MARGIN,
    gap: float = GAP,
    cluster_gap: float = CLUSTER_GAP,
    grid: float = 0.0,
    corner_keepout: float = 0.0,
) -> Plan:
    """The placement plan for `parts` inside `board` (min x, min y, max x, max y, mm).

    Clusters run in rows from the top-left, in sheet order; a cluster that would
    end below the bottom margin is still placed, with `inside` false, so nothing
    is dropped. `zones` maps a sheet key (`"2"`) to its label text. Every part keeps
    room above it for its reference designator, and with `grid` the footprint centres
    land on that grid (edges are respected: a part never snaps outward). With
    `corner_keepout`, a square of that size in every corner stays empty, for the
    mounting holes.
    """
    nets = nets or []
    zones = zones or {}
    # the order Layout lists components in is not stable; the plan must be
    parts = sorted(parts, key=lambda item: sort_key(item.refdes))
    by_refdes = {part.refdes: part for part in parts}
    assignment = _assign_clusters(parts, nets)
    clusters: dict[str, list[str]] = {}
    for refdes, anchor in assignment.items():
        clusters.setdefault(anchor, []).append(refdes)
    min_x, min_y, max_x, max_y = board
    connectors = [p for p in parts if role_of(p.refdes) == "connector"]
    testpoints = [p for p in parts if role_of(p.refdes) == "testpoint"]
    # edge bands: connectors stand on the left/right edge turned by 90°, test points
    # along the bottom edge
    band_side = max((_size(p, 90)[0] for p in connectors), default=0.0)
    band_bottom = max((_size(p)[1] for p in testpoints), default=0.0)
    left = min_x + margin + (band_side + cluster_gap if connectors else 0.0)
    right = max_x - margin - (band_side + cluster_gap if connectors else 0.0)
    top = max_y - margin - (LABEL_HEIGHT + gap)  # the zone labels sit above the top row
    bottom = min_y + margin + (band_bottom + cluster_gap if testpoints else 0.0)
    if corner_keepout > 0:
        left = max(left, min_x + corner_keepout)
        right = min(right, max_x - corner_keepout)

    # cluster boxes
    boxes: dict[str, tuple[dict[str, tuple[float, float]], tuple[float, float, float, float]]] = {}
    turns: dict[str, dict[str, float]] = {}
    for anchor, members in clusters.items():
        core = by_refdes.get(anchor)
        satellites = [
            by_refdes[r]
            for r in sorted(members, key=sort_key)
            if r != anchor and role_of(r) == "part"
        ]
        satellites.sort(key=lambda p: (_split(p.refdes)[0] != "C", sort_key(p.refdes)))
        if core is not None and core.pins:
            positions, rotations = _pin_cluster_layout(core, satellites, nets, gap, grid)
        else:
            positions, rotations = _cluster_layout(core, satellites, gap, grid), {}
        turns[anchor] = rotations
        boxes[anchor] = (positions, _bbox(by_refdes, positions, rotations))

    plan = Plan(clusters={k: sorted(v, key=sort_key) for k, v in clusters.items()})
    order = sorted(boxes, key=lambda a: (_group_order(cluster_group(a)), sort_key(a)))
    x = left
    row_top = top
    row_height = 0.0
    centres: dict[str, tuple[float, float]] = {}
    current_sheet: str | None = None
    for anchor in order:
        positions, (bx0, by0, bx1, by1) = boxes[anchor]
        width, height = bx1 - bx0, by1 - by0
        sheet = cluster_group(anchor)
        new_sheet = current_sheet is not None and sheet != current_sheet
        if x > left and (new_sheet or x + width > right + 1e-6):
            # a sheet starts a row of its own, under its label; a sheet too wide for
            # one row continues on the next without another label
            row_top -= row_height + cluster_gap + ((LABEL_HEIGHT + gap) if new_sheet else 0.0)
            x = left
            row_height = 0.0
        current_sheet = sheet
        origin_x = x - bx0
        origin_y = row_top - by1
        centres[anchor] = (origin_x, origin_y)
        for refdes, (rx, ry) in positions.items():
            part = by_refdes[refdes]
            rotation = turns[anchor].get(refdes, 0.0)
            w, h = _size(part, rotation)
            cx, cy = origin_x + rx, origin_y + ry
            # the bands on the edges (connectors, test points) are not cluster room
            inside = (
                cx - w / 2 >= min_x + margin - 1e-6
                and cx + w / 2 <= max_x - margin + 1e-6
                and cy - h / 2 >= bottom - 1e-6
                and cy + h / 2 <= max_y - margin + 1e-6
            )
            sx, sy = _shift(part, rotation)
            fw, fh = _footprint(part, rotation)
            plan.placements.append(
                Placement(
                    refdes,
                    part.cell,
                    group_of(refdes),
                    anchor,
                    _snap(cx + sx, grid),
                    _snap(cy + sy, grid),
                    fw,
                    fh,
                    rotation,
                    inside,
                    role_of(refdes),
                )
            )
        x += width + cluster_gap
        row_height = max(row_height, height)

    # connectors on the nearest side edge, beside their cluster: stacked top-down in
    # cluster order, and the whole column lifted if it would run below the margin
    board_mid = (min_x + max_x) / 2
    edge_room = (max_y - margin - corner_keepout) - (min_y + margin + corner_keepout)
    side_of: dict[str, int] = {}
    for part in connectors:
        anchor = assignment.get(part.refdes, "")
        cx, _cy = centres.get(anchor, (board_mid, (top + bottom) / 2))
        side_of[part.refdes] = -1 if cx < board_mid else 1

    def _column_height(sign: int) -> float:
        heights = [_size(p, 90)[1] for p in connectors if side_of[p.refdes] == sign]
        return sum(heights) + gap * max(len(heights) - 1, 0)

    # an edge that cannot hold its connectors hands the one whose cluster stands
    # nearest the middle to the other edge, while that edge has the room
    for sign in (-1, 1):
        while _column_height(sign) > edge_room + 1e-6:
            movable = [p for p in connectors if side_of[p.refdes] == sign]
            if len(movable) < 2:
                break
            centre_x = {
                p.refdes: centres.get(assignment.get(p.refdes, ""), (board_mid, 0.0))[0]
                for p in movable
            }
            mover = max(movable, key=lambda p: (sign * centre_x[p.refdes], sort_key(p.refdes)))
            if _column_height(-sign) + gap + _size(mover, 90)[1] > edge_room + 1e-6:
                break
            side_of[mover.refdes] = -sign
    for side_sign in (-1, 1):
        column = []
        for part in connectors:
            if side_of[part.refdes] != side_sign:
                continue
            anchor = assignment.get(part.refdes, "")
            _cx, cy = centres.get(anchor, (board_mid, (top + bottom) / 2))
            column.append((cy, part))
        column.sort(key=lambda item: (-item[0], sort_key(item[1].refdes)))
        ys: list[float] = []
        limit_top = max_y - margin - corner_keepout
        for target_y, part in column:
            _w, h = _size(part, 90)
            y = min(target_y, limit_top - h / 2)
            ys.append(y)
            limit_top = y - h / 2 - gap
        if column:
            lowest = ys[-1] - _size(column[-1][1], 90)[1] / 2
            deficit = (min_y + margin + corner_keepout) - lowest
            if deficit > 0:
                ys = [y + deficit for y in ys]
        # turned so the reference designator (the part's "up" side) faces inward and
        # the housing stands on the margin: 270° on the left edge, 90° on the right
        rotation = 270.0 if side_sign < 0 else 90.0
        for (_target_y, part), y in zip(column, ys, strict=True):
            w, h = _size(part, rotation)
            edge_x = min_x + margin + w / 2 if side_sign < 0 else max_x - margin - w / 2
            inside = y + h / 2 <= max_y - margin + 1e-6 and y - h / 2 >= min_y + margin - 1e-6
            sx, sy = _shift(part, rotation)
            fw, fh = _footprint(part, rotation)
            low = min_x + margin + fw / 2 if side_sign < 0 else None
            high = None if side_sign < 0 else max_x - margin - fw / 2
            plan.placements.append(
                Placement(
                    part.refdes,
                    part.cell,
                    group_of(part.refdes),
                    assignment.get(part.refdes, ""),
                    _snap(edge_x + sx, grid, low, high),
                    _snap(y + sy, grid),
                    fw,
                    fh,
                    rotation,
                    inside,
                    "connector",
                )
            )

    # test points along the bottom edge, near their cluster
    row = []
    for part in testpoints:
        anchor = assignment.get(part.refdes, "")
        cx, _cy = centres.get(anchor, (board_mid, 0.0))
        row.append((cx, part))
    row.sort(key=lambda item: (item[0], sort_key(item[1].refdes)))
    cursor = max(left, min_x + corner_keepout)
    for target_x, part in row:
        w, h = _size(part)
        x_pos = max(target_x - w / 2, cursor)
        x_pos = min(x_pos, min(right, max_x - corner_keepout) - w)
        cx = x_pos + w / 2
        cy = min_y + margin + h / 2
        cursor = x_pos + w + gap
        inside = cx + w / 2 <= right + 1e-6
        sx, sy = _shift(part)
        fw, fh = _footprint(part)
        plan.placements.append(
            Placement(
                part.refdes,
                part.cell,
                group_of(part.refdes),
                assignment.get(part.refdes, ""),
                _snap(cx + sx, grid),
                _snap(cy + sy, grid, low=min_y + margin + fh / 2),
                fw,
                fh,
                0.0,
                inside,
                "testpoint",
            )
        )

    # one label per sheet above its clusters; Layout anchors text at its centre, so the
    # position is the label's midpoint, estimated from its length
    for key in sorted({cluster_group(a) for a in order}, key=_group_order):
        xs = [boxes[a][1][0] + centres[a][0] for a in order if cluster_group(a) == key]
        ys = [boxes[a][1][3] + centres[a][1] for a in order if cluster_group(a) == key]
        if not xs:
            continue
        text = zones.get(key) or f"SHEET {key}"
        half_width = len(text) * LABEL_CHAR_WIDTH / 2
        x_mid = min(
            max(min(xs) + half_width, min_x + margin + half_width), max_x - margin - half_width
        )
        y_mid = max(ys) + gap + LABEL_HEIGHT / 2
        plan.labels.append(Label(text, round(x_mid, 3), round(y_mid, 3), key))
    return plan
