"""A locked replacement folder never authorizes ending an existing CAD process."""

from __future__ import annotations

import pytest

from xpedition_cli import native_com_adapter as adapter


def test_locked_folder_requires_explicit_session_shutdown(monkeypatch):
    monkeypatch.setattr(adapter, "_layout_processes", lambda: [1234])
    monkeypatch.setattr(
        adapter,
        "_application",
        lambda *args, **kwargs: pytest.fail("Implicit CAD attachment/shutdown was attempted"),
    )
    monkeypatch.setattr(
        adapter.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("A force-termination fallback was attempted"),
    )
    with pytest.raises(adapter.AdapterError) as failure:
        adapter._quit_layout(None)
    assert failure.value.code == "E_HUMAN_REQUIRED"
    assert failure.value.details["pids"] == [1234]
    assert failure.value.details["shutdown_attempted"] is False


def test_absent_layout_needs_no_shutdown(monkeypatch):
    monkeypatch.setattr(adapter, "_layout_processes", lambda: [])
    monkeypatch.setattr(
        adapter,
        "_application",
        lambda *args, **kwargs: pytest.fail("An absent CAD instance was contacted"),
    )
    assert adapter._quit_layout(None) is False
