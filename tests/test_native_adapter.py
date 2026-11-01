from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from xpedition_cli.backends.native_xpedition import NativeBackend
from xpedition_cli.errors import CLIError


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep these tests off the caller's real session state.

    `invoke` reads the recorded session, and a timing-out call writes one. Without
    this the suite would clobber the session of whoever ran it, and a stale record
    left on the machine would decide the result of a test about timeouts.
    """
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))


def test_native_adapter_uses_argv_and_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    adapter = tmp_path / "native-adapter.exe"
    adapter.write_bytes(b"placeholder")
    monkeypatch.setenv("XPEDITION_NATIVE_COMMAND", str(adapter))
    seen: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            argv, 0, '{"ok":true,"schema_version":"1.0","data":{"ready":true}}', ""
        )

    monkeypatch.setattr("xpedition_cli.backends.native_xpedition.subprocess.run", fake_run)
    result = NativeBackend().invoke("health", {"project": "demo"})
    assert result == {"ready": True}
    assert seen["argv"] == [str(adapter.resolve())]
    kwargs = seen["kwargs"]
    assert isinstance(kwargs, dict)
    assert "shell" not in kwargs
    assert json.loads(str(kwargs["input"])) == {"method": "health", "params": {"project": "demo"}}


def test_trace_advisory_keeps_success_and_does_not_retry_or_echo_other_stderr(
    tmp_path, monkeypatch, capsys
):
    from xpedition_cli.errors import TRACE_RETURNED_WARNING

    executable = tmp_path / "adapter.exe"
    executable.write_bytes(b"fixture")
    monkeypatch.setenv("XPEDITION_NATIVE_COMMAND", str(executable))
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv,
            0,
            '{"ok":true,"data":{"saved":true}}',
            "private native detail\n" + TRACE_RETURNED_WARNING + "\n",
        )

    monkeypatch.setattr("xpedition_cli.backends.native_xpedition.subprocess.run", run)
    assert NativeBackend().invoke("save") == {"saved": True}
    assert len(calls) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == TRACE_RETURNED_WARNING + "\n"


def test_frozen_binary_prefers_its_embedded_adapter_over_an_old_path_adapter(tmp_path, monkeypatch):
    from xpedition_cli.backends import native_xpedition

    executable = tmp_path / "candidate.exe"
    executable.write_bytes(b"offline executable fixture")
    monkeypatch.delenv("XPEDITION_NATIVE_COMMAND", raising=False)
    monkeypatch.setattr(native_xpedition.sys, "frozen", True, raising=False)
    monkeypatch.setattr(native_xpedition.sys, "executable", str(executable))
    monkeypatch.setattr(native_xpedition.shutil, "which", lambda name: "old-adapter.exe")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, '{"ok":true,"data":{"ready":true}}', "")

    monkeypatch.setattr(native_xpedition.subprocess, "run", run)
    assert NativeBackend().invoke("health")["ready"]
    assert calls == [[str(executable), "--internal-native-adapter"]]


def test_native_adapter_rejects_non_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    adapter = tmp_path / "native-adapter.exe"
    adapter.write_bytes(b"placeholder")
    monkeypatch.setenv("XPEDITION_NATIVE_COMMAND", str(adapter))
    completed = subprocess.CompletedProcess([str(adapter)], 0, "banner\n{}", "")
    monkeypatch.setattr(
        "xpedition_cli.backends.native_xpedition.subprocess.run", lambda *args, **kwargs: completed
    )
    with pytest.raises(CLIError) as raised:
        NativeBackend().invoke("snapshot")
    assert raised.value.code == "E_SERVER"


def test_native_adapter_timeout_is_reported_as_retryable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An adapter that never answers becomes `E_TIMEOUT` (exit 8, retryable), the one
    declared error code that no test reached before."""
    adapter = tmp_path / "native-adapter.exe"
    adapter.write_bytes(b"placeholder")
    monkeypatch.setenv("XPEDITION_NATIVE_COMMAND", str(adapter))

    def never_answers(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, float(kwargs.get("timeout") or 1.0))

    monkeypatch.setattr("xpedition_cli.backends.native_xpedition.subprocess.run", never_answers)
    with pytest.raises(CLIError) as raised:
        NativeBackend().invoke("snapshot", {}, timeout_seconds=0.01)
    assert raised.value.code == "E_TIMEOUT"
    assert raised.value.details["method"] == "snapshot"
    # The call was killed mid-operation, so the session it was driving is suspect.
    assert "inspect the operation" in raised.value.details["hint"]
    assert "reconcile" in raised.value.details["hint"]
