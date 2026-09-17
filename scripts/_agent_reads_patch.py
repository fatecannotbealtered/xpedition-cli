"""One-shot application of source-hash-pinned edits on a dedicated review branch."""
from pathlib import Path
import hashlib
import os

BRANCH = "codex/scoped-reference-bounded-reads-20260917"
if os.environ.get("GITHUB_REF") != f"refs/heads/{BRANCH}":
    raise SystemExit("This patch is restricted to its dedicated review branch")
expected = {
    "xpedition_cli/main.py": "e12923d4b33c7ec6a6b8dcb5e4239e5817a5e73b",
    "xpedition_cli/reference_data.py": "a553cde9e07be0fa531ad9776679ad0121513e91",
    "CHANGELOG.md": "de4e0cacf51a63edd19e6c5d6b51fbda6f0e14e2",
    "skills/xpedition-cli/SKILL.md": "515b8cff14f50af6d16ddc5862345c79b786751f",
}
for name, sha in expected.items():
    raw = Path(name).read_bytes()
    actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if actual != sha:
        raise SystemExit(f"Source changed; refusing to patch {name}: {actual}")

def replace(text, old, new, count=1):
    if text.count(old) != count:
        raise SystemExit(f"Unexpected patch anchor count: {text.count(old)}: {old[:100]!r}")
    return text.replace(old, new)

p = Path("xpedition_cli/main.py")
s = p.read_text(encoding="utf-8")
s = replace(s, "from pathlib import Path\n", "from collections.abc import Iterable\nfrom pathlib import Path\n")
s = replace(s, "from .reference_data import reference, release_readiness\n",
            "from .reference_data import reference, release_readiness\n"
            "from .reference_query import SELECTOR_FLAGS, validate_reference_options\n"
            "from .query_page import query_page\n")
s = replace(s, 'VALUE_FLAGS = {\n', 'VALUE_FLAGS = {\n    *SELECTOR_FLAGS,\n')
s = replace(s, '            key = name[2:].replace("-", "_")\n', '''            key = name[2:].replace("-", "_")
            if name in SELECTOR_FLAGS:
                if key in options:
                    raise CLIError("E_USAGE", f"option {name} may only be supplied once")
                if not separator and str(value).startswith("--"):
                    raise CLIError("E_USAGE", f"option {name} requires a value")
''')
s = replace(s, '    return positionals, options\n',
            '    validate_reference_options(positionals, options)\n    return positionals, options\n')
s = replace(s, 'def dispatch(positionals: list[str], options: dict[str, Any]) -> dict[str, Any]:\n',
            'def dispatch(positionals: list[str], options: dict[str, Any]) -> dict[str, Any]:\n'
            '    validate_reference_options(positionals, options)\n')
s = replace(s, '''    if command == ("reference",):
        return reference()
''', '''    if command == ("reference",):
        return reference(command=options.get("command"), domain=options.get("domain"),
                         schema=options.get("schema"))
''')
start = s.index('def _list_data(')
end = s.index('\n\ndef _schematic_pins(', start)
s = s[:start] + '''def _list_data(
    project: dict[str, Any],
    items: Iterable[Any],
    options: dict[str, Any],
    untrusted: list[str] | None = None,
) -> dict[str, Any]:
    page = query_page(items, query=options.get("query"), limit=options.get("limit"),
                      offset=int(options.get("offset") or 0))
    return {
        "project": project["project"],
        "revision": project["revision"],
        **page,
        "_untrusted": untrusted or ["project", "items"],
    }
''' + s[end:]
start = s.index('def _agent_query(')
end = s.index('\n\ndef _agent_request(', start)
s = s[:start] + '''def _agent_query(project: dict[str, Any], query: str | None) -> Iterable[dict[str, Any]]:
    if not query:
        raise CLIError("E_USAGE", "agent query requires --query")
    items = (
        {"kind": kind, "value": item}
        for kind, key in (("component", "components"), ("net", "nets"),
                          ("connection", "connections"))
        for item in project[key]
    )
    return (item for item in items if _query_matches(item, query))
''' + s[end:]
s = replace(s, 'return _list_data(project, _agent_query(project, options.get("query")), options)',
            'return _list_data(project, _agent_query(project, options.get("query")),\n'
            '                              {**options, "query": None})')
s = replace(s, 'return _list_data(project, _agent_query(project, query_options.get("query")), query_options)',
            'return _list_data(project, _agent_query(project, query_options.get("query")),\n'
            '                          {**query_options, "query": None})')
s = replace(s, '''            all_items = (
                [{"kind": "component", "value": item} for item in project["components"]]
                + [{"kind": "net", "value": item} for item in project["nets"]]
                + [{"kind": "connection", "value": item} for item in project["connections"]]
            )
''', '''            all_items = (
                {"kind": kind, "value": item}
                for kind, key in (("component", "components"), ("net", "nets"),
                                  ("connection", "connections"))
                for item in project[key]
            )
''')
s = replace(s, '''            all_items = []
            for kind in sorted(pcb_keys):
                all_items.extend({"kind": kind, "value": item} for item in pcb[kind])
''', '''            all_items = ({"kind": kind, "value": item}
                         for kind in sorted(pcb_keys) for item in pcb[kind])
''')
s = replace(s, '''            items = []
            for kind in sorted(keys):
                items.extend({"kind": kind, "value": item} for item in project["library"][kind])
            matches = [item for item in items if _query_matches(item, query)]
            result = _list_data(project, matches, options, ["project", "items", "query"])
''', '''            items = ({"kind": kind, "value": item}
                     for kind in sorted(keys) for item in project["library"][kind])
            result = _list_data(project, items, options, ["project", "items", "query"])
''')
s = replace(s, '  --backup                --rules PATH      --limit N  --since VERSION\n',
            '  --backup                --rules PATH      --limit N  --since VERSION\n'
            '\nReference selectors (choose one):\n'
            '  reference --command "pcb trace" | --domain pcb | --schema context\n')
p.write_text(s, encoding="utf-8")

p = Path("xpedition_cli/reference_data.py")
s = p.read_text(encoding="utf-8")
s = replace(s, 'from .contract_gen import CODES\n',
            'from .contract_gen import CODES\nfrom .reference_query import select_reference, selector_params\n')
s = replace(s, '''            ["xpedition-cli reference --compact"],
        ),
''', '''            ["xpedition-cli reference --compact",
             'xpedition-cli reference --command "pcb trace" --compact',
             "xpedition-cli reference --domain pcb --compact",
             "xpedition-cli reference --schema context --compact"],
            params=selector_params(),
        ),
''')
s = replace(s, '            "error_codes",\n', '            "error_codes",\n            "selection",\n')
s = replace(s, '''            "selection",
        ],
        "untrusted_fields": [],
''', '''            "selection",
        ],
        "optional_fields": ["selection"],
        "untrusted_fields": [],
''')
s = replace(s, 'def reference() -> dict[str, Any]:\n', 'def _full_reference() -> dict[str, Any]:\n')
s += '''

def reference(
    *, command: str | None = None, domain: str | None = None, schema: str | None = None
) -> dict[str, Any]:
    return select_reference(_full_reference(), command=command, domain=domain, schema=schema)
'''
p.write_text(s, encoding="utf-8")

p = Path("CHANGELOG.md")
s = p.read_text(encoding="utf-8")
s = replace(s, '## [Unreleased]\n', '''## [Unreleased]

### Added

- Scoped reference discovery by exact command, top-level domain or existing
  output-schema name. A command slice retains its success and dry-run schemas,
  error tables and permission metadata; the full catalog remains the default.
  Selectors are declared from one source and rejected on unrelated commands.

### Changed

- Bounded local query pages stop after one matching lookahead record rather than
  filtering every record. Unfiltered sequence reads slice directly; agent and
  library queries no longer re-filter a materialized result. Matching and paging
  semantics remain compatible, including zero limits and clamped offsets.
  This is not native query pushdown or a COM-session optimization.
''')
p.write_text(s, encoding="utf-8")

p = Path("skills/xpedition-cli/SKILL.md")
s = p.read_text(encoding="utf-8")
s = replace(s, 'failure. Use `--compact` and `--fields` to keep agent context small.\n',
            'failure. Use `--compact` and `--fields` to keep agent context small.\n\n'
            'Discover the reference command\'s own selectors in its live parameter list.\n'
            'When supported by the installed binary, request only the needed command\n'
            'or domain and its schemas instead of reloading the entire catalog. An\n'
            'unknown selector is an argument to fix, not an unavailable native backend.\n')
s = replace(s, '## Read recipes\n', '''## Read recipes

Use a positive page limit for exploratory reads and follow the returned next-page
marker only when more records are needed. Result counts describe the current
page, not the whole design. A small local page does not prove that Xpedition read
only that many objects; do not interpret it as a native-query performance claim.
''')
p.write_text(s, encoding="utf-8")
print("Applied scoped discovery and bounded reads; native write paths are unchanged.")
