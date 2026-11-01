"""Scope/token controlled external owner method; never selects a CAD application.

This is an acceptance method, not a public CLI capability. The native child owns
all COM references and release decisions. A consumed token is never refunded,
including a Popen failure. Timeout never terminates the child or CAD process.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.native_acceptance.owner_support import (  # noqa: E402
    CriticalInputDrift,
    OwnerPreflightError,
    child_environment,
    require_callables,
    strict_file_pin,
)
from xpedition_cli._vendor.tool_driver_sdk import tokens  # noqa: E402
from xpedition_cli._vendor.tool_driver_sdk.scope import ScopeResolver, forbidden  # noqa: E402
from xpedition_cli._vendor.tool_driver_sdk.store import (  # noqa: E402
    Blocked,
    BudgetSpent,
    StateStore,
    TokenUsed,
    target_key,
)
from xpedition_cli._vendor.tool_driver_sdk.validation import ContractValidator  # noqa: E402

SPEC = ROOT / "xpedition_cli/_vendor/spec"
CAPABILITY = "external.owner.no_value_lifecycle"
COMMAND = "external native owner control"
EFFECTS = ["observe", "session", "artifact", "change", "persist"]
LIMITS = dict(
    Activation=1, Open=1, Load=1, UnLoad=1, Close=1, Quit=1, ValuePUT=0, SynchCES=0, Save=0
)


def _noedit_receipt(*args):
    return _receipt(*args)


@dataclass(frozen=True)
class ControlProfile:
    """Compiled method configuration. It is never constructed from JSON input."""

    kind: str
    capability: str
    command: str
    limits: Mapping[str, int]
    native_filename: str
    request_schema: str
    result_schema: str
    receipt_builder: Callable
    plan_summary: str = ""
    constraints: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()

    def __post_init__(self):
        allowed = {
            "no_value": (
                "owner_lifecycle.py",
                "owned-native-control-request/v1",
                "owned-native-control-result/v1",
                LIMITS,
            ),
            "single_field_value_put": (
                "owner_field.py",
                "owned-native-field-request/v1",
                "owned-native-field-result/v1",
                dict(LIMITS, Open=2, Close=2, ValuePUT=1, Save=1),
            ),
        }
        expected = allowed.get(self.kind)
        actual = (self.native_filename, self.request_schema, self.result_schema, dict(self.limits))
        if expected is None or actual != expected or not callable(self.receipt_builder):
            raise ValueError("Unsupported compiled native method profile")
        if not self.capability or not self.command:
            raise ValueError("Compiled profile capability/command missing")
        object.__setattr__(self, "limits", MappingProxyType(dict(self.limits)))
        object.__setattr__(self, "constraints", tuple(self.constraints))
        object.__setattr__(self, "unknowns", tuple(self.unknowns))


NOEDIT_PROFILE = ControlProfile(
    kind="no_value",
    capability=CAPABILITY,
    command=COMMAND,
    limits=LIMITS,
    native_filename="owner_lifecycle.py",
    request_schema="owned-native-control-request/v1",
    result_schema="owned-native-control-result/v1",
    receipt_builder=_noedit_receipt,
    plan_summary=(
        "One activation/Open/Load/read/UnLoad; conditional normal Close(False)/Quit only."
    ),
    constraints=(
        "No Value PUT, SynchCES, Save, replay, timeout kill, ROT selection "
        "or production capability.",
    ),
    unknowns=("Load has no read-only parameter and may produce native auxiliary file changes.",),
)


class ControlError(RuntimeError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.details = code, details


def utc():
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def file_pin(path):
    path = Path(path).resolve()
    raw = path.read_bytes()
    return dict(path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def target(path):
    return dict(
        mode="file",
        engine_id="xpedition-pcb",
        instance_id=None,
        object_id="Board1",
        locator=Path(path).resolve().as_uri(),
    )


def target_keys(contract):
    # Same actual file/project and global engine identities as the existing SDK;
    # no alias, new engine, excluded operation or replacement state directory.
    keys = [
        target_key(dict(target(contract[name]), mode=mode))
        for mode in ("file", "session")
        for name in ("project", "board")
    ]
    keys.append(
        target_key(
            dict(
                mode="session",
                engine_id="xpedition-pcb",
                instance_id=None,
                object_id="pcb",
                locator="xpedition://pcb",
            )
        )
    )
    return list(dict.fromkeys(keys))


def check_inventory(reference):
    strict_file_pin(reference)
    inventory = read_json(reference["path"])
    if type(inventory.get("complete")) is not bool or not isinstance(inventory.get("files"), list):
        raise OwnerPreflightError(
            "Protected inventory must explicitly declare file enumeration coverage"
        )
    root = Path(inventory["root"]).resolve()
    expected = {}
    for row in inventory["files"]:
        candidate = Path(row["path"])
        path = (candidate if candidate.is_absolute() else root / candidate).resolve()
        if not path.is_relative_to(root) or path in expected:
            raise OwnerPreflightError("Protected inventory escapes root or repeats a file")
        strict_file_pin(dict(row, path=str(path)))
        expected[path] = row["sha256"]
    errors = inventory.get("errors", [])
    if (
        not isinstance(errors, list)
        or (inventory["complete"] and errors)
        or (not inventory["complete"] and not errors)
    ):
        raise OwnerPreflightError("Protected inventory error/coverage contradiction")
    failed_paths = set()
    for error in errors:
        item = Path(error["path"])
        path = (item if item.is_absolute() else root / item).resolve()
        if not path.is_relative_to(root) or path in expected or path in failed_paths:
            raise OwnerPreflightError("Protected unreadable entry is invalid or repeated")
        failed_paths.add(path)
        try:
            path.read_bytes()
        except OSError as exc:
            if type(exc).__name__ != error["type"] or exc.errno != error.get("errno"):
                raise OwnerPreflightError(
                    "Protected unreadable entry changed failure state"
                ) from exc
        else:
            raise OwnerPreflightError(
                "Previously unreadable protection entry changed; freeze a fresh explicit baseline"
            )
    actual = {p.resolve() for p in root.rglob("*") if p.is_file()}
    if actual != set(expected) | failed_paths:
        raise OwnerPreflightError("Protected inventory file collection drifted")
    return dict(
        pin=reference,
        files=len(expected),
        complete=inventory["complete"],
        known_unreadable=len(errors),
    )


def preflight(contract_path, *, profile=NOEDIT_PROFILE):
    """Pure file/dependency/scope checks. Does not import the native child."""
    pinned_contract = file_pin(contract_path)
    contract = read_json(contract_path)
    if not isinstance(profile, ControlProfile):
        raise OwnerPreflightError("Native profile must be a compiled ControlProfile")
    kind = contract.get("profile_kind")
    if kind != profile.kind and not (profile is NOEDIT_PROFILE and kind is None):
        raise OwnerPreflightError("Contract belongs to a different native method profile")
    for name in ("project", "board", "output", "journal"):
        if not isinstance(contract.get(name), str) or not Path(contract[name]).is_absolute():
            raise OwnerPreflightError("Absolute contract path missing: " + name)
    if not isinstance(contract.get("board_name"), str) or not contract["board_name"]:
        raise OwnerPreflightError("Board name missing")
    if contract.get("limits") != profile.limits:
        raise OwnerPreflightError("Contract limits differ from the compiled method profile")
    wait = contract.get("max_wait_seconds", 360)
    if type(wait) not in (int, float) or not 1 <= wait <= 900:
        raise OwnerPreflightError("Owner wait must be bounded to 1..900 seconds")
    native = contract["native_entry"]
    if (
        Path(native["path"]).resolve()
        != Path(__file__).with_name(profile.native_filename).resolve()
    ):
        raise OwnerPreflightError("Native entry is not the compiled method entry")
    pins = [
        contract["environment"],
        contract["scope"],
        contract["closed_input_inventory"],
        native,
        *contract["source_pins"],
        *contract["file_pins"],
    ]
    if (
        not contract["source_pins"]
        or not contract["file_pins"]
        or not contract["protected_inventories"]
    ):
        raise OwnerPreflightError("Frozen source, input and protection pins must not be empty")
    for pin in pins:
        strict_file_pin(pin)
    # Compilation validates syntax without executing imports or CAD-related code.
    compile(Path(native["path"]).read_bytes(), native["path"], "exec")
    inventories = [check_inventory(pin) for pin in contract["protected_inventories"]]
    closed = check_inventory(contract["closed_input_inventory"])
    if closed["complete"] is not True:
        raise OwnerPreflightError("Diagnostic closed input inventory must be complete")
    for name in ("project", "board"):
        path = str(Path(contract[name]).resolve())
        if path not in {str(Path(pin["path"]).resolve()) for pin in contract["file_pins"]}:
            raise OwnerPreflightError("Actual target file is not pinned: " + name)
    require_callables(
        [
            (tokens, ("issue", "verify", "digest", "fingerprint")),
            (StateStore, ("dispatch", "finish", "add_plan", "blocking", "heartbeat")),
            (ScopeResolver, ("resolve",)),
            (subprocess, ("Popen",)),
        ]
    )
    environment = child_environment(read_json(contract["environment"]["path"]))
    declared_journal = Path(environment["XPEDITION_CLI_CONFIG_DIR"]).resolve() / "driver-v2"
    if Path(contract["journal"]).resolve() != declared_journal:
        raise OwnerPreflightError("Journal must be the declared existing SDK driver-v2 directory")
    if (
        Path(environment["XPEDITION_CLI_SCOPE_FILE"]).resolve()
        != Path(contract["scope"]["path"]).resolve()
    ):
        raise OwnerPreflightError("Declared environment scope differs from pinned scope")
    validator = ContractValidator(SPEC)
    scope = ScopeResolver("xpedition-cli", validator=validator, environ=environment).resolve()
    outputs = [
        dict(path=contract["output"], effect="artifact", exists=Path(contract["output"]).exists())
    ]
    for name in ("project", "board"):
        scope.require(effects=EFFECTS, target=target(contract[name]), outputs=outputs)
    revision = tokens.digest(dict(contract=pinned_contract, pins=pins, inventories=inventories))
    return contract, pinned_contract, environment, validator, scope, revision


def snapshot(revision, *, source="disk"):
    return dict(
        id="snapshot_" + revision[:24],
        source=source,
        revision=revision,
        observed_at=utc(),
        dependencies=[],
    )


def _envelope(validator, *, data=None, code=None, message=None, details=None):
    value = dict(ok=code is None, schema_version="1.0", meta={"duration_ms": 0})
    if code:
        mapping = validator.code_table()[code]
        error_details = dict(
            dispatch_state="not_started",
            next_action="Inspect the operation record before another native action.",
        )
        error_details.update(details or {})
        if code == "E_OUTCOME_UNKNOWN" and "operation_id" in error_details:
            error_details.setdefault(
                "lookup", "xpedition-cli operation show --id " + error_details["operation_id"]
            )
            error_details.setdefault(
                "lookup_argv", ["operation", "show", "--id", error_details["operation_id"]]
            )
        value["error"] = dict(
            code=code,
            message=message or code,
            retryable=mapping["retryable"],
            details=error_details,
        )
        exit_code = mapping["exit"]
    else:
        value["data"], exit_code = data or {}, 0
    validator.validate("response", value)
    return exit_code, value


def _blocked(store, keys):
    blocker = store.blocking(keys, exclude=[])
    if blocker:
        record, live = blocker
        raise ControlError(
            "E_CONFLICT",
            "Native target has a running or unresolved operation",
            conflict="lock_held" if live else "unresolved_operation",
            active_operation=record["operation_id"],
        )


def plan(contract_path, *, profile=NOEDIT_PROFILE):
    contract, pin, _, validator, scope, revision = preflight(contract_path, profile=profile)
    store = StateStore(contract["journal"])
    try:
        _blocked(store, target_keys(contract))
        operation, plan_id = "op_" + uuid.uuid4().hex, "plan_" + uuid.uuid4().hex
        expires = datetime.now(UTC) + timedelta(minutes=10)
        preview = dict(
            plan_id=plan_id,
            operation_id=operation,
            capability_id=profile.capability,
            target=target(contract["project"]),
            base_snapshot=snapshot(revision),
            authorization_scope_id=scope.scope_id,
            effects=EFFECTS,
            level="plan",
            changes=[
                dict(
                    action="no_value_lifecycle" if profile is NOEDIT_PROFILE else profile.kind,
                    resource="owned diagnostic Layout",
                    summary=profile.plan_summary
                    or (
                        "Owned single-field PUT and Save/reopen: Activation1/Open2/Load1/UnLoad1/"
                        "ValuePUT1/Save1/Close2/Quit1/SynchCES0; conditional stages only."
                    ),
                )
            ],
            outputs=[
                dict(
                    path=contract["output"],
                    effect="artifact",
                    exists=Path(contract["output"]).exists(),
                )
            ],
            unknowns=list(profile.unknowns)
            or ["Native effects and single-field persistence need independent evidence."],
            constraints=list(profile.constraints)
            or [
                "Only the frozen single-field contract; no Sync, replay, timeout kill "
                "or production widths capability."
            ],
            trial_snapshot=None,
            can_apply=True,
            expires_at=expires.isoformat().replace("+00:00", "Z"),
            extensions={
                "contract": pin,
                "limits": dict(profile.limits),
                "profile_kind": profile.kind,
                "closed_input_inventory": contract["closed_input_inventory"],
            },
        )
        validator.validate("plan", preview)
        store.add_plan(
            dict(
                operation_id=operation,
                plan_id=plan_id,
                capability_id=profile.capability,
                command=profile.command,
                target_key=target_keys(contract)[0],
                request_digest=pin["sha256"],
                scope_id=scope.scope_id,
                effects=EFFECTS,
                plan=preview,
                plan_expires_at=expires.timestamp(),
            )
        )
        token = tokens.issue(
            store.secret(),
            dict(
                op=operation,
                plan=plan_id,
                cap=profile.capability,
                digest=pin["sha256"],
                scope=scope.scope_id,
                exp=expires.timestamp(),
            ),
        )
        return _envelope(
            validator,
            data=dict(preview=preview, confirm_token=token, expires_at=preview["expires_at"]),
        )
    finally:
        store.close()


def _disk_persistence(
    record,
    result,
    output_root,
    required_evidence,
    *,
    manual_save=False,
    rule_persistence=False,
):
    """Derive disk persistence from frozen, separately hashed file observations."""
    disk = result.get("disk_observation")
    if not isinstance(disk, dict):
        return None
    before_pin = record["plan"]["extensions"].get("closed_input_inventory")
    if disk.get("before") != before_pin:
        return None
    for key in ("before", "after", "delta"):
        strict_file_pin(disk[key])
        if key != "before" and (
            not Path(disk[key]["path"]).resolve().is_relative_to(output_root)
            or disk[key] not in required_evidence
        ):
            return None
    before, after, delta = (read_json(disk[key]["path"]) for key in ("before", "after", "delta"))
    if Path(before["root"]).resolve() != Path(after["root"]).resolve():
        return None

    def rows(inventory):
        values = {}
        if type(inventory.get("complete")) is not bool or not isinstance(
            inventory.get("errors"), list
        ):
            raise OwnerPreflightError("Disk observation coverage is invalid")
        if inventory["complete"] != (not inventory["errors"]):
            raise OwnerPreflightError("Disk observation errors contradict coverage")
        for row in inventory["files"]:
            name = row["path"]
            if (
                not isinstance(name, str)
                or Path(name).is_absolute()
                or ".." in Path(name).parts
                or name in values
                or type(row["bytes"]) is not int
                or row["bytes"] < 0
                or not isinstance(row["sha256"], str)
                or len(row["sha256"]) != 64
            ):
                raise OwnerPreflightError("Disk observation file entry is invalid")
            values[name] = row
        return values

    left, right = rows(before), rows(after)
    unreadable = {row["path"] for row in after["errors"]}
    derived = dict(
        added=sorted(set(right) - set(left)),
        removed=(
            [] if "<enumeration>" in unreadable else sorted(set(left) - set(right) - unreadable)
        ),
        changed=sorted(key for key in set(left) & set(right) if left[key] != right[key]),
        unobserved=sorted(unreadable),
        complete=before["complete"] and after["complete"],
        manual_Save_called=bool(manual_save),
        rule_persistence_proven=bool(rule_persistence),
    )
    if delta != derived:
        return None
    changed = any(derived[key] for key in ("added", "removed", "changed"))
    if (
        disk.get("changes_observed") is not changed
        or disk.get("coverage_complete") is not derived["complete"]
    ):
        return None
    return "partial" if changed else "unchanged" if derived["complete"] else "unknown"


def _validated_evidence(result, result_pin, *, profile=NOEDIT_PROFILE):
    """Profile-independent truth/pin checks before any compiled receipt builder."""
    counts = result.get("counts")
    if not isinstance(counts, dict) or set(counts) != set(profile.limits):
        return None
    if any(
        type(counts[key]) is not int or not 0 <= counts[key] <= profile.limits[key]
        for key in profile.limits
    ):
        return None
    if result.get("complete") is not True or result.get("evidence_complete") is not True:
        return None
    missing = result.get("missing_required_evidence", [])
    if not isinstance(missing, list) or missing:
        return None
    occurred = any(counts.values())
    if not occurred:
        return "not_started"
    if "missing_required_evidence" not in result:
        return None
    evidence = result.get("required_evidence")
    if not isinstance(evidence, list) or not evidence:
        return None
    output_root = Path(result_pin["path"]).parent.parent.resolve()
    verified_evidence_paths = set()
    for pin in evidence:
        if not Path(pin["path"]).resolve().is_relative_to(output_root):
            return None
        strict_file_pin(pin)
        verified_evidence_paths.add(Path(pin["path"]).resolve())
    # Partial is a known result, not a substitute for a missing effect inventory.
    changes = result.get("known_effects")
    if not isinstance(changes, list) or not changes:
        return None
    for change in changes:
        if set(change) != {"id", "change", "evidence_ref"} or not all(
            isinstance(v, str) and v for v in change.values()
        ):
            return None
        evidence_path = Path(change["evidence_ref"].split("#", 1)[0]).resolve()
        if evidence_path not in verified_evidence_paths:
            return None
    return True


def _receipt(record, result, result_pin, protected_ok, validator, *, profile=NOEDIT_PROFILE):
    checked = _validated_evidence(result, result_pin, profile=profile)
    if checked is not True:
        return checked
    counts = result["counts"]
    evidence = result["required_evidence"]
    output_root = Path(result_pin["path"]).parent.parent.resolve()
    changes = result["known_effects"]
    if result.get("rules_saved") is not False:
        return None
    completed = result.get("control_completed") is True
    if completed and any(
        counts[k] != 1 for k in ("Activation", "Open", "Load", "UnLoad", "Close", "Quit")
    ):
        return None
    persistence = _disk_persistence(record, result, output_root, evidence)
    if completed and persistence is None:
        return None
    native_verification = result.get("verification", {})
    status = native_verification.get("status")
    if status not in {"passed", "failed", "incomplete"}:
        return None
    if not protected_ok:
        status = "failed"
    if completed and status != "passed":
        completed = False
    ref = result_pin["path"]
    after = snapshot(result_pin["sha256"])
    verification = dict(
        status=status,
        scope="Frozen no-value owned reference and Load/UnLoad control only",
        snapshot_id=after["id"],
        coverage="complete" if status == "passed" else "partial",
        checks=[
            dict(
                id="native_owner_control",
                status=status,
                required=True,
                **(
                    {"evidence_ref": ref}
                    if status != "incomplete"
                    else {"reason": "Native control evidence incomplete"}
                ),
            ),
            dict(
                id="protected_files",
                status="passed" if protected_ok else "failed",
                required=True,
                evidence_ref=ref,
            ),
        ],
        not_checked=[
            dict(
                id="rule_write_persistence_and_production",
                required=False,
                reason="No PUT, Sync or Save is authorized; production widths remains disabled.",
            )
        ],
        evidence_refs=[ref],
    )
    if status == "incomplete":
        verification["reason"] = native_verification.get("reason", "Native readback incomplete")
    receipt = dict(
        operation_id=record["operation_id"],
        capability_id=CAPABILITY,
        plan_id=record["plan_id"],
        target=record["plan"]["target"],
        authorization_scope_id=record["scope_id"],
        effects=EFFECTS,
        snapshot_before=record["plan"]["base_snapshot"],
        snapshot_after=after,
        outcome=dict(
            execution="completed" if completed else "failed",
            change="applied" if completed else "partial",
            persistence=persistence or "unknown",
            delivery="not_applicable",
            verification=verification,
        ),
        failed_stage=None if completed else "apply",
        changed_items=changes,
        artifacts=[
            dict(
                id="native-result",
                path=ref,
                sha256=result_pin["sha256"],
                source_snapshot_id=after["id"],
            )
        ],
        deliveries=[],
        recovery=dict(
            action="inspect",
            reason="No-value method result only; do not replay this operation.",
            supported=True,
        ),
        extensions=dict(
            native_counts=counts,
            native_stage=result.get("stage"),
            rules_saved=False,
            control_completed=completed,
            protected_ok=protected_ok,
            disk_observation=result.get("disk_observation"),
        ),
    )
    validator.validate("receipt", receipt)
    return receipt


def _early_not_started(contract, stderr_path, operation, request_sha256, *, profile=NOEDIT_PROFILE):
    """Accept only a pinned child's explicit, bound, zero-call final marker.

    Exit code, traceback text, a missing result or a zero-looking error message
    is never sufficient. A changed source or any ambiguous marker stays unknown.
    """
    try:
        for pin in [contract["native_entry"], *contract["source_pins"]]:
            strict_file_pin(pin)
        stderr_pin = file_pin(stderr_path)
        strict_file_pin(stderr_pin)
        raw = Path(stderr_path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != stderr_pin["sha256"]:
            return None
        lines = raw.splitlines()
        if not lines:
            return None
        marker = json.loads(lines[-1].decode("utf-8"))
        if (
            not isinstance(marker, dict)
            or marker.get("schema") != "owned-control-early-failure/v1"
            or marker.get("operation_id") != operation
            or marker.get("request_sha256") != request_sha256
            or marker.get("native_dispatch_state") != "not_started"
        ):
            return None
        counts = marker.get("counts")
        error = marker.get("error")
        if (
            not isinstance(counts, dict)
            or set(counts) != set(profile.limits)
            or any(type(value) is not int or value != 0 for value in counts.values())
            or not isinstance(error, dict)
            or not all(isinstance(error.get(key), str) for key in ("type", "message"))
        ):
            return None
        return dict(marker=marker, stderr=stderr_pin)
    except Exception:
        return None


def confirm(contract_path, token, *, profile=NOEDIT_PROFILE):
    contract, pin, environment, validator, scope, revision = preflight(
        contract_path, profile=profile
    )
    store = StateStore(contract["journal"])
    try:
        claims = tokens.verify(store.secret(), token, time.time())
        record = store.get(claims.get("op", ""))
        if (
            record is None
            or claims.get("cap") != profile.capability
            or claims.get("digest") != pin["sha256"]
        ):
            raise ControlError(
                "E_CONFLICT", "Token does not match frozen contract", conflict="token_mismatch"
            )
        if record["status"] != "planned":
            raise ControlError(
                "E_CONFLICT",
                "Confirmation was already consumed; native child will not be replayed",
                conflict="token_used",
                operation_id=record["operation_id"],
            )
        if claims.get("scope") != scope.scope_id:
            raise ControlError(
                "E_CONFLICT", "Scope differs from the plan", conflict="scope_changed"
            )
        if revision != record["plan"]["base_snapshot"]["revision"]:
            raise ControlError(
                "E_CONFLICT", "Frozen input changed after planning", conflict="state_drift"
            )
        output = Path(contract["output"])
        control = output / "control"
        if control.exists() or (output / "native/result.json").exists():
            raise ControlError(
                "E_CONFLICT",
                "Frozen output already used; this method never overwrites evidence",
                conflict="state_drift",
            )
        _blocked(store, target_keys(contract))
        try:
            store.dispatch(
                record["operation_id"],
                fingerprint=tokens.fingerprint(token),
                token_expires=claims["exp"],
                key=None,
                digest=pin["sha256"],
                target_keys=target_keys(contract),
                exclude=[],
                budget=scope.max_operations,
                lease=60,
            )
        except BudgetSpent as spent:
            raise forbidden(
                scope,
                {"limit": f"Scope operation budget spent: {spent.used} of {spent.limit}"},
            ) from None
        started = False
        try:
            request = dict(
                schema=profile.request_schema,
                operation_id=record["operation_id"],
                contract_path=str(Path(contract_path).resolve()),
                contract_sha256=pin["sha256"],
                contract=contract,
                target=target(contract["project"]),
                target_keys=target_keys(contract),
                conflict_exclude=[],
                effective_journal=contract["journal"],
                effects=EFFECTS,
            )
            request_path = control / "native-request.json"
            write_new(request_path, request)
            request_pin = file_pin(request_path)
            stdout = (control / "stdout.bin").open("xb")
            stderr = (control / "stderr.bin").open("xb")
            try:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-B",
                        contract["native_entry"]["path"],
                        "--request",
                        str(request_path),
                        "--request-sha256",
                        request_pin["sha256"],
                        "--operation-id",
                        record["operation_id"],
                    ],
                    env=environment,
                    cwd=str(ROOT),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                started = True
                write_new(
                    control / "child-started.json",
                    dict(pid=process.pid, started_at=utc(), request=request_pin),
                )
                deadline = time.monotonic() + contract.get("max_wait_seconds", 360)
                while process.poll() is None and time.monotonic() < deadline:
                    store.heartbeat(record["operation_id"], 60)
                    time.sleep(min(0.25, max(0, deadline - time.monotonic())))
                exited = process.poll() is not None
            finally:
                stdout.close()
                stderr.close()
            extra = dict(
                dispatch_state="dispatched",
                child_pid=process.pid,
                child_exited=exited,
                child_exit_code=process.returncode if exited else None,
                no_retry=True,
                no_kill=True,
            )
            result_path = output / "native/result.json"
            if not exited or not result_path.is_file():
                early = (
                    _early_not_started(
                        contract,
                        control / "stderr.bin",
                        record["operation_id"],
                        request_pin["sha256"],
                        profile=profile,
                    )
                    if exited
                    else None
                )
                if early is not None:
                    store.finish(
                        record["operation_id"],
                        "failed",
                        extra=dict(
                            extra,
                            dispatch_state="not_started",
                            stage="bound_child_early_failure",
                            no_native_call=True,
                            native_counts=early["marker"]["counts"],
                            early_failure=early,
                            token_remains_consumed=True,
                        ),
                    )
                    return _envelope(
                        validator,
                        code="E_NATIVE_FAILURE",
                        message="Pinned child proved a pre-CAD failure; token stays used.",
                        details=dict(
                            operation_id=record["operation_id"],
                            dispatch_state="not_started",
                            no_native_call=True,
                            evidence_ref=early["stderr"]["path"],
                        ),
                    )
                store.finish(
                    record["operation_id"],
                    "unknown",
                    extra=dict(extra, stage="child_timeout_or_reply_missing"),
                )
                return unknown(
                    validator,
                    record["operation_id"],
                    "Native child did not provide a final readable result; it was not killed.",
                )
            result_pin, result = file_pin(result_path), read_json(result_path)
            if (
                result.get("schema") != profile.result_schema
                or result.get("operation_id") != record["operation_id"]
                or result.get("request_sha256") != request_pin["sha256"]
            ):
                store.finish(
                    record["operation_id"],
                    "unknown",
                    extra=dict(extra, stage="native_result_binding_failed", result=result_pin),
                )
                return unknown(
                    validator,
                    record["operation_id"],
                    "Native result identity or source binding is incomplete.",
                )
            protected_ok = True
            try:
                for inventory in contract["protected_inventories"]:
                    check_inventory(inventory)
            except Exception:
                protected_ok = False
            receipt_rejection = None
            try:
                basic = _validated_evidence(result, result_pin, profile=profile)
                if basic is not True:
                    receipt = basic
                else:
                    receipt = profile.receipt_builder(
                        record, result, result_pin, protected_ok, validator
                    )
                    if isinstance(receipt, dict):
                        validator.validate("receipt", receipt)
                        if receipt["outcome"]["verification"]["status"] == "passed" and (
                            not protected_ok
                            or result.get("verification", {}).get("status") != "passed"
                        ):
                            raise ValueError("Receipt promoted unverified native evidence")
                        if (
                            any(
                                receipt.get(key) != record[field]
                                for key, field in (
                                    ("operation_id", "operation_id"),
                                    ("plan_id", "plan_id"),
                                    ("capability_id", "capability_id"),
                                    ("authorization_scope_id", "scope_id"),
                                )
                            )
                            or receipt.get("target") != record["plan"]["target"]
                        ):
                            raise ValueError("Compiled receipt builder changed request identity")
            except Exception as exc:
                # Evidence pin or receipt validation failures do not erase the
                # already reported native stages (in particular Close).
                receipt = None
                receipt_rejection = dict(type=type(exc).__name__, message=str(exc))
            if receipt == "not_started":
                store.finish(
                    record["operation_id"],
                    "failed",
                    extra=dict(
                        extra,
                        dispatch_state="not_started",
                        stage=result.get("stage"),
                        result=result_pin,
                        native_counts=result["counts"],
                    ),
                )
                return _envelope(
                    validator,
                    code="E_NATIVE_FAILURE",
                    message="Child completed with zero native calls; token remains consumed.",
                    details=dict(
                        operation_id=record["operation_id"], native_counts=result["counts"]
                    ),
                )
            if receipt is None:
                store.finish(
                    record["operation_id"],
                    "unknown",
                    extra=dict(
                        extra,
                        stage="native_effect_evidence_incomplete",
                        result=result_pin,
                        reported_native_counts=result.get("counts"),
                        reported_native_stage=result.get("stage"),
                        reported_control_completed=result.get("control_completed"),
                        reported_verification=result.get("verification"),
                        missing_required_evidence=result.get("missing_required_evidence"),
                        receipt_rejection=receipt_rejection,
                    ),
                )
                return unknown(
                    validator,
                    record["operation_id"],
                    "Native stages occurred but their complete effect evidence is unavailable.",
                )
            rule = validator.receipt_rule(receipt)
            status = (
                "succeeded"
                if rule["ok"]
                else "unknown"
                if rule["codes"] == ["E_OUTCOME_UNKNOWN"]
                else "partial"
                if rule["codes"] == ["E_PARTIAL"]
                else "failed"
            )
            store.finish(record["operation_id"], status, receipt, dict(extra, result=result_pin))
            if rule["ok"]:
                return _envelope(validator, data=dict(receipt=receipt))
            return _envelope(
                validator,
                code=rule["codes"][0],
                message="Read the bounded native method receipt; do not replay.",
                details=dict(
                    dispatch_state="dispatched",
                    operation_id=record["operation_id"],
                    receipt=receipt,
                ),
            )
        except Exception as exc:
            state = "unknown" if started else "failed"
            store.finish(
                record["operation_id"],
                state,
                extra=dict(
                    dispatch_state="unknown" if started else "not_started",
                    stage="controller_after_dispatch",
                    error_type=type(exc).__name__,
                    child_started=started,
                    token_remains_consumed=True,
                ),
            )
            if started:
                return unknown(
                    validator,
                    record["operation_id"],
                    "Controller stopped after child start; no retry or automatic CAD action.",
                )
            return _envelope(
                validator,
                code="E_IO",
                message="Native child was not started; consumed token remains used.",
                details=dict(operation_id=record["operation_id"], dispatch_state="not_started"),
            )
    finally:
        store.close()


def unknown(validator, operation, message):
    return _envelope(
        validator,
        code="E_OUTCOME_UNKNOWN",
        message=message,
        details=dict(
            dispatch_state="unknown",
            operation_id=operation,
            lookup="xpedition-cli operation show --id " + operation,
            lookup_argv=["operation", "show", "--id", operation],
        ),
    )


def main(argv=None, *, profile=NOEDIT_PROFILE):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        code, value = (
            plan(args.contract, profile=profile)
            if args.dry_run
            else confirm(args.contract, args.confirm, profile=profile)
        )
    except Exception as exc:
        validator = ContractValidator(SPEC)
        if isinstance(exc, ControlError):
            code, value = _envelope(validator, code=exc.code, message=str(exc), details=exc.details)
        elif isinstance(exc, CriticalInputDrift):
            code, value = _envelope(
                validator,
                code="E_CONFLICT",
                message="Frozen source or target input drifted; no child was started.",
                details={"conflict": "state_drift", "reason": str(exc)},
            )
        elif isinstance(exc, tokens.TokenError):
            code, value = _envelope(
                validator,
                code="E_CONFLICT",
                message="Token is invalid or expired",
                details={"conflict": exc.reason},
            )
        elif isinstance(exc, (TokenUsed, Blocked)):
            code, value = _envelope(
                validator,
                code="E_CONFLICT",
                message="SDK refused dispatch; nothing was started",
                details={
                    "conflict": "token_used"
                    if isinstance(exc, TokenUsed)
                    else "unresolved_operation",
                    "active_operation": getattr(getattr(exc, "record", {}), "get", lambda _: None)(
                        "operation_id"
                    )
                    or "unknown",
                },
            )
        elif hasattr(exc, "code") and hasattr(exc, "details"):
            code, value = _envelope(validator, code=exc.code, message=str(exc), details=exc.details)
        else:
            code, value = _envelope(
                validator,
                code="E_VALIDATION",
                message="Offline preflight failed: " + type(exc).__name__,
                details={"problem": "invalid_value", "reason": str(exc)},
            )
    print(json.dumps(value, ensure_ascii=False, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
