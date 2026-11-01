"""One strict PDF source path comparison for planning and protected readback."""

from __future__ import annotations

import ntpath
import os
from pathlib import Path, PureWindowsPath


def same_project_path(observed, expected):
    """Use the owner's Windows key only after rejecting ambiguous path inputs."""
    from .designer_owner_dispatch import _project_key

    windows_paths = []
    for value in (observed, expected):
        if type(value) is not str or not value or any(ord(char) < 32 for char in value):
            return False
        windows = PureWindowsPath(value)
        windows_paths.append(bool(windows.drive))
        if ".." in windows.parts:
            return False
        if windows.drive:
            if not windows.is_absolute() or value.startswith(("\\\\?\\", "\\\\.\\")):
                return False
        elif os.name == "nt" or not Path(value).is_absolute():
            return False
    if windows_paths[0] != windows_paths[1]:
        return False
    if not windows_paths[0]:
        return Path(observed).resolve() == Path(expected).resolve()
    if ntpath.normcase(ntpath.normpath(observed)) != ntpath.normcase(ntpath.normpath(expected)):
        return False
    try:
        return _project_key(observed) == _project_key(expected)
    except (OSError, ValueError, RuntimeError):
        return False
