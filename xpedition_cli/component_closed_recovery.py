"""One declared, complete ZIP of a caller-released component-connect project.

These file operations neither release applications nor prove an OS atomic
snapshot. A caller must establish the native close boundary separately. Failed
archives are retained at the declared output path; nothing is retried or removed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from . import project_backup as B
from . import project_isolated_copy as I

CONTRACT = "component-complete-closed-recovery-v1"
MAX_FILES = 10000
MAX_TOTAL_BYTES = 2 * 1024**3
MAX_ARCHIVE_BYTES = 1024**3
MAX_MANIFEST_BYTES = 8 * 1024**2
CHUNK_BYTES = 1024**2
_SHA = re.compile(r"[0-9a-f]{64}")


class RecoveryError(ValueError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()


def _budgets():
    return {
        "max_files": MAX_FILES,
        "max_total_bytes": MAX_TOTAL_BYTES,
        "max_archive_bytes": MAX_ARCHIVE_BYTES,
    }


def _path(value):
    if not isinstance(value, (str, os.PathLike)) or not str(value):
        raise ValueError("An explicit absolute path is required")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Paths must be absolute and must not contain parent traversal")
    return path


def _reparse(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _directory(path):
    for item in [*reversed(path.parents), path]:
        info = item.stat(follow_symlinks=False)
        if _reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError("Every directory must exist and be non-reparse: " + str(item))
    info = path.stat(follow_symlinks=False)
    return {
        "path": str(path),
        "st_dev": info.st_dev,
        "st_ino": info.st_ino,
        "st_mode": info.st_mode,
        "file_attributes": getattr(info, "st_file_attributes", 0),
    }


def _missing(path):
    try:
        path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return
    raise ValueError("Recovery output already exists; retain it without overwrite: " + str(path))


def _file_state(info):
    return tuple(
        getattr(info, key) for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    )


def _read_identity(info):
    # Windows path.stat reports creation time as ctime, while fd.fstat may
    # report the modification time. Compare ctime only within the same API.
    return tuple(getattr(info, key) for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns"))


def _pin(path):
    before = path.stat(follow_symlinks=False)
    result = I._record(path)
    after = path.stat(follow_symlinks=False)
    if _reparse(after) or _file_state(before) != _file_state(after):
        raise ValueError("A file was replaced or changed during hashing: " + str(path))
    return result


def _project_paths(project, board):
    project, board = _path(project), _path(board)
    if project.suffix.lower() != ".prj" or board.suffix.lower() != ".pcb":
        raise ValueError("The exact project PRJ and board PCB paths are required")
    root = project.parent
    board.relative_to(root)
    _directory(root)
    _directory(board.parent)
    for path in (project, board):
        info = path.stat(follow_symlinks=False)
        if _reparse(info) or not stat.S_ISREG(info.st_mode):
            raise ValueError("Exact non-reparse project and board files are required")
    return project, board, root


def _fingerprint(value):
    if value.get("fingerprint") != _digest(
        {key: item for key, item in value.items() if key != "fingerprint"}
    ):
        raise ValueError("Frozen recovery evidence fingerprint differs")


def plan_recovery(path, project, board, *, protected_roots=(), input_paths=()):
    """Freeze one absent ZIP and its existing, non-reparse parent; write nothing."""
    try:
        archive = _path(path)
        project, board, root = _project_paths(project, board)
        if archive.suffix.lower() != ".zip":
            raise ValueError("Recovery output must name one exact ZIP")
        I._safe_member(zipfile.ZipInfo(archive.name))
        protected = list(dict.fromkeys([str(root), *(str(_path(p)) for p in protected_roots)]))
        inputs = list(
            dict.fromkeys([str(project), str(board), *(str(_path(p)) for p in input_paths)])
        )
        for item in protected:
            guard = Path(item)
            if archive == guard or guard in archive.parents:
                raise ValueError("Recovery output is inside a protected project tree")
        if any(archive == Path(item) for item in inputs):
            raise ValueError("Recovery output aliases a declared input")
        parent = _directory(archive.parent)
        _missing(archive)
        result = {
            "contract": CONTRACT,
            "kind": "recovery-plan",
            "archive_path": str(archive),
            "project": str(project),
            "board": str(board),
            "root": str(root),
            "parent_identity": parent,
            "protected_roots": protected,
            "input_paths": inputs,
            "budgets": _budgets(),
        }
        result["fingerprint"] = _digest(result)
        return result
    except RecoveryError:
        raise
    except (OSError, ValueError, TypeError) as error:
        raise RecoveryError(
            "E_VALIDATION", "Invalid closed recovery output", {"reason": str(error)}
        ) from error


def verify_plan(plan):
    """Recheck the same absent output and parent identity, without making a directory."""
    try:
        _fingerprint(plan)
        if plan["contract"] != CONTRACT or plan["kind"] != "recovery-plan":
            raise ValueError("Unknown recovery plan contract")
        fresh = plan_recovery(
            plan["archive_path"],
            plan["project"],
            plan["board"],
            protected_roots=plan["protected_roots"],
            input_paths=plan["input_paths"],
        )
        if fresh != plan:
            raise ValueError("Recovery path, parent identity or budgets changed since planning")
        return copy.deepcopy(plan)
    except RecoveryError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise RecoveryError(
            "E_CONFLICT", "Frozen recovery plan is no longer valid", {"reason": str(error)}
        ) from error


def _tree(root):
    files = []
    total = 0
    listing = []
    seen = set()
    for name, path in B.project_files(root, all_files=True):
        if len(listing) >= MAX_FILES:
            raise ValueError("Complete project exceeds the recovery file budget")
        I._safe_member(zipfile.ZipInfo(name))
        if name.casefold() == B.MANIFEST.casefold():
            raise ValueError("Project contains the reserved backup metadata member")
        if name.casefold() in seen:
            raise ValueError("Project contains case-aliasing member names")
        seen.add(name.casefold())
        size = path.stat(follow_symlinks=False).st_size
        total += size
        if total > MAX_TOTAL_BYTES:
            raise ValueError("Complete project exceeds the recovery expanded-byte budget")
        listing.append((name, path))
        files.append(_pin(path))
    if not listing or list(B.project_files(root, all_files=True)) != listing:
        raise ValueError("Complete project member set changed during observation")
    return {name: pin for (name, _), pin in zip(listing, files, strict=True)}


def observe_closed(project, board):
    """Hash every readable file twice; closure itself must be proved by the caller."""
    try:
        project, board, root = _project_paths(project, board)
        root_before = _directory(root)
        first, second = _tree(root), _tree(root)
        if first != second or _directory(root) != root_before:
            raise ValueError("Complete closed tree differs between independent hash passes")
        for required in (project.name, board.relative_to(root).as_posix(), "database/icdb.dat"):
            if required not in second or second[required]["bytes"] <= 0:
                raise ValueError(
                    "Required initialized project member is absent or empty: " + required
                )
        result = {
            "contract": CONTRACT,
            "kind": "closed-tree",
            "root": str(root),
            "project": str(project),
            "board": str(board),
            "files": list(second.values()),
            "file_manifest": {name: pin["sha256"] for name, pin in second.items()},
            "file_bytes": {name: pin["bytes"] for name, pin in second.items()},
            "file_count": len(second),
            "total_bytes": sum(pin["bytes"] for pin in second.values()),
            "observation": {"passes": 2, "identical": True, "atomic_snapshot": False},
            "budgets": _budgets(),
        }
        result["fingerprint"] = _digest(result)
        return result
    except RecoveryError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RecoveryError(
            "E_VALIDATION", "Complete closed project cannot be observed", {"reason": str(error)}
        ) from error


def _closed_rows(closed):
    """Validate frozen pins only; do not stat a possibly reopened live project."""
    _fingerprint(closed)
    if closed["contract"] != CONTRACT or closed["kind"] != "closed-tree":
        raise ValueError("Unknown closed-tree evidence contract")
    project, board, root = (_path(closed[key]) for key in ("project", "board", "root"))
    if (
        project.parent != root
        or project.suffix.lower() != ".prj"
        or board.suffix.lower() != ".pcb"
        or closed["budgets"] != _budgets()
    ):
        raise ValueError("Closed-tree root or budgets are inconsistent")
    required = {project.name, board.relative_to(root).as_posix(), "database/icdb.dat"}
    files = closed["files"]
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError("Closed-tree pins are empty or exceed the file budget")
    rows = {}
    seen = set()
    for pin in files:
        if set(pin) != {"path", "bytes", "sha256"}:
            raise ValueError("A complete path/bytes/SHA-256 file pin is required")
        name = _path(pin["path"]).relative_to(root).as_posix()
        I._safe_member(zipfile.ZipInfo(name))
        if name.casefold() in seen or name.casefold() == B.MANIFEST.casefold():
            raise ValueError("Duplicate or reserved closed-tree member")
        seen.add(name.casefold())
        if type(pin["bytes"]) is not int or pin["bytes"] < 0 or not _SHA.fullmatch(pin["sha256"]):
            raise ValueError("Invalid closed-tree byte count or SHA-256")
        rows[name] = pin
    total = sum(pin["bytes"] for pin in rows.values())
    if (
        type(closed["file_count"]) is not int
        or closed["file_count"] != len(rows)
        or type(closed["total_bytes"]) is not int
        or total != closed["total_bytes"]
        or total > MAX_TOTAL_BYTES
        or closed["file_manifest"] != {name: pin["sha256"] for name, pin in rows.items()}
        or closed["file_bytes"] != {name: pin["bytes"] for name, pin in rows.items()}
        or not required <= rows.keys()
        or any(rows[name]["bytes"] == 0 for name in required)
        or set(closed["observation"]) != {"passes", "identical", "atomic_snapshot"}
        or type(closed["observation"]["passes"]) is not int
        or closed["observation"]["passes"] != 2
        or closed["observation"]["identical"] is not True
        or closed["observation"]["atomic_snapshot"] is not False
        or any(type(value) is not int for value in closed["file_bytes"].values())
    ):
        raise ValueError("Closed-tree complete member pins or accounting are inconsistent")
    return rows


class _BoundedWriter:
    def __init__(self, stream):
        self.stream = stream

    def write(self, raw):
        if self.tell() + len(raw) > MAX_ARCHIVE_BYTES:
            raise ValueError("Recovery ZIP exceeds its declared archive-byte budget")
        return self.stream.write(raw)

    def tell(self):
        return self.stream.tell()

    def seek(self, *args):
        return self.stream.seek(*args)

    def flush(self):
        return self.stream.flush()


def _stream_member(bundle, name, pin):
    path = Path(pin["path"])
    before = path.stat(follow_symlinks=False)
    if _reparse(before) or not stat.S_ISREG(before.st_mode):
        raise ValueError("A recovery member is no longer a regular file")
    count, sha = 0, hashlib.sha256()
    with path.open("rb") as source, bundle.open(name, "w") as output:
        opened = os.fstat(source.fileno())
        if _read_identity(before) != _read_identity(opened):
            raise ValueError("Recovery member changed before its read handle opened")
        while raw := source.read(CHUNK_BYTES):
            count += len(raw)
            if count > pin["bytes"]:
                raise ValueError("Recovery member grew while streaming")
            sha.update(raw)
            output.write(raw)
        final = os.fstat(source.fileno())
    current = path.stat(follow_symlinks=False)
    if (
        _file_state(opened) != _file_state(final)
        or _file_state(before) != _file_state(current)
        or _read_identity(final) != _read_identity(current)
        or _reparse(current)
        or not stat.S_ISREG(current.st_mode)
        or count != pin["bytes"]
        or sha.hexdigest() != pin["sha256"]
    ):
        raise ValueError("Recovery member bytes differ from the complete closed observation")


def seal(plan, closed):
    """Exclusively create the one declared ZIP; retain any partial output on failure."""
    plan, closed = copy.deepcopy(plan), copy.deepcopy(closed)
    progress = {
        "contract": CONTRACT,
        "created": False,
        "complete": False,
        "verified": False,
        "archive_path": plan.get("archive_path") if isinstance(plan, dict) else None,
        "written_members": [],
        "atomic_snapshot": False,
    }
    try:
        verify_plan(plan)
        rows = _closed_rows(closed)
        if any(plan[key] != closed[key] for key in ("root", "project", "board")):
            raise ValueError("Closed-tree evidence belongs to a different recovery plan")
        if observe_closed(plan["project"], plan["board"]) != closed:
            raise ValueError("Project changed after its complete closed observation")
        verify_plan(plan)
        archive = Path(plan["archive_path"])
        manifest = {
            "project": Path(plan["project"]).name,
            "created": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "files": len(rows),
            "bytes": closed["total_bytes"],
            "skipped": [],
            "tool_version": __version__,
            "all_files": True,
            "sha256": closed["file_manifest"],
            "atomic_snapshot": False,
        }
        with archive.open("xb") as raw:
            progress["created"] = True
            if _directory(archive.parent) != plan["parent_identity"]:
                raise ValueError("Recovery parent identity changed at output creation")
            with zipfile.ZipFile(_BoundedWriter(raw), "w", zipfile.ZIP_DEFLATED) as bundle:
                for name, pin in rows.items():
                    _stream_member(bundle, name, pin)
                    progress["written_members"].append(name)
                encoded = json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8")
                if len(encoded) > MAX_MANIFEST_BYTES:
                    raise ValueError("Recovery manifest exceeds the metadata budget")
                bundle.writestr(B.MANIFEST, encoded)
            raw.flush()
            os.fsync(raw.fileno())
        if observe_closed(plan["project"], plan["board"]) != closed:
            raise ValueError("Complete project changed while its recovery ZIP was written")
        result = {
            "contract": CONTRACT,
            "archive": _pin(archive),
            "manifest": manifest,
            "closed_tree": copy.deepcopy(closed),
            "created": True,
            "complete": True,
            "verified": True,
            "atomic_snapshot": False,
        }
        proof = verify_archive(result)
        if not proof["valid"]:
            raise ValueError(proof["reason"])
        result["archive_members"] = proof["archive_members"]
        return result
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        zipfile.BadZipFile,
        RuntimeError,
    ) as error:
        if progress["created"]:
            try:
                progress["archive"] = _pin(Path(progress["archive_path"]))
            except (OSError, ValueError) as pin_error:
                progress["archive_pin_error"] = type(pin_error).__name__ + ": " + str(pin_error)
        raise RecoveryError(
            "E_PARTIAL" if progress["created"] else "E_VALIDATION",
            "Closed recovery did not verify; retain declared output without cleanup or replay",
            {"reason": str(error), "cause": type(error).__name__, "recovery_result": progress},
        ) from error


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate backup metadata key")
        result[key] = value
    return result


def verify_archive(evidence):
    """Rehash ZIP and compare every member to frozen pins, without reading live files."""
    try:
        if evidence["contract"] != CONTRACT:
            raise ValueError("Unknown recovery archive evidence contract")
        rows = _closed_rows(evidence["closed_tree"])
        archive = _path(evidence["archive"]["path"])
        _directory(archive.parent)
        archive_before = _pin(archive)
        if archive_before != evidence["archive"] or archive_before["bytes"] > MAX_ARCHIVE_BYTES:
            raise ValueError("Recovery ZIP bytes differ from the recorded archive")
        members = {}
        with zipfile.ZipFile(archive) as bundle:
            infos = bundle.infolist()
            names = [info.filename for info in infos]
            if (
                len(infos) != len(rows) + 1
                or len({name.casefold() for name in names}) != len(names)
                or set(names) != {*rows, B.MANIFEST}
                or sum(info.file_size for info in infos) > MAX_TOTAL_BYTES + MAX_MANIFEST_BYTES
            ):
                raise ValueError("Recovery ZIP does not contain exactly the complete member set")
            metadata = bundle.getinfo(B.MANIFEST)
            if metadata.file_size > MAX_MANIFEST_BYTES:
                raise ValueError("Recovery ZIP metadata budget exceeded")
            for info in infos:
                I._safe_member(info)
                count, sha = 0, hashlib.sha256()
                limit = (
                    MAX_MANIFEST_BYTES
                    if info.filename == B.MANIFEST
                    else rows[info.filename]["bytes"]
                )
                with bundle.open(info) as source:
                    while raw := source.read(CHUNK_BYTES):
                        count += len(raw)
                        if count > limit:
                            raise ValueError("Recovery ZIP member exceeds its frozen byte count")
                        sha.update(raw)
                members[info.filename] = {
                    "bytes": count,
                    "sha256": sha.hexdigest(),
                    "crc": info.CRC,
                }
                if info.filename != B.MANIFEST and (
                    count != rows[info.filename]["bytes"]
                    or sha.hexdigest() != rows[info.filename]["sha256"]
                ):
                    raise ValueError(
                        "Recovery ZIP member differs from its complete closed-tree pin"
                    )
            manifest = json.loads(bundle.read(B.MANIFEST), object_pairs_hook=_json_object)
        if (
            manifest != evidence["manifest"]
            or manifest.get("project") != Path(evidence["closed_tree"]["project"]).name
            or type(manifest.get("files")) is not int
            or manifest["files"] != len(rows)
            or type(manifest.get("bytes")) is not int
            or manifest["bytes"] != evidence["closed_tree"]["total_bytes"]
            or manifest.get("all_files") is not True
            or manifest.get("skipped") != []
            or manifest.get("atomic_snapshot") is not False
            or manifest.get("sha256") != evidence["closed_tree"]["file_manifest"]
            or ("archive_members" in evidence and evidence["archive_members"] != members)
            or _pin(archive) != archive_before
        ):
            raise ValueError("Recovery metadata, member proof or archive identity is inconsistent")
        return {
            "valid": True,
            "complete": True,
            "archive": archive_before,
            "manifest": manifest,
            "archive_members": members,
            "crc_verified": True,
            "live_project_read": False,
            "atomic_snapshot": False,
        }
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        zipfile.BadZipFile,
        RuntimeError,
    ) as error:
        return {"valid": False, "complete": False, "reason": str(error), "live_project_read": False}
