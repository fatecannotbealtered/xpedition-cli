"""Route two differential pairs across a flex board with ground guards beside them, to a reviewer's
requirements file, check the plan before drawing it, and report each requirement as measured;
leave the board unsaved.

Session mode, Layout. No Designer or Layout may run when the evaluation starts: on the real
Xpedition the hook makes the flex sample up to the placed board, a 90 x 18 mm strip with a 40-pin
board-to-board connector at each end, J1 on the top side and J2 on the bottom, copies the
requirements file beside it, and leaves Layout open with the board; after quits that Layout,
discarding the changes. The board marks no bend, and the user says it lies flat, so vias may go
anywhere between the connectors. The pairs reach J2's pads on the bottom layer, so each changes
layers near J2. The fake host holds no board.
"""
import json
import shutil

from _report import says_not_saved
from _xpedition import FIXTURES, build, confirm, ok_calls, pcb_args, poll, session_with, stop_all, wait_job

SCENARIO = "scoped_change"
COMMANDS = ["pcb get", "component get", "constraint get", "constraint import", "padstack list", "padstack create", "plane create",
            "trace check", "trace create", "via create", "pcb check", "job get"]
TURNS = ["Layout has the flex board Demo.pcb open, with the connector J1 at one end and J2 at the other; it lies flat in the "
         "product and does not bend. Route the SuperSpeed pairs SS_TX1_P/SS_TX1_N and SS_RX1_P/SS_RX1_N from J1 to J2 on layer "
         "1, with the rules in {documents}/rules-flex.json, and put ground traces on both sides of each pair with GND vias "
         "along them where they fit, all vias VIA_D0.2_P0.4. Pour GND on layers 2 and 3. The reviewer's requirements are in "
         "{documents}/requirements-flex.json: check the plan against them before you draw it, and report each requirement as "
         "measured on the board. Leave the other nets. Do not save the board."]
FAKE_HOST = None
PAIRS = (("SS_TX1_P", "SS_TX1_N"), ("SS_RX1_P", "SS_RX1_N"))
NETS = [net for pair in PAIRS for net in pair]


def before(run, turn):
    if turn == 0:
        run.notes["layout"] = build(run, "placed", keep_open=True, sample="flex")
        shutil.copyfile(FIXTURES / "requirements-flex.json", run.documents / "requirements-flex.json")


def after(run):
    stop_all(run)


def _checked(run):
    """The whole result of pcb check with the requirements, run by the grader."""
    out = run.documents / "grader-check.json"
    envelope = run.tool("pcb", "check", *pcb_args(run), "--requirements", str(run.documents / "requirements-flex.json"),
                        "--output", str(out))
    job = ((envelope.get("data") or {}).get("job") or {}).get("id")
    if job:
        wait_job(run, job)
    try:
        return json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        run.check(False, f"the grader's pcb check wrote no result: {(envelope.get('error') or {}).get('message')}")
        return {}


def grade(run):
    checked = _checked(run)
    rules = checked.get("rules") or []
    still_open = sorted({name for row in rules if row.get("rule") == "unrouted" for name in row.get("objects") or []} & set(NETS))
    run.check(not still_open, f"{', '.join(still_open)} still open")
    for row in checked.get("requirements") or []:
        shown = {k: row.get(k) for k in ("measured_pct", "measured_mm", "failed", "reason")}
        run.check(row.get("ok") is True, f"requirement {row.get('index')} ({row.get('label') or row.get('kind')}) is not met: "
                                         f"{json.dumps(shown)}")
    floating = [row["net"] for row in checked.get("connectivity") or [] if row.get("floating")]
    run.check(not floating, f"pieces of {', '.join(floating)} join the rest of their net nowhere")
    checks = [c for c in run.calls if c.command == "trace check" and c.ok and c.value("--file") and c.value("--requirements")]
    drawn = ok_calls(run, "trace create")
    run.check(checks and drawn and checks[0].started < drawn[-1].started,
              "the plan was not checked against the requirements with trace check before it was drawn")
    message = run.messages[-1] if run.messages else ""
    run.check(not ok_calls(run, "doc save"), "the board was saved, although the user said not to")
    run.check(says_not_saved(message), "the report does not say that the board is not saved")


def _route(agent, *, guards=True, check=True):
    """Lanes for each pair's nets on layer 1 from J1's pins to a via beside J2's pin and on to it on
    layer 4, ground guards beside and between the pairs with vias where they fit, as wide and as
    far off them as the rules file's GND class and its clearance to the pairs allow, GND on layers 2
    and 3, the plan checked against the requirements first."""
    path = agent.documents / "PCB" / "Demo.pcb"
    pcb = ["--session", session_with(agent, path), "--doc", str(path)]
    prj = str(agent.documents / "Demo.prj")
    requirements = str(agent.documents / "requirements-flex.json")
    poll(agent, confirm(agent, "constraint", "import", *pcb, "--file", str(agent.documents / "rules-flex.json")))
    vias = (agent.run("padstack", "list", "--doc", prj, "--type", "via", "--limit", "100").data or {}).get("items") or []
    if not any(v.get("id") == "VIA_D0.2_P0.4" for v in vias):
        poll(agent, confirm(agent, "padstack", "create", "--doc", prj, "--drill", "0.2mm", "--pad", "0.4mm"))
    pins = {}
    for refdes in ("J1", "J2"):
        for pin in (agent.run("component", "get", *pcb, "--refdes", refdes).data or {}).get("pins") or []:
            pins.setdefault(pin.get("net"), {})[refdes] = (pin["x_mm"], pin["y_mm"])
    plan = {"lanes": [], "traces": [], "vias": []}
    for index, net in enumerate(NETS):
        (x1, y1), (x2, y2) = pins[net]["J1"], pins[net]["J2"]
        at = x2 - (1.0 if index % 2 == 0 else 1.5)  # beside J2's pin on the board's side, staggered
        plan["lanes"].append({"net": net, "layer": 1, "width_mm": 0.1, "from_mm": [[x1, y1]], "to_mm": [[at, y2]],
                              "edge_mm": [[1.0, 0.0], [89.0, 0.0]], "offset_mm": y1})
        plan["vias"].append({"net": net, "at_mm": [at, y2], "padstack": "VIA_D0.2_P0.4"})
        plan["traces"].append({"net": net, "layer": 4, "width_mm": 0.1, "points_mm": [[at, y2], [x2, y2]]})
    if guards:
        plan["guards"] = [{"nets": [list(pair) for pair in PAIRS], "net": "GND", "width_mm": 0.1, "gap_mm": 0.15, "between": True,
                           "via_pitch_mm": 2.0, "padstack": "VIA_D0.2_P0.4"}]
    route = agent.documents / "route.json"
    route.write_text(json.dumps(plan), encoding="utf-8")
    if check:
        poll(agent, agent.run("trace", "check", *pcb, "--file", str(route), "--requirements", requirements))
    poll(agent, confirm(agent, "trace", "create", *pcb, "--file", str(route)))
    for layer in ("2", "3"):
        poll(agent, confirm(agent, "plane", "create", *pcb, "--net", "GND", "--layer", layer))
    poll(agent, agent.run("pcb", "check", *pcb, "--requirements", requirements))


def right(agent, turn):
    _route(agent)
    agent.say("- Changed: SS_TX1_P/N and SS_RX1_P/N run on layer 1 from J1 to a via beside each of J2's pins and on to it on layer 4, "
              "with GND guards beside and between the pairs and vias along them where they fit; GND poured on layers 2 and 3.\n"
              "- Requirements: ground beside both pairs, every via VIA_D0.2_P0.4, the via pitch where there is room and the pours on "
              "layers 2 and 3 all pass in pcb check.\n- Saved: no, Demo.pcb is not saved.\n- Verified: yes, trace check before drawing "
              "and pcb check after.")


def wrong_no_guards(agent, turn):
    _route(agent, guards=False)
    agent.say("Routed the two pairs on layer 1 and poured GND on layers 2 and 3. Not saved.")


def wrong_unchecked(agent, turn):
    _route(agent, check=False)
    agent.say("Routed the two pairs with guards and poured GND on layers 2 and 3; the requirements pass. Not saved.")
