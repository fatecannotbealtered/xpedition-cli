"""The adapter layer: the only code of xpedition-cli that talks to Xpedition (DCLI-260).

Each adapter capability is a method that takes the request dict of
``contract/schemas/adapter.schema.json`` and returns its reply. Implement only what the
chosen modes need: ``probe`` always; ``sessions`` and the instance-side capabilities for
session mode; ``devices`` for device mode. A failure raises ``AdapterError`` with an error
code, and a write capability says in ``dispatch_state`` whether the change began.

The conformance suites never call this code: they run the tool with ``XPEDITION_CLI_FAKE_HOST``
set, and the fake host answers every capability (DVER-023).
"""
from __future__ import annotations

from typing import Any

from .._driver import Adapter as BaseAdapter


class Adapter(BaseAdapter):
    def probe(self, request: dict[str, Any]) -> dict[str, Any]:
        # {{fill: find Xpedition and return found, version, path and license, as adapter.schema.json describes}}
        return {"found": False}
