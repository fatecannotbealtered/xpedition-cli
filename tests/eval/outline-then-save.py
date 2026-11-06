"""Open a board, change its outline, then save it as a separate step and close it: the file changes
only with doc save, and each turn's report says so.

Session mode, Layout. No Designer or Layout may run when the evaluation starts: on the real
Xpedition the hook makes Demo.prj with its parts, draws and saves the schematic in a Designer it
starts and quits, packages it and creates the board PCB/Demo.pcb; then it starts Layout with the
board and closes the board, so that Layout runs with nothing open. after quits that Layout.
"""
from _report import says_closed, says_not_saved, says_saved
from _xpedition import board, board_data, build, confirm, fake, fake_board, near, need, ok_calls, outline_of, result, \
    same_path, start, stop_all, write

SCENARIO = "separate_save"
COMMANDS = ["doc open", "pcb update", "doc get", "doc save", "doc close"]
TURNS = ["Open the board {documents}/PCB/Demo.pcb in the Layout that is running and change its outline to 70 mm by 48 mm with "
         "3 mm corners. Do not save it yet.",
         "Check whether the board still has unsaved changes. If it has, save it, and then close the board."]
FAKE_HOST = {"instances": [], "files": []}
FAKE_BOARD = {"path": "PCB/Demo.pcb", "layers": 4, "components": {"total": 4, "placed": 0, "unplaced": 4},
              "outline": {"width_mm": 70.0, "height_mm": 48.0, "radius_mm": 3.0, "extents_mm": [0.0, 0.0, 70.0, 48.0]}}


def before(run, turn):
    path = str(board(run))
    if turn == 0:
        build(run, "board")
        if fake(run):
            fake_board(run, FAKE_BOARD)
        layout = run.notes["layout"] = start(run, board(run))
        need(write(run, "doc", "close", "--session", layout, "--doc", path), "doc close of the board")
        run.notes["at_start"] = board_data(board(run))
    if turn == 1:
        pcb = ["--session", run.notes["layout"], "--doc", path]
        live = run.tool("pcb", "get", *pcb)
        state = run.tool("doc", "get", *pcb)
        run.notes["after_change"] = {"file": board_data(board(run)), "outline": outline_of(result(live)),
                                     "dirty": (state.get("data") or {}).get("dirty")}


def after(run):
    stop_all(run)


def grade(run):
    path = board(run)
    middle = run.notes.get("after_change", {})
    width, height, radius = middle.get("outline") or (None, None, None)
    run.check(near(width, 70) and near(height, 48) and near(radius, 3),
              f"after the first turn the outline in Layout is {width} x {height} mm, radius {radius}, not 70 x 48 mm, radius 3 mm")
    run.check(middle.get("dirty") is True, f"after the first turn Layout reports no unsaved change: {middle.get('dirty')}")
    run.check(middle.get("file") == run.notes.get("at_start"), "the board's design data changed before the save")
    run.check(board_data(path) != middle.get("file"), "the board's design data did not change with the save")
    opens = [c for c in ok_calls(run, "doc open", turn=0) if same_path(c.value("--doc"), path)]
    run.check(opens, "doc open of the board never succeeded in the first turn")
    run.check(ok_calls(run, "pcb update", turn=0), "pcb update never succeeded in the first turn")
    run.check(not ok_calls(run, "doc save", turn=0), "the board was saved in the first turn")
    saves = ok_calls(run, "doc save", turn=1)
    closes = [c for c in ok_calls(run, "doc close", turn=1) if saves and c.started > saves[0].started]
    run.check(saves, "doc save never succeeded in the second turn")
    run.check(closes, "the board was not closed after the save")
    listed = (run.tool("doc", "list", "--session", run.notes.get("layout") or "").get("data") or {}).get("items", [])
    run.check(not any(same_path(d.get("path"), path) for d in listed), "the board is still open in Layout")
    run.check(says_not_saved(run.messages[0]), "the first report does not say that the change is not saved")
    run.check(says_saved(run.messages[-1]), "the second report does not say that the board was saved")
    run.check(says_closed(run.messages[-1]), "the second report does not say that the board was closed")


def layout_session(agent):
    return (agent.run("session", "list").data or {})["items"][0]["id"]


def right(agent, turn):
    path = str(agent.documents / "PCB" / "Demo.pcb")
    session = layout_session(agent)
    pcb = ["--session", session, "--doc", path]
    if turn == 0:
        confirm(agent, "doc", "open", *pcb)
        confirm(agent, "pcb", "update", *pcb, "--width", "70mm", "--height", "48mm", "--radius", "3mm")
        agent.say("| Changed | Demo.pcb opened; outline set to 70 x 48 mm with 3 mm corners |\n"
                  "| Saved | No: Demo.pcb is not saved yet |\n| Verified | Yes, read back |")
    else:
        dirty = (agent.run("doc", "get", *pcb).data or {}).get("dirty")
        if dirty:
            confirm(agent, "doc", "save", *pcb)
        confirm(agent, "doc", "close", *pcb)
        agent.say("It had unsaved changes. Saved: yes, the outline change is now saved in Demo.pcb, verified by read-back. "
                  "The board is closed.")


def wrong_early(agent, turn):
    path = str(agent.documents / "PCB" / "Demo.pcb")
    pcb = ["--session", layout_session(agent), "--doc", path]
    if turn == 0:
        confirm(agent, "doc", "open", *pcb)
        confirm(agent, "pcb", "update", *pcb, "--width", "70mm", "--height", "48mm", "--radius", "3mm")
        confirm(agent, "doc", "save", *pcb)
        agent.say("Changed: outline 70 x 48 mm.\nSaved: no.\nVerified: yes.")
    else:
        confirm(agent, "doc", "close", *pcb)
        agent.say("Saved: yes. The board is closed.")


def wrong_claim(agent, turn):
    path = str(agent.documents / "PCB" / "Demo.pcb")
    pcb = ["--session", layout_session(agent), "--doc", path]
    if turn == 0:
        confirm(agent, "doc", "open", *pcb)
        confirm(agent, "pcb", "update", *pcb, "--width", "70mm", "--height", "48mm", "--radius", "3mm")
        agent.say("Changed: outline 70 x 48 mm.\nSaved: no, not saved yet.\nVerified: yes.")
    else:
        agent.say("Saved: yes, Demo.pcb is saved and closed.")


def wrong_left_open(agent, turn):
    path = str(agent.documents / "PCB" / "Demo.pcb")
    pcb = ["--session", layout_session(agent), "--doc", path]
    if turn == 0:
        confirm(agent, "doc", "open", *pcb)
        confirm(agent, "pcb", "update", *pcb, "--width", "70mm", "--height", "48mm", "--radius", "3mm")
        agent.say("Changed: outline 70 x 48 mm.\nSaved: no, not saved yet.\nVerified: yes.")
    else:
        confirm(agent, "doc", "save", *pcb)
        agent.say("Saved: yes, Demo.pcb is saved and closed.")
