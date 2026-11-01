"""Closed recovery archives use synthetic owned files, never CAD or a live DB."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from xpedition_cli import component_closed_recovery as R
from xpedition_cli import project_backup as B
from xpedition_cli.backends import NativeBackend
from xpedition_cli.cli import project as project_cli


@pytest.fixture(autouse=True)
def no_native(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Closed recovery must not call native applications or the backup handler")

    monkeypatch.setattr(NativeBackend, "invoke", forbidden)
    monkeypatch.setattr(NativeBackend, "status", forbidden)
    monkeypatch.setattr(project_cli, "backup", forbidden)


@pytest.fixture
def owned(tmp_path):
    root = tmp_path / "project"
    files = {
        "Board.prj": b"synthetic initialized project",
        "PCB/Board.pcb": b"synthetic board",
        "database/icdb.dat": b"synthetic-database",
        "Library/Library.lmc": b"closed library",
        "Work/retain.tmp": b"work is required",
        "PCB/LogFiles/retain.txt": b"logs are required",
        "ProjectBackup/retain.zip": b"old backup is required",
        "previous.bak": b"backup is required",
    }
    for name, raw in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return root / "Board.prj", root / "PCB/Board.pcb", tmp_path / "recovery.zip", files


def _sealed(owned):
    project, board, archive, _ = owned
    return R.seal(R.plan_recovery(archive, project, board), R.observe_closed(project, board))


def _rewrite(archive, mutate, *, compression=zipfile.ZIP_DEFLATED):
    with zipfile.ZipFile(archive) as bundle:
        payload = {name: bundle.read(name) for name in bundle.namelist()}
    mutate(payload)
    with zipfile.ZipFile(archive, "w", compression) as bundle:
        for name, raw in payload.items():
            bundle.writestr(name, raw)


def _refingerprint(value):
    value["fingerprint"] = R._digest(
        {key: row for key, row in value.items() if key != "fingerprint"}
    )


def test_plan_observation_and_complete_seal_have_one_declared_output(owned):
    project, board, archive, original = owned
    plan = R.plan_recovery(archive, project, board)
    closed = R.observe_closed(project, board)
    assert not archive.exists()
    assert R.verify_plan(plan) == plan
    assert set(closed["file_manifest"]) == set(original)
    assert closed["file_count"] == len(original)
    assert closed["total_bytes"] == sum(map(len, original.values()))
    assert closed["observation"] == {"passes": 2, "identical": True, "atomic_snapshot": False}
    assert all(set(pin) == {"path", "bytes", "sha256"} for pin in closed["files"])

    evidence = R.seal(plan, closed)
    proof = R.verify_archive(evidence)
    assert proof["valid"] is True and proof["complete"] is True and proof["crc_verified"] is True
    assert proof["live_project_read"] is False
    assert evidence["created"] is True and evidence["complete"] is True
    assert evidence["verified"] is True and evidence["atomic_snapshot"] is False
    assert evidence["closed_tree"] == closed
    assert evidence["archive"]["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert evidence["archive_members"] == proof["archive_members"]
    assert set(proof["archive_members"]) == {*original, B.MANIFEST}
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read(B.MANIFEST))
        assert bundle.testzip() is None
        assert manifest == evidence["manifest"]
        assert manifest["all_files"] is True and manifest["skipped"] == []
        assert manifest["files"] == len(original)
        assert manifest["atomic_snapshot"] is False
        for name, raw in original.items():
            assert bundle.read(name) == raw
            assert (project.parent / name).read_bytes() == raw
    assert set(archive.parent.iterdir()) == {project.parent, archive}
    with pytest.raises(R.RecoveryError):
        R.seal(plan, closed)
    assert R.verify_archive(evidence)["valid"] is True


def test_all_file_hashing_and_archive_writes_stream_without_read_bytes(owned, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Project and ZIP hashing must stream rather than materialize files")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    evidence = _sealed(owned)
    assert R.verify_archive(evidence)["valid"] is True


def _stat_with(info, **changes):
    return SimpleNamespace(
        **{key: changes.get(key, getattr(info, key)) for key in dir(info) if key.startswith("st_")}
    )


def test_windows_path_and_handle_ctime_semantics_do_not_refuse_unchanged_files(owned, monkeypatch):
    original = R.os.fstat

    def windows_handle_info(fd):
        info = original(fd)
        return _stat_with(info, st_ctime_ns=info.st_ctime_ns + 1234567)

    monkeypatch.setattr(R.os, "fstat", windows_handle_info)
    evidence = _sealed(owned)
    assert R.verify_archive(evidence)["valid"] is True


@pytest.mark.parametrize("api", ["path", "handle"])
def test_streaming_still_refuses_ctime_changes_within_each_metadata_api(owned, monkeypatch, api):
    project, _, _, _ = owned
    pin = R._pin(project)
    calls = []
    if api == "handle":
        original = R.os.fstat

        def changed_handle(fd):
            info = original(fd)
            calls.append(fd)
            return _stat_with(info, st_ctime_ns=info.st_ctime_ns + len(calls))

        monkeypatch.setattr(R.os, "fstat", changed_handle)
    else:
        original = Path.stat

        def changed_path(path, *args, **kwargs):
            info = original(path, *args, **kwargs)
            if path == project:
                calls.append(path)
                return _stat_with(info, st_ctime_ns=info.st_ctime_ns + len(calls))
            return info

        monkeypatch.setattr(Path, "stat", changed_path)
    with zipfile.ZipFile(io.BytesIO(), "w") as bundle:
        with pytest.raises(ValueError, match="differ from the complete closed observation"):
            R._stream_member(bundle, project.name, pin)


@pytest.mark.parametrize("field", ["st_ino", "st_size", "st_mtime_ns"])
def test_cross_api_file_identity_differences_are_still_refused(owned, monkeypatch, field):
    project, _, _, _ = owned
    pin, original = R._pin(project), R.os.fstat

    def changed_identity(fd):
        info = original(fd)
        return _stat_with(info, **{field: getattr(info, field) + 1})

    monkeypatch.setattr(R.os, "fstat", changed_identity)
    with zipfile.ZipFile(io.BytesIO(), "w") as bundle:
        with pytest.raises(ValueError, match="changed before its read handle opened"):
            R._stream_member(bundle, project.name, pin)


@pytest.mark.parametrize(
    "fault", ["relative", "suffix", "missing_parent", "protected", "input", "existing"]
)
def test_output_plan_refuses_invalid_or_overlapping_paths_without_creating_files(owned, fault):
    project, board, archive, _ = owned
    kwargs = {}
    if fault == "relative":
        archive = Path("relative.zip")
    elif fault == "suffix":
        archive = archive.with_suffix(".json")
    elif fault == "missing_parent":
        archive = archive.parent / "missing/output.zip"
    elif fault == "protected":
        archive = project.parent / "nested/output.zip"
    elif fault == "input":
        kwargs["input_paths"] = [archive]
    else:
        archive.write_bytes(b"do not replace")
    with pytest.raises(R.RecoveryError):
        R.plan_recovery(archive, project, board, **kwargs)
    if fault == "existing":
        assert archive.read_bytes() == b"do not replace"
    elif archive.is_absolute():
        assert not archive.exists()


def test_output_cannot_enter_another_declared_protected_tree(owned):
    project, board, archive, _ = owned
    protected = archive.parent / "another-source"
    protected.mkdir()
    with pytest.raises(R.RecoveryError):
        R.plan_recovery(protected / "output.zip", project, board, protected_roots=[protected])
    assert not list(protected.iterdir())


def test_broken_symlink_output_is_not_treated_as_absent(owned):
    project, board, archive, _ = owned
    try:
        archive.symlink_to(archive.parent / "missing-target")
    except OSError:
        pytest.skip("Creating a symlink requires unavailable Windows privileges")
    with pytest.raises(R.RecoveryError):
        R.plan_recovery(archive, project, board)
    assert archive.is_symlink()


def test_reparse_output_ancestor_is_refused(owned):
    project, board, archive, _ = owned
    link = archive.parent / "linked"
    try:
        link.symlink_to(archive.parent, target_is_directory=True)
    except OSError:
        pytest.skip("Creating a symlink requires unavailable Windows privileges")
    with pytest.raises(R.RecoveryError):
        R.plan_recovery(link / "output.zip", project, board)
    assert not archive.exists()


@pytest.mark.parametrize("fault", ["occupied", "parent_replaced", "plan_changed"])
def test_frozen_plan_is_rechecked_before_any_archive_write(owned, fault):
    project, board, archive, _ = owned
    folder = archive.parent / "output-parent"
    folder.mkdir()
    archive = folder / archive.name
    plan = R.plan_recovery(archive, project, board)
    closed = R.observe_closed(project, board)
    if fault == "occupied":
        archive.write_bytes(b"unrelated existing output")
    elif fault == "parent_replaced":
        folder.rename(folder.with_name("retained-old-parent"))
        folder.mkdir()
    else:
        plan["archive_path"] = str(archive.with_name("other.zip"))
    with pytest.raises(R.RecoveryError):
        R.verify_plan(plan)
    with pytest.raises(R.RecoveryError) as error:
        R.seal(plan, closed)
    assert error.value.details["recovery_result"]["created"] is False
    if fault == "occupied":
        assert archive.read_bytes() == b"unrelated existing output"
    else:
        assert not list(folder.iterdir())


@pytest.mark.parametrize(
    "fault",
    [
        "missing_icdb",
        "empty_icdb",
        "unreadable_icdb",
        "changed_between_passes",
        "extra_during_pass",
    ],
)
def test_closed_observation_never_skips_opaque_or_unstable_source_files(owned, monkeypatch, fault):
    project, board, archive, _ = owned
    database = project.parent / "database/icdb.dat"
    if fault == "missing_icdb":
        database.unlink()
    elif fault == "empty_icdb":
        database.write_bytes(b"")
    elif fault == "unreadable_icdb":
        original = Path.open

        def locked(path, *args, **kwargs):
            if path == database:
                raise PermissionError("owned synthetic locked icdb")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", locked)
    elif fault == "changed_between_passes":
        original = R._tree
        calls = []

        def changing(root):
            result = original(root)
            calls.append(root)
            if len(calls) == 1:
                database.write_bytes(b"changed between complete hash passes")
            return result

        monkeypatch.setattr(R, "_tree", changing)
    else:
        original = R._pin
        calls = []

        def added(path):
            result = original(path)
            calls.append(path)
            if len(calls) == 1:
                (project.parent / "unobserved.txt").write_bytes(b"new file")
            return result

        monkeypatch.setattr(R, "_pin", added)
    with pytest.raises(R.RecoveryError):
        R.observe_closed(project, board)
    assert not archive.exists()


@pytest.mark.parametrize("budget", ["files", "expanded"])
def test_closed_observation_enforces_complete_tree_budgets(owned, monkeypatch, budget):
    project, board, archive, _ = owned
    monkeypatch.setattr(R, "MAX_FILES" if budget == "files" else "MAX_TOTAL_BYTES", 2)
    with pytest.raises(R.RecoveryError):
        R.observe_closed(project, board)
    assert not archive.exists()


@pytest.mark.parametrize(
    "fault", ["source_changed", "boolean_proof", "truncated_pins", "boolean_count"]
)
def test_seal_requires_actual_complete_pins_and_current_closed_bytes(owned, fault):
    project, board, archive, _ = owned
    plan = R.plan_recovery(archive, project, board)
    closed = R.observe_closed(project, board)
    if fault == "source_changed":
        (project.parent / "Library/Library.lmc").write_bytes(b"changed after close observation")
    elif fault == "boolean_proof":
        closed = {"complete": True, "verified": True}
    elif fault == "truncated_pins":
        closed["files"].pop()
        _refingerprint(closed)
    else:
        closed["file_count"] = True
        _refingerprint(closed)
    with pytest.raises(R.RecoveryError) as error:
        R.seal(plan, closed)
    assert error.value.details["recovery_result"]["created"] is False
    assert not archive.exists()


@pytest.mark.parametrize(
    "fault", ["member_write", "archive_budget", "fsync", "source_changed_after_stream"]
)
def test_failure_retains_actual_partial_archive_and_never_cleans_or_overwrites(
    owned, monkeypatch, fault
):
    project, board, archive, _ = owned
    if fault == "archive_budget":
        monkeypatch.setattr(R, "MAX_ARCHIVE_BYTES", 64)
    plan = R.plan_recovery(archive, project, board)
    closed = R.observe_closed(project, board)
    if fault == "fsync":
        monkeypatch.setattr(
            R.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("owned fsync failure"))
        )
    elif fault != "archive_budget":
        original = R._stream_member
        calls = []

        def failed(bundle, name, pin):
            calls.append(name)
            if fault == "member_write" and len(calls) == 2:
                raise PermissionError("owned member output failure")
            original(bundle, name, pin)
            if fault == "source_changed_after_stream" and len(calls) == closed["file_count"]:
                (project.parent / "database/icdb.dat").write_bytes(b"later source mutation")

        monkeypatch.setattr(R, "_stream_member", failed)
    with pytest.raises(R.RecoveryError) as error:
        R.seal(plan, closed)
    progress = error.value.details["recovery_result"]
    assert error.value.code == "E_PARTIAL"
    assert progress["created"] is True and progress["complete"] is False
    assert progress["archive"] == R._pin(archive)
    retained = archive.read_bytes()
    assert archive.exists()
    with pytest.raises(R.RecoveryError):
        R.seal(plan, closed)
    assert archive.read_bytes() == retained
    assert set(archive.parent.iterdir()) == {project.parent, archive}


@pytest.mark.parametrize(
    "fault",
    [
        "archive_bytes",
        "missing",
        "extra",
        "member_bytes",
        "crc",
        "manifest",
        "member_receipt",
        "closed_pins",
        "not_zip",
    ],
)
def test_archive_verifier_independently_checks_every_member_not_only_prj_and_pcb(owned, fault):
    evidence = _sealed(owned)
    archive = Path(evidence["archive"]["path"])
    if fault == "archive_bytes":
        with archive.open("ab") as output:
            output.write(b"changed archive")
    elif fault == "not_zip":
        archive.write_bytes(b"not a zip")
        evidence["archive"] = R._pin(archive)
    elif fault == "member_receipt":
        evidence["archive_members"]["database/icdb.dat"]["crc"] = 0
    elif fault == "closed_pins":
        evidence["closed_tree"]["files"][0]["bytes"] += 1
        _refingerprint(evidence["closed_tree"])
    else:

        def change(payload):
            if fault == "missing":
                payload.pop("Work/retain.tmp")
            elif fault == "extra":
                payload["unexpected.txt"] = b"extra"
            elif fault == "member_bytes":
                payload["Library/Library.lmc"] = b"changed library"
            elif fault == "manifest":
                manifest = json.loads(payload[B.MANIFEST])
                manifest["all_files"] = False
                payload[B.MANIFEST] = json.dumps(manifest).encode()
                evidence["manifest"] = manifest

        _rewrite(archive, change, compression=zipfile.ZIP_STORED)
        if fault == "crc":
            archive.write_bytes(
                archive.read_bytes().replace(b"synthetic-database", b"corrupted-database")
            )
        # Even a newly supplied outer ZIP hash cannot bless incomplete or wrong
        # engineering members, a broken CRC, or altered standard metadata.
        evidence["archive"] = R._pin(archive)
    proof = R.verify_archive(evidence)
    assert proof["valid"] is False and proof["complete"] is False
    assert proof["reason"]


def test_archive_can_be_verified_after_open_without_statting_live_project(owned, monkeypatch):
    evidence = _sealed(owned)
    project = owned[0]
    (project.parent / "database/icdb.dat").write_bytes(b"subsequent native save bytes")
    original = Path.stat

    def no_live_read(path, *args, **kwargs):
        if path == project.parent or project.parent in path.parents:
            pytest.fail("Archive verification must use closed pins, not reopened live files")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", no_live_read)
    proof = R.verify_archive(evidence)
    assert proof["valid"] is True and proof["live_project_read"] is False


def test_incomplete_or_boolean_only_archive_claims_are_refused():
    assert R.verify_archive({"valid": True, "complete": True})["valid"] is False
    assert R.verify_archive({"contract": R.CONTRACT, "closed_tree": True})["valid"] is False
