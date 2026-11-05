"""The logs Xpedition's stock tools write, read back: the packager, JobWizard, and Layout's
forward annotation.

Each reader takes the log's path, as the tool left it, and never fails: a missing or
unreadable log reads as empty. The `parse_*` functions do the same work on text.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

PACKAGER_ERROR = "     ERROR:"
_JOBWIZARD_COPIED = re.compile(r"Successfully copied (\d+) file")
# JobWizard logs in the interface language: "未能" (could not), "失败" (failed), "无法" (unable)
_JOBWIZARD_FAILURE = re.compile(r"未能|失败|无法|failed|unable|error|cannot", re.I)


def tool_text(raw: bytes) -> str:
    """A stock tool's log or output as text.

    The tools write the system code page (`mbcs`, which exists on Windows only). UTF-8
    is tried first: ASCII is UTF-8, and the code page's Chinese practically never
    decodes as UTF-8, while `mbcs` on an English machine decodes UTF-8 bytes into
    mojibake without raising. Whatever is left is read as UTF-8 with the bytes it
    cannot decode replaced, so a log never makes a reader fail.
    """
    for encoding in ("utf-8-sig", "mbcs"):
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "replace")


def _read(path: Path) -> str | None:
    try:
        return tool_text(Path(path).read_bytes())
    except OSError:
        return None


def parse_packager_log(text: str) -> tuple[list[str], str]:
    """The packager report's error lines and its verdict (the last "has NOT been packaged"
    or "terminating" line; empty when it has none)."""
    lines = text.splitlines()
    errors = [line.strip() for line in lines if line.startswith(PACKAGER_ERROR) or line.lstrip().startswith("ERROR:")]
    verdict = ""
    for line in lines:
        if "has NOT been packaged" in line or "terminating" in line.lower():
            verdict = line.strip()
    return errors, verdict


def packager_messages(log_path: Path) -> tuple[list[str], str]:
    """Pull the error lines and the verdict out of the packager report (`PartPkg.log`)."""
    text = _read(log_path)
    return ([], "") if text is None else parse_packager_log(text)


def parse_jobwizard_log(text: str) -> tuple[list[str], int | None]:
    """JobWizard's log: its error lines and the number of files it copied (None if none)."""
    copied = None
    errors: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        match = _JOBWIZARD_COPIED.search(stripped)
        if match:
            copied = int(match.group(1))
        elif _JOBWIZARD_FAILURE.search(stripped):
            errors.append(stripped)
    return errors, copied


def jobwizard_messages(log_path: Path) -> tuple[list[str], int | None]:
    """JobWizard's log: its error lines and the number of files it copied (None if none)."""
    text = _read(log_path)
    return ([], None) if text is None else parse_jobwizard_log(text)


def parse_annotation_log(text: str) -> dict[str, Any]:
    """What Layout's `ForwardAnnotation.txt` says: errors, warnings, counts, the verdict."""
    lines = [line.rstrip() for line in text.splitlines()]
    errors = [line.strip() for line in lines if re.search(r"^\s*ERROR\b", line)]
    warnings = [line.strip() for line in lines if re.search(r"^\s*WARNING\b", line)]
    components = nets = pins = None
    for line in lines:
        match = re.search(r"(\d+) components were found", line)
        if match:
            components = int(match.group(1))
        match = re.search(r"(\d+) nets were found containing (\d+) pins", line)
        if match:
            nets, pins = int(match.group(1)), int(match.group(2))
    completed = any("successfully completed" in line for line in lines)
    return {
        "errors": errors,
        "warnings": warnings,
        "components": components,
        "nets": nets,
        "pins": pins,
        "completed": completed,
    }


def annotation_summary(log_path: Path) -> dict[str, Any]:
    """What Layout's `ForwardAnnotation.txt` says; a missing log has not completed."""
    text = _read(log_path)
    if text is None:
        return {"errors": [], "warnings": [], "components": None, "nets": None, "pins": None, "completed": False}
    return parse_annotation_log(text)
