"""Exercise the CLI with stdole2 metadata; never activate an application.

Some Windows images package stdole2.tlb in a PE resource container. In that
case only this test harness extracts a standalone TYPELIB resource, using
non-executable data/resource mapping. The CLI still rejects PE containers.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

if sys.platform != 'win32':
    raise SystemExit('This smoke requires Windows; it must not silently skip.')
root = Path(os.environ['SystemRoot'])
candidates = [root / 'System32' / 'stdole2.tlb', root / 'SysWOW64' / 'stdole2.tlb']
original = next((p for p in candidates if p.is_file()), None)
if original is None:
    raise SystemExit('Standard Windows OLE type library not found.')
output = Path('inventory-smoke')
output.mkdir(exist_ok=True)
raw = original.read_bytes()
original_hash = hashlib.sha256(raw).hexdigest()
print(json.dumps({'system_file_header': raw[:16].hex(), 'system_file_sha256': original_hash}))
source = original
extracted = False
if raw[:2] == b'MZ':
    import win32api

    # Microsoft resource-only loading guidance: DATAFILE_EXCLUSIVE | IMAGE_RESOURCE.
    # No imports, DllMain, GetProcAddress or application factory invocation.
    module = win32api.LoadLibraryEx(str(original), 0, 0x40 | 0x20)
    try:
        assert int(module) & 3, 'Module was not mapped as data/resource.'
        names = win32api.EnumResourceNames(module, 'TYPELIB')
        assert len(names) == 1, 'Do not guess between multiple TYPELIB resources.'
        resource = bytes(win32api.LoadResource(module, 'TYPELIB', names[0]))
    finally:
        win32api.FreeLibrary(module)
    assert resource[:4] in {b'MSFT', b'SLTG'}, 'Resource is not a standalone type library.'
    source = (output / 'standard-ole-resource.tlb').resolve()
    source.write_bytes(resource)  # Temporary runner fixture; not uploaded or committed.
    extracted = True
else:
    assert raw[:4] in {b'MSFT', b'SLTG'}, 'Unsupported system type-library container.'
before = hashlib.sha256(source.read_bytes()).hexdigest()

def query(label, *args):
    result = subprocess.run(
        [sys.executable, '-m', 'xpedition_cli.main', 'system', 'api-inventory',
         '--input', str(source), '--compact', '--limit', '200', *args],
        capture_output=True, text=True, encoding='utf-8', timeout=45, check=False,
    )
    body = json.loads(result.stdout)
    (output / (label + '.json')).write_text(json.dumps(body, indent=2), encoding='utf-8')
    assert result.returncode == 0 and body['ok'], body
    data = body['data']
    assert data['complete_in_scope'] and data['issue_count'] == 0, data
    assert all(value is False for value in data['execution'].values())
    assert data['semantic_validation'] == 'not_performed'
    assert data['source']['sha256'] == before
    return data

headers = query('type-headers')
assert headers['total'] > 0 and not headers['has_more']
interfaces = [r for r in headers['items'] if r['functions'] > 0 and r['kind'] in {3, 4}]
assert interfaces, 'No interface members exercised.'
selected = next((r for r in interfaces if r['name'] == 'IFont'), interfaces[0])
members = query('interface-members', '--name', selected['name'])
assert members['items'] and any('member_id' in r for r in members['items'])
variables = [r for r in headers['items'] if r['variables'] > 0]
assert variables, 'No variable descriptors exercised.'
constants = query('variable-members', '--name', variables[0]['name'])
assert any(r['kind'] == 'variable' for r in constants['items'])
assert hashlib.sha256(source.read_bytes()).hexdigest() == before
assert hashlib.sha256(original.read_bytes()).hexdigest() == original_hash
summary = {'run_id': os.environ.get('GITHUB_RUN_ID'), 'source_sha256': before,
           'system_file_sha256': original_hash, 'resource_only_fixture_extraction': extracted,
           'type_count': headers['total'], 'interface': selected['name'],
           'interface_members_observed': members['count'],
           'variable_type': variables[0]['name'], 'variables_observed': constants['count'],
           'status': 'passed', 'scope': 'Windows standard OLE metadata only',
           'xpedition_executed': False}
(output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
print(json.dumps(summary))
