"""`changelog` returns every entry of a release, however the release is laid out."""

from __future__ import annotations

import json
import re
from pathlib import Path

from xpedition_cli import main as cli
from xpedition_cli.cli.environment import changelog_changes

ROOT = Path(__file__).resolve().parent.parent


def test_a_repeated_heading_counts_every_time_and_an_entry_keeps_its_lines() -> None:
    body = """
### Added

- First feature, described
  over two lines.
- Second feature
  - with a nested point

### Changed

- One change

### Notes

- not a category, ignored

### Added

- Third feature, from a later section
"""
    changes = changelog_changes(body)
    assert changes["added"] == [
        "First feature, described over two lines.",
        "Second feature; with a nested point",
        "Third feature, from a later section",
    ]
    assert changes["changed"] == ["One change"]
    assert changes["fixed"] == [] and changes["security"] == []


def test_every_entry_of_the_1_0_0_release_is_returned(capsys) -> None:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    section = text[text.index("## [1.0.0]") :]
    following = re.search(r"^## \[", section[3:], re.MULTILINE)
    if following:  # the oldest release runs to the link references
        section = section[: following.start() + 3]
    written = sum(1 for line in section.splitlines() if line.startswith("- "))
    assert cli.main(["changelog", "--since", "0.9.0", "--compact"]) == 0
    entries = json.loads(capsys.readouterr().out)["data"]["entries"]
    release = next(entry for entry in entries if entry["version"] == "1.0.0")
    assert sum(len(items) for items in release["changes"].values()) == written


def test_a_wheel_reads_its_bundled_copies_and_never_the_working_directory(
    tmp_path, monkeypatch
) -> None:
    from xpedition_cli import resources

    package = tmp_path / "site-packages" / "xpedition_cli"
    (package / "_bundled").mkdir(parents=True)
    (package / "_bundled" / "contract.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(resources, "PACKAGE", package)
    monkeypatch.delattr("sys._MEIPASS", raising=False)
    assert resources.locate("contract/contract.json") == package / "_bundled" / "contract.json"
    # a CHANGELOG.md in the working directory belongs to some other project
    monkeypatch.chdir(tmp_path)
    (tmp_path / "CHANGELOG.md").write_text("## [9.9.9]\n", encoding="utf-8")
    assert resources.locate("CHANGELOG.md") is None
