from __future__ import annotations

import errno
import hashlib
import json
import multiprocessing
import os
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier

import pytest

from xpedition_cli import confirm
from xpedition_cli import confirmation_store as store
from xpedition_cli.errors import CLIError

SCOPE = {"operation": "test-only", "project": "synthetic", "backend": "mock"}


def _fingerprint(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    return tmp_path / "config"


def _worker(directory, pipe):
    os.environ["XPEDITION_CLI_CONFIG_DIR"] = directory
    pipe.send("ready")
    action, token = pipe.recv()
    # Widen the original read/check/write window without synchronizing *inside*
    # the new lock (that would deliberately deadlock a correct implementation).
    if action == "consume":
        if hasattr(confirm, "_prune_consumed"):
            original = confirm._prune_consumed

            def delayed_legacy():
                value = original()
                time.sleep(0.03)
                return value

            confirm._prune_consumed = delayed_legacy
        else:
            original = store.read_consumed

            def delayed_read(path):
                value = original(path)
                time.sleep(0.03)
                return value

            store.read_consumed = delayed_read
    if action == "issue":
        original_random = secrets.token_bytes

        def delayed_secret(size=None):
            if size == 32:
                time.sleep(0.03)
            return original_random(size)

        secrets.token_bytes = delayed_secret
    try:
        if action == "issue":
            result = confirm.issue(SCOPE)[0]
        else:
            confirm.consume(token, SCOPE)
            result = "accepted"
    except CLIError as error:
        result = error.code
    pipe.send(result)
    pipe.close()
    if action == "consume_and_exit":
        os._exit(0)


def _parallel(directory, actions):
    context = multiprocessing.get_context("spawn")
    workers = []
    try:
        for _ in actions:
            parent, child = context.Pipe()
            process = context.Process(target=_worker, args=(str(directory), child))
            process.start()
            child.close()
            workers.append((process, parent))
        for _, pipe in workers:
            assert pipe.poll(20), "worker did not start"
            assert pipe.recv() == "ready"
        for (_, pipe), action in zip(workers, actions, strict=True):
            pipe.send(action)
        results = []
        for process, pipe in workers:
            assert pipe.poll(20), "worker did not finish"
            results.append(pipe.recv())
            process.join(10)
            assert process.exitcode == 0
        return results
    finally:
        for process, pipe in workers:
            if process.is_alive():
                process.terminate()
            process.join(10)
            pipe.close()


def _holder(path, pipe):
    with store.exclusive_lock(Path(path)):
        pipe.send("locked")
        pipe.recv()
    pipe.close()


@contextmanager
def _held_lock(path):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_holder, args=(str(path), child))
    process.start()
    child.close()
    try:
        assert parent.poll(20)
        assert parent.recv() == "locked"
        yield process
    finally:
        if process.is_alive():
            parent.send("release")
            process.join(10)
        if process.is_alive():
            process.terminate()
        process.join(10)
        parent.close()


@pytest.mark.parametrize("round_number", range(3))
def test_one_token_has_one_winner_across_processes(config, round_number):
    token, _ = confirm.issue(SCOPE)
    results = _parallel(config, [("consume", token)] * 4)
    assert results.count("accepted") == 1, results
    assert results.count("E_CONFLICT") == 3, results
    assert _fingerprint(token) in json.loads((config / "confirm-consumed.json").read_text())


def test_distinct_concurrent_tokens_do_not_lose_ledger_entries(config):
    tokens = [confirm.issue(SCOPE)[0] for _ in range(8)]
    assert _parallel(config, [("consume", token) for token in tokens]) == ["accepted"] * 8
    recorded = json.loads((config / "confirm-consumed.json").read_text())
    assert set(recorded) == {_fingerprint(token) for token in tokens}
    for token in tokens:
        with pytest.raises(CLIError, match="already used"):
            confirm.consume(token, SCOPE)


def test_threads_also_have_one_winner(config):
    token, _ = confirm.issue(SCOPE)
    barrier = Barrier(8)

    def attempt(_):
        barrier.wait(timeout=10)
        try:
            confirm.consume(token, SCOPE)
            return "accepted"
        except CLIError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert results.count("accepted") == 1
    assert results.count("E_CONFLICT") == 7


def test_concurrent_first_use_does_not_overwrite_the_secret(config):
    tokens = _parallel(config, [("issue", None)] * 6)
    assert len((config / "confirm.secret").read_bytes()) == 32
    assert len(set(tokens)) == 6
    for token in tokens:
        confirm.consume(token, SCOPE)


def test_busy_lock_is_conflict_not_degraded_permission_to_write(config, monkeypatch, capsys):
    token, _ = confirm.issue(SCOPE)
    monkeypatch.setattr(store, "LOCK_TIMEOUT_SECONDS", 0.05)
    with _held_lock(config / "confirm-consumed.json.lock"):
        with pytest.raises(CLIError) as failure:
            confirm.consume(token, SCOPE)
    assert failure.value.code == "E_CONFLICT"
    assert failure.value.exit_code == 6 and not failure.value.is_retryable
    assert failure.value.details == {"stage": "confirmation_lock", "write_attempted": False}
    assert not (config / "confirm-consumed.json").exists()
    assert "degraded" not in capsys.readouterr().err
    confirm.consume(token, SCOPE)  # Contention did not consume this token.


def test_terminated_owner_releases_lock_without_unlinking(config):
    token, _ = confirm.issue(SCOPE)
    lock = config / "confirm-consumed.json.lock"
    with _held_lock(lock) as owner:
        owner.terminate()
        owner.join(10)
        assert not owner.is_alive()
        assert lock.exists()
        confirm.consume(token, SCOPE)
    assert lock.exists()


def test_abrupt_exit_after_claim_keeps_token_consumed(config):
    token, _ = confirm.issue(SCOPE)
    assert _parallel(config, [("consume_and_exit", token)]) == ["accepted"]
    with pytest.raises(CLIError, match="already used"):
        confirm.consume(token, SCOPE)


def test_expiry_is_rechecked_after_acquiring_lock(config, monkeypatch):
    token, _ = confirm.issue(SCOPE)
    expiry = confirm._decode(token)["expires_at"]

    @contextmanager
    def acquired_after_expiry(path):
        monkeypatch.setattr(store.time, "time", lambda: expiry)
        yield

    monkeypatch.setattr(store, "exclusive_lock", acquired_after_expiry)
    with pytest.raises(CLIError, match="expired"):
        confirm.consume(token, SCOPE)
    assert not (config / "confirm-consumed.json").exists()


@pytest.mark.parametrize("bad", [b"", b"short", b"x" * 33])
def test_corrupt_secret_is_not_rotated_or_used(config, bad):
    config.mkdir()
    path = config / "confirm.secret"
    path.write_bytes(bad)
    with pytest.raises(CLIError) as failure:
        confirm.issue(SCOPE)
    assert failure.value.code == "E_IO"
    assert path.read_bytes() == bad


@pytest.mark.parametrize("bad", [b"{", b"[]", b"null", b"\xff"])
def test_damaged_ledger_degrades_explicitly_and_can_be_rebuilt(config, capsys, bad):
    token, _ = confirm.issue(SCOPE)
    ledger = config / "confirm-consumed.json"
    ledger.write_bytes(bad)
    confirm.consume(token, SCOPE)
    streams = capsys.readouterr()
    assert streams.out == "" and "replay protection degraded" in streams.err
    assert token not in streams.err
    assert _fingerprint(token) in json.loads(ledger.read_text())
    with pytest.raises(CLIError, match="already used"):
        confirm.consume(token, SCOPE)


def test_legacy_entries_survive_while_bad_and_expired_entries_are_pruned(config, capsys):
    token, _ = confirm.issue(SCOPE)
    ledger = config / "confirm-consumed.json"
    ledger.write_text(json.dumps({"old": time.time() + 600, "expired": 1, "bad": [], "nan": "NaN"}))
    confirm.consume(token, SCOPE)
    assert set(json.loads(ledger.read_text())) == {"old", _fingerprint(token)}
    assert "ledger_invalid_entries" in capsys.readouterr().err


def test_failed_replacement_preserves_old_ledger_and_warns_without_secrets(
    config, monkeypatch, capsys
):
    consumed, _ = confirm.issue(SCOPE)
    fresh, _ = confirm.issue(SCOPE)
    confirm.consume(consumed, SCOPE)
    ledger = config / "confirm-consumed.json"
    before = ledger.read_bytes()

    def full_disk(*args):
        raise OSError(errno.ENOSPC, "private-path-or-token-must-not-appear")

    monkeypatch.setattr(store.os, "replace", full_disk)
    confirm.consume(fresh, SCOPE)  # Pinned spec's explicit storage-degradation policy.
    assert ledger.read_bytes() == before
    assert not list(config.glob("*.tmp"))
    streams = capsys.readouterr()
    assert streams.out == "" and "ledger_write_failed" in streams.err
    assert fresh not in streams.err and "private-path" not in streams.err
    with pytest.raises(CLIError, match="already used"):
        confirm.consume(consumed, SCOPE)


def test_unavailable_lock_does_not_rewrite_ledger_or_ignore_known_replays(
    config, monkeypatch, capsys
):
    consumed, _ = confirm.issue(SCOPE)
    fresh, _ = confirm.issue(SCOPE)
    confirm.consume(consumed, SCOPE)
    ledger = config / "confirm-consumed.json"
    before = ledger.read_bytes()

    @contextmanager
    def unavailable(path):
        raise OSError(errno.EROFS, "private-path")
        yield  # pragma: no cover - contextmanager's generator signature

    monkeypatch.setattr(store, "exclusive_lock", unavailable)
    confirm.consume(fresh, SCOPE)
    assert ledger.read_bytes() == before
    with pytest.raises(CLIError, match="already used"):
        confirm.consume(consumed, SCOPE)
    streams = capsys.readouterr()
    assert streams.out == "" and "lock_unavailable" in streams.err
    assert "private-path" not in streams.err and fresh not in streams.err


def test_expired_and_mismatched_tokens_do_not_enter_claim(config, monkeypatch):
    token, _ = confirm.issue(SCOPE)

    def unexpected(*args):
        raise AssertionError("invalid token reached the consumption store")

    monkeypatch.setattr(confirm, "claim", unexpected)
    with pytest.raises(CLIError, match="does not match"):
        confirm.consume(token, {**SCOPE, "project": "different"})
    expired = confirm._encode({"scope_hash": confirm._scope_hash(SCOPE), "expires_at": 1})
    with pytest.raises(CLIError, match="expired"):
        confirm.consume(expired, SCOPE)


@pytest.mark.parametrize("bad", [None, [], {}, "bad", "NaN", float("inf"), True])
def test_nonfinite_or_malformed_expiry_is_conflict(config, bad):
    token = confirm._encode({"scope_hash": confirm._scope_hash(SCOPE), "expires_at": bad})
    with pytest.raises(CLIError) as failure:
        confirm.consume(token, SCOPE)
    assert failure.value.code == "E_CONFLICT"
    assert not (config / "confirm-consumed.json").exists()


def test_non_ascii_signature_is_a_conflict_not_an_unhandled_exception(config):
    token, _ = confirm.issue(SCOPE)
    with pytest.raises(CLIError) as failure:
        confirm.consume(token.rsplit(".", 1)[0] + ".中文", SCOPE)
    assert failure.value.code == "E_CONFLICT"


def test_alternate_base64_spelling_cannot_bypass_consumption_fingerprint(config):
    token, _ = confirm.issue(SCOPE)
    confirm.consume(token, SCOPE)
    body, signature = token.rsplit(".", 1)
    with pytest.raises(CLIError) as failure:
        confirm.consume(body + "====." + signature, SCOPE)
    assert failure.value.code == "E_CONFLICT"


def test_new_ledger_files_are_owner_only_on_posix(config):
    token, _ = confirm.issue(SCOPE)
    confirm.consume(token, SCOPE)
    if os.name == "posix":
        for name in (
            "confirm.secret",
            "confirm.secret.lock",
            "confirm-consumed.json",
            "confirm-consumed.json.lock",
        ):
            assert (config / name).stat().st_mode & 0o777 == 0o600
