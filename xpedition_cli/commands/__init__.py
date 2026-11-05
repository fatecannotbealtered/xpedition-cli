"""The domain commands of xpedition-cli, as registrations (DCLI-029).

Each command is one dict. The runtime checks every field when the tool starts and refuses a
registration that breaks a rule, so a mistake shows up at once. A command needs:

- ``path``: ``noun verb`` in lowercase words, the noun taken from Xpedition's object model
- ``summary``: one line
- ``effects``: what it changes (DCLI-051); the runtime derives the confirmation and risk tiers
- ``modes``, ``target_kind``, ``flags`` (with a unit for every physical quantity), ``untrusted_fields``
- ``adapter_capabilities``: the adapter methods it calls (DCLI-260)
- ``survey_ids``: the capability IDs of docs/APPLICATION.md it provides (DREPO-061)
- ``irreversible``, ``halt``, ``tolerances``, ``default_fields``, ``related_knowledge``
- ``output_schema``, ``examples``, ``maturity`` and ``evidence``

Every command also gets a behavior matrix in tests/matrix/; its ``normal`` row is the write
sample (DVER-002, DVER-030). Run ``python scripts/spec.py generate`` after a change.
"""
from __future__ import annotations

from typing import Any

COMMANDS: list[dict[str, Any]] = []
