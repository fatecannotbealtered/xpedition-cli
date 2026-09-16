from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class Backend(Protocol):
    """Minimal backend boundary shared by MockBackend and future adapters."""

    name: str

    def load(self, project_path: str | None) -> tuple[dict[str, Any], Path | None]: ...

    def snapshot(self, project: dict[str, Any]) -> dict[str, Any]: ...
