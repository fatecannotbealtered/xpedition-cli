"""Shared test setup.

Every test runs against its own configuration directory: the CLI keeps its
confirmation secret, consumed-token ledger, audit log, session state and
knowledge-base bindings in XPEDITION_CLI_CONFIG_DIR (by default ~/.xpedition-cli),
and a test that forgot to point it elsewhere wrote into the developer's real one.

`adapter` puts a FakeAdapter behind the native backend; `cli` runs a command and
returns its exit code and decoded envelope.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeAdapter  # noqa: E402

from xpedition_cli import main as entry  # noqa: E402
from xpedition_cli.backends import native_xpedition  # noqa: E402


@pytest.fixture(autouse=True)
def _own_config_dir(tmp_path_factory, monkeypatch) -> None:
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path_factory.mktemp("config")))


@pytest.fixture
def adapter(tmp_path, monkeypatch) -> FakeAdapter:
    """A ready native backend whose every adapter call a FakeAdapter answers."""
    binary = tmp_path / "native-adapter.exe"
    binary.write_bytes(b"placeholder")
    monkeypatch.setenv("XPEDITION_NATIVE_COMMAND", str(binary))
    fake = FakeAdapter()
    ready = {
        "backend": "native_xpedition",
        "available": True,
        "licensed": "unknown",
        "automation_command_configured": True,
        "automation_command_explicit": True,
        "automation_command": str(binary),
        "sdd_home": "C:/SDD_HOME",
        "com_registered": True,
        "designer_com_registered": True,
        "reason": None,
    }
    monkeypatch.setattr(native_xpedition.NativeBackend, "status", lambda self: dict(ready))
    monkeypatch.setattr(native_xpedition.subprocess, "run", fake.run)
    return fake


@pytest.fixture
def cli(capsys):
    """Run the CLI in-process: (exit code, the JSON envelope on stdout)."""

    def run(*argv: str) -> tuple[int, dict]:
        code = entry.main(list(argv))
        out = capsys.readouterr().out
        return code, json.loads(out) if out.strip() else {}

    return run


@pytest.fixture
def project(tmp_path) -> Path:
    """A minimal .prj with a schematic and a board design, no board file yet."""
    path = tmp_path / "Board.prj"
    path.write_text(
        "\n".join(
            [
                "SECTION DesignInfo",
                'KEY CentralLibrary "' + str(tmp_path / "Lib" / "Lib.lmc") + '"',
                'KEY DxD_Version "XPED2604"',
                "ENDSECTION",
                "SECTION iCDB",
                "LIST Designs",
                'VALUE "Schematic1"',
                'VALUE "Board1"',
                "ENDLIST",
                "ENDSECTION",
                "SECTION Schematic1",
                'KEY ConfigType "Board1"',
                "ENDSECTION",
                "SECTION Board1",
                "LIST Symbols",
                'VALUE "SymbolLibs\\Globals"',
                "ENDLIST",
                "LIST PDBs",
                'VALUE "PartsDBLibs\\PartQuest.pdb"',
                "ENDLIST",
                "LIST 2dCellLibraries",
                'VALUE "CellDBLibs\\PartQuest.cel"',
                "ENDLIST",
                'KEY ConfigType "PCB"',
                'KEY RootBlock "Schematic1"',
                'KEY PCBDesignPath ""',
                "ENDSECTION",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path
