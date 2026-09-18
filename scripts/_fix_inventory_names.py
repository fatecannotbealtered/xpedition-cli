from pathlib import Path
import hashlib
import os

assert os.environ['GITHUB_REF'] == 'refs/heads/codex/readonly-api-inventory-20260918'
p = Path('xpedition_cli/api_inventory.py')
s = p.read_text(encoding='utf-8')
# Windows git checkout may use CRLF; compare normalized source bytes.
assert hashlib.sha256(s.encode()).hexdigest() == 'c1906014c1fd377f66f02eef11c57300773ef951c1ba14a21ad59a1fe7e39a69'
for old in ('info.GetNames(desc.memid, count + 1)', 'info.GetNames(desc.memid, 1)'):
    assert s.count(old) == 1
    s = s.replace(old, 'info.GetNames(desc.memid)')
p.write_text(s, encoding='utf-8', newline='\n')
p = Path('tests/test_api_inventory.py')
s = p.read_text(encoding='utf-8')
old = '''    def GetNames(self, memid, count):
        return ("Value", "first", "second")[:count] if memid == 7 else ("Constant",)'''
assert s.count(old) == 1
s = s.replace(old, '''    def GetNames(self, memid):
        return ("Value", "first", "second") if memid == 7 else ("Constant",)''')
p.write_text(s, encoding='utf-8', newline='\n')
p = Path('docs/API_INVENTORY_DESIGN.md')
s = p.read_text(encoding='utf-8')
s += '''

The remaining metadata calls were checked against the published pywin32 binding.
GetNames accepts the member ID, not the native C++ output-buffer length argument.
Both production calls and the strict fake provider now use the one-argument API.
Reference: https://mhammond.github.io/pywin32/PyITypeInfo__GetNames_meth.html .
The Windows correction run validates the full suite and the actual standard OLE
fixture before recording API_INVENTORY_WINDOWS_VALIDATION.json. That is still not
an Xpedition installation or a call to any discovered application method.
'''
p.write_text(s, encoding='utf-8', newline='\n')
