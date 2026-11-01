"""kb list | add | remove: the knowledge-base documents bound on this machine.

Binding and unbinding are writes like any other. The token is bound to the entry
as it stands at the dry run, so a name re-pointed in between refuses the
confirmation instead of being overwritten.
"""

from __future__ import annotations

from typing import Any

from .. import knowledge_base
from .common import check_gate, confirmed, previewed


def listing(options: dict[str, Any]) -> dict[str, Any]:
    return knowledge_base.listing()


def _run(options: dict[str, Any], verb: str, change: dict[str, Any]) -> dict[str, Any]:
    check_gate(options, f"kb {verb}")
    scope = {"operation": f"kb_{verb}", **change}
    if options.get("dry_run"):
        return previewed(knowledge_base.preview(change), scope)
    confirmed(options, scope)
    return knowledge_base.apply(change)


def add(options: dict[str, Any]) -> dict[str, Any]:
    # the raw values: knowledge_base refuses padding and control characters itself
    about = options.get("about")
    change = knowledge_base.plan_add(
        str(options.get("name") or ""),
        str(options.get("url") or ""),
        None if about is None else str(about),
    )
    return _run(options, "add", change)


def remove(options: dict[str, Any]) -> dict[str, Any]:
    return _run(options, "remove", knowledge_base.plan_remove(str(options.get("name") or "")))
