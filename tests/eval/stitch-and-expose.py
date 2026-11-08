"""Pour a second GND plane, stitch the two with vias, bare a patch of GND copper and draw the board;
report it and leave the board unsaved.

Session mode, Layout. No Designer or Layout may run when the evaluation starts: on the real
Xpedition the hook makes Demo.prj up to the laid-out board (a GND plane on layer 2, routed, saved)
and leaves Layout open with it; after quits that Layout, discarding the changes. The fake host
cannot pour or stitch copper.
"""
from _report import says_not_saved, says_verified
from _xpedition import build, by_net, confirm, copper, digest, drawn, ended, finally_verified, near, ok_calls, pcb_args, poll, \
    read, session_with, stop_all

SCENARIO = "scoped_change"
COMMANDS = ["plane create", "via create", "solder-mask create", "pcb render", "job get", "pcb get"]
TURNS = ["Layout has the board Demo.pcb open, routed, with a GND plane on layer 2. Pour a GND plane on the top layer as well, "
         "stitch the two GND planes together with vias on a 4 mm grid, and bare the top GND copper over the rectangle from "
         "(60, 38) mm to (66, 44) mm so it can be probed. Then draw the top of the board into {documents}/top.png for me. "
         "Do not save the board."]
FAKE_HOST = None
CORNERS = ((60.0, 38.0), (66.0, 44.0))


def before(run, turn):
    if turn == 0:
        run.notes["layout"] = build(run, "laid_out", keep_open=True)
        run.notes["gnd_vias"] = len(by_net(copper(run)["vias"], "GND"))


def _mm(value):
    try:
        return float(str(value).lower().removesuffix("mm"))
    except ValueError:
        return None


def grade(run):
    planes = ok_calls(run, "plane create")
    run.check(any(c.value("--net") == "GND" and c.value("--layer") == "1" for c in planes), "no GND plane was poured on layer 1")
    stitched = [c for c in ok_calls(run, "via create") if c.value("--net") == "GND" and near(_mm(c.value("--pitch")), 4.0)]
    run.check(stitched, "GND was not stitched on a 4 mm grid")
    vias = len(by_net(copper(run)["vias"], "GND"))
    run.check(vias > run.notes.get("gnd_vias", 0), f"GND has {vias} vias, no more than the {run.notes.get('gnd_vias')} it had")
    openings = [c for c in ok_calls(run, "solder-mask create")
                if sorted([_mm(c.value("--x1")), _mm(c.value("--x2"))]) == [60.0, 66.0]
                and sorted([_mm(c.value("--y1")), _mm(c.value("--y2"))]) == [38.0, 44.0]
                and (c.value("--side") or "top") == "top" and not c.value("--cover-layer")]
    run.check(openings, f"no top soldermask opening over {CORNERS}")
    if openings:
        run.check("GND" in (ended(run, openings[-1]).get("nets") or []), "the opening bares no GND copper")
    top = drawn(run, "--layers", "1")
    run.check((top.get("planes") or 0) >= 1, f"the top layer holds no plane: {top}")
    run.check((top.get("openings") or 0) >= 1, f"the top holds no soldermask opening: {top}")
    pictures = [c for c in ok_calls(run, "pcb render") if (c.value("--output") or "").replace("\\", "/").endswith("/top.png")]
    run.check(pictures and digest(run.documents / "top.png"), "top.png was not drawn in the documents folder")
    if pictures and openings:
        run.check(pictures[-1].started > openings[-1].started, "the picture was drawn before the opening was made")
    message = run.messages[-1]
    run.check(not ok_calls(run, "doc save"), "the board was saved, although the user said not to")
    run.check(read(run, "doc", "get", *pcb_args(run)).get("dirty") is True, "the board holds no unsaved changes")
    run.check(says_not_saved(message), "the report does not say that the board is not saved")
    writes = [calls[-1] for calls in (planes, stitched, openings) if calls]
    run.check(not (len(writes) == 3 and all(finally_verified(run, c) for c in writes)) or says_verified(message),
              "read-back verified every change, but the report does not say so")


def after(run):
    stop_all(run)


def copper_work(agent, stitch=True):
    path = agent.documents / "PCB" / "Demo.pcb"
    pcb = ["--session", session_with(agent, path), "--doc", str(path)]
    confirm(agent, "plane", "create", *pcb, "--net", "GND", "--layer", "1")
    if stitch:
        poll(agent, confirm(agent, "via", "create", *pcb, "--net", "GND", "--pitch", "4mm"))
    confirm(agent, "solder-mask", "create", *pcb, "--x1", "60mm", "--y1", "38mm", "--x2", "66mm", "--y2", "44mm")
    poll(agent, confirm(agent, "pcb", "render", *pcb, "--output", str(agent.documents / "top.png")))


def right(agent, turn):
    copper_work(agent)
    agent.say("- Changed: GND plane poured on layer 1; GND stitched between layers 1 and 2 on a 4 mm grid; the top soldermask "
              "is open over (60, 38) to (66, 44) mm, baring GND; the top is drawn in top.png.\n- Saved: no, Demo.pcb is not "
              "saved.\n- Verified: yes, each change read back.")


def wrong_no_stitch(agent, turn):
    copper_work(agent, stitch=False)
    agent.say("- Changed: GND plane on layer 1, stitched on a 4 mm grid, opening made, top.png drawn.\n- Saved: no, not saved."
              "\n- Verified: yes.")


def wrong_report(agent, turn):
    copper_work(agent)
    agent.say("All done, see top.png.")
