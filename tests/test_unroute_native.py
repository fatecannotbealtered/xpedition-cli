"""Synthetic native objects exercising deletion, Save and normal lifecycle boundaries."""

import copy
from types import SimpleNamespace

import pytest
from test_unroute_public import fixture_state, remove

from xpedition_cli import board_unroute as U
from xpedition_cli import native_com_adapter as A
from xpedition_cli.cell_refresh import _canonical


class Collection(list):
    @property
    def Count(self):
        return len(self)

    def Item(self, number):
        return self[number - 1]


@pytest.mark.parametrize("drift_mm", [0, 0.001])
def test_trace_identity_uses_capture_precision_without_hiding_drift(drift_mm):
    """Real contour conversion and capture precision must agree at the UID boundary."""
    geometry = SimpleNamespace(
        IsCircle=lambda: False,
        PointsArray=((785.1, 1145.17716535433), (590.55118110236,) * 2, (0.0, 0.0)),
        LineWidth=10.0,
        Cutouts=Collection(),
    )
    trace = SimpleNamespace(
        UniqueId=318,
        Net=SimpleNamespace(Name="A", UniqueId=11),
        Layer=1,
        Geometry=geometry,
    )
    shape = _canonical(
        A._geometry_record(geometry, layer=1, width=10.0 * A.TH_TO_MM, net="A", kind="trace")
    )
    shape["path"][0][0] += drift_mm
    snapshot = {
        "model": {
            "connectivity": {"nets": [{"net": "A", "uid": "11"}]},
            "traces": [{**shape, "uid": "318"}],
            "vias": [],
        }
    }
    doc = SimpleNamespace(CurrentUnit=2, Traces=Collection([trace]), Vias=Collection())
    if drift_mm:
        with pytest.raises(A.AdapterError, match="Routing changed"):
            U.observe(doc, A, geometry=snapshot)
    else:
        observed = U.observe(doc, A, geometry=snapshot)
        assert observed["routing_objects"] == [
            {"kind": "trace", "uid": "318", "net": "A", "geometry": {**shape, "uid": "318"}}
        ]


def native_case(tmp_path, monkeypatch, fault=None):
    state = fixture_state()
    if fault == "partial_same_net":
        row = copy.deepcopy(state["routing_objects"][0])
        row["uid"] = "93"
        state["routing_objects"].append(row)
        state["geometry"]["model"]["traces"].append(copy.deepcopy(row["geometry"]))
    for native_row, captured in zip(
        state["routing_objects"], state["geometry"]["model"]["traces"], strict=True
    ):
        native_row["geometry"]["uid"] = native_row["uid"]
        captured["uid"] = native_row["uid"]
    for c in state["geometry"]["components"]:
        c["geometry"] = [
            {
                "net": p["net"],
                "number": p["number"],
                "center_mm": [p["x"], p["y"]],
                "copper": [
                    {
                        "layer": 1,
                        "geometry": [
                            {"bounds_mm": [p["x"] - 0.3, p["y"] - 0.3, p["x"] + 0.3, p["y"] + 0.3]}
                        ],
                    }
                ],
            }
            for p in c["pins"]
        ]
    app = SimpleNamespace(ActiveDocument=None, connectivity_reads=[])
    events = []
    stored = None
    board = tmp_path / "board.pcb"
    board.write_bytes(b"synthetic")

    class Trace:
        def __init__(self, row, doc):
            self.row, self.doc = row, doc
            self.UniqueId = row["uid"]
            self.Net = SimpleNamespace(
                Name=row["net"], UniqueId="11" if row["net"] == "A" else "12"
            )
            self.Layer = row["geometry"]["layer"]
            self.Geometry = SimpleNamespace(record=row["geometry"], LineWidth=10)

        def Delete(self):
            events.append("delete:" + self.UniqueId)
            if (fault == "partial" and self.Net.Name == "B") or (
                fault == "partial_same_net" and self.UniqueId == "93"
            ):
                raise RuntimeError("synthetic delete refusal")
            model = self.doc.state["geometry"]["model"]
            self.doc.state["routing_objects"] = [
                r for r in self.doc.state["routing_objects"] if r["uid"] != self.UniqueId
            ]
            model["traces"].remove(self.row["geometry"])
            if not any(r["net"] == self.Net.Name for r in self.doc.state["routing_objects"]):
                remove(self.doc.state, {self.Net.Name})
            if fault == "foreign_trace":
                remove(self.doc.state, {"B"})
            if fault == "protected_hole":
                model["holes"][0][2] = 1.2
            if fault == "protected_component":
                self.doc.state["geometry"]["components"][1]["protected_geometry"]["copper"] = (
                    "changed"
                )
            if fault == "added_trace":
                row = copy.deepcopy(self.doc.state["routing_objects"][0])
                row["uid"] = "999"
                self.doc.state["routing_objects"].append(row)
                model["traces"].append(copy.deepcopy(row["geometry"]))
            if fault == "missing_connectivity":
                model["connectivity"]["complete"] = False
            if fault == "foreign_connectivity":
                model["connectivity"]["nets"][1]["opens"] = 1
            if fault == "missing_opens":
                del model["connectivity"]["nets"][0]["opens"]
            if fault == "wrong_disconnect":
                model["connectivity"]["nets"][0].update(opens=0, routed=True)
                model["unrouted"] = []
            if fault == "identity_failure":
                self.doc.state["routing_objects"][0]["uid"] = ""
            if fault == "delete_after":
                raise RuntimeError("synthetic failure after deletion")

    class Doc:
        FullName = str(board)
        CurrentUnit = 2

        def __init__(self, state):
            self.state = state

        @property
        def Traces(self):
            return Collection(
                Trace(r, self) for r in self.state["routing_objects"] if r["kind"] == "trace"
            )

        Vias = Collection()

        @property
        def Nets(self):
            doc = self

            class Net:
                def __init__(self, row):
                    self.row = row
                    self.Name, self.UniqueId = row["net"], row["uid"]

                def IsRouted(self):
                    return self.row["routed"]

                @property
                def NumberOfOpens(self):
                    read = {
                        "getter": "NumberOfOpens",
                        "net": self.Name,
                        "after_delete": bool(events),
                    }
                    app.connectivity_reads.append(read)
                    if events and self.Name == "A":
                        if fault == "connectivity_getter":
                            read["result"] = "raised"
                            raise RuntimeError("synthetic NumberOfOpens getter failure")
                        if fault == "connectivity_missing":
                            read["result"] = "missing"
                            return None
                    read["result"] = self.row["opens"]
                    return self.row["opens"]

            return Collection(
                Net(row) for row in doc.state["geometry"]["model"]["connectivity"]["nets"]
            )

        def Save(self):
            nonlocal stored
            events.append("save")
            if fault == "save":
                raise RuntimeError("synthetic Save refusal")
            if fault == "save_false":
                return False
            stored = copy.deepcopy(self.state)

        def Close(self, save):
            assert save is True
            events.append("close")
            if fault == "close":
                raise RuntimeError("synthetic close refusal")
            app.ActiveDocument = None
            return True

    def open_doc(application, path):
        assert application is app and path == board
        assert app.ActiveDocument is None
        events.append("reopen")
        if fault == "reopen":
            raise RuntimeError("synthetic reopen failure")
        app.ActiveDocument = Doc(copy.deepcopy(stored))
        if fault == "reopen_mismatch":
            app.ActiveDocument.state["geometry"]["model"]["holes"][0][2] = 1.2
        return app.ActiveDocument, []

    app.ActiveDocument = Doc(state)
    monkeypatch.setattr(A, "_application", lambda *a, **k: app)
    monkeypatch.setattr(A, "_licensed_document", lambda app: app.ActiveDocument)
    monkeypatch.setattr(A, "_open_layout_document", open_doc)
    monkeypatch.setattr(A, "_settle", lambda *a: None)

    def capture(doc, a, **kwargs):
        if fault == "observation_failure" and events:
            raise RuntimeError("synthetic observation failure after Delete")
        observed = copy.deepcopy(doc.state["geometry"])
        if fault in {"connectivity_getter", "connectivity_missing"}:
            connectivity = A._route_status(doc)
            observed["model"]["connectivity"] = {
                "complete": connectivity["observation_complete"],
                "nets": connectivity["net_status"],
            }
            observed["model"]["unrouted"] = connectivity["unrouted"]
        return observed

    monkeypatch.setattr(U, "capture", capture)
    monkeypatch.setattr(A, "_geometry_record", lambda geom, **k: copy.deepcopy(geom.record))
    params = {
        "project": str(board),
        "nets": ["A", "B"] if fault == "partial" else ["A"],
        "continue_on_error": False,
    }
    return app, params, events


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "zero",
        "partial",
        "save",
        "save_false",
        "close",
        "reopen",
        "reopen_mismatch",
        "uid_drift",
        "delete_after",
    ],
)
def test_native_unroute_uses_real_object_selection_and_normal_reopen(tmp_path, monkeypatch, fault):
    app, params, events = native_case(tmp_path, monkeypatch, fault)
    if fault == "zero":
        remove(app.ActiveDocument.state, {"A"})
    preview = A._unroute_nets(params, object())
    assert not events
    assert preview["unroute"]["expected_connectivity"] == [
        {"net": n, "uid": "11" if n == "A" else "12", "opens": 1, "routed": False}
        for n in params["nets"]
    ]
    if fault == "uid_drift":
        app.ActiveDocument.state["routing_objects"][0]["uid"] = "replacement"
    apply = {**params, "apply": True, "expected": preview["unroute"]["fingerprint"]}
    if fault in {"partial", "save", "save_false", "close", "reopen", "uid_drift"}:
        with pytest.raises(A.AdapterError) as caught:
            A._unroute_nets(apply, object())
        if fault == "uid_drift":
            assert caught.value.code == "E_CONFLICT"
            assert caught.value.details["write_attempted"] is False
            assert not events
            return
        result = caught.value.details["native_result"]
        assert result["change_status"] == ("partial" if fault == "partial" else "applied")
        assert result["persistence_state"] == ("saved" if fault == "partial" else "unknown")
        assert result["persistence_verified"] is (fault == "partial")
        if fault in {"save", "save_false"}:
            assert "close" not in events and "reopen" not in events
        if fault == "close":
            assert "reopen" not in events
        return
    result = A._unroute_nets(apply, object())
    assert result["persistence_verified"] is (fault not in {"zero", "reopen_mismatch"})
    if fault == "zero":
        assert result["no_change"] is True and not events
    else:
        assert events == ["delete:91", "save", "close", "reopen"]
        assert result["removed_objects"][0]["uid"] == "91"
        assert [r["uid"] for r in result["state_after"]["routing_objects"]] == ["92"]
    assert (
        result["state_after"]["geometry"]["components"]
        == preview["unroute"]["before"]["geometry"]["components"]
    )


@pytest.mark.parametrize(
    "fault",
    [
        "plane",
        "plane_layer",
        "missing_stackup",
        "native_units",
        "third_pin",
        "overlapping_pads",
        "at",
        "all",
    ],
)
def test_unsupported_unroute_stops_before_any_delete(tmp_path, monkeypatch, fault):
    app, params, events = native_case(tmp_path, monkeypatch)
    geo = app.ActiveDocument.state["geometry"]
    if fault == "plane":
        geo["model"]["planes"] = [{"net": "A", "layer": 2}]
    if fault == "plane_layer":
        geo["stackup"][1]["usage"] = "plane"
    if fault == "missing_stackup":
        geo["stackup"].pop()
    if fault == "native_units":
        app.ActiveDocument.CurrentUnit = 4
    if fault == "third_pin":
        geo["components"][0]["geometry"].append(copy.deepcopy(geo["components"][0]["geometry"][0]))
    if fault == "overlapping_pads":
        geo["components"][1]["geometry"][0]["copper"] = copy.deepcopy(
            geo["components"][0]["geometry"][0]["copper"]
        )
    if fault == "at":
        params["at"] = "0,0"
    if fault == "all":
        params["all"] = True
    with pytest.raises(A.AdapterError) as caught:
        A._unroute_nets(params, object())
    assert caught.value.code == "E_UNSUPPORTED"
    assert caught.value.details["write_attempted"] is False
    assert not events


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "mechanical",
        "pin_hole",
        "foreign_via",
        "foreign_drill",
        "orphan_link",
        "selected_remains",
    ],
)
def test_unroute_via_holes_are_removed_only_by_bound_native_ownership(tmp_path, monkeypatch, fault):
    app, _, _ = native_case(tmp_path, monkeypatch)
    state = copy.deepcopy(app.ActiveDocument.state)
    model = state["geometry"]["model"]
    for uid, net, y in [("201", "A", 0), ("202", "B", 3)]:
        via = {
            "kind": "via",
            "uid": uid,
            "net": net,
            "padstack": "ROUND",
            "center_mm": [2.5, y],
            "drill_mm": 0.3,
            "shape_type": 1,
            "plated": True,
            "span": [1, 2],
            "pad_geometry": [{"layer": 1, "diameter_mm": 0.6}, {"layer": 2, "diameter_mm": 0.6}],
        }
        model["vias"].append(via)
        model["holes"].append([2.5, y, 0.3])
        model["via_holes"].append({"uid": uid, "net": net, "geometry": [2.5, y, 0.3]})
        state["routing_objects"].append(
            {"kind": "via", "uid": uid, "net": net, "geometry": copy.deepcopy(via)}
        )
    # A mechanically owned duplicate at identical coordinates must remain.
    model["holes"].append([2.5, 0, 0.3])
    if fault == "orphan_link":
        model["via_holes"][0]["uid"] = "unrelated"
        with pytest.raises(A.AdapterError) as refused:
            U.bind(state, ["A"], A)
        assert refused.value.code == "E_UNSUPPORTED"
        return
    bound = U.bind(state, ["A"], A)
    after = copy.deepcopy(state)
    remove(after, {"A"})
    am = after["geometry"]["model"]
    if fault == "selected_remains":
        after["routing_objects"].append(copy.deepcopy(state["routing_objects"][-2]))
    else:
        am["vias"] = [v for v in am["vias"] if v["net"] != "A"]
        am["via_holes"] = [v for v in am["via_holes"] if v["net"] != "A"]
        am["holes"].remove([2.5, 0, 0.3])
    if fault == "mechanical":
        am["holes"].remove([2.5, 0, 0.3])
    if fault == "pin_hole":
        am["holes"].remove([0, 0, 1])
    if fault == "foreign_drill":
        am["holes"][1][2] = 0.4
    if fault == "foreign_via":
        after["routing_objects"][-1]["uid"] = "replacement"
    proof = U.compare(bound, after)
    assert proof["valid"] is (fault is None)


@pytest.mark.parametrize("shape,plated", [(1, True), (2, True), (10, True), (12, True), (1, False)])
def test_native_hole_shape_enum_not_positive_drill_alone_defines_round_scope(shape, plated):
    hole = SimpleNamespace(ShapeType=shape, Plated=plated, GetDrillSize=lambda unit: 0.3)
    via = SimpleNamespace(
        UniqueId=7,
        CurrentPadstack=SimpleNamespace(Name="VIA", Holes=Collection([hole])),
        GetPositionX=lambda unit: 10,
        GetPositionY=lambda unit: 12,
        StartLayer=1,
        EndLayer=4,
    )
    if shape == 1 and plated:
        result = A._via_ownership(via, "A")
        assert result["shape_type"] == 1 and result["plated"] is True
        assert result["drill_mm"] == 0.3 and result["uid"] == "7"
    else:
        with pytest.raises(ValueError, match="round plated"):
            A._via_ownership(via, "A")
