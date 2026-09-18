"""Both native domains start through the common launcher.

`SDD_HOME/common/win64/bin` holds ~35 KB launcher stubs that set up the release
environment and then start the ~37 MB products under `wg` and `wv`. Starting a
product directly skips that setup and the process exits with
STATUS_DLL_NOT_FOUND (0xC0000135), which is what the schematic domain used to do:
every native schematic command was unusable from a cold start.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from xpedition_cli import native_com_adapter as adapter
from xpedition_cli.native_com_adapter import AdapterError, _native_executable


@pytest.fixture
def sdd_home(tmp_path, monkeypatch) -> Path:
    home = (tmp_path / "SDD_HOME").resolve()
    launchers = home / "common" / "win64" / "bin"
    launchers.mkdir(parents=True)
    for name in ("viewdraw.exe", "ExpeditionPCB.exe"):
        (launchers / name).write_bytes(b"launcher stub")
    # The real products, which must not be chosen.
    for product, name in (("wv", "viewdraw.exe"), ("wg", "ExpeditionPCB.exe")):
        directory = home / product / "win64" / "bin"
        directory.mkdir(parents=True)
        (directory / name).write_bytes(b"product")
    monkeypatch.setattr(adapter, "_configure_environment", lambda: home)
    return home


@pytest.mark.parametrize(
    ("domain", "executable"), [("schematic", "viewdraw.exe"), ("pcb", "ExpeditionPCB.exe")]
)
def test_every_domain_starts_through_the_common_launcher(sdd_home, domain, executable) -> None:
    resolved, working_directory = _native_executable(domain)
    assert resolved == sdd_home / "common" / "win64" / "bin" / executable
    assert working_directory == resolved.parent
    # Naming the product directly is the 0xC0000135 bug; guard both spellings.
    assert "wv" not in resolved.parts and "wg" not in resolved.parts


def test_a_missing_launcher_is_reported_with_its_path(sdd_home) -> None:
    (sdd_home / "common" / "win64" / "bin" / "viewdraw.exe").unlink()
    with pytest.raises(AdapterError) as caught:
        _native_executable("schematic")
    assert caught.value.code == "E_NOT_FOUND"
    assert caught.value.details["domain"] == "schematic"
    assert "viewdraw.exe" in caught.value.details["path"]
