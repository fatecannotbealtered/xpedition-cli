"""Take back copper work: delete the traces and vias of two nets, a plane and a soldermask opening,
and keep everything else; report it and leave the board unsaved.

Session mode, Layout. No Designer or Layout may run when the evaluation starts: on the real
Xpedition the hook makes Demo.prj up to the laid-out board (a GND plane on layer 2, routed) and
leaves Layout open with it, then pours GND on layer 1 too, stitches the two planes on a 4 mm grid,
opens the top soldermask over (60, 38) to (66, 44) mm and saves; after quits that Layout,
discarding the changes. The fake host cannot hold copper.
"""
from _report import says_not_saved, says_verified
from _xpedition import build, by_net, confirm, copper, drawn, finally_verified, need, ok_calls, pcb_args, read, session_with, \
    stop_all, write

SCENARIO = "scoped_change"
COMMANDS = ["trace delete", "via delete", "plane delete", "solder-mask delete", "pcb get"]
TURNS = ["Layout has the board Demo.pcb open. Take back the last copper work on it: delete the traces and the vias of the nets 5V "
         "and GND, the GND plane on layer 1, and the top soldermask opening at (63, 41) mm. Keep everything else. Do not save "
         "the board."]
FAKE_HOST = None
NETS = ("5V", "GND")


def before(run, turn):
    if turn == 0:
        run.notes["layout"] = build(run, "laid_out", keep_open=True)
        pcb = pcb_args(run)
        need(write(run, "plane", "create", *pcb, "--net", "GND", "--layer", "1"), "plane create on layer 1")
        need(write(run, "via", "create", *pcb, "--net", "GND", "--pitch", "4mm"), "via create")
        need(write(run, "solder-mask", "create", *pcb, "--x1", "60mm", "--y1", "38mm", "--x2", "66mm", "--y2", "44mm"),
             "solder-mask create")
        need(write(run, "doc", "save", *pcb), "doc save of the board")
        traces = copper(run)["traces"]
        run.notes["kept"] = sorted((t.get("net"), t.get("layer")) for t in traces if t.get("net") not in NETS)


def after(run):
    stop_all(run)


def grade(run):
    now = copper(run)
    left = sorted({item.get("net") for item in by_net(now["traces"] + now["vias"], *NETS)})
    run.check(not left, f"traces or vias of {left} are still on the board")
    kept = sorted((t.get("net"), t.get("layer")) for t in now["traces"] if t.get("net") not in NETS)
    run.check(kept == run.notes.get("kept"), f"traces of other nets changed: {run.notes.get('kept')} became {kept}")
    top = drawn(run, "--layers", "1")
    run.check(not top.get("planes"), f"the top layer still holds a plane: {top}")
    run.check(not top.get("openings"), f"the top still holds a soldermask opening: {top}")
    inner = drawn(run, "--layers", "2")
    run.check((inner.get("planes") or 0) >= 1, "the GND plane on layer 2 is gone, although it was to be kept")
    message = run.messages[-1]
    run.check(not ok_calls(run, "doc save"), "the board was saved, although the user said not to")
    run.check(read(run, "doc", "get", *pcb_args(run)).get("dirty") is True, "the board holds no unsaved changes")
    run.check(says_not_saved(message), "the report does not say that the board is not saved")
    writes = [calls[-1] for calls in (ok_calls(run, c) for c in COMMANDS[:4]) if calls]
    run.check(not (len(writes) == 4 and all(finally_verified(run, c) for c in writes)) or says_verified(message),
              "read-back verified every deletion, but the report does not say so")


def take_back(agent, layer="1"):
    path = agent.documents / "PCB" / "Demo.pcb"
    pcb = ["--session", session_with(agent, path), "--doc", str(path)]
    confirm(agent, "trace", "delete", *pcb, "--nets", ",".join(NETS))
    confirm(agent, "via", "delete", *pcb, "--nets", ",".join(NETS))
    confirm(agent, "plane", "delete", *pcb, "--net", "GND", "--layer", layer)
    confirm(agent, "solder-mask", "delete", *pcb, "--x", "63mm", "--y", "41mm")


def right(agent, turn):
    take_back(agent)
    agent.say("- Changed: the traces and vias of 5V and GND, the GND plane on layer 1 and the top soldermask opening at "
              "(63, 41) mm are deleted; everything else stays.\n- Saved: no, Demo.pcb is not saved.\n- Verified: yes, each "
              "deletion read back.")


def wrong_plane(agent, turn):
    take_back(agent, layer="2")
    agent.say("- Changed: deleted the 5V and GND copper, the GND plane on layer 1 and the opening.\n- Saved: no, not saved.\n"
              "- Verified: yes.")


def wrong_report(agent, turn):
    take_back(agent)
    agent.say("Deleted.")
