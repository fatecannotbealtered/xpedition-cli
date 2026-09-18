"""Exercise the public CLI against stdole2.tlb; no application is activated."""
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
source = next((p for p in candidates if p.is_file()), None)
if source is None:
    raise SystemExit('Standard Windows OLE type library not found.')
output = Path('inventory-smoke')
output.mkdir(exist_ok=True)
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
summary = {'run_id': os.environ.get('GITHUB_RUN_ID'), 'source_sha256': before,
           'type_count': headers['total'], 'interface': selected['name'],
           'interface_members_observed': members['count'],
           'variable_type': variables[0]['name'], 'variables_observed': constants['count'],
           'status': 'passed', 'scope': 'Windows standard OLE metadata only',
           'xpedition_executed': False}
(output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
print(json.dumps(summary))
