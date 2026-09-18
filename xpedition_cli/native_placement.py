"""Conservative Layout binding for selected-origin placement tasks.

API names/signatures were cross-checked against a published community makepy
interface. This new path has not been validated on licensed Xpedition. It never
disables placement DRC while editing, edits routing, flips parts, or invokes bulk UnPlace.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any

from .errors import CLIError
from .placement import execute_placement, normalize_state, plan_placement, validate_request


class LayoutPlacementDriver:
    def __init__(self, doc: Any, selection: list[str], *, unit_mm: int):
        self.doc = doc
        self.unit_mm = unit_mm
        self.components: dict[str, Any] = {}
        # One collection pass per adapter request, not one full-board scan per move.
        try:
            collection = doc.Components
            count = collection.Count
            if type(count) is not int or count < 0:
                raise ValueError("invalid count")
            wanted = set(selection)
            for i in range(1, count + 1):
                item = collection.Item(i)
                name = item.RefDes
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
                row = {
                    "refdes": item.RefDes,
                    "object_id": str(object_id),
                    "unit": "mm",
                    "x": item.GetPositionX(self.unit_mm),
                    "y": item.GetPositionY(self.unit_mm),
                    "rotation": item.GetOrientation(0),
                    "side": "top" if side == 1 else "bottom",
                    "placed": item.Placed,
                    "anchor": item.Anchor,
                    "fix_lock": item.FixLock,
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

    def save(self) -> None:
        self.doc.Save()


def run(params: dict[str, Any], client: Any) -> dict[str, Any]:
    request = validate_request(params.get("request"))
    apply = params.get("apply", False)
    if type(apply) is not bool:
        raise CLIError("E_VALIDATION", "apply must be a boolean")
    expected = params.get("state_digest")
    if apply and (not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected)):
        raise CLIError("E_USAGE", "a preview state digest is required")
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
            app = bridge._application(client, attach_only=True)
            _doc, prompts = bridge._open_layout_document(app, pcb_path)
            doc = bridge._licensed_document(app)
            driver = LayoutPlacementDriver(doc, request["selection"], unit_mm=bridge.UNIT_MM)
            if apply:
                result = execute_placement(request, expected, driver)
            else:
                result = plan_placement(request, driver.observe(request["selection"]))
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
