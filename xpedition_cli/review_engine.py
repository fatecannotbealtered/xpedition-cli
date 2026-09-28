"""The rules behind `schematic check`, on the design as Designer reads it back.

Two layers of findings, told apart by `source`:

- `cli/<rule>` -- rules decidable from the netlist alone: duplicate reference
  designators and net names, open pins, single-pin and unnamed nets, missing part
  numbers, decoupling, I2C pull-ups, net names, reference-designator prefixes;
- `xpedition/verify:<rule>` and `xpedition/grc:<check>` -- Designer's own
  verification, passed in by the caller.

The netlist rules only use what a snapshot carries: components with pins that
name their net (or `no_connect`), nets flagged `unnamed`, and connections.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from .changeset import verify_project

GROUND_NAMES = {"GND", "AGND", "PGND", "DGND", "VSS", "GROUND", "0V", "EARTH"}
POWER_NAMES = {"VCC", "VDD", "VBUS", "VBAT", "VSYS", "VIN", "VRAW", "AVDD", "DVDD", "VPP", "VREF"}
POWER_PATTERN = re.compile(r"^[+-]?\d+(\.\d+)?V\d*[A-Z_]*$", re.I)
NET_NAME_PATTERN = re.compile(r"^[A-Z0-9_+.\-/\[\]:]+$")
TWO_PIN_PREFIXES = {"R", "C", "L", "D", "F", "FB"}


def is_ground_net(name: str) -> bool:
    upper = str(name).upper()
    return (
        upper in GROUND_NAMES
        or upper.startswith("GND")
        or upper.endswith("GND")
        or upper.startswith("VSS")
    )


def is_power_net(name: str) -> bool:
    upper = str(name).upper()
    if is_ground_net(upper):
        return False
    if upper in POWER_NAMES or POWER_PATTERN.match(upper):
        return True
    return upper.startswith(("VCC", "VDD", "VBUS", "VBAT", "VSYS", "AVDD", "DVDD", "+"))


def _refdes_prefix(refdes: str) -> str:
    match = re.match(r"^([A-Za-z]+)", str(refdes))
    return match.group(1).upper() if match else ""


def _finding(
    rule: str,
    severity: str,
    text: str,
    *,
    refdes: str | None = None,
    net: str | None = None,
    evidence: list[str] | None = None,
    suggestion: str = "",
    confidence: float = 1.0,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "refdes": refdes,
        "net": net,
        "source": f"cli/{rule}",
        "finding": text,
        "evidence": list(evidence or []),
        "suggestion": suggestion,
        "confidence": confidence,
    }


def _net_of_pins(project: dict[str, Any]) -> dict[str, str]:
    """`R1.1` -> net name, from component pins first and connections as a fallback."""
    pin_net: dict[str, str] = {}
    for component in project["components"]:
        refdes = str(component.get("refdes", ""))
        for pin in component.get("pins", []) or []:
            number = pin.get("number")
            net = pin.get("net")
            if number is not None and net:
                pin_net[f"{refdes}.{number}"] = str(net)
    for connection in project.get("connections", []) or []:
        net = str(connection.get("net", "") or "")
        for pin in connection.get("pins", []) or []:
            pin_net.setdefault(str(pin), net)
    return pin_net


def schematic_findings(project: dict[str, Any]) -> list[dict[str, Any]]:
    """Netlist-decidable schematic rules (xpedition-schematic's DS checklist, in code)."""
    findings: list[dict[str, Any]] = []
    components = project["components"]
    pin_net = _net_of_pins(project)
    net_pins: dict[str, set[str]] = defaultdict(set)
    for ref, net in pin_net.items():
        net_pins[net].add(ref)
    unnamed = {str(net.get("name")) for net in project.get("nets", []) if net.get("unnamed")}
    nets_of: dict[str, set[str]] = defaultdict(set)
    for ref, net in pin_net.items():
        nets_of[ref.partition(".")[0]].add(net)

    for component in components:
        refdes = str(component.get("refdes", ""))
        for pin in component.get("pins", []) or []:
            number = pin.get("number")
            if number is None:
                continue
            ref = f"{refdes}.{number}"
            if ref in pin_net or pin.get("no_connect"):
                continue
            findings.append(
                _finding(
                    "open-pin",
                    "high",
                    "pin is connected to nothing and carries no no-connect mark",
                    refdes=refdes,
                    evidence=[ref],
                    suggestion="wire or label the pin, or place a no-connect mark on it",
                )
            )

    for net, pins in sorted(net_pins.items()):
        if net in unnamed:
            if len(pins) >= 3:
                findings.append(
                    _finding(
                        "unnamed-net",
                        "low",
                        "a net joining three or more pins has no name",
                        net=net,
                        evidence=sorted(pins),
                        suggestion="label the net so reviews and the PCB can refer to it",
                    )
                )
            continue
        if len(pins) == 1:
            findings.append(
                _finding(
                    "single-pin-net",
                    "medium",
                    "net has a single pin: a label used once connects nothing",
                    net=net,
                    evidence=sorted(pins),
                    suggestion="add the other end of the net or mark the pin no-connect",
                )
            )

    missing = [
        str(c.get("refdes", ""))
        for c in components
        if not str(c.get("internal_part_no") or "").strip()
    ]
    if missing:
        findings.append(
            _finding(
                "missing-part-number",
                "medium",
                f"{len(missing)} components have no part number",
                evidence=missing[:50],
                suggestion="assign part numbers from the central library before packaging",
            )
        )

    capacitors = [
        str(c.get("refdes", ""))
        for c in components
        if _refdes_prefix(str(c.get("refdes", ""))) == "C"
    ]
    for component in components:
        refdes = str(component.get("refdes", ""))
        if _refdes_prefix(refdes) != "U":
            continue
        for net in sorted(n for n in nets_of.get(refdes, set()) if is_power_net(n)):
            decoupled = any(
                net in nets_of.get(cap, set())
                and any(is_ground_net(n) for n in nets_of.get(cap, set()))
                for cap in capacitors
            )
            if not decoupled:
                findings.append(
                    _finding(
                        "decoupling",
                        "medium",
                        "IC supply net has no capacitor to ground",
                        refdes=refdes,
                        net=net,
                        evidence=[f"{refdes} on {net}"],
                        suggestion="add a decoupling capacitor from the rail to ground at the pin",
                    )
                )

    resistors = [
        str(c.get("refdes", ""))
        for c in components
        if _refdes_prefix(str(c.get("refdes", ""))) == "R"
    ]

    def through_resistors(net: str) -> set[str]:
        """Nets joined to `net` through one resistor."""
        joined: set[str] = set()
        for res in resistors:
            nets = nets_of.get(res, set())
            if net in nets:
                joined.update(n for n in nets if n != net)
        return joined

    i2c_nets = [
        net
        for net in sorted(net_pins)
        if net not in unnamed and re.search(r"(^|_)(SDA|SCL)(_|$)", net.upper())
    ]
    # A pull-up counts on the net itself or one series resistor away (SDA_HOST -33R- SDA).
    pulled_directly = {
        net for net in i2c_nets if any(is_power_net(n) for n in through_resistors(net))
    }
    for net in i2c_nets:
        pulled = net in pulled_directly or any(n in pulled_directly for n in through_resistors(net))
        if not pulled:
            findings.append(
                _finding(
                    "i2c-pullup",
                    "medium",
                    "I2C line has no pull-up resistor to a supply",
                    net=net,
                    evidence=sorted(net_pins[net]),
                    suggestion="add a pull-up resistor to the bus supply",
                )
            )

    for net in sorted(net_pins):
        if net in unnamed or NET_NAME_PATTERN.match(net):
            continue
        findings.append(
            _finding(
                "net-name",
                "low",
                "net name is not ASCII upper-case",
                net=net,
                evidence=[net],
                suggestion="rename to UPPER_SNAKE_CASE (rails by voltage, active-low with _N)",
            )
        )

    for component in components:
        refdes = str(component.get("refdes", ""))
        pins = component.get("pins", []) or []
        prefix = _refdes_prefix(refdes)
        if prefix in TWO_PIN_PREFIXES and pins and len(pins) != 2:
            findings.append(
                _finding(
                    "refdes-prefix",
                    "low",
                    f"reference designator prefix {prefix} on a {len(pins)}-pin part",
                    refdes=refdes,
                    evidence=[refdes],
                    suggestion="check the prefix against the device class",
                    confidence=0.7,
                )
            )
    return findings


def run_review(
    project: dict[str, Any], extra_findings: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    checks = verify_project(project)
    findings: list[dict[str, Any]] = []
    for refdes in checks["duplicate_refdes"]:
        findings.append(
            {
                "severity": "high",
                "refdes": refdes,
                "net": None,
                "source": "cli/structure",
                "finding": "duplicate reference designator",
                "evidence": [refdes],
                "suggestion": "assign a unique refdes before export",
                "confidence": 1.0,
            }
        )
    for net in checks["duplicate_nets"]:
        findings.append(
            {
                "severity": "high",
                "refdes": None,
                "net": net,
                "source": "cli/structure",
                "finding": "duplicate net name",
                "evidence": [net],
                "suggestion": "merge or rename duplicate nets",
                "confidence": 1.0,
            }
        )
    for net in checks["connections_with_missing_nets"]:
        findings.append(
            {
                "severity": "high",
                "refdes": None,
                "net": net,
                "source": "cli/structure",
                "finding": "connection references a missing net",
                "evidence": [net],
                "suggestion": "create the net before connecting pins",
                "confidence": 1.0,
            }
        )
    findings.extend(schematic_findings(project))
    for finding in extra_findings or []:
        if isinstance(finding, dict):
            findings.append(finding)
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    findings.sort(
        key=lambda f: (
            order.get(str(f.get("severity")), 4),
            str(f.get("source")),
            str(f.get("refdes") or ""),
            str(f.get("net") or ""),
        )
    )
    counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0, "info": 0}
    for finding in findings:
        severity = str(finding.get("severity", "info"))
        counts[severity] = counts.get(severity, 0) + 1
    return {
        "project": project["project"],
        "findings": findings,
        "summary": {"total": len(findings), "by_severity": counts, "valid": not findings},
        "_untrusted": [
            "project",
            "findings[].finding",
            "findings[].evidence",
            "findings[].suggestion",
        ],
    }
