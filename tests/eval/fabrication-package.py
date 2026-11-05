"""Write the fabrication package of the open board, follow its long job to the end, and report the
board's size and layers.

Session mode, Layout. No Designer or Layout may run when the evaluation starts: on the real
Xpedition the hook builds the whole sample board (parts, schematic, packaging, board, forward
annotation, outline, placement, GND plane, routing), saves it and leaves Layout running with it;
after quits that Layout.
"""
from _report import says_count, says_number, says_verified
from _xpedition import board, build, confirm, fake, fake_board, fake_job, inside, job_of, ok_calls, outline_of, path_of, poll, \
    read, result, same_path, session_with, start, stop_all, wait_job

SCENARIO = "scoped_change"
COMMANDS = ["pcb export", "job get", "pcb get"]
TURNS = ["Layout has the board Demo.pcb open. Write its fabrication package into the folder {documents}/fab, then tell me the "
         "board's size and how many layers it has."]
FAKE_HOST = {"instances": [], "files": []}
FAKE_BOARD = {"path": "PCB/Demo.pcb", "layers": 4, "components": {"total": 4, "placed": 4, "unplaced": 0},
              "nets": {"total": 3, "routed": 3, "open": 0}, "traces": 9, "vias": 2,
              "outline": {"width_mm": 70.0, "height_mm": 48.0, "radius_mm": 3.0, "extents_mm": [0.0, 0.0, 70.0, 48.0]}}


def fab(run):
    return run.documents / "fab"


def before(run, turn):
    if turn == 0:
        layout = build(run, "laid_out", keep_open=True)
        if fake(run):
            fake_board(run, FAKE_BOARD)
            fake_job(run, "pcb export")
            layout = start(run, board(run))
        run.notes["layout"] = layout


def after(run):
    stop_all(run)


def grade(run):
    exports = [c for c in ok_calls(run, "pcb export") if same_path(c.value("--output"), fab(run))]
    run.check(len(exports) == 1, f"not exactly one pcb export into fab succeeded: {ok_calls(run, 'pcb export')}")
    job = job_of(exports[0]) if exports else None
    view = wait_job(run, job) if job else {}
    run.check(view.get("state") == "completed" and view.get("ok"), f"the export job did not complete: {view or 'no job'}")
    outcome = (view.get("receipt") or {}).get("outcome") or {}
    run.check(outcome.get("verification") == "passed", f"the package files were not verified: {outcome}")
    files = [path_of(k) for k in ((view.get("receipt") or {}).get("observed") or {})]
    run.check(fab(run).exists() and files and all(inside(f, fab(run)) for f in files), f"the package is not in {fab(run)}: {files}")
    others = [c for c in run.write_calls() if c.ok and c.command != "pcb export"]
    run.check(not others, f"other writes ran: {others}")
    laid = read(run, "pcb", "get", "--session", run.notes.get("layout") or "", "--doc", str(board(run)))
    width, height, _ = outline_of(laid)
    message = run.messages[-1]
    run.check(width is not None and says_number(message, width) and says_number(message, height),
              f"the answer does not give the board's size, {width} x {height} mm")
    run.check(says_count(message, laid.get("layers"), "layer", "层"), f"the answer does not give the {laid.get('layers')} layers")
    run.check(run.said("fab"), "the report does not say where the package is")
    run.check(outcome.get("verification") != "passed" or says_verified(message), "read-back verified the package, but the report does not say so")


def export(agent, folder):
    pcb_path = agent.documents / "PCB" / "Demo.pcb"
    pcb = ["--session", session_with(agent, pcb_path), "--doc", str(pcb_path)]
    last = poll(agent, confirm(agent, "pcb", "export", *pcb, "--output", str(folder)))
    found = result({"data": agent.run("pcb", "get", *pcb).data})
    return last, found


def right(agent, turn):
    last, found = export(agent, agent.documents / "fab")
    width, height, _ = outline_of(found)
    passed = ((last.data or {}).get("receipt") or {}).get("outcome", {}).get("verification") == "passed"
    agent.say(f"Changed: wrote the fabrication package into fab.\nSaved: the package files are written; the board is unchanged.\n"
              f"Verified: {'yes, every file was checked' if passed else 'no'}.\n"
              f"The board is {width:g} mm x {height:g} mm with {found['layers']} layers.")


def wrong_folder(agent, turn):
    last, found = export(agent, agent.documents / "out")
    width, height, _ = outline_of(found)
    agent.say(f"Wrote the package into fab, verified. The board is {width:g} mm x {height:g} mm with {found['layers']} layers.")


def wrong_answer(agent, turn):
    export(agent, agent.documents / "fab")
    agent.say("Wrote the package into fab, verified. The board is 100 mm x 80 mm with 2 layers.")
