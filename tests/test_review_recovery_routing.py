"""Review reproductions; native facts here are explicit synthetic fixtures."""

import copy
import json
import os
from types import SimpleNamespace

import pytest
from test_driver_v2 import _scope, _worker_fixture
from test_library_pad_dimensions import RES, _held
from test_library_parts import _spec

from xpedition_cli import drc_engine as D
from xpedition_cli import library_parts as P
from xpedition_cli import native_com_adapter as A
from xpedition_cli.cli import library, pcb
from xpedition_cli.driver import Adapter, runtime


def cross_partition():
    original = _spec(RES, partition="PartsA")
    held = _held(original)
    held.cells[0]["partition"] = "CellsB"
    held.texts[("cells", "CellsB")] = held.texts.pop(("cells", "PartsA"))
    spec = copy.deepcopy(original)
    cell = held.cells[0]
    spec["parts"][0]["footprint"] = {
        "cell": cell["name"],
        "pad_dimensions": {
            p["number"]: {"copper": [1.225, 1.4], "mask": [1.325, 1.5], "paste": [1.225, 1.4]}
            for p in cell["pins"]
        },
    }
    return spec, held


def test_cross_partition_resize_rejects_without_rehoming_definitions():
    spec, held = cross_partition()
    before = copy.deepcopy(held)
    with pytest.raises(P.PartsFileError, match="PartsA.*CellsB|CellsB.*PartsA"):
        P.plan(spec, held)
    assert held == before
    assert [(c["name"], c["partition"]) for c in held.cells] == [(held.cells[0]["name"], "CellsB")]


def test_same_named_cells_cannot_hide_the_actual_dependency_partition():
    spec, held = cross_partition()
    held.cells.insert(0, {**copy.deepcopy(held.cells[0]), "partition": "PartsA"})
    held.texts[("cells", "PartsA")] = held.texts[("cells", "CellsB")]
    before = copy.deepcopy(held)
    with pytest.raises(P.PartsFileError) as caught:
        P.plan(spec, held)
    assert caught.value.code == "E_CONFLICT"
    assert caught.value.details["cell_matches"] == 2
    assert held == before


def test_public_cross_partition_resize_stops_before_native_writes(tmp_path, monkeypatch, project):
    _scope(tmp_path, monkeypatch)
    spec, held = cross_partition()
    parts = tmp_path / "parts.json"
    parts.write_text(json.dumps(spec))
    monkeypatch.setattr(
        library,
        "load_library",
        lambda *a, **k: (
            held,
            {
                "library": "fixture.lmc",
                "failed": [],
                "files": [],
                "registration": {"parts": ["PartsA"], "cells": ["CellsB"]},
            },
        ),
    )

    class Backend:
        def invoke(self, method, params, **kwargs):
            if method == "identity":
                return {"application": {"pid": 4242}, "adapter": _worker_fixture()}
            if method == "snapshot":
                return {"components": [], "fixture": True}
            pytest.fail("No converter, symbol write or Designer close may be dispatched")

    with runtime(Adapter(Backend()), state_dir=tmp_path / "state") as cli:
        result = cli.run(
            [
                "library",
                "create",
                "--project",
                str(project),
                "--file",
                str(parts),
                "--dangerous",
                "--dry-run",
            ]
        )
    assert not result.envelope["ok"], result.envelope
    assert result.envelope["error"]["code"] == "E_UNSUPPORTED"
    assert result.envelope["error"]["details"]["dispatch_state"] == "not_started"
    assert result.envelope["error"]["details"]["cell_partition"] == "CellsB"


@pytest.mark.parametrize("missing", ["effective_rules", "effective_elements"])
def test_old_effective_settings_cannot_qualify_as_this_run(tmp_path, missing):
    import time

    from test_drc_engine import report_files

    paths = report_files(tmp_path / "PCB/board.pcb")
    before = {k: D.file_record(p) for k, p in paths.items()}
    started = time.time_ns()
    report_files(tmp_path / "PCB/board.pcb")
    os.utime(paths[missing], ns=(1_000_000_000, 1_000_000_000))
    with pytest.raises(ValueError, match="effective|configuration|setup"):
        D.read_reports(paths, before, started)


def test_geometry_inspect_does_not_open_switch_or_save(tmp_path, monkeypatch):
    from xpedition_cli import cell_refresh, native_identity

    board = tmp_path / "a.pcb"
    board.write_bytes(b"fixture")
    doc = SimpleNamespace(FullName=str(board), CurrentUnit=2)
    app = SimpleNamespace(ActiveDocument=doc)
    monkeypatch.setattr(A, "_application", lambda *a, **k: app)
    monkeypatch.setattr(A, "_licensed_document", lambda a: doc)
    monkeypatch.setattr(A, "_open_layout_document", lambda *a: pytest.fail("must not open/switch"))
    monkeypatch.setattr(native_identity, "application_identity", lambda *a: {"pid": 42})
    geometry = {"components": [{"uid": "62"}], "model": {}, "stackup": []}
    monkeypatch.setattr(cell_refresh, "capture", lambda *a, **k: copy.deepcopy(geometry))
    result = A._board_info({"project": str(board), "geometry": True, "pid": 42}, object())
    assert result["observation"]["geometry"] == geometry
    assert result["observation"]["persistence"] == "unknown"
    assert result["observation"]["engineering_write_dispatched"] is False


def test_public_geometry_inspect_is_read_only(tmp_path, monkeypatch):
    board = tmp_path / "a.pcb"
    board.write_bytes(b"fixture")
    _scope(tmp_path, monkeypatch)
    calls = []

    class Backend:
        read_timeout_seconds = 120

        def invoke(self, method, params, **kwargs):
            calls.append((method, params))
            return {
                "pcb": str(board),
                "observation": {
                    "geometry": {"components": [{"uid": "62"}]},
                    "persistence": "unknown",
                    "engineering_write_dispatched": False,
                },
            }

    backend = Backend()
    monkeypatch.setattr(pcb, "reader", lambda options: backend)
    with runtime(Adapter(backend), state_dir=tmp_path / "state") as cli:
        result = cli.run(
            ["pcb", "inspect", "--project", str(board), "--live-geometry", "--pid", "42"]
        )
    assert result.envelope["ok"], result.envelope
    assert result.envelope["data"]["observation"]["persistence"] == "unknown"
    assert calls == [
        ("board_info", {"project": str(board), "start": False, "geometry": True, "pid": 42})
    ]


def test_unknown_can_be_observed_without_reconciliation_or_artifact_unlock(tmp_path, monkeypatch):
    from xpedition_cli._vendor.tool_driver_sdk import DriverError

    _scope(tmp_path, monkeypatch)
    board = tmp_path / "a.pcb"
    board.write_bytes(b"fixture")
    output = tmp_path / "snapshot.json"
    calls = []

    class Backend:
        read_timeout_seconds = 120

        def invoke(self, method, params, **kwargs):
            calls.append((method, params))
            if method == "identity":
                return {"application": {"pid": 4242}, "adapter": _worker_fixture()}
            if method == "snapshot":
                return {"components": [], "fixture": True}
            if method == "board_geometry":
                return {"model": {"layers": 4, "components": [], "pads": []}}
            assert method == "board_info" and params["geometry"] and not params["start"]
            return {
                "pcb": str(board),
                "observation": {
                    "geometry": {"components": [{"uid": "62", "refdes": "R1"}]},
                    "persistence": "unknown",
                    "engineering_write_dispatched": False,
                },
            }

    class LostReply(Adapter):
        def apply(self, call, plan):
            raise DriverError(
                "E_TIMEOUT",
                "Synthetic loss after dispatch; no saved witness",
                dispatch_state="dispatched",
            )

    backend = Backend()
    monkeypatch.setattr(pcb, "native", lambda: backend)
    monkeypatch.setattr(pcb, "reader", lambda options: backend)
    args = ["pcb", "snapshot", "--project", str(board), "--output", str(output)]
    with runtime(LostReply(backend), state_dir=tmp_path / "state") as cli:
        planned = cli.run([*args, "--dry-run"])
        assert planned.envelope["ok"], planned.envelope
        op = planned.envelope["data"]["preview"]["operation_id"]
        failed = cli.run([*args, "--confirm", planned.envelope["data"]["confirm_token"]])
        assert failed.envelope["error"]["code"] == "E_OUTCOME_UNKNOWN"
    with runtime(Adapter(backend), state_dir=tmp_path / "state") as cli:
        before = cli.run(["operation", "show", "--id", op]).envelope["data"]
        record_before = cli._store_open().get(op)
        observed = cli.run(
            ["pcb", "inspect", "--project", str(board), "--live-geometry", "--pid", "4242"]
        )
        after = cli.run(["operation", "show", "--id", op]).envelope["data"]
        record_after = cli._store_open().get(op)
        blocked = cli.run([*args, "--dry-run"])
    assert observed.envelope["ok"], observed.envelope
    assert observed.envelope["data"]["observation"]["persistence"] == "unknown"
    assert record_before == record_after
    before["operation_status"].pop("observed_at")
    after["operation_status"].pop("observed_at")
    assert before == after
    assert before["operation_status"]["status"] == "unknown"
    assert blocked.envelope["error"]["details"]["conflict"] == "unresolved_operation"
    assert not output.exists()


@pytest.mark.parametrize(
    "fault", ["wrong_pid", "wrong_document", "wrong_units", "drift", "read_error"]
)
def test_geometry_observation_fails_closed(tmp_path, monkeypatch, fault):
    from xpedition_cli import cell_refresh, native_identity

    board = tmp_path / "a.pcb"
    board.write_bytes(b"fixture")
    doc = SimpleNamespace(
        FullName=str(tmp_path / "other.pcb" if fault == "wrong_document" else board),
        CurrentUnit=4 if fault == "wrong_units" else 2,
    )
    app = object()
    monkeypatch.setattr(A, "_application", lambda *a, **k: app)
    monkeypatch.setattr(A, "_licensed_document", lambda a: doc)
    monkeypatch.setattr(
        native_identity,
        "application_identity",
        lambda *a: {"pid": 99 if fault == "wrong_pid" else 42},
    )
    reads = []

    def read(*args, **kwargs):
        reads.append(1)
        if fault == "read_error":
            raise RuntimeError("unavailable getter")
        return {"components": [len(reads)] if fault == "drift" else []}

    monkeypatch.setattr(cell_refresh, "capture", read)
    with pytest.raises(A.AdapterError) as caught:
        A._board_info({"project": str(board), "pid": 42, "geometry": True}, object())
    assert caught.value.details["write_attempted"] is False
    if fault in {"wrong_pid", "wrong_document", "wrong_units"}:
        assert not reads


def test_geometry_observation_obeys_existing_scope_before_native_dispatch(tmp_path, monkeypatch):
    _scope(tmp_path, monkeypatch)
    scope_file = tmp_path / "scope.json"
    scope = json.loads(scope_file.read_text())
    scope["roots"] = [str(tmp_path / "permitted")]
    scope_file.write_text(json.dumps(scope))
    board = tmp_path / "outside.pcb"
    board.write_bytes(b"fixture")
    monkeypatch.setattr(pcb, "reader", lambda *a: pytest.fail("scope must be checked first"))
    with runtime(state_dir=tmp_path / "state") as cli:
        result = cli.run(
            ["pcb", "inspect", "--project", str(board), "--live-geometry", "--pid", "42"]
        )
    assert result.envelope["error"]["code"] == "E_FORBIDDEN"


def test_native_dimension_dispatch_guard_runs_before_library_or_designer_access(
    tmp_path, monkeypatch
):
    project = tmp_path / "a.prj"
    project.write_bytes(b"fixture")
    monkeypatch.setattr(A, "_central_library", lambda *a: pytest.fail("must reject first"))
    with pytest.raises(A.AdapterError) as caught:
        A._library_import(
            {
                "project": str(project),
                "partition": "PartsA",
                "padstacks": "would write pads",
                "dimension_dependencies": [
                    {"name": "CELL", "partition": "CellsB", "part_partition": "PartsA"}
                ],
                "required_partitions": {"parts": ["PartsA"], "cells": ["CellsB"]},
            },
            object(),
        )
    assert caught.value.code == "E_UNSUPPORTED"
    assert caught.value.details["native_definition_write_started"] is False


def test_strict_observation_does_not_turn_collection_failure_into_empty_geometry():
    class Unreadable:
        @property
        def Count(self):
            raise RuntimeError("native count unavailable")

    with pytest.raises(RuntimeError, match="count unavailable"):
        list(A._items(Unreadable(), strict=True))


def test_strict_observation_does_not_skip_unrepresentable_protected_keepout(monkeypatch):
    class Items:
        Count = 1

        def Item(self, index):
            assert index == 1
            return SimpleNamespace(
                Component=None, Geometry=object(), ObstructType=1, Layer=1, LayerName="Top"
            )

    monkeypatch.setattr(A, "_propget", lambda *args: Items())
    monkeypatch.setattr(A, "_geometry_record", lambda *args, **kwargs: None)
    with pytest.raises(A.AdapterError, match="keepout"):
        A._board_keepouts(object(), strict=True)


def test_strict_capture_does_not_call_collection_default_item_without_index():
    from xpedition_cli.cell_refresh import capture

    class Collection:
        Count = 0

        def __call__(self, *args):
            pytest.fail("A callable COM collection is not a zero-argument accessor")

    adapter = SimpleNamespace(
        _items=A._items,
        _board_model=lambda doc, **kwargs: {
            "traces": [],
            "layout_observation": {"trace_envelopes": []},
        },
        _board_stackup=lambda doc: [],
        AdapterError=A.AdapterError,
    )
    assert (
        capture(SimpleNamespace(Components=Collection()), adapter, strict=True)["components"] == []
    )


def test_public_live_observation_rejects_repeated_connectivity_failure_without_side_effects(
    tmp_path, monkeypatch
):
    from xpedition_cli import native_identity
    from xpedition_cli.errors import CLIError

    _scope(tmp_path, monkeypatch)
    board = tmp_path / "readonly.pcb"
    board.write_bytes(b"synthetic")

    class Empty:
        Count = 0

    class Unreadable:
        @property
        def Count(self):
            raise RuntimeError("Native Nets enumeration unavailable")

    doc = SimpleNamespace(
        FullName=str(board),
        CurrentUnit=2,
        LayerCount=4,
        Components=Empty(),
        BoardOutline=None,
        Vias=Empty(),
        MountingHoles=Empty(),
        Traces=Empty(),
        Nets=Unreadable(),
    )
    monkeypatch.setattr(A, "_application", lambda *a, **k: object())
    monkeypatch.setattr(A, "_licensed_document", lambda *a: doc)
    monkeypatch.setattr(A, "_board_keepouts", lambda *a, **k: [])
    monkeypatch.setattr(native_identity, "application_identity", lambda *a: {"pid": 42})

    class Backend:
        read_timeout_seconds = 30

        def invoke(self, method, params, **kwargs):
            assert method == "board_info"
            try:
                return A._board_info(params, None)
            except A.AdapterError as error:
                raise CLIError(error.code, error.message, error.details) from error

    backend = Backend()
    monkeypatch.setattr(pcb, "reader", lambda options: backend)
    with runtime(Adapter(backend), state_dir=tmp_path / "state") as cli:
        for _ in range(2):
            reply = cli.run(
                ["pcb", "inspect", "--project", str(board), "--live-geometry", "--pid", "42"]
            )
            assert reply.envelope["error"]["code"] == "E_NATIVE_FAILURE"
            assert reply.envelope["error"]["details"]["write_attempted"] is False
    assert doc.CurrentUnit == 2
    assert board.read_bytes() == b"synthetic"
