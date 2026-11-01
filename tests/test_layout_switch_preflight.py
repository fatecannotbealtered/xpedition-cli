"""Do not enter a foreign-board Save All modal during project attachment."""

from types import SimpleNamespace

import pytest

from xpedition_cli import native_com_adapter as native


@pytest.mark.parametrize("current", ["other.pcb", ""])
def test_open_refuses_foreign_or_unidentified_active_document(tmp_path, current):
    calls = []
    app = SimpleNamespace(
        ActiveDocument=SimpleNamespace(FullName=str(tmp_path / current) if current else ""),
        OpenDocument=lambda *args: calls.append("open"),
    )
    with pytest.raises(native.AdapterError) as error:
        native._open_layout_document(app, tmp_path / "requested.pcb")
    assert error.value.code == "E_CONFLICT"
    assert error.value.details["write_attempted"] is False
    assert not calls


def test_open_refuses_unobservable_active_document(tmp_path):
    class App:
        @property
        def ActiveDocument(self):
            raise RuntimeError("unobservable")

        def OpenDocument(self, path):
            pytest.fail("must not dispatch OpenDocument")

    with pytest.raises(native.AdapterError) as error:
        native._open_layout_document(App(), tmp_path / "requested.pcb")
    assert error.value.code == "E_CONFLICT"
    assert error.value.details["write_attempted"] is False


def test_public_attach_plan_refuses_switch_before_open_dispatch(tmp_path, monkeypatch):
    from test_driver_v2 import _scope, _worker_fixture

    from xpedition_cli.driver import Adapter, runtime

    _scope(tmp_path, monkeypatch)
    requested = tmp_path / "requested.pcb"
    requested.write_bytes(b"synthetic fixture")
    calls = []

    class Backend:
        def invoke(self, method, params, **kwargs):
            calls.append(method)
            assert method == "identity"
            return {
                "application": {
                    "pid": 4242,
                    "document": {
                        "open": True,
                        "path": str(tmp_path / "other.pcb"),
                        "status": "observed",
                    },
                },
                "adapter": _worker_fixture(),
            }

    with runtime(Adapter(Backend()), state_dir=tmp_path / "state") as cli:
        result = cli.run(
            [
                "session",
                "attach",
                "--kind",
                "pcb",
                "--pid",
                "4242",
                "--project",
                str(requested),
                "--dry-run",
            ]
        )
    assert result.envelope["error"]["code"] == "E_CONFLICT", result.envelope
    assert result.envelope["error"]["details"]["dispatch_state"] == "not_started"
    assert calls == ["identity"]
