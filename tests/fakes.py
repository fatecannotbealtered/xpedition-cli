"""A stand-in for the Xpedition adapter, at the one boundary the CLI crosses.

Every native command reaches Designer or Layout through one subprocess call: a
JSON request on stdin, one JSON object back. `FakeAdapter` answers that call from
per-method handlers and records every request, so a test drives the real CLI
end to end and checks both what it sent and what it made of the answer.
"""

from __future__ import annotations

import copy
import json
import subprocess
from collections.abc import Callable
from typing import Any

Handler = Callable[[dict[str, Any]], dict[str, Any]] | dict[str, Any]

SCHEMATIC = {
    "project": "Board",
    "revision": "native",
    "sheets": [{"name": "Schematic1.1", "full_name": "Schematic1.1"}, {"name": "Schematic1.2"}],
    "components": [
        {
            "refdes": "U1",
            "internal_part_no": "MCU-001",
            "value": "MCU-001",
            "package": "QFN32",
            "description": "microcontroller",
            "manufacturer": "",
            "mpn": "",
            "sheet": 1,
            "x": 400,
            "y": 500,
            "pins": [
                {"number": "1", "net": "+3V3"},
                {"number": "2", "net": "GND"},
                {"number": "3", "net": "I2C_SDA"},
                {"number": "4", "net": "I2C_SCL"},
            ],
        },
        {
            "refdes": "C1",
            "internal_part_no": "CAP-100N",
            "value": "100nF",
            "package": "0402",
            "sheet": 1,
            "x": 300,
            "y": 500,
            "pins": [{"number": "1", "net": "+3V3"}, {"number": "2", "net": "GND"}],
        },
        {
            "refdes": "R1",
            "internal_part_no": "RES-4K7",
            "value": "4.7k",
            "package": "0402",
            "sheet": 2,
            "x": 600,
            "y": 300,
            "pins": [{"number": "1", "net": "+3V3"}, {"number": "2", "net": "I2C_SDA"}],
        },
        {
            "refdes": "R2",
            "internal_part_no": "RES-4K7",
            "value": "4.7k",
            "package": "0402",
            "sheet": 2,
            "x": 700,
            "y": 300,
            "pins": [{"number": "1", "net": "+3V3"}, {"number": "2", "net": "I2C_SCL"}],
        },
    ],
    "nets": [
        {"name": "+3V3", "sheets": [1, 2]},
        {"name": "GND", "sheets": [1]},
        {"name": "I2C_SDA", "sheets": [1, 2]},
        {"name": "I2C_SCL", "sheets": [1, 2]},
    ],
    "connections": [
        {"net": "+3V3", "pins": ["C1.1", "R1.1", "R2.1", "U1.1"]},
        {"net": "GND", "pins": ["C1.2", "U1.2"]},
        {"net": "I2C_SDA", "pins": ["R1.2", "U1.3"]},
        {"net": "I2C_SCL", "pins": ["R2.2", "U1.4"]},
    ],
    "metadata": {"backend": "native_xpedition", "domain": "schematic"},
}

BOARD = {
    "project": "Board.pcb",
    "revision": "native",
    "components": [],
    "nets": [],
    "connections": [],
    "pcb": {
        "components": [
            {
                "refdes": "U1",
                "footprint": "QFN32",
                "x": 10.0,
                "y": 10.0,
                "rotation": 0,
                "side": "top",
                "placed": True,
            },
            {
                "refdes": "C1",
                "footprint": "0402",
                "x": 5.0,
                "y": 10.0,
                "rotation": 0,
                "side": "top",
                "placed": True,
            },
            {
                "refdes": "R1",
                "footprint": "0402",
                "x": 0,
                "y": 0,
                "rotation": 0,
                "side": "top",
                "placed": False,
            },
        ],
        "nets": [{"name": "+3V3"}, {"name": "GND"}],
        "footprints": ["0402", "QFN32"],
        "tracks": [{"net": "GND", "layer": 1}],
        "vias": [],
    },
    "metadata": {"backend": "native_xpedition", "layer_count": 4},
}


def schematic_snapshot() -> dict[str, Any]:
    return copy.deepcopy(SCHEMATIC)


def board_snapshot() -> dict[str, Any]:
    return copy.deepcopy(BOARD)


class Failure(Exception):
    """Raised by a handler: the adapter answers with this error instead of data."""

    def __init__(self, code: str, message: str = "failed", details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}


class FakeAdapter:
    """Answers adapter calls by method; records every request in `calls`."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.handlers: dict[str, Handler] = {
            "health": {
                "ready": True,
                "application_running": True,
                "designer_application_running": True,
            },
            "snapshot": self._snapshot,
        }
        self.errors: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _snapshot(params: dict[str, Any]) -> dict[str, Any]:
        return schematic_snapshot() if params.get("domain") == "schematic" else board_snapshot()

    def on(self, method: str, answer: Handler) -> FakeAdapter:
        self.handlers[method] = answer
        return self

    def fail(
        self, method: str, code: str, message: str = "failed", details: dict[str, Any] | None = None
    ) -> FakeAdapter:
        self.errors[method] = {"code": code, "message": message, "details": details or {}}
        return self

    def methods(self) -> list[str]:
        return [call["method"] for call in self.calls]

    def params(self, method: str) -> list[dict[str, Any]]:
        return [call["params"] for call in self.calls if call["method"] == method]

    def last(self, method: str) -> dict[str, Any]:
        found = self.params(method)
        assert found, f"{method} was never called; calls: {self.methods()}"
        return found[-1]

    def run(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        request = json.loads(kwargs["input"])
        method, params = request["method"], request.get("params") or {}
        self.calls.append({"method": method, "params": params, "timeout": kwargs.get("timeout")})
        if method in self.errors:
            body = {"ok": False, "error": self.errors[method]}
            return subprocess.CompletedProcess(argv, 0, json.dumps(body), "")
        if method not in self.handlers:
            raise AssertionError(f"unexpected adapter call {method!r} with {params!r}")
        answer = self.handlers[method]
        try:
            data = answer(params) if callable(answer) else copy.deepcopy(answer)
        except Failure as failure:
            error = {"code": failure.code, "message": failure.message, "details": failure.details}
            return subprocess.CompletedProcess(
                argv, 0, json.dumps({"ok": False, "error": error}), ""
            )
        return subprocess.CompletedProcess(argv, 0, json.dumps({"ok": True, "data": data}), "")


# -- a central library ------------------------------------------------------------------

LIBRARY_PARTS = {
    "partition": "PartQuest",
    "parts": [
        {
            "number": "RES-10K",
            "description": "resistor 10k 0402",
            "prefix": "R",
            "value": "10k",
            "symbol": {"kind": "RES"},
            "footprint": {"family": "chip", "size": "0402"},
        },
        {
            "number": "MCU-8",
            "description": "microcontroller, SOIC-8",
            "prefix": "U",
            "symbol": {
                "left": [["1", "VDD"], ["2", "PA0"], ["3", "PA1"], ["4", "GND"]],
                "right": [["8", "PB0"], ["7", "PB1"], ["6", "SWDIO"], ["5", "SWCLK"]],
            },
            "footprint": {
                "family": "gullwing",
                "pins": 8,
                "pitch": 1.27,
                "span": {"nominal": 6.0, "tolerance": 0.2},
                "terminal": [0.4, 1.27],
                "lead_width": [0.31, 0.51],
                "body": [3.9, 4.9],
                "height": 1.75,
            },
        },
    ],
}


class FakeLibrary:
    """A central library as the adapter's `library_export` hands it over: HKP texts in a
    cache folder and symbol files, all written by the tool's own library writers.

    `absorb` is a `library_import` handler: it merges what an import sends, the way the
    converters merge into the databases, so a later export shows the new parts.
    """

    def __init__(self, folder, spec: dict[str, Any] | None = None) -> None:
        from pathlib import Path

        from xpedition_cli import library_parts

        self.folder = Path(folder)
        self.cache = self.folder / "cache"
        self.symbols = self.folder / "SymbolLibs"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.texts = {"parts": "", "cells": "", "padstacks": ""}
        plan = library_parts.plan(spec or LIBRARY_PARTS)
        self.merge("PartQuest", plan.texts(), plan.symbols)

    def merge(self, partition: str, texts: dict[str, str], symbols: dict[str, str]) -> None:
        for kind in ("parts", "cells"):
            if texts.get(kind):
                path = self.cache / f"{kind}-{partition}.hkp"
                old = path.read_text(encoding="utf-8") if path.exists() else ""
                path.write_text(old + "\n" + texts[kind], encoding="utf-8")
        if texts.get("padstacks"):
            path = self.cache / "padstacks-.hkp"
            old = path.read_text(encoding="utf-8") if path.exists() else ""
            path.write_text(old + "\n" + texts["padstacks"], encoding="utf-8")
        folder = self.symbols / partition / "sym"
        folder.mkdir(parents=True, exist_ok=True)
        for name, text in symbols.items():
            (folder / f"{name}.1").write_text(text, encoding="utf-8")

    def export(self, params: dict[str, Any]) -> dict[str, Any]:
        wanted = params.get("partitions")
        files = []
        for path in sorted(self.cache.glob("*.hkp")):
            kind, _, partition = path.stem.partition("-")
            if kind not in (params.get("kinds") or ["parts", "cells", "padstacks"]):
                continue
            if kind != "padstacks" and wanted and partition not in wanted:
                continue
            files.append({"kind": kind, "partition": partition, "path": str(path), "cached": True})
        return {
            "project": params["project"],
            "library": str(self.folder / "Lib.lmc"),
            "root": str(self.folder),
            "symbols": str(self.symbols),
            "files": files,
            "failed": [],
            "exported": 0,
        }

    def absorb(self, params: dict[str, Any]) -> dict[str, Any]:
        self.merge(
            params["partition"],
            {k: params.get(k) or "" for k in ("parts", "cells", "padstacks")},
            params.get("symbols") or {},
        )
        return {
            "project": params["project"],
            "library": str(self.folder / "Lib.lmc"),
            "partition": params["partition"],
            "steps": [],
            "failed": [],
            "pdb_registered": True,
            "symbols_written": sorted(params.get("symbols") or {}),
            "symbols_registered": bool(params.get("symbols")),
            "cells_registered": [],
            "cells_missing": [],
            "ok": True,
        }
