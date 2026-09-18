from pathlib import Path
import hashlib
import os

BRANCH = "codex/pin-assignment-workflow-20260918"
if os.environ.get("GITHUB_REF") != f"refs/heads/{BRANCH}":
    raise SystemExit("Isolated review branch only")
EXPECTED = {
    "xpedition_cli/main.py": "e12923d4b33c7ec6a6b8dcb5e4239e5817a5e73b",
    "xpedition_cli/reference_data.py": "a553cde9e07be0fa531ad9776679ad0121513e91",
    "CHANGELOG.md": "de4e0cacf51a63edd19e6c5d6b51fbda6f0e14e2",
    "skills/xpedition-cli/SKILL.md": "515b8cff14f50af6d16ddc5862345c79b786751f",
}
for path, expected in EXPECTED.items():
    raw = Path(path).read_bytes()
    if hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() != expected:
        raise SystemExit(f"Source changed: {path}")

def replace(path, old, new):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"Anchor is not unique: {path}: {old[:50]}")
    p.write_text(text.replace(old, new), encoding="utf-8")

replace("xpedition_cli/main.py", "from .output import emit, failure, redact, success\n", "from .output import emit, failure, redact, success\nfrom . import pin_assignment\n")
replace("xpedition_cli/main.py", "    return positionals, options\n", "    if tuple(positionals[:2]) in pin_assignment.COMMANDS:\n        pin_assignment.validate_argv(argv)\n        options['fields'] = pin_assignment.protected_fields(options.get('fields'))\n    return positionals, options\n")
replace("xpedition_cli/main.py", '    if command == ("context",):\n', '    if command in pin_assignment.COMMANDS:\n        return pin_assignment.run(tuple(positionals), options)\n    if command == ("context",):\n')
replace("xpedition_cli/main.py", 'Commands:\n  context', 'Commands:\n  schematic pin-plan|pin-check   plan/check CSV pin assignments against saved snapshots only\n  context')
replace("xpedition_cli/reference_data.py", 'from .contract_gen import CODES\n', 'from .contract_gen import CODES\nfrom .pin_assignment_contract import OUTPUT_SCHEMA as PIN_OUTPUT_SCHEMA, commands as pin_commands\n')
replace("xpedition_cli/reference_data.py", '    return result\n\n\ndef release_readiness', '    return result + pin_commands()\n\n\ndef release_readiness')
replace("xpedition_cli/reference_data.py", '        "schemas": SCHEMAS,', '        "schemas": {**SCHEMAS, "pin_assignment": PIN_OUTPUT_SCHEMA},')
replace("xpedition_cli/reference_data.py", '"applies_to": ["exchange inspect", "exchange import"],', '"applies_to": ["exchange inspect", "exchange import", "schematic pin-plan", "schematic pin-check"],')
replace("xpedition_cli/reference_data.py", '"Every public command has a command-level test (107 of 107); the contract "', '"The original 107-command release recorded command-level and native evidence; "\n            "additional offline pin workflows do not extend that native evidence. The contract "')
replace("CHANGELOG.md", "## [Unreleased]\n", """## [Unreleased]

### Added

- Read-only `schematic pin-plan` and `schematic pin-check` compare exact CSV
  pin/net assignments with supplied snapshots. Plans identify noop/connect/reassign
  and shared-net review needs. Missing or conflicting observations never count as
  verified. No native calls, automatic ChangeSets, write tokens or modifications.
- Bounded pages and peer samples, full-input assessment before paging, file hashes,
  strict CSV/JSON validation and machine-readable input/observation contracts.
  Source freshness and live electrical correctness are explicitly unverified.
""")
replace("skills/xpedition-cli/SKILL.md", "## Agent Defaults\n", """## Agent Defaults

For pin-assignment planning, discover the installed binary's capabilities first.
When it exposes the offline pin workflows, read `reference/pin-assignment.md`.
A supplied snapshot comparison is not a live read-back or authorization to write.
""")
