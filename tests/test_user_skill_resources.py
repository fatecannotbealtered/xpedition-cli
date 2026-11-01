"""Installed Skill resources stay operational; development records stay outside."""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["xpedition-cli", "xpedition-pcb", "xpedition-schematic"])
def test_installable_skill_has_guidance_and_no_private_history(name):
    root = ROOT / "skills" / name
    for path in root.rglob("*"):
        if path.is_file():
            assert not path.name.startswith("historical-")
            assert path.suffix not in {".log", ".xml", ".ndjson"}
    text = (root / "SKILL.md").read_text(encoding="utf-8")
    assert "STOP CHECKPOINT" in text
    prompts = json.loads((root / "test-prompts.json").read_text(encoding="utf-8"))
    assert len(prompts["scenarios"]) >= 3


def test_private_historical_skill_records_are_not_distributed():
    assert not (ROOT / "development/skill-history").exists()
    assert not (ROOT / "development/skill-evals").exists()
