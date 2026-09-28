"""A project folder as a zip, and back.

`project backup` writes the whole project folder -- the `.prj`, the schematic
database, the board and, when it lives there, the central library -- to one zip,
with a manifest. `project restore` compares a backup with the folder as it is and
makes the folder what the backup holds: files it lacks are added, files that differ
replaced, files the backup does not have removed. Pure Python; closing the project
in Designer and Layout while the files change is the command's job.

Left out, as `project create` leaves them out of a copy: `LogFiles`, the
`ProjectBackup` Designer keeps itself, the library's `Work` scratch space and
`*.bak`. A restore leaves those alone too.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import zipfile
import zlib
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MANIFEST = "xpedition-cli-backup.json"
IGNORED_DIRS = {"logfiles", "projectbackup", "work"}
IGNORED_FILES = ("*.bak",)
SAMPLE = 20


class BackupError(ValueError):
    """The backup cannot be made or read as asked."""


def _ignored(relative: Path) -> bool:
    if any(part.casefold() in IGNORED_DIRS for part in relative.parts[:-1]):
        return True
    return any(fnmatch.fnmatch(relative.name.casefold(), pattern) for pattern in IGNORED_FILES)


def project_files(folder: Path, skip: Path | None = None) -> Iterator[tuple[str, Path]]:
    """(name in the zip, file) for every file a backup takes, in a stable order; with
    `skip`, a folder of backups inside the project folder is left out."""
    if skip is not None and folder not in skip.parents:
        skip = None  # the folder itself, or one outside it, leaves nothing out
    for item in sorted(folder.rglob("*")):
        if not item.is_file():
            continue
        if skip is not None and (item == skip or skip in item.parents):
            continue
        relative = item.relative_to(folder)
        if _ignored(relative):
            continue
        yield relative.as_posix(), item


def default_archive(project: Path, now: datetime | None = None) -> Path:
    """`<folder>-backups/<project>-<time>.zip` beside the project folder."""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    folder = project.parent
    return folder.parent / f"{folder.name}-backups" / f"{project.stem}-{stamp}.zip"


def central_library(project: Path) -> dict[str, Any]:
    """Where the project's central library is, and whether a backup of the folder holds it."""
    path = ""
    try:
        for line in project.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("KEY CentralLibrary "):
                path = line[len("KEY CentralLibrary ") :].strip().strip('"')
                break
    except OSError:
        path = ""
    if not path:
        return {"path": None, "inside": False}
    library = Path(path)
    if not library.is_absolute():
        library = project.parent / library
    inside = project.parent.resolve() in library.resolve().parents
    return {"path": str(library), "inside": inside}


def create(project: Path, archive: Path, *, version: str = "") -> dict[str, Any]:
    """Zip the project folder to `archive` (which must not exist)."""
    folder = project.parent
    if archive.exists():
        raise BackupError(f"{archive} exists already")
    if archive.suffix.lower() != ".zip":
        raise BackupError("a backup is a .zip file")
    archive.parent.mkdir(parents=True, exist_ok=True)
    files = 0
    size = 0
    skipped: list[dict[str, str]] = []
    partial = archive.with_suffix(".zip.part")
    try:
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as bundle:
            for name, item in project_files(folder, skip=archive.parent):
                try:
                    bundle.write(item, name)
                except OSError as exc:
                    # a file an application holds locked cannot be read: say which
                    skipped.append({"file": name, "error": str(exc)[:160]})
                    continue
                files += 1
                size += item.stat().st_size
            manifest = {
                "project": project.name,
                "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "files": files,
                "bytes": size,
                "skipped": skipped,
                "tool_version": version,
            }
            bundle.writestr(MANIFEST, json.dumps(manifest, indent=1))
        partial.replace(archive)
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise BackupError(f"cannot write the backup: {exc}") from exc
    return {
        "backup": str(archive),
        "files": files,
        "bytes": size,
        "compressed": archive.stat().st_size,
        "skipped": skipped,
        "complete": not skipped,
    }


def _crc(path: Path) -> int:
    value = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            value = zlib.crc32(chunk, value)
    return value & 0xFFFFFFFF


def read_manifest(archive: Path, project: Path) -> tuple[dict[str, Any], list[zipfile.ZipInfo]]:
    """The backup's manifest and file entries; refuses a zip that is not this project's."""
    try:
        bundle = zipfile.ZipFile(archive)
    except (OSError, zipfile.BadZipFile) as exc:
        raise BackupError(f"{archive.name} is not a readable zip: {exc}") from exc
    with bundle:
        names = bundle.namelist()
        if MANIFEST not in names:
            raise BackupError(f"{archive.name} is not a backup made by project backup")
        manifest = json.loads(bundle.read(MANIFEST).decode("utf-8"))
        entries = [info for info in bundle.infolist() if info.filename != MANIFEST]
    if str(manifest.get("project", "")).casefold() != project.name.casefold():
        raise BackupError(
            f"{archive.name} is a backup of {manifest.get('project')!r}, not {project.name!r}"
        )
    for info in entries:
        target = Path(info.filename)
        if target.is_absolute() or ".." in target.parts:
            raise BackupError(f"{archive.name} names a file outside the project: {info.filename}")
    return manifest, entries


def plan_restore(project: Path, archive: Path) -> dict[str, Any]:
    """What restoring `archive` changes in the project folder."""
    manifest, entries = read_manifest(archive, project)
    folder = project.parent
    held = {name: path for name, path in project_files(folder, skip=archive.parent)}
    add: list[str] = []
    replace: list[str] = []
    same = 0
    for info in entries:
        if info.is_dir():
            continue
        current = held.get(info.filename)
        if current is None:
            add.append(info.filename)
        elif current.stat().st_size != info.file_size or _crc(current) != info.CRC:
            replace.append(info.filename)
        else:
            same += 1
    wanted = {info.filename for info in entries}
    remove = sorted(name for name in held if name not in wanted)
    digest = hashlib.sha256(
        json.dumps([sorted(add), sorted(replace), remove], ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return {
        "backup": str(archive),
        "created": manifest.get("created"),
        "files": len(entries),
        "add": sorted(add),
        "replace": sorted(replace),
        "remove": remove,
        "unchanged": same,
        "digest": digest[:16],
    }


def summary(plan: dict[str, Any]) -> dict[str, Any]:
    """The plan with its lists cut to a sample, for a preview."""
    return {
        **{k: v for k, v in plan.items() if k not in ("add", "replace", "remove")},
        **{
            key: {"count": len(plan[key]), "sample": plan[key][:SAMPLE]}
            for key in ("add", "replace", "remove")
        },
    }


def restore(project: Path, archive: Path, plan: dict[str, Any]) -> dict[str, Any]:
    """Make the project folder what the backup holds, as `plan` said it would."""
    folder = project.parent
    removed = 0
    failed: list[dict[str, str]] = []
    for name in plan["remove"]:
        try:
            (folder / name).unlink()
            removed += 1
        except FileNotFoundError:
            continue
        except OSError as exc:
            failed.append({"file": name, "error": str(exc)[:160]})
    written = 0
    wanted = set(plan["add"]) | set(plan["replace"])
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            if info.filename not in wanted:
                continue
            target = folder / info.filename
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as source, target.open("wb") as sink:
                    for chunk in iter(lambda: source.read(1 << 20), b""):
                        sink.write(chunk)
                written += 1
            except OSError as exc:
                failed.append({"file": info.filename, "error": str(exc)[:160]})
    # folders the removed files leave empty go too, deepest first
    for name in sorted({str(Path(n).parent) for n in plan["remove"]}, key=len, reverse=True):
        directory = folder / name
        if name not in (".", "") and directory.is_dir() and not any(directory.iterdir()):
            try:
                directory.rmdir()
            except OSError:
                pass
    return {"written": written, "removed": removed, "failed": failed, "complete": not failed}
