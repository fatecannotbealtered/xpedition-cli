"""Last bounded, non-authorizing NativeOM observation of original owned windows.

Uses AccessibleObjectFromWindow only. No WM_GETOBJECT/ObjectFromLresult, title
match, activation, new application, new view or writer-admission fallback.
"""

from __future__ import annotations

import json
import re
import time

from . import designer_window_identity as W

MAX_WINDOW_VISITS = 4096
BASE_OWN_WINDOW_TIER = 256
MAX_OWN_WINDOWS = 1024
MAX_CENSUS_SERIALIZED_BYTES = 256 * 1024
MAX_CANDIDATES = MAX_OWN_WINDOWS
QUERY_TIERS = (32, 128, 512, MAX_CANDIDATES)
MAX_READ_OPERATIONS = 9 + MAX_CANDIDATES * (5 + 10)
MAX_QUERY_ROW_BYTES = 2048
MAX_QUERY_CAPTURE_BYTES = 2 + MAX_CANDIDATES * (MAX_QUERY_ROW_BYTES + 1)
MAX_WALL_SECONDS = 30.0
APPLICATION_MEMBERS = (
    ("4adef4e2-690a-11ce-9261-0020c5e26659", 1, 2, "declared_document_application"),
    ("d8b95af2-d943-4295-8d86-71b4f904c3aa", 101, 2, "declared_sheet_document_application"),
    ("0892a013-86bc-11ce-8238-00001b4d36b5", 9, 1, "declared_view_application"),
)


class ObservationGap(ValueError):
    """Fixed internal reason; raw native descriptions are never returned."""


class Budget:
    def __init__(self, clock):
        self.clock, self.started, self.calls = clock, clock(), 0
        self.point = "nativeom.ownership"

    def read(self, point, operation):
        self.point = point
        if self.clock() - self.started > MAX_WALL_SECONDS:
            raise ObservationGap("nativeom_observer_wall_limit")
        if self.calls >= MAX_READ_OPERATIONS:
            raise ObservationGap("nativeom_observer_read_limit")
        self.calls += 1
        value = operation()
        if self.clock() - self.started > MAX_WALL_SECONDS:
            raise ObservationGap("nativeom_observer_wall_limit")
        return value


def _same(left, right, reason):
    value = left == right
    if type(value) is not bool or value is not True:
        raise ObservationGap(reason)


def _raw(value, com):
    raw_type = getattr(com, "TypeIIDs", {}).get(getattr(com, "IID_IDispatch", None))
    if raw_type is not None and type(value) is raw_type:
        return value
    if value is None or isinstance(value, (str, bytes, int, float, bool)):
        raise ObservationGap("nativeom_returned_dispatch_unobserved")
    raw = getattr(value, "_oleobj_", None)
    if raw is None:
        raise ObservationGap("nativeom_returned_dispatch_unobserved")
    return raw


def _pin(ownership, identity):
    if not isinstance(ownership, dict) or ownership.get("status") != "verified":
        reason = ownership.get("reason") if isinstance(ownership, dict) else None
        return None, reason if isinstance(reason, str) and re.fullmatch(
            r"[a-z0-9_]{1,100}", reason
        ) else "original_start_ownership_missing"
    if (
        ownership.get("domain") != "schematic"
        or identity.get("domain") != "schematic"
        or not re.fullmatch(r"op-[a-zA-Z0-9_-]{1,80}", str(ownership.get("startup_operation_id")))
        or not re.fullmatch(r"[0-9a-f]{64}", str(ownership.get("source_sha256")))
    ):
        return None, "original_start_ownership_header_invalid"
    rows = ownership.get("generations")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 2:
        return None, "original_start_generation_set_invalid"
    if any(not isinstance(row, dict) for row in rows):
        return None, "original_start_generation_shape_invalid"
    if len({row.get("pid") for row in rows if type(row.get("pid")) is int}) != len(rows):
        return None, "original_start_generation_pid_invalid_or_duplicate"
    roles = [row.get("role") for row in rows]
    if roles.count("application") != 1 or roles.count("launcher") != len(rows) - 1:
        return None, "original_start_generation_role_invalid"
    for row in rows:
        birth = row.get("creation_time_100ns")
        if (
            type(row.get("pid")) is not int
            or row["pid"] <= 0
            or not isinstance(birth, str)
            or not birth.isascii()
            or not birth.isdecimal()
            or int(birth) <= 0
        ):
            return None, "original_start_generation_incomplete"
        W._path(row.get("executable"))
    application = next(row for row in rows if row["role"] == "application")
    if (
        type(identity.get("pid")) is not int
        or identity.get("pid") != application["pid"]
        or W._path(identity["public_fields"].get("FullName")) != W._path(application["executable"])
    ):
        return None, "original_start_application_context_mismatch"
    if identity.get("document", {}).get("open") is not True or W._path(
        identity["document"].get("path")
    ) != W._path(ownership.get("project")):
        return None, "original_start_project_context_mismatch"
    return rows, None


def _matches(row, actual):
    return (
        isinstance(actual, dict)
        and type(actual.get("pid")) is int
        and actual.get("pid") == row["pid"]
        and actual.get("alive") is True
        and actual.get("birth_filetime_100ns") == row["creation_time_100ns"]
        and W._path(actual.get("executable")) == W._path(row["executable"])
    )


class WindowCensus:
    """One capture: distinct HWND observations, not an atomic live-window count."""

    def __init__(self, owned_pids, clock=time.monotonic):
        self.owned_pids, self.clock, self.started = owned_pids, clock, clock()
        self.windows, self.seen = [], set()
        self.visits = self.unique_owned = self.duplicates = 0
        self.serialized_bytes, self.tier = 2, min(BASE_OWN_WINDOW_TIER, MAX_OWN_WINDOWS)
        self.upgrades, self.stopped = 0, None

    def check(self):
        if self.stopped is None and self.clock() - self.started > MAX_WALL_SECONDS:
            self.stopped = "nativeom_window_inventory_wall_limit"
        return self.stopped is None

    def visit(self, hwnd, pid, top_level):
        if not self.check():
            return False
        self.visits += 1
        if self.visits > MAX_WINDOW_VISITS:
            self.stopped = "nativeom_window_visit_limit"
            return False
        if pid not in self.owned_pids:
            return True
        if hwnd in self.seen:
            self.duplicates += 1
            return True
        self.seen.add(hwnd)
        self.unique_owned += 1
        if len(self.windows) >= self.tier and self.tier < MAX_OWN_WINDOWS:
            self.tier, self.upgrades = MAX_OWN_WINDOWS, self.upgrades + 1
        if len(self.windows) >= self.tier:
            self.stopped = "nativeom_owned_window_limit"
            return False
        row = {"hwnd": hwnd, "pid": pid, "top_level": top_level}
        size = len(json.dumps(row, separators=(",", ":")).encode("utf-8"))
        extra = size + (1 if self.windows else 0)
        if self.serialized_bytes + extra > MAX_CENSUS_SERIALIZED_BYTES:
            self.stopped = "nativeom_window_capture_byte_limit"
            return False
        self.windows.append(row)
        self.serialized_bytes += extra
        return True

    def result(self, success):
        by_pid = []
        for pid in sorted(self.owned_pids):
            rows = [row for row in self.windows if row["pid"] == pid]
            by_pid.append(
                {
                    "pid": pid,
                    "captured": len(rows),
                    "top_level": sum(row["top_level"] is True for row in rows),
                    "descendant": sum(row["top_level"] is False for row in rows),
                }
            )
        return {
            "windows": self.windows,
            "visits": self.visits,
            "complete": success and self.stopped is None,
            "reason": self.stopped or (None if success else "nativeom_window_enumeration_failed"),
            "census": {
                "captured_unique_owned": len(self.windows),
                "distinct_owned_observations_lower_bound": self.unique_owned,
                "duplicate_owned_visits": self.duplicates,
                "serialized_capture_bytes": self.serialized_bytes,
                "active_owned_window_tier": self.tier,
                "tier_upgrades": self.upgrades,
                "atomic_live_window_count": False,
                "by_pid": by_pid,
            },
        }


def _walk_inventory(
    owned_pids, enumerate_roots, enumerate_children, pid_reader, clock=time.monotonic
):
    """Only top-level results enter the child-enumeration queue, even for foreign PIDs."""
    roots, census = [], WindowCensus(owned_pids, clock)

    def visit(hwnd, top_level):
        if not census.check():
            return False
        return census.visit(int(hwnd), pid_reader(hwnd), top_level)

    def root_callback(hwnd):
        roots.append(int(hwnd))
        return visit(hwnd, True)

    def child_callback(hwnd):
        return visit(hwnd, False)

    success = bool(enumerate_roots(root_callback))
    if success and census.stopped is None:
        for root in roots:
            if not census.check():
                break
            enumerate_children(root, child_callback)
            if census.stopped is not None:
                break
    return census.result(success)


def _enumerate_windows(owned_pids):
    """Only PID metadata is read for other windows; never text, title or class."""
    import ctypes
    from ctypes import wintypes as w

    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    user.EnumWindows.argtypes, user.EnumWindows.restype = (callback_type, w.LPARAM), w.BOOL
    user.EnumChildWindows.argtypes, user.EnumChildWindows.restype = (
        (w.HWND, callback_type, w.LPARAM),
        w.BOOL,
    )
    user.GetWindowThreadProcessId.argtypes = (w.HWND, ctypes.POINTER(w.DWORD))
    user.GetWindowThreadProcessId.restype = w.DWORD

    def pid_reader(hwnd):
        pid = w.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)

    def enumerate_roots(callback):
        @callback_type
        def native(hwnd, _):
            return callback(int(hwnd))

        return bool(user.EnumWindows(native, 0))

    def enumerate_children(root, callback):
        @callback_type
        def native(hwnd, _):
            return callback(int(hwnd))

        user.EnumChildWindows(root, native, 0)  # This API's return value is not defined.

    return _walk_inventory(owned_pids, enumerate_roots, enumerate_children, pid_reader)


def _bridge_aoaw_result(hr, address, adopt, bridge):
    """Two independent refs: one adopted AOAW ref, one obtained by bridge QI."""
    if type(hr) is not int or address is not None and (type(address) is not int or address < 0):
        raise ObservationGap("nativeom_api_pointer_slot_shape_invalid")
    nonnull = bool(address)
    if hr != 0 or not nonnull:
        return {"hresult": hr, "nonnull": nonnull, "object": None}
    owner = adopt(address)
    try:
        result = bridge(owner)
    finally:
        del owner
    return {"hresult": hr, "nonnull": True, "object": result}


def _accessible_native_dispatch(hwnd):
    """Adopt the successful AOAW reference, and keep pywin32's QI ref separate."""
    import ctypes
    from ctypes import wintypes as w

    from comtypes import GUID
    from comtypes.automation import IDispatch

    com = W._load_com()
    oleacc = ctypes.WinDLL("oleacc", use_last_error=True)
    call = oleacc.AccessibleObjectFromWindow
    call.argtypes = (w.HWND, w.DWORD, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
    call.restype = ctypes.c_long
    requested = GUID(str(com.IID_IDispatch))
    slot = ctypes.c_void_p()
    hr = int(call(hwnd, 0xFFFFFFF0, ctypes.byref(requested), ctypes.byref(slot)))  # OBJID_NATIVEOM
    return _bridge_aoaw_result(
        hr,
        slot.value,
        lambda address: ctypes.cast(ctypes.c_void_p(address), ctypes.POINTER(IDispatch)),
        lambda owner: com.ObjectFromAddress(
            ctypes.cast(owner, ctypes.c_void_p).value, com.IID_IDispatch
        ),
    )


def _relation(model, held, com, budget):
    raw = _raw(model, com)
    unknown = budget.read("nativeom.model.IUnknown", lambda: raw.QueryInterface(com.IID_IUnknown))
    equal = unknown == held
    if type(equal) is not bool:
        raise ObservationGap("nativeom_canonical_comparison_unobserved")
    if type(equal) is bool and equal:
        return {"matched": True, "via": "same_held_application_canonical"}
    supported = []
    for iid, dispid, flags, label in APPLICATION_MEMBERS:
        try:
            interface = budget.read(
                "nativeom.declared_interface.QI",
                lambda iid=iid: raw.QueryInterface("{" + iid + "}", com.IID_IUnknown),
            )
        except Exception as exc:
            if getattr(exc, "hresult", None) == -2147467262:
                continue
            raise
        _same(
            budget.read(
                "nativeom.declared_interface.IUnknown",
                lambda interface=interface: interface.QueryInterface(com.IID_IUnknown),
            ),
            unknown,
            "nativeom_declared_interface_identity_mismatch",
        )
        supported.append((iid, dispid, flags, label))
    if not supported:
        return {"matched": False, "reason": "native_dispatch_has_no_declared_application_path"}
    actual = budget.read(
        "nativeom.Application.GetIDsOfNames", lambda: raw.GetIDsOfNames("Application")
    )
    matches = [row for row in supported if type(actual) is int and row[1] == actual]
    if len(matches) != 1:
        return {
            "matched": False,
            "reason": "nativeom_declared_application_dispid_unqualified",
            "observed_dispid": actual if type(actual) is int else None,
        }
    iid, dispid, flags, label = matches[0]
    app = budget.read(
        "nativeom.Application.InvokeTypes", lambda: raw.InvokeTypes(dispid, 0, flags, (9, 0), ())
    )
    app_raw = _raw(app, com)
    app_unknown = budget.read(
        "nativeom.Application.IUnknown", lambda: app_raw.QueryInterface(com.IID_IUnknown)
    )
    equal = app_unknown == held
    return {
        "matched": type(equal) is bool and equal is True,
        "via": label,
        "declared_interface_iid": iid,
        "application_dispid": dispid,
    }


def _retain_census(result, inventory, live):
    """Keep only safe partial facts, including when the outer soft wall expires."""
    if not isinstance(inventory, dict):
        return
    windows = inventory.get("windows")
    captured = len(windows) if isinstance(windows, list) else None
    result["captured_owned_window_count"] = captured
    result["unqueried_captured_window_count"] = captured
    result["inventory_reported_complete"] = inventory.get("complete") is True
    visits = inventory.get("visits")
    result["window_visits"] = visits if type(visits) is int and visits >= 0 else None
    original = inventory.get("census")
    partial = {"captured_window_rows": captured, "atomic_live_window_count": False}
    if isinstance(original, dict):
        for key in (
            "captured_unique_owned",
            "distinct_owned_observations_lower_bound",
            "duplicate_owned_visits",
            "serialized_capture_bytes",
            "active_owned_window_tier",
            "tier_upgrades",
        ):
            value = original.get(key)
            partial[key] = value if type(value) is int and value >= 0 else None
        rows = original.get("by_pid")
        if isinstance(rows, list) and len(rows) <= len(live):
            partial["by_original_generation"] = [
                {
                    "pid": row["pid"],
                    "role": live[row["pid"]]["role"],
                    **{
                        key: row.get(key) if type(row.get(key)) is int and row[key] >= 0 else None
                        for key in ("captured", "top_level", "descendant")
                    },
                }
                for row in rows
                if isinstance(row, dict) and type(row.get("pid")) is int and row["pid"] in live
            ]
    result["partial_census"] = partial


def observe(
    app,
    identity,
    ownership,
    *,
    com_module=None,
    generation_reader=None,
    window_reader=None,
    window_enumerator=None,
    nativeom_reader=None,
    clock=time.monotonic,
):
    result = {
        "kind": "original_owned_designer_nativeom_observation_v1",
        "read_only": True,
        "authority_granted": False,
        "status": "skipped",
        "observation_complete": False,
        "candidate_coverage_complete": False,
        "formal_window_relation_observed": False,
        "windows": [],
        "inventory_complete": False,
        "nativeom_reader_attempt_count": 0,
        "nativeom_reader_return_count": 0,
        "aoaw_call_count": 0,
        "known_aoaw_completed_lower_bound": 0,
        "relationship_check_started_count": 0,
        "strong_witness_complete": False,
        "early_stop_for_witness": False,
        "serialized_query_rows_bytes": 2,
        "omitted_query_row_count": 0,
        "call_point": "nativeom.ownership",
        "read_operation_count": 0,
        "limits": {
            "window_visits": MAX_WINDOW_VISITS,
            "owned_windows": MAX_OWN_WINDOWS,
            "owned_window_tiers": [BASE_OWN_WINDOW_TIER, MAX_OWN_WINDOWS],
            "serialized_census_bytes": MAX_CENSUS_SERIALIZED_BYTES,
            "candidates": MAX_CANDIDATES,
            "query_tiers": list(QUERY_TIERS),
            "read_budget_formula": "9+1024*(5+10) guarded read operations",
            "query_compact_row_bytes": MAX_QUERY_ROW_BYTES,
            "query_compact_rows_capture_bytes": MAX_QUERY_CAPTURE_BYTES,
            "query_bytes_unit": "compact windows-list rows; not the outer pretty JSON envelope",
            "read_operations": MAX_READ_OPERATIONS,
            "wall_seconds_checked_between_calls": MAX_WALL_SECONDS,
            "AOAW_has_individual_timeout": False,
            "blocking_read_hard_budget": "existing_native_adapter_process_request",
            "outer_hard_budget_basis": (
                "caller-requested timeout reduced by remaining operation/stage budget"
            ),
            "count_unit": "guarded_read_operation_not_each_internal_Win32_call",
        },
    }
    budget = Budget(clock)
    pending_row = None
    captured_query_bytes, captured_query_rows = 2, 0

    def capture_pending():
        nonlocal pending_row, captured_query_bytes, captured_query_rows
        if pending_row is None:
            return
        size = len(json.dumps(pending_row, separators=(",", ":")).encode("utf-8"))
        projected = captured_query_bytes + size + (1 if captured_query_rows else 0)
        if size > MAX_QUERY_ROW_BYTES or projected > MAX_QUERY_CAPTURE_BYTES:
            result["windows"].pop()
            result["omitted_query_row_count"] += 1
            result["omitted_last_query_row_bytes"] = size
            pending_row = None
            raise ObservationGap("nativeom_query_row_byte_limit")
        captured_query_bytes, captured_query_rows = projected, captured_query_rows + 1
        result["serialized_query_rows_bytes"] = projected
        pending_row = None

    try:
        rows, reason = _pin(ownership, identity)
        if rows is None:
            result["reason"] = reason
            return result
        result["status"] = "incomplete"
        graph = identity.get("designer_existing_views_observation") or {}
        if (
            graph.get("observation_complete") is not True
            or graph.get("status") != "observed"
            or graph.get("authority_granted") is not False
            or type(graph.get("document_count")) is not int
            or type(graph.get("total_view_count")) is not int
            or graph.get("document_count") != 0
            or graph.get("total_view_count") != 0
        ):
            raise ObservationGap("nativeom_empty_existing_graph_prerequisite_unobserved")
        com = com_module if com_module is not None else W._load_com()
        generation_reader, window_reader = (
            generation_reader or W._generation,
            window_reader or W._window,
        )
        window_enumerator = window_enumerator or _enumerate_windows
        official_reader = nativeom_reader is None
        nativeom_reader = nativeom_reader or _accessible_native_dispatch
        live = {}
        skipped = 0
        for row in rows:
            try:
                actual = budget.read(
                    "nativeom.owned_generation.before",
                    lambda row=row: generation_reader(row["pid"]),
                )
            except OSError as exc:
                if row["role"] == "launcher" and (
                    getattr(exc, "winerror", None) == 87 or getattr(exc, "errno", None) == 87
                ):
                    skipped += 1
                    continue
                raise
            if not _matches(row, actual):
                if row["role"] == "launcher":
                    skipped += 1
                    continue
                raise ObservationGap("original_application_generation_changed")
            live[row["pid"]] = row
        result["live_original_generation_count"], result["ended_or_changed_launcher_count"] = (
            len(live),
            skipped,
        )
        project = W.document_binding(identity["document"])
        if (
            W.document_binding(
                budget.read("nativeom.project.before", lambda: W._read_document(app))
            )
            != project
        ):
            raise ObservationGap("nativeom_native_project_changed")
        held = budget.read(
            "nativeom.held_application.IUnknown",
            lambda: app._oleobj_.QueryInterface(com.IID_IUnknown),
        )

        def capture():
            inventory = window_enumerator(set(live))
            _retain_census(result, inventory, live)
            return inventory

        inventory = budget.read("nativeom.enumerate_existing_windows", capture)
        if inventory.get("complete") is not True:
            raise ObservationGap(
                inventory.get("reason")
                if inventory.get("reason")
                in {
                    "nativeom_window_visit_limit",
                    "nativeom_owned_window_limit",
                    "nativeom_window_capture_byte_limit",
                    "nativeom_window_inventory_wall_limit",
                    "nativeom_window_enumeration_failed",
                }
                else "nativeom_window_inventory_incomplete"
            )
        windows = inventory.get("windows")
        if (
            not isinstance(windows, list)
            or len(windows) > MAX_OWN_WINDOWS
            or type(inventory.get("visits")) is not int
            or not 0 <= inventory["visits"] <= MAX_WINDOW_VISITS
        ):
            raise ObservationGap("nativeom_window_inventory_shape_or_bound")
        if any(
            not isinstance(candidate, dict)
            or type(candidate.get("hwnd")) is not int
            or candidate["hwnd"] <= 0
            or type(candidate.get("pid")) is not int
            or candidate["pid"] not in live
            for candidate in windows
        ):
            raise ObservationGap("nativeom_candidate_not_original_owned_window")
        if len({candidate["hwnd"] for candidate in windows}) != len(windows):
            raise ObservationGap("nativeom_window_inventory_duplicate_handle")
        result["inventory_complete"] = True
        windows = sorted(windows, key=lambda candidate: candidate.get("top_level") is not True)
        query_tier = min(QUERY_TIERS[0], MAX_CANDIDATES)
        result["active_query_tier"], result["query_tier_upgrades"] = query_tier, 0

        def read_model(hwnd):
            result["nativeom_reader_attempt_count"] += 1
            result["unqueried_captured_window_count"] = (
                len(windows) - result["nativeom_reader_attempt_count"]
            )
            result["aoaw_call_count"] = None
            response = nativeom_reader(hwnd)
            result["nativeom_reader_return_count"] += 1
            if official_reader:
                result["aoaw_call_count"] = result["nativeom_reader_return_count"]
                result["known_aoaw_completed_lower_bound"] = result["nativeom_reader_return_count"]
            return response

        for ordinal, candidate in enumerate(windows[:MAX_CANDIDATES]):
            if ordinal >= query_tier:
                query_tier = next(tier for tier in QUERY_TIERS if tier > query_tier)
                result["active_query_tier"] = query_tier
                result["query_tier_upgrades"] += 1
            hwnd, pid = candidate.get("hwnd"), candidate.get("pid")
            if type(hwnd) is not int or hwnd <= 0 or pid not in live:
                raise ObservationGap("nativeom_candidate_not_original_owned_window")
            row = live[pid]
            entry = {"hwnd": hwnd, "pid": pid, "original_role": row["role"], "relation": None}
            result["windows"].append(entry)
            pending_row = entry
            actual = budget.read(
                "nativeom.candidate.generation.before", lambda pid=pid: generation_reader(pid)
            )
            if not _matches(row, actual):
                raise ObservationGap("nativeom_candidate_generation_changed")
            facts, _ = W._generation_observation(actual, pid, row["executable"])
            facts["original_creation_time_matches"] = (
                actual["birth_filetime_100ns"] == row["creation_time_100ns"]
            )
            entry["owned_generation_observation"] = facts
            before = budget.read(
                "nativeom.candidate.window.before", lambda hwnd=hwnd: window_reader(hwnd)
            )
            entry["observed_window"] = W._window_facts(before)
            if (
                W._window_facts(before) != before
                or before.get("hwnd") != hwnd
                or before.get("is_window") is not True
                or before.get("pid") != pid
                or before.get("thread_id", 0) <= 0
            ):
                raise ObservationGap("nativeom_candidate_window_changed")
            response = budget.read(
                "nativeom.AccessibleObjectFromWindow", lambda hwnd=hwnd: read_model(hwnd)
            )
            hr, nonnull = response.get("hresult"), response.get("nonnull")
            if type(hr) is not int or type(nonnull) is not bool:
                raise ObservationGap("nativeom_api_response_unobserved")
            entry.update(api_hresult_hex=f"0x{hr & 0xFFFFFFFF:08x}", api_nonnull=nonnull)
            if hr != 0 or not nonnull:
                if nonnull or hr == 0 or response.get("object") is not None:
                    raise ObservationGap("nativeom_api_reference_ownership_unproved")
                entry["relation"] = {"matched": False, "reason": "api_returned_no_native_object"}
            else:
                result["relationship_check_started_count"] += 1
                entry["relation"] = _relation(response.get("object"), held, com, budget)
            if (
                budget.read(
                    "nativeom.candidate.window.recheck", lambda hwnd=hwnd: window_reader(hwnd)
                )
                != before
            ):
                raise ObservationGap("nativeom_candidate_window_changed")
            if not _matches(
                row,
                budget.read(
                    "nativeom.candidate.generation.recheck", lambda pid=pid: generation_reader(pid)
                ),
            ):
                raise ObservationGap("nativeom_candidate_generation_changed")
            if entry["relation"]["matched"] is True:
                result["formal_window_relation_observed"] = True
            capture_pending()
            if result["formal_window_relation_observed"]:
                result["early_stop_for_witness"] = True
                break
        for row in live.values():
            if not _matches(
                row,
                budget.read(
                    "nativeom.owned_generation.recheck",
                    lambda row=row: generation_reader(row["pid"]),
                ),
            ):
                raise ObservationGap("original_owned_generation_changed")
        _same(
            budget.read(
                "nativeom.held_application.IUnknown.recheck",
                lambda: app._oleobj_.QueryInterface(com.IID_IUnknown),
            ),
            held,
            "nativeom_held_application_changed",
        )
        if (
            W.document_binding(
                budget.read("nativeom.project.recheck", lambda: W._read_document(app))
            )
            != project
        ):
            raise ObservationGap("nativeom_native_project_changed")
        complete = result["nativeom_reader_return_count"] == len(windows)
        result["strong_witness_complete"] = result["formal_window_relation_observed"]
        result.update(
            candidate_coverage_complete=complete,
            observation_complete=complete,
            status="observed"
            if complete
            else "observed_witness"
            if result["strong_witness_complete"]
            else "incomplete",
            reason="nativeom_candidate_coverage_limit"
            if not complete and not result["strong_witness_complete"]
            else "complete_formal_witness_early_stop"
            if not complete
            else "no_existing_original_owned_windows"
            if not windows
            else "formal_native_app_window_relation_observed"
            if result["formal_window_relation_observed"]
            else "no_matching_native_application_object_observed",
        )
    except Exception as exc:
        try:
            capture_pending()
        except ObservationGap:
            result["query_row_capture_incomplete"] = True
        result.update(
            status="incomplete",
            formal_window_relation_observed=False,
            strong_witness_complete=False,
            reason=str(exc)
            if isinstance(exc, (ObservationGap, W.IdentityGap))
            else "nativeom_read_failed",
            error_type=type(exc).__name__,
        )
        hr = getattr(exc, "hresult", None)
        if type(hr) is int:
            result["hresult_hex"] = f"0x{hr & 0xFFFFFFFF:08x}"
    result["call_point"], result["read_operation_count"] = budget.point, budget.calls
    return result
