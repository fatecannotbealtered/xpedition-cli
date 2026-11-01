"""Finite Save/Close output policies with only synthetic files; no CAD or journal."""

import copy
import hashlib
import json
import struct
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from xpedition_cli import component_save_policy as P
from xpedition_cli.cell_refresh import digest

HKP = b"""! Native Graphics Settings
! Created 10/19/26 10:54:52
    "Global.Option.IgnoreSelectionFilter" "0"
    "[VirtualGlobal].iDC.SplitViewHeight.0" "146"
    "[VirtualGlobal].iDC.SplitViewHeight.1" "782"
    "[VirtualGlobal].iDC.SplitViewHeight.2" "185"
    "Global.Option.DRC.ShowSevere" "0"
.view "Board"
    ..extrema
    (-10,-20)        ! (xmin, ymin)
    (60,43)         ! (xmax, ymax)
    ..WindowPlacement
    0x00000002      ! flags
    0x00000003      ! showCmd
    (-1, -1)        ! ptMinPosition
    (-9, -38)       ! ptMaxPosition
    (0, 0) (776,331) ! ..rcNormalPosition
    ..Pad_All_Enabled True
.generic_view "SPECIAL:Persistent3DViewData"
    ..extrema
    (0,0)          ! (xmin, ymin)
    (0,0)          ! (xmax, ymax)
    ..WindowPlacement
    0x00000000      ! flags
    0x00000006      ! showCmd
    (0, 0)          ! ptMinPosition
    (0, 1002104954)  ! ptMaxPosition
    (32765, 0) (0,-1871132048) ! ..rcNormalPosition
    ..Is_Float_View False
    ..View_Type 0
    ..View_Data ""
"""


def bmp():
    width, height, bits = 4, 3, 24
    size = ((width * bits + 31) // 32) * 4 * height
    return (
        b"BM"
        + struct.pack("<III", 54 + size, 0, 54)
        + struct.pack("<IiiHHIIiiII", 40, width, height, 1, bits, 0, size, 0, 0, 0, 0)
        + b"\x11" * size
    )


def pin(path):
    raw = path.read_bytes()
    return {"path": str(path), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def inputs(root, *, opaque_icdb=False, source_backup=None):
    rows = [pin(path) for path in sorted(root.rglob("*")) if path.is_file()]
    unavailable = []
    if opaque_icdb:
        rows = [row for row in rows if row["path"] != str(root / "database/icdb.dat")]
        unavailable = [
            {
                "path": str(root / "database/icdb.dat"),
                "live_read": "unavailable",
                "source_only_sha256": "e" * 64,
            }
        ]
    return {
        "project": str(root / "Board.prj"),
        "source_backup": copy.deepcopy(source_backup)
        if source_backup is not None
        else {"path": str(root.parent / "source.zip"), "sha256": "a" * 64},
        "live_files": rows,
        "live_unavailable": unavailable,
        "non_rule_observed_files": [],
        "live_file_count": len(rows) + len(unavailable),
        "non_rule_exclusion_policy": "synthetic original server journal policy",
        "limitations": ["live opaque bytes may be unknown"],
    }


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setattr(P.getpass, "getuser", lambda: "unit.user")
    root = tmp_path / "project"
    payloads = {
        "Board.prj": b"synthetic native project",
        "PCB/Board.pcb": b"synthetic existing descriptor",
        "database/icdb.dat": b"synthetic opaque DB",
        "Library/strict.lmc": b"protected library",
        "PCB/Work/unrelated.bin": b"protected unknown Work",
        "PCB/Config/other.user/UserSettings.upf": b"protected other user",
        "PCB/Config/Graphics Settings.hkp": HKP,
        "PCB/Config/unit.user/Graphics Settings.hkp": HKP,
        "PCB/Config/unit.user/UserSettings.upf": b"opaque current user preferences",
        "PCB/Config/ScreenShot.bmp": bmp(),
        "PCB/LogFiles/PCBLogFile.txt": b"Xpedition Layout Version: synthetic\r\nDesign loaded\r\n",
    }
    for name in ("LayoutDB.lyt", "LogicDB.lgc", "JobPrefsDB.jpf"):
        for directory in ("PCB/Layout", "PCB/Work/Layout_Temp"):
            payloads[directory + "/" + name] = ("opaque original " + name).encode()
    for relative, raw in payloads.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    original = inputs(root)
    declaration = P.declare(root / "PCB/Board.pcb", original, active_view_name="Board")
    before = P.observe(declaration, original)
    return SimpleNamespace(root=root, declaration=declaration, before=before)


def after(case, *, opaque_icdb=False):
    return P.observe(
        case.declaration,
        inputs(
            case.root, opaque_icdb=opaque_icdb, source_backup=getattr(case, "source_backup", None)
        ),
    )


def verify(case, phase="save", observed=None):
    return P.verify_phase(case.declaration, case.before, observed or after(case), phase=phase)


def change(case, relative, raw):
    (case.root / relative).write_bytes(raw)


@pytest.mark.parametrize("phase", ["save", "close"])
def test_only_prospective_exact_native_save_outputs_are_accepted(case, phase):
    for name in ("LayoutDB.lyt", "LogicDB.lgc", "JobPrefsDB.jpf"):
        for directory in ("PCB/Layout", "PCB/Work/Layout_Temp"):
            change(case, directory + "/" + name, ("opaque saved " + name).encode())
    change(case, "PCB/Config/unit.user/UserSettings.upf", b"opaque saved user preferences")
    changed_hkp = HKP.replace(b"10/19/26 10:54:52", b"10/22/26 10:06:18")
    changed_hkp = changed_hkp.replace(b'IgnoreSelectionFilter" "0"', b'IgnoreSelectionFilter" "1"')
    changed_hkp = changed_hkp.replace(b'SplitViewHeight.1" "782"', b'SplitViewHeight.1" "780"')
    changed_hkp = changed_hkp.replace(b"(-10,-20)", b"(-10,-86)")
    changed_hkp = changed_hkp.replace(b"(32765, 0) (0,-1871132048)", b"(0, -2) (3,2147483647)")
    change(case, "PCB/Config/Graphics Settings.hkp", changed_hkp)
    image = bytearray(bmp())
    image[-1] = 99
    change(case, "PCB/Config/ScreenShot.bmp", bytes(image))
    log = "Xpedition Layout Version: synthetic\r\n已保存设计\r\n".encode()
    change(case, "PCB/LogFiles/PCBLogFile.txt", log)
    observation = after(case)
    proof = verify(case, phase, observation)
    assert proof["valid"], proof
    assert observation["input_files_sha256"] == digest(observation["inputs"])
    assert any("opaque UPF/JPF/LGC" in item for item in proof["not_checked"])
    log_row = next(row for row in proof["files"] if row["policy"] == "save_log")
    assert log_row["log"]["before"]["text"].endswith("Design loaded\r\n")
    assert log_row["log"]["after"]["text"] == log.decode()
    assert log_row["engineering_safety_proven"] is False
    assert log_row["_untrusted"] == ["log.before.text", "log.after.text"]


def test_declaration_adds_only_existing_exact_paths_and_retains_original_three(case):
    original = {
        str(case.root / relative)
        for relative in (
            "PCB/Board.pcb",
            "PCB/Layout/LayoutDB.lyt",
            "PCB/Work/Layout_Temp/LayoutDB.lyt",
        )
    }
    declared = {row["path"] for row in case.declaration["files"]}
    added = {row["path"] for row in case.declaration["allowed_output_files"]}
    assert original <= declared and not original & added
    assert not any("other.user" in path for path in added)
    assert all(
        row["may_create"] is False and row["input_pin"]["path"] == row["path"]
        for row in case.declaration["files"]
    )


def test_hkp_requires_actual_view_identity_and_never_guesses_the_pcb_stem(case):
    with pytest.raises(ValueError, match="ActiveView.Name"):
        P.declare(case.root / "PCB/Board.pcb", inputs(case.root))
    for relative in (
        "PCB/Config/Graphics Settings.hkp",
        "PCB/Config/unit.user/Graphics Settings.hkp",
    ):
        change(case, relative, HKP.replace(b'.view "Board"', b'.view "Actual Native View"'))
    declaration = P.declare(
        case.root / "PCB/Board.pcb", inputs(case.root), active_view_name="Actual Native View"
    )
    observation = P.observe(declaration, inputs(case.root))
    assert P.verify_phase(declaration, observation, observation, phase="save")["valid"]
    with pytest.raises(ValueError, match="complete supported bound UI"):
        P.declare(case.root / "PCB/Board.pcb", inputs(case.root), active_view_name="Board")


@pytest.mark.parametrize("phase", ["transaction", "open", "continuation", "SAVE", ""])
def test_save_policy_cannot_be_reused_for_transaction_open_or_later_baselines(case, phase):
    assert not verify(case, phase)["valid"]


@pytest.mark.parametrize(
    "relative",
    [
        "Library/strict.lmc",
        "PCB/Work/unrelated.bin",
        "PCB/Config/other.user/UserSettings.upf",
        "PCB/Config/unknown.hkp",
        "PCB/Layout/NetPropsDB.npr",
        "PCB/Work/unknown.tmp",
    ],
)
def test_unknown_config_work_db_and_library_changes_or_creations_remain_protected(case, relative):
    (case.root / relative).parent.mkdir(parents=True, exist_ok=True)
    change(case, relative, b"unapproved replacement or new file")
    proof = verify(case)
    assert not proof["valid"] and "Unknown protected file" in proof["reason"]


def test_unknown_file_deletion_is_rejected(case):
    (case.root / "PCB/Work/unrelated.bin").unlink()
    assert not verify(case)["valid"]


def test_declared_file_deletion_cannot_be_observed_as_an_allowed_save(case):
    (case.root / "PCB/Config/ScreenShot.bmp").unlink()
    with pytest.raises(ValueError, match="disappeared"):
        after(case)


def test_three_db_pairs_must_agree_before_and_after_not_just_have_native_roles(case):
    change(case, "PCB/Layout/JobPrefsDB.jpf", b"only one mirror changed")
    assert not verify(case)["valid"]
    with pytest.raises(ValueError, match="mirrors differ"):
        P.declare(case.root / "PCB/Board.pcb", inputs(case.root), active_view_name="Board")


def test_optional_db_pair_must_be_both_absent_or_both_present(case):
    first = case.root / "PCB/Layout/LogicDB.lgc"
    second = case.root / "PCB/Work/Layout_Temp/LogicDB.lgc"
    first.unlink()
    with pytest.raises(ValueError, match="Both existing"):
        P.declare(case.root / "PCB/Board.pcb", inputs(case.root), active_view_name="Board")
    second.unlink()
    declaration = P.declare(
        case.root / "PCB/Board.pcb", inputs(case.root), active_view_name="Board"
    )
    assert not any(row["path"].endswith("LogicDB.lgc") for row in declaration["files"])


@pytest.mark.parametrize(
    "old,new",
    [
        (b'DRC.ShowSevere" "0"', b'DRC.ShowSevere" "1"'),
        (b'SplitViewHeight.0" "146"', b'SplitViewHeight.0" "145"'),
        (b"..Pad_All_Enabled True", b"..Pad_All_Enabled False"),
        (b'..View_Data ""', b'..View_Data "uninterpreted private content"'),
        (b"..Is_Float_View False", b"..Is_Float_View True"),
        (b"..View_Type 0", b"..View_Type 1"),
        (b"0x00000006      ! showCmd", b"0x00000006       ! showCmd"),
        (b"(0,0)          ! (xmin, ymin)", b"(1,0)          ! (xmin, ymin)"),
    ],
)
def test_hkp_ui_normalization_never_ignores_other_fields_or_whitespace(case, old, new):
    change(case, "PCB/Config/Graphics Settings.hkp", HKP.replace(old, new))
    proof = verify(case)
    assert not proof["valid"], proof


@pytest.mark.parametrize(
    "raw",
    [
        HKP + b'    "Global.Option.IgnoreSelectionFilter" "1"\n',
        HKP.replace(b"2147483647", b"2147483648").replace(b"1002104954", b"2147483648"),
        HKP.replace(b'IgnoreSelectionFilter" "0"', b'IgnoreSelectionFilter" "2"'),
        HKP.replace(b"10/19/26 10:54:52", b"99/99/26 10:54:52"),
        HKP.replace(b'    "[VirtualGlobal].iDC.SplitViewHeight.2" "185"\n', b""),
    ],
)
def test_hkp_ambiguous_missing_out_of_bounds_or_invalid_syntax_refuses(case, raw):
    change(case, "PCB/Config/Graphics Settings.hkp", raw)
    assert not verify(case)["valid"]


@pytest.mark.parametrize("fault", ["trailing", "compression", "width", "header", "magic"])
def test_bmp_must_be_a_real_fixed_bounded_thumbnail(case, fault):
    raw = bytearray(bmp())
    if fault == "trailing":
        raw += b"unaccounted trailer"
    elif fault == "compression":
        struct.pack_into("<I", raw, 30, 1)
    elif fault == "width":
        struct.pack_into("<i", raw, 18, 4097)
    elif fault == "header":
        struct.pack_into("<i", raw, 38, 1)
    else:
        raw[:2] = b"XX"
    change(case, "PCB/Config/ScreenShot.bmp", bytes(raw))
    assert not verify(case)["valid"]


@pytest.mark.parametrize("raw", [b"arbitrary renamed text", b"Xpedition Layout\x00not plain text"])
def test_native_log_role_does_not_accept_arbitrary_binary_or_unidentified_text(case, raw):
    change(case, "PCB/LogFiles/PCBLogFile.txt", raw)
    assert not verify(case)["valid"]


def test_actual_bytes_must_match_input_pins_before_they_can_be_retained(case):
    frozen = inputs(case.root)
    change(case, "PCB/Config/Graphics Settings.hkp", HKP + b"unexpected actual bytes")
    with pytest.raises(ValueError, match="Actual policy bytes differ"):
        P.observe(case.declaration, frozen)


def test_windows_path_and_handle_ctime_semantics_do_not_fake_content_drift(case, monkeypatch):
    real_fstat = P.os.fstat

    def handle_stat(fd):
        observed = real_fstat(fd)
        return SimpleNamespace(
            **{
                key: getattr(observed, key)
                for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns")
            },
            st_ctime_ns=123456789,
        )

    monkeypatch.setattr(P.os, "fstat", handle_stat)
    observed = after(case)
    assert verify(case, observed=observed)["valid"]


@pytest.mark.parametrize(
    "relative,raw",
    [
        ("PCB/Config/Graphics Settings.hkp", b"an unsupported config format"),
        ("PCB/Config/ScreenShot.bmp", b"not a bitmap"),
        ("PCB/LogFiles/PCBLogFile.txt", b"not an identified native log"),
    ],
)
def test_unsupported_original_ui_and_log_formats_refuse_the_plan_before_mutation(
    case, relative, raw
):
    change(case, relative, raw)
    with pytest.raises(ValueError):
        P.declare(case.root / "PCB/Board.pcb", inputs(case.root), active_view_name="Board")


def test_retained_raw_bytes_cannot_be_replaced_by_success_flags(case):
    observed = after(case)
    row = next(row for row in observed["files"] if "raw_hex" in row)
    row["raw_hex"] = b"manually verified".hex()
    observed["fingerprint"] = digest(
        {key: value for key, value in observed.items() if key != "fingerprint"}
    )
    proof = verify(case, observed=observed)
    assert not proof["valid"] and "Retained policy bytes" in proof["reason"]


def test_icdb_visibility_is_preserved_and_delegated_never_normalized(case):
    observed = after(case, opaque_icdb=True)
    raw_before, raw_after = copy.deepcopy(case.before), copy.deepcopy(observed)
    proof = verify(case, observed=observed)
    assert proof["valid"], proof
    assert proof["delegated_icdb_observations"][0]["verified_by_this_policy"] is False
    assert case.before == raw_before and observed == raw_after
    assert observed["inputs"]["live_unavailable"][0]["source_only_sha256"] == "e" * 64


def test_source_backup_cannot_be_replaced_during_save_or_close(case):
    observed = after(case)
    observed["inputs"]["source_backup"]["sha256"] = "b" * 64
    observed["input_files_sha256"] = digest(observed["inputs"])
    observed["fingerprint"] = digest(
        {key: value for key, value in observed.items() if key != "fingerprint"}
    )
    assert not verify(case, observed=observed)["valid"]


@pytest.mark.parametrize(
    "username", ["..", "../other", "domain\\user", "C:other", "CON", "name.", "two words"]
)
def test_username_cannot_escape_the_exact_current_os_user_path(case, monkeypatch, username):
    monkeypatch.setattr(P.getpass, "getuser", lambda: username)
    with pytest.raises(ValueError, match="OS username"):
        P.declare(case.root / "PCB/Board.pcb", inputs(case.root), active_view_name="Board")


def test_reader_is_pure_and_does_not_read_live_policy_bytes_or_current_identity(case, monkeypatch):
    observed = after(case)
    monkeypatch.setattr(P, "_raw", lambda *_: pytest.fail("reader cannot read live files"))
    monkeypatch.setattr(
        P.getpass, "getuser", lambda: pytest.fail("reader cannot query current username")
    )
    monkeypatch.setattr(
        Path, "stat", lambda *_args, **_kwargs: pytest.fail("reader cannot stat live files")
    )
    assert verify(case, observed=observed)["valid"]


def source_zip(root, archive):
    rows = {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }
    manifest = {
        "project": "Board.prj",
        "all_files": True,
        "skipped": [],
        "files": len(rows),
        "bytes": sum(map(len, rows.values())),
        "sha256": {name: hashlib.sha256(raw).hexdigest() for name, raw in rows.items()},
    }
    with zipfile.ZipFile(archive, "x") as bundle:
        for name, raw in rows.items():
            bundle.writestr(name, raw)
        bundle.writestr("xpedition-cli-backup.json", json.dumps(manifest))
    proof = {"archive": pin(archive), "manifest": manifest}
    observed = P.R._source_backup(str(archive), root / "Board.prj", root / "PCB/Board.pcb")
    return proof, observed


@pytest.fixture
def retained_forward_report(case, tmp_path):
    # Synthetic source report: no private engineering output is distributed.
    raw = b"synthetic forward annotation report\n"
    change(case, "PCB/LogFiles/ForwardAnnotation.txt", raw)
    source, archive = source_zip(case.root, tmp_path / "source-with-retained-report.zip")
    case.source_backup, case.source_proof = archive, source
    case.declaration = P.declare(
        case.root / "PCB/Board.pcb",
        inputs(case.root, source_backup=archive),
        active_view_name="Board",
        source_archive_proof=source,
    )
    case.before = after(case)
    return case


@pytest.mark.parametrize("phase", ["save", "close", "open", "continuation"])
def test_synthetic_nonempty_source_report_is_strict_input_not_marker_or_output(
    retained_forward_report, phase
):
    case = retained_forward_report
    path = str(case.root / "PCB/LogFiles/ForwardAnnotation.txt")
    (report,) = case.declaration["protected_source_reports"]
    assert report["path"] == path and report["input_pin"] == pin(Path(path))
    assert report["source_archive"] == case.source_proof["archive"]
    assert report["allowed_output"] is False
    for key in ("files", "allowed_output_files", "open_output_files", "runtime_markers"):
        assert path not in {row["path"] for row in case.declaration[key]}
    proof = P.verify_phase(case.declaration, case.before, after(case), phase=phase)
    assert proof["valid"], proof
    assert proof["protected_source_reports"][0]["unchanged"]


@pytest.mark.parametrize("phase", ["save", "close", "open", "continuation"])
@pytest.mark.parametrize("fault", ["rewrite", "delete"])
def test_retained_source_report_changes_never_gain_session_marker_permission(
    retained_forward_report, phase, fault
):
    case = retained_forward_report
    path = case.root / "PCB/LogFiles/ForwardAnnotation.txt"
    if fault == "rewrite":
        path.write_bytes(b"a replacement source report is not an allowed output")
    else:
        path.unlink()  # Synthetic fixture only; the product never deletes it.
    proof = P.verify_phase(case.declaration, case.before, after(case), phase=phase)
    assert not proof["valid"], proof


def test_retained_source_report_drift_already_refuses_declaration(retained_forward_report):
    case = retained_forward_report
    change(case, "PCB/LogFiles/ForwardAnnotation.txt", b"changed before preview")
    with pytest.raises(ValueError, match="differs from closed source"):
        P.declare(
            case.root / "PCB/Board.pcb",
            inputs(case.root, source_backup=case.source_backup),
            active_view_name="Board",
            source_archive_proof=case.source_proof,
        )


def test_retained_source_report_cannot_be_removed_from_declaration(retained_forward_report):
    case = retained_forward_report
    declaration = copy.deepcopy(case.declaration)
    declaration["protected_source_reports"] = []
    declaration["fingerprint"] = digest(
        {key: value for key, value in declaration.items() if key != "fingerprint"}
    )
    proof = P.verify_phase(declaration, case.before, case.before, phase="save")
    assert not proof["valid"], proof


def test_other_nonempty_source_report_does_not_inherit_an_exception(case, tmp_path):
    change(case, "PCB/LogFiles/PCBInfoFile.txt", b"another report stays unsupported")
    source, archive = source_zip(case.root, tmp_path / "other-existing-report.zip")
    with pytest.raises(ValueError, match="requires authenticated current closed-source absence"):
        P.declare(
            case.root / "PCB/Board.pcb",
            inputs(case.root, source_backup=archive),
            active_view_name="Board",
            source_archive_proof=source,
        )


def test_oversized_source_forward_report_refuses(case, tmp_path):
    change(case, "PCB/LogFiles/ForwardAnnotation.txt", b"x" * (P.MAX_LOG_BYTES + 1))
    source, archive = source_zip(case.root, tmp_path / "oversized-existing-report.zip")
    with pytest.raises(ValueError, match="differs from closed source"):
        P.declare(
            case.root / "PCB/Board.pcb",
            inputs(case.root, source_backup=archive),
            active_view_name="Board",
            source_archive_proof=source,
        )


def auto_block(board, *, closed=False, job=None):
    title = "自动保存日志已关闭" if closed else "自动保存日志文件"
    divider = "-" * (18 if closed else 16)
    return (
        "\r\n                                "
        + title
        + "\r\n                                "
        + divider
        + "\r\n\r\n                       10:32 AM Thursday, October 22, 2026\r\n"
        + "Job Name: "
        + str(job or board)
        + "\r\n\r\n"
    ).encode("cp936")


@pytest.fixture
def lifecycle(case, tmp_path):
    change(case, "PCB/LogFiles/PCBAutoSave.txt", b"")
    source, archive = source_zip(case.root, tmp_path / "current-closed-source.zip")
    for path, maximum in P._marker_paths(case.root / "PCB/Board.pcb", case.root):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"native session bytes" if maximum else b"")
    case.source_backup = archive
    case.source_proof = source
    case.declaration = P.declare(
        case.root / "PCB/Board.pcb",
        inputs(case.root, source_backup=archive),
        active_view_name="Board",
        source_archive_proof=source,
    )
    case.before = after(case)
    return case


def close_markers(case):
    # Only synthetic fixture setup deletes files. The product module never does.
    for marker in case.declaration["runtime_markers"]:
        Path(marker["path"]).unlink()
    return after(case)


def test_authenticated_exact_five_session_lifetimes_and_autosave_append(lifecycle):
    case = lifecycle
    assert len(case.declaration["runtime_markers"]) == 5
    assert all(row["policy"] == "session_marker" for row in case.declaration["runtime_markers"])
    added = {row["path"] for row in case.declaration["allowed_output_files"]}
    assert not added & {row["path"] for row in case.declaration["runtime_markers"]}
    block = auto_block(case.root / "PCB/Board.pcb") + auto_block(
        case.root / "PCB/Board.pcb", closed=True
    )
    change(case, "PCB/LogFiles/PCBAutoSave.txt", block)
    closed = close_markers(case)
    close_proof = P.verify_phase(case.declaration, case.before, closed, phase="close")
    assert close_proof["valid"], close_proof
    assert all(
        row["pin"] is None
        for row in closed["files"]
        if row["path"] in {item["path"] for item in case.declaration["runtime_markers"]}
    )
    for marker in case.declaration["runtime_markers"]:
        Path(marker["path"]).write_bytes(b"new native session" if marker["max_bytes"] else b"")
    reopened = after(case)
    proof = P.verify_phase(case.declaration, closed, reopened, phase="open")
    assert proof["valid"], proof
    rows = [row for row in proof["files"] if row["policy"] == "session_marker"]
    assert all(
        not row["presence_transition"]["before_present"]
        and row["presence_transition"]["after_present"]
        for row in rows
    )
    assert not any(row["presence_transition"]["tool_deletion_dispatched"] for row in rows)


@pytest.mark.parametrize("phase", ["save", "open"])
def test_marker_can_disappear_only_during_declared_close(lifecycle, phase):
    closed = close_markers(lifecycle)
    proof = P.verify_phase(lifecycle.declaration, lifecycle.before, closed, phase=phase)
    assert not proof["valid"], proof


@pytest.mark.parametrize("phase", ["save", "close", "open"])
def test_present_marker_cannot_be_silently_rewritten_in_a_phase(lifecycle, phase):
    marker = lifecycle.declaration["runtime_markers"][0]
    Path(marker["path"]).write_bytes(b"rewritten existing marker")
    proof = P.verify_phase(lifecycle.declaration, lifecycle.before, after(lifecycle), phase=phase)
    assert not proof["valid"], proof


def test_marker_exceeding_budget_or_nonempty_report_refuses_preview(lifecycle):
    case = lifecycle
    report = next(row for row in case.declaration["runtime_markers"] if row["max_bytes"] == 0)
    Path(report["path"]).write_bytes(b"unproved report content")
    with pytest.raises(ValueError, match="budget or empty-report"):
        P.declare(
            case.root / "PCB/Board.pcb",
            inputs(case.root, source_backup=case.source_backup),
            active_view_name="Board",
            source_archive_proof=case.source_proof,
        )
    Path(report["path"]).write_bytes(b"")
    marker = case.declaration["runtime_markers"][0]
    Path(marker["path"]).write_bytes(b"x" * (marker["max_bytes"] + 1))
    with pytest.raises(ValueError, match="budget or empty-report"):
        P.declare(
            case.root / "PCB/Board.pcb",
            inputs(case.root, source_backup=case.source_backup),
            active_view_name="Board",
            source_archive_proof=case.source_proof,
        )


def test_nonempty_report_recreation_fails_even_with_closed_absence(lifecycle):
    closed = close_markers(lifecycle)
    report = next(row for row in lifecycle.declaration["runtime_markers"] if row["max_bytes"] == 0)
    Path(report["path"]).write_bytes(b"new nonempty report")
    proof = P.verify_phase(lifecycle.declaration, closed, after(lifecycle), phase="open")
    assert not proof["valid"] and "empty-report" in proof["reason"]


@pytest.mark.parametrize("fault", ["flag", "manifest", "archive_pin", "source_contains_marker"])
def test_source_absence_is_read_from_the_actual_complete_authenticated_zip(
    lifecycle, tmp_path, fault
):
    case = lifecycle
    source = copy.deepcopy(case.source_proof)
    current = inputs(case.root, source_backup=case.source_backup)
    if fault == "flag":
        source["verified"] = True
    elif fault == "manifest":
        source["manifest"]["sha256"].pop("Library/strict.lmc")
        source["manifest"]["files"] -= 1
    elif fault == "archive_pin":
        source["archive"]["sha256"] = "0" * 64
    else:
        source, record = source_zip(case.root, tmp_path / "not-session-free.zip")
        current = inputs(case.root, source_backup=record)
    with pytest.raises(ValueError):
        P.declare(
            case.root / "PCB/Board.pcb",
            current,
            active_view_name="Board",
            source_archive_proof=source,
        )


def test_autosave_is_not_an_ordinary_xpedition_layout_log(lifecycle):
    change(lifecycle, "PCB/LogFiles/PCBAutoSave.txt", b"Xpedition Layout\r\nordinary log\r\n")
    assert not P.verify_phase(
        lifecycle.declaration, lifecycle.before, after(lifecycle), phase="close"
    )["valid"]


def test_synthetic_cp936_bytes_parse_and_only_job_name_is_retargeted_for_the_fixture(lifecycle):
    board = r"C:\ExampleProjects\PCB\Board.pcb"
    raw = auto_block(board) + auto_block(board, closed=True)
    parsed = P._autosave(raw)
    assert [row["title"] for row in parsed["entries"]] == ["自动保存日志文件", "自动保存日志已关闭"]
    original_job = parsed["entries"][0]["job_name"].encode("cp936")
    assert parsed["entries"][0]["job_name"] == parsed["entries"][1]["job_name"]
    retargeted = raw.replace(original_job, str(lifecycle.root / "PCB/Board.pcb").encode("cp936"))
    change(lifecycle, "PCB/LogFiles/PCBAutoSave.txt", retargeted)
    proof = P.verify_phase(lifecycle.declaration, lifecycle.before, after(lifecycle), phase="close")
    assert proof["valid"], proof


@pytest.mark.parametrize(
    "fault",
    ["foreign_job", "bad_title", "bad_date", "bad_weekday", "bare_lf", "plain_tail", "too_large"],
)
def test_autosave_append_requires_known_cp936_blocks_and_actual_job(lifecycle, fault):
    case = lifecycle
    board = case.root / "PCB/Board.pcb"
    raw = auto_block(board, closed=True)
    if fault == "foreign_job":
        raw = auto_block(board, closed=True, job=case.root / "PCB/Foreign.pcb")
    elif fault == "bad_title":
        raw = raw.replace("自动保存日志已关闭".encode("cp936"), "任意日志正文".encode("cp936"))
    elif fault == "bad_date":
        raw = raw.replace(b"October 22", b"October 99")
    elif fault == "bad_weekday":
        raw = raw.replace(b"Thursday", b"Monday")
    elif fault == "bare_lf":
        raw = raw.replace(b"\r\n", b"\n")
    elif fault == "plain_tail":
        raw += b"arbitrary tail\r\n"
    else:
        raw *= P.MAX_AUTOSAVE_TAIL_BYTES // len(raw) + 1
    change(case, "PCB/LogFiles/PCBAutoSave.txt", raw)
    proof = P.verify_phase(case.declaration, case.before, after(case), phase="close")
    assert not proof["valid"], proof


@pytest.mark.parametrize("phase", ["save", "open", "continuation"])
def test_autosave_cannot_append_outside_close(lifecycle, phase):
    change(
        lifecycle,
        "PCB/LogFiles/PCBAutoSave.txt",
        auto_block(lifecycle.root / "PCB/Board.pcb", closed=True),
    )
    assert not P.verify_phase(
        lifecycle.declaration, lifecycle.before, after(lifecycle), phase=phase
    )["valid"]


def test_autosave_keeps_actual_close_prefix_and_rejects_initial_nonempty_rewrite(case, tmp_path):
    old = auto_block(case.root / "PCB/Board.pcb")
    change(case, "PCB/LogFiles/PCBAutoSave.txt", old)
    source, record = source_zip(case.root, tmp_path / "nonempty-log-source.zip")
    current = inputs(case.root, source_backup=record)
    declaration = P.declare(
        case.root / "PCB/Board.pcb", current, active_view_name="Board", source_archive_proof=source
    )
    before = P.observe(declaration, current)
    change(
        case,
        "PCB/LogFiles/PCBAutoSave.txt",
        old.replace(b"10:32", b"10:33") + auto_block(case.root / "PCB/Board.pcb", closed=True),
    )
    after_observed = P.observe(declaration, inputs(case.root, source_backup=record))
    assert not P.verify_phase(declaration, before, after_observed, phase="close")["valid"]
    with pytest.raises(ValueError, match="equal historical source bytes or be exactly empty"):
        P.declare(
            case.root / "PCB/Board.pcb",
            inputs(case.root, source_backup=record),
            active_view_name="Board",
            source_archive_proof=source,
        )


def source_081_log():
    # Synthetic two-entry historical input; not native evidence.
    board = r"C:\ExampleProjects\PCB\Board.pcb"
    return auto_block(board) + auto_block(board, closed=True)


@pytest.fixture
def historical_autosave(case, tmp_path):
    historical = source_081_log()
    change(case, "PCB/LogFiles/PCBAutoSave.txt", historical)
    source, record = source_zip(case.root, tmp_path / "synthetic-closed-source.zip")
    original_archive = pin(Path(source["archive"]["path"]))
    change(case, "PCB/LogFiles/PCBAutoSave.txt", b"")
    case.source_backup, case.source_proof = record, source
    case.declaration = P.declare(
        case.root / "PCB/Board.pcb",
        inputs(case.root, source_backup=record),
        active_view_name="Board",
        source_archive_proof=source,
    )
    case.before = after(case)
    case.historical, case.original_archive = historical, original_archive
    return case


def test_synthetic_source_empty_initial_save_exact_close_append_open_empty(historical_autosave):
    case = historical_autosave
    opening = case.declaration["autosave_initial_open"]
    assert opening["transition"] == "observed_empty_after_open"
    assert opening["bytes_equal"] is False and opening["tool_reset_dispatched"] is False
    assert bytes.fromhex(opening["source"]["raw_hex"]) == case.historical
    assert len(opening["source_log"]["entries"]) == 2
    assert opening["current"]["bytes"] == 0
    assert all(
        Path(row["job_name"]) != case.root / "PCB/Board.pcb"
        for row in opening["source_log"]["entries"]
    )
    assert P.verify_phase(case.declaration, case.before, case.before, phase="save")["valid"]
    fresh = auto_block(case.root / "PCB/Board.pcb", closed=True)
    change(case, "PCB/LogFiles/PCBAutoSave.txt", fresh)
    closed = after(case)
    proof = P.verify_phase(case.declaration, case.before, closed, phase="close")
    assert proof["valid"], proof
    row = next(row for row in proof["files"] if row["policy"] == "autosave_log")
    assert row["log"]["transition"] == "appended_actual_before"
    change(case, "PCB/LogFiles/PCBAutoSave.txt", b"")
    reopened = after(case)
    proof = P.verify_phase(case.declaration, closed, reopened, phase="open")
    assert proof["valid"], proof
    row = next(row for row in proof["files"] if row["policy"] == "autosave_log")
    assert row["log"]["transition"] == "observed_empty_after_open"
    assert row["log"]["before"]["text"] == fresh.decode("cp936")
    assert row["log"]["after"]["text"] == ""
    assert case.declaration["autosave_source_prefix"]["raw_hex"] == case.historical.hex()
    assert pin(Path(case.source_proof["archive"]["path"])) == case.original_archive


def test_initial_autosave_also_accepts_the_exact_historical_source(historical_autosave):
    case = historical_autosave
    change(case, "PCB/LogFiles/PCBAutoSave.txt", case.historical)
    declaration = P.declare(
        case.root / "PCB/Board.pcb",
        inputs(case.root, source_backup=case.source_backup),
        active_view_name="Board",
        source_archive_proof=case.source_proof,
    )
    assert declaration["autosave_initial_open"]["transition"] == "source_equal"
    assert declaration["autosave_initial_open"]["bytes_equal"] is True


@pytest.mark.parametrize("raw_kind", ["valid_new_log", "nonempty_truncation", "old_time_rewrite"])
def test_initial_open_rejects_any_other_nonempty_source_log_change(historical_autosave, raw_kind):
    case = historical_autosave
    if raw_kind == "valid_new_log":
        raw = auto_block(case.root / "PCB/Board.pcb")
    elif raw_kind == "nonempty_truncation":
        raw = case.historical.split("自动保存日志已关闭".encode("cp936"))[0].rstrip(b" ")
    else:
        raw = case.historical.replace(b"10:47", b"10:48")
    change(case, "PCB/LogFiles/PCBAutoSave.txt", raw)
    with pytest.raises(ValueError, match="equal historical source bytes or be exactly empty"):
        P.declare(
            case.root / "PCB/Board.pcb",
            inputs(case.root, source_backup=case.source_backup),
            active_view_name="Board",
            source_archive_proof=case.source_proof,
        )


@pytest.mark.parametrize("phase", ["save", "continuation"])
def test_nonempty_actual_live_log_cannot_be_cleared_outside_open(historical_autosave, phase):
    case = historical_autosave
    change(case, "PCB/LogFiles/PCBAutoSave.txt", auto_block(case.root / "PCB/Board.pcb"))
    before = after(case)
    change(case, "PCB/LogFiles/PCBAutoSave.txt", b"")
    proof = P.verify_phase(case.declaration, before, after(case), phase=phase)
    assert not proof["valid"], proof


def test_open_cannot_rewrite_to_a_different_nonempty_log(historical_autosave):
    case = historical_autosave
    original = auto_block(case.root / "PCB/Board.pcb")
    change(case, "PCB/LogFiles/PCBAutoSave.txt", original)
    before = after(case)
    change(case, "PCB/LogFiles/PCBAutoSave.txt", original.replace(b"10:32", b"10:33"))
    proof = P.verify_phase(case.declaration, before, after(case), phase="open")
    assert not proof["valid"], proof


def test_close_preserves_actual_before_bytes_after_an_initial_empty_reset(historical_autosave):
    case = historical_autosave
    original = auto_block(case.root / "PCB/Board.pcb")
    change(case, "PCB/LogFiles/PCBAutoSave.txt", original)
    before = after(case)
    changed = original.replace(b"10:32", b"10:33") + auto_block(
        case.root / "PCB/Board.pcb", closed=True
    )
    change(case, "PCB/LogFiles/PCBAutoSave.txt", changed)
    proof = P.verify_phase(case.declaration, before, after(case), phase="close")
    assert not proof["valid"] and "actual before bytes" in proof["reason"]


def test_continuation_new_session_pin_needs_prior_closed_absence_and_current_source_zip(
    lifecycle, tmp_path
):
    case = lifecycle
    closed = close_markers(case)
    source, record = source_zip(case.root, tmp_path / "same-window-closed.zip")
    for marker in case.declaration["runtime_markers"]:
        Path(marker["path"]).write_bytes(
            b"new session in continuation" if marker["max_bytes"] else b""
        )
    current = P.observe(case.declaration, inputs(case.root, source_backup=record))
    missing = P.verify_phase(case.declaration, case.before, current, phase="continuation")
    assert not missing["valid"]
    proof = P.verify_phase(
        case.declaration,
        case.before,
        current,
        phase="continuation",
        closed_observation=closed,
        source_archive_proof=source,
    )
    assert proof["valid"], proof
    assert proof["source_archive_transition"]["raw_observations_normalized"] is False
    rejected = P.verify_phase(
        case.declaration,
        case.before,
        current,
        phase="continuation",
        closed_observation=case.before,
        source_archive_proof=source,
    )
    assert not rejected["valid"]


@pytest.mark.parametrize(
    "relative", ["PCB/Work/new.tmp", "database/cdbsvr/other.adr", "PCB/LogFiles/OtherInfo.txt"]
)
def test_lifecycle_allowance_never_covers_sibling_or_same_extension_files(lifecycle, relative):
    path = lifecycle.root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    assert not P.verify_phase(
        lifecycle.declaration, lifecycle.before, after(lifecycle), phase="close"
    )["valid"]


@pytest.fixture
def open_copies(lifecycle):
    case = lifecycle
    board = case.root / "PCB/Board.pcb"
    case.initial_source_bytes = {}
    for source, destination in P._backup_paths(board, "unit.user"):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(("old destination " + destination.name).encode())
        case.initial_source_bytes[str(source)] = source.read_bytes()
    case.declaration = P.declare(
        board,
        inputs(case.root, source_backup=case.source_backup),
        active_view_name="Board",
        source_archive_proof=case.source_proof,
    )
    case.before = after(case)
    for name in ("LayoutDB.lyt", "LogicDB.lgc", "JobPrefsDB.jpf"):
        for directory in ("PCB/Layout", "PCB/Work/Layout_Temp"):
            change(case, directory + "/" + name, ("closed source " + name).encode())
    change(
        case,
        "PCB/Config/Graphics Settings.hkp",
        HKP.replace(b"10/19/26 10:54:52", b"10/22/26 10:06:18"),
    )
    change(case, "PCB/Config/unit.user/UserSettings.upf", b"closed current user preferences")
    log = next(row for row in case.before["files"] if row["path"].endswith("PCBLogFile.txt"))
    case.initial_log = bytes.fromhex(log["raw_hex"])
    change(case, "PCB/LogFiles/PCBLogFile.txt", case.initial_log + b"Design saved\r\n")
    case.closed = after(case)
    proof = P.verify_phase(case.declaration, case.before, case.closed, phase="save")
    assert proof["valid"], proof
    return case


def rotate_open_copies(case):
    for mapping in case.declaration["open_backup_copies"]:
        Path(mapping["destination"]).write_bytes(Path(mapping["source"]).read_bytes())
    change(case, "PCB/LogFiles/PCBLogFile.txt", case.initial_log)
    return after(case)


def test_six_exact_103_mapping_paths_copy_only_this_closed_source_and_restore_initial_log(
    open_copies,
):
    case = open_copies
    mappings = case.declaration["open_backup_copies"]
    expected = [
        ("PCB/Config/Graphics Settings.hkp", "PCB/Config/Graphics Settings.bak"),
        ("PCB/Config/unit.user/UserSettings.upf", "PCB/Config/UserSettings.bak"),
        ("PCB/Layout/LayoutDB.lyt", "PCB/Work/BackupDatabase/LayoutDB.lyt"),
        ("PCB/Layout/LogicDB.lgc", "PCB/Work/BackupDatabase/LogicDB.lgc"),
        ("PCB/Layout/JobPrefsDB.jpf", "PCB/Work/BackupDatabase/JobPrefsDB.jpf"),
        ("PCB/Layout/JobPrefsDB.jpf", "PCB/Work/JobPrefsDB.bak"),
    ]
    assert [
        (
            Path(row["source"]).relative_to(case.root).as_posix(),
            Path(row["destination"]).relative_to(case.root).as_posix(),
        )
        for row in mappings
    ] == expected
    assert not {row["destination"] for row in mappings} & {
        row["path"] for row in case.declaration["allowed_output_files"]
    }
    original_closed = copy.deepcopy(case.closed)
    reopened = rotate_open_copies(case)
    proof = P.verify_phase(case.declaration, case.closed, reopened, phase="open")
    assert proof["valid"], proof
    copies = [row["copy_evidence"] for row in proof["files"] if row["policy"] == "open_backup_copy"]
    assert len(copies) == 6 and all(row["copy_required"] for row in copies)
    assert all(
        row["destination_equals_closed_source"] and row["source_unchanged"] for row in copies
    )
    assert all(row["engineering_safety_proven"] is False for row in copies)
    log = next(row for row in proof["files"] if row["policy"] == "save_log")
    assert log["log"]["transition"] == "restored_declaration_initial"
    assert log["log"]["before"]["text"].endswith("Design saved\r\n")
    assert log["log"]["after"]["text"] == case.initial_log.decode()
    assert case.closed == original_closed


def test_backup_destinations_may_remain_unchanged_at_open(open_copies):
    proof = P.verify_phase(
        open_copies.declaration, open_copies.closed, open_copies.closed, phase="open"
    )
    assert proof["valid"], proof
    assert not any(
        row["copy_evidence"]["copy_required"]
        for row in proof["files"]
        if row["policy"] == "open_backup_copy"
    )


@pytest.mark.parametrize("phase", ["save", "close", "continuation"])
def test_backup_copy_cannot_be_reused_outside_open(open_copies, phase):
    reopened = rotate_open_copies(open_copies)
    proof = P.verify_phase(open_copies.declaration, open_copies.closed, reopened, phase=phase)
    assert not proof["valid"], proof


@pytest.mark.parametrize("fault", ["wrong_source", "one_byte", "initial_source_instead_of_closed"])
def test_changed_backup_requires_exact_corresponding_closed_source(open_copies, fault):
    case = open_copies
    rotate_open_copies(case)
    mapping = case.declaration["open_backup_copies"][2]
    if fault == "wrong_source":
        raw = (case.root / "PCB/Layout/LogicDB.lgc").read_bytes()
    elif fault == "one_byte":
        raw = bytearray(Path(mapping["source"]).read_bytes())
        raw[-1] ^= 1
        raw = bytes(raw)
    else:
        raw = case.initial_source_bytes[mapping["source"]]
    Path(mapping["destination"]).write_bytes(raw)
    proof = P.verify_phase(case.declaration, case.closed, after(case), phase="open")
    assert not proof["valid"] and "unchanged closed-source" in proof["reason"]


def test_source_cannot_change_together_with_matching_new_backup(open_copies):
    case = open_copies
    rotate_open_copies(case)
    for directory in ("PCB/Layout", "PCB/Work/Layout_Temp"):
        change(case, directory + "/LayoutDB.lyt", b"source changed during Open")
    change(case, "PCB/Work/BackupDatabase/LayoutDB.lyt", b"source changed during Open")
    proof = P.verify_phase(case.declaration, case.closed, after(case), phase="open")
    assert not proof["valid"] and "unchanged closed-source" in proof["reason"]


@pytest.mark.parametrize(
    "relative", ["PCB/Work/BackupDatabase/PartsDB.pdb", "PCB/Config/Unknown.bak"]
)
def test_sibling_backup_or_bak_extension_is_never_an_output_permission(open_copies, relative):
    case = open_copies
    path = case.root / relative
    path.write_bytes(b"unapproved backup")
    assert not P.verify_phase(case.declaration, case.closed, after(case), phase="open")["valid"]


def test_absent_backup_destination_is_not_declared_or_created_by_the_policy(lifecycle):
    case = lifecycle
    destination = case.root / "PCB/Work/BackupDatabase/LayoutDB.lyt"
    assert not destination.exists()
    assert str(destination) not in {row["path"] for row in case.declaration["open_output_files"]}
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes((case.root / "PCB/Layout/LayoutDB.lyt").read_bytes())
    assert not P.verify_phase(case.declaration, case.before, after(case), phase="open")["valid"]


@pytest.mark.parametrize("target", ["source_path", "initial_log_raw", "after_raw"])
def test_copy_and_initial_log_proofs_reject_substituted_receipt_facts(open_copies, target):
    case = open_copies
    reopened = rotate_open_copies(case)
    declaration = copy.deepcopy(case.declaration)
    before = copy.deepcopy(case.closed)
    if target == "source_path":
        declaration["open_backup_copies"][2]["source"] = str(case.root / "PCB/Layout/LogicDB.lgc")
    elif target == "initial_log_raw":
        path = str(case.root / "PCB/LogFiles/PCBLogFile.txt")
        declaration["initial_save_logs"][path]["raw_hex"] = b"forged initial text".hex()
    else:
        row = next(row for row in reopened["files"] if row["path"].endswith("PCBLogFile.txt"))
        row["raw_hex"] = b"forged restored log".hex()
        reopened["fingerprint"] = digest(
            {key: value for key, value in reopened.items() if key != "fingerprint"}
        )
    declaration["fingerprint"] = digest(
        {key: value for key, value in declaration.items() if key != "fingerprint"}
    )
    if target != "after_raw":
        for observation in (before, reopened):
            observation["declaration_sha256"] = declaration["fingerprint"]
            observation["fingerprint"] = digest(
                {key: value for key, value in observation.items() if key != "fingerprint"}
            )
    assert not P.verify_phase(declaration, before, reopened, phase="open")["valid"]


def test_open_pcb_log_rejects_arbitrary_truncation_and_other_valid_text(open_copies):
    case = open_copies
    for raw in (case.initial_log[:-2], case.initial_log.replace(b"loaded", b"LOADED")):
        change(case, "PCB/LogFiles/PCBLogFile.txt", raw)
        proof = P.verify_phase(case.declaration, case.closed, after(case), phase="open")
        assert not proof["valid"], proof


@pytest.mark.skipif(P.os.name != "nt", reason="Actual 103 ANSI native PCBLog uses Windows mbcs")
def test_actual_103_pcb_log_64_79_64_chain_keeps_saved_and_initial_raw_facts(lifecycle):
    case = lifecycle
    initial = bytes.fromhex(
        "0d0a587065646974696f6e204c61796f75742056657273696f6e3a203132362e302e323336362e3638340d0a"
        "0d0ad2d1b3c9b9a6b6c1c8a1c9e8bcc6a1a30d0a"
    )
    saved = initial + bytes.fromhex("d2d1b1a3b4e6c9e8bcc62e2e2e0d0a")
    assert len(initial) == 64 and len(saved) == 79
    assert hashlib.sha256(initial).hexdigest() == (
        "b4d4d1c951d26f04b2418ed11c4955d15634d33d5e7dbdf875c4574036f8bc8f"
    )
    assert hashlib.sha256(saved).hexdigest() == (
        "e7433e8da06fe40eda755e034a3beeb7cfd26d11ec2236c5af0dec67b937e290"
    )
    change(case, "PCB/LogFiles/PCBLogFile.txt", initial)
    declaration = P.declare(
        case.root / "PCB/Board.pcb",
        inputs(case.root, source_backup=case.source_backup),
        active_view_name="Board",
        source_archive_proof=case.source_proof,
    )
    initial_observed = P.observe(declaration, inputs(case.root, source_backup=case.source_backup))
    change(case, "PCB/LogFiles/PCBLogFile.txt", saved)
    closed = P.observe(declaration, inputs(case.root, source_backup=case.source_backup))
    assert P.verify_phase(declaration, initial_observed, closed, phase="save")["valid"]
    change(case, "PCB/LogFiles/PCBLogFile.txt", initial)
    reopened = P.observe(declaration, inputs(case.root, source_backup=case.source_backup))
    proof = P.verify_phase(declaration, closed, reopened, phase="open")
    assert proof["valid"], proof
    row = next(row for row in proof["files"] if row["policy"] == "save_log")
    assert row["log"]["transition"] == "restored_declaration_initial"
    assert (
        bytes.fromhex(
            next(
                row["raw_hex"] for row in closed["files"] if row["path"].endswith("PCBLogFile.txt")
            )
        )
        == saved
    )


# Minimal ASCII excerpt retained from 183's actual adapter observations, not a vendor fixture.
GLOBAL_PLACEMENT_183 = (
    b".Global_Constants 8 2\r\n"
    b"    ..File_Type Graphics_Settings\r\n"
    b"    ..Version Version_9\r\n"
    b"    ..Num_Views 2\r\n"
    b"    ..Active_View 1\r\n"
    b"    ..WindowPlacement\r\n"
    b"\t0x00000000\t! flags\r\n"
    b"\t0x00000001\t! showCmd\r\n"
    b"\t(-1, -1)\t! ptMinPosition\r\n"
    b"\t(-1, -1)\t! ptMaxPosition\r\n"
    b"\t(16, 16) (2016,1216)\t! ..rcNormalPosition\r\n"
    b"    ..Active_Route_Layer 1\r\n"
)
GLOBAL_HKP_183 = HKP.replace(b'.view "Board"', GLOBAL_PLACEMENT_183 + b'.view "Board"')
GLOBAL_HKP_183_SAVED = GLOBAL_HKP_183.replace(
    b"\t0x00000001\t! showCmd", b"\t0x00000002\t! showCmd"
).replace(b"\t(-1, -1)\t! ptMinPosition", b"\t(-32000, -32000)\t! ptMinPosition")
GLOBAL_WINDOW_BLOCK_183 = (
    b"    ..WindowPlacement"
    + GLOBAL_PLACEMENT_183.split(b"    ..WindowPlacement", 1)[1].split(
        b"    ..Active_Route_Layer", 1
    )[0]
)


@pytest.fixture
def global_case(case):
    for relative in (
        "PCB/Config/Graphics Settings.hkp",
        "PCB/Config/unit.user/Graphics Settings.hkp",
    ):
        change(case, relative, GLOBAL_HKP_183)
    case.declaration = P.declare(
        case.root / "PCB/Board.pcb", inputs(case.root), active_view_name="Board"
    )
    case.before = after(case)
    return case


def test_preview_declares_the_finite_existing_global_window_placement_scope(global_case):
    case = global_case
    assert P.CONTRACT == "component-exact-save-close-files-v6"
    rows = [row for row in case.declaration["files"] if row["policy"] == "hkp_ui_fields"]
    assert len(rows) == 2
    for row in rows:
        scope = row["ui_field_scope"]
        assert scope["phases"] == ["save", "close"]
        assert scope["all_other_bytes"] == "exact"
        assert scope["active_view_name"] == "Board"
        global_scope = scope["global_window_placement"]
        assert global_scope["section_header"] == ".Global_Constants 8 2"
        assert global_scope["presence"] == (
            "one existing complete five-field block on both sides, or absent"
        )
        assert global_scope["flags"].startswith("uint32")
        assert global_scope["showCmd"].startswith("uint32")
        assert global_scope["rcNormalPosition"].startswith("four signed int32")


def test_observed_87_header_is_bound_per_file_and_keeps_all_other_bytes_strict(case):
    relative = "PCB/Config/Graphics Settings.hkp"
    original = GLOBAL_HKP_183.replace(b".Global_Constants 8 2", b".Global_Constants 87 2")
    change(case, relative, original)
    case.declaration = P.declare(
        case.root / "PCB/Board.pcb", inputs(case.root), active_view_name="Board"
    )
    case.before = after(case)
    row = next(row for row in case.declaration["files"] if row["path"] == str(case.root / relative))
    assert row["ui_field_scope"]["global_window_placement"]["section_header"] == (
        ".Global_Constants 87 2"
    )
    with pytest.raises(ValueError, match="Global_Constants"):
        P._hkp(original, "Board")  # An old 8/2 declaration must not accept the new header.
    change(case, relative, original.replace(b"0x00000001\t! showCmd", b"0x00000002\t! showCmd"))
    assert verify(case)["valid"]
    for changed in (
        original.replace(b"..Active_Route_Layer 1", b"..Active_Route_Layer 2"),
        original.replace(b".Global_Constants 87 2", b".Global_Constants 8 2"),
        original.replace(b".Global_Constants 87 2", b".Global_Constants 88 2"),
        original + b".Global_Constants 8 2\r\n",
    ):
        change(case, relative, changed)
        assert not verify(case)["valid"]


def test_unknown_global_header_never_enters_a_new_declaration(case):
    raw = GLOBAL_HKP_183.replace(b".Global_Constants 8 2", b".Global_Constants 88 2")
    change(case, "PCB/Config/Graphics Settings.hkp", raw)
    with pytest.raises(ValueError, match="Global_Constants"):
        P.declare(case.root / "PCB/Board.pcb", inputs(case.root), active_view_name="Board")


@pytest.mark.parametrize("phase", ["save", "close"])
@pytest.mark.parametrize(
    "relative", ["PCB/Config/Graphics Settings.hkp", "PCB/Config/unit.user/Graphics Settings.hkp"]
)
def test_actual_183_minimal_ui_delta_has_only_two_explicit_global_changes(
    global_case, phase, relative
):
    case = global_case
    change(case, relative, GLOBAL_HKP_183_SAVED)
    proof = verify(case, phase)
    assert proof["valid"], proof
    row = next(row for row in proof["files"] if row["path"] == str(case.root / relative))
    fields = row["ui_fields"]
    before = {item["field"]: item["values"] for item in fields["before"]}
    after_fields = {item["field"]: item["values"] for item in fields["after"]}
    assert {name for name in before if before[name] != after_fields[name]} == {
        "global.placement.showCmd",
        "global.placement.ptMinPosition",
    }
    assert before["global.placement.showCmd"] == ["0x00000001"]
    assert after_fields["global.placement.showCmd"] == ["0x00000002"]
    assert before["global.placement.ptMinPosition"] == ["-1", "-1"]
    assert after_fields["global.placement.ptMinPosition"] == ["-32000", "-32000"]
    assert row["engineering_safety_proven"] is False


@pytest.mark.parametrize(
    "old,new",
    [
        (b"0x00000000\t! flags", b"0xFFFFFFFF\t! flags"),
        (b"0x00000001\t! showCmd", b"0x80000000\t! showCmd"),
        (b"(-1, -1)\t! ptMinPosition", b"(-2147483648, 2147483647)\t! ptMinPosition"),
        (b"(-1, -1)\t! ptMaxPosition", b"(2147483647, -2147483648)\t! ptMaxPosition"),
        (
            b"(16, 16) (2016,1216)",
            b"(-2147483648, 2147483647) (2147483647,-2147483648)",
        ),
    ],
)
def test_global_ui_fields_use_uint32_and_signed_int32_without_geometry_rules(global_case, old, new):
    case = global_case
    change(case, "PCB/Config/Graphics Settings.hkp", GLOBAL_HKP_183.replace(old, new))
    assert verify(case)["valid"]


@pytest.mark.parametrize(
    "old,new",
    [
        (b"..Active_Route_Layer 1", b"..Active_Route_Layer 2"),
        (b"..Num_Views 2", b"..Num_Views 3"),
        (b"0x00000000\t! flags", b"0x00000000 \t! flags"),
        (b"(-1, -1)\t! ptMinPosition", b"(-1,-1)\t! ptMinPosition"),
        (b"\t! showCmd\r\n", b"\t! showCmd\n"),
        (b"0x00000000\t! flags", b"0x00000000\t! Flags"),
        (b"0x00000000\t! flags", b"-1\t! flags"),
        (b"0x00000001\t! showCmd", b"1\t! showCmd"),
        (b"0x00000001\t! showCmd", b"0X00000001\t! showCmd"),
        (b"0x00000001\t! showCmd", b"0x100000000\t! showCmd"),
        (b"(-1, -1)\t! ptMinPosition", b"(-2147483649, -1)\t! ptMinPosition"),
        (b"(-1, -1)\t! ptMinPosition", b"(-1, 2147483648)\t! ptMinPosition"),
        (b"(-1, -1)\t! ptMaxPosition", b"(2147483648, -1)\t! ptMaxPosition"),
        (b"(-1, -1)\t! ptMaxPosition", b"(-1, -2147483649)\t! ptMaxPosition"),
        (b"(16, 16) (2016,1216)", b"(-2147483649, 16) (2016,1216)"),
        (b"(16, 16) (2016,1216)", b"(16, 2147483648) (2016,1216)"),
        (b"(16, 16) (2016,1216)", b"(16, 16) (-2147483649,1216)"),
        (b"(16, 16) (2016,1216)", b"(16, 16) (2016,2147483648)"),
        (b"(-1, -1)\t! ptMinPosition", b"(-1, -1, 0)\t! ptMinPosition"),
        (b"..WindowPlacement\r\n", b"..WindowPlacement \r\n"),
        (b".Global_Constants 8 2", b".Global_Constants 8 3"),
        (b'.view "Board"', b'.view "Wrong actual view"'),
    ],
)
def test_global_placement_never_ignores_other_bytes_bad_syntax_or_out_of_range_values(
    global_case, old, new
):
    case = global_case
    change(case, "PCB/Config/Graphics Settings.hkp", GLOBAL_HKP_183.replace(old, new))
    assert not verify(case)["valid"]


@pytest.mark.parametrize(
    "raw",
    [
        GLOBAL_HKP_183 + b".Global_Constants 8 2\r\n",
        GLOBAL_HKP_183.replace(GLOBAL_PLACEMENT_183, GLOBAL_PLACEMENT_183 * 2),
        GLOBAL_HKP_183.replace(
            b"    ..Active_Route_Layer 1\r\n",
            GLOBAL_WINDOW_BLOCK_183 + b"    ..Active_Route_Layer 1\r\n",
        ),
        GLOBAL_HKP_183.replace(b"    ..Active_Route_Layer 1\r\n", b"\t0x00000000\t! flags\r\n"),
        GLOBAL_HKP_183.replace(b"\t0x00000000\t! flags\r\n", b""),
        GLOBAL_HKP_183.replace(
            b"\t0x00000000\t! flags\r\n\t0x00000001\t! showCmd\r\n",
            b"\t0x00000001\t! showCmd\r\n\t0x00000000\t! flags\r\n",
        ),
        GLOBAL_HKP_183.replace(
            b"    ..WindowPlacement\r\n", b".Unknown_Global_Scope\r\n    ..WindowPlacement\r\n"
        ),
        GLOBAL_HKP_183.replace(b".Global_Constants 8 2", b".Unknown_Global_Scope 8 2"),
        GLOBAL_HKP_183.replace(GLOBAL_PLACEMENT_183, b""),
    ],
)
def test_global_placement_cannot_be_added_removed_duplicated_reordered_or_moved(global_case, raw):
    case = global_case
    change(case, "PCB/Config/Graphics Settings.hkp", raw)
    assert not verify(case)["valid"]


def test_global_placement_cannot_be_newly_created_even_when_every_field_is_well_formed(case):
    change(case, "PCB/Config/Graphics Settings.hkp", GLOBAL_HKP_183)
    assert not verify(case)["valid"]


@pytest.mark.parametrize(
    "raw",
    [
        GLOBAL_HKP_183 + b".Global_Constants 8 2\r\n",
        GLOBAL_HKP_183.replace(b"\t0x00000000\t! flags\r\n", b""),
        GLOBAL_HKP_183.replace(b".Global_Constants 8 2", b" .Global_Constants 8 2"),
        HKP + GLOBAL_PLACEMENT_183.split(b"\t(-1, -1)\t! ptMaxPosition", 1)[0],
    ],
)
def test_original_ambiguous_or_incomplete_global_block_is_rejected_before_mutation(case, raw):
    change(case, "PCB/Config/Graphics Settings.hkp", raw)
    with pytest.raises(ValueError):
        P.declare(case.root / "PCB/Board.pcb", inputs(case.root), active_view_name="Board")


def test_unknown_top_level_section_cannot_inherit_allowed_active_view_placement_scope(case):
    raw = HKP.replace(b"    ..WindowPlacement\n", b".Unknown_View\n    ..WindowPlacement\n", 1)
    change(case, "PCB/Config/Graphics Settings.hkp", raw)
    with pytest.raises(ValueError, match="complete supported bound UI"):
        P.declare(case.root / "PCB/Board.pcb", inputs(case.root), active_view_name="Board")


@pytest.mark.parametrize("phase", ["open", "continuation"])
def test_global_placement_does_not_expand_save_only_hkp_to_open_or_continuation(lifecycle, phase):
    case = lifecycle
    change(case, "PCB/Config/Graphics Settings.hkp", GLOBAL_HKP_183)
    case.declaration = P.declare(
        case.root / "PCB/Board.pcb",
        inputs(case.root, source_backup=case.source_backup),
        active_view_name="Board",
        source_archive_proof=case.source_proof,
    )
    case.before = after(case)
    change(case, "PCB/Config/Graphics Settings.hkp", GLOBAL_HKP_183_SAVED)
    proof = verify(case, phase)
    assert not proof["valid"] and "Save-only" in proof["reason"]


def test_recomputed_receipt_fingerprint_cannot_broaden_declared_global_ui_scope(global_case):
    case = global_case
    declaration, observation = copy.deepcopy(case.declaration), copy.deepcopy(case.before)
    row = next(row for row in declaration["files"] if row["policy"] == "hkp_ui_fields")
    row["ui_field_scope"]["global_window_placement"]["flags"] = "any private field"
    declaration["fingerprint"] = digest(
        {key: value for key, value in declaration.items() if key != "fingerprint"}
    )
    observation["declaration_sha256"] = declaration["fingerprint"]
    observation["fingerprint"] = digest(
        {key: value for key, value in observation.items() if key != "fingerprint"}
    )
    proof = P.verify_phase(declaration, observation, observation, phase="save")
    assert not proof["valid"] and "exact original file declaration" in proof["reason"]
