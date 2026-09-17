"""One-shot, pinned-source patch application on the dedicated work branch only."""
from pathlib import Path
import hashlib
import os

BASE = "2a66f2dc52c95a7165c9cc675ed01b0c65d8e7b5"
BRANCH = "codex/agent-hardening-20260917"
if os.environ.get("GITHUB_REF") != f"refs/heads/{BRANCH}":
    raise SystemExit("This one-shot patch is restricted to its dedicated work branch")

EXPECTED = {
    "xpedition_cli/main.py": "e12923d4b33c7ec6a6b8dcb5e4239e5817a5e73b",
    "xpedition_cli/output.py": "7ab374208dc0547e92f2719e6e7fc86dd846c64d",
    "xpedition_cli/routing_plan.py": "d4d7d26b16c544601ace24582a01a7dcb6d4304c",
    "xpedition_cli/reference_data.py": "a553cde9e07be0fa531ad9776679ad0121513e91",
    "CHANGELOG.md": "de4e0cacf51a63edd19e6c5d6b51fbda6f0e14e2",
    "skills/xpedition-cli/SKILL.md": "515b8cff14f50af6d16ddc5862345c79b786751f",
}
for name, expected_sha in EXPECTED.items():
    raw = Path(name).read_bytes()
    actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if actual != expected_sha:
        raise SystemExit(f"Refusing to patch changed source: {name} ({actual})")

def replace(text, old, new, count=1):
    if text.count(old) != count:
        raise SystemExit(f"Patch anchor count changed ({text.count(old)}): {old[:100]!r}")
    return text.replace(old, new)

p = Path("xpedition_cli/main.py")
s = p.read_text(encoding="utf-8")
s = replace(s, 'from .output import emit, failure, redact, success\n',
            'from .output import emit, failure, redact, success\nfrom .native_verification import verify_native_changes\n')
s = replace(s, '''    if command == ("project", "init"):
        if options.get("dry_run") and options.get("confirm"):
''', '''    if command == ("project", "init"):
        backend_name = str(options.get("backend") or "mock")
        if backend_name not in {"mock", "native_xpedition"}:
            raise CLIError("E_VALIDATION", f"unsupported backend: {backend_name}")
        if backend_name == "native_xpedition" and not options.get("template"):
            raise CLIError(
                "E_USAGE", "native project init requires --template PATH; no MockBackend fallback",
                {"backend": backend_name, "required": ["template"]},
            )
        if options.get("dry_run") and options.get("confirm"):
''')
s = replace(s, '''            reread, _ = backend.load(str(path), domain=changeset_domain or "pcb")
            return {
''', '''            # A successful adapter call or snapshot does not prove the requested
            # mutation happened. Never offer automatic replay after a post-write
            # read-back failure: the design may already have changed.
            try:
                reread, _ = backend.load(str(path), domain=changeset_domain or "pcb")
            except CLIError as error:
                raise CLIError(
                    "E_PROJECT_INVALID", "native write returned, but read-back failed; inspect before retrying",
                    {"stage": "read_back", "write_attempted": True, "reread": False,
                     "cause": {"code": error.code, "details": error.details or {}},
                     "applied": native_result.get("applied", []),
                     "_untrusted": ["cause.details", "applied"]},
                ) from error
            verification = verify_native_changes(projected, reread, changeset["operations"])
            verification.update({"reread": True, "save_requested": True,
                                 "saved": native_result.get("saved")})
            if not verification["valid"]:
                raise CLIError(
                    "E_PROJECT_INVALID", "native postconditions did not verify; inspect before another write",
                    {"stage": "verify", "write_attempted": True,
                     "project": str(path), "verification": verification,
                     "applied": native_result.get("applied", []),
                     "_untrusted": ["project", "verification.issues", "applied"]},
                )
            return {
''')
s = replace(s, '''                "verification": {
                    "valid": True,
                    "saved": True,
                    "reread": True,
                    "component_count": len(reread["components"]),
                    "net_count": len(reread["nets"]),
                },
''', '''                "verification": verification,
''')
s = replace(s, '''                "(fix it, or pass --dangerous to let Layout judge)",''',
            '''                "(fix the plan before confirming)",''')
p.write_text(s, encoding="utf-8")

p = Path("xpedition_cli/output.py")
s = p.read_text(encoding="utf-8")
s = replace(s, 'from .errors import CLIError\n',
            'from .errors import CLIError\nfrom .field_projection import project_fields as _project_fields\n')
start = s.index('def project_fields(')
end = s.index('\n\ndef success(', start)
s = s[:start] + '''def project_fields(data: Any, fields: str | None) -> Any:
    return _project_fields(data, fields)
''' + s[end:]
p.write_text(s, encoding="utf-8")

p = Path("xpedition_cli/routing_plan.py")
s = p.read_text(encoding="utf-8")
s = replace(s, '''    segments = list(board.segments)
    vias = list(board.vias)
''', '''    segments = list(board.segments)
    vias = list(board.vias)
    first_new_segment = len(segments)
    first_new_via = len(vias)
''')
s = replace(s, '''        for a2, b2, layer2, net2, width2, index2 in segments[i + 1 :]:
''', '''        # Old-old pairs can never contribute a finding. Start at the first
        # new segment and index directly: no quadratic traversal or tail copies.
        for j in range(max(i + 1, first_new_segment), len(segments)):
            a2, b2, layer2, net2, width2, index2 = segments[j]
''')
s = replace(s, '''        for a, b, layer, net2, width, index2 in segments:
''', '''        segment_start = first_new_segment if index < 0 else 0
        for j in range(segment_start, len(segments)):
            a, b, layer, net2, width, index2 = segments[j]
''')
s = replace(s, '''        for p2, net2, index2 in vias:
''', '''        via_start = first_new_via if index < 0 else 0
        for j in range(via_start, len(vias)):
            p2, net2, index2 = vias[j]
''')
p.write_text(s, encoding="utf-8")

p = Path("xpedition_cli/reference_data.py")
s = p.read_text(encoding="utf-8")
s = replace(s, '"description": "comma-separated top-level or dotted paths in data",',
    '"description": "comma-separated data paths; arrays project per item (items.refdes or items[].refdes); pagination and _untrusted are retained",')
for flag, type_name in (("dry-run", "boolean"), ("confirm", "string")):
    old = f'''"name": "{flag}",
                "type": "{type_name}",
                "applies_to": ["change apply", "change rollback", "schematic apply"],'''
    new = f'''"name": "{flag}",
                "type": "{type_name}",
                "applies_to": [item["path"] for item in commands() if item["type"] == "write"],'''
    s = replace(s, old, new)
p.write_text(s, encoding="utf-8")

p = Path("CHANGELOG.md")
s = p.read_text(encoding="utf-8")
s = replace(s, '## [Unreleased]\n', '''## [Unreleased]

### Fixed

- Explicit NativeBackend project initialization without a template fails before
  preview, token consumption or file creation; it never creates a mock project.
- Native ChangeSet results verify the requested final coordinates, properties,
  part identities and connectivity against read-back instead of hardcoding
  verification success. Missing observations or unsupported verification fail
  closed; post-write read-back failures are non-retryable and report the stage.
  Read-back is not a durability or electrical-correctness claim.
- `--fields` projects records inside arrays without changing their order or
  cardinality. Paging controls and `_untrusted` annotations survive projection;
  parent/child selectors are order-independent. Missing fields retain legacy
  omission semantics; no post-write selector error is introduced.
- `reference` derives confirm/dry-run applicability from write-command metadata;
  trace validation no longer recommends the globally rejected `--dangerous` flag.

### Changed

- Routing-plan checks skip existing-existing segment and via pairs before
  iteration and avoid repeated tail-list copies. The geometric predicates and
  finding order are unchanged. Incremental edits no longer pay a quadratic
  existing-board pair scan; new-new comparisons still require further indexing.
''')
p.write_text(s, encoding="utf-8")

p = Path("skills/xpedition-cli/SKILL.md")
s = p.read_text(encoding="utf-8")
s = replace(s, '## Agent Defaults\n', '''## Agent Defaults

For query projection and native post-write verification, read
`reference/agent-hardening.md`. In particular, a failed post-write verification
is not permission to resend the write; inspect the observed state first.
''')
p.write_text(s, encoding="utf-8")
print("Applied pinned-source hardening patch; no .agent/ or canonical contract changes.")
