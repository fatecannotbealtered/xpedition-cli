"""Bound selected-net removal and independent persistence evidence.

The verified subset is two separated physical pins per selected net, without
planes. This deliberately does not infer connectivity for arbitrary copper.
"""

from __future__ import annotations

import copy
from collections import Counter

from .cell_refresh import _canonical, active, capture, digest


def key(row):
    return row["kind"], row["uid"]


def _collection(owner, name, a):
    value = getattr(owner, name)
    if callable(value) and not hasattr(value, "Count"):
        value = value()
    return list(a._items(value, strict=True))


def observe(doc, a, geometry=None, *, references=False):
    native_unit = int(doc.CurrentUnit)
    if native_unit != 2:
        raise a.AdapterError(
            "E_UNSUPPORTED",
            "Unroute geometry requires native CurrentUnit=mils",
            {"native_unit": native_unit, "supported_native_unit": 2},
        )
    geometry = capture(doc, a, strict=True) if geometry is None else geometry
    objects, refs = [], {}
    net_ids = {r["net"]: r["uid"] for r in geometry["model"]["connectivity"]["nets"]}
    captured_traces = geometry["model"]["traces"]
    captured_by_uid = {}
    for row in captured_traces:
        uid = row.get("uid")
        if type(uid) is not str or not uid.strip() or uid in captured_by_uid:
            raise a.AdapterError("E_CONFLICT", "Routing changed: capture trace UID is incomplete")
        captured_by_uid[uid] = row
    for kind, name in (("trace", "Traces"), ("via", "Vias")):
        for item in _collection(doc, name, a):
            raw_uid = item.UniqueId
            if type(raw_uid) not in (str, int) or not str(raw_uid).strip():
                raise a.AdapterError("E_NATIVE_FAILURE", "Routing object UID is unavailable")
            uid, net, net_uid = str(raw_uid), str(item.Net.Name), str(item.Net.UniqueId)
            if not uid or not net or net_ids.get(net) != net_uid or (kind, uid) in refs:
                raise a.AdapterError("E_NATIVE_FAILURE", "Routing object identity is incomplete")
            if kind == "trace":
                # Match capture's existing precision. Native mil-to-mm floats
                # otherwise differ from the same contour in the strict snapshot.
                shape = _canonical(
                    a._geometry_record(
                        item.Geometry,
                        layer=int(item.Layer),
                        width=float(item.Geometry.LineWidth) * a.TH_TO_MM,
                        net=net,
                        kind=kind,
                    )
                )
                captured = captured_by_uid.get(uid)
                raw_shape = {key: value for key, value in shape.items() if key != "uid"}
                if captured is None or raw_shape != {
                    key: value for key, value in captured.items() if key != "uid"
                }:
                    raise a.AdapterError(
                        "E_CONFLICT", "Routing changed: native trace UID/material binding differs"
                    )
                shape = {**raw_shape, "uid": uid}
            else:
                matches = [r for r in geometry["model"]["vias"] if r.get("uid") == uid]
                if len(matches) != 1:
                    raise a.AdapterError(
                        "E_UNSUPPORTED", "Native via geometry identity is incomplete"
                    )
                shape = matches[0]
            if not shape:
                raise a.AdapterError("E_UNSUPPORTED", "Routing object geometry is unavailable")
            row = {"kind": kind, "uid": uid, "net": net, "geometry": shape}
            objects.append(row)
            refs[key(row)] = item
    for kind in ("trace", "via"):
        if (
            kind == "trace"
            and {row["uid"] for row in objects if row["kind"] == "trace"} != captured_by_uid.keys()
        ):
            raise a.AdapterError(
                "E_CONFLICT", "Routing changed: native trace UID inventory differs"
            )
        if Counter(digest(r["geometry"]) for r in objects if r["kind"] == kind) != Counter(
            digest(r) for r in geometry["model"][kind + "s"]
        ):
            raise a.AdapterError("E_CONFLICT", "Routing changed during identity observation")
    state = {"geometry": geometry, "routing_objects": sorted(objects, key=key)}
    return (state, refs) if references else state


def _separated_pins(geometry, net):
    pins = [p for c in geometry["components"] for p in c.get("geometry", []) if p["net"] == net]
    if len(pins) != 2 or pins[0]["center_mm"] == pins[1]["center_mm"]:
        return False
    shapes = []
    for pin in pins:
        rows = [
            (pad["layer"], shape.get("bounds_mm"))
            for pad in pin["copper"]
            for shape in pad["geometry"]
        ]
        if not rows or any(not b or len(b) != 4 for _, b in rows):
            return False
        shapes.append(rows)
    # Disjoint bounds suffice to prove separated pads; overlapping bounds are
    # conservatively unsupported, never declared electrically disconnected.
    return all(
        la != lb or a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1]
        for la, a in shapes[0]
        for lb, b in shapes[1]
    )


def bind(state, nets, a):
    from .task_readback import _exclude_owned_via_holes

    geometry = state["geometry"]
    model = geometry["model"]
    observed = model["connectivity"]
    if observed.get("complete") is not True or any(
        not isinstance(r.get("uid"), str)
        or not r["uid"]
        or type(r.get("opens")) is not int
        or r["opens"] < 0
        or type(r.get("routed")) is not bool
        for r in observed.get("nets", [])
    ):
        raise a.AdapterError(
            "E_NATIVE_FAILURE", "Connectivity is incomplete", {"write_attempted": False}
        )
    rows = {r["net"]: r for r in observed["nets"]}
    if len(rows) != len(observed["nets"]) or not nets or not set(nets) <= rows.keys():
        raise a.AdapterError(
            "E_NOT_FOUND",
            "Selected nets must have unique native identities",
            {"write_attempted": False},
        )
    conductors = [r for r in geometry.get("stackup", []) if r.get("kind") == "conductor"]
    if (
        not conductors
        or len(conductors) != model.get("layers")
        or any(r.get("usage") != "signal" for r in conductors)
        or model.get("planes")
        or any(not _separated_pins(geometry, n) for n in nets)
    ):
        raise a.AdapterError(
            "E_UNSUPPORTED",
            "Verified unroute requires two separated physical pins per net and no planes",
            {
                "write_attempted": False,
                "supported_scope": "whole two-terminal nets; separated pins, no planes",
            },
        )
    if not _exclude_owned_via_holes(copy.deepcopy(model), set(nets)):
        raise a.AdapterError(
            "E_UNSUPPORTED",
            "Selected via holes lack supported native ownership",
            {"write_attempted": False},
        )
    selected = [r for r in state["routing_objects"] if r["net"] in nets]
    result = {
        "before": state,
        "nets": nets,
        "selected_objects": selected,
        "expected_connectivity": [{**rows[n], "opens": 1, "routed": False} for n in nets],
    }
    result["fingerprint"] = digest(result)
    return result


def compare(bound, observed):
    """Compare object identities and full protected geometry, not deletion counts."""
    before = bound["before"]
    old = {key(r): r for r in before["routing_objects"]}
    new = {key(r): r for r in observed["routing_objects"]}
    selected = {key(r): r for r in bound["selected_objects"]}
    removed = old.keys() - new.keys()
    checks = []

    def check(name, value, *, completion=False):
        checks.append(
            {
                "name": name,
                "passed": bool(value),
                "category": "completion" if completion else "protection",
            }
        )

    check(
        "routing identity inventory complete",
        len(old) == len(before["routing_objects"]) and len(new) == len(observed["routing_objects"]),
    )
    check(
        "only authorized native objects removed",
        removed <= selected.keys() and new.keys() <= old.keys(),
    )
    check(
        "remaining routing identities and geometry unchanged",
        all(old.get(k) == r for k, r in new.items()),
    )
    check("all requested objects removed", removed == selected.keys(), completion=True)
    expected = copy.deepcopy(before["geometry"])
    for k in removed & selected.keys():
        row = selected[k]
        expected["model"][row["kind"] + "s"].remove(row["geometry"])
        if row["kind"] == "via":
            holes = [h for h in expected["model"]["via_holes"] if h["uid"] == row["uid"]]
            for h in holes:
                expected["model"]["holes"].remove(h["geometry"])
                expected["model"]["via_holes"].remove(h)
    actual = copy.deepcopy(observed["geometry"])
    old_conn = expected["model"].pop("connectivity")
    new_conn = actual["model"].pop("connectivity")
    expected["model"].pop("unrouted")
    actual["model"].pop("unrouted")
    check("protected board, components, pads and holes", expected == actual)
    check(
        "complete connectivity",
        old_conn.get("complete") is True
        and new_conn.get("complete") is True
        and all(
            isinstance(r.get("net"), str)
            and r["net"]
            and isinstance(r.get("uid"), str)
            and r["uid"]
            and type(r.get("opens")) is int
            and r["opens"] >= 0
            and type(r.get("routed")) is bool
            for r in new_conn["nets"]
        )
        and len({r["net"] for r in new_conn["nets"]}) == len(new_conn["nets"])
        and len({r["uid"] for r in new_conn["nets"]}) == len(new_conn["nets"]),
    )
    check(
        "all native net identities preserved",
        {r["net"]: r["uid"] for r in old_conn["nets"]}
        == {r["net"]: r["uid"] for r in new_conn["nets"]},
    )
    expected_rows = {r["net"]: r for r in old_conn["nets"]}
    actual_rows = {r["net"]: r for r in new_conn["nets"]}
    check(
        "non-target connectivity unchanged",
        {n: r for n, r in expected_rows.items() if n not in bound["nets"]}
        == {n: r for n, r in actual_rows.items() if n not in bound["nets"]},
    )
    check(
        "observed unrouted set matches complete connectivity",
        sorted(observed["geometry"]["model"]["unrouted"], key=lambda r: r["net"])
        == sorted(
            [r for r in actual_rows.values() if r.get("opens") or r.get("routed") is False],
            key=lambda r: r["net"],
        ),
    )
    expected_rows.update({r["net"]: r for r in bound["expected_connectivity"]})
    completed_nets = {
        n
        for n in bound["nets"]
        if all(key(r) in removed for r in selected.values() if r["net"] == n)
    }
    check(
        "fully removed nets have expected disconnects",
        all(actual_rows.get(n) == expected_rows[n] for n in completed_nets),
    )
    check("expected opens and routed state", expected_rows == actual_rows, completion=True)
    check(
        "expected unrouted set",
        sorted(observed["geometry"]["model"]["unrouted"], key=lambda r: r["net"])
        == sorted(
            [r for r in expected_rows.values() if r["opens"] or not r["routed"]],
            key=lambda r: r["net"],
        ),
        completion=True,
    )
    return {
        "valid": all(r["passed"] for r in checks),
        "protection_valid": all(r["passed"] for r in checks if r["category"] == "protection"),
        "removal_complete": all(r["passed"] for r in checks if r["category"] == "completion"),
        "checks": checks,
        "removed_objects": [old[k] for k in sorted(removed)],
        "engineering_complete": False,
    }


def execute(params, client, a):
    if params.get("all") or params.get("at") or params.get("layer") or not params.get("nets"):
        raise a.AdapterError(
            "E_UNSUPPORTED",
            "Verified unroute currently requires explicit whole nets",
            {"write_attempted": False},
        )
    try:
        _, board, app, doc = active(params, client, a, require_project=False)
        nets = list(dict.fromkeys(params["nets"]))
        state, refs = observe(doc, a, references=True)
        plan = bind(state, nets, a)
    except Exception as error:
        raise a.AdapterError(
            getattr(error, "code", "E_NATIVE_FAILURE"),
            "Unroute preflight could not bind the target",
            {
                **(getattr(error, "details", {}) or {}),
                "write_attempted": False,
                "stage": "unroute.preflight",
            },
        ) from error
    selected = plan["selected_objects"]
    result = {
        "pcb": str(board),
        "nets": nets,
        "unroute": plan,
        "to_delete": {k + "s": sum(r["kind"] == k for r in selected) for k in ("trace", "via")},
        "targets": [
            {
                "target": n,
                **{
                    k + "s": sum(r["kind"] == k and r["net"] == n for r in selected)
                    for k in ("trace", "via")
                },
            }
            for n in nets
        ],
        "applied": False,
        "saved": False,
        "persistence_verified": False,
        "change_status": "not_applied",
        "persistence_state": "unchanged",
        "stages": {
            "delete": "not_started",
            "observe_after_delete": "not_started",
            "protection": "not_started",
            "save": "not_started",
            "close": "not_started",
            "reopen": "not_started",
        },
        "delete_attempts": [],
        "items": [],
        "skipped": [],
        "summary": {"total": 0, "succeeded": 0, "failed": 0},
        "_untrusted": [
            "pcb",
            "nets",
            "unroute",
            "targets",
            "state_after",
            "geometry_after",
            "removed_objects",
            "remaining_objects",
            "items",
            "skipped",
        ],
    }
    if not params.get("apply"):
        return result
    if params.get("expected") != plan["fingerprint"]:
        raise a.AdapterError(
            "E_CONFLICT",
            "Selected routing objects changed after preview",
            {"write_attempted": False},
        )
    if not selected:
        return {
            **result,
            "no_change": True,
            "deleted": 0,
            "removed_objects": [],
            "items": [{**row, "deleted": 0, "ok": True} for row in result["targets"]],
            "summary": {"total": len(nets), "succeeded": len(nets), "failed": 0},
            "state_after": state,
            "geometry_after": state["geometry"],
        }
    errors = []
    result["change_status"] = "unknown"
    result["persistence_state"] = "unknown"
    result["stages"]["delete"] = "started"
    attempted = []
    try:
        for net in nets:
            attempted.append(net)
            for row in [r for r in selected if r["net"] == net]:
                attempt = {"uid": row["uid"], "kind": row["kind"], "net": net, "returned": False}
                result["delete_attempts"].append(attempt)
                try:
                    a._native_call(
                        "unroute.Delete." + row["kind"] + "." + row["uid"], refs[key(row)].Delete
                    )
                    attempt["returned"] = True
                except Exception as error:
                    errors.append(
                        {"kind": row["kind"], "uid": row["uid"], "cause": type(error).__name__}
                    )
            if errors and params.get("continue_on_error") is False:
                break
        result["stages"]["observe_after_delete"] = "started"
        state = observe(doc, a)
        result["stages"]["observe_after_delete"] = "complete"
        # Retain a returned observation even if its comparison is incomplete.
        # Live changes alone do not establish what an application may have persisted.
        result.update(state_after=state, geometry_after=state["geometry"], deletion_errors=errors)
        result["stages"]["protection"] = "started"
        proof = compare(plan, state)
        result["stages"]["protection"] = "passed" if proof["protection_valid"] else "failed"
        result.update(
            state_after=state,
            geometry_after=state["geometry"],
            removed_objects=proof["removed_objects"],
            deleted=len(proof["removed_objects"]),
            deletion_errors=errors,
            protection=proof,
            remaining_objects=[r for r in state["routing_objects"] if r["net"] in nets],
        )
        removed = {key(row) for row in proof["removed_objects"]}
        result["items"] = [
            {
                **row,
                "deleted": sum(key(r) in removed for r in selected if r["net"] == row["target"]),
                "ok": all(key(r) in removed for r in selected if r["net"] == row["target"]),
            }
            for row in result["targets"]
            if row["target"] in attempted
        ]
        result["skipped"] = [n for n in nets if n not in attempted]
        for item in result["items"]:
            if not item["ok"]:
                item["error"] = {
                    "code": "E_CONFLICT",
                    "retryable": False,
                    "not_deleted": item["traces"] + item["vias"] - item["deleted"],
                }
        succeeded = sum(row["ok"] for row in result["items"])
        result["summary"] = {
            "total": len(attempted),
            "succeeded": succeeded,
            "failed": len(attempted) - succeeded,
        }
        changed = state != plan["before"]
        result.update(
            applied=changed,
            change_status="applied" if proof["valid"] else "partial" if changed else "not_applied",
            persistence_state="unknown" if changed else "unchanged",
        )
        result["stages"]["delete"] = (
            "completed" if proof["valid"] else "partial" if changed else "failed"
        )
        if not proof["protection_valid"]:
            raise a.AdapterError(
                "E_PARTIAL",
                "Unroute protection failed; automatic save, close and reopen were not started",
                {"failed_checks": [r["name"] for r in proof["checks"] if not r["passed"]]},
            )
        if not changed:
            raise a.AdapterError("E_NATIVE_FAILURE", "No requested routing object was removed")
        result["stages"]["save"] = "started"
        result["persistence_state"] = "unknown"
        try:
            a._save_close_board(app, board)
        except Exception as error:
            if (getattr(error, "details", {}) or {}).get("save_acknowledged") is True:
                result.update(saved=True, persistence_state="unknown")
                result["stages"].update(save="acknowledged", close="unconfirmed")
            raise
        result.update(saved=True, persistence_state="unknown")
        result["stages"].update(save="acknowledged", close="confirmed", reopen="started")
        a._native_call("unroute.reopen", lambda: a._open_layout_document(app, board))
        reopened = a._licensed_document(app)
        if str(reopened.FullName).casefold() != str(board).casefold():
            raise a.AdapterError("E_CONFLICT", "Reopened board has a different identity")
        after = observe(reopened, a)
        result.update(
            state_after=after, geometry_after=after["geometry"], persistence_verified=state == after
        )
        result["stages"]["reopen"] = "verified" if result["persistence_verified"] else "mismatch"
        result["persistence_state"] = "saved" if result["persistence_verified"] else "unknown"
        # A Delete call can raise after changing the document. Retain its raw
        # diagnostic; completion depends on observed identities and persistence.
        if not proof["valid"]:
            raise a.AdapterError("E_PARTIAL", "Only part of the requested unroute was established")
        return result
    except Exception as error:
        result["post_write_error"] = {
            "code": getattr(error, "code", "E_NATIVE_FAILURE"),
            "cause": type(error).__name__,
            "failed_checks": (getattr(error, "details", {}) or {}).get("failed_checks", []),
            "stages": dict(result["stages"]),
        }
        raise a.AdapterError(
            getattr(error, "code", "E_NATIVE_FAILURE"),
            "Unroute did not complete every required stage",
            {"write_attempted": True, "native_result": result},
        ) from error


def verify(result, before, after, plan):
    try:
        return _verify(result, before, after, plan)
    except (KeyError, TypeError, ValueError) as error:
        return {
            "valid": False,
            "engineering_complete": False,
            "checks": [{"name": "complete independent unroute evidence", "passed": False}],
            "scope": "selected native routing objects and persistence",
            "source": "incomplete independent native observation",
            "read_error": type(error).__name__,
            "not_checked": ["missing native fields", "native DRC"],
        }


def _verify(result, before, after, plan):
    bound = plan["extensions"]["legacy_preview"]["unroute"]
    observed = after["extensions"]["facts"]["cell_refresh"]
    state = {k: observed[k] for k in ("geometry", "routing_objects")}
    proof = compare(bound, state)
    checks = proof["checks"]
    checks.append(
        {
            "name": "original preview and independent before state",
            "passed": bound["before"]
            == {
                k: before["extensions"]["facts"]["cell_refresh"][k]
                for k in ("geometry", "routing_objects")
            },
        }
    )
    checks.append(
        {"name": "fresh independent after state", "passed": state == result.get("state_after")}
    )
    checks.append(
        {
            "name": "saved/reopened or unchanged no-op",
            "passed": (result.get("saved") is True and result.get("persistence_verified") is True)
            or (
                result.get("no_change") is True
                and not bound["selected_objects"]
                and state == bound["before"]
            ),
        }
    )
    return {
        **proof,
        "valid": all(r["passed"] for r in checks),
        "persistence_valid": result.get("persistence_verified") is True
        and state == result.get("state_after"),
        "scope": "selected objects, expected disconnects, protected geometry and save/reopen",
        "source": "independent native geometry after normal save/close/reopen"
        if result.get("persistence_verified") is True
        else "independent live native geometry; historical persistence not established"
        if not result.get("no_change")
        else "independent native no-op readback; no save or reopen",
        "not_checked": [
            "native DRC",
            "general copper connectivity outside the declared two-terminal scope",
        ],
    }
