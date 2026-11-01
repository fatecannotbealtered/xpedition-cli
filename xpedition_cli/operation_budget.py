"""Bounded native-operation budgets; timing is evidence, never design revision state.

Each public request owns one monotonic deadline. A confirmation shares that
deadline across its drift read, dispatch, independent readback and verification.
The immutable profile is part of the signed plan; dynamic timings are not.
"""

from __future__ import annotations

import copy
import json
import math
import sys
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from .component_execution_budget import ENGINE_SECONDS
from .errors import CLIError

PROFILE_VERSION = "native-budget-v2"
_ACTIVE: ContextVar[OperationBudget | None] = ContextVar("xpedition_operation_budget", default=None)


def profile_for(command: str) -> dict[str, Any]:
    """Return a fresh, public fixed profile, selected without project-name heuristics."""
    kind = "general"
    preview, confirm = 1500, 2700
    dispatch = 1200
    observe, handler_preview, query = 660, 660, preview
    geometry, board_info = 300, 600
    if command in {"pcb render", "pcb snapshot"}:
        kind, preview, confirm, dispatch = "complete-board-artifact", 1200, 2760, 600
        observe, geometry = 960, 600
    elif command == "pcb view":
        kind, preview, confirm, dispatch = "board-artifact", 1200, 1800, 600
    elif command == "pcb inspect":
        kind, board_info, query = "complete-board-query", 1200, 1260
    elif command == "schematic build":
        kind, preview, confirm, dispatch = "schematic-build", 1800, 3300, 1800
    elif command == "pcb routing trace":
        kind, preview, confirm, dispatch = (
            "bounded-component-routing",
            2700,
            7560,
            1200 + ENGINE_SECONDS,
        )
        observe, handler_preview, board_info = 1260, 1200, 1200
    elif command == "pcb routing auto":
        kind, preview, confirm, dispatch = "bounded-routing", 1800, 3300, 1800
    elif command == "pcb placement move":
        kind, preview, confirm, dispatch = "placement-batch", 1800, 4260, 2100
        observe, handler_preview, geometry = 960, 600, 600
    elif command == "pcb checks run":
        kind, preview, confirm, dispatch = "complete-board-check", 1800, 4260, 1200
        observe, geometry = 960, 600
    elif command == "session close":
        kind, preview, confirm, dispatch = "owned-session-close", 900, 1500, 300
    elif command == "project create":
        kind = "project-create"
    profile = {
        "id": f"{PROFILE_VERSION}:{kind}",
        "version": PROFILE_VERSION,
        "total_seconds": {"preview": preview, "confirm": confirm, "query": preview},
        "stage_seconds": {
            "setup": 120,
            "observe_plan": observe,
            "handler_preview": handler_preview,
            "observe_confirm": observe,
            "dispatch": dispatch,
            "observe_after": observe,
            "verify": 120,
            "query": query if command == "pcb inspect" else preview,
        },
        "native_call_seconds": {
            "snapshot": 300,
            "board_geometry": geometry,
            "board_info": board_info,
            "outline_state": 300,
            "close": 180,
            "export_pdf": 600,
        },
        "os_wait_seconds": {"exit_observation": 60},
        "native_return_reserve_seconds": {"export_pdf": 15},
        "confirmation_reserve_seconds": {"observe_after": observe, "verify": 120},
        "native_phase_call_seconds": {
            "placement_batch": {"preview": 600, "apply": 1500},
            "component_hand_route": {"preview": 1200, "apply": ENGINE_SECONDS},
        },
        "cost_basis": {
            "sample_terminals": 467,
            "measured_complete_capture_seconds": 434.625,
            "planning_capture_seconds": 450,
            "complete_capture_hard_limit_seconds": 600,
            "batch_calibration_selected_components": 6,
            "batch_calibration_steps": 1,
            "query_formula": "2*450 + 300 identity/material allowance = 1200 native seconds",
            "render_confirm_formula": "120 setup + 960 before + 600 producer "
            "+ 960 after + 120 verify = 2760",
            "batch_apply_formula": "3*450 + 150 bounded action/persistence allowance = 1500",
            "batch_confirm_formula": "120 setup + 960 before + (600 preview + 1500 apply) "
            "+ 960 after + 120 verify = 4260",
            "component_trace_confirm_formula": "120 setup + 1260 before "
            "+ (1200 preview + 3600 native apply) + 1260 after + 120 verify = 7560",
            "qualification": "runtime resource planning only; "
            "467-terminal workflow not qualified by this profile",
            "environment_basis": "measured development candidate on the existing licensed "
            "CAD installation; not a clean-machine claim",
        },
        "timeout_policy": "no_automatic_replay; retain_original_operation_id_and_receipt",
    }
    if command == "project create":
        from .project_create_budget import HARD_CAP_SECONDS, MODEL

        profile["native_call_seconds"]["clone_project"] = HARD_CAP_SECONDS
        profile["native_requested_upper_bound_methods"] = ["clone_project"]
        profile["cost_basis"]["project_create"] = copy.deepcopy(MODEL)
    if command == "pcb checks run":
        profile["native_call_seconds"]["cell_refresh_state"] = 600
        profile["native_call_seconds"]["batch_drc"] = 1200
        profile["cost_basis"]["native_check"] = (
            "complete467-terminal captures use the existing600-second cap; "
            "SDK before/after stages also reserve ordinary snapshots and file observations"
        )
    return profile


def budget_catalog() -> dict[str, Any]:
    from .component_execution_budget import contract as component_cost_contract
    from .native_observation_stage import stage_limits
    from .placement_text_witness import contract as placement_text_contract

    commands = (
        "pcb render",
        "pcb snapshot",
        "pcb view",
        "pcb inspect",
        "pcb checks run",
        "schematic build",
        "pcb routing trace",
        "pcb routing auto",
        "pcb placement move",
        "session close",
        "project create",
    )
    return {
        "version": PROFILE_VERSION,
        "clock": "monotonic",
        "unit": "seconds",
        "native_call_unit": "adapter_subprocess_request_attempt",
        "complete_native_read_stage": stage_limits(),
        "component_execution_resources": component_cost_contract(),
        "placement_text_resources": placement_text_contract(),
        "fixed_full_read_counts": {
            "live_board_query": {"complete_captures": 2},
            "render_confirm": {"complete_geometry_reads": 3, "ordinary_snapshots": 2},
            "batch_confirm": {
                "sdk_independent_captures": 2,
                "handler_fresh_preview": 1,
                "native_apply_captures": 3,
            },
            "component_trace_confirm": {
                "sdk_independent_captures": 4,
                "handler_fresh_preview": 1,
                "native_apply_minimum_captures": 6,
            },
            "boundary": "counts retain full observations; "
            "no write-phase or cross-command cached substitute",
        },
        "profiles": {profile_for(item)["id"]: profile_for(item) for item in (*commands, "other")},
        "command_profiles": {item: profile_for(item)["id"] for item in commands},
        "default_profile": profile_for("other")["id"],
        "semantics": (
            "One total per public request, not a fresh total per native call. Preview and "
            "confirmation use the same signed profile. Confirmation reserves independent "
            "readback and verification time before dispatch. Native calls are never replayed."
        ),
    }


def current_budget() -> OperationBudget | None:
    return _ACTIVE.get()


class OperationBudget:
    def __init__(self, command: str, mode: str, *, clock=None, progress=None, quiet=False):
        if mode not in {"preview", "confirm", "query"}:
            raise ValueError("unsupported operation budget mode")
        self.command, self.mode = command, mode
        self.profile = profile_for(command)
        self.clock = clock or time.monotonic
        self.progress = progress
        self.quiet = quiet
        self.started = self.clock()
        self.deadline = self.started + self.profile["total_seconds"][mode]
        self.stages: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.active_stage: dict[str, Any] | None = None
        self.failed_stage: str | None = None
        self.first_failed_stage: str | None = None
        self._native_deadline = None
        self.dispatched = False

    def _emit(self, event: str, **values) -> None:
        if self.quiet:
            return
        item = {
            "type": "operation_progress",
            "event": event,
            "profile_id": self.profile["id"],
            "mode": self.mode,
            **values,
        }
        try:
            if self.progress:
                self.progress(item)
            else:
                sys.stderr.write(json.dumps(item, ensure_ascii=True, separators=(",", ":")) + "\n")
                sys.stderr.flush()
        except (OSError, ValueError):
            pass  # Diagnostics cannot replace a returned native outcome.

    def report(self) -> dict[str, Any]:
        now = self.clock()
        stages = []
        for item in self.stages:
            record = {key: value for key, value in item.items() if not key.startswith("_")}
            if "wall_ms" not in record:
                record["wall_ms"] = round(max(0.0, now - item["_started"]) * 1000, 3)
            stages.append(record)
        return {
            "profile_id": self.profile["id"],
            "profile": copy.deepcopy(self.profile),
            "mode": self.mode,
            "wall_ms": round(max(0.0, now - self.started) * 1000, 3),
            "remaining_ms": round(max(0.0, self.deadline - now) * 1000, 3),
            "native_call_count": len(self.calls),
            "native_call_unit": "adapter_subprocess_request_attempt",
            "cumulative_native_ms": round(sum(item["wall_ms"] for item in self.calls), 3),
            "stages": stages,
            "native_calls": copy.deepcopy(self.calls),
            "failed_stage": self.failed_stage,
            "first_failed_stage": self.first_failed_stage,
            "dispatch_started": self.dispatched,
        }

    def remaining_seconds(self) -> float:
        """A nested OS waiter shares the current phase and operation deadline."""
        now = self.clock()
        limit = self.deadline
        if self.active_stage is not None:
            limit = min(limit, self.active_stage["_deadline"])
        return max(0.0, limit - now)

    def _timeout(self, reason: str, *, method: str | None = None) -> CLIError:
        stage = self.active_stage["stage"] if self.active_stage else "setup"
        self.failed_stage = stage
        if self.first_failed_stage is None:
            self.first_failed_stage = stage
        return CLIError(
            "E_TIMEOUT",
            "The bounded operation budget was exhausted; no call was replayed.",
            {
                "budget_reason": reason,
                "failed_stage": stage,
                "method": method,
                "write_attempted": self.dispatched,
                "native_query_only": stage != "dispatch",
                "operation_budget": self.report(),
            },
        )

    def checkpoint(self) -> None:
        now = self.clock()
        if now >= self.deadline:
            raise self._timeout("total_deadline")
        if self.active_stage and now >= self.active_stage["_deadline"]:
            raise self._timeout("stage_deadline")

    @contextmanager
    def stage(self, name: str):
        if name not in self.profile["stage_seconds"]:
            raise ValueError("unknown operation stage")
        self.checkpoint()
        previous = self.active_stage
        now = self.clock()
        limit = min(self.profile["stage_seconds"][name], max(0, self.deadline - now))
        if self.mode == "confirm":
            reserves = self.profile["confirmation_reserve_seconds"]
            reserve = (
                sum(reserves.values())
                if name in {"observe_confirm", "dispatch"}
                else (reserves["verify"] if name == "observe_after" else 0)
            )
            limit = min(limit, max(0, self.deadline - now - reserve))
        record = {
            "stage": name,
            "limit_seconds": limit,
            "status": "running",
            "_started": now,
            "_deadline": now + limit,
        }
        self.stages.append(record)
        self.active_stage = record
        self._emit(
            "stage_started",
            stage=name,
            limit_seconds=limit,
            total_remaining_seconds=round(max(0, self.deadline - now), 3),
        )
        try:
            self.checkpoint()
            yield self
            self.checkpoint()
            record["status"] = "completed"
        except BaseException:
            record["status"] = "failed"
            self.failed_stage = name
            if self.first_failed_stage is None:
                self.first_failed_stage = name
            raise
        finally:
            record["wall_ms"] = round(max(0.0, self.clock() - now) * 1000, 3)
            self._emit(
                "stage_finished", **{k: v for k, v in record.items() if not k.startswith("_")}
            )
            self.active_stage = previous

    @contextmanager
    def native_call(self, method: str, requested_seconds: float):
        self.checkpoint()
        if not math.isfinite(requested_seconds) or requested_seconds <= 0:
            raise CLIError("E_VALIDATION", "Native timeout must be finite and positive.")
        limit = float(self.profile["native_call_seconds"].get(method, requested_seconds))
        # Public --timeout remains a user-selected upper bound for query stages;
        # internal fixed 60/120-second observers instead use the signed profile.
        if (
            self.active_stage and self.active_stage["stage"] == "query"
        ) or method in self.profile.get("native_requested_upper_bound_methods", []):
            limit = min(limit, requested_seconds)
        now = self.clock()
        limit = min(limit, self.deadline - now)
        if self.active_stage:
            limit = min(limit, self.active_stage["_deadline"] - now)
        if limit <= 0:
            raise self._timeout("no_native_call_budget", method=method)
        record = {
            "method": method,
            "stage": self.active_stage["stage"] if self.active_stage else "setup",
            "limit_seconds": round(limit, 6),
            "wall_ms": 0.0,
            "status": "running",
        }
        self.calls.append(record)
        self._emit(
            "native_call_started",
            method=method,
            stage=record["stage"],
            call_number=len(self.calls),
            limit_seconds=record["limit_seconds"],
        )
        previous_deadline = self._native_deadline
        self._native_deadline = (method, now + limit)
        try:
            yield limit
            record["status"] = "returned"
        except BaseException as error:
            record["status"] = (
                "not_dispatched"
                if isinstance(error, CLIError)
                and (error.details or {}).get("native_request_dispatched") is False
                else "failed"
            )
            self.failed_stage = record["stage"]
            if self.first_failed_stage is None:
                self.first_failed_stage = record["stage"]
            raise
        finally:
            self._native_deadline = previous_deadline
            record["wall_ms"] = round(max(0.0, self.clock() - now) * 1000, 3)
            self._emit("native_call_finished", call_number=len(self.calls), **record)


@contextmanager
def operation_request(command: str, mode: str, *, quiet=False, clock=None, progress=None):
    """Reuse an outer request across SDK revision/apply; never reset its deadline."""
    active = current_budget()
    if active is not None:
        yield active
        return
    budget = OperationBudget(command, mode, quiet=quiet, clock=clock, progress=progress)
    token = _ACTIVE.set(budget)
    try:
        yield budget
    finally:
        _ACTIVE.reset(token)


@contextmanager
def operation_stage(name: str):
    budget = current_budget()
    if budget is None:
        yield None
    else:
        with budget.stage(name):
            yield budget


@contextmanager
def native_call_budget(method: str, requested_seconds: float):
    budget = current_budget()
    if budget is None:
        yield requested_seconds
    else:
        with budget.native_call(method, requested_seconds) as seconds:
            yield seconds


def native_call_remaining(method: str):
    """Remaining allocation for explicitly selected methods, including route setup."""
    budget = current_budget()
    if budget is None or method not in budget.profile.get(
        "native_requested_upper_bound_methods", []
    ):
        return None
    bound = budget._native_deadline
    if bound is None or bound[0] != method:
        return None
    return max(0.0, min(bound[1] - budget.clock(), budget.remaining_seconds()))


def checkpoint() -> None:
    budget = current_budget()
    if budget is not None:
        budget.checkpoint()
