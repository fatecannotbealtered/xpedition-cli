"""Command registration (DCLI-029): the command model, its checks and the derived tiers.

A domain command is registered as a dict with the contract's registration fields plus the
fields ``reference`` shows: ``output_schema``, ``examples``, ``maturity`` and ``evidence``,
and optionally ``departures``, ``formats`` and ``output_coordinate_system``. The runtime
checks every registration and refuses to run a tool whose registration breaks a rule. The
confirmation tier and the risk tier are derived here, never taken from the author
(DCLI-051, DCLI-052, DSEC-001, DSEC-003).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .contract import Contract

PATH = re.compile(r"^[a-z][a-z0-9-]*( [a-z][a-z0-9-]*)*$")
FLAG = re.compile(r"^--[a-z][a-z0-9-]*$")
DOMAIN_TYPES = {"string", "integer", "number", "boolean", "quantity", "path"}
# Shared flags keep one meaning in every tool (DCLI-026); a command that offers one uses this type.
SHARED_TYPES = {
    "--limit": "integer", "--offset": "integer", "--query": "string", "--sort": "string",
    "--order": "string", "--state": "string", "--ids": "list", "--continue-on-error": "boolean",
    "--wait": "duration", "--output": "path", "--input": "input",
}
WRITE_CAPABILITIES = ("preview", "apply", "read_back")


class RegistrationError(Exception):
    """A registration breaks a rule of DCLI-029."""


@dataclass
class Flag:
    name: str
    type: str
    summary: str = ""
    required: bool = False
    quantity: str | None = None
    secret: bool = False
    output: bool = False
    coordinate_system: str | None = None
    choices: tuple[str, ...] | None = None  # runtime commands only
    maximum: int | None = None  # runtime commands only
    default: Any = None  # runtime commands only
    runtime: bool = False  # added by the runtime, never an input of the adapter

    @property
    def key(self) -> str:
        return self.name[2:].replace("-", "_")

    def registration(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "type": self.type}
        if self.summary:
            out["summary"] = self.summary
        for name in ("required", "secret", "output"):
            if getattr(self, name):
                out[name] = True
        if self.quantity:
            out["quantity"] = self.quantity
        if self.coordinate_system:
            out["coordinate_system"] = self.coordinate_system
        return out


@dataclass
class Command:
    path: str
    summary: str
    effects: list[str]
    modes: list[str]
    target_kind: str
    flags: list[Flag]
    kind: str  # domain, builtin or standard
    untrusted_fields: list[str] = field(default_factory=list)
    adapter_capabilities: list[str] = field(default_factory=list)
    survey_ids: list[str] = field(default_factory=list)
    irreversible: bool = False
    halt: bool = False
    tolerances: dict[str, float] = field(default_factory=dict)
    default_fields: list[str] = field(default_factory=list)
    related_knowledge: list[str] = field(default_factory=list)
    output_schema: dict[str, Any] = field(default_factory=dict)
    examples: list[str] = field(default_factory=list)
    maturity: str = "draft"
    evidence: list[str] = field(default_factory=list)
    departures: list[dict[str, str]] = field(default_factory=list)
    formats: list[str] = field(default_factory=lambda: ["json", "text"])
    output_coordinate_system: str | None = None
    tier_when: list[dict[str, str]] = field(default_factory=list)
    needs_scope: bool = True
    counts_for_risk: bool = True
    handler: Callable[..., Any] | None = None

    @property
    def verb(self) -> str:
        return self.path.split(" ")[-1]

    @property
    def writes(self) -> bool:
        return any(e != "observe" for e in self.effects)

    def flag(self, name: str) -> Flag | None:
        return next((f for f in self.flags if f.name == name), None)

    def registration(self) -> dict[str, Any]:
        """The entry of ``contract/commands.json`` (DREPO-031)."""
        return {
            "path": self.path, "summary": self.summary, "effects": list(self.effects), "modes": list(self.modes),
            "target_kind": self.target_kind, "flags": [f.registration() for f in self.flags],
            "untrusted_fields": list(self.untrusted_fields), "adapter_capabilities": list(self.adapter_capabilities),
            "survey_ids": list(self.survey_ids), "irreversible": self.irreversible, "halt": self.halt,
            "tolerances": dict(self.tolerances), "default_fields": list(self.default_fields),
            "related_knowledge": list(self.related_knowledge),
        }


class Tiers:
    """Confirmation and risk tiers from effects (DCLI-051, DCLI-052, DSEC-001)."""

    def __init__(self, contract: Contract) -> None:
        self.contract = contract

    def strictest(self, tiers: list[str]) -> str:
        return max(tiers, key=self.contract.tier_rank) if tiers else "none"

    def base(self, command: Command, mode: str) -> str:
        """The tier before anything about the call is known."""
        if command.halt:
            return "direct"
        tiers = []
        for effect in command.effects:
            tier = self.contract.effects[effect]["tier"]
            tiers.append(tier[mode] if isinstance(tier, dict) else tier)
        if command.irreversible:
            tiers.append("dangerous")
        return self.strictest(tiers)

    def per_mode(self, command: Command) -> str | dict[str, str]:
        tiers = {mode: self.base(command, mode) for mode in command.modes}
        values = set(tiers.values())
        return values.pop() if len(values) == 1 else tiers

    def risk(self, command: Command) -> str:
        risks = [self.contract.effects[e]["risk"] for e in command.effects]
        return max(risks, key=self.contract.risk_rank) if risks else "T0"


def domain_command(reg: dict[str, Any], contract: Contract, tool_modes: list[str]) -> Command:
    """Check one domain registration and build its command (DCLI-029, DCLI-085)."""
    path = reg.get("path", "")
    where = f"command {path!r}"
    extra = set(reg) - set(contract["registration_fields"]) - {
        "output_schema", "examples", "maturity", "evidence", "departures", "formats", "output_coordinate_system",
    }
    if extra:
        raise RegistrationError(f"{where}: unknown fields {sorted(extra)}")
    missing = [f for f in contract["registration_fields"] if f not in reg] + [
        f for f in ("output_schema", "examples", "maturity", "evidence") if f not in reg
    ]
    if missing:
        raise RegistrationError(f"{where}: missing fields {missing}")
    if not PATH.match(path) or len(path.split(" ")) < 2:
        raise RegistrationError(f"{where}: a domain command is `noun verb` in lowercase words (DCLI-020)")
    words = path.split(" ")
    noun, verb = words[0], words[-1]
    if noun in contract["reserved_names"]:
        raise RegistrationError(f"{where}: {noun!r} belongs to the built-in and standard commands (DCLI-024)")
    if verb not in contract.verbs:
        raise RegistrationError(f"{where}: {verb!r} is not a verb of DCLI-022; use one of {sorted(contract.verbs)}")
    effects = list(reg["effects"])
    if not effects or len(set(effects)) != len(effects) or any(e not in contract.effects for e in effects):
        raise RegistrationError(f"{where}: effects must be a non-empty list of distinct effects of DCLI-051")
    if "observe" in effects and len(effects) > 1:
        raise RegistrationError(f"{where}: a read command has only the effect observe")
    verb_effect = contract.verbs[verb]
    if verb_effect == "observe" and effects != ["observe"]:
        raise RegistrationError(f"{where}: the verb {verb!r} reads; its only effect is observe (DCLI-022)")
    if verb_effect not in (None, "observe") and effects == ["observe"]:
        raise RegistrationError(f"{where}: the verb {verb!r} writes; a read uses list, get, search or check (DCLI-022)")
    halt = bool(reg["halt"])
    if (verb == "stop") != halt:
        raise RegistrationError(f"{where}: a command with the verb stop, and only such a command, is a halt command (DSEC-018)")
    if halt and (effects != ["lifecycle"] or reg["irreversible"]):
        raise RegistrationError(f"{where}: a halt command has only the lifecycle effect and is not irreversible (DSEC-018)")
    modes = list(reg["modes"])
    if not modes or any(m not in tool_modes for m in modes):
        raise RegistrationError(f"{where}: modes must be among the tool's modes {tool_modes}")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", str(reg["target_kind"])):
        raise RegistrationError(f"{where}: target_kind is a lowercase word")
    flags = [_domain_flag(f, contract, where) for f in reg["flags"]]
    names = [f.name for f in flags]
    if len(set(names)) != len(names):
        raise RegistrationError(f"{where}: a flag is declared twice")
    caps = list(reg["adapter_capabilities"])
    unknown = [c for c in caps if c not in contract.capabilities]
    if unknown:
        raise RegistrationError(f"{where}: unknown adapter capabilities {unknown}")
    needed = list(WRITE_CAPABILITIES) if any(e != "observe" for e in effects) else ["read"]
    if any(c not in caps for c in needed):
        raise RegistrationError(f"{where}: the command needs the adapter capabilities {needed}")
    if reg["maturity"] not in [m["level"] for m in contract["maturity_levels"]]:
        raise RegistrationError(f"{where}: unknown maturity {reg['maturity']!r}")
    if not reg["examples"]:
        raise RegistrationError(f"{where}: at least one example is needed (DCLI-031)")
    for name, value in reg["tolerances"].items():
        if not isinstance(value, (int, float)) or value < 0:
            raise RegistrationError(f"{where}: the tolerance of {name} is a number of at least 0")
    for ref in reg["evidence"]:
        if not re.fullmatch(r"(live|eval):[^#\s]+(#\S+)?", ref):
            raise RegistrationError(f"{where}: evidence {ref!r} is not <kind>:<path>[#<anchor>] (DVER-041)")
    return Command(
        path=path, summary=reg["summary"], effects=effects, modes=modes, target_kind=reg["target_kind"],
        flags=flags, kind="domain", untrusted_fields=list(reg["untrusted_fields"]), adapter_capabilities=caps,
        survey_ids=list(reg["survey_ids"]), irreversible=bool(reg["irreversible"]), halt=halt,
        tolerances=dict(reg["tolerances"]), default_fields=list(reg["default_fields"]),
        related_knowledge=list(reg["related_knowledge"]), output_schema=dict(reg["output_schema"]),
        examples=list(reg["examples"]), maturity=reg["maturity"], evidence=list(reg["evidence"]),
        departures=list(reg.get("departures", [])), formats=list(reg.get("formats", ["json", "text"])),
        output_coordinate_system=reg.get("output_coordinate_system"),
    )


def _domain_flag(spec: dict[str, Any], contract: Contract, where: str) -> Flag:
    allowed = {"name", "summary", "type", "required", "quantity", "secret", "output", "coordinate_system"}
    if not isinstance(spec, dict) or set(spec) - allowed or "name" not in spec or "type" not in spec:
        raise RegistrationError(f"{where}: a flag has name, type and only the fields {sorted(allowed)}")
    name, kind = spec["name"], spec["type"]
    if not FLAG.match(name):
        raise RegistrationError(f"{where}: flag {name!r} is not --lowercase-words")
    if name in contract.global_flags or name in ("--help", "--version"):
        raise RegistrationError(f"{where}: {name} is a global flag (DCLI-025, DCLI-036)")
    if name in contract.target_flags:
        raise RegistrationError(f"{where}: {name} is a target flag the runtime adds by mode (DCLI-027)")
    if name in SHARED_TYPES:
        if kind != SHARED_TYPES[name]:
            raise RegistrationError(f"{where}: the shared flag {name} has the type {SHARED_TYPES[name]} (DCLI-026)")
    elif kind not in DOMAIN_TYPES:
        raise RegistrationError(f"{where}: flag {name} has type {kind!r}; use one of {sorted(DOMAIN_TYPES)}")
    quantity = spec.get("quantity")
    if (kind == "quantity") != (quantity is not None):
        raise RegistrationError(f"{where}: flag {name} is a physical parameter only with type quantity and a quantity (DCLI-085)")
    if quantity is not None and quantity not in contract.units:
        raise RegistrationError(f"{where}: flag {name} names the unknown quantity {quantity!r} (DCLI-080)")
    # A name ending in a unit, such as --x-mm, marks a physical parameter without a quantity.
    # One-letter units are left out: --layer-a is not an ampere.
    suffixes = {u["suffix"].replace("_", "-") for u in contract["units"] if len(u["suffix"]) > 2}
    if kind != "quantity" and any(name.endswith(s) for s in suffixes):
        raise RegistrationError(
            f"{where}: flag {name} carries a unit in its name; declare it as a quantity and take the unit in the value (DCLI-085)"
        )
    if spec.get("output") and kind != "path":
        raise RegistrationError(f"{where}: flag {name} names a file the command creates only with type path")
    return Flag(
        name=name, type=kind, summary=spec.get("summary", ""), required=bool(spec.get("required")),
        quantity=quantity, secret=bool(spec.get("secret")), output=bool(spec.get("output")),
        coordinate_system=spec.get("coordinate_system"),
    )
