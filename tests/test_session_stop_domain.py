"""`session stop` quits the application that is actually attached.

Quitting discards unsaved design work. `--kind` used to default to pcb, so with
only Designer running the command reported success against an application that
was not there and left the one the caller meant still running. The domain is now
resolved against the live session, and anything ambiguous fails closed.
"""

from __future__ import annotations

import pytest

from xpedition_cli import main as cli
from xpedition_cli.errors import CLIError


def probe(monkeypatch, *, layout: bool, designer: bool, probed: bool = True) -> None:
    monkeypatch.setattr(
        cli,
        "_native_live_applications",
        lambda ready: {
            "probed": probed,
            "layout": layout,
            "designer": designer,
            "reason": None,
        },
    )


@pytest.mark.parametrize(
    ("layout", "designer", "expected"), [(False, True, "schematic"), (True, False, "pcb")]
)
def test_the_single_attached_application_is_the_one_stopped(
    monkeypatch, layout, designer, expected
) -> None:
    probe(monkeypatch, layout=layout, designer=designer)
    assert cli._resolve_stop_domain({}) == expected


def test_both_attached_refuses_instead_of_picking_one(monkeypatch) -> None:
    probe(monkeypatch, layout=True, designer=True)
    with pytest.raises(CLIError) as caught:
        cli._resolve_stop_domain({})
    assert caught.value.code == "E_USAGE"
    assert "--kind pcb" in caught.value.message and "--kind schematic" in caught.value.message


def test_nothing_attached_is_not_a_successful_stop(monkeypatch) -> None:
    probe(monkeypatch, layout=False, designer=False)
    with pytest.raises(CLIError) as caught:
        cli._resolve_stop_domain({})
    assert caught.value.code == "E_NOT_FOUND"


@pytest.mark.parametrize("selector", [{"kind": "pcb"}, {"project": "board.pcb"}])
def test_an_explicit_domain_that_is_not_running_is_reported(monkeypatch, selector) -> None:
    probe(monkeypatch, layout=False, designer=True)
    with pytest.raises(CLIError) as caught:
        cli._resolve_stop_domain(selector)
    assert caught.value.code == "E_NOT_FOUND"
    assert caught.value.details["attached"] == ["schematic"]


def test_an_explicit_domain_that_is_running_is_honoured(monkeypatch) -> None:
    probe(monkeypatch, layout=True, designer=True)
    assert cli._resolve_stop_domain({"kind": "schematic"}) == "schematic"


def test_without_a_probe_the_historical_default_still_applies(monkeypatch) -> None:
    # A machine with no adapter must not be blocked by a probe that cannot run.
    probe(monkeypatch, layout=False, designer=False, probed=False)
    assert cli._resolve_stop_domain({}) == "pcb"
    assert cli._resolve_stop_domain({"kind": "schematic"}) == "schematic"


def test_an_invalid_kind_is_still_a_validation_error(monkeypatch) -> None:
    probe(monkeypatch, layout=True, designer=True)
    with pytest.raises(CLIError) as caught:
        cli._resolve_stop_domain({"kind": "typo"})
    assert caught.value.code == "E_VALIDATION"
