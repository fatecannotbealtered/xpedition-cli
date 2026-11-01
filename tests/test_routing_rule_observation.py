"""Native-shaped supported location getter calls; no effective-path inference."""

from types import SimpleNamespace

import pytest

from xpedition_cli.routing_rule_observation import MAX_POINTS, observe, observe_named_widths


def test_location_scheme_reads_explicit_units_and_preserves_different_vertices():
    calls = []

    def pick(*args):
        calls.append(args)
        return "(Master)" if args[0] == 1 else "local-zone"

    result = observe(SimpleNamespace(PickScheme=pick), {"layer": 2, "points": [[1, 2], [3, 4]]})
    assert calls == [(1.0, 2.0, 2, -1, 4), (3.0, 4.0, 2, -1, 4)]
    assert [p["scheme"] for p in result["points"]] == ["(Master)", "local-zone"]
    assert result["observation_complete"]
    assert result["path_rule_applicability_proven"] is False
    assert result["influence_region_rule_applicability_proven"] is False


@pytest.mark.parametrize("value", [None, "", 1, False])
def test_missing_scheme_never_becomes_master(value):
    result = observe(
        SimpleNamespace(PickScheme=lambda *args: value),
        {"layer": 1, "points": [[0, 0], [1, 0]]},
    )
    assert result["observation_complete"] is False
    assert len(result["unreviewed"]) == 2
    assert all(p["scheme"] is None for p in result["points"])


def test_getter_failure_preserves_second_vertex_and_failure():
    def pick(x, *args):
        if x == 0:
            raise RuntimeError("owned fake getter failure")
        return "(Master)"

    result = observe(SimpleNamespace(PickScheme=pick), {"layer": 1, "points": [[0, 0], [1, 0]]})
    assert result["points"][0]["status"] == "unreadable"
    assert result["points"][1]["point_index"] == 1
    assert result["points"][1]["scheme"] == "(Master)"
    assert result["observation_complete"] is False


@pytest.mark.parametrize("layer", [None, 0, -1, True, 1.5, "1"])
def test_unknown_layer_has_no_default_or_native_call(layer):
    doc = SimpleNamespace(PickScheme=lambda *args: pytest.fail("no getter without layer"))
    result = observe(doc, {"layer": layer, "points": [[0, 0], [1, 0]]})
    assert result["points"] == [] and not result["observation_complete"]


def test_point_budget_and_malformed_points_are_explicit():
    doc = SimpleNamespace(PickScheme=lambda *args: "(Master)")
    result = observe(doc, {"layer": 1, "points": [[0, 0]] * (MAX_POINTS + 1)})
    assert result["points"] == [] and result["unreviewed"]
    result = observe(doc, {"layer": 1, "points": [[float("nan"), 0], [1, 0]]})
    assert not result["observation_complete"]
    assert result["points"][0]["point_index"] == 1


def width_fixture():
    net_class = SimpleNamespace(Name="Power")
    net = SimpleNamespace(Name="SUPPLY", UniqueId=123, NetClass=net_class)
    collection = SimpleNamespace(Count=1)
    doc = SimpleNamespace(
        LayerCount=4,
        PickScheme=lambda x, *_: "(Master)" if x == 1 else "Local",
        Nets=collection,
    )
    calls = []

    def propget(obj, dispid, layer, scheme, unit):
        assert obj is net_class
        calls.append((dispid, layer, scheme, unit))
        return {7: 0.2, 8: 0.3, 9: 0.4}[dispid]

    native = SimpleNamespace(
        NET_CLASS_WIDTHS={"minimum": 7, "typical": 8, "expansion": 9},
        _com_member=lambda owner, name: owner.Nets,
        _items=lambda coll, strict: [net],
        _propget=propget,
    )
    item = {"net": "SUPPLY", "net_uid": "123", "layer": 2, "points": [[1, 2], [3, 4]]}
    return doc, native, item, calls


def test_named_widths_bind_each_location_scheme_to_net_and_explicit_mm():
    doc, native, item, calls = width_fixture()
    result = observe_named_widths(doc, item, native)
    widths = result["named_widths"]
    assert widths["net"] == {"name": "SUPPLY", "uid": "123"}
    assert widths["class_name"] == "Power"
    assert widths["observation_complete"] is True
    assert [row["scheme"] for row in widths["points"]] == ["(Master)", "Local"]
    assert all(
        row["values_mm"] == {"minimum": 0.2, "typical": 0.3, "expansion": 0.4}
        for row in widths["points"]
    )
    assert calls == [(n, 2, scheme, 4) for scheme in ("(Master)", "Local") for n in (7, 8, 9)]
    assert widths["path_applicability_proven"] is False
    assert widths["clearance_applicability_proven"] is False


@pytest.mark.parametrize("fault", ["uid", "duplicate", "bad_width", "scheme"])
def test_named_width_failures_remain_unreviewed(fault):
    doc, native, item, _calls = width_fixture()
    if fault == "uid":
        item["net_uid"] = "999"
    elif fault == "duplicate":
        doc.Nets.Count = 2
        old = native._items
        native._items = lambda collection, strict: old(collection, strict) * 2
    elif fault == "bad_width":
        native._propget = lambda *_: float("nan")
    else:
        doc.PickScheme = lambda *_: None
    result = observe_named_widths(doc, item, native)["named_widths"]
    assert result["observation_complete"] is False
    assert result["unreviewed"]
    assert result["path_applicability_proven"] is False


@pytest.mark.parametrize("width,blocked", [(0.1, True), (0.3, False)])
def test_component_plan_freezes_samples_and_refuses_known_low_width(monkeypatch, width, blocked):
    from xpedition_cli import board_component_connect as component
    from xpedition_cli import native_component_candidate as candidate

    doc, native, request, _calls = width_fixture()
    request["width_mm"] = width
    calls = []

    def bind(*_args, **_kwargs):
        calls.append(True)
        return {
            "no_op": False,
            "blockers": [{"code": "E_CANDIDATE_MATERIAL_INCOMPLETE"}] if len(calls) == 1 else [],
            "can_apply": len(calls) != 1,
            "snapshot": {},
            "connection": {},
        }

    def fingerprint(plan):
        assert "location_rule_observation" in plan
        return "sampled-plan"

    monkeypatch.setattr(component, "bind", bind)
    monkeypatch.setattr(component, "_fingerprint", fingerprint)
    monkeypatch.setattr(candidate, "evaluate", lambda *_: {})
    plan = component._prepare_plan(doc, {}, request, {"live_files": []}, native, {})
    assert plan["fingerprint"] == "sampled-plan"
    assert plan["location_rule_observation"]["named_widths"]["observation_complete"]
    assert plan["can_apply"] is not blocked
    assert (
        any(row["code"] == "E_OBSERVED_POINT_MINIMUM_WIDTH_VIOLATION" for row in plan["blockers"])
        is blocked
    )


@pytest.mark.parametrize("fault", ["scheme", "width", "identity"])
def test_component_plan_refuses_when_a_point_rule_observation_is_unreadable(monkeypatch, fault):
    from xpedition_cli import board_component_connect as component
    from xpedition_cli import native_component_candidate as candidate

    doc, native, request, _calls = width_fixture()
    request["width_mm"] = 0.3
    if fault == "scheme":
        doc.PickScheme = lambda *_: None
    elif fault == "width":
        native._propget = lambda *_: float("nan")
    else:
        request["net_uid"] = "999"
    calls = []

    def bind(*_args, **_kwargs):
        calls.append(True)
        return {
            "no_op": False,
            "blockers": [{"code": "E_CANDIDATE_MATERIAL_INCOMPLETE"}] if len(calls) == 1 else [],
            "can_apply": len(calls) != 1,
            "snapshot": {},
            "connection": {},
        }

    monkeypatch.setattr(component, "bind", bind)
    monkeypatch.setattr(component, "_fingerprint", lambda _plan: "sampled-plan")
    monkeypatch.setattr(candidate, "evaluate", lambda *_: {})
    plan = component._prepare_plan(doc, {}, request, {"live_files": []}, native, {})
    assert plan["can_apply"] is False
    assert plan["location_rule_observation"]["named_widths"]["observation_complete"] is False
    assert any(row["code"] == "E_OBSERVED_POINT_RULES_INCOMPLETE" for row in plan["blockers"])
