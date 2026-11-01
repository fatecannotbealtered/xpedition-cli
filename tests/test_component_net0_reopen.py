"""Synthetic fresh native-mask receipts; no live CAD or historical acceptance."""

import copy
import json
from types import SimpleNamespace as NS

import pytest
from test_board_component_connect import apply, fixture_case, pin
from test_board_components import BOARD, snapshot, task
from test_native_component_transaction import AdapterError

from xpedition_cli import board_component_connect as B
from xpedition_cli import board_components as C
from xpedition_cli import component_net0_reopen as NR
from xpedition_cli import native_pad_mask as M


def lifecycle():
    process = {
        "pid": 17,
        "creation_time_100ns": "123456789",
        "executable": "D:/synthetic/native/wg.exe",
    }
    return {
        "save_count": 1,
        "save_returned": True,
        "close_count": 1,
        "close_returned": True,
        "closed_observed": True,
        "open_count": 1,
        "open_returned_document": True,
        "expected_path": BOARD,
        "reopened_path": BOARD,
        "process_before": process,
        "process_after": copy.deepcopy(process),
    }


def sync_mirrors(state):
    fields = ("component_uid", "pin_uid", "refdes", "pin", "net", "net_uid")
    materials = state["all_pin_pad_material"]
    state["pin_identities"] = [{key: row[key] for key in fields} for row in materials]
    by_owner = {}
    for row in materials:
        component = by_owner.setdefault(
            row["component_uid"],
            {"uid": row["component_uid"], "refdes": row["refdes"], "pins": [], "geometry": []},
        )
        header = {
            "uid": row["pin_uid"],
            "number": row["pin"],
            "net": row["net"],
            "net_uid": row["net_uid"],
        }
        component["pins"].append({**header, "center_mm": row["center_mm"]})
        component["geometry"].append({**header, "center_mm": row["center_mm"]})
    state["geometry"]["components"] = list(by_owner.values())


def net0_case():
    state, add, observe, request = task(count=3)
    foreign = next(row for row in state["all_pin_pad_material"] if row["net"] == "B")
    foreign.update(net="(Net0)", net_uid="default-before")
    sync_mirrors(state)
    before_state, material = observe()
    before = snapshot(before_state, material, request)
    nodes = sorted(
        (row for row in before["nodes"] if row["kind"] == "pin"),
        key=lambda row: (row["component_uid"], row["region_index"]),
    )
    connection = C.plan_connection(
        before,
        first=nodes[0]["node_id"],
        second=nodes[1]["node_id"],
        points=[[0, 0], [6, 0]],
        width_mm=0.254,
    )
    assert connection["structural_valid"] and not connection["no_op"]
    add("new-connection", connection["points"])
    target = state["geometry"]["model"]["connectivity"]["nets"][0]
    target.update(opens=1, routed=False)
    state["geometry"]["model"]["unrouted"] = [copy.deepcopy(target)]
    current, _ = observe()

    def reopen(mutate=None):
        for row in state["all_pin_pad_material"]:
            if row["net"] == "(Net0)":
                row["net_uid"] = "default-after"
        sync_mirrors(state)
        if mutate is not None:
            mutate(state)
        return observe()[0]

    return before, connection, current, reopen, request


def test_only_qualified_comparison_accepts_rebuilt_default_reference_and_keeps_raw():
    before, connection, current, reopen, request = net0_case()
    after = reopen()
    retained = copy.deepcopy((current, after))
    material = M.observe_components(after, request)
    strict = C.verify_connection(before, after, material, plan=connection, board_id=BOARD)
    assert not strict["valid"]
    comparison = NR.compare_reopened(current, after, request, lifecycle())
    proof = C.verify_connection(
        before,
        after,
        material,
        plan=connection,
        board_id=BOARD,
        reopen_comparison=comparison,
    )
    assert proof["valid"], proof
    assert proof["created_trace_uids"] == ["new-connection"]
    assert comparison["identity_proof"]["old_uid"] == "default-before"
    assert comparison["identity_proof"]["new_uid"] == "default-after"
    assert (current, after) == retained
    assert M.verify_component_evidence(after, request)["valid"]


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "save_false",
        "close_false",
        "closed_not_empty",
        "open_bool",
        "repeated",
        "pid",
        "path",
    ],
)
def test_default_reference_change_requires_complete_owned_lifecycle(fault):
    _, _, current, reopen, request = net0_case()
    after, life = reopen(), lifecycle()
    if fault == "missing":
        life = None
    elif fault == "save_false":
        life["save_returned"] = False
    elif fault == "close_false":
        life["close_returned"] = False
    elif fault == "closed_not_empty":
        life["closed_observed"] = False
    elif fault == "open_bool":
        life["open_returned_document"] = False
    elif fault == "repeated":
        life["open_count"] = 2
    elif fault == "pid":
        life["process_after"]["pid"] += 1
    else:
        life["reopened_path"] = "D:/synthetic/another.pcb"
    with pytest.raises((KeyError, TypeError, ValueError)):
        NR.compare_reopened(current, after, request, life)


@pytest.mark.parametrize("fault", ["text", "mirror", "missing_geometry", "duplicate", "bad_mask"])
def test_raw_non_reference_changes_or_incomplete_mirrors_are_not_hidden(fault):
    _, _, current, reopen, request = net0_case()
    after = reopen()
    if fault == "text":
        after["texts"][0]["text"] = "real non-target change"
    elif fault == "mirror":
        row = next(
            c for c in after["geometry"]["components"] if c["geometry"][0]["net"] == "(Net0)"
        )
        row["geometry"][0]["net_uid"] = "not-the-default-reference"
    elif fault == "missing_geometry":
        after["geometry"]["components"][0]["geometry"].clear()
    elif fault == "duplicate":
        after["pin_identities"].append(copy.deepcopy(after["pin_identities"][0]))
    else:
        after["native_mask_proof"]["material_sha256"] = "0" * 64
    with pytest.raises((KeyError, TypeError, ValueError)):
        NR.compare_reopened(current, after, request, lifecycle())


def test_comparison_is_recomputed_and_not_a_supplied_verification_override():
    before, connection, current, reopen, request = net0_case()
    after = reopen()
    comparison = NR.compare_reopened(current, after, request, lifecycle())
    comparison["raw_design_after_sha256"] = "0" * 64
    proof = C.verify_connection(
        before,
        after,
        M.observe_components(after, request),
        plan=connection,
        board_id=BOARD,
        reopen_comparison=comparison,
    )
    assert not proof["valid"]


def test_unchanged_raw_state_does_not_claim_new_reopen_equivalence():
    _, _, current, _, request = net0_case()
    assert NR.compare_reopened(current, copy.deepcopy(current), request, None) is None


def native_case(tmp_path, monkeypatch):
    """A fake mask engine creates fresh valid receipts after its fake Open."""
    case = fixture_case(tmp_path, monkeypatch, count=3)
    case.identity["generation"]["executable"] = "D:/synthetic/native/wg.exe"
    default = next(row for row in case.state["all_pin_pad_material"] if row["net"] == "B")
    default.update(net="(Net0)", net_uid="default-before")
    doc = case.application.ActiveDocument
    actual_pin = next(
        pin
        for component in doc.Components
        for pin in component.Pins
        if str(pin.UniqueId) == default["pin_uid"]
    )
    actual_pin.Net = NS(Name="(Net0)", UniqueId="default-before")
    sync_mirrors(case.state)
    raw, observed = case.observe()
    query = C.build_snapshot(
        raw,
        observed,
        board_id=str(case.board),
        net_id=case.request["net_uid"],
        layer=1,
    )
    assert query["valid"], query
    case.request.update(query=query, snapshot_id=query["snapshot_id"])
    query_path = case.request["query_file"]["path"]
    with open(query_path, "w", encoding="utf-8") as stream:
        json.dump({"ok": True, "data": {"observation": {"connectivity_components": query}}}, stream)
    case.request["query_file"] = pin(query_path)
    original_open = case.adapter._open_layout_document

    def set_default(uid):
        default["net_uid"] = uid
        actual_pin.Net.UniqueId = uid
        sync_mirrors(case.state)

    def open_rebuilt_reference(app, board):
        returned = original_open(app, board)
        set_default("default-after")
        return returned

    case.adapter._open_layout_document = open_rebuilt_reference
    return case, set_default


def test_native_lifecycle_and_sdk_recompute_reference_proof_without_rewriting_raw(
    tmp_path, monkeypatch
):
    case, _ = native_case(tmp_path, monkeypatch)
    result = apply(case)
    assert result["persistence_verified"] is True
    assert [row[0] for row in case.events].count("Save") == 1
    assert [row[0] for row in case.events].count("Close") == 1
    assert [row[0] for row in case.events].count("Open") == 1
    assert NR.default_uid(result["state_after"]) == "default-before"
    assert NR.default_uid(result["state_reopened"]) == "default-after"
    assert result["net0_reopen_comparison"]["identity_proof"]["default_member_count"] == 1
    fresh, _ = case.observe()
    request = {key: case.request[key] for key in ("net", "net_uid", "layer")}
    assert M.verify_component_evidence(fresh, request)["valid"]
    assert B.verify_receipt(
        result,
        result["trace"],
        {"state": fresh, "board": str(case.board), "identity": case.identity},
    )["valid"]
    assert NR.default_uid(fresh) == "default-after"


@pytest.mark.parametrize("fault", ["reported_proof", "raw_mask", "fresh_reference", "real_text"])
def test_saved_or_fresh_verification_does_not_accept_a_reported_alias_flag(
    tmp_path, monkeypatch, fault
):
    case, set_default = native_case(tmp_path, monkeypatch)
    result = apply(case)
    fresh, _ = case.observe()
    if fault == "reported_proof":
        result["net0_reopen_comparison"]["identity_proof"]["new_uid"] = "forged"
    elif fault == "raw_mask":
        result["state_reopened"]["native_mask_proof"]["material_sha256"] = "0" * 64
    elif fault == "fresh_reference":
        set_default("default-a-second-unowned-open")
        fresh, _ = case.observe()
    else:
        fresh["texts"][0]["text"] = "real independent non-target change"
    assert not B.verify_receipt(
        result,
        result["trace"],
        {"state": fresh, "board": str(case.board), "identity": case.identity},
    )["valid"]


def test_same_open_preconfirm_reference_drift_remains_a_strict_refusal(tmp_path, monkeypatch):
    case, set_default = native_case(tmp_path, monkeypatch)
    preview = B.execute(case.params, None, case.adapter)
    set_default("changed-without-owned-reopen")
    case.params.update(apply=True, expected=preview["trace"]["fingerprint"])
    with pytest.raises(AdapterError) as failure:
        B.execute(case.params, None, case.adapter)
    assert failure.value.details["write_attempted"] is False
    assert "no longer binds" in failure.value.details["cause"]
    assert case.events == []


def test_noop_cannot_supply_a_reopen_alias_as_persistence_qualification(tmp_path, monkeypatch):
    case, _ = native_case(tmp_path, monkeypatch)
    case.request["second"] = copy.deepcopy(case.request["first"])
    result = B.execute(case.params, None, case.adapter)
    raw, _ = case.observe()
    assert result["no_op"] and B.verify_receipt(result, result["trace"], raw)["valid"]
    result["net0_reopen_comparison"] = {"verified": True}
    assert not B.verify_receipt(result, result["trace"], raw)["valid"]
    assert case.events == []
