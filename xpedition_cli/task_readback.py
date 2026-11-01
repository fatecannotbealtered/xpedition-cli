"""Scoped native/file verification for packaging, board creation and placement."""

from __future__ import annotations

import copy
import hashlib
import math
from pathlib import Path

from . import project_file as P


def _component_index(rows):
    """Index complete component identities without changing native observations."""
    if not isinstance(rows, list):
        return None
    indexed, uids = {}, set()
    for row in rows:
        if not isinstance(row, dict):
            return None
        refdes, uid = row.get("refdes"), row.get("uid")
        if (
            not isinstance(refdes, str)
            or not refdes.strip()
            or refdes in indexed
            or type(uid) not in (str, int)
            or not str(uid).strip()
            or str(uid) in uids
        ):
            return None
        for key in ("identity_observation_coverage", "pin_observation_coverage"):
            if key in row and (
                not isinstance(row[key], dict)
                or row[key].get("complete") is not True
                or row[key].get("errors")
            ):
                return None
        indexed[refdes] = row
        uids.add(str(uid))
    return indexed


def _component_pins(components):
    """Keep every component/pin identity; malformed or duplicate pins fail closed."""
    if components is None:
        return None
    indexed = {}
    for refdes, row in components.items():
        pins = row.get("pins")
        if not isinstance(pins, list):
            return None
        for pin in pins:
            if not isinstance(pin, dict):
                return None
            number, net = pin.get("number"), pin.get("net")
            if (
                not isinstance(number, str)
                or not number.strip()
                or (refdes, number) in indexed
                or "net" not in pin
                or (net is not None and (not isinstance(net, str) or not net.strip()))
            ):
                return None
            indexed[refdes, number] = pin
    return indexed


def _update_connectivity_matches(expected, actual):
    if expected is None or actual is None or expected.keys() != actual.keys():
        return False
    members = {}
    for pin in expected.values():
        net = pin["net"]
        if isinstance(net, str):
            members[net] = members.get(net, 0) + 1
    for identity, pin in expected.items():
        net, observed = pin["net"], actual[identity]["net"]
        if net == observed:
            continue
        # Forward Annotation assigns unconnected Layout pins to literal (Net0).
        # Only explicit NCs and native unnamed nets with one component pin qualify.
        # Count all component pins on the source net, regardless of their flags.
        unconnected = (
            net is None and pin.get("no_connect") is True and pin.get("net_unnamed") is False
        ) or (
            isinstance(net, str)
            and pin.get("net_unnamed") is True
            and pin.get("no_connect") is False
            and members[net] == 1
        )
        if observed != "(Net0)" or not unconnected:
            return False
    return True


def _placement_protected_fields(row):
    """A selected move may change its pose and absolute pin coordinates only."""
    protected = {k: v for k, v in row.items() if k not in {"x", "y", "rotation", "placed"}}
    if "pins" in protected:
        pins = protected["pins"]
        if not isinstance(pins, list) or any(not isinstance(pin, dict) for pin in pins):
            return None
        protected["pins"] = [{k: v for k, v in pin.items() if k not in {"x", "y"}} for pin in pins]
    return protected


def _exclude_owned_via_holes(model, nets):
    """Remove only selected via holes proven by native UID/net and drill geometry.

    Supported scope is one centered circular plated drill per observed via.
    Component, mechanical, foreign and unassociated holes stay protected.
    """
    selected = [v for v in model.get("vias", []) if v.get("net") in nets]
    owned = [h for h in model.get("via_holes", []) if h.get("net") in nets]
    uids = [v.get("uid") for v in selected]
    if any(uid is None for uid in uids) or len(set(uids)) != len(uids):
        return False
    if len(owned) != len(selected) or {h.get("uid") for h in owned} != set(uids):
        return False
    remaining = list(model.get("holes", []))
    for via in selected:
        records = [h for h in owned if h.get("uid") == via["uid"]]
        if len(records) != 1 or records[0].get("net") != via["net"]:
            return False
        actual = records[0].get("geometry", [])
        center = via.get("center_mm", [])
        drill = via.get("drill_mm")
        span = via.get("span", [])
        if (
            not via.get("padstack")
            or via.get("shape_type") != 1
            or via.get("plated") is not True
            or len(center) != 2
            or len(actual) != 3
            or type(drill) not in (float, int)
            or drill <= 0
            or len(span) != 2
            or not all(type(v) is int and v > 0 for v in span)
            or not all(
                type(v) in (float, int) and math.isfinite(v) for v in actual + center + [drill]
            )
            or any(abs(a - b) > 0.001 for a, b in zip(actual, [*center, drill], strict=True))
        ):
            return False
        # Exact removal of one native observation preserves duplicate/unowned holes.
        if actual not in remaining:
            return False
        remaining.remove(actual)
    model["holes"] = remaining
    model["via_holes"] = [h for h in model.get("via_holes", []) if h.get("net") not in nets]
    return True


def _board_edit_readback(command, options, result, before, after, plan):
    """Verify measured changes/protection and reopening; routing completion is separate."""
    if command == "pcb silkscreen labels" and options.get("file"):
        from .board_labels import compare, persistence_evidence, translated_geometry

        old = before["extensions"]["facts"].get("cell_refresh", {}).get("geometry")
        new = after["extensions"]["facts"].get("cell_refresh", {}).get("geometry")
        bound = plan["extensions"]["legacy_preview"].get("label_moves", {})
        old_state = before["extensions"]["facts"].get("cell_refresh", {}).get("label_state")
        new_state = after["extensions"]["facts"].get("cell_refresh", {}).get("label_state")
        moves = bound.get("request", {}).get("moves", [])
        try:
            expected = (
                translated_geometry(old, moves, bound.get("before", {}).get("texts", []))
                if old and moves
                else None
            )
        except (ValueError, KeyError, TypeError):
            expected = None
        checks = [
            *persistence_evidence(result, bound, new_state)["checks"],
            {
                "name": "fresh independent full text inventory and Format protection",
                "passed": bool(
                    old_state
                    and new_state
                    and old_state == bound.get("before")
                    and new_state.get("geometry") == new
                    and compare(bound, new_state)["valid"]
                    and new_state == result.get("state_reopened")
                ),
            },
            {
                "name": "fresh geometry exactly matches UID-bound translations",
                "passed": bool(expected and new == expected == result.get("geometry_after")),
            },
            {
                "name": "independent original baseline matches bound native plan",
                "passed": bool(old and old == bound.get("before", {}).get("geometry")),
            },
            {
                "name": "saved and reopened with complete native text protection",
                "passed": all(result.get(k) is True for k in ("saved", "persistence_verified"))
                and isinstance(result.get("verification"), dict)
                and result["verification"].get("valid") is True,
            },
        ]
        return {
            "valid": all(r["passed"] for r in checks),
            "checks": checks,
            "engineering_complete": False,
            "scope": "UID-bound silk position translations, unchanged captured "
            "materials/identities and save/reopen",
            "source": "fresh independent geometry after normal save/close/reopen",
            "not_checked": [
                "native DRC",
                "font collision beyond observed envelopes",
                "manufacturing",
            ],
        }
    old = before["extensions"]["facts"].get("cell_refresh", {}).get("geometry")
    new = after["extensions"]["facts"].get("cell_refresh", {}).get("geometry")
    checks = []

    def check(name, valid):
        checks.append({"name": name, "passed": bool(valid)})

    check("fresh independent geometry", old and new and new == result.get("geometry_after"))
    check(
        "saved and reopened",
        result.get("saved") is True and result.get("persistence_verified") is True,
    )
    if old and new:
        left, right = copy.deepcopy(old), copy.deepcopy(new)
        if command == "pcb routing auto":
            nets = {n.strip() for n in str(options.get("nets") or "").split(",") if n.strip()}
            if not nets:
                nets = {p.get("net") for c in old["components"] for p in c["pins"] if p.get("net")}
            layers = {int(v) for v in str(options.get("layers") or "").split(",") if v.strip()}
            check(
                "selected via holes have native ownership and matching drill geometry",
                all(_exclude_owned_via_holes(state["model"], nets) for state in (left, right)),
            )
            for key in ("traces", "vias", "unrouted"):
                for state in (left, right):
                    state["model"][key] = [
                        v for v in (state["model"].get(key) or []) if v.get("net") not in nets
                    ]
            complete = True
            for state in (left, right):
                observed = state["model"].get("connectivity") or {}
                rows = observed.get("nets") or []
                identities = [row.get("net") for row in rows]
                complete = complete and (
                    observed.get("complete") is True
                    and len(identities) == len(set(identities))
                    and nets <= set(identities)
                    and all(
                        isinstance(row.get("uid"), str)
                        and bool(row["uid"])
                        and type(row.get("routed")) is bool
                        and type(row.get("opens")) is int
                        and row["opens"] >= 0
                        for row in rows
                    )
                )
                if observed:
                    observed["nets"] = [row for row in rows if row.get("net") not in nets]
            check("complete native connectivity observation", complete)
            if complete:
                check(
                    "selected native net identities protected",
                    {
                        r["net"]: r["uid"]
                        for r in old["model"]["connectivity"]["nets"]
                        if r["net"] in nets
                    }
                    == {
                        r["net"]: r["uid"]
                        for r in new["model"]["connectivity"]["nets"]
                        if r["net"] in nets
                    },
                )
            selected_traces = [v for v in new["model"].get("traces", []) if v.get("net") in nets]
            check(
                "actual trace layers",
                all(not layers or v.get("layer") in layers for v in selected_traces),
            )
            check(
                "actual trace geometry",
                all(v.get("width", 0) > 0 and v.get("path") for v in selected_traces),
            )
            completion = bool(complete) and all(
                row["routed"] and row["opens"] == 0
                for row in new["model"]["connectivity"]["nets"]
                if row["net"] in nets
            )
        else:
            requested = plan["extensions"]["legacy_preview"].get("labels", [])
            refs = {row["refdes"] for row in requested}
            for row in requested:
                matches = [
                    v
                    for v in new["model"].get("texts", [])
                    if v.get("kind") == "refdes" and v.get("refdes") == row["refdes"]
                ]
                check(
                    "label " + row["refdes"],
                    len(matches) == 1
                    and all(
                        abs(matches[0].get(key, -1e9) - row[planned]) <= 0.001
                        for key, planned in (
                            ("x", "x"),
                            ("y", "y"),
                            ("height", "text_height"),
                            ("pen", "pen"),
                        )
                    ),
                )
            for state in (left, right):
                state["model"]["texts"] = [
                    {
                        k: v
                        for k, v in row.items()
                        if k not in {"x", "y", "height", "pen", "extents", "strokes"}
                    }
                    if row.get("kind") == "refdes" and row.get("refdes") in refs
                    else row
                    for row in state["model"].get("texts", [])
                ]
            completion = not result.get("unplaced")
        check("protected board and objects", left == right)
    else:
        completion = False
    return {
        "valid": all(c["passed"] for c in checks),
        "checks": checks,
        "engineering_complete": bool(completion and all(c["passed"] for c in checks)),
        "source": "independent native geometry after normal save/close/reopen",
        "scope": "selected changes and unchanged captured board geometry",
        "not_checked": [
            "native DRC",
            "full physical connectivity proof",
            "unobserved object types",
        ],
    }


def verify_task(command, options, result, before, after, plan=None):
    if command == "pcb routing trace" and options.get("mode") == "component-connect":
        from .board_component_connect import _inputs, _source_inputs, verify_receipt
        from .board_routepass import _dependency_identity, design_state

        try:
            bound = plan["extensions"]["legacy_preview"]["trace"]
            old = before["extensions"]["facts"]["component_state"]
            fresh = after["extensions"]["facts"]["component_state"]
            proof = verify_receipt(result, bound, fresh)
            identity_ok = old["identity"] == fresh["identity"] == bound["identity"]
            baseline_ok = design_state(old["state"]) == design_state(bound["before"])
            disk = _inputs(
                {
                    "project": bound["project"],
                    "source_backup": bound["input_files"]["source_backup"]["path"],
                },
                Path(bound["board"]),
                bound["request"],
            )
            expected_disk = bound["input_files"] if bound["no_op"] else result["reopened_inputs"]
            disk_ok = _dependency_identity(disk) == _dependency_identity(expected_disk)
            independent_source = {}
            _source_inputs(bound, independent_source, "sdk_after_observation")
            return {
                **proof,
                "valid": proof.get("valid") is True and identity_ok and baseline_ok and disk_ok,
                "independent_owner_matches": identity_ok,
                "independent_baseline_matches": baseline_ok,
                "independent_disk_matches": disk_ok,
                "independent_disk_inputs": disk,
                "independent_source_inputs": independent_source,
                "scope": "isolated two-component native transaction, complete material and object "
                "protection, strict saved/reopened state; native rule engine acknowledgement",
                "not_checked": ["complete effective-rule parser", "manufacturing", "thermal/tie"],
            }
        except (KeyError, TypeError, ValueError, OSError):
            return {
                "valid": False,
                "scope": "isolated component connection",
                "reason": "incomplete independent evidence",
            }
    if command == "pcb routing auto" and options.get("mode") == "bounded-general":
        from .board_general import material_request, verify_receipt
        from .board_routepass import _states_equal

        try:
            bound = plan["extensions"]["legacy_preview"]["general_route"]
            before_observation = before["extensions"]["facts"]["general_route_state"]
            after_observation = after["extensions"]["facts"]["general_route_state"]
            request = material_request(bound["request"])
            proof = verify_receipt(result, bound, after_observation["state"])
            checks = [
                {
                    "name": "complete General Route lifecycle receipt independently recomputed",
                    "passed": proof.get("valid") is True,
                },
                {
                    "name": "independent full baseline matches frozen General Route preview",
                    "passed": _states_equal(before_observation["state"], bound["before"], request),
                },
                {
                    "name": "independent pre/post identities match the bound application and board",
                    "passed": before_observation["identity"]
                    == after_observation["identity"]
                    == bound["identity"]
                    and before_observation["board"] == after_observation["board"] == result["pcb"],
                },
            ]
            return {
                **proof,
                "valid": all(row["passed"] for row in checks),
                "checks": checks,
                "scope": "one selected General Route command, declared GND changes, complete "
                "material-group merge, strict foreign protection and save/reopen",
                "source": "fresh authenticated material getters before dispatch and after reopen",
                "not_checked": [
                    "complete effective-rule parser",
                    "DRC",
                    "manufacturing",
                    "unobservable native rule cache version",
                ],
            }
        except (KeyError, TypeError, ValueError, IndexError):
            return {
                "valid": False,
                "scope": "bounded selected General Route",
                "checks": [
                    {"name": "complete independent General Route evidence", "passed": False}
                ],
            }
    if command == "pcb routing auto" and options.get("mode") == "bounded-native":
        from .board_routepass import design_state, verify_receipt

        try:
            bound = plan["extensions"]["legacy_preview"]["routepass"]
            before_state = before["extensions"]["facts"]["routepass_state"]["state"]
            after_state = after["extensions"]["facts"]["routepass_state"]["state"]
            proof = verify_receipt(result, bound, after_state)
            check = {
                "name": "independent complete pre-dispatch state matches frozen preview",
                "passed": design_state(before_state) == design_state(bound["before"]),
            }
            proof["checks"].append(check)
            proof["valid"] = proof["valid"] and check["passed"]
            return {
                **proof,
                "scope": "one native no-via pass, complete created copper chain, "
                "captured protected objects and strict save/reopen",
                "source": "fresh independent native getters after normal authenticated reopen",
                "not_checked": [
                    "complete effective-rule parser",
                    "DRC",
                    "manufacturing",
                    "unobservable native rule cache version",
                ],
            }
        except (KeyError, TypeError, ValueError):
            return {
                "valid": False,
                "scope": "bounded native RoutePass",
                "checks": [{"name": "complete independent routepass evidence", "passed": False}],
            }
    if command == "pcb copper pour":
        from .board_copper import verify

        return verify(result, before, after, plan)
    if command == "pcb routing unroute":
        from .board_unroute import verify

        return verify(result, before, after, plan)
    if command in {"pcb routing auto", "pcb silkscreen labels"}:
        return _board_edit_readback(command, options, result, before, after, plan)
    if command == "pcb update":
        if options.get("refresh_cells"):
            from .cell_refresh import compare

            bound = plan["extensions"]["legacy_preview"]["cell_refresh"]
            state = after["extensions"]["facts"].get("cell_refresh", {}).get("geometry")
            local = (result.get("definitions_after") or bound["definitions"])["local"]
            catalog = (result.get("definitions_after") or bound["definitions"]).get("local_catalog")
            comparison = compare(bound, state, local, catalog) if state else {"valid": False}
            comparison["valid"] = bool(
                comparison["valid"]
                and (result.get("persistence_verified") or result.get("no_change"))
            )
            return {
                **comparison,
                "scope": "bound central/local definitions, target copper/mask/paste dimensions "
                "and centers, protected identities/networks/poses and captured board geometry",
                "source": "fresh independent native getters after saved/reopened refresh, "
                "native read-only database exports",
                "not_checked": [
                    "native DRC",
                    "unsupported geometry types",
                    "3D",
                    "routing/manufacturing acceptance",
                ],
            }
        expected = plan["extensions"]["legacy_preview"].get("verification_input") or {}
        actual = after["extensions"]["facts"].get("native") or {}
        expected_rows = _component_index(expected.get("components"))
        actual_rows = _component_index(actual.get("components"))
        expected_pins = _component_pins(expected_rows)
        actual_pins = _component_pins(actual_rows)

        def connectivity(pins):
            return {
                f"{refdes}.{number}": pin["net"] for (refdes, number), pin in (pins or {}).items()
            }

        expected_parts = {
            refdes: row.get("internal_part_no") for refdes, row in (expected_rows or {}).items()
        }
        actual_parts = {
            refdes: row.get("part_number") for refdes, row in (actual_rows or {}).items()
        }
        valid = bool(
            expected_rows
            and actual_rows is not None
            and result.get("persistence_verified") is True
            and result.get("outcome") in {"annotated", "annotated_on_open", "in_synch"}
            and not result.get("errors")
            and all(
                isinstance(number, str) and number.strip() for number in expected_parts.values()
            )
            and expected_parts == actual_parts
            and _update_connectivity_matches(expected_pins, actual_pins)
        )
        return {
            "valid": valid,
            "expected": {"parts": expected_parts, "pins": connectivity(expected_pins)},
            "observed": {"parts": actual_parts, "pins": connectivity(actual_pins)},
            "source": "bound source schematic and fresh PCB pin getters after board save/reopen",
            "scope": "schematic/PCB part numbers and pin-to-net correspondence",
            "not_checked": ["footprint geometry synchronization", "routing", "DRC"],
        }
    if command == "schematic package":
        path = Path(options["project"]).resolve().parent / "Integration/PartPkg.log"
        observed = {"path": str(path), "exists": path.is_file()}
        if path.is_file():
            observed.update(
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                mtime_ns=path.stat().st_mtime_ns,
                size=path.stat().st_size,
            )
        valid = bool(
            result.get("packaged") is True
            and result.get("exit_code") == 0
            and not result.get("errors")
            and observed.get("size", 0) > 0
            and observed.get("sha256") == result.get("log_sha256")
            and observed.get("mtime_ns", 0) >= result.get("started_ns", 2**63)
        )
        return {
            "valid": valid,
            "expected": {"new_report": True, "errors": 0},
            "observed": observed,
            "source": "completed native packager process and freshly reopened report bytes",
            "scope": "packager termination and report generation",
            "not_checked": ["PCB instance correspondence", "CAM/DFM"],
        }
    if command == "pcb create":
        project = Path(options["project"]).resolve()
        design = P.board_design(
            P.designs(project.read_text(encoding="utf-8")), options.get("design")
        )
        expected = project.parent / "PCB" / ((options.get("name") or project.stem) + ".pcb")
        native = result.get("native_readback") or {}
        document = (
            after["extensions"]["facts"]
            .get("secondary_applications", {})
            .get("pcb", {})
            .get("document", {})
        )
        valid = bool(
            design
            and design.pcb_path
            and expected.is_file()
            and P.local_path(project.parent, design.pcb_path).resolve() == expected
            and result.get("persistence_verified") is True
            and native.get("source") == "native_board_getters_after_save_close_open"
            and Path(str(native.get("path") or "")).resolve() == expected
            and document.get("open") is True
            and Path(str(document.get("path") or "")).resolve() == expected
            and type(native.get("layer_count")) is int
            and native["layer_count"] >= 2
        )
        return {
            "valid": valid,
            "expected": {"path": str(expected)},
            "observed": {"native": native, "document": document},
            "source": "native board getters after save/reopen and independent project linkage",
            "scope": "new board linkage, identity and readable layer count",
            "not_checked": ["template object equivalence", "schematic synchronization"],
        }
    if command == "pcb placement move" and options.get("file"):
        from .errors import CLIError
        from .placement import (
            batch_reopen_comparison,
            canonical_state,
            validate_batch_preview,
            verify_batch_state,
        )

        try:
            bound = plan["extensions"]["legacy_preview"]
            validate_batch_preview(bound, bound["request"])
            old = before["extensions"]["facts"]["cell_refresh"]["geometry"]
            new = after["extensions"]["facts"]["cell_refresh"]["geometry"]
            no_change = bound["summary"]["changed_count"] == 0
            lifecycle = result["lifecycle"]
            transition = None
            if no_change:
                proof = verify_batch_state(bound, new)
                persistence = (
                    result.get("no_change") is True
                    and result.get("write_attempted") is False
                    and result.get("saved") is False
                    and result.get("persistence_verified") is False
                    and all(
                        type(lifecycle.get(key)) is int and lifecycle[key] == 0
                        for key in ("save_count", "close_count", "open_count")
                    )
                )
            else:
                # Recompute the native post-edit/reopen transition from its raw
                # frames, then independently bind this fresh SDK after-frame to
                # the same exception.  Native "verified" alone is insufficient.
                edit = result["geometry_after_edit"]
                edit_proof = verify_batch_state(bound, edit)
                transition = batch_reopen_comparison(edit, result["geometry_after"], lifecycle)
                reference = transition["reference_equivalence"]
                proof = verify_batch_state(bound, new, reopen_proof=reference)
                if reference is not None:
                    from .native_process import process_identity, same_identity

                    actual_process = process_identity("pcb", lifecycle["pid_after"])
                    if not same_identity(actual_process, reference["lifecycle"]["process_after"]):
                        raise ValueError("Independent native process generation differs")
                if (
                    not edit_proof["valid"]
                    or not transition["valid"]
                    or result.get("reopen_comparison") != transition
                ):
                    raise ValueError("Complete reopened native material proof does not recompute")
                persistence = (
                    result.get("saved") is True
                    and result.get("persistence_verified") is True
                    and all(
                        type(lifecycle.get(key)) is int and lifecycle[key] == 1
                        for key in ("save_count", "close_count", "open_count")
                    )
                    and all(
                        lifecycle.get(key) is True
                        for key in (
                            "save_acknowledged",
                            "save_reply_received",
                            "close_acknowledged",
                            "close_reply_received",
                            "closed_observed",
                            "open_reply_received",
                            "reopen_verified",
                        )
                    )
                    and lifecycle.get("save_returned") is True
                    and lifecycle.get("close_returned") is True
                    and lifecycle.get("save_return_type") == "bool"
                    and lifecycle.get("close_return_type") == "bool"
                    and type(lifecycle.get("pid_before")) is int
                    and lifecycle["pid_before"] > 0
                    and lifecycle["pid_before"] == lifecycle.get("pid_after")
                    and lifecycle["pid_before"]
                    == before["extensions"]["facts"]["application"]["pid"]
                    and lifecycle["pid_after"] == after["extensions"]["facts"]["application"]["pid"]
                    and Path(lifecycle["board"]).resolve()
                    == Path(lifecycle["reopened_path"]).resolve()
                    == Path(result["pcb"]).resolve()
                )
            valid = (
                proof["valid"]
                and persistence
                and result.get("outcome") == "complete"
                and result.get("verification", {}).get("valid") is True
                and canonical_state(old) == canonical_state(bound["protection_before"])
                and canonical_state(new) == canonical_state(result["geometry_after"])
            )
            return {
                **proof,
                "valid": bool(valid),
                "persistence_verified": not no_change and bool(valid),
                "no_change": no_change,
                "source": (
                    "independent complete native material getters; no save or reopen dispatched"
                    if no_change
                    else "independent complete native material getters and strict "
                    "one Save/Close/Open lifecycle"
                ),
                "not_checked": ["native DRC", "atomic multi-object transaction", "manufacturing"]
                + proof.get("not_checked", []),
                **({"reopen_comparison": transition} if transition is not None else {}),
            }
        except (CLIError, KeyError, TypeError, ValueError, OSError):
            return {
                "valid": False,
                "scope": "complete batch placement material and lifecycle verification",
            }
    if command == "pcb placement move" and options.get("to"):
        expected_xy = [float(n) for n in str(options["to"]).split(",")]
        rows = after["extensions"]["facts"].get("native", {}).get("components", [])
        before_rows = before["extensions"]["facts"].get("native", {}).get("components", [])
        actual_index = _component_index(rows)
        before_index = _component_index(before_rows)
        row = (actual_index or {}).get(options["refdes"], {})
        original = (before_index or {}).get(options["refdes"], {})

        expected_rotation = (
            float(options["rotate"]) % 360
            if options.get("rotate") is not None
            else result.get("target", {}).get("rotation")
        )
        valid = bool(
            result.get("persistence_verified") is True
            and row.get("placed") is True
            and actual_index is not None
            and before_index is not None
            and {ref: str(item["uid"]) for ref, item in actual_index.items()}
            == {ref: str(item["uid"]) for ref, item in before_index.items()}
            and {ref: item for ref, item in actual_index.items() if ref != options["refdes"]}
            == {ref: item for ref, item in before_index.items() if ref != options["refdes"]}
            and _placement_protected_fields(row) is not None
            and _placement_protected_fields(row) == _placement_protected_fields(original)
            and all(
                type(row.get(axis)) in {int, float} and abs(row[axis] - wanted) <= 0.001
                for axis, wanted in zip(("x", "y"), expected_xy, strict=True)
            )
            and type(row.get("rotation")) in {int, float}
            and expected_rotation is not None
            and abs((row["rotation"] - expected_rotation + 180) % 360 - 180) <= 0.001
        )
        return {
            "valid": valid,
            "expected": {"origin_mm": expected_xy, "rotation_deg": expected_rotation},
            "observed": row,
            "units": "mm/degrees",
            "tolerance": 0.001,
            "source": "fresh component getters after board save/close/open",
            "scope": "requested instance origin, orientation, placement and preserved instance set",
            "not_checked": ["pad/drill geometry", "DRC", "bottom side"],
        }
    return {"valid": False, "scope": "unsupported native task verification"}
