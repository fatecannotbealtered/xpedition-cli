from __future__ import annotations

from pathlib import Path
from typing import Any

from ..models import load_project, snapshot


class MockBackend:
    name = "mock"

    def load(self, project_path: str | None) -> tuple[dict[str, Any], Path | None]:
        return load_project(project_path)

    def snapshot(self, project: dict[str, Any]) -> dict[str, Any]:
        return snapshot(project)

    def capabilities(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "available": True,
            "licensed": False,
            "operations": [
                "place_component",
                "create_net",
                "connect",
                "move_component",
                "delete_component",
                "set_property",
                "place_pcb_component",
                "move_pcb_component",
                "create_track",
                "create_via",
                "create_zone",
            ],
        }
