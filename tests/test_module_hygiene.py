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

import pytest

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


def _uncollected_imports(relative, source, collected):
    """Only three fixed non-handler callpoints may rely on explicit collection."""
    from xpedition_cli.designer_frozen_binding import GENERATED_MODULE

    reviewed = {
        (
            "designer_frozen_binding.py",
            "frozen_owner_binding",
            "GENERATED_MODULE",
        ): GENERATED_MODULE,
        ("native_com_adapter.py", "_dispatch_designer_host_bootstrap", "comtypes"): "comtypes",
        (
            "native_com_adapter.py",
            "_dispatch_designer_host_bootstrap",
            "comtypes.client",
        ): "comtypes.client",
    }
    failures = []

    class Imports(ast.NodeVisitor):
        scope = "<module>"

        def visit_FunctionDef(self, node):
            previous, self.scope = self.scope, node.name
            self.generic_visit(node)
            self.scope = previous

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            dynamic = (
                isinstance(node.func, ast.Attribute) and node.func.attr == "import_module"
            ) or (isinstance(node.func, ast.Name) and node.func.id == "__import__")
            if dynamic:
                argument = node.args[0] if len(node.args) == 1 and not node.keywords else None
                name = (
                    argument.value
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
                    else argument.id
                    if isinstance(argument, ast.Name)
                    else None
                )
                required = reviewed.get((relative, self.scope, name))
                if required not in collected or not (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "importlib"
                ):
                    failures.append((self.scope, node.lineno, name))
            self.generic_visit(node)

    Imports().visit(ast.parse(source))
    return failures


def test_every_handler_is_imported_by_a_statement_the_freezer_can_see() -> None:
    """PyInstaller bundles the modules it sees imported. A handler module looked up by
    a computed name was left out of 1.0.1's binaries, which then ran no command."""
    import build
    from xpedition_cli.cli import registry

    for command in registry.commands():
        module_name, _, function_name = command.handler.partition(":")
        handler = getattr(registry._handler_module(module_name), function_name, None)
        assert callable(handler), command.path
    arguments = build.collection_arguments(windows=True, profile="onedir-owner")
    collected = {
        arguments[index + 1] for index, item in enumerate(arguments) if item == "--hidden-import"
    }
    for module in PACKAGE.rglob("*.py"):
        text = module.read_text(encoding="utf-8")
        assert not _uncollected_imports(module.relative_to(PACKAGE).as_posix(), text, collected), (
            module.relative_to(PACKAGE)
        )


@pytest.mark.parametrize(
    "source,collected",
    [
        ("def frozen_owner_binding():\n importlib.import_module(GENERATED_MODULE)", set()),
        ('def frozen_owner_binding():\n importlib.import_module("uncollected")', {"uncollected"}),
        (
            "def another():\n importlib.import_module(GENERATED_MODULE)",
            {"xpedition_owner_build_binding"},
        ),
        (
            'def frozen_owner_binding():\n __import__("xpedition_owner_build_binding")',
            {"xpedition_owner_build_binding"},
        ),
    ],
)
def test_fixed_import_exceptions_reject_missing_collection_or_changed_callpoint(source, collected):
    assert _uncollected_imports("designer_frozen_binding.py", source, collected)


def test_a_new_dynamic_handler_cannot_borrow_a_collected_owner_exception():
    source = 'def _handler_module():\n return importlib.import_module("comtypes")'
    assert _uncollected_imports("cli/registry.py", source, {"comtypes"})


def test_import_words_in_comments_or_documentation_are_not_executable_imports():
    assert _uncollected_imports("new.py", '# __import__("not executed")', set()) == []
