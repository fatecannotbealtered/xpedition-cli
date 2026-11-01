"""Active annular field binding is explicit and raw, never guessed nm/mil equivalence."""

import pytest
from test_drc_engine import report_files

from xpedition_cli import drc_engine as D


def setup_binding(tmp_path, selected_rules, effective_rules):
    paths = report_files(tmp_path / "PCB/owned.pcb")
    selected = {}
    for kind in ("rules", "elements"):
        path = tmp_path / ("selected-" + kind)
        path.write_bytes(paths["effective_" + kind].read_bytes())
        if kind == "rules":
            path.write_text(selected_rules, encoding="utf-8")
        selected[kind] = D.file_record(path)
    paths["effective_rules"].write_text(effective_rules, encoding="utf-8")
    return selected, paths


HEADER = '.FILETYPE BATCH_DRC_FILE\n.VERSION "2.02"\n.BASE_UNITS 0\n'
ACTIVE = '.MINIMUMANNULARRING YES\n.MINIMUMANNULARRINGVALUE "124460"\n'


def test_same_explicit_value_unit_and_version_bind_without_unit_inference(tmp_path):
    selected, paths = setup_binding(tmp_path, HEADER + ACTIVE, HEADER + ACTIVE)
    result = D.configuration_binding(selected, paths, 4)
    assert result["verified"]
    assert result["comparisons"]["rules"]["not_verified"] == []


@pytest.mark.parametrize("actual", ["4.9", "124461", "0.12446"])
def test_numerical_nm_mil_coincidence_is_not_a_serialization_contract(tmp_path, actual):
    selected, paths = setup_binding(
        tmp_path, HEADER + ACTIVE, (HEADER + ACTIVE).replace("124460", actual)
    )
    with pytest.raises(ValueError, match="do not match selected settings"):
        D.configuration_binding(selected, paths, 4)


@pytest.mark.parametrize("side", ["selected", "effective", "both"])
@pytest.mark.parametrize(
    "fault",
    [
        "missing_unit",
        "duplicate_unit",
        "duplicate_value",
        "duplicate_enable",
        "missing_value",
        "nan",
        "negative",
    ],
)
def test_ambiguous_active_annular_context_refuses_even_identical_files(tmp_path, side, fault):
    text = HEADER + ACTIVE
    broken = text
    if fault == "missing_unit":
        broken = text.replace(".BASE_UNITS 0\n", "")
    elif fault == "duplicate_unit":
        broken = text + ".BASE_UNITS 0\n"
    elif fault == "duplicate_value":
        broken = text + '.MINIMUMANNULARRINGVALUE "124460"\n'
    elif fault == "duplicate_enable":
        broken = text + ".MINIMUMANNULARRING YES\n"
    elif fault == "missing_value":
        broken = text.replace('.MINIMUMANNULARRINGVALUE "124460"\n', "")
    elif fault == "nan":
        broken = text.replace("124460", "nan")
    elif fault == "negative":
        broken = text.replace("124460", "-1")
    selected, paths = setup_binding(
        tmp_path,
        broken if side in {"selected", "both"} else text,
        broken if side in {"effective", "both"} else text,
    )
    with pytest.raises(ValueError, match="annular"):
        D.configuration_binding(selected, paths, 4)


def test_other_numeric_fields_are_not_converted(tmp_path):
    selected, paths = setup_binding(
        tmp_path,
        HEADER + ACTIVE + ".OTHER_NUMERIC 124460\n",
        HEADER + ACTIVE + ".OTHER_NUMERIC 4.9\n",
    )
    with pytest.raises(ValueError, match="OTHER_NUMERIC"):
        D.configuration_binding(selected, paths, 4)


def test_disabled_annular_legacy_value_is_retained_as_unverified(tmp_path):
    selected, paths = setup_binding(
        tmp_path,
        (HEADER + ACTIVE).replace("YES", "NO"),
        (HEADER + ACTIVE).replace("YES", "NO").replace("124460", "4.9"),
    )
    result = D.configuration_binding(selected, paths, 4)
    assert result["verified"]
    assert result["comparisons"]["rules"]["not_verified"][0]["field"] == ["MINIMUMANNULARRINGVALUE"]
