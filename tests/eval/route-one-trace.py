"""Draw one planned trace between two named pins, as wide as the net's class allows, around the
other nets' copper; leave the board unsaved.

Session mode, Layout. No Designer or Layout may run when the evaluation starts: on the real
Xpedition the hook makes Demo.prj up to the placed board and leaves Layout open with it; after quits
that Layout, discarding the changes. On that board a straight line from J1 pin 2 to C1 pin 2 passes
J1 pin 1 and C1 pin 1, both on 5V, so the trace must go around them. The fake host holds no board.
"""
import json

from _report import says_not_saved, says_verified
from _xpedition import build, by_net, class_of, confirm, copper, finally_verified, ok_calls, pcb_args, poll, read, \
    rules, session_with, stop_all, width_on

SCENARIO = "scoped_change"
COMMANDS = ["component get", "constraint get", "trace create", "pcb get"]
TURNS = ["Layout has the board Demo.pcb open. Join pin 2 of J1 to pin 2 of C1, both on the net GND, with a trace on layer 1, as "
         "wide as GND's net class allows there. Keep clear of the other nets' copper, and draw nothing else. Do not save the "
         "board."]
FAKE_HOST = None
NEAR_MM = 0.05


def before(run, turn):
    if turn == 0:
        run.notes["layout"] = build(run, "placed", keep_open=True)


def after(run):
    stop_all(run)


def _pin(run, refdes, number):
    pins = read(run, "component", "get", *pcb_args(run), "--refdes", refdes).get("pins") or []
    return next(((p.get("x_mm"), p.get("y_mm")) for p in pins if p.get("number") == number), (None, None))


def _touches(traces, spot):
    return any(abs(p[0] - spot[0]) <= NEAR_MM and abs(p[1] - spot[1]) <= NEAR_MM for t in traces for p in t.get("path") or [])


def grade(run):
    now = copper(run)
    gnd = [t for t in by_net(now["traces"], "GND") if t.get("layer") == 1]
    run.check(gnd, "GND has no trace on layer 1")
    run.check(not [t for t in now["traces"] if t.get("net") != "GND"] and not now["vias"],
              "the board holds traces or vias other than GND's on layer 1")
    for refdes in ("J1", "C1"):
        spot = _pin(run, refdes, "2")
        run.check(None not in spot and _touches(gnd, spot), f"no GND trace on layer 1 ends on {refdes} pin 2 at {spot}")
    width = width_on(class_of(rules(run), "GND"), 1)
    wrong = sorted({t.get("width") for t in gnd if width is None or abs((t.get("width") or 0) - width) > 0.001})
    run.check(not wrong, f"GND's traces on layer 1 are {wrong} mm wide, not the class's {width} mm")
    message = run.messages[-1] if run.messages else ""
    run.check(not ok_calls(run, "doc save"), "the board was saved, although the user said not to")
    run.check(says_not_saved(message), "the report does not say that the board is not saved")
    drawn = ok_calls(run, "trace create")
    run.check(drawn, "trace create did not draw the trace")
    run.check(not drawn or not finally_verified(run, drawn[-1]) or says_verified(message),
              "read-back verified the trace, but the report does not say so")


def _draw(agent, detour=True):
    path = agent.documents / "PCB" / "Demo.pcb"
    pcb = ["--session", session_with(agent, path), "--doc", str(path)]
    pins = {}
    for refdes in ("J1", "C1"):
        found = (agent.run("component", "get", *pcb, "--refdes", refdes).data or {}).get("pins") or []
        pins[refdes] = next((p["x_mm"], p["y_mm"]) for p in found if p["number"] == "2")
    out = agent.documents / "rules-now.json"
    agent.run("constraint", "get", *pcb, "--output", str(out))
    width = width_on(class_of(json.loads(out.read_text(encoding="utf-8")), "GND"), 1)
    (x1, y1), (x2, y2) = pins["J1"], pins["C1"]
    below = min(y1, y2) - 4.0
    points = [[x1, y1], [x1, below], [x2, below], [x2, y2]] if detour else [[x1, y1], [x2, y2]]
    plan = agent.documents / "plan.json"
    plan.write_text(json.dumps({"traces": [{"net": "GND", "layer": 1, "width_mm": width, "points_mm": points}]}), encoding="utf-8")
    return poll(agent, confirm(agent, "trace", "create", *pcb, "--file", str(plan))), width


def right(agent, turn):
    _call, width = _draw(agent)
    agent.say(f"- Changed: one GND trace on layer 1, {width:g} mm wide as its class allows, from J1 pin 2 down and around the 5V "
              "pads to C1 pin 2.\n- Saved: no, Demo.pcb is not saved.\n- Verified: yes, the trace read back.")


def wrong_straight(agent, turn):
    _draw(agent, detour=False)
    agent.say("Drew the GND trace from J1 pin 2 to C1 pin 2. Not saved.")
