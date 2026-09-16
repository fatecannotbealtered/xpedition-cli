"""Build-time CHANGELOG source used by the runtime ``changelog`` command."""

from __future__ import annotations

import sys
from pathlib import Path


def markdown() -> str:
    """Read the source changelog or its PyInstaller-bundled copy."""
    candidates = [
        Path(__file__).resolve().parent.parent / "CHANGELOG.md",
        Path(getattr(sys, "_MEIPASS", "")) / "CHANGELOG.md",
        Path.cwd() / "CHANGELOG.md",
    ]
    for path in candidates:
        if path.exists():
            return path.read_text(encoding="utf-8")
    return ""
