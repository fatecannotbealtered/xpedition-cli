"""Complete physical read-only evidence fixtures; no native applications are started."""

from __future__ import annotations

import copy
import os
from types import SimpleNamespace

import pytest

from xpedition_cli import cell_refresh as refresh
from xpedition_cli import native_com_adapter as native
from xpedition_cli.layout_reminders import trace_geometry_binding
from xpedition_cli.placement import canonical_state


def trace_model():
    traces = [
        {
            "kind": "trace",
            "net": "OLD",
            "layer": 1,
            "width": 0.254,
            "path": [[1.0, 1.0, 0.0], [3.0, 4.0, 0.0]],
        },
        {
            "kind": "trace",
            "net": "OLD",
            "layer": 1,
            "width": 0.254,
            "path": [[3.0, 4.0, 0.0], [6.0, 4.0, 0.0]],
        },
    ]
    return {
        "traces": traces,
        "layout_observation": {
            "trace_envelopes": [
                {
                    "model_index": i,
                    "uid": str(i + 901),
                    "geometry_sha256": trace_geometry_binding(row),
                }
                for i, row in enumerate(traces)
            ]
        },
    }


def test_strict_trace_uid_promotion_is_one_to_one_bound_to_exact_raw_material_without_mutation():
    source = trace_model()
    original = copy.deepcopy(source)
    promoted = refresh.strict_physical_model(source, native)
    assert [row["uid"] for row in promoted["traces"]] == ["901", "902"]
    assert "layout_observation" not in promoted
    assert source == original


@pytest.mark.parametrize(
    "fault",
    [
        "missing_inventory",
        "missing_entry",
        "duplicate_uid",
        "missing_uid",
        "bool_uid",
        "duplicate_index",
        "index_outside",
        "index_bool",
        "geometry_changed",
        "hash_missing",
        "malformed_row",
    ],
)
def test_strict_trace_identity_material_binding_refuses_incomplete_or_false_observation(fault):
    source = trace_model()
    rows = source["layout_observation"]["trace_envelopes"]
    if fault == "missing_inventory":
        source.pop("layout_observation")
    elif fault == "missing_entry":
        rows.pop()
    elif fault == "duplicate_uid":
        rows[1]["uid"] = rows[0]["uid"]
    elif fault == "missing_uid":
        rows[0].pop("uid")
    elif fault == "bool_uid":
        rows[0]["uid"] = True
    elif fault == "duplicate_index":
        rows[1]["model_index"] = rows[0]["model_index"]
    elif fault == "index_outside":
        rows[0]["model_index"] = 2
    elif fault == "index_bool":
        rows[0]["model_index"] = False
    elif fault == "geometry_changed":
        source["traces"][0]["width"] += 0.00001
    elif fault == "hash_missing":
        rows[0].pop("geometry_sha256")
    else:
        rows[0] = None
    with pytest.raises(native.AdapterError) as error:
        refresh.strict_physical_model(source, native)
    assert error.value.code == "E_UNSUPPORTED"


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "wrong_pid",
        "wrong_path",
        "missing_pid",
        "start_true",
        "pid_bool",
        "changed_after_capture",
    ],
)
def test_bound_material_observer_never_opens_or_quiets_and_refuses_foreign_process_or_board(
    tmp_path, monkeypatch, fault
):
    from xpedition_cli import native_identity

    board = tmp_path / "owned.pcb"
    doc = SimpleNamespace(FullName=str(board))
    app = SimpleNamespace(ActiveDocument=doc)
    params = {
        "project": str(board),
        "observe_only": True,
        "start": False,
        "pid": 5151,
        "strict": True,
    }
    calls = []
    identity = {"pid": 5151}
    if fault == "wrong_pid":
        identity["pid"] = 9191
    elif fault == "wrong_path":
        doc.FullName += ".foreign"
    elif fault == "missing_pid":
        params.pop("pid")
    elif fault == "start_true":
        params["start"] = True
    elif fault == "pid_bool":
        params["pid"] = True

    def application(client, **kwargs):
        assert os.environ["XPEDITION_OBSERVE_ONLY"] == "1"
        assert kwargs == {"attach_only": True, "quiet": False}
        calls.append("attach_existing")
        return app

    def capture(document, adapter, *, strict=False):
        assert document is doc and strict is True
        calls.append("read_material")
        if fault == "changed_after_capture":
            doc.FullName += ".different"
        return {"complete_fixture": True}

    adapter = SimpleNamespace(
        _layout_board_path=lambda p: board,
        _application=application,
        _licensed_document=lambda application: application.ActiveDocument,
        AdapterError=native.AdapterError,
    )
    monkeypatch.setattr(native_identity, "application_identity", lambda *args: identity)
    monkeypatch.setattr(refresh, "capture", capture)
    monkeypatch.delenv("XPEDITION_OBSERVE_ONLY", raising=False)
    if fault is None:
        assert refresh.observe(params, None, adapter)["geometry"] == {"complete_fixture": True}
        assert calls == ["attach_existing", "read_material"]
    else:
        with pytest.raises(native.AdapterError) as error:
            refresh.observe(params, None, adapter)
        assert error.value.details["write_attempted"] is False
        if fault in {"wrong_pid", "wrong_path", "missing_pid", "start_true", "pid_bool"}:
            assert "read_material" not in calls
    assert "XPEDITION_OBSERVE_ONLY" not in os.environ


@pytest.mark.parametrize("copper", ["planes", "vias"])
def test_batch_preflight_refuses_copper_without_stable_native_identity(copper):
    from test_placement import material_state, observations

    from xpedition_cli.errors import CLIError

    state = material_state(observations())
    state["model"][copper] = [{"net": "FOREIGN", "layer": 1, "path": [[1, 1, 0], [2, 3, 0]]}]
    with pytest.raises(CLIError) as error:
        canonical_state(state)
    assert error.value.code == "E_UNSUPPORTED"
