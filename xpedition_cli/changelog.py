"""Build-time CHANGELOG source used by the runtime ``changelog`` command."""

from __future__ import annotations

from . import resources


def markdown() -> str:
    """The changelog this installation shipped with (see `resources`)."""
    path = resources.locate("CHANGELOG.md")
    return path.read_text(encoding="utf-8") if path else ""
