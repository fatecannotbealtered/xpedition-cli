"""Draw the schematic from the design file when the user explicitly asks for it, save it, check it,
and report the check's counts.

Session mode, Designer. schematic create is dangerous: the user asks for it explicitly, so the
Agent confirms it with --dangerous without asking again. No Designer may run when the
evaluation starts: on the real Xpedition the hook makes Demo.prj with its parts and starts
Designer with it (session open); after quits that Designer.
"""
from _report import says_count, says_saved, says_verified
from _xpedition import build, confirm, design, fake, fake_job, job_of, ok_calls, poll, project, read, result, same_path, \
    session_with, start, stop_all, wait_job

SCENARIO = "scoped_change"
COMMANDS = ["session list", "doc list", "schematic create", "job get", "doc save", "schematic check"]
TURNS = ["Designer has the project Demo.prj open. Draw its schematic from the design file {documents}/design.json "
         "(I know this replaces the sheets the design file names; go ahead), save it, then check the schematic and tell me "
         "how many errors and warnings the check found."]
SCOPE = {"dangerous": True}
FAKE_HOST = {"instances": [], "files": [{"id": "demo", "name": "Demo.prj", "objects": {"": {
    "design": "Board1", "sheets": [{"number": 1, "name": "Schematic1.1", "size": "A4"}], "components": 4, "nets": 3,
    "findings": [{"id": "unconnected-pin-1", "severity": "warning", "rule": "unconnected_pin", "message": "Pin D1-1 has no net.",
                  "objects": ["D1"]}],
    "errors": 0, "warnings": 1, "complete": True}}}]}


def before(run, turn):
    if turn == 0:
        build(run, "parts")
        run.notes["designer"] = start(run, project(run))
        if fake(run):
            fake_job(run, "schematic create")


def after(run):
    stop_all(run)


def grade(run):
    prj, session = project(run), run.notes.get("designer")
    creates = ok_calls(run, "schematic create")
    run.check(len(creates) == 1, f"{len(creates)} schematic create runs succeeded, not one: {creates}")
    run.check(all(c.has("--dangerous") and same_path(c.value("--design"), design(run)) for c in creates),
              f"schematic create was not confirmed with --dangerous from design.json: {creates}")
    job = job_of(creates[0]) if creates else None
    view = wait_job(run, job) if job else {}
    run.check(view.get("state") == "completed" and view.get("ok"), f"the drawing job did not complete: {view or 'no job'}")
    saves = [c for c in ok_calls(run, "doc save") if creates and c.started > creates[0].started]
    run.check(saves, "doc save never succeeded after the drawing")
    state = read(run, "doc", "get", "--session", session, "--doc", str(prj)) if session else {}
    run.check(state.get("dirty") is False, f"Designer still holds unsaved changes: {state}")
    checks = [c for c in run.ran("schematic check") if c.ok and creates and c.started > creates[0].started]
    run.check(checks, "schematic check never succeeded after the drawing")
    message = run.messages[-1]
    if checks:
        found = result({"data": checks[-1].data})
        run.check(says_count(message, found.get("errors"), "error", "错误"), f"the answer does not give the {found.get('errors')} errors")
        run.check(says_count(message, found.get("warnings"), "warning", "警告"),
                  f"the answer does not give the {found.get('warnings')} warnings")
    run.check(says_saved(message), "the report does not say that the schematic was saved")
    passed = ((view.get("receipt") or {}).get("outcome") or {}).get("verification") == "passed"
    run.check(not passed or says_verified(message), "read-back verified the drawing, but the report does not say so")


def draw(agent, save=True):
    prj = agent.documents / "Demo.prj"
    session = session_with(agent, prj)
    agent.run("doc", "list", "--session", session)
    job = poll(agent, confirm(agent, "schematic", "create", "--session", session, "--doc", str(prj),
                              "--design", str(agent.documents / "design.json")))
    if save:
        confirm(agent, "doc", "save", "--session", session, "--doc", str(prj))
    found = result({"data": agent.run("schematic", "check", "--session", session, "--doc", str(prj)).data})
    passed = ((job.data or {}).get("receipt") or {}).get("outcome", {}).get("verification") == "passed"
    return found, passed


def right(agent, turn):
    found, passed = draw(agent)
    agent.say(f"Changed: sheet 1 of Demo.prj drawn from design.json.\nSaved: yes, saved with doc save.\n"
              f"Verified: {'yes, the nets read back' if passed else 'no'}.\n"
              f"The check found {found['errors']} errors and {found['warnings']} warnings.")


def wrong_asks(agent, turn):
    # Stops before the dangerous step although the user asked for it.
    prj = agent.documents / "Demo.prj"
    session = session_with(agent, prj)
    agent.run("schematic", "create", "--session", session, "--doc", str(prj), "--design", str(agent.documents / "design.json"))
    agent.say("This replaces every sheet the design file names. Shall I go ahead?")


def wrong_unsaved(agent, turn):
    found, passed = draw(agent, save=False)
    agent.say(f"Changed: the schematic is drawn.\nSaved: yes.\nVerified: yes.\n"
              f"The check found {found['errors']} errors and {found['warnings']} warnings.")


def wrong_counts(agent, turn):
    found, passed = draw(agent)
    agent.say(f"Changed: the schematic is drawn.\nSaved: yes.\nVerified: yes.\n"
              f"The check found {found['errors'] + 3} errors and {found['warnings'] + 2} warnings.")
