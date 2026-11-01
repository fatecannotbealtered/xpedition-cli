"""The native boundary uses the same generated error extensions as the driver."""

from __future__ import annotations

import json

from . import resources
from .contract_gen import CODES as BASE_CODES


def _codes() -> dict:
    path = resources.locate("contract/driver-v2/product_contract.json")
    if path is None:
        raise RuntimeError("Generated driver error contract is missing")
    extensions = json.loads(path.read_text(encoding="utf-8"))["reference"]["error_codes"]
    return {**BASE_CODES, **extensions}


CODES = _codes()


def exit_for(code: str) -> int:
    return CODES.get(code, {}).get("exit", 1)


def retryable(code: str) -> bool:
    return bool(CODES.get(code, {}).get("retryable", False))
