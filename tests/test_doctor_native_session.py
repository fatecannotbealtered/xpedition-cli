"""`doctor` has to say which Xpedition application is actually attached.

Layout and Designer are separate products with separate COM classes: `pcb *`
reaches Layout, `schematic *` and `agent snapshot` reach Designer. `doctor`
previously reported only that an adapter was configured, so an agent could not
tell the two apart, or tell that neither was running, until a task command
failed. These tests pin the reporting, and pin that the probe can never be the
reason `doctor` itself fails.
"""

from __future__ import annotations

import pytest

from xpedition_cli import main as cli
from xpedition_cli.native_com_adapter import AdapterError, _active_object, _viewdraw_active


def session_check(monkeypatch, *, layout: bool, designer: bool) -> dict:
    monkeypatch.setattr(
        cli,
        "_native_live_applications",
        lambda ready: {"probed": True, "layout": layout, "designer": designer, "reason": None},
    )
    checks = cli._doctor({})["checks"]
    return next(check for check in checks if check["check"] == "native_session")


def test_both_applications_attached_passes_without_a_fix(monkeypatch) -> None:
    check = session_check(monkeypatch, layout=True, designer=True)
    assert check["status"] == "pass" and check["fix"] is None
    assert "Layout" in check["message"] and "Designer" in check["message"]


@pytest.mark.parametrize(
    ("layout", "designer", "missing", "affected"),
    [
        (True, False, "Designer", "agent snapshot"),
        (False, True, "Layout", "pcb commands"),
    ],
)
def test_one_application_attached_names_the_other_and_what_it_serves(
    monkeypatch, layout, designer, missing, affected
) -> None:
    check = session_check(monkeypatch, layout=layout, designer=designer)
    assert check["status"] == "pass"
    assert missing in check["message"] and affected in check["message"]


def test_nothing_attached_warns_with_a_runnable_fix(monkeypatch) -> None:
    check = session_check(monkeypatch, layout=False, designer=False)
    assert check["status"] == "warn"
    # The fix has to name both kinds; picking the wrong one starts the wrong product.
    assert "--kind pcb" in check["fix"] and "--kind schematic" in check["fix"]


def test_probe_is_skipped_when_the_backend_is_not_ready() -> None:
    # No adapter subprocess may be spawned on a machine without Xpedition.
    assert cli._native_live_applications(False) == {
        "probed": False,
        "layout": False,
        "designer": False,
        "reason": None,
    }


def test_a_failing_probe_is_reported_but_does_not_break_doctor(monkeypatch) -> None:
    def explode(*args, **kwargs):
        raise RuntimeError("adapter exploded")

    monkeypatch.setattr(cli.NativeBackend, "invoke", explode)
    result = cli._native_live_applications(True)
    assert result["probed"] is False and "adapter exploded" in result["reason"]

    monkeypatch.setattr(cli, "_native_live_applications", lambda ready: result)
    check = next(c for c in cli._doctor({})["checks"] if c["check"] == "native_session")
    assert check["status"] == "warn" and "could not probe" in check["message"]


@pytest.mark.parametrize(
    ("attach", "application", "kind"),
    [(_active_object, "Layout", "--kind pcb"), (_viewdraw_active, "Designer", "--kind schematic")],
)
def test_attach_failure_names_the_application_and_how_to_start_it(
    attach, application, kind
) -> None:
    class NoSession:
        def GetActiveObject(self, progid):  # noqa: N802 - COM spelling
            raise OSError("not running")

    with pytest.raises(AdapterError) as caught:
        attach(NoSession())
    error = caught.value
    assert error.code == "E_NOT_FOUND"
    assert application in error.details["application"]
    assert kind in error.details["hint"]
    assert error.details["serves_commands"]
