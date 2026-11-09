"""Tidy a placed board by named changes: one part to the bottom at a position, a mounting hole in
each corner, every reference designator beside its part; then take one hole back and say which
holes are left. The board stays unsaved.

Session mode, Layout. No Designer or Layout may run when the evaluation starts: on the real
Xpedition the hook makes Demo.prj up to the placed board, 70 x 48 mm, and leaves Layout open with
it; after quits that Layout, discarding the changes. The fake host holds no board.
"""
from _report import says_count, says_not_saved, says_verified
from _xpedition import build, confirm, finally_verified, ok_calls, pcb_args, project, read, session_with, stop_all

SCENARIO = "scoped_change"
COMMANDS = ["component list", "component move", "padstack list", "mounting-hole create", "mounting-hole delete",
            "mounting-hole list", "refdes move"]
TURNS = ["Layout has the board Demo.pcb open. Put R1 on the bottom side at (24, 12) mm. Put an unplated 3.2 mm mounting hole in "
         "each corner of the board, 3 mm in from both edges, with the central library's padstack for it. Then give every "
         "component's reference designator a free spot beside its part. Do not save the board.",
         "Take the top right mounting hole out again.",
         "Which mounting holes does the board have now?"]
FAKE_HOST = None
CORNERS = {(3.0, 3.0), (67.0, 3.0), (3.0, 45.0)}  # the top right one, (67, 45), is taken out


def before(run, turn):
    if turn == 0:
        run.notes["layout"] = build(run, "placed", keep_open=True)


def after(run):
    stop_all(run)


def _hole_padstacks(run):
    items = read(run, "padstack", "list", "--doc", str(project(run)), "--type", "mounting_hole", "--limit", "100").get("items") or []
    return {i.get("id") for i in items if abs((i.get("drill_mm") or 0) - 3.2) < 0.001 and not i.get("plated")}


def grade(run):
    pcb = pcb_args(run)
    r1 = read(run, "component", "get", *pcb, "--refdes", "R1")
    run.check(r1.get("side") == "bottom", f"R1 is on the {r1.get('side')}, not the bottom")
    run.check(abs((r1.get("x_mm") or 0) - 24) < 0.01 and abs((r1.get("y_mm") or 0) - 12) < 0.01,
              f"R1 stands at ({r1.get('x_mm')}, {r1.get('y_mm')}), not (24, 12) mm")
    holes = read(run, "mounting-hole", "list", *pcb).get("items") or []
    spots = {(round(h.get("x_mm") or 0, 2), round(h.get("y_mm") or 0, 2)) for h in holes}
    run.check(spots == CORNERS, f"the board's mounting holes stand at {sorted(spots)}, not {sorted(CORNERS)}")
    allowed = _hole_padstacks(run)
    wrong = sorted({h.get("padstack") for h in holes} - allowed)
    run.check(allowed and not wrong, f"the holes use {wrong}, not an unplated 3.2 mm padstack such as {sorted(allowed)}")
    parts = [c.get("refdes") for c in read(run, "component", "list", *pcb, "--limit", "100").get("items") or []]
    moved = {c.value("--refdes") for c in ok_calls(run, "refdes move", turn=0)}
    run.check(set(parts) <= moved, f"the reference designators of {sorted(set(parts) - moved)} were not given a spot")
    covering = [c.value("--refdes") for c in ok_calls(run, "refdes move", turn=0) if ((c.data or {}).get("result") or {}).get("overlaps")]
    run.check(not covering, f"the reference designators of {covering} were left over other objects")
    run.check(not ok_calls(run, "doc save"), "the board was saved, although the user said not to")
    run.check(read(run, "doc", "get", *pcb).get("dirty") is True, "the board holds no unsaved changes")
    run.check(any(says_not_saved(m) for m in run.messages[:2]), "no report says that the board is not saved")
    writes = [c for c in ok_calls(run, "component move", turn=0) + ok_calls(run, "mounting-hole create", turn=0)]
    run.check(not (writes and all(finally_verified(run, c) for c in writes)) or says_verified(run.messages[0]),
              "read-back verified the changes, but the first report does not say so")
    last = run.messages[-1] if run.messages else ""
    run.check(says_count(last, 3, "mounting hole", "mounting holes", "hole", "holes", "安装孔", "孔"),
              "the last answer does not say the board has 3 mounting holes")


def _padstack(agent):
    items = (agent.run("padstack", "list", "--doc", str(agent.documents / "Demo.prj"), "--type", "mounting_hole",
                       "--limit", "100").data or {}).get("items") or []
    return next(i["id"] for i in items if abs((i.get("drill_mm") or 0) - 3.2) < 0.001 and not i.get("plated"))


def _pcb(agent):
    path = agent.documents / "PCB" / "Demo.pcb"
    return ["--session", session_with(agent, path), "--doc", str(path)]


def right(agent, turn):
    pcb = _pcb(agent)
    if turn == 0:
        confirm(agent, "component", "move", *pcb, "--refdes", "R1", "--x", "24mm", "--y", "12mm", "--side", "bottom")
        confirm(agent, "mounting-hole", "create", *pcb, "--padstack", _padstack(agent), "--corners", "--inset", "3mm")
        for item in (agent.run("component", "list", *pcb, "--limit", "100").data or {}).get("items") or []:
            confirm(agent, "refdes", "move", *pcb, "--refdes", item["refdes"], "--auto")
        agent.say("- Changed: R1 is on the bottom at (24, 12) mm; a 3.2 mm unplated mounting hole stands in each corner, 3 mm "
                  "in; every reference designator sits on a free spot beside its part.\n- Saved: no, Demo.pcb is not saved.\n"
                  "- Verified: yes, each change read back.")
    elif turn == 1:
        confirm(agent, "mounting-hole", "delete", *pcb, "--x", "67mm", "--y", "45mm")
        agent.say("The top right mounting hole at (67, 45) mm is deleted and verified; the board is still not saved.")
    else:
        holes = (agent.run("mounting-hole", "list", *pcb).data or {}).get("items") or []
        spots = ", ".join(f"({h['x_mm']:g}, {h['y_mm']:g})" for h in holes)
        agent.say(f"The board has {len(holes)} mounting holes, at {spots} mm.")


def wrong_top_side(agent, turn):
    pcb = _pcb(agent)
    if turn == 0:
        confirm(agent, "component", "move", *pcb, "--refdes", "R1", "--x", "24mm", "--y", "12mm")
        confirm(agent, "mounting-hole", "create", *pcb, "--padstack", _padstack(agent), "--corners", "--inset", "3mm")
        agent.say("Done: R1 moved, holes placed. Not saved; verified.")
    elif turn == 1:
        confirm(agent, "mounting-hole", "delete", *pcb, "--x", "67mm", "--y", "45mm")
        agent.say("Deleted.")
    else:
        agent.say("The board has 3 mounting holes.")
