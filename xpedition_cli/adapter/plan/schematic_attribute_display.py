"""The part attributes whose display Designer reports: the six named families a schematic
read covers. Any other attribute, and free text, is not read as a display."""

from __future__ import annotations

from typing import Any

DISPLAY_NAMES = ("Name", "Value", "Part Label", "Part Name", "Part Number", "Ref Designator")
VALUE_VISIBLE = 3  # VdVisibilityFlag.VDVALUEVISIBLE, installed Viewdraw 151.0 typelib


def canonical_name(name: Any) -> str:
    """An attribute name compared without case or spaces: `Ref Designator` is `refdesignator`."""
    return "".join(str(name).split()).casefold()


def is_display_name(name: Any) -> bool:
    """Whether `name` is one of `DISPLAY_NAMES`, by `canonical_name`."""
    return canonical_name(name) in {canonical_name(item) for item in DISPLAY_NAMES}
