from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contract_gen import exit_for, retryable


@dataclass
class CLIError(Exception):
    """A structured, contract-bound CLI failure."""

    code: str
    message: str
    details: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        super().__init__(self.message)
        if self.details is None:
            self.details = {}

    @property
    def exit_code(self) -> int:
        return exit_for(self.code)

    @property
    def is_retryable(self) -> bool:
        return retryable(self.code)
