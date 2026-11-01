"""254's actual PROXIMITY record shape; synthetic files, no CAD or rerun.

The source DrcViolations.dat SHA256 was
ac4fded51981010af624cb278eae16f041a04aa26fa92736c4b3d1409fa727ea.
It contained16 PROXIMITY plus14 UNROUTED_NET records, matching the native30.
"""

import copy

import pytest
from test_drc_engine import report_files

from xpedition_cli import drc_engine as D

HEADER = '.FILETYPE DRCFILE\n.VERSION "2002"\n.DATE "October 18, 2026"\n.TIME "14:41:3"\n'
PROXIMITY = """.PROXIMITY
..VIO_XY (4230000, 29630000)
..CLEARANCE 0
..OBJECT_1
...OBJECT
....PAD PART
.....LYR 1
.....NET "A_IN"
.....NETCLASS "(Default)"
.....NAME "RECT1.7X1.7"
.....REF_DES "J1"
.....XY (5080000, 30480000)
..OBJECT_2
...OBJECT
....OUTLINE_OBJ PLACE
.....SIDE TOP
.....XY (3870000, 26840000)
.....HGT 3000000
.....UNDER_HGT 0
.....REF_DES "J1"
"""
UNROUTED = """.UNROUTED_NET
..XY (5080000, 12700000)
..OBJECT
...PAD PART
....REF_DES "J3"
....NET "B_IN"
....XY (5080000, 12700000)
"""


def read(tmp_path, records=PROXIMITY, count=1):
    paths = report_files(tmp_path / "PCB/owned.pcb")
    paths["violations"].write_text(HEADER + records, encoding="utf-8")
    paths["report"].write_text(
        f"DRC\n===\n检查未布线的管脚 : YES\nTotal DRC Hazards Found : {count}\n", encoding="utf-8"
    )
    return D.read_reports(paths, {}, 1, engine_version="MGCPCBEngines.DRC.151")


def test_actual_pair_schema_is_preserved_without_inventing_outline_net(tmp_path):
    result = read(tmp_path)
    assert result["count"] == 1 and result["kinds"] == ["PROXIMITY"]
    row = result["violations"][0]
    assert row["position_mm"] == [4.23, 29.63]
    pad, outline = row["objects"]
    assert (pad["kind"], pad["group"], pad["refdes"], pad["net"], pad["layer"]) == (
        "PAD",
        "OBJECT_1",
        "J1",
        "A_IN",
        1.0,
    )
    assert pad["position_mm"] == [5.08, 30.48]
    assert (outline["kind"], outline["group"], outline["refdes"], outline["side"]) == (
        "OUTLINE_OBJ",
        "OBJECT_2",
        "J1",
        "TOP",
    )
    assert outline["position_mm"] == [3.87, 26.84] and outline["net"] is None
    # Parsing a report is distinct from independent reopened native matching.
    native = D.compare_hazards(copy.deepcopy(result["violations"]), [])
    assert not native["valid"] and native["unmatched_records"][0]["reason"] == "content_mismatch"


def test_mixed_actual_record_families_are_counted_without_dropping_pairs(tmp_path):
    result = read(tmp_path, PROXIMITY + UNROUTED, count=2)
    assert result["count"] == 2
    assert result["kinds"] == ["PROXIMITY", "UNROUTED_NET"]
    assert [len(row["objects"]) for row in result["violations"]] == [2, 1]


@pytest.mark.parametrize(
    "fault",
    [
        "wrong_position_keyword",
        "missing_pair",
        "duplicate_group",
        "missing_wrapper",
        "missing_object_position",
        "missing_owner",
        "bad_position",
        "duplicate_position",
        "unknown_kind",
        "duplicate_object_xy",
        "duplicate_owner",
        "duplicate_net",
    ],
)
def test_incomplete_or_unknown_pair_shape_stays_incomplete(tmp_path, fault):
    records = PROXIMITY
    if fault == "wrong_position_keyword":
        records = records.replace("..VIO_XY", "..XY")
    elif fault == "missing_pair":
        records = records.split("..OBJECT_2")[0]
    elif fault == "duplicate_group":
        records = records.replace("..OBJECT_2", "..OBJECT_1")
    elif fault == "missing_wrapper":
        records = records.replace("..OBJECT_2\n...OBJECT", "..OBJECT_2\n...UNKNOWN")
    elif fault == "missing_object_position":
        records = records.replace(".....XY (3870000, 26840000)\n", "")
    elif fault == "missing_owner":
        records = records.rsplit('.....REF_DES "J1"', 1)[0]
    elif fault == "bad_position":
        records = records.replace("(4230000, 29630000)", "(4230000, 29630000, 1)")
    elif fault == "duplicate_position":
        records = records.replace(
            "..VIO_XY (4230000, 29630000)", "..VIO_XY (4230000, 29630000)\n..VIO_XY (1, 2)"
        )
    elif fault == "duplicate_object_xy":
        records = records.replace(
            ".....XY (5080000, 30480000)", ".....XY (5080000, 30480000)\n.....XY (1, 2)"
        )
    elif fault == "duplicate_owner":
        records = records.replace('.....REF_DES "J1"', '.....REF_DES "J1"\n.....REF_DES "J99"', 1)
    elif fault == "duplicate_net":
        records = records.replace('.....NET "A_IN"', '.....NET "A_IN"\n.....NET "wrong"', 1)
    else:
        records = records.replace(".PROXIMITY", ".UNKNOWN_HAZARD")
    with pytest.raises(ValueError, match="Unsupported or incomplete native violation"):
        read(tmp_path, records)


def test_count_mismatch_is_still_rejected(tmp_path):
    with pytest.raises(ValueError, match="record count disagree"):
        read(tmp_path, count=2)
