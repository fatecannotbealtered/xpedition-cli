"""A command that pages declares it, and a command that declares paging pages.

`reference` is what an agent plans with: a list it can page without knowing is
fetched whole, and a `--limit` it was promised but that is ignored hands back
more than it asked for. Every read command runs here against a faked adapter.
"""

from __future__ import annotations

from xpedition_cli.cli.registry import commands


def _paged() -> list:
    return [
        command
        for command in commands()
        if not command.writes and any(param.name == "limit" for param in command.params)
    ]


def test_every_command_that_declares_paging_pages(cli, adapter, project, tmp_path) -> None:
    from fakes import FakeLibrary

    adapter.on("verify", {"scheme": "full", "findings": [], "logs": []})
    adapter.on("library_export", FakeLibrary(tmp_path / "Lib").export)
    checked = []
    for command in _paged():
        code, result = cli(*command.path.split(), "--project", str(project), "--limit", "1")
        assert code == 0, (command.path, result)
        data = result["data"]
        listed = data["items"] if "items" in data else data["findings"]
        assert len(listed) <= 1 and data["count"] == len(listed), command.path
        assert {"offset", "next_offset", "has_more"} <= set(data), command.path
        assert command.contract().get("default_sort"), command.path
        declared = {param.name for param in command.params}
        assert {"limit", "offset"} <= declared, command.path
        checked.append(command.path)
    # a vacuous pass would hide a broken registry
    assert set(checked) >= {
        "schematic components",
        "schematic nets",
        "schematic check",
        "bom export",
        "library list",
        "library check",
    }


def test_every_read_that_does_not_page_refuses_limit(cli, project) -> None:
    paged = {command.path for command in _paged()}
    for command in commands():
        if command.path in paged or command.writes:
            continue
        code, result = cli(*command.path.split(), "--limit", "1")
        assert code == 2 and result["error"]["code"] == "E_USAGE", command.path
