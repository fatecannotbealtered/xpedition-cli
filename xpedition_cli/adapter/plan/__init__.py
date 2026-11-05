"""Pure-Python planners and parsers: no Xpedition, no COM, no I/O beyond the files they are given.
They are unit-tested on their own."""
from __future__ import annotations

from typing import Any


class PlanError(ValueError):
    """A plan or a parse that cannot go on, with a runtime error code.

    ``code`` is one of the runtime's codes (``E_VALIDATION`` for values the plan rejects,
    ``E_USAGE`` for a wrong request, ``E_NOT_FOUND``, ``E_CONFLICT``); ``details`` is a dict the
    handler passes on. Handlers turn it into the runtime's ``AdapterError``.
    """

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = dict(details or {})
