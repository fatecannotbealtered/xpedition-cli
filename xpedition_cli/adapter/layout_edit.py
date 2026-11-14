"""Board edits on the open board: traces drawn from a routing plan, vias placed and changed,
soldermask openings, and traces, vias, plane shapes and openings deleted.

Each write names its objects by net (and layer), previews how many it removes, removes them with
the objects' own ``Delete`` and reads the counts back (``read_keys``: ``/traces/<net>[/<layer>]``,
``/vias/<net>``, ``/planes/<net>/<layer>``). Xpedition's Undo is not reliable for changes made
through automation, so these deletes are how an Agent takes back a routing attempt before a save.
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .._driver import AdapterError
from . import base
from . import layout as L
from . import layout_output as O
from .base import Context
from .plan import PlanError, board_metrics
from .plan import fanout as FO
from .plan import guards as GD
from .plan import lanes as LN
from .plan import openings as OP
from .plan import requirements as RQ
from .plan import routing as plan_routing
from .plan import stitching as ST
from .plan import trace_plan as TP
from .platform import com


def _nets(ctx: Context) -> list[str]:
    nets = plan_routing.parse_names(base.need_input(ctx, "nets"))
    if not nets:
        raise AdapterError("E_VALIDATION", f"{ctx.command} needs --nets: one or more net names, such as SS_TX1_P,SS_TX1_N.",
                           dispatch_state="not_started")
    return nets


def _layer_list(ctx: Context, layer_count: int) -> list[int]:
    try:
        layers = plan_routing.parse_route_layers(ctx.inputs.get("layers"))
    except Exception:  # noqa: BLE001 - the planner says what is wrong
        raise AdapterError("E_VALIDATION", "--layers is a list of conductor layer numbers, such as 1,4.", dispatch_state="not_started") from None
    wrong = [n for n in layers if not 1 <= n <= layer_count]
    if wrong:
        raise AdapterError("E_VALIDATION", f"The board has conductor layers 1 to {layer_count}; there is no layer {wrong[0]}.",
                           dispatch_state="not_started")
    return layers


def _known(doc: Any, nets: list[str]) -> None:
    missing = [name for name in nets if L.find_net(doc, name) is None]
    if missing:
        raise AdapterError("E_NOT_FOUND", f"The board has no net {missing[0]}.", dispatch_state="not_started")


def trace_key(net: str, layer: int | None = None) -> str:
    return f"/traces/{L.segment(net)}" + (f"/{int(layer)}" if layer is not None else "")


def via_key(net: str) -> str:
    return f"/vias/{L.segment(net)}"


class TraceDelete(L.ReadsBack):
    """Delete the traces of the nets named, on the layers named or on every layer; with the region
    flags, only those that lie wholly inside the rectangle, such as a stub or an island. Vias stay:
    via delete removes them."""

    command = "trace delete"

    def _look(self, ctx: Context, doc: Any) -> tuple[list[str], list[int], dict[tuple[str, int | None], int],
                                                   dict[tuple[str, int | None], int]]:
        """The nets, the layers, each net's traces on each layer now, and how many of them go."""
        nets = _nets(ctx)
        _known(doc, nets)
        layers = _layer_list(ctx, int(doc.LayerCount))
        region = L.region_inputs(ctx.inputs)
        board = L.trace_counts(doc)
        counts = {(net, layer): board.get((net, layer), 0) for net in nets for layer in layers or [None]}
        if region is None:
            return nets, layers, counts, counts
        going = dict.fromkeys(counts, 0)
        for trace in L.collection(doc, "Traces"):
            net, layer = L.net_name(trace), int(trace.Layer)
            key = (net, layer if layers else None)
            if key in going and _trace_within(trace, region):
                going[key] += 1
        return nets, layers, counts, going

    def preview(self, ctx: Context) -> dict[str, Any]:
        path = ctx.document_path()
        region = L.region_inputs(ctx.inputs)
        nets, layers, counts, going = L.in_layout(ctx, lambda app, doc: self._look(ctx, doc), budget_s=L.READ_BUDGET_S,
                                                  what="reading the traces")
        live = {k: v for k, v in going.items() if v}
        on = f" on layers {', '.join(map(str, layers))}" if layers else ""
        within = f" wholly within {L.region_text(region)}" if region is not None else ""
        if not live:
            return base.preview(f"The nets {', '.join(nets)} have no traces{on}{within}; nothing to delete.", [], [])
        targets = [base.target(path, "trace", trace_key(net, layer), label=f"{net}" + (f" on layer {layer}" if layer else ""))
                   for net, layer in live]
        changes = [base.change(base.key(path, trace_key(net, layer)), "count", counts[(net, layer)], counts[(net, layer)] - count)
                   for (net, layer), count in live.items()]
        return base.preview(f"Delete {sum(live.values())} traces of {len(live)} nets{on}{within}; their vias stay (via delete removes "
                            "them). The board is not saved.", targets, changes)

    def apply(self, ctx: Context) -> dict[str, Any]:
        region = L.region_inputs(ctx.inputs)

        def work(app: Any, doc: Any) -> dict[str, Any]:
            nets, layers, _counts, _going = self._look(ctx, doc)
            wanted = set(nets)
            deleted = 0
            for trace in L.collection(doc, "Traces"):
                if L.net_name(trace) in wanted and (not layers or int(trace.Layer) in layers) and (
                        region is None or _trace_within(trace, region)):
                    try:
                        trace.Delete()
                        deleted += 1
                    except Exception as exc:  # noqa: BLE001 - classified
                        if not deleted:
                            raise L.com_error(exc, "deleting a trace", "not_started") from None
                        return base.facts("partial", "unchanged", result={"deleted": deleted},
                                          native_message=f"deleting a trace failed after {deleted}: {exc}")
            return base.facts("applied" if deleted else "not_applied", "unchanged", result={"deleted": deleted})

        return L.in_layout(ctx, work, budget_s=L.WRITE_BUDGET_S, write=True, what="deleting the traces")


def _trace_within(trace: Any, region: L.Region) -> bool:
    """Whether a trace lies wholly inside the region: both corners of its extents."""
    try:
        x0, y0, x1, y1 = L.extrema_mm(trace)
    except Exception:  # noqa: BLE001 - a trace without extents is left alone
        return False
    return L.in_region((x0, y0), region) and L.in_region((x1, y1), region)


def _via_point(via: Any) -> tuple[float, float]:
    return float(via.GetPositionX(L.UNIT_MM)), float(via.GetPositionY(L.UNIT_MM))


class ViaDelete(L.ReadsBack):
    """Delete the vias of the nets named, routing vias and stitching vias alike; with the region
    flags, only those inside the rectangle."""

    command = "via delete"

    def _look(self, ctx: Context, doc: Any) -> tuple[list[str], dict[str, int], dict[str, int], dict[str, str | None]]:
        """The nets, each one's vias now, how many of them go, and its padstack."""
        nets = _nets(ctx)
        _known(doc, nets)
        vias = L.via_summary(doc)
        region = L.region_inputs(ctx.inputs)
        going = {net: 0 for net in nets}
        for via in L.collection(doc, "Vias") if region is not None else ():
            net = L.net_name(via)
            if net in going and L.in_region(_via_point(via), region):
                going[net] += 1
        counts = {net: vias.get(net, {}).get("count", 0) for net in nets}
        return nets, counts, going if region is not None else counts, {net: vias.get(net, {}).get("padstack") for net in nets}

    def preview(self, ctx: Context) -> dict[str, Any]:
        path = ctx.document_path()
        region = L.region_inputs(ctx.inputs)
        nets, counts, going, stacks = L.in_layout(ctx, lambda app, doc: self._look(ctx, doc), budget_s=L.READ_BUDGET_S,
                                                  what="reading the vias")
        live = {k: v for k, v in going.items() if v}
        within = f" within {L.region_text(region)}" if region is not None else ""
        if not live:
            return base.preview(f"The nets {', '.join(nets)} have no vias{within}; nothing to delete.", [], [])
        targets = [base.target(path, "via", via_key(net), label=net) for net in live]
        changes = []
        for net, count in live.items():
            key = base.key(path, via_key(net))
            changes.append(base.change(key, "count", counts[net], counts[net] - count))
            if count == counts[net]:  # the padstack goes with the last via, so a later read-back, such as a save's, expects none
                changes.append(base.change(key, "padstack", stacks[net], None))
        return base.preview(f"Delete {sum(live.values())} vias of {len(live)} nets{within}. The board is not saved.", targets, changes)

    def apply(self, ctx: Context) -> dict[str, Any]:
        region = L.region_inputs(ctx.inputs)

        def work(app: Any, doc: Any) -> dict[str, Any]:
            nets, _counts, _going, _stacks = self._look(ctx, doc)
            wanted = set(nets)
            deleted = 0
            for via in L.collection(doc, "Vias"):
                if L.net_name(via) in wanted and (region is None or L.in_region(_via_point(via), region)):
                    try:
                        via.Delete()
                        deleted += 1
                    except Exception as exc:  # noqa: BLE001 - classified
                        if not deleted:
                            raise L.com_error(exc, "deleting a via", "not_started") from None
                        return base.facts("partial", "unchanged", result={"deleted": deleted},
                                          native_message=f"deleting a via failed after {deleted}: {exc}")
            return base.facts("applied" if deleted else "not_applied", "unchanged", result={"deleted": deleted})

        return L.in_layout(ctx, work, budget_s=L.WRITE_BUDGET_S, write=True, what="deleting the vias")


def _plane_inputs(ctx: Context) -> tuple[str, int]:
    net = str(base.need_input(ctx, "net")).strip()
    layer = base.need_input(ctx, "layer")
    if not net:
        raise AdapterError("E_VALIDATION", "plane delete needs a net name.", dispatch_state="not_started")
    if type(layer) is not int or layer < 1:
        raise AdapterError("E_VALIDATION", "--layer is a conductor layer number, 1 at the top.", dispatch_state="not_started")
    return net, layer


def _plane_shapes(doc: Any, net: str, layer: int) -> list[Any]:
    if L.find_net(doc, net) is None:
        raise AdapterError("E_NOT_FOUND", f"The board has no net {net}.", dispatch_state="not_started")
    shapes = [shape for shape in L.collection(doc, "PlaneShapes") if L.net_name(shape) == net and int(shape.Layer) == layer]
    if not shapes:
        raise AdapterError("E_NOT_FOUND", f"{net} has no plane shape on layer {layer}.", dispatch_state="not_started")
    return shapes


class PlaneDelete(L.ReadsBack):
    """Delete the plane shapes of one net on one layer; Layout drops their generated copper."""

    command = "plane delete"

    def preview(self, ctx: Context) -> dict[str, Any]:
        net, layer = _plane_inputs(ctx)
        path = ctx.document_path()
        count, now = L.in_layout(ctx, lambda app, doc: (len(_plane_shapes(doc, net, layer)), L.plane_values(doc, net, layer)),
                                 budget_s=L.READ_BUDGET_S, what="reading the planes")
        domain = L.plane_key(net, layer)
        key = base.key(path, domain)
        # the fill goes with the shape, so a later read-back, such as a save's, expects none
        changes = [base.change(key, "exists", True, False)]
        changes += [base.change(key, name, now[name], None) for name in ("hatch", "hatch_width_mm", "hatch_distance_mm")
                    if now.get(name) is not None]
        return base.preview(f"Delete the {count} plane shape(s) of {net} on layer {layer}, and with them its copper there. The board "
                            "is not saved.", [base.target(path, "plane", domain, label=f"{net} on layer {layer}")], changes)

    def apply(self, ctx: Context) -> dict[str, Any]:
        net, layer = _plane_inputs(ctx)

        def work(app: Any, doc: Any) -> dict[str, Any]:
            done = 0
            for shape in _plane_shapes(doc, net, layer):
                try:
                    shape.Delete()
                    done += 1
                except Exception as exc:  # noqa: BLE001 - classified
                    if not done:
                        raise L.com_error(exc, "deleting the plane shape", "not_started") from None
                    return base.facts("partial", "unchanged", result={"net": net, "layer": layer, "deleted": done},
                                      native_message=f"deleting a plane shape failed after {done}: {exc}")
            return base.facts("applied", "unchanged", result={"net": net, "layer": layer, "deleted": done})

        return L.in_layout(ctx, work, budget_s=L.WRITE_BUDGET_S, write=True, what="deleting the plane")


def _library_via(path: str, name: str) -> bool | None:
    """Whether the central library of the board's project holds a via padstack ``name``; None when
    it holds a padstack of that name of another type."""
    from .layout_rules import board_project
    from .library import default_cache, read_library, refuse_failed
    from .plan import padstacks as PS

    prj, _design = board_project(path)
    library = read_library(Path(prj), default_cache(), kinds=("padstacks",), symbols=False)
    refuse_failed(library, "its padstacks cannot be read")
    if name not in library.padstacks.get("padstacks", {}):
        return False
    return True if PS.row(library.padstacks, name)["type"] == "via" else None


def board_padstack(doc: Any, name: str) -> Any | None:
    """A padstack the board uses already (``Document.Padstacks`` lists only those)."""
    return next((item for item in L.collection(doc, "Padstacks") if L.name_of(item) == name), None)


def local_padstack(doc: Any, name: str) -> Any | None:
    """The padstack ``name`` from the design's own padstack library, when ``Document.PadstackNames``
    lists it there (``Document.PutPadstack`` with bFromCentralLib False). A copy of a project whose
    central library is on another machine's drive still holds the padstacks its design uses."""
    if name not in _local_names(doc):
        return None
    try:
        return doc.PutPadstack(1, int(doc.LayerCount), name, False, False)
    except Exception:  # noqa: BLE001 - the design's library cannot give it
        return None


def _local_names(doc: Any) -> tuple[str, ...]:
    try:
        return tuple(str(name) for name in (doc.PadstackNames or ()))
    except Exception:  # noqa: BLE001 - the design's library cannot be read
        return ()


def _padstack_pad_radius(stack: Any, scale: float) -> float | None:
    """The radius in mm of a padstack's first pad: a circle's radius, or half a shape's width."""
    from .layout_output import geometry_record

    try:
        for pad in L.collection(stack, "Pads"):
            for geometry in L.collection(pad, "Geometries"):
                record = geometry_record(geometry, scale)
                if record and record.get("circle"):
                    return float(record["circle"][2])
                if record and record.get("path"):
                    xs = [p[0] for p in record["path"]]
                    return (max(xs) - min(xs)) / 2
    except Exception:  # noqa: BLE001 - the pads cannot be read
        pass
    return None


def check_via_padstack(doc: Any, path: str, name: str) -> bool:
    """Whether the board, or the design's own padstack library, has the via padstack ``name``
    already; otherwise the central library must hold it as a via, or the call is refused before
    anything changes."""
    if board_padstack(doc, name) is not None or name in _local_names(doc):
        return True
    held = _library_via(path, name)
    if held is None:
        raise AdapterError("E_VALIDATION", f"{name} in the central library is not a via padstack.", dispatch_state="not_started")
    if not held:
        raise AdapterError("E_NOT_FOUND", f"Neither the board nor the central library holds a padstack {name}; padstack list --type "
                           "via lists the library's vias, and padstack create adds one.", dispatch_state="not_started")
    return False


def via_padstack(doc: Any, name: str) -> Any:
    """The padstack object ``name``, pulled from the central library onto the board when it lacks
    it (``Document.PutPadstack``, through every layer)."""
    found = board_padstack(doc, name)
    if found is not None:
        return found
    return local_padstack(doc, name) or doc.PutPadstack(1, int(doc.LayerCount), name, False, True)


def _via_update_inputs(ctx: Context) -> tuple[list[str], list[str], str]:
    nets = plan_routing.parse_names(ctx.inputs.get("nets"))
    classes = plan_routing.parse_names(ctx.inputs.get("net_classes"))
    if not nets and not classes:
        raise AdapterError("E_VALIDATION", "via update needs --nets or --net-classes.", dispatch_state="not_started")
    padstack = str(base.need_input(ctx, "padstack")).strip()
    if not padstack:
        raise AdapterError("E_VALIDATION", "via update needs --padstack.", dispatch_state="not_started")
    return nets, classes, padstack


def _via_nets(doc: Any, nets: list[str], classes: list[str]) -> list[str]:
    from .layout_board import _selected

    return _selected(doc, nets, classes) or []


class ViaUpdate(L.ReadsBack):
    """Change the padstack of every via of the nets named (``Via.CurrentPadstack``). The
    autorouter places the via each net class is assigned in Constraint Manager, which its
    automation cannot change; so a router run is followed by via update to the size the design
    asks for (knowledge layout-router-via-from-net-class)."""

    command = "via update"

    def _look(self, ctx: Context, doc: Any) -> tuple[list[str], str, dict[str, str], bool]:
        nets, classes, padstack = _via_update_inputs(ctx)
        chosen = _via_nets(doc, nets, classes)
        on_board = check_via_padstack(doc, ctx.document_path(), padstack)
        now = {net: L.via_padstack_name(doc, net) for net in chosen if L.via_count(doc, net)}
        return chosen, padstack, now, on_board

    def preview(self, ctx: Context) -> dict[str, Any]:
        path = ctx.document_path()
        chosen, padstack, now, on_board = L.in_layout(ctx, lambda app, doc: self._look(ctx, doc), budget_s=L.READ_BUDGET_S,
                                                      what="reading the vias")
        moving = {net: name for net, name in now.items() if name != padstack}
        if not moving:
            which = "have no vias" if not now else f"use {padstack} already"
            return base.preview(f"The vias of the {len(chosen)} nets {which}; nothing changes.", [], [])
        targets = [base.target(path, "via", via_key(net), label=net) for net in moving]
        changes = [base.change(base.key(path, via_key(net)), "padstack", name, padstack) for net, name in moving.items()]
        pulled = "" if on_board else ", pulled from the central library first"
        return base.preview(f"Change the vias of {len(moving)} nets to {padstack}{pulled}. A larger via can break a clearance: run pcb "
                            "check after. The board is not saved.", targets, changes)

    def apply(self, ctx: Context) -> dict[str, Any]:
        def work(app: Any, doc: Any) -> dict[str, Any]:
            chosen, padstack, _now, on_board = self._look(ctx, doc)
            wanted = set(chosen)
            changed = 0
            doc.TransactionStart(0)
            try:
                stack = via_padstack(doc, padstack)
                for via in L.collection(doc, "Vias"):
                    if L.net_name(via) in wanted and L.name_of(via) != padstack:
                        via.CurrentPadstack = stack
                        changed += 1
            except Exception as exc:  # noqa: BLE001 - classified
                if not changed:
                    raise L.com_error(exc, "changing the vias", "not_started") from None
                return base.facts("partial", "unchanged", result={"padstack": padstack, "changed": changed},
                                  native_message=f"changing a via failed after {changed}: {exc}")
            finally:
                doc.TransactionEnd(True)
            return base.facts("applied" if changed else "not_applied", "unchanged",
                              result={"padstack": padstack, "changed": changed, "pulled": not on_board})

        return L.in_layout(ctx, work, budget_s=L.WRITE_BUDGET_S, write=True, what="changing the vias")


def _via_row(path: str, name: str) -> dict[str, Any] | None:
    """The central library's row of the padstack ``name`` (pad and drill in mm), or None."""
    from .layout_rules import board_project
    from .library import default_cache, read_library, refuse_failed
    from .plan import padstacks as PS

    prj, _design = board_project(path)
    library = read_library(Path(prj), default_cache(), kinds=("padstacks",), symbols=False)
    refuse_failed(library, "its padstacks cannot be read")
    if name not in library.padstacks.get("padstacks", {}):
        return None
    return PS.row(library.padstacks, name)


_PAD_RADII: dict[tuple[str, str], tuple[float | None, str]] = {}


def via_pad_radius(doc: Any, path: str, name: str) -> tuple[float | None, str]:
    """The pad radius of a via padstack, read once per board and name in this process."""
    key = (path, name)
    if key not in _PAD_RADII or _PAD_RADII[key][0] is None:
        _PAD_RADII[key] = _via_pad_radius(doc, path, name)
    return _PAD_RADII[key]


def _via_pad_radius(doc: Any, path: str, name: str) -> tuple[float | None, str]:
    """The pad radius in mm of the via padstack ``name``, measured on a via of the board that uses
    it, else on the padstack of the design's own library, else from the central library's row;
    with None, the reason. A project often names a central library on another machine's drive,
    so a via the board has, or one its own library holds, needs no central library."""
    from .layout_output import _via_radius

    scale = L.geometry_scale(doc)
    if scale is not None:
        for via in L.collection(doc, "Vias"):
            if L.name_of(via) == name:
                return _via_radius(via, scale), ""
        local = local_padstack(doc, name)
        radius = _padstack_pad_radius(local, scale) if local is not None else None
        if radius:
            return radius, ""
    missing = f"no via of the board uses {name}, the design's own library has no padstack {name}"
    try:
        row = _via_row(path, name)
    except AdapterError as exc:
        reason = str(exc.data.get("message") or exc).rstrip(".")
        return None, f"{missing}, and {reason[:1].lower()}{reason[1:]}"
    if row is None or row.get("type") != "via" or not row.get("pad_mm"):
        return None, (f"{missing}, and the central library holds no via padstack {name}; padstack list --type via lists its "
                      "vias, and padstack create adds one")
    return float(row["pad_mm"]) / 2, ""


def _class_via(doc: Any, net: str) -> str | None:
    """The via the net's class is assigned for the through span, as the autorouter places it."""
    span = [(1, int(doc.LayerCount))]
    for net_class in L.collection(doc, "NetClasses"):
        if any(L.name_of(item) == net for item in L.collection(net_class, "Nets")):
            return L.class_vias(net_class, span).get(f"1-{int(doc.LayerCount)}")
    return None


def _clearances(path: str) -> ST.Clearances:
    """The via clearances of the board's default clearance rule on layer 1, from Constraint Manager."""
    from .layout_rules import board_project, ces_design, read_current
    from .plan.rules import DEFAULT_RULE

    prj, design_name = board_project(path)
    with ces_design(prj, design_name) as design:
        current = read_current(design, 1)
    rule = current.clearances.get(DEFAULT_RULE, {}).get("1", {})
    return ST.Clearances(via_to_pad=rule.get("pad_to_via_mm", 0.254), via_to_trace=rule.get("trace_to_via_mm", 0.254),
                         via_to_via=rule.get("via_to_via_mm", 0.254))


CHORD_MM = 0.002  # an arc's chords stay this close to it, so a clearance checked on them holds on the arc


def _centre_line(path: list[list[float]]) -> list[tuple[float, float]]:
    """A trace's centre line from its (x, y, r) rows, each arc as chords within ``CHORD_MM`` of it:
    an arc's row is its centre, which the trace does not pass."""
    rows = [(float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else 0.0) for p in path]
    points: list[tuple[float, float]] = []
    for index, (x, y, r) in enumerate(rows):
        if r and 0 < index < len(rows) - 1:
            (px, py, _), (nx, ny, _) = rows[index - 1], rows[index + 1]
            radius = math.hypot(px - x, py - y) or abs(r)
            start, end = math.atan2(py - y, px - x), math.atan2(ny - y, nx - x)
            sweep = (end - start) % (2 * math.pi)
            if r > 0:
                sweep -= 2 * math.pi  # a positive radius runs clockwise
            step = 2 * math.acos(max(-1.0, 1 - CHORD_MM / radius)) if radius > CHORD_MM else math.pi / 2
            count = max(2, math.ceil(abs(sweep) / step))
            points += [(x + radius * math.cos(start + sweep * k / count), y + radius * math.sin(start + sweep * k / count))
                       for k in range(1, count)]
            continue
        points.append((x, y))
    return points


def _stitch_board(doc: Any, net: str) -> ST.Board:
    from .layout_output import board_model, geometry_record

    model = board_model(doc, full=True, silk=False)
    scale = L.geometry_scale(doc)
    if model is None or scale is None or not model["outline"]:
        raise AdapterError("E_UNSUPPORTED", "The board's geometry can be read only on a board in mils with an outline; switch the "
                           "board's unit to mils in Layout, or set the outline with pcb update.", dispatch_state="not_started")
    board = ST.Board(outline=ST.polygon(model["outline"][0].get("path"), model["outline"][0].get("circle")))
    board.pads = [ST.polygon(pad.get("path"), pad.get("circle")) for pad in model["pads"]]
    board.traces = [(_centre_line(trace["path"]), float(trace.get("width") or 0.0), str(trace.get("net") or ""))
                    for trace in model["traces"] if trace.get("path")]
    board.vias = [(via["circle"][0], via["circle"][1]) for via in model["vias"]]
    board.holes = [(x, y, d) for x, y, d in model["holes"]]
    for shape in L.collection(doc, "PlaneShapes"):
        if L.net_name(shape) != net:
            continue
        record = geometry_record(shape.Geometry, scale)
        if record:
            board.planes.setdefault(int(shape.Layer), []).append(ST.polygon(record.get("path"), record.get("circle")))
    for contour in L.collection(doc, "Contours"):
        if int(contour.ContourType) == CONTOUR_INTERNAL:
            record = geometry_record(contour.Geometry, scale)
            if record:
                board.cutouts.append(ST.polygon(record.get("path"), record.get("circle")))
    board.bends = _zone_shapes(O.flex_model(doc))
    board.bodies = part_bodies(doc, scale)
    board.keepouts = via_keepouts(model)
    return board


def part_bodies(doc: Any, scale: float) -> list[list[tuple[float, float]]]:
    """The parts' placement outlines in mm, which Layout's DRC reports a via under; not the board's own."""
    from .layout_output import geometry_record

    bodies = []
    for outline in L.collection(doc, "PlacementOutlines"):
        try:
            if com.member(outline, "Component") is None:
                continue  # a board's own placement outline, not a part's
            record = geometry_record(outline.Geometry, scale)
        except Exception:  # noqa: BLE001 - an outline that cannot be read is left out
            continue
        if record:
            bodies.append(ST.polygon(record.get("path"), record.get("circle")))
    return bodies


def via_keepouts(model: dict[str, Any]) -> list[list[tuple[float, float]]]:
    """The shapes of the board's keepouts that keep vias out, on any layer, from a full board model."""
    return [ST.polygon(k["shape"].get("path"), k["shape"].get("circle")) for k in model.get("keepouts") or []
            if k.get("kind") in TP.VIA_KEEPOUTS and k.get("shape")]



def _via_create_inputs(ctx: Context) -> dict[str, Any]:
    net = str(base.need_input(ctx, "net")).strip()
    x, y, pitch = ctx.inputs.get("x_mm"), ctx.inputs.get("y_mm"), ctx.inputs.get("pitch_mm")
    single = x is not None or y is not None
    if single == (pitch is not None) or (single and (x is None or y is None)):
        raise AdapterError("E_VALIDATION", "via create takes either --x and --y (one via) or --pitch (a stitching grid).",
                           dispatch_state="not_started")
    margin = ctx.inputs.get("margin_mm")
    margin = 0.5 if margin is None else float(margin)
    if margin < 0:
        raise AdapterError("E_VALIDATION", "--margin is a distance of 0 or more.", dispatch_state="not_started")
    region = L.region_inputs(ctx.inputs)
    if region is not None and single:
        raise AdapterError("E_VALIDATION", "--from-x, --from-y, --to-x and --to-y limit a stitching grid; one via takes --x and --y "
                           "alone.", dispatch_state="not_started")
    return {"net": net, "x": x, "y": y, "pitch": pitch, "margin": margin, "region": region,
            "padstack": str(ctx.inputs.get("padstack") or "").strip()}


def _via_plan(ctx: Context, doc: Any) -> dict[str, Any]:
    inputs = _via_create_inputs(ctx)
    path = ctx.document_path()
    net = inputs["net"]
    if L.find_net(doc, net) is None:
        raise AdapterError("E_NOT_FOUND", f"The board has no net {net}.", dispatch_state="not_started")
    padstack = inputs["padstack"] or _class_via(doc, net)
    if not padstack:
        raise AdapterError("E_VALIDATION", f"{net} has no via assigned for the through span; name one with --padstack.",
                           dispatch_state="not_started")
    radius, why = via_pad_radius(doc, path, padstack)
    if radius is None:
        raise AdapterError("E_NOT_FOUND", f"The size of the via {padstack} is not known: {why}.", dispatch_state="not_started")
    board = _stitch_board(doc, net)
    clear = _clearances(path)
    blocked: list[str] = []
    if inputs["pitch"] is None:
        point = (round(float(inputs["x"]), 4), round(float(inputs["y"]), 4))
        why = None if ST.inside(point, board.outline) else "outside the board outline"
        # a via asked for by place may stand under a part and near a bend zone, where pcb check lists it
        why = why or ST.free(point, net, radius, replace(board, bodies=[]), clear, bend_margin=0.0)
        why = "in a bend zone, which takes no vias" if why == "in a bend zone" else why
        points = [] if why else [point]
        if why:
            blocked.append(f"a via at ({point[0]:g}, {point[1]:g}) mm is {why}")
        dropped: dict[str, int] = {}
    else:
        try:
            result = ST.stitch(net, radius, board, clear, float(inputs["pitch"]), inputs["margin"], inputs["region"])
        except PlanError as exc:
            if exc.code == "E_CONFLICT":
                raise base.blocked(exc.message) from None
            raise AdapterError(exc.code, exc.message, dispatch_state="not_started") from None
        points, dropped = result.points, result.dropped
        if not points:
            reasons = ", ".join(f"{count} {why}" for why, count in sorted(dropped.items())) or "no grid point inside the outline"
            blocked.append(f"no point of the {inputs['pitch']:g} mm grid is free ({reasons})")
    return {**inputs, "padstack": padstack, "points": points, "dropped": dropped, "blocked": blocked,
            "before": L.via_count(doc, net), "stack_before": L.via_padstack_name(doc, net), "radius": radius, "clearances": clear}


def stack_after(before: str | None, added: str) -> str:
    """The padstack a net's vias report once vias of ``added`` join those of ``before``."""
    names = {added} | (set(before.removeprefix("mixed: ").split(", ")) if before else set())
    return added if len(names) == 1 else "mixed: " + ", ".join(sorted(names))


class ViaCreate(L.ReadsBack):
    """Vias of one net: one at --x, --y, or a stitching grid at --pitch inside the net's copper on
    two layers (``plan.stitching``). The positions are worked out against the rules' clearances, so
    the vias go in with Layout's online DRC off (``TransactionStart(0)``) and pcb check judges them.
    The via is the net class's own unless --padstack names another."""

    command = "via create"
    long_job = True

    def preview(self, ctx: Context) -> dict[str, Any]:
        path = ctx.document_path()
        plan = L.in_layout(ctx, lambda app, doc: _via_plan(ctx, doc), budget_s=L.READ_BUDGET_S, what="planning the vias")
        net, count = plan["net"], len(plan["points"])
        target = [base.target(path, "via", via_key(net), label=net)]
        if plan["blocked"]:
            return base.preview(f"Place {plan['padstack']} vias of {net}.", target, [], blocked_by=plan["blocked"],
                                knowledge=["layout-via-near-pad"])
        dropped = "; ".join(f"{n} grid points {why}" for why, n in sorted(plan["dropped"].items()))
        within = f" within {L.region_text(plan['region'])}" if plan.get("region") else ""
        where = (f"at ({plan['points'][0][0]:g}, {plan['points'][0][1]:g}) mm" if plan["pitch"] is None
                 else f"on a {plan['pitch']:g} mm grid inside its copper on two layers{within}" + (f" ({dropped})" if dropped else ""))
        key = base.key(path, via_key(net))
        changes = [base.change(key, "count", plan["before"], plan["before"] + count)]
        after = stack_after(plan["stack_before"], plan["padstack"])
        if count and after != plan["stack_before"]:  # a later read-back, such as a save's, expects the new padstack
            changes.append(base.change(key, "padstack", plan["stack_before"], after))
        return base.preview(f"Place {count} {plan['padstack']} via(s) of {net} {where}, clear of other copper by the board's default "
                            "clearance rule. Run pcb check after. The board is not saved.", target, changes)

    def run(self, ctx: Context, progress: Any) -> dict[str, Any]:
        return self.apply(ctx)

    def apply(self, ctx: Context) -> dict[str, Any]:
        def work(app: Any, doc: Any) -> dict[str, Any]:
            plan = _via_plan(ctx, doc)
            if plan["blocked"]:
                raise base.blocked("; ".join(plan["blocked"]), knowledge=["layout-via-near-pad"])
            net_obj = L.find_net(doc, plan["net"])
            placed = 0
            doc.TransactionStart(0)
            try:
                stack = via_padstack(doc, plan["padstack"])
                for x, y in plan["points"]:
                    doc.PutVia(x, y, stack, net_obj, None, 0, L.UNIT_MM)
                    placed += 1
            except Exception as exc:  # noqa: BLE001 - classified
                if not placed:
                    raise L.com_error(exc, "placing a via", "not_started") from None
                return base.facts("partial", "unchanged", result={"net": plan["net"], "padstack": plan["padstack"], "placed": placed},
                                  native_message=f"placing a via failed after {placed}: {exc}")
            finally:
                doc.TransactionEnd(True)
            return base.facts("applied", "unchanged", result={
                "net": plan["net"], "padstack": plan["padstack"], "placed": placed,
                "points_mm": [[x, y] for x, y in plan["points"][:VIA_POINTS_LISTED]], "dropped": plan["dropped"]})

        return L.in_layout(ctx, work, budget_s=L.job_budget(L.WRITE_BUDGET_S), write=True, what="placing the vias")


VIA_POINTS_LISTED = 200


CONTOUR_INTERNAL = 1  # EPcbContourType: 0 the board's, 1 a cutout inside it
FAB_SOLDERMASK = 16  # EPcbFabricationType epcbFabSoldermask
FAB_COVER_LAYER = 1024  # epcbFabCoverLayer: the coverlay of a flex board
SIDE_TOP, SIDE_BOTTOM = 1, 512  # EPcbSide


def _side(ctx: Context) -> tuple[str, int]:
    side = str(ctx.inputs.get("side") or "top").strip().lower()
    if side not in ("top", "bottom"):
        raise AdapterError("E_VALIDATION", "--side is top or bottom.", dispatch_state="not_started")
    return side, SIDE_TOP if side == "top" else SIDE_BOTTOM


def _mask_kind(ctx: Context) -> tuple[str, int]:
    return ("cover-layer", FAB_COVER_LAYER) if ctx.inputs.get("cover_layer") else ("solder-mask", FAB_SOLDERMASK)


MASK_CLEARANCE_MM = 0.1  # how far an opening meant for one net keeps from other nets' copper, by default
MASK_CLEARANCE_MAX_MM = 2.0
Box = tuple[float, float, float, float]


def _rect(ctx: Context) -> Box | None:
    values = [ctx.inputs.get(name) for name in ("x1_mm", "y1_mm", "x2_mm", "y2_mm")]
    if all(v is None for v in values):
        return None
    if any(v is None for v in values):
        raise AdapterError("E_VALIDATION", "A rectangle needs all of --x1, --y1, --x2 and --y2: two opposite corners.",
                           dispatch_state="not_started")
    x1, y1, x2, y2 = (round(float(v), 4) for v in values)
    box = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    if box[2] - box[0] <= 0 or box[3] - box[1] <= 0:
        raise AdapterError("E_VALIDATION", "The opening needs an area: the corners differ in x and in y.", dispatch_state="not_started")
    return box


@dataclass
class MaskRequest:
    side: str
    side_code: int
    kind: str
    fab: int
    shape: list[tuple[float, float]]  # the opening, or with ``fit`` the region it is fitted in
    polygon: bool
    net: str | None
    fit: bool
    clearance: float


def _mask_request(ctx: Context) -> MaskRequest:
    from .layout_board import polygon_ring

    side, code = _side(ctx)
    kind, fab = _mask_kind(ctx)
    box, value = _rect(ctx), ctx.inputs.get("polygon")
    if (box is None) == (value is None):
        raise AdapterError("E_VALIDATION", "Give the opening as a rectangle, --x1, --y1, --x2 and --y2, or as --polygon, not both.",
                           dispatch_state="not_started")
    shape = OP.rect_ring(box) if box is not None else polygon_ring(value)
    net = str(ctx.inputs.get("net") or "").strip() or None
    fit = bool(ctx.inputs.get("fit"))
    if fit and net is None:
        raise AdapterError("E_VALIDATION", "--fit finds where an opening bares one net's copper alone: name the net with --net.",
                           dispatch_state="not_started")
    clearance = ctx.inputs.get("clearance_mm")
    clearance = MASK_CLEARANCE_MM if clearance is None else float(clearance)
    if not 0 <= clearance <= MASK_CLEARANCE_MAX_MM:
        raise AdapterError("E_VALIDATION", f"--clearance is 0 to {MASK_CLEARANCE_MAX_MM:g} mm.", dispatch_state="not_started")
    return MaskRequest(side, code, kind, fab, shape, value is not None, net, fit, clearance)


def _box_of(points: list[tuple[float, float]]) -> Box:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return round(min(xs), 4), round(min(ys), 4), round(max(xs), 4), round(max(ys), 4)


def layer_copper(doc: Any, layer: int) -> OP.LayerCopper:
    """The copper of one layer as the opening checks take it: Layout's plane copper and the pads as
    regions, the traces and the vias, each with its net."""
    from .layout_output import board_model, geometry_record

    model = board_model(doc, full=True, silk=False, shapes=False)
    scale = L.geometry_scale(doc)
    if model is None or scale is None:
        raise AdapterError("E_UNSUPPORTED", "The board's copper can be read only on a board in mils with an outline; switch the board's "
                           "unit to mils in Layout.", dispatch_state="not_started")
    copper = OP.LayerCopper()
    for pad in model["pads"]:
        if int(pad.get("layer") or 0) == layer:
            copper.regions.append((_net_or_none(pad.get("net")), [_ring(pad)]))
    for trace in model["traces"]:
        if int(trace.get("layer") or 0) == layer and trace.get("path"):
            copper.traces.append((_net_or_none(trace.get("net")), float(trace.get("width") or 0.0), _centre_line(trace["path"])))
    for via in model["vias"]:
        copper.vias.append((_net_or_none(via.get("net")), (float(via["circle"][0]), float(via["circle"][1])), float(via["circle"][2])))
    for piece in L.collection(doc, "GeneratedPlanes"):  # the copper itself, its clearances and hatching as holes
        if int(piece.Layer) != layer:
            continue
        geometry = piece.Geometry
        record = geometry_record(geometry, scale)
        if not record:
            continue
        rings = [_ring(record)]
        for hole in L.collection(geometry, "Cutouts"):
            cut = geometry_record(hole, scale)
            if cut:
                rings.append(_ring(cut))
        copper.regions.append((_net_or_none(L.net_name(piece)), rings))
    return copper


def _net_or_none(name: Any) -> str:
    return str(name or "") or "(no net)"


def _ring(record: dict[str, Any]) -> list[tuple[float, float]]:
    """A geometry record as a polygon in mm: a circle as 24 sides around it, a path with its arcs as chords."""
    if record.get("circle"):
        x, y, r = (float(v) for v in record["circle"][:3])
        reach = r / math.cos(math.pi / 24)
        return [(x + reach * math.cos(k * math.pi / 12), y + reach * math.sin(k * math.pi / 12)) for k in range(24)]
    return ST.polygon(record.get("path"), None)


@dataclass
class MaskPlan:
    shape: list[tuple[float, float]] | None  # the opening to draw, None where no fit was found
    box: Box | None
    exposure: dict[str, Any]
    blocked: list[str]


def _overlaps(a: Box, b: Box) -> bool:
    return min(a[2], b[2]) - max(a[0], b[0]) > 0 and min(a[3], b[3]) - max(a[1], b[1]) > 0


def _mask_plan(doc: Any, request: MaskRequest) -> MaskPlan:
    """Where the opening goes and what it bares, with what stands in its way."""
    layer = 1 if request.side == "top" else int(doc.LayerCount)
    copper = layer_copper(doc, layer)
    shape = request.shape
    blocked: list[str] = []
    bends = O.bend_boxes(doc)
    margin = f"{request.clearance:g} mm"
    if request.fit:
        rect = OP.fit(request.shape, copper, request.net or "", request.clearance, avoid=bends)
        if rect is None:
            region = "the polygon" if request.polygon else "the rectangle"
            return MaskPlan(None, None, {"bared": [], "near": []}, [
                f"no rectangle of at least {OP.MIN_SIDE_MM:g} mm a side inside {region} lies on {request.net}'s copper on layer "
                f"{layer}, keeps {margin} from other copper and stays out of the bend zones; give a larger region, or pour "
                f"{request.net} there first"])
        shape = OP.rect_ring(rect)
    exposure = OP.exposure(shape, copper, request.clearance)
    box = _box_of(shape)
    if request.net is None:
        if not exposure["bared"]:
            blocked.append(f"no copper lies inside the opening on layer {layer}, so it would bare nothing; pour or route the copper first")
    else:
        for row in exposure["near"]:
            if row["net"] == request.net:
                continue
            at = f"({row['at_mm'][0]:g}, {row['at_mm'][1]:g})"
            if row["gap_mm"] == 0:
                blocked.append(f"the opening bares {row['net']}'s copper at {at} on layer {layer}, not only {request.net}'s; move it off, "
                               "or give a region with --fit")
            else:
                blocked.append(f"{row['net']}'s copper comes {row['gap_mm']:g} mm from the opening at {at} on layer {layer}, within the "
                               f"{margin} clearance; move the opening off, or give a region with --fit")
        if request.net not in exposure["bared"]:
            blocked.append(f"the opening bares none of {request.net}'s copper on layer {layer}; pcb render --layers {layer} shows "
                           "where it lies")
    for zone in bends:
        if _overlaps(box, zone):
            blocked.append(f"the opening reaches into the bend zone {zone[0]:g},{zone[1]:g} to {zone[2]:g},{zone[3]:g} mm, where bare "
                           "copper cracks as the board bends; keep it out of the zones pcb get lists")
    return MaskPlan(shape, box, exposure, blocked)


def _mask_where(plan: MaskPlan, request: MaskRequest) -> str:
    box = plan.box
    if box is None:
        return ""
    if request.polygon and not request.fit:
        return f"the polygon of {len(plan.shape or [])} points within {box[0]:g},{box[1]:g} to {box[2]:g},{box[3]:g} mm"
    return f"{box[0]:g},{box[1]:g} to {box[2]:g},{box[3]:g} mm"


def mask_key(kind: str, side: str, box: tuple[float, float, float, float]) -> str:
    return f"/{kind}/{side}/" + ",".join(f"{v:g}" for v in box)


def _openings(doc: Any, fab_type: int, side_code: int) -> list[tuple[Any, tuple[float, float, float, float]]]:
    """The board's own openings of one kind on one side, with their extents. Layout is asked for
    those alone: a board's parts carry hundreds of fabrication graphics."""
    found = L.filtered(doc, "FabricationLayerGfxs", fab_type, 0, side_code, False)
    out = []
    for gfx in found if found is not None else L.collection(doc, "FabricationLayerGfxs"):
        try:
            if int(gfx.Type) != fab_type or int(gfx.Side) != side_code or gfx.Component is not None:
                continue
            out.append((gfx, tuple(round(v, 4) for v in L.extrema_mm(gfx))))
        except Exception:  # noqa: BLE001 - a graphic that cannot be read is left alone
            continue
    return out


def opening_exists(doc: Any, kind: str, side: str, box: tuple[float, ...], shape: list[tuple[float, float]] | None = None) -> bool:
    """Whether the side holds an opening with these extents, and, with ``shape``, of that shape's area."""
    fab_type = FAB_COVER_LAYER if kind == "cover-layer" else FAB_SOLDERMASK
    side_code = SIDE_TOP if side == "top" else SIDE_BOTTOM
    for gfx, found in _openings(doc, fab_type, side_code):
        if not all(abs(a - b) < 0.002 for a, b in zip(found, box, strict=True)):
            continue
        if shape is None or abs(_area(opening_shape(doc, gfx, found)) - _area(shape)) <= 0.01 * max(_area(shape), 1e-6):
            return True
    return False


def _area(ring: list[tuple[float, float]]) -> float:
    return abs(sum(ring[i - 1][0] * ring[i][1] - ring[i][0] * ring[i - 1][1] for i in range(len(ring)))) / 2


def opening_shape(doc: Any, gfx: Any, box: Box) -> list[tuple[float, float]]:
    """An opening's shape as a polygon in mm; its box where the shape cannot be read."""
    from .layout_output import geometry_record
    from .plan.board_render import polygon_of

    scale = L.geometry_scale(doc)
    try:
        record = geometry_record(gfx.Geometry, scale) if scale is not None else None
    except Exception:  # noqa: BLE001 - its box stands in
        record = None
    if record:
        return polygon_of(record.get("path"), record.get("circle"))
    return OP.rect_ring(box)


def opening_facts(doc: Any, clearance: float = MASK_CLEARANCE_MM) -> list[dict[str, Any]]:
    """The board's own openings, each with its side, kind and extents, the nets whose copper it
    bares (``bared``) and each net's copper within ``clearance`` of it (``near``)."""
    out = []
    for side, code in (("top", SIDE_TOP), ("bottom", SIDE_BOTTOM)):
        found = [(kind, gfx, box) for kind, fab in (("soldermask", FAB_SOLDERMASK), ("coverlay", FAB_COVER_LAYER))
                 for gfx, box in _openings(doc, fab, code)]
        if not found:
            continue
        copper = layer_copper(doc, 1 if side == "top" else int(doc.LayerCount))
        for kind, gfx, box in found:
            exposure = OP.exposure(opening_shape(doc, gfx, box), copper, clearance)
            out.append({"side": side, "kind": kind, "extents_mm": list(box), **exposure})
    return out


def opening_rows(doc: Any) -> list[dict[str, Any]]:
    """pcb check's rule for the board's own openings: one that bares the copper of two nets, or
    comes within ``MASK_CLEARANCE_MM`` of a second net's copper, so that a spring, foam or
    conductive glue on it would join them."""
    rows = []
    for opening in opening_facts(doc):
        if len(opening["near"]) < 2:
            continue
        box = opening["extents_mm"]
        where = f"the {opening['side']} {opening['kind']} opening at {box[0]:g},{box[1]:g} to {box[2]:g},{box[3]:g} mm"
        close = [row for row in opening["near"] if row["gap_mm"] > 0]
        finding = f"{where} bares {' and '.join(opening['bared']) or 'no copper'}"
        if close:
            finding += "; " + ", ".join(f"{row['net']}'s copper comes {row['gap_mm']:g} mm from it at ({row['at_mm'][0]:g}, "
                                        f"{row['at_mm'][1]:g})" for row in close)
        rows.append({"rule": "opening_short", "severity": "medium", "net": opening["near"][0]["net"], "other": opening["near"][1]["net"],
                     "finding": finding + f": a spring, foam or glue on it would join them; keep {MASK_CLEARANCE_MM:g} mm from other "
                                          "nets"})
    return rows


def listed_openings(doc: Any) -> list[dict[str, Any]]:
    """The board's own soldermask and coverlay openings as pcb get lists them."""
    rows = []
    for side, code in (("top", SIDE_TOP), ("bottom", SIDE_BOTTOM)):
        for kind, fab in (("solder-mask", FAB_SOLDERMASK), ("cover-layer", FAB_COVER_LAYER)):
            rows += [{"side": side, "kind": kind, "extents_mm": list(box)} for _gfx, box in _openings(doc, fab, code)]
    return rows


def cover_layer_sides(doc: Any) -> set[str]:
    """The sides whose outer insulation includes a cover layer (a flex board's coverlay)."""
    from .layout_output import board_stackup

    rows = board_stackup(doc)
    conductors = [r["board_layer"] for r in rows if r["kind"] == "conductor"]
    if not conductors:
        return set()
    sides = set()
    for row in rows:
        if row.get("insulation") == "cover_layer":
            if row["board_layer"] < min(conductors):
                sides.add("top")
            elif row["board_layer"] > max(conductors):
                sides.add("bottom")
    return sides


class SolderMaskCreate(L.ReadsBack):
    """An opening in the soldermask, or in the coverlay of a flex board (--cover-layer), on one side:
    a rectangle or a polygon, ``PutFabricationLayerGfx``, filled. Copper under it is left bare. With
    --net it may bare that net's copper alone, at the margin from all other copper; --fit finds the
    largest rectangle that does inside the region given."""

    command = "solder-mask create"

    def preview(self, ctx: Context) -> dict[str, Any]:
        request = _mask_request(ctx)
        path = ctx.document_path()

        def look(app: Any, doc: Any) -> tuple[MaskPlan, bool, bool]:
            plan = _mask_plan(doc, request)
            cover = request.kind != "cover-layer" or request.side in cover_layer_sides(doc)
            exists = plan.box is not None and opening_exists(doc, request.kind, request.side, plan.box, plan.shape)
            return plan, exists, cover

        plan, exists, cover = L.in_layout(ctx, look, budget_s=L.READ_BUDGET_S, what="reading the copper under the opening")
        label = f"{request.kind} opening on the {request.side}"
        if plan.box is None:
            return base.preview(f"Find room for a {request.side} opening on {request.net}'s copper.",
                                [base.target(path, "opening", None, label=label)], [], blocked_by=plan.blocked)
        domain = mask_key(request.kind, request.side, plan.box)
        target = [base.target(path, "opening", domain, label=label)]
        where = _mask_where(plan, request)
        if exists:
            return base.preview(f"The {request.side} {request.kind} has an opening at {where} already; nothing changes.", target, [])
        blocked = list(plan.blocked)
        if not cover:
            blocked.append(f"the board's stackup has no cover layer on the {request.side} side (pcb get lists the stackup); open the "
                           "soldermask instead, without --cover-layer")
        layer = "coverlay" if request.kind == "cover-layer" else "soldermask"
        others = [row for row in plan.exposure["near"] if row["net"] != request.net]
        apart = "" if request.net is None or others else f", {request.clearance:g} mm or more from all other copper"
        fitted = " (the largest rectangle that fits)" if request.fit else ""
        return base.preview(f"Open the {request.side} {layer} over {where}{fitted}, baring the copper of "
                            f"{', '.join(plan.exposure['bared']) or 'no net'} there{apart}. The board is not saved.", target,
                            [base.change(base.key(path, domain), "exists", False, True)], blocked_by=blocked or None)

    def apply(self, ctx: Context) -> dict[str, Any]:
        request = _mask_request(ctx)

        def work(app: Any, doc: Any) -> dict[str, Any]:
            plan = _mask_plan(doc, request)
            if plan.shape is None or plan.box is None:
                raise base.blocked("The board changed since the preview: " + "; ".join(plan.blocked) + ".")
            result = {"kind": request.kind, "side": request.side, "rect_mm": list(plan.box), "nets": plan.exposure["bared"]}
            if request.polygon and not request.fit:
                result["points_mm"] = [[round(x, 4), round(y, 4)] for x, y in plan.shape]
            if opening_exists(doc, request.kind, request.side, plan.box, plan.shape):
                return base.facts("not_applied", "unchanged", result={**result, "created": False})
            if plan.blocked:
                raise base.blocked("The board changed since the preview: " + "; ".join(plan.blocked) + ".")
            ring = [*plan.shape, plan.shape[0]]
            points = [[p[0] for p in ring], [p[1] for p in ring], [0.0] * len(ring)]
            doc.TransactionStart(0)
            try:
                doc.PutFabricationLayerGfx(request.fab, request.side_code, 0.0, len(ring), points, True, None, L.UNIT_MM)
            except Exception as exc:  # noqa: BLE001 - classified
                raise L.com_error(exc, "drawing the opening", com.call_state(exc)) from None
            finally:
                doc.TransactionEnd(True)
            return base.facts("applied", "unchanged", result={**result, "created": True})

        return L.in_layout(ctx, work, budget_s=L.WRITE_BUDGET_S, write=True, what="drawing the opening")


class SolderMaskDelete(L.ReadsBack):
    """Delete the soldermask (or coverlay) opening that contains a point on one side."""

    command = "solder-mask delete"

    def _found(self, ctx: Context, doc: Any) -> tuple[str, str, Any, tuple[float, float, float, float]]:
        side, code = _side(ctx)
        kind, fab = _mask_kind(ctx)
        x, y = base.need_input(ctx, "x_mm"), base.need_input(ctx, "y_mm")
        hits = [(g, box) for g, box in _openings(doc, fab, code) if box[0] <= x <= box[2] and box[1] <= y <= box[3]
                and OP.inside((x, y), opening_shape(doc, g, box))]
        if not hits:
            raise AdapterError("E_NOT_FOUND", f"No {side} {kind} opening contains ({x:g}, {y:g}) mm.", dispatch_state="not_started")
        if len(hits) > 1:
            raise AdapterError("E_AMBIGUOUS", f"{len(hits)} {side} {kind} openings contain ({x:g}, {y:g}) mm; give a point inside one only.",
                               dispatch_state="not_started", candidates=[mask_key(kind, side, box) for _g, box in hits])
        return kind, side, hits[0][0], hits[0][1]

    def preview(self, ctx: Context) -> dict[str, Any]:
        path = ctx.document_path()
        kind, side, _gfx, box = L.in_layout(ctx, lambda app, doc: self._found(ctx, doc), budget_s=L.READ_BUDGET_S,
                                            what="finding the opening")
        domain = mask_key(kind, side, box)
        return base.preview(f"Delete the {side} {kind} opening at {box[0]:g},{box[1]:g} to {box[2]:g},{box[3]:g} mm. The board is not "
                            "saved.", [base.target(path, "opening", domain, label=f"{kind} opening on the {side}")],
                            [base.change(base.key(path, domain), "exists", True, False)])

    def apply(self, ctx: Context) -> dict[str, Any]:
        def work(app: Any, doc: Any) -> dict[str, Any]:
            kind, side, gfx, box = self._found(ctx, doc)
            try:
                gfx.Delete()
            except Exception as exc:  # noqa: BLE001 - classified
                raise L.com_error(exc, "deleting the opening", com.call_state(exc)) from None
            return base.facts("applied", "unchanged", result={"kind": kind, "side": side, "rect_mm": list(box)})

        return L.in_layout(ctx, work, budget_s=L.WRITE_BUDGET_S, write=True, what="deleting the opening")


def _clearance_lookup(current: Any) -> Any:
    """``clearance(net_a, net_b, layer, kind)`` in mm from the board's rules: the rule of the two
    nets' class pair, else of one class against (All), else (All) against (All); between the two
    traces of a differential pair, the spacing their class sets for the pair on that layer."""
    from .plan import rules as R

    pairs = {frozenset(pair) for pair in current.differential_pairs if len(pair) == 2}

    def rule_for(a: str, b: str) -> str:
        pairs = current.class_clearances
        for key in ((a, b), (b, a), (a, R.ALL_CLASSES), (R.ALL_CLASSES, a), (b, R.ALL_CLASSES), (R.ALL_CLASSES, b)):
            if key in pairs:
                return pairs[key]
        return pairs.get((R.ALL_CLASSES, R.ALL_CLASSES), R.DEFAULT_RULE)

    def clearance(net_a: str, net_b: str, layer: int, kind: str) -> float:
        a = current.net_class_of.get(net_a, R.DEFAULT_CLASS)
        b = current.net_class_of.get(net_b, R.DEFAULT_CLASS)
        if kind == "trace_to_trace" and a == b and frozenset((net_a, net_b)) in pairs:
            spacing = current.net_classes.get(a, {}).get(str(layer), {}).get("differential_spacing_mm")
            if spacing:
                return float(spacing)
        layers = current.clearances.get(rule_for(a, b)) or current.clearances.get(R.DEFAULT_RULE, {})
        return float(layers.get(str(layer), {}).get(f"{kind}_mm", 0.254))

    return clearance


def _trace_rules(doc: Any, path: str) -> TP.Rules:
    from .layout_output import net_classes
    from .layout_rules import board_project, ces_design, read_current

    prj, design_name = board_project(path)
    layer_count = int(doc.LayerCount)
    with ces_design(prj, design_name) as design:
        current = read_current(design, layer_count)
    widths: dict[str, dict[int, tuple[float, float]]] = {}
    for row in net_classes(doc, layer_count):
        widths[row["name"]] = {int(layer): (float(values.get("minimum_mm", 0.0)), float(values.get("expansion_mm", 25.0)))
                               for layer, values in row["widths"].items()}
    return TP.Rules(net_class_of=current.net_class_of, widths=widths, clearance=_clearance_lookup(current),
                    pairs=[list(pair) for pair in current.differential_pairs if len(pair) == 2])


def _copper(doc: Any) -> TP.Copper:
    """The board's copper, read once for a call; it also keeps the rules and the flex model the
    call reads (``_rules_of``, ``_flex_of``), each read once."""
    from .layout_output import board_model

    from .layout_output import geometry_record, keepouts

    model = board_model(doc, full=True, silk=False, shapes=False)
    scale = L.geometry_scale(doc)
    if model is None or scale is None:
        raise AdapterError("E_UNSUPPORTED", "The board's geometry can be read only on a board in mils with an outline; switch the "
                           "board's unit to mils in Layout, or set the outline with pcb update.", dispatch_state="not_started")
    copper = copper_from_model(model, int(doc.LayerCount), {L.name_of(net) for net in L.collection(doc, "Nets")})
    try:
        copper.keepouts = [(k["kind"], k.get("layer"), ST.polygon(k["shape"].get("path"), k["shape"].get("circle")))
                           for k in keepouts(doc, scale, geometry=True)
                           if k.get("kind") in TP.TRACE_KEEPOUTS + TP.VIA_KEEPOUTS and k.get("shape")]
    except Exception:  # noqa: BLE001 - keepouts that cannot be read are left out
        copper.keepouts = []
    for shape in L.collection(doc, "PlaneShapes"):
        try:
            record = geometry_record(shape.Geometry, scale)
            if record:
                copper.planes.append((int(shape.Layer), L.net_name(shape), ST.polygon(record.get("path"), record.get("circle"))))
        except Exception:  # noqa: BLE001 - a shape that cannot be read is left out
            continue
    copper.border = _route_border(doc)
    copper.bodies = part_bodies(doc, scale)
    return copper


def copper_from_model(model: dict[str, Any], layer_count: int, nets: set[str]) -> TP.Copper:
    """The copper of a board model as the plan checks take it, with the model kept for the fan-outs."""
    copper = TP.Copper(layer_count=layer_count, nets=nets)
    copper.pads = [(int(p.get("layer") or 1), str(p.get("net") or ""), ST.polygon(p.get("path"), p.get("circle"))) for p in model["pads"]]
    copper.traces = [(int(t.get("layer") or 1), str(t.get("net") or ""), float(t.get("width") or 0.0), _centre_line(t["path"]))
                     for t in model["traces"] if t.get("path")]
    copper.vias = [(str(v.get("net") or ""), float(v["circle"][0]), float(v["circle"][1]), float(v["circle"][2]))
                   for v in model["vias"]]
    copper.model = model  # the parts and their pads, for the fan-outs
    copper.rules = None
    copper.flex = None
    return copper


def board_obstacles(doc: Any, path: str, copper: TP.Copper) -> GD.Obstacles:
    """All the board's copper, inside its route border and clear of its cutouts, with its clearance rules."""
    if not copper.bodies and L.geometry_scale(doc) is not None:
        copper.bodies = part_bodies(doc, L.geometry_scale(doc))
    return GD.Obstacles(copper, TP.Plan(), _rules_of(doc, path, copper), _route_border(doc) or _outline_polygon(
        LN.outline_corners(_outline_rows(doc))), _cutouts(doc))


DEFAULT_CLEARANCE_MM = 0.1  # the clearance taken where the board's rules cannot be read


def ruled_obstacles(doc: Any, path: str, copper: TP.Copper) -> GD.Obstacles:
    """The board's copper with its clearance rules, or, where the rules cannot be read (a board
    without its project beside it), with ``DEFAULT_CLEARANCE_MM`` between any two nets."""
    try:
        return board_obstacles(doc, path, copper)
    except AdapterError:
        rules = TP.Rules(net_class_of={}, widths={}, clearance=lambda *_args: DEFAULT_CLEARANCE_MM)
        return GD.Obstacles(copper, TP.Plan(), rules, _route_border(doc) or _outline_polygon(LN.outline_corners(_outline_rows(doc))),
                            _cutouts(doc))


def _rules_of(doc: Any, path: str, copper: TP.Copper) -> TP.Rules:
    """The clearance rules, read once per reading of the copper: each reading opens the constraints."""
    if getattr(copper, "rules", None) is None:
        copper.rules = _trace_rules(doc, path)
    return copper.rules


def _flex_of(doc: Any, copper: TP.Copper) -> dict[str, Any]:
    """The planes, keepouts and outline the flex rules read, once per reading of the copper."""
    if getattr(copper, "flex", None) is None:
        copper.flex = O.flex_model(doc)
    return copper.flex


def _routing_plan(ctx: Context) -> tuple[TP.Plan, list[LN.Lane]]:
    """The plan's traces and vias, and its lanes, which become traces on the board's outline (``_laid``)."""
    source = str(base.need_input(ctx, "file"))
    try:
        data = json.loads(Path(source).read_text(encoding="utf-8"))
    except OSError as exc:
        raise AdapterError("E_NOT_FOUND", f"The routing plan {source} cannot be read: {exc.strerror}.", dispatch_state="not_started") from None
    except ValueError as exc:
        raise AdapterError("E_VALIDATION", f"The routing plan {source} is not JSON: {exc}.", dispatch_state="not_started") from None
    try:
        lanes = LN.parse(data.pop("lanes")) if isinstance(data, dict) and "lanes" in data else []
        fanouts = TP.parse_fanouts(data.pop("fanouts")) if isinstance(data, dict) and "fanouts" in data else []
        guards = GD.parse_guards(data.pop("guards")) if isinstance(data, dict) and "guards" in data else []
        rings = GD.parse_rings(data.pop("rings")) if isinstance(data, dict) and "rings" in data else []
        if guards and not lanes:
            raise TP._error("guards run beside lanes, and the plan has none")
        plan = TP.parse(data, lanes=bool(lanes or fanouts or rings))
        plan.fanouts, plan.guards, plan.rings = fanouts, guards, rings
        return plan, lanes
    except PlanError as exc:
        raise AdapterError(exc.code, exc.message, dispatch_state="not_started") from None


def _outline_rows(doc: Any) -> list[tuple[float, float, float]]:
    """The board outline's (x, y, r) rows in mm."""
    scale = L.geometry_scale(doc)
    outline = doc.BoardOutline
    if scale is None or outline is None:
        raise AdapterError("E_UNSUPPORTED", "Lanes follow the board outline, which can be measured only on a board in mils or mm with "
                           "an outline; switch the board's unit in Layout or draw the outline first.", dispatch_state="not_started")
    rows = outline.Geometry.PointsArray
    return [(float(x) * scale, float(y) * scale, float(r) * scale) for x, y, r in zip(*rows[:3], strict=False)]


def _model_pins(model: dict[str, Any], refdes: str) -> list[FO.Pin] | None:
    """A placed part's pins as the board model read them: each pin's first pad and its extents."""
    part = next((c for c in model.get("components") or [] if c.get("refdes") == refdes and c.get("pins")), None)
    if part is None:
        return None
    pads: dict[str, list[dict[str, Any]]] = {}
    for pad in model.get("pads") or []:
        if pad.get("refdes") == refdes:
            pads.setdefault(str(pad.get("pin")), []).append(pad)
    pins = []
    for pin in part["pins"]:
        shapes = pads.get(str(pin["pin"]))
        if not shapes:
            continue
        layer = int(shapes[0].get("layer") or 1)  # the first pad's layer, its shapes there taken together
        points = [p for pad in shapes if int(pad.get("layer") or 1) == layer for p in ST.polygon(pad.get("path"), pad.get("circle"))]
        box = (min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points))
        pins.append(FO.Pin(str(pin["pin"]), pin.get("net") or None, (float(pin["x"]), float(pin["y"])), box, layer, points))
    return pins


def _fanout_pins(component: Any) -> list[FO.Pin]:
    pins = []
    for pin in L.collection(component, "Pins"):
        pads = L.collection(pin, "Pads")
        if not pads:
            continue
        pins.append(FO.Pin(L.name_of(pin), L.net_name(pin) or None,
                           (float(pin.GetPositionX(L.UNIT_MM)), float(pin.GetPositionY(L.UNIT_MM))),
                           L.extrema_mm(pads[0]), int(pads[0].Layer)))
    return pins


def _fanned(doc: Any, path: str, plan: TP.Plan, copper: TP.Copper | None = None,
            lanes: list[LN.Lane] | None = None) -> tuple[TP.Plan, dict[str, FO.Fanout], set[int]]:
    """The plan with each fan-out's stubs, joins and vias, the fan-outs by connector, and the indices
    of the stubs in the plan's traces. A near pin of a net whose lane starts or ends at the connector
    on another layer than its pads changes to the lane's layer through a via of the fan-out. Each
    stub and join keeps within its net class's widths on its layer: a join as wide as the pads it
    joins is drawn no wider than the class allows."""
    from . import layout_parts
    from .plan.shapes import flatten

    if not plan.fanouts:
        return plan, {}, set()
    body = FO.centroid(flatten(_outline_rows(doc)))
    rules = _rules_of(doc, path, copper) if copper is not None else _trace_rules(doc, path)
    stubs: set[int] = set()
    clear = _clearances(path)
    traces, vias, notes = list(plan.traces), list(plan.vias), list(plan.notes)
    fanouts: dict[str, FO.Fanout] = {}
    keepouts = copper.keepouts if copper is not None else _copper(doc).keepouts
    for spec in plan.fanouts:
        model = getattr(copper, "model", None)
        pins = _model_pins(model, spec["refdes"]) if model else None
        if pins is None:
            pins = _fanout_pins(layout_parts.find(doc, spec["refdes"]))
        radius, why = via_pad_radius(doc, path, spec["padstack"])
        if radius is None:
            raise AdapterError("E_VALIDATION", f"The routing plan is not valid: the size of the via {spec['padstack']} is not known: {why}.",
                               dispatch_state="not_started")
        try:
            lift = {lane.net: lane.layer for lane in lanes or [] if spec["refdes"] in (lane.from_ref, lane.to_ref)}
            pad_layer = pins[0].layer if pins else None
            kept = [("via" if kind in TP.VIA_KEEPOUTS else "trace", poly) for kind, layer, poly in keepouts
                    if kind in TP.VIA_KEEPOUTS or (kind in TP.TRACE_KEEPOUTS and layer == pad_layer)]
            fanout = FO.plan(pins, body, radius, clear.via_to_pad, clear.via_to_via, spec["layer"],
                             set(spec["plane_nets"]), set(spec["skip"]), spec["stub_width"], set(spec["give_way"]),
                             set(spec["nets"]) if spec.get("nets") is not None else None, lift, kept, spec.get("beyond", False))
        except PlanError as exc:
            raise AdapterError(exc.code, f"The routing plan is not valid: the fan-out of {spec['refdes']}: {exc.message}.",
                               dispatch_state="not_started") from None
        fanouts[spec["refdes"]] = fanout
        for net, layer, width, points in fanout.traces:
            if abs(width - spec["stub_width"]) < 1e-9:
                stubs.add(len(traces))
            traces.append(TP.Trace(net, layer, within_class(rules, net, layer, width), list(points),
                                   origin=f"the fan-out of {spec['refdes']}"))
        vias += [TP.Via(net, at, spec["padstack"], origin=f"the fan-out of {spec['refdes']}") for net, at in fanout.vias]
        if fanout.loose:
            notes.append(f"{spec['refdes']} leaves pins {', '.join(fanout.loose)} to connect: no via of its fan-out is free for them")
    return TP.Plan(traces=traces, vias=vias, notes=notes), fanouts, stubs


def within_class(rules: TP.Rules, net: str, layer: int, width: float) -> float:
    """``width`` brought within the widths the net's class allows on the layer."""
    low, high = rules.widths.get(rules.net_class_of.get(net, "(Default)"), {}).get(layer, (0.0, math.inf))
    return round(min(max(width, low), high), 4)


def _laid(doc: Any, path: str, plan: TP.Plan, lanes: list[LN.Lane], copper: TP.Copper | None = None) -> TP.Plan:
    """The plan with each fan-out, each lane as a trace along the board outline, and its guards and
    rings, which keep clear of the board's ``copper`` (read here when not given)."""
    guards, rings = plan.guards, plan.rings
    moving = _guard_lanes(guards, lanes)
    plan = _lanes_laid(doc, path, plan, lanes, copper, list(moving.values()))
    if not guards and not rings:
        return plan
    return _guarded(doc, path, plan, lanes, guards, rings, copper or _copper(doc), moving)


def _guard_lanes(guards: list[GD.Guard], lanes: list[LN.Lane]) -> dict[tuple[int, int], LN.Lane]:
    """Each guard line that moves with jogging lanes, as a lane of its own by guard and line, so that
    its crossings are staggered with theirs (``LN.stagger``). A guard whose lanes cannot be read here
    is left to ``_guarded``, which says why."""
    out: dict[tuple[int, int], LN.Lane] = {}
    for index, guard in enumerate(guards):
        try:
            groups = GD.members(guard, lanes, f"guards[{index}]")
            lines = GD.guard_offsets(guard, groups, f"guards[{index}]")
            moves = GD.guard_jogs(guard, groups, f"guards[{index}]")
        except PlanError as exc:
            if "jog" in exc.message:
                raise AdapterError(exc.code, exc.message, dispatch_state="not_started") from None
            continue
        first = groups[0][0]
        for line, ((offset, width), targets) in enumerate(zip(lines, moves, strict=True)):
            if targets:
                out[(index, line)] = replace(first, net=guard.net, width=width, offset=offset, neck=None,
                                             jogs=[(at, moved, 0.0) for at, moved in targets])
    return out


def _moved_jogs(moving: dict[tuple[int, int], LN.Lane] | None, index: int, line: int) -> list[tuple[Any, float, float]]:
    """The jogs of a guard's line that moves with its lanes, staggered with theirs; none for a line that does not."""
    pseudo = (moving or {}).get((index, line))
    return list(pseudo.jogs) if pseudo is not None else []


def _runs_forward(lane: LN.Lane, traces: list[TP.Trace], ring: list[tuple[tuple[float, float], float]]) -> bool:
    """Whether the lane, as drawn, runs with its edge's chain: its trace starts nearer the chain's start."""
    chain, _left = LN.edge_chain(ring, lane.edge)
    drawn = next((t for t in traces if t.net == lane.net and t.layer == lane.layer and t.joins is not None), None) or         next((t for t in traces if t.net == lane.net and t.layer == lane.layer), None)
    if drawn is None or len(chain) < 2:
        return True
    start = drawn.points[0]
    return math.dist(start, chain[0][0]) <= math.dist(start, chain[-1][0])


def _route_border(doc: Any) -> list[tuple[float, float]] | None:
    """Layout's route border as a polygon in mm, or None when the board has none to read."""
    from .plan.shapes import flatten

    scale = L.geometry_scale(doc)
    border = com.member(doc, "RouteBorder")
    if scale is None or border is None:
        return None
    try:
        rows = border.Geometry.PointsArray
        polygon = flatten([(float(x) * scale, float(y) * scale, float(r) * scale) for x, y, r in zip(*rows[:3], strict=False)])
    except Exception:  # noqa: BLE001 - no route border to read
        return None
    if len(polygon) > 1 and polygon[0] == polygon[-1]:
        polygon.pop()
    return polygon if len(polygon) >= 3 else None


def _zone_shapes(flex: dict[str, Any]) -> list[list[tuple[float, float]]]:
    """Each bend zone's own outline where a keepout draws it, else its band's rectangle."""
    return [[(float(x), float(y)) for x, y in zone["polygon_mm"]] if zone.get("polygon_mm") else
            [(zone["extents_mm"][0], zone["extents_mm"][1]), (zone["extents_mm"][2], zone["extents_mm"][1]),
             (zone["extents_mm"][2], zone["extents_mm"][3]), (zone["extents_mm"][0], zone["extents_mm"][3])]
            for zone in board_metrics.bend_zones(flex)]


def _guarded(doc: Any, path: str, plan: TP.Plan, lanes: list[LN.Lane], guards: list[GD.Guard], rings: list[GD.Ring],
             copper: TP.Copper, moving: dict[tuple[int, int], LN.Lane] | None = None) -> TP.Plan:
    """The plan with ground traces beside the lanes its guards name and around the outline, each kept
    where it clears the other nets' copper inside the route border, with vias along them."""
    flex = _flex_of(doc, copper)
    ring = LN.sharpened(LN.outline_corners(_outline_rows(doc)), O.bend_boxes(doc, flex), _zone_shapes(flex))
    radii: dict[str, float] = {}

    def radius_of(padstack: str) -> float:
        if padstack not in radii:
            radius, why = via_pad_radius(doc, path, padstack)
            if radius is None:
                raise AdapterError("E_VALIDATION", f"The routing plan is not valid: the size of the via {padstack} is not known: {why}.",
                                   dispatch_state="not_started")
            radii[padstack] = radius
        return radii[padstack]

    for via in plan.vias:
        via.radius = via.radius or radius_of(via.padstack)
    obstacles = GD.Obstacles(copper, plan, _rules_of(doc, path, copper), _route_border(doc) or _outline_polygon(ring), _cutouts(doc))
    joined = [0]  # the free ends joined to copper of their net
    bends = O.bend_boxes(doc, flex)
    traces, vias, notes = list(plan.traces), list(plan.vias), list(plan.notes)
    findings: list[dict[str, Any]] = []
    loose: list[dict[str, Any]] = []  # each end left joined to nothing, tried again once every line has its vias

    def targets_of(net: str, layer: int, own: GD._Segments, width: float) -> list[GD.Point]:
        """The pins and vias of the net an end may join: not copper its own line already touches,
        to which a join would run back over the line."""
        def apart(at: GD.Point, reach: float) -> bool:
            return own.near(at, width / 2 + reach) > width / 2 + reach

        found = [(x, y) for owner, x, y, r in copper.vias if owner == net and apart((x, y), r)]
        found += [v.at for v in vias if v.net == net and apart(v.at, v.radius)]
        found += [centre for pad_layer, owner, poly in copper.pads if owner == net and pad_layer == layer
                  for centre in [pad_centre(poly)] if apart(centre, max(math.dist(centre, q) for q in poly))]
        return found

    def free_end_row(what: str, net: str, layer: int, thin: float, width: float, tip: GD.Point, outward: GD.Point, first: bool,
                     path: list[GD.Point], targets: list[GD.Point]) -> dict[str, Any]:
        why = GD.why_no_join(tip, outward, net, layer, thin, obstacles, targets)
        pour = GD.open_around(path, first, net, layer, width, obstacles)
        return {"kind": "free_end", "what": what, "net": net, "layer": layer, **why, "end_mm": _xy(tip),
                **({"pour_at_mm": list(pour)} if pour else {}), "next": _free_end_next(why, net, layer, pour)}

    def lay(net: str, layer: int, width: float, chain: GD.Chain, start: float, end: float, pitch: float | None,
            padstack: str | None, aside: list[float] | None = None, left: bool = True,
            ends: tuple[bool, bool] = (True, True), join_width: float | None = None, what: str = "") -> tuple[float, int, int]:
        """Draw a stretch of a chain with its vias: its length, the vias placed, and its ends left
        without one. A free end joins nearby copper of its net with a trace ``join_width`` wide. A
        stretch with no via whose ends join nothing would float, and an end that joins nothing is a
        hanger: each goes into ``findings``, with what stands in the way."""
        placed = 0
        thin = min(width, join_width or width)
        ends = list(ends)
        at_end: dict[int, list[GD.Point]] = {}  # the join, stub or run-on piece at each end, from the line's end outwards
        run_on: list[list[GD.Point]] = []
        for number in (0, 1):  # an end on a via or pad of its net is drawn on to its centre at the line's width
            tip = GD._at_length(chain.line, start if number == 0 else end)
            held = obstacles.holder(tip, net, layer) if ends[number] else None
            if held is None:
                continue
            centre, (x0, y0, x1, y1) = held
            if math.dist(tip, centre) < GD.END_SNAP_MM or GD._path_clear([tip, centre], net, layer, width, obstacles):
                ends[number] = False  # joined to it
                joined[0] += 1
                if math.dist(tip, centre) >= GD.END_SNAP_MM:
                    at_end[number] = [tip, centre]
                    run_on.append([tip, centre])
                continue
            out, limit = 0.0, 2 * (math.hypot(x1 - x0, y1 - y0) + width)  # else the end leaves it, to join copper or take a via
            while out < limit:
                q = GD._at_length(chain.line, start + out if number == 0 else end - out)
                if not (x0 - 1e-6 <= q[0] <= x1 + 1e-6 and y0 - 1e-6 <= q[1] <= y1 + 1e-6):
                    break
                out += GD.END_SNAP_MM
            if number == 0:
                start = min(start + out + GD.END_SNAP_MM, end)
            else:
                end = max(end - out - GD.END_SNAP_MM, start)
        start, end = GD.snapped(chain, start, end)
        if end - start < GD.END_SNAP_MM:  # nothing left outside the vias
            return 0.0, 0, 0, {"rows": [], "line": [], "placed": 0}
        points, radii = GD.drawn(chain, start, end)
        whole = TP.Trace(net, layer, width, points, radii).path
        own = GD._Segments(whole)
        targets = targets_of(net, layer, own, width)
        joins: list[list[GD.Point]] = []
        unjoined: list[tuple[int, GD.Point, GD.Point]] = []
        for number, wanted in enumerate(ends):  # first, each end joins copper of its net nearby, the line kept whole
            if not wanted:
                continue
            tip = points[0] if number == 0 else points[-1]
            inner = whole[1] if number == 0 else whole[-2]
            length = math.dist(tip, inner) or 1.0
            outward = ((tip[0] - inner[0]) / length, (tip[1] - inner[1]) / length)
            path = GD.join(tip, outward, net, layer, thin, obstacles, targets, walls=_walls(traces, net, layer, tip))
            back = 0.0
            while path is not None and GD.heads_back(outward, path) and back < GD.CUT_BACK_MM:
                # the join turns back over the line, a hairpin: cut the line back until a join leaves it going on
                back += GD.CUT_STEP_MM
                cut = (start + back, end) if number == 0 else (start, end - back)
                if cut[1] - cut[0] < GD.END_SNAP_MM:
                    break
                cut_points, cut_radii = GD.drawn(chain, *cut)
                try:
                    cut_whole = TP.Trace(net, layer, width, cut_points, cut_radii).path
                except PlanError:  # cut inside an arc that no longer fits the piece left: cut further
                    continue
                cut_tip, cut_inner = (cut_whole[0], cut_whole[1]) if number == 0 else (cut_whole[-1], cut_whole[-2])
                size = math.dist(cut_tip, cut_inner) or 1.0
                cut_out = ((cut_tip[0] - cut_inner[0]) / size, (cut_tip[1] - cut_inner[1]) / size)
                cut_path = GD.join(cut_tip, cut_out, net, layer, thin, obstacles, targets, walls=_walls(traces, net, layer, cut_tip))
                if cut_path is not None and not GD.heads_back(cut_out, cut_path):
                    start, end = cut
                    points, radii, whole = cut_points, cut_radii, cut_whole
                    tip, outward, path = cut_tip, cut_out, cut_path
            if path is not None and GD.heads_back(outward, path):  # no cut leaves a join going on: the end joins nothing
                path = None
            if path is None:
                unjoined.append((number, tip, outward))
                continue
            joins.append(path)
            at_end[number] = path
            obstacles.add_trace(net, layer, thin, path, [False] * (len(path) - 1))
            joined[0] += 1
        if pitch and padstack and unjoined:  # then an end that joins nothing takes a via near it, the line cut back to it
            radius = radius_of(padstack)
            wanted = tuple(any(number == side for number, _tip, _out in unjoined) for side in (0, 1))
            start, end, spots, done = GD.end_spots(chain, start, end, net, radius, max(pitch / 2, GD.END_REACH_MM), obstacles, bends,
                                                   aside, left, wanted, layer, width, thin)
            vias.extend(TP.Via(net, at, padstack, radius) for at in spots)
            placed = len(spots)
            unjoined = [row for row in unjoined if not done[row[0]]]
            if any(done):
                points, radii = GD.drawn(chain, start, end)
            for at in spots:  # a via moved off the line: the line's end is joined to its centre, where Layout joins it
                tip = min((points[0], points[-1]), key=lambda q: math.dist(q, at))
                if math.dist(tip, at) < GD.END_SNAP_MM:
                    continue
                at_end[0 if tip == points[0] else 1] = [tip, at]
                if GD.in_pad_box(tip, at, radius):
                    run_on.append([tip, at])  # drawing runs the end on to the centre at the line's width
                else:
                    joins.append([tip, at])  # a stub
        trace = TP.Trace(net, layer, width, points, radii, free_ends=True, origin=what)
        traces.append(trace)
        obstacles.add_trace(net, layer, width, trace.path)
        for piece in run_on:
            obstacles.add_trace(net, layer, width, piece, [False])
        traces.extend(TP.Trace(net, layer, thin, path, origin=f"a join of {what}") for path in joins)
        left_free = [(tip, outward, number == 0) for number, tip, outward in unjoined]
        free = len(left_free)
        line = GD.cut(chain.line, start, end)
        if pitch and padstack:
            radius = radius_of(padstack)
            for at in GD.vias_along(line, net, radius, pitch, obstacles, bends, aside, left, width):
                vias.append(TP.Via(net, at, padstack, radius))
                placed += 1
            if at_end:  # then along the line with its joins as one, as pcb check measures it: a via where the joint leaves a gap
                first, last = at_end.get(0, []), at_end.get(1, [])
                whole_line = list(reversed(first))[:-1] + line + last[1:]
                widths = [thin] * max(len(first) - 1, 0) + [width] * (len(line) - 1) + [thin] * max(len(last) - 1, 0)
                joints = [GD.length_of(first)] if first else []
                joints += [GD.length_of(whole_line) - GD.length_of(last)] if last else []
                for at in GD.vias_along(whole_line, net, radius, pitch, obstacles, bends, GD.across(thin, radius), width=widths,
                                        half_ends=False, joints=joints):
                    vias.append(TP.Via(net, at, padstack, radius))
                    placed += 1
        floating = False
        group: dict[str, Any] | None = None
        if not placed and len(left_free) == 2:  # a ring's half met by the other half at the ring's start is held by it
            spot = GD.via_spot(line, net, radius_of(padstack), obstacles, bends,
                               [(1.0 if left else -1.0) * off for off in aside or [0.0]], width) if pitch and padstack else None
            if spot is not None:  # none at its pitch or at its ends, but one fits elsewhere along it
                vias.append(TP.Via(net, spot, padstack, radius_of(padstack)))
                obstacles.add_via(net, spot, radius_of(padstack))
                placed += 1
            else:
                why = GD.why_no_via(line, net, radius_of(padstack), obstacles, bends,
                                    [(1.0 if left else -1.0) * off for off in aside or [0.0]], width) if pitch and padstack else \
                    {"blocked_by": [], "reason": "it asks for no vias"}
                side = GD.open_side(line, net, layer, width, obstacles)
                row = {"what": what, "net": net, "layer": layer, "from_mm": _xy(points[0]), "to_mm": _xy(points[-1]),
                       "length_mm": round(end - start, 3), **(why or {})}
                if side is None:  # hemmed in on both sides all along: no pour reaches it either
                    floating = True
                    group = {"kind": "floating", **row, "next": _floating_next(why, what)}
                else:
                    group = {"kind": "pour_only", **row, "pour_at_mm": list(side),
                             "next": f"only a pour of {net} on layer {layer} can ground it, such as at {tuple(side)}; "
                                     "pcb check tells whether one did"}
                findings.append(group)
        added = []
        for tip, outward, first in left_free:
            end_row = None if floating else free_end_row(what, net, layer, thin, width, tip, outward, first, trace.path, targets)
            if end_row is not None:
                findings.append(end_row)
                added.append(end_row)
            loose.append({"row": end_row, "group": group, "what": what, "net": net, "layer": layer, "thin": thin, "width": width,
                          "tip": tip, "outward": outward, "first": first, "own": own, "path": trace.path, "line": line})
        return end - start, placed, free, {"rows": added, "line": line, "placed": placed}

    def held_by_a_via(spec: GD.Ring, layer: int, what: str, halves: list[dict[str, Any]], aside: list[float] | None,
                      left: bool) -> int:
        """The ring's stretch through its start, laid as two halves that meet there, or the whole
        ring with no ends at all, has no via: one where it fits along it, else it floats or only a
        pour can ground it, as one line. Returns the vias placed."""
        line = halves[0]["line"] + (halves[1]["line"][1:] if len(halves) > 1 else [])
        signed = [(1.0 if left else -1.0) * off for off in aside or [0.0]]
        radius = radius_of(spec.padstack) if spec.pitch and spec.padstack else 0.0
        spot = GD.via_spot(line, spec.net, radius, obstacles, bends, signed, spec.width) if radius else None
        if spot is not None:
            vias.append(TP.Via(spec.net, spot, spec.padstack, radius))
            obstacles.add_via(spec.net, spot, radius)
            return 1
        why = GD.why_no_via(line, spec.net, radius, obstacles, bends, signed, spec.width) if radius else \
            {"blocked_by": [], "reason": "it asks for no vias"}
        side = GD.open_side(line, spec.net, layer, spec.width, obstacles)
        name = f"{what} on layer {layer}"
        row = {"what": name, "net": spec.net, "layer": layer, "from_mm": _xy(line[0]), "to_mm": _xy(line[-1]),
               "length_mm": round(GD.length_of(line), 3), **(why or {})}
        rows = [r for half in halves for r in half["rows"]]
        mine = [entry for entry in loose if any(entry["line"] is half["line"] for half in halves)]
        if side is None:
            group = {"kind": "floating", **row, "next": _floating_next(why, name)}
            findings[:] = [f for f in findings if not any(f is r for r in rows)]  # its ends are the line's
            for entry in mine:
                entry["row"] = None
        else:
            group = {"kind": "pour_only", **row, "pour_at_mm": list(side),
                     "next": f"only a pour of {spec.net} on layer {layer} can ground it, such as at {tuple(side)}; "
                             "pcb check tells whether one did"}
        findings.append(group)
        for entry in mine:
            entry["group"] = group
        return 0

    def rejoined() -> None:
        """Each end left joined to nothing tries again, now that every line has its vias: a via
        placed along a later line may stand within its reach. A line that floated, or that only a
        pour could ground, is held by an end joined now."""
        settled = []
        for entry in loose:
            net, layer, thin, tip = entry["net"], entry["layer"], entry["thin"], entry["tip"]
            path = GD.join(tip, entry["outward"], net, layer, thin, obstacles, targets_of(net, layer, entry["own"], entry["width"]),
                           walls=_walls(traces, net, layer, tip))
            if path is None:
                continue
            traces.append(TP.Trace(net, layer, thin, path, origin=f"a join of {entry['what']}"))
            obstacles.add_trace(net, layer, thin, path, [False] * (len(path) - 1))
            joined[0] += 1
            entry["joined"] = True
            settled.append(entry)
        for entry in settled:
            if entry["row"] is not None:
                findings[:] = [f for f in findings if f is not entry["row"]]
            group = entry["group"]
            if group is None or not any(f is group for f in findings):
                continue
            findings[:] = [f for f in findings if f is not group]  # held by the end joined now
            for other in loose:
                if other["group"] is group and not other.get("joined") and other["row"] is None:
                    other["row"] = free_end_row(other["what"], other["net"], other["layer"], other["thin"], other["width"], other["tip"],
                                                other["outward"], other["first"], other["path"],
                                                targets_of(other["net"], other["layer"], other["own"], other["width"]))
                    findings.append(other["row"])

    try:
        lines: list[GD.Line] = []
        for index, guard in enumerate(guards):
            groups = GD.members(guard, lanes, f"guards[{index}]")
            beside = [lane for group in groups for lane in group]
            for lane in beside:  # a gap under the rules leaves no stretch beside the lanes; say so instead of laying nothing
                need = obstacles.need(guard.net, lane.net, lane.layer, "trace_to_trace")
                if guard.gap < need - TP.CLEARANCE_TOLERANCE:
                    raise TP._error(f"guards[{index}] keeps {guard.gap:g} mm from {lane.net}, but the clearance rules ask "
                                    f"{need:g} mm between {guard.net} and {lane.net} on layer {lane.layer}, so no stretch of it "
                                    f"beside the lanes would clear; give gap_mm of at least {need:g}, or import a rule between "
                                    "their classes that allows the gap")
            cls = obstacles.rules.net_class_of.get(guard.net, "(Default)")
            widest = obstacles.rules.widths.get(cls, {}).get(beside[0].layer, (0.0, math.inf))[1]
            # a line between two groups fills the room up to its net class's widest trace, so it stays within the rules
            forward = _runs_forward(beside[0], traces, ring)
            lines += [GD.Line(beside[0].layer, guard.net, offset, max(guard.width, min(width, widest)), beside, guard.pitch,
                              guard.padstack, guard.min_run, guard.width, _moved_jogs(moving, index, line), forward)
                      for line, (offset, width) in enumerate(GD.guard_offsets(guard, groups, f"guards[{index}]"))]
        totals: dict[int, list[float]] = {}
        for item in GD.merged(lines):
            chain = GD.guard_chain(item.beside[0], item.offset, ring, item.jogs, item.forward)
            ranges = []
            for member in item.beside:
                mine = [t for t in traces if t.net == member.net and t.layer == member.layer]
                ranges.append(GD.lane_span(chain.line, GD.guard_line(member, member.offset, ring, member.jogs, item.forward), mine))
            ranges = [r for r in ranges if r is not None]
            total = totals.setdefault(item.layer, [0, 0.0, 0, 0, 0])
            total[0] += 1
            if not ranges:
                continue
            first, last = min(r[0] for r in ranges), max(r[1] for r in ranges)  # beside any of its lanes
            for begin, finish in GD.spans(chain, item.net, item.layer, item.width, obstacles, item.min_run):
                begin, finish = GD.snapped(chain, max(begin, first), min(finish, last))
                if finish - begin < item.min_run - 1e-9:
                    continue
                aside = GD.across(item.width, radius_of(item.padstack)) if item.pitch and item.padstack else None
                more, vias_placed, _free, _laid = lay(item.net, item.layer, item.width, chain, begin, finish, item.pitch, item.padstack,
                                                      aside, join_width=item.join_width, what=_guard_name(item))
                total[1] += more
                total[2] += vias_placed
                total[3] += 1
        later: list[tuple[str, int, str]] = []  # each layer's line of the preview; its ends without a via are counted at the end
        for layer, (count, length, placed, stretches, _none) in sorted(totals.items()):
            later.append(("guards", layer, f"guards on layer {layer}: {int(count)} lines kept in {int(stretches)} stretches, "
                                           f"{length:.1f} mm, {int(placed)} vias"))
        for index, spec in enumerate(rings):
            what = f"rings[{index}]"
            try:
                chain, left = GD.ring_chain(ring, spec.offset)
            except PlanError as exc:
                why = exc.message.removeprefix("The routing plan is not valid: ").rstrip(".")
                raise TP._error(f"{what} cannot follow the outline {spec.offset:g} mm inside it: {why}") from None
            for layer in spec.layers:
                length = 0.0
                count = placed = 0
                aside = GD.across(spec.width, radius_of(spec.padstack)) if spec.padstack else None  # inwards first; both ways, as pcb check looks
                stretches = GD.spans(chain, spec.net, layer, spec.width, obstacles, spec.min_run, closed=True)
                through = len(stretches) >= 1 and stretches[0][0] < 1e-9 and stretches[-1][1] > chain.length - 1e-9
                laid_out = []
                for number, (begin, finish) in enumerate(stretches):
                    ends = (not (through and number == 0), not (through and number == len(stretches) - 1))  # met at the ring's start
                    more, vias_placed, _free, laid = lay(spec.net, layer, spec.width, chain, begin, finish, spec.pitch, spec.padstack,
                                                         aside, left, ends, what=f"{what} on layer {layer}")
                    length, count, placed = length + more, count + 1, placed + vias_placed
                    laid_out.append(laid)
                if through:  # the stretch through the ring's start, laid as two halves, or the whole ring: one line
                    halves = [laid_out[-1], laid_out[0]] if len(laid_out) >= 2 else [laid_out[0]]
                    if not sum(half["placed"] for half in halves) and all(half["line"] for half in halves):
                        placed += held_by_a_via(spec, layer, what, halves, aside, left)
                    count -= 1 if len(stretches) >= 2 else 0
                later.append((what, layer, f"{what} on layer {layer}: {length:.1f} of {chain.length:.1f} mm in {count} stretches, "
                                           f"{placed} vias"))
        rejoined()
        if joined[0]:
            notes.append(f"guards and rings: {joined[0]} ends joined to a pin or via of their net nearby")
        for kind, layer, text in later:
            mine = [f for f in findings if f["layer"] == layer
                    and (f["what"] == f"{kind} on layer {layer}" if kind != "guards" else not f["what"].startswith("rings["))]
            free = sum(1 for f in mine if f["kind"] == "free_end") + 2 * sum(1 for f in mine if f["kind"] == "floating")
            notes.append(text + (f", {free} ends without a via" if free else ""))
    except PlanError as exc:
        raise AdapterError(exc.code, exc.message, dispatch_state="not_started") from None
    if len(traces) + len(vias) > TP.MAX_ITEMS:
        raise AdapterError("E_VALIDATION", f"The routing plan is not valid: it names more than {TP.MAX_ITEMS} items.",
                           dispatch_state="not_started")
    if findings:
        count = {kind: sum(1 for f in findings if f["kind"] == kind) for kind in ("floating", "pour_only", "free_end")}
        notes.append(f"guards and rings: {count['floating']} lines would float, {count['pour_only']} only a pour can ground and "
                     f"{count['free_end']} ends join nothing; trace check says what blocks each")
    return TP.Plan(traces=traces, vias=vias, notes=notes, findings=findings)


def _xy(point: tuple[float, float]) -> list[float]:
    return [round(point[0], 4), round(point[1], 4)]


def _guard_name(item: GD.Line) -> str:
    """A guard line by the lanes on either side of it, such as "guard between A and B"."""
    below = [lane for lane in item.beside if lane.offset < item.offset]
    above = [lane for lane in item.beside if lane.offset > item.offset]
    near = [max(below, key=lambda lane: lane.offset).net] if below else []
    near += [min(above, key=lambda lane: lane.offset).net] if above else []
    return f"guard between {near[0]} and {near[1]}" if len(near) == 2 else f"guard beside {near[0]}" if near else "guard"


def _short(why: dict[str, Any] | None) -> str:
    if not why or "gap_mm" not in why:
        return (why or {}).get("reason", "")
    return f"{', '.join(why['blocked_by'])}: {why['gap_mm']:g} mm where {why['needed_mm']:g} mm is needed at ({why['at_mm'][0]:g}, " \
           f"{why['at_mm'][1]:g})"


def _room(why: dict[str, Any]) -> str:
    """How much more room a blocked spot needs, said to the micrometre."""
    return f"{max(why['needed_mm'] - why['gap_mm'], 0.001):.3f} mm"


def _floating_next(why: dict[str, Any] | None, what: str) -> str:
    if why and "gap_mm" in why:
        return f"make {_room(why)} more room for a via there, or leave this stretch of the {what} out"
    return f"give the {what} via_pitch_mm and a padstack, or leave this stretch out"


def _free_end_next(why: dict[str, Any], net: str, layer: int, pour: tuple[float, float] | None) -> str:
    if pour is not None:
        return f"a pour of {net} on layer {layer} can reach this end, such as at {pour}; pcb check tells whether one did"
    if "gap_mm" in why:
        return (f"make {_room(why)} more room there, or leave this end free; other copper closes it in, so unless a pour of {net} "
                "reaches it, DRC reports it as a hanger, which pcb check shows")
    return (f"give the line a via near this end, or leave it free; other copper closes it in, so unless a pour of {net} reaches it, "
            "DRC reports it as a hanger, which pcb check shows")


def _floating_reason(finding: dict[str, Any]) -> str:
    return (f"the {finding['what']} on layer {finding['layer']} from {tuple(finding['from_mm'])} to {tuple(finding['to_mm'])} would "
            f"float: no via fits along it ({_short(finding)}) and neither end reaches copper of {finding['net']}; {finding['next']}")


def _walls(traces: list[TP.Trace], net: str, layer: int,
           tip: tuple[float, float]) -> list[tuple[str, float, list[tuple[float, float]], list[bool]]]:
    """The net, width and centre line of the plan's traces of other nets on the layer that pass near a
    free end, with which pieces of the line are chords of arcs."""
    out = []
    reach = GD.REACH_MM + 0.5
    for trace in traces:
        if trace.net == net or trace.layer != layer:
            continue
        xs, ys = [p[0] for p in trace.points], [p[1] for p in trace.points]
        if min(xs) - reach <= tip[0] <= max(xs) + reach and min(ys) - reach <= tip[1] <= max(ys) + reach:
            arcs: list[bool] = []
            path = TP.sampled(trace.points, trace.radii, GD.WALL_CHORD_MM, arcs)
            if GD._closest(path, tip)[0] <= reach:
                out.append((trace.net, trace.width, path, arcs))
    return out


def pad_centre(polygon: list[tuple[float, float]]) -> tuple[float, float]:
    """The centre of a pad's extents, its pin's origin for the pads a connector has."""
    xs, ys = [p[0] for p in polygon], [p[1] for p in polygon]
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


def _cutouts(doc: Any) -> list[list[tuple[float, float]]]:
    """The board's internal contours, such as slots, as polygons in mm."""
    from .layout_output import geometry_record

    scale = L.geometry_scale(doc)
    found = []
    for contour in L.collection(doc, "Contours") if scale is not None else ():
        try:
            if int(contour.ContourType) != CONTOUR_INTERNAL:
                continue
            record = geometry_record(contour.Geometry, scale)
        except Exception:  # noqa: BLE001 - a contour Layout cannot describe is left out
            continue
        if record:
            found.append(ST.polygon(record.get("path"), record.get("circle")))
    return found


def _outline_polygon(ring: list[tuple[tuple[float, float], float]]) -> list[tuple[float, float]]:
    """The outline's corners as a polygon, when the board has no route border to keep inside."""
    return [corner[0] for corner in ring]


def _board_pins(copper: TP.Copper | None, doc: Any) -> dict[str, tuple[float, float]]:
    """Every placed pin's position, by ``REFDES.PIN``."""
    model = getattr(copper, "model", None) or _copper(doc).model
    return {f"{part['refdes']}.{pin['pin']}": (float(pin["x"]), float(pin["y"]))
            for part in model.get("components") or [] for pin in part.get("pins") or []}


def _pin_ends(doc: Any, path: str, copper: TP.Copper | None, lanes: list[LN.Lane]) -> dict[str, Any]:
    """Every placed pin's position by ``REFDES.PIN``; for a pin a lane ends on, the pin and the point
    where a trace leaving it straight towards the board's body clears its row's pads."""
    from .plan.shapes import flatten

    ends: dict[str, Any] = dict(_board_pins(copper, doc))
    model = getattr(copper, "model", None) or _copper(doc).model
    body = FO.centroid(flatten(_outline_rows(doc)))
    try:
        clearance = _clearances(path).via_to_pad
    except AdapterError:  # a board without its project's rules: the default clearance
        clearance = DEFAULT_CLEARANCE_MM
    for lane in lanes:
        for ref in (lane.from_ref, lane.to_ref):
            if not ref or "." not in ref or ref not in ends:
                continue
            refdes, number = ref.rsplit(".", 1)
            exit_at = FO.pin_exit(_model_pins(model, refdes) or [], number, body, clearance, lane.width)
            if exit_at is not None:
                ends[ref] = [ends[ref], exit_at]
    return ends


def _lanes_laid(doc: Any, path: str, plan: TP.Plan, lanes: list[LN.Lane], copper: TP.Copper | None = None,
                guard_lanes: list[LN.Lane] | None = None) -> TP.Plan:
    """The plan with each fan-out, and each lane as a trace along the board outline; ``guard_lanes``,
    the guard lines that jog with their lanes, cross in turn with them."""
    if any("." in (ref or "") for lane in lanes for ref in (lane.from_ref, lane.to_ref)):
        try:
            LN.resolve_pins(lanes, _pin_ends(doc, path, copper, lanes))
        except PlanError as exc:
            raise AdapterError(exc.code, exc.message, dispatch_state="not_started") from None
    plan, fanouts, stubs = _fanned(doc, path, plan, copper, lanes)
    if not lanes:
        return plan
    flex = _flex_of(doc, copper) if copper is not None else O.flex_model(doc)
    bends = O.bend_boxes(doc, flex)
    zones = _zone_shapes(flex)
    ring = LN.sharpened(LN.outline_corners(_outline_rows(doc)), bends, zones)
    clearance = _clearances(path).via_to_pad if fanouts else 0.1
    if any(lane.jogs for lane in lanes):
        try:
            LN.stagger(lanes + list(guard_lanes or []), _clearances(path).via_to_pad)
        except AdapterError:  # a board without its project's rules: the default clearance
            LN.stagger(lanes + list(guard_lanes or []), DEFAULT_CLEARANCE_MM)
    try:
        expanded = [LN.expand(copy.copy(lane), ring, f"lanes[{index}]", fanouts, clearance, zones) for index, lane in enumerate(lanes)]
        owned = [(lane, piece) for lane, trace in zip(lanes, expanded, strict=True) for piece in LN.necked(lane, trace)]
    except PlanError as exc:
        raise AdapterError(exc.code, exc.message, dispatch_state="not_started") from None
    laid = [piece for _lane, piece in owned]
    plan = TP.Plan(traces=plan.traces, vias=plan.vias, notes=plan.notes + LN.swapped(expanded))
    traces = plan.traces + laid
    # a lane's fan-out stubs, and its neck, as wide as the lane wherever that clears; a net's other lanes keep their widths
    full = {lane.net: lane.width for lane in lanes if lane.from_ref or lane.to_ref}
    wanted = {i: full[t.net] for i, t in enumerate(plan.traces) if i in stubs and t.width < full.get(t.net, 0.0) - 1e-9}
    start = len(plan.traces)
    wanted.update({start + i: lane.width for i, (lane, piece) in enumerate(owned)
                   if lane.neck is not None and piece.width < lane.width - 1e-9})
    if copper is not None and wanted:
        traces = _widened(doc, path, plan, lanes, traces, copper, wanted)
    if len(traces) + len(plan.vias) > TP.MAX_ITEMS:
        raise AdapterError("E_VALIDATION", f"The routing plan is not valid: it names more than {TP.MAX_ITEMS} items.",
                           dispatch_state="not_started")
    return TP.Plan(traces=traces, vias=plan.vias, notes=plan.notes)


def _widened(doc: Any, path: str, plan: TP.Plan, lanes: list[LN.Lane], traces: list[TP.Trace], copper: TP.Copper,
              wanted: dict[int, float]) -> list[TP.Trace]:
    """The traces ``wanted`` names, a lane's neck or fan-out stubs, at the width it gives wherever that
    clears the board's copper, the plan's vias and the other nets' traces at their lanes' widths,
    narrow only where it must be (``LN.widened``)."""
    full = {lane.net: lane.width for lane in lanes}
    sizes: dict[str, float] = {}
    for via in plan.vias:  # the fan-out's vias have no size yet
        if not via.radius:
            if via.padstack not in sizes:
                sizes[via.padstack] = via_pad_radius(doc, path, via.padstack)[0] or 0.0
            via.radius = sizes[via.padstack]
    others = [TP.Trace(t.net, t.layer, max(t.width, full.get(t.net, t.width)), t.points, t.radii) for t in traces]
    obstacles = GD.Obstacles(copper, TP.Plan(traces=others, vias=plan.vias), _rules_of(doc, path, copper),
                             _route_border(doc) or _outline_polygon(LN.outline_corners(_outline_rows(doc))), _cutouts(doc))
    out: list[TP.Trace] = []
    for index, trace in enumerate(traces):
        width = wanted.get(index)
        if width is None:
            out.append(trace)
            continue
        # widened only where it keeps the rules whole: the slack a guard beside an arc is given is no room to grow into
        out += LN.widened(trace, width, lambda a, b, w, t=trace: obstacles.piece_clear(a, b, t.net, t.layer, w, 0.0))
    return out


def _plan_notes(doc: Any, path: str, plan: TP.Plan, copper: TP.Copper | None = None) -> list[str]:
    """What the plan's own copper shows before it is drawn: each pair's two lengths, the pairs
    recognised by name, and what the board's bend zones forbid of it."""
    lengths: dict[str, float] = {}
    for trace in plan.traces:
        lengths[trace.net] = lengths.get(trace.net, 0.0) + TP.length(trace)
    defined = _rules_of(doc, path, copper).pairs if copper is not None else None
    notes = [f"{a} and {b} are {lengths[a]:.3f} and {lengths[b]:.3f} mm long, {abs(lengths[a] - lengths[b]):.3f} mm apart"
             for a, b in board_metrics._pairs(sorted(lengths), defined)]
    model = {**(_flex_of(doc, copper) if copper is not None else O.flex_model(doc)),
             "layers": copper.layer_count if copper is not None else int(doc.LayerCount),
             "traces": [{"net": t.net, "layer": t.layer, "width": t.width, "path": TP.drawn(t.points, t.radii)} for t in plan.traces],
             "vias": [{"net": v.net, "circle": [v.at[0], v.at[1], 0.0]} for v in plan.vias]}
    flex = [row for row in board_metrics.flex_rules(model) if row["rule"] != "layer_change_outside_stiffener" or
            any(v.net == row.get("net") for v in plan.vias)]
    notes += [f"{row['net']}: {row['finding']}" for row in flex]
    return notes


def _trace_look(ctx: Context, doc: Any, plan: TP.Plan,
                copper: TP.Copper | None = None) -> tuple[list[str], dict[tuple[str, int], int], dict[str, int]]:
    path = ctx.document_path()
    radii: dict[str, float] = {}
    blocked: list[str] = []
    for via in plan.vias:
        if via.padstack not in radii:
            radius, why = via_pad_radius(doc, path, via.padstack)
            if radius is None:
                blocked.append(f"the size of the via {via.padstack} is not known: {why}")
            radii[via.padstack] = radius or 0.0
        via.radius = radii[via.padstack]
    copper = copper or _copper(doc)
    drawn, extended = _as_drawn(plan, copper)  # checked as drawn: an end on a via or a pad runs on to its centre
    rules = _rules_of(doc, path, copper)
    found = TP.check(drawn, copper, rules)
    planned = {TP.without_where(r) for r in TP.check(plan, copper, rules)} if extended else set()
    for number, reason in enumerate(found):
        if TP.without_where(reason) in planned:
            continue
        own = reason[:reason.find(":") + 1]
        names = [own] + [f"trace {n} of {net}:" for n, net in re.findall(r"trace (\d+) of (\S+) in the plan", reason)]
        name = next((name for name in names if name in extended), None)
        if name is not None:  # what only the run on to the centre brings
            places = " and ".join(f"({x:g}, {y:g})" for x, y in extended[name])
            who = "its" if name == own else f"the {name[:-1]}'s"
            found[number] = (f"{reason}, where {who} end runs on to the centre of the pin or via at {places} mm, at its width: "
                             "Layout joins a trace to a pin or via only there")
    blocked += found
    blocked += [_floating_reason(f) for f in plan.findings if f["kind"] == "floating"]  # copper joined to nothing is a defect
    counts: dict[str, int] = {}
    for net, *_rest in copper.vias:
        counts[net] = counts.get(net, 0) + 1
    return blocked, _drawn(doc, plan, copper), {v.net: counts.get(v.net, 0) for v in plan.vias}


def _as_drawn(plan: TP.Plan, copper: TP.Copper) -> tuple[TP.Plan, dict[str, list[tuple[float, float]]]]:
    """The plan's traces and vias as trace create draws them (``_extended``), and, by each trace's
    name in a reason (``trace 3 of GND:``), the places its ends run on to. The via radii must be
    known (``_trace_look``)."""
    points = _model_points(plan, copper)
    traces, extended = [], {}
    for index, trace in enumerate(plan.traces):
        pts, radii, extended_to = _extended(trace, points)
        traces.append(TP.Trace(trace.net, trace.layer, trace.width, pts, radii, trace.free_ends, origin=trace.origin))
        if extended_to:
            extended[f"trace {index + 1} of {trace.net}:"] = extended_to
    return TP.Plan(traces=traces, vias=plan.vias), extended


def _connection_points(doc: Any, plan: TP.Plan) -> dict[tuple[str, int], list[tuple[tuple[float, float], tuple[float, ...]]]]:
    """Where Layout joins a trace to a pin or a via of the plan's nets, per net and layer: each pin's
    origin with its pad's extents, and each via's centre with its extents. Layout connects a trace
    only at those exact points, not anywhere on the pad or via, and the coordinates Agents read are
    rounded, so ends are joined to them here."""
    points: dict[tuple[str, int], list[tuple[tuple[float, float], tuple[float, ...]]]] = {}
    layers = range(1, int(doc.LayerCount) + 1)
    for name in sorted({t.net for t in plan.traces}):
        net = L.find_net(doc, name)
        if net is None:
            continue
        for pin in L.collection(net, "Pins"):
            try:
                origin = (float(pin.GetPositionX(L.UNIT_MM)), float(pin.GetPositionY(L.UNIT_MM)))
                for pad in L.collection(pin, "Pads"):
                    points.setdefault((name, int(pad.Layer)), []).append((origin, L.extrema_mm(pad)))
            except Exception:  # noqa: BLE001 - a pin Layout cannot report keeps the plan's ends
                continue
        for via in L.collection(net, "Vias"):
            try:
                x0, y0, x1, y1 = L.extrema_mm(via)
            except Exception:  # noqa: BLE001
                continue
            for layer in layers:
                points.setdefault((name, layer), []).append((((x0 + x1) / 2, (y0 + y1) / 2), (x0, y0, x1, y1)))
    for via in plan.vias:
        box = (via.at[0] - via.radius, via.at[1] - via.radius, via.at[0] + via.radius, via.at[1] + via.radius)
        for layer in layers:
            points.setdefault((via.net, layer), []).append(((via.at[0], via.at[1]), box))
    return points


SNAP_MM = 0.01  # an end this close to a pin's origin or a via's centre is moved onto it


Points = dict[tuple[str, int], list[tuple[tuple[float, float], tuple[float, ...]]]]


def _extended(trace: TP.Trace, points: Points) -> tuple[list[tuple[float, float]], list[float], list[tuple[float, float]]]:
    """The trace as Layout draws it: an end that lies on a pin's pad or on a via extended to the
    pin's origin or the via's centre, at the trace's width; its points, their corner radii, and the
    places its ends were extended to."""
    found = points.get((trace.net, trace.layer), [])
    pts = [(float(p[0]), float(p[1])) for p in trace.points]
    radii = list(trace.radii)
    extended: list[tuple[float, float]] = []

    def target(end: tuple[float, float]) -> tuple[float, float] | None:
        for origin, (x0, y0, x1, y1) in found:
            if x0 - 1e-6 <= end[0] <= x1 + 1e-6 and y0 - 1e-6 <= end[1] <= y1 + 1e-6:
                return origin
        return None

    first, last = target(pts[0]), target(pts[-1])
    if first is not None and first != pts[0]:
        if math.dist(first, pts[0]) < SNAP_MM:
            pts[0] = first  # a few nanometres off: moved, not extended by a segment Layout would keep as a trace of its own
        else:
            pts.insert(0, first)
            radii.insert(0, 0.0)
            extended.append(first)
    if last is not None and last != pts[-1]:
        if math.dist(last, pts[-1]) < SNAP_MM:
            pts[-1] = last
        else:
            pts.append(last)
            radii.append(0.0)
            extended.append(last)
    if any(radii) and (pts[0] != trace.points[0] or pts[-1] != trace.points[-1]):
        radii = LN._fitted(pts, radii, [True] * len(pts))  # an end moved a hair onto a centre may leave its arc a hair too wide
    return pts, radii, extended


def _joined(trace: TP.Trace, points: Points) -> list[tuple[float, float, float]]:
    """The (x, y, r) rows Layout draws for the trace (``_extended``), each rounded corner as its arc."""
    pts, radii, _extended_to = _extended(trace, points)
    return TP.drawn(pts, radii)


def _model_points(plan: TP.Plan, copper: TP.Copper) -> Points:
    """``_connection_points`` from the board's copper as read, without asking Layout again: each
    pad's centre for its pin's origin, and each via's centre, with their extents."""
    nets = {t.net for t in plan.traces}
    points: Points = {}
    for layer, net, poly in copper.pads:
        if net in nets and poly:
            xs, ys = [p[0] for p in poly], [p[1] for p in poly]
            points.setdefault((net, layer), []).append((pad_centre(poly), (min(xs), min(ys), max(xs), max(ys))))
    vias = [(net, (x, y), r) for net, x, y, r in copper.vias] + [(v.net, v.at, v.radius) for v in plan.vias]
    for net, at, r in vias:
        if net in nets:
            for layer in range(1, copper.layer_count + 1):
                points.setdefault((net, layer), []).append((at, (at[0] - r, at[1] - r, at[0] + r, at[1] + r)))
    return points


COVER_TOLERANCE_MM = 0.01


def _drawn(doc: Any, plan: TP.Plan, copper: TP.Copper | None = None) -> dict[tuple[str, int], bool]:
    """For each net and layer of the plan, whether every point along its planned traces lies on a
    trace of that net and layer on the board (``copper``, read here when not given). Layout keeps one
    planned trace as several objects, split at its arcs and elsewhere, so the plan is matched by its
    geometry, not by a count of objects."""
    copper = copper or _copper(doc)
    wanted = {(t.net, t.layer) for t in plan.traces}
    grids: dict[tuple[str, int], TP._Grid] = {key: TP._Grid() for key in wanted}
    for layer, net, width, points in copper.traces:
        grid = grids.get((net, layer))
        if grid is not None:
            for a, b in zip(points, points[1:], strict=False):
                grid.add((a, b, width), [a, b], width / 2)
    out: dict[tuple[str, int], bool] = {key: True for key in wanted}
    for trace in plan.traces:
        key = (trace.net, trace.layer)
        if not out[key]:
            continue
        grid = grids[key]
        out[key] = all(any(TP.point_segment(point, a, b) <= COVER_TOLERANCE_MM + width / 2
                           for a, b, width in grid.near([point], COVER_TOLERANCE_MM)) for point in trace.path)
    return out


def _net_states(doc: Any, plan: TP.Plan) -> dict[str, Any]:
    """How many of the plan's nets are routed now, and the open connections of the others: a plan
    of dozens of nets listed net by net would not fit one response beside its receipt."""
    names = sorted({t.net for t in plan.traces} | {v.net for v in plan.vias})
    routed, still_open = 0, []
    for name in names:
        try:
            net = L.find_net(doc, name)
            opens = int(net.NumberOfOpens or 0)
        except Exception:  # noqa: BLE001 - a net Layout cannot report is left out
            continue
        if bool(com.member(net, "IsRouted")) and opens == 0:
            routed += 1
        else:
            still_open.append({"net": name, "opens": opens})
    return {"nets": {"total": len(names), "routed": routed}, "open": still_open[:OPEN_LISTED],
            **({"open_count": len(still_open)} if len(still_open) > OPEN_LISTED else {})}


PLANS_INDEX = "trace-plans"  # the kept plans, newest last, with the board each was drawn on
PLANS_KEPT = 20


def planned_drawn(store: Any, doc: Any, path: str, net: str, layer: int, copper: TP.Copper | None = None) -> bool | None:
    """Whether the newest plan drawn on this board with traces of ``net`` on ``layer`` still lies on
    the board's traces, as trace create's read-back measures it; None when no kept plan has them."""
    wanted = os.path.normcase(os.path.abspath(path))
    for row in reversed(store.get(PLANS_INDEX, []) or []):
        if row.get("doc") != wanted:
            continue
        data = store.get(row.get("key"))
        if not data:
            continue
        plan = _unkept(data)
        mine = [t for t in plan.traces if t.net == net and t.layer == layer]
        if mine:
            return _drawn(doc, TP.Plan(traces=mine), copper).get((net, layer))
    return None


OPEN_LISTED = 20  # the open nets trace create's result names; open_count counts them all


def _plan_key(ctx: Context) -> str | None:
    operation = ctx.operation_id
    return f"trace-plan-{operation.lower()}" if operation else None


def _kept(plan: TP.Plan) -> dict[str, Any]:
    return {"traces": [[t.net, t.layer, t.width, [list(p) for p in t.points], list(t.radii), t.free_ends] for t in plan.traces],
            "vias": [[v.net, list(v.at), v.padstack, v.radius] for v in plan.vias]}


def _unkept(data: dict[str, Any]) -> TP.Plan:
    return TP.Plan(traces=[TP.Trace(net, layer, width, [tuple(p) for p in points], list(radii), free) for net, layer, width, points, radii, free
                           in data["traces"]],
                   vias=[TP.Via(net, tuple(at), padstack, radius) for net, at, padstack, radius in data["vias"]])


NOTES_SHOWN = 8  # the preview names this many notes on the plan's own copper, and counts the rest
BLOCKED_SHOWN = 20  # and this many reasons the plan is refused, so that the preview fits one response
BLOCKED_CHARS = 4000  # and no more than this many characters of them


class TraceCreate(L.ReadsBack):
    """The traces and vias of a routing plan (``plan.trace_plan``), checked offline against the
    board's copper and rules before Layout is touched, then placed with ``PutTrace`` and ``PutVia``
    with Layout's online DRC off, since the check kept the rules. For the routes an Agent plans
    itself, such as a differential pair between two connectors."""

    command = "trace create"
    long_job = True

    def preview(self, ctx: Context) -> dict[str, Any]:
        path = ctx.document_path()
        given, lanes = _routing_plan(ctx)

        def look(app: Any, doc: Any) -> tuple[TP.Plan, list[str], dict[tuple[str, int], int], dict[str, int], list[str]]:
            copper = _copper(doc)
            plan = _laid(doc, path, given, lanes, copper)
            return plan, *_trace_look(ctx, doc, plan, copper), plan.notes + _plan_notes(doc, path, plan, copper)

        plan, blocked, traces, vias, notes = L.in_layout(ctx, look, budget_s=L.READ_BUDGET_S, what="checking the routing plan")
        added_vias: dict[str, int] = {}
        for v in plan.vias:
            added_vias[v.net] = added_vias.get(v.net, 0) + 1
        changes = [base.change(base.key(path, trace_key(net, layer)), "plan_drawn", drawn, True)
                   for (net, layer), drawn in sorted(traces.items())]
        changes += [base.change(base.key(path, via_key(net)), "count", vias.get(net, 0), vias.get(net, 0) + n) for net, n in added_vias.items()]
        targets = [base.target(path, "trace", "/traces", label="the routing plan's traces and vias")]
        laid = f", {len(lanes)} of them lanes along the board outline," if lanes else ""
        shown = "; ".join(notes[:NOTES_SHOWN]) + (f"; and {len(notes) - NOTES_SHOWN} more" if len(notes) > NOTES_SHOWN else "")
        listed = base.within(blocked[:BLOCKED_SHOWN], BLOCKED_CHARS)
        more = len(blocked) - len(listed)
        return base.preview(f"Draw {len(plan.traces)} traces{laid} and place {len(plan.vias)} vias of the routing plan, checked against "
                            "the board's copper and clearance rules. Run pcb check after. The board is not saved."
                            + (f" The plan's own copper: {shown}." if notes else ""), targets, changes,
                            blocked_by=(listed + ([f"and {more} more reasons; trace check lists them all"] if more else [])) or None)

    def run(self, ctx: Context, progress: Any) -> dict[str, Any]:
        return self.apply(ctx)

    def read_back(self, ctx: Context) -> dict[str, Any]:
        """``plan_drawn`` of each net and layer, matched against the plan's geometry; the vias by count."""
        given, lanes = _routing_plan(ctx)
        path = ctx.document_path()
        targets = list(ctx.request.get("targets") or [])
        fields = list(ctx.request.get("fields") or [])

        def work(app: Any, doc: Any) -> dict[str, Any]:
            copper = _copper(doc)
            # the plan as it was drawn: guards, rings and their vias, worked out again on the board they are on now, would differ
            kept = ctx.store.get(_plan_key(ctx)) if _plan_key(ctx) else None
            drawn = _drawn(doc, _unkept(kept) if kept else _laid(doc, path, given, lanes, copper), copper)
            via_targets = [target for target in targets if "/vias/" in target]
            result = L.read_keys(doc, path, via_targets, [f for f in fields if f != "plan_drawn"]) if via_targets else \
                {"observed": {}, "unreadable": []}
            for (net, layer), covered in drawn.items():
                key = base.key(path, trace_key(net, layer))
                if key in targets:
                    result["observed"][key] = {"plan_drawn": covered}
            return result

        # in the long job's worker the read-back has the job's time: a plan of hundreds of traces is matched one by one
        return L.in_layout(ctx, work, budget_s=L.job_budget(L.READ_BUDGET_S), what="reading the board back")

    def apply(self, ctx: Context) -> dict[str, Any]:
        given, lanes = _routing_plan(ctx)

        def work(app: Any, doc: Any) -> dict[str, Any]:
            copper = _copper(doc)
            plan = _laid(doc, ctx.document_path(), given, lanes, copper)
            blocked, _traces, _vias = _trace_look(ctx, doc, plan, copper)
            if blocked:
                raise base.blocked("; ".join(blocked[:20]))
            if _plan_key(ctx):
                ctx.store.set(_plan_key(ctx), _kept(plan))
                index = [row for row in ctx.store.get(PLANS_INDEX, []) or [] if row.get("key") != _plan_key(ctx)]
                index.append({"doc": os.path.normcase(os.path.abspath(ctx.document_path())), "key": _plan_key(ctx)})
                for row in index[:-PLANS_KEPT]:
                    ctx.store.delete(row["key"])
                ctx.store.set(PLANS_INDEX, index[-PLANS_KEPT:])
            drawn = placed = 0
            points = _connection_points(doc, plan)
            doc.TransactionStart(0)
            try:
                for trace in plan.traces:
                    rows = _joined(trace, points)
                    xs, ys, rs = [r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows]
                    doc.PutTrace(trace.layer, L.find_net(doc, trace.net), trace.width, len(xs), [xs, ys, rs], None, 0, L.UNIT_MM)
                    drawn += 1
                stacks: dict[str, Any] = {}
                for via in plan.vias:
                    stack = stacks.get(via.padstack) or via_padstack(doc, via.padstack)
                    stacks[via.padstack] = stack
                    doc.PutVia(via.at[0], via.at[1], stack, L.find_net(doc, via.net), None, 0, L.UNIT_MM)
                    placed += 1
            except Exception as exc:  # noqa: BLE001 - classified
                if not (drawn or placed):
                    raise L.com_error(exc, "drawing the routing plan", "not_started") from None
                return base.facts("partial", "unchanged", result={"traces": drawn, "vias": placed, **_net_states(doc, plan)},
                                  native_message=f"the routing plan stopped after {drawn} traces and {placed} vias: {exc}")
            finally:
                doc.TransactionEnd(True)
            return base.facts("applied", "unchanged", result={"traces": drawn, "vias": placed, **_net_states(doc, plan),
                                                              **_free_ends(plan)})

        return L.in_layout(ctx, work, budget_s=L.job_budget(L.WRITE_BUDGET_S), write=True, what="drawing the routing plan")


FREE_ENDS_LISTED = 10


def _free_ends(plan: TP.Plan) -> dict[str, Any]:
    """The ends of the plan's guards and rings that touch no copper of their net (DCLI-068)."""
    rows = [{"net": f["net"], "layer": f["layer"], "at_mm": f["end_mm"], "pour_can_reach": "pour_at_mm" in f}
            for f in plan.findings if f["kind"] == "free_end"]
    rows.sort(key=lambda row: row["pour_can_reach"])
    return {"free_ends": rows[:FREE_ENDS_LISTED], "free_end_count": len(rows)} if rows else {}


FINDINGS_LISTED = 5


class TraceCheck(base.Handler):
    """A routing plan worked out against the open board as trace create would draw it, without
    drawing it: what trace create would refuse, the guard and ring lines that would float and the
    ends that join nothing, each with what blocks it, and the requirements of a file as the plan
    would meet them (``plan.requirements``; planes are poured later, so a plane requirement waits
    for pcb check)."""

    command = "trace check"

    def read(self, ctx: Context) -> dict[str, Any]:
        given, lanes = _routing_plan(ctx)
        path = ctx.document_path()
        requirements = None
        if ctx.inputs.get("requirements"):
            try:
                requirements = RQ.load(str(ctx.inputs["requirements"]))
            except PlanError as exc:
                raise AdapterError(exc.code, exc.message, dispatch_state="not_started") from None
        output = ctx.inputs.get("output")

        def work(app: Any, doc: Any) -> dict[str, Any]:
            copper = _copper(doc)
            plan = _laid(doc, path, given, lanes, copper)
            blocked, _traces, _vias = _trace_look(ctx, doc, plan, copper)
            kinds = {kind: [f for f in plan.findings if f["kind"] == kind] for kind in ("floating", "pour_only", "free_end")}
            kinds["free_end"].sort(key=lambda f: "pour_at_mm" in f)  # the ends no pour reaches first
            notes = plan.notes + _plan_notes(doc, path, plan, copper)
            out: dict[str, Any] = {"traces": len(plan.traces), "vias": len(plan.vias), "blocked": blocked[:FINDINGS_LISTED],
                                   "blocked_count": len(blocked), "notes": notes[:FINDINGS_LISTED]}
            whole: dict[str, Any] = {**out, "blocked": blocked, "notes": notes}
            for kind, key in (("floating", "floating"), ("pour_only", "pour_only"), ("free_end", "free_ends")):
                rows = [{k: v for k, v in f.items() if k != "kind"} for f in kinds[kind]]
                out[key], whole[key] = rows[:FINDINGS_LISTED], rows
                out[f"{kind}_count"] = whole[f"{kind}_count"] = len(rows)
            if requirements:
                drawn, _extended_to = _as_drawn(plan, copper)  # measured as drawn, as pcb check measures the board
                model = _planned_model(copper.model, drawn)
                rules = _rules_of(doc, path, copper)
                obstacles = GD.Obstacles(copper, drawn, rules, _route_border(doc) or _outline_polygon(LN.outline_corners(_outline_rows(doc))),
                                         _cutouts(doc))
                measured = RQ.measure(model, requirements, _zone_shapes(_flex_of(doc, copper)), obstacles)
                out["requirements"], whole["requirements"] = RQ.listed(measured), measured
                out["requirements_failed"] = whole["requirements_failed"] = sum(1 for row in measured if row.get("ok") is False)
            if output:  # the plan as worked out, for a person or an Agent to see where each line runs
                whole["plan"] = {"traces": [{"number": i + 1, "net": t.net, "layer": t.layer, "width_mm": t.width, "from": t.origin,
                                             "points_mm": [[round(x, 4), round(y, 4)] + ([round(r, 4)] if r else [])
                                                           for (x, y), r in zip(t.points, t.radii, strict=True)]}
                                            for i, t in enumerate(plan.traces)],
                                 "vias": [{"number": i + 1, "net": v.net, "at_mm": [round(v.at[0], 4), round(v.at[1], 4)],
                                           "padstack": v.padstack, "from": v.origin} for i, v in enumerate(plan.vias)]}
                out["output"] = O.write_output(str(output), whole)
            return base.fitted(out, ["notes", "pour_only", "free_ends", "floating", "blocked", "requirements"])

        return L.in_layout(ctx, work, budget_s=L.job_budget(L.READ_BUDGET_S), what="checking the routing plan")


def _planned_model(model: dict[str, Any], plan: TP.Plan) -> dict[str, Any]:
    """The board model with the plan's traces and vias laid over it, as the requirements measure it."""
    traces = [{"net": t.net, "layer": t.layer, "width": t.width, "path": [[p[0], p[1], 0.0] for p in t.path]} for t in plan.traces]
    vias = [{"net": v.net, "circle": [v.at[0], v.at[1], v.radius], "padstack": v.padstack} for v in plan.vias]
    return {**{k: v for k, v in model.items() if k != "planes"}, "traces": list(model.get("traces") or []) + traces,
            "vias": list(model.get("vias") or []) + vias}


HANDLERS: list[base.Handler] = [TraceCreate(), TraceCheck(), TraceDelete(), ViaDelete(), PlaneDelete(), ViaUpdate(), ViaCreate(),
                                SolderMaskCreate(), SolderMaskDelete()]
