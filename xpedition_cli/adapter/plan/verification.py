"""Designer's own schematic verification, read from the logs it writes.

`Full Verification` (command 34155) and `Quick Verification` (46461) write
`LogFiles/vdrc.log` (the electrical rules) and `LogFiles/grc.log` (the graphical rules)
under the project. Each finding comes back in the shape `review_engine` gives its own,
told apart by `source`: `xpedition/verify:<rule>` and `xpedition/grc:<check>`.
"""

from __future__ import annotations

import re
from typing import Any

FULL_VERIFICATION_COMMAND = 34155
QUICK_VERIFICATION_COMMAND = 46461
VERIFY_SEVERITY = {"error": "high", "warning": "medium", "note": "low"}
_VDRC_FINDING = re.compile(r"^(\S+)\s+-\s+\[(.*?)\]\s*(.*)$")


def parse_vdrc(text: str) -> list[dict[str, Any]]:
    """Designer's `LogFiles/vdrc.log`: SEVERITY / GROUP headers, then one finding per line.

    SEVERITY: Warning
      GROUP: Interconnectivity : 8 Warning(s)
        drc-BI-GROUND - [flatnet : GND, component: U1($2I14), pin: $1P11] BI pin ...
    """
    findings: list[dict[str, Any]] = []
    severity = "info"
    group = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("SEVERITY:"):
            severity = VERIFY_SEVERITY.get(line.partition(":")[2].strip().lower(), "info")
            continue
        if line.startswith("GROUP:"):
            group = line.partition(":")[2].split(":")[0].strip()
            continue
        match = _VDRC_FINDING.match(line)
        if not match:
            continue
        rule, fields, message = match.groups()
        net = refdes = pin = None
        for field in fields.split(","):
            key, _, value = field.partition(":")
            key, value = key.strip().lower(), value.strip()
            if key == "flatnet":
                net = value
            elif key == "component":
                refdes = value.split("(")[0].strip()
            elif key == "pin":
                pin = value
        findings.append(
            {
                "severity": severity,
                "refdes": refdes,
                "net": net,
                "source": f"xpedition/verify:{rule}",
                "finding": message.strip() or rule,
                "evidence": [item for item in (group, fields, pin) if item],
                "suggestion": "check the rule in Verify.ini or fix the connection",
                "confidence": 1.0,
            }
        )
    return findings


def parse_grc(text: str) -> list[dict[str, Any]]:
    """Designer's `LogFiles/grc.log`: report / check headings with issue lines under them.

    A line at the left margin ending in "report" starts a report, a line indented up to
    four spaces names a check, a deeper line is an issue ("No issues ..." is none).
    """
    findings: list[dict[str, Any]] = []
    report = check = ""
    for raw in text.splitlines():
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if indent == 0 and line.endswith("report"):
            report = line
            continue
        if indent == 0:
            continue
        if indent <= 4:
            check = line
            continue
        if line.lower().startswith("no issues"):
            continue
        findings.append(
            {
                "severity": "low",
                "refdes": None,
                "net": None,
                "source": f"xpedition/grc:{check or report}",
                "finding": line,
                "evidence": [item for item in (report, check) if item],
                "suggestion": "fix the graphical issue on the sheet",
                "confidence": 1.0,
            }
        )
    return findings
