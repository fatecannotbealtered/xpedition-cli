from __future__ import annotations

import platform
from dataclasses import asdict, dataclass
from typing import Any

from .backends.native_xpedition import NativeBackend


@dataclass(frozen=True)
class Capability:
    name: str
    status: str
    backend: str
    operations: tuple[str, ...]
    reason: str | None = None


class CapabilityRegistry:
    """Runtime capability registry; unsupported native actions stay explicit."""

    def __init__(self) -> None:
        native_status = NativeBackend().status()
        native_command = native_status.get("automation_command_configured")
        native_available = bool(native_status.get("available"))
        native_reason = native_status.get("reason")
        self._capabilities = [
            Capability(
                "mock",
                "available",
                "MockBackend",
                (
                    "project.info",
                    "project.init",
                    "project.tree",
                    "project.snapshot",
                    "project.diff",
                    "design.snapshot",
                    "change.validate",
                    "change.preview",
                    "change.apply",
                    "change.history",
                    "change.rollback",
                    "review.run",
                    "review.findings",
                    "review.report",
                    "bom.export",
                    "bom.normalize",
                    "bom.group",
                    "bom.variants",
                    "bom.missing",
                    "bom.duplicates",
                    "bom.validate",
                    "bom.compare",
                    "schematic.read",
                    "schematic.apply",
                    "schematic.write",
                    "pcb.read",
                    "pcb.write",
                    "constraints.read",
                    "analysis.read",
                    "analysis.run",
                    "manufacturing.read",
                    "manufacturing.artifacts",
                    "manufacturing.verify",
                    "manufacturing.bom",
                    "library.read",
                    "library.validate",
                    "exchange.inspect",
                    "exchange.import",
                    "session.status",
                    "session.logs",
                ),
            ),
            Capability(
                "native_xpedition",
                "available" if native_available else "unavailable",
                "NativeBackend",
                tuple(
                    NativeBackend().capabilities().get("operations", []) if native_available else ()
                ),
                native_reason
                or (
                    "native COM adapter is ready"
                    if native_command
                    else "install the native COM adapter"
                ),
            ),
            Capability(
                "exchange_files",
                "available",
                "ExchangeBackend",
                ("exchange.inspect", "exchange.import"),
                "JSON/CSV/BOM and IPC-2581 parsing is available; PDF, EDN and ODB++ are planned",
            ),
            Capability(
                "agent_stdio",
                "available",
                "AgentBridge",
                (
                    "agent.snapshot",
                    "agent.query",
                    "agent.review",
                    "agent.capabilities",
                    "agent.serve",
                ),
            ),
            Capability(
                "agent_mcp",
                "available",
                "MCPServer",
                ("initialize", "ping", "tools/list", "tools/call"),
            ),
        ]

    def as_dict(self) -> list[dict[str, Any]]:
        return [asdict(item) for item in self._capabilities]

    def backend(self, name: str) -> Capability:
        for item in self._capabilities:
            if item.name == name:
                return item
        return Capability(name, "unknown", "", (), "unknown backend")

    def summary(self) -> dict[str, Any]:
        return {
            "platform": platform.platform(),
            "xpedition_native_command_configured": bool(
                NativeBackend().status().get("automation_command_configured")
            ),
            "capabilities": self.as_dict(),
            # What is not there yet: these reads return empty data on the native
            # backend, and the adapter implements only part of the ChangeSet set.
            "planned_command_domains": [
                "native_constraints",
                "native_analysis",
                "native_manufacturing",
                "native_library_reads",
                "native_changeset_operations",
                "exchange_imports",
            ],
            "_untrusted": ["platform", "capabilities"],
        }
