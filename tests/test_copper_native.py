"""Synthetic 151.0-shaped native objects, not real CAD acceptance.

The actual execute/observe/read_geometry/compare/save-close path runs. Only the
application attachment, saved-document reopening, and independent whole-board
capture are fixtures. Parameterized property reads use the real adapter's
_propget and a recording IDispatch stand-in; no COM server is created.
"""

import copy
import math
import sys
from types import SimpleNamespace

import pytest

from xpedition_cli import board_copper as C
from xpedition_cli import native_com_adapter as A


class Collection(list):
    @property
    def Count(self):
        return len(self)

    def Item(self, number):
        return self[number - 1]


class PropertyDispatch:
    def __init__(self, owner, values):
        self.owner, self.values = owner, values

    def Invoke(self, dispid, locale, kind, wants_result, *arguments):
        self.owner.getter_calls.append((dispid, locale, kind, wants_result, arguments))
        assert (locale, kind, wants_result) == (0, 2, 1)
        assert arguments == ((0,) if dispid in (33, 53) else (C.MM,))
        value = self.values[dispid]
        if isinstance(value, Exception):
            raise value
        return value


def rectangle(x1=1, y1=1, x2=9, y2=9):
    return [[x1, y1, 0], [x2, y1, 0], [x2, y2, 0], [x1, y2, 0], [x1, y1, 0]]


class NativeGeometry:
    def __init__(self, path=None, *, circle=None, cutouts=(), valid=True, closed=True):
        self.path = copy.deepcopy(rectangle() if path is None else path)
        self.circle = circle
        self.Cutouts = Collection(cutouts)
        self.Filled = True
        self.valid, self.closed = valid, closed
        self.getter_calls, self.method_calls = [], []
        values = {1: tuple(tuple(p[i] for p in self.path) for i in range(3))}
        if circle is not None:
            values.update(dict(zip((10, 11, 12), circle, strict=True)))
        self._oleobj_ = PropertyDispatch(self, values)

    @property
    def PointsArray(self):
        raise AssertionError("PointsArray must be invoked as a parameterized property")

    def IsValid(self):
        self.method_calls.append("IsValid")
        return self.valid

    def IsClosed(self):
        self.method_calls.append("IsClosed")
        return self.closed

    def IsCircle(self):
        self.method_calls.append("IsCircle")
        return self.circle is not None

    def IsPointInside(self, x, y, unit):
        assert unit == C.MM
        self.method_calls.append(("IsPointInside", x, y, unit))
        if self.circle:
            cx, cy, radius = self.circle
            return math.hypot(x - cx, y - cy) <= radius
        xs, ys = zip(*(p[:2] for p in self.path), strict=True)
        return min(xs) <= x <= max(xs) and min(ys) <= y <= max(ys)

    def record(self):
        result = (
            {"circle": list(self.circle)} if self.circle else {"path": copy.deepcopy(self.path)}
        )
        for cutout in self.Cutouts:
            if cutout.circle:
                result.setdefault("circular_cutouts", []).append(list(cutout.circle))
            else:
                result.setdefault("cutouts", []).append(copy.deepcopy(cutout.path))
        return result


class PlaneClass:
    Name = "Default"
    Parent = None

    def __init__(self):
        self.getter_calls = []
        for name in C._CLASS_SCALARS:
            setattr(self, name, False)
        self._oleobj_ = PropertyDispatch(self, {**dict.fromkeys(C._CLASS_MM, 0.254), 33: 0})


def board_geometry():
    return {
        "components": [
            {
                "refdes": refdes,
                "uid": uid,
                "protected_geometry": {"silk": [[x, 2], [x, 5]]},
                "geometry": [
                    {
                        "number": number,
                        "net": net,
                        "center_mm": [x, y],
                        "copper": [{"layer": 1}, {"layer": 2}],
                    }
                    for number, net, y in [("1", "A", 3), ("2", "B", 4)]
                ],
            }
            for refdes, uid, x in [("J1", "67", 3), ("R1", "62", 7)]
        ],
        "stackup": [{"layer": layer, "kind": "conductor", "usage": "signal"} for layer in (1, 2)],
        "model": {
            "layers": 2,
            "outline": [{"kind": "outline", "path": rectangle(0, 0, 10, 10)}],
            "holes": [[3, 3, 1]],
            "traces": [{"kind": "trace", "net": "B", "layer": 1, "width": 0.254}],
            "vias": [],
            "slots": [],
            "planes": [],
            "unrouted": [{"net": "A", "uid": "11", "opens": 1, "routed": False}],
            "connectivity": {
                "complete": True,
                "nets": [
                    {"net": "A", "uid": "11", "opens": 1, "routed": False},
                    {"net": "B", "uid": "12", "opens": 0, "routed": True},
                ],
            },
        },
    }


def native_case(tmp_path, monkeypatch, fault=None):
    """Keep the actual strict plane reader and save/close guards in the loop."""
    monkeypatch.setitem(sys.modules, "pythoncom", SimpleNamespace(DISPATCH_PROPERTYGET=2))
    events, native_calls = [], []
    stored = None
    board = tmp_path / "owned.pcb"
    board.write_bytes(b"synthetic-owned-board")
    app = SimpleNamespace(ActiveDocument=None)

    def corrupt(doc, phase):
        if fault == f"{phase}_hole":
            doc.geometry["model"]["holes"][0][2] = 1.2
        if fault == f"{phase}_component":
            doc.geometry["components"][1]["protected_geometry"]["silk"] = [[7, 8]]
        if fault == f"{phase}_trace":
            doc.geometry["model"]["traces"].clear()
        if fault == f"{phase}_foreign_assignment":
            doc.assignments[0]._state = 1
        if fault == f"{phase}_connectivity":
            doc.geometry["model"]["connectivity"]["complete"] = False

    class Assignment:
        def __init__(self, doc, layer, state=2):
            self.doc = doc
            self.Net = doc.nets[0]
            self.LayerObject = SimpleNamespace(ConductorLayer=layer)
            self.PlaneClass = doc.plane_class
            self.UseRouteBorderAsPlaneShape = False
            self._state = state

        @property
        def PlaneDataState(self):
            return self._state

        @PlaneDataState.setter
        def PlaneDataState(self, value):
            events.append(("generate", self.Net.UniqueId, self.LayerObject.ConductorLayer, value))
            assert self.LayerObject.ConductorLayer == 2, "Unrequested same-net layer was generated"
            if fault == "generate_fail":
                raise RuntimeError("synthetic generation refusal")
            self._state = value
            if fault != "draft_only":
                cuts = [
                    NativeGeometry(circle=(3, 4, 0.6)),
                    NativeGeometry(circle=(7, 4, 0.6)),
                    NativeGeometry(circle=(3, 3, 0.5)),
                ]
                generated = Generated(self.doc, NativeGeometry(cutouts=cuts))
                self.doc.generated = [generated]
                if fault == "partial_regions":
                    self.doc.generated.append(copy.copy(generated))
                if fault == "wrong_generated_layer":
                    generated.Layer = 1
                if fault == "wrong_generated_net":
                    generated.Net = self.doc.nets[1]
                if fault == "generated_unfilled":
                    generated.Geometry.Filled = False
                if fault == "foreign_pin_copper":
                    generated.Geometry.Cutouts.pop(0)
                if fault == "missing_contour":
                    generated.Geometry._oleobj_.values[1] = None
                if fault == "nonfinite_contour":
                    generated.Geometry._oleobj_.values[1] = ((1, 9, math.nan), (1, 1, 9), (0, 0, 0))
                if fault == "cutout_getter":
                    cuts[0]._oleobj_.values[12] = RuntimeError("synthetic cutout getter failure")
                if fault == "nested_cutout":
                    cuts[0].Cutouts.append(NativeGeometry(circle=(3, 4, 0.2)))
                if fault == "generated_invalid":
                    generated.valid = False
                if fault == "missing_hatch_width":
                    generated._oleobj_.values[51] = None
                if fault == "nonfinite_hatch_angle":
                    generated._oleobj_.values[53] = math.nan
                if fault == "missing_fill_setting":
                    generated.FillNonSquareHatchAreas = None
            corrupt(self.doc, "generated")
            if fault == "generate_after":
                raise RuntimeError("synthetic generation response failure after change")

    class Generated:
        HatchPattern = 0
        Layer = 2
        FillNonSquareHatchAreas = False

        def __init__(self, doc, geometry):
            self.Net = doc.nets[0]
            self.Geometry = geometry
            self.valid = True
            self.getter_calls = []
            self._oleobj_ = PropertyDispatch(self, {51: 0.254, 52: 0.508, 53: 0})
            self.Extrema = SimpleNamespace(
                GetMinX=lambda unit: 1,
                GetMinY=lambda unit: 1,
                GetMaxX=lambda unit: 9,
                GetMaxY=lambda unit: 9,
            )

        def IsValid(self):
            return self.valid

        @property
        def UniqueId(self):
            raise AssertionError("Hidden GeneratedPlane UID must not be read")

    class Doc:
        FullName = str(board)
        CurrentUnit = 2

        def __init__(self):
            self.geometry = board_geometry()
            self.nets = Collection(
                [SimpleNamespace(Name="A", UniqueId="11"), SimpleNamespace(Name="B", UniqueId="12")]
            )
            self.plane_class = PlaneClass()
            self.assignments = [Assignment(self, 1)]
            self.shapes, self.generated = [], []

        @property
        def Nets(self):
            return self.nets

        @property
        def PlaneShapes(self):
            return Collection(self.shapes)

        @property
        def GeneratedPlanes(self):
            return Collection(self.generated)

        @property
        def PlaneAssignments(self):
            return Collection(self.assignments)

        @property
        def PlaneClasses(self):
            return Collection([self.plane_class])

        def PutPlaneShape(
            self, layer, count, rows, net, obstruct, hatch, width, distance, comp, unit
        ):
            events.append(("create", net.UniqueId, layer))
            assert (layer, count, obstruct, hatch, width, distance, comp, unit) == (
                2,
                5,
                False,
                0,
                0,
                0,
                None,
                C.MM,
            )
            assert rows == ((1, 9, 9, 1, 1), (1, 1, 9, 9, 1), (0.0,) * 5)
            if fault == "create_fail":
                raise RuntimeError("synthetic creation refused before change")
            shape = SimpleNamespace(
                UniqueId=301,
                Net=net,
                Layer=layer,
                RouteObstructed=False,
                Geometry=NativeGeometry(list(map(list, zip(*rows, strict=True)))),
            )
            self.shapes.append(shape)
            self.assignments.append(Assignment(self, layer))
            corrupt(self, "created")
            if fault == "create_after":
                raise RuntimeError("synthetic response lost after creation")
            return shape

        def FindPlaneAssignment(self, layer, net):
            events.append(("find_assignment", net.UniqueId, layer))
            assert layer == 2 and net is self.nets[0]
            if fault == "missing_assignment":
                return None
            if fault == "wrong_assignment":
                return self.assignments[0]
            return self.assignments[1]

        def Save(self):
            nonlocal stored
            events.append("save")
            if fault == "save_raise":
                raise RuntimeError("synthetic Save refusal")
            if fault == "save_false":
                return False
            stored = copy.deepcopy(self)
            return True

        def Close(self, save):
            assert save is True
            events.append("close")
            if fault == "close_raise":
                raise RuntimeError("synthetic Close refusal")
            if fault == "close_false":
                return False
            if fault != "close_still_open":
                app.ActiveDocument = None
            return True

    def capture(doc, a, **kwargs):
        assert kwargs == {"strict": True}
        has_created = bool(doc.shapes)
        has_generated = bool(doc.generated)
        if (fault == "observe_created" and has_created) or (
            fault == "observe_generated" and has_generated
        ):
            raise RuntimeError("synthetic protected read failure")
        if fault == "reopen_observe" and "reopen" in events:
            raise RuntimeError("synthetic reopened read failure")
        value = copy.deepcopy(doc.geometry)
        value["model"]["planes"] = [
            {
                "kind": "plane",
                "net": plane.Net.Name,
                "layer": plane.Layer,
                **plane.Geometry.record(),
            }
            for plane in doc.generated
        ]
        return value

    def reopen(application, path):
        assert application is app and path == board and app.ActiveDocument is None
        events.append("reopen")
        if fault == "reopen_raise":
            raise RuntimeError("synthetic reopen refusal")
        app.ActiveDocument = copy.deepcopy(stored)
        if fault == "reopen_drift":
            app.ActiveDocument.geometry["model"]["holes"][0][2] = 1.2
        if fault == "reopen_geometry_drift":
            app.ActiveDocument.geometry["components"][1]["protected_geometry"]["silk"] = [[7, 8]]
        if fault == "reopen_plane_state_drift":
            app.ActiveDocument.generated[0]._oleobj_.values[52] = 0.50800001
        if fault == "reopen_contour_drift":
            cutout = app.ActiveDocument.generated[0].Geometry.Cutouts[2]
            cutout.circle = (3, 3, 0.50000001)
            cutout._oleobj_.values[12] = 0.50000001
        if fault == "reopen_rectangle_winding":
            shape = app.ActiveDocument.shapes[0]
            points = shape.Geometry.path
            shape.Geometry = NativeGeometry([points[0], *points[-2:0:-1], points[0]])
        return app.ActiveDocument, []

    original_call = A._native_call

    def recorded_call(name, function):
        native_calls.append((name, "begin"))
        try:
            value = original_call(name, function)
        except Exception:
            native_calls.append((name, "error"))
            raise
        native_calls.append((name, "returned"))
        return value

    app.ActiveDocument = Doc()
    monkeypatch.setattr(A, "_application", lambda *args, **kwargs: app)
    monkeypatch.setattr(A, "_licensed_document", lambda application: application.ActiveDocument)
    monkeypatch.setattr(A, "_open_layout_document", reopen)
    monkeypatch.setattr(A, "_settle", lambda seconds: None)
    monkeypatch.setattr(A, "_native_call", recorded_call)
    monkeypatch.setattr(C, "capture", capture)
    params = {"project": str(board), "net": "A", "layer": 2, "margin": 1}
    return app, params, events, native_calls


def apply_preview(params, events):
    preview = C.execute(params, object(), A)
    assert events == [], "Preview must not create, generate, save, close or open"
    assert "state_before_save" not in preview and "reopen_comparison" not in preview
    return {**params, "apply": True, "expected": preview["copper"]["fingerprint"]}, preview


def test_execute_targets_one_assignment_and_reopens_actual_generated_geometry(
    tmp_path, monkeypatch
):
    app, params, events, calls = native_case(tmp_path, monkeypatch)
    request, preview = apply_preview(params, events)
    result = C.execute(request, object(), A)
    assert events == [
        ("create", "11", 2),
        ("find_assignment", "11", 2),
        ("generate", "11", 2, 1),
        "save",
        "close",
        "reopen",
    ]
    assert result["change_status"] == "applied"
    assert result["saved"] is True and result["persistence_verified"] is True
    assert result["persistence_state"] == "saved"
    assert result["stages"]["close"] == "confirmed"
    assert result["stages"]["reopen"] == "verified"
    assert result["state_before_save"] == result["state_after"]
    assert result["reopen_comparison"] == {
        "equal": True,
        "before_sha256": C.digest(result["state_before_save"]),
        "after_sha256": C.digest(result["state_after"]),
        "geometry_equal": True,
        "plane_state_equal": True,
        "persistence_equivalent": True,
        "equivalence_basis": "strict",
    }
    assert {"state_before_save", "reopen_comparison"} <= set(result["_untrusted"])
    assert result["protection"]["protection_valid"] is True
    state = result["state_after"]["plane_state"]
    assert state["assignments"][0] == preview["copper"]["before"]["plane_state"]["assignments"][0]
    assert state["assignments"][0]["layer"] == 1 and state["assignments"][0]["state"] == 2
    assert state["assignments"][1]["layer"] == 2 and state["assignments"][1]["state"] == 1
    assert state["shapes"][0]["geometry"] == {"path": rectangle()}
    assert state["generated"][0]["geometry"] == {
        "path": rectangle(),
        "circular_cutouts": [[3, 4, 0.6], [7, 4, 0.6], [3, 3, 0.5]],
    }
    assert all(
        not p["copper_at_point"]
        for p in state["generated"][0]["point_witnesses"]
        if p.get("net") == "B"
    )
    assert app.ActiveDocument.assignments[0].PlaneDataState == 2
    actual_generated = app.ActiveDocument.generated[0]
    assert (51, 0, 2, 1, (4,)) in actual_generated.getter_calls
    assert (52, 0, 2, 1, (4,)) in actual_generated.getter_calls
    assert (53, 0, 2, 1, (0,)) in actual_generated.getter_calls
    assert state["generated"][0]["hatch_width_mm"] == 0.254
    assert state["generated"][0]["hatch_distance_mm"] == 0.508
    assert state["generated"][0]["hatch_angle_degrees"] == 0
    assert calls.index(("copper.observe_generated", "returned")) < calls.index(
        ("layout.SaveBeforeClose", "begin")
    )
    assert calls.index(("layout.CloseSavedDocument", "returned")) < calls.index(
        ("copper.reopen", "begin")
    )
    assert ("copper.observe_reopened", "returned") in calls


@pytest.mark.parametrize("phase", ["created", "generated"])
@pytest.mark.parametrize(
    "fault", ["hole", "component", "trace", "foreign_assignment", "connectivity"]
)
def test_execute_protection_damage_never_saves_closes_or_reopens(
    tmp_path, monkeypatch, phase, fault
):
    _, params, events, calls = native_case(tmp_path, monkeypatch, f"{phase}_{fault}")
    request, _ = apply_preview(params, events)
    with pytest.raises(A.AdapterError) as caught:
        C.execute(request, object(), A)
    result = caught.value.details["native_result"]
    assert result["change_status"] == "partial"
    assert result["created_shape_uid"] == "301"
    assert result["applied"] is True and result["saved"] is False
    assert result["persistence_state"] == "unknown"
    assert result["persistence_verified"] is False
    assert result["protection"]["protection_valid"] is False
    assert "state_before_save" not in result and "reopen_comparison" not in result
    assert not any(e in events for e in ("save", "close", "reopen"))
    assert (
        result["stages"]["save"]
        == result["stages"]["close"]
        == result["stages"]["reopen"]
        == "not_started"
    )
    assert not any(name.startswith("layout.") or name == "copper.reopen" for name, _ in calls)
    if phase == "created":
        assert not any(
            isinstance(e, tuple) and e[0] in {"find_assignment", "generate"} for e in events
        )


@pytest.mark.parametrize(
    "fault",
    [
        "observe_created",
        "observe_generated",
        "missing_contour",
        "nonfinite_contour",
        "cutout_getter",
        "nested_cutout",
        "generated_invalid",
        "missing_hatch_width",
        "nonfinite_hatch_angle",
        "missing_fill_setting",
    ],
)
def test_execute_observation_failure_preserves_partial_change_without_lifecycle(
    tmp_path, monkeypatch, fault
):
    _, params, events, calls = native_case(tmp_path, monkeypatch, fault)
    request, _ = apply_preview(params, events)
    with pytest.raises(A.AdapterError) as caught:
        C.execute(request, object(), A)
    result = caught.value.details["native_result"]
    assert result["change_status"] == "partial" and result["applied"] is True
    assert result["created_shape_uid"] == "301"
    assert result["saved"] is False and result["persistence_state"] == "unknown"
    assert result["failure_observation_error"]
    assert "state_before_save" not in result and "reopen_comparison" not in result
    assert not any(e in events for e in ("save", "close", "reopen"))
    assert result["stages"]["save"] == "not_started"
    assert ("copper.observe_failure", "error") in calls
    assert sum(e[0] == "create" for e in events if isinstance(e, tuple)) == 1
    assert sum(e[0] == "generate" for e in events if isinstance(e, tuple)) <= 1


@pytest.mark.parametrize(
    "fault",
    [
        "create_fail",
        "create_after",
        "generate_fail",
        "generate_after",
        "draft_only",
        "partial_regions",
        "missing_assignment",
        "wrong_assignment",
        "wrong_generated_layer",
        "wrong_generated_net",
        "generated_unfilled",
        "foreign_pin_copper",
    ],
)
def test_execute_partial_or_refused_generation_is_not_saved_as_success(
    tmp_path, monkeypatch, fault
):
    _, params, events, _ = native_case(tmp_path, monkeypatch, fault)
    request, _ = apply_preview(params, events)
    with pytest.raises(A.AdapterError) as caught:
        C.execute(request, object(), A)
    result = caught.value.details["native_result"]
    assert result["change_status"] == ("not_applied" if fault == "create_fail" else "partial")
    assert result["applied"] is (fault != "create_fail")
    assert result["saved"] is False and result["persistence_verified"] is False
    assert result["persistence_state"] == "unknown"
    assert "state_before_save" not in result and "reopen_comparison" not in result
    assert not any(e in events for e in ("save", "close", "reopen"))
    assert sum(e[0] == "create" for e in events if isinstance(e, tuple)) == 1
    assert sum(e[0] == "generate" for e in events if isinstance(e, tuple)) <= 1
    if fault == "draft_only":
        assert len(result["state_after"]["plane_state"]["shapes"]) == 1
        assert result["state_after"]["plane_state"]["generated"] == []
        assert result["protection"]["generation_complete"] is False
    if fault == "partial_regions":
        assert len(result["state_after"]["plane_state"]["generated"]) == 2
        assert result["protection"]["generation_complete"] is False
    if fault in {"missing_assignment", "wrong_assignment"}:
        assert not any(isinstance(e, tuple) and e[0] == "generate" for e in events)


@pytest.mark.parametrize(
    "fault",
    [
        "save_raise",
        "save_false",
        "close_raise",
        "close_false",
        "close_still_open",
        "reopen_raise",
        "reopen_drift",
        "reopen_geometry_drift",
        "reopen_plane_state_drift",
        "reopen_contour_drift",
        "reopen_observe",
    ],
)
def test_execute_lifecycle_failure_preserves_known_generation_and_save_facts(
    tmp_path, monkeypatch, fault
):
    _, params, events, calls = native_case(tmp_path, monkeypatch, fault)
    request, _ = apply_preview(params, events)
    with pytest.raises(A.AdapterError) as caught:
        C.execute(request, object(), A)
    result = caught.value.details["native_result"]
    assert result["change_status"] == "applied"
    assert result["protection"]["valid"] is True
    assert result["saved"] is not fault.startswith("save_")
    assert result["persistence_verified"] is False and result["persistence_state"] == "unknown"
    before = result["state_before_save"]
    after = result["state_after"]
    assert C.compare(result["copper"], before, result["created_shape_uid"])["valid"] is True
    assert len(before["plane_state"]["generated"]) == 1
    assert before["geometry"]["model"]["holes"] == [[3, 3, 1]]
    assert before["plane_state"]["generated"][0]["hatch_distance_mm"] == 0.508
    assert before["plane_state"]["generated"][0]["geometry"]["circular_cutouts"][2][2] == 0.5
    assert events.count("save") == 1
    assert events.count("close") == (0 if fault.startswith("save_") else 1)
    assert events.count("reopen") == (1 if fault.startswith("reopen_") else 0)
    if fault.startswith("close_"):
        assert result["stages"]["save"] == "acknowledged"
        assert result["stages"]["close"] == "unconfirmed"
    if fault.endswith("_drift"):
        assert caught.value.code == "E_VERIFY_INCOMPLETE"
        assert result["stages"]["reopen"] == "mismatch"
        assert result["post_write_error"]["reason"] == "Saved/reopened copper differs"
        assert before != after
        assert result["geometry_after"] == after["geometry"]
        assert result["reopen_comparison"] == {
            "equal": False,
            "before_sha256": C.digest(before),
            "after_sha256": C.digest(after),
            "geometry_equal": fault == "reopen_plane_state_drift",
            "plane_state_equal": fault == "reopen_geometry_drift",
            "persistence_equivalent": False,
            "equivalence_basis": "different_or_unsupported",
        }
    else:
        expected_code = {
            "save_raise": "E_SERVER",
            "save_false": "E_SERVER",
            "close_raise": "E_SERVER",
            "close_false": "E_HUMAN_REQUIRED",
            "close_still_open": "E_HUMAN_REQUIRED",
            "reopen_raise": "E_NATIVE_FAILURE",
            "reopen_observe": "E_NATIVE_FAILURE",
        }[fault]
        assert caught.value.code == expected_code
        assert before == after
        assert "reopen_comparison" not in result
    assert result["post_write_error"]["code"] == caught.value.code
    assert result["post_write_error"]["stage"] == (
        "reopen" if fault.startswith("reopen_") else "save"
    )
    if fault == "reopen_observe":
        assert result["stages"]["reopen"] == "returned"
        assert ("copper.reopen", "returned") in calls
        assert ("copper.observe_reopened", "error") in calls
    assert not any(name == "copper.observe_failure" for name, _ in calls)


@pytest.mark.parametrize("fault", [None, "save_false", "reopen_drift", "reopen_observe"])
def test_execute_presave_witness_is_detached_from_both_observations(tmp_path, monkeypatch, fault):
    _, params, events, _ = native_case(tmp_path, monkeypatch, fault)
    request, _ = apply_preview(params, events)
    original_call = A._native_call
    generated_observations = []

    def retain_generated_observation(name, function):
        observed = original_call(name, function)
        if name == "copper.observe_generated":
            generated_observations.append(observed)
        return observed

    monkeypatch.setattr(A, "_native_call", retain_generated_observation)
    if fault is None:
        result = C.execute(request, object(), A)
    else:
        with pytest.raises(A.AdapterError) as caught:
            C.execute(request, object(), A)
        result = caught.value.details["native_result"]
    assert len(generated_observations) == 1
    before = result["state_before_save"]
    expected = copy.deepcopy(before)
    assert before == generated_observations[0] and before is not generated_observations[0]
    generated_observations[0]["geometry"]["model"]["holes"][0][2] = 99
    generated_observations[0]["plane_state"]["generated"][0]["geometry"]["path"][0][0] = 99
    result["state_after"]["plane_state"]["assignments"][0]["state"] = 4
    assert before == expected, "Retained nested geometry and plane state must not alias live reads"
    after = copy.deepcopy(result["state_after"])
    before["geometry"]["model"]["holes"].clear()
    before["plane_state"]["generated"].clear()
    assert result["state_after"] == after
    assert events.count("save") == 1


@pytest.mark.parametrize(
    "fault",
    [
        "native_units",
        "existing_shape",
        "existing_generated",
        "plane_layer",
        "duplicate_conductor",
        "incomplete_layer_index",
        "route_border",
        "via",
        "outline",
        "drift",
    ],
)
def test_preflight_and_drift_refuse_before_any_native_mutation(tmp_path, monkeypatch, fault):
    app, params, events, _ = native_case(tmp_path, monkeypatch)
    request, _ = apply_preview(params, events)
    doc = app.ActiveDocument
    if fault == "native_units":
        doc.CurrentUnit = C.MM
    if fault == "existing_shape":
        doc.PutPlaneShape(
            2,
            5,
            ((1, 9, 9, 1, 1), (1, 1, 9, 9, 1), (0.0,) * 5),
            doc.nets[0],
            False,
            0,
            0,
            0,
            None,
            C.MM,
        )
    if fault == "existing_generated":
        doc.geometry["model"]["planes"] = [{"kind": "plane"}]
        monkeypatch.setattr(C, "capture", lambda *args, **kwargs: copy.deepcopy(doc.geometry))
    if fault == "plane_layer":
        doc.geometry["stackup"][1]["usage"] = "plane"
    if fault == "duplicate_conductor":
        doc.geometry["stackup"][0]["layer"] = 2
    if fault == "incomplete_layer_index":
        doc.geometry["stackup"][0]["layer"] = 3
    if fault == "route_border":
        doc.assignments[0].UseRouteBorderAsPlaneShape = True
    if fault == "via":
        doc.geometry["model"]["vias"] = [{"uid": "588"}]
    if fault == "outline":
        doc.geometry["model"]["outline"][0]["path"][1][1] = 2
    if fault == "drift":
        doc.geometry["model"]["holes"][0][2] = 1.2
    events.clear()
    with pytest.raises(A.AdapterError) as caught:
        C.execute(request, object(), A)
    assert caught.value.details["write_attempted"] is False
    if fault in {"duplicate_conductor", "incomplete_layer_index"}:
        assert caught.value.code == "E_UNSUPPORTED"
    assert events == []


@pytest.mark.parametrize("fault,code", [("missing", "E_NOT_FOUND"), ("uid_drift", "E_CONFLICT")])
def test_last_net_binding_failure_remains_predispatch(tmp_path, monkeypatch, fault, code):
    app, params, events, calls = native_case(tmp_path, monkeypatch)
    request, preview = apply_preview(params, events)
    assert preview["copper"]["target"]["net_uid"] == "11"
    original_find_net = A._find_net
    bindings = []

    def change_only_at_final_binding(doc, name):
        assert doc is app.ActiveDocument and name == "A"
        assert doc.nets[0].UniqueId == "11"
        bindings.append(name)
        # execute has already completed its fresh observe/bind with the old UID.
        assert calls[-1] == ("copper.observe_before", "returned")
        if fault == "missing":
            doc.nets.pop(0)
        else:
            doc.nets[0].UniqueId = "999"
        return original_find_net(doc, name)

    monkeypatch.setattr(A, "_find_net", change_only_at_final_binding)
    with pytest.raises(A.AdapterError) as caught:
        C.execute(request, object(), A)
    assert caught.value.code == code
    assert caught.value.details["write_attempted"] is False
    assert caught.value.details["stage"] == "copper.bind_net"
    assert "native_result" not in caught.value.details
    assert bindings == ["A"]
    assert events == [], "No PutPlaneShape/generation/Save/Close/Open may follow binding failure"
    assert all(name == "copper.observe_before" for name, _ in calls)


def test_geometry_parameterized_properties_predicates_and_cutouts_are_observed(monkeypatch):
    monkeypatch.setitem(sys.modules, "pythoncom", SimpleNamespace(DISPATCH_PROPERTYGET=2))
    cut = NativeGeometry(rectangle(2, 2, 3, 3))
    circle = NativeGeometry(circle=(6, 6, 0.5))
    geometry = NativeGeometry(cutouts=[cut, circle])
    observed = C.read_geometry(geometry, A)
    assert observed == {
        "path": rectangle(),
        "cutouts": [rectangle(2, 2, 3, 3)],
        "circular_cutouts": [[6, 6, 0.5]],
    }
    assert (
        geometry.method_calls
        == cut.method_calls
        == circle.method_calls
        == ["IsValid", "IsClosed", "IsCircle"]
    )
    assert geometry.getter_calls == cut.getter_calls == [(1, 0, 2, 1, (4,))]
    assert circle.getter_calls == [(d, 0, 2, 1, (4,)) for d in (10, 11, 12)]


@pytest.mark.parametrize(
    "fault",
    [
        "invalid",
        "open",
        "missing",
        "nan",
        "empty",
        "uneven",
        "cutout_count",
        "cutout_item",
        "circle_radius",
        "nested",
    ],
)
def test_actual_geometry_reader_rejects_incomplete_native_evidence(monkeypatch, fault):
    monkeypatch.setitem(sys.modules, "pythoncom", SimpleNamespace(DISPATCH_PROPERTYGET=2))
    geometry = NativeGeometry()
    if fault == "invalid":
        geometry.valid = False
    if fault == "open":
        geometry.closed = False
    if fault == "missing":
        geometry._oleobj_.values[1] = None
    if fault == "nan":
        geometry._oleobj_.values[1] = ((1, 9, math.nan), (1, 1, 9), (0, 0, 0))
    if fault == "empty":
        geometry._oleobj_.values[1] = ((1, 1, 1), (1, 1, 1), (0, 0, 0))
    if fault == "uneven":
        geometry._oleobj_.values[1] = ((1, 9, 9), (1, 1), (0, 0, 0))
    if fault == "cutout_count":
        geometry.Cutouts = SimpleNamespace(Count=None)
    if fault == "cutout_item":

        def fail_item(number):
            raise RuntimeError("synthetic cutout enumeration error")

        geometry.Cutouts = SimpleNamespace(Count=1, Item=fail_item)
    if fault == "circle_radius":
        geometry = NativeGeometry(circle=(3, 3, 0))
    if fault == "nested":
        geometry.Cutouts.append(NativeGeometry(cutouts=[NativeGeometry()]))
    with pytest.raises((ValueError, TypeError, RuntimeError)):
        C.read_geometry(geometry, A)
