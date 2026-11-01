"""An attach that finds no running application says which one and how to start it.

Layout and Designer are separate products with separate COM classes: `pcb *`
reaches Layout; `schematic *`, `library *` and `bom *` reach Designer. The
doctor's own reporting is in test_cli_environment.
"""

from __future__ import annotations

import pytest

from xpedition_cli.native_com_adapter import AdapterError, _active_object, _viewdraw_active


@pytest.mark.parametrize("hresult", [-2147024882, -2147024891, -2147467259, None])
def test_layout_attachment_error_is_not_absence_and_is_not_retried(monkeypatch, hresult):
    from xpedition_cli import native_com_adapter as A

    class NativeFailure(Exception):
        pass

    error = NativeFailure("synthetic native attachment failure")
    error.hresult = hresult
    calls = []

    class Client:
        def GetActiveObject(self, name):
            calls.append(name)
            raise error

    monkeypatch.setattr(A, "application_progids", lambda domain: ("first", "second"))
    with pytest.raises(AdapterError) as caught:
        _active_object(Client())
    assert caught.value.code != "E_NOT_FOUND"
    assert caught.value.details["hresult"] == hresult
    assert caught.value.details["write_attempted"] is False
    assert calls == ["first"]


@pytest.mark.parametrize(
    ("attach", "application", "kind"),
    [(_active_object, "Layout", "--kind pcb"), (_viewdraw_active, "Designer", "--kind schematic")],
)
def test_attach_failure_names_the_application_and_how_to_start_it(
    attach, application, kind
) -> None:
    class NoSession:
        def GetActiveObject(self, progid):  # noqa: N802 - COM spelling
            error = OSError("not running")
            error.hresult = -2147221021
            raise error

    with pytest.raises(AdapterError) as caught:
        attach(NoSession())
    error = caught.value
    assert error.code == "E_NOT_FOUND"
    assert application in error.details["application"]
    assert kind in error.details["hint"]
    assert error.details["serves_commands"]
