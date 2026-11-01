"""Sync handler discovery and evidence hashes without changing driver policies."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from xpedition_cli.cli.registry import by_path, commands  # noqa: E402
from xpedition_cli.reference_data import SCHEMAS  # noqa: E402


def refresh_design(design):
    for item in design.get("evidence", []):
        if item.get("path") and (ROOT / item["path"]).is_file():
            item["sha256"] = hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest()
            if item.get("id") == "readme" and item["path"] == "README.md":
                item["source"] = "current isolated checkout; exact bytes bound by sha256"
                item["summary"] = (
                    "Current engineer entry and historical notes; this document is not native "
                    "acceptance evidence for the current candidate."
                )


def parameter(param):
    value = param.contract()
    if value["type"] not in {"string", "integer", "number", "boolean", "json", "path"}:
        value["type"] = "string"
    choices = value.pop("choices", None)
    if choices:
        value["enum"] = choices
    return value


def main():
    path = ROOT / "registry.json"
    registry = json.loads(path.read_text(encoding="utf-8"))
    handlers = by_path()
    affected = {
        "session start",
        "session attach",
        "session close",
        "session inspect",
        "project backup",
        "project inspect",
        "schematic export",
        "pcb outline set",
        "pcb silkscreen labels",
        "pcb view",
        "pcb snapshot",
        "pcb render",
        "pcb inspect",
        "pcb routing unroute",
        "pcb routing trace",
        "pcb routing auto",
        "pcb routing via",
        "pcb routing apply",
        "pcb copper pour",
        "pcb copper remove",
        "pcb checks run",
        "pcb diagnostics close",
    }
    for item in registry["commands"]:
        command = handlers[item["path"]]
        item["description"] = command.description
        item["examples"] = list(command.examples)
        if item["path"] in {"session start", "session attach"}:
            limit = (
                "Designer uses only a normally scoped, confirmed CLI-owned empty startup and "
                "authenticated host Application handoff; --project and existing-instance "
                "takeover at startup are unsupported. Subsequent calls require the same owner, "
                "scope, source and original SDK start/registry. Runtime Run and its disabled "
                "host-bootstrap experiment remain historical. Public editing acceptance and "
                "frozen packaging are recorded separately; no readiness gate is waived. "
                "Layout retains its existing gates and original unknowns remain unchanged."
            )
            previous = [
                value
                for value in item["driver"]["limits"]
                if not value.startswith(
                    (
                        "Explicit host-bootstrap identity is a fixed diagnostic "
                        "session effect only.",
                        "Host-bootstrap identity is disabled before scope/store/token",
                        "Designer start and attachment are disabled before scope/store/token",
                        "Designer uses only a normally scoped, confirmed CLI-owned empty startup",
                    )
                )
            ]
            item["driver"]["limits"] = list(dict.fromkeys([*previous, limit]))
        if item["path"] == "pcb routing auto":
            stale_limit = (
                "Safety disabled in this candidate; refuses before native attachment, routing, "
                "plane regeneration or saving."
            )
            item["driver"]["limits"] = [
                value
                for value in item["driver"]["limits"]
                if value != stale_limit
                and not value.startswith("Legacy auto refuses. Explicit bounded-native is ")
            ]
            limit = (
                "Legacy auto refuses. Explicit bounded-native is one "
                "two-/three-pad net or the exact six-pin GND set, "
                "with old target copper refused by default; explicit --continue-existing "
                "only binds six-pin GND, three old target traces, eleven board traces, three "
                "observed material groups and two opens. Every old trace UID/full raw path "
                "must remain exact and all old plus new target copper enters acceptance; "
                "one signal "
                "layer and one NoViaPass with no cleanup/fanout/unroute/via/plane regeneration. "
                "A closed source recovery, readable live dependencies and complete supported "
                "native observations are separately bound; same-owner pre-Go observations "
                "and readable file hashes must match. Checkpoint is observation only; atomic "
                "database freeze and opaque live icdb/cache versions are unproven. Actual "
                "new copper/contact/protection (three/six pins require the complete material graph "
                "including T junctions, all members and drilled voids) and strict "
                "save/reopen are independently verified. Configuration has no supported "
                "getter; neither complete effective-rule parsing nor DRC is established. "
                "Only the exact bound primary board, next native autoroute report and "
                "existing MVO warning log may change; no blanket log-directory exclusion. "
                "Unexpected file changes retain post-Go facts before Save refusal."
            )
            item["driver"]["limits"] = list(dict.fromkeys([*item["driver"]["limits"], limit]))
            general_limit = (
                "Explicit bounded-general requires a version1 plan binding one actual unique "
                "unconnected FromTo, six actual GND pins, complete material groups, supported "
                "native configuration GET observations and full source/dependency identity. "
                "One formal General Route35367 candidate may alter only predeclared old "
                "GND copper within full-material bounds. Configuration is not an output "
                "guarantee: any via, wrong layer, foreign/unlisted old-object change or "
                "incomplete graph stops Save/Close/Open. Expected2 merges only the selected "
                "two groups; expected1 requires all six pins and all target copper in one "
                "component. Strict BOOL persistence and fresh independent receipt/state "
                "verification remain required; no NoViaPass.Go or global settings PUT."
            )
            item["driver"]["limits"] = list(
                dict.fromkeys([*item["driver"]["limits"], general_limit])
            )
        if item["path"] in {"pcb routing via", "pcb routing apply"}:
            limit = (
                "Safety disabled in this candidate; refuses before native attachment, routing, "
                "plane regeneration or saving."
            )
            item["driver"]["limits"] = list(dict.fromkeys([*item["driver"]["limits"], limit]))
        if item["path"] == "pcb routing trace":
            limit = (
                "Preview only: production local-rule applicability and bounded generated-copper "
                "impact are unproved; can_apply=false, no dangerous override."
            )
            item["driver"]["limits"] = list(dict.fromkeys([*item["driver"]["limits"], limit]))
        if item["path"] == "pcb constraints widths":
            limit = (
                "Offline preview only: the complete class/assignment/three-field all-layer/"
                "Layout synchronization contract is unverified; can_apply=false. Direct "
                "adapter preview/apply refuses before COM attachment. A single constraint "
                "PUT cannot validate the whole command; see docs/CURRENT-CANDIDATE.md."
            )
            item["driver"]["limits"] = list(dict.fromkeys([*item["driver"]["limits"], limit]))
            item["driver"]["error_codes"] = sorted(
                set(item["driver"]["error_codes"]) | {"E_UNSUPPORTED", "E_CONFLICT", "E_NOT_FOUND"}
            )
        if item["path"] == "pcb checks result":
            item["driver"]["error_codes"] = sorted(
                set(item["driver"]["error_codes"]) | {"E_CONFLICT", "E_NOT_FOUND", "E_INTEGRITY"}
            )
            limit = (
                "--native-files verifies retained requested/output file hashes and parses "
                "historical bytes only; it never executes a checker, establishes native "
                "completion/freshness, matches live hazards or changes original receipts. "
                "PARTIAL_NET parsing does not establish a native hazard mapping."
            )
            item["driver"]["limits"] = list(dict.fromkeys([*item["driver"]["limits"], limit]))
        if item["path"] in affected:
            item["driver"]["error_codes"] = sorted(
                set(item["driver"]["error_codes"])
                | {
                    "E_FORBIDDEN",
                    "E_CONFLICT",
                    "E_NOT_FOUND",
                    "E_HUMAN_REQUIRED",
                    "E_BACKEND_UNAVAILABLE",
                    "E_NATIVE_FAILURE",
                    "E_OUTCOME_UNKNOWN",
                    "E_VERIFY_INCOMPLETE",
                    "E_VERIFY_FAILED",
                    "E_PARTIAL",
                    "E_TIMEOUT",
                    "E_UNSUPPORTED",
                }
            )
        if item["path"] in {
            "session close",
            "pcb view",
            "pcb copper pour",
            "pcb copper remove",
            "pcb routing unroute",
            "pcb routing trace",
            "pcb silkscreen labels",
        }:
            item["driver"]["effects"] = command.extra["effects"]
        reserved = {
            "dangerous",
            "dry-run",
            "confirm",
            "compact",
            "format",
            "fields",
            "help",
            "quiet",
            "request-id",
            "version",
        }
        item["params"] = [
            parameter(param) for param in command.params if param.name not in reserved
        ]
    for name in registry["schemas"].keys() & SCHEMAS.keys():
        registry["schemas"][name] = SCHEMAS[name]
    from xpedition_cli.library_parts import input_schema

    identifier = "urn:xpedition-cli:library-parts-input:1"
    registry.setdefault("json_schemas", {})[identifier] = {
        **input_schema(),
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": identifier,
        "title": "Contents of the library create --file input (not CLI flags)",
    }
    from xpedition_cli.board_general import input_schema as general_schema

    general_identifier = "urn:xpedition-cli:bounded-general-input:1"
    registry["json_schemas"][general_identifier] = {
        **general_schema(),
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": general_identifier,
        "title": "Contents of pcb routing auto --mode bounded-general --file (not CLI flags)",
    }
    next(c for c in registry["commands"] if c["path"] == "library create")["driver"][
        "request_schema"
    ] = identifier
    refresh_design(registry["design"])
    path.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\r\n"
    )
    design_path = ROOT / "development/current-design.json"
    design = json.loads(design_path.read_text(encoding="utf-8"))
    refresh_design(design)
    design_path.write_text(
        json.dumps(design, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    current_path = ROOT / "development-current-registry.json"
    current = json.loads(current_path.read_text(encoding="utf-8"))
    current["design"] = design
    drivers = {item["path"]: item.get("driver") for item in current["commands"]}
    for item in registry["commands"]:
        if item["path"] in affected | {"library create", "pcb checks result"}:
            drivers[item["path"]] = item["driver"]
        if item["path"] == "pcb constraints widths":
            drivers[item["path"]] = {
                **drivers.get(item["path"], {}),
                "limits": item["driver"]["limits"],
                "error_codes": item["driver"]["error_codes"],
            }
    current["json_schemas"] = registry.get("json_schemas", {})
    for name in ("close_diagnostic", "session_start"):
        current.setdefault("schemas", {})[name] = registry["schemas"][name]
    current["commands"] = [
        {
            **command.contract(),
            **({"driver": drivers[command.path]} if drivers.get(command.path) else {}),
        }
        for command in commands()
    ]
    current_path.write_text(
        json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    canonical_path = ROOT / "development-registry.json"
    canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
    canonical["commands"] = registry["commands"]
    canonical["json_schemas"] = registry.get("json_schemas", {})
    for name in ("close_diagnostic", "session_start"):
        canonical.setdefault("schemas", {})[name] = registry["schemas"][name]
    canonical["design"]["application"] = design["application"]
    refresh_design(canonical["design"])
    canonical_path.write_text(
        json.dumps(canonical, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    baseline_path = ROOT / "development/current-command-baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline["commands"] = [command.contract() for command in commands()]
    baseline_path.write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps({"custom_commands": len(registry["commands"]), "version_changed": False}))


if __name__ == "__main__":
    main()
