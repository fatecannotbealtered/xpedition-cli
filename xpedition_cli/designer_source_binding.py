"""Bound the complete closed setup/library source before queued NewProject.

This is a file observation, not permission, native initialization or an index.
No CAD object, configuration write, source copy or component install is used.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path, PureWindowsPath

from .execution_lifecycle import _plain_path
from .project_file import template_state

MAX_FILES = 8192
MAX_BYTES = 512 * 1024 * 1024


class SourceBindingError(ValueError):
    pass


def _tree(root):
    root = _plain_path(root)
    pending, files, total, directories, entries, names = [root], [], 0, 0, 0, []
    while pending:
        directory = _plain_path(pending.pop())
        directories += 1
        names.append(directory.relative_to(root).as_posix())
        if directories > MAX_FILES:
            raise SourceBindingError("setup_source_resource_limit")
        with os.scandir(directory) as scan:
            children = []
            for entry in scan:
                entries += 1
                if entries > 2 * MAX_FILES:
                    raise SourceBindingError("setup_source_resource_limit")
                children.append(Path(entry.path))
        for path in children:
            info = path.lstat()
            if path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise SourceBindingError("setup_source_reparse_refused")
            if stat.S_ISDIR(info.st_mode):
                pending.append(path)
                continue
            if not stat.S_ISREG(info.st_mode):
                raise SourceBindingError("setup_source_nonregular_refused")
            if len(files) >= MAX_FILES or total + info.st_size > MAX_BYTES:
                raise SourceBindingError("setup_source_resource_limit")
            digest, size = hashlib.sha256(), 0
            with _plain_path(path).open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    size += len(block)
                    if total + size > MAX_BYTES:
                        raise SourceBindingError("setup_source_resource_limit")
                    digest.update(block)
            after = path.lstat()
            if size != info.st_size or (after.st_size, after.st_mtime_ns) != (
                info.st_size,
                info.st_mtime_ns,
            ):
                raise SourceBindingError("setup_source_changed_during_read")
            total += size
            files.append((path.relative_to(root).as_posix(), size, digest.hexdigest()))
    return {"root": str(root), "directories": sorted(names), "files": sorted(files), "bytes": total}


def setup_source_binding(template):
    """Two complete byte observations of a self-contained setup/library source.

    Only uninitialized setup templates use this initial dedicated route. The
    existing initialized-project/closed-copy protocol remains separate.
    """
    path = _plain_path(Path(template).absolute())
    if path.suffix.lower() != ".prj" or not path.is_file():
        raise SourceBindingError("setup_source_project_required")
    state = template_state(path)
    if state.get("creation_method") != "native_new_project" or state.get("initialized"):
        raise SourceBindingError("dedicated_new_project_requires_setup_source")
    library_value = next(
        (
            line[len("KEY CentralLibrary ") :].strip().strip('"')
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("KEY CentralLibrary ")
        ),
        "",
    )
    if not library_value:
        raise SourceBindingError("setup_source_library_missing")
    windows = PureWindowsPath(library_value)
    if ".." in windows.parts:
        raise SourceBindingError("setup_source_library_parent_reference_unsupported")
    library = _plain_path(
        Path(library_value) if windows.anchor else path.parent.joinpath(*windows.parts)
    )
    if not library.is_file():
        raise SourceBindingError("setup_source_library_missing")
    if not library.is_relative_to(path.parent):
        raise SourceBindingError("dedicated_setup_library_must_be_self_contained")
    roots = [path.parent]
    first = [_tree(root) for root in roots]
    second = [_tree(root) for root in roots]
    if (
        first != second
        or template_state(path) != state
        or sum(len(tree["files"]) for tree in second) > MAX_FILES
        or sum(tree["bytes"] for tree in second) > MAX_BYTES
    ):
        raise SourceBindingError("setup_source_changed_or_resource_limit")
    closure = hashlib.sha256(
        json.dumps(second, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    template_hash = next(
        digest
        for tree in second
        if path.is_relative_to(Path(tree["root"]))
        for relative, _size, digest in tree["files"]
        if relative == path.relative_to(Path(tree["root"])).as_posix()
    )
    return {
        "scheme": "setup-template-closure-v1",
        "template": str(path),
        "library": str(library),
        "template_sha256": template_hash,
        "closure_sha256": closure,
        "file_count": sum(len(tree["files"]) for tree in second),
        "total_bytes": sum(tree["bytes"] for tree in second),
    }
