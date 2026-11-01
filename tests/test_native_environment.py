"""Existing Windows license settings must reach a newly launched CAD process."""

import os
import re
import sys
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from xpedition_cli import native_com_adapter as adapter


def configure(monkeypatch, tmp_path, registered, initial=None):
    home = tmp_path / "XENTP2604" / "SDD_HOME"
    home.mkdir(parents=True)
    environment = {"XPEDITION_MGC_WD": str(tmp_path / "work"), **(initial or {})}
    monkeypatch.setattr(
        adapter, "os", SimpleNamespace(name="nt", environ=environment, pathsep=os.pathsep)
    )
    monkeypatch.setattr(adapter, "_sdd_home", lambda: home)

    def query(key, name):
        if (key, name) not in registered:
            raise FileNotFoundError(name)
        value = registered[key, name]
        if isinstance(value, OSError):
            raise value
        return value if isinstance(value, tuple) else (value, 1)

    def expand(value):
        # Windows expands against the current process, not values merely read
        # from the registry. This reproduces a stale caller environment.
        return re.sub(r"%([^%]+)%", lambda match: environment.get(match[1], match[0]), value)

    registry = SimpleNamespace(
        HKEY_CURRENT_USER="user",
        HKEY_LOCAL_MACHINE="machine",
        REG_SZ=1,
        REG_EXPAND_SZ=2,
        OpenKey=lambda hive, _path: nullcontext(hive),
        QueryValueEx=query,
        ExpandEnvironmentStrings=expand,
    )
    monkeypatch.setitem(sys.modules, "winreg", registry)
    # This legacy fallback must not replace the user's configured source.
    (tmp_path / "LICENSE.DAT").write_text("synthetic fallback; not a real license")
    assert adapter._configure_environment() == home
    return environment


def test_existing_user_license_settings_reach_launcher(monkeypatch, tmp_path):
    environment = configure(
        monkeypatch,
        tmp_path,
        {
            ("user", "MGLS_LICENSE_FILE"): "28000@synthetic-user-host",
            ("user", "SALT_LICENSE_SERVER"): "29000@synthetic-user-host",
            ("machine", "SALT_LICENSE_SERVER"): "29000@synthetic-machine-host",
        },
    )
    assert environment["MGLS_LICENSE_FILE"] == "28000@synthetic-user-host"
    assert environment["SALT_LICENSE_SERVER"] == "29000@synthetic-user-host"


def test_machine_license_setting_is_used_when_user_setting_is_absent(monkeypatch, tmp_path):
    environment = configure(
        monkeypatch, tmp_path, {("machine", "SALT_LICENSE_SERVER"): "29000@synthetic-host"}
    )
    assert environment["SALT_LICENSE_SERVER"] == "29000@synthetic-host"


@pytest.mark.parametrize("configured", ["28000@explicit-process-host", ""])
def test_explicit_process_license_setting_is_preserved(monkeypatch, tmp_path, configured):
    environment = configure(
        monkeypatch,
        tmp_path,
        {("user", "MGLS_LICENSE_FILE"): "28000@synthetic-user-host"},
        {"MGLS_LICENSE_FILE": configured},
    )
    assert environment["MGLS_LICENSE_FILE"] == configured


def test_unreadable_registry_setting_is_not_exposed_or_invented(monkeypatch, tmp_path, capsys):
    environment = configure(
        monkeypatch,
        tmp_path,
        {("user", "SALT_LICENSE_SERVER"): PermissionError("synthetic private diagnostic")},
    )
    assert "SALT_LICENSE_SERVER" not in environment
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("dependency", ["LM_LICENSE_FILE", "SALT_LICENSE_SERVER"])
def test_expand_string_sees_license_setting_not_yet_loaded(monkeypatch, tmp_path, dependency):
    environment = configure(
        monkeypatch,
        tmp_path,
        {
            ("user", "MGLS_LICENSE_FILE"): (f"%{dependency}%", 2),
            ("user", dependency): "28000@synthetic-configured-host",
        },
    )
    assert environment["MGLS_LICENSE_FILE"] == "28000@synthetic-configured-host"


def test_expand_string_resolves_transitive_case_insensitive_references(monkeypatch, tmp_path):
    environment = configure(
        monkeypatch,
        tmp_path,
        {
            ("user", "MGLS_LICENSE_FILE"): ("%lm_license_file%", 2),
            ("user", "LM_LICENSE_FILE"): ("%salt_license_server%", 2),
            ("machine", "SALT_LICENSE_SERVER"): "28000@synthetic-configured-host",
        },
    )
    assert environment["MGLS_LICENSE_FILE"] == "28000@synthetic-configured-host"


def test_plain_registry_strings_remain_literal(monkeypatch, tmp_path):
    environment = configure(
        monkeypatch,
        tmp_path,
        {("user", "MGLS_LICENSE_FILE"): "%LM_LICENSE_FILE%"},
        {"LM_LICENSE_FILE": "28000@explicit-host"},
    )
    assert environment["MGLS_LICENSE_FILE"] == "%LM_LICENSE_FILE%"


@pytest.mark.parametrize("value", ["28000@explicit-host", ""])
def test_expand_string_respects_explicit_dependency_and_empty_value(monkeypatch, tmp_path, value):
    environment = configure(
        monkeypatch,
        tmp_path,
        {
            ("user", "MGLS_LICENSE_FILE"): ("%LM_LICENSE_FILE%", 2),
            ("user", "LM_LICENSE_FILE"): "28000@registry-host",
        },
        {"LM_LICENSE_FILE": value},
    )
    assert environment["MGLS_LICENSE_FILE"] == value
    assert environment["LM_LICENSE_FILE"] == value


def test_expand_string_can_use_existing_process_variables(monkeypatch, tmp_path):
    environment = configure(
        monkeypatch,
        tmp_path,
        {("user", "MGLS_LICENSE_FILE"): ("%license_root%/synthetic.lic", 2)},
        {"LICENSE_ROOT": "D:/synthetic"},
    )
    assert environment["MGLS_LICENSE_FILE"] == "D:/synthetic/synthetic.lic"


@pytest.mark.parametrize(
    ("registered", "reason"),
    [
        (
            {("user", "MGLS_LICENSE_FILE"): ("private-prefix/%PRIVATE_MISSING_NAME%", 2)},
            "unresolved_reference",
        ),
        ({("user", "MGLS_LICENSE_FILE"): ("%MGLS_LICENSE_FILE%", 2)}, "cyclic_reference"),
        (
            {
                ("user", "MGLS_LICENSE_FILE"): ("%LM_LICENSE_FILE%", 2),
                ("user", "LM_LICENSE_FILE"): ("%MGLS_LICENSE_FILE%", 2),
            },
            "cyclic_reference",
        ),
    ],
)
def test_unresolvable_license_references_stop_without_values_or_partial_environment(
    monkeypatch, tmp_path, registered, reason, capsys
):
    registered = {**registered, ("user", "SALT_LICENSE_SERVER"): "private-host-value"}
    with pytest.raises(adapter.AdapterError) as caught:
        configure(monkeypatch, tmp_path, registered)
    assert caught.value.code == "E_CONFIG"
    assert caught.value.details["reason"] == reason
    assert caught.value.details["setting"] in {"MGLS_LICENSE_FILE", "LM_LICENSE_FILE"}
    diagnostic = str(caught.value) + repr(caught.value.details)
    assert not any(
        secret in diagnostic
        for secret in ("private-prefix", "PRIVATE_MISSING_NAME", "private-host-value")
    )
    assert "MGLS_LICENSE_FILE" not in adapter.os.environ
    assert "SALT_LICENSE_SERVER" not in adapter.os.environ
    assert capsys.readouterr() == ("", "")
