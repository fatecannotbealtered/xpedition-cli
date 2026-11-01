"""Observed 151.0 report/config conflict; synthetic input, no CAD mutation."""

import pytest
from test_drc_engine import report_files

from xpedition_cli import drc_engine as D


@pytest.mark.parametrize(
    "configured,reported",
    [("YES", "NO"), ("YES", "YES"), ("YES", None), ("YES", "duplicate"), ("NO", "YES")],
)
@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize(
    "rule,label",
    [
        ("PARTCOVERLAYER", "检查缺少的元件覆盖层焊盘"),
        ("VIACOVERLAYER", "检查缺少的通孔覆盖层焊盘"),
    ],
)
def test_cover_rule_configuration_never_substitutes_for_reported_execution(
    tmp_path, configured, reported, explicit, rule, label
):
    board = tmp_path / "PCB/test.pcb"
    paths = report_files(board)
    with paths["effective_rules"].open("a") as f:
        f.write(f".{rule} {configured}\n")
    lines = [] if reported is None else [reported]
    if reported == "duplicate":
        lines = ["YES", "NO"]
    paths["report"].write_text(
        "DRC\n===\n检查未布线的管脚 : YES\n"
        + "".join(f"{label} : {value}\n" for value in lines)
        + "Total DRC Hazards Found : 1\n",
        encoding="utf-8",
    )
    selected = {}
    for kind in ("rules", "elements"):
        p = tmp_path / kind
        text = paths["effective_" + kind].read_text()
        if kind == "rules" and not explicit:
            text = text.replace(f".{rule} {configured}\n", "")
        p.write_text(text)
        selected[kind] = D.file_record(p)
    result = D.read_reports(
        paths, {}, 0, selected=selected, layer_count=4, engine_version="MGCPCBEngines.DRC.151"
    )
    observation = result["rule_execution_verification"]
    consistent = reported == configured
    assert (rule in result["rules_checked"]) is (consistent and reported == "YES")
    assert (rule in observation["unverified_rules"]) is not consistent
    assert observation["selected_rules_verified"] is (consistent or not explicit)
    assert (
        next(r for r in observation["observations"] if r["rule"] == rule)["configured"]
        == configured
    )
    assert next(r for r in observation["observations"] if r["rule"] == rule)["reported"] == (
        reported if reported in {"YES", "NO"} else None
    )


def test_engine_added_switch_without_a_supported_report_marker_is_not_claimed(tmp_path):
    paths = report_files(tmp_path / "PCB/test.pcb")
    selected = {}
    for kind in ("rules", "elements"):
        path = tmp_path / kind
        path.write_bytes(paths["effective_" + kind].read_bytes())
        selected[kind] = D.file_record(path)
    with paths["effective_rules"].open("a") as stream:
        stream.write(".NEW_ENGINE_SWITCH YES\n")
    result = D.read_reports(
        paths, {}, 0, selected=selected, layer_count=4, engine_version="MGCPCBEngines.DRC.151"
    )
    assert "NEW_ENGINE_SWITCH" not in result["rules_checked"]
    assert "NEW_ENGINE_SWITCH" in result["rule_execution_verification"]["unverified_rules"]
    assert result["configuration_binding"]["verified"]
