"""Conservative Layout binding for selected-origin placement tasks.

API names/signatures were cross-checked against a published community makepy
interface. One licensed smoke run covers it, top side only (2026-09-19, docs/E2E.md). It never
disables placement DRC while editing, edits routing, flips parts, or invokes bulk UnPlace.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

from .errors import CLIError
from .placement import (
    BATCH_GUARD,
    MAX_BOARD_COMPONENTS,
    _exception_issue,
    execute_placement,
    normalize_state,
    plan_placement,
    validate_request,
)


class LayoutPlacementDriver:
    def __init__(
        self, doc: Any, selection: list[str], *, unit_mm: int, app=None, board=None, bridge=None
    ):
        self.doc = doc
        self.unit_mm = unit_mm
        self.app, self.board, self.bridge = app, board, bridge
        self.components: dict[str, Any] = {}
        # One collection pass per adapter request, not one full-board scan per move.
        try:
            collection = doc.Components
            count = collection.Count
            if type(count) is not int or not 0 <= count <= MAX_BOARD_COMPONENTS:
                raise ValueError("invalid count")
            wanted = set(selection)
            names, ids = set(), set()
            for i in range(1, count + 1):
                item = collection.Item(i)
                name = item.RefDes
                uid = item.UniqueId
                if not isinstance(name, str) or not name.strip() or name in names:
                    raise CLIError("E_CONFLICT", "full-board reference designator is ambiguous")
                if type(uid) not in (int, str) or not str(uid).strip() or str(uid) in ids:
                    raise CLIError("E_CONFLICT", "full-board object identity is ambiguous")
                names.add(name)
                ids.add(str(uid))
                if name in wanted:
                    if name in self.components:
                        raise CLIError("E_CONFLICT", "selected reference designator is ambiguous")
                    self.components[name] = item
        except CLIError:
            raise
        except Exception as error:
            raise CLIError(
                "E_BACKEND_UNAVAILABLE", "cannot enumerate Layout component identities"
            ) from error
        if set(self.components) != set(selection):
            raise CLIError("E_NOT_FOUND", "selected components were not all found")

    def observe(self, selection: list[str]) -> list[dict[str, Any]]:
        rows = []
        try:
            for name in selection:
                item = self.components[name]
                # No fallback to PositionX/Y: those may use implicit display units.
                side = item.Side
                if type(side) is not int or side not in (1, 512):
                    raise ValueError("unknown component side")
                object_id = item.UniqueId
                if type(object_id) not in (int, str) or str(object_id).strip() == "":
                    raise ValueError("unobserved identity")
                protection = {}
                for member, field in (("Anchor", "anchor"), ("FixLock", "fix_lock")):
                    value = getattr(item, member)
                    if type(value) not in (int, bool) or not 0 <= int(value) <= 0xFFFFFFFF:
                        raise ValueError("unobserved protection flags")
                    # Both documented native scalar representations preserve the
                    # safety decision: zero is movable; every nonzero is protected.
                    protection[field] = int(value)
                row = {
                    "refdes": item.RefDes,
                    "object_id": str(object_id),
                    "unit": "mm",
                    "x": item.GetPositionX(self.unit_mm),
                    "y": item.GetPositionY(self.unit_mm),
                    "rotation": item.GetOrientation(0),
                    "side": "top" if side == 1 else "bottom",
                    "placed": item.Placed,
                    **protection,
                }
                rows.append(row)
            return normalize_state(rows, selection)
        except Exception as error:
            # Fail before mutation when observation/protection support is missing.
            # execute_placement classifies an observation error AFTER a write separately.
            raise CLIError(
                "E_BACKEND_UNAVAILABLE", "cannot observe exact placement, side or protection state"
            ) from error

    def enable_drc(self) -> bool:
        try:
            previous = self.doc.RespectComponentPlacementDRC
            if type(previous) is not bool:
                raise ValueError("unknown placement DRC state")
        except Exception as error:
            raise CLIError("E_BACKEND_UNAVAILABLE", "cannot read placement DRC state") from error
        try:
            self.doc.RespectComponentPlacementDRC = True
            if self.doc.RespectComponentPlacementDRC is not True:
                raise ValueError("placement DRC was not enabled")
        except Exception as error:
            restored = False
            try:
                self.restore_drc(previous)
                restored = True
            except Exception:
                pass
            raise CLIError(
                "E_BACKEND_UNAVAILABLE",
                "cannot enable placement DRC",
                {"drc_restored": restored, "write_attempted": False},
            ) from error
        return previous

    def restore_drc(self, previous: bool) -> None:
        self.doc.RespectComponentPlacementDRC = previous
        if self.doc.RespectComponentPlacementDRC is not previous:
            raise CLIError("E_PROJECT_INVALID", "placement DRC setting did not restore")

    def move(self, target: dict[str, Any]) -> None:
        item = self.components[target["refdes"]]
        # Each selected part is moved in explicit order, with native placement DRC
        # enabled. An error can leave it unplaced; do not conceal it with blind undo.
        item.UnPlace()
        item.Place(
            target["x"],
            target["y"],
            target["rotation"],
            target["side"] == "top",
            0,
            self.unit_mm,
            0,
        )

    def observe_failure(self, refdes: str) -> dict[str, Any]:
        """Report a failed part without requiring it to remain placed."""
        item = self.components[refdes]
        if item.RefDes != refdes or type(item.Placed) is not bool:
            raise CLIError("E_VERIFY_INCOMPLETE", "failed component state is unavailable")
        return {
            "refdes": item.RefDes,
            "object_id": str(item.UniqueId),
            "placed": item.Placed,
            "x": item.GetPositionX(self.unit_mm),
            "y": item.GetPositionY(self.unit_mm),
            "rotation": item.GetOrientation(0),
            "side": item.Side,
            "unit": "mm",
        }

    def capture_state(self) -> dict[str, Any]:
        from .cell_refresh import capture
        from .placement_text_witness import capture as capture_text_witness
        from .placement_text_witness import preflight_native_text_count

        if self.bridge is None:
            raise CLIError("E_VERIFY_INCOMPLETE", "full-board native material binding is required")
        preflight_native_text_count(self.doc)
        state = capture(self.doc, self.bridge, strict=True)
        return capture_text_witness(self.doc, self.bridge, state)

    def save_reopen(self, lifecycle: dict[str, Any]) -> None:
        from .native_identity import application_identity
        from .native_process import process_identity, same_identity

        if self.app is None or self.board is None or self.bridge is None:
            raise CLIError("E_VERIFY_INCOMPLETE", "authenticated native lifecycle is required")
        identity = application_identity(self.app, "pcb")
        pid = identity.get("pid")
        if (
            type(pid) is not int
            or pid <= 0
            or Path(str(self.doc.FullName)).resolve() != self.board.resolve()
        ):
            raise CLIError("E_CONFLICT", "placement application/document identity is incomplete")
        lifecycle.update(pid_before=pid, board=str(self.board.resolve()), reopen_verified=False)
        # OS evidence is independent of a reusable numeric PID.  A missing pin
        # never grants the later Net0-reference exception; unchanged raw states
        # retain the pre-existing strict lifecycle behavior.
        try:
            lifecycle["process_identity_before"] = process_identity("pcb", pid)
        except (OSError, ValueError) as error:
            lifecycle["process_identity_before"] = None
            lifecycle["process_identity_before_error"] = type(error).__name__
        self.bridge._save_close_board(self.app, self.board, lifecycle=lifecycle)
        lifecycle["open_count"] += 1
        lifecycle["open_reply_received"] = False
        opened = self.bridge._open_layout_document(self.app, self.board)
        lifecycle["open_reply_received"] = True
        if not isinstance(opened, tuple) or len(opened) != 2 or opened[0] is None:
            raise CLIError("E_VERIFY_INCOMPLETE", "placement reopen returned no document")
        reopened = self.bridge._licensed_document(self.app)
        after = application_identity(self.app, "pcb")
        lifecycle.update(pid_after=after.get("pid"), reopened_path=str(reopened.FullName))
        try:
            lifecycle["process_identity_after"] = process_identity("pcb", after.get("pid"))
        except (OSError, ValueError, TypeError) as error:
            lifecycle["process_identity_after"] = None
            lifecycle["process_identity_after_error"] = type(error).__name__
        if (
            after.get("pid") != pid
            or Path(str(reopened.FullName)).resolve() != self.board.resolve()
        ):
            raise CLIError("E_CONFLICT", "placement reopened process or board differs")
        if (
            lifecycle["process_identity_before"] is not None
            and lifecycle["process_identity_after"] is not None
            and not same_identity(
                lifecycle["process_identity_before"], lifecycle["process_identity_after"]
            )
        ):
            raise CLIError("E_CONFLICT", "placement native process generation changed")
        # Every wrapper is rebuilt from the reopened native collection. Cached COM
        # components may otherwise keep reporting a stale closed document.
        rebuilt = LayoutPlacementDriver(
            reopened,
            list(self.components),
            unit_mm=self.unit_mm,
            app=self.app,
            board=self.board,
            bridge=self.bridge,
        )
        self.doc, self.components = rebuilt.doc, rebuilt.components
        lifecycle["reopen_verified"] = True


def run(params: dict[str, Any], client: Any) -> dict[str, Any]:
    request = validate_request(params.get("request"))
    apply = params.get("apply", False)
    if type(apply) is not bool:
        raise CLIError("E_VALIDATION", "apply must be a boolean")
    expected = params.get("state_digest")
    if apply and (not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected)):
        raise CLIError("E_USAGE", "a preview state digest is required")
    if apply and params.get("state_guard") != BATCH_GUARD:
        raise CLIError(
            "E_CONFLICT",
            "a complete native placement material guard is required",
            {"stage": "precondition", "write_attempted": False},
        )
    from . import native_com_adapter as bridge
    from .audit import config_dir
    from .confirmation_store import exclusive_lock

    pcb_path = bridge._layout_board_path(params).resolve()
    key = hashlib.sha256(os.path.normcase(str(pcb_path)).encode("utf-8")).hexdigest()
    lock = config_dir() / f"placement-{key}.lock"
    try:
        with exclusive_lock(lock):
            # This sidecar is shared only by this new batch method. Legacy commands,
            # other config directories, engineers and external tools do not cooperate.
            try:
                app = bridge._application(client, attach_only=True, quiet=False)
                doc = bridge._licensed_document(app)
                if Path(str(doc.FullName)).resolve() != pcb_path:
                    raise CLIError(
                        "E_CONFLICT", "the requested placement board must already be open"
                    )
                prompts = []
                driver = LayoutPlacementDriver(
                    doc,
                    request["selection"],
                    unit_mm=bridge.UNIT_MM,
                    app=app,
                    board=pcb_path,
                    bridge=bridge,
                )
            except Exception as error:
                raise CLIError(
                    getattr(error, "code", "E_VERIFY_INCOMPLETE"),
                    "placement identity precondition failed; no component edit was dispatched",
                    {
                        **(getattr(error, "details", {}) or {}),
                        "stage": "precondition",
                        "write_attempted": False,
                        "cause": type(error).__name__,
                        "cause_error": _exception_issue(error),
                    },
                ) from error
            if apply:
                result = execute_placement(request, expected, driver)
            else:
                result = plan_placement(
                    request,
                    driver.observe(request["selection"]),
                    protection_state=driver.capture_state(),
                )
            result.update(pcb=str(pcb_path), prompts=prompts)
            result["_untrusted"] = [*result.get("_untrusted", []), "pcb", "prompts"]
            return result
    except CLIError as error:
        if (error.details or {}).get("stage") == "confirmation_lock":
            raise CLIError(
                "E_CONFLICT",
                "placement session is busy; no batch write was started",
                {"stage": "placement_lock", "write_attempted": False},
            ) from error
        raise
    except OSError as error:
        # Engineering operation exclusion is not the token ledger's permitted
        # degradation policy. Never run the batch without this lock.
        raise CLIError(
            "E_IO",
            "cannot acquire placement session lock",
            {"stage": "placement_lock", "write_attempted": False},
        ) from error
