"""Bounded read de-duplication over native-shaped fixtures, never CAD acceptance."""

from __future__ import annotations

import copy
from types import SimpleNamespace as NS

import pytest
from test_components_public_observation import native_component_case, query
from test_unroute_native import Collection

from xpedition_cli import board_labels, board_unroute, cell_refresh, layout_reminders
from xpedition_cli import native_com_adapter as A
from xpedition_cli import native_observation_stage as S


def issued(tmp_path, monkeypatch):
    doc = NS(FullName=str(tmp_path / "authorized.pcb"), CurrentUnit=2)
    identity = {"pid": 42, "generation": "original"}
    calls = []
    source = {"components": [{"uid": "all-components"}], "model": {"all_pins": 467}}

    def capture(actual, adapter, *, strict):
        assert actual is doc and adapter is A and strict
        calls.append("complete_native_capture")
        return copy.deepcopy(source)

    monkeypatch.setattr(cell_refresh, "capture", capture)
    return doc, identity, calls, source


def test_stage_is_fresh_complete_native_data_with_immutable_independent_consumer_copies(
    tmp_path, monkeypatch
):
    doc, identity, calls, source = issued(tmp_path, monkeypatch)
    with S.full_capture(doc, A, identity_reader=lambda: identity) as stage:
        first = S.material(stage, doc, A, kind="complete_capture")
        first["model"]["all_pins"] = 2
        assert S.material(stage, doc, A, kind="complete_capture") == source
        with pytest.raises(AttributeError):
            stage._payload = "{}"
        assert calls == ["complete_native_capture"]
    with pytest.raises(ValueError, match="expired"):
        S.material(stage, doc, A, kind="complete_capture")
    source["model"]["all_pins"] = 468
    with S.full_capture(doc, A, identity_reader=lambda: identity) as next_stage:
        assert S.material(next_stage, doc, A, kind="complete_capture")["model"]["all_pins"] == 468
    assert len(calls) == 2


@pytest.mark.parametrize("fault", ["pid", "generation", "board", "unit", "document", "expired"])
def test_stage_refuses_stale_foreign_or_cross_lifecycle_identity(tmp_path, monkeypatch, fault):
    doc, identity, _, _ = issued(tmp_path, monkeypatch)
    with S.full_capture(doc, A, identity_reader=lambda: identity) as stage:
        actual_doc = doc
        if fault == "pid":
            identity["pid"] += 1
        elif fault == "generation":
            identity["generation"] = "reused-pid"
        elif fault == "board":
            doc.FullName += ".foreign"
        elif fault == "unit":
            doc.CurrentUnit = 4
        elif fault == "document":
            # Same path and PID after Open still cannot use the old COM object.
            actual_doc = NS(FullName=doc.FullName, CurrentUnit=2)
        else:
            monkeypatch.setattr(S.time, "monotonic", lambda: stage._started + 601)
        with pytest.raises(ValueError, match="expired"):
            S.material(stage, actual_doc, A, kind="complete_capture")


@pytest.mark.parametrize("injected", [{}, {"_payload": "{}", "_active": True}, None, "cache"])
def test_request_shaped_stage_cannot_be_injected(tmp_path, monkeypatch, injected):
    doc, _, calls, _ = issued(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="native-issued"):
        S.material(injected, doc, A, kind="complete_capture")
    assert calls == []


def test_stage_cannot_supply_mutation_references_or_different_source(tmp_path, monkeypatch):
    doc, identity, _, _ = issued(tmp_path, monkeypatch)
    with S.full_capture(doc, A, identity_reader=lambda: identity) as stage:
        with pytest.raises(ValueError, match="source"):
            S.material(stage, doc, A, kind="raw_board_model")
        with pytest.raises(ValueError, match="mutation references"):
            board_labels.observe(
                doc, tmp_path / "authorized.pcb", A, references=True, _capture_stage=stage
            )


def test_finite_stage_size_and_elapsed_limits_fail_closed(tmp_path, monkeypatch):
    doc, identity, _, _ = issued(tmp_path, monkeypatch)
    monkeypatch.setattr(S, "MAX_STAGE_BYTES", 8)
    with pytest.raises(ValueError, match="bounded stage material size"):
        with S.full_capture(doc, A, identity_reader=lambda: identity):
            pytest.fail("Oversize complete observation must not create a stage")


def test_strict_capture_reads_all_127_components_467_pin_headers_once(tmp_path):
    pins = []

    class Pin:
        Pads = FabricationPads = Holes = Collection()

        def __init__(self, number):
            self.UniqueId = "pin-" + str(number)
            self.Name = str(number)
            self.net_reads = self.x_reads = self.y_reads = 0

        @property
        def Net(self):
            self.net_reads += 1
            return NS(
                Name="(Net0)" if int(self.Name) >= 417 else "REAL",
                UniqueId="net0" if int(self.Name) >= 417 else "real-net",
            )

        def GetPositionX(self, unit):
            assert unit == 4
            self.x_reads += 1
            return float(self.Name)

        def GetPositionY(self, unit):
            assert unit == 4
            self.y_reads += 1
            return 1.0

    components = []
    for index in range(127):
        component_pins = [Pin(len(pins) + offset) for offset in range(4 if index < 86 else 3)]
        pins.extend(component_pins)
        components.append(
            NS(
                UniqueId="owner-" + str(index),
                RefDes="U" + str(index),
                PartNumber="DEMO",
                Cell=NS(Name="DEMO_GEOM"),
                Placed=True,
                Side=1,
                Orientation=0.0,
                Anchor=0,
                FixLock=0,
                Pins=Collection(component_pins),
                PlacementOutlines=Collection(),
                InsertionOutlines=Collection(),
                GetPositionX=lambda _unit: 0.0,
                GetPositionY=lambda _unit: 0.0,
            )
        )
    doc = NS(Components=Collection(components))
    calls = []
    adapter = NS(
        AdapterError=A.AdapterError,
        _items=A._items,
        _component_record=lambda *_: pytest.fail("Overwritten permissive record must not be read"),
        _board_model=lambda *_, **kwargs: (
            calls.append("whole-model")
            or {"traces": [], "layout_observation": {"trace_envelopes": []}}
        ),
        _board_stackup=lambda *_: [],
    )
    result = cell_refresh.capture(doc, adapter, strict=True)
    assert len(result["components"]) == 127
    actual_pins = [pin for row in result["components"] for pin in row["geometry"]]
    assert len(actual_pins) == len(pins) == 467
    assert len({pin["uid"] for pin in actual_pins}) == 467
    assert sum(pin["net"] == "(Net0)" for pin in actual_pins) == 50
    assert all((pin.net_reads, pin.x_reads, pin.y_reads) == (1, 1, 1) for pin in pins)
    assert calls == ["whole-model"]


def foreign_case(tmp_path, monkeypatch):
    case = native_component_case(tmp_path, monkeypatch, pins=467, traces=0)
    for index in range(2, 467):
        row = case.state["all_pin_pad_material"][index]
        row.update(net="(Net0)", net_uid="foreign-net0")
        case.state["pin_identities"][index].update(net="(Net0)", net_uid="foreign-net0")
        case.state["geometry"]["components"][index]["geometry"][0].update(
            net="(Net0)", net_uid="foreign-net0"
        )
        case.doc.Components[index].Pins[0].Net = NS(Name="(Net0)", UniqueId="foreign-net0")
    return case


def test_shared_query_preserves_all_467_physical_pins_and_two_independent_full_captures(
    tmp_path, monkeypatch
):
    case = foreign_case(tmp_path, monkeypatch)
    result = query(case)["observation"]
    state = result["native_routepass_state"]
    assert len(state["all_pin_pad_material"]) == 467
    assert len([row for row in state["all_pin_pad_material"] if row["net"] == "(Net0)"]) == 465
    assert result["native_routepass_evidence"]["valid"]
    assert case.events.count("capture") == 2
    assert result["observation_cost"]["complete_full_board_captures"] == 2
    assert result["observation_cost"]["cross_phase_capture_reuse"] is False
    assert not case.engine.masks


@pytest.mark.parametrize(
    "fault", ["foreign_geometry", "foreign_pin_uid", "foreign_net_uid", "text"]
)
def test_shared_query_refuses_non_target_identity_geometry_and_text_drift(
    tmp_path, monkeypatch, fault
):
    case = foreign_case(tmp_path, monkeypatch)
    base = cell_refresh.capture
    captures = []

    def capture(*args, **kwargs):
        geometry = base(*args, **kwargs)
        captures.append(1)
        if fault == "foreign_geometry" and len(captures) == 2:
            geometry["components"][-1]["geometry"][0]["center_mm"][0] += 0.1
        if len(captures) == 1 and fault in {"foreign_pin_uid", "foreign_net_uid"}:
            pin = case.doc.Components[-1].Pins[0]
            if fault == "foreign_pin_uid":
                pin.UniqueId = "same-shape-foreign-replacement"
            else:
                pin.Net.UniqueId = "same-name-foreign-net-replacement"
        return geometry

    monkeypatch.setattr(cell_refresh, "capture", capture)
    if fault == "text":
        reads = []

        def texts(*args, **kwargs):
            reads.append(1)
            return {
                "geometry": copy.deepcopy(case.state["geometry"]),
                "texts": [] if len(reads) == 1 else [{"uid": "changed-foreign-text"}],
            }

        monkeypatch.setattr(board_labels, "observe", texts)
    with pytest.raises(A.AdapterError) as error:
        query(case)
    assert error.value.details["write_attempted"] is False
    assert not case.engine.masks


def test_render_same_result_uses_one_fresh_raw_model_getter(tmp_path, monkeypatch):
    board = tmp_path / "phone-copy.pcb"
    doc = NS(FullName=str(board), CurrentUnit=2, Components=Collection())
    unvalidated = NS(FullName=str(board), CurrentUnit=None)
    app = NS(ActiveDocument=unvalidated)
    calls = []
    raw = {"layers": 4, "traces": [], "pads": [], "outline": []}
    monkeypatch.setattr(A, "_layout_board_path", lambda *_: board)
    monkeypatch.setattr(A, "_application", lambda *_, **__: app)
    monkeypatch.setattr(A, "_licensed_document", lambda *_: doc)
    monkeypatch.setattr(A, "_open_requested_document", lambda *_: unvalidated)
    monkeypatch.setattr(A, "application_identity", lambda *_: {"pid": 42})
    monkeypatch.setattr(A, "_board_model", lambda *_, **__: calls.append("raw-model") or raw)
    monkeypatch.setattr(layout_reminders, "report", lambda *_: {})

    def capture(actual, adapter, *, strict, _model_stage):
        assert actual is doc and strict is True
        model = S.material(_model_stage, doc, adapter, kind="raw_board_model")
        assert model == raw
        calls.append("complete-physical-instances")
        return {"components": [], "stackup": [], "model": model}

    monkeypatch.setattr(cell_refresh, "capture", capture)
    result = A._board_geometry({"project": str(board), "observe_only": True, "pid": 42}, None)
    assert result["model"]["layers"] == 4
    assert calls == ["raw-model", "complete-physical-instances"]


def test_render_rejects_a_different_validated_active_board(tmp_path, monkeypatch):
    board = tmp_path / "owned.pcb"
    original = NS(FullName=str(board), CurrentUnit=None)
    other = NS(FullName=str(tmp_path / "other.pcb"), CurrentUnit=2)
    app = NS(ActiveDocument=original)
    monkeypatch.setattr(A, "_layout_board_path", lambda *_: board)
    monkeypatch.setattr(A, "_application", lambda *_, **__: app)
    monkeypatch.setattr(A, "_open_requested_document", lambda *_: original)
    monkeypatch.setattr(A, "_licensed_document", lambda *_: other)
    monkeypatch.setattr(A, "application_identity", lambda *_: {"pid": 42})
    monkeypatch.setattr(A, "_board_model", lambda *_, **__: pytest.fail("wrong board read"))

    with pytest.raises(A.AdapterError) as error:
        A._board_geometry({"project": str(board), "observe_only": True, "pid": 42}, None)
    assert error.value.code == "E_CONFLICT"
    assert error.value.details["write_attempted"] is False


@pytest.mark.parametrize(
    "fault",
    [
        "missing_capture_uid",
        "same_shape_replacement",
        "duplicate_capture_uid",
        "duplicate_native_uid",
        "foreign_uid",
        "foreign_material",
    ],
)
def test_old_copper_identity_is_one_to_one_with_complete_shape_even_when_not_target(
    monkeypatch, fault
):
    from test_layout_routing_integration import native_document

    doc, trace, _ = native_document(monkeypatch)
    foreign = copy.deepcopy(trace)
    foreign.UniqueId = 319
    foreign.Net = NS(Name="FOREIGN", UniqueId=12, NumberOfOpens=0, IsRouted=lambda: True)
    doc.Nets.append(foreign.Net)
    doc.Traces.append(foreign)
    snapshot = cell_refresh.capture(doc, A, strict=True)
    if fault == "missing_capture_uid":
        snapshot["model"]["traces"][0].pop("uid")
    elif fault == "same_shape_replacement":
        trace.UniqueId = 777
    elif fault == "duplicate_capture_uid":
        snapshot["model"]["traces"][1]["uid"] = snapshot["model"]["traces"][0]["uid"]
    elif fault == "duplicate_native_uid":
        foreign.UniqueId = trace.UniqueId
    elif fault == "foreign_uid":
        foreign.UniqueId = 777
    else:
        foreign.Geometry.LineWidth += 0.01
    with pytest.raises(A.AdapterError):
        board_unroute.observe(doc, A, geometry=snapshot)
