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
from pathlib import Path
from typing import Any

from .._driver import AdapterError
from . import base
from . import layout as L
from . import layout_output as O
from .base import Context
from .plan import PlanError, board_metrics
from .plan import fanout as FO
from .plan import lanes as LN
from .plan import routing as plan_routing
from .plan import stitching as ST
from .plan import trace_plan as TP
from .plan.shapes import box_polygon, segment_box
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
    """Delete the traces of the nets named, on the layers named or on every layer. Vias stay:
    via delete removes them."""

    command = "trace delete"

    def _look(self, ctx: Context, doc: Any) -> tuple[list[str], list[int], dict[tuple[str, int | None], int]]:
        nets = _nets(ctx)
        _known(doc, nets)
        layers = _layer_list(ctx, int(doc.LayerCount))
        board = L.trace_counts(doc)
        counts = {(net, layer): board.get((net, layer), 0) for net in nets for layer in layers or [None]}
        return nets, layers, counts

    def preview(self, ctx: Context) -> dict[str, Any]:
        path = ctx.document_path()
        nets, layers, counts = L.in_layout(ctx, lambda app, doc: self._look(ctx, doc), budget_s=L.READ_BUDGET_S,
                                           what="reading the traces")
        live = {k: v for k, v in counts.items() if v}
        on = f" on layers {', '.join(map(str, layers))}" if layers else ""
        if not live:
            return base.preview(f"The nets {', '.join(nets)} have no traces{on}; nothing to delete.", [], [])
        targets = [base.target(path, "trace", trace_key(net, layer), label=f"{net}" + (f" on layer {layer}" if layer else ""))
                   for net, layer in live]
        changes = [base.change(base.key(path, trace_key(net, layer)), "count", count, 0) for (net, layer), count in live.items()]
        return base.preview(f"Delete {sum(live.values())} traces of {len(nets)} nets{on}; their vias stay (via delete removes them). "
                            "The board is not saved.", targets, changes)

    def apply(self, ctx: Context) -> dict[str, Any]:
        def work(app: Any, doc: Any) -> dict[str, Any]:
            nets, layers, _counts = self._look(ctx, doc)
            wanted = set(nets)
            deleted = 0
            for trace in L.collection(doc, "Traces"):
                if L.net_name(trace) in wanted and (not layers or int(trace.Layer) in layers):
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


def via_pad_radius(doc: Any, path: str, name: str) -> tuple[float | None, str]:
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
    return board


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
        why = why or ST.free(point, net, radius, board, clear)
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


def _rect(ctx: Context) -> tuple[float, float, float, float]:
    values = [ctx.inputs.get(name) for name in ("x1_mm", "y1_mm", "x2_mm", "y2_mm")]
    if any(v is None for v in values):
        raise AdapterError("E_VALIDATION", "solder-mask create needs --x1, --y1, --x2 and --y2: two opposite corners.",
                           dispatch_state="not_started")
    x1, y1, x2, y2 = (round(float(v), 4) for v in values)
    box = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    if box[2] - box[0] <= 0 or box[3] - box[1] <= 0:
        raise AdapterError("E_VALIDATION", "The opening needs an area: the corners differ in x and in y.", dispatch_state="not_started")
    return box


def mask_key(kind: str, side: str, box: tuple[float, float, float, float]) -> str:
    return f"/{kind}/{side}/" + ",".join(f"{v:g}" for v in box)


def _openings(doc: Any, fab_type: int, side_code: int) -> list[tuple[Any, tuple[float, float, float, float]]]:
    out = []
    for gfx in L.collection(doc, "FabricationLayerGfxs"):
        try:
            if int(gfx.Type) != fab_type or int(gfx.Side) != side_code or gfx.Component is not None:
                continue
            out.append((gfx, tuple(round(v, 4) for v in L.extrema_mm(gfx))))
        except Exception:  # noqa: BLE001 - a graphic that cannot be read is left alone
            continue
    return out


def opening_exists(doc: Any, kind: str, side: str, box: tuple[float, ...]) -> bool:
    fab_type = FAB_COVER_LAYER if kind == "cover-layer" else FAB_SOLDERMASK
    side_code = SIDE_TOP if side == "top" else SIDE_BOTTOM
    return any(all(abs(a - b) < 0.002 for a, b in zip(found, box, strict=True)) for _g, found in _openings(doc, fab_type, side_code))


def _copper_nets(doc: Any, side: str, box: tuple[float, float, float, float]) -> list[str]:
    """The nets with copper inside the box on the outer layer of that side: pads, traces and planes."""
    from .layout_output import board_model, geometry_record

    layer = 1 if side == "top" else int(doc.LayerCount)
    model = board_model(doc, full=True, silk=False)
    nets: set[str] = set()
    if model is not None:
        for pad in model["pads"]:
            if int(pad.get("layer") or 0) == layer and box_polygon(box, ST.polygon(pad.get("path"), pad.get("circle"))):
                nets.add(str(pad.get("net") or "") or "(no net)")
        for trace in model["traces"]:
            points = [(p[0], p[1]) for p in trace.get("path") or []]
            if int(trace.get("layer") or 0) == layer and any(segment_box(points[i], points[i + 1], box) == 0.0
                                                              for i in range(len(points) - 1)):
                nets.add(str(trace.get("net") or ""))
    scale = L.geometry_scale(doc)
    for shape in L.collection(doc, "PlaneShapes"):
        if int(shape.Layer) == layer and scale is not None:
            record = geometry_record(shape.Geometry, scale)
            if record and box_polygon(box, ST.polygon(record.get("path"), record.get("circle"))):
                nets.add(L.net_name(shape))
    return sorted(n for n in nets if n)


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
    """A rectangular opening in the soldermask, or in the coverlay of a flex board (--cover-layer),
    on one side: ``PutFabricationLayerGfx``, filled. Copper under it is left bare."""

    command = "solder-mask create"

    def preview(self, ctx: Context) -> dict[str, Any]:
        side, _code = _side(ctx)
        kind, _fab = _mask_kind(ctx)
        box = _rect(ctx)
        path = ctx.document_path()

        def look(app: Any, doc: Any) -> tuple[bool, list[str], bool]:
            cover = kind != "cover-layer" or side in cover_layer_sides(doc)
            return opening_exists(doc, kind, side, box), _copper_nets(doc, side, box), cover

        exists, nets, cover = L.in_layout(ctx, look, budget_s=L.READ_BUDGET_S, what="reading the copper under the opening")
        domain = mask_key(kind, side, box)
        target = [base.target(path, "opening", domain, label=f"{kind} opening on the {side}")]
        where = f"{box[0]:g},{box[1]:g} to {box[2]:g},{box[3]:g} mm"
        if exists:
            return base.preview(f"The {side} {kind} has an opening at {where} already; nothing changes.", target, [])
        blocked = [] if nets else [f"no copper lies inside {where} on the {side} side, so the opening would bare nothing; pour or "
                                   "route the copper first"]
        if not cover:
            blocked.append(f"the board's stackup has no cover layer on the {side} side (pcb get lists the stackup); open the "
                           "soldermask instead, without --cover-layer")
        layer = "coverlay" if kind == "cover-layer" else "soldermask"
        return base.preview(f"Open the {side} {layer} over {where}, baring the copper of {', '.join(nets) or 'no net'} there. The board "
                            "is not saved.", target, [base.change(base.key(path, domain), "exists", False, True)],
                            blocked_by=blocked or None)

    def apply(self, ctx: Context) -> dict[str, Any]:
        side, code = _side(ctx)
        kind, fab = _mask_kind(ctx)
        box = _rect(ctx)

        def work(app: Any, doc: Any) -> dict[str, Any]:
            if opening_exists(doc, kind, side, box):
                return base.facts("not_applied", "unchanged", result={"kind": kind, "side": side, "created": False})
            x1, y1, x2, y2 = box
            points = [[x1, x2, x2, x1, x1], [y1, y1, y2, y2, y1], [0.0] * 5]
            doc.TransactionStart(0)
            try:
                doc.PutFabricationLayerGfx(fab, code, 0.0, 5, points, True, None, L.UNIT_MM)
            except Exception as exc:  # noqa: BLE001 - classified
                raise L.com_error(exc, "drawing the opening", com.call_state(exc)) from None
            finally:
                doc.TransactionEnd(True)
            return base.facts("applied", "unchanged", result={"kind": kind, "side": side, "created": True,
                                                              "rect_mm": list(box), "nets": _copper_nets(doc, side, box)})

        return L.in_layout(ctx, work, budget_s=L.WRITE_BUDGET_S, write=True, what="drawing the opening")


class SolderMaskDelete(L.ReadsBack):
    """Delete the soldermask (or coverlay) opening that contains a point on one side."""

    command = "solder-mask delete"

    def _found(self, ctx: Context, doc: Any) -> tuple[str, str, Any, tuple[float, float, float, float]]:
        side, code = _side(ctx)
        kind, fab = _mask_kind(ctx)
        x, y = base.need_input(ctx, "x_mm"), base.need_input(ctx, "y_mm")
        hits = [(g, box) for g, box in _openings(doc, fab, code) if box[0] <= x <= box[2] and box[1] <= y <= box[3]]
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
    return TP.Rules(net_class_of=current.net_class_of, widths=widths, clearance=_clearance_lookup(current))


def _copper(doc: Any) -> TP.Copper:
    from .layout_output import _via_radius, board_model

    model = board_model(doc, full=True, silk=False)
    scale = L.geometry_scale(doc)
    if model is None or scale is None:
        raise AdapterError("E_UNSUPPORTED", "The board's geometry can be read only on a board in mils with an outline; switch the "
                           "board's unit to mils in Layout, or set the outline with pcb update.", dispatch_state="not_started")
    copper = TP.Copper(layer_count=int(doc.LayerCount), nets={L.name_of(net) for net in L.collection(doc, "Nets")})
    copper.pads = [(int(p.get("layer") or 1), str(p.get("net") or ""), ST.polygon(p.get("path"), p.get("circle"))) for p in model["pads"]]
    copper.traces = [(int(t.get("layer") or 1), str(t.get("net") or ""), float(t.get("width") or 0.0), _centre_line(t["path"]))
                     for t in model["traces"] if t.get("path")]
    for via in L.collection(doc, "Vias"):
        x0, y0, x1, y1 = L.extrema_mm(via)
        copper.vias.append((L.net_name(via), (x0 + x1) / 2, (y0 + y1) / 2, _via_radius(via, scale)))
    return copper


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
        plan = TP.parse(data, lanes=bool(lanes or fanouts))
        plan.fanouts = fanouts
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


def _fanned(doc: Any, path: str, plan: TP.Plan) -> tuple[TP.Plan, dict[str, FO.Fanout]]:
    """The plan with each fan-out's stubs, joins and vias, and the fan-outs by connector."""
    from . import layout_parts
    from .plan.shapes import flatten

    if not plan.fanouts:
        return plan, {}
    body = FO.centroid(flatten(_outline_rows(doc)))
    clear = _clearances(path)
    traces, vias, notes = list(plan.traces), list(plan.vias), list(plan.notes)
    fanouts: dict[str, FO.Fanout] = {}
    for spec in plan.fanouts:
        component = layout_parts.find(doc, spec["refdes"])
        radius, why = via_pad_radius(doc, path, spec["padstack"])
        if radius is None:
            raise AdapterError("E_VALIDATION", f"The routing plan is not valid: the size of the via {spec['padstack']} is not known: {why}.",
                               dispatch_state="not_started")
        try:
            fanout = FO.plan(_fanout_pins(component), body, radius, clear.via_to_pad, clear.via_to_via, spec["layer"],
                             set(spec["plane_nets"]), set(spec["skip"]), spec["stub_width"], set(spec["give_way"]))
        except PlanError as exc:
            raise AdapterError(exc.code, f"The routing plan is not valid: the fan-out of {spec['refdes']}: {exc.message}.",
                               dispatch_state="not_started") from None
        fanouts[spec["refdes"]] = fanout
        traces += [TP.Trace(net, layer, width, list(points)) for net, layer, width, points in fanout.traces]
        vias += [TP.Via(net, at, spec["padstack"]) for net, at in fanout.vias]
        if fanout.loose:
            notes.append(f"{spec['refdes']} leaves pins {', '.join(fanout.loose)} to connect: no via between the rows is free for them")
    return TP.Plan(traces=traces, vias=vias, notes=notes), fanouts


def _laid(doc: Any, path: str, plan: TP.Plan, lanes: list[LN.Lane]) -> TP.Plan:
    """The plan with each fan-out, and each lane as a trace along the board outline."""
    plan, fanouts = _fanned(doc, path, plan)
    if not lanes:
        return plan
    ring = LN.outline_corners(_outline_rows(doc))
    clearance = _clearances(path).via_to_pad if fanouts else 0.1
    try:
        laid = [LN.expand(copy.copy(lane), ring, f"lanes[{index}]", fanouts, clearance) for index, lane in enumerate(lanes)]
    except PlanError as exc:
        raise AdapterError(exc.code, exc.message, dispatch_state="not_started") from None
    if len(plan.traces) + len(plan.vias) + len(laid) > TP.MAX_ITEMS:
        raise AdapterError("E_VALIDATION", f"The routing plan is not valid: it names more than {TP.MAX_ITEMS} items.",
                           dispatch_state="not_started")
    return TP.Plan(traces=plan.traces + laid, vias=plan.vias, notes=plan.notes)


def _plan_notes(doc: Any, plan: TP.Plan) -> list[str]:
    """What the plan's own copper shows before it is drawn: each pair's two lengths, the pairs
    recognised by name, and what the board's bend zones forbid of it."""
    lengths: dict[str, float] = {}
    for trace in plan.traces:
        lengths[trace.net] = lengths.get(trace.net, 0.0) + TP.length(trace)
    notes = [f"{a} and {b} are {lengths[a]:.3f} and {lengths[b]:.3f} mm long, {abs(lengths[a] - lengths[b]):.3f} mm apart"
             for a, b in board_metrics._pairs(sorted(lengths))]
    scale = L.geometry_scale(doc)
    model = {"planes": O._quietly(lambda: O.plane_shapes(doc), []), "keepouts": O._quietly(lambda: O.keepouts(doc, scale), []),
             "outline": [{"path": O._quietly(lambda: _outline_rows(doc), [])}],
             "traces": [{"net": t.net, "layer": t.layer, "width": t.width, "path": TP.drawn(t.points, t.radii)} for t in plan.traces],
             "vias": [{"net": v.net, "circle": [v.at[0], v.at[1], 0.0]} for v in plan.vias]}
    flex = [row for row in board_metrics.flex_rules(model) if row["rule"] != "layer_change_outside_stiffener" or
            any(v.net == row.get("net") for v in plan.vias)]
    notes += [f"{row['net']}: {row['finding']}" for row in flex]
    return notes


def _trace_look(ctx: Context, doc: Any, plan: TP.Plan) -> tuple[list[str], dict[tuple[str, int], int], dict[str, int]]:
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
    blocked += TP.check(plan, _copper(doc), _trace_rules(doc, path))
    via_rows = L.via_summary(doc)
    traces = _drawn(doc, plan)
    vias = {v.net: via_rows.get(v.net, {}).get("count", 0) for v in plan.vias}
    return blocked, traces, vias


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


def _joined(trace: TP.Trace, points: dict[tuple[str, int], list[tuple[tuple[float, float], tuple[float, ...]]]]) -> list[tuple[float, float, float]]:
    """The (x, y, r) rows Layout draws for the trace: an end that lies on a pin's pad or on a via
    extended to the pin's origin or the via's centre, and each rounded corner as its arc."""
    found = points.get((trace.net, trace.layer), [])
    pts = [(float(p[0]), float(p[1])) for p in trace.points]
    radii = list(trace.radii)

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
    if last is not None and last != pts[-1]:
        if math.dist(last, pts[-1]) < SNAP_MM:
            pts[-1] = last
        else:
            pts.append(last)
            radii.append(0.0)
    return TP.drawn(pts, radii)


COVER_TOLERANCE_MM = 0.01


def _drawn(doc: Any, plan: TP.Plan) -> dict[tuple[str, int], bool]:
    """For each net and layer of the plan, whether every point along its planned traces lies on a
    trace of that net and layer on the board. Layout keeps one planned trace as several objects, split
    at its arcs and elsewhere, so the plan is matched by its geometry, not by a count of objects."""
    from .layout_output import geometry_record
    from .plan.shapes import flatten

    scale = L.geometry_scale(doc)
    wanted = {(t.net, t.layer) for t in plan.traces}
    board: dict[tuple[str, int], list[tuple[list[tuple[float, float]], float]]] = {key: [] for key in wanted}
    for trace in L.collection(doc, "Traces"):
        key = (L.net_name(trace), int(trace.Layer))
        if key not in wanted or scale is None:
            continue
        try:
            geometry = trace.Geometry
            record = geometry_record(geometry, scale)
            width = float(geometry.LineWidth or 0.0) * scale
        except Exception:  # noqa: BLE001 - a trace Layout cannot describe covers nothing
            continue
        if record and record.get("path"):
            board[key].append((flatten([tuple(p) for p in record["path"]]), width))
    out: dict[tuple[str, int], bool] = {}
    for key in wanted:
        segments = [(a, b, width) for points, width in board[key] for a, b in zip(points, points[1:], strict=False)]
        out[key] = all(any(TP.point_segment(point, a, b) <= COVER_TOLERANCE_MM + width / 2 for a, b, width in segments)
                       for trace in plan.traces if (trace.net, trace.layer) == key for point in trace.path)
    return out


def _net_states(doc: Any, plan: TP.Plan) -> list[dict[str, Any]]:
    """Whether each net of the plan is routed now, and its open connections."""
    rows = []
    for name in sorted({t.net for t in plan.traces} | {v.net for v in plan.vias}):
        try:
            net = L.find_net(doc, name)
            opens = int(net.NumberOfOpens or 0)
            rows.append({"net": name, "routed": bool(com.member(net, "IsRouted")) and opens == 0, "opens": opens})
        except Exception:  # noqa: BLE001 - a net Layout cannot report is left out
            continue
    return rows


NOTES_SHOWN = 8  # the preview names this many notes on the plan's own copper, and counts the rest
BLOCKED_SHOWN = 20  # and this many reasons the plan is refused, so that the preview fits one response


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
            plan = _laid(doc, path, given, lanes)
            return plan, *_trace_look(ctx, doc, plan), plan.notes + _plan_notes(doc, plan)

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
        return base.preview(f"Draw {len(plan.traces)} traces{laid} and place {len(plan.vias)} vias of the routing plan, checked against "
                            "the board's copper and clearance rules. Run pcb check after. The board is not saved."
                            + (f" The plan's own copper: {shown}." if notes else ""), targets, changes,
                            blocked_by=(blocked[:BLOCKED_SHOWN] + ([f"and {len(blocked) - BLOCKED_SHOWN} more reasons of the same kinds"]
                                                                   if len(blocked) > BLOCKED_SHOWN else [])) or None)

    def run(self, ctx: Context, progress: Any) -> dict[str, Any]:
        return self.apply(ctx)

    def read_back(self, ctx: Context) -> dict[str, Any]:
        """``plan_drawn`` of each net and layer, matched against the plan's geometry; the vias by count."""
        given, lanes = _routing_plan(ctx)
        path = ctx.document_path()
        targets = list(ctx.request.get("targets") or [])
        fields = list(ctx.request.get("fields") or [])

        def work(app: Any, doc: Any) -> dict[str, Any]:
            drawn = _drawn(doc, _laid(doc, path, given, lanes))
            via_targets = [target for target in targets if "/vias/" in target]
            result = L.read_keys(doc, path, via_targets, [f for f in fields if f != "plan_drawn"]) if via_targets else \
                {"observed": {}, "unreadable": []}
            for (net, layer), covered in drawn.items():
                key = base.key(path, trace_key(net, layer))
                if key in targets:
                    result["observed"][key] = {"plan_drawn": covered}
            return result

        return L.in_layout(ctx, work, budget_s=L.READ_BUDGET_S, what="reading the board back")

    def apply(self, ctx: Context) -> dict[str, Any]:
        given, lanes = _routing_plan(ctx)

        def work(app: Any, doc: Any) -> dict[str, Any]:
            plan = _laid(doc, ctx.document_path(), given, lanes)
            blocked, _traces, _vias = _trace_look(ctx, doc, plan)
            if blocked:
                raise base.blocked("; ".join(blocked[:20]))
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
                return base.facts("partial", "unchanged", result={"traces": drawn, "vias": placed, "nets": _net_states(doc, plan)},
                                  native_message=f"the routing plan stopped after {drawn} traces and {placed} vias: {exc}")
            finally:
                doc.TransactionEnd(True)
            return base.facts("applied", "unchanged", result={"traces": drawn, "vias": placed, "nets": _net_states(doc, plan)})

        return L.in_layout(ctx, work, budget_s=L.job_budget(L.WRITE_BUDGET_S), write=True, what="drawing the routing plan")


HANDLERS: list[base.Handler] = [TraceCreate(), TraceDelete(), ViaDelete(), PlaneDelete(), ViaUpdate(), ViaCreate(), SolderMaskCreate(),
                                SolderMaskDelete()]
