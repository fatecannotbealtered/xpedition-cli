"""Finite General Route acceptance; synthetic native objects are not live proof."""

import copy
import sys
from types import SimpleNamespace as NS

import pytest
from test_routepass_continuation import continuation_case
from test_routepass_guard import native_case

from xpedition_cli import board_general as G
from xpedition_cli import native_com_adapter as A


class LateBound:
    """Real Invoke-only shape: no makepy-generated GetX convenience members."""

    def __init__(self, getter, *, name="IMGCPCBPin", guid="{E972C9A3-D5F6-4B04-8FE7-EDEDEDEDED09}"):
        self._oleobj_ = self
        self.getter, self.name, self.guid = getter, name, guid

    def Invoke(self, dispid, lcid, kind, result, *args):
        assert lcid == 0 and kind == 2 and result == 1
        return self.getter(dispid, args)

    def GetTypeInfo(self):
        return NS(GetDocumentation=lambda _: (self.name,), GetTypeAttr=lambda: (self.guid,))

    def IsValid(self):
        return True


def config():
    return {
        "layer_count": 2,
        "supported_fields_complete": True,
        "engine_configuration_complete": False,
        "not_checked": [
            {"dispid": 1, "reason": "Deprecated"},
            {"dispid": 21, "reason": "Deprecated"},
        ],
        "active_route_layer": 1,
        "active_mode": 1,
        "route_layers": [
            {"layer": 1, "editor_enabled": True, "document_enabled": True},
            {"layer": 2, "editor_enabled": True, "document_enabled": True},
        ],
        "route_max_vias": 4,
        "interactive_drc": True,
        "via_rules": [{"enum": i, "enabled": True} for i in range(4)],
        "net_rules": [{"enum": i, "enabled": True} for i in range(4)],
        "push_and_shove": [{"enum": i, "enabled": True} for i in range(7)],
        "output_guarantee": False,
    }


def general_case(*, fault=None):
    original, after = continuation_case(fault="missing_J3" if fault is None else fault)
    before = original["before"]
    request = {
        "version": 1,
        "net": "GND",
        "layer": 1,
        "endpoint_pin_uids": [
            next(r["pin_uid"] for r in before["pin_identities"] if r["refdes"] == name)
            for name in ("J2", "C2")
        ],
        "allow_replace_trace_uids": ["1442"],
        "bounds_mm": [-10, -10, 40, 40],
        "expected_components_after": 2,
    }
    pin_rows = {r["pin_uid"]: r for r in before["pin_identities"]}
    row = {
        "net": "GND",
        "net_uid": "n1",
        "from_layer": 1,
        "to_layer": 1,
        "nearest_from_pin": pin_rows[request["endpoint_pin_uids"][0]],
        "nearest_to_pin": pin_rows[request["endpoint_pin_uids"][1]],
        "selected": False,
        "identity_sha256": "7" * 64,
    }
    row["from_object"] = {"kind": "pin", **copy.deepcopy(row["nearest_from_pin"])}
    row["to_object"] = {"kind": "pin", **copy.deepcopy(row["nearest_to_pin"])}
    deps = {
        "source_backup": {},
        "live_files": [],
        "live_unavailable": [],
        "non_rule_observed_files": [],
        "live_file_count": 0,
        "non_rule_exclusion_policy": {},
        "limitations": [],
    }
    plan = G.bind(before, request, deps, config(), [row], None, A)
    if fault is None:
        after["geometry"]["model"]["connectivity"]["nets"][0].update(opens=1, routed=False)
        after["geometry"]["model"]["unrouted"] = [
            copy.deepcopy(after["geometry"]["model"]["connectivity"]["nets"][0])
        ]
    return plan, after


def test_one_declared_group_merge_accepts_known_partial_not_whole_net():
    plan, after = general_case()
    result = G.compare(plan, after)
    assert result["valid"], result
    assert result["completion_valid"] and not result["whole_net_complete"]
    assert result["native_connectivity"]["opens"] == 1


@pytest.mark.parametrize(
    "fault",
    [
        "isolated",
        "wrong_net",
        "wrong_layer",
        "microgap",
        "hole_only",
        "old_deleted_native",
        "old_renamed_native",
        "old_width_native",
    ],
)
def test_old_foreign_copper_and_material_faults_cannot_pass(fault):
    plan, after = general_case(fault=fault)
    assert not G.compare(plan, after)["valid"]


def test_native_opens_zero_does_not_override_wrong_component_group():
    plan, after = general_case()
    after["geometry"]["model"]["connectivity"]["nets"][0].update(opens=0, routed=True)
    after["geometry"]["model"]["unrouted"] = []
    assert not G.compare(plan, after)["valid"]


def test_declared_bounds_cover_full_material_not_just_centerline():
    plan, after = general_case()
    request = copy.deepcopy(plan["request"])
    request["bounds_mm"][2] = 20
    plan = G.bind(
        plan["before"], request, plan["input_files"], config(), plan["fromto_inventory"], None, A
    )
    result = G.compare(plan, after)
    assert not result["valid"]
    assert any("material bounds" in row["name"] and not row["passed"] for row in result["checks"])


def test_whole_net_merge_requires_explicit_before_bound_after_one():
    plan, _ = general_case()
    request = {**plan["request"], "expected_components_after": 1}
    whole = G.bind(
        plan["before"], request, plan["input_files"], config(), plan["fromto_inventory"], None, A
    )
    assert len(whole["expected_pin_groups"]) == 1 and len(whole["expected_pin_groups"][0]) == 6
    assert whole["fingerprint"] != plan["fingerprint"]


@pytest.mark.parametrize("fault", ["old_renamed_native", "old_width_native"])
def test_only_predeclared_target_replacement_is_accepted_with_complete_material_graph(fault):
    plan, after = general_case(fault=fault)
    request = {
        **plan["request"],
        "expected_components_after": 1,
        "allow_replace_trace_uids": ["1441"],
    }
    whole = G.bind(
        plan["before"], request, plan["input_files"], config(), plan["fromto_inventory"], None, A
    )
    result = G.compare(whole, after)
    assert result["valid"], result
    assert result["whole_net_complete"]
    assert result["deleted_objects"] or result["changed_old_objects"]


def test_nearest_pin_observation_cannot_replace_actual_false_endpoint_identity():
    plan, _ = general_case()
    fromtos = copy.deepcopy(plan["fromto_inventory"])
    fromtos[0]["from_object"] = copy.deepcopy(fromtos[0]["to_object"])
    with pytest.raises(ValueError, match="False FromTo endpoint"):
        G.bind(plan["before"], plan["request"], plan["input_files"], config(), fromtos, None, A)


def test_configuration_getters_freeze_all_options_without_writing_or_claiming_no_vias(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "pythoncom", NS(DISPATCH_PROPERTYGET=2))

    def editor_get(dispid, args):
        calls.append((dispid, args))
        assert dispid not in (1, 21), "Deprecated routing getters must never be accessed"
        return {2: 4, 53: True, 5: True, 4: True, 22: True, 23: False}[dispid]

    editor = LateBound(editor_get)
    gui_calls = []

    def gui_get(dispid, args):
        gui_calls.append((dispid, args))
        return 1 if dispid in (16, 17) and not args else None

    gui = LateBound(gui_get)
    doc = LateBound(
        lambda dispid, args: (
            editor
            if dispid == 108 and not args
            else 2
            if dispid == 17 and not args
            else True
            if dispid == 75 and len(args) == 1
            else None
        )
    )
    app = LateBound(lambda dispid, args: gui if dispid == 12 and not args else None)
    with pytest.raises(AttributeError, match="GetRouteLayerEnable"):
        editor.GetRouteLayerEnable(1)
    result = G.observe_configuration(doc, 2, app, A)
    assert result["output_guarantee"] is False
    assert result["route_max_vias"] == 4
    assert result["active_mode"] == 1
    assert gui_calls == [(16, ()), (17, ())]
    assert result["via_rules"][0]["enabled"] is False
    assert "more via per SMD" in result["via_rule_semantics"]["0"]
    assert len(result["push_and_shove"]) == 7
    assert len(calls) == 2 + 7 + 4 + 4 + 2
    assert result["engine_configuration_complete"] is False
    assert result["supported_fields_complete"] is True and "route_options" not in result
    assert {row["dispid"] for row in result["not_checked"] if "dispid" in row} == {1, 21}
    assert [(value, args) for value, args in calls if value == 5] == [(5, (1,)), (5, (2,))]


def test_real_fromto_adapter_distinguishes_false_endpoints_true_pins_and_explicit_mm(monkeypatch):
    plan, _ = general_case()
    before, selected = plan["before"], plan["selected_fromto"]
    calls = []

    class Collection(list):
        def Item(self, index):
            return self[index - 1]

        @property
        def Count(self):
            return len(self)

    monkeypatch.setitem(sys.modules, "pythoncom", NS(DISPATCH_PROPERTYGET=2))
    endpoints = [
        LateBound(
            lambda dispid, args, uid=selected[key]["pin_uid"]: (
                uid if dispid == 64 and not args else None
            )
        )
        for key in ("nearest_from_pin", "nearest_to_pin")
    ]

    def get_fromto(dispid, args):
        calls.append((dispid, args))
        if dispid in (3, 4, 5, 6):
            return float(dispid - 2)
        if dispid in (11, 12):
            return endpoints[dispid - 11]
        return {7: 1, 8: 1, 9: NS(Name="GND", UniqueId="n1"), 1: False}[dispid]

    item = LateBound(get_fromto)
    with pytest.raises(AttributeError, match="GetFromX"):
        item.GetFromX(4)
    doc = LateBound(
        lambda dispid, args: Collection([item]) if dispid == 29 and args == (0, 1) else None
    )
    rows = G.observe_fromtos(doc, before, A)
    assert rows[0][0]["unit_enum"] == 4 and rows[0][0]["from_mm"] == [1.0, 2.0]
    assert {(11, (False,)), (11, (True,)), (12, (False,)), (12, (True,))} <= set(calls)
    assert [args for dispid, args in calls if dispid in (3, 4, 5, 6)] == [(4,)] * 4
    assert rows[0][0]["from_object"]["type_info"]["name"] == "IMGCPCBPin"


def test_missing_configuration_read_is_not_coerced_to_false():
    with pytest.raises(ValueError, match="BOOL"):
        G._bool(None, "failed getter")


@pytest.mark.parametrize("active_mode", [0, 4])
def test_non_route_active_mode_is_refused_with_gui_route_mode_guidance(monkeypatch, active_mode):
    plan, _ = general_case()
    monkeypatch.setitem(sys.modules, "pythoncom", NS(DISPATCH_PROPERTYGET=2))
    editor = LateBound(
        lambda dispid, args: {2: 4, 53: True, 5: True, 4: True, 22: True, 23: False}[dispid]
    )
    gui = LateBound(
        lambda dispid, args: active_mode if dispid == 17 else 1 if dispid == 16 else None
    )
    doc = LateBound(lambda dispid, args: 2 if dispid == 17 else editor if dispid == 108 else True)
    app = LateBound(lambda dispid, args: gui if dispid == 12 else None)
    actual = G.observe_configuration(doc, 2, app, A)
    assert actual["supported_fields_complete"] is True and actual["active_mode"] == active_mode
    with pytest.raises(ValueError, match="GUI.*Route working mode"):
        G.bind(
            plan["before"],
            plan["request"],
            plan["input_files"],
            actual,
            plan["fromto_inventory"],
            None,
            A,
        )


@pytest.mark.parametrize("failure", [RuntimeError("native ActiveMode unavailable"), None, True])
def test_active_mode_adapter_failure_retains_exact_incomplete_read(monkeypatch, failure):
    monkeypatch.setitem(sys.modules, "pythoncom", NS(DISPATCH_PROPERTYGET=2))
    calls = []

    def gui_get(dispid, args):
        calls.append((dispid, args))
        if dispid == 17:
            if isinstance(failure, Exception):
                raise failure
            return failure
        return 1 if dispid == 16 else None

    gui = LateBound(gui_get)
    editor = LateBound(lambda *_args: pytest.fail("Mode failure must stop subsequent GETs"))
    doc = LateBound(lambda dispid, args: 2 if dispid == 17 else editor if dispid == 108 else None)
    app = LateBound(lambda dispid, args: gui if dispid == 12 else None)
    with pytest.raises(ValueError, match="active_mode PROPERTYGET 17") as raised:
        G.observe_configuration(doc, 2, app, A)
    partial = raised.value.configuration_observation
    assert partial["supported_fields_complete"] is False
    assert partial["active_route_layer"] == 1
    assert "active_mode" not in partial
    assert partial["failed_field"]["field"] == "active_mode"
    assert partial["failed_field"]["interface"] == "IMGCPCBGui"
    assert partial["failed_field"]["dispid"] == 17
    assert partial["failed_field"]["arguments"] == []
    assert partial["failed_field"]["returned"] is False
    assert "active_mode" not in partial["completed_fields"]
    assert calls == [(16, ()), (17, ())]


def test_supported_configuration_failure_has_exact_field_and_completed_partial(monkeypatch):
    monkeypatch.setitem(sys.modules, "pythoncom", NS(DISPATCH_PROPERTYGET=2))

    def editor_get(dispid, args):
        assert dispid not in (1, 21)
        if dispid == 4 and args == (3,):
            raise RuntimeError("registered native getter failure")
        return {2: 4, 53: True, 5: True, 4: True, 22: True, 23: False}[dispid]

    editor = LateBound(editor_get)
    gui = LateBound(lambda dispid, args: 1)
    doc = LateBound(lambda dispid, args: 2 if dispid == 17 else editor if dispid == 108 else True)
    app = LateBound(lambda dispid, args: gui)
    with pytest.raises(ValueError, match="push_and_shove.3") as raised:
        G.observe_configuration(doc, 2, app, A)
    partial = raised.value.configuration_observation
    assert not partial["supported_fields_complete"]
    assert partial["layer_count"] == 2 and partial["route_max_vias"] == 4
    assert partial["failed_field"]["field"] == "push_and_shove.3"
    assert partial["failed_field"]["dispid"] == 4 and partial["failed_field"]["arguments"] == [3]
    assert partial["failed_field"]["returned"] is False
    assert "push_and_shove.2" in partial["completed_fields"]
    assert "push_and_shove.3" not in partial["completed_fields"]


@pytest.mark.parametrize(
    "name,guid",
    [("UNKNOWN", "{UNKNOWN}"), ("IMGCPCBPin", "{E972C9A3-D5F6-4B04-8FE7-EDEDEDEDED0B}")],
)
def test_formal_endpoint_type_must_match_before_uid_can_bind_inventory(name, guid):
    plan, _ = general_case()

    def no_uid(_dispid, _args):
        pytest.fail("unknown type must not be guessed by reading a possibly matching UID")

    with pytest.raises(ValueError, match="unknown formal type") as raised:
        G._endpoint_object(
            LateBound(no_uid, name=name, guid=guid), plan["before"], A, pins_only=False
        )
    assert raised.value.endpoint_type_observation == {
        "name": name,
        "guid": guid,
        "supported": False,
    }


def test_preflight_getter_failure_preserves_complete_state_without_selection(tmp_path, monkeypatch):
    _, params, events = native_general(tmp_path, monkeypatch)

    def fail(*_args):
        raise A.AdapterError("E_NATIVE_FAILURE", "formal PROPERTYGET5 returned native error")

    monkeypatch.setattr(G, "observe_configuration", fail)
    with pytest.raises(A.AdapterError) as raised:
        G.execute(params, object(), A)
    assert not events
    assert raised.value.code == "E_NATIVE_FAILURE"
    assert raised.value.details["preflight_observation"]["state"] is None
    assert raised.value.details["write_attempted"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", True),
        ("net", "A_IN"),
        ("layer", True),
        ("endpoint_pin_uids", ["x", "x"]),
        ("bounds_mm", [0, 0, float("nan"), 5]),
        ("allow_replace_trace_uids", ["1442", "1442"]),
        ("expected_components_after", True),
    ],
)
def test_plan_refuses_ambiguous_or_invalid_fields(field, value):
    plan, _ = general_case()
    with pytest.raises(ValueError):
        G.validate({**plan["request"], field: value})


def test_no_manual_verified_switch_or_unknown_plan_fields():
    plan, _ = general_case()
    with pytest.raises(ValueError):
        G.validate({**plan["request"], "verified": True})


def native_general(tmp_path, monkeypatch, fault=None):
    app, params, events, identity, _ = native_case(tmp_path, monkeypatch, fault=fault)
    plan, after = general_case()

    def closed_identity(_app):
        if fault == "post_close_missing":
            raise ValueError("Post-Close application evidence unavailable")
        result = {key: copy.deepcopy(identity[key]) for key in ("application", "generation")}
        if fault == "post_close_drift":
            result["generation"]["birth"] = "different"
        return result

    monkeypatch.setattr(G.board_labels, "_application_identity", closed_identity)
    app.ActiveDocument.state = copy.deepcopy(plan["before"])
    project, board = params["project"], app.ActiveDocument.FullName
    monkeypatch.setattr(
        G,
        "active",
        lambda *args, **kwargs: (
            project,
            __import__("pathlib").Path(board),
            app,
            app.ActiveDocument,
        ),
    )
    current_config = config()
    app.general_configuration = current_config
    monkeypatch.setattr(G, "observe_configuration", lambda *args: copy.deepcopy(current_config))
    monkeypatch.setattr(G.win_dialogs, "desktop_locked", lambda: False)

    def put_active(_app, layer, _adapter):
        events.append(("Gui.ActiveRouteLayer", layer))
        current_config["active_route_layer"] = layer
        if fault == "ui_reply_loss":
            raise RuntimeError("UI layer PUT returned no reply")
        if fault == "ui_wrong_value":
            current_config["active_route_layer"] = layer + 1
        if fault == "ui_other_setting":
            current_config["route_max_vias"] += 1

    monkeypatch.setattr(G, "_put_active_layer", put_active)
    selected = NS(value=False)

    def fromtos(doc, state, a, *, selected=False):
        if selected and not holder.value:
            return []
        row = {**copy.deepcopy(plan["selected_fromto"]), "selected": holder.value}
        return [(row, actual, 0)]

    holder = selected

    class FromTo:
        @property
        def Selected(self):
            return holder.value

        @Selected.setter
        def Selected(self, value):
            events.append(("FromTo.Selected", value))
            holder.value = value

    actual = FromTo()
    monkeypatch.setattr(G, "observe_fromtos", fromtos)

    def selection(*_args):
        return {
            "complete": True,
            "nets": [{"uid": "foreign", "name": "OTHER", "selected": True}]
            if fault == "foreign_selection"
            else [],
            "components": [],
            "pins": [],
            "traces": [],
            "vias": [],
            "fromtos": [r[0] for r in fromtos(None, None, None, selected=True)],
        }

    monkeypatch.setattr(G, "observe_selection", selection)

    def unselect():
        events.append(("UnSelectAll",))
        holder.value = False

    app.ActiveDocument.UnSelectAll = unselect

    def process(command, synchronous):
        events.append(("ProcessCommand", command, synchronous))
        app.ActiveDocument.state = copy.deepcopy(after)
        if fault == "command_reply_loss":
            raise RuntimeError("native command reply lost after edit")
        return None if fault == "command_none" else False if fault == "command_false" else True

    app.Gui = NS(ProcessCommand=process)
    params.pop("routepass_request")
    params.update(mode="bounded-general", general_request=plan["request"])
    return app, params, events


def test_selection_command_and_strict_persistence_complete(tmp_path, monkeypatch):
    app, params, events = native_general(tmp_path, monkeypatch)
    preview = G.execute(params, object(), A)
    assert not events
    result = G.execute({**params, "apply": True, "expected": preview["state_digest"]}, object(), A)
    assert [r[0] for r in events] == [
        "UnSelectAll",
        "FromTo.Selected",
        "ProcessCommand",
        "Save",
        "Close",
        "Open",
    ]
    assert result["saved"] and result["persistence_verified"]
    assert G.verify_receipt(result, preview["general_route"], app.ActiveDocument.state)["valid"]


def test_preview_and_confirmation_disable_legacy_quiet_attach_setters(tmp_path, monkeypatch):
    _, params, events = native_general(tmp_path, monkeypatch)
    original = G.active
    observed = []

    def attach(*args, **kwargs):
        observed.append(copy.deepcopy(kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(G, "active", attach)
    preview = G.execute(params, object(), A)
    assert events == []
    G.execute({**params, "apply": True, "expected": preview["state_digest"]}, object(), A)
    assert observed == [
        {"require_project": False, "quiet": False},
        {"require_project": False, "quiet": False},
    ]


def test_default_does_not_select_a_different_active_layer(tmp_path, monkeypatch):
    app, params, events = native_general(tmp_path, monkeypatch)
    app.general_configuration["active_route_layer"] = 2
    with pytest.raises(A.AdapterError):
        G.execute(params, object(), A)
    assert events == []


def test_explicit_ui_layer_selection_has_one_put_and_unchanged_other_configuration(
    tmp_path, monkeypatch
):
    app, params, events = native_general(tmp_path, monkeypatch)
    app.general_configuration["active_route_layer"] = 2
    params["general_request"]["select_active_layer"] = True
    preview = G.execute(params, object(), A)
    assert preview["general_route"]["ui_layer_selection"]["before"] == 2
    assert events == []
    done = G.execute({**params, "apply": True, "expected": preview["state_digest"]}, object(), A)
    assert events[0] == ("Gui.ActiveRouteLayer", 1)
    assert [r[0] for r in events].count("Gui.ActiveRouteLayer") == 1
    assert done["ui_write_acknowledged"] and done["engineering_write_dispatched"]
    assert done["configuration_after_ui"]["route_max_vias"] == 4
    assert G.verify_receipt(done, preview["general_route"], app.ActiveDocument.state)["valid"]


@pytest.mark.parametrize("fault", ["ui_reply_loss", "ui_wrong_value", "ui_other_setting"])
def test_ui_precondition_failure_retains_ui_facts_with_zero_engineering_commands(
    tmp_path, monkeypatch, fault
):
    app, params, events = native_general(tmp_path, monkeypatch, fault=fault)
    app.general_configuration["active_route_layer"] = 2
    params["general_request"]["select_active_layer"] = True
    preview = G.execute(params, object(), A)
    with pytest.raises(A.AdapterError) as raised:
        G.execute({**params, "apply": True, "expected": preview["state_digest"]}, object(), A)
    actual = raised.value.details["native_result"]
    assert events == [("Gui.ActiveRouteLayer", 1)]
    assert actual["ui_write_attempted"] is True
    assert not actual["engineering_write_dispatched"] and not actual["write_attempted"]
    assert not actual["applied"] and not actual["saved"]


@pytest.mark.parametrize("locked_index", range(7))
def test_desktop_lock_stops_each_dependent_action_without_replay(
    tmp_path, monkeypatch, locked_index
):
    app, params, events = native_general(tmp_path, monkeypatch)
    app.general_configuration["active_route_layer"] = 2
    params["general_request"]["select_active_layer"] = True
    preview = G.execute(params, object(), A)
    reads = []

    def locked():
        reads.append(1)
        return len(reads) - 1 == locked_index

    monkeypatch.setattr(G.win_dialogs, "desktop_locked", locked)
    with pytest.raises(A.AdapterError) as raised:
        G.execute({**params, "apply": True, "expected": preview["state_digest"]}, object(), A)
    assert len(events) == locked_index
    assert raised.value.code == "E_HUMAN_REQUIRED"
    assert sum(r[0] == "ProcessCommand" for r in events) <= 1
    if locked_index == 6:
        actual = raised.value.details["native_result"]
        assert actual["saved"] and actual["stages"]["close"] == "confirmed"
        assert app.ActiveDocument is None


def test_only_initial_postroute_and_reopen_recompute_material_while_raw_gates_remain_fresh(
    tmp_path, monkeypatch
):
    _, params, _ = native_general(tmp_path, monkeypatch)
    observed, reused = [], []
    full, raw = G.R.observe, G.R.observe_for_save

    def full_observe(*args, **kwargs):
        observed.append(1)
        return full(*args, **kwargs)

    def raw_observe(*args, **kwargs):
        reused.append(1)
        return raw(*args, **kwargs)

    monkeypatch.setattr(G.R, "observe", full_observe)
    monkeypatch.setattr(G.R, "observe_for_save", raw_observe)
    preview = G.execute(params, object(), A)
    observed.clear()
    done = G.execute({**params, "apply": True, "expected": preview["state_digest"]}, object(), A)
    assert len(observed) == 3 and len(reused) == 3
    assert done["execution_budget_seconds"] == 1800
    assert all(row["complete"] and row["elapsed_seconds"] >= 0 for row in done["phase_timings"])


def test_raw_drift_gate_refuses_without_fallback_material_or_command(tmp_path, monkeypatch):
    _, params, events = native_general(tmp_path, monkeypatch)
    preview = G.execute(params, object(), A)

    def raw_fail(*_args):
        raise ValueError("Fresh pre-Save raw design/material inputs differ or are incomplete")

    monkeypatch.setattr(G.R, "observe_for_save", raw_fail)
    with pytest.raises(A.AdapterError) as raised:
        G.execute({**params, "apply": True, "expected": preview["state_digest"]}, object(), A)
    assert not events
    result = raised.value.details["native_result"]
    assert result["phase_timings"][-1]["phase"] == "before_selection_raw"
    assert result["phase_timings"][-1]["complete"] is False
    assert not result["write_attempted"]


@pytest.mark.parametrize(
    "fault,last",
    [
        ("command_reply_loss", "ProcessCommand"),
        ("command_none", "ProcessCommand"),
        ("command_false", "ProcessCommand"),
        ("save_none", "Save"),
        ("save_false", "Save"),
        ("save_exception", "Save"),
        ("save_reply_loss", "Save"),
        ("close_none", "Close"),
        ("close_false", "Close"),
        ("close_exception", "Close"),
        ("open_fail", "Open"),
        ("reopen_drift", "Open"),
        ("post_close_missing", "Close"),
        ("post_close_drift", "Close"),
    ],
)
def test_ambiguous_phase_stops_without_following_persistence(tmp_path, monkeypatch, fault, last):
    _, params, events = native_general(tmp_path, monkeypatch, fault=fault)
    preview = G.execute(params, object(), A)
    with pytest.raises(A.AdapterError) as raised:
        G.execute({**params, "apply": True, "expected": preview["state_digest"]}, object(), A)
    assert events[-1][0] == last
    assert sum(r[0] == "ProcessCommand" for r in events) == 1
    assert raised.value.details["native_result"]["write_attempted"] is True
    if fault.startswith("post_close"):
        result = raised.value.details["native_result"]
        assert result["saved"] is True and not result["persistence_verified"]
        assert result["stages"]["close"] == "confirmed"
        assert result["stages"]["reopen"] == "not_started"


def test_input_drift_refuses_before_any_selection_or_route(tmp_path, monkeypatch):
    app, params, events = native_general(tmp_path, monkeypatch)
    preview = G.execute(params, object(), A)
    app.ActiveDocument.state["texts"][0]["position"][0] += 1
    with pytest.raises(A.AdapterError):
        G.execute({**params, "apply": True, "expected": preview["state_digest"]}, object(), A)
    assert not events


@pytest.mark.parametrize("active_mode", [0, 4])
def test_active_mode_drift_after_preview_stops_without_selection_or_command(
    tmp_path, monkeypatch, active_mode
):
    app, params, events = native_general(tmp_path, monkeypatch)
    preview = G.execute(params, object(), A)
    app.general_configuration["active_mode"] = active_mode
    with pytest.raises(A.AdapterError) as raised:
        G.execute({**params, "apply": True, "expected": preview["state_digest"]}, object(), A)
    assert events == []
    assert raised.value.details["write_attempted"] is False
    assert "Route working mode" in raised.value.details["cause"]


def test_active_mode_drift_during_selection_stops_before_command(tmp_path, monkeypatch):
    app, params, events = native_general(tmp_path, monkeypatch)
    preview = G.execute(params, object(), A)
    original = app.ActiveDocument.UnSelectAll

    def drift():
        original()
        app.general_configuration["active_mode"] = 0

    app.ActiveDocument.UnSelectAll = drift
    with pytest.raises(A.AdapterError) as raised:
        G.execute({**params, "apply": True, "expected": preview["state_digest"]}, object(), A)
    assert [row[0] for row in events] == ["UnSelectAll", "FromTo.Selected"]
    actual = raised.value.details["native_result"]
    assert actual["engineering_write_dispatched"] is False
    assert actual["write_attempted"] is False
    assert not any(row[0] in ("ProcessCommand", "Save", "Close", "Open") for row in events)


def test_last_foreign_selection_refuses_after_selection_but_before_route(tmp_path, monkeypatch):
    _, params, events = native_general(tmp_path, monkeypatch, fault="foreign_selection")
    preview = G.execute(params, object(), A)
    with pytest.raises(A.AdapterError) as raised:
        G.execute({**params, "apply": True, "expected": preview["state_digest"]}, object(), A)
    assert [r[0] for r in events] == ["UnSelectAll", "FromTo.Selected"]
    assert raised.value.details["native_result"]["write_attempted"] is False


def test_complete_selection_adapter_uses_formal_collections_not_highlight_or_makepy_names(
    monkeypatch,
):
    plan, _ = general_case()
    monkeypatch.setitem(sys.modules, "pythoncom", NS(DISPATCH_PROPERTYGET=2))
    calls = []

    class Collection(list):
        @property
        def Count(self):
            return len(self)

        def Item(self, index):
            return self[index - 1]

    def get(dispid, args):
        calls.append((dispid, args))
        return (
            Collection([NS(UniqueId="n1", Name="GND", Selected=True)])
            if dispid == 23
            else Collection()
        )

    doc = LateBound(get)
    selected = {**copy.deepcopy(plan["selected_fromto"]), "selected": True}
    monkeypatch.setattr(G, "observe_fromtos", lambda *args, **kwargs: [(selected, object(), 0)])
    observed = G.observe_selection(doc, plan["before"], A)
    assert G._selection_allowed(observed, plan)
    assert calls == [
        (23, (1, True, "*")),
        (22, (1, -1, 4, "*")),
        (26, (1,)),
        (27, (1, 0)),
        (28, (1,)),
    ]
    for kind in ("components", "pins", "traces", "vias"):
        altered = copy.deepcopy(observed)
        altered[kind] = [{"uid": "other", "selected": True}]
        assert not G._selection_allowed(altered, plan)


@pytest.mark.parametrize(
    "tamper",
    [
        "missing_consumption",
        "one_open_claim",
        "foreign_protection",
        "missing_selection",
        "fake_complete",
        "missing_post_close",
    ],
)
def test_receipt_recomputes_actual_evidence_not_success_flags(tmp_path, monkeypatch, tamper):
    app, params, _ = native_general(tmp_path, monkeypatch)
    preview = G.execute(params, object(), A)
    result = G.execute({**params, "apply": True, "expected": preview["state_digest"]}, object(), A)
    if tamper == "missing_consumption":
        result.pop("state_after")
    elif tamper == "one_open_claim":
        result["native_calls_returned"][-1]["returned"] = False
    elif tamper == "foreign_protection":
        result["state_after"]["texts"][0]["position"][0] += 1
    elif tamper == "missing_selection":
        result["selected_fromto_evidence"] = []
    elif tamper == "missing_post_close":
        result.pop("post_close_identity")
    else:
        result["general_route"]["request"]["allow_replace_trace_uids"].append("protected-0")
    assert not G.verify_receipt(result, preview["general_route"], app.ActiveDocument.state)["valid"]
