"""Padstacks of the central library: the rows ``padstack list`` returns, and the via padstack
``padstack create`` adds.

A via padstack has one round pad on every conductor layer around a plated, drilled hole. With a
soldermask opening its pad is bare on both sides; without one the via is tented, covered by the
soldermask or the coverlay. Its pads and hole are named by their size, as the parts file's are,
so the same size is shared with the padstacks the library holds already.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import PlanError
from . import library_hkp as H
from . import library_read as R

TYPES = {"VIA": "via", "PIN_SMD": "smd", "PIN_THROUGH": "through", "MOUNTING_HOLE": "mounting_hole"}
MAX_SIZE_MM = 10.0


def type_name(kind: str) -> str:
    return TYPES.get(kind, kind.lower())


def row(library: dict[str, Any], name: str) -> dict[str, Any]:
    """One padstack as ``padstack list`` and the read-back give it, lengths in mm."""
    geometry = R.padstack_geometry(library, name) or {}
    pad = geometry.get("pad") or {}
    hole = geometry.get("hole") or {}
    mask = geometry.get("mask") or {}
    out: dict[str, Any] = {"name": name, "type": type_name(str(geometry.get("type", ""))),
                           "pad_shape": str(pad.get("shape", "")).lower() or None, "pad_mm": pad.get("width"),
                           "drill_mm": hole.get("width"), "plated": hole.get("plated"), "mask_mm": mask.get("width")}
    if pad.get("height") is not None and pad.get("height") != pad.get("width"):
        out["pad_height_mm"] = pad.get("height")
    return out


def rows(library: dict[str, Any], kind: str | None = None) -> list[dict[str, Any]]:
    out = [row(library, name) for name in sorted(library.get("padstacks", {}))]
    return [r for r in out if kind is None or r["type"] == kind]


@dataclass(frozen=True)
class Via:
    name: str
    drill_mm: float
    pad_mm: float
    mask_mm: float | None = None  # None: tented

    def fields(self) -> dict[str, Any]:
        return {"type": "via", "pad_mm": self.pad_mm, "drill_mm": self.drill_mm, "plated": True, "mask_mm": self.mask_mm}


def default_name(drill_mm: float, pad_mm: float) -> str:
    return f"VIA_D{H._num(drill_mm)}_P{H._num(pad_mm)}"


def via(name: str | None, drill_mm: Any, pad_mm: Any, mask_mm: Any = None) -> Via:
    """The via padstack the inputs describe; refuses sizes that make no via."""
    for label, value in (("--drill", drill_mm), ("--pad", pad_mm)):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 < value <= MAX_SIZE_MM:
            raise PlanError("E_VALIDATION", f"{label} is a length of more than 0 and at most {MAX_SIZE_MM:g} mm.")
    if pad_mm <= drill_mm:
        raise PlanError("E_VALIDATION", f"The pad ({pad_mm:g} mm) must be wider than the hole ({drill_mm:g} mm), or no copper is "
                        "left around it.")
    if mask_mm is not None and (not isinstance(mask_mm, (int, float)) or isinstance(mask_mm, bool)
                                or not 0 < mask_mm <= MAX_SIZE_MM):
        raise PlanError("E_VALIDATION", f"--mask is a length of more than 0 and at most {MAX_SIZE_MM:g} mm.")
    text = (name or "").strip() or default_name(drill_mm, pad_mm)
    if len(text) > 64 or '"' in text or not text.isascii():
        raise PlanError("E_VALIDATION", "--name is an ASCII name of at most 64 characters without quotes.")
    return Via(text, round(float(drill_mm), 4), round(float(pad_mm), 4), None if mask_mm is None else round(float(mask_mm), 4))


def plan(spec: Via, library: dict[str, Any]) -> str:
    """``add`` when the library lacks the padstack, ``skip`` when it holds the same one; refuses
    a padstack of that name with other content."""
    if spec.name not in library.get("padstacks", {}):
        return "add"
    held = row(library, spec.name)
    same = (held["type"] == "via" and held["plated"] is not False
            and all(_close(held.get(k), getattr(spec, k)) for k in ("pad_mm", "drill_mm", "mask_mm")))
    if same:
        return "skip"
    raise PlanError("E_CONFLICT", f"The central library holds a padstack {spec.name} with other content "
                    f"({held['type']}, pad {held.get('pad_mm')} mm, drill {held.get('drill_mm')} mm, mask {held.get('mask_mm')} mm); "
                    "give the new via another --name.", {"padstack": spec.name})


def _close(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) < 0.0005


def hkp(spec: Via) -> str:
    """The PadstackDB2HKP text of the via: its pads, its hole and the padstack."""
    pad = H.Pad(H._pad_name("ROUND", spec.pad_mm), "ROUND", spec.pad_mm)
    hole = H.Hole(H._hole_name((spec.drill_mm, spec.drill_mm), True), spec.drill_mm, True)
    plan_ = H.LibraryPlan()
    plan_.pads[pad.name] = pad
    plan_.holes[hole.name] = hole
    mask = None
    if spec.mask_mm is not None:
        mask_pad = H.Pad(H._pad_name("ROUND", spec.mask_mm), "ROUND", spec.mask_mm)
        plan_.pads[mask_pad.name] = mask_pad
        mask = mask_pad.name
    plan_.padstacks[spec.name] = H.Padstack(spec.name, "VIA", pad=pad.name, mask=mask, hole=hole.name)
    return H.render_padstacks(plan_)
