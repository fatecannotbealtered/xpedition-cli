"""Supported Layout local-library refresh; no component deletion or database edits.

The 2604 command catalog and visible Integration/Library Tools control identify
ECO_UPDATELOCALLIBRARYDATA. This native command refreshes local definitions AND
their instances. ResetCellEx alone only consumes an already-local definition.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import library_read as R

COMMAND = "ECO_UPDATELOCALLIBRARYDATA"
TOLERANCE_MM = 0.001


def _services_windows(pid):
    import win32gui
    import win32process

    found = []
    win32gui.EnumWindows(
        lambda hwnd, _: (
            found.append(hwnd)
            if win32gui.IsWindowVisible(hwnd)
            and win32process.GetWindowThreadProcessId(hwnd)[1] == pid
            and win32gui.GetWindowText(hwnd) in ("Library Services", "库服务")
            else None
        ),
        None,
    )
    return found


def _services_paths(hwnd):
    import ctypes
    from ctypes import wintypes

    import win32con
    import win32gui

    api = ctypes.windll.user32
    api.SendMessageTimeoutW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    api.SendMessageTimeoutW.restype = wintypes.LPARAM
    paths = []
    deadline = time.monotonic() + 6

    def send(control, message, index=0, pointer=0):
        if time.monotonic() >= deadline:
            raise TimeoutError("Library Services control-read deadline")
        result = ctypes.c_size_t()
        if not api.SendMessageTimeoutW(
            control, message, index, pointer, 2, 500, ctypes.byref(result)
        ):
            raise TimeoutError("Library Services control did not respond")
        return result.value

    # Native Win32 controls: importing UI Automation into an active COM worker
    # changes its threading setup. Only the currently selected path is needed.
    def read(control, _):
        if win32gui.GetClassName(control) == "ComboBox":
            index = send(control, win32con.CB_GETCURSEL)
            if index >= 0x7FFFFFFF:
                return
            length = send(control, win32con.CB_GETLBTEXTLEN, index)
            if length >= 32768:
                raise ValueError("Library Services path length is unavailable")
            buffer = ctypes.create_unicode_buffer(length + 1)
            send(control, win32con.CB_GETLBTEXT, index, ctypes.addressof(buffer))
            text = buffer.value
            if text.lower().endswith(".lmc"):
                paths.append(text)

    win32gui.EnumChildWindows(hwnd, read, None)
    return paths


def _services_close(hwnd):
    import win32con
    import win32gui

    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)


def prepare_library_services(app, plan, a):
    """Normal lifecycle of a NEW, path-verified Library Services window only.

    Enterprise 2604 can retain its temporary PartsDB reservation immediately after
    opening a board. Its visible Library Services open/close workflow releases it.
    Never close a pre-existing editor, press Apply, or force-release a reservation.
    """
    from . import win_dialogs
    from .native_identity import application_identity

    pid = application_identity(app, "pcb")["pid"]
    expected = Path(plan["definitions"]["central_library"]).resolve()
    evidence = {"pid": pid, "library": str(expected), "status": "not_started"}

    def refuse(message):
        raise a.AdapterError(
            "E_CONFLICT", message, {"library_services": evidence, "write_attempted": False}
        )

    if win_dialogs.desktop_locked():
        refuse("Library Services requires an interactive desktop")
    if _services_windows(pid) or win_dialogs.find_dialogs(pid=pid):
        refuse("Close existing owned Library Services/editor dialogs normally first")
    if not app.Gui.ProcessCommand("SETUP_LIBRARYSERVICES", False):
        refuse("Layout refused the visible Library Services command")
    evidence["status"] = "open_requested"
    deadline = time.monotonic() + 6
    windows = _services_windows(pid)
    while not windows and time.monotonic() < deadline:
        time.sleep(0.1)
        windows = _services_windows(pid)
    if len(windows) != 1:
        refuse("The newly opened Library Services window could not be identified")
    hwnd = windows[0]
    paths = _services_paths(hwnd)
    evidence["observed_libraries"] = paths
    if not paths or any(Path(path).resolve() != expected for path in paths):
        refuse("Library Services identity differs; its window was left untouched")
    # Recheck ownership immediately before sending the normal close request.
    if _services_windows(pid) != [hwnd]:
        refuse("Library Services window identity changed before normal closure")
    _services_close(hwnd)
    evidence["status"] = "close_requested"
    deadline = time.monotonic() + 5
    while hwnd in _services_windows(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    if hwnd in _services_windows(pid) or win_dialogs.find_dialogs(pid=pid):
        refuse("Library Services did not close normally; no refresh was dispatched")
    evidence["status"] = "normally_closed"
    evidence["forced_reservation_release"] = False
    return evidence


def after_services(params, plan, client, a):
    # Hosted editor closure can invalidate the old ActiveDocument wrapper.
    # Attach afresh once; this identity read never opens a board or retries a write.
    _, _, app, doc = active(params, client, a)
    lmc = Path(plan["definitions"]["central_library"]).resolve()
    if (
        a._central_library(Path(plan["project"]))[0].resolve() != lmc
        or capture(doc, a) != plan["before"]
        or any(
            _file(Path(source["path"])) != source
            for source in plan["definitions"]["sources"]
            if Path(source["path"]).resolve() != lmc
        )
    ):
        raise a.AdapterError(
            "E_CONFLICT",
            "Bound geometry or definition input changed during Library Services preparation",
            {"write_attempted": False},
        )
    # Normal native closure can save the LMC container. Its byte change is an
    # explicit planned effect, not permission for definition/PRJ/geometry drift.
    # All cell/padstack database bytes and the full library path remain bound.
    return app, doc


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _file(path):
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _canonical(value):
    if isinstance(value, float):
        return round(value, 8)
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        items = [_canonical(v) for v in value]
        return (
            sorted(items, key=lambda v: json.dumps(v, sort_keys=True))
            if all(isinstance(v, dict) for v in items)
            else items
        )
    return value


def _bounds(g):
    if g.IsCircle():
        x, y, radius = float(g.CircleX), float(g.CircleY), float(g.CircleR)
        box = [x - radius, y - radius, x + radius, y + radius]
        shape = "ROUND"
    else:
        points = g.PointsArray
        box = [min(points[0]), min(points[1]), max(points[0]), max(points[1])]
        shape = "polygon"
    return {
        "shape": shape,
        "bounds_mm": [float(x) * 0.0254 for x in box],
        "points_mil": g.PointsArray,
        "circle_mm": [v * 0.0254 for v in (x, y, radius)] if shape == "ROUND" else None,
    }


def physical_model(model):
    """The exact physical model, excluding only the named drawing-analysis sidecar.

    layout_observation holds advisory collection counts, envelope provenance and
    observation indices. Its trace inventory necessarily changes during authorized
    routing/removal. Raw trace paths/widths and every other physical field remain
    protected exactly; strict native reader failures still stop capture.
    """
    return {key: value for key, value in model.items() if key != "layout_observation"}


def strict_physical_model(model, a):
    """Promote proven trace UID bindings before discarding advisory sidecars."""
    import copy

    from .layout_reminders import trace_geometry_binding

    physical = copy.deepcopy(physical_model(model))
    traces = physical.get("traces")
    if not isinstance(traces, list):
        raise a.AdapterError("E_UNSUPPORTED", "Complete native trace material is unavailable")
    observation = model.get("layout_observation")
    rows = observation.get("trace_envelopes") if isinstance(observation, dict) else None
    if not isinstance(rows, list) or len(rows) != len(traces):
        raise a.AdapterError(
            "E_UNSUPPORTED", "Complete native trace identity bindings are unavailable"
        )
    indexes, ids = set(), set()
    for row in rows:
        if not isinstance(row, dict):
            raise a.AdapterError("E_UNSUPPORTED", "Native trace identity binding is malformed")
        index, uid = row.get("model_index"), row.get("uid")
        if (
            type(index) is not int
            or not 0 <= index < len(traces)
            or index in indexes
            or type(uid) not in (str, int)
            or not str(uid).strip()
            or str(uid) in ids
            or row.get("geometry_sha256") != trace_geometry_binding(traces[index])
        ):
            raise a.AdapterError(
                "E_UNSUPPORTED", "Native trace identity/material binding is ambiguous"
            )
        indexes.add(index)
        ids.add(str(uid))
        traces[index]["uid"] = str(uid)
    return physical


def capture(doc, a, *, strict=False, _model_stage=None):
    """Strict pin getters; never instantiate hosted CellEditor/LibraryServices."""

    def collection(owner, name):
        if not strict:
            return getattr(owner, name)
        value = getattr(owner, name)
        if callable(value) and not hasattr(value, "Count"):
            value = value()
        return a._items(value, strict=True)

    components = []
    for comp in collection(doc, "Components") if strict else a._com_member(doc, "Components"):
        row = {} if strict else a._component_record(comp)
        pins = list(collection(comp, "Pins"))
        if strict:
            side = int(comp.Side)
            if side not in {1, 512}:
                raise a.AdapterError("E_UNSUPPORTED", "Component side cannot be observed")
            if type(comp.Placed) is not bool:
                raise a.AdapterError(
                    "E_UNSUPPORTED", "Component placement state cannot be observed"
                )
            protection = {}
            protection_raw_types = {}
            for public_name, field in (("Anchor", "anchor"), ("FixLock", "fix_lock")):
                value = getattr(comp, public_name)
                if not isinstance(value, (int, bool)) or not 0 <= int(value) <= 0xFFFFFFFF:
                    raise a.AdapterError(
                        "E_UNSUPPORTED", "Component protection flags cannot be observed"
                    )
                protection[field] = int(value)
                protection_raw_types[field] = type(value).__name__
            row.update(
                uid=comp.UniqueId,
                refdes=str(comp.RefDes),
                part_number=str(comp.PartNumber),
                footprint=str(comp.Cell.Name),
                placed=bool(comp.Placed),
                x=float(comp.GetPositionX(4)),
                y=float(comp.GetPositionY(4)),
                rotation=float(comp.Orientation),
                side="top" if side == 1 else "bottom",
                unit="mm",
                protection_raw_types=protection_raw_types,
                **protection,
            )
            if row["uid"] is None or not row["refdes"]:
                raise a.AdapterError("E_NATIVE_FAILURE", "Component identity was not returned")
            row["pins"] = []
            for pin in pins:
                net = pin.Net
                pin_uid = pin.UniqueId
                net_uid = net.UniqueId if net is not None else None
                if (
                    type(pin_uid) not in (str, int)
                    or not str(pin_uid).strip()
                    or (
                        net is not None
                        and (type(net_uid) not in (str, int) or not str(net_uid).strip())
                    )
                ):
                    raise a.AdapterError("E_UNSUPPORTED", "Pin/net identity cannot be observed")
                row["pins"].append(
                    {
                        "uid": str(pin_uid),
                        "net_uid": str(net_uid) if net is not None else None,
                        "number": str(pin.Name),
                        "net": str(net.Name) if net is not None else None,
                        "x": float(pin.GetPositionX(4)),
                        "y": float(pin.GetPositionY(4)),
                    }
                )
        # Resolve source partitions through read-only converter exports. Do not
        # initialize hosted library consumers merely to inspect placed geometry.
        row["partition"] = None
        row["placement_outlines"] = [
            {
                "layer": int(outline.Layer),
                "geometry": a._geometry_record(outline.Geometry),
                "height_mm": float(outline.Height) * 0.0254,
            }
            for outline in collection(comp, "PlacementOutlines")
        ]
        row["insertion_outlines"] = [
            {"layer": int(outline.Layer), "geometry": a._geometry_record(outline.Geometry)}
            for outline in collection(comp, "InsertionOutlines")
        ]
        if any(
            not outline["geometry"]
            for key in ("placement_outlines", "insertion_outlines")
            for outline in row[key]
        ):
            raise a.AdapterError("E_UNSUPPORTED", "An instance outline could not be observed")
        row["geometry"] = []
        for pin_index, pin in enumerate(pins):
            # A complete stage observes each header once; subsequent consumers
            # use those exact values rather than repeat COM scalar getters.
            header = row["pins"][pin_index] if strict else None
            row["geometry"].append(
                {
                    **({"uid": header["uid"], "net_uid": header["net_uid"]} if strict else {}),
                    "number": header["number"] if strict else str(pin.Name),
                    "net": header["net"]
                    if strict
                    else (str(pin.Net.Name) if pin.Net is not None else None),
                    "center_mm": [header["x"], header["y"]]
                    if strict
                    else ([pin.GetPositionX(4), pin.GetPositionY(4)]),
                    "copper": [
                        {
                            "layer": int(p.Layer),
                            "geometry": [_bounds(g) for g in collection(p, "Geometries")],
                        }
                        for p in collection(pin, "Pads")
                    ],
                    "fabrication": [
                        {
                            "name": str(p.Name),
                            "type": int(p.Type),
                            "side": int(p.Side),
                            "layer": int(p.Layer),
                            "geometry": [_bounds(g) for g in collection(p, "Geometries")],
                        }
                        for p in collection(pin, "FabricationPads")
                    ],
                    "holes": [
                        {
                            "center_mm": [float(h.PositionX) * 0.0254, float(h.PositionY) * 0.0254],
                            "diameter_mm": h.GetDrillSize(4),
                            "plated": bool(h.Plated),
                        }
                        for h in collection(pin, "Holes")
                    ],
                }
            )
        components.append(row)
    if _model_stage is not None:
        if not strict:
            raise ValueError("Shared native model material requires strict complete capture")
        from .native_observation_stage import material

        raw_model = material(_model_stage, doc, a, kind="raw_board_model")
    else:
        raw_model = a._board_model(doc, strict=True) if strict else a._board_model(doc)
    return _canonical(
        {
            "components": components,
            "model": strict_physical_model(raw_model, a) if strict else physical_model(raw_model),
            "stackup": a._board_stackup(doc),
        }
    )


@contextmanager
def _read_only_application(enabled):
    if not enabled:
        yield
        return
    previous = os.environ.get("XPEDITION_OBSERVE_ONLY")
    os.environ["XPEDITION_OBSERVE_ONLY"] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("XPEDITION_OBSERVE_ONLY", None)
        else:
            os.environ["XPEDITION_OBSERVE_ONLY"] = previous


def _readonly_identity(params, app, board, a):
    from .native_identity import application_identity

    observed = application_identity(app, "pcb")
    if observed.get("pid") != params["pid"]:
        raise a.AdapterError(
            "E_CONFLICT",
            "Read-only material observer belongs to another process",
            {"write_attempted": False},
        )
    doc = a._licensed_document(app)
    if Path(str(doc.FullName)).resolve() != board.resolve():
        raise a.AdapterError(
            "E_CONFLICT",
            "Read-only material observer requires the same already-open board",
            {"write_attempted": False},
        )
    return doc


def active(params, client, a, *, require_project=True, quiet=True):
    project = Path(str(params["project"])).resolve()
    if require_project and project.suffix.lower() != ".prj":
        raise a.AdapterError(
            "E_VALIDATION", "Cell refresh requires a project .prj", {"write_attempted": False}
        )
    board = a._layout_board_path(params)
    read_only = params.get("observe_only") is True
    if read_only and (
        params.get("start") is not False or type(params.get("pid")) is not int or params["pid"] <= 0
    ):
        raise a.AdapterError(
            "E_VALIDATION",
            "Read-only material observation requires start=false and one positive bound PID",
            {"write_attempted": False},
        )
    with _read_only_application(read_only):
        app = a._application(
            client, attach_only=True, **({"quiet": False} if read_only or not quiet else {})
        )
    if read_only:
        return project, board, app, _readonly_identity(params, app, board, a)
    doc = a._licensed_document(app)
    if Path(str(doc.FullName)).resolve() != board.resolve():
        raise a.AdapterError(
            "E_CONFLICT", "The requested board must already be open", {"write_attempted": False}
        )
    return project, board, app, doc


def observe(params, client, a):
    _, board, app, doc = active(
        params,
        client,
        a,
        require_project=False,
        quiet=False,
    )

    def finish(result):
        if params.get("observe_only") is True:
            _readonly_identity(params, app, board, a)
        return result

    if params.get("label_objects") is True:
        from . import board_labels

        try:
            state = board_labels.observe(doc, board, a)
        except Exception as error:
            raise a.AdapterError(
                "E_VERIFY_INCOMPLETE",
                "Independent silk snapshot lacks complete native text/material evidence",
                {
                    "write_attempted": False,
                    "cause": type(error).__name__,
                    "label_inventory": board_labels.inventory(doc, board, a),
                },
            ) from error
        return finish({"board": str(board), "geometry": state["geometry"], "label_state": state})
    geometry = capture(doc, a, strict=True) if params.get("strict") is True else capture(doc, a)
    if params.get("plane_objects") is True:
        from .board_copper import observe as plane_state

        return finish({"board": str(board), **plane_state(doc, a, geometry)})
    if params.get("routing_objects") is True:
        from .board_unroute import observe as routing_state

        return finish({"board": str(board), **routing_state(doc, a, geometry)})
    return finish({"board": str(board), "geometry": geometry})


def _export(source, tool, cache, a):
    if not source.is_file():
        raise a.AdapterError(
            "E_NOT_FOUND",
            "Definition database is missing",
            {"source": str(source), "write_attempted": False},
        )
    before = _file(source)
    dest = cache / (uuid.uuid4().hex + ".hkp")
    run = a._run_library_tool(
        tool,
        ["-i", str(source), "-o", str(dest), "-a", "-u", "mm", "-l", str(dest.with_suffix(".log"))],
        timeout=30,
    )
    if run["exit_code"] or run["dialogs"] or not dest.is_file():
        raise a.AdapterError(
            "E_NATIVE_FAILURE", "Definition export failed", {"source": str(source), "tool": run}
        )
    if _file(source) != before:
        raise a.AdapterError(
            "E_CONFLICT", "Definition changed during export", {"write_attempted": False}
        )
    return R.decode_text(dest.read_bytes()), before


def _cells(text, partition=""):
    """Retain even unmodelled cell properties; timestamps/history are not geometry."""

    def record(node):
        return [
            node.keyword,
            node.values,
            sorted(
                [record(c) for c in node.children if c.keyword not in {"TIMESTAMP", "HISTORY"}],
                key=lambda value: json.dumps(value, sort_keys=True),
            ),
        ]

    raw = {
        str(r.values[0]): record(r)
        for r in R.parse_records(text)
        if r.keyword in R.CELL_KINDS and r.values
    }
    cells = R.parse_cells(text, partition)
    for cell in cells:
        cell["refresh_record"] = raw[cell["name"]]
    return cells


def _pads(text):
    from .pad_scope import records, supported_pad, supported_stack

    value = R.parse_padstacks(text)
    native = records(text)
    for name, shape in value["pads"].items():
        shape["native_supported"] = supported_pad(native["PAD", name])
    value["supported_stacks"] = {
        name: supported_stack(native["PADSTACK", name]) for name in value["padstacks"]
    }
    return value


def definitions(project, board, geometry, cache, a):
    """Read source and saved board definitions via the installed read converters."""
    cache.mkdir(parents=True, exist_ok=True)
    lmc, root = a._central_library(project)
    sources = [_file(project), _file(lmc)]
    central = []
    # Cells imported by converters can have an empty native Partition property.
    # Resolve exact names across ALL central cell partitions, refusing ambiguity;
    # do not infer a partition from a filename or silently pick the first match.
    cell_sources = [_file(p) for p in sorted((root / "CellDBLibs").glob("*.cel"))]
    exported = a._library_export(
        {"library": str(lmc), "cache": str(cache), "kinds": ["cells"], "fresh": True}
    )
    if exported["failed"]:
        raise a.AdapterError(
            "E_NATIVE_FAILURE",
            "Central cell catalog export is incomplete",
            {"failed": exported["failed"], "write_attempted": False},
        )
    for item in exported["files"]:
        central += _cells(R.decode_text(Path(item["path"]).read_bytes()), item["partition"])
    if cell_sources != [_file(p) for p in sorted((root / "CellDBLibs").glob("*.cel"))]:
        raise a.AdapterError(
            "E_CONFLICT", "Central cell sources changed during export", {"write_attempted": False}
        )
    sources += cell_sources
    text, source = _export(root / "Layout/PadstackDB.psk", "PadstackDB2HKP", cache, a)
    central_pads = _pads(text)
    sources.append(source)
    text, source = _export(board.parent / "Layout/CellDB.cel", "CellDB2HKP", cache, a)
    local = _cells(text)
    sources.append(source)
    text, source = _export(board.parent / "Layout/PadstackDB.psk", "PadstackDB2HKP", cache, a)
    local_pads = _pads(text)
    sources.append(source)
    result = {
        "central_library": str(lmc),
        "sources": sources,
        "central": {},
        "local": {},
        "resolved_cells": {},
        "local_catalog": {
            "cells": local,
            "padstacks": {k: v for k, v in local_pads.items() if k != "supported_stacks"},
        },
    }
    for comp in geometry["components"]:
        name = comp["footprint"]
        for level, cells, pads in [
            ("central", central, central_pads),
            ("local", local, local_pads),
        ]:
            found = [
                c
                for c in cells
                if c["name"] == name
                and (
                    level == "local" or not comp["partition"] or c["partition"] == comp["partition"]
                )
            ]
            if len(found) != 1:
                raise a.AdapterError(
                    "E_NOT_FOUND",
                    "Exactly one matching cell definition is required",
                    {
                        "cell": name,
                        "partition": comp["partition"],
                        "level": level,
                        "count": len(found),
                        "write_attempted": False,
                    },
                )
            cell = {k: v for k, v in found[0].items() if k != "partition"}
            if level == "central":
                result["resolved_cells"][name] = {
                    "partition": found[0]["partition"],
                    "native_partition": comp["partition"],
                    "resolution": "exact unique cell name among exported central partitions",
                }
            dependencies = {}
            for pin in cell["pins"] + cell["holes"]:
                stack = R.padstack_geometry(pads, pin["padstack"])
                if not stack or not stack["pad"]:
                    raise a.AdapterError(
                        "E_NOT_FOUND",
                        "Cell padstack or copper definition is missing",
                        {"write_attempted": False},
                    )
                raw_stack = pads["padstacks"][pin["padstack"]]
                stack["paste"] = pads["pads"].get(raw_stack.get("top_paste"))
                stack["definition"] = raw_stack
                stack["native_supported"] = pads["supported_stacks"][pin["padstack"]]
                stack["all_pads"] = {
                    k: pads["pads"].get(v)
                    for k, v in raw_stack.items()
                    if k not in {"type", "hole"}
                }
                dependencies[pin["padstack"]] = stack
            result[level][name] = _canonical({"cell": cell, "padstacks": dependencies})
    return result


def _invariants(state, changed):
    result = json.loads(json.dumps(state))
    for comp in result["components"]:
        if comp["refdes"] in changed:
            for pin in comp["geometry"]:
                pin["copper_layers"] = [p["layer"] for p in pin["copper"]]
                pin["fabrication_surfaces"] = [
                    {k: p[k] for k in ("type", "side", "layer")} for p in pin["fabrication"]
                ]
                pin["protected_fabrication"] = [
                    p for p in pin["fabrication"] if p["side"] != 1 or p["type"] not in {1, 2}
                ]
                pin.pop("copper")
                pin.pop("fabrication")
    for key, rows in result["model"].items():
        if key == "pads" and isinstance(rows, list):
            result["model"][key] = [
                {k: v for k, v in r.items() if k not in {"path", "circle", "width"}}
                if isinstance(r, dict) and r.get("refdes") in changed
                else r
                for r in rows
            ]
    return result


def _orthogonal(angle):
    try:
        value = float(angle)
        return math.isfinite(value) and abs(value / 90 - round(value / 90)) < 1e-8
    except (TypeError, ValueError):
        return False


def _supported_shape(shape):
    return bool(
        shape
        and shape.get("native_supported", True)
        and shape.get("shape") in {"RECTANGLE", "SQUARE", "ROUND"}
        and all(
            isinstance(shape.get(k), (float, int)) and math.isfinite(shape[k]) and shape[k] > 0
            for k in ("width", "height")
        )
        and (shape["shape"] == "RECTANGLE" or abs(shape["width"] - shape["height"]) < 1e-8)
    )


def _cell_invariant(cell):
    value = copy_json(cell)
    for pin in value.get("pins", []):
        pin.pop("padstack", None)  # A dimension-derived padstack name may change.

    def ignore_pin_stack(node, inside_pin=False):
        inside_pin = inside_pin or node[0] == "PIN"
        if inside_pin and node[0] == "PADSTACK":
            node[1] = ["<dimension-bound-padstack>"]
        for child in node[2]:
            ignore_pin_stack(child, inside_pin)

    if value.get("refresh_record"):
        ignore_pin_stack(value["refresh_record"])
    return value


def copy_json(value):
    return json.loads(json.dumps(value))


def _supported_delta(comp, new, old):
    if not (comp.get("placed") and comp.get("side") == "top" and _orthogonal(comp["rotation"])):
        return False
    if _cell_invariant(new["cell"]) != _cell_invariant(old["cell"]) or new["cell"].get("holes"):
        return False
    for new_pin, old_pin in zip(new["cell"]["pins"], old["cell"]["pins"], strict=True):
        if not _orthogonal(new_pin["rotation"]) or not _orthogonal(
            float(comp["rotation"]) + new_pin["rotation"]
        ):
            return False
        ns, os = new["padstacks"][new_pin["padstack"]], old["padstacks"][old_pin["padstack"]]
        if any(
            s.get("type") != "PIN_SMD" or s.get("hole") or not s.get("native_supported", True)
            for s in (ns, os)
        ):
            return False
        for kind in ("pad", "mask", "paste"):
            a, b = ns.get(kind), os.get(kind)
            if not _supported_shape(a) or not _supported_shape(b):
                return False
            if {k: v for k, v in a.items() if k not in {"width", "height"}} != {
                k: v for k, v in b.items() if k not in {"width", "height"}
            }:
                return False
        # Technology membership and all non-dimensional properties remain fixed.
        if set(ns.get("all_pads", {})) != set(os.get("all_pads", {})):
            return False
        for stack in (ns, os):
            for bottom, top in (
                ("bottom", "pad"),
                ("bottom_mask", "mask"),
                ("bottom_paste", "paste"),
            ):
                if bottom in stack.get("all_pads", {}) and stack["all_pads"][bottom] != stack[top]:
                    return False
        for key, value in ns.get("all_pads", {}).items():
            previous = os["all_pads"][key]
            if not _supported_shape(value) or not _supported_shape(previous):
                return False
            if {k: v for k, v in value.items() if k not in {"width", "height"}} != {
                k: v for k, v in previous.items() if k not in {"width", "height"}
            }:
                return False
    return True


def _silk_matches(comp, model, cell):
    """Narrow straight-path/circle check, including a no-op after a partial refresh."""
    if "silkscreen" not in cell:
        return True  # Old synthetic fixtures; every real parsed cell has this key.
    x, y = float(comp.get("x", 0)), float(comp.get("y", 0))
    angle = math.radians(float(comp["rotation"]))

    def point(p):
        return [
            round(x + p[0] * math.cos(angle) - p[1] * math.sin(angle), 4),
            round(y + p[0] * math.sin(angle) + p[1] * math.cos(angle), 4),
        ]

    def normalized(points):
        # Direction of a straight stroke is immaterial; topology and all vertices remain.
        return min(points, list(reversed(points)))

    expected = []
    for g in cell["silkscreen"]:
        if g["kind"] == "circle":
            expected.append(
                {
                    "circle": [*point(g["points"][0]), round(g["radius"], 4)],
                    "width": round(g["width"], 4),
                }
            )
        elif g["kind"] == "path":
            expected.append(
                {
                    "path": normalized([point(p) for p in g["points"]]),
                    "width": round(max(g["width"], 0.001), 4),
                }
            )
        else:
            return False
    observed = []
    for g in model.get("silk", []):
        if g.get("refdes") != comp["refdes"]:
            continue
        if "circle" in g:
            observed.append(
                {"circle": [round(v, 4) for v in g["circle"]], "width": round(g.get("width", 0), 4)}
            )
        elif "path" in g and all(len(p) < 3 or abs(p[2]) < 1e-8 for p in g["path"]):
            observed.append(
                {
                    "path": normalized([[round(v, 4) for v in p[:2]] for p in g["path"]]),
                    "width": round(g.get("width", 0), 4),
                }
            )
        else:
            return False
    return _canonical(expected) == _canonical(observed)


def _contour_matches(wanted, observed, center, angle):
    if not _supported_shape(wanted) or not _orthogonal(angle) or len(observed) != 1:
        return False
    g = observed[0]
    width, height = wanted["width"], wanted["height"]
    if round(angle / 90) % 2:
        width, height = height, width
    box = [
        center[0] - width / 2,
        center[1] - height / 2,
        center[0] + width / 2,
        center[1] + height / 2,
    ]
    if len(g.get("bounds_mm", [])) != 4 or not all(
        abs(a - b) <= TOLERANCE_MM for a, b in zip(g["bounds_mm"], box, strict=True)
    ):
        return False
    if wanted["shape"] == "ROUND":
        circle = g.get("circle_mm")
        return bool(
            g.get("shape") == "ROUND"
            and circle
            and len(circle) == 3
            and all(
                abs(a - b) <= TOLERANCE_MM
                for a, b in zip(circle, [*center, width / 2], strict=True)
            )
        )
    if g.get("shape") != "polygon":
        return False
    points = g.get("points_mil", [])
    if (
        len(points) != 3
        or not all(len(row) == 5 for row in points)
        or any(abs(v) > 1e-8 for v in points[2])
    ):
        return False
    xy = [(x * 0.0254, y * 0.0254) for x, y in zip(points[0], points[1], strict=True)]
    if any(abs(a - b) > TOLERANCE_MM for a, b in zip(xy[0], xy[-1], strict=True)):
        return False
    corners = [(x, y) for x in (box[0], box[2]) for y in (box[1], box[3])]
    indices = [
        next(
            (
                i
                for i, c in enumerate(corners)
                if max(abs(p[0] - c[0]), abs(p[1] - c[1])) <= TOLERANCE_MM
            ),
            None,
        )
        for p in xy
    ]
    return bool(
        None not in indices
        and len(set(indices[:4])) == 4
        and all(
            (abs(a[0] - b[0]) <= TOLERANCE_MM) != (abs(a[1] - b[1]) <= TOLERANCE_MM)
            for a, b in zip(xy, xy[1:], strict=False)
        )
    )


def compare(plan, state, local, catalog=None):
    changed = plan["affected_instances"]
    checks = [
        {
            "id": "protected-objects",
            "passed": _invariants(plan["before"], changed) == _invariants(state, changed),
        },
        {"id": "board-local-definitions", "passed": local == plan["definitions"]["central"]},
    ]
    if "local_catalog" in plan["definitions"]:
        previous = plan["definitions"]["local_catalog"]
        permitted = {"padstacks": set(), "pads": set(), "holes": set()}
        for name in plan["changed_cells"]:
            for stack_name, stack in plan["definitions"]["central"][name]["padstacks"].items():
                permitted["padstacks"].add(stack_name)
                permitted["pads"].update(stack["definition"].values())
                permitted["holes"].add(stack["definition"].get("hole"))
        unchanged = catalog is not None
        if catalog is not None:
            previous_cells = {c["name"]: c for c in previous["cells"]}
            current_cells = {c["name"]: c for c in catalog["cells"]}
            unchanged = previous_cells.keys() == current_cells.keys() and all(
                name in plan["changed_cells"] or value == current_cells[name]
                for name, value in previous_cells.items()
            )
            for kind, items in previous["padstacks"].items():
                if kind == "supported_stacks":
                    # Read older in-flight plans that carried parser metadata;
                    # this is not a native geometry catalog or a permitted change.
                    unchanged = unchanged and items == catalog["padstacks"].get(kind, items)
                    continue
                unchanged = unchanged and all(
                    name in permitted[kind] or value == catalog["padstacks"][kind].get(name)
                    for name, value in items.items()
                )
                unchanged = (
                    unchanged and set(catalog["padstacks"][kind]) - set(items) <= permitted[kind]
                )
        checks.append({"id": "unrelated-local-definitions", "passed": unchanged})
    for comp in state["components"]:
        if comp["refdes"] not in changed:
            continue
        definition = plan["definitions"]["central"][comp["footprint"]]
        checks.append(
            {
                "id": f"{comp['refdes']}.instance-silkscreen",
                "passed": _silk_matches(comp, state["model"], definition["cell"]),
            }
        )
        pins = {p["number"]: p for p in definition["cell"]["pins"]}
        for pin in comp["geometry"]:
            expected = definition["padstacks"][pins[pin["number"]]["padstack"]]
            angle = float(comp["rotation"]) + pins[pin["number"]]["rotation"]
            swap = round(angle / 90) % 2
            for kind, wanted, observed in [
                ("copper", expected["pad"], [g for p in pin["copper"] for g in p["geometry"]]),
                (
                    "mask",
                    expected["mask"],
                    [
                        g
                        for p in pin["fabrication"]
                        if p["type"] == 2 and p["side"] == 1
                        for g in p["geometry"]
                    ],
                ),
                (
                    "paste",
                    expected["paste"],
                    [
                        g
                        for p in pin["fabrication"]
                        if p["type"] == 1 and p["side"] == 1
                        for g in p["geometry"]
                    ],
                ),
            ]:
                size = [wanted["width"], wanted["height"]] if wanted else []
                if swap:
                    size.reverse()
                actual = [
                    [g["bounds_mm"][2] - g["bounds_mm"][0], g["bounds_mm"][3] - g["bounds_mm"][1]]
                    for g in observed
                ]
                valid = (
                    len(actual) == 1
                    and len(size) == 2
                    and all(
                        abs(x - y) <= TOLERANCE_MM for x, y in zip(actual[0], size, strict=True)
                    )
                )
                valid = valid and _contour_matches(wanted, observed, pin["center_mm"], angle)
                checks.append(
                    {
                        "id": f"{comp['refdes']}.{pin['number']}.{kind}",
                        "passed": valid,
                        "expected_mm": size,
                        "observed_mm": actual,
                    }
                )
    return {
        "valid": all(c["passed"] for c in checks),
        "checks": checks,
        "units": "mm",
        "tolerance": TOLERANCE_MM,
    }


def make_plan(params, client, a):
    project, board, app, doc = active(params, client, a)
    if a._com_member(doc.ProjectIntegration, "ForwardAnnotationStatus") != a.PRJINT_IN_SYNCH:
        raise a.AdapterError(
            "E_CONFLICT",
            "Complete logical pcb update before cell refresh",
            {"write_attempted": False},
        )
    before = capture(doc, a)
    defs = definitions(project, board, before, Path(params["cache"]), a)
    if {c["name"] for c in defs["local_catalog"]["cells"]} != {
        c["footprint"] for c in before["components"]
    }:
        raise a.AdapterError(
            "E_UNSUPPORTED",
            "Refresh currently requires all local cells to have observed instances",
            {"write_attempted": False},
        )
    changed = sorted(
        name for name in defs["central"] if defs["central"][name] != defs["local"][name]
    )
    affected = sorted(c["refdes"] for c in before["components"] if c["footprint"] in changed)
    for c in before["components"]:
        if c["refdes"] not in affected:
            continue
        new, old = defs["central"][c["footprint"]], defs["local"][c["footprint"]]

        if not _supported_delta(c, new, old):
            raise a.AdapterError(
                "E_UNSUPPORTED",
                "Refresh supports only copper/mask/paste dimensions of top-side orthogonal "
                "SMD instances with orthogonal local pads, rectangle/square/circle contours "
                "and unchanged cell attributes, outlines, silk, text, pins and technology",
                {"cell": c["footprint"], "write_attempted": False},
            )
        if not _silk_matches(c, before["model"], old["cell"]):
            raise a.AdapterError(
                "E_UNSUPPORTED",
                "Instance silkscreen is not proven to match its old local cell",
                {"cell": c["footprint"], "write_attempted": False},
            )
    plan = {
        "project": str(project),
        "pcb": str(board),
        "definitions": defs,
        "before": before,
        "changed_cells": changed,
        "affected_instances": affected,
        "logical_status": "in_synch",
        "native_command": COMMAND,
        "library_services": {
            "action": "open and normally close a new PID/library-matched Library Services window",
            "existing_dialogs": "refuse without closing",
            "apply_or_forced_release": False,
            "lmc_container_may_be_saved": True,
            "definition_database_changes": "refuse",
        },
        "no_change": not changed,
        "limits": [
            "no logical annotation",
            "no routing edits",
            "native DRC is separate",
            "GUI command refreshes used local definitions and their instances together",
        ],
        "source": "installed native converters in mm and native instance getters",
    }
    plan["fingerprint"] = digest(plan)
    if not affected:
        supported_instances = [
            c["refdes"]
            for c in before["components"]
            if c.get("placed")
            and c.get("side") == "top"
            and all(
                s["type"] == "PIN_SMD"
                for s in defs["central"][c["footprint"]]["padstacks"].values()
            )
        ]
        proof = compare(
            {**plan, "affected_instances": supported_instances},
            before,
            defs["local"],
            defs["local_catalog"],
        )
        if not proof["valid"]:
            raise a.AdapterError(
                "E_CONFLICT",
                "Local definitions match but instance geometry differs; "
                "establish a supported instance reset before proceeding",
                {"write_attempted": False, "verification": proof},
            )
    return plan, app, doc


def execute(params, client, a):
    try:
        with a._native_step("cell_refresh.plan"):
            plan, app, doc = make_plan(params, client, a)
    except Exception as exc:
        error = (
            exc if isinstance(exc, a.AdapterError) else a._com_error(exc, "cell_refresh_preflight")
        )
        raise a.AdapterError(
            error.code, error.message, {**(error.details or {}), "write_attempted": False}
        ) from exc
    if not params.get("apply"):
        return plan
    if params.get("expected") != plan["fingerprint"]:
        raise a.AdapterError(
            "E_CONFLICT",
            "Bound cell refresh input/instance state changed",
            {"write_attempted": False},
        )
    stages = {
        "central_definition": "observed",
        "local_definition": "not_started",
        "instances": "not_started",
        "save_reopen": "not_started",
    }
    result = {
        "pcb": plan["pcb"],
        "outcome": "cells_unchanged" if plan["no_change"] else "cells_refreshed",
        "applied": False,
        "saved": False,
        "persistence_verified": False,
        "stages": stages,
        "plan_fingerprint": plan["fingerprint"],
        "native_log": {"status": "not_generated"},
        "change_status": "unknown",
    }
    if plan["no_change"]:
        result["verification"] = compare(
            plan,
            plan["before"],
            plan["definitions"]["local"],
            plan["definitions"].get("local_catalog"),
        )
        result["no_change"] = True
        result["change_status"] = "not_applied"
        return result
    log = Path(plan["pcb"]).parent / "LogFiles/UpdateLocalDBs.txt"
    stamp = log.stat().st_mtime_ns if log.exists() else None
    try:
        with a._native_step("cell_refresh.library_services"):
            result["library_services"] = prepare_library_services(app, plan, a)
        lmc_source = next(
            s
            for s in plan["definitions"]["sources"]
            if Path(s["path"]).resolve() == Path(plan["definitions"]["central_library"]).resolve()
        )
        result["library_services"]["lmc_before"] = lmc_source
        result["library_services"]["lmc_after"] = _file(Path(lmc_source["path"]))
        with a._native_step("cell_refresh.after_services_identity"):
            app, doc = after_services(params, plan, client, a)
        stages["local_definition"] = stages["instances"] = "unknown"
        started_ns = time.time_ns()
        with a._native_step("cell_refresh.native_command"):
            accepted = app.Gui.ProcessCommand(COMMAND, True)
        if not accepted:
            raise a.AdapterError(
                "E_NATIVE_FAILURE", "Layout refused the supported local-library refresh command"
            )
        fresh = (
            log.is_file()
            and log.stat().st_mtime_ns != stamp
            and log.stat().st_mtime_ns >= started_ns
        )
        text = a._tool_text(log.read_bytes()) if fresh else ""
        result["native_log"] = {
            "status": "fresh" if fresh else "not_generated",
            "path": str(log),
            "sha256": _file(log)["sha256"] if fresh else None,
        }
        after_command = capture(a._licensed_document(app), a)
        result["after_command"] = after_command
        result["applied"] = after_command != plan["before"]
        result["change_status"] = "partial" if result["applied"] else "unknown"
        if (
            "The Local Cell And Padstack DBs have been updated with no errors or warnings."
            not in text
        ):
            stages["instances"] = "changed" if result["applied"] else "unchanged"
            if "Cell And Padstack DBs have not been updated." in text and not result["applied"]:
                if all(_file(Path(s["path"])) == s for s in plan["definitions"]["sources"]):
                    stages["local_definition"] = "unchanged"
                    result["change_status"] = "not_applied"
                    result["persistence_state"] = "unchanged"
            raise a.AdapterError(
                "E_NATIVE_FAILURE",
                "Local-library refresh did not produce a fresh successful report",
                {
                    "report": text[-6000:],
                    "next_action": "Inspect UpdateLocalDBs.txt and owned Library Services/editor "
                    "state; do not replay an uncertain write.",
                },
            )
        stages["local_definition"] = "native_reported_updated"
        stages["instances"] = "changed" if result["applied"] else "unchanged"
        with a._native_step("cell_refresh.save_close_reopen"):
            # Refresh also replaces native document interfaces. Drop the prior
            # wrapper before saving; never redispatch the refresh on this error.
            app = a._application(client, attach_only=True)
            doc = a._reopen_board(app, Path(plan["pcb"]))
        result["saved"] = result["persistence_verified"] = True
        stages["save_reopen"] = "verified"
        with a._native_step("cell_refresh.reopened_readback"):
            state = capture(doc, a)
            defs = definitions(
                Path(plan["project"]), Path(plan["pcb"]), state, Path(params["cache"]), a
            )
        result["definitions_after"] = defs
        result["applied"] = result["applied"] or defs["local"] != plan["definitions"]["local"]
        result["verification"] = compare(plan, state, defs["local"], defs.get("local_catalog"))
        result["change_status"] = "applied" if result["verification"]["valid"] else "partial"
        stages["local_definition"] = (
            "verified" if defs["local"] == plan["definitions"]["central"] else "mismatch"
        )
        stages["instances"] = "verified" if result["verification"]["valid"] else "mismatch"
        return result
    except Exception as exc:
        library_changed = False
        library = plan["definitions"].get("central_library")
        if library:
            try:
                before_lmc = next(
                    s
                    for s in plan["definitions"]["sources"]
                    if Path(s["path"]).resolve() == Path(library).resolve()
                )
                after_lmc = _file(Path(library))
                library_changed = before_lmc != after_lmc
                result["library_container"] = {
                    "before": before_lmc,
                    "after": after_lmc,
                    "changed": library_changed,
                }
            except (OSError, StopIteration):
                library_changed = None
                result["library_container"] = {"status": "not_observed"}
        if stages["local_definition"] == "not_started":
            result["change_status"] = (
                "unknown"
                if library_changed is None
                else "partial"
                if library_changed
                else "not_applied"
            )
            result["persistence_state"] = (
                "unknown"
                if library_changed is None
                else "partial"
                if library_changed
                else "unchanged"
            )
        result["outcome"] = "cell_refresh_incomplete"
        error = exc if isinstance(exc, a.AdapterError) else a._com_error(exc, "cell_refresh")
        details = {**(error.details or {}), "native_result": result}
        if library_changed is not False:
            details["write_attempted"] = True
        raise a.AdapterError(error.code, error.message, details) from exc
