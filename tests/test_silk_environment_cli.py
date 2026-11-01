"""Real public SDK read route; native observations here are an explicit double."""

import pytest
from test_driver_v2 import _scope, _worker_fixture

from xpedition_cli.cli import pcb
from xpedition_cli.driver import Adapter, runtime


@pytest.mark.parametrize("extra,ok", [([], True), (["--nets", "GND"], False)])
def test_public_surroundings_query_is_bound_and_never_edits(tmp_path, monkeypatch, extra, ok):
    _scope(tmp_path, monkeypatch)
    board = tmp_path / "owned.pcb"
    board.write_bytes(b"synthetic")
    calls = []

    class Backend:
        read_timeout_seconds = 600

        def invoke(self, method, params, **kwargs):
            calls.append((method, params))
            if method == "identity":
                return {"application": {"pid": 4242}, "adapter": _worker_fixture()}
            assert method == "silk_environment_state"
            assert params["pid"] == 4242 and params["start"] is False
            assert params["observe_only"] is True and params["text_uids"] == ["563"]
            return {
                "silk_environment": {"fixture_only": True},
                "qualification": {"write_eligibility": {"eligible": False}},
                "can_apply": False,
            }

    backend = Backend()
    monkeypatch.setattr(pcb, "reader", lambda options: backend)
    with runtime(Adapter(backend), state_dir=tmp_path / "state") as cli:
        result = cli.run(
            [
                "pcb",
                "inspect",
                "--project",
                str(board),
                "--live-geometry",
                "--pid",
                "4242",
                "--silk-text-uids",
                "563",
                *extra,
            ]
        )
    assert result.envelope["ok"] is ok, result.envelope
    if ok:
        assert result.envelope["data"]["can_apply"] is False
    else:
        assert result.envelope["error"]["code"] == "E_VALIDATION"
    assert all(method in {"identity", "silk_environment_state"} for method, _ in calls)
