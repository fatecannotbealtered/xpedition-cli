"""Authenticate closed-copy genesis against this CLI's existing operation record.

This reads the CLI journal, never a vendor design database. The native document
may subsequently be opened; a closed-copy receipt is provenance, not proof that
its live opaque database bytes have remained identical after opening.
"""

from __future__ import annotations

import copy
import json
import re
import sqlite3
from pathlib import Path

from .audit import config_dir
from .cell_refresh import digest


def _verify_origin(origin, project, board, *, record_reader=None):
    operation_id = origin.get("copy_operation_id")
    if not isinstance(operation_id, str) or not re.fullmatch(
        r"op[-_][a-zA-Z0-9]{8,80}", operation_id
    ):
        raise ValueError(
            "Isolation requires the original successful public closed-copy operation ID"
        )
    if record_reader is None:
        # Existing component-connect callers retain their original journal route.
        path = config_dir() / "driver-v2" / "operations.sqlite3"
        if not path.is_file() or path.is_symlink():
            raise ValueError("The existing continuous operation journal is unavailable")
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT operation_id,command,status,scope_id,receipt FROM operations "
                "WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
    elif callable(record_reader):
        row = record_reader(operation_id)
    else:
        raise ValueError("The SDK journal reader is unavailable")
    if row is None or row["command"] != "project create" or row["status"] != "succeeded":
        raise ValueError("The original closed-copy operation is absent or not successful")
    stored_receipt = row["receipt"]
    receipt = (
        stored_receipt if isinstance(stored_receipt, dict) else json.loads(stored_receipt or "null")
    )
    if not isinstance(receipt, dict):
        raise ValueError("The original public copy receipt is incomplete")
    outcome = receipt["outcome"]
    producer = receipt["extensions"]["result"]
    proof = receipt["extensions"]["file_readback"]
    if (
        receipt.get("operation_id") != operation_id
        or receipt.get("authorization_scope_id") != row["scope_id"]
        or outcome.get("execution") != "completed"
        or outcome.get("persistence") != "saved"
        or outcome["verification"].get("status") != "passed"
        or producer.get("creation_method") != "closed_backup_copy"
        or producer.get("opened") is not False
        or producer.get("native_open_verified") is not False
        or type(producer.get("native_calls_dispatched")) is not int
        or producer["native_calls_dispatched"] != 0
        or proof.get("valid") is not True
        or proof.get("complete") is not True
        or Path(receipt["target"]["object_id"]).resolve() != Path(project).resolve()
    ):
        raise ValueError(
            "Closed-copy identity, authorization or independent complete bytes are unproved"
        )
    registered = producer["closed_copy"]["registration"]["data"]
    supplied = {
        key: value
        for key, value in origin.items()
        if key not in {"copy_operation_id", "continuation_receipt"}
    }
    if (
        registered != supplied
        or Path(registered["target_board"]["path"]).resolve() != Path(board).resolve()
    ):
        raise ValueError(
            "The supplied isolation origin differs from its original public copy receipt"
        )
    from .project_isolated_copy import derive_copy

    derived = derive_copy(
        Path(registered["origin_project"]["path"]),
        Path(project),
        Path(registered["source_backup"]["path"]),
    )
    genesis = derived["genesis"]
    if (
        derived["registration"]["data"] != registered
        or proof.get("actual_target_manifest") != genesis["file_manifest"]
        or producer["closed_copy"].get("actual_target_manifest") != genesis["file_manifest"]
    ):
        raise ValueError(
            "The complete original copy differs from independently derived archive genesis"
        )
    return {
        "operation_id": operation_id,
        "receipt_sha256": digest(receipt),
        "source": "existing continuous journal: successful public project create --closed-copy",
        "genesis": copy.deepcopy(genesis),
    }


def verify_origin(origin, project, board, *, record_reader=None):
    try:
        return _verify_origin(origin, project, board, record_reader=record_reader)
    except (sqlite3.Error, KeyError, TypeError, AttributeError, OSError) as error:
        raise ValueError(
            "Original closed-copy evidence cannot be read completely: " + type(error).__name__
        ) from error
