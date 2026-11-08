"""Put the power nets in a net class of their own width, route only that class, and give their vias
the size the design asks for, adding that via to the central library; report it and leave the
board unsaved.

Session mode, Layout. No Designer or Layout may run when the evaluation starts: on the real
Xpedition the hook makes Demo.prj up to the placed board and leaves Layout open with it, pours a
GND plane on layer 2 and saves, so the router connects GND to the plane through vias; after quits
that Layout, discarding the routing. The central library of Xpedition's stock template holds no
via with a 0.3 mm drill and a 0.65 mm pad. The fake host cannot route a board.
"""
import json

from _report import says_not_saved, says_verified
from _xpedition import ask, build, by_net, class_of, confirm, copper, finally_verified, near, need, ok_calls, pcb_args, \
    poll, project, read, rules, session_with, stop_all, width_on, write

SCENARIO = "scoped_change"
COMMANDS = ["constraint get", "constraint import", "padstack list", "padstack create", "router run", "via update", "job get",
            "pcb get"]
TURNS = ["Layout has the board Demo.pcb open: its parts are placed and GND has a plane on layer 2, but nothing is routed. Put the "
         "power nets 5V and GND in a net class POWER, 0.5 mm wide on every layer, and route only that class with the autorouter. "
         "Every via on those nets must have a 0.3 mm drill and a 0.65 mm pad; add such a via to the central library if it has "
         "none. Do not save the board."]
FAKE_HOST = None
POWER = ("5V", "GND")
DRILL, PAD = 0.3, 0.65


def before(run, turn):
    if turn == 0:
        run.notes["layout"] = build(run, "placed", keep_open=True)
        need(write(run, "plane", "create", *pcb_args(run), "--net", "GND", "--layer", "2"), "plane create")
        need(write(run, "doc", "save", *pcb_args(run)), "doc save of the board")


def after(run):
    stop_all(run)


def grade(run):
    found = rules(run)
    classes = {net: class_of(found, net).get("name") for net in POWER}
    run.check(all(str(name).upper() == "POWER" for name in classes.values()), f"5V and GND are not both in class POWER: {classes}")
    power = class_of(found, "5V")
    layers = int(read(run, "pcb", "get", *pcb_args(run)).get("layers") or 0)
    widths = {layer: width_on(power, layer) for layer in range(1, layers + 1)}
    run.check(layers and all(near(w, 0.5) for w in widths.values()), f"class POWER is not 0.5 mm wide on every layer: {widths}")
    copper_now = copper(run)
    drawn = by_net(copper_now["traces"], *POWER)
    run.check(drawn, "5V and GND have no traces: the class was not routed")
    run.check(all(near(t.get("width"), 0.5) for t in drawn),
              f"power traces are not 0.5 mm wide: {sorted({t.get('width') for t in drawn})}")
    others = sorted({t.get("net") for t in copper_now["traces"] if t.get("net") not in POWER})
    run.check(not others, f"nets outside class POWER were routed too: {others}")
    library = read(run, "padstack", "list", "--doc", str(project(run)), "--type", "via", "--limit", "100").get("items") or []
    sized = {i.get("id") for i in library if near(i.get("drill_mm"), DRILL) and near(i.get("pad_mm"), PAD)}
    run.check(sized, f"the central library holds no via with a {DRILL} mm drill and a {PAD} mm pad")
    routed = [c.started for c in ok_calls(run, "router run")]
    with_vias = sorted({v.get("net") for v in by_net(copper_now["vias"], *POWER)})
    updates = [c for c in ok_calls(run, "via update") if routed and c.started > max(routed)]
    if with_vias:
        last = updates[-1] if updates else None
        run.check(last is not None, f"the vias of {with_vias} were not changed after the last routing")
        if last is not None:
            run.check(last.value("--padstack") in sized, f"via update used {last.value('--padstack')}, not a {DRILL}/{PAD} mm via")
            named = set((last.value("--nets") or "").split(",")) | {n for n in POWER if "POWER" in (last.value("--net-classes") or "").upper()}
            run.check(set(with_vias) <= named, f"via update left out nets with vias: {set(with_vias) - named}")
    message = run.messages[-1]
    run.check(not ok_calls(run, "doc save"), "the board was saved, although the user said not to")
    run.check(read(run, "doc", "get", *pcb_args(run)).get("dirty") is True, "the board holds no unsaved changes")
    run.check(says_not_saved(message), "the report does not say that the board is not saved")
    writes = [calls[-1] for calls in (ok_calls(run, c) for c in ("constraint import", "router run", "via update")) if calls]
    run.check(not (writes and all(finally_verified(run, c) for c in writes)) or says_verified(message),
              "read-back verified every change, but the report does not say so")


def _pcb(agent):
    path = agent.documents / "PCB" / "Demo.pcb"
    return ["--session", session_with(agent, path), "--doc", str(path)]


def route_power(agent, width=0.5, classes=True):
    pcb, prj = _pcb(agent), str(agent.documents / "Demo.prj")
    ask(agent, "constraint", "get", *pcb)
    rules_file = agent.documents / "power-rules.json"
    rules_file.write_text(json.dumps({"net_classes": [{"name": "POWER", "nets": list(POWER),
                                                       "layers": {"all": {"width_mm": width}}}]}), encoding="utf-8")
    poll(agent, confirm(agent, "constraint", "import", *pcb, "--file", str(rules_file)))
    vias = (agent.run("padstack", "list", "--doc", prj, "--type", "via", "--limit", "100").data or {}).get("items") or []
    name = next((v["id"] for v in vias if near(v.get("drill_mm"), DRILL) and near(v.get("pad_mm"), PAD)), None)
    if name is None:
        poll(agent, confirm(agent, "padstack", "create", "--doc", prj, "--drill", f"{DRILL}mm", "--pad", f"{PAD}mm"))
        name = "VIA_D0.3_P0.65"
    poll(agent, confirm(agent, "router", "run", *pcb, *(["--net-classes", "POWER"] if classes else [])))
    confirm(agent, "via", "update", *pcb, "--net-classes", "POWER", "--padstack", name)
    return name


def right(agent, turn):
    name = route_power(agent)
    agent.say(f"- Changed: 5V and GND are in net class POWER, 0.5 mm on every layer; the router routed POWER only; its vias are "
              f"now {name} (0.3 mm drill, 0.65 mm pad), added to the central library.\n- Saved: the rules and the library via are "
              "saved by Constraint Manager and the library; the board is not saved.\n- Verified: yes, each change read back.")


def wrong_every_net(agent, turn):
    name = route_power(agent, classes=False)
    agent.say(f"- Changed: class POWER at 0.5 mm, routed, vias {name}.\n- Saved: the board is not saved.\n- Verified: yes.")


def wrong_width(agent, turn):
    name = route_power(agent, width=0.3)
    agent.say(f"- Changed: class POWER at 0.5 mm, POWER routed, vias {name}.\n- Saved: the board is not saved.\n- Verified: yes.")


def wrong_report(agent, turn):
    route_power(agent)
    agent.say("Done: the power nets are routed.")
