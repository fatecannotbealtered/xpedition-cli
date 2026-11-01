"""Fixed private field profile using the original scope/token/operation store."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.native_acceptance import owner_control as core  # noqa: E402
from scripts.native_acceptance import owner_field_rules as rules  # noqa: E402

LIMITS = dict(core.LIMITS, Open=2, Close=2, ValuePUT=1, Save=1)
CAPABILITY = "external.owner.single_field_value_put"


def _verified_persistence(record, result, result_pin):
    """Verify this method's fixed persisted branch independently of its flags.

    This checks the existing after-UnLoad/read/Save/reopen method, not a vendor
    requirement that all possible submission methods use that sequence.
    """
    try:
        counts, facts = result["counts"], result["facts"]
        if set(counts) != set(LIMITS) or any(
            type(counts[name]) is not int or not 0 <= counts[name] <= limit
            for name, limit in LIMITS.items()
        ):
            return False
        if any(
            counts[name] < minimum
            for name, minimum in {
                "Activation": 1,
                "Open": 2,
                "Load": 1,
                "UnLoad": 1,
                "ValuePUT": 1,
                "Save": 1,
                "Close": 1,
            }.items()
        ):
            return False
        contract_pin = record["plan"]["extensions"]["contract"]
        core.strict_file_pin(contract_pin)
        contract = core.read_json(contract_pin["path"])
        board = Path(contract["board"]).resolve()
        expected_target = core.target(contract["project"])
        if record["plan"]["target"] != expected_target:
            return False
        target = contract["field"]
        if any(
            target.get(name) != value
            for name, value in {
                "scheme": "(Master)",
                "net_class": "(Default)",
                "layer": "SIGNAL_1",
                "metallic_index": 1,
                "constraint_type": 183,
                "target_th": 14,
                "unit_enum": 1,
            }.items()
        ):
            return False
        native_root = Path(result_pin["path"]).resolve().parent
        pins = {}
        for pin in result["required_evidence"]:
            core.strict_file_pin(pin)
            path = Path(pin["path"]).resolve()
            if not path.is_relative_to(native_root):
                return False
            if path in pins and pins[path] != pin:
                return False
            pins[path] = pin

        def observed(name):
            path = native_root / name
            if path not in pins:
                raise ValueError("Required persisted-branch evidence missing: " + name)
            core.strict_file_pin(pins[path])
            value = core.read_json(path)
            core.strict_file_pin(pins[path])
            return value

        required_facts = (
            "Load-return",
            "PUT-return",
            "UnLoad-return",
            "consumption",
            "Save-return",
            "Close-after-save",
            "reopen",
            "after-reopen-protection",
            "persistence",
        )
        for name in required_facts:
            if name not in facts or core.tokens.canonical(
                observed("fact-" + name + ".json")
            ) != core.tokens.canonical(facts[name]):
                return False
        for name in ("Load-return", "PUT-return", "UnLoad-return"):
            if facts[name].get("returned_void") is not True:
                return False
        if facts["PUT-return"].get("raw_target") != 14.0 or facts["PUT-return"].get("unit") != "th":
            return False
        if facts["Save-return"].get("actual_return") is not True:
            return False
        close = facts["Close-after-save"]
        if close.get("actual_return") is not True or close.get("save_argument") is not False:
            return False
        for key in ("returned_path", "authenticated_path"):
            if Path(facts["reopen"][key]).resolve() != board:
                return False
        baseline = observed("baseline-b-independent-layout.json")
        consumed = observed("after-unload-independent-layout.json")
        reopened = observed("after-reopen-independent-layout.json")
        consumption = rules.compare_consumed(baseline, consumed)
        protection = rules.compare_persisted(consumed, reopened)
        if core.tokens.canonical(facts["consumption"]) != core.tokens.canonical(consumption):
            return False
        if core.tokens.canonical(facts["after-reopen-protection"]) != core.tokens.canonical(
            protection
        ):
            return False
        for label, held in (("after-unload", consumed), ("after-reopen", reopened)):
            fresh = observed(label + "-fresh-independent-layout.json")
            rules.compare_persisted(held, fresh)
            comparison = observed(label + "-physical-document-comparison.json")
            if (
                comparison.get("fingerprints_equal") is not True
                or Path(comparison["fresh_path"]).resolve() != board
            ):
                return False
        return facts["persistence"].get("reopened_independent_read_passed") is True
    except (KeyError, TypeError, ValueError, OSError, core.OwnerPreflightError):
        return False


def receipt(record, result, result_pin, protected_ok, validator):
    counts, facts = result["counts"], result.get("facts", {})
    if not isinstance(facts, dict):
        return None
    if not any(counts.values()):
        return "not_started"
    native = result.get("verification", {})
    status = native.get("status")
    if status not in {"passed", "failed", "incomplete"}:
        return None
    if not protected_ok:
        status = "failed"
    persisted = facts.get("persistence", {}).get("reopened_independent_read_passed") is True
    if persisted and not _verified_persistence(record, result, result_pin):
        return None
    completed = result.get("control_completed") is True
    returned = {
        "Load-return": ("returned_void", True),
        "PUT-return": ("returned_void", True),
        "UnLoad-return": ("returned_void", True),
        "Save-return": ("actual_return", True),
        "Close-after-save": ("actual_return", True),
    }
    if (completed or persisted) and any(
        facts.get(name, {}).get(key) is not value for name, (key, value) in returned.items()
    ):
        return None
    if completed and (
        facts.get("Close-final", {}).get("actual_return") is not True
        or facts.get("Quit-return", {}).get("returned_void") is not True
        or facts.get("consumption", {}).get("consumed") is not True
        or facts.get("consumption", {}).get("protected_named_widths") != 22
        or facts.get("consumption", {}).get("full_remaining_fingerprint_equal") is not True
    ):
        return None
    if completed:
        if counts != LIMITS or not persisted or result.get("rules_saved") is not True:
            return None
        if status != "passed" or not protected_ok:
            completed = False
    if result.get("rules_saved") is not persisted:
        return None
    disk = core._disk_persistence(
        record,
        result,
        Path(result_pin["path"]).parent.parent,
        result["required_evidence"],
        manual_save=bool(counts["Save"]),
        rule_persistence=persisted,
    )
    if completed and disk is None:
        return None
    uncertain_put = counts["ValuePUT"] == 1 and not facts.get("PUT-return", {}).get("returned_void")
    after = core.snapshot(result_pin["sha256"])
    ref = result_pin["path"]
    verification = dict(
        status=status,
        scope="One named CT183 field and frozen bounded protection only",
        snapshot_id=after["id"],
        coverage="complete" if status == "passed" else "partial",
        checks=[
            dict(
                id="field_lifecycle",
                status=status,
                required=True,
                **(
                    {"evidence_ref": ref}
                    if status != "incomplete"
                    else {"reason": "Actual lifecycle evidence incomplete"}
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
                id="production_widths_effective_rules",
                required=False,
                reason=(
                    "No class creation, net assignment, other fields, "
                    "local rule/impedance or production unlock"
                ),
            )
        ],
        evidence_refs=[ref],
    )
    if status == "incomplete":
        verification["reason"] = native.get("reason", "Stopped stage retained")
    result_receipt = dict(
        operation_id=record["operation_id"],
        capability_id=CAPABILITY,
        plan_id=record["plan_id"],
        target=record["plan"]["target"],
        authorization_scope_id=record["scope_id"],
        effects=core.EFFECTS,
        snapshot_before=record["plan"]["base_snapshot"],
        snapshot_after=after,
        outcome=dict(
            execution="completed" if completed else "unknown" if uncertain_put else "failed",
            change="applied" if completed else "unknown" if uncertain_put else "partial",
            persistence="saved" if persisted else disk or "unknown",
            delivery="not_applicable",
            verification=verification,
        ),
        failed_stage=None if completed else "apply",
        changed_items=result["known_effects"],
        artifacts=[
            dict(
                id="native-field-result",
                path=ref,
                sha256=result_pin["sha256"],
                source_snapshot_id=after["id"],
            )
        ],
        deliveries=[],
        recovery=dict(
            action="inspect",
            supported=True,
            reason="Never replay PUT or the lifecycle; inspect exact stage facts",
        ),
        extensions=dict(
            native_counts=counts,
            native_stage=result["stage"],
            facts=facts,
            rules_saved=persisted,
            field_lifecycle_completed=completed,
            protected_ok=protected_ok,
            product_can_apply=False,
            save_state=result.get("save_state", "not_reported"),
            persistence_verification_state=result.get(
                "persistence_verification_state", "not_established"
            ),
            disk_observation=result.get("disk_observation"),
        ),
    )
    validator.validate("receipt", result_receipt)
    return result_receipt


FIELD_PROFILE = core.ControlProfile(
    kind="single_field_value_put",
    capability=CAPABILITY,
    command="external native single field lifecycle",
    limits=LIMITS,
    native_filename="owner_field.py",
    request_schema="owned-native-field-request/v1",
    result_schema="owned-native-field-result/v1",
    receipt_builder=receipt,
    plan_summary=(
        "Existing F: one named 12->14th PUT, UnLoad, independent consumption; "
        "conditional Save/Close/Open/read/Close/Quit"
    ),
    constraints=(
        (
            "Master/Default/SIGNAL_1/FindItem183 only; Minimum permits 12 or 14th, "
            "other22 and frozen objects identical"
        ),
        "PUT1/UnLoad1/Save1/Open2/Close2/Quit1 maximum; Sync0; no retry or finally writes",
        "Old C/D/E and historical unknowns unchanged; old no-value contract not widened",
    ),
    unknowns=(
        "Load/normal application actions may cause native backup and storage writes",
        "Associated impedance and complete local/effective rules remain unproved",
    ),
)

if __name__ == "__main__":
    raise SystemExit(core.main(profile=FIELD_PROFILE))
