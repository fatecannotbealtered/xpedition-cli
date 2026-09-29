"""A module that defines one name twice keeps only the second, silently.

The adapter is a long module; 1.0.1 added `_release_project(params, client)` and
`_reopen_project(params, client)` for the backup commands under names two helpers
already had, and the draw's reopen and the board replacement called the new
functions with the old arguments until a live run fell over. No top-level name is
defined twice anywhere in the package.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "xpedition_cli"


def test_no_module_defines_a_top_level_name_twice() -> None:
    repeated = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = Counter(
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        )
        twice = sorted(name for name, count in names.items() if count > 1)
        if twice:
            repeated[str(path.relative_to(PACKAGE))] = twice
    assert repeated == {}


def test_every_handler_is_imported_by_a_statement_the_freezer_can_see() -> None:
    """PyInstaller bundles the modules it sees imported. A handler module looked up by
    a computed name was left out of 1.0.1's binaries, which then ran no command."""
    from xpedition_cli.cli import registry

    for command in registry.commands():
        module_name, _, function_name = command.handler.partition(":")
        handler = getattr(registry._handler_module(module_name), function_name, None)
        assert callable(handler), command.path
    for module in PACKAGE.rglob("*.py"):
        text = module.read_text(encoding="utf-8")
        assert "import_module(" not in text and "__import__(" not in text, module.name
