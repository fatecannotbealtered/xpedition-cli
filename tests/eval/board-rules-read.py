"""Answer questions about a board's net classes and the central library's vias by reading them, and
change nothing.

Session mode, Layout. No Designer or Layout may run when the evaluation starts: on the real
Xpedition the hook makes Demo.prj up to the placed board, leaves Layout open with it and imports a
net class POWER holding 5V and GND, 0.45 mm wide; after quits that Layout. The grader compares the
answer with what the tool reads at the end. The fake host holds no constraints.
"""
import json

from _xpedition import ask, build, class_of, need, pcb_args, project, read, rules, session_with, stop_all, width_on, write

SCENARIO = "read_answer"
COMMANDS = ["constraint get", "padstack list", "pcb get"]
TURNS = ["Without changing anything: on the board Demo.pcb that Layout has open, which net class holds the nets 5V and GND, and "
         "how wide are that class's traces on layer 1? And which via padstacks in the project's central library have a "
         "0.25 mm drill, with the pad size of each?"]
FAKE_HOST = None


def before(run, turn):
    if turn == 0:
        run.notes["layout"] = build(run, "placed", keep_open=True)
        setup = run.documents / "setup-rules.json"
        setup.write_text(json.dumps({"net_classes": [{"name": "POWER", "nets": ["5V", "GND"],
                                                      "layers": {"all": {"width_mm": 0.45}}}]}), encoding="utf-8")
        need(write(run, "constraint", "import", *pcb_args(run), "--file", str(setup)), "constraint import")


def after(run):
    stop_all(run)


def _said_number(run, value):
    return any(run.said(text) for text in {f"{value:g}", f"{value:.2f}", f"{value:.3f}"})


def grade(run):
    power = class_of(rules(run), "5V")
    run.check(power.get("name") and run.said(power["name"]), f"the answer does not name the class {power.get('name')}")
    width = width_on(power, 1)
    run.check(isinstance(width, (int, float)) and _said_number(run, width), f"the answer does not give the width {width} mm")
    items = read(run, "padstack", "list", "--doc", str(project(run)), "--type", "via", "--limit", "100").get("items") or []
    vias = [i for i in items if abs((i.get("drill_mm") or 0) - 0.25) < 0.001]
    run.check(vias, "the library holds no via with a 0.25 mm drill to ask about")
    missing = [i.get("id") for i in vias if not run.said(i.get("id"))]
    run.check(not missing, f"the answer leaves out the vias {missing}")
    pads = [i.get("pad_mm") for i in vias if not _said_number(run, i.get("pad_mm"))]
    run.check(not pads, f"the answer leaves out the pad sizes {pads}")
    run.check(not run.write_calls(), f"a write command ran: {run.write_calls()}")


def answer(agent, write_too=False):
    path = agent.documents / "PCB" / "Demo.pcb"
    pcb = ["--session", session_with(agent, path), "--doc", str(path)]
    if write_too:
        agent.run("constraint", "import", *pcb, "--file", str(agent.documents / "setup-rules.json"))
    out = agent.documents / "rules-now.json"
    ask(agent, "constraint", "get", *pcb, "--output", str(out))
    found = json.loads(out.read_text(encoding="utf-8"))
    power = class_of(found, "5V")
    items = (agent.run("padstack", "list", "--doc", str(agent.documents / "Demo.prj"), "--type", "via", "--limit", "100").data
             or {}).get("items") or []
    vias = [f"{i['id']} (pad {i['pad_mm']:g} mm)" for i in items if abs((i.get("drill_mm") or 0) - 0.25) < 0.001]
    return power["name"], width_on(power, 1), vias


def right(agent, turn):
    name, width, vias = answer(agent)
    agent.say(f"5V and GND are in net class {name}, whose traces are {width:g} mm wide on layer 1. The central library's vias "
              f"with a 0.25 mm drill: {', '.join(vias)}. Nothing was changed.")


def wrong_guess(agent, turn):
    agent.say("5V and GND are in the class POWER, 0.5 mm wide on layer 1. The library's 0.25 mm vias: VC050D025P (pad 0.5 mm).")


def wrong_write(agent, turn):
    name, width, vias = answer(agent, write_too=True)
    agent.say(f"5V and GND are in net class {name}, {width:g} mm on layer 1. Vias with a 0.25 mm drill: {', '.join(vias)}.")
