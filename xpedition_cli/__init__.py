"""xpedition-cli: lets an AI Agent operate Xpedition safely from the command line.

The driver runtime in ``_driver/`` does everything every driver tool shares. This package adds
the domain commands (``commands/``) and the adapter that talks to Xpedition (``adapter/``).
"""
from __future__ import annotations

import sys
from pathlib import Path

from ._driver import Tool
from .adapter import Adapter
from .commands import COMMANDS

# package.json, CHANGELOG.md, knowledge/, skills/ and contract/ sit at the project root; in the
# native program they are bundled next to the code.
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))

TOOL = Tool(
    root=ROOT,
    adapter=Adapter,
    commands=COMMANDS,
    application="Xpedition",
    versions="2604",
    modes=["file", "session"],
    host_component=False,
)


def main() -> None:
    import os

    if os.environ.get("XPEDITION_CLI_WORKER"):
        # A long job's worker process: it runs the job, never the CLI (adapter/jobs.py).
        from .adapter.worker import main as worker

        worker()
        return
    TOOL.main()
