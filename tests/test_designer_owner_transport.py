"""Pure protocol tests; native COM factory is never called in this file."""

from __future__ import annotations

import ctypes
import json
import threading

import pytest

from xpedition_cli import designer_owner_transport as T


@pytest.fixture(autouse=True)
def no_native_broker_factory(monkeypatch):
    hits = []

    def forbidden(*args, **kwargs):
        hits.append(True)
        pytest.fail("Pure transport tests must not initialize a native broker or DLL")

    monkeypatch.setattr(T, "create_native_runtime", forbidden)
    monkeypatch.setattr(ctypes, "WinDLL", forbidden, raising=False)
    monkeypatch.setattr(ctypes, "OleDLL", forbidden, raising=False)
    yield
    assert hits == [], "Swallowing a native initialization attempt still fails"


class Pin:
    def __init__(self, *, birth="100", pid=123, alive=True):
        self.value = {
            "pid": pid,
            "birth_filetime_100ns": birth,
            "executable": r"C:\licensed\viewdraw.exe",
            "alive": alive,
        }

    def snapshot(self):
        return dict(self.value)


def _core(*, peer=None, pin=None, marshal=None, discard=None):
    calls, discarded = [], []
    application, stream = object(), object()
    expected = T.ExpectedGeneration(123, "100", r"C:\licensed\viewdraw.exe")
    pin = pin or Pin()

    def rpc():
        calls.append("rpc")
        return peer or {"status": 0, "protocol_sequence": 3, "client_pid": 123}

    def read_epoch(value):
        calls.append("epoch")
        return value

    def transfer(value):
        calls.append("marshal")
        assert value is application
        return stream

    handoff = T.Handoff(
        "private-epoch", rpc, read_epoch, marshal or transfer, discard or discarded.append
    )
    return handoff, expected, pin, application, stream, calls, discarded


def test_unarmed_offer_is_not_authenticated_or_unwrapped():
    handoff, _, _, app, _, calls, _ = _core()
    assert handoff.offer(app, "private-epoch") is False
    assert calls == []
    assert handoff.snapshot()["reason"] == "host_offer_unarmed_closed_or_duplicate"


@pytest.mark.parametrize(
    "peer",
    [
        {"status": 1725, "protocol_sequence": 3, "client_pid": 123},
        {"status": 0, "protocol_sequence": 1, "client_pid": 123},
        {"status": 0, "protocol_sequence": 3, "client_pid": 124},
    ],
)
def test_wrong_rpc_peer_refuses_before_epoch_or_app_unwrap(peer):
    handoff, expected, pin, app, _, calls, _ = _core(peer=peer)
    handoff.arm(expected, pin)
    assert handoff.offer(app, "private-epoch") is False
    assert calls == ["rpc"]
    assert handoff.snapshot()["accepted"] == 0


def test_epoch_is_only_correlation_and_does_not_replace_peer_authentication():
    handoff, expected, pin, app, _, calls, _ = _core()
    handoff.arm(expected, pin)
    assert handoff.offer(app, "other-epoch") is False
    assert calls == ["rpc", "epoch"]
    assert handoff.snapshot()["reason"] == "host_offer_epoch_mismatch"


def test_same_pid_different_birth_and_exited_original_handle_are_rejected():
    for field, value in (
        ("birth_filetime_100ns", "101"),
        ("alive", False),
        ("executable", r"C:\foreign\viewdraw.exe"),
    ):
        handoff, expected, pin, app, _, calls, _ = _core()
        handoff.arm(expected, pin)
        pin.value[field] = value
        assert handoff.offer(app, "private-epoch") is False
        assert calls == []
        assert handoff.snapshot()["reason"] == "host_generation_before_mismatch"


def test_complete_offer_yields_one_stream_and_never_exports_native_capability():
    handoff, expected, pin, app, stream, calls, _ = _core()
    handoff.arm(expected, pin)
    assert handoff.offer(app, "private-epoch") is True
    assert handoff.take_stream() is stream
    with pytest.raises(T.TransportError, match="host_handoff_not_complete"):
        handoff.take_stream()
    assert calls == ["rpc", "epoch", "marshal"]
    snapshot = handoff.snapshot()
    assert snapshot["startup_script_returned"] is None
    assert snapshot["application_server_pid_observed"] is False
    assert "private-epoch" not in json.dumps(snapshot)
    assert str(id(app)) not in json.dumps(snapshot)
    assert handoff.offer(app, "private-epoch") is False


def test_generation_change_after_marshal_discards_packet_once():
    pin, discarded = Pin(), []
    stream = object()

    def transfer(incoming):
        pin.value["birth_filetime_100ns"] = "101"
        return stream

    handoff, expected, _, app, _, _, _ = _core(pin=pin, marshal=transfer, discard=discarded.append)
    handoff.arm(expected, pin)
    assert handoff.offer(app, "private-epoch") is False
    assert discarded == [stream]
    assert handoff.snapshot()["reason"] == "host_generation_after_mismatch"


def test_late_marshal_cannot_revive_a_closed_handoff():
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    discarded, answer = [], []
    stream = object()

    def transfer(incoming):
        entered.set()
        assert release.wait(2), "test teardown must release fake marshal"
        return stream

    handoff, expected, pin, app, _, _, _ = _core(marshal=transfer, discard=discarded.append)
    handoff.arm(expected, pin)

    def callback():
        answer.append(handoff.offer(app, "private-epoch"))
        completed.set()

    thread = threading.Thread(target=callback)
    thread.start()
    try:
        assert entered.wait(1)
        assert handoff.close() is False
        assert handoff.snapshot()["inflight"] == 1
        assert handoff.snapshot()["accepted"] == 0
    finally:
        release.set()
        thread.join(2)
    assert completed.is_set() and not thread.is_alive()
    assert answer == [False]
    assert discarded == [stream]
    assert handoff.snapshot()["accepted"] == 0
    assert handoff.close() is True


def test_packet_release_cannot_hold_observer_lock():
    entered, release, observed = threading.Event(), threading.Event(), threading.Event()
    snapshots = []

    def discard(stream):
        entered.set()
        release.wait()

    handoff, expected, pin, app, _, _, _ = _core(discard=discard)
    handoff.arm(expected, pin)
    assert handoff.offer(app, "private-epoch") is True
    closer = threading.Thread(target=handoff.close)
    observer = None
    closer.start()
    try:
        assert entered.wait(1)

        def observe():
            snapshots.append(handoff.snapshot())
            observed.set()

        observer = threading.Thread(target=observe)
        observer.start()
        assert observed.wait(1)
        assert snapshots[0]["closed_to_new_calls"] is True
    finally:
        release.set()
        closer.join(2)
        if observer is not None:
            observer.join(2)
    assert not closer.is_alive() and observer is not None and not observer.is_alive()


def test_default_contract_has_no_enable_flag_or_application_lookup():
    description = T.transport_contract()
    assert description["native_default"] == "deny"
    assert description["running_instance_lookup"] is False
    assert description["native_qualification"] == "requires_session_specific_observation"


def test_early_callback_waits_for_independent_launch_anchor_without_unwrap():
    handoff, expected, pin, app, stream, calls, _ = _core()
    handoff._arm_wait_seconds = 1
    result = []
    thread = threading.Thread(target=lambda: result.append(handoff.offer(app, "private-epoch")))
    thread.start()
    try:
        assert handoff.changed.wait(1)
        assert handoff.snapshot()["pending_original_launch_anchor"] is True
        assert calls == []
        handoff.arm(expected, pin)
    finally:
        handoff._armed.set()
        thread.join(2)
    assert not thread.is_alive() and result == [True]
    assert handoff.take_stream() is stream


@pytest.mark.parametrize("late", [False, True])
def test_failed_packet_release_is_sticky_and_never_retried(late):
    attempts = []

    def fail(stream):
        attempts.append(stream)
        raise RuntimeError("synthetic release failure")

    pin = Pin()
    stream = object()

    def marshal(incoming):
        if late:
            pin.value["birth_filetime_100ns"] = "101"
        return stream

    handoff, expected, _, app, _, _, _ = _core(pin=pin, marshal=marshal, discard=fail)
    handoff.arm(expected, pin)
    assert handoff.offer(app, "private-epoch") is (not late)
    assert handoff.close() is False
    assert handoff.close() is False
    assert attempts == [stream]
    assert handoff._failed_streams == [stream]
    assert handoff.snapshot()["marshal_cleanup_observation"] == "failed"


def test_entry_counts_are_unobserved_until_broker_monitor_is_constructed():
    handoff, *_ = _core()
    before = handoff.snapshot()["broker_entries"]
    assert before["status"] == "unobserved"
    assert all(before[name] is None for name in T._ENTRY_COUNTERS)
    T.BrokerDispatch(handoff)
    actual = handoff.snapshot()["broker_entries"]
    assert actual["status"] == "synchronized_live"
    assert all(actual[name] == 0 for name in T._ENTRY_COUNTERS)
    assert actual["offer_counts_cover_all_com_entries"] is False
    assert before["names_query_count"] is None


def test_unknown_names_and_bad_name_shape_are_counted_without_any_offer_or_secret():
    handoff, *_ = _core()
    entries = T.BrokerDispatch(handoff)
    assert entries.names_query(1, lambda: "OfferStartupApplication") is True
    assert entries.names_query(1, lambda: "secret-external-name") is False
    assert entries.names_query(2, lambda: pytest.fail("Bad count must not inspect names")) is False
    state = handoff.snapshot()
    assert state["received"] == 0
    assert state["broker_entries"]["names_query_count"] == 3
    assert state["broker_entries"]["names_query_rejected"] == 2
    assert state["broker_entries"]["names_query_inflight"] == 0
    assert "secret-external-name" not in json.dumps(state)


@pytest.mark.parametrize("shape", ["member", "arity", "named"])
def test_early_invoke_rejection_is_visible_while_offer_count_stays_zero(shape):
    handoff, *_ = _core()
    entries = T.BrokerDispatch(handoff)

    def forbidden():
        pytest.fail("An early rejected Invoke must not read later arguments")

    member = 2 if shape == "member" else 1
    count = forbidden if shape == "member" else lambda: 1 if shape == "arity" else 2
    named = forbidden if shape in {"member", "arity"} else lambda: 1
    assert entries.invoke(member, count, named, forbidden) == (False, None)
    state = handoff.snapshot()
    assert state["received"] == state["callback_body_completed"] == 0
    assert state["broker_entries"]["invoke_entry_count"] == 1
    assert state["broker_entries"]["invoke_shape_rejected"] == 1
    assert state["broker_entries"]["recognized_offer_dispatched"] == 0
    assert state["broker_entries"]["invoke_inflight"] == 0


def test_recognized_invoke_counts_dispatch_without_changing_offer_authentication():
    handoff, expected, pin, app, stream, calls, _ = _core()
    entries = T.BrokerDispatch(handoff)
    handoff.arm(expected, pin)
    assert entries.invoke(1, lambda: 2, lambda: 0, lambda: (app, "private-epoch")) == (True, True)
    state = handoff.snapshot()
    assert state["received"] == state["accepted"] == state["callback_body_completed"] == 1
    assert state["broker_entries"]["invoke_entry_count"] == 1
    assert state["broker_entries"]["recognized_offer_dispatched"] == 1
    assert state["broker_entries"]["invoke_shape_rejected"] == 0
    assert handoff.take_stream() is stream
    assert calls == ["rpc", "epoch", "marshal"]
    assert "private-epoch" not in json.dumps(state)
    assert str(id(app)) not in json.dumps(state)


def test_a_recognized_dispatch_can_still_be_an_unauthenticated_offer():
    handoff, expected, pin, app, _, calls, _ = _core(
        peer={"status": 0, "protocol_sequence": 3, "client_pid": 999}
    )
    entries = T.BrokerDispatch(handoff)
    handoff.arm(expected, pin)
    assert entries.invoke(1, lambda: 2, lambda: 0, lambda: (app, "private-epoch")) == (True, False)
    state = handoff.snapshot()
    assert state["accepted"] == 0 and state["rejected"] == 1
    assert state["broker_entries"]["recognized_offer_dispatched"] == 1
    assert calls == ["rpc"]


def test_live_entry_and_offer_snapshot_is_atomic_copy_while_marshal_is_pending():
    entered, release = threading.Event(), threading.Event()

    def marshal(incoming):
        entered.set()
        assert release.wait(2), "Fixture teardown must release the pure fake marshal"
        return object()

    handoff, expected, pin, app, *_ = _core(marshal=marshal)
    entries = T.BrokerDispatch(handoff)
    handoff.arm(expected, pin)
    answer = []
    thread = threading.Thread(
        target=lambda: answer.append(
            entries.invoke(1, lambda: 2, lambda: 0, lambda: (app, "private-epoch"))
        )
    )
    thread.start()
    try:
        assert entered.wait(1)
        state = handoff.snapshot()
        assert state["received"] == state["inflight"] == 1
        assert state["accepted"] == 0
        assert state["broker_entries"]["invoke_inflight"] == 1
        assert state["broker_entries"]["recognized_offer_dispatched"] == 1
        state["broker_entries"]["invoke_entry_count"] = 999
        assert handoff.snapshot()["broker_entries"]["invoke_entry_count"] == 1
    finally:
        release.set()
        thread.join(2)
    assert answer == [(True, True)] and not thread.is_alive()
    assert handoff.snapshot()["broker_entries"]["invoke_inflight"] == 0


def test_entry_read_failure_is_not_counted_as_an_offer_or_known_shape_rejection():
    handoff, *_ = _core()
    entries = T.BrokerDispatch(handoff)

    def failed():
        raise RuntimeError("secret-not-retained")

    with pytest.raises(RuntimeError):
        entries.invoke(1, failed, lambda: 0, lambda: pytest.fail("Arguments reached"))
    state = handoff.snapshot()
    assert state["received"] == 0
    assert state["broker_entries"]["invoke_read_failed"] == 1
    assert state["broker_entries"]["invoke_shape_rejected"] == 0
    assert state["broker_entries"]["invoke_inflight"] == 0
    assert "secret-not-retained" not in json.dumps(state)
