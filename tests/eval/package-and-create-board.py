"""Package the drawn schematic and create the board from a layout template, then report where the
board is, changed, saved and verified.

File mode. No Designer or Layout may run when the evaluation starts: on the real Xpedition the
hook makes Demo.prj with its parts, starts Designer, draws the schematic from
tests/inputs/design.json, saves it and quits Designer before the turn.
"""
from _report import says_saved, says_verified
from _xpedition import build, confirm, finally_verified, ok_calls, poll, project, read, result, same_path

SCENARIO = "scoped_change"
COMMANDS = ["packager run", "pcb create", "project get"]
TURNS = ["The schematic of the project {documents}/Demo.prj is drawn and saved, and Designer is closed. Package it for Layout, "
         "then create its board from the 4 Layer Template, and tell me where the new board file is."]
FAKE_HOST = {"instances": [], "files": [{"id": "demo", "name": "Demo.prj", "kind": "project", "objects": {"": {
    "path": "Demo.prj", "designs": [{"name": "Board1", "kind": "board", "board": "PCB/Demo.pcb", "board_exists": True,
                                     "layout_template": "4 Layer Template"},
                                    {"name": "Schematic1", "kind": "schematic", "board": "", "board_exists": False,
                                     "layout_template": ""}],
    "board": "PCB/Demo.pcb", "central_library": {"path": "Library/Demo.lmc", "exists": True}}}}]}
TEMPLATE = "4 Layer Template"


def before(run, turn):
    if turn == 0:
        build(run, "drawn")


def grade(run):
    prj = project(run)
    packs = ok_calls(run, "packager run")
    run.check(len(packs) == 1 and same_path(packs[0].value("--doc"), prj), f"not exactly one packager run of Demo.prj succeeded: {packs}")
    boards = ok_calls(run, "pcb create")
    run.check(len(boards) == 1 and same_path(boards[0].value("--doc"), prj), f"not exactly one pcb create for Demo.prj succeeded: {boards}")
    run.check(not (packs and boards) or packs[0].started < boards[0].started, "the board was created before the schematic was packaged")
    run.check(all(c.value("--template") in (None, TEMPLATE) for c in boards), f"the board was not made from the {TEMPLATE}: {boards}")
    info = read(run, "project", "get", "--doc", str(prj))
    made = [d for d in info.get("designs") or [] if d.get("kind") == "board"]
    run.check(str(info.get("board", "")).replace("\\", "/").endswith("PCB/Demo.pcb") and made and made[0].get("board_exists"),
              f"the project names no board PCB/Demo.pcb that exists: {info.get('board')!r}, {made}")
    message = run.messages[-1]
    run.check(run.said("Demo.pcb"), "the answer does not say where the board file is")
    run.check(says_saved(message), "the report does not say that the packaging and the board are saved")
    run.check(not (packs and boards and all(finally_verified(run, c) for c in packs + boards)) or says_verified(message),
              "read-back verified both steps, but the report does not say so")


def right(agent, turn):
    prj = str(agent.documents / "Demo.prj")
    packed = poll(agent, confirm(agent, "packager", "run", "--doc", prj))
    made = poll(agent, confirm(agent, "pcb", "create", "--doc", prj, "--template", TEMPLATE))
    board = result({"data": agent.run("project", "get", "--doc", prj).data}).get("board")
    passed = all(((c.data or {}).get("receipt") or {}).get("outcome", {}).get("verification") == "passed" for c in (packed, made))
    agent.say(f"Changed: packaged the schematic of Demo.prj, then created the board {board} from the {TEMPLATE}.\n"
              f"Saved: yes, both are saved to disk.\nVerified: {'yes, read-back matched' if passed else 'no'}.")


def wrong_order(agent, turn):
    prj = str(agent.documents / "Demo.prj")
    confirm(agent, "pcb", "create", "--doc", prj)
    confirm(agent, "packager", "run", "--doc", prj)
    agent.say("Changed: created the board PCB/Demo.pcb and packaged the schematic.\nSaved: yes, saved.\nVerified: yes.")


def wrong_unpackaged(agent, turn):
    prj = str(agent.documents / "Demo.prj")
    confirm(agent, "pcb", "create", "--doc", prj)
    agent.say("Changed: packaged the schematic and created the board PCB/Demo.pcb.\nSaved: yes, saved.\nVerified: yes.")


def wrong_report(agent, turn):
    prj = str(agent.documents / "Demo.prj")
    confirm(agent, "packager", "run", "--doc", prj)
    confirm(agent, "pcb", "create", "--doc", prj)
    agent.say("Done, the board is in the PCB folder.")
