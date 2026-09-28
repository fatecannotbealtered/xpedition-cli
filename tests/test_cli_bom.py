"""bom export | check, from the schematic Designer holds."""

from __future__ import annotations

import copy
import json

from fakes import schematic_snapshot


def test_export_lists_one_row_a_part(cli, adapter, project) -> None:
    code, payload = cli("bom", "export", "--project", str(project))
    data = payload["data"]
    assert code == 0 and data["parts"] == 4 and data["grouped"] is False
    rows = {row["refdes"]: row for row in data["items"]}
    assert rows["C1"]["internal_part_no"] == "CAP-100N" and rows["C1"]["value"] == "100nF"
    assert "source" not in rows["C1"] and "revision" not in rows["C1"]
    assert adapter.last("snapshot")["domain"] == "schematic"


def test_export_groups_by_part_number(cli, adapter, project) -> None:
    _, payload = cli("bom", "export", "--project", str(project), "--group")
    groups = {row["part_number"]: row for row in payload["data"]["items"]}
    assert groups["RES-4K7"]["quantity"] == 2 and groups["RES-4K7"]["refdes"] == ["R1", "R2"]
    assert payload["data"]["grouped"] is True and payload["data"]["count"] == 3


def test_export_writes_rows_and_compares_with_a_baseline(cli, adapter, project, tmp_path) -> None:
    saved = tmp_path / "bom.json"
    code, payload = cli("bom", "export", "--project", str(project), "--output", str(saved))
    assert code == 0 and payload["data"]["path"] == str(saved)
    assert len(json.loads(saved.read_text(encoding="utf-8"))["rows"]) == 4
    code, payload = cli("bom", "export", "--project", str(project), "--output", str(saved))
    assert code == 6
    design = schematic_snapshot()
    design["components"][1]["value"] = "1uF"
    design["components"] = [part for part in design["components"] if part["refdes"] != "R2"]
    design["components"].append({**copy.deepcopy(design["components"][2]), "refdes": "R3"})
    adapter.on("snapshot", lambda params: copy.deepcopy(design))
    code, payload = cli("bom", "export", "--project", str(project), "--baseline", str(saved))
    changes = payload["data"]["changes"]
    assert code == 0 and changes["added"] == ["R3"] and changes["removed"] == ["R2"]
    assert changes["changed"] == [
        {"refdes": "C1", "fields": {"value": {"before": "100nF", "after": "1uF"}}}
    ]
    envelope = tmp_path / "envelope.json"
    envelope.write_text(
        json.dumps({"ok": True, "data": json.loads(saved.read_text(encoding="utf-8"))}), "utf-8"
    )
    code, payload = cli("bom", "export", "--project", str(project), "--baseline", str(envelope))
    assert code == 0 and payload["data"]["changes"]["removed"] == ["R2"]
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"items": []}), encoding="utf-8")
    code, payload = cli("bom", "export", "--project", str(project), "--baseline", str(wrong))
    assert code == 2 and payload["error"]["code"] == "E_VALIDATION"


def test_export_pages(cli, adapter, project) -> None:
    _, payload = cli("bom", "export", "--project", str(project), "--limit", "2")
    assert payload["data"]["count"] == 2 and payload["data"]["has_more"] is True


def test_check_passes_a_consistent_bom(cli, adapter, project) -> None:
    code, payload = cli("bom", "check", "--project", str(project))
    data = payload["data"]
    assert code == 0 and data["valid"] is True and data["issues"] == []
    assert data["parts"] == 4 and data["part_numbers"] == 3


def test_check_finds_missing_repeated_and_inconsistent_parts(cli, adapter, project) -> None:
    design = schematic_snapshot()
    design["components"][0]["internal_part_no"] = ""
    design["components"][3]["value"] = "10k"  # R2 shares R1's part number
    design["components"].append(copy.deepcopy(design["components"][1]))  # a second C1
    adapter.on("snapshot", lambda params: copy.deepcopy(design))
    _, payload = cli("bom", "check", "--project", str(project))
    kinds = {issue["kind"] for issue in payload["data"]["issues"]}
    assert payload["data"]["valid"] is False
    assert kinds == {"missing_part_number", "duplicate_refdes", "inconsistent_value"}
