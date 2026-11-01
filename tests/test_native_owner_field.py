"""Synthetic native-shaped lifecycle failures, never COM or a real journal."""

import types

import pytest

from scripts.native_acceptance import owner_field as field


class Fake:
    def __init__(self, fail=None):
        self.entered = False
        self.calls = []
        self.facts = {}
        self.fail = fail
        self.counts = {"ValuePUT": 0}
        self.log = types.SimpleNamespace(safe_event=lambda *a, **k: None)

    def action(self, name):
        self.calls.append(name)
        if self.fail == name:
            raise OSError(name)

    def fact(self, k, v):
        self.facts[k] = v

    def baseline_read(self):
        self.action("baseline")
        return {}

    def verify_identity(self):
        self.action("identity")

    def evidence_guard(self):
        return None

    def load(self):
        self.action("Load")

    def read_loaded(self):
        self.action("loaded-read")
        return {}

    def put_once(self, loaded):
        self.counts["ValuePUT"] += 1
        self.action("PUT")

    def sample_logs(self):
        self.action("log")
        return {}

    def unload(self):
        self.action("UnLoad")

    def release_constraints(self):
        self.action("release")
        return True

    def after_release(self):
        self.action("consumption")

    def save_once(self):
        self.action("Save")

    def close_document(self, label):
        self.action(label)

    def open_document(self, label):
        self.action(label)

    def verify_consumed(self, label):
        self.action(label)

    def quit_owned(self):
        self.action("Quit")


@pytest.fixture(autouse=True)
def fake_baseline(monkeypatch):
    monkeypatch.setattr(field.rules, "validate_baseline", lambda fp: {})


def test_full_sequence_and_reentry_zero_additional_actions():
    f = Fake()
    field.run_field(f)
    assert f.calls == [
        "baseline",
        "identity",
        "Load",
        "loaded-read",
        "PUT",
        "log",
        "identity",
        "UnLoad",
        "release",
        "consumption",
        "Save",
        "Close-after-save",
        "reopen",
        "after-reopen",
        "Close-final",
        "Quit",
    ]
    before = list(f.calls)
    with pytest.raises(ValueError, match="already entered"):
        field.run_field(f)
    assert f.calls == before


@pytest.mark.parametrize(
    "failure",
    [
        "baseline",
        "identity",
        "Load",
        "loaded-read",
        "PUT",
        "UnLoad",
        "release",
        "consumption",
        "Save",
        "Close-after-save",
        "reopen",
        "after-reopen",
        "Close-final",
        "Quit",
    ],
)
def test_failure_never_continues_or_replays(failure):
    f = Fake(failure)
    with pytest.raises(OSError):
        field.run_field(f)
    if failure == "loaded-read":
        assert f.calls[-3:] == ["identity", "UnLoad", "release"]
    else:
        assert f.calls[-1] == failure
    assert f.calls.count("PUT") <= 1
    assert f.calls.count("UnLoad") <= 1


def test_noncritical_log_failure_still_unloads_once():
    f = Fake("log")
    field.run_field(f)
    assert f.calls.count("UnLoad") == 1
    assert f.calls[-1] == "Quit"


def test_old_value_after_release_forbids_save_close_open():
    f = Fake("consumption")
    with pytest.raises(OSError):
        field.run_field(f)
    assert "PUT" in f.calls and "UnLoad" in f.calls
    assert all(k not in f.calls for k in ("Save", "Close-after-save", "reopen", "Quit"))


class Raw:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def Invoke(self, *args):
        self.calls.append(args)
        if self.error:
            raise self.error


def native_fixture(raw):
    import threading

    owner = types.SimpleNamespace(
        thread=threading.get_ident(),
        counts=dict.fromkeys(field.LIMITS, 0),
        field_target=raw,
        document=raw,
        application=raw,
        contract={"board": "D:/owned-F/PCB.pcb"},
        allow_save=True,
        allow_close=True,
        allow_open=True,
        log=types.SimpleNamespace(event=lambda *a, **k: None, safe_event=lambda *a, **k: None),
    )
    formal = types.SimpleNamespace(member=lambda *a: ({"dispid": 55}, "frozen formal"))
    original = types.SimpleNamespace(
        check=lambda: None,
        calls=[],
        formal=formal,
        call=lambda *a, **k: (_ for _ in ()).throw(ValueError("base refused")),
    )
    return owner, field.FieldNative(owner, original, formal)


def test_actual_raw_put_reply_loss_counts_once_and_never_replays():
    raw = Raw(OSError("reply lost"))
    owner, native = native_fixture(raw)
    with pytest.raises(OSError):
        native.call(raw, "ConstraintsAuto", "IConstraint", "Value", 14.0, kind=field.PUT)
    with pytest.raises(ValueError, match="already attempted"):
        native.call(raw, "ConstraintsAuto", "IConstraint", "Value", 14.0, kind=field.PUT)
    assert len(raw.calls) == owner.counts["ValuePUT"] == 1


@pytest.mark.parametrize("wrong", ["object", "value", "type"])
def test_native_put_capability_refuses_before_invoke(wrong):
    raw = Raw()
    owner, native = native_fixture(raw)
    obj = Raw() if wrong == "object" else raw
    value = 13.0 if wrong == "value" else 14 if wrong == "type" else 14.0
    with pytest.raises(ValueError):
        native.call(obj, "ConstraintsAuto", "IConstraint", "Value", value, kind=field.PUT)
    assert owner.counts["ValuePUT"] == 0 and raw.calls == []


def test_save_and_close_are_bound_to_current_doc_and_stage():
    raw = Raw()
    owner, native = native_fixture(raw)
    owner.allow_save = False
    with pytest.raises(ValueError):
        native.call(raw, "MGCPCB", "IMGCPCBDocument", "Save", kind=field.METHOD)
    with pytest.raises(ValueError):
        native.call(Raw(), "MGCPCB", "IMGCPCBDocument", "Close", False, kind=field.METHOD)
    assert owner.counts["Save"] == owner.counts["Close"] == 0 and raw.calls == []


def test_range_is_actually_parsed_from_pinned_vendor_definition(tmp_path):
    import json

    p = tmp_path / "definition.json"
    p.write_text(
        json.dumps(
            {
                "accessibility": {
                    "tree": (
                        "约束: Value: EXP_TRACE_WIDTH\n文本 th ID: 850\n"
                        "最小值: Value: 0.01 ID: 578\n最大值: Value: 5000 ID: 579"
                    )
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert field.definition_range(field.core.file_pin(p))["upper"] == "5000"
    p.write_text(json.dumps({"accessibility": {"tree": "EXP_TRACE_WIDTH"}}), encoding="utf-8")
    with pytest.raises(ValueError):
        field.definition_range(field.core.file_pin(p))


def test_fact_file_failure_keeps_memory_and_marks_missing_without_abandoning_release():
    owner = types.SimpleNamespace(
        facts={},
        requirements=[],
        missing_required_evidence=[],
        log=types.SimpleNamespace(file=lambda *a: (_ for _ in ()).throw(PermissionError("fact"))),
    )
    field.FieldOwner.fact(owner, "PUT-return", {"returned_void": True})
    assert owner.facts["PUT-return"]["returned_void"] is True
    assert owner.missing_required_evidence == [
        {"name": "fact-PUT-return.json", "error_type": "PermissionError"}
    ]


def test_put_evidence_failure_can_unload_but_cannot_save():
    class Missing(Fake):
        def put_once(self, loaded):
            super().put_once(loaded)
            self.missing = True

        def after_release(self):
            super().after_release()
            if self.missing:
                raise PermissionError("required evidence")

    f = Missing()
    with pytest.raises(PermissionError):
        field.run_field(f)
    assert f.calls.count("UnLoad") == 1 and "Save" not in f.calls
