"""Every DS id the planner emits is a checklist row that names that very issue.

An agent cites a check by its id, and a reviewer looks the id up in the
checklist of schematic-conventions.md. Two plan-time checks once reported under
DS-09 and DS-10, the ids of the refdes-prefix and part-number checks, so the
lookup described a different rule than the one that fired.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLANNER = ROOT / "xpedition_cli" / "schematic_layout.py"
SKILL = ROOT / "skills" / "xpedition-schematic" / "reference"


def checklist() -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    text = (SKILL / "schematic-conventions.md").read_text(encoding="utf-8")
    for line in text.splitlines():
        match = re.match(r"^\| (DS-\d\d) \|(.*)\|$", line)
        if match:
            assert match.group(1) not in rows, f"{match.group(1)} has two rows"
            rows[match.group(1)] = [cell.strip() for cell in match.group(2).split("|")]
    return rows


def emitted() -> set[str]:
    source = PLANNER.read_text(encoding="utf-8")
    return set(re.findall(r'"check": "(DS-\d\d)"', source))


def test_every_emitted_id_is_a_row_naming_its_own_dry_run_issue() -> None:
    rows = checklist()
    ids = emitted()
    assert {"DS-07", "DS-08", "DS-15", "DS-16"} <= ids
    for check in sorted(ids):
        assert check in rows, f"{check} is emitted but has no checklist row"
        how = rows[check][-1]
        assert "schematic draw --dry-run" in how and f"issue `{check}`" in how, (check, how)


def test_the_design_format_lists_every_plan_time_check() -> None:
    text = (SKILL / "schematic-design-format.md").read_text(encoding="utf-8")
    section = text[text.index("## 7. What the planner checks") :]
    section = section[: section.index("\n## ", 5)]
    for check in sorted(emitted()):
        assert f"- {check}:" in section, f"{check} is missing from the design format's §7"
