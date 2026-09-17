"""One-shot source-pinned diagnostic boundary patch for its review branch."""
import hashlib
import os
from pathlib import Path

BRANCH = "codex/diagnostic-backend-boundaries-20260917"
if os.environ.get("GITHUB_REF") != f"refs/heads/{BRANCH}":
    raise SystemExit("Wrong branch; no patch applied")
expected = {
    "xpedition_cli/main.py": "e12923d4b33c7ec6a6b8dcb5e4239e5817a5e73b",
    "CHANGELOG.md": "de4e0cacf51a63edd19e6c5d6b51fbda6f0e14e2",
    "skills/xpedition-cli/SKILL.md": "515b8cff14f50af6d16ddc5862345c79b786751f",
}
for name, sha in expected.items():
    raw = Path(name).read_bytes()
    actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if actual != sha:
        raise SystemExit(f"Refusing to patch changed source: {name}")

def replace(text, old, new):
    if text.count(old) != 1:
        raise SystemExit(f"Patch anchor changed: {old[:90]!r}")
    return text.replace(old, new)

p = Path("xpedition_cli/main.py")
s = p.read_text(encoding="utf-8")
s = replace(s, '''    if positionals[0] == "agent":
        verb = positionals[1] if len(positionals) > 1 else ""
        backend = _backend(options)
''', '''    if positionals[0] == "agent":
        verb = positionals[1] if len(positionals) > 1 else ""
        if verb == "capabilities":
            # Like the streaming capability method, discovery does not load a
            # project or require an already-working native session.
            return CapabilityRegistry().summary()
        backend = _backend(options)
''')
s = replace(s, '''        if verb == "capabilities":
            return CapabilityRegistry().summary()
        if verb == "serve":
''', '''        if verb == "serve":
''')
s = replace(s, '''    if positionals[0] == "analysis":
        backend = _backend(options)
''', '''    if positionals[0] == "analysis":
        if command == ("analysis", "run") and options.get("backend") == "native_xpedition":
            raise CLIError(
                "E_BACKEND_UNAVAILABLE",
                "analysis run is MockBackend-only; no native analysis was executed",
                {
                    "backend": "native_xpedition",
                    "supported_backends": ["mock"],
                    "native_entry_points": {"drc": "pcb drc", "review": "review run"},
                    "hint": "consult reference; these checks do not cover every analysis kind",
                },
            )
        backend = _backend(options)
''')
p.write_text(s, encoding="utf-8")
p = Path("CHANGELOG.md")
s = p.read_text(encoding="utf-8")
s = replace(s, "## [Unreleased]\n", '''## [Unreleased]

### Fixed

- `agent capabilities` no longer loads a project or requires the selected native
  backend to be operational. Capability discovery now matches its streaming
  counterpart and remains available when a design path is absent or malformed.
- `analysis run --backend native_xpedition` explicitly rejects the unsupported
  combination before native access instead of running Mock checks on a native
  snapshot. Native stored-result reads remain available. This intentionally
  tightens backend selection; it does not implement a native analysis engine.
''')
p.write_text(s, encoding="utf-8")
p = Path("skills/xpedition-cli/SKILL.md")
s = p.read_text(encoding="utf-8")
s = replace(s, "- Keep project paths and ChangeSet targets narrow and explicit.\n", '''- Keep project paths and ChangeSet targets narrow and explicit.
- Selecting a backend does not mean every command supports it. Treat declared
  unavailability as a capability boundary; never substitute mock analysis for
  an upstream check. Capability discovery does not need to open a design.
''')
p.write_text(s, encoding="utf-8")
print("Patched diagnostic boundaries; no native operations or release changes.")
