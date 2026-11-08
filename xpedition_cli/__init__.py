"""xpedition-cli: lets an AI Agent operate Xpedition safely from the command line.

The driver runtime in ``_driver/`` does everything every driver tool shares. This package adds
the domain commands (``commands/``) and the adapter that talks to Xpedition (``adapter/``).
"""
from __future__ import annotations

import sys
from pathlib import Path

from ._driver import Tool
from .adapter import Adapter
from .commands import COMMANDS, STANDARD_MATURITY

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
    # A project is opened in Designer, a board in Layout; --kind of session open and doc create
    # takes only these (DCLI-210).
    doc_kinds=["project", "board"],
    # Designer takes about a minute to open or close a project, and Layout as long to start or
    # quit; the runtime runs these as long jobs in its own worker (DCLI-065). A new project's first
    # open builds its database. A save closes and opens the document again, so that Xpedition
    # writes it out and the read-back reads the file.
    long_commands=["session open", "session close", "doc create", "doc open", "doc save", "doc close"],
    maturity=STANDARD_MATURITY,
)


def main() -> None:
    TOOL.main()
