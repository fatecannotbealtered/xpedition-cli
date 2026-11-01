"""Complete closed-backup byte copies; no native application or database calls."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

from . import project_backup as B
from . import project_file as P

CONTRACT = "complete-closed-project-copy-v1"
MAX_FILES = 10000
MAX_ARCHIVE_BYTES = 1024**3
MAX_EXPANDED_BYTES = 2 * 1024**3
_SHA = re.compile(r"[0-9a-f]{64}")


class CopyError(ValueError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()


def _record(path):
    path = Path(path)
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise CopyError(
            "E_VALIDATION", "A regular non-reparse source file is required", {"path": str(path)}
        )
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        before = handle.fileno()
        first = os.fstat(before)
        for chunk in iter(lambda: handle.read(1024**2), b""):
            sha.update(chunk)
        last = os.fstat(before)
    if any(
        getattr(first, key) != getattr(last, key)
        for key in ("st_size", "st_mtime_ns", "st_ctime_ns")
    ):
        raise CopyError("E_CONFLICT", "A source file changed while hashing", {"path": str(path)})
    return {"path": str(path), "bytes": last.st_size, "sha256": sha.hexdigest()}


def _tree(root):
    info = root.stat(follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise CopyError("E_VALIDATION", "Project root must be a regular non-reparse directory")
    files = list(B.project_files(root, all_files=True))
    if not 1 <= len(files) <= MAX_FILES:
        raise CopyError("E_VALIDATION", "Complete project file budget is empty or exceeded")
    result = {name: _record(path) for name, path in files}
    if list(B.project_files(root, all_files=True)) != files:
        raise CopyError("E_CONFLICT", "Project file set changed during observation")
    return result


def _safe_member(info):
    name = info.filename
    parts = PurePosixPath(name).parts
    mode = info.external_attr >> 16
    reserved = {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
    if (
        not name
        or info.is_dir()
        or name.startswith(("/", "\\"))
        or "\\" in name
        or ":" in name
        or "//" in name
        or any(part in {".", ".."} for part in name.split("/"))
        or any(
            part.endswith((".", " ")) or part.split(".")[0].casefold() in reserved for part in parts
        )
        or any(ord(char) < 32 or char in '<>"|?*' for char in name)
        or stat.S_ISLNK(mode)
        or stat.S_ISDIR(mode)
        or info.flag_bits & 1
    ):
        raise CopyError("E_VALIDATION", "Unsafe backup member", {"member": name})


def _decode(raw):
    encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8"
    text = raw.decode(encoding)
    if text.encode(encoding) != raw:
        raise CopyError("E_UNSUPPORTED", "Project encoding cannot be preserved exactly")
    return text, encoding


def _library_closure(text, root, manifest):
    references, active_list = [], None
    for index, line in enumerate(text.splitlines(), 1):
        start = re.match(r"^\s*LIST\s+(\S+)", line)
        if start:
            active_list = start[1]
        elif re.match(r"^\s*(ENDLIST|SECTION|ENDSECTION)\b", line):
            active_list = None
        key = re.match(r'^\s*KEY\s+(CentralLibrary|DBCFile)\s+"([^"]*)"', line)
        item = re.match(r'^\s*VALUE\s+"([^"]*)"', line)
        if key:
            references.append((index, key[1], key[2]))
        elif item and active_list in {"Symbols", "PDBs", P.CELL_LIST}:
            references.append((index, "LIST " + active_list, item[1]))
    central = [value for _, key, value in references if key == "CentralLibrary"]
    if len(central) != 1 or not central[0]:
        raise CopyError(
            "E_VALIDATION", "One closed source-local CentralLibrary reference is required"
        )
    library = P.local_path(root, central[0]).resolve()
    library.relative_to(root)
    names = {name.casefold() for name in manifest}
    if library.relative_to(root).as_posix().casefold() not in names:
        raise CopyError("E_VALIDATION", "CentralLibrary is outside the complete backup")
    result = []
    for line, key, value in references:
        if not value:
            continue
        choices = []
        for base in dict.fromkeys((root, library.parent)):
            candidate = P.local_path(base, value).resolve()
            try:
                relative = candidate.relative_to(root).as_posix()
            except ValueError:
                continue
            folded = relative.casefold()
            if folded in names or any(name.startswith(folded.rstrip("/") + "/") for name in names):
                if relative not in choices:
                    choices.append(relative)
        if len(choices) != 1:
            raise CopyError(
                "E_VALIDATION",
                "Library reference is external, missing or ambiguous",
                {"line": line, "key": key, "value": value},
            )
        result.append({"line": line, "key": key, "value": value, "source_relative": choices[0]})
    return result


def derive_copy(template, project, source_backup):
    """Pure source/ZIP derivation; never reads or requires an empty target tree."""
    template, project, archive = (
        Path(str(path)).expanduser().resolve() for path in (template, project, source_backup)
    )
    try:
        root, target = template.parent, project.parent
        if template.suffix.lower() != ".prj" or project.name != template.name:
            raise CopyError(
                "E_VALIDATION", "Closed copy retains the initialized source .prj filename"
            )
        if not str(project).isascii():
            raise CopyError("E_VALIDATION", "Closed-copy target path must be ASCII")
        if root == target or root in target.parents or target in root.parents:
            raise CopyError("E_VALIDATION", "Source and target project trees must be disjoint")
        source = _tree(root)
        archive_pin = _record(archive)
        if archive.suffix.lower() != ".zip" or archive_pin["bytes"] > MAX_ARCHIVE_BYTES:
            raise CopyError("E_VALIDATION", "A bounded complete project-backup ZIP is required")
        with zipfile.ZipFile(archive) as bundle:
            infos = bundle.infolist()
            if (
                not 2 <= len(infos) <= MAX_FILES + 1
                or sum(i.file_size for i in infos) > MAX_EXPANDED_BYTES
            ):
                raise CopyError("E_VALIDATION", "Backup member count/size budget exceeded")
            for info in infos:
                _safe_member(info)
            names = [i.filename for i in infos]
            if (
                len({name.casefold() for name in names}) != len(names)
                or bundle.testzip() is not None
            ):
                raise CopyError("E_VALIDATION", "Backup has duplicate members or invalid CRC")
            metadata = json.loads(bundle.read(B.MANIFEST))
            hashes = metadata["sha256"]
            members = [name for name in names if name != B.MANIFEST]
            if (
                metadata.get("project") != template.name
                or metadata.get("all_files") is not True
                or metadata.get("skipped") != []
                or type(metadata.get("files")) is not int
                or metadata["files"] != len(members)
                or set(hashes) != set(members)
                or set(source) != set(members)
                or template.name not in members
                or "database/icdb.dat" not in members
                or source["database/icdb.dat"]["bytes"] == 0
                or any(
                    not isinstance(value, str) or _SHA.fullmatch(value) is None
                    for value in hashes.values()
                )
            ):
                raise CopyError(
                    "E_VALIDATION", "Backup/source is not one complete initialized project tree"
                )
            if "source" in metadata and Path(metadata["source"]).resolve() != root:
                raise CopyError(
                    "E_CONFLICT", "Backup source path differs from the supplied project"
                )
            for name in members:
                sha = hashlib.sha256()
                with bundle.open(name) as handle:
                    for chunk in iter(lambda: handle.read(1024**2), b""):
                        sha.update(chunk)
                if sha.hexdigest() != hashes[name] or source[name]["sha256"] != hashes[name]:
                    raise CopyError(
                        "E_CONFLICT",
                        "Source or archive member differs from frozen backup",
                        {"member": name},
                    )
            raw = bundle.read(template.name)
        if _record(archive) != archive_pin or _tree(root) != source:
            raise CopyError(
                "E_CONFLICT", "Complete source or archive changed during copy derivation"
            )
        text, encoding = _decode(raw)
        designs = [item for item in P.designs(text) if item.is_board() and item.pcb_path]
        if len(designs) != 1 or PureWindowsPath(designs[0].pcb_path).anchor:
            raise CopyError(
                "E_UNSUPPORTED", "Closed copy requires one existing relative board design"
            )
        board = P.local_path(root, designs[0].pcb_path).resolve()
        board_rel = board.relative_to(root).as_posix()
        if board_rel not in source or not source[board_rel]["bytes"]:
            raise CopyError(
                "E_VALIDATION", "Initialized source board is not in the complete archive"
            )
        database = P._key(P._section_body(text, "iCDB"), "iCDBDir")
        if database and P.local_path(root, database).resolve() != root / "database":
            raise CopyError("E_UNSUPPORTED", "Only a source-local database directory is supported")
        closure = _library_closure(text, root, hashes)
        rewritten_text, rewrites = P.relocate_library_references(text, root, target)
        rewritten = rewritten_text.encode(encoding)
        genesis = dict(hashes)
        genesis[template.name] = hashlib.sha256(rewritten).hexdigest()
        differences = (
            []
            if rewritten == raw
            else [
                {
                    "path": template.name,
                    "source_sha256": hashes[template.name],
                    "target_sha256": genesis[template.name],
                    "kind": "public_prj_library_references_only",
                    "lines": [
                        {"line": i, "before": old, "after": new}
                        for i, (old, new) in enumerate(
                            zip(text.splitlines(), rewritten_text.splitlines(), strict=True), 1
                        )
                        if old != new
                    ],
                }
            ]
        )
        registration = {
            "schema_version": 1,
            "kind": "isolated-component-connect",
            "origin_project": source[template.name],
            "origin_board": source[board_rel],
            "source_backup": archive_pin,
            "target_project": {"path": str(project)},
            "target_board": {"path": str(target / board_rel)},
            "genesis": {
                "file_manifest": genesis,
                "project_sha256": genesis[template.name],
                "board_sha256": genesis[board_rel],
            },
            "provenance": {
                "kind": "closed-backup-copy",
                "project_library_relocation": {
                    "encoding": encoding,
                    "from_root": str(root),
                    "to_root": str(target),
                    "rewritten": rewrites,
                },
            },
        }
        return {
            "contract": CONTRACT,
            "source_project": source[template.name],
            "source_board": source[board_rel],
            "source_backup": archive_pin,
            "source_manifest": hashes,
            "genesis": registration["genesis"],
            "target_project": str(project),
            "target_board": str(target / board_rel),
            "project_encoding": encoding,
            "library_closure": closure,
            "exact_differences": differences,
            "registration": {"data": registration},
        }
    except CopyError:
        raise
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        raise CopyError(
            "E_VALIDATION",
            "Complete closed source derivation failed",
            {"cause": type(error).__name__, "reason": str(error)},
        ) from error


def plan_copy(template, project, source_backup):
    plan = derive_copy(template, project, source_backup)
    root = Path(plan["target_project"]).parent
    if root.exists() and (root.is_symlink() or any(root.iterdir())):
        raise CopyError("E_CONFLICT", "Closed-copy target directory must be absent or empty")
    plan["target_directory_state"] = "empty" if root.exists() else "absent"
    plan["fingerprint"] = _digest(plan)
    return plan


def verify_copy(result):
    """Independently derive expected bytes and read the entire actual target tree."""
    try:
        saved = result["closed_copy"]
        plan = saved["plan"]
        if plan["fingerprint"] != _digest(
            {key: value for key, value in plan.items() if key != "fingerprint"}
        ):
            raise ValueError("Frozen copy plan fingerprint differs")
        source = derive_copy(
            plan["source_project"]["path"], plan["target_project"], plan["source_backup"]["path"]
        )
        if source != {
            key: value
            for key, value in plan.items()
            if key not in {"fingerprint", "target_directory_state"}
        }:
            raise ValueError("Source or expected genesis differs from the original plan")
        if saved["registration"] != source["registration"] or saved["genesis"] != source["genesis"]:
            raise ValueError("Producer registration/genesis differs from independent derivation")
        actual = _tree(Path(source["target_project"]).parent)
        hashes = {name: row["sha256"] for name, row in actual.items()}
        if hashes != source["genesis"]["file_manifest"]:
            raise ValueError("Complete actual target file set/hashes differ from expected genesis")
        return {
            "valid": True,
            "complete": True,
            "source": {
                "project": source["source_project"],
                "board": source["source_board"],
                "backup": source["source_backup"],
            },
            "genesis": source["genesis"],
            "target": {"project": source["target_project"], "board": source["target_board"]},
            "actual_target_manifest": hashes,
            "actual_target_files": actual,
            "native_open_verified": False,
        }
    except (CopyError, OSError, ValueError, KeyError, TypeError) as error:
        return {
            "valid": False,
            "complete": False,
            "reason": str(error),
            "native_open_verified": False,
        }


def apply_copy(plan):
    """Recheck and copy once, retaining partial files without automatic cleanup."""
    fresh = plan_copy(
        plan["source_project"]["path"], plan["target_project"], plan["source_backup"]["path"]
    )
    if fresh != plan:
        raise CopyError(
            "E_CONFLICT", "Source/ZIP/target changed since copy preview", {"write_attempted": False}
        )
    result = {
        "project": Path(plan["target_project"]).stem,
        "path": plan["target_project"],
        "template": plan["source_project"]["path"],
        "creation_method": "closed_backup_copy",
        "created": False,
        "opened": False,
        "native_open_verified": False,
        "native_calls_dispatched": 0,
        "file_copy_verified": False,
        "closed_copy": {
            "plan": plan,
            "genesis": plan["genesis"],
            "registration": plan["registration"],
            "source_pins": {
                "project": plan["source_project"],
                "board": plan["source_board"],
                "backup": plan["source_backup"],
            },
            "written_files": [],
            "exact_differences": plan["exact_differences"],
        },
    }
    root = Path(plan["target_project"]).parent
    try:
        root.mkdir(parents=True, exist_ok=plan["target_directory_state"] == "empty")
        with zipfile.ZipFile(plan["source_backup"]["path"]) as bundle:
            for name, expected in plan["genesis"]["file_manifest"].items():
                destination = root / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as output:
                    if name == Path(plan["target_project"]).name:
                        raw = bundle.read(name)
                        text, encoding = _decode(raw)
                        transformed, _ = P.relocate_library_references(
                            text, Path(plan["source_project"]["path"]).parent, root
                        )
                        output.write(transformed.encode(encoding))
                    else:
                        with bundle.open(name) as source:
                            for chunk in iter(lambda: source.read(1024**2), b""):
                                output.write(chunk)
                row = _record(destination)
                result["closed_copy"]["written_files"].append(row)
                if row["sha256"] != expected:
                    raise ValueError("Copied member bytes differ: " + name)
        verification = verify_copy(result)
        result["closed_copy"]["target_verification"] = verification
        if not verification["valid"]:
            raise ValueError(verification["reason"])
        result["closed_copy"]["actual_target_manifest"] = verification["actual_target_manifest"]
        result.update(
            created=True, file_copy_verified=True, files=len(plan["genesis"]["file_manifest"])
        )
        return result
    except Exception as error:
        result["file_copy_error"] = {"cause": type(error).__name__, "message": str(error)}
        raise CopyError(
            "E_PARTIAL",
            "Closed copy did not verify; retain target files, no cleanup/replay",
            {"native_result": result, "write_attempted": True},
        ) from error
