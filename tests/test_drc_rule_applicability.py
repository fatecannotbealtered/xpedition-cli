"""Reduced synthetic form of the retained 151 Chinese plane execution conflict."""

import pytest

from xpedition_cli import drc_engine as D


def test_selected_plane_yes_with_reported_nonexecution_is_incomplete(tmp_path):
    selected = tmp_path / "selected.drc"
    selected.write_text(".FILETYPE BATCH_DRC_FILE\n.PLANESCLEARANCESANDRULES YES\n")
    observed = D._rule_execution(
        "平面安全间距和规则\n-------------------\n未执行接近度检查。\n",
        [{"name": "PLANESCLEARANCESANDRULES", "values": ["YES"]}],
        {"rules": D.file_record(selected)},
        engine_version="MGCPCBEngines.DRC.151",
    )
    assert observed["selected_rules_verified"] is False
    assert "PLANESCLEARANCESANDRULES" in observed["unverified_rules"]
    row = observed["observations"][0]
    assert row["reported"] == "NO"
    assert row["execution"]["status"] == "not_performed"
    assert row["applicability"]["status"] == "unverified"


@pytest.mark.parametrize(
    "version,marker",
    [
        (None, "检查未布线的管脚 : YES"),
        ("MGCPCBEngines.DRC.155", "检查未布线的管脚 : YES"),
        ("MGCPCBEngines.DRC.151", "Check unrouted pins : YES"),
        ("MGCPCBEngines.DRC.151", "检查未布线的管脚 : YES\n检查未布线的管脚 : YES"),
    ],
)
def test_unsupported_or_ambiguous_execution_profile_is_unverified(tmp_path, version, marker):
    selected = tmp_path / "rules"
    selected.write_text(".UNROUTEDPINS YES\n")
    result = D._rule_execution(
        marker,
        [{"name": "UNROUTEDPINS", "values": ["YES"]}],
        {"rules": D.file_record(selected)},
        engine_version=version,
    )
    assert result["selected_rules_verified"] is False
    assert result["observations"][0]["status"] == "unverified"
