"""Knowledge-base documents bound on this machine: a name, a link and what it covers.

The rules an agent should follow on a real design -- a company's layout rules,
drawing conventions, review checklists -- live in that company's knowledge base,
not in this package. The tool only remembers which documents apply and shows them
in `context`; the agent reads them with whatever it has for that document system
(for a Feishu wiki, lark-cli and its skills). Nothing here fetches, caches or
interprets a document, so no system is special-cased and no credential is held.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .audit import config_dir
from .confirmation_store import atomic_write
from .errors import CLIError

FILE_NAME = "knowledge-base.json"
NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
URL_LIMIT = 2048
ABOUT_LIMIT = 200
# A link holds no whitespace or control character; text riding along in one would
# reach every agent that reads `context`.
_NOT_IN_A_LINK = re.compile(r"[\s\x00-\x1f\x7f]")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _path() -> Path:
    return config_dir() / FILE_NAME


def _read() -> dict[str, Any]:
    """The whole file, checked; an empty one when nothing was ever bound."""
    path = _path()
    if not path.is_file():
        return {"schema_version": "1.0", "documents": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:  # ValueError: not UTF-8, or not JSON
        raise CLIError(
            "E_CONFIG",
            "the knowledge-base configuration cannot be read",
            {"path": str(path), "reason": str(exc)[:200]},
        ) from exc
    documents = value.get("documents") if isinstance(value, dict) else None
    if not isinstance(documents, dict):
        raise CLIError(
            "E_CONFIG", "the knowledge-base configuration has no documents map", {"path": str(path)}
        )
    for name, entry in documents.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("url"), str):
            # refused, not skipped: the next save would delete it for good
            raise CLIError(
                "E_CONFIG",
                "a knowledge-base entry has no link; fix or remove it in the file",
                {"path": str(path), "name": name, "_untrusted": ["name"]},
            )
    return value


def load() -> dict[str, dict[str, Any]]:
    """The bound documents by name; empty when nothing was ever bound."""
    return _read()["documents"]


def _write(value: dict[str, Any]) -> None:
    path = _path()
    value["documents"] = dict(sorted(value["documents"].items()))
    data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, data)  # a reader never sees half a file
    except OSError as exc:
        raise CLIError(
            "E_IO",
            "the knowledge-base configuration cannot be written",
            {"path": str(path), "reason": str(exc)[:200]},
        ) from exc


def entries(documents: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {"name": name, "url": str(entry.get("url", "")), "about": str(entry.get("about", ""))}
        for name, entry in sorted(documents.items())
    ]


def _check_name(name: str) -> None:
    if not NAME_PATTERN.fullmatch(name):
        raise CLIError(
            "E_VALIDATION",
            "a knowledge-base name is lowercase letters, digits, - and _, up to 32 characters",
            {"name": name[:64], "_untrusted": ["name"]},
        )


def _check_link(url: str) -> str:
    link = url.strip()
    try:
        parsed = urlparse(link)
        netloc = parsed.netloc
    except ValueError:  # e.g. an unclosed IPv6 bracket
        parsed, netloc = None, ""
    if len(link) > URL_LIMIT:
        problem = f"a link is at most {URL_LIMIT} characters"
    elif _NOT_IN_A_LINK.search(link):
        problem = "a link holds no spaces or control characters"
    elif parsed is None or parsed.scheme not in ("http", "https") or not netloc:
        problem = "the document is an http(s) link"
    elif "@" in netloc:
        problem = "a link carries no user name or password; the agent signs in with its own tools"
    else:
        return link
    raise CLIError("E_VALIDATION", problem, {"url": link[:200], "_untrusted": ["url"]})


def _check_about(about: str) -> str:
    description = " ".join(about.split())
    if len(description) > ABOUT_LIMIT:
        raise CLIError(
            "E_VALIDATION",
            f"--about is at most {ABOUT_LIMIT} characters",
            {"length": len(description)},
        )
    if _CONTROL.search(description):
        raise CLIError("E_VALIDATION", "--about is plain text, without control characters")
    return description


def plan_add(name: str, url: str, about: str | None = None) -> dict[str, Any]:
    """The binding `kb add` would make, checked against the file but not written.

    Without `about`, a name bound again keeps what it covered; an empty one clears it.
    """
    _check_name(name)
    link = _check_link(url)
    description = None if about is None else _check_about(about)
    previous = load().get(name)
    before = entries({name: previous})[0] if previous else None
    if description is None:
        description = before["about"] if before else ""
    return {
        "name": name,
        "before": before,
        "after": {"name": name, "url": link, "about": description},
    }


def plan_remove(name: str) -> dict[str, Any]:
    documents = load()
    if name not in documents:
        raise CLIError(
            "E_NOT_FOUND",
            "no knowledge-base document is bound under that name",
            {"name": name[:64], "bound": sorted(documents), "_untrusted": ["name", "bound"]},
        )
    return {"name": name, "before": entries({name: documents[name]})[0], "after": None}


def preview(change: dict[str, Any]) -> dict[str, Any]:
    if change["after"] is None:
        action = "remove"
    else:
        action = "replace" if change["before"] else "add"
    return {
        "changes": [
            {
                "action": action,
                "resource": "knowledge_base_document",
                "id": change["name"],
                "before": change["before"],
                "after": change["after"],
            }
        ],
        "config": str(_path()),
        "risk": {
            "tier": "T1",
            "blast_radius": "one entry in knowledge-base.json; no document is read or changed",
        },
        "_untrusted": ["changes", "config"],
    }


def apply(change: dict[str, Any]) -> dict[str, Any]:
    """Write a planned change; the caller has already consumed its confirmation."""
    value = _read()  # anything else the file holds is written back as it was
    documents = value["documents"]
    name, after = change["name"], change["after"]
    if after is None:
        documents.pop(name, None)
        _write(value)
        return {
            "name": name,
            "removed": True,
            "url": change["before"]["url"],
            "config": str(_path()),
            "_untrusted": ["url", "config"],
        }
    documents[name] = {
        **documents.get(name, {}),
        "url": after["url"],
        "about": after["about"],
        "added_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _write(value)
    return {
        "name": name,
        "url": after["url"],
        "about": after["about"],
        "replaced": change["before"],
        "config": str(_path()),
        "_untrusted": ["url", "about", "replaced", "config"],
    }


def listing() -> dict[str, Any]:
    documents = entries(load())
    return {
        "documents": documents,
        "count": len(documents),
        "config": str(_path()),
        "_untrusted": ["documents", "config"],
    }


def for_context() -> dict[str, Any]:
    """What `context` shows: the bound documents, or why they could not be read."""
    try:
        return {"documents": entries(load())}
    except CLIError as error:
        # a broken file must not take the rest of context down with it
        return {"documents": [], "error": error.message}
