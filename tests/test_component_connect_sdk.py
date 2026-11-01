"""Public component SDK lifecycle with synthetic native and copy-producer facts.

The runtime, durable operation journal, CLI parser, candidate masks, transaction
receipts and independent readback are real code. Only native responses and the
separately tested closed-copy producer authority are fixture boundaries.
"""

import copy
from types import SimpleNamespace as NS

import pytest
import test_project_isolated_copy as factory_tests
from test_board_component_connect import (
    DESCRIPTOR,
    _post_open_fixture,
    fixture_case,
    json_file,
)
from test_driver_v2 import _scope, _worker_fixture
from test_native_pad_mask import cells

from xpedition_cli import board_component_connect as B
from xpedition_cli import board_components as C
from xpedition_cli import driver
from xpedition_cli.cell_refresh import digest
from xpedition_cli.cli import pcb
from xpedition_cli.errors import CLIError


def test_public_closed_copy_never_reports_native_reopen(tmp_path, monkeypatch):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    _scope(tmp_path, monkeypatch)
    source, archive, target, _ = factory_tests.fixture(tmp_path)

    class NoNative:
        read_timeout_seconds = 30

        def invoke(self, *args, **kwargs):
            pytest.fail("Closed copying must not invoke native application methods")

    monkeypatch.setattr(driver, "NativeBackend", NoNative)
    args = [
        "project",
        "create",
        "--template",
        str(source),
        "--project",
        str(target),
        "--closed-copy",
        "--source-backup",
        str(archive),
    ]
    with driver.runtime(state_dir=tmp_path / "sdk") as cli:
        planned = cli.run([*args, "--dry-run"])
        assert planned.exit_code == 0, planned.envelope
        done = cli.run([*args, "--confirm", planned.envelope["data"]["confirm_token"]])
    assert done.exit_code == 0, done.envelope
    receipt = done.envelope["data"]["receipt"]
    assert receipt["outcome"]["verification"]["status"] == "passed"
    result = receipt["extensions"]["result"]
    assert result["native_calls_dispatched"] == 0
    assert result["opened"] is False and result["native_open_verified"] is False
    assert receipt["extensions"]["file_readback"]["native_open_verified"] is False
    assert receipt["extensions"]["persistence_independently_reopened"] is False


def sdk_case(tmp_path, monkeypatch, *, corrupt_after_handler=False, no_op=False, fault=None):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    _scope(tmp_path, monkeypatch)
    case = fixture_case(tmp_path, monkeypatch, fault=fault)
    calls = []

    # The CLI parses coordinates as doubles. Register those exact candidate
    # constructor inputs with the same synthetic native engine used by the
    # ordinary raw readers; no verifier or eligibility result is replaced.
    def register_cli_candidate(_doc, engine):
        points = [[float(value) for value in point] for point in case.request["points"]]
        raw = [[point[axis] for point in points] for axis in (0, 1)] + [[0.0] * len(points)]
        engine.paths[(digest(raw), case.request["width_mm"])] = cells(0, 0, 7, 1)

    case.observe(register_cli_candidate)

    class Backend:
        read_timeout_seconds = 30
        worker_build = "a" * 64

        def invoke(self, method, request, **_kwargs):
            calls.append((method, request.get("apply")))
            if method == "identity":
                return {
                    "application": copy.deepcopy(case.identity["application"]),
                    "adapter": _worker_fixture(self.worker_build),
                }
            if method == "board_info":
                assert request["start"] is False
                assert request["component_request"] == {
                    "net": case.request["net"],
                    "layer": case.request["layer"],
                }
                state, observed = case.observe()
                snapshot = C.build_snapshot(
                    state,
                    observed,
                    board_id=str(case.board),
                    net_id=case.request["net_uid"],
                    layer=case.request["layer"],
                )
                assert snapshot["valid"], snapshot
                return {
                    "observation": {
                        "connectivity_components": snapshot,
                        "native_routepass_state": state,
                        "native_routepass_identity": copy.deepcopy(case.identity),
                    }
                }
            if method == "hand_route":
                assert request["mode"] == "component-connect"
                try:
                    result = B.execute(request, None, case.adapter)
                except case.adapter.AdapterError as error:
                    raise CLIError(error.code, str(error), error.details) from error
                if request.get("apply") is True and corrupt_after_handler:
                    case.project.write_bytes(b"disk corruption after native handler returned")
                return result
            raise AssertionError("Unexpected native SDK operation: " + method)

    backend = Backend()
    monkeypatch.setattr(driver, "NativeBackend", lambda: backend)
    monkeypatch.setattr(pcb, "native", lambda: backend)
    query = case.request["query"]
    first_node = case.request["first"]["node_id"]
    second_node = first_node if no_op else case.request["second"]["node_id"]

    def component(node_id):
        return next(
            row["component_id"] for row in query["components"] if node_id in row["node_ids"]
        )

    args = [
        "pcb",
        "routing",
        "trace",
        "--mode",
        "component-connect",
        "--project",
        str(case.project),
        "--source-backup",
        case.params["source_backup"],
        "--component-query",
        case.request["query_file"]["path"],
        "--isolated-origin",
        case.request["isolated_origin_file"]["path"],
        "--recovery-backup",
        case.request["recovery_backup"],
        "--snapshot-id",
        query["snapshot_id"],
        "--from-component",
        component(first_node),
        "--to-component",
        component(second_node),
        "--from-node",
        first_node,
        "--to-node",
        second_node,
        "--net",
        case.request["net"],
        "--layer",
        "1",
        "--width",
        "0.254",
        "--points",
        "0,0 6,0",
    ]
    return NS(case=case, backend=backend, calls=calls, args=args)


def test_public_runtime_proves_connection_and_repeated_token_never_dispatches_again(
    tmp_path, monkeypatch
):
    setup = sdk_case(tmp_path, monkeypatch)
    with driver.runtime(state_dir=tmp_path / "sdk") as cli:
        planned = cli.run([*setup.args, "--dry-run"])
        assert planned.exit_code == 0, planned.envelope
        preview = planned.envelope["data"]["preview"]
        assert preview["can_apply"] is True
        assert preview["extensions"]["legacy_preview"]["trace"]["candidate"]["valid"] is True
        token = planned.envelope["data"]["confirm_token"]
        done = cli.run([*setup.args, "--confirm", token])
        assert done.exit_code == 0, done.envelope
        receipt = done.envelope["data"]["receipt"]
        assert receipt["extensions"]["file_readback"]["valid"] is True
        assert receipt["extensions"]["file_readback"]["independent_disk_matches"] is True
        assert receipt["extensions"]["persistence_independently_reopened"] is True
        native = receipt["extensions"]["result"]
        assert native["persistence_verified"] is True
        assert native["verification"]["components_before"] == 4
        assert native["verification"]["components_after"] == 3
        assert native["verification"]["all_members_connected"] is False
        before_events = copy.deepcopy(setup.case.events)
        repeated = cli.run([*setup.args, "--confirm", token])
        assert repeated.exit_code == 6, repeated.envelope
        assert setup.case.events == before_events
        assert [row[0] for row in setup.case.events] == [
            "TransactionStart",
            "PutTrace",
            "TransactionEnd",
            "Save",
            "Close",
            "Open",
        ]
        shown = cli.run(["operation", "show", "--id", preview["operation_id"]])
        assert shown.envelope["data"]["operation_status"]["status"] == "succeeded"


@pytest.mark.parametrize("drift", ["native_state", "query_bytes", "worker"])
def test_public_confirmation_rejects_stale_inputs_before_native_write(tmp_path, monkeypatch, drift):
    setup = sdk_case(tmp_path, monkeypatch)
    with driver.runtime(state_dir=tmp_path / "sdk") as cli:
        planned = cli.run([*setup.args, "--dry-run"])
        assert planned.exit_code == 0, planned.envelope
        if drift == "native_state":
            setup.case.state["texts"][0]["text"] = "changed after public preview"
        elif drift == "query_bytes":
            from pathlib import Path

            path = Path(setup.case.request["query_file"]["path"])
            path.write_bytes(path.read_bytes() + b"\n")
        else:
            setup.backend.worker_build = "b" * 64
        rejected = cli.run(
            [
                *setup.args,
                "--confirm",
                planned.envelope["data"]["confirm_token"],
            ]
        )
        assert rejected.exit_code == 6, rejected.envelope
        assert not setup.case.events
        assert not any(
            method == "hand_route" and applied is True for method, applied in setup.calls
        )


def test_independent_sdk_disk_readback_rejects_corruption_after_successful_handler(
    tmp_path, monkeypatch
):
    setup = sdk_case(tmp_path, monkeypatch, corrupt_after_handler=True)
    with driver.runtime(state_dir=tmp_path / "sdk") as cli:
        planned = cli.run([*setup.args, "--dry-run"])
        assert planned.exit_code == 0, planned.envelope
        done = cli.run([*setup.args, "--confirm", planned.envelope["data"]["confirm_token"]])
    assert done.exit_code != 0 and done.envelope["ok"] is False, done.envelope
    assert done.envelope["error"]["code"] != "E_UNKNOWN", done.envelope
    receipt = done.envelope["error"]["details"]["receipt"]
    assert receipt["extensions"]["result"]["persistence_verified"] is True
    assert receipt["extensions"]["file_readback"]["valid"] is False
    assert receipt["extensions"]["file_readback"]["independent_disk_matches"] is False
    assert receipt["extensions"]["persistence_independently_reopened"] is False
    assert receipt["outcome"]["verification"]["status"] != "passed"
    assert sum(row[0] == "PutTrace" for row in setup.case.events) == 1
    assert sum(row[0] == "Save" for row in setup.case.events) == 1


def test_public_same_component_noop_has_zero_apply_and_zero_persistence(tmp_path, monkeypatch):
    setup = sdk_case(tmp_path, monkeypatch, no_op=True)
    with driver.runtime(state_dir=tmp_path / "sdk") as cli:
        planned = cli.run([*setup.args, "--dry-run"])
        assert planned.exit_code == 0, planned.envelope
        native_preview = planned.envelope["data"]["preview"]["extensions"]["legacy_preview"]
        assert native_preview["no_op"] is True and native_preview["trace"]["can_apply"] is False
        done = cli.run([*setup.args, "--confirm", planned.envelope["data"]["confirm_token"]])
    assert done.exit_code == 0, done.envelope
    assert done.envelope["data"]["receipt"]["extensions"]["file_readback"]["valid"] is True
    assert (
        done.envelope["data"]["receipt"]["extensions"]["persistence_independently_reopened"]
        is False
    )
    assert not setup.case.events
    assert not any(method == "hand_route" and applied is True for method, applied in setup.calls)


def saved_sdk_case(tmp_path, monkeypatch, *, alternate_journal=False):
    """Produce authority through the actual SDK, never by inserting a journal row."""
    setup = sdk_case(tmp_path, monkeypatch)
    case = setup.case
    _post_open_fixture(
        case,
        {
            "Library/cells.cel": b"original synthetic critical bytes",
            "PCB/Work/unrelated.bin": b"original protected unknown bytes",
            "PCB/Layout/LayoutDB.lyt": b"original synthetic native copper storage",
        },
    )
    doc = case.adapter._licensed_document(None)
    original_save, original_open = doc.Save, case.adapter._open_layout_document

    def save_descriptor():
        returned = original_save()
        case.board.write_bytes(DESCRIPTOR.replace(b'"136"', b'"150"'))
        return returned

    def open_descriptor(*args):
        opened = original_open(*args)
        case.board.write_bytes(DESCRIPTOR.replace(b'"136"', b'"151"'))
        return opened

    doc.Save = save_descriptor
    case.adapter._open_layout_document = open_descriptor
    options = {"state_dir": tmp_path / "alternate-sdk"} if alternate_journal else {}
    with driver.runtime(**options) as cli:
        planned = cli.run([*setup.args, "--dry-run"])
        assert planned.exit_code == 0, planned.envelope
        done = cli.run([*setup.args, "--confirm", planned.envelope["data"]["confirm_token"]])
    assert done.exit_code == 0, done.envelope
    native = done.envelope["data"]["receipt"]["extensions"]["result"]
    assert native["saved_board_file"] != native["reopened_board_file"]
    assert native["save_to_reopen_descriptor"]["field_changes"][0]["before"] == "150"
    assert native["save_to_reopen_descriptor"]["field_changes"][0]["after"] == "151"
    assert B.verify_saved_receipt(native)["valid"]
    return setup, done.envelope


def prepare_continuation(setup, envelope, *, descriptor_counter=152, receipt_payload=None):
    case = setup.case
    receipt_file = json_file(
        case.tmp_path / "complete-sdk-receipt.json",
        envelope if receipt_payload is None else receipt_payload,
    )
    previous = envelope["data"]["receipt"]["extensions"]["result"]
    archive = previous["recovery_archive"]["archive"]
    case.board.write_bytes(DESCRIPTOR.replace(b'"136"', f'"{descriptor_counter}"'.encode()))
    case.origin["continuation_receipt"] = receipt_file
    case.request["isolated_origin"] = copy.deepcopy(case.origin)
    case.request["isolated_origin_file"] = json_file(case.tmp_path / "origin.json", case.origin)

    def register_next_candidate(_doc, engine):
        for raw in ([[6, 12], [0, 0], [0.0, 0.0]], [[6.0, 12.0], [0.0, 0.0], [0.0, 0.0]]):
            engine.paths[(digest(raw), 0.254)] = cells(6, 0, 13, 1)

    state, observed = case.observe(register_next_candidate)
    query = C.build_snapshot(
        state, observed, board_id=str(case.board), net_id=case.request["net_uid"], layer=1
    )
    assert query["valid"], query
    selectors = []
    for pin_uid in ("pad-owner-1", "pad-owner-2"):
        node_id = next(row["node_id"] for row in query["nodes"] if row.get("pin_uid") == pin_uid)
        component_id = next(
            row["component_id"] for row in query["components"] if node_id in row["node_ids"]
        )
        selectors.append({"component_id": component_id, "node_id": node_id})
    case.request.update(
        query=query,
        snapshot_id=query["snapshot_id"],
        first=selectors[0],
        second=selectors[1],
        points=[[6, 0], [12, 0]],
        query_file=json_file(case.tmp_path / "query.json", query),
        recovery_backup=str(case.tmp_path / "second-closed-stage.zip"),
    )
    case.params.pop("apply", None)
    case.params.pop("expected", None)
    case.params["source_backup"] = archive["path"]
    replacements = {
        "--source-backup": archive["path"],
        "--recovery-backup": case.request["recovery_backup"],
        "--snapshot-id": query["snapshot_id"],
        "--from-component": selectors[0]["component_id"],
        "--to-component": selectors[1]["component_id"],
        "--from-node": selectors[0]["node_id"],
        "--to-node": selectors[1]["node_id"],
        "--points": "6,0 12,0",
    }
    args = list(setup.args)
    for key, value in replacements.items():
        args[args.index(key) + 1] = value
    return args


def test_continuation_uses_complete_nested_sdk_receipt_and_original_producer(tmp_path, monkeypatch):
    setup, envelope = saved_sdk_case(tmp_path, monkeypatch)
    args = prepare_continuation(setup, envelope)
    events = copy.deepcopy(setup.case.events)
    with driver.runtime() as cli:
        planned = cli.run([*args, "--dry-run"])
    assert planned.exit_code == 0, planned.envelope
    bound = planned.envelope["data"]["preview"]["extensions"]["legacy_preview"]["trace"]
    assert bound["can_apply"] is True
    proof = bound["origin"]["continuation_sdk_evidence"]
    assert proof["operation_id"] == envelope["data"]["receipt"]["operation_id"]
    assert proof["receipt_sha256"] == digest(envelope["data"]["receipt"])
    assert proof["reopened_to_backup_descriptor"]["field_changes"][0]["before"] == "151"
    assert proof["reopened_to_backup_descriptor"]["field_changes"][0]["after"] == "150"
    descriptor = bound["origin"]["post_open_genesis_evidence"]["descriptor"]
    assert descriptor["field_changes"][0]["before"] == "150"
    assert descriptor["field_changes"][0]["after"] == "152"
    assert (
        descriptor["genesis"]["archive"]["sha256"]
        == envelope["data"]["receipt"]["extensions"]["result"]["recovery_archive"]["archive"][
            "sha256"
        ]
    )
    assert setup.case.events == events


@pytest.mark.parametrize("tamper", ["native", "outcome", "operation", "cropped"])
def test_continuation_rejects_receipt_tampering_or_missing_original_producer(
    tmp_path, monkeypatch, tamper
):
    setup, envelope = saved_sdk_case(tmp_path, monkeypatch)
    receipt = envelope["data"]["receipt"]
    receipt_payload = None
    if tamper == "native":
        receipt["extensions"]["result"]["trace_uid"] = "forged-returned-uid"
    elif tamper == "outcome":
        receipt["outcome"]["persistence"] = "unsaved"
    elif tamper == "operation":
        receipt["operation_id"] = "op-missingoriginalproducer123"
    else:
        receipt_payload = receipt["extensions"]["result"]
    args = prepare_continuation(setup, envelope, receipt_payload=receipt_payload)
    events = copy.deepcopy(setup.case.events)
    with driver.runtime() as cli:
        rejected = cli.run([*args, "--dry-run"])
    assert rejected.exit_code != 0, rejected.envelope
    assert setup.case.events == events


@pytest.mark.parametrize(
    "drift", ["descriptor", "critical", "native", "database", "unknown", "layout"]
)
def test_continuation_does_not_rebaseline_descriptor_critical_or_native_drift(
    tmp_path, monkeypatch, drift
):
    setup, envelope = saved_sdk_case(tmp_path, monkeypatch)
    case = setup.case
    if drift == "critical":
        # Even placing changed bytes into the new complete own backup cannot
        # replace the previous independently reopened SDK state.
        (case.project.parent / "Library/cells.cel").write_bytes(b"unregistered new library")
    elif drift in ("database", "unknown", "layout"):
        relative = {
            "database": "database/icdb.dat",
            "unknown": "PCB/Work/unrelated.bin",
            "layout": "PCB/Layout/LayoutDB.lyt",
        }[drift]
        (case.project.parent / relative).write_bytes(b"unregistered bytes in new own backup")
    args = prepare_continuation(setup, envelope)
    if drift == "descriptor":
        case.board.write_bytes(case.board.read_bytes().replace(b"unchanged", b"unauthorized"))
    elif drift == "native":
        case.state["texts"][0]["text"] = "new unregistered unsaved native text"
        # A fresh query legitimately sees this text but is not chain authority.
        args = prepare_continuation(setup, envelope)
    events = copy.deepcopy(case.events)
    with driver.runtime() as cli:
        rejected = cli.run([*args, "--dry-run"])
    assert rejected.exit_code != 0, rejected.envelope
    assert case.events == events


def test_continuation_requires_original_journal_even_for_complete_successful_sdk_envelope(
    tmp_path, monkeypatch
):
    setup, envelope = saved_sdk_case(tmp_path, monkeypatch, alternate_journal=True)
    with pytest.raises(ValueError, match="original continuous operation journal is unavailable"):
        B._sdk_component_receipt(envelope, setup.case.project, setup.case.board)


def test_successful_sdk_noop_is_not_a_saved_reopened_continuation_producer(tmp_path, monkeypatch):
    setup = sdk_case(tmp_path, monkeypatch, no_op=True)
    with driver.runtime() as cli:
        planned = cli.run([*setup.args, "--dry-run"])
        assert planned.exit_code == 0, planned.envelope
        done = cli.run([*setup.args, "--confirm", planned.envelope["data"]["confirm_token"]])
    assert done.exit_code == 0, done.envelope
    with pytest.raises(ValueError, match="saved native lifecycle is incomplete"):
        B._sdk_component_receipt(done.envelope, setup.case.project, setup.case.board)
    assert not setup.case.events


@pytest.mark.parametrize("fault", ["save_false", "open_bad_return"])
def test_actual_failed_sdk_persistence_receipt_is_not_continuation_authority(
    tmp_path, monkeypatch, fault
):
    setup = sdk_case(tmp_path, monkeypatch, fault=fault)
    with driver.runtime() as cli:
        planned = cli.run([*setup.args, "--dry-run"])
        assert planned.exit_code == 0, planned.envelope
        failed = cli.run([*setup.args, "--confirm", planned.envelope["data"]["confirm_token"]])
    assert failed.exit_code != 0, failed.envelope
    receipt = failed.envelope["error"]["details"]["receipt"]
    assert receipt["outcome"]["persistence"] != "saved"
    assert receipt["extensions"]["persistence_independently_reopened"] is False
    with pytest.raises(ValueError, match="original successful component operation is absent"):
        B._sdk_component_receipt(receipt, setup.case.project, setup.case.board)
