"""The public SDK remains single-use while the private JSON ledger degrades.

Only the exact legacy-ledger publication boundary fails. Public parsing, scope
checks, SDK SQLite dispatch, the handler, and the knowledge-base write are real.
No native adapter or external document is used.
"""

from __future__ import annotations

import errno
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINK = "https://example.com/offline-ledger-proof"
FAULT_MARKER = "private-ledger-fault-must-not-appear"
PUBLIC_ENTRY_WITH_LEDGER_FAULT = r"""
import errno
import json
import os
from pathlib import Path
import sys

from xpedition_cli import confirm
from xpedition_cli import confirmation_store as store

config = Path(os.environ["XPEDITION_CLI_CONFIG_DIR"])
ledger = config / "confirm-consumed.json"
knowledge = config / "knowledge-base.json"
trace = Path(os.environ["XPEDITION_TEST_BOUNDARY_TRACE"])
original_write = store.atomic_write
original_claim = confirm.claim

def event(boundary, **fields):
    with trace.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"boundary": boundary, **fields}) + "\n")

def fault_only_legacy_ledger(path, data):
    if path == ledger:
        event("legacy_ledger_publish", errno=errno.ENOSPC, winerror=None)
        raise OSError(errno.ENOSPC, "private-ledger-fault-must-not-appear")
    result = original_write(path, data)
    if path == knowledge:
        event("knowledge_base_publish")
    return result

def trace_legacy_claim(path, fingerprint, expires_at):
    if path == ledger:
        event("legacy_claim")
    return original_claim(path, fingerprint, expires_at)

store.atomic_write = fault_only_legacy_ledger
confirm.claim = trace_legacy_claim

# This is the same public entry point as python -m xpedition_cli. The private
# handler_harness is deliberately not imported.
from xpedition_cli.main import main
raise SystemExit(main(sys.argv[1:]))
"""


def _run_public(args, config, scope, trace):
    environment = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "XPEDITION_CLI_CONFIG_DIR": str(config),
        "XPEDITION_CLI_SCOPE_FILE": str(scope),
        "XPEDITION_NATIVE_COMMAND": str(config / "no-live-adapter.exe"),
        "XPEDITION_TEST_BOUNDARY_TRACE": str(trace),
    }
    process = subprocess.run(
        [sys.executable, "-B", "-c", PUBLIC_ENTRY_WITH_LEDGER_FAULT, *args],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    envelope = json.loads(process.stdout)
    events = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
    assert FAULT_MARKER not in process.stdout + process.stderr
    assert str(config) not in process.stderr
    return process, envelope, events


def _journal_snapshot(path):
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        operations = connection.execute(
            "SELECT operation_id, command, status FROM operations ORDER BY operation_id"
        ).fetchall()
        consumed = connection.execute(
            "SELECT operation_id FROM consumed_tokens ORDER BY operation_id"
        ).fetchall()
    return {"operations": operations, "consumed_operations": consumed}


def test_public_sdk_rejects_replay_when_private_ledger_cannot_be_published(
    tmp_path, record_testsuite_property
):
    config = tmp_path / "public-config"
    config.mkdir()
    scope = tmp_path / "public-scope.json"
    scope.write_text(
        json.dumps(
            {
                "scope_id": "owned-offline-ledger-proof",
                "tool": "xpedition-cli",
                "granted_by": "offline test fixture",
                "granted_at": "2026-10-06T00:00:00Z",
                "expires_at": None,
                "effects": ["observe", "configure", "persist"],
                "dangerous": True,
                "roots": [str(tmp_path.resolve()), str(config.resolve())],
                "outputs": [str(tmp_path.resolve())],
                "destinations": [],
            }
        ),
        encoding="utf-8",
    )
    ledger = config / "confirm-consumed.json"
    ledger.write_bytes(b"\xff")
    knowledge = config / "knowledge-base.json"
    journal = config / "driver-v2" / "operations.sqlite3"
    assert not journal.exists() and not knowledge.exists()
    args = [
        "kb",
        "bind",
        "--name",
        "ledger-proof",
        "--url",
        LINK,
        "--about",
        "offline fixture",
        "--compact",
        "--dangerous",
    ]
    preview, planned, preview_events = _run_public(
        [*args, "--dry-run"], config, scope, tmp_path / "preview-events.jsonl"
    )
    assert preview.returncode == 0 and planned["ok"], planned.get("error")
    assert preview_events == []
    assert not knowledge.exists() and ledger.read_bytes() == b"\xff"
    token = planned["data"]["confirm_token"]
    operation_id = planned["data"]["preview"]["operation_id"]
    before = _journal_snapshot(journal)
    assert before == {
        "operations": [(operation_id, "kb bind", "planned")],
        "consumed_operations": [],
    }

    applied, result, apply_events = _run_public(
        [*args, "--confirm", token], config, scope, tmp_path / "apply-events.jsonl"
    )
    assert applied.returncode == 0 and result["ok"], result.get("error")
    assert token not in applied.stdout + applied.stderr
    assert "ledger_unreadable" in applied.stderr
    assert "ledger_write_failed" in applied.stderr
    assert apply_events == [
        {"boundary": "legacy_claim"},
        {"boundary": "legacy_ledger_publish", "errno": errno.ENOSPC, "winerror": None},
        {"boundary": "knowledge_base_publish"},
    ]
    assert ledger.read_bytes() == b"\xff"
    content = json.loads(knowledge.read_text(encoding="utf-8"))
    assert set(content["documents"]) == {"ledger-proof"}
    assert content["documents"]["ledger-proof"]["url"] == LINK
    assert content["documents"]["ledger-proof"]["about"] == "offline fixture"
    after_apply = _journal_snapshot(journal)
    assert after_apply == {
        "operations": [(operation_id, "kb bind", "succeeded")],
        "consumed_operations": [(operation_id,)],
    }
    binding_bytes = knowledge.read_bytes()
    binding_mtime = knowledge.stat().st_mtime_ns

    repeated, refusal, replay_events = _run_public(
        [*args, "--confirm", token], config, scope, tmp_path / "replay-events.jsonl"
    )
    assert repeated.returncode == 6 and refusal["ok"] is False
    assert refusal["error"]["code"] == "E_CONFLICT"
    assert refusal["error"]["retryable"] is False
    assert refusal["error"]["details"]["conflict"] == "token_used"
    assert refusal["error"]["details"]["dispatch_state"] == "not_started"
    assert refusal["error"]["details"]["operation_id"] == operation_id
    assert token not in repeated.stdout + repeated.stderr
    assert "degraded" not in repeated.stderr
    assert replay_events == []  # SDK refused before the private claim or business write.
    assert knowledge.read_bytes() == binding_bytes
    assert knowledge.stat().st_mtime_ns == binding_mtime
    assert ledger.read_bytes() == b"\xff"
    after_replay = _journal_snapshot(journal)
    assert after_replay == after_apply
    record_testsuite_property(
        "public_sdk_degraded_legacy_ledger_counts",
        json.dumps(
            {
                "operations_before_confirm": len(before["operations"]),
                "consumed_before_confirm": len(before["consumed_operations"]),
                "operations_after_confirm": len(after_apply["operations"]),
                "consumed_after_confirm": len(after_apply["consumed_operations"]),
                "operations_after_replay": len(after_replay["operations"]),
                "consumed_after_replay": len(after_replay["consumed_operations"]),
                "legacy_claims_on_confirm": 1,
                "legacy_claims_on_replay": 0,
                "knowledge_base_publications": 1,
                "operation_id": operation_id,
            },
            sort_keys=True,
        ),
    )
