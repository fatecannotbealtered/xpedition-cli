"""Owned synthetic pass objects; exact dispatch and failure stage evidence."""

import copy
import hashlib
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from test_routepass_geometry import baseline, routed_state
from test_unroute_native import Collection

from xpedition_cli import board_routepass as R
from xpedition_cli import native_com_adapter as A


def native_case(tmp_path, monkeypatch, fault=None):
    work = tmp_path / "project"
    project, board = work / "owned.prj", work / "PCB" / "owned.pcb"
    board.parent.mkdir(parents=True)
    project.write_text(
        'SECTION iCDB\nLIST Designs\nVALUE "Main"\nENDLIST\nENDSECTION\n'
        'SECTION Main\nKEY ConfigType "PCB"\nKEY RootBlock "Root"\n'
        'KEY PCBDesignPath "PCB/owned.pcb"\nENDSECTION\n',
        encoding="utf-8",
    )
    board.write_bytes(b"owned native board")
    icdb = work / "database" / "icdb.dat"
    icdb.parent.mkdir()
    icdb.write_bytes(b"owned native opaque database")
    source_backup = tmp_path / "closed-source.zip"
    files = {p.relative_to(work).as_posix(): p.read_bytes() for p in (project, board, icdb)}
    meta = {
        "project": project.name,
        "files": len(files),
        "bytes": sum(map(len, files.values())),
        "all_files": True,
        "skipped": [],
        "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
    }
    with zipfile.ZipFile(source_backup, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr("xpedition-cli-backup.json", json.dumps(meta))
    if fault in {"go_library_change", "go_config_change"}:
        protected_file = work / (
            "Library/parts.cel" if fault == "go_library_change" else "Config/rules.xml"
        )
        protected_file.parent.mkdir(parents=True)
        protected_file.write_bytes(b"frozen protected dependency")
    if fault and fault.startswith("route_"):
        route_logs = work / "PCB" / "LogFiles"
        route_logs.mkdir()
        if fault != "route_mvo_unexpected_create":
            (route_logs / "MVO_rules_log.txt").write_bytes(b"old native MVO diagnostic")
        if fault == "route_overwrite_old_report":
            (route_logs / "AutoRouteReport01.txt").write_bytes(b"Xpedition Layout old report")
        if fault == "route_rules_change":
            (route_logs / "MVO_Rules.txt").write_bytes(b"real frozen input rules")
    events, stored = [], None
    identity = {
        "application": {"guid": "owned"},
        "generation": {"birth": "123"},
        "board": str(board),
    }
    app = NS(ActiveDocument=None, observation_phases=[])
    net = NS(Name="A", UniqueId="11")

    class Nets(Collection):
        def Add(self, item):
            self.append(item)

    class Pass:
        def PassType(self, *args):
            events.append(("PassType", *args))
            # Resets all properties, matching the formal native contract.
            self.config, self.items, self.layers = (True, True), None, {1, 2}

        def Config(self, *args):
            events.append(("Config", *args))
            self.config = args
            if fault == "state_drift_during_configuration":
                app.ActiveDocument.state["texts"][0]["position"][0] += 1
                app.ActiveDocument.version += 1

        def Items(self, kind, nets):
            events.append(("Items", kind, [str(n.UniqueId) for n in nets]))
            self.items = nets

        def Order(self, *args):
            events.append(("Order", *args))

        def LayerSelect(self, kind, layer):
            events.append(("LayerSelect", kind, layer))
            self.layers.discard(layer) if kind == 3 else self.layers.add(layer)

        def Go(self):
            events.append(("Go",))
            assert self.config == (False, False) and self.layers == {1}
            assert len(self.items) == 1 and self.items[0] is net
            if fault == "no_route":
                return None
            app.ActiveDocument.state = getattr(app, "route_state", routed_state)(
                app.ActiveDocument.state
            )
            app.ActiveDocument.version += 1
            if fault in {"go_library_change", "go_config_change"}:
                protected_file.write_bytes(b"native pass unexpectedly modified a protected input")
            if fault == "go_new_path":
                (work / "new-rule.rdb").write_bytes(b"unexpected new dependency")
            if fault == "go_board_output":
                board.write_bytes(b"actual native route output before manual Save")
            if fault and fault.startswith("route_"):
                report = route_logs / "AutoRouteReport01.txt"
                report.write_bytes(b"Xpedition Layout native report: one connection routed")
                (route_logs / "MVO_rules_log.txt").write_bytes(
                    b"WARNING! obsolete MVO format; not all features supported"
                )
                if fault == "route_wrong_report":
                    report.rename(route_logs / "AutoRouteReport02.txt")
                elif fault == "route_other_log":
                    (route_logs / "unexpected.txt").write_bytes(b"not authorised")
                elif fault == "route_rules_change":
                    (route_logs / "MVO_Rules.txt").write_bytes(b"unexpected rule change")
                elif fault == "route_delete":
                    (route_logs / "MVO_rules_log.txt").unlink()
                elif fault == "route_binary":
                    report.write_bytes(b"Xpedition Layout\x00binary")
                elif fault == "route_big_report":
                    report.write_bytes(b"Xpedition Layout" + b"x" * 1048576)
                elif fault == "route_wrong_signature":
                    report.write_bytes(b"ordinary text without the native report header")
            if fault == "go_reply_loss":
                raise RuntimeError("actual mutation then reply lost")
            if fault == "protected":
                holes = app.ActiveDocument.state["geometry"]["model"]["holes"]
                if holes:
                    holes[0][2] += 1
                else:
                    holes.append([0, 0, 1])
            if fault == "foreign_net":
                app.ActiveDocument.state["routing_objects"][-1]["net"] = "B"
            if fault == "go_nonvoid":
                return True

    class Doc:
        FullName = str(board)

        @property
        def Checkpoint(self):
            if fault == "checkpoint_read_failed":
                raise RuntimeError("synthetic checkpoint getter unavailable")
            return NS(IsValid=lambda: fault != "checkpoint_invalid", version=self.version)

        def LocateCheckpoint(self, checkpoint):
            if fault == "checkpoint_before":
                return 1
            return 3 if fault == "checkpoint_changed" or checkpoint.version != self.version else 2

        def __init__(self, state):
            self.state = state
            self.version = 0

        def NewRoutePass(self):
            return Pass()

        def Save(self):
            nonlocal stored
            events.append(("Save",))
            if fault == "save_exception":
                raise RuntimeError("Save exception")
            stored = copy.deepcopy(self.state)
            if fault == "save_reply_loss":
                raise RuntimeError("Save reply loss")
            return None if fault == "save_none" else False if fault == "save_false" else True

        def Close(self, save):
            events.append(("Close", save))
            if fault == "close_exception":
                raise RuntimeError("Close reply loss")
            if fault in {"close_none", "close_false"}:
                return None if fault == "close_none" else False
            app.ActiveDocument = None
            return True

    app.ActiveDocument = Doc(baseline())
    app.Utility = NS(NewNets=Nets)

    def observe(doc, a, request):
        app.observation_phases.append(
            "reopened"
            if any(e[0] == "Open" for e in events)
            else "post_go"
            if any(e[0] == "Go" for e in events)
            else "pre_go"
        )
        if any(e[0] == "Go" for e in events) and fault == "readback":
            raise RuntimeError("native readback unavailable")
        return copy.deepcopy(doc.state)

    def opened(application, path):
        assert application is app and path == board
        events.append(("Open",))
        if fault == "open_fail":
            raise RuntimeError("Open failed")
        app.ActiveDocument = Doc(copy.deepcopy(stored))
        if fault == "reopen_drift":
            app.ActiveDocument.state["texts"][0]["position"][0] += 1
        return app.ActiveDocument, []

    monkeypatch.setattr(
        R, "active", lambda *args, **kwargs: (project, board, app, app.ActiveDocument)
    )
    monkeypatch.setattr(R, "observe", observe)

    def observe_for_save(doc, a, request, verified_state):
        state = observe(doc, a, request)
        app.observation_phases[-1] = "pre_save"
        return state

    monkeypatch.setattr(R, "observe_for_save", observe_for_save)
    monkeypatch.setattr(R.board_labels, "_identity", lambda *args: copy.deepcopy(identity))
    monkeypatch.setattr(A, "_licensed_document", lambda app: app.ActiveDocument)
    monkeypatch.setattr(A, "_find_net", lambda doc, name: net)
    monkeypatch.setattr(A, "_open_layout_document", opened)
    params = {
        "project": str(project),
        "routepass_request": {"net": "A", "layer": 1},
        "source_backup": str(source_backup),
    }
    return app, params, events, identity, net


def test_native_configuration_and_one_pass_protected_save_reopen(tmp_path, monkeypatch):
    app, params, events, *_ = native_case(tmp_path, monkeypatch)
    preview = R.execute(params, object(), A)
    assert not events and not preview["applied"]
    app.observation_phases.clear()
    params.update(apply=True, expected=preview["state_digest"])
    result = R.execute(params, object(), A)
    assert [e[0] for e in events] == [
        "PassType",
        "Config",
        "Items",
        "Order",
        "LayerSelect",
        "LayerSelect",
        "Go",
        "Save",
        "Close",
        "Open",
    ]
    assert result["saved"] and result["persistence_verified"]
    assert app.observation_phases == ["pre_go", "pre_go", "post_go", "pre_save", "reopened"]
    assert R.verify_receipt(result, preview["routepass"], app.ActiveDocument.state)["valid"]


def test_route_preview_reports_missing_layout_provenance_and_no_candidate_path(
    tmp_path, monkeypatch
):
    app, params, events, *_ = native_case(tmp_path, monkeypatch)
    before = copy.deepcopy(app.ActiveDocument.state)
    preview = R.execute(params, object(), A)
    coverage = preview["reminders_before"]["layout_reminder_coverage"]
    assert coverage["status"] == "partial"
    assert coverage["layout_observation_present"] is False
    assert coverage["empty_warnings_mean_layout_clear"] is False
    assert {row["collection"] for row in coverage["unreviewed_collections"]} >= {
        "texts",
        "pads",
        "silk",
        "traces",
        "outline",
        "component_envelopes",
    }
    assert "strict physical model" in coverage["routepass_observation_limit"]
    assert preview["route_projection_boundary"]["native_candidate_path_observed"] is False
    assert preview["route_projection_boundary"]["new_trace_text_projection_checked"] is False
    assert app.ActiveDocument.state == before
    assert not events


def test_route_preview_preserves_available_text_projection_risks_without_moving_objects(
    tmp_path, monkeypatch
):
    from test_layout_reminders import model, text

    app, params, events, *_ = native_case(tmp_path, monkeypatch)
    readable = model(text(), text((3, 2, 5, 3), uid="text-2", refdes="J1"))
    target = app.ActiveDocument.state["geometry"]["model"]
    readable["layout_observation"]["layer_count"]["value"] = target["layers"]
    for key in ("texts", "outline", "layout_observation"):
        target[key] = copy.deepcopy(readable[key])
    before = copy.deepcopy(app.ActiveDocument.state)
    preview = R.execute(params, object(), A)
    risks = preview["reminders_before"]["layout_reminders"]
    assert any(row["kind"] == "projected_text_text_overlap" for row in risks)
    assert preview["reminders_before"]["layout_reminder_coverage"]["layout_observation_present"]
    assert app.ActiveDocument.state == before
    assert not events
    params.update(apply=True, expected=preview["state_digest"])
    result = R.execute(params, object(), A)
    assert result["persistence_verified"]
    assert any(
        row["kind"] == "projected_text_text_overlap"
        for row in result["reminders_after"]["layout_reminders"]
    )
    assert (
        app.ActiveDocument.state["geometry"]["model"]["texts"]
        == before["geometry"]["model"]["texts"]
    )


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("go_reply_loss", ["Go"]),
        ("go_nonvoid", ["Go"]),
        ("readback", ["Go"]),
        ("protected", ["Go"]),
        ("foreign_net", ["Go"]),
        ("go_library_change", ["Go"]),
        ("go_config_change", ["Go"]),
        ("go_new_path", ["Go"]),
        ("save_none", ["Go", "Save"]),
        ("save_false", ["Go", "Save"]),
        ("save_exception", ["Go", "Save"]),
        ("save_reply_loss", ["Go", "Save"]),
        ("close_none", ["Go", "Save", "Close"]),
        ("close_false", ["Go", "Save", "Close"]),
        ("close_exception", ["Go", "Save", "Close"]),
        ("open_fail", ["Go", "Save", "Close", "Open"]),
        ("reopen_drift", ["Go", "Save", "Close", "Open"]),
    ],
)
def test_failures_preserve_stage_facts_and_never_repeat_or_save_early(
    tmp_path, monkeypatch, fault, expected
):
    _, params, events, *_ = native_case(tmp_path, monkeypatch, fault)
    preview = R.execute(params, object(), A)
    params.update(apply=True, expected=preview["state_digest"])
    with pytest.raises(A.AdapterError) as caught:
        R.execute(params, object(), A)
    assert [e[0] for e in events if e[0] in {"Go", "Save", "Close", "Open"}] == expected
    result = caught.value.details["native_result"]
    assert result["write_attempted"] and not result["persistence_verified"]
    assert result["saved"] is ("Close" in expected)
    if fault == "go_reply_loss":
        assert result["native_calls_returned"][-1]["returned"] is False


@pytest.mark.parametrize(
    "fault",
    [
        "wrong_target",
        "input_drift",
        "identity_drift",
        "configuration_drift",
        "state_drift",
        "state_drift_during_configuration",
    ],
)
def test_confirmation_drift_rejects_before_any_pass(tmp_path, monkeypatch, fault):
    app, params, events, identity, net = native_case(tmp_path, monkeypatch, fault)
    preview = R.execute(params, object(), A)
    params.update(apply=True, expected=preview["state_digest"])
    if fault == "wrong_target":
        net.UniqueId = "wrong"
    elif fault == "input_drift":
        Path(params["project"]).write_bytes(b"changed source")
    elif fault == "identity_drift":
        identity["generation"]["birth"] = "456"
    elif fault == "state_drift":
        app.ActiveDocument.state["texts"][0]["position"][0] += 1
    elif fault == "configuration_drift":
        original = R.configuration

        def bad(state, request):
            config = original(state, request)
            config["config"] = [False, True]
            return config

        monkeypatch.setattr(R, "configuration", bad)
    with pytest.raises(A.AdapterError):
        R.execute(params, object(), A)
    assert not any(e[0] in {"Go", "Save", "Close", "Open"} for e in events)


@pytest.mark.parametrize(
    "fault",
    [
        "save_none",
        "close_none",
        "missing_go",
        "cleanup",
        "reordered_reset",
        "missing_state",
        "missing_identity",
        "flag_only",
        "checkpoint_missing",
        "checkpoint_false",
        "save_gate_checkpoint_missing",
        "pre_save_inputs_missing",
        "pre_save_inputs_changed",
        "pre_save_raw_logs_missing",
        "atomic_claim",
        "observable_missing",
        "observable_digest",
        "pre_save_state_missing",
    ],
)
def test_receipt_rejects_contradictory_complete_flags(tmp_path, monkeypatch, fault):
    app, params, _, *_ = native_case(tmp_path, monkeypatch)
    preview = R.execute(params, object(), A)
    params.update(apply=True, expected=preview["state_digest"])
    result = R.execute(params, object(), A)
    if fault == "save_none":
        result["native_calls_returned"][-3]["return_kind"] = "none"
    elif fault == "close_none":
        result["native_calls_returned"][-2]["return_kind"] = "none"
    elif fault == "missing_go":
        result["native_calls_returned"].pop(-4)
    elif fault == "cleanup":
        result["native_calls_returned"][1]["arguments"] = [False, True]
    elif fault == "reordered_reset":
        result["native_calls_returned"][0], result["native_calls_returned"][1] = (
            result["native_calls_returned"][1],
            result["native_calls_returned"][0],
        )
    elif fault == "missing_state":
        result.pop("state_after")
    elif fault == "missing_identity":
        result.pop("reopen_evidence")
    elif fault == "checkpoint_missing":
        result.pop("checkpoint_evidence")
    elif fault == "checkpoint_false":
        result["checkpoint_evidence"][-1]["location"] = 3
    elif fault == "save_gate_checkpoint_missing":
        result.pop("save_gate_checkpoint_evidence")
    elif fault == "pre_save_inputs_missing":
        result.pop("pre_save_input_observation")
    elif fault == "pre_save_inputs_changed":
        result["pre_save_input_observation"]["live_files"][0]["sha256"] = "0" * 64
    elif fault == "pre_save_raw_logs_missing":
        result["pre_save_input_observation"].pop("non_rule_observed_files")
    elif fault == "atomic_claim":
        result["observable_state_guard_evidence"]["atomic_database_freeze"] = True
    elif fault == "observable_missing":
        result.pop("observable_state_guard_evidence")
    elif fault == "observable_digest":
        result["observable_state_guard_evidence"]["observations"][-1]["design_state_sha256"] = (
            "0" * 64
        )
    elif fault == "pre_save_state_missing":
        result.pop("state_before_save")
    else:
        result = {"applied": True, "saved": True, "persistence_verified": True}
    assert not R.verify_receipt(result, preview["routepass"], app.ActiveDocument.state)["valid"]


@pytest.mark.parametrize("fault", ["native_change_during_compare", "input_change_during_compare"])
def test_readback_must_still_be_current_immediately_before_save(tmp_path, monkeypatch, fault):
    app, params, events, *_ = native_case(tmp_path, monkeypatch)
    preview = R.execute(params, object(), A)
    original = R.compare

    def comparison(plan, state):
        proof = original(plan, state)
        if fault == "native_change_during_compare":
            app.ActiveDocument.state["texts"][0]["position"][0] += 1
            app.ActiveDocument.version += 1
        else:
            Path(params["project"]).write_bytes(b"late real dependency drift")
        return proof

    monkeypatch.setattr(R, "compare", comparison)
    params.update(apply=True, expected=preview["state_digest"])
    with pytest.raises(A.AdapterError) as caught:
        R.execute(params, object(), A)
    assert [e[0] for e in events if e[0] in {"Go", "Save", "Close", "Open"}] == ["Go"]
    result = caught.value.details["native_result"]
    assert result["applied"] and not result["saved"] and not result["persistence_verified"]


def test_incomplete_pad_preflight_preserves_the_actual_observation(tmp_path, monkeypatch):
    app, params, events, *_ = native_case(tmp_path, monkeypatch)
    material = app.ActiveDocument.state["endpoint_pad_material"][0]["material"]
    material.update(complete=False, unread=[{"reason": "actual adapter arced contour unsupported"}])
    with pytest.raises(A.AdapterError) as caught:
        R.execute(params, object(), A)
    assert not events
    retained = caught.value.details["preflight_observation"]
    assert retained["endpoint_pad_material"][0]["material"] == material


def test_only_predeclared_primary_board_file_can_receive_native_route_output(tmp_path, monkeypatch):
    app, params, _, *_ = native_case(tmp_path, monkeypatch, "go_board_output")
    preview = R.execute(params, object(), A)
    allowed = preview["routepass"]["allowed_output_files"]
    assert len([r for r in allowed if r["role"] == "primary_board"]) == 1
    assert allowed[0]["path"].endswith("owned.pcb")
    params.update(apply=True, expected=preview["state_digest"])
    result = R.execute(params, object(), A)
    assert result["persistence_verified"]
    assert result["dependency_change_evidence"]["valid"]
    assert len(result["dependency_change_evidence"]["changes"]) == 1
    assert result["dependency_change_evidence"]["changes"][0]["allowed"]
    result["dependency_change_evidence"]["changes"] = []
    assert not R.verify_receipt(result, preview["routepass"], app.ActiveDocument.state)["valid"]


def test_void_no_route_result_is_not_reported_as_applied_or_saved(tmp_path, monkeypatch):
    _, params, events, *_ = native_case(tmp_path, monkeypatch, "no_route")
    preview = R.execute(params, object(), A)
    params.update(apply=True, expected=preview["state_digest"])
    with pytest.raises(A.AdapterError) as caught:
        R.execute(params, object(), A)
    result = caught.value.details["native_result"]
    assert [e[0] for e in events if e[0] in {"Go", "Save", "Close", "Open"}] == ["Go"]
    assert result["write_attempted"] and not result["applied"] and not result["saved"]
    assert result["change_status"] == "not_applied"
    assert result["persistence_state"] == "unknown"
    assert result["native_calls_returned"][-1]["call"] == "Go"
    assert result["native_calls_returned"][-1]["returned"]


@pytest.mark.parametrize(
    "fault",
    ["checkpoint_before", "checkpoint_invalid", "checkpoint_changed", "checkpoint_read_failed"],
)
def test_checkpoint_observation_never_claims_atomic_freeze(fault, tmp_path, monkeypatch):
    app, params, _, *_ = native_case(tmp_path, monkeypatch, fault)
    preview = R.execute(params, object(), A)
    params.update(apply=True, expected=preview["state_digest"])
    result = R.execute(params, object(), A)
    assert result["persistence_verified"]
    assert result["observable_state_guard_evidence"]["atomic_database_freeze"] is False
    assert result["observable_state_guard_evidence"]["exclusive_database_lock"] is False
    assert all(row["atomic_freeze_proven"] is False for row in result["checkpoint_evidence"])
    if fault == "checkpoint_before":
        assert all(
            row["location"] == 1 and row["location_name"] == "Before"
            for row in result["checkpoint_evidence"]
        )
    elif fault == "checkpoint_invalid":
        assert all(row["valid"] is False for row in result["checkpoint_evidence"])
    elif fault == "checkpoint_read_failed":
        assert all(
            row["status"] == "reference_unavailable" for row in result["checkpoint_evidence"]
        )
    assert R.verify_receipt(result, preview["routepass"], app.ActiveDocument.state)["valid"]


def test_declared_native_report_and_existing_mvo_log_are_exact_bounded_outputs(
    tmp_path, monkeypatch
):
    app, params, events, *_ = native_case(tmp_path, monkeypatch, "route_outputs")
    preview = R.execute(params, object(), A)
    outputs = preview["routepass"]["allowed_output_files"]
    report = next(r for r in outputs if r["role"] == "native_autoroute_report")
    mvo = next(r for r in outputs if r["role"] == "native_mvo_warning_log")
    assert report["path"].endswith("AutoRouteReport01.txt") and report["may_create"]
    assert report["existing_report_indices"] == [] and report["max_bytes"] == 1048576
    assert mvo["path"].endswith("MVO_rules_log.txt") and mvo["may_create"] is False
    params.update(apply=True, expected=preview["state_digest"])
    result = R.execute(params, object(), A)
    assert result["persistence_verified"]
    assert [e[0] for e in events if e[0] in {"Go", "Save", "Close", "Open"}] == [
        "Go",
        "Save",
        "Close",
        "Open",
    ]
    delta = result["dependency_change_evidence"]
    assert delta["valid"] and len(delta["changes"]) == 2
    warning = next(r for r in delta["changes"] if r["path"].endswith("MVO_rules_log.txt"))
    assert "not all features supported" in warning["after"]["text_observation"]["text"]
    assert warning["after"]["text_observation"]["_untrusted"] == ["text"]
    assert R.verify_receipt(result, preview["routepass"], app.ActiveDocument.state)["valid"]
    preview["routepass"]["allowed_output_files"][1]["path"] = str(
        Path(params["project"]).parent / "MVO_Rules.txt"
    )
    assert not R.verify_receipt(result, preview["routepass"], app.ActiveDocument.state)["valid"]


@pytest.mark.parametrize(
    "fault",
    [
        "route_wrong_report",
        "route_other_log",
        "route_rules_change",
        "route_delete",
        "route_binary",
        "route_big_report",
        "route_wrong_signature",
        "route_overwrite_old_report",
        "route_mvo_unexpected_create",
    ],
)
def test_failed_file_protection_still_preserves_full_actual_copper_readback(
    tmp_path, monkeypatch, fault
):
    _, params, events, *_ = native_case(tmp_path, monkeypatch, fault)
    preview = R.execute(params, object(), A)
    params.update(apply=True, expected=preview["state_digest"])
    with pytest.raises(A.AdapterError) as caught:
        R.execute(params, object(), A)
    result = caught.value.details["native_result"]
    assert [e[0] for e in events if e[0] in {"Go", "Save", "Close", "Open"}] == ["Go"]
    assert result["applied"] and result["change_status"] == "partial"
    assert result["stages"]["readback"] == "complete"
    assert result["state_after"]["geometry"]["model"]["connectivity"]["nets"][0]["opens"] == 0
    assert result["verification"]["valid"]
    assert not result["dependency_change_evidence"]["valid"]
    assert not result["saved"] and not result["persistence_verified"]


@pytest.mark.parametrize(
    "names",
    [
        ["AutoRouteReport00.txt"],
        ["AutoRouteReport02.txt"],
        ["AutoRouteReport100.txt"],
        ["AutoRouteReport01.log"],
        [f"AutoRouteReport{i:02}.txt" for i in range(1, 100)],
    ],
)
def test_unknown_or_exhausted_report_sequence_rejects_before_native_pass(
    tmp_path, monkeypatch, names
):
    _, params, events, *_ = native_case(tmp_path, monkeypatch)
    directory = Path(params["project"]).parent / "PCB" / "LogFiles"
    directory.mkdir()
    for name in names:
        (directory / name).write_bytes(b"Xpedition Layout old native report")
    with pytest.raises(A.AdapterError):
        R.execute(params, object(), A)
    assert events == []


def test_existing_report_indices_are_frozen_and_next_output_is_exact(tmp_path, monkeypatch):
    _, params, _, *_ = native_case(tmp_path, monkeypatch)
    directory = Path(params["project"]).parent / "PCB" / "LogFiles"
    directory.mkdir()
    (directory / "AutoRouteReport01.txt").write_bytes(b"Xpedition Layout original report")
    preview = R.execute(params, object(), A)
    declared = next(
        r
        for r in preview["routepass"]["allowed_output_files"]
        if r["role"] == "native_autoroute_report"
    )
    assert declared["path"].endswith("AutoRouteReport02.txt")
    assert declared["existing_report_indices"] == [1]
    (directory / "AutoRouteReport02.txt").write_bytes(b"Xpedition Layout interposed report")
    params.update(apply=True, expected=preview["state_digest"])
    with pytest.raises(A.AdapterError, match="drifted"):
        R.execute(params, object(), A)


def test_source_complete_and_live_opaque_read_boundary_is_explicit(tmp_path, monkeypatch):
    _, params, _, *_ = native_case(tmp_path, monkeypatch)
    project = Path(params["project"])
    board = project.parent / "PCB" / "owned.pcb"
    original = Path.read_bytes
    icdb = project.parent / "database" / "icdb.dat"

    def locked(path):
        if path == icdb:
            raise PermissionError("synthetic healthy native icdb lock")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", locked)
    deps = R.input_files(project, board, params["source_backup"])
    assert deps["source_backup"]["crc_and_members_verified"]
    assert deps["source_backup"]["all_files"] and deps["source_backup"]["files"] == 3
    assert deps["live_unavailable"] == [
        {
            "path": str(icdb),
            "live_read": "unavailable",
            "source_only_sha256": hashlib.sha256(b"owned native opaque database").hexdigest(),
        }
    ]
    assert not any(r["path"] == str(icdb) for r in deps["live_files"])


def test_other_unread_file_fails_and_cannot_be_omitted_by_client(tmp_path, monkeypatch):
    _, params, _, *_ = native_case(tmp_path, monkeypatch)
    original = Path.read_bytes
    project = Path(params["project"])

    def failed(path):
        if path == project:
            raise PermissionError("synthetic unavailable current project")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", failed)
    params["input_files"] = []
    with pytest.raises(A.AdapterError, match="preflight"):
        R.execute(params, object(), A)


def test_known_log_growth_is_disclosed_but_design_change_is_bound(tmp_path, monkeypatch):
    _, params, _, *_ = native_case(tmp_path, monkeypatch)
    project = Path(params["project"])
    board = project.parent / "PCB" / "owned.pcb"
    log = project.parent / "database" / "cdbsvr" / "log" / "now.txt"
    log.parent.mkdir(parents=True)
    log.write_bytes(b"first event")
    before = R.input_files(project, board, params["source_backup"])
    log.write_bytes(b"second event")
    after = R.input_files(project, board, params["source_backup"])
    assert before["non_rule_observed_files"] != after["non_rule_observed_files"]
    assert R._dependency_identity(before) == R._dependency_identity(after)
    design = project.parent / "rules.rdb"
    design.write_bytes(b"new actual rule dependency")
    changed = R.input_files(project, board, params["source_backup"])
    assert R._dependency_identity(after) != R._dependency_identity(changed)


@pytest.mark.parametrize(
    "fault", ["not_all", "skipped", "bad_member", "missing_icdb", "missing_metadata"]
)
def test_source_recovery_must_be_complete_and_independently_validated(tmp_path, monkeypatch, fault):
    _, params, _, *_ = native_case(tmp_path, monkeypatch)
    archive_path = Path(params["source_backup"])
    with zipfile.ZipFile(archive_path) as archive:
        members = {n: archive.read(n) for n in archive.namelist()}
    meta = json.loads(members["xpedition-cli-backup.json"])
    if fault == "not_all":
        meta["all_files"] = False
    elif fault == "skipped":
        meta["skipped"] = ["database/icdb.dat"]
    elif fault == "bad_member":
        members["owned.prj"] = b"wrong source bytes"
    elif fault == "missing_icdb":
        members.pop("database/icdb.dat")
    if fault == "missing_metadata":
        members.pop("xpedition-cli-backup.json")
    else:
        members["xpedition-cli-backup.json"] = json.dumps(meta).encode()
    with zipfile.ZipFile(archive_path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    with pytest.raises(A.AdapterError, match="preflight"):
        R.execute(params, object(), A)
