from pathlib import Path
import hashlib
import os

assert os.environ['GITHUB_REF'] == 'refs/heads/codex/readonly-api-inventory-20260918'
p = Path('xpedition_cli/api_inventory.py')
assert hashlib.sha256(p.read_bytes()).hexdigest() == '921db8ee335413b79908e27035455fb4904be86807cd5c6d7e8cc68325532864'
s = p.read_text(encoding='utf-8')
old = '        library = com.LoadTypeLibEx(str(path), com.REGKIND_NONE)'
assert s.count(old) == 1
s = s.replace(old, '        library = _load_metadata(com, path)')
anchor = 'def _pythoncom() -> Any:\n'
assert s.count(anchor) == 1
s = s.replace(anchor, '''def _load_metadata(com: Any, path: Path) -> Any:
    # pywin32 exposes LoadTypeLib, not LoadTypeLibEx. Microsoft documents that
    # LoadTypeLib does NOT register a type library when a path is supplied.
    # Keep this absolute-path boundary even if a caller bypasses run().
    if not path.is_absolute():
        raise CLIError("E_USAGE", "metadata loading requires an absolute file path")
    return com.LoadTypeLib(str(path))


''' + anchor)
p.write_text(s, encoding='utf-8')
p = Path('tests/test_api_inventory.py')
s = p.read_text(encoding='utf-8')
s = s.replace('from types import SimpleNamespace as NS', 'from pathlib import Path\nfrom types import SimpleNamespace as NS')
s = s.replace('LoadTypeLibEx', 'LoadTypeLib')
old = '    def LoadTypeLib(self, path, flags):\n        assert flags == self.REGKIND_NONE'
assert s.count(old) == 1
s = s.replace(old, '    def LoadTypeLib(self, path):\n        assert Path(path).is_absolute()')
s = s.replace('    REGKIND_NONE = 2\n', '')
s += '''

def test_metadata_loader_never_uses_filename_only_registration_semantics():
    com = Com()
    with pytest.raises(CLIError) as failure:
        inventory._load_metadata(com, Path("relative.tlb"))
    assert failure.value.code == "E_USAGE"
    assert com.calls == []
'''
p.write_text(s, encoding='utf-8')
p = Path('skills/xpedition-cli/reference/api-inventory.md')
s = p.read_text(encoding='utf-8')
old = 'LoadTypeLibEx with REGKIND_NONE, not Dispatch, GetActiveObject, makepy, registration\nor method invocation.'
assert old in s
s = s.replace(old, 'the pywin32 LoadTypeLib binding with a resolved absolute path. Microsoft documents\nthat supplying the path disables its legacy registration behavior. No filename-only\nlookup, Dispatch, GetActiveObject, makepy or method invocation is used.')
p.write_text(s, encoding='utf-8')
p = Path('docs/API_INVENTORY_DESIGN.md')
s = p.read_text(encoding='utf-8')
old = '''- [Microsoft LoadTypeLibEx](https://learn.microsoft.com/en-us/windows/win32/api/oleauto/nf-oleauto-loadtypelibex):
  REGKIND_NONE disables the registration process. The loader is supplied an explicit
  standalone local type library; no ProgID or application activation fallback.'''
assert old in s
s = s.replace(old, '''- [Microsoft LoadTypeLib](https://learn.microsoft.com/en-us/windows/win32/api/oleauto/nf-oleauto-loadtypelib):
  supplying a path disables the legacy automatic-registration behavior. pywin32
  exposes LoadTypeLib, not LoadTypeLibEx; the code explicitly requires the resolved
  absolute path. No filename-only lookup, ProgID or activation fallback is used.''')
s += '''

## Real-binding correction

The first Windows smoke revealed that the system stdole2.tlb is a PE resource
container. The smoke harness now extracts its sole TYPELIB as a data/image resource
without executable initialization; CLI input restrictions remain unchanged.
The next run reached a binding error. Upstream PythonCOM.cpp confirms pywin32
exports LoadTypeLib, not the initially assumed LoadTypeLibEx. The implementation
and fake provider now use the actual one-argument binding with an absolute-path
guard. Microsoft explicitly documents non-registration for a supplied path.
No permissive fallback or ctypes COM-pointer wrapper was introduced.

The initial 256-test validation record is historical and did not establish real
binding compatibility. API_INVENTORY_BINDING_VALIDATION.json records the corrected
suite; the independent Windows smoke must still pass before claiming that binding
is tested. System OLE metadata remains distinct from target Xpedition evidence.
'''
p.write_text(s, encoding='utf-8')
