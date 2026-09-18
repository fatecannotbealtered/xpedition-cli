"""Source-pinned integration, restricted to one review branch. Removed after CI."""
from __future__ import annotations

import ast
import hashlib
import os
from pathlib import Path

BRANCH = "codex/selected-placement-tasks-20260918"
if os.environ.get("GITHUB_REF") != f"refs/heads/{BRANCH}":
    raise SystemExit("Wrong branch; refusing bootstrap")
EXPECTED = {
    "xpedition_cli/main.py": "e12923d4b33c7ec6a6b8dcb5e4239e5817a5e73b",
    "xpedition_cli/native_com_adapter.py": "e35ae25de290b4180feda4f8575f6cd8010d51cd",
    "xpedition_cli/backends/native_xpedition.py": "71a26efa38808b1ccd7025351c0a93d0cfd8b486",
    "xpedition_cli/reference_data.py": "a553cde9e07be0fa531ad9776679ad0121513e91",
    "CHANGELOG.md": "9daa034d049c103b555e8158602c639de5e90f83",
    "skills/xpedition-cli/SKILL.md": "b270593c925faed94eddba661b3f06c19e1992c8",
}
for name, expected in EXPECTED.items():
    raw = Path(name).read_bytes()
    actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if actual != expected:
        raise SystemExit(f"Changed source, refusing patch: {name} ({actual})")


def replace(text, old, new, count=1):
    if text.count(old) != count:
        raise SystemExit(f"Patch anchor changed: {old[:80]!r}")
    return text.replace(old, new)


path = Path("xpedition_cli/main.py")
s = path.read_text(encoding="utf-8")
s = replace(s, "    return positionals, options\n", '''    if positionals[:2] in (["pcb", "placement-plan"], ["pcb", "placement"]):
        from .placement_command import validate_cli

        validate_cli(argv, positionals)
    return positionals, options
''')
s = replace(s, '    if command == ("context",):\n', '''    if command in {("pcb", "placement-plan"), ("pcb", "placement")}:
        from .placement_command import dispatch_placement

        return dispatch_placement(positionals, options)
    if command == ("context",):
''')
s = replace(s, "  pcb info|components|footprints|nets|layers|stackup|tracks|vias|zones|keepouts|query\n", '''  pcb placement-plan             plan explicit local origin transforms from observations
  pcb placement                  preview/confirm selected native placements (native smoke missing)
  pcb info|components|footprints|nets|layers|stackup|tracks|vias|zones|keepouts|query
''')
path.write_text(s, encoding="utf-8")

path = Path("xpedition_cli/native_com_adapter.py")
s = path.read_text(encoding="utf-8")
s = replace(s, '        if method == "move_component":\n', '''        if method == "placement_batch":
            from .errors import CLIError
            from .native_placement import run as run_placement

            try:
                return run_placement(params, client)
            except CLIError as error:
                raise AdapterError(error.code, error.message, error.details) from error
        if method == "move_component":
''')
path.write_text(s, encoding="utf-8")

path = Path("xpedition_cli/backends/native_xpedition.py")
s = path.read_text(encoding="utf-8")
s = replace(s, '                "arrange_components",\n', '                "arrange_components",\n                "placement_batch",\n')
path.write_text(s, encoding="utf-8")

path = Path("xpedition_cli/reference_data.py")
s = path.read_text(encoding="utf-8")
s = replace(s, 'from .contract_gen import CODES\n', 'from .contract_gen import CODES\nfrom . import placement_contract\n')
s = replace(s, '\n\ndef _param(\n', '\n\nSCHEMAS.update(placement_contract.SCHEMAS)\n\ndef _param(\n')
# Locate the exact return in commands, not a similar return in another helper.
lines = s.splitlines(keepends=True)
function = next(n for n in ast.parse(s).body if isinstance(n, ast.FunctionDef) and n.name == "commands")
ret = function.body[-1]
assert isinstance(ret, ast.Return) and isinstance(ret.value, ast.Name) and ret.value.id == "result"
lines.insert(ret.lineno - 1, '    result.extend(placement_contract.commands())\n')
s = ''.join(lines)
for flag, kind in (("dry-run", "boolean"), ("confirm", "string")):
    old = f'''"name": "{flag}",
                "type": "{kind}",
                "applies_to": ["change apply", "change rollback", "schematic apply"],'''
    new = f'''"name": "{flag}",
                "type": "{kind}",
                "applies_to": ["change apply", "change rollback", "schematic apply", "pcb placement"],'''
    s = replace(s, old, new)
s = replace(s, '"applies_to": ["exchange inspect", "exchange import"],', '"applies_to": ["exchange inspect", "exchange import", "pcb placement-plan"],')
function = next(n for n in ast.parse(s).body if isinstance(n, ast.FunctionDef) and n.name == "release_readiness")
lines = s.splitlines(keepends=True)
lines[function.lineno - 1:function.end_lineno] = ['''def release_readiness() -> dict[str, Any]:
    return {
        "level": "beta",
        "fcc_required": True,
        "fcc_status": "verified",
        "mock_upstream_required": True,
        "mock_upstream_status": "verified",
        "live_smoke_required_for_stable": True,
        "live_smoke_status": "missing",
        "reason": (
            "Selected placement tasks have command-level and simulated native-object tests, "
            "but the new native placement path has no licensed smoke record. The older "
            "docs/E2E.md workflow evidence is retained and does not validate these additions. "
            "Run disposable-board top/bottom, protected-part, refusal, stale-preview, "
            "save/close/reopen and DRC checks before marking this implementation stable."
        ),
        "required_evidence": [
            "functional_contract_coverage_100",
            "mock_upstream_contract_tests",
            "recorded_live_smoke_for_stable",
        ],
    }
''']
s = ''.join(lines)
path.write_text(s, encoding="utf-8")

path = Path("CHANGELOG.md")
s = path.read_text(encoding="utf-8")
s = replace(s, '## [Unreleased]\n', '''## [Unreleased]

### Added

- Selected-origin placement tasks: offline `pcb placement-plan` and guarded native
  `pcb placement` for explicit translate, rotation about an origin, alignment to
  an anchor, and equal-origin-spacing distribution. Input JSON Schemas are exposed
  by reference; native identity, units, side and protection are read, not guessed.
- One preview/confirmation for a serial batch, per-item and final target read-back,
  native placement DRC enable/restore checks, stop-on-uncertainty results and one
  save after verified completion. No routing deletion or unselected placement is
  requested. This is not route repair, all-or-none rollback or a global write lock.

### Changed

- Runtime readiness is beta: prior live E2E records do not cover the new native
  placement path. Public interface references, synthetic tests and a native smoke
  checklist are recorded without claiming licensed execution or release readiness.
''')
path.write_text(s, encoding="utf-8")
path = Path("skills/xpedition-cli/SKILL.md")
s = path.read_text(encoding="utf-8")
s = replace(s, '## Agent Defaults\n', '''## Agent Defaults

For small local layout adjustments, use the selected-placement workflow only when
it is advertised by the installed binary's reference; read `reference/placement-tasks.md`.
Its native smoke status and partial-execution boundaries remain explicit.
''')
path.write_text(s, encoding="utf-8")
print("Integrated selected-placement tasks. No canonical spec, version or release changes.")
