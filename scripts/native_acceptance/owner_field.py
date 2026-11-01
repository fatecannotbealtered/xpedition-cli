"""One named PUT, formal UnLoad, independent consumption and conditional persistence.

External acceptance method only. The old no-value contract is never widened.
No default ROT, no SynchCES, no retry, no finally Save/Close/Quit.
"""

from __future__ import annotations

import argparse
import datetime
import gc
import json
import os
import re
import sys
import threading
import time
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.native_acceptance import owner_field_rules as rules  # noqa: E402
from scripts.native_acceptance import owner_lifecycle as core  # noqa: E402
from scripts.native_acceptance.owner_support import (  # noqa: E402
    classify_process_roles,
    strict_file_pin,
)

GET, PUT, METHOD = core.GET, core.PUT, core.METHOD
LIMITS = dict(core.LIMITS, Open=2, Close=2, ValuePUT=1, Save=1)
CAD_DISPATCHED = False
LAST_RESULT = None


def definition_range(reference):
    strict_file_pin(reference)
    observed = core.read_json(reference["path"])
    tree = observed["accessibility"]["tree"]
    if "EXP_TRACE_WIDTH" not in tree or not re.search(r"文本 th ID:", tree):
        raise ValueError("Pinned definition is not EXP_TRACE_WIDTH in th")

    def scalar(label, control):
        values = re.findall(label + r": Value: ([0-9.]+) ID: " + control + r"\b", tree)
        if len(values) != 1:
            raise ValueError("Unique definition range field missing: " + label)
        return Decimal(values[0])

    low, high = scalar("最小值", "578"), scalar("最大值", "579")
    if not low <= Decimal(14) <= high:
        raise ValueError("14th is outside the pinned vendor definition")
    return dict(
        source=reference,
        definition="EXP_TRACE_WIDTH",
        unit="th",
        lower=str(low),
        upper=str(high),
        candidate="14",
        scope="Historical same-version definition only; not a current effective-rule validator",
    )


def check_contract(contract):
    if contract.get("profile_kind") != "single_field_value_put" or contract["limits"] != LIMITS:
        raise ValueError("A separately frozen single-field contract is required")
    if contract.get("field") != dict(
        scheme="(Master)",
        net_class="(Default)",
        layer="SIGNAL_1",
        metallic_index=1,
        constraint_type=183,
        original_th=12,
        target_th=14,
        unit_enum=1,
        minimum_allowed_th=[12, 14],
    ):
        raise ValueError("Only the exact named 12 -> 14th experiment is supported")
    for key in ("target_helper", "range_evidence"):
        strict_file_pin(contract[key])
    return True


def field_preflight(contract):
    check_contract(contract)
    deps, minimum, base, layout, support = core.import_dependencies(contract)
    bootstrap = core.BootstrapLog()
    minimum.client_environment(dict(environment=contract["environment"]), bootstrap)
    report, clsid = core.runtime_preflight(contract, minimum, base, layout, support)
    helper = core.load_module("field_formal_selection", contract["target_helper"]["path"])
    if not callable(getattr(helper, "target", None)):
        raise ValueError("Formal target selector is missing")
    # This old class supplies exact, frozen formal metadata only. Never call its
    # legacy PUT/synchronization/main implementation.
    provider = helper.native_class(minimum, base, layout, dict(kind="value-put"))(
        core.BootstrapLog(), time.monotonic() + 30
    )
    formal = provider.formal
    signatures = []
    for lib, typ, name, kind in (
        ("ConstraintsAuto", "IConstraintsMutable", "FindItem", METHOD),
        ("ConstraintsAuto", "IConstraint", "Value", PUT),
        ("ConstraintsAuto", "IConstraint", "ValueString", GET),
        ("ConstraintsAuto", "IConstraint", "IsReadOnly", METHOD),
        ("ConstraintsAuto", "IConstraint", "IsValid", METHOD),
        ("MGCPCB", "IMGCPCBDocument", "Save", METHOD),
    ):
        # Formal.member needs a real object only for live interface diagnosis;
        # offline signatures are taken from the frozen exact-key type table.
        typ_row = formal.types[(lib, typ)]
        matches = [m for m in typ_row["members"] if m["name"] == name and m["invoke_kind"] == kind]
        if len(matches) != 1 or matches[0]["function_flags"] != 0:
            raise ValueError("Required supported formal member missing: " + typ + "." + name)
        signatures.append(dict(library=lib, interface=typ, signature=matches[0]))
    report.update(
        field_signatures=signatures,
        child_environment_bootstrap=bootstrap.events,
        no_value_contract_widened=False,
        field_target=contract["field"],
    )
    return report, clsid, (minimum, base, layout, support, helper, formal)


class FieldNative:
    """Add only exact selected-object PUT and guarded persistence calls."""

    def __init__(self, owner, original, formal):
        self.owner, self.original = owner, original
        original.formal = formal

    def __getattr__(self, name):
        return getattr(self.original, name)

    def call(self, obj, library, typ, name, *args, kind=GET, **kwargs):
        o = self.owner
        special = None
        extra_read = library == "ConstraintsAuto" and (
            typ == "IConstraintsMutable"
            and name == "FindItem"
            and kind == METHOD
            and args == (183,)
            or typ == "IConstraint"
            and name in {"IsValid", "IsReadOnly"}
            and kind == METHOD
            and not args
            or typ == "IConstraint"
            and name == "ValueString"
            and kind == GET
            and not args
        )
        if (library, typ, name, kind) == ("ConstraintsAuto", "IConstraint", "Value", PUT):
            if obj is not o.field_target or args != (14.0,) or type(args[0]) is not float:
                raise ValueError("PUT is not the frozen unique target/value")
            special = "ValuePUT"
        if library == "MGCPCB" and kind == METHOD:
            if (
                typ == "IMGCPCBApplication"
                and name == "OpenDocument"
                and args == (o.contract["board"],)
            ):
                special = "Open"
                if obj is not o.application or not o.allow_open:
                    raise ValueError("Open target/stage not authorized")
            elif typ == "IMGCPCBDocument" and name in {"Save", "Close"}:
                special = name
                if obj is not o.document or not getattr(o, "allow_" + name.lower()):
                    raise ValueError("Persistence target/stage not authorized")
                if args != (() if name == "Save" else (False,)):
                    raise ValueError("Only Save() and Close(False) are supported")
        if not extra_read and special is None:
            return self.original.call(obj, library, typ, name, *args, kind=kind, **kwargs)
        if threading.get_ident() != o.thread:
            raise ValueError("STA owner thread changed")
        self.original.check()
        if len(self.original.calls) >= 16000:
            raise ValueError("Native call budget exceeded")
        if special and o.counts[special] >= LIMITS[special]:
            raise ValueError("Native action already attempted: " + special)
        member, source = self.original.formal.member(obj, library, typ, name, kind)
        emitter = o.log.event if special else o.log.safe_event
        emitter(
            "native_call_started",
            durable=bool(special),
            member=f"{library}/{typ}.{name}",
            kind=kind,
            dispid=member["dispid"],
            arguments=list(args),
            formal_source=source,
        )
        if special:
            o.counts[special] += 1
        raw = getattr(obj, "_oleobj_", obj)
        try:
            value = raw.Invoke(member["dispid"], 0, kind, 1, *args)
        except BaseException as exc:
            self.original.calls.append(dict(member=f"{library}/{typ}.{name}", completed=False))
            o.log.safe_event(
                "native_call_failed",
                member=f"{library}/{typ}.{name}",
                error_type=type(exc).__name__,
                message=str(exc),
            )
            raise
        self.original.calls.append(dict(member=f"{library}/{typ}.{name}", completed=True))
        o.log.safe_event("native_call_returned", member=f"{library}/{typ}.{name}", result=value)
        return value


class FieldOwner(core.Owner):
    def __init__(self, contract, log, minimum, base, layout, support, helper, formal):
        super().__init__(contract, log, minimum, base, layout, support)
        self.counts = dict.fromkeys(LIMITS, 0)
        self.native = FieldNative(self, self.native, formal)
        self.helper = helper
        self.field_target = None
        self.allow_open = self.allow_save = self.allow_close = False
        self.facts = {}
        self.consumed = None
        self.entered = False

    def fact(self, key, value):
        self.facts[key] = value
        try:
            self.requirements.append(self.log.file("fact-" + key + ".json", value))
        except Exception as exc:
            self.missing_required_evidence.append(
                dict(name="fact-" + key + ".json", error_type=type(exc).__name__)
            )

    def evidence_guard(self):
        if self.missing_required_evidence:
            raise ValueError("Required identity evidence missing; stop dependent actions")
        for ref in self.requirements:
            strict_file_pin(ref)

    def open_document(self, label):
        self.evidence_guard()
        self.identity("pre_open")
        self.evidence_guard()
        self.stage = label
        self.allow_open = True
        returned = self.native.call(
            self.application,
            "MGCPCB",
            "IMGCPCBApplication",
            "OpenDocument",
            self.contract["board"],
            kind=METHOD,
        )
        self.allow_open = False
        returned = self.authenticate(returned)
        path = self.native.call(returned, "MGCPCB", "IMGCPCBDocument", "FullName")
        if core.normalized(path) != core.normalized(self.contract["board"]):
            raise ValueError("Open returned wrong document")
        self.document = self.authenticate(
            self.native.call(self.application, "MGCPCB", "IMGCPCBApplication", "ActiveDocument")
        )
        import pythoncom

        self.document_unknown = getattr(self.document, "_oleobj_", self.document).QueryInterface(
            pythoncom.IID_IUnknown
        )
        returned = None
        self.identity("post_open")
        self.evidence_guard()
        self.fact(label, dict(returned_path=path, authenticated_path=self.contract["board"]))

    def put_once(self, loaded):
        self.stage = "pre-PUT"
        self.identity("before-put")
        self.protected("before-put")
        self.evidence_guard()
        obj, row = self.helper.target(
            self.native,
            self.auto,
            self.design,
            loaded["inventory"],
            self.baseline["widths"],
            self.log,
            "selected-target.json",
        )
        self.requirements.append(core.file_pin(self.log.directory / "selected-target.json"))
        if row["linear_unit_enum"] != 1 or Decimal(str(row["raw_value"])) != Decimal(12):
            raise ValueError("Target unit or original value drifted")
        if not row["target_currently_writable_valid"] or not row["unit_calibration_exact"]:
            raise ValueError("Unique target is not independently writable/calibrated")
        range_row = definition_range(self.contract["range_evidence"])
        # The historical vendor definition is a bounded range source only,
        # never a current effective-rule validator or a manual verified flag.
        self.fact("range-source", range_row)
        self.field_target = obj
        self.evidence_guard()
        self.stage = "ValuePUT"
        returned = self.native.call(obj, "ConstraintsAuto", "IConstraint", "Value", 14.0, kind=PUT)
        if returned is not None:
            raise ValueError("PUT did not return formal void; outcome unknown")
        self.fact("PUT-return", dict(returned_void=True, raw_target=14.0, unit="th"))
        value = self.native.call(obj, "ConstraintsAuto", "IConstraint", "Value")
        valid = self.native.call(obj, "ConstraintsAuto", "IConstraint", "IsValid", kind=METHOD)
        self.fact(
            "same-object-read", dict(value=value, is_valid=valid, proves_Layout_consumption=False)
        )
        if Decimal(str(value)) != Decimal(14) or valid is not True:
            raise ValueError("Same-object result is incomplete; no dependent action")
        obj = None
        self.field_target = None
        gc.collect()
        self.log.safe_event(
            "target_subreference_ended_before_UnLoad", configuration_lock_release_not_inferred=True
        )

    def release_constraints(self):
        self.field_target = None
        return super().release_constraints()

    def after_release(self):
        self.identity("after-unload")
        held = self.observe_layout("after-unload")
        self.reference_after_unload = self.compare_fresh_document("after-unload", held)
        self.fact("consumption", rules.compare_consumed(self.baseline, held))
        self.protected("after-unload")
        self.evidence_guard()
        self.consumed = held
        return held

    def verify_consumed(self, label):
        self.identity(label)
        held = self.observe_layout(label)
        self.compare_fresh_document(label, held)
        self.fact(label + "-protection", rules.compare_persisted(self.consumed, held))
        self.protected(label)
        self.evidence_guard()
        return held

    def save_once(self):
        self.verify_consumed("before-save")
        self.stage = "Save"
        self.allow_save = True
        value = self.native.call(self.document, "MGCPCB", "IMGCPCBDocument", "Save", kind=METHOD)
        self.allow_save = False
        self.fact("Save-return", dict(actual_return=value, proves_reopen=False))
        if value is not True:
            raise ValueError("Save did not return actual True")
        self.verify_consumed("after-save")

    def close_document(self, label):
        self.identity("before-close")
        self.protected(label)
        self.evidence_guard()
        self.stage = label
        self.allow_close = True
        value = self.native.call(
            self.document, "MGCPCB", "IMGCPCBDocument", "Close", False, kind=METHOD
        )
        self.allow_close = False
        self.fact(label, dict(actual_return=value, save_argument=False))
        if value is not True:
            raise ValueError("Close(False) did not return actual True")
        self.document = self.document_unknown = None
        self.identity("post_close")
        self.evidence_guard()  # A missing post_close artifact forbids Open/Quit.

    def quit_owned(self):
        self.evidence_guard()
        self.stage = "Quit"
        value = self.native.call(
            self.application, "MGCPCB", "IMGCPCBApplication", "Quit", kind=METHOD
        )
        if value is not None:
            raise ValueError("Quit did not return formal void")
        self.fact("Quit-return", dict(returned_void=True))
        self.application = self.application_unknown = None
        gc.collect()


def run_field(owner):
    """Finite sequence also exercised with synthetic native-shaped stage adapters."""
    if owner.entered:
        raise ValueError("Single-field owner was already entered; no replay")
    owner.entered = True
    baseline = owner.baseline_read()
    owner.fact("baseline", rules.validate_baseline(baseline))
    owner.verify_identity()
    owner.evidence_guard()
    if owner.load() is not None:
        raise ValueError("Load returned non-void; no dependent action")
    owner.fact("Load-return", dict(returned_void=True))
    try:
        loaded = owner.read_loaded()
        owner.put_once(loaded)
    except Exception as exc:
        if owner.counts["ValuePUT"] == 0:
            # No field write was dispatched. Release only this known-loaded
            # design after a fresh valid original-chain identity observation.
            owner.fact("pre-PUT-stop", dict(type=type(exc).__name__, message=str(exc)))
            owner.verify_identity()
            if owner.unload() is not None:
                raise ValueError("Pre-PUT cleanup UnLoad outcome incomplete") from exc
            owner.fact("UnLoad-return", dict(returned_void=True, pre_PUT_stop=True))
            owner.release_constraints()
        raise
    try:
        owner.fact("loaded-log-sample", owner.sample_logs())
    except Exception as exc:
        owner.log.safe_event(
            "noncritical_log_failure", error_type=type(exc).__name__, message=str(exc)
        )
    owner.verify_identity()
    if owner.unload() is not None:
        raise ValueError("UnLoad returned non-void; no release or Save")
    owner.fact("UnLoad-return", dict(returned_void=True))
    if owner.release_constraints() is not True:
        raise ValueError("Local constraints references not ended")
    owner.after_release()  # Old 12th or unexpected fields stop here, no Save/Close/Open.
    owner.save_once()
    owner.close_document("Close-after-save")
    owner.open_document("reopen")
    owner.verify_consumed("after-reopen")
    owner.fact("persistence", dict(reopened_independent_read_passed=True))
    owner.close_document("Close-final")
    owner.quit_owned()


def activate_owned(owner, clsid, request):
    global CAD_DISPATCHED
    import pythoncom
    import win32com.client

    from xpedition_cli._vendor.tool_driver_sdk.scope import ScopeResolver
    from xpedition_cli._vendor.tool_driver_sdk.store import StateStore, target_key

    c, log = owner.contract, owner.log
    owner.protected("before-activation")
    core.verify_inventory(c["closed_input_inventory"])
    before = core.process_inventory()
    owner.requirements.append(log.file("processes-before.json", before))
    log.event("Layout_activation_started", durable=True, clsid=str(clsid), maximum=1)
    owner.stage = "Activation"
    owner.counts["Activation"] += 1
    CAD_DISPATCHED = True
    raw = pythoncom.CoCreateInstance(
        clsid, None, pythoncom.CLSCTX_LOCAL_SERVER, pythoncom.IID_IDispatch
    )
    owner.application = win32com.client.Dispatch(raw)
    owner.application_unknown = owner.application._oleobj_.QueryInterface(pythoncom.IID_IUnknown)
    raw = None
    gui = owner.native.call(owner.application, "MGCPCB", "IMGCPCBApplication", "Gui")
    hwnd = owner.native.call(gui, "MGCPCB", "IMGCPCBGui", "HWND")
    gui = None
    process = owner.process.observe_window(hwnd)
    role = classify_process_roles(
        before, core.process_inventory(), process, expected_worker=c["layout_executable"]["path"]
    )
    owner.requirements.append(log.file("new-process-role.json", role))
    owner.identity_expected = dict(
        c["identity_expected"], forbidden_processes=[dict(pid=r["pid"]) for r in before]
    )
    owner.identity("pre_open")
    original = owner.last_identity["activation_application"]
    owner.identity_expected["activation"] = dict(
        pid=process["pid"],
        birth_filetime_100ns=process["birth_filetime_100ns"],
        hwnd=hwnd,
        executable=process["executable"],
        instance_guid=original["instance_guid"]["value"],
    )
    target = dict(
        mode="session",
        engine_id="xpedition-pcb",
        instance_id=str(process["pid"]),
        object_id=c["board_name"],
        locator=Path(c["project"]).as_uri(),
    )
    ScopeResolver(
        "xpedition-cli", environ=core.read_json(c["environment"]["path"])
    ).resolve().require(
        effects=request["effects"],
        target=target,
        paths=[(c["project"], "observe"), (str(Path(c["project"]).parent), "persist")],
        outputs=[dict(path=c["output"], effect="artifact")],
    )
    keys = [
        target_key(dict(target, mode=m, locator=Path(c[f]).as_uri()))
        for m in ("file", "session")
        for f in ("project", "board")
    ]
    keys.append(target_key(dict(target, object_id="pcb", locator="xpedition://pcb")))
    store = StateStore(c["journal"])
    try:
        if store.blocking(keys, exclude=()):
            raise ValueError("Actual owned target is blocked; no Open")
    finally:
        store.close()
    owner.requirements.append(
        log.file(
            "actual-PID-scope.json", dict(target=target, target_keys=keys, conflict_exclude=[])
        )
    )
    owner.protected("before-open")
    core.verify_inventory(c["closed_input_inventory"])
    owner.open_document("initial-open")
    return process


def main():
    global LAST_RESULT
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("request", "request-sha256", "operation-id", "contract"):
        parser.add_argument("--" + name)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    if args.preflight:
        report, _, _ = field_preflight(core.read_json(args.contract))
        print(json.dumps(report, ensure_ascii=False))
        return 0
    ref = core.file_pin(args.request)
    if ref["sha256"] != args.request_sha256:
        raise ValueError("Dispatched request hash differs")
    request = core.read_json(args.request)
    if (
        request["schema"] != "owned-native-field-request/v1"
        or request["operation_id"] != args.operation_id
    ):
        raise ValueError("Wrong field request/operation identity")
    contract = core.read_json(request["contract_path"])
    if (
        core.file_pin(request["contract_path"])["sha256"] != request["contract_sha256"]
        or contract != request["contract"]
    ):
        raise ValueError("Frozen field contract differs")
    check_contract(contract)
    log = core.Evidence(Path(contract["output"]) / "native")
    result = dict(
        schema="owned-native-field-result/v1",
        operation_id=args.operation_id,
        request_sha256=args.request_sha256,
        complete=True,
        control_completed=False,
        evidence_complete=False,
        stage="preflight",
        counts=dict.fromkeys(LIMITS, 0),
        known_effects=[],
        required_evidence=[],
        missing_required_evidence=[],
        verification=dict(status="incomplete", reason="Field lifecycle not completed"),
        rules_saved=False,
        save_state="not_called",
        persistence_verification_state="not_established",
        product_can_apply=False,
        old_C_D_E_recovered=False,
    )
    LAST_RESULT = result
    owner = None
    initialized = False
    try:
        report, clsid, deps = field_preflight(contract)
        result["required_evidence"].append(log.file("runtime-preflight.json", report))
        from xpedition_cli._vendor.tool_driver_sdk.store import StateStore

        store = StateStore(contract["journal"])
        try:
            actual = store.get(args.operation_id)
            if (
                not actual
                or actual["status"] != "dispatched"
                or actual["request_digest"] != request["contract_sha256"]
            ):
                raise ValueError("Original operation store has no matching active dispatch")
        finally:
            store.close()
        owner = FieldOwner(contract, log, *deps)
        result["counts"] = owner.counts
        import pythoncom

        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        initialized = True
        log.event(
            "owner_STA_initialized",
            durable=True,
            client_pid=os.getpid(),
            thread=threading.get_ident(),
        )
        result["actual_process"] = activate_owned(owner, clsid, request)
        run_field(owner)
        result.update(
            control_completed=True,
            evidence_complete=True,
            stage="field_persistence_completed",
            rules_saved=True,
            verification=dict(
                status="passed",
                reason="Named PUT consumed and independently read after Save/Close/Open",
            ),
        )
    except BaseException as exc:
        result["stage"] = owner.stage if owner else "preflight"
        result["error"] = dict(
            type=type(exc).__name__, message=str(exc), hresult=getattr(exc, "hresult", None)
        )
        result["verification"] = dict(
            status="incomplete", reason="Stopped without replay; actual stage facts retained"
        )
        log.safe_event("field_stopped", stage=result["stage"], error=result["error"])
    if owner:
        result["facts"] = owner.facts
        save_fact = owner.facts.get("Save-return", {})
        result["save_state"] = (
            "not_called"
            if owner.counts["Save"] == 0
            else "returned_true"
            if save_fact.get("actual_return") is True
            else "returned_false"
            if save_fact.get("actual_return") is False
            else "reply_unknown"
        )
        result["rules_saved"] = (
            owner.facts.get("persistence", {}).get("reopened_independent_read_passed") is True
        )
        result["persistence_verification_state"] = (
            "passed" if result["rules_saved"] else "not_established"
        )
        try:
            ledger = log.file(
                "actual-stage-ledger.json",
                dict(counts=owner.counts, facts=owner.facts, stage=result["stage"]),
            )
            owner.requirements.append(ledger)
        except Exception as exc:
            result["missing_required_evidence"].append(
                dict(name="actual-stage-ledger.json", error_type=type(exc).__name__)
            )
            ledger = dict(path=str(log.directory / "events.jsonl"))
        if any(owner.counts.values()):
            result["known_effects"] = [
                dict(
                    id="owned-F-field-lifecycle",
                    change="Exact actual PUT/release/consume/persistence stages retained",
                    evidence_ref=ledger["path"],
                )
            ]
        core.finalize_required_evidence(result, owner)
        if not result["control_completed"] and CAD_DISPATCHED:
            hold = min(max(int(contract.get("failure_hold_seconds", 60)), 0), 120)
            result["owner_reference_expires_at"] = (
                datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=hold)
            ).isoformat()
            log.file("stopped-owner-holding.json", result)
            until = time.monotonic() + hold
            while time.monotonic() < until:
                pythoncom.PumpWaitingMessages()
                time.sleep(0.25)
        owner.field_target = owner.params = owner.design = owner.auto = None
        owner.document = owner.document_unknown = owner.application = owner.application_unknown = (
            None
        )
        gc.collect()
        log.safe_event("client_references_ended", service_lock_release_not_inferred=True)
    if initialized:
        pythoncom.CoUninitialize()
    try:
        if result["control_completed"]:
            observation = core.observe_exit(owner.process, result["actual_process"])
            result["required_evidence"].append(log.file("owned-process-exit.json", observation))
            if observation["exited"] is not True:
                raise ValueError("Final original OS generation exit not established")
        after = core.observe_files(Path(contract["project"]).parent)
        after_pin = log.file("F-after-field-files.json", after)
        delta = core.files_delta(core.read_json(contract["closed_input_inventory"]["path"]), after)
        delta.update(
            manual_Save_called=bool(result["counts"]["Save"]),
            rule_persistence_proven=result.get("rules_saved") is True,
        )
        delta_pin = log.file("F-field-delta.json", delta)
        result["required_evidence"] += [after_pin, delta_pin]
        result["disk_observation"] = dict(
            before=contract["closed_input_inventory"],
            after=after_pin,
            delta=delta_pin,
            changes_observed=any(delta[k] for k in ("added", "removed", "changed")),
            coverage_complete=delta["complete"],
        )
    except Exception as exc:
        result["missing_required_evidence"].append(
            dict(name="final-observation", error_type=type(exc).__name__, message=str(exc))
        )
    result["evidence_complete"] = not result.get("missing_required_evidence")
    core.finalize_required_evidence(result, owner)
    result["noncritical_event_errors"] = log.errors
    result["finished_at"] = datetime.datetime.now(datetime.UTC).isoformat()
    log.file("result.json", result)
    log.stream.close()
    return 0 if result["control_completed"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:

        def argument(name):
            return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else None

        marker = dict(
            schema="owned-control-early-failure/v1",
            operation_id=argument("--operation-id"),
            request_sha256=argument("--request-sha256"),
            native_dispatch_state="unknown" if CAD_DISPATCHED else "not_started",
            counts=None if CAD_DISPATCHED else dict.fromkeys(LIMITS, 0),
            reported_result=LAST_RESULT,
            error=dict(type=type(exc).__name__, message=str(exc)),
        )
        sys.stderr.write(json.dumps(marker, ensure_ascii=False) + "\n")
        raise SystemExit(2) from None
