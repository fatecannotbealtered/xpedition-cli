"""Finite, allowlisted native observations and owned persistence/fault checks.

Started only by the explicit ordinary-user acceptance run. This file is not run
by the restricted Codex task. No IPC server, arbitrary command, or app activation.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from checks import Stop, positive_ccw, require

PACK = Path(__file__).resolve().parent
ROOT = PACK.parent.parent
STEPS = (
    "preflight",
    "guard",
    "source-library-readback",
    "failed-package-readback",
    "schematic-read",
    "pcb-read",
    "persistence",
    "release-target",
    "orphan-fault",
    "orphan-baseline",
)


def utc():
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def actor():
    spec = importlib.util.spec_from_file_location("readonly_identity", PACK / "identity_probe.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    row = module.process_identity(
        {"pid": os.getpid(), "parent_pid": os.getppid(), "exe": "python.exe"}
    )
    security = row.get("security_metadata") or {}
    require(
        security.get("account", "").casefold() == "example\\example-user"
        and security.get("elevated") == 0
        and security.get("restricted_token") is False,
        "E_ACTOR",
        "Run only in the user's ordinary non-elevated Windows terminal",
        {"caller": row, "COM_probe_attempted": False},
    )
    return module, row


def initialize(manifest):
    scratch = Path(manifest["scratch_root"])
    for key, value in {
        "XPEDITION_CLI_CONFIG_DIR": str(scratch / "state"),
        "XPEDITION_MGC_WD": str(scratch / "wdir"),
        "XPEDITION_REQUIRE_EXISTING": "1",
        "XPEDITION_EXPECTED_ADAPTER_BUILD": manifest["adapter_sha256"],
    }.items():
        os.environ[key] = value
    sys.path.insert(0, manifest["checkout"])
    import win32com

    cache = scratch / "com-cache"
    cache.mkdir(parents=True, exist_ok=True)
    win32com.__gen_path__ = str(cache)
    import win32com.gen_py

    win32com.gen_py.__path__ = [str(cache)]
    from xpedition_cli import native_com_adapter as bridge
    from xpedition_cli.native_identity import adapter_identity, application_identity

    pythoncom, client = bridge._import_com()
    pythoncom.CoInitialize()
    return bridge, client, pythoncom, adapter_identity, application_identity


def active_path(bridge, app, domain):
    if domain == "schematic":
        opened = app.IsProjectOpened()
        require(type(opened) is bool, "E_IDENTITY", "Designer open-state getter is unavailable")
        if not opened:
            return None
        value = bridge._com_member(bridge._com_member(app, "GetProjectData"), "GetProjectFilePath")
        require(
            isinstance(value, str) and value,
            "E_IDENTITY",
            "Open Designer project lacks a readable path",
        )
        return str(Path(value).resolve())
    doc = app.ActiveDocument
    if doc is None:
        return None
    value = doc.FullName
    require(
        isinstance(value, str) and value,
        "E_IDENTITY",
        "Open PCB document lacks a readable full path",
    )
    return str(Path(value).resolve())


def dirty(app):
    try:
        doc = app.ActiveDocument
        if doc is None:
            return {"value": None, "status": "no_active_document", "source": "ActiveDocument"}
        info = doc._oleobj_.GetTypeInfo()
        members = {
            info.GetNames(info.GetFuncDesc(i)[0])[0]
            for i in range(info.GetTypeAttr().cFuncs)
            if info.GetFuncDesc(i)[4] == 2
        }
        for name in ("Dirty", "Modified", "IsModified", "IsDirty", "NeedSave"):
            if name in members:
                value = getattr(doc, name)
                if type(value) is bool:
                    return {
                        "value": value,
                        "status": "observed",
                        "source": "ActiveDocument." + name,
                    }
    except Exception as exc:
        return {"value": None, "status": "unknown", "error_type": type(exc).__name__}
    return {"value": None, "status": "unknown", "source": "No declared boolean dirty getter found"}


def bind(manifest, module, bridge, client, application_identity, context, initial=False):
    rows, error = module.process_list()
    require(
        not error, "E_IDENTITY", "Cannot enumerate application process identities", {"error": error}
    )
    apps, observed = {}, {}
    for domain, info in manifest["applications"].items():
        candidates = []
        for row in rows:
            if row["exe"].casefold() != info["process_name"].casefold():
                continue
            identity = module.process_identity(row)
            security = identity.get("security_metadata") or {}
            if (
                identity.get("path", "").casefold() == info["path"].casefold()
                and security.get("account", "").casefold() == manifest["account"].casefold()
            ):
                candidates.append(identity)
        require(
            len(candidates) == 1,
            "E_IDENTITY",
            "Expected one ordinary-user engine at the pinned executable path",
            {"domain": domain, "processes": candidates},
        )
        process = candidates[0]
        require(
            (process.get("architecture") or {}).get("bitness") == 64,
            "E_VERSION",
            "Native engine architecture changed",
            {"process": process},
        )
        app = (
            bridge._viewdraw_active(client)
            if domain == "schematic"
            else bridge._active_object(client)
        )
        identity = application_identity(app, domain)
        require(
            identity.get("pid") == process["pid"],
            "E_IDENTITY",
            "COM object lacks the exact verified engine PID",
            {"domain": domain, "com": identity, "process": process},
        )
        typelib = identity.get("type_library") or {}
        require(
            typelib.get("major") == 155 and typelib.get("minor") == 0,
            "E_VERSION",
            "Live type-library version differs from the fixed 155.0 profile",
            {"domain": domain, "actual": typelib},
        )
        if not initial:
            frozen = context["applications"][domain]
            require(
                process["pid"] == frozen["process"]["pid"]
                and process.get("created_utc") == frozen["process"].get("created_utc"),
                "E_IDENTITY",
                "Native engine changed during the run",
                {"domain": domain, "actual": process},
            )
        current = active_path(bridge, app, domain)
        if initial:
            allowed = (
                context["owned_projects"] if domain == "schematic" else context["owned_boards"]
            )
            require(
                current is None or (manifest.get("resume_source") and current in allowed),
                "E_UNOWNED_DOCUMENT",
                "An application already holds a document outside this new run; nothing was closed",
                {"domain": domain, "active_document": current},
            )
        elif current:
            allowed = set(
                context["owned_projects"] if domain == "schematic" else context["owned_boards"]
            )
            require(
                current in allowed,
                "E_UNOWNED_DOCUMENT",
                "Application switched away from an explicitly owned scratch document",
                {"domain": domain, "active_document": current},
            )
        observed[domain] = {
            "process": process,
            "application": identity,
            "active_document": current,
            "dirty": dirty(app),
            "observed_utc": utc(),
        }
        apps[domain] = app
    return apps, observed


def scalar_getters(obj, names):
    info = obj._oleobj_.GetTypeInfo()
    members = {
        info.GetNames(info.GetFuncDesc(i)[0])[0]
        for i in range(info.GetTypeAttr().cFuncs)
        if info.GetFuncDesc(i)[4] == 2
    }
    result = {}
    for name in names:
        if name in members:
            value = getattr(obj, name)
            if value is None or type(value) in (int, float, str, bool):
                result[name] = value
    return result


def pcb_getters(bridge, app, constants=None):
    doc = bridge._licensed_document(app)
    result = {
        "source": "MGCPCB native component/pin property getters",
        "units": "mm",
        "angle_units": "degrees",
        "layer_count": doc.LayerCount,
        "components": [],
    }
    for component in bridge._items(bridge._com_member(doc, "Components")):
        side = component.Side
        require(
            type(side) is int and side in (1, 512), "E_READBACK", "Unknown native component side"
        )
        placed = component.Placed
        row = {
            "refdes": component.RefDes,
            "object_id": str(component.UniqueId),
            "cell": component.CellName,
            "placed": placed,
            "bottom": side == 512,
            "x": component.GetPositionX(4) if placed else None,
            "y": component.GetPositionY(4) if placed else None,
            "rotation": component.GetOrientation(0) if placed else None,
            "pins": [],
        }
        for pin in bridge._items(component.Pins):
            net = pin.Net
            pin_record = {
                "number": str(pin.Name),
                "net": str(net.Name) if net else None,
                "x": pin.GetPositionX(4) if placed else None,
                "y": pin.GetPositionY(4) if placed else None,
                "layer": pin.Layer,
                "pads": [],
                "holes": [],
            }
            if placed:
                for pad in bridge._items(pin.Pads):
                    fields = scalar_getters(pad, ("Type", "PadType", "Layer", "LayerType"))
                    names = [
                        name
                        for name, value in (constants or {}).items()
                        if any(fields.get(k) == value for k in ("Type", "PadType", "LayerType"))
                        and "pad" in name.lower()
                    ]
                    pin_record["pads"].append(
                        {
                            "layer": pad.Layer,
                            "declared_fields": fields,
                            "type_enum_names": names,
                            "geometries": [
                                bridge._geometry_record(g) for g in bridge._items(pad.Geometries)
                            ],
                        }
                    )
                for hole in bridge._items(pin.Holes):
                    fields = scalar_getters(hole, ("Plated", "IsPlated", "Plating"))
                    plated = next(
                        (
                            v
                            for k, v in fields.items()
                            if k in ("Plated", "IsPlated") and type(v) is bool
                        ),
                        None,
                    )
                    pin_record["holes"].append(
                        {
                            "drill_mm": hole.GetDrillSize(4),
                            "plated": plated,
                            "declared_fields": fields,
                        }
                    )
            row["pins"].append(pin_record)
        result["components"].append(row)
    # All pad shapes/layers are recorded separately, never inferred from the inputs.
    result["native_geometry"] = bridge._board_model(doc)
    return result


def units(bridge, app):
    info = app._oleobj_.GetTypeInfo()
    lib, _ = info.GetContainingTypeLib()
    found, docs, enum_meanings, errors = {}, [], {}, []
    for i in range(lib.GetTypeInfoCount()):
        try:
            item = lib.GetTypeInfo(i)
            attrs = item.GetTypeAttr()
            for j in range(attrs.cVars):
                desc = item.GetVarDesc(j)
                name = item.GetNames(desc[0])[0]
                if name in ("epcbUnitMM", "epcbAngleUnitDegrees"):
                    found[name] = desc[1]
                if (
                    any(word in name.lower() for word in ("pad", "layer", "plated"))
                    and type(desc[1]) is int
                ):
                    enum_meanings[name] = desc[1]
            for j in range(attrs.cFuncs):
                desc = item.GetFuncDesc(j)
                name = item.GetNames(desc[0])[0]
                if name in ("Place", "GetOrientation", "SetOrientation", "SetPosition"):
                    document = item.GetDocumentation(desc[0])
                    if document[1]:
                        docs.append({"member": name, "documentation": document[1]})
        except Exception as exc:
            errors.append({"type_index": i, "error_type": type(exc).__name__})
    require(
        found.get("epcbUnitMM") == 4 and found.get("epcbAngleUnitDegrees") == 0,
        "E_UNIT_CONTRACT",
        "Current type library did not confirm explicit mm/degree enum values",
        {"observed": found},
    )
    # Positivity must be described by authoritative type-library documentation.
    ccw = positive_ccw(docs)
    require(
        ccw,
        "E_ROTATION_CONTRACT",
        "Positive rotation convention is not established by current authoritative API "
        "documentation; placement was not attempted",
        {
            "observed_enums": found,
            "orientation_documentation": docs,
            "expected_file": "expected-geometry.json",
        },
    )
    return {
        "constants": found,
        "pad_layer_enums": enum_meanings,
        "positive_rotation": "counterclockwise",
        "source": "current native type-library enum constants and orientation documentation",
        "documentation": docs,
        "enumeration_errors": errors,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step", choices=STEPS, required=True)
    args = parser.parse_args()
    manifest_bytes = (PACK / "manifest.json").read_bytes()
    require(
        hashlib.sha256(manifest_bytes).hexdigest()
        == os.environ.get("XPEDITION_ACCEPTANCE_MANIFEST_SHA256"),
        "E_HASH",
        "Native worker requires the active sealed manifest",
    )
    manifest = json.loads(manifest_bytes)
    scratch, results = Path(manifest["scratch_root"]), Path(manifest["results_root"])
    context = json.loads((results / "run-context.json").read_text(encoding="utf-8"))
    require(
        os.getppid() == context["runner_pid"],
        "E_PARENT",
        "Native worker must be a finite child of the active explicit runner",
    )
    require(
        context["active_step"] == args.step,
        "E_STEP",
        "The requested native step is not the recorded fixed stage",
    )
    module, caller = actor()
    bridge, client, pythoncom, adapter_identity, application_identity = initialize(manifest)
    try:
        require(
            adapter_identity()["build_sha256"] == manifest["adapter_sha256"],
            "E_HASH",
            "Actual loaded adapter differs from the fixed source",
        )
        apps, observed = bind(
            manifest,
            module,
            bridge,
            client,
            application_identity,
            context,
            args.step == "preflight",
        )
        target, orphan_project = scratch / "target/QA_ROOT.prj", scratch / "orphan/QA_ORPHAN.prj"
        payload = {
            "caller": caller,
            "adapter": adapter_identity(),
            "applications": observed,
            "native_task_acceptance": "not_run",
            "license_expiry": "unknown",
            "system_configuration_changed": False,
        }
        if args.step == "preflight" or args.step == "guard":
            pass
        elif args.step == "source-library-readback":
            from xpedition_cli.library_readback import verify_definitions

            payload = verify_definitions(
                "library create",
                {
                    "project": str(scratch / "source/QA_SOURCE.prj"),
                    "file": str(Path(manifest["inputs_root"]) / "parts-L0.json"),
                },
            )
        elif args.step == "failed-package-readback":
            record = json.loads(
                Path(manifest["failed_package"]["apply_record"]["source"]).read_text(
                    encoding="utf-8"
                )
            )
            old_receipt = record["error"]["details"]["receipt"]
            prior = old_receipt["extensions"]["result"]
            path = scratch / "target/Integration/PartPkg.log"
            actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            require(
                manifest["failed_package"].get("stage") == "registration"
                or actual_hash == prior["log_sha256"],
                "E_READBACK",
                "Failed package report changed; further writes are refused",
            )
            payload = {
                "outcome": "partial",
                "expected": {"packaged": False, "exit_code": 5},
                "observed": prior or old_receipt["extensions"]["handler_error"],
                "sha256": actual_hash,
                "source": "independently reopened failed native report "
                "plus fresh schematic comparison",
                "rollback": False,
                "replay": False,
            }
        elif args.step == "schematic-read":
            require(
                observed["schematic"]["active_document"] == str(target),
                "E_IDENTITY",
                "Owned target schematic is not open",
            )
            payload["snapshot"] = bridge._designer_snapshot(apps["schematic"], {"design": "Board1"})
        elif args.step == "pcb-read":
            require(
                observed["pcb"]["active_document"] == str(scratch / "target/PCB/QA_ROOT.pcb"),
                "E_IDENTITY",
                "Owned target PCB is not open",
            )
            payload["units"] = units(bridge, apps["pcb"])
            payload["snapshot"] = pcb_getters(
                bridge, apps["pcb"], payload["units"]["pad_layer_enums"]
            )
        elif args.step in ("persistence", "release-target"):
            require(
                observed["schematic"]["active_document"] == str(target)
                and observed["pcb"]["active_document"] == str(scratch / "target/PCB/QA_ROOT.pcb"),
                "E_IDENTITY",
                "Only the two identified owned target documents may be saved/reopened",
            )
            view = apps["schematic"].ActiveDocument
            require(
                view is not None,
                "E_PERSISTENCE",
                "No owned active schematic sheet can be explicitly saved",
            )
            view.Save()
            board = bridge._licensed_document(apps["pcb"])
            board.Save()
            payload["save_acknowledgements"] = {
                "schematic": True,
                "pcb": True,
                "persistence_verified": False,
            }
            board.Close(False)
            apps["schematic"].CloseProject()
            if args.step == "persistence":
                bridge._ensure_project(apps["schematic"], target)
                bridge._open_layout_document(apps["pcb"], scratch / "target/PCB/QA_ROOT.pcb")
                payload["schematic_reopened"] = bridge._designer_snapshot(
                    apps["schematic"], {"design": "Board1"}
                )
                unit_record = units(bridge, apps["pcb"])
                payload["pcb_reopened"] = pcb_getters(
                    bridge, apps["pcb"], unit_record["pad_layer_enums"]
                )
            else:
                payload["documents_closed"] = True
            payload["application_windows_retained"] = True
        elif args.step == "orphan-baseline":
            require(
                observed["schematic"]["active_document"] == str(orphan_project),
                "E_IDENTITY",
                "Baseline writes are restricted to the owned fault project",
            )
            before = bridge._designer_snapshot(apps["schematic"], {"design": "Board1"})
            require(
                not any(row["refdes"] == "R_KEEP" for row in before["components"]),
                "E_REPLAY",
                "R_KEEP already exists; baseline cannot be replayed",
            )
            bridge._apply_designer_operation(
                apps["schematic"],
                {
                    "type": "place_component",
                    "library": "QA20261006",
                    "device_name": "QA_R0805_10K",
                    "symbol_name": "QA_RES_e7eaea6b",
                    "refdes": "R_KEEP",
                    "x": 200,
                    "y": 400,
                },
                client,
                "Board1",
                {},
            )
            apps["schematic"].ActiveDocument.Save()
            bridge._reopen_project(apps["schematic"], orphan_project)
            after = bridge._designer_snapshot(apps["schematic"], {"design": "Board1"})
            kept = [row for row in after["components"] if row["refdes"] == "R_KEEP"]
            require(
                len(kept) == 1
                and kept[0]["uid"]
                and kept[0]["x"] == 200
                and kept[0]["y"] == 400
                and kept[0]["internal_part_no"] == "QA_R0805_10K",
                "E_READBACK",
                "R_KEEP differs after save/reopen",
            )
            payload.update(
                expected={"refdes": "R_KEEP", "x": 200, "y": 400},
                observed=kept[0],
                before=before,
                after=after,
                source="real placement followed by save/close/open and native getters",
                coordinate_unit="native Designer sheet units; physical scaling unproven",
            )
        elif args.step == "orphan-fault":
            require(
                observed["schematic"]["active_document"] == str(orphan_project),
                "E_IDENTITY",
                "Orphan injection is restricted to its separately cloned owned project",
            )
            before = bridge._designer_snapshot(apps["schematic"], {"design": "Board1"})
            operation = dict(manifest["orphan_operation"])
            trace = {"real_add_calls": 0, "setter_faults": 0, "fault_injected": True}

            class ComponentProxy:
                def __init__(self, obj):
                    object.__setattr__(self, "real", obj)

                def __getattr__(self, name):
                    return getattr(self.real, name)

                def __setattr__(self, name, value):
                    if name == "Refdes":
                        trace["setter_faults"] += 1
                        raise RuntimeError(
                            "QA fault_injected immediately before Refdes assignment; "
                            "real native placement already returned"
                        )
                    raise Stop("E_FAULT_SCOPE", "No other component setter is allowed")

            class BlockProxy:
                def __init__(self, obj):
                    self.real = obj

                def __getattr__(self, name):
                    return getattr(self.real, name)

                def AddPartInstance(self, library, device, symbol, x, y):
                    require(
                        (library, device, symbol, x, y)
                        == (
                            operation["library"],
                            operation["device_name"],
                            operation["symbol_name"],
                            500,
                            400,
                        ),
                        "E_FAULT_SCOPE",
                        "Placement arguments differ from the frozen fault case",
                    )
                    trace["real_add_calls"] += 1
                    require(
                        trace["real_add_calls"] == 1, "E_REPLAY", "A second placement is forbidden"
                    )
                    native = self.real.AddPartInstance(library, device, symbol, x, y)
                    require(
                        native is not None,
                        "E_OUTCOME_UNKNOWN",
                        "Real AddPartInstance did not return an object",
                    )
                    return ComponentProxy(native)

            class ViewProxy:
                def __init__(self, obj):
                    self.real = obj

                @property
                def Block(self):
                    return BlockProxy(self.real.Block)

            class AppProxy:
                def __init__(self, obj):
                    self.real = obj

                @property
                def ActiveView(self):
                    return ViewProxy(self.real.ActiveView)

            observed_error = None
            try:
                bridge._apply_designer_operation(
                    AppProxy(apps["schematic"]), operation, client, "Board1", {}
                )
            except bridge.AdapterError as exc:
                observed_error = {"code": exc.code, "message": str(exc), "details": exc.details}
            require(
                observed_error is not None,
                "E_FAULT_SCOPE",
                "The injected setter failure was not reported by the candidate adapter",
            )
            after = bridge._designer_snapshot(apps["schematic"], {"design": "Board1"})
            payload.update(
                before=before,
                after=after,
                adapter_error=observed_error,
                trace=trace,
                dirty_after=dirty(apps["schematic"]),
                mutation_attempted=True,
                application_windows_retained=True,
                save_after_fault=False,
                cleanup_attempted=False,
            )
        print(
            json.dumps(
                {"ok": True, "utc": utc(), "step": args.step, "data": payload}, ensure_ascii=False
            )
        )
        return 0
    finally:
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "utc": utc(),
                    "error": {
                        "code": getattr(exc, "code", "E_NATIVE"),
                        "message": str(exc),
                        "details": getattr(exc, "details", {}),
                    },
                    "application_windows_retained": True,
                    "automatic_retry": False,
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(2) from None
