"""Every test runs against its own configuration directory.

The CLI keeps its confirmation secret, consumed-token ledger, audit log, session
state and knowledge-base bindings in XPEDITION_CLI_CONFIG_DIR, by default
~/.xpedition-cli. A test that forgot to point it elsewhere wrote into the
developer's real one, so this points it at a fresh directory for every test; a
test that needs a particular directory still sets the variable itself.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _own_config_dir(tmp_path_factory, monkeypatch) -> None:
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path_factory.mktemp("config")))
