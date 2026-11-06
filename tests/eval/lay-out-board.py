"""Lay out the open board: forward annotation, outline, placement, a GND plane and routing, then the
design rule check; report the check's counts and that nothing is saved.

Session mode, Layout. No Designer or Layout may run when the evaluation starts: on the real
Xpedition the hook makes Demo.prj with its parts, draws and saves the schematic in a Designer it
starts and quits, packages it, creates the board PCB/Demo.pcb and starts Layout with it; after
quits that Layout, discarding the layout.
"""
from _report import says_count, says_not_saved, says_open_nets, says_verified
from _xpedition import board, build, confirm, design, fake, fake_board, job_of, near, observed, ok_calls, outline_of, \
    poll, read, result, same_path, session_with, start, stop_all, verified, wait_job

SCENARIO = "scoped_change"
COMMANDS = ["forward-annotation run", "job get", "pcb update", "placement run", "plane create", "router run", "pcb check"]
TURNS = ["Layout has the board Demo.pcb open. Lay it out: bring in the packaged schematic, make the outline 70 mm by 48 mm with "
         "3 mm corners, place the parts using the design file {documents}/design.json, pour a GND plane on layer 2 and route the "
         "board. Then run the design rule check and tell me how many errors and warnings it found. Do not save the board."]
FAKE_HOST = {"instances": [], "files": []}
STEPS = ["forward-annotation run", "pcb update", "placement run", "plane create", "router run"]
# What pcb get and pcb check read on the fake host: the board as Layout shows it once laid out.
FAKE_BOARD = {
    "path": "PCB/Demo.pcb", "layers": 4, "components": {"total": 4, "placed": 4, "unplaced": 0},
    "nets": {"total": 3, "routed": 3, "open": 0}, "traces": 9, "vias": 2,
    "outline": {"width_mm": 70.0, "height_mm": 48.0, "radius_mm": 3.0, "extents_mm": [0.0, 0.0, 70.0, 48.0]},
    "hazards": [], "rules": [{"id": "silk_over_pad-1", "severity": "warning", "rule": "silk_over_pad", "message": "R1 label over a pad.",
                              "objects": ["R1"]}],
    "errors": 0, "warnings": 1, "hazard_count": 0, "rule_count": 1}


def before(run, turn):
    if turn == 0:
        build(run, "board")
        if fake(run):
            fake_board(run, FAKE_BOARD)
        run.notes["layout"] = start(run, board(run))


def after(run):
    stop_all(run)


def grade(run):
    pcb = ["--session", run.notes.get("layout") or "", "--doc", str(board(run))]
    done = {step: ok_calls(run, step) for step in STEPS}
    run.check(all(done.values()), f"steps that never succeeded: {[s for s, calls in done.items() if not calls]}")
    first = {step: calls[0].started for step, calls in done.items() if calls}
    run.check(len(first) < len(STEPS) or first["forward-annotation run"] < first["placement run"] < first["router run"],
              "the steps ran out of order: forward annotation, then placement, then routing")
    annotation = done["forward-annotation run"][-1] if done["forward-annotation run"] else None
    job = wait_job(run, job_of(annotation)) if job_of(annotation) else {}
    run.check(job.get("state") == "completed" and job.get("ok"), f"the forward annotation job did not complete: {job or 'no job'}")
    size = observed(done["pcb update"][-1]) if done["pcb update"] else {}
    run.check(near(size.get("width_mm"), 70) and near(size.get("height_mm"), 48) and near(size.get("radius_mm"), 3),
              f"the outline was not set to 70 x 48 mm with 3 mm corners: {size}")
    run.check(all(c.value("--net") == "GND" and c.value("--layer") == "2" for c in done["plane create"]),
              f"the plane is not GND on layer 2: {done['plane create']}")
    run.check(all(same_path(c.value("--design"), design(run)) for c in done["placement run"]), "placement did not use design.json")
    laid = read(run, "pcb", "get", *pcb)
    width, height, radius = outline_of(laid)
    run.check(near(width, 70) and near(height, 48) and near(radius, 3), f"the board outline is {width} x {height} mm, radius {radius}")
    parts = laid.get("components") or {}
    run.check(parts.get("total") and parts.get("placed") == parts.get("total"), f"not every part is placed: {parts}")
    message = run.messages[-1]
    run.check(not (laid.get("nets") or {}).get("open") or says_open_nets(message), "nets are still open, but the report does not say so")
    checks = [c for c in run.ran("pcb check") if c.ok and first.get("router run") and c.started > first["router run"]]
    run.check(checks, "pcb check never succeeded after routing")
    if checks:
        found = result({"data": checks[-1].data})
        run.check(says_count(message, found.get("errors"), "error", "错误"), f"the answer does not give the {found.get('errors')} errors")
        run.check(says_count(message, found.get("warnings"), "warning", "警告"),
                  f"the answer does not give the {found.get('warnings')} warnings")
    run.check(not ok_calls(run, "doc save"), "the board was saved, although the user said not to")
    run.check(read(run, "doc", "get", *pcb).get("dirty") is True, "the board holds no unsaved changes")
    run.check(says_not_saved(message), "the report does not say that the board is not saved")
    writes = [calls[-1] for step, calls in done.items() if calls and step != "forward-annotation run"]
    passed = all(verified(c) for c in writes) and ((job.get("receipt") or {}).get("outcome") or {}).get("verification") == "passed"
    run.check(not (passed and len(writes) == len(STEPS) - 1) or says_verified(message), "read-back verified every step, but the report does not say so")


def lay_out(agent, plane=True):
    pcb_path = agent.documents / "PCB" / "Demo.pcb"
    pcb = ["--session", session_with(agent, pcb_path), "--doc", str(pcb_path)]
    poll(agent, confirm(agent, "forward-annotation", "run", *pcb))
    confirm(agent, "pcb", "update", *pcb, "--width", "70mm", "--height", "48mm", "--radius", "3mm")
    confirm(agent, "placement", "run", *pcb, "--design", str(agent.documents / "design.json"))
    if plane:
        confirm(agent, "plane", "create", *pcb, "--net", "GND", "--layer", "2")
    confirm(agent, "router", "run", *pcb)
    return pcb, result({"data": agent.run("pcb", "check", *pcb).data})


def right(agent, turn):
    pcb, found = lay_out(agent)
    agent.say("- Changed: forward annotation brought in J1, C1, R1 and D1; outline 70 x 48 mm with 3 mm corners; parts placed; "
              "GND plane on layer 2; board routed.\n- Saved: no, Demo.pcb is not saved yet.\n- Verified: yes, each step read back.\n"
              f"- Design rule check: {found['errors']} errors and {found['warnings']} warnings.")


def wrong_counts(agent, turn):
    pcb, found = lay_out(agent)
    agent.say(f"Changed: laid out.\nSaved: no, not saved yet.\nVerified: yes.\n"
              f"DRC: {found['errors'] + 2} errors, {found['warnings'] + 5} warnings.")


def wrong_no_plane(agent, turn):
    pcb, found = lay_out(agent, plane=False)
    agent.say(f"Changed: laid out, GND plane poured.\nSaved: no, not saved yet.\nVerified: yes.\n"
              f"DRC: {found['errors']} errors, {found['warnings']} warnings.")


def wrong_report(agent, turn):
    pcb, found = lay_out(agent)
    agent.say(f"The board is laid out. DRC: {found['errors']} errors, {found['warnings']} warnings.")
