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
