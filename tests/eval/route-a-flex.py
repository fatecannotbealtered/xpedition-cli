"""Route two differential pairs across a flex board between its two connectors, with two ground
planes, one hatched over the bends, stitched together between them; leave the board unsaved.

Session mode, Layout. No Designer or Layout may run when the evaluation starts: on the real
Xpedition the hook makes the flex sample up to the placed board, a 90 x 18 mm strip with a 40-pin
board-to-board connector at each end, J1 on the top side and J2 on the bottom, and leaves Layout
open with it; after quits that Layout, discarding the changes. The board marks no bend, so the
user names where it bends; the pairs reach J2's pads on the bottom layer, so each changes layers
near J2. The fake host holds no board.
"""
import json

from _report import says_not_saved
from _xpedition import build, by_net, confirm, copper, near, ok_calls, pcb_args, poll, read, session_with, stop_all, wait_job

SCENARIO = "scoped_change"
COMMANDS = ["pcb get", "component get", "constraint get", "constraint import", "padstack list", "padstack create", "plane create",
            "trace create", "via create", "pcb check", "job get"]
TURNS = ["Layout has the flex board Demo.pcb open, with the connector J1 at one end and J2 at the other. Route the SuperSpeed pairs "
         "SS_TX1_P/SS_TX1_N and SS_RX1_P/SS_RX1_N from J1 to J2, with the rules in {documents}/rules-flex.json. The board bends "
         "across x 20 to 24 mm and across x 66 to 70 mm. Make layers 2 and 3 GND planes, layer 2 hatched over the bends, and "
         "stitch the two together with vias between the bends. Leave the other nets. Do not save the board."]
FAKE_HOST = None
PAIRS = (("SS_TX1_P", "SS_TX1_N"), ("SS_RX1_P", "SS_RX1_N"))
NETS = [net for pair in PAIRS for net in pair]
BENDS = ((20.0, 24.0), (66.0, 70.0))
STITCHES = 5  # GND vias between the bends at the least


def before(run, turn):
    if turn == 0:
        run.notes["layout"] = build(run, "placed", keep_open=True, sample="flex")


def after(run):
    stop_all(run)


def _bent(x):
    return any(low - 0.05 <= x <= high + 0.05 for low, high in BENDS)


def _checked(run):
    """The whole result of pcb check, run by the grader."""
    out = run.documents / "grader-check.json"
    envelope = run.tool("pcb", "check", *pcb_args(run), "--output", str(out))
    job = ((envelope.get("data") or {}).get("job") or {}).get("id")
    if job:
        wait_job(run, job)
    try:
        return json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        run.check(False, f"the grader's pcb check wrote no result: {(envelope.get('error') or {}).get('message')}")
        return {}


def grade(run):
    facts = read(run, "pcb", "get", *pcb_args(run))
    zones = facts.get("bend_zones") or []
    for low, high in BENDS:
        run.check(any("hatched_plane" in (zone.get("marked_by") or []) and zone["extents_mm"][0] <= low + 0.1
                      and zone["extents_mm"][2] >= high - 0.1 for zone in zones),
                  f"layer 2 is not hatched across the bend at x {low:g} to {high:g} mm beside a solid GND plane")
    rules = _checked(run).get("rules") or []
    still_open = sorted({name for row in rules if row.get("rule") == "unrouted" for name in row.get("objects") or []} & set(NETS))
    run.check(not still_open, f"{', '.join(still_open)} still open")
    bent = [row.get("message") for row in rules if str(row.get("rule", "")).startswith("bend_")]
    run.check(not bent, f"pcb check finds what a bend forbids: {bent[:3]}")
    skewed = [row.get("message") for row in rules if row.get("rule") == "pair_skew" and set(row.get("objects") or []) & set(NETS)]
    run.check(not skewed, f"a pair's two lengths differ too much: {skewed}")
    now = copper(run)
    in_bends = [via for via in now["vias"] if _bent(float(via["circle"][0]))]
    run.check(not in_bends, f"{len(in_bends)} vias stand in a bend")
    between = [via for via in by_net(now["vias"], "GND") if BENDS[0][1] < float(via["circle"][0]) < BENDS[1][0]]
    run.check(len(between) >= STITCHES, f"{len(between)} GND vias stitch the planes between the bends, not {STITCHES} or more")
    others = sorted({trace.get("net") for trace in now["traces"]} - set(NETS) - {"GND"})
    run.check(not others, f"traces of other nets were drawn: {others}")
    message = run.messages[-1] if run.messages else ""
    run.check(not ok_calls(run, "doc save"), "the board was saved, although the user said not to")
    run.check(says_not_saved(message), "the report does not say that the board is not saved")


def _route(agent, *, hatch=True, via_in_bend=False, stitch=True):
    """Pour GND on layers 2 and 3 outside the bends and hatch layer 2 over them, run each pair's
    nets straight from J1 on layer 1 to a via beside J2's pin and on to it on layer 4, and stitch
    GND between the bends."""
    path = agent.documents / "PCB" / "Demo.pcb"
    pcb = ["--session", session_with(agent, path), "--doc", str(path)]
    prj = str(agent.documents / "Demo.prj")
    poll(agent, confirm(agent, "constraint", "import", *pcb, "--file", str(agent.documents / "rules-flex.json")))
    vias = (agent.run("padstack", "list", "--doc", prj, "--type", "via", "--limit", "100").data or {}).get("items") or []
    name = next((v["id"] for v in vias if near(v.get("drill_mm"), 0.2) and near(v.get("pad_mm"), 0.4)), None)
    if name is None:
        poll(agent, confirm(agent, "padstack", "create", "--doc", prj, "--drill", "0.2mm", "--pad", "0.4mm"))
        name = "VIA_D0.2_P0.4"
    pins = {}
    for refdes in ("J1", "J2"):
        for pin in (agent.run("component", "get", *pcb, "--refdes", refdes).data or {}).get("pins") or []:
            pins.setdefault(pin.get("net"), {})[refdes] = (pin["x_mm"], pin["y_mm"])
    for layer in ("2", "3"):
        for region in (["--to-x", "20mm"], ["--from-x", "24mm", "--to-x", "66mm"], ["--from-x", "70mm"]):
            poll(agent, confirm(agent, "plane", "create", *pcb, "--net", "GND", "--layer", layer, *region))
    for low, high in (("20mm", "24mm"), ("66mm", "70mm")) if hatch else ():
        poll(agent, confirm(agent, "plane", "create", *pcb, "--net", "GND", "--layer", "2", "--from-x", low, "--to-x", high,
                            "--hatch", "diagonal", "--hatch-width", "0.1mm", "--hatch-distance", "0.3mm"))
    plan = {"lanes": [], "traces": [], "vias": []}
    for index, net in enumerate(NETS):
        (x1, y1), (x2, y2) = pins[net]["J1"], pins[net]["J2"]
        at = x2 - (1.0 if index % 2 == 0 else 1.5)  # beside J2's pin on the board's side, staggered
        plan["lanes"].append({"net": net, "layer": 1, "width_mm": 0.1, "from_mm": [[x1, y1]], "to_mm": [[at, y2]],
                              "edge_mm": [[1.0, 0.0], [89.0, 0.0]], "offset_mm": y1})
        plan["vias"].append({"net": net, "at_mm": [at, y2], "padstack": name})
        plan["traces"].append({"net": net, "layer": 4, "width_mm": 0.1, "points_mm": [[at, y2], [x2, y2]]})
    if via_in_bend:
        plan["vias"].append({"net": "GND", "at_mm": [22.0, 3.0], "padstack": name})
    route = agent.documents / "route.json"
    route.write_text(json.dumps(plan), encoding="utf-8")
    poll(agent, confirm(agent, "trace", "create", *pcb, "--file", str(route)))
    if stitch:
        poll(agent, confirm(agent, "via", "create", *pcb, "--net", "GND", "--pitch", "3mm", "--from-x", "25mm", "--to-x", "65mm",
                            "--padstack", name))
    poll(agent, agent.run("pcb", "check", *pcb))


def right(agent, turn):
    _route(agent)
    agent.say("- Changed: SS_TX1_P/N and SS_RX1_P/N run on layer 1 from J1 straight across both bends to a via beside each of "
              "J2's pins, and on to it on layer 4; GND poured on layers 2 and 3 outside the bends, layer 2 hatched over them, "
              "and stitched with vias on a 3 mm grid between the bends.\n- Saved: no, Demo.pcb is not saved.\n"
              "- Verified: yes, the traces and vias read back; pcb check finds nothing in the bends and the pairs' lengths equal.")


def wrong_solid_bends(agent, turn):
    _route(agent, hatch=False)
    agent.say("Routed the two pairs and poured GND on layers 2 and 3 outside the bends. Not saved.")


def wrong_no_stitching(agent, turn):
    _route(agent, stitch=False)
    agent.say("Routed the two pairs and poured GND on layers 2 and 3, layer 2 hatched over the bends. Not saved.")


def wrong_via_in_bend(agent, turn):
    _route(agent, via_in_bend=True)
    agent.say("Routed the two pairs, hatched layer 2 over the bends and stitched GND. Not saved.")
