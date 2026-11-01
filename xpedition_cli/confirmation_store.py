"""Local confirmation bookkeeping; NOT a lock on a design or a transaction.

All cooperating processes using this implementation and configuration directory
serialize ledger check/update. Lock files must never be unlinked: replacing a
locked inode can split the exclusion domain. The pinned spec permits storage
failure degradation, which is explicitly warned about and is not replay-safe.
"""

from __future__ import annotations

import errno
import json
import math
import os
import secrets
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from .errors import CLIError

LOCK_TIMEOUT_SECONDS = 5.0
_THREAD_GATE = threading.Lock()


def _busy() -> CLIError:
    return CLIError(
        "E_CONFLICT",
        "confirmation store is busy; no write was authorized",
        {"stage": "confirmation_lock", "write_attempted": False},
    )


def _try_lock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    """Bounded thread/process exclusion on a persistent sidecar lock file.

    A busy lock is a conflict, never a storage-degradation fallback. OSError
    denotes unavailable storage/locking. The caller decides its storage policy.
    """
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    if not _THREAD_GATE.acquire(timeout=LOCK_TIMEOUT_SECONDS):
        raise _busy()
    fd = None
    locked = False
    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        while True:
            try:
                _try_lock(fd)
                locked = True
                break
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _busy() from error
                time.sleep(min(0.01, remaining))
        yield
    finally:
        if fd is not None:
            if locked:
                with suppress(OSError):
                    _unlock(fd)
            with suppress(OSError):
                os.close(fd)
        _THREAD_GATE.release()


def atomic_write(path: Path, data: bytes) -> None:
    """Publish a complete same-directory file, never truncate the old ledger.

    Flush file contents before replacement. This is not a claim of durability
    through power failure on every filesystem (directory fsync is not portable).
    """
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def machine_secret(path: Path) -> bytes:
    def read() -> bytes:
        value = path.read_bytes()
        if len(value) != 32:
            raise CLIError(
                "E_IO",
                "local confirmation secret is invalid; inspect configuration before writing",
                {"stage": "confirmation_secret", "write_attempted": False},
            )
        return value

    try:
        return read()
    except FileNotFoundError:
        pass
    with exclusive_lock(path.with_name(path.name + ".lock")):
        try:
            return read()  # Another process may have initialized it while we waited.
        except FileNotFoundError:
            value = secrets.token_bytes(32)
            atomic_write(path, value)
            return value


def _warn_degraded(reason: str) -> None:
    # Only internal reason labels; never interpolate paths, tokens or exceptions.
    with suppress(OSError, ValueError):
        print(
            f"WARNING: confirmation replay protection degraded ({reason}); "
            "do not automatically retry writes.",
            file=sys.stderr,
        )


def read_consumed(path: Path) -> dict[str, float]:
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, ValueError):
        _warn_degraded("ledger_unreadable")
        return {}
    if not isinstance(values, dict):
        _warn_degraded("ledger_invalid")
        return {}
    now = time.time()
    retained = {}
    invalid = False
    for key, value in values.items():
        try:
            expiry = float(value)
        except (TypeError, ValueError, OverflowError):
            invalid = True
            continue
        if isinstance(value, bool) or not math.isfinite(expiry):
            invalid = True
        elif expiry > now:
            retained[key] = expiry
    if invalid:
        _warn_degraded("ledger_invalid_entries")
    return retained


def _check_live(expires_at: float) -> None:
    if expires_at <= time.time():
        raise CLIError("E_CONFLICT", "confirmation token has expired; run --dry-run again")


def _check_unused(values: dict[str, float], fingerprint: str) -> None:
    if fingerprint in values:
        raise CLIError("E_CONFLICT", "confirmation token already used; run --dry-run again")


def claim(path: Path, fingerprint: str, expires_at: float) -> None:
    try:
        with exclusive_lock(path.with_name(path.name + ".lock")):
            _check_live(expires_at)  # Expiry may have elapsed while waiting for the lock.
            values = read_consumed(path)
            _check_unused(values, fingerprint)
            values[fingerprint] = expires_at
            try:
                atomic_write(path, json.dumps(values, sort_keys=True).encode("utf-8"))
            except OSError:
                # CLI-SPEC section 9 expressly permits ledger storage degradation.
                _warn_degraded("ledger_write_failed")
    except OSError:
        # Do not perform an unlocked read/modify/write and lose other entries.
        # Still reject a replay present in a readable legacy ledger.
        _warn_degraded("lock_unavailable")
        _check_live(expires_at)
        _check_unused(read_consumed(path), fingerprint)
