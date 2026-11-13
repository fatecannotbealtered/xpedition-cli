"""Xpedition's project database server.

A project's database is served while ``database/cdbsvr/sAddress.adr`` exists in the project's
folder and ``iCDBNetServer.exe`` runs; a file left with no server running is stale. The server
goes on serving the project for up to a few seconds after the last program with it open lets
go (seen live after Designer quit and after JobWizard ended). A copy of the project taken
meanwhile holds a database that was "not closed or disconnected properly", which Layout
refuses to open until iCDB Project Backup repairs it. A call that closes a project therefore
waits for the server to let it go before it reports done.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from . import processes

RELEASE_WAIT_S = 15.0  # the server lets go within seconds; past this something else holds the project
POLL_S = 0.1
SERVED = ("database", "cdbsvr", "sAddress.adr")
SERVER = "iCDBNetServer.exe"


def project_folder(path: str | os.PathLike[str]) -> str | None:
    """The folder of the project a project file (``.prj``) or a board (``PCB/<name>.pcb``)
    belongs to, as a normalized path; None when it has no database folder."""
    file = Path(os.path.abspath(path))
    for folder in (file.parent, file.parent.parent):
        if (folder / "database").is_dir():
            return os.path.normcase(str(folder))
    return None


def served(folder: str) -> bool:
    return Path(folder, *SERVED).exists() and bool(processes.processes(SERVER))


def wait_released(folder: str, budget_s: float = RELEASE_WAIT_S) -> bool:
    """Whether the server let the project go within ``budget_s``."""
    deadline = time.monotonic() + budget_s
    while served(folder):
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_S)
    return True


def still_served(folder: str) -> dict[str, str]:
    """The notice for a project the server still serves when the wait ends."""
    return {"severity": "warning",
            "message": f"Xpedition's database server still serves the project in {folder} {RELEASE_WAIT_S:g} s after it was "
                       f"closed, so another program has it open. A copy of the project taken while "
                       f"{os.path.join(*SERVED)} exists is not closed properly, and Layout will not open the copy."}
