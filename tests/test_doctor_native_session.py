"""An attach that finds no running application says which one and how to start it.

Layout and Designer are separate products with separate COM classes: `pcb *`
reaches Layout; `schematic *`, `library *` and `bom *` reach Designer. The
doctor's own reporting is in test_cli_environment.
"""

from __future__ import annotations

import pytest

from xpedition_cli.native_com_adapter import AdapterError, _active_object, _viewdraw_active


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
