"""A repeated read error is not a zero-open connectivity measurement."""

from types import SimpleNamespace as NS

import pytest

from xpedition_cli import native_com_adapter as A
from xpedition_cli.task_readback import verify_task


class Collection:
    def __init__(self, rows):
        self.rows = rows
        self.Count = len(rows)

    def Item(self, index):
        return self.rows[index - 1]


def board(net=None):
    return NS(
        Nets=Collection([net or NS(Name="A", UniqueId="nA", IsRouted=True, NumberOfOpens=0)]),
        Traces=Collection([]),
        Vias=Collection([]),
    )


@pytest.mark.parametrize("field", ["Nets", "Name", "UniqueId", "IsRouted", "NumberOfOpens"])
@pytest.mark.parametrize("failure", ["exception", "missing", "none"])
def test_required_connectivity_getters_are_not_default_values(field, failure):
    class Broken:
        def __getattr__(self, name):
            if name == field:
                if failure == "exception":
                    raise RuntimeError("COM getter failed")
                if failure == "missing":
                    raise AttributeError(name)
                return None
            return {"Name": "A", "UniqueId": "nA", "IsRouted": True, "NumberOfOpens": 0}[name]

    doc = Broken() if field == "Nets" else board(Broken())
    for _ in range(2):
        with pytest.raises(A.AdapterError):
            A._route_status(doc)


@pytest.mark.parametrize("fault", ["item", "count", "duplicate", "blank", "fractional"])
def test_partial_enumeration_and_unknown_identity_stop(fault):
    doc = board()
    if fault == "item":
        doc.Nets.Count = 2
    if fault == "count":
        doc.Nets.Count = None
    if fault == "duplicate":
        doc.Nets = Collection([doc.Nets.rows[0], doc.Nets.rows[0]])
    if fault == "blank":
        doc.Nets.rows[0].Name = ""
    if fault == "fractional":
        doc.Nets.rows[0].NumberOfOpens = 0.5
    with pytest.raises(A.AdapterError):
        A._route_status(doc)


@pytest.mark.parametrize(
    "rows", [[], [NS(Name="A", UniqueId="nA", IsRouted=True, NumberOfOpens=0)]]
)
def test_observed_empty_and_zero_are_complete(rows):
    doc = board()
    doc.Nets = Collection(rows)
    status = A._route_status(doc)
    assert status["observation_complete"] is True
    assert status["all_connected"] is True
    assert status["opens"] == 0


def test_false_isrouted_with_zero_opens_is_not_complete():
    status = A._route_status(board(NS(Name="A", UniqueId="nA", IsRouted=False, NumberOfOpens=0)))
    assert status["all_connected"] is False
    assert status["unrouted"] == [{"net": "A", "uid": "nA", "routed": False, "opens": 0}]


def test_151_isrouted_method_runs_once_and_propagates_failure():
    calls = []

    def method():
        calls.append(True)
        return True

    doc = board(NS(Name="A", UniqueId="nA", IsRouted=method, NumberOfOpens=0))
    assert A._route_status(doc)["all_connected"] is True
    assert calls == [True]

    def failed():
        calls.append(False)
        raise RuntimeError("native method failed")

    doc.Nets.rows[0].IsRouted = failed
    with pytest.raises(A.AdapterError):
        A._route_status(doc)
    assert calls == [True, False]


@pytest.mark.parametrize("state", [None, {"complete": False}, {"complete": True, "nets": []}])
def test_saved_identical_geometry_does_not_prove_connectivity(state):
    g = {
        "components": [{"pins": [{"net": "A"}]}],
        "model": {
            "traces": [],
            "vias": [],
            "holes": [],
            "unrouted": [],
            "connectivity": state,
        },
    }
    snap = {"extensions": {"facts": {"cell_refresh": {"geometry": g}}}}
    result = {"geometry_after": g, "saved": True, "persistence_verified": True}
    verified = verify_task("pcb routing auto", {"nets": "A"}, result, snap, snap, {})
    assert verified["valid"] is False
    assert verified["engineering_complete"] is False
    assert result["saved"] is True


def test_post_save_observation_error_keeps_native_save_fact(monkeypatch, tmp_path):
    from contextlib import nullcontext

    from xpedition_cli import cell_refresh

    doc = board()
    saved = []
    doc.Save = lambda: saved.append(True)
    monkeypatch.setattr(A, "_application", lambda *a, **k: object())
    monkeypatch.setattr(A, "_open_layout_document", lambda *a: (doc, []))
    monkeypatch.setattr(A, "_licensed_document", lambda *a: doc)
    monkeypatch.setattr(A, "_route_pass", lambda *a: NS(Go=lambda: None))
    monkeypatch.setattr(A, "_PromptAnswerer", lambda: nullcontext(NS(answered=[])))
    monkeypatch.setattr(A, "_regenerate_planes", lambda *a: 0)
    monkeypatch.setattr(A, "_traces_by_layer", lambda *a: {})
    monkeypatch.setattr(
        cell_refresh,
        "capture",
        lambda *a, **k: A._route_status(
            board(NS(Name="A", UniqueId="nA", IsRouted=True, NumberOfOpens=None))
        ),
    )
    path = tmp_path / "a.pcb"
    path.write_bytes(b"synthetic")
    # This is an already-acknowledged Save fact, not an eligible autorouter operation.
    doc.Save()
    result = {"saved": True}
    A._verify_board_edit_persistence(result, object(), doc, path)
    assert saved == [True]
    assert result["saved"] is True and result["persistence_state"] == "saved"
    assert result["change_status"] == "applied"
    assert result["persistence_verified"] is False
    assert result["post_save_error"]["code"] == "E_NATIVE_FAILURE"
