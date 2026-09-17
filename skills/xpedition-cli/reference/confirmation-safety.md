# Confirmation concurrency and its limits

A confirmation is an authorization to attempt one operation, not proof that it
completed. Never alter, regenerate by hand, or replay a token after an uncertain
write. Observe the project before requesting a new preview.

## What the local store protects

Cooperating CLI processes sharing the same configuration directory now serialize
checking and recording a consumed token under one cross-process lock. A thread
gate also serializes callers within a process. POSIX uses `flock`; Windows uses a
nonblocking byte-range lock through `msvcrt`. Lock wait is bounded; contention
returns `E_CONFLICT` with stage `confirmation_lock` and `write_attempted: false`
for THIS invocation. Another invocation may already have used the token.

The lock covers bookkeeping only and is released before native execution. The
ledger is updated by a same-directory temporary file, file flush and replacement,
not in-place truncation. Sidecar lock files persist and must not be deleted while
any caller may be running. Process exit releases the OS lock; no stale-PID deletion
or age-based forced unlock is used. Expiry is rechecked after acquiring the lock.

First-use secret creation is serialized and atomically published too. An existing
invalid-length secret is not silently rotated or used to sign tokens. Legitimate
issued token format, signature, scope hash, TTL and the existing JSON ledger format
are unchanged. Noncanonical base64 representations are rejected, so spelling the
same signed payload differently cannot create a new consumption fingerprint.

## Storage failure is explicitly degraded, not replay-safe

The pinned CLI-SPEC section 9 permits ledger storage failures without blocking the
write. This patch preserves that policy rather than silently changing the shared
contract. An unreadable/invalid ledger, unavailable lock storage, or failed ledger
replacement emits a fixed, secret-free warning on stderr. Known consumed tokens
in a readable ledger are still rejected. When the lock is unavailable, no unsafe
unlocked ledger rewrite is attempted. A failed replacement leaves the old ledger
untouched and removes the temporary file when possible.

In that degraded mode a fresh token can be accepted without a durable consumption
record, and replay protection is NOT guaranteed. Stop automatic retries and inspect
the storage/environment. Changing this to fail-closed needs a specification policy
change and consumer migration, not a private override in this tool.

## Not covered

This is not a per-project write lock, distributed lock, exactly-once native action,
rollback protocol, or operation journal. Different valid tokens can still authorize
concurrent design operations. Old binaries do not cooperate with the new sidecar
lock; do not mix versions concurrently against a shared configuration directory.
Local filesystems on the CI platforms are tested, not arbitrary network shares.
A file flush plus replacement is not a universal power-loss durability guarantee.
No licensed Xpedition application or production design is used by these tests.

The tests include spawned processes, threads, concurrent secret initialization,
process termination, persisted replay rejection, corrupted stores, failed writes,
legacy records, expired tokens and real CLI invocations on temporary Mock projects.
