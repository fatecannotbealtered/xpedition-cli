"""Start Layout with a board, report the board's layers and outline, then quit that Layout.

Session mode, Layout, started by the Agent. No Designer or Layout may run when the evaluation
starts: on the real Xpedition the hook makes Demo.prj with its parts, draws and saves the
schematic in a Designer it starts and quits, packages it and creates the board PCB/Demo.pcb.
after quits the Layout the Agent started if it still runs.
"""
from _report import says_closed, says_count, says_number
from _xpedition import board, build, ended, fake, fake_board, lifecycle, ok_calls, outline_of, result, same_path, stop_all, write

SCENARIO = "scoped_change"
COMMANDS = ["session open", "session get", "pcb get", "session close"]
TURNS = ["Start Layout with the board {documents}/PCB/Demo.pcb, tell me how many layers the board has and how big its "
         "outline is, then quit that Layout."]
FAKE_HOST = {"instances": [], "files": []}
FAKE_BOARD = {"path": "PCB/Demo.pcb", "layers": 4, "components": {"total": 0, "placed": 0, "unplaced": 0},
              "nets": {"total": 0, "routed": None, "open": None}, "traces": 0, "vias": 0,
              "outline": {"width_mm": 100.0, "height_mm": 80.0, "radius_mm": 0.0, "extents_mm": [0.0, 0.0, 100.0, 80.0]}}
LIFECYCLE = {"session open", "session close"}


def before(run, turn):
    if turn == 0:
        build(run, "board")
        if fake(run):
            fake_board(run, FAKE_BOARD)


def started(run):
    """The sessions the Agent started; session open is a long job, whose result names the session."""
    return [ended(run, c).get("id") for c in ok_calls(run, "session open")]


def after(run):
    stop_all(run)
    for session in started(run):
        if session:
            try:
                write(run, "session", "close", "--session", session, "--discard")
            except Exception:  # noqa: BLE001 - after must go on
                pass


def grade(run):
    path = board(run)
    opens = [c for c in ok_calls(run, "session open") if same_path(c.value("--doc"), path)]
    run.check(len(opens) == 1, f"not exactly one session open with the board succeeded: {opens}")
    session = ended(run, opens[0]).get("id") if opens else None
    reads = [c for c in run.ran("pcb get") if c.ok and opens and c.started > opens[0].started]
    run.check(reads, "pcb get never succeeded on the new Layout")
    message = run.messages[-1]
    if reads:
        found = result({"data": reads[-1].data})
        width, height, _ = outline_of(found)
        run.check(says_count(message, found.get("layers"), "layer", "层"), f"the answer does not give the {found.get('layers')} layers")
        run.check(width is not None and says_number(message, width) and says_number(message, height),
                  f"the answer does not give the outline, {width} x {height} mm")
    closes = [c for c in ok_calls(run, "session close") if c.value("--session") == session and reads and c.started > reads[-1].started]
    run.check(closes, f"the Layout the Agent started ({session}) was not closed after reading the board")
    listed = (run.tool("session", "list").get("data") or {}).get("items", [])
    run.check(not any(s.get("id") == session for s in listed), f"the session {session} still runs")
    others = [c for c in run.write_calls() if c.ok and c.command not in LIFECYCLE]
    run.check(not others, f"other writes ran: {others}")
    run.check(says_closed(message), "the report does not say that Layout was closed")


def open_and_read(agent):
    opened = lifecycle(agent, "session", "open", "--doc", str(agent.documents / "PCB" / "Demo.pcb"))
    session = opened.data["result"]["id"]
    agent.run("session", "get", "--session", session)
    found = result({"data": agent.run("pcb", "get", "--session", session, "--doc", str(agent.documents / "PCB" / "Demo.pcb")).data})
    return session, found


def right(agent, turn):
    session, found = open_and_read(agent)
    lifecycle(agent, "session", "close", "--session", session)
    width, height, _ = outline_of(found)
    agent.say(f"Demo.pcb has {found['layers']} layers, and its outline is {width:g} mm x {height:g} mm. "
              f"I closed the Layout I started ({session}); nothing was changed or saved.")


def wrong_left_running(agent, turn):
    session, found = open_and_read(agent)
    width, height, _ = outline_of(found)
    agent.say(f"Demo.pcb has {found['layers']} layers, and its outline is {width:g} mm x {height:g} mm. Layout is closed.")


def wrong_answer(agent, turn):
    session, found = open_and_read(agent)
    lifecycle(agent, "session", "close", "--session", session)
    agent.say("Demo.pcb has 2 layers, and its outline is 50 mm x 50 mm. I closed Layout.")
