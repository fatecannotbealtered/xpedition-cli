"""NativeOM pure fakes only: no real HWND enumeration, AOAW, COM server or UI."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from designer_identity_fakes import FakeComError, Scene, hard_no_native  # noqa: F401

from xpedition_cli import designer_nativeom as M
from xpedition_cli import native_com_adapter as A
from xpedition_cli import native_identity as N
from xpedition_cli import native_process as P


@pytest.fixture(autouse=True)
def no_default_nativeom(monkeypatch):
    hits = []

    def refuse(*args, **kwargs):
        hits.append(True)
        pytest.fail("NativeOM tests must inject fake enum and official-API endpoints")

    monkeypatch.setattr(M, "_enumerate_windows", refuse)
    monkeypatch.setattr(M, "_accessible_native_dispatch", refuse)
    yield
    assert hits == []


class Raw:
    def __init__(self, owner=None, role=None):
        self.unknown, self.owner, self.role = object(), owner, role

    def QueryInterface(self, iid, use_iid=None):
        if iid == "unknown":
            return self.unknown
        if self.role is not None and iid == "{" + self.role[0] + "}":
            assert use_iid == "unknown"
            return SimpleNamespace(QueryInterface=lambda requested: self.unknown)
        raise FakeComError()

    def GetIDsOfNames(self, name):
        assert name == "Application"
        return self.role[1]

    def InvokeTypes(self, dispid, locale, flags, result_type, args):
        assert (dispid, locale, flags, result_type, args) == (
            self.role[1],
            0,
            self.role[2],
            (9, 0),
            (),
        )
        return self.owner


class NativeOM:
    def __init__(self, tmp_path):
        self.scene = Scene(tmp_path)
        self.com = SimpleNamespace(
            IID_IUnknown="unknown", IID_IDispatch="dispatch", TypeIIDs={"dispatch": Raw}
        )
        self.identity = {
            "domain": "schematic",
            "pid": 40,
            "document": self.scene.document(),
            "public_fields": {"FullName": self.scene.exe},
            "designer_existing_views_observation": {
                "status": "observed",
                "authority_granted": False,
                "observation_complete": True,
                "document_count": 0,
                "total_view_count": 0,
            },
        }
        self.ownership = {
            "status": "verified",
            "domain": "schematic",
            "startup_operation_id": "op-original103",
            "source_sha256": "a" * 64,
            "project": self.scene.path,
            "generations": [
                {
                    "role": "application",
                    "pid": 40,
                    "executable": self.scene.exe,
                    "creation_time_100ns": "123456789",
                }
            ],
        }
        self.generations = {40: deepcopy(self.scene.generation)}
        self.windows = [{"hwnd": 123, "pid": 40, "top_level": True}]
        self.window_states = {123: deepcopy(self.scene.window)}
        self.api_calls, self.enums = [], []
        self.response = {"hresult": 0, "nonnull": True, "object": self.scene.app}

    def generation(self, pid):
        value = self.generations[pid]
        if isinstance(value, Exception):
            raise value
        return deepcopy(value)

    def window(self, hwnd):
        return deepcopy(self.window_states[hwnd])

    def enumerate(self, pids):
        self.enums.append(set(pids))
        return {
            "windows": deepcopy(self.windows),
            "visits": len(self.windows) + 2,
            "complete": True,
        }

    def api(self, hwnd):
        self.api_calls.append(hwnd)
        return self.response

    def run(self, **options):
        settings = dict(
            com_module=self.com,
            generation_reader=self.generation,
            window_reader=self.window,
            window_enumerator=self.enumerate,
            nativeom_reader=self.api,
        )
        settings.update(options)
        return M.observe(self.scene.app, self.identity, self.ownership, **settings)


@pytest.mark.parametrize(
    "ownership",
    [None, {}, {"status": "unavailable", "reason": "original_start_pointer_recovery_required"}],
)
def test_missing_verified_original_start_does_not_even_load_native_dependencies(
    tmp_path, ownership
):
    case = NativeOM(tmp_path)
    result = M.observe(case.scene.app, case.identity, ownership)
    assert result["status"] == "skipped" and result["authority_granted"] is False
    assert result["read_operation_count"] == 0


def test_direct_native_application_relation_preserves_original_inputs(tmp_path):
    case = NativeOM(tmp_path)
    original = deepcopy(case.ownership), deepcopy(case.identity)
    result = case.run()
    assert (
        result["observation_complete"] is True and result["formal_window_relation_observed"] is True
    )
    assert result["windows"][0]["relation"]["via"] == "same_held_application_canonical"
    assert result["authority_granted"] is False
    assert case.ownership == original[0] and case.identity == original[1]
    assert str(tmp_path) not in str(result)


@pytest.mark.parametrize("role", M.APPLICATION_MEMBERS)
def test_only_declared_document_view_application_paths_may_link_model(tmp_path, role):
    case = NativeOM(tmp_path)
    case.response["object"] = Raw(case.scene.app, role)
    result = case.run()
    assert result["formal_window_relation_observed"] is True
    assert result["windows"][0]["relation"]["via"] == role[3]
    assert result["authority_granted"] is False


def test_same_pid_or_title_does_not_make_an_unrelated_dispatch_the_application(tmp_path):
    case = NativeOM(tmp_path)
    case.response["object"] = Raw()
    result = case.run()
    assert result["observation_complete"] is True
    assert result["formal_window_relation_observed"] is False


@pytest.mark.parametrize("change", ["source", "domain", "pid", "birth", "project", "role"])
def test_incomplete_original_start_pin_is_not_replaced_by_basic_pid(tmp_path, change):
    case = NativeOM(tmp_path)
    if change == "source":
        case.ownership["source_sha256"] = "missing"
    elif change == "domain":
        case.identity["domain"] = "pcb"
    elif change == "pid":
        case.ownership["generations"][0]["pid"] = 41
    elif change == "birth":
        case.ownership["generations"][0]["creation_time_100ns"] = ""
    elif change == "project":
        case.ownership["project"] = str(tmp_path / "foreign.prj")
    elif change == "role":
        case.ownership["generations"][0]["role"] = "same_parent"
    result = case.run()
    assert result["formal_window_relation_observed"] is False and case.enums == case.api_calls == []


def test_changed_or_ended_launcher_cannot_supply_a_reused_pid_window(tmp_path):
    case = NativeOM(tmp_path)
    case.ownership["generations"].append(
        {
            "role": "launcher",
            "pid": 41,
            "executable": str(tmp_path / "common/viewdraw.exe"),
            "creation_time_100ns": "123456788",
        }
    )
    case.generations[41] = {
        "pid": 41,
        "alive": True,
        "executable": str(tmp_path / "foreign.exe"),
        "birth_filetime_100ns": "987654321",
    }
    result = case.run()
    assert result["ended_or_changed_launcher_count"] == 1 and case.enums == [{40}]
    assert case.api_calls == [123]


def test_proved_original_launcher_window_needs_the_native_model_relation_too(tmp_path):
    case = NativeOM(tmp_path)
    image = str(tmp_path / "common/viewdraw.exe")
    case.ownership["generations"].append(
        {"role": "launcher", "pid": 41, "executable": image, "creation_time_100ns": "123456788"}
    )
    case.generations[41] = {
        "pid": 41,
        "alive": True,
        "executable": image,
        "birth_filetime_100ns": "123456788",
    }
    case.windows = [{"hwnd": 125, "pid": 41}]
    case.window_states[125] = {"hwnd": 125, "pid": 41, "is_window": True, "thread_id": 56}
    result = case.run()
    assert result["formal_window_relation_observed"] is True
    assert result["windows"][0]["original_role"] == "launcher"
    assert result["authority_granted"] is False


@pytest.mark.parametrize(
    "fault",
    ["birth", "image", "dead", "window_pid", "window_handle", "thread", "foreign_candidate"],
)
def test_each_candidate_needs_fresh_original_generation_and_actual_window(tmp_path, fault):
    case = NativeOM(tmp_path)
    if fault == "birth":
        case.generations[40]["birth_filetime_100ns"] = "987654321"
    elif fault == "image":
        case.generations[40]["executable"] = str(tmp_path / "foreign.exe")
    elif fault == "dead":
        case.generations[40]["alive"] = False
    elif fault == "window_pid":
        case.window_states[123]["pid"] = 99
    elif fault == "window_handle":
        case.window_states[123]["hwnd"] = 456
    elif fault == "thread":
        case.window_states[123]["thread_id"] = 0
    elif fault == "foreign_candidate":
        case.windows[0]["pid"] = 99
    result = case.run()
    assert result["status"] == "incomplete" and result["formal_window_relation_observed"] is False
    assert case.api_calls == []


@pytest.mark.parametrize("hr,nonnull", [(-2147467262, False), (-2147467259, False)])
def test_no_native_object_is_recorded_with_exact_hresult_not_fabricated(tmp_path, hr, nonnull):
    case = NativeOM(tmp_path)
    case.response = {"hresult": hr, "nonnull": nonnull, "object": None}
    result = case.run()
    assert (
        result["observation_complete"] is True
        and result["formal_window_relation_observed"] is False
    )
    assert result["windows"][0]["api_hresult_hex"] == f"0x{hr & 0xFFFFFFFF:08x}"


@pytest.mark.parametrize(
    "hr,nonnull,obj",
    [(0, False, None), (-2147467259, True, None), (0, True, "Viewdraw.Application")],
)
def test_malformed_reference_or_string_cannot_become_an_application(tmp_path, hr, nonnull, obj):
    case = NativeOM(tmp_path)
    case.response = {"hresult": hr, "nonnull": nonnull, "object": obj}
    result = case.run()
    assert result["status"] == "incomplete" and result["formal_window_relation_observed"] is False


@pytest.mark.parametrize("fault", ["birth_after", "project_after", "held_after"])
def test_post_observation_rechecks_refuse_real_changes(tmp_path, fault):
    case = NativeOM(tmp_path)
    held_model = Raw()
    held_model.unknown = case.scene.app_unknown
    case.response["object"] = held_model

    def api(hwnd):
        if fault == "birth_after":
            case.generations[40]["birth_filetime_100ns"] = "987654321"
        elif fault == "project_after":
            case.scene.path = str(tmp_path / "other.prj")
        elif fault == "held_after":
            case.scene.app_unknown = object()
        return case.response

    result = case.run(nativeom_reader=api)
    assert result["status"] == "incomplete" and result["formal_window_relation_observed"] is False


def test_empty_owned_window_snapshot_is_not_native_model_support(tmp_path):
    case = NativeOM(tmp_path)
    case.windows = []
    result = case.run()
    assert result["observation_complete"] is True
    assert result["reason"] == "no_existing_original_owned_windows"
    assert result["formal_window_relation_observed"] is False and case.api_calls == []


@pytest.mark.parametrize("hr,address", [(0, None), (-2147467259, None), (-2147467259, 100)])
def test_aoaw_reference_is_never_adopted_on_failure_or_null(hr, address):
    calls = []
    result = M._bridge_aoaw_result(
        hr, address, lambda value: calls.append("adopt"), lambda owner: calls.append("bridge")
    )
    assert calls == [] and result["object"] is None


@pytest.mark.parametrize("bridge_error", [False, True])
def test_api_reference_and_bridge_qi_reference_have_independent_ownership(bridge_error):
    counts = dict(adopted=0, original_released=0, qi=0)

    class Holder:
        def __init__(self):
            counts["adopted"] += 1

        def __del__(self):
            counts["original_released"] += 1

    def adopt(address):
        assert address == 100
        return Holder()

    def bridge(owner):
        counts["qi"] += 1
        if bridge_error:
            raise FakeComError()
        return object()

    if bridge_error:
        with pytest.raises(FakeComError):
            M._bridge_aoaw_result(0, 100, adopt, bridge)
    else:
        result = M._bridge_aoaw_result(0, 100, adopt, bridge)
        assert result["object"] is not None
    assert counts == {"adopted": 1, "original_released": 1, "qi": 1}


def test_candidate_bound_cannot_be_reported_as_complete_absence(monkeypatch, tmp_path):
    case = NativeOM(tmp_path)
    monkeypatch.setattr(M, "MAX_CANDIDATES", 0)
    result = case.run()
    assert result["candidate_coverage_complete"] is False
    assert result["observation_complete"] is False and case.api_calls == []


def test_read_wall_and_inventory_limits_remain_explicit_incomplete(monkeypatch, tmp_path):
    case = NativeOM(tmp_path)
    result = case.run(
        window_enumerator=lambda pids: {
            "windows": [],
            "visits": 4097,
            "complete": False,
            "reason": "nativeom_window_visit_limit",
        }
    )
    assert result["reason"] == "nativeom_window_visit_limit" and case.api_calls == []
    monkeypatch.setattr(M, "MAX_READ_OPERATIONS", 0)
    result = case.run()
    assert result["reason"] == "nativeom_observer_read_limit"
    monkeypatch.setattr(M, "MAX_READ_OPERATIONS", 1024)
    ticks = iter((0, 31))
    result = case.run(clock=lambda: next(ticks))
    assert result["reason"] == "nativeom_observer_wall_limit"
    assert result["limits"]["AOAW_has_individual_timeout"] is False


def test_a_failing_native_api_does_not_print_native_description(tmp_path):
    case = NativeOM(tmp_path)

    def error(hwnd):
        raise ValueError("private/engineering/path secret token")

    result = case.run(nativeom_reader=error)
    assert result["status"] == "incomplete" and "secret token" not in str(result)


def test_native_identity_handler_passes_only_explicit_private_owner_header(monkeypatch, tmp_path):
    case = NativeOM(tmp_path)
    seen = []
    fake_com = SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None)
    monkeypatch.setattr(A, "_import_com", lambda: (fake_com, SimpleNamespace()))
    monkeypatch.setattr(A, "_heal_gen_py_cache", lambda: None)
    monkeypatch.setattr(A, "_viewdraw_active", lambda client: case.scene.app)
    monkeypatch.setattr(A, "adapter_identity", lambda: {"build_sha256": "synthetic"})
    monkeypatch.setenv("XPEDITION_EXPECTED_ADAPTER_BUILD", "synthetic")

    def identity(app, domain, **kwargs):
        seen.append(kwargs)
        return {"domain": domain, "pid": 40, "document": case.scene.document()}

    monkeypatch.setattr(A, "application_identity", identity)
    A.dispatch("identity", {"domain": "schematic", "_nativeom_ownership": case.ownership})
    assert seen == [{"nativeom_ownership": case.ownership}]


def test_positive_nativeom_remains_non_authorizing_through_identity_integration(
    monkeypatch, tmp_path
):
    from test_designer_existing_views import Graph

    from xpedition_cli import native_release_preflight as R

    graph = Graph(tmp_path, 0)
    case = NativeOM(tmp_path)
    graph.scene.install_dependencies(monkeypatch)
    graph.app.GetFramework = lambda: None
    monkeypatch.setattr(P, "unique_executable_pid", lambda domain, image: graph.scene.rows()[0])
    monkeypatch.setattr(M, "_enumerate_windows", case.enumerate)
    monkeypatch.setattr(
        M,
        "_accessible_native_dispatch",
        lambda hwnd: {"hresult": 0, "nonnull": True, "object": graph.app},
    )
    identity = N.application_identity(graph.app, "schematic", nativeom_ownership=case.ownership)
    assert identity["designer_nativeom_observation"]["formal_window_relation_observed"] is True
    assert identity["designer_nativeom_observation"]["authority_granted"] is False
    assert identity["pid_source"]["property"] == "FullName"
    with pytest.raises(Exception) as failure:
        R._application_pin(identity, "schematic", graph.scene.rows())
    assert failure.value.code == "E_HUMAN_REQUIRED"


def test_census_upgrades_bounded_storage_without_duplicate_inflation():
    census = M.WindowCensus({40}, clock=lambda: 0)
    for hwnd in range(1, 258):
        assert census.visit(hwnd, 40, hwnd == 1)
    assert census.visit(2, 40, False)
    assert census.visit(9999, 99, True)
    result = census.result(True)
    assert result["complete"] is True and len(result["windows"]) == 257
    facts = result["census"]
    assert facts["distinct_owned_observations_lower_bound"] == 257
    assert facts["duplicate_owned_visits"] == 1
    assert facts["tier_upgrades"] == 1 and facts["active_owned_window_tier"] == 1024
    assert facts["by_pid"] == [{"pid": 40, "captured": 257, "top_level": 1, "descendant": 256}]
    assert facts["atomic_live_window_count"] is False


def test_final_tier_limit_preserves_partial_census_and_zero_api_counts(monkeypatch, tmp_path):
    monkeypatch.setattr(M, "BASE_OWN_WINDOW_TIER", 2)
    monkeypatch.setattr(M, "MAX_OWN_WINDOWS", 4)
    census = M.WindowCensus({40}, clock=lambda: 0)
    for hwnd in range(1, 5):
        assert census.visit(hwnd, 40, hwnd == 1)
    assert not census.visit(5, 40, False)
    inventory = census.result(True)
    case = NativeOM(tmp_path)
    result = case.run(window_enumerator=lambda pids: inventory)
    assert result["reason"] == "nativeom_owned_window_limit"
    assert result["inventory_complete"] is False
    assert result["captured_owned_window_count"] == 4
    assert result["partial_census"]["distinct_owned_observations_lower_bound"] == 5
    assert result["nativeom_reader_attempt_count"] == result["aoaw_call_count"] == 0
    assert result["candidate_coverage_complete"] is False and case.api_calls == []


def test_serialized_byte_and_inner_wall_limits_preserve_observed_lower_bounds(monkeypatch):
    monkeypatch.setattr(M, "MAX_CENSUS_SERIALIZED_BYTES", 2)
    census = M.WindowCensus({40}, clock=lambda: 0)
    assert not census.visit(1, 40, True)
    value = census.result(True)
    assert value["reason"] == "nativeom_window_capture_byte_limit"
    assert value["census"]["distinct_owned_observations_lower_bound"] == 1
    assert value["census"]["captured_unique_owned"] == 0
    ticks = iter((0, 31))
    census = M.WindowCensus({40}, clock=lambda: next(ticks))
    assert not census.visit(1, 40, True)
    assert census.result(True)["reason"] == "nativeom_window_inventory_wall_limit"


def test_returned_partial_census_survives_outer_post_read_wall_limit(tmp_path):
    case = NativeOM(tmp_path)
    clock = {"now": 0}
    census = M.WindowCensus({40}, clock=lambda: 0)
    for hwnd in range(1, 258):
        assert census.visit(hwnd, 40, hwnd == 1)
    inventory = census.result(True)
    inventory["complete"], inventory["reason"] = False, "nativeom_window_inventory_wall_limit"

    def enumerate(pids):
        clock["now"] = 31
        return inventory

    result = case.run(window_enumerator=enumerate, clock=lambda: clock["now"])
    assert result["reason"] == "nativeom_observer_wall_limit"
    assert result["captured_owned_window_count"] == 257
    assert result["partial_census"]["duplicate_owned_visits"] == 0
    assert result["aoaw_call_count"] == result["nativeom_reader_attempt_count"] == 0
    assert result["inventory_complete"] is False and result["observation_complete"] is False


def test_query_prioritizes_topology_but_preserves_full_inventory_and_unqueried_count(
    monkeypatch, tmp_path
):
    case = NativeOM(tmp_path)
    case.windows = [
        {"hwnd": 124, "pid": 40, "top_level": False},
        {"hwnd": 123, "pid": 40, "top_level": True},
    ]
    case.window_states[124] = {"hwnd": 124, "pid": 40, "thread_id": 56, "is_window": True}
    monkeypatch.setattr(M, "MAX_CANDIDATES", 1)
    result = case.run()
    assert result["captured_owned_window_count"] == 2 and case.api_calls == [123]
    assert result["nativeom_reader_attempt_count"] == result["nativeom_reader_return_count"] == 1
    assert result["aoaw_call_count"] is None
    assert result["unqueried_captured_window_count"] == 1
    assert result["inventory_complete"] is True and result["candidate_coverage_complete"] is False
    assert result["authority_granted"] is False


def test_unreturned_reader_does_not_invent_exact_physical_api_count(tmp_path):
    case = NativeOM(tmp_path)

    def fail(hwnd):
        raise ValueError("private native failure")

    result = case.run(nativeom_reader=fail)
    assert result["nativeom_reader_attempt_count"] == 1
    assert result["nativeom_reader_return_count"] == 0
    assert result["aoaw_call_count"] is None
    assert result["known_aoaw_completed_lower_bound"] == 0


def test_foreign_root_owned_descendants_and_duplicate_callbacks_are_fully_accounted():
    child_queries = []
    pids = {1: 99, 2: 40, 3: 40, 4: 40}

    def roots(callback):
        return callback(1) and callback(4)

    def children(root, callback):
        child_queries.append(root)
        if root == 1:
            # EnumChildWindows itself supplies child AND grandchild.
            assert callback(2) and callback(3)
        elif root == 4:
            assert callback(2)  # Repeated/reparented observation is not a second unique HWND.

    inventory = M._walk_inventory({40}, roots, children, lambda hwnd: pids[hwnd], clock=lambda: 0)
    assert child_queries == [1, 4]  # Never recursively re-enumerate 2 or 3.
    assert inventory["complete"] is True
    assert {row["hwnd"] for row in inventory["windows"]} == {2, 3, 4}
    assert inventory["census"]["duplicate_owned_visits"] == 1
    assert inventory["census"]["distinct_owned_observations_lower_bound"] == 3


def many_windows(case, count):
    case.windows = [
        {"hwnd": 1000 + index, "pid": 40, "top_level": index < 218} for index in range(count)
    ]
    case.window_states = {
        row["hwnd"]: {"hwnd": row["hwnd"], "pid": 40, "thread_id": 55, "is_window": True}
        for row in case.windows
    }


def test_measured_935_inventory_can_be_exhausted_with_derived_read_and_byte_budget(tmp_path):
    case = NativeOM(tmp_path)
    image = str(tmp_path / "common/viewdraw.exe")
    case.ownership["generations"].append(
        {"role": "launcher", "pid": 41, "executable": image, "creation_time_100ns": "123456788"}
    )
    case.generations[41] = {
        "pid": 41,
        "alive": True,
        "executable": image,
        "birth_filetime_100ns": "123456788",
    }
    many_windows(case, 935)
    case.response = {"hresult": -2147467259, "nonnull": False, "object": None}
    result = case.run(clock=lambda: 0)
    assert result["candidate_coverage_complete"] is True
    assert result["nativeom_reader_return_count"] == 935
    assert result["unqueried_captured_window_count"] == 0
    assert result["read_operation_count"] == 9 + 935 * 5
    assert result["active_query_tier"] == 1024 and result["query_tier_upgrades"] == 3
    assert result["serialized_query_rows_bytes"] <= M.MAX_QUERY_CAPTURE_BYTES
    assert (
        result["formal_window_relation_observed"] is False and result["authority_granted"] is False
    )


def test_early_complete_witness_is_not_full_candidate_coverage(tmp_path):
    case = NativeOM(tmp_path)
    many_windows(case, 935)
    result = case.run(clock=lambda: 0)
    assert case.api_calls == [1000]
    assert result["strong_witness_complete"] is True
    assert result["early_stop_for_witness"] is True and result["status"] == "observed_witness"
    assert (
        result["candidate_coverage_complete"] is False and result["observation_complete"] is False
    )
    assert result["unqueried_captured_window_count"] == 934
    assert result["authority_granted"] is False


def test_query_rows_are_bounded_without_silently_dropping_a_complete_claim(monkeypatch, tmp_path):
    case = NativeOM(tmp_path)
    monkeypatch.setattr(M, "MAX_QUERY_ROW_BYTES", 1)
    result = case.run(clock=lambda: 0)
    assert result["reason"] == "nativeom_query_row_byte_limit"
    assert result["omitted_query_row_count"] == 1 and result["windows"] == []
    assert result["strong_witness_complete"] is False and result["observation_complete"] is False


def test_read_budget_is_derived_from_full_capture_and_three_declared_paths():
    assert M.MAX_CANDIDATES == M.MAX_OWN_WINDOWS == 1024
    assert M.MAX_READ_OPERATIONS == 9 + 1024 * (5 + 10)
    assert M.MAX_QUERY_CAPTURE_BYTES == 2 + 1024 * (M.MAX_QUERY_ROW_BYTES + 1)
