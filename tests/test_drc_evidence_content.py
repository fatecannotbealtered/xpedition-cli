"""DRC content and selected-input provenance, using an explicit native engine double."""

import copy
import hashlib
import os
from types import SimpleNamespace

import pytest
from test_drc_engine import report_files

from xpedition_cli import cell_refresh, native_identity
from xpedition_cli import drc_engine as D
from xpedition_cli import native_com_adapter as A


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "missing_category",
        "missing_pair",
        "changed_value",
        "changed_mode",
        "wrong_pair",
        "duplicate_category",
    ],
)
def test_effective_element_categories_bind_by_identity_and_disclose_additions(tmp_path, fault):
    paths = report_files(tmp_path / "PCB/test.pcb")
    header = '.FILETYPE BATCH_DRC_E2E_FILE\n.VERSION "2.02"\n'
    selected_text = (
        header
        + """.PROGRAMDEFINEDMATRIXENTRY "Traces"
..PDME "Part Pads" "127000" "DEFAULT"
..PDME "Route Border" "0" "DEFAULT"
.PROGRAMDEFINEDMATRIXENTRY "Part Pads"
..PDME "Route Border" "0" "DEFAULT"
"""
    )
    # Native 151 can add a new category and reorder both rows and entries.
    effective_text = (
        header
        + """.PROGRAMDEFINEDMATRIXENTRY "Bond Pads"
..PDME "Via Pads" "" "NONE"
.PROGRAMDEFINEDMATRIXENTRY "Part Pads"
..PDME "Route Border" "0" "DEFAULT"
.PROGRAMDEFINEDMATRIXENTRY "Traces"
..PDME "Route Border" "0" "DEFAULT"
..PDME "Part Pads" "127000" "DEFAULT"
"""
    )
    if fault == "missing_category":
        effective_text = effective_text.replace(
            '.PROGRAMDEFINEDMATRIXENTRY "Part Pads"\n..PDME "Route Border" "0" "DEFAULT"\n', ""
        )
    if fault == "missing_pair":
        effective_text = effective_text.replace('..PDME "Part Pads" "127000" "DEFAULT"\n', "")
    if fault == "changed_value":
        effective_text = effective_text.replace('"127000"', '"254000"')
    if fault == "changed_mode":
        effective_text = effective_text.replace('"127000" "DEFAULT"', '"127000" "NONE"')
    if fault == "wrong_pair":
        effective_text = effective_text.replace(
            '..PDME "Part Pads" "127000"', '..PDME "Via Pads" "127000"'
        )
    if fault == "duplicate_category":
        effective_text += (
            '.PROGRAMDEFINEDMATRIXENTRY "Traces"\n..PDME "Part Pads" "127000" "DEFAULT"\n'
        )
    selected = {}
    for kind in ["rules", "elements"]:
        source = tmp_path / ("selected-" + kind)
        source.write_bytes(paths["effective_" + kind].read_bytes())
        if kind == "elements":
            source.write_text(selected_text)
        selected[kind] = D.file_record(source)
    paths["effective_elements"].write_text(effective_text)
    if fault:
        with pytest.raises(ValueError):
            D.configuration_binding(selected, paths, 4)
    else:
        result = D.configuration_binding(selected, paths, 4)
        assert result["verified"]
        assert result["comparisons"]["elements"]["engine_added_categories"] == [
            {"name": "Bond Pads", "entries": [["Via Pads", "", "NONE"]]}
        ]


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "no_automatic_setup",
        "one_setup_missing",
        "old_setup",
        "wrong_setup",
        "wrong_output_directory",
        "save_reply_lost",
        "close_reply_lost",
    ],
)
def test_external_selected_profile_and_go_side_effective_configuration(
    tmp_path, monkeypatch, fault
):
    board = tmp_path / "PCB/test.pcb"
    board.parent.mkdir()
    board.write_bytes(b"synthetic")
    project = tmp_path / "test.prj"
    project.write_bytes(b"synthetic")
    paths = report_files(board)
    source = tmp_path / "explicit profiles"
    source.mkdir()
    selected = {}
    for kind in ("rules", "elements"):
        p = source / kind
        p.write_bytes(paths["effective_" + kind].read_bytes())
        selected[kind] = str(p)
    original_bytes = {k: (source / k).read_bytes() for k in selected}
    # Board holds a valid but unrelated older setup. It must never be consumed as
    # proof of the explicitly selected external profile.
    paths["effective_rules"].write_text(
        '.FILETYPE BATCH_DRC_FILE\n.VERSION "2.02"\n.UNROUTEDPINS NO\n.SINGLEPOINTNETS YES\n'
    )
    calls = []
    old_effective = {
        key: (paths[key].read_bytes(), paths[key].stat().st_mtime_ns)
        for key in ("effective_rules", "effective_elements")
    }

    class Engine:
        Errors = []

        def Go(self):
            calls.append("go")
            assert self.SetupFileName != selected["rules"]
            assert self.SetupElementsFileName != selected["elements"]
            assert open(self.SetupFileName, "rb").read() == original_bytes["rules"]
            assert open(self.SetupElementsFileName, "rb").read() == original_bytes["elements"]
            report_files(board)
            for kind in selected:
                if fault == "one_setup_missing" and kind == "elements":
                    paths["effective_elements"].unlink()
                    continue
                path = paths["effective_" + kind]
                path.write_bytes(original_bytes[kind])
                if fault == "old_setup":
                    os.utime(path, ns=(1_000_000_000, 1_000_000_000))
            if fault == "wrong_setup":
                paths["effective_rules"].write_text(
                    '.FILETYPE BATCH_DRC_FILE\n.VERSION "2.02"\n'
                    ".UNROUTEDPINS NO\n.SINGLEPOINTNETS YES\n"
                )
            if fault in {"no_automatic_setup", "wrong_output_directory"}:
                for key, (data, mtime) in old_effective.items():
                    paths[key].write_bytes(data)
                    os.utime(paths[key], ns=(mtime, mtime))
            raise RuntimeError("The DRC engine found 1 hazards. Browse Errors object for details.")

        def WriteSetupFile(self):
            pytest.fail("Hidden WriteSetupFile must never be dispatched")

    doc = SimpleNamespace(FullName=str(board))
    engine = Engine()
    client = SimpleNamespace(Dispatch=lambda progid: engine)

    def save_close(*args, lifecycle):
        calls.append("save_close")
        if fault == "save_reply_lost":
            lifecycle.update(save_count=1, close_count=0, save_reply_received=False)
            raise A.AdapterError("E_TIMEOUT", "synthetic Save reply lost")
        if fault == "close_reply_lost":
            lifecycle.update(
                save_count=1,
                close_count=1,
                save_acknowledged=True,
                closed_observed=True,
                close_reply_received=False,
            )
            raise A.AdapterError("E_VERIFY_INCOMPLETE", "synthetic Close reply lost")
        lifecycle.update(save_count=1, close_count=1, save_acknowledged=True, closed_observed=True)

    native = SimpleNamespace(
        AdapterError=A.AdapterError,
        _layout_board_path=lambda params: board,
        _application=lambda *a, **k: object(),
        _licensed_document=lambda app: doc,
        _save_close_board=save_close,
        _native_call=lambda label, fn: fn(),
    )
    monkeypatch.setattr(cell_refresh, "capture", lambda *a: {"model": {"layers": 4}})
    monkeypatch.setattr(
        native_identity, "application_identity", lambda *a: {"product_version": "151 fixture"}
    )
    monkeypatch.setattr(D, "registered_engine", lambda: "MGCPCBEngines.DRC.151")
    if fault:
        with pytest.raises(A.AdapterError) as caught:
            D.run({"project": str(project), **selected}, client, native)
        facts = caught.value.details["native_result"]
        if fault in {"save_reply_lost", "close_reply_lost"}:
            assert "native_go_dispatched" not in facts and calls == ["save_close"]
            assert facts["failure"]["stage"] == "save_close"
            assert facts["board_lifecycle"]["save_count"] == 1
            assert facts["persistence_state"] == (
                "unknown" if fault == "save_reply_lost" else "partial"
            )
            if fault == "close_reply_lost":
                assert facts["board_save_acknowledged"] and facts["board_close_observed"]
            assert original_bytes == {k: (source / k).read_bytes() for k in selected}
            return
        assert facts["native_go_dispatched"] and facts["native_go_returned"]
        assert "found 1 hazards" in facts["native_go_error"]
        assert facts["board_save_acknowledged"] and facts["board_close_observed"]
        assert facts["source"]["complete"] is False
    else:
        result = D.run({"project": str(project), **selected}, client, native)
        assert result["native_reports"]["configuration_binding"]["verified"]
        assert (
            result["native_reports"]["files"]["effective_rules"]["sha256"]
            == hashlib.sha256(original_bytes["rules"]).hexdigest()
        )
        assert result["native_reports"]["count"] == 1 and result["clean"] is False
        assert all(row["retained_copy"] for row in result["effective_configuration_before_go"])
        assert result["setup_export"] == "not_called_hidden_unsupported"
        assert result["native_stages"]["report_read"] == "complete"
        assert "found 1 hazards" in result["native_go_error"]
    assert calls == ["save_close", "go"]
    assert original_bytes == {k: (source / k).read_bytes() for k in selected}


@pytest.mark.parametrize(
    "fault", [None, "type", "object", "position", "object_position", "missing_object"]
)
def test_hazards_match_content_not_count(fault):
    records = [
        {
            "kind": "UNROUTED_NET",
            "position_mm": [20, 15],
            "objects": [
                {"refdes": "J1", "net": "QA_SIGNAL", "position_mm": [20, 15]},
            ],
        }
    ]
    observed = [
        {
            "kind": "PartialNets",
            "x": 20,
            "y": 15,
            "object_details": [
                {"refdes": "J1", "net": "QA_SIGNAL", "position_mm": [20, 15]},
            ],
        }
    ]
    observed = copy.deepcopy(observed)
    if fault == "type":
        observed[0]["kind"] = "Unrelated"
    elif fault == "object":
        observed[0]["object_details"][0]["refdes"] = "R1"
    elif fault == "position":
        observed[0]["x"] = 20.1
    elif fault == "object_position":
        observed[0]["object_details"][0]["position_mm"] = [21, 15]
    elif fault == "missing_object":
        observed[0]["object_details"] = []
    result = D.compare_hazards(records, observed)
    assert result["valid"] is (fault is None)
    assert "pin_number" in result["not_verified"]


def test_duplicate_hazards_cannot_reuse_one_observed_object():
    row = {
        "kind": "UNROUTED_NET",
        "position_mm": [20, 15],
        "objects": [
            {"refdes": "J1", "net": "QA_SIGNAL", "position_mm": [20, 15]},
        ],
    }
    hazard = {
        "kind": "PartialNets",
        "x": 20,
        "y": 15,
        "object_details": copy.deepcopy(row["objects"]),
    }
    assert not D.compare_hazards([row, row], [hazard])["valid"]
