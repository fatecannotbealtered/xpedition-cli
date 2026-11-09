"""Answer questions about the schematic's components and nets by reading them, and change nothing.

Session mode, Designer. No Designer or Layout may run when the evaluation starts: on the real
Xpedition the hook makes Demo.prj up to the drawn and saved schematic and leaves Designer open with
it; after quits that Designer. The grader compares the answer with what the tool reads at the end.
The fake host holds no schematic.
"""
import re

from _report import says_count
from _xpedition import ask, build, project, read, session_with, stop_all

SCENARIO = "read_answer"
COMMANDS = ["component list", "component get", "net list", "net get"]
TURNS = ["Without changing anything: in the project Demo.prj that Designer has open, which nets are the two pins of R1 on, and "
         "which component pins does the net GND join? Give every component of the schematic with its part number and value, "
         "and say how many nets the schematic has."]
FAKE_HOST = None
UNNAMED = r"\$\w+|unnamed|no name|without a name|未命名|没有名字|无名"


def before(run, turn):
    if turn == 0:
        run.notes["designer"] = build(run, "drawn", keep_open=True)


def after(run):
    stop_all(run)


def _schematic(run):
    args = ["--session", run.notes.get("designer") or "", "--doc", str(project(run))]
    return args, read(run, "component", "list", *args, "--limit", "100").get("items") or []


def grade(run):
    args, components = _schematic(run)
    run.check(components, "the schematic holds no components to ask about")
    message = run.messages[-1] if run.messages else ""
    r1 = read(run, "component", "get", *args, "--refdes", "R1")
    for pin in r1.get("pins") or []:
        net = pin.get("net") or ""
        if net.startswith("$"):
            run.check(re.search(UNNAMED, message, re.IGNORECASE), f"the answer does not say R1 pin {pin.get('number')} is on an "
                      "unnamed net")
        else:
            run.check(run.said(net), f"the answer does not name the net {net} of R1 pin {pin.get('number')}")
    gnd = read(run, "net", "get", *args, "--name", "GND")
    missing = sorted({p.get("refdes") for p in gnd.get("pins") or [] if not run.said(p.get("refdes") or "")})
    run.check(gnd.get("pins") and not missing, f"the answer leaves out the GND pins of {missing}")
    numbers = [c.get("part_number") for c in components if c.get("part_number") and not run.said(c["part_number"])]
    run.check(not numbers, f"the answer leaves out the part numbers {numbers}")
    values = [c.get("value") for c in components if c.get("value") and not run.said(c["value"])]
    run.check(not values, f"the answer leaves out the values {values}")
    total = read(run, "net", "list", *args, "--limit", "1").get("total")
    run.check(says_count(message, total, "net", "nets", "网络"), f"the answer does not say the schematic has {total} nets")
    run.check(not run.write_calls(), f"a write command ran: {run.write_calls()}")


def _read(agent):
    prj = agent.documents / "Demo.prj"
    args = ["--session", session_with(agent, prj), "--doc", str(prj)]
    components = (ask(agent, "component", "list", *args, "--limit", "100") or {}).get("items") or []
    r1 = ask(agent, "component", "get", *args, "--refdes", "R1") or {}
    gnd = ask(agent, "net", "get", *args, "--name", "GND") or {}
    total = (ask(agent, "net", "list", *args, "--limit", "1") or {}).get("total")
    return components, r1, gnd, total


def right(agent, turn):
    components, r1, gnd, total = _read(agent)
    pins = "; ".join(f"pin {p['number']} is on {p['net'] if not (p['net'] or '').startswith('$') else 'an unnamed net, ' + p['net']}"
                     for p in r1.get("pins") or [])
    joined = ", ".join(f"{p['refdes']} pin {p['number']}" for p in gnd.get("pins") or [])
    parts = "; ".join(f"{c['refdes']}: {c['part_number']}" + (f", {c['value']}" if c.get("value") else "") for c in components)
    agent.say(f"R1: {pins}. GND joins {joined}. Components: {parts}. The schematic has {total} nets. Nothing was changed.")


def wrong_guess(agent, turn):
    agent.say("R1 sits on 5V and GND. GND joins C1 and J1. Components: R1 RES-1K 1k, C1 CAP-100N 100n. The schematic has 2 nets.")
