"""One finite material-read deadline for any actual net; synthetic adapter, no CAD."""

import pytest
from test_driver_v2 import _scope

from xpedition_cli.cli import pcb
from xpedition_cli.driver import Adapter, runtime
from xpedition_cli.operation_budget import profile_for

LIVE_READ_LIMIT = int(profile_for("pcb inspect")["native_call_seconds"]["board_info"])


@pytest.mark.parametrize(
    "net,budget,ok",
    [
        ("GND", 600, True),
        ("GND", 601, True),
        ("GND", LIVE_READ_LIMIT, True),
        ("GND", LIVE_READ_LIMIT + 1, False),
        ("A_IN", 181, True),
        ("A_IN", 180, True),
        ("A_IN", 600, True),
        ("A_IN", 601, True),
        ("A_IN", LIVE_READ_LIMIT, True),
        ("A_IN", LIVE_READ_LIMIT + 1, False),
    ],
)
def test_public_scoped_material_deadline(tmp_path, monkeypatch, net, budget, ok):
    _scope(tmp_path, monkeypatch)
    board = tmp_path / "EXAMPLE_BOARD.pcb"
    board.write_bytes(b"synthetic")
    calls = []

    class Backend:
        read_timeout_seconds = budget

        def invoke(self, method, params, **kwargs):
            calls.append((method, params, kwargs))
            return {
                "pcb": str(board),
                "observation": {"engineering_write_dispatched": False, "persistence": "unknown"},
            }

    monkeypatch.setattr(pcb, "reader", lambda _: Backend())
    with runtime(Adapter(Backend()), state_dir=tmp_path / "original-state") as cli:
        r = cli.run(
            [
                "pcb",
                "inspect",
                "--project",
                str(board),
                "--live-geometry",
                "--pid",
                "42",
                "--nets",
                net,
                "--layers",
                "1",
                "--timeout",
                str(budget),
            ]
        )
    assert r.envelope["ok"] is ok, r.envelope
    if ok:
        assert len(calls) == 1 and calls[0][0] == "board_info"
        assert calls[0][1]["start"] is False and calls[0][1]["pid"] == 42
        assert calls[0][2] == {"timeout_seconds": budget}
    else:
        assert not calls and r.envelope["error"]["code"] == "E_VALIDATION"
