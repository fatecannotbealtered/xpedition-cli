from pathlib import Path
import hashlib
import os

BRANCH = 'codex/readonly-api-inventory-20260918'
if os.environ.get('GITHUB_REF') != f'refs/heads/{BRANCH}':
    raise SystemExit('Isolated review branch only')
EXPECTED = {
    'xpedition_cli/main.py': 'e12923d4b33c7ec6a6b8dcb5e4239e5817a5e73b',
    'xpedition_cli/reference_data.py': 'a553cde9e07be0fa531ad9776679ad0121513e91',
    'CHANGELOG.md': 'de4e0cacf51a63edd19e6c5d6b51fbda6f0e14e2',
    'skills/xpedition-cli/SKILL.md': '515b8cff14f50af6d16ddc5862345c79b786751f',
}
for path, expected in EXPECTED.items():
    raw = Path(path).read_bytes()
    assert hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == expected, path

def replace(path, old, new):
    p = Path(path)
    text = p.read_text(encoding='utf-8')
    assert text.count(old) == 1, (path, old[:70])
    p.write_text(text.replace(old, new), encoding='utf-8')

replace('xpedition_cli/main.py', 'from .output import emit, failure, redact, success\n',
        'from .output import emit, failure, redact, success\nfrom . import api_inventory\n')
replace('xpedition_cli/main.py', '    return positionals, options\n',
        '    if tuple(positionals[:2]) == api_inventory.COMMAND:\n'
        '        api_inventory.validate_argv(argv)\n'
        '        options["fields"] = api_inventory.protected_fields(options.get("fields"))\n'
        '    return positionals, options\n')
replace('xpedition_cli/main.py', '    if command == ("context",):\n',
        '    if command == api_inventory.COMMAND:\n'
        '        return api_inventory.run(options)\n'
        '    if command == ("context",):\n')
replace('xpedition_cli/main.py', 'Commands:\n  context',
        'Commands:\n  system api-inventory           inspect a trusted standalone COM type library\n  context')
replace('xpedition_cli/reference_data.py', 'from .contract_gen import CODES\n',
        'from .contract_gen import CODES\n'
        'from .api_inventory_contract import OUTPUT_SCHEMA as API_OUTPUT_SCHEMA, command as api_command\n')
replace('xpedition_cli/reference_data.py', '    return result\n\n\ndef release_readiness',
        '    return result + [api_command()]\n\n\ndef release_readiness')
replace('xpedition_cli/reference_data.py', '        "schemas": SCHEMAS,',
        '        "schemas": {**SCHEMAS, "api_inventory": API_OUTPUT_SCHEMA},')
replace('xpedition_cli/reference_data.py', '"applies_to": ["exchange inspect", "exchange import"],',
        '"applies_to": ["exchange inspect", "exchange import", "system api-inventory"],')
replace('xpedition_cli/reference_data.py', '{"name": "name", "type": "string", "applies_to": ["project init"]},',
        '{"name": "name", "type": "string", "applies_to": ["project init", "system api-inventory"]},')
replace('xpedition_cli/reference_data.py',
        '"Every public command has a command-level test (107 of 107); the contract "',
        '"The original 107-command release recorded native evidence; added metadata "\n'
        '            "inventory does not validate target Xpedition behavior. The contract "')
replace('CHANGELOG.md', '## [Unreleased]\n', '''## [Unreleased]

### Added

- `system api-inventory` reads trusted standalone COM type-library metadata on
  Windows without application activation, method invocation or registration.
  Type headers and selected member pages report names, identities and raw type
  descriptors; they do not become callable CLI capabilities or inferred schemas.
- Exact type selection, bounded pages and descriptor depth, incomplete-read
  reporting, source hashes and safe projection. DLL/EXE/URL/UNC input, backend
  selection and write flags are rejected. Xpedition semantics remain untested.
- A Windows smoke workflow exercises the real pywin32 loader against Windows'
  standard OLE type library, without Xpedition. It is not a licensed-native test.
''')
replace('skills/xpedition-cli/SKILL.md', '## Agent Defaults\n', '''## Agent Defaults

For API investigation, check the installed runtime catalog first. When metadata
inventory is available, read `reference/api-inventory.md`; a type-library member
is not authorization or evidence that a CLI operation is safe or implemented.
''')
print('Integrated metadata inventory only; no native design or shared-contract changes.')
