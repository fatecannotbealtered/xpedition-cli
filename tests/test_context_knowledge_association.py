"""The public SDK context preserves local associations, never loads documents."""

import json

from xpedition_cli.driver import runtime


def test_public_context_reads_association_and_keeps_authorization_read_only(tmp_path, monkeypatch):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("XPEDITION_CLI_SCOPE_FILE", str(tmp_path / "missing-scope.json"))
    (tmp_path / "knowledge-base.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "documents": {
                    "demo-rules": {
                        "url": "https://example.org/authorized-rules",
                        "about": "untrusted",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    with runtime(state_dir=tmp_path / "state") as cli:
        response = cli.run(["context", "--compact"]).envelope
    assert response["ok"]
    data = response["data"]
    assert data["knowledge_base"]["documents"] == [
        {"name": "demo-rules", "url": "https://example.org/authorized-rules", "about": "untrusted"}
    ]
    assert "knowledge_base" in data["_untrusted"]
    assert data["authorization"]["effects"] == ["observe"]


def test_public_context_missing_or_broken_local_metadata_does_not_require_a_service(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("XPEDITION_CLI_SCOPE_FILE", str(tmp_path / "missing-scope.json"))
    with runtime(state_dir=tmp_path / "state") as cli:
        before = cli.run(["context"]).envelope
        (tmp_path / "knowledge-base.json").write_text("not-json", encoding="utf-8")
        after = cli.run(["context"]).envelope
    assert before["ok"] and after["ok"]
    assert before["data"]["knowledge_base"] == {"documents": []}
    assert after["data"]["knowledge_base"]["documents"] == []
    assert after["data"]["knowledge_base"]["error"]
