"""The board commands that read the open board, check it, or write files from it: ``pcb get``,
``pcb check`` and ``pcb export``.

The board model (``board_model``) is what ``plan.board_metrics`` measures and checks: the
outline, the placed parts with their extents and pins, traces and vias, and for the checks the
pads, holes and silkscreen too, all in mm. Positions read through a getter that takes a unit
(``GetPositionX(4)``, ``Extrema``) come in mm whatever the board's unit; raw geometry
(``PointsArray``, ``CircleX``, ``LineWidth``) comes in the board's unit, which ``geometry_scale``
measures on the board outline. Without an outline on a board that is not in mils, the geometry and
metrics say they are unavailable; the unit is not changed, since that would change the document
in a read.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .._driver import AdapterError
from .._driver.targets import path_of
from . import base
from . import layout as L
from .base import Context
from .plan import PlanError, board_metrics, fab_package
from .plan import board_render as PR
from .plan import guards as GD
from .plan import requirements as RQ
from .plan import routing as plan_routing
from .platform import com, dialogs

STACKUP_DISPID = 157  # Document.LayerStack([in] VARIANT_BOOL bIncludeInsulationLayer)
NET_CLASSES_DISPID = 24  # Document.NetClasses([in] BSTR sNetClassName)
OBSTRUCTS_DISPID = 41  # Document.Obstructs(eSelectType, eSelectOnly, nLayerOnly)
PLACEMENT_OBSTRUCTS_DISPID = 40  # Document.PlacementObstructs(eSelectOnly, nLayerOnly)
THICKNESS_DISPID = 1  # LayerProperties.Thickness([in] EPcbUnit)
NET_CLASS_WIDTHS = {"minimum": 7, "typical": 8, "expansion": 9}  # NetClass.<kind>TraceWidth(layer, scheme, unit)
MASTER_SCHEME = "(Master)"
LAYER_KINDS = {0: "conductor", 1: "fabrication", 2: "user", 3: "insulation"}  # EPcbLayerType
LAYER_USAGES = {1: "signal", 2: "plane", 3: "split_mixed", 4: "flooded_signal", 5: "plating"}
FAB_SILKSCREEN = 2  # FabricationLayerGfx/Text.Type: 1 assembly, 2 silkscreen
TEXT_KINDS = {1: "refdes", 4: "part_number"}  # FabricationLayerText.TextType
SIDE_BOTTOM = 512  # EPcbSide: 1 top, 512 bottom
LISTED = 25  # list entries a result carries; the counts stay whole
UNIT_NOTE = ("The unit of the board's raw geometry could not be measured: the board has no outline and does not count in mils. "
             "Draw the outline with pcb update, then read again.")


# ------------------------------------------------------------------ the board model


def _side(obj: Any) -> str:
    try:
        side = int(obj.Side)
    except Exception:  # noqa: BLE001 - no side: the top
        return "top"
    return "bottom" if side == SIDE_BOTTOM else "top"


def _side_name(obj: Any) -> str:
    try:
        name = str(obj.SideName or "").lower()
    except Exception:  # noqa: BLE001 - the side number instead
        name = ""
    return name if name in ("top", "bottom") else _side(obj)


def geometry_record(geometry: Any, scale: float, **extra: Any) -> dict[str, Any] | None:
    """A geometry as plain data: a ``circle`` (x, y, r) or a ``path`` of (x, y, r) rows, in mm."""
    try:
        if bool(geometry.IsCircle()):
            circle = [round(float(geometry.CircleX) * scale, 4), round(float(geometry.CircleY) * scale, 4),
                      round(float(geometry.CircleR) * scale, 4)]
            return {"circle": circle, **extra}
        rows = geometry.PointsArray
    except Exception:  # noqa: BLE001 - a geometry that cannot be read is left out
        return None
    if not rows or len(rows) < 3:
        return None
    path = [[round(float(x) * scale, 4), round(float(y) * scale, 4), round(float(r) * scale, 4)]
            for x, y, r in zip(rows[0], rows[1], rows[2], strict=False)]
    return {"path": path, **extra}


def _position(obj: Any) -> tuple[float, float]:
    return round(float(obj.GetPositionX(L.UNIT_MM)), 4), round(float(obj.GetPositionY(L.UNIT_MM)), 4)


def _component(component: Any, model: dict[str, Any], scale: float, full: bool, holes: bool = True) -> dict[str, Any]:
    refdes = str(component.RefDes or "")
    placed = bool(component.Placed)
    row: dict[str, Any] = {"refdes": refdes, "placed": placed, "cell": str(component.CellName or ""), "side": _side(component)}
    if not placed:
        return row  # an unplaced part's pins and texts stand at its cell's own coordinates
    row["extents"] = [round(value, 4) for value in L.extrema_mm(component)]
    row["x"], row["y"] = _position(component)
    row["rotation"] = round(float(component.Orientation or 0.0) % 360.0, 4)
    pins: list[dict[str, Any]] = []
    for pin in L.collection(component, "Pins"):
        name, net = L.name_of(pin), L.net_name(pin)
        x, y = _position(pin)
        pins.append({"pin": name, "net": net, "x": x, "y": y})
        if not full:
            continue
        for pad in L.collection(pin, "Pads"):
            layer = int(pad.Layer)
            for geometry in L.collection(pad, "Geometries"):
                record = geometry_record(geometry, scale, layer=layer, net=net, refdes=refdes, pin=name, kind="pad")
                if record:
                    model["pads"].append(record)
        for hole in L.collection(pin, "Holes") if holes else ():
            size = float(hole.GetDrillSize(L.UNIT_MM))
            if size > 0:
                model["holes"].append([x, y, round(size, 4)])
    row["pins"] = pins
    return row


def _owner(item: Any) -> tuple[str | None, bool]:
    """Whose a silkscreen item is (None: the board's) and whether it is on the board."""
    component = item.Component
    if component is None:
        return None, True
    return str(component.RefDes), bool(component.Placed)


def _silkscreen(doc: Any, model: dict[str, Any], scale: float) -> None:
    for gfx in L.collection(doc, "FabricationLayerGfxs"):
        try:
            if int(gfx.Type) != FAB_SILKSCREEN:
                continue
            owner, on_board = _owner(gfx)
            if not on_board:
                continue
            geometry = gfx.Geometry
            width = 0.0 if bool(geometry.Filled) else (float(geometry.LineWidth or 0.0) * scale or 0.12)
            record = geometry_record(geometry, scale, side=_side_name(gfx), width=round(width, 4), kind="silk", refdes=owner)
            if record:
                model["silk"].append(record)
        except Exception:  # noqa: BLE001 - a graphic that cannot be read is left out
            continue
    for text in L.collection(doc, "FabricationLayerTexts"):
        try:
            if int(text.Type) != FAB_SILKSCREEN:
                continue
            owner, on_board = _owner(text)
            if not on_board:
                continue
            fmt = text.Format
            record: dict[str, Any] = {"text": str(text.TextString), "kind": TEXT_KINDS.get(int(text.TextType or 0), "text"),
                                      "side": _side_name(text), "refdes": owner, "height": round(float(fmt.Height or 0.0) * scale, 4),
                                      "pen": round(float(fmt.PenWidth or 0.0) * scale, 4)}
            record["extents"] = [round(value, 4) for value in L.extrema_mm(text)]
            model["texts"].append(record)
        except Exception:  # noqa: BLE001 - a text that cannot be read is left out
            continue


def board_model(doc: Any, *, full: bool, status: dict[str, dict[str, Any]] | None = None, silk: bool = True,
                shapes: bool = True) -> dict[str, Any] | None:
    """The board as ``plan.board_metrics`` takes it, or None on a board that is not in mils.
    ``full`` adds the pads, holes and, unless ``silk`` is false, the silkscreen the board rules
    need, and unless ``shapes`` is false the keepouts, cutouts, planes and pins' holes; a reader of
    the copper alone leaves the silkscreen and the shapes out, half the time."""
    scale = L.geometry_scale(doc)
    if scale is None:
        return None
    status = L.route_status(doc) if status is None else status
    model: dict[str, Any] = {
        "layers": int(doc.LayerCount), "outline": [], "components": [], "pads": [], "vias": [], "traces": [], "holes": [],
        "silk": [], "texts": [], "connectivity": {"complete": True},
        "unrouted": [{"net": name, "opens": row["opens"]} for name, row in sorted(status.items()) if not row["routed"]],
    }
    outline = doc.BoardOutline
    record = geometry_record(outline.Geometry, scale, kind="outline") if outline is not None else None
    if record:
        model["outline"].append(record)
    for component in L.collection(doc, "Components"):
        model["components"].append(_component(component, model, scale, full, holes=shapes))
    radii: dict[str, float] = {}  # each padstack's pad is read once: reading it via by via takes seconds on a routed board
    for via in L.collection(doc, "Vias"):
        x, y = _position(via)
        name = L.name_of(via)
        if name not in radii:
            radii[name] = round(_via_radius(via, scale), 4)
        model["vias"].append({"circle": [x, y, radii[name]], "net": L.net_name(via), "kind": "via", "padstack": name})
    for trace in L.collection(doc, "Traces"):
        geometry = trace.Geometry
        record = geometry_record(geometry, scale, layer=int(trace.Layer), net=L.net_name(trace),
                                 width=round(float(geometry.LineWidth or 0.0) * scale, 4), kind="trace")
        if record:
            model["traces"].append(record)
    if full and silk:
        _silkscreen(doc, model, scale)
    if full and shapes:
        for hole_owner in L.collection(doc, "MountingHoles"):
            x, y = _position(hole_owner)
            for hole in L.collection(hole_owner, "Holes"):
                size = float(hole.GetDrillSize(L.UNIT_MM))
                if size > 0:
                    model["holes"].append([x, y, round(size, 4)])
        model["keepouts"] = _quietly(lambda: keepouts(doc, scale, geometry=True), [])
        model["cutouts"] = _quietly(lambda: cutouts(doc, scale), [])
        model["planes"] = _quietly(lambda: plane_shapes(doc), [])
    return model


# ------------------------------------------------------------------ pcb get


def _quietly(fn: Any, default: Any = None) -> Any:
    """A part of the board's report that cannot be read is reported as None, not as an error."""
    try:
        return fn()
    except Exception:  # noqa: BLE001 - the rest of the report stands
        return default


# EPcbInsulationType: what an insulation layer of the stackup is; a flex board has cover layers
INSULATION_TYPES = {0: "dielectric", 1: "solder_mask", 2: "rigid_substrate", 3: "flex_substrate", 4: "cover_layer", 5: "adhesive",
                    6: "stiffener"}


def board_stackup(doc: Any) -> list[dict[str, Any]]:
    """Every layer of the stackup from the top, conductors and insulation alike."""
    layers: list[dict[str, Any]] = []
    for layer in L.items(com.propget(doc, STACKUP_DISPID, True)):
        kind = LAYER_KINDS.get(int(layer.Type), "other")
        row: dict[str, Any] = {"board_layer": int(layer.BoardLayer), "kind": kind}
        if kind == "conductor":
            row["layer"] = int(layer.ConductorLayer)
        properties = layer.LayerProperties
        if properties is not None:
            row["name"] = str(properties.StackupLayerName or "")
            row["description"] = str(properties.Description or "")
            row["thickness_mm"] = _quietly(lambda p=properties: round(float(com.propget(p, THICKNESS_DISPID, L.UNIT_MM)), 4))
            if kind == "conductor":
                row["usage"] = LAYER_USAGES.get(int(properties.LayerUsage or 0), "other")
            elif kind == "insulation":
                row["dielectric_constant"] = _quietly(lambda p=properties: float(p.DielectricConstant))
        if kind == "insulation":
            insulation = _quietly(lambda: int(layer.InsulationType))
            if insulation is not None:
                row["insulation"] = INSULATION_TYPES.get(insulation, f"type {insulation}")
        layers.append(row)
    return layers


def net_classes(doc: Any, layer_count: int) -> list[dict[str, Any]]:
    """Each net class with its number of nets, its trace widths per layer (Master scheme), mm, and the
    via it is assigned for each via span, the one the autorouter places."""
    classes: list[dict[str, Any]] = []
    spans = _quietly(lambda: L.via_spans(doc), [])
    for net_class in L.items(com.propget(doc, NET_CLASSES_DISPID, "*")):
        widths: dict[str, dict[str, float]] = {}
        for layer in range(1, layer_count + 1):
            row = {f"{kind}_mm": _quietly(lambda d=dispid, n=layer: round(float(com.propget(net_class, d, n, MASTER_SCHEME, L.UNIT_MM)), 4))
                   for kind, dispid in NET_CLASS_WIDTHS.items()}
            widths[str(layer)] = {kind: value for kind, value in row.items() if value is not None}
        classes.append({"name": L.name_of(net_class), "nets": L.count(net_class, "Nets"), "widths": widths,
                        "vias": L.class_vias(net_class, spans)})
    return classes


def keepout_count(doc: Any) -> int:
    """The board's obstructs (route, via, plane... keepouts) and its placement keepouts."""
    return int(com.propget(doc, OBSTRUCTS_DISPID, -1, 0, 0).Count) + int(com.propget(doc, PLACEMENT_OBSTRUCTS_DISPID, 0, 0).Count)


# EPcbObstructType: what an obstruct keeps out
OBSTRUCT_KINDS = {1: "plane", 2: "plane_no_connect", 4: "test_point", 8: "trace", 16: "trace_and_via", 32: "via", 64: "panel",
                  128: "pad", 256: "tuning_pattern"}
SIDES = {1: "top", 512: "bottom"}
PADSTACK_VIA = 2  # EPcbPadstackObjectType epcbPadstackObjectVia
PADSTACK_NAMES_DISPID = 227  # Document.PadstackNames(type, pin class, pattern, from the central library): read bare, its filters are lost


def _extents(item: Any) -> list[float] | None:
    return _quietly(lambda: [round(v, 4) for v in L.extrema_mm(item)])


def keepouts(doc: Any, scale: float | None, geometry: bool = False) -> list[dict[str, Any]]:
    """The board's keepouts: each obstruct with what it keeps out and its layer, and each placement
    keepout with its side and, where it limits height, the height in mm, as a stiffener's does;
    with ``geometry``, each one's shape."""
    rows: list[dict[str, Any]] = []
    for item in L.items(com.propget(doc, OBSTRUCTS_DISPID, -1, 0, 0)):
        row = {"kind": OBSTRUCT_KINDS.get(_quietly(lambda item=item: int(item.ObstructType)), "other"),
               "layer": _quietly(lambda item=item: int(item.Layer)), "extents_mm": _extents(item)}
        rows.append(row)
        if geometry and scale:
            row["shape"] = _quietly(lambda item=item: geometry_record(item.Geometry, scale))
    for item in L.items(com.propget(doc, PLACEMENT_OBSTRUCTS_DISPID, 0, 0)):
        height = _quietly(lambda item=item: float(item.Height) * scale) if scale else None
        row = {"kind": "placement", "side": SIDES.get(_quietly(lambda item=item: int(item.Side))),
               "height_mm": round(height, 4) if height else None, "extents_mm": _extents(item)}
        rows.append(row)
        if geometry and scale:
            row["shape"] = _quietly(lambda item=item: geometry_record(item.Geometry, scale))
    return rows


def keepout_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """How many keepouts of each kind, and the placement keepouts that limit height."""
    kinds: dict[str, int] = {}
    for row in rows:
        kinds[row["kind"]] = kinds.get(row["kind"], 0) + 1
    raised = [{k: row[k] for k in ("side", "height_mm", "extents_mm")} for row in rows if row["kind"] == "placement" and row["height_mm"]]
    return {"by_kind": kinds, "height_limits": raised[:LISTED]}


def cutouts(doc: Any, scale: float | None) -> list[dict[str, Any]]:
    """The cutouts and slots inside the board outline."""
    from .layout_edit import CONTOUR_INTERNAL

    rows = []
    for contour in L.collection(doc, "Contours"):
        if _quietly(lambda contour=contour: int(contour.ContourType)) != CONTOUR_INTERNAL:
            continue
        row: dict[str, Any] = {"extents_mm": _extents(contour)}
        if scale:
            row["shape"] = _quietly(lambda contour=contour: geometry_record(contour.Geometry, scale))
        rows.append(row)
    return rows


def plane_shapes(doc: Any) -> list[dict[str, Any]]:
    """Each plane shape: its net, layer, fill (solid, or the hatch with its line width and distance) and extents."""
    rows = []
    for shape in L.collection(doc, "PlaneShapes"):
        row = {"net": L.net_name(shape), "layer": _quietly(lambda shape=shape: int(shape.Layer))}
        row.update(_quietly(lambda shape=shape: L.plane_fill(doc, shape), {}))
        row["extents_mm"] = _extents(shape)
        rows.append(row)
    return rows


def generated_planes(doc: Any) -> set[tuple[str, int]]:
    """The nets and layers Layout has generated plane copper for."""
    pieces = L.raw_items(doc, "GeneratedPlanes")
    if pieces is not None:
        try:
            return {(L.raw_net(piece, "GeneratedPlane"), int(L._raw_get(piece, "GeneratedPlane", "Layer"))) for piece in pieces}
        except Exception:  # noqa: BLE001 - read them through the wrappers
            pass
    return {(L.net_name(item), int(item.Layer)) for item in L.collection(doc, "GeneratedPlanes")}


def with_copper(doc: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The plane shapes, each with whether Layout made copper in it (``copper``): a piece of its net
    and layer that lies within its extents. A shape that touches no pad, trace or via of its net on
    its layer gets none, though the shapes beside it do. Hatched copper comes in hundreds of
    pieces: once each shape of a net and layer holds one, the rest of them are not read."""
    waiting: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("extents_mm"):
            row["copper"] = False
            waiting.setdefault((row.get("net"), row.get("layer")), []).append(row)
    for layer in sorted({layer for _net, layer in waiting if layer is not None}):
        fast = L.raw_items(doc, "GeneratedPlanes", 0, layer)
        for item in fast if fast is not None else L.collection(doc, "GeneratedPlanes"):
            try:
                if fast is not None:
                    left = waiting.get((L.raw_net(item, "GeneratedPlane"), layer))
                    piece = tuple(L.raw_extents(item, "GeneratedPlane")) if left else None
                else:
                    if int(item.Layer) != layer:
                        continue
                    left = waiting.get((L.net_name(item), layer))
                    piece = tuple(L.extrema_mm(item)) if left else None
                if not left or piece is None:
                    continue
            except Exception:  # noqa: BLE001 - a piece that cannot be read is left out
                continue
            for row in list(left):
                if _within_share(piece, row["extents_mm"]) >= 0.9:
                    row["copper"] = True
                    left.remove(row)
    return rows


def _within_share(inner: tuple[float, ...], outer: Any) -> float:
    """How much of ``inner``'s box lies within ``outer``'s."""
    w = min(inner[2], outer[2]) - max(inner[0], outer[0])
    h = min(inner[3], outer[3]) - max(inner[1], outer[1])
    area = max((inner[2] - inner[0]) * (inner[3] - inner[1]), 1e-12)
    return w * h / area if w > 0 and h > 0 else 0.0


def plane_groups(rows: list[dict[str, Any]], generated: set[tuple[str, int]] | None = None) -> list[dict[str, Any]]:
    """The plane shapes grouped by net, layer and fill, each group with how many shapes it holds and
    the extents they cover together: a board of many small power shapes stays whole in one response.
    With ``generated``, each group says whether Layout made copper for its net on its layer: a shape
    that touches no pad, trace or via of its net there gets none."""
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        fill = {k: row[k] for k in ("hatch", "hatch_width_mm", "hatch_distance_mm") if row.get(k) is not None}
        made = {} if generated is None else {"generated": (row["net"], row["layer"]) in generated}
        group = groups.setdefault((row["net"], row["layer"], tuple(sorted(fill.items()))),
                                  {"net": row["net"], "layer": row["layer"], **fill, **made, "shapes": 0, "extents_mm": None})
        group["shapes"] += 1
        if row.get("copper") is False:  # this shape got none, whatever the others of its net and layer got
            group["generated"] = False
            group["without_copper"] = group.get("without_copper", 0) + 1
        box = row.get("extents_mm")
        if box:
            now = group["extents_mm"]
            group["extents_mm"] = box if now is None else [min(now[0], box[0]), min(now[1], box[1]), max(now[2], box[2]),
                                                           max(now[3], box[3])]
    return sorted(groups.values(), key=lambda g: (g["net"], g["layer"] or 0))


def via_padstacks(doc: Any, scale: float | None) -> list[dict[str, Any]]:
    """The via padstacks of the design's own library, with how many vias of the board use each and
    its pad diameter: from a via that uses it, or else from the library's padstack."""
    from .layout_edit import _padstack_pad_radius, local_padstack

    names = _quietly(lambda: [str(name) for name in (com.propget(doc, PADSTACK_NAMES_DISPID, PADSTACK_VIA, -1, "*", False) or ())], [])
    used: dict[str, int] = {}
    pads: dict[str, float] = {}
    for via in L.collection(doc, "Vias"):
        name = L.name_of(via)
        if not name:
            continue
        used[name] = used.get(name, 0) + 1
        if name not in pads and scale:
            pads[name] = round(2 * _via_radius(via, scale), 4)
    rows = []
    for name in sorted(set(names) | set(used)):
        row: dict[str, Any] = {"name": name, "vias": used.get(name, 0)}
        if name not in pads and scale:
            radius = _quietly(lambda name=name: _padstack_pad_radius(local_padstack(doc, name), scale))
            if radius:
                pads[name] = round(2 * radius, 4)
        if name in pads:
            row["pad_mm"] = pads[name]
        rows.append(row)
    return rows


def geometry_summary(model: dict[str, Any]) -> dict[str, Any]:
    """The geometry in short: the outline and how many objects the model holds. The whole model
    of a real board is far larger than one response may be."""
    counts = {name: len(model[name]) for name in ("components", "traces", "vias", "unrouted")}
    counts["placed"] = sum(1 for item in model["components"] if item.get("placed"))
    outline = model["outline"][0] if model["outline"] else {}
    return {"available": True, "outline_mm": outline.get("path") or outline.get("circle"), "counts": counts}


def metrics_summary(model: dict[str, Any]) -> dict[str, Any]:
    measured = board_metrics.measure(model)
    return {"available": True, "board": measured["board"], "summary": measured["summary"],
            "unplaced": measured["parts"]["unplaced"][:LISTED], "supply_nets": measured["ratsnest"]["supply_nets"][:LISTED],
            "trace_mm_by_layer": measured["routing"]["by_layer"], "pairs": measured["routing"]["pairs"][:LISTED]}


def flex_model(doc: Any, traces: list[dict[str, Any]] | None = None, vias: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """What the flex rules of ``board_metrics`` read: the planes, keepouts and outline, with the
    traces and vias given."""
    scale = L.geometry_scale(doc)
    outline = doc.BoardOutline
    record = _quietly(lambda: geometry_record(outline.Geometry, scale, kind="outline")) if outline is not None and scale else None
    return {"planes": _quietly(lambda: plane_shapes(doc), []), "keepouts": _quietly(lambda: keepouts(doc, scale), []),
            "outline": [record] if record else [], "traces": traces or [], "vias": vias or []}


def bend_boxes(doc: Any, flex: dict[str, Any] | None = None) -> list[tuple[float, float, float, float]]:
    """The extents of the board's bend zones, as pcb get lists them; none on a rigid board.
    ``flex`` is a ``flex_model`` already read."""
    return [(z["extents_mm"][0], z["extents_mm"][1], z["extents_mm"][2], z["extents_mm"][3])
            for z in board_metrics.bend_zones(flex or flex_model(doc))]


def board_ears(doc: Any) -> list[dict[str, Any]]:
    """The ears of the board outline, as ``plan.ears`` finds them."""
    from .layout_edit import _outline_rows
    from .plan import ears, lanes

    return ears.ears(lanes.outline_corners(_outline_rows(doc)))


def board_openings(doc: Any) -> list[dict[str, Any]]:
    from .layout_edit import listed_openings

    return listed_openings(doc)


def board_user_layers(doc: Any) -> list[str]:
    from .layout_silk import user_layers

    return user_layers(doc)


def silkscreen_texts(doc: Any) -> list[dict[str, Any]]:
    from .layout_silk import listed

    return listed(doc)


def board_facts(doc: Any, path: str, full: bool = False) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The board in numbers, and the model they were measured on: with ``full``, the whole model
    pcb get --output writes, read once for both."""
    layer_count = int(doc.LayerCount)
    components = L.collection(doc, "Components")
    placed = sum(1 for item in components if bool(item.Placed))
    status = _quietly(lambda: L.route_status(doc))
    nets: dict[str, Any] = {"total": L.count(doc, "Nets"), "routed": None, "open": None}
    if status is not None:
        still_open = sorted(name for name, row in status.items() if not row["routed"])
        nets.update(total=len(status), routed=len(status) - len(still_open), open=len(still_open))
    if full:
        model = board_model(doc, full=True, status=status) if L.geometry_scale(doc) is not None else None
    else:
        model = _quietly(lambda: board_model(doc, full=False, status=status)) if status is not None else None
    scale = L.geometry_scale(doc)
    unavailable = {"available": False, "reason": UNIT_NOTE if scale is None else "The geometry could not be read."}
    blocked = _quietly(lambda: keepouts(doc, scale), [])
    shapes = _quietly(lambda: plane_shapes(doc), [])
    return ({
        "path": path, "layers": layer_count,
        "components": {"total": len(components), "placed": placed, "unplaced": len(components) - placed},
        "nets": nets, "traces": L.count(doc, "Traces"), "vias": L.count(doc, "Vias"),
        "outline": _quietly(lambda: L.outline_values(doc)), "stackup": _quietly(lambda: board_stackup(doc)),
        "net_classes": _quietly(lambda: net_classes(doc, layer_count)), "keepouts": _quietly(lambda: keepout_count(doc)),
        "keepout_kinds": _quietly(lambda: keepout_summary(blocked)),
        "bend_zones": _quietly(lambda: board_metrics.bend_zones({"planes": shapes, "keepouts": blocked,
                                                                 "outline": model["outline"] if model else []})[:LISTED]),
        "cutouts": _quietly(lambda: len(cutouts(doc, None))),
        "ears": _quietly(lambda: board_ears(doc)[:LISTED]),
        "openings": _quietly(lambda: board_openings(doc)[:LISTED]),
        "user_layers": _quietly(lambda: board_user_layers(doc)),
        "silkscreen_texts": _quietly(lambda: silkscreen_texts(doc)),
        "planes": _quietly(lambda: plane_groups(with_copper(doc, shapes), generated_planes(doc))[:LISTED]),
        "via_padstacks": _quietly(lambda: via_padstacks(doc, scale)),
        "geometry": geometry_summary(model) if model is not None else unavailable,
        "metrics": _quietly(lambda: metrics_summary(model), unavailable) if model is not None else unavailable,
    }, model)


class BoardGet(base.Handler):
    """The open board in numbers: counts, stackup, net classes and widths, keepouts, a geometry
    summary and the layout metrics."""

    command = "pcb get"

    def read(self, ctx: Context) -> dict[str, Any]:
        path = ctx.document_path()
        output = ctx.inputs.get("output")

        def work(app: Any, doc: Any) -> dict[str, Any]:
            facts, model = board_facts(doc, path, full=bool(output))
            if output:
                full = {**facts, "model": model, "metrics_full": board_metrics.measure(model) if model else None}
                facts["output"] = write_output(str(output), full)
            if isinstance(facts.get("geometry"), dict):  # a curved outline's path fills the response; --output holds it
                facts["geometry"] = {k: v for k, v in facts["geometry"].items() if k != "outline_mm"}
            return fitted_with_counts(facts, CUT_FIRST)

        return L.in_layout(ctx, work, budget_s=L.READ_BUDGET_S, what="reading the board")


CUT_FIRST = ["net_classes", "via_padstacks", "planes", "silkscreen_texts", "openings", "bend_zones", "ears"]  # in this order: constraint get gives the classes whole


def fitted_with_counts(out: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    """``out`` cut to fit one response as ``base.fitted`` cuts it, with ``<key>_count`` beside each
    list it shortened: --output holds them whole."""
    whole = {key: len(out[key]) for key in keys if isinstance(out.get(key), list)}
    out = base.fitted(out, keys)
    for key, count in whole.items():
        if len(out.get(key) or []) < count:
            out[f"{key}_count"] = count
    return out


def write_output(path: str, data: dict[str, Any]) -> str:
    """Write the whole result to ``--output`` and return the path; the response keeps the summary (DCLI-008)."""
    target = os.path.abspath(path)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=1, default=str)
    return target


# ------------------------------------------------------------------ pcb check

BATCH_DRC_COMMAND = 32769  # Analysis > Batch DRC... (an MFC command id; Batch DRC has no automation call)
HAZARDS_BATCH = 2  # epcbHazardTypeBatch
HAZARD_X, HAZARD_Y, HAZARD_ACTUAL, HAZARD_REQUIRED = 9, 10, 16, 17  # IMGCPCBHazard property dispids, taking an EPcbUnit
HAZARD_OBJECTS = 6
DRC_WAIT_S = 18.0
DRC_QUIET_S = 6.0  # no Processing window this long after the command: the run is over
DRC_POLL_S = 0.5
WARNING_KINDS = frozenset({"ViasUnderParts"})  # a via under a part is normal with tented vias
RULE_SEVERITY = {"high": "error", "medium": "warning", "low": "info"}
SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
CHECK_LISTED = 10  # hazards and rules each; a longer result would pass the 8000-character response limit
NETS_LISTED = 5  # nets with connectivity problems, each with its first islands
ISLANDS_LISTED = 2
CONNECTIVITY_KINDS = {"Hangers": "hangers", "TraceLoops": "loops", "PlaneIslands": "plane_islands"}
TIE_STARTS = 8  # the points of an island tried for a path back to the rest of its net
TIE_WIDTH_MM = 0.1  # a path back is this wide unless the island's traces are narrower
DESCRIPTION_LIMIT = 160


def run_batch_drc(app: Any, pid: int | None) -> dict[str, Any]:
    """Batch DRC on the open board through its menu command; the caller answers its dialog
    (``DRC_PROMPTS``). Layout shows a Processing window while it checks; the run is over when the
    window has come and gone, or has not come within ``DRC_QUIET_S``."""
    started = time.monotonic()
    try:
        accepted = app.Gui.ProcessCommand(BATCH_DRC_COMMAND)
    except Exception as exc:  # noqa: BLE001 - classified
        raise L.com_error(exc, "starting Batch DRC", "not_started") from None
    if not accepted:
        raise AdapterError("E_NATIVE_FAILURE", "Layout did not accept the Batch DRC command.", dispatch_state="not_started")
    seen = False
    while True:
        elapsed = time.monotonic() - started
        try:
            busy = bool(dialogs.processing_windows(pid))
        except Exception:  # noqa: BLE001 - the windows cannot be read
            busy = False
        if busy:
            seen = True
        elif seen or elapsed >= DRC_QUIET_S:
            break
        if elapsed > L.job_budget(DRC_WAIT_S):
            raise AdapterError("E_TIMEOUT", f"Batch DRC was still running after {L.job_budget(DRC_WAIT_S):g} s, and Layout goes on with it. Wait until "
                               "the session is ready again (session get), then run pcb check again.", dispatch_state="not_started")
        time.sleep(DRC_POLL_S)
    return {"seconds": round(time.monotonic() - started, 1), "processing_seen": seen}


def _constants_table() -> Any:
    import win32com.client

    return win32com.client.constants


def hazard_names(app: Any) -> dict[int, str]:
    """Hazard type numbers to names (``PartialNets``, ``ViasUnderParts``...), from the type
    library's ``epcbHazardType*`` constants, which win32com fills once the library is loaded."""
    try:
        com.late_bound(app)
        table = _constants_table()
    except Exception:  # noqa: BLE001 - the numbers stand without names
        return {}
    names: dict[int, str] = {}
    for mapping in getattr(table, "__dicts__", None) or []:
        for key, value in mapping.items():
            if key.startswith("epcbHazardType") and isinstance(value, int):
                names.setdefault(value, key[len("epcbHazardType"):])
    return names


def _mm(owner: Any, dispid: int) -> float | None:
    try:
        return round(float(com.propget(owner, dispid, L.UNIT_MM)), 4)
    except Exception:  # noqa: BLE001 - not reported for this hazard
        return None


def _object_name(item: Any) -> str:
    """What a hazard names: ``R1.2`` for a pin, ``U1`` for a part's outline, else its net or kind."""
    try:
        kind = str(item._oleobj_.GetTypeInfo().GetDocumentation(-1)[0])
    except Exception:  # noqa: BLE001 - no type information
        kind = ""
    refdes = _quietly(lambda: str(item.Component.RefDes or ""), "")
    if refdes and "Pin" in kind:
        return f"{refdes}.{L.name_of(item)}"
    if refdes:
        return refdes
    return L.net_name(item) or kind.replace("IMGCPCB", "") or "object"


LOST_LABEL = re.compile(r"^\?+\s*(\d*)\s*:\s*")  # a lost label, with the number some labels end in


def hazard_text(text: str) -> str:
    """A hazard's description on one line. Layout in a language other than English hands its
    labels over COM as question marks (``???? : CC1``): those labels are left out and the values
    kept, ``CC1; J2-11; 1``."""
    values = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        line = LOST_LABEL.sub(lambda m: f"{m.group(1)}: " if m.group(1) else "", line)
        if line and set(line) - {"?", " "}:
            values.append(line)
    return "; ".join(values)


def hazard_record(hazard: Any, names: dict[int, str]) -> dict[str, Any]:
    kind = _quietly(lambda: int(hazard.Type))
    name = names.get(kind, f"type {kind}") if kind is not None else "unknown"
    actual, required = _mm(hazard, HAZARD_ACTUAL), _mm(hazard, HAZARD_REQUIRED)
    objects = _quietly(lambda: [_object_name(item) for item in L.items(com.member(hazard, "Objects"))[:HAZARD_OBJECTS]], [])
    return {"type": name, "severity": "warning" if name in WARNING_KINDS else "error",
            "description": _quietly(lambda: hazard_text(hazard.Description)[:DESCRIPTION_LIMIT], ""), "x_mm": _mm(hazard, HAZARD_X),
            "y_mm": _mm(hazard, HAZARD_Y),
            "objects": objects, "actual_mm": actual if actual is None or actual >= 0 else None,
            "required_mm": required if required is None or required >= 0 else None}


def rule_findings(model: dict[str, Any] | None, reason: str = UNIT_NOTE) -> list[dict[str, Any]]:
    if model is None:
        return [{"id": "board_rules-1", "severity": "info", "rule": "board_rules_unavailable", "message": reason, "objects": []}]
    return finding_rows(board_metrics.board_rules(model))


def requirement_facts(doc: Any, requirements: list[RQ.Requirement]) -> dict[str, Any]:
    """What the opening and text requirements measure beyond the board model: the board's openings
    with the copper each bares, the outline's ears, and the board's own texts with where they lie."""
    from .layout_edit import opening_facts
    from .layout_silk import board_texts

    kinds = {item.kind for item in requirements}
    facts: dict[str, Any] = {}
    if "opening" in kinds:
        clearance = max(item.values["clearance_mm"] for item in requirements if item.kind == "opening")
        facts["openings"] = opening_facts(doc, clearance)
        facts["ears"] = _quietly(lambda: board_ears(doc), [])
    if "text" in kinds:
        facts["board_texts"] = [{"text": words, "place": place} for _text, words, place in board_texts(doc)]
    return facts


def empty_plane_rows(doc: Any) -> list[dict[str, Any]]:
    """pcb check's rule for plane shapes that hold no copper while others of their net on their layer
    do: the shape touches no pad, trace or via of its net there, such as a band hatched over a bend
    zone on an inner ground layer."""
    rows = with_copper(doc, plane_shapes(doc))
    made = {(row.get("net"), row.get("layer")) for row in rows if row.get("copper")}
    out = []
    for row in rows:
        if row.get("copper") is False and (row.get("net"), row.get("layer")) in made:
            x0, y0, x1, y1 = row["extents_mm"]
            fill = row.get("hatch") or "solid"
            out.append({"rule": "plane_without_copper", "severity": "medium", "net": row.get("net"),
                        "finding": f"the {fill} {row.get('net')} shape on layer {row.get('layer')} at {x0:g},{y0:g} to {x1:g},{y1:g} mm "
                                   f"holds no copper: it touches no pad, trace or via of {row.get('net')} on its layer. Tie it with a "
                                   f"trace of {row.get('net')} across it into the copper beside it; plane create --bends does this "
                                   "for the bands over bend zones"})
    return out


def finding_rows(rows: list[dict[str, Any]], first: int = 1) -> list[dict[str, Any]]:
    """Board rule rows as pcb check lists them, numbered from ``first``."""
    return [{"id": f"{row['rule']}-{index}", "severity": RULE_SEVERITY.get(row["severity"], "info"), "rule": row["rule"],
             "message": row["finding"], "objects": [str(v) for v in (row.get("refdes"), row.get("other"), row.get("net")) if v]}
            for index, row in enumerate(rows, first)]


def check_board(app: Any, doc: Any, pid: int | None, path: str, output: str | None = None,
                requirements: list[RQ.Requirement] | None = None) -> dict[str, Any]:
    """Run Batch DRC and read its hazards and the board rules; group the connectivity problems by
    net, with each island Layout's connectivity finds and a path back to the rest of its net; and
    measure the ``requirements``. The lists carry the first entries, errors first, so that the
    result fits one response; the counts are whole."""
    drc = run_batch_drc(app, pid)
    try:
        found = L.items(doc.GetHazards(HAZARDS_BATCH))
        names = hazard_names(app) if found else {}  # loading the type library costs time; only for names
        hazards = [hazard_record(item, names) for item in found]
    except Exception as exc:  # noqa: BLE001 - a hazard list that cannot be read is not an empty one
        raise L.com_error(exc, "reading the Batch DRC hazards", "not_started") from None
    hazards.sort(key=lambda row: (row["severity"] != "error", row["type"]))
    model = _quietly(lambda: board_model(doc, full=True)) if L.geometry_scale(doc) is not None else None
    if L.geometry_scale(doc) is None:
        rules = rule_findings(None)
    else:
        rules = rule_findings(model, "The board geometry could not be read, so the board rules were not checked; the hazards above stand.")
        from .layout_edit import opening_rows
        rules += finding_rows(_quietly(lambda: opening_rows(doc), []), len(rules) + 1)
        rules += finding_rows(_quietly(lambda: empty_plane_rows(doc), []), len(rules) + 1)
    rules.sort(key=lambda row: SEVERITY_ORDER.get(row["severity"], 3))
    severities = [row["severity"] for row in hazards] + [row["severity"] for row in rules]
    out: dict[str, Any] = {"hazards": hazards[:CHECK_LISTED], "rules": rules[:CHECK_LISTED], "errors": severities.count("error"),
                           "warnings": severities.count("warning"), "hazard_count": len(hazards), "rule_count": len(rules), "drc": drc}
    read: list[GD.Obstacles | None] = []

    def board_copper() -> GD.Obstacles | None:  # read once, and only when an island or a via pitch needs it
        if not read:  # the clearance rules come from Constraint Manager: when it cannot be read, DRC's findings stand alone
            from .layout_edit import board_obstacles, copper_from_model
            nets = {L.name_of(net) for net in L.collection(doc, "Nets")}
            read.append(_quietly(lambda: board_obstacles(doc, path, copper_from_model(model, int(doc.LayerCount), nets))))
        return read[0]

    problems = connectivity(doc, hazards, board_copper if model is not None else None)
    out["connectivity"], out["connectivity_count"] = problems[:NETS_LISTED], len(problems)
    if requirements:
        if model is None:
            raise AdapterError("E_UNSUPPORTED", "The board geometry could not be read, so the requirements were not measured; the board "
                               "must count in mils or mm and have an outline.", dispatch_state="not_started")
        bends = [tuple(z["extents_mm"]) for z in board_metrics.bend_zones(model)]
        flagged = _quietly(lambda: with_copper(doc, [dict(row) for row in model.get("planes") or []]), [])
        model = {**model, "planes": [{**row, "generated": row.get("copper")} for row in flagged]}
        model.update(requirement_facts(doc, requirements))
        measured = RQ.measure(model, requirements, bends, board_copper())
        out["requirements"] = RQ.listed(measured)
        out["requirements_failed"] = sum(1 for row in measured if row.get("ok") is False)
    if output:
        whole = {**out, "hazards": hazards, "rules": rules, "connectivity": problems}
        if requirements:
            whole["requirements"] = measured
        out["output"] = write_output(output, whole)
    return base.fitted(out, ["rules", "hazards", "connectivity", "requirements"])


def connectivity(doc: Any, hazards: list[dict[str, Any]], board_copper: Any) -> list[dict[str, Any]]:
    """Each net with a connectivity problem: DRC's dangling ends, loops and cut-off pour counted, and
    the islands Layout's connectivity finds (``Subnet``: which connected piece of its net an object
    belongs to, planes included), each with its pins, traces and vias and, where one clears, a path
    that joins it to the rest of its net (``board_copper`` gives the copper it must clear)."""
    rows: dict[str, dict[str, Any]] = {}
    for hazard in hazards:
        kind = CONNECTIVITY_KINDS.get(hazard["type"])
        if kind:
            net = (hazard.get("description") or "").split(";")[0].strip()
            row = rows.setdefault(net, {"net": net})
            row[kind] = row.get(kind, 0) + 1
    scale = L.geometry_scale(doc)
    layer_count = int(doc.LayerCount)
    for net in L.collection(doc, "Nets"):
        if scale is None or _quietly(lambda net=net: int(net.NumberOfOpens or 0), 0) <= 0:
            continue
        name = L.name_of(net)
        pieces = _quietly(lambda net=net: _pieces(net, scale))
        if pieces is None:
            rows.setdefault(name, {"net": name})["reason"] = "its connected pieces could not be read"
            continue
        if len(pieces) < 2:
            continue
        main = max(pieces, key=lambda piece: (len(piece["pins"]), piece["traces"] + len(piece["vias"])))
        islands = sorted((piece for piece in pieces if piece is not main), key=lambda piece: (bool(piece["pins"]), -piece["traces"]))
        row = rows.setdefault(name, {"net": name})
        row["pieces"] = len(pieces)
        listed = []
        for piece in islands[:ISLANDS_LISTED]:
            item = {"pins": piece["pins"][:5], "traces": piece["traces"], "vias": len(piece["vias"]), "at_mm": piece["at"],
                    "floating": not piece["pins"]}
            copper = board_copper() if item["floating"] and board_copper is not None else None  # pins: a connection not routed yet
            if copper is not None:
                tie = _quietly(lambda piece=piece, copper=copper: _tie(name, piece, main, copper, layer_count))
                if tie:
                    item["tie"] = tie
            listed.append(item)
        row["islands"] = listed
        row["floating"] = sum(1 for piece in islands if not piece["pins"])
        if len(islands) > ISLANDS_LISTED:
            row["islands_omitted"] = len(islands) - ISLANDS_LISTED
    for row in rows.values():
        row["next"] = _connectivity_next(row)
    return sorted(rows.values(), key=lambda row: (-row.get("floating", 0), -row.get("pieces", 1), row["net"]))


def _pieces(net: Any, scale: float) -> list[dict[str, Any]]:
    """The connected pieces of a net, by each object's ``Subnet``: their pins, vias, and traces with their ends."""
    pieces: dict[int, dict[str, Any]] = {}

    def piece(item: Any) -> dict[str, Any]:
        return pieces.setdefault(int(item.Subnet), {"pins": [], "pin_spots": [], "vias": [], "ends": [], "traces": 0, "widths": [],
                                                    "at": None})

    for pin in L.collection(net, "Pins"):
        row = piece(pin)
        x, y = _position(pin)
        row["pins"].append(f"{_quietly(lambda pin=pin: str(pin.Component.RefDes), '?')}.{L.name_of(pin)}")
        row["pin_spots"].append((pin, (x, y)))
        row["at"] = row["at"] or [x, y]
    for via in L.collection(net, "Vias"):
        row = piece(via)
        x, y = _position(via)
        row["vias"].append((x, y))
        row["at"] = row["at"] or [x, y]
    for trace in L.collection(net, "Traces"):
        row = piece(trace)
        row["traces"] += 1
        record = _quietly(lambda trace=trace: geometry_record(trace.Geometry, scale))
        points = board_metrics._trace_points(record) if record and record.get("path") else []
        if len(points) < 2:
            continue
        layer = int(trace.Layer)
        row["widths"].append(float(trace.Geometry.LineWidth or 0.0) * scale)
        for tip, inner in ((points[0], points[1]), (points[-1], points[-2])):
            row["ends"].append((layer, tip, _toward(inner, tip)))
        row["at"] = row["at"] or [round(points[0][0], 4), round(points[0][1], 4)]
    return list(pieces.values())


def _pad_layers(pin: Any) -> list[int]:
    return sorted({int(pad.Layer) for pad in L.collection(pin, "Pads")})


def _tie(net: str, island: dict[str, Any], main: dict[str, Any], obstacles: GD.Obstacles, layer_count: int) -> dict[str, Any] | None:
    """A path that joins an island to the rest of its net: from one of its trace ends, vias or pins
    to a via, pin or trace end of the main piece within reach, clear of the other nets' copper."""
    width = min([TIE_WIDTH_MM, *[w for w in island["widths"] if w > 0]])
    reach = GD.JOIN_REACH_MM
    starts: list[tuple[list[int], tuple[float, float], tuple[float, float] | None]] = []
    starts += [([layer], tip, outward) for layer, tip, outward in island["ends"]]
    starts += [(list(range(1, layer_count + 1)), at, None) for at in island["vias"]]
    starts += [(_quietly(lambda pin=pin: _pad_layers(pin), []), at, None) for pin, at in island["pin_spots"]]
    near_pins = [(spot, _quietly(lambda pin=pin: _pad_layers(pin), [])) for pin, spot in main["pin_spots"]
                 if any(_close(spot, at, reach) for _layers, at, _o in starts[:TIE_STARTS])]
    for layers, at, outward in starts[:TIE_STARTS]:
        for on in layers:
            targets = list(main["vias"]) + [tip for end_layer, tip, _o in main["ends"] if end_layer == on]
            targets += [spot for spot, pad_layers in near_pins if on in pad_layers]
            targets = [t for t in targets if _close(t, at, reach)]
            if not targets:
                continue
            way = outward or _toward(at, min(targets, key=lambda t: (t[0] - at[0]) ** 2 + (t[1] - at[1]) ** 2))
            path = GD.join(at, way, net, on, width, obstacles, targets, reach)
            if path:
                return {"layer": on, "width_mm": round(width, 4), "path_mm": [[round(p[0], 4), round(p[1], 4)] for p in path]}
    return None


def _close(a: tuple[float, float], b: tuple[float, float], reach: float) -> bool:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 <= reach * reach


def _toward(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    length = ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5 or 1.0
    return (b[0] - a[0]) / length, (b[1] - a[1]) / length


def _connectivity_next(row: dict[str, Any]) -> list[str]:
    net, steps = row["net"], []
    if row.get("floating"):
        steps.append(f"{row['floating']} pieces of {net} hold no pin and join the rest of it nowhere: add each one's tie path to the "
                     f"routing plan as a trace of {net} on its layer, or delete the piece")
    if row.get("pieces", 1) - 1 - row.get("floating", 0) > 0:
        steps.append(f"{row['pieces'] - 1 - row.get('floating', 0)} pieces of {net} with pins are not joined to the rest yet: route them")
    if row.get("loops"):
        steps.append(f"{net} closes {row['loops']} loops: join each pin and via one way only, as a tree")
    if row.get("hangers"):
        steps.append(f"{row['hangers']} trace ends of {net} touch nothing: extend them to copper of {net} or cut them back")
    if row.get("plane_islands"):
        steps.append(f"{row['plane_islands']} pieces of {net}'s pour are cut off: tie them to its copper with a via or a trace")
    return steps


class BoardCheck(base.Handler):
    """Layout's Batch DRC on the open board, its hazards (``GetHazards``), the board rules DRC does
    not cover (``plan.board_metrics.board_rules``), each net's connected pieces, and the
    requirements of a file (``plan.requirements``). Not ``MGCPCBEngines.DRC``: that engine saves and
    closes the board."""

    command = "pcb check"
    answers = dialogs.DRC_PROMPTS

    def read(self, ctx: Context) -> dict[str, Any]:
        ref = ctx.need_session(base.LAYOUT)
        requirements = None
        if ctx.inputs.get("requirements"):
            try:
                requirements = RQ.load(str(ctx.inputs["requirements"]))
            except PlanError as exc:
                raise AdapterError(exc.code, exc.message, dispatch_state="not_started") from None
        path = ctx.document_path()
        return L.in_layout(ctx, lambda app, doc: check_board(app, doc, ref.pid, path, ctx.inputs.get("output"), requirements),
                           budget_s=L.job_budget(L.READ_BUDGET_S), rules=dialogs.DRC_PROMPTS, what="running Batch DRC")


# ------------------------------------------------------------------ pcb export

OUTPUT_COMMANDS = (("ncdrill", 33018), ("odb", 33017), ("gerber", 33016))  # Output menu commands, in this order
OUTPUT_TIMEOUT_S = 300.0
OUTPUT_SETTLE_S = 2.0
OUTPUT_POLL_S = 1.0
OUTPUT_ROUNDS = 2  # a dialog can write its own settings back over a patched setup: patch and run once more
SETUPS = (("ODBSetup.ocf", fab_package.patch_odb_setup), ("ODBSetup.eocf", fab_package.patch_odb_setup),
          ("PlotSetup.gpf", fab_package.patch_gerber_setup), ("GerberPlot.egpf", fab_package.patch_gerber_setup))
BACKUP_SUFFIX = ".bak-xpedition-cli"
PACKAGE_FILES = ("manifest.json", "README.md", "centroid.csv", "bom.csv")
DIALOG_CLASS = "#32770"
WS_MINIMIZEBOX = 0x00020000


def _has_entries(folder: str) -> bool:
    with os.scandir(folder) as entries:
        return any(True for _ in entries)


def _output(ctx: Context) -> str:
    target = os.path.abspath(str(base.need_input(ctx, "output")))
    if os.path.isfile(target):
        raise AdapterError("E_VALIDATION", f"{target} is a file; --output names the folder for the package.", dispatch_state="not_started")
    if os.path.isdir(target) and _has_entries(target):
        raise AdapterError("E_VALIDATION", f"{target} is not empty; name a new or empty folder with --output.", dispatch_state="not_started")
    return target


def pending_setups(config: Path) -> list[str]:
    """The setup files that lack the package's changes (drill spans and the outline in the ODB++
    job, the cells' silkscreen and the outline in the Gerber set)."""
    names: list[str] = []
    for name, patcher in SETUPS:
        path = config / name
        try:
            if path.is_file() and patcher(path.read_text(encoding="utf-8", errors="replace"))[1]:
                names.append(name)
        except OSError:
            continue
    return names


def patch_setups(config: Path) -> list[str]:
    """Patch the setup files that need it, each backed up once; the names patched."""
    patched: list[str] = []
    for name, patcher in SETUPS:
        path = config / name
        try:
            if not path.is_file():
                continue
            text, changes = patcher(path.read_text(encoding="utf-8", errors="replace"))
            if not changes:
                continue
            backup = config / f"{name}{BACKUP_SUFFIX}"
            if not backup.exists():
                backup.write_bytes(path.read_bytes())
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            raise AdapterError("E_NATIVE_FAILURE", f"The output setup {name} could not be patched: {exc}",
                               knowledge=["layout-output-setups"]) from None
        patched.append(name)
    return patched


def output_windows(pid: int | None) -> list[str]:
    """Layout's output dialogs and progress boxes: titled windows that are neither a document
    (``[...]``) nor the main window."""
    found: list[str] = []
    for window in dialogs.top_windows(pid):
        title = str(window.get("title") or "")
        main = not window.get("owner") and bool(window.get("style", 0) & WS_MINIMIZEBOX) and window.get("class") != DIALOG_CLASS
        if title and not title.startswith("[") and not main:
            found.append(title)
    return found


def run_output(app: Any, pid: int | None, command: int, progress: Any) -> dict[str, Any]:
    """One Output menu command: its dialog's OK is pressed meanwhile (``OUTPUT_PROMPTS``), and the
    run waits until the dialog and its progress box are gone."""
    started = time.monotonic()
    with dialogs.PromptAnswerer(dialogs.OUTPUT_PROMPTS, pid, interval_s=0.7), L.watch_dialogs(progress, pid, dialogs.OUTPUT_PROMPTS):
        try:
            accepted = app.Gui.ProcessCommand(command)
        except Exception as exc:  # noqa: BLE001 - classified
            raise L.com_error(exc, f"running the Output command {command}", com.call_state(exc)) from None
        if not accepted:
            raise AdapterError("E_NATIVE_FAILURE", f"Layout did not accept the Output command {command}.", dispatch_state="dispatched")
        time.sleep(OUTPUT_SETTLE_S)
        deadline = time.monotonic() + OUTPUT_TIMEOUT_S
        while time.monotonic() < deadline and output_windows(pid):
            time.sleep(OUTPUT_POLL_S)
    return {"command": command, "seconds": round(time.monotonic() - started, 1), "finished": not output_windows(pid)}


def package_components(doc: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for component in L.collection(doc, "Components"):
        refdes = str(component.RefDes or "")
        if not refdes:
            continue
        placed = bool(component.Placed)
        x, y = _position(component) if placed else (None, None)
        rows.append({"refdes": refdes, "part_number": str(component.PartNumber or ""), "footprint": str(component.CellName or ""),
                     "x": x, "y": y, "rotation": float(component.Orientation or 0.0), "side": _side(component), "placed": placed})
    return rows


class BoardExport(base.Handler):
    """The fabrication package (``plan.fab_package``) from the open board, which stays open.

    Layout has no automation call for ODB++, Gerber or NC drill: their Output menu commands run
    with the dialogs answered. The setups in the board's ``Config`` folder are patched first;
    the dialogs keep their settings in memory and may write them back over the patch, so when a
    setup lacks the patch after the run it is patched and run once more (knowledge
    ``layout-output-setups``)."""

    command = "pcb export"
    answers = dialogs.OUTPUT_PROMPTS
    long_job = True

    def preview(self, ctx: Context) -> dict[str, Any]:
        output = _output(ctx)
        path = ctx.document_path()
        L.in_layout(ctx, lambda app, doc: None, budget_s=L.READ_BUDGET_S, what="checking the open board")
        board_dir = Path(path).parent
        pending = pending_setups(board_dir / "Config")
        targets = [base.target(output, "folder", label="fabrication package"),
                   base.target(str(board_dir / "Output"), "folder", label="Layout's output folder")]
        changes = [base.change(base.key(os.path.join(output, name)), "exists", False, True) for name in PACKAGE_FILES]
        patch = ""
        if pending:
            targets.append(base.target(str(board_dir / "Config"), "folder", label="output setups"))
            changes += [base.change(base.key(str(board_dir / "Config" / name)), "patched", False, True) for name in pending]
            patch = f"First patch the output setups {', '.join(pending)} (each backed up once). "
        return base.preview(f"{patch}Run NC drill, ODB++ and Gerber through Layout's Output dialogs into the board's Output folder, "
                            f"then write the package into {output}: the outputs under board-house names, a centroid file, a BOM, a "
                            "README and a manifest. The board stays open and is not saved.", targets, changes)

    def run(self, ctx: Context, progress: Any) -> dict[str, Any]:
        ref = ctx.need_session(base.LAYOUT)
        path = ctx.document_path()
        target = Path(_output(ctx))
        return L.run_com(lambda: self._export(ref.pid, path, target, progress))

    def _export(self, pid: int, path: str, target: Path, progress: Any) -> dict[str, Any]:
        app = L.attach(pid)
        doc = L.board(app, path)
        pcb = Path(path)
        config, outputs = pcb.parent / "Config", pcb.parent / "Output"
        since = time.time()
        runs: list[dict[str, Any]] = []
        patched: list[str] = []
        step, total = 0, len(OUTPUT_COMMANDS) + 1
        try:
            for round_number in range(1, OUTPUT_ROUNDS + 1):
                patched += patch_setups(config)
                for name, command in OUTPUT_COMMANDS:
                    progress(step, total, "steps")
                    runs.append({**run_output(app, pid, command, progress), "format": name, "round": round_number})
                    step += 1
                if not pending_setups(config) or round_number == OUTPUT_ROUNDS:
                    break
                total += len(OUTPUT_COMMANDS)
        except AdapterError as exc:
            if not (patched or runs):
                raise
            # setups patched or outputs written: some files changed, the package was not written
            data = {k: v for k, v in exc.data.items() if k not in ("code", "dispatch_state", "change", "persistence")}
            raise AdapterError(exc.data.get("code", "E_NATIVE_FAILURE"), f"{exc} The package was not written.",
                               dispatch_state="dispatched", change="not_applicable", persistence="partial", **data) from None
        progress(step, total, "steps")
        rect = _quietly(lambda: L.board_rect(doc))
        size = {"width": round(rect[2] - rect[0], 3) if rect else None, "height": round(rect[3] - rect[1], 3) if rect else None,
                "unit": "mm"}
        layers = int(_quietly(lambda: doc.LayerCount, 0) or 0)
        odb_root = outputs / "ODBpp"
        odb_dir = next((item for item in sorted(odb_root.iterdir()) if item.is_dir()), None) if odb_root.is_dir() else None
        try:
            manifest = fab_package.write_package(target, pcb.stem, size, layers, outputs / "Gerber", outputs / "NCDrill", odb_dir,
                                                 package_components(doc), requested=[name for name, _ in OUTPUT_COMMANDS], runs=runs,
                                                 since=since)
        except OSError as exc:
            raise AdapterError("E_NATIVE_FAILURE", f"The package could not be written: {exc}", dispatch_state="dispatched") from None
        files = [*manifest["files"], str(target / "manifest.json")]
        progress(total, total, "steps")
        # the receipt records every file with its size and SHA-256; the result keeps only the count
        # and the manifest, so that job get stays within one response (DCLI-008)
        result = {"path": str(target), "file_count": len(files), "manifest": str(target / "manifest.json"),
                  "board": {"width_mm": size["width"], "height_mm": size["height"], "layers": layers},
                  "problems": manifest["checks"]["problems"], "setups_patched": sorted(set(patched)),
                  "setups_rewritten": pending_setups(config)}
        return {"change": "not_applicable", "persistence": "saved", "result": result, "files": [{"path": item} for item in files]}

    def read_back(self, ctx: Context) -> dict[str, Any]:
        """Which of the previewed files exist, and whether each output setup the run patched now
        holds the package's changes."""
        fields = list(ctx.request.get("fields") or [])
        observed: dict[str, dict[str, Any]] = {}
        for target in ctx.request.get("targets") or []:
            path = path_of(target)
            if not path:
                continue
            row: dict[str, Any] = {}
            if "exists" in fields:
                row["exists"] = os.path.isfile(path)
            if "patched" in fields and Path(path).name in dict(SETUPS):
                row["patched"] = os.path.isfile(path) and Path(path).name not in pending_setups(Path(path).parent)
            if row:
                observed[target] = row
        return {"observed": observed, "unreadable": sorted(field for field in fields if field not in ("exists", "patched"))}


# ------------------------------------------------------------------ pcb render

RENDER_SCALE = 20.0  # pixels per mm unless --resolution says otherwise
VIA_RADIUS_MM = 0.3  # a via whose pads cannot be read is drawn this size


def _png_path(ctx: Context) -> str:
    target = os.path.abspath(str(base.need_input(ctx, "output")))
    if not target.lower().endswith(".png"):
        raise AdapterError("E_VALIDATION", "--output names a .png file.", dispatch_state="not_started")
    if os.path.isdir(target):
        raise AdapterError("E_VALIDATION", f"{target} is a folder; --output names a .png file.", dispatch_state="not_started")
    return target


def _render_inputs(ctx: Context, layer_count: int) -> tuple[str, list[int], float]:
    side = str(ctx.inputs.get("side") or "top").strip().lower()
    if side not in ("top", "bottom"):
        raise AdapterError("E_VALIDATION", "--side is top or bottom.", dispatch_state="not_started")
    try:
        layers = plan_routing.parse_route_layers(ctx.inputs.get("layers"))
    except Exception:  # noqa: BLE001 - the planner says what is wrong
        raise AdapterError("E_VALIDATION", "--layers is a list of conductor layer numbers, such as 1,4.", dispatch_state="not_started") from None
    if any(not 1 <= n <= layer_count for n in layers):
        raise AdapterError("E_VALIDATION", f"The board has conductor layers 1 to {layer_count}.", dispatch_state="not_started")
    resolution = ctx.inputs.get("resolution")
    resolution = RENDER_SCALE if resolution is None else float(resolution)
    if not 1 <= resolution <= 200:
        raise AdapterError("E_VALIDATION", "--resolution is 1 to 200 pixels per mm.", dispatch_state="not_started")
    return side, layers, resolution


def _via_radius(via: Any, scale: float) -> float:
    try:
        for pad in L.collection(via, "Pads"):
            for geometry in L.collection(pad, "Geometries"):
                record = geometry_record(geometry, scale)
                if record and record.get("circle"):
                    return float(record["circle"][2])
                if record and record.get("path"):
                    xs = [p[0] for p in record["path"]]
                    return (max(xs) - min(xs)) / 2
    except Exception:  # noqa: BLE001 - the pads cannot be read
        pass
    return VIA_RADIUS_MM


def board_picture(doc: Any) -> PR.Picture:
    """The board as pcb render draws it: outline, cutouts, pads, traces, vias, plane shapes, holes and openings."""
    from .layout_edit import CONTOUR_INTERNAL, FAB_COVER_LAYER, FAB_SOLDERMASK, SIDE_TOP

    model = board_model(doc, full=True, silk=False)
    scale = L.geometry_scale(doc)
    if model is None or scale is None or not model["outline"]:
        raise AdapterError("E_UNSUPPORTED", UNIT_NOTE + " pcb render also needs a board outline.", dispatch_state="not_started")
    outline = model["outline"][0]
    picture = PR.Picture(outline=PR.polygon_of(outline.get("path"), outline.get("circle")), layer_count=int(doc.LayerCount))
    picture.pads = [(int(p.get("layer") or 1), PR.polygon_of(p.get("path"), p.get("circle"))) for p in model["pads"]]
    picture.traces = [(int(t.get("layer") or 1), [(q[0], q[1]) for q in PR.polygon_of(t.get("path"))], float(t.get("width") or 0.0))
                      for t in model["traces"] if t.get("path")]
    picture.vias = [(float(L.extrema_mm(v)[0] + L.extrema_mm(v)[2]) / 2, float(L.extrema_mm(v)[1] + L.extrema_mm(v)[3]) / 2,
                     _via_radius(v, scale)) for v in L.collection(doc, "Vias")]
    picture.holes = [(x, y, d) for x, y, d in model["holes"]]
    for shape in L.collection(doc, "PlaneShapes"):
        record = geometry_record(shape.Geometry, scale)
        if record:
            picture.planes.append((int(shape.Layer), PR.polygon_of(record.get("path"), record.get("circle"))))
    for piece in L.collection(doc, "GeneratedPlanes"):  # the copper itself, hatching and holes included
        geometry = piece.Geometry
        record = geometry_record(geometry, scale)
        if not record:
            continue
        rings = [PR.polygon_of(record.get("path"), record.get("circle"))]
        for hole in L.collection(geometry, "Cutouts"):
            cut = geometry_record(hole, scale)
            if cut:
                rings.append(PR.polygon_of(cut.get("path"), cut.get("circle")))
        picture.copper.append((int(piece.Layer), rings))
    for contour in L.collection(doc, "Contours"):
        if int(contour.ContourType) == CONTOUR_INTERNAL:
            record = geometry_record(contour.Geometry, scale)
            if record:
                picture.cutouts.append(PR.polygon_of(record.get("path"), record.get("circle")))
    for gfx in L.collection(doc, "FabricationLayerGfxs"):
        try:
            if int(gfx.Type) in (FAB_SOLDERMASK, FAB_COVER_LAYER) and gfx.Component is None:
                record = _quietly(lambda gfx=gfx: geometry_record(gfx.Geometry, scale))
                if record:
                    shape = PR.polygon_of(record.get("path"), record.get("circle"))
                else:  # its shape cannot be read: its box
                    x0, y0, x1, y1 = L.extrema_mm(gfx)
                    shape = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
                picture.openings.append(("top" if int(gfx.Side) == SIDE_TOP else "bottom", shape))
        except Exception:  # noqa: BLE001 - a graphic that cannot be read is left out
            continue
    return picture


class BoardRender(base.Handler):
    """A PNG of the board drawn from its geometry (``plan.board_render``): it needs no screen, so it
    works with the desktop locked, and an Agent checks the picture alongside the data."""

    command = "pcb render"
    long_job = True  # reading a real board's geometry takes tens of seconds; drawing it, under one

    def preview(self, ctx: Context) -> dict[str, Any]:
        output = _png_path(ctx)
        L.in_layout(ctx, lambda app, doc: _render_inputs(ctx, int(doc.LayerCount)), budget_s=L.READ_BUDGET_S, what="reading the board")
        return base.preview(f"Draw the board as a picture into {output}.", [base.target(output, "file", label="board picture")],
                            [base.change(base.key(output), "exists", os.path.isfile(output), True)])

    def apply(self, ctx: Context) -> dict[str, Any]:
        output = _png_path(ctx)

        def work(app: Any, doc: Any) -> dict[str, Any]:
            side, layers, resolution = _render_inputs(ctx, int(doc.LayerCount))
            picture = board_picture(doc)
            png, width, height = PR.render(picture, side=side, layers=layers or None, scale=resolution)
            os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
            with open(output, "wb") as handle:
                handle.write(png)
            result = {"path": output, "side": side, "layers": layers or list(range(1, picture.layer_count + 1)), "width_px": width,
                      "height_px": height, **PR.drawn(picture, side=side, layers=layers or None)}
            return base.facts("not_applicable", "saved", result=result, files=[{"path": output}])

        return L.in_layout(ctx, work, budget_s=L.job_budget(L.WRITE_BUDGET_S), write=True, what="drawing the board")

    def run(self, ctx: Context, progress: Any) -> dict[str, Any]:
        return self.apply(ctx)

    def read_back(self, ctx: Context) -> dict[str, Any]:
        output = _png_path(ctx)
        targets = list(ctx.request.get("targets") or [])
        return {"observed": {t: {"exists": os.path.isfile(output)} for t in targets if t == base.key(output)}, "unreadable": []}


HANDLERS: list[base.Handler] = [BoardGet(), BoardCheck(), BoardExport(), BoardRender()]
