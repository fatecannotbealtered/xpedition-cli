"""Isolated file provenance and synthetic one-shot lifecycle, never real CAD."""

import copy
import hashlib
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from test_board_components import task
from test_native_component_transaction import AdapterError
from test_native_pad_mask import Collection, FakeMask, array, cells

from xpedition_cli import board_component_connect as B
from xpedition_cli import board_components as C
from xpedition_cli import native_pad_mask as M
from xpedition_cli.cell_refresh import _canonical, digest


def pin(path):
    data = Path(path).read_bytes()
    return {
        "path": str(Path(path).resolve()),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def json_file(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return pin(path)


def backup(path, root, project):
    rows = {
        file.relative_to(root).as_posix(): file.read_bytes()
        for file in root.rglob("*")
        if file.is_file()
    }
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in rows.items()}
    meta = {
        "all_files": True,
        "skipped": [],
        "project": project.name,
        "files": len(rows),
        "bytes": sum(map(len, rows.values())),
        "sha256": hashes,
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xpedition-cli-backup.json", json.dumps(meta))
        for name, data in rows.items():
            archive.writestr(name, data)
    return pin(path), hashes


def fixture_case(tmp_path, monkeypatch, *, count=4, pure=False, fault=None, minimum=0.254):
    source_root, target_root = tmp_path / "origin", tmp_path / "isolated"
    for root in (source_root, target_root):
        (root / "PCB").mkdir(parents=True)
        (root / "database").mkdir()
        (root / "Board.prj").write_bytes(b"SECTION DesignInfo\nENDSECTION\n")
        (root / "PCB" / "Board.pcb").write_bytes(b"synthetic original board")
        (root / "database" / "icdb.dat").write_bytes(b"synthetic opaque source")
    source_project, project = source_root / "Board.prj", target_root / "Board.prj"
    source_board, board = source_root / "PCB" / "Board.pcb", target_root / "PCB" / "Board.pcb"
    archive, hashes = backup(tmp_path / "closed-source.zip", source_root, source_project)
    origin = {
        "schema_version": 1,
        "kind": "isolated-component-connect",
        "copy_operation_id": "op-syntheticclosedcopy0001",
        "origin_project": pin(source_project),
        "origin_board": pin(source_board),
        "source_backup": archive,
        "target_project": {"path": str(project)},
        "target_board": {"path": str(board)},
        "genesis": {
            "project_sha256": pin(project)["sha256"],
            "board_sha256": pin(board)["sha256"],
            "file_manifest": hashes,
        },
        "provenance": {"kind": "closed-backup-copy"},
    }
    origin_file = json_file(tmp_path / "origin.json", origin)

    def synthetic_copy_producer(supplied, actual_project, actual_board):
        # The real journal/SDK producer authority is tested independently. This
        # fixture replaces only that boundary; it still derives all copied bytes
        # from the complete ZIP instead of accepting a caller's verified flag.
        if supplied.get("copy_operation_id") != "op-syntheticclosedcopy0001":
            raise ValueError("Synthetic copy producer identity differs")
        manifest, relative_board = B._genesis_manifest(
            supplied, supplied["source_backup"]["path"], actual_project, actual_board
        )
        return {
            "operation_id": supplied["copy_operation_id"],
            "receipt_sha256": digest({"synthetic_sdk_producer": manifest}),
            "source": "synthetic SDK producer fixture; not journal authority evidence",
            "genesis": {
                "project_sha256": manifest[Path(actual_project).name],
                "board_sha256": manifest[relative_board],
                "file_manifest": manifest,
            },
        }

    monkeypatch.setattr(B, "verify_origin", synthetic_copy_producer)
    state, add, observe, material_request = task(count=count, pure=pure)
    for row in state["all_pin_pad_material"]:
        row["center_mm"] = [float(value) for value in row["center_mm"]]
    state["rules"]["net_classes"] = (
        [
            {
                "name": "synthetic-class",
                "nets": [material_request["net"]],
                "membership_observation": {"complete": True},
                "widths": {"1": {"minimum": minimum}},
            }
        ]
        if minimum is not None
        else []
    )
    mask_context = {}
    outline_raw = array(-5, -5, 80, 80)
    state["geometry"]["model"]["outline"] = [
        {"kind": "outline", "path": [list(point) for point in zip(*outline_raw, strict=True)]}
    ]

    def prepare_scratch(mask_doc, engine):
        mask_context.update(doc=mask_doc, engine=engine)
        engine.filled[digest(outline_raw)] = cells(-5, -5, 80, 80)
        engine.filled[digest(array(-6, -6, 81, 81))] = cells(-6, -6, 81, 81)
        points = [[4, 0], [10, 0]] if pure else [[0, 0], [6, 0]]
        raw = [[point[0] for point in points], [point[1] for point in points], [0.0, 0.0]]
        engine.paths[(digest(raw), 0.254)] = cells(points[0][0], 0, points[-1][0] + 1, 1)

    before, material = observe(prepare_scratch)
    query = C.build_snapshot(
        before, material, board_id=str(board), net_id=material_request["net_uid"], layer=1
    )
    assert query["valid"], query
    if pure:
        first, second, points = "trace:island-0", "trace:island-1", [[4, 0], [10, 0]]
    else:
        first = next(
            row["node_id"] for row in query["nodes"] if row.get("pin_uid") == "pad-owner-0"
        )
        second = next(
            row["node_id"] for row in query["nodes"] if row.get("pin_uid") == "pad-owner-1"
        )
        points = [[0, 0], [6, 0]]
    query_file = json_file(
        tmp_path / "query.json",
        {"ok": True, "data": {"observation": {"connectivity_components": query}}},
    )
    request = {
        "snapshot_id": query["snapshot_id"],
        **material_request,
        "first": {"node_id": first},
        "second": {"node_id": second},
        "width_mm": 0.254,
        "points": points,
        "query": query,
        "query_file": query_file,
        "isolated_origin": origin,
        "isolated_origin_file": origin_file,
        "recovery_backup": str(tmp_path / "same-window-recovery.zip"),
    }
    events, observations, candidate_reads = [], [], []
    named_class = NS(Name="synthetic-class")
    net = NS(Name=request["net"], UniqueId=request["net_uid"], NetClass=named_class)
    app = NS(ActiveDocument=None)

    class Doc:
        FullName = str(board)

        def TransactionStart(self, mode):
            events.append(("TransactionStart", mode))
            return {"start_false": False, "start_none": None, "start_int": 1}.get(fault, True)

        def PutTrace(self, layer, actual_net, width, count_points, rows, component, anchor, unit):
            events.append(
                (
                    "PutTrace",
                    layer,
                    actual_net.UniqueId,
                    width,
                    count_points,
                    rows,
                    component,
                    anchor,
                    unit,
                )
            )
            if fault == "new_pieces":
                add("new-connection", [[0, 0], [3, 0]], width=width)
                add("new-second-piece", [[3, 0], [6, 0]], width=width)
            else:
                add(
                    "transient-return" if fault == "returned_uid" else "new-connection",
                    request["points"],
                    width=0.3 if fault == "new_width" else width,
                )
            if fault == "extra_merge":
                add("unselected-bridge", [[6, 0], [12, 0]])
            target = state["geometry"]["model"]["connectivity"]["nets"][0]
            target.update(
                opens=target["opens"] - (0 if fault == "native_opens" else 1), routed=count == 2
            )
            state["geometry"]["model"]["unrouted"] = (
                [copy.deepcopy(target)] if target["opens"] else []
            )
            if fault == "put_reply_lost":
                raise RuntimeError("synthetic reply lost after insertion")
            return NS(
                UniqueId="transient-return"
                if fault == "returned_uid"
                else "protected-other-net"
                if fault == "returned_old_uid"
                else "new-connection"
            )

        def TransactionEnd(self, keep):
            events.append(("TransactionEnd", keep))
            if fault == "returned_uid":
                native = next(row for row in doc.Traces if row.UniqueId == "transient-return")
                native.UniqueId = "new-connection"
                observed = next(
                    row for row in state["routing_objects"] if row["uid"] == "transient-return"
                )
                observed["uid"] = observed["geometry"]["uid"] = native.UniqueId
            return {"end_false": False, "end_none": None, "end_int": 1}.get(fault, True)

        def Save(self):
            events.append(("Save",))
            board.write_bytes(b"synthetic saved board with new trace")
            return {"save_false": False, "save_none": None, "save_int": 1}.get(fault, True)

        def Close(self, save):
            events.append(("Close", save))
            if fault != "close_active":
                app.ActiveDocument = None
            return {"close_false": False, "close_none": None, "close_int": 1}.get(fault, True)

    doc = Doc()
    doc.ActiveView = NS(Name="synthetic-current-view")
    doc.CurrentUnit = 2
    doc.LayerCount = 4
    doc.PickScheme = lambda _x, _y, _layer, _to_layer, _unit: "(Master)"
    doc.Nets = Collection([net])
    doc.Traces = mask_context["doc"].Traces
    doc.Components = Collection()
    for row in state["all_pin_pad_material"]:
        actual_pin = NS(
            UniqueId=row["pin_uid"],
            Name=row["pin"],
            Net=NS(Name=row["net"], UniqueId=row["net_uid"]),
            GetPositionX=lambda unit, x=row["center_mm"][0]: x,
            GetPositionY=lambda unit, y=row["center_mm"][1]: y,
            material=copy.deepcopy(row["material"]),
        )
        doc.Components.append(
            NS(UniqueId=row["component_uid"], RefDes=row["refdes"], Pins=Collection([actual_pin]))
        )
    outline_geometry = NS(
        IsValid=lambda: True,
        IsCircle=lambda: False,
        IsArced=lambda: False,
        IsClosed=lambda: True,
        values={1: outline_raw},
    )
    doc.BoardOutline = NS(Geometry=outline_geometry)
    app.ActiveDocument = doc
    identity = {
        "board": str(board),
        "application": {"type_library": {"major": 151, "minor": 0}, "pid": 123},
        "generation": {"birth_filetime_100ns": "456"},
    }

    def native_observe(*_):
        observations.append(len(observations) + 1)
        if fault == "pre_save_drift" and len(observations) == 6:
            state["texts"][0]["text"] = "changed at save gate"
        raw, _ = observe()
        return raw

    def open_doc(actual_app, actual_board):
        assert actual_app is app and actual_board == board
        events.append(("Open", str(actual_board)))
        app.ActiveDocument = doc
        if fault == "open_bad_return":
            return None
        if fault == "open_wrong_path":
            doc.FullName = str(tmp_path / "wrong.pcb")
        if fault == "reopen_state_drift":
            state["texts"][0]["text"] = "changed after Open"
        return doc, []

    def propget(owner, dispid, *args):
        engine = mask_context["engine"]
        if owner is named_class:
            assert dispid in (7, 8, 9) and args == (1, "(Master)", 4)
            return minimum if minimum is not None else 0.254
        if owner is doc:
            assert dispid == 326 and not args
            candidate_reads.append(1)
            if len(candidate_reads) == 3:
                if fault == "candidate_rule_drift":
                    state["rules"]["scheme"] = "changed during scratch-mask read"
                elif fault == "candidate_stackup_drift":
                    state["geometry"]["stackup"][1]["usage"] = "plane"
                elif fault == "candidate_text_drift":
                    state["texts"][0]["text"] = "changed during scratch-mask read"
            return engine
        if owner is engine:
            assert not args
            return {0: engine.masks, 2: engine.utility}[dispid]
        if isinstance(owner, FakeMask):
            if dispid == 1:
                return owner.shapes
            if dispid == 2:
                assert args == (4,)
                return (
                    owner.area_override
                    if owner.area_override is not None
                    else len(owner.cells) * 0.01
                )
        if owner is outline_geometry and dispid == 22:
            assert not args
            return Collection()
        if isinstance(owner, NS) and hasattr(owner, "values"):
            assert not args if dispid == 7 else args == (4,)
            return owner.values[dispid]
        raise AssertionError((owner, dispid, args))

    adapter = NS(
        UNIT_MM=4,
        AdapterError=AdapterError,
        _native_call=lambda name, function: function(),
        _licensed_document=lambda _: app.ActiveDocument,
        _find_net=lambda *args: net,
        _open_layout_document=open_doc,
        _propget=propget,
        _items=lambda collection, strict: iter(collection),
        _com_member=lambda owner, name: getattr(owner, name),
        NET_CLASS_WIDTHS={"minimum": 7, "typical": 8, "expansion": 9},
    )
    monkeypatch.setattr(B, "active", lambda *args, **kwargs: (None, board, app, doc))
    monkeypatch.setattr(B.board_labels, "_identity", lambda *_: copy.deepcopy(identity))
    monkeypatch.setattr(
        B.board_labels,
        "_application_identity",
        lambda *_: {key: copy.deepcopy(identity[key]) for key in ("application", "generation")},
    )
    monkeypatch.setattr(B, "_observe", native_observe)
    monkeypatch.setattr(B, "_observe_before_transaction", lambda *args: native_observe(*args))
    monkeypatch.setattr(M, "observe_pin", lambda pin, *_: copy.deepcopy(pin.material))
    params = {
        "project": str(project),
        "component_connect_request": request,
        "source_backup": archive["path"],
    }
    return NS(
        params=params,
        adapter=adapter,
        events=events,
        state=state,
        observe=observe,
        request=request,
        board=board,
        project=project,
        identity=identity,
        application=app,
        observations=observations,
        candidate_reads=candidate_reads,
        origin=origin,
        tmp_path=tmp_path,
    )


def apply(case):
    preview = B.execute(case.params, None, case.adapter)
    assert not case.events
    case.params.update(apply=True, expected=preview["trace"]["fingerprint"])
    return B.execute(case.params, None, case.adapter)


def failure(case):
    with pytest.raises(AdapterError) as error:
        apply(case)
    return error.value.details["native_result"]


@pytest.mark.parametrize("count", [2, 4, 7])
def test_generic_isolated_connection_saves_reopens_and_recomputes_complete_receipt(
    tmp_path, monkeypatch, count
):
    case = fixture_case(tmp_path, monkeypatch, count=count)
    result = apply(case)
    assert [row[0] for row in case.events] == [
        "TransactionStart",
        "PutTrace",
        "TransactionEnd",
        "Save",
        "Close",
        "Open",
    ]
    assert case.events[0] == ("TransactionStart", 1)
    assert case.events[1][-3:] == (None, 0, 4)
    assert case.events[2] == ("TransactionEnd", True)
    assert result["saved"] and result["persistence_verified"]
    assert result["verification"]["components_after"] == count - 1
    assert B.verify_receipt(
        result,
        result["trace"],
        {
            "state": result["state_reopened"],
            "board": str(case.board),
            "identity": case.identity,
        },
    )["valid"]
    assert not result["limits"].get("effective_rule_parser", False)


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("start_false", ["TransactionStart"]),
        ("start_none", ["TransactionStart"]),
        ("start_int", ["TransactionStart"]),
        ("put_reply_lost", ["TransactionStart", "PutTrace"]),
        ("end_false", ["TransactionStart", "PutTrace", "TransactionEnd"]),
        ("end_none", ["TransactionStart", "PutTrace", "TransactionEnd"]),
        ("end_int", ["TransactionStart", "PutTrace", "TransactionEnd"]),
        ("new_width", ["TransactionStart", "PutTrace", "TransactionEnd"]),
        ("extra_merge", ["TransactionStart", "PutTrace", "TransactionEnd"]),
        ("returned_old_uid", ["TransactionStart", "PutTrace", "TransactionEnd"]),
        ("new_pieces", ["TransactionStart", "PutTrace", "TransactionEnd"]),
        ("native_opens", ["TransactionStart", "PutTrace", "TransactionEnd"]),
        ("pre_save_drift", ["TransactionStart", "PutTrace", "TransactionEnd"]),
    ],
)
def test_native_or_physical_failure_never_saves_closes_opens_or_retries(
    tmp_path, monkeypatch, fault, expected
):
    case = fixture_case(tmp_path, monkeypatch, fault=fault)
    result = failure(case)
    assert [row[0] for row in case.events] == expected
    assert result["write_attempted"] is True
    assert not result["saved"] and not result["persistence_verified"]
    assert "rollback" not in result


@pytest.mark.parametrize(
    "fault,tail",
    [
        ("save_false", ["Save"]),
        ("save_none", ["Save"]),
        ("save_int", ["Save"]),
        ("close_false", ["Save", "Close"]),
        ("close_none", ["Save", "Close"]),
        ("close_int", ["Save", "Close"]),
        ("close_active", ["Save", "Close"]),
        ("open_bad_return", ["Save", "Close", "Open"]),
        ("open_wrong_path", ["Save", "Close", "Open"]),
        ("reopen_state_drift", ["Save", "Close", "Open"]),
    ],
)
def test_persistence_requires_strict_replies_and_fresh_actual_reopened_state(
    tmp_path, monkeypatch, fault, tail
):
    case = fixture_case(tmp_path, monkeypatch, fault=fault)
    result = failure(case)
    assert [row[0] for row in case.events][3:] == tail
    assert not result["persistence_verified"]


def test_pure_copper_query_is_valid_but_native_accounting_blocks_apply(tmp_path, monkeypatch):
    case = fixture_case(tmp_path, monkeypatch, pure=True)
    result = B.execute(case.params, None, case.adapter)
    assert not result["trace"]["can_apply"]
    assert result["trace"]["blockers"][0]["code"] == "E_NATIVE_TOPOLOGY_INCOMPLETE"
    assert not case.events


@pytest.mark.parametrize(
    "minimum,width,code",
    [
        (0.254, 0.25, "E_KNOWN_MINIMUM_WIDTH_VIOLATION"),
        (None, 0.254, "E_NAMED_WIDTH_OBSERVATION_INCOMPLETE"),
    ],
)
def test_known_minimum_failure_or_absent_read_is_blocked_before_scratch_or_write(
    tmp_path, monkeypatch, minimum, width, code
):
    case = fixture_case(tmp_path, monkeypatch, minimum=minimum)
    case.request["width_mm"] = width
    preview = B.execute(case.params, None, case.adapter)
    assert not preview["trace"]["can_apply"]
    assert code in {row["code"] for row in preview["trace"]["blockers"]}
    assert not case.events


def test_noop_has_no_apply_qualification_and_zero_native_receipt_is_independently_valid(
    tmp_path, monkeypatch
):
    case = fixture_case(tmp_path, monkeypatch)
    case.request["second"] = copy.deepcopy(case.request["first"])
    preview = B.execute(case.params, None, case.adapter)
    assert preview["no_op"] and not preview["trace"]["can_apply"]
    assert not preview["trace"]["blockers"]
    raw, _ = case.observe()
    assert B.verify_receipt(preview, preview["trace"], raw)["valid"]
    assert not case.events
    preview["native_calls_returned"] = [{"call": "Save", "attempted": True, "returned": True}]
    assert not B.verify_receipt(preview, preview["trace"], raw)["valid"]


def test_invalid_or_missing_candidate_evidence_cannot_enable_a_write(tmp_path, monkeypatch):
    case = fixture_case(tmp_path, monkeypatch)
    raw, _ = case.observe()
    inputs = B._inputs(case.params, case.board, case.request)
    plan = B.bind(
        raw, case.request, inputs, case.adapter, case.identity, candidate={"verified": True}
    )
    assert not plan["can_apply"]
    assert "E_CANDIDATE_MATERIAL_INCOMPLETE" in {row["code"] for row in plan["blockers"]}
    assert not case.events


def test_full_file_diff_preserves_server_journal_changes_and_unreadability():
    def inputs(rows=(), logs=(), unread=()):
        return {
            "live_files": list(rows),
            "non_rule_observed_files": list(logs),
            "live_unavailable": list(unread),
        }

    before = inputs(
        rows=[{"path": "protected", "sha256": "a"}],
        logs=[{"path": "serverlog", "bytes": 1}],
        unread=[{"path": "database", "live_read": "unavailable"}],
    )
    after = inputs(
        rows=[{"path": "protected", "sha256": "b"}, {"path": "database", "sha256": "c"}],
        logs=[{"path": "serverlog", "bytes": 2}],
    )
    observed = B.full_file_diff(before, after, [])
    assert len(observed["changes"]) == 3
    assert {row["policy"] for row in observed["changes"]} == {
        "separately_observed_server_journal",
        "protected_dependency_or_unavailable_read",
    }
    assert all(row["engineering_safety_proven"] is False for row in observed["changes"])


@pytest.mark.parametrize(
    "fault",
    [
        "boolean",
        "counter",
        "fresh_state",
        "old_uid",
        "fingerprint",
        "dependency",
        "before_transaction_state",
    ],
)
def test_reader_recomputes_receipts_instead_of_trusting_success_fields(
    tmp_path, monkeypatch, fault
):
    case = fixture_case(tmp_path, monkeypatch)
    result = apply(case)
    bound = copy.deepcopy(result["trace"])
    fresh = copy.deepcopy(result["state_reopened"])
    if fault == "boolean":
        result["native_calls_returned"][5]["reply"] = {"type": "int", "value": 1}
    elif fault == "counter":
        result["counters"]["PutTrace"]["attempted"] = 2
    elif fault == "fresh_state":
        fresh["texts"][0]["text"] = "foreign change"
    elif fault == "old_uid":
        result["state_reopened"]["native_trace_material"][0]["uid"] = "replaced"
    elif fault == "fingerprint":
        bound["request"]["width_mm"] = 0.3
    elif fault == "before_transaction_state":
        result["state_before_transaction"]["rules"]["scheme"] = "forged final fresh design"
    else:
        result["reopened_inputs"]["live_files"][0]["sha256"] = "f" * 64
    assert not B.verify_receipt(result, bound, fresh)["valid"]


@pytest.mark.parametrize("field", ["path", "width", "uid"])
def test_stale_foreign_trace_query_cannot_rebaseline_changed_raw_copper(
    tmp_path, monkeypatch, field
):
    case = fixture_case(tmp_path, monkeypatch)

    def edit_foreign_native(mask_doc, engine):
        native = next(row for row in mask_doc.Traces if row.UniqueId == "protected-other-net")
        routing = next(
            row for row in case.state["routing_objects"] if row["uid"] == native.UniqueId
        )
        raw, width = native.Geometry.values[1], native.Geometry.values[5]
        occupied = copy.deepcopy(engine.paths[(digest(raw), width)])
        if field == "path":
            raw = copy.deepcopy(raw)
            raw[0][0] += 1e-10
            native.Geometry.values[1] = raw
            engine.paths[(digest(raw), width)] = occupied
            routing["geometry"]["path"] = _canonical([list(p) for p in zip(*raw, strict=True)])
        elif field == "width":
            native.Geometry.values[5] = width + 1e-10
            engine.paths[(digest(raw), width + 1e-10)] = occupied
            routing["geometry"]["width"] = _canonical(width + 1e-10)
        else:
            native.UniqueId = routing["uid"] = "replaced-foreign-copper"
            routing["geometry"]["uid"] = native.UniqueId

    current_state, observed = case.observe(edit_foreign_native)
    current = C.build_snapshot(
        current_state,
        observed,
        board_id=str(case.board),
        net_id=case.request["net_uid"],
        layer=1,
    )
    assert current["valid"], current
    # Target partition and the projection that excludes all traces are still
    # identical. The retained full raw/routing inventories must independently bind.
    assert current["snapshot_id"] == case.request["query"]["snapshot_id"]
    assert current["protection_sha256"] == case.request["query"]["protection_sha256"]
    assert current["raw_traces"] != case.request["query"]["raw_traces"]
    with pytest.raises(AdapterError) as error:
        B.execute(case.params, None, case.adapter)
    assert "retained component query" in error.value.details["cause"]
    assert error.value.details["write_attempted"] is False
    assert not case.events


@pytest.mark.parametrize(
    "fault,key",
    [
        ("candidate_rule_drift", "rules"),
        ("candidate_stackup_drift", "geometry"),
        ("candidate_text_drift", "texts"),
    ],
)
def test_complete_fresh_native_design_after_candidate_rejects_unsaved_changes(
    tmp_path, monkeypatch, fault, key
):
    case = fixture_case(tmp_path, monkeypatch, fault=fault)
    result = failure(case)
    assert len(case.candidate_reads) == 3
    assert (
        "Complete native design changed after candidate cleanup"
        in result["post_write_error"]["message"]
    )
    assert result["state_before_transaction"][key] != result["trace"]["before"][key]
    assert result["write_attempted"] is False
    assert result["native_calls_returned"] == []
    assert not case.events
    assert not result["saved"] and not result["persistence_verified"]


def test_continuation_rejects_cropped_native_result_even_with_current_backup(tmp_path, monkeypatch):
    case = fixture_case(tmp_path, monkeypatch)
    result = apply(case)
    receipt_file = json_file(tmp_path / "saved-receipt.json", result)
    current_archive, _ = backup(
        tmp_path / "own-current-backup.zip", case.project.parent, case.project
    )
    case.origin["continuation_receipt"] = receipt_file
    case.request["isolated_origin"] = copy.deepcopy(case.origin)
    case.request["isolated_origin_file"] = json_file(tmp_path / "origin.json", case.origin)

    def register_next_candidate(_doc, engine):
        engine.paths[(digest([[6, 12], [0, 0], [0.0, 0.0]]), 0.254)] = cells(6, 0, 13, 1)

    raw, observed = case.observe(register_next_candidate)
    query = C.build_snapshot(
        raw, observed, board_id=str(case.board), net_id=case.request["net_uid"], layer=1
    )
    case.request.update(
        query=query,
        snapshot_id=query["snapshot_id"],
        first={
            "node_id": next(
                row["node_id"] for row in query["nodes"] if row.get("pin_uid") == "pad-owner-1"
            )
        },
        second={
            "node_id": next(
                row["node_id"] for row in query["nodes"] if row.get("pin_uid") == "pad-owner-2"
            )
        },
        points=[[6, 0], [12, 0]],
    )
    case.request["query_file"] = json_file(tmp_path / "query.json", query)
    case.params.pop("apply")
    case.params["source_backup"] = current_archive["path"]
    before_events = copy.deepcopy(case.events)
    with pytest.raises(AdapterError) as error:
        B.execute(case.params, None, case.adapter)
    assert "complete SDK receipt" in error.value.details["cause"]
    assert case.events == before_events
    assert case.origin["genesis"]["board_sha256"] != pin(case.board)["sha256"]


@pytest.mark.parametrize(
    "fault", ["verified", "origin_hash", "genesis", "same_tree", "changed_query"]
)
def test_origin_and_retained_query_cannot_be_replaced_with_manual_proof(
    tmp_path, monkeypatch, fault
):
    case = fixture_case(tmp_path, monkeypatch)
    if fault == "changed_query":
        Path(case.request["query_file"]["path"]).write_text("{}", encoding="utf-8")
    else:
        if fault == "verified":
            case.origin["verified"] = True
        elif fault == "origin_hash":
            case.origin["origin_board"]["sha256"] = "f" * 64
        elif fault == "genesis":
            case.origin["genesis"]["board_sha256"] = "f" * 64
        else:
            case.origin["target_project"]["path"] = case.origin["origin_project"]["path"]
        case.request["isolated_origin"] = copy.deepcopy(case.origin)
        case.request["isolated_origin_file"] = json_file(tmp_path / "origin.json", case.origin)
    with pytest.raises((ValueError, AdapterError)):
        B.execute(case.params, None, case.adapter)
    assert not case.events


DESCRIPTOR = b'SECTION PCB\r\nKEY iCDBSessionID "136"\r\nKEY Design "unchanged"\r\nENDSECTION\r\n'


def _bytes_pin(data):
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _post_open_fixture(case, extras=None):
    source_project = Path(case.origin["origin_project"]["path"])
    source_board = Path(case.origin["origin_board"]["path"])
    source_board.write_bytes(DESCRIPTOR)
    case.board.write_bytes(DESCRIPTOR)
    extras = dict(extras or {})
    for filename in ("LayoutDB.lyt", "LogicDB.lgc", "JobPrefsDB.jpf"):
        first, second = "PCB/Layout/" + filename, "PCB/Work/Layout_Temp/" + filename
        if first in extras and second not in extras:
            extras[second] = extras[first]
        elif second in extras and first not in extras:
            extras[first] = extras[second]
    for relative, data in extras.items():
        for root in (source_project.parent, case.project.parent):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    archive_pin, manifest = backup(
        Path(case.params["source_backup"]), source_project.parent, source_project
    )
    case.origin["origin_board"] = pin(source_board)
    case.origin["source_backup"] = archive_pin
    case.origin["genesis"] = {
        "project_sha256": manifest[source_project.name],
        "board_sha256": manifest["PCB/Board.pcb"],
        "file_manifest": manifest,
    }
    case.request["isolated_origin"] = copy.deepcopy(case.origin)
    case.request["isolated_origin_file"] = json_file(case.tmp_path / "origin.json", case.origin)
    case.board.write_bytes(DESCRIPTOR.replace(b'"136"', b'"138"'))


def test_descriptor_open_change_pins_full_bytes_and_only_one_decimal_session_field():
    current = DESCRIPTOR.replace(b'"136"', b'"138"')
    evidence = B.descriptor_open_transition(
        DESCRIPTOR, current, _bytes_pin(DESCRIPTOR), _bytes_pin(current)
    )
    assert evidence["byte_equal"] is False
    assert evidence["genesis"]["raw_hex"] == DESCRIPTOR.hex()
    assert evidence["current"]["raw_hex"] == current.hex()
    assert evidence["field_changes"][0]["before"] == "136"
    assert evidence["field_changes"][0]["after"] == "138"
    assert B.descriptor_open_transition(DESCRIPTOR, DESCRIPTOR, {}, {})["byte_equal"]


@pytest.mark.parametrize(
    "current",
    [
        DESCRIPTOR.replace(b'"136"', b'"138"').replace(b"unchanged", b"modified"),
        DESCRIPTOR.replace(b'"136"', b'"138"').replace(b"\r\n", b"\n"),
        DESCRIPTOR.replace(b'"136"', b'"138"') + b'KEY iCDBSessionID "139"\r\n',
        DESCRIPTOR.replace(b'"136"', b'"nonnumeric"'),
        DESCRIPTOR.replace(b"iCDBSessionID", b"OtherKey"),
    ],
)
def test_descriptor_other_bytes_newlines_duplicate_or_nondecimal_fields_are_rejected(current):
    with pytest.raises(ValueError):
        B.descriptor_open_transition(
            DESCRIPTOR, current, _bytes_pin(DESCRIPTOR), _bytes_pin(current)
        )


def test_first_post_open_state_accepts_only_session_change_and_retains_other_output_diff(
    tmp_path, monkeypatch
):
    case = fixture_case(tmp_path, monkeypatch)
    _post_open_fixture(
        case,
        {
            "Library/cells.cel": b"synthetic library",
            "PCB/Layout/LayoutDB.lyt": b"synthetic layout",
            "PCB/Config/Rules.dat": b"synthetic config",
            "PCB/Config/User.bak": b"old backup",
            "PCB/Work/Open.log": b"old output",
        },
    )
    (case.board.parent / "Config/User.bak").write_bytes(b"observed changed backup")
    (case.board.parent / "Work/Open.log").write_bytes(b"observed changed output")
    preview = B.execute(case.params, None, case.adapter)
    assert preview["trace"]["can_apply"], preview
    evidence = preview["trace"]["origin"]["post_open_genesis_evidence"]
    assert evidence["descriptor"]["field_changes"][0]["after"] == "138"
    assert len(evidence["critical_genesis_files"]) == 3
    assert {row["relative_path"] for row in evidence["all_genesis_differences"]} == {
        "PCB/Board.pcb",
        "PCB/Config/User.bak",
        "PCB/Work/Open.log",
    }
    assert all(
        row["engineering_safety_proven"] is False for row in evidence["all_genesis_differences"]
    )
    assert not case.events


@pytest.mark.parametrize(
    "relative", ["Library/cells.cel", "PCB/Layout/LayoutDB.lyt", "PCB/Config/Rules.dat"]
)
def test_initial_readable_critical_files_must_still_equal_genesis(tmp_path, monkeypatch, relative):
    case = fixture_case(tmp_path, monkeypatch)
    _post_open_fixture(case, {relative: b"strict synthetic genesis"})
    (case.project.parent / relative).write_bytes(b"unregistered critical drift")
    with pytest.raises(AdapterError) as error:
        B.execute(case.params, None, case.adapter)
    assert "critical post-Open dependency differs" in error.value.details["cause"]
    assert not case.events


def test_only_pinned_existing_two_layout_storage_outputs_are_declared_and_recomputed(
    tmp_path, monkeypatch
):
    case = fixture_case(tmp_path, monkeypatch)
    _post_open_fixture(
        case,
        {
            "PCB/Layout/LayoutDB.lyt": b"old synthetic persistent copper",
            "PCB/Work/Layout_Temp/LayoutDB.lyt": b"old synthetic persistent copper",
            "PCB/Work/unrelated.bin": b"protected unrelated bytes",
        },
    )
    preview = B.execute(case.params, None, case.adapter)
    plan = preview["trace"]
    outputs = plan["allowed_output_files"]
    assert len(outputs) == 3
    assert {row["role"] for row in outputs} == {"primary_board", "native_copper_storage"}
    assert all(row["may_create"] is False for row in outputs)
    before, after = plan["input_files"], copy.deepcopy(plan["input_files"])
    for row in after["live_files"]:
        if row["path"].endswith("LayoutDB.lyt"):
            row["bytes"] += 1
            row["sha256"] = "b" * 64
    assert B.storage_dependency_changes(before, after, plan)["valid"]
    evidence = B.full_file_diff(before, after, outputs)
    assert len(evidence["changes"]) == 2
    assert all(
        row["policy"] == "declared_existing_native_copper_storage" for row in evidence["changes"]
    )
    for row in after["live_files"]:
        if row["path"].endswith("unrelated.bin"):
            row["sha256"] = "c" * 64
    assert not B.storage_dependency_changes(before, after, plan)["valid"]
    forged = copy.deepcopy(plan)
    forged["allowed_output_files"].append(
        {"path": "another.bin", "role": "native_copper_storage", "may_create": True}
    )
    assert not B.storage_dependency_changes(before, before, forged)["valid"]


def test_native_layout_storage_write_still_requires_complete_physical_proof(tmp_path, monkeypatch):
    case = fixture_case(tmp_path, monkeypatch)
    _post_open_fixture(
        case,
        {
            "PCB/Layout/LayoutDB.lyt": b"synthetic original layout",
            "PCB/Work/Layout_Temp/LayoutDB.lyt": b"synthetic original layout",
        },
    )
    doc = case.adapter._licensed_document(None)
    original_save = doc.Save

    def save_with_native_storage():
        returned = original_save()
        for relative in ("Layout/LayoutDB.lyt", "Work/Layout_Temp/LayoutDB.lyt"):
            (case.board.parent / relative).write_bytes(b"synthetic new copper storage")
        return returned

    doc.Save = save_with_native_storage
    result = apply(case)
    assert result["persistence_verified"] and B.verify_saved_receipt(result)["valid"]
    assert result["verification"]["valid"] and result["verification"]["requested_geometry_matches"]
    changes = result["full_file_diffs"]["save"]["changes"]
    assert len(changes) == 3
    assert sum(row["policy"] == "declared_existing_native_copper_storage" for row in changes) == 2
    assert all(row["engineering_safety_proven"] is False for row in changes)


def test_end_can_reassign_only_new_trace_uid_without_changing_old_raw_or_explicit_geometry(
    tmp_path, monkeypatch
):
    case = fixture_case(tmp_path, monkeypatch, fault="returned_uid")
    result = apply(case)
    assert result["trace_uid"] == result["put_trace_returned_uid_before_end"] == "transient-return"
    assert result["actual_created_trace_uids"] == ["new-connection"]
    assert result["trace_identity_evidence"]["new_uid_reassigned_at_end"] is True
    assert result["trace_identity_evidence"]["returned_uid_present_after_end"] is False
    assert result["verification"]["requested_geometry_matches"] is True
    assert B.verify_saved_receipt(result)["valid"]
    assert [row[0] for row in case.events] == [
        "TransactionStart",
        "PutTrace",
        "TransactionEnd",
        "Save",
        "Close",
        "Open",
    ]


@pytest.mark.parametrize("fact", ["returned_uid", "actual_uid_set", "derived_identity"])
def test_reader_recomputes_returned_and_final_created_uid_facts_independently(
    tmp_path, monkeypatch, fact
):
    case = fixture_case(tmp_path, monkeypatch, fault="returned_uid")
    result = apply(case)
    if fact == "returned_uid":
        result["put_trace_returned_uid_before_end"] = "new-connection"
    elif fact == "actual_uid_set":
        result["actual_created_trace_uids"] = ["transient-return"]
    else:
        result["trace_identity_evidence"]["new_uid_reassigned_at_end"] = False
    assert not B.verify_saved_receipt(result)["valid"]


@pytest.mark.parametrize("coordinate", [0.00001, 43.828394, 1.23450001])
def test_more_than_four_decimal_new_points_are_rejected_without_rounding_or_native_calls(
    tmp_path, monkeypatch, coordinate
):
    case = fixture_case(tmp_path, monkeypatch)
    case.request["points"] = [[0, 0], [coordinate, 6]]
    with pytest.raises(AdapterError) as error:
        B.execute(case.params, None, case.adapter)
    assert "four decimal places" in error.value.details["cause"]
    assert case.request["points"][1][0] == coordinate
    assert not case.events and not case.observations


def test_old_raw_copper_with_six_decimal_vertices_is_preserved_without_rounding(
    tmp_path, monkeypatch
):
    case = fixture_case(tmp_path, monkeypatch)

    def old_native_precision(doc, engine):
        trace = next(row for row in doc.Traces if row.UniqueId == "protected-other-net")
        raw, width = copy.deepcopy(trace.Geometry.values[1]), trace.Geometry.values[5]
        occupied = copy.deepcopy(engine.paths[(digest(raw), width)])
        raw[0][0] = 50.123456
        trace.Geometry.values[1] = raw
        trace.values[7].values[1] = raw[0][0] - width / 2
        engine.paths[(digest(raw), width)] = occupied
        routing = next(row for row in case.state["routing_objects"] if row["uid"] == trace.UniqueId)
        routing["geometry"]["path"] = _canonical([list(point) for point in zip(*raw, strict=True)])

    raw, observed = case.observe(old_native_precision)
    query = C.build_snapshot(
        raw, observed, board_id=str(case.board), net_id=case.request["net_uid"], layer=1
    )
    assert query["valid"], query
    case.request.update(query=query, snapshot_id=query["snapshot_id"])
    case.request["query_file"] = json_file(case.tmp_path / "query.json", query)
    result = apply(case)
    old = next(
        row
        for row in result["trace"]["snapshot"]["raw_traces"]
        if row["uid"] == "protected-other-net"
    )
    final = next(
        row
        for row in result["state_reopened"]["native_trace_material"]
        if row["uid"] == "protected-other-net"
    )
    assert old == final and final["points_array_mm"][0][0] == 50.123456


def test_missing_recovery_path_is_explicit_blocker_and_noop_has_no_artifact(tmp_path, monkeypatch):
    case = fixture_case(tmp_path, monkeypatch)
    case.request["recovery_backup"] = None
    planned = B.execute(case.params, None, case.adapter)
    assert not planned["trace"]["can_apply"]
    assert any(row["code"] == "E_RECOVERY_BACKUP_REQUIRED" for row in planned["trace"]["blockers"])
    case.request["second"] = copy.deepcopy(case.request["first"])
    noop = B.execute(case.params, None, case.adapter)
    assert noop["no_op"] and noop["trace"]["recovery_plan"] is None
    assert noop["trace"]["closed_snapshot_sampling"] is None
    assert "closed_snapshot_sampling" not in noop
    assert "recovery_archive" not in noop and not case.events


def test_closed_window_archive_proves_real_icdb_bytes_without_rewriting_old_opaque_pin(
    tmp_path, monkeypatch
):
    case = fixture_case(tmp_path, monkeypatch)
    original_inputs = B.R.input_files
    icdb = case.project.parent / "database/icdb.dat"

    def live_locked(project, board, archive):
        inputs = original_inputs(project, board, archive)
        if Path(project) == case.project and case.adapter._licensed_document(None) is not None:
            inputs["live_files"] = [row for row in inputs["live_files"] if row["path"] != str(icdb)]
            inputs["live_unavailable"] = [
                {
                    "path": str(icdb),
                    "live_read": "unavailable",
                    "source_only_sha256": inputs["source_backup"]["icdb_source_sha256"],
                }
            ]
        return inputs

    monkeypatch.setattr(B.R, "input_files", live_locked)
    doc = case.adapter._licensed_document(None)
    original_save = doc.Save

    def save_new_icdb():
        returned = original_save()
        icdb.write_bytes(b"synthetic persisted native icdb bytes")
        return returned

    doc.Save = save_new_icdb
    result = apply(case)
    old = result["trace"]["input_files"]["live_unavailable"][0]
    closed = result["closed_dependency_changes"]["icdb"]
    assert closed["before"]["observation"] == old
    assert closed["bytes_comparable"] is False and closed["bytes_equal"] is None
    assert closed["after"]["observation"] == pin(icdb)
    assert old["source_only_sha256"] != pin(icdb)["sha256"]
    assert result["reopened_inputs"]["live_unavailable"][0] == old
    assert result["recovery_archive"]["archive"]["path"] == case.request["recovery_backup"]
    assert B.K.verify_archive(result["recovery_archive"])["valid"]
    assert B.verify_saved_receipt(result)["valid"]


def test_closed_unknown_work_change_keeps_save_close_facts_and_stops_before_archive_open(
    tmp_path, monkeypatch
):
    case = fixture_case(tmp_path, monkeypatch)
    _post_open_fixture(case, {"PCB/Work/unrelated.bin": b"protected old work bytes"})
    doc = case.adapter._licensed_document(None)
    original_close = doc.Close

    def close_with_unknown_work(save):
        returned = original_close(save)
        (case.board.parent / "Work/unrelated.bin").write_bytes(b"undeclared native side effect")
        return returned

    doc.Close = close_with_unknown_work
    result = failure(case)
    assert result["saved"] is True
    assert result["stages"]["close"] == "confirmed"
    assert result["closed_dependency_changes"]["valid"] is False
    assert not any(row[0] == "Open" for row in case.events)
    assert not Path(case.request["recovery_backup"]).exists()


def test_recovery_output_created_after_plan_is_never_overwritten_or_followed_by_open(
    tmp_path, monkeypatch
):
    case = fixture_case(tmp_path, monkeypatch)
    doc = case.adapter._licensed_document(None)
    original_close = doc.Close
    recovery = Path(case.request["recovery_backup"])

    def close_with_concurrent_output(save):
        returned = original_close(save)
        recovery.write_bytes(b"retain concurrent output verbatim")
        return returned

    doc.Close = close_with_concurrent_output
    result = failure(case)
    assert result["saved"] is True and result["stages"]["close"] == "confirmed"
    assert recovery.read_bytes() == b"retain concurrent output verbatim"
    assert not any(row[0] == "Open" for row in case.events)


@pytest.mark.parametrize("tamper", ["archive", "closed_tree", "visibility", "closed_descriptor"])
def test_reader_recomputes_closed_archive_all_members_and_visibility_facts(
    tmp_path, monkeypatch, tamper
):
    case = fixture_case(tmp_path, monkeypatch)
    result = apply(case)
    if tamper == "archive":
        Path(result["recovery_archive"]["archive"]["path"]).write_bytes(b"changed archive")
    elif tamper == "closed_tree":
        result["closed_tree"]["files"][0]["sha256"] = "e" * 64
    elif tamper == "visibility":
        result["closed_dependency_changes"]["icdb"]["bytes_equal"] = None
    else:
        result["closed_descriptor_pin"]["raw_hex"] = b"different closed bytes".hex()
    assert not B.verify_saved_receipt(result)["valid"]


def _native_db_pairs(case):
    _post_open_fixture(
        case,
        {
            "PCB/Layout/LogicDB.lgc": b"original mirrored logic store",
            "PCB/Layout/JobPrefsDB.jpf": b"original mirrored job preferences",
        },
    )


def test_save_only_exact_declared_db_pairs_are_persisted_in_same_closed_window(
    tmp_path, monkeypatch
):
    case = fixture_case(tmp_path, monkeypatch)
    _native_db_pairs(case)
    doc = case.adapter._licensed_document(None)
    original_save = doc.Save

    def save_mirrored_db_pairs():
        reply = original_save()
        for directory in ("Layout", "Work/Layout_Temp"):
            for filename in ("LogicDB.lgc", "JobPrefsDB.jpf"):
                (case.board.parent / directory / filename).write_bytes(b"new mirrored native store")
        return reply

    doc.Save = save_mirrored_db_pairs
    result = apply(case)
    assert result["save_dependency_changes"]["save_policy"]["valid"]
    assert len(result["trace"]["save_policy"]["declaration"]["allowed_output_files"]) == 4
    assert B.verify_saved_receipt(result)["valid"]
    assert (
        result["recovery_archive"]["manifest"]["sha256"]["PCB/Layout/LogicDB.lgc"]
        == (pin(case.board.parent / "Layout/LogicDB.lgc")["sha256"])
    )


def test_mismatched_db_mirror_after_true_save_stops_before_close_and_archive(tmp_path, monkeypatch):
    case = fixture_case(tmp_path, monkeypatch)
    _native_db_pairs(case)
    doc = case.adapter._licensed_document(None)
    original_save = doc.Save

    def save_one_db_member():
        reply = original_save()
        (case.board.parent / "Layout/LogicDB.lgc").write_bytes(b"unmirrored changed bytes")
        return reply

    doc.Save = save_one_db_member
    result = failure(case)
    assert result["saved"] is True and result["save_dependency_changes"]["valid"] is False
    assert not any(row[0] in ("Close", "Open") for row in case.events)
    assert not Path(case.request["recovery_backup"]).exists()


def test_save_policy_roles_do_not_authorize_later_open_changes(tmp_path, monkeypatch):
    case = fixture_case(tmp_path, monkeypatch)
    _native_db_pairs(case)
    original_open = case.adapter._open_layout_document

    def open_with_changed_logic(*args):
        opened = original_open(*args)
        for directory in ("Layout", "Work/Layout_Temp"):
            (case.board.parent / directory / "LogicDB.lgc").write_bytes(b"changed during Open")
        return opened

    case.adapter._open_layout_document = open_with_changed_logic
    result = failure(case)
    assert result["saved"] is True and result["reopen_dependency_changes"]["valid"] is False
    assert result["persistence_verified"] is False
    assert B.K.verify_archive(result["recovery_archive"])["valid"]


def test_unknown_existing_hkp_syntax_is_blocked_before_any_native_transaction(
    tmp_path, monkeypatch
):
    case = fixture_case(tmp_path, monkeypatch)
    _post_open_fixture(case, {"PCB/Config/Graphics Settings.hkp": b"unsupported HKP syntax"})
    result = B.execute(case.params, None, case.adapter)
    assert not result["trace"]["can_apply"]
    assert any(row["code"] == "E_SAVE_POLICY_INCOMPLETE" for row in result["trace"]["blockers"])
    assert not case.events


def test_explicit_server_journal_policy_does_not_become_stale_through_save_raw_receipts(
    tmp_path, monkeypatch
):
    case = fixture_case(tmp_path, monkeypatch)
    _post_open_fixture(case, {"database/cdbsvr/log/session.txt": b"old server journal"})
    planned = B.execute(case.params, None, case.adapter)
    (case.project.parent / "database/cdbsvr/log/session.txt").write_bytes(b"new server journal")
    case.params.update(apply=True, expected=planned["trace"]["fingerprint"])
    result = B.execute(case.params, None, case.adapter)
    assert result["persistence_verified"] and B.verify_saved_receipt(result)["valid"]


SESSION_MARKERS = {
    "PCB/AutoActive.lck": b"synthetic active-session marker",
    "PCB/Work/Layout_Temp/LayoutTemp.tmp": b"synthetic temporary session marker",
    "database/cdbsvr/sAddress.adr": b"synthetic session address",
    "PCB/LogFiles/ForwardAnnotation.txt": b"",
    "PCB/LogFiles/PCBInfoFile.txt": b"",
}


def _runtime_marker_fixture(
    case,
    *,
    close_remove=True,
    reopen_create=True,
    wrong_job=False,
    historical_log=b"",
    initial_log=None,
    reopened_log=None,
):
    _post_open_fixture(
        case,
        {
            "PCB/Layout/LayoutDB.lyt": b"unchanged synthetic copper storage",
            "PCB/LogFiles/PCBAutoSave.txt": historical_log,
            "PCB/Work/unrelated.bin": b"protected unknown Work bytes",
        },
    )
    autosave = case.board.parent / "LogFiles/PCBAutoSave.txt"
    if initial_log is not None:
        # This models actual initial native Open output while retaining the
        # complete historical source ZIP and source directory bytes.
        autosave.write_bytes(initial_log)

    def markers(suffix=b""):
        for relative, raw in SESSION_MARKERS.items():
            path = case.project.parent / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw + suffix if raw else b"")

    markers()
    doc = case.adapter._licensed_document(None)
    original_close, original_open = doc.Close, case.adapter._open_layout_document

    def close_session(save):
        reply = original_close(save)
        if close_remove:
            # This synthetic native Close models observed disappearance. The
            # production policy does not delete or unlock any file.
            for relative in SESSION_MARKERS:
                (case.project.parent / relative).unlink()
        log = case.project.parent / "PCB/LogFiles/PCBAutoSave.txt"
        name = case.board.with_name("wrong.pcb") if wrong_job else case.board
        text = (
            "\r\n                                自动保存日志已关闭\r\n"
            "                                ------------------\r\n\r\n"
            "                       10:32 AM Thursday, October 22, 2026\r\n"
            "Job Name: " + str(name) + "\r\n\r\n"
        )
        log.write_bytes(log.read_bytes() + text.encode("cp936"))
        return reply

    def open_session(*args):
        opened = original_open(*args)
        if reopen_create:
            markers(b" after actual Open")
        if reopened_log is not None:
            autosave.write_bytes(reopened_log)
        return opened

    doc.Close = close_session
    case.adapter._open_layout_document = open_session
    return doc, markers


def test_exact_runtime_markers_close_and_reopen_with_full_same_window_raw_evidence(
    tmp_path, monkeypatch
):
    case = fixture_case(tmp_path, monkeypatch)
    _runtime_marker_fixture(case)
    result = apply(case)
    plan = result["trace"]
    assert result["persistence_verified"] and B.verify_saved_receipt(result)["valid"]
    assert len(plan["allowed_output_files"]) == 3
    assert len(plan["save_policy"]["declaration"]["runtime_markers"]) == 5
    source = plan["origin"]["current_closed_source"]
    assert source == plan["save_policy"]["declaration"]["source_archive_proof"]
    assert not set(SESSION_MARKERS) & source["manifest"]["sha256"].keys()
    assert B.K.verify_archive(result["recovery_archive"])["valid"]
    assert not set(SESSION_MARKERS) & result["recovery_archive"]["manifest"]["sha256"].keys()
    for key, phase in (
        ("save_dependency_changes", "save"),
        ("closed_dependency_changes", "close"),
        ("reopen_dependency_changes", "open"),
    ):
        assert result[key]["save_policy"]["valid"]
        assert result[key]["save_policy"]["phase"] == phase
    runtime_paths = {str(case.project.parent / name) for name in SESSION_MARKERS}
    closed_rows = {row["path"]: row for row in result["closed_policy_observation"]["files"]}
    assert all(closed_rows[path]["pin"] is None for path in runtime_paths)
    assert runtime_paths <= {row["path"] for row in result["full_file_diffs"]["close"]["changes"]}
    assert runtime_paths <= {row["path"] for row in result["full_file_diffs"]["open"]["changes"]}
    assert all(
        row["engineering_safety_proven"] is False
        for row in result["full_file_diffs"]["close"]["changes"]
    )


def test_close_does_not_have_to_remove_runtime_markers(tmp_path, monkeypatch):
    case = fixture_case(tmp_path, monkeypatch)
    _runtime_marker_fixture(case, close_remove=False, reopen_create=False)
    result = apply(case)
    assert result["persistence_verified"] and B.verify_saved_receipt(result)["valid"]
    assert set(SESSION_MARKERS) <= result["recovery_archive"]["manifest"]["sha256"].keys()


@pytest.mark.parametrize("change", ["remove", "rewrite"])
def test_save_cannot_delete_or_rebaseline_a_runtime_marker(tmp_path, monkeypatch, change):
    case = fixture_case(tmp_path, monkeypatch)
    doc, _ = _runtime_marker_fixture(case)
    original = doc.Save

    def save_changed_marker():
        reply = original()
        marker = case.board.parent / "AutoActive.lck"
        if change == "remove":
            marker.unlink()
        else:
            marker.write_bytes(b"changed during Save")
        return reply

    doc.Save = save_changed_marker
    result = failure(case)
    assert result["saved"] is True and not result["persistence_verified"]
    assert not any(row[0] in ("Close", "Open") for row in case.events)


def test_runtime_marker_policy_does_not_extend_transaction_storage_outputs(tmp_path, monkeypatch):
    case = fixture_case(tmp_path, monkeypatch)
    doc, _ = _runtime_marker_fixture(case)
    original = doc.TransactionEnd

    def changed_marker(keep):
        reply = original(keep)
        (case.board.parent / "AutoActive.lck").write_bytes(b"changed during transaction")
        return reply

    doc.TransactionEnd = changed_marker
    result = failure(case)
    assert result["dependency_change_evidence"]["valid"] is False
    assert not any(row[0] in ("Save", "Close", "Open") for row in case.events)


def test_close_cannot_remove_unknown_work_file_with_the_session_markers(tmp_path, monkeypatch):
    case = fixture_case(tmp_path, monkeypatch)
    doc, _ = _runtime_marker_fixture(case)
    original = doc.Close

    def removed_unknown(save):
        reply = original(save)
        (case.board.parent / "Work/unrelated.bin").unlink()
        return reply

    doc.Close = removed_unknown
    result = failure(case)
    assert result["saved"] and not result["persistence_verified"]
    assert not any(row[0] == "Open" for row in case.events)
    assert not Path(case.request["recovery_backup"]).exists()


@pytest.mark.parametrize("change", ["existing_marker", "nonempty_report", "unknown_file"])
def test_open_can_create_only_bounded_markers_absent_in_the_closed_window(
    tmp_path, monkeypatch, change
):
    case = fixture_case(tmp_path, monkeypatch)
    _runtime_marker_fixture(case, close_remove=change != "existing_marker")
    original = case.adapter._open_layout_document

    def opened_bad_artifact(*args):
        opened = original(*args)
        if change == "nonempty_report":
            (case.board.parent / "LogFiles/PCBInfoFile.txt").write_bytes(b"unexpected report")
        elif change == "unknown_file":
            (case.board.parent / "Work/new-session-like.tmp").write_bytes(b"not one of the five")
        return opened

    case.adapter._open_layout_document = opened_bad_artifact
    result = failure(case)
    assert result["saved"] and not result["persistence_verified"]
    assert any(row[0] == "Open" for row in case.events)
    assert B.K.verify_archive(result["recovery_archive"])["valid"]


def test_autosave_append_requires_the_actual_board_job_name_before_open(tmp_path, monkeypatch):
    case = fixture_case(tmp_path, monkeypatch)
    _runtime_marker_fixture(case, wrong_job=True)
    result = failure(case)
    assert result["saved"] and not result["persistence_verified"]
    assert not any(row[0] == "Open" for row in case.events)


@pytest.mark.parametrize("tamper", ["source_absence", "closed_presence", "opened_raw"])
def test_reader_recomputes_runtime_source_absence_and_each_phase_raw_observation(
    tmp_path, monkeypatch, tamper
):
    case = fixture_case(tmp_path, monkeypatch)
    _runtime_marker_fixture(case)
    result = apply(case)
    if tamper == "source_absence":
        result["trace"]["origin"]["current_closed_source"]["manifest"]["sha256"][
            "PCB/AutoActive.lck"
        ] = "e" * 64
    elif tamper == "closed_presence":
        row = next(
            row
            for row in result["closed_policy_observation"]["files"]
            if row["path"].endswith("AutoActive.lck")
        )
        row["pin"] = pin(case.board.parent / "AutoActive.lck")
    else:
        row = next(
            row
            for row in result["reopened_policy_observation"]["files"]
            if row["path"].endswith("AutoActive.lck")
        )
        row["raw_hex"] = b"different marker bytes".hex()
    assert not B.verify_saved_receipt(result)["valid"]


@pytest.mark.parametrize("closed_absent", [True, False])
def test_continuation_marker_changes_require_the_authenticated_previous_closed_absence(
    tmp_path, monkeypatch, closed_absent
):
    case = fixture_case(tmp_path, monkeypatch)
    _, markers = _runtime_marker_fixture(
        case, close_remove=closed_absent, reopen_create=closed_absent
    )
    result = apply(case)
    old_source = copy.deepcopy(result["reopened_inputs"]["source_backup"])
    markers(b" from a subsequent native Open")
    case.params["source_backup"] = result["recovery_archive"]["archive"]["path"]
    current = B._inputs(case.params, case.board, case.request)
    source = B._source_archive_proof(current)
    events = copy.deepcopy(case.events)
    if closed_absent:
        proof = B._continuation_files(result, current, source)
        assert proof["dependency_changes"]["valid"]
        assert proof["dependency_changes"]["save_policy"]["phase"] == "continuation"
        assert proof["current_policy_observation"]["inputs"] == current
        assert result["reopened_inputs"]["source_backup"] == old_source
        # The native plan shares this evidence in two fields; a durable JSON
        # receipt reconstructs separate equal objects. Both forms must bind the
        # same fingerprint while preserving every original raw observation.
        shared = copy.deepcopy(result["trace"])
        shared["origin"]["post_open_genesis_evidence"]["protected_files_chain"] = proof
        shared["origin"]["continuation_sdk_evidence"] = {"files": proof}
        retained = copy.deepcopy(shared)
        assert B._fingerprint(shared) == B._fingerprint(json.loads(json.dumps(shared)))
        assert shared == retained
    else:
        with pytest.raises(ValueError):
            B._continuation_files(result, current, source)
    assert case.events == events


@pytest.mark.parametrize("historical_source", [False, True])
def test_autosave_closed_nonempty_to_open_empty_keeps_source_and_all_phase_evidence(
    tmp_path, monkeypatch, historical_source
):
    from test_component_save_policy import source_081_log

    case = fixture_case(tmp_path, monkeypatch)
    historical = source_081_log() if historical_source else b""
    _runtime_marker_fixture(case, historical_log=historical, initial_log=b"", reopened_log=b"")
    original_source = copy.deepcopy(case.origin["source_backup"])
    result = apply(case)
    plan, archive = result["trace"], result["recovery_archive"]
    declaration = plan["save_policy"]["declaration"]
    autosave = str(case.board.parent / "LogFiles/PCBAutoSave.txt")
    assert result["persistence_verified"] and B.verify_saved_receipt(result)["valid"]
    assert len(plan["allowed_output_files"]) == 3
    assert [row["path"] for row in declaration["open_output_files"]] == [autosave]
    initial = declaration["autosave_initial_open"]
    assert bytes.fromhex(initial["source"]["raw_hex"]) == historical
    assert initial["source"]["bytes"] == (632 if historical_source else 0)
    assert initial["current"]["bytes"] == 0 and initial["tool_reset_dispatched"] is False
    assert initial["transition"] == (
        "observed_empty_after_open" if historical_source else "source_equal"
    )
    observations = {}
    for key in (
        "pre_save_policy_observation",
        "after_save_policy_observation",
        "closed_policy_observation",
        "reopened_policy_observation",
    ):
        observations[key] = next(row for row in result[key]["files"] if row["path"] == autosave)
    assert observations["pre_save_policy_observation"]["raw_hex"] == ""
    assert observations["after_save_policy_observation"]["raw_hex"] == ""
    closed = observations["closed_policy_observation"]
    assert closed["pin"]["bytes"] > 0 and closed["raw_hex"]
    assert observations["reopened_policy_observation"]["pin"]["bytes"] == 0
    assert observations["reopened_policy_observation"]["raw_hex"] == ""
    assert archive["manifest"]["sha256"]["PCB/LogFiles/PCBAutoSave.txt"] == closed["pin"]["sha256"]
    open_proof = result["reopen_dependency_changes"]
    assert open_proof["valid"] and open_proof["save_policy"]["valid"]
    log_proof = next(row for row in open_proof["save_policy"]["files"] if row["path"] == autosave)
    assert log_proof["log"]["transition"] == "observed_empty_after_open"
    assert log_proof["log"]["tool_reset_dispatched"] is False
    permitted = open_proof["protected_dependency_changes"]["allowed_output_files"]
    assert {row["path"] for row in permitted} == {
        *[row["path"] for row in plan["allowed_output_files"]],
        autosave,
    }
    difference = next(
        row for row in result["full_file_diffs"]["open"]["changes"] if row["path"] == autosave
    )
    assert difference["policy"] == "declared_save_side_effect:native_autosave_log"
    assert difference["engineering_safety_proven"] is False
    assert pin(original_source["path"]) == original_source
    assert B.K.verify_archive(archive)["valid"]
    assert B._fingerprint(plan) == B._fingerprint(json.loads(json.dumps(plan)))

    # The next source is the actual nonempty same-window closed ZIP. The
    # current live log stays empty; its original observation is not rewritten.
    prior_inputs = copy.deepcopy(result["reopened_inputs"])
    case.params["source_backup"] = archive["archive"]["path"]
    current = B._inputs(case.params, case.board, case.request)
    continuation = B._continuation_files(result, current, B._source_archive_proof(current))
    assert continuation["dependency_changes"]["valid"]
    assert result["reopened_inputs"] == prior_inputs
    assert continuation["current_policy_observation"]["inputs"] == current


def test_open_autosave_nonempty_rewrite_is_not_permitted_by_its_typed_output_path(
    tmp_path, monkeypatch
):
    case = fixture_case(tmp_path, monkeypatch)
    _runtime_marker_fixture(case)
    original = case.adapter._open_layout_document

    def rewritten_log(*args):
        opened = original(*args)
        log = case.board.parent / "LogFiles/PCBAutoSave.txt"
        log.write_bytes(log.read_bytes().replace(b"10:32", b"10:33"))
        return opened

    case.adapter._open_layout_document = rewritten_log
    result = failure(case)
    proof = result["reopen_dependency_changes"]
    assert result["saved"] and not result["persistence_verified"]
    assert proof["valid"] is False and proof["save_policy"]["valid"] is False
    assert str(case.board.parent / "LogFiles/PCBAutoSave.txt") not in {
        row["path"] for row in proof["protected_dependency_changes"]["allowed_output_files"]
    }
    assert B.K.verify_archive(result["recovery_archive"])["valid"]


def test_save_cannot_clear_the_actual_nonempty_historical_autosave(tmp_path, monkeypatch):
    from test_component_save_policy import source_081_log

    case = fixture_case(tmp_path, monkeypatch)
    doc, _ = _runtime_marker_fixture(case, historical_log=source_081_log())
    original_save = doc.Save
    source = copy.deepcopy(case.origin["source_backup"])

    def cleared_log():
        saved = original_save()
        (case.board.parent / "LogFiles/PCBAutoSave.txt").write_bytes(b"")
        return saved

    doc.Save = cleared_log
    result = failure(case)
    assert result["saved"] and not result["save_dependency_changes"]["valid"]
    assert not any(row[0] in ("Close", "Open") for row in case.events)
    assert not Path(case.request["recovery_backup"]).exists()
    assert pin(source["path"]) == source


@pytest.mark.parametrize("relative", ["PCB/Work/unrelated.bin", "PCB/Config/new-unlisted.dat"])
def test_open_empty_autosave_does_not_allow_other_work_or_config_changes(
    tmp_path, monkeypatch, relative
):
    case = fixture_case(tmp_path, monkeypatch)
    _runtime_marker_fixture(case, reopened_log=b"")
    original = case.adapter._open_layout_document

    def opened_unknown(*args):
        opened = original(*args)
        path = case.project.parent / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not an allowed Open log output")
        return opened

    case.adapter._open_layout_document = opened_unknown
    result = failure(case)
    assert result["saved"] and not result["persistence_verified"]
    assert not result["reopen_dependency_changes"]["valid"]
    assert B.K.verify_archive(result["recovery_archive"])["valid"]


def test_reader_recomputes_open_empty_autosave_raw_bytes_and_predeclared_diff(
    tmp_path, monkeypatch
):
    case = fixture_case(tmp_path, monkeypatch)
    _runtime_marker_fixture(case, reopened_log=b"")
    result = apply(case)
    assert B.verify_saved_receipt(result)["valid"]
    row = next(
        row
        for row in result["reopened_policy_observation"]["files"]
        if row["path"].endswith("PCBAutoSave.txt")
    )
    row["raw_hex"] = b"forged nonempty log bytes".hex()
    assert not B.verify_saved_receipt(result)["valid"]


def _sample_fault(case, index):
    return B.K.RecoveryError(
        "E_VALIDATION",
        "Owned synthetic complete closed read failed",
        {
            "reason": "synthetic temporary read failure " + str(index),
            "path": str(case.project.parent / "database/icdb.dat"),
            "sample": index,
            "nested": {"read_error": "PermissionError", "file_set_complete": False},
        },
    )


def _delayed_closed_read(case, monkeypatch, failures):
    original = B.K.observe_closed
    calls, waits = [], []

    def observed(project, board):
        calls.append((project, board))
        if len(calls) <= failures:
            raise _sample_fault(case, len(calls))
        return original(project, board)

    monkeypatch.setattr(B.K, "observe_closed", observed)
    monkeypatch.setattr(B.time, "sleep", waits.append)
    return calls, waits


@pytest.mark.parametrize("failures", [1, 2, 3, 16])
def test_bounded_closed_sampling_retains_failed_reads_before_complete_success(
    tmp_path, monkeypatch, failures
):
    case = fixture_case(tmp_path, monkeypatch)
    calls, waits = _delayed_closed_read(case, monkeypatch, failures)
    result = apply(case)
    contract = result["trace"]["closed_snapshot_sampling"]
    assert contract == B.CLOSED_SNAPSHOT_SAMPLING
    assert contract["max_attempts"] == 17 and contract["between_attempts_ms"] == 500
    samples = result["closed_snapshot_sampling"]
    assert samples["contract"] == contract
    rows = samples["attempts"]
    assert len(rows) == failures + 1
    assert [row["outcome"] for row in rows] == ["failed"] * failures + ["succeeded"]
    assert waits == [0.5] * failures
    for index, row in enumerate(rows[:-1], 1):
        assert row["error"] == B._error_fact(_sample_fault(case, index))
        assert row["stage"] == "closed_snapshot" and row["observe_started"] is True
        assert type(row["elapsed_ms"]) is float and row["elapsed_ms"] >= 0
        assert row["guard"]["active_document"] == {"type": "NoneType", "is_none": True}
        assert row["guard"]["application_identity"] == {
            key: case.identity[key] for key in ("application", "generation")
        }
    assert rows[-1]["closed_tree_fingerprint"] == result["closed_tree"]["fingerprint"]
    assert rows[-1]["file_count"] == result["closed_tree"]["file_count"]
    assert rows[-1]["total_bytes"] == result["closed_tree"]["total_bytes"]
    assert result["recovery_archive"]["closed_tree"] == result["closed_tree"]
    assert result["persistence_verified"] and B.verify_saved_receipt(result)["valid"]
    assert B.K.verify_archive(result["recovery_archive"])["valid"]
    assert [row[0] for row in case.events] == [
        "TransactionStart",
        "PutTrace",
        "TransactionEnd",
        "Save",
        "Close",
        "Open",
    ]
    # K.seal keeps its original independent pre/post-write complete observations.
    # Only this declared closed_snapshot phase has the max-seventeen sample budget.
    assert len(calls) == failures + 3


def test_continuous_closed_read_failure_preserves_last_details_without_archive_or_open(
    tmp_path, monkeypatch
):
    case = fixture_case(tmp_path, monkeypatch)
    calls, waits = _delayed_closed_read(case, monkeypatch, 100)
    result = failure(case)
    rows = result["closed_snapshot_sampling"]["attempts"]
    assert len(rows) == len(calls) == 17 and waits == [0.5] * 16
    assert [row["outcome"] for row in rows] == ["failed"] * 17
    assert result["closed_snapshot_failure"] == rows[-1]
    assert result["post_write_error"] == {
        **B._error_fact(_sample_fault(case, 17)),
        "stage": "closed_snapshot",
    }
    assert result["saved"] is True and result["stages"]["close"] == "confirmed"
    assert result["stages"]["closed_snapshot"] == "started"
    assert result["stages"]["recovery_archive"] == "not_started"
    assert not result["persistence_verified"]
    assert "closed_tree" not in result and "recovery_archive" not in result
    assert not Path(case.request["recovery_backup"]).exists()
    assert [row[0] for row in case.events] == [
        "TransactionStart",
        "PutTrace",
        "TransactionEnd",
        "Save",
        "Close",
    ]


def test_r8_permission_denied_adapter_shape_is_retained_before_fourth_complete_read(
    tmp_path, monkeypatch
):
    # R8 run203 stopped after three failed complete reads, with Save/Close known
    # and no archive/Open. Reproduce its adapter error shape only on owned files;
    # this v2 synthetic success neither diagnoses that lock nor promotes run203.
    case = fixture_case(tmp_path, monkeypatch)
    original = B.K.observe_closed
    path = str(case.project.parent / "database/icdb.dat")
    error = B.K.RecoveryError(
        "E_VALIDATION",
        "Complete closed project cannot be observed",
        {"reason": f"[Errno 13] Permission denied: '{path}'"},
    )
    calls, waits = [], []

    def observed(project, board):
        calls.append((project, board))
        if len(calls) <= 3:
            raise error
        return original(project, board)

    monkeypatch.setattr(B.K, "observe_closed", observed)
    monkeypatch.setattr(B.time, "sleep", waits.append)
    result = apply(case)
    rows = result["closed_snapshot_sampling"]["attempts"]
    assert [row["outcome"] for row in rows] == ["failed"] * 3 + ["succeeded"]
    assert [row["error"] for row in rows[:-1]] == [B._error_fact(error)] * 3
    assert waits == [0.5] * 3
    assert result["persistence_verified"] and B.verify_saved_receipt(result)["valid"]
    assert [row[0] for row in case.events] == [
        "TransactionStart",
        "PutTrace",
        "TransactionEnd",
        "Save",
        "Close",
        "Open",
    ]


@pytest.mark.parametrize(
    "drift", ["application", "generation", "identity_getter", "document_opened"]
)
def test_closed_sampling_stops_immediately_on_owner_or_document_guard_failure(
    tmp_path, monkeypatch, drift
):
    case = fixture_case(tmp_path, monkeypatch)
    calls, waits = _delayed_closed_read(case, monkeypatch, 100)

    def wait_changed_owner(seconds):
        waits.append(seconds)
        if drift == "application":
            case.identity["application"]["pid"] += 1
        elif drift == "generation":
            case.identity["generation"]["birth_filetime_100ns"] = "457"
        elif drift == "document_opened":
            case.application.ActiveDocument = NS(FullName="unrelated document")

    identity_reads = []
    original = B.board_labels._application_identity

    def application_identity(app):
        identity_reads.append(1)
        if drift == "identity_getter" and len(identity_reads) == 2:
            raise RuntimeError("owned synthetic native application identity getter failed")
        return original(app)

    monkeypatch.setattr(B.time, "sleep", wait_changed_owner)
    monkeypatch.setattr(B.board_labels, "_application_identity", application_identity)
    result = failure(case)
    rows = result["closed_snapshot_sampling"]["attempts"]
    assert len(calls) == 1 and waits == [0.5]
    assert [row["outcome"] for row in rows] == ["failed", "guard_failed"]
    assert rows[1]["observe_started"] is False
    assert result["closed_snapshot_failure"] == rows[1]
    assert result["post_write_error"]["stage"] == "closed_snapshot"
    assert not any(row[0] == "Open" for row in case.events)
    assert not Path(case.request["recovery_backup"]).exists()


def test_initial_closed_identity_read_error_stops_before_any_file_sampling(tmp_path, monkeypatch):
    case = fixture_case(tmp_path, monkeypatch)
    calls, waits = _delayed_closed_read(case, monkeypatch, 100)

    def broken_identity(app):
        raise RuntimeError("owned closed identity read failed before any complete hash")

    monkeypatch.setattr(B.board_labels, "_application_identity", broken_identity)
    result = failure(case)
    rows = result["closed_snapshot_sampling"]["attempts"]
    assert len(rows) == 1 and rows[0]["outcome"] == "guard_failed"
    assert rows[0]["observe_started"] is False
    assert not calls and not waits
    assert result["stages"]["close"] == "confirmed"
    assert not any(row[0] == "Open" for row in case.events)


@pytest.mark.parametrize(
    "tamper",
    [
        "contract",
        "attempt_count",
        "failure_order",
        "failure_details",
        "guard",
        "wait",
        "elapsed_missing",
        "elapsed_negative",
        "elapsed_bool",
        "elapsed_nonfinite",
        "last_fingerprint",
        "last_count",
        "complete_flag_only",
        "not_observed",
    ],
)
def test_reader_recomputes_sampling_budget_failure_order_and_complete_last_tree(
    tmp_path, monkeypatch, tamper
):
    case = fixture_case(tmp_path, monkeypatch)
    _delayed_closed_read(case, monkeypatch, 1)
    result = apply(case)
    samples = result["closed_snapshot_sampling"]
    rows = samples["attempts"]
    if tamper == "contract":
        result["trace"]["closed_snapshot_sampling"]["max_attempts"] = 18
        result["trace"]["fingerprint"] = B._fingerprint(result["trace"])
    elif tamper == "attempt_count":
        samples["attempts"] = [copy.deepcopy(rows[0])] * 18
    elif tamper == "failure_order":
        rows[0]["outcome"] = "succeeded"
    elif tamper == "failure_details":
        rows[0]["error"].pop("details")
    elif tamper == "guard":
        rows[-1]["guard"]["application_identity"]["generation"]["birth_filetime_100ns"] = "999"
    elif tamper == "wait":
        rows[-1]["wait_before_ms"] = 0
    elif tamper == "elapsed_missing":
        rows[-1].pop("elapsed_ms")
    elif tamper == "elapsed_negative":
        rows[-1]["elapsed_ms"] = -1
    elif tamper == "elapsed_bool":
        rows[-1]["elapsed_ms"] = True
    elif tamper == "elapsed_nonfinite":
        rows[-1]["elapsed_ms"] = float("inf")
    elif tamper == "last_fingerprint":
        rows[-1]["closed_tree_fingerprint"] = "0" * 64
    elif tamper == "last_count":
        rows[-1]["file_count"] = True
    elif tamper == "complete_flag_only":
        samples.update(attempts=[], complete=True)
    else:
        rows[-1]["observe_started"] = False
    assert not B.verify_saved_receipt(result)["valid"]


def test_continuation_advances_closed_zip_without_claiming_locked_live_icdb_hashes_equal(
    tmp_path, monkeypatch
):
    case = fixture_case(tmp_path, monkeypatch)
    doc, markers = _runtime_marker_fixture(case)
    database = case.project.parent / "database/icdb.dat"
    original_inputs, original_save = B.R.input_files, doc.Save

    def locked_live(project, board, archive):
        inputs = original_inputs(project, board, archive)
        if Path(project) == case.project and case.adapter._licensed_document(None) is not None:
            inputs["live_files"] = [
                row for row in inputs["live_files"] if row["path"] != str(database)
            ]
            inputs["live_unavailable"] = [
                {
                    "path": str(database),
                    "live_read": "unavailable",
                    "source_only_sha256": inputs["source_backup"]["icdb_source_sha256"],
                }
            ]
        return inputs

    def save_new_closed_icdb():
        reply = original_save()
        database.write_bytes(b"new actual same-window closed icdb bytes")
        return reply

    monkeypatch.setattr(B.R, "input_files", locked_live)
    doc.Save = save_new_closed_icdb
    result = apply(case)
    previous = copy.deepcopy(result["reopened_inputs"])
    original_opaque = result["trace"]["input_files"]["live_unavailable"][0]
    assert previous["live_unavailable"][0] == original_opaque
    closed = result["closed_dependency_changes"]["icdb"]
    assert closed["bytes_comparable"] is False and closed["bytes_equal"] is None
    assert closed["after"]["observation"] == pin(database)
    assert pin(database)["sha256"] != original_opaque["source_only_sha256"]
    markers(b" from the next independently observed Open")
    case.params["source_backup"] = result["recovery_archive"]["archive"]["path"]
    current = B._inputs(case.params, case.board, case.request)
    source = B._source_archive_proof(current)
    events = copy.deepcopy(case.events)
    proof = B._continuation_files(result, current, source)
    visibility = proof["dependency_changes"]["icdb"]
    assert proof["dependency_changes"]["valid"]
    assert visibility["bytes_comparable"] is False and visibility["bytes_equal"] is None
    assert visibility["unknown_live_bytes_are_equal"] is False
    assert visibility["before"]["observation"] == original_opaque
    assert visibility["after"]["observation"]["source_only_sha256"] == pin(database)["sha256"]
    assert proof["same_window_archive"] == result["recovery_archive"]["archive"]
    assert result["reopened_inputs"] == previous
    assert case.events == events
