"""First authorized Open changes bytes; only its bounded certificate admits diagnostics.

Every CAD fact is produced by the existing fake startup/COM fixtures through the
real public SDK. No Windows process, COM server or native adapter is started.
"""

from __future__ import annotations

import copy
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from xpedition_cli import close_diagnostic as D
from xpedition_cli import closed_copy_startup as S
from xpedition_cli import driver
from xpedition_cli._vendor.tool_driver_sdk import tokens
from xpedition_cli._vendor.tool_driver_sdk.validation import ContractError
from xpedition_cli.errors import CLIError
from xpedition_cli.operation_budget import current_budget

pytest_plugins = ("test_close_diagnostic",)

CHANGED = {"open_changes": True}
DEFERRED = {"defer_start": True, "open_changes": True}


def _qualification(output):
    return ((output.envelope.get("data") or {}).get("extensions") or {}).get(
        "closed_copy_startup"
    ) or {}


def _start(case):
    preview = case.cli.run([*case.start_args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    case.start_confirm_token = preview.envelope["data"]["confirm_token"]
    output = case.cli.run([*case.start_args, "--confirm", case.start_confirm_token])
    case.start_output = output
    case.post_open_token = _qualification(output).get("token")
    receipt = (output.envelope.get("data") or {}).get("receipt")
    if receipt:
        case.start_id = receipt["operation_id"]
    return output


def _args(case, *, token=True):
    args = list(case.args)
    if "--post-open-token" in args:
        index = args.index("--post-open-token")
        del args[index : index + 2]
    selected = case.post_open_token if token is True else token
    return [*args, *(["--post-open-token", selected] if selected else [])]


def _original_bytes(case, operation_id):
    row = (
        case.cli._store_open()
        .connection()
        .execute(
            "SELECT status,receipt,resolution,extra FROM operations WHERE operation_id=?",
            (operation_id,),
        )
        .fetchone()
    )
    return tuple(row) if row else None


def _refused_without_cad(case, *, token=True):
    requests, events = list(case.requests), list(case.events)
    output = case.cli.run([*_args(case, token=token), "--dry-run"])
    assert not output.envelope["ok"], output.envelope
    assert output.envelope["error"]["code"] in {"E_CONFLICT", "E_FORBIDDEN"}, output.envelope
    assert case.requests == requests and case.events == events
    return output


@pytest.mark.parametrize("case", [CHANGED, {"open_changes": False}], indirect=True)
def test_first_open_baseline_preserves_genesis_and_allows_only_limited_diagnostic(case):
    copy_id = case.origin["copy_operation_id"]
    original = _original_bytes(case, copy_id)
    qualification = _qualification(case.start_output)
    assert qualification["token"] == case.post_open_token
    proof = qualification["proof"]
    assert proof["coverage"] == ["project", "board"]
    assert proof["icdb_observed"] is False
    assert proof["persistence_verified"] is False and proof["execution_qualified"] is False
    assert proof["preflight"]["copy_id"] == copy_id
    assert proof["preflight"]["startup_id"] == case.start_id
    assert proof["preflight"]["copy_receipt_sha256"] == D.digest(case.preparation["copy_receipt"])
    assert (
        proof["preflight"]["before"]["board"]["sha256"]
        == hashlib.sha256(case.before_open_bytes).hexdigest()
    )
    assert proof["after"]["board"]["sha256"] == hashlib.sha256(case.after_open_bytes).hexdigest()
    assert {row["role"] for row in proof["file_changes"]} == (
        {"board"} if case.open_changes else set()
    )
    claims = tokens.verify(case.cli._store_open().secret(), case.post_open_token, 0)
    assert claims["purpose"] == "closed-copy-first-open-baseline-v1"
    assert sum(method == "open" for method, _ in case.startup.calls) == 1
    assert sum(method == "start" for method, _ in case.startup.calls) == 1
    assert case.before_open_bytes is not None and case.after_open_bytes is not None
    assert (case.before_open_bytes != case.after_open_bytes) is case.open_changes
    assert case.board.read_bytes() == case.after_open_bytes
    # The producer's copy receipt is immutable; a startup does not rewrite genesis.
    assert case.cli._store_open().get(copy_id)["receipt"] == case.preparation["copy_receipt"]
    preview = case.cli.run([*_args(case), "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    output = case.cli.run([*_args(case), "--confirm", preview.envelope["data"]["confirm_token"]])
    assert output.envelope["error"]["code"] == "E_OUTCOME_UNKNOWN", output.envelope
    details = output.envelope["error"]["details"]
    assert details["diagnostic_complete"] is True
    receipt = details["receipt"]
    observation = receipt["extensions"]["independent_readback"]
    assert observation["persistence_verified"] is False
    assert observation["execution_qualified"] is False
    assert receipt["outcome"]["persistence"] == "unknown"
    assert len(observation["frames"]) == 2
    assert all(len(frame["files"]) == 12 for frame in observation["frames"])
    assert all("icdb" not in role.lower() for role in observation["frames"][0]["files"])
    assert case.events.count("Document.Save") == case.events.count("Document.Close(True)") == 1
    assert _original_bytes(case, copy_id) == original
    diagnostic_id = receipt["operation_id"]
    saved = _original_bytes(case, diagnostic_id)
    _refused_without_cad(case)
    assert _original_bytes(case, diagnostic_id) == saved


@pytest.mark.parametrize("case", [DEFERRED], indirect=True)
def test_locked_target_icdb_is_not_new_required_coverage(case, monkeypatch):
    target = case.project.parent / "database/icdb.dat"
    assert target.is_file()
    actual_open = Path.open
    attempts = []

    def locked(path, *args, **kwargs):
        if Path(path) == target:
            attempts.append(str(path))
            raise PermissionError("synthetic live icdb sharing lock")
        return actual_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", locked)
    output = _start(case)
    assert output.envelope["ok"] and case.post_open_token, output.envelope
    preview = case.cli.run([*_args(case), "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    result = case.cli.run([*_args(case), "--confirm", preview.envelope["data"]["confirm_token"]])
    assert result.envelope["error"]["details"]["diagnostic_complete"] is True, result.envelope
    assert attempts == []


@pytest.mark.parametrize("case", [DEFERRED], indirect=True)
@pytest.mark.parametrize("role", ["project", "board"])
def test_prestart_genesis_drift_never_dispatches_start_or_open(case, role):
    preview = case.cli.run([*case.start_args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    path = getattr(case, role)
    path.write_bytes(path.read_bytes() + b"\nexternal prestart writer\n")
    before = list(case.startup.calls)
    result = case.cli.run(
        [*case.start_args, "--confirm", preview.envelope["data"]["confirm_token"]]
    )
    assert not result.envelope["ok"], result.envelope
    assert not _qualification(result).get("token")
    calls = [method for method, _ in case.startup.calls[len(before) :]]
    assert "start" not in calls and "open" not in calls
    assert "Document.Save" not in case.events


@pytest.mark.parametrize("case", [DEFERRED, {"defer_start": True}], indirect=True)
@pytest.mark.parametrize(
    "fault",
    [
        "open_failed",
        "open_timeout",
        "response_schema",
        "certificate_schema",
        "commit_late",
        "sign_late",
        "render_late",
    ],
)
def test_incomplete_or_late_startup_never_authorizes_via_token_or_genesis(case, monkeypatch, fault):
    preview = case.cli.run([*case.start_args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope

    def expire():
        budget = current_budget()
        budget.clock = lambda: budget.deadline + 1

    if fault in {"open_failed", "open_timeout"}:
        invoke = case.backend._invoke

        def failed_open(method, params, **kwargs):
            result = invoke(method, params, **kwargs)
            if method == "open":
                raise CLIError(
                    "E_TIMEOUT" if fault == "open_timeout" else "E_NATIVE_FAILURE",
                    "synthetic Open reply unavailable",
                    {"native_request_dispatched": True},
                )
            return result

        monkeypatch.setattr(case.backend, "_invoke", failed_open)
    elif fault in {"response_schema", "certificate_schema"}:
        validate = case.cli.validator.validate

        def rejected(kind, value):
            if kind == "response":
                data = value.get("data") or {}
                receipt = data.get("receipt") or {}
                startup = (receipt.get("target", {}).get("extensions") or {}).get(
                    "command"
                ) == "session start"
                certificate = ((data.get("extensions") or {}).get("closed_copy_startup") or {}).get(
                    "token"
                )
                if startup and (fault == "response_schema" or certificate):
                    raise ContractError("synthetic startup final-response rejection")
            return validate(kind, value)

        monkeypatch.setattr(case.cli.validator, "validate", rejected)
    elif fault == "commit_late":
        finalize = S.finalize

        def delayed_commit(*args, **kwargs):
            store = args[1]
            transaction = store._transaction

            @contextmanager
            def late():
                with transaction() as connection:
                    yield connection
                expire()

            with monkeypatch.context() as scoped:
                scoped.setattr(store, "_transaction", late)
                return finalize(*args, **kwargs)

        monkeypatch.setattr(S, "finalize", delayed_commit)
    elif fault == "sign_late":
        issue = tokens.issue

        def delayed_sign(secret, claims):
            value = issue(secret, claims)
            if claims.get("purpose") == "closed-copy-first-open-baseline-v1":
                expire()
            return value

        monkeypatch.setattr(tokens, "issue", delayed_sign)
    else:
        render = case.cli._render

        def delayed_render(envelope, flags):
            value = render(envelope, flags)
            if (
                ((envelope.get("data") or {}).get("extensions") or {}).get("closed_copy_startup")
                or {}
            ).get("token"):
                expire()
            return value

        monkeypatch.setattr(case.cli, "_render", delayed_render)
    output = case.cli.run(
        [*case.start_args, "--confirm", preview.envelope["data"]["confirm_token"]]
    )
    assert not _qualification(output).get("token"), output.envelope
    assert json.loads(output.stdout) == output.envelope
    assert b'"token": "ct_' not in output.stdout
    assert sum(method == "open" for method, _ in case.startup.calls) == 1
    _refused_without_cad(case, token=None)


@pytest.mark.parametrize("case", [CHANGED], indirect=True)
@pytest.mark.parametrize("stage", ["preview", "confirm", "worker"])
@pytest.mark.parametrize("role", ["project", "board"])
def test_postbaseline_drift_refuses_at_each_presave_boundary(case, monkeypatch, stage, role):
    path = getattr(case, role)

    def change():
        path.write_bytes(path.read_bytes() + b"\nwriter outside authorized Open\n")

    if stage == "preview":
        change()
        _refused_without_cad(case)
        return
    preview = case.cli.run([*_args(case), "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    if stage == "confirm":
        change()
    else:
        invoke = case.backend._invoke

        def changed_before_worker(method, params, **kwargs):
            if method == "close_diagnostic":
                change()
            return invoke(method, params, **kwargs)

        monkeypatch.setattr(case.backend, "_invoke", changed_before_worker)
    result = case.cli.run([*_args(case), "--confirm", preview.envelope["data"]["confirm_token"]])
    assert not result.envelope["ok"], result.envelope
    assert "Document.Save" not in case.events and "Document.Close(True)" not in case.events


@pytest.mark.parametrize("case", [CHANGED], indirect=True)
@pytest.mark.parametrize(
    "change",
    [
        "scope",
        "candidate",
        "process",
        "journal",
        "missing_token",
        "tampered_token",
        "wrong_purpose",
        "wrong_proof",
    ],
)
def test_postopen_certificate_rejects_foreign_binding_without_cad(case, monkeypatch, change):
    original = _original_bytes(case, case.start_id)
    if change == "scope":
        path = case.tmp_path / "scope.json"
        grant = json.loads(path.read_text())
        grant["granted_by"] = "different authority with same scope ID"
        path.write_text(json.dumps(grant))
    elif change == "candidate":
        real = S.adapter_identity

        def changed():
            identity = copy.deepcopy(real())
            identity["build_sha256"] = "f" * 64
            return identity

        monkeypatch.setattr(S, "adapter_identity", changed)
    elif change == "process":
        case.startup.identities[22220]["creation_time_100ns"] = "133000000099999999"
    elif change == "journal":
        with driver.runtime(
            driver.Adapter(case.backend), state_dir=case.tmp_path / "other-journal"
        ) as cli:
            before = list(case.requests)
            result = cli.run([*_args(case), "--dry-run"])
            assert not result.envelope["ok"], result.envelope
            assert case.requests == before
        return
    elif change == "missing_token":
        _refused_without_cad(case, token=None)
        return
    elif change in {"wrong_purpose", "wrong_proof"}:
        secret = case.cli._store_open().secret()
        claims = tokens.verify(secret, case.post_open_token, 0)
        claims["purpose" if change == "wrong_purpose" else "proof_sha256"] = "wrong-binding"
        _refused_without_cad(case, token=tokens.issue(secret, claims))
        return
    else:
        body, mac = case.post_open_token.split(".")
        bad = body + "." + ("A" if mac[0] != "A" else "B") + mac[1:]
        _refused_without_cad(case, token=bad)
        return
    _refused_without_cad(case)
    assert _original_bytes(case, case.start_id) == original


@pytest.mark.parametrize("case", [CHANGED], indirect=True)
@pytest.mark.parametrize("terminal", ["dispatched", "unknown"])
def test_postopen_baseline_does_not_hide_a_related_writer(case, monkeypatch, terminal):
    from test_close_diagnostic import test_public_diagnostic_rejects_alias_dispatched_after_revision

    test_public_diagnostic_rejects_alias_dispatched_after_revision(case, monkeypatch, terminal)


@pytest.mark.parametrize("case", [CHANGED], indirect=True)
def test_certificate_cannot_survive_source_receipt_pruning(case, monkeypatch):
    store = case.cli._store_open()
    monkeypatch.setattr(store, "clock", lambda: 10**12)
    monkeypatch.setattr(store, "retain_days", 0)
    store.prune()
    assert store.get(case.origin["copy_operation_id"]) is None
    assert store.get(case.start_id) is None
    _refused_without_cad(case)


@pytest.mark.parametrize("case", [CHANGED], indirect=True)
def test_removed_copy_slot_cannot_launder_old_startup_or_certificate(case):
    from xpedition_cli import pcb_document_guard as G

    store = case.cli._store_open()
    copy_id = case.origin["copy_operation_id"]
    original = _original_bytes(case, case.start_id)
    store.connection().execute(
        "DELETE FROM pcb_close_qualification_v1 WHERE copy_operation_id=?", (copy_id,)
    )
    _refused_without_cad(case)
    # Even a newly registered original copy receipt cannot supply the lost
    # first-Open chain; this is not an adoption path for historical startups.
    G.register_copy_qualification(store, case.preparation["copy_receipt"])
    _refused_without_cad(case)
    _refused_without_cad(case, token=None)
    assert _original_bytes(case, case.start_id) == original


@pytest.mark.parametrize("case", [CHANGED], indirect=True)
@pytest.mark.parametrize("fault", ["scope", "proof"])
def test_worker_revalidates_transition_chain_before_save(case, monkeypatch, fault):
    preview = case.cli.run([*_args(case), "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    invoke = case.backend._invoke

    def changed_worker_input(method, params, **kwargs):
        if method == "close_diagnostic":
            if fault == "scope":
                path = case.tmp_path / "scope.json"
                value = json.loads(path.read_text())
                value["granted_by"] = "changed between controller and worker"
                path.write_text(json.dumps(value))
            else:
                params = copy.deepcopy(params)
                transition = params["binding"]["copy_proof"]["post_open_transition"]
                transition["proof"]["after"]["board"]["sha256"] = "0" * 64
        return invoke(method, params, **kwargs)

    monkeypatch.setattr(case.backend, "_invoke", changed_worker_input)
    output = case.cli.run([*_args(case), "--confirm", preview.envelope["data"]["confirm_token"]])
    assert not output.envelope["ok"], output.envelope
    assert "Document.Save" not in case.events and "Document.Close(True)" not in case.events
    _refused_without_cad(case)


@pytest.mark.parametrize("case", [CHANGED], indirect=True)
@pytest.mark.parametrize("role", ["project", "board"])
def test_identical_bytes_in_replaced_physical_file_are_not_the_baseline(case, role):
    import os

    path = getattr(case, role)
    before = path.stat().st_ino
    replacement = path.with_name("replacement-" + path.name)
    replacement.write_bytes(path.read_bytes())
    assert replacement.stat().st_ino != before
    os.replace(replacement, path)
    _refused_without_cad(case)


@pytest.mark.parametrize("case", [DEFERRED], indirect=True)
@pytest.mark.parametrize("calls", ["missing", "duplicate", "other_writer"])
def test_startup_without_exactly_one_observed_open_cannot_publish(case, monkeypatch, calls):
    invoke = case.backend._invoke
    if calls == "missing":
        # A successful-looking fake native reply with no request telemetry is
        # not proof of the one authorized Open. Production retains its wrapper.
        monkeypatch.setattr(case.backend, "invoke", invoke)
    else:
        from xpedition_cli.operation_budget import native_call_budget

        def two_open_requests(method, params, **kwargs):
            reply = invoke(method, params, **kwargs)
            if method == "open":
                extra = "open" if calls == "duplicate" else "save"
                with native_call_budget(extra, kwargs.get("timeout_seconds", 30.0)):
                    if calls == "duplicate":
                        invoke(method, params, **kwargs)
                    else:
                        case.board.write_bytes(case.board.read_bytes() + b"unapproved writer")
            return reply

        monkeypatch.setattr(case.backend, "_invoke", two_open_requests)
    output = _start(case)
    assert not _qualification(output).get("token"), output.envelope
    _refused_without_cad(case, token=None)


@pytest.mark.parametrize("case", [DEFERRED], indirect=True)
def test_proven_undispatched_sdk_refusal_releases_only_its_preflight_for_retry(case, monkeypatch):
    from xpedition_cli._vendor.tool_driver_sdk.store import TokenUsed

    preview = case.cli.run([*case.start_args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    token = preview.envelope["data"]["confirm_token"]
    operation_id = preview.envelope["data"]["preview"]["operation_id"]
    original = _original_bytes(case, operation_id)
    store = case.cli._store_open()
    copy_id = case.origin["copy_operation_id"]

    def not_dispatched(*args, **kwargs):
        raise TokenUsed("synthetic SDK refusal before dispatch")

    with monkeypatch.context() as scoped:
        scoped.setattr(store, "dispatch", not_dispatched)
        refusal = case.cli.run([*case.start_args, "--confirm", token])
    assert not refusal.envelope["ok"], refusal.envelope
    assert _original_bytes(case, operation_id) == original
    row = (
        store.connection()
        .execute(
            "SELECT startup_operation_id,used_by,startup_preflight,startup_baseline "
            "FROM pcb_close_qualification_v1 WHERE copy_operation_id=?",
            (copy_id,),
        )
        .fetchone()
    )
    assert tuple(row) == (None, None, None, None)
    assert not any(method in {"start", "open"} for method, _ in case.startup.calls)
    retried = case.cli.run([*case.start_args, "--confirm", token])
    assert retried.envelope["ok"] and _qualification(retried).get("token"), retried.envelope
    assert sum(method == "open" for method, _ in case.startup.calls) == 1
    assert sum(method == "start" for method, _ in case.startup.calls) == 1


@pytest.mark.parametrize("case", [DEFERRED], indirect=True)
@pytest.mark.parametrize("mismatch", ["preflight", "used_by", "startup_id", "sdk_started"])
def test_undispatched_cleanup_never_clears_a_mismatched_claim(case, monkeypatch, mismatch):
    from xpedition_cli._vendor.tool_driver_sdk.store import TokenUsed

    preview = case.cli.run([*case.start_args, "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    token = preview.envelope["data"]["confirm_token"]
    operation_id = preview.envelope["data"]["preview"]["operation_id"]
    store = case.cli._store_open()
    copy_id = case.origin["copy_operation_id"]
    retained = []

    def row():
        return tuple(
            store.connection()
            .execute(
                "SELECT startup_operation_id,used_by,startup_preflight,startup_baseline "
                "FROM pcb_close_qualification_v1 WHERE copy_operation_id=?",
                (copy_id,),
            )
            .fetchone()
        )

    def inconsistent_refusal(*args, **kwargs):
        if mismatch == "sdk_started":
            store.connection().execute(
                "UPDATE operations SET status='unknown',started_at=1 WHERE operation_id=?",
                (operation_id,),
            )
        else:
            column, value = {
                "preflight": ("startup_preflight", '{"tampered":true}'),
                "used_by": ("used_by", "op-competing-writer"),
                "startup_id": ("startup_operation_id", "op-competing-startup"),
            }[mismatch]
            store.connection().execute(
                f"UPDATE pcb_close_qualification_v1 SET {column}=? WHERE copy_operation_id=?",
                (value, copy_id),
            )
        retained.append(row())
        raise TokenUsed("synthetic refusal with conflicting durable evidence")

    with monkeypatch.context() as scoped:
        scoped.setattr(store, "dispatch", inconsistent_refusal)
        refusal = case.cli.run([*case.start_args, "--confirm", token])
    assert not refusal.envelope["ok"], refusal.envelope
    assert row() == retained[0]
    retried = case.cli.run([*case.start_args, "--confirm", token])
    assert not retried.envelope["ok"], retried.envelope
    assert not any(method in {"start", "open"} for method, _ in case.startup.calls)
    assert row() == retained[0]


@pytest.mark.parametrize("case", [DEFERRED], indirect=True)
@pytest.mark.parametrize("role", ["project", "board"])
def test_covered_file_unreadable_after_open_never_gets_a_baseline(case, monkeypatch, role):
    target = getattr(case, role)
    invoke, actual_open = case.backend._invoke, Path.open
    blocked = False
    attempts = []

    def returned_open(method, params, **kwargs):
        nonlocal blocked
        result = invoke(method, params, **kwargs)
        if method == "open":
            blocked = True
        return result

    def unreadable(path, *args, **kwargs):
        if blocked and Path(path) == target:
            attempts.append(str(path))
            raise PermissionError("synthetic covered-file read failure after Open")
        return actual_open(path, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(case.backend, "_invoke", returned_open)
        scoped.setattr(Path, "open", unreadable)
        output = _start(case)
    assert attempts
    assert not _qualification(output).get("token"), output.envelope
    assert sum(method == "open" for method, _ in case.startup.calls) == 1
    _refused_without_cad(case, token=None)
