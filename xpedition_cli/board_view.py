"""Bound display configuration and independently observed Layout view state."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

from .cell_refresh import digest


def _file(path):
    return {
        "path": str(path),
        "exists": path.is_file(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
    }


def _scheme_inventory(config, name, a):
    """Bind native profile copies as well as the local root definition."""
    selected, protected = [], []
    directories = [config]
    for path in sorted(config.rglob("*")):
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise a.AdapterError(
                "E_UNSUPPORTED",
                "Linked scheme directories are not supported",
                {"write_attempted": False},
            )
        if path.is_dir():
            directories.append(path)
        elif path.is_file() and path.suffix.lower() == ".dcs":
            row = {
                "relative_path": path.relative_to(config).as_posix(),
                "sha256": _file(path)["sha256"],
            }
            (selected if path.name.casefold() == name.casefold() else protected).append(row)
    return selected, protected, [str(d / name) for d in directories]


def scheme_plan(board, layers, params, a):
    scheme, top = a.display_scheme(params)
    config = board.parent / "Config"
    if not scheme.startswith("Loc: ") or any(c in scheme[5:] for c in "/\\:"):
        raise a.AdapterError(
            "E_UNSUPPORTED",
            "Only local, file-bound display schemes are supported",
            {"write_attempted": False},
        )
    target = config / (scheme[5:] + ".dcs")
    source = config / "All On.dcs" if top else target
    if not source.is_file():
        raise a.AdapterError(
            "E_NOT_FOUND",
            "Display scheme source is missing",
            {"source": str(source), "write_attempted": False},
        )
    content = source.read_text(encoding="utf-8-sig")
    desired = a.top_view_scheme_text(content, layers) if top else content
    definition_hash = hashlib.sha256(desired.encode("utf-8")).hexdigest()
    provenance = target.with_suffix(".dcs.xpedition.json")
    native_files, protected, possible_files = _scheme_inventory(config, target.name, a)
    verified = True
    if top and target.exists():
        try:
            recorded = json.loads(provenance.read_text(encoding="utf-8"))
            verified = (
                recorded["source_sha256"] == _file(source)["sha256"]
                and recorded["input_definition_sha256"] == definition_hash
                and recorded["target_sha256"] == _file(target)["sha256"]
                and recorded["scheme"] == scheme
                and recorded["recipe"] == "native_LoadScheme_SaveScheme_LoadScheme"
                and recorded["native_files"] == native_files
                and all(f["sha256"] == _file(target)["sha256"] for f in native_files)
            )
        except (OSError, ValueError, KeyError, TypeError):
            verified = False
    elif top and (provenance.exists() or native_files):
        verified = False
    elif not top:
        verified = all(f["sha256"] == _file(target)["sha256"] for f in native_files)
    if not verified and not params.get("allow_unverified_scheme"):
        raise a.AdapterError(
            "E_CONFLICT",
            "Existing display scheme or native profile copy has conflicting or missing "
            "source/content provenance; it will not be overwritten",
            {"target": str(target), "write_attempted": False},
        )
    return {
        "scheme": scheme,
        "top_view": top,
        "config_directory": str(config),
        "source": _file(source),
        "target": _file(target),
        "definition_sha256": definition_hash,
        "definition_verified": verified,
        "provenance": _file(provenance) if top else None,
        "new_files": [] if target.is_file() else [str(target), *([str(provenance)] if top else [])],
        "native_files": native_files,
        "protected_schemes": protected,
        "potential_native_files": [] if target.is_file() else possible_files,
        "native_output_scope": {
            "directory": str(config),
            "filename": target.name,
            "recursive": True,
            "existing_copies": "refuse before registration",
        },
        "layers": layers,
    }


def ensure_scheme(plan, a):
    board = Path(plan["config_directory"]).parent / "bound.pcb"
    current = scheme_plan(board, plan["layers"], {"scheme": plan["scheme"]}, a)
    if current != plan:
        raise a.AdapterError(
            "E_CONFLICT", "Display scheme input changed after preview", {"write_attempted": False}
        )
    if not plan["new_files"]:
        return False
    source = Path(plan["source"]["path"]).read_text(encoding="utf-8-sig")
    text = a.top_view_scheme_text(source, plan["layers"])
    # Exclusive creation: never replace an existing user display configuration.
    with Path(plan["target"]["path"]).open("xb") as output:
        output.write(text.encode("utf-8"))
    return True


def register_new_scheme(plan, doc, a):
    """Register the newly created owned definition through published display APIs.

    Raw DCS creation alone did not establish the loaded scheme name in 151.0.
    SaveScheme/LoadScheme supplies native registration; no private DCS key is edited.
    Never call this on a file that existed before this operation.
    """
    if not plan["new_files"]:
        return False
    if _file(Path(plan["target"]["path"]))["sha256"] != plan["definition_sha256"]:
        raise a.AdapterError(
            "E_CONFLICT",
            "New scheme changed before native registration",
            {"definition_file_created": True},
        )
    config = Path(plan["config_directory"])
    target = Path(plan["target"]["path"])
    actual, protected, _ = _scheme_inventory(config, target.name, a)
    expected = [{"relative_path": target.name, "sha256": plan["definition_sha256"]}]
    if actual != expected or protected != plan["protected_schemes"]:
        raise a.AdapterError(
            "E_CONFLICT",
            "Scheme files changed before native registration",
            {"definition_file_created": True, "native_registration_started": False},
        )
    control = doc.ActiveView.DisplayControl
    for name in ("LoadScheme", "SaveScheme", "LoadScheme"):
        returned = a._native_call(
            "view." + name, lambda method=name: getattr(control, method)(plan["scheme"])
        )
        if returned is not True:
            raise a.AdapterError(
                "E_NATIVE_FAILURE",
                "Native display scheme registration did not return success",
                {"stage": name, "definition_file_created": True},
            )
    if str(control.Name) != plan["scheme"]:
        raise a.AdapterError("E_VERIFY_INCOMPLETE", "Registered scheme name could not be read back")
    native_files, protected, _ = _scheme_inventory(config, target.name, a)
    target_sha = _file(target)["sha256"]
    if protected != plan["protected_schemes"] or not all(
        f["sha256"] == target_sha for f in native_files
    ):
        raise a.AdapterError(
            "E_VERIFY_INCOMPLETE",
            "Native scheme registration changed protected files or emitted conflicting copies",
            {"definition_file_created": True, "native_save_acknowledged": True},
        )
    record = {
        "scheme": plan["scheme"],
        "source_sha256": plan["source"]["sha256"],
        "input_definition_sha256": plan["definition_sha256"],
        "target_sha256": target_sha,
        "native_files": native_files,
        "recipe": "native_LoadScheme_SaveScheme_LoadScheme",
    }
    with Path(plan["provenance"]["path"]).open("x", encoding="utf-8") as stream:
        json.dump(record, stream, sort_keys=True)
    return True


def display_state(doc, a):
    result = {"scheme": None, "viewport_mm": None, "visible_objects": [], "complete": False}
    try:
        view = doc.ActiveView
        result["scheme"] = str(view.DisplayControl.Name)
        result["viewport_mm"] = list(a._extrema_mm(view))
        if not all(math.isfinite(v) for v in result["viewport_mm"]):
            raise ValueError("Invalid view extents")
        for key, kind in (("Components", "component"), ("Traces", "trace")):
            collection = getattr(doc, key)
            if callable(collection) and not hasattr(collection, "Count"):
                collection = collection()
            for obj in a._items(collection, strict=True):
                visible = view.IsVisible(obj)
                if type(visible) not in (bool, int) or visible not in (-1, 0, 1):
                    raise ValueError("Unobservable visibility")
                result["visible_objects"].append(
                    {
                        "kind": kind,
                        "uid": str(obj.UniqueId),
                        "visible": bool(visible),
                        **(
                            {"refdes": str(obj.RefDes)}
                            if kind == "component"
                            else {"net": str(obj.Net.Name), "layer": int(obj.Layer)}
                        ),
                    }
                )
                if kind == "component":
                    for pin in a._items(obj.Pins, strict=True):
                        pin_visible = view.IsVisible(pin)
                        if type(pin_visible) not in (bool, int) or pin_visible not in (-1, 0, 1):
                            raise ValueError("Unobservable pin visibility")
                        result["visible_objects"].append(
                            {
                                "kind": "pin",
                                "uid": str(pin.UniqueId),
                                "refdes": str(obj.RefDes),
                                "pin": str(pin.Name),
                                "visible": bool(pin_visible),
                            }
                        )
        result["complete"] = bool(result["scheme"])
    except Exception as error:
        result["error_type"] = type(error).__name__
    return result


def contains(actual, expected):
    return bool(
        actual
        and expected
        and len(actual) == len(expected) == 4
        and actual[0] <= expected[0] + 0.001
        and actual[1] <= expected[1] + 0.001
        and actual[2] >= expected[2] - 0.001
        and actual[3] >= expected[3] - 0.001
    )


def reminders(model):
    """Compatibility list; callers must also expose the associated coverage report."""
    from .layout_reminders import report

    return report(model)["layout_reminders"]


def active(params, client, a):
    board = a._layout_board_path(params)
    app = a._active_object(client)
    a._check_bound_instance(app, "pcb")
    doc = a._licensed_document(app)
    if Path(str(doc.FullName)).resolve() != board.resolve():
        raise a.AdapterError(
            "E_CONFLICT", "Requested board must already be open", {"write_attempted": False}
        )
    if int(doc.CurrentUnit) != 2:
        raise a.AdapterError(
            "E_UNSUPPORTED",
            "View geometry reader requires CurrentUnit=mils; no unit was changed",
            {"write_attempted": False},
        )
    return board, app, doc


def observe(params, client, a):
    from .layout_reminders import report

    board, app, doc = active(params, client, a)
    model = a._board_model(doc, strict=True)
    names = [row["net"] for row in model["connectivity"]["nets"]]
    rule_files = {
        path.name: _file(path)
        for path in sorted((board.parent / "Config").iterdir())
        if path.suffix.lower() in {".drc", ".e2e"}
        or path.name in {"BatchDrc.dat", "BatchDrcE2E.dat"}
    }
    clearances = (
        a._selected_clearances(doc, names, list(range(1, int(doc.LayerCount) + 1)))
        if 2 <= len(names) <= 8 and 1 <= int(doc.LayerCount) <= 8
        else None
    )
    return {
        "pcb": str(board),
        "application": a.application_identity(app, "pcb"),
        "schemes": [str(n) for n in doc.DisplaySchemes],
        "display": display_state(doc, a),
        "model": model,
        "rule_observation": {
            "files": rule_files,
            "master_clearances": clearances,
            "scope": "local checker profile bytes and bounded named-net Master clearances; "
            "other/location schemes unverified",
        },
        **report(model),
        "scheme_plan": scheme_plan(board, int(doc.LayerCount), params, a),
    }


def show(params, client, a):
    from . import win_dialogs as w
    from .layout_reminders import report

    board, app, doc = active(params, client, a)
    # Before any native display mutation, file creation, activation or input.
    if w.desktop_locked():
        raise a.AdapterError(
            "E_HUMAN_REQUIRED",
            "Unlock the interactive desktop before pcb view",
            {"write_attempted": False},
        )
    identity = a.application_identity(app, "pcb")
    pid = identity.get("pid")
    if not pid or str(pid) != os.environ.get("XPEDITION_EXPECTED_PID"):
        raise a.AdapterError(
            "E_CONFLICT", "View requires a bound application PID", {"write_attempted": False}
        )
    native_hwnd = identity.get("pid_source", {}).get("hwnd")
    window = w.main_window("expeditionpcb.exe")
    if not window or not w.same_root_window(window["hwnd"], native_hwnd):
        raise a.AdapterError(
            "E_HUMAN_REQUIRED",
            "The visible window is not the bound Layout board window",
            {"write_attempted": False},
        )
    plan = scheme_plan(board, int(doc.LayerCount), params, a)
    if params.get("scheme_plan") and params["scheme_plan"] != plan:
        raise a.AdapterError(
            "E_CONFLICT", "Display scheme changed after confirmation", {"write_attempted": False}
        )
    output = Path(params["output"]).resolve() if params.get("output") else None
    if output and (output.suffix.lower() != ".png" or output.exists()):
        raise a.AdapterError(
            "E_CONFLICT", "View requires a new .png output", {"write_attempted": False}
        )
    schemes = [str(n) for n in doc.DisplaySchemes]
    if plan["scheme"] not in schemes and not plan["new_files"]:
        raise a.AdapterError(
            "E_NOT_FOUND",
            "Scheme is not enumerated by this document",
            {"available": schemes, "write_attempted": False},
        )
    refs = [str(n).strip() for n in params.get("refdes") or [] if str(n).strip()]
    desired = a._parts_window(doc, refs) if refs else a._extrema_mm(doc.BoardOutline)
    before = a._board_model(doc, strict=True)
    written = ensure_scheme(plan, a)
    native_ack = (
        register_new_scheme(plan, doc, a)
        if written
        else a._load_display_scheme(doc, plan["scheme"])
    )
    current = display_state(doc, a)
    foreground = bool(window and w.bring_to_front(int(window["hwnd"])))
    picked = None
    if current["scheme"] != plan["scheme"] and foreground:
        picked = w.choose_combo_item(
            "expeditionpcb.exe", "CMD_DISPLAY_SCHEMES", plan["scheme"], expected_hwnd=native_hwnd
        )
    viewport_requested = a._zoom_view(doc, desired) if refs else a._fit_board_view(app, doc)
    observed = display_state(doc, a)  # fresh native getter, never inferred from a click
    identity_ok = (
        Path(str(doc.FullName)).resolve() == board.resolve()
        and a.application_identity(app, "pcb").get("pid") == pid
    )
    applied = identity_ok and observed["complete"] and observed["scheme"] == plan["scheme"]
    viewport_ok = contains(observed["viewport_mm"], desired)
    foreground = bool(foreground and w.foreground_matches(native_hwnd))
    result = {
        "pcb": str(board),
        "scheme": plan["scheme"],
        "scheme_applied": bool(applied),
        "scheme_written": written,
        "schemes": schemes,
        "scheme_plan": plan,
        "native_load_acknowledged": native_ack,
        "gui_selection": picked,
        "display_observed": observed,
        "identity_verified": identity_ok,
        "window_hwnd": int(window["hwnd"]),
        "foreground": foreground,
        "locked": w.desktop_locked(),
        "viewport_requested": viewport_requested,
        "fitted": viewport_ok and not refs,
        "zoomed": viewport_ok and bool(refs),
        "view": {"refdes": refs, "window": list(desired)},
        "components": len([o for o in observed["visible_objects"] if o["kind"] == "component"]),
        **report(before),
        "prompts": [],
        "screenshot": None,
        "geometry_sha256": digest(before),
        "_untrusted": ["pcb", "schemes", "prompts", "layout_reminders", "layout_reminder_coverage"],
    }
    if output and foreground and identity_ok:
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            result["screenshot"] = w.capture_window(int(window["hwnd"]), str(output))
            result["screenshot"]["valid_context"] = w.foreground_matches(native_hwnd)
            from PIL import Image, PngImagePlugin

            binding = {
                "generation_id": params.get("artifact_generation"),
                "project": str(Path(params["project"]).resolve()),
                "pcb": str(board),
                "pid": pid,
                "kind": "view",
                "scheme": observed["scheme"],
                "viewport_mm": observed["viewport_mm"],
                "source_geometry_sha256": digest(before),
                "capture": capture_binding(result["screenshot"]),
            }
            with Image.open(output) as img:
                img.load()
                info = PngImagePlugin.PngInfo()
                info.add_text("xpedition", json.dumps(binding))
                img.save(output, pnginfo=info)
            result["screenshot"]["bytes"] = output.stat().st_size
            result["artifact"] = {
                **binding,
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            }
        except (OSError, ValueError) as error:
            # Scheme/viewport observations preceded capture. Keep those facts;
            # an incomplete artifact never authorizes replay of the display task.
            result["screenshot"] = {
                "path": str(output),
                "complete": False,
                "valid_context": False,
                "error": {
                    "code": "E_CONFLICT" if isinstance(error, FileExistsError) else "E_IO",
                    "message": str(error),
                },
            }
    return result


def capture_binding(screenshot):
    return {
        key: screenshot.get(key)
        for key in (
            "window_hwnd",
            "window_pid",
            "bounds_px",
            "coordinate_space",
            "bounds_verified",
            "method",
            "dpi",
        )
    }


def verify(options, result, before, after, generation):
    checks = []

    def check(name, value):
        checks.append({"name": name, "passed": bool(value)})

    display = after.get("display") or {}
    plan = after.get("scheme_plan") or {}
    check(
        "native effective scheme",
        display.get("complete") is True
        and plan.get("definition_verified") is True
        and display.get("scheme") == result.get("scheme") == plan.get("scheme")
        and result.get("scheme_applied") is True,
    )
    check("geometry protected", before.get("model") == after.get("model"))
    check(
        "observed rules protected",
        isinstance(before.get("rule_observation"), dict)
        and before.get("rule_observation") == after.get("rule_observation"),
    )
    check(
        "displayed objects",
        all(
            r.get("visible") is True
            for r in display.get("visible_objects", [])
            if r.get("kind") != "component"
        )
        and all(
            any(
                p.get("kind") == "pin" and p.get("refdes") == c.get("refdes")
                for p in display.get("visible_objects", [])
            )
            for c in display.get("visible_objects", [])
            if c.get("kind") == "component"
        )
        and display.get("complete") is True,
    )
    check(
        "viewport independent readback",
        contains(display.get("viewport_mm"), result.get("view", {}).get("window")),
    )
    check(
        "foreground and target",
        result.get("foreground") is True
        and result.get("identity_verified") is True
        and not result.get("locked"),
    )
    if options.get("output"):
        try:
            from PIL import Image

            path = Path(options["output"])
            with Image.open(path) as img:
                binding = json.loads(img.info["xpedition"])
                img.verify()
            with Image.open(path) as img:
                img.load()
                decoded = img.format == "PNG" and img.width > 0 and img.height > 0
                pixels = [img.width, img.height]
            shot = result.get("screenshot") or {}
            bounds = shot.get("bounds_px") or []
            check(
                "physical window pixels",
                shot.get("coordinate_space") == "physical_pixels"
                and shot.get("bounds_verified") is True
                and shot.get("method") == "gdi_physical_frame"
                and type(shot.get("dpi")) is int
                and shot["dpi"] > 0
                and type(shot.get("window_hwnd")) is int
                and shot["window_hwnd"] > 0
                and shot["window_hwnd"] == result.get("window_hwnd")
                and shot.get("window_pid") == after["application"]["pid"]
                and len(bounds) == 4
                and all(type(v) is int for v in bounds)
                and [bounds[2] - bounds[0], bounds[3] - bounds[1]] == pixels
                and [shot.get("width"), shot.get("height")] == pixels
                and binding.get("capture") == capture_binding(shot),
            )
            check(
                "fresh decoded screenshot",
                decoded
                and result.get("screenshot", {}).get("valid_context") is True
                and not result.get("screenshot", {}).get("blank")
                and binding["generation_id"] == generation
                and binding["pcb"] == after["pcb"]
                and Path(binding["project"]).resolve() == Path(options["project"]).resolve()
                and binding["scheme"] == display["scheme"]
                and binding["source_geometry_sha256"] == digest(after["model"])
                and binding["pid"] == after["application"]["pid"]
                and binding["viewport_mm"] == display["viewport_mm"]
                and hashlib.sha256(path.read_bytes()).hexdigest()
                == result.get("artifact", {}).get("sha256"),
            )
        except (OSError, ValueError, KeyError, TypeError):
            check("fresh decoded screenshot", False)
    return {
        "valid": all(c["passed"] for c in checks),
        "checks": checks,
        "scope": (
            "bound scheme, native display/viewport, protected geometry and decoded "
            "physical-window screenshot provenance"
        ),
        "not_checked": ["human visual acceptance", "native DRC", "manufacturing"],
    }
