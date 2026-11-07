"""Board edits on the open board: traces drawn from a routing plan, vias placed and changed,
soldermask openings, and traces, vias, plane shapes and openings deleted.

Each write names its objects by net (and layer), previews how many it removes, removes them with
the objects' own ``Delete`` and reads the counts back (``read_keys``: ``/traces/<net>[/<layer>]``,
``/vias/<net>``, ``/planes/<net>/<layer>``). Xpedition's Undo is not reliable for changes made
through automation, so these deletes are how an Agent takes back a routing attempt before a save.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .._driver import AdapterError
from . import base
from . import layout as L
from .base import Context
from .plan import PlanError
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
        counts: dict[tuple[str, int | None], int] = {}
        for net in nets:
            for layer in layers or [None]:
                counts[(net, layer)] = L.trace_count(doc, net, layer)
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


class ViaDelete(L.ReadsBack):
    """Delete the vias of the nets named: routing vias and stitching vias alike."""

    command = "via delete"

    def _look(self, ctx: Context, doc: Any) -> tuple[list[str], dict[str, int], dict[str, str | None]]:
        nets = _nets(ctx)
        _known(doc, nets)
        return nets, {net: L.via_count(doc, net) for net in nets}, {net: L.via_padstack_name(doc, net) for net in nets}

    def preview(self, ctx: Context) -> dict[str, Any]:
        path = ctx.document_path()
        nets, counts, stacks = L.in_layout(ctx, lambda app, doc: self._look(ctx, doc), budget_s=L.READ_BUDGET_S,
                                           what="reading the vias")
        live = {k: v for k, v in counts.items() if v}
        if not live:
            return base.preview(f"The nets {', '.join(nets)} have no vias; nothing to delete.", [], [])
        targets = [base.target(path, "via", via_key(net), label=net) for net in live]
        changes = []
        for net, count in live.items():
            key = base.key(path, via_key(net))
            # the padstack goes with the vias, so a later read-back, such as a save's, expects none
            changes += [base.change(key, "count", count, 0), base.change(key, "padstack", stacks[net], None)]
        return base.preview(f"Delete {sum(live.values())} vias of {len(live)} nets. The board is not saved.", targets, changes)

    def apply(self, ctx: Context) -> dict[str, Any]:
        def work(app: Any, doc: Any) -> dict[str, Any]:
            nets, _counts, _stacks = self._look(ctx, doc)
            wanted = set(nets)
            deleted = 0
            for via in L.collection(doc, "Vias"):
                if L.net_name(via) in wanted:
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


def check_via_padstack(doc: Any, path: str, name: str) -> bool:
    """Whether the board has the via padstack ``name`` already; otherwise the central library must
    hold it as a via, or the call is refused before anything changes."""
    if board_padstack(doc, name) is not None:
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
    return doc.PutPadstack(1, int(doc.LayerCount), name, False, True)


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
    it, else from the central library's row; with None, the reason. A project often names a
    central library on another machine's drive, so a via the board has needs no library."""
    from .layout_output import _via_radius

    scale = L.geometry_scale(doc)
    if scale is not None:
        for via in L.collection(doc, "Vias"):
            if L.name_of(via) == name:
                return _via_radius(via, scale), ""
    try:
        row = _via_row(path, name)
    except AdapterError as exc:
        reason = str(exc.data.get("message") or exc).rstrip(".")
        return None, f"no via of the board uses {name}, and {reason[:1].lower()}{reason[1:]}"
    if row is None or row.get("type") != "via" or not row.get("pad_mm"):
        return None, (f"no via of the board uses {name}, and the central library holds no via padstack {name}; padstack list "
                      "--type via lists its vias, and padstack create adds one")
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


def _stitch_board(doc: Any, net: str) -> ST.Board:
    from .layout_output import board_model, geometry_record

    model = board_model(doc, full=True, silk=False)
    scale = L.geometry_scale(doc)
    if model is None or scale is None or not model["outline"]:
        raise AdapterError("E_UNSUPPORTED", "The board's geometry can be read only on a board in mils with an outline; switch the "
                           "board's unit to mils in Layout, or set the outline with pcb update.", dispatch_state="not_started")
    board = ST.Board(outline=ST.polygon(model["outline"][0].get("path"), model["outline"][0].get("circle")))
    board.pads = [ST.polygon(pad.get("path"), pad.get("circle")) for pad in model["pads"]]
    board.traces = [([(p[0], p[1]) for p in trace["path"]], float(trace.get("width") or 0.0), str(trace.get("net") or ""))
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
    return {"net": net, "x": x, "y": y, "pitch": pitch, "margin": margin, "padstack": str(ctx.inputs.get("padstack") or "").strip()}


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
            result = ST.stitch(net, radius, board, clear, float(inputs["pitch"]), inputs["margin"])
        except PlanError as exc:
            if exc.code == "E_CONFLICT":
                raise base.blocked(exc.message) from None
            raise AdapterError(exc.code, exc.message, dispatch_state="not_started") from None
        points, dropped = result.points, result.dropped
        if not points:
            reasons = ", ".join(f"{count} {why}" for why, count in sorted(dropped.items())) or "no grid point inside the outline"
            blocked.append(f"no point of the {inputs['pitch']:g} mm grid is free ({reasons})")
    return {**inputs, "padstack": padstack, "points": points, "dropped": dropped, "blocked": blocked,
            "before": L.via_count(doc, net), "radius": radius, "clearances": clear}


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
        where = (f"at ({plan['points'][0][0]:g}, {plan['points'][0][1]:g}) mm" if plan["pitch"] is None
                 else f"on a {plan['pitch']:g} mm grid inside its copper on two layers" + (f" ({dropped})" if dropped else ""))
        return base.preview(f"Place {count} {plan['padstack']} via(s) of {net} {where}, clear of other copper by the board's default "
                            "clearance rule. Run pcb check after. The board is not saved.", target,
                            [base.change(base.key(path, via_key(net)), "count", plan["before"], plan["before"] + count)])

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
    nets' class pair, else of one class against (All), else (All) against (All)."""
    from .plan import rules as R

    def rule_for(a: str, b: str) -> str:
        pairs = current.class_clearances
        for key in ((a, b), (b, a), (a, R.ALL_CLASSES), (R.ALL_CLASSES, a), (b, R.ALL_CLASSES), (R.ALL_CLASSES, b)):
            if key in pairs:
                return pairs[key]
        return pairs.get((R.ALL_CLASSES, R.ALL_CLASSES), R.DEFAULT_RULE)

    def clearance(net_a: str, net_b: str, layer: int, kind: str) -> float:
        a = current.net_class_of.get(net_a, R.DEFAULT_CLASS)
        b = current.net_class_of.get(net_b, R.DEFAULT_CLASS)
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
    copper.traces = [(int(t.get("layer") or 1), str(t.get("net") or ""), float(t.get("width") or 0.0), [(q[0], q[1]) for q in t["path"]])
                     for t in model["traces"] if t.get("path")]
    for via in L.collection(doc, "Vias"):
        x0, y0, x1, y1 = L.extrema_mm(via)
        copper.vias.append((L.net_name(via), (x0 + x1) / 2, (y0 + y1) / 2, _via_radius(via, scale)))
    return copper


def _routing_plan(ctx: Context) -> TP.Plan:
    source = str(base.need_input(ctx, "file"))
    try:
        data = json.loads(Path(source).read_text(encoding="utf-8"))
    except OSError as exc:
        raise AdapterError("E_NOT_FOUND", f"The routing plan {source} cannot be read: {exc.strerror}.", dispatch_state="not_started") from None
    except ValueError as exc:
        raise AdapterError("E_VALIDATION", f"The routing plan {source} is not JSON: {exc}.", dispatch_state="not_started") from None
    try:
        return TP.parse(data)
    except PlanError as exc:
        raise AdapterError(exc.code, exc.message, dispatch_state="not_started") from None


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
    traces = {(t.net, t.layer): L.trace_count(doc, t.net, t.layer) for t in plan.traces}
    vias = {v.net: L.via_count(doc, v.net) for v in plan.vias}
    return blocked, traces, vias


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


class TraceCreate(L.ReadsBack):
    """The traces and vias of a routing plan (``plan.trace_plan``), checked offline against the
    board's copper and rules before Layout is touched, then placed with ``PutTrace`` and ``PutVia``
    with Layout's online DRC off, since the check kept the rules. For the routes an Agent plans
    itself, such as a differential pair between two connectors."""

    command = "trace create"
    long_job = True

    def preview(self, ctx: Context) -> dict[str, Any]:
        path = ctx.document_path()
        plan = _routing_plan(ctx)
        blocked, traces, vias = L.in_layout(ctx, lambda app, doc: _trace_look(ctx, doc, plan), budget_s=L.READ_BUDGET_S,
                                            what="checking the routing plan")
        added_traces: dict[tuple[str, int], int] = {}
        for t in plan.traces:
            added_traces[(t.net, t.layer)] = added_traces.get((t.net, t.layer), 0) + 1
        added_vias: dict[str, int] = {}
        for v in plan.vias:
            added_vias[v.net] = added_vias.get(v.net, 0) + 1
        changes = [base.change(base.key(path, trace_key(net, layer)), "count", traces.get((net, layer), 0), traces.get((net, layer), 0) + n)
                   for (net, layer), n in added_traces.items()]
        changes += [base.change(base.key(path, via_key(net)), "count", vias.get(net, 0), vias.get(net, 0) + n) for net, n in added_vias.items()]
        targets = [base.target(path, "trace", "/traces", label="the routing plan's traces and vias")]
        return base.preview(f"Draw {len(plan.traces)} traces and place {len(plan.vias)} vias of the routing plan, checked against the "
                            "board's copper and clearance rules. Run pcb check after. The board is not saved.", targets, changes,
                            blocked_by=blocked or None)

    def run(self, ctx: Context, progress: Any) -> dict[str, Any]:
        return self.apply(ctx)

    def apply(self, ctx: Context) -> dict[str, Any]:
        plan = _routing_plan(ctx)

        def work(app: Any, doc: Any) -> dict[str, Any]:
            blocked, _traces, _vias = _trace_look(ctx, doc, plan)
            if blocked:
                raise base.blocked("; ".join(blocked[:20]))
            drawn = placed = 0
            doc.TransactionStart(0)
            try:
                for trace in plan.traces:
                    xs, ys = [p[0] for p in trace.points], [p[1] for p in trace.points]
                    doc.PutTrace(trace.layer, L.find_net(doc, trace.net), trace.width, len(xs), [xs, ys, [0.0] * len(xs)], None, 0,
                                 L.UNIT_MM)
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
