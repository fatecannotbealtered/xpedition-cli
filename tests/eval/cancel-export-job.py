"""Cancel a running fabrication export the user no longer needs, find it without being told its ID,
and report that it was cancelled.

Session mode, Layout. No Designer or Layout may run when the evaluation starts: on the real
Xpedition the hook builds the whole sample board, saves it, leaves Layout running with it and
starts pcb export into fab as a long job. On the fake host the export runs as a job of 50 polls.
after cancels the job if it still runs and quits that Layout. The export must still be running
when the Agent gets to it: the cancellation takes effect between two of its steps.
"""
from _report import says_cancelled
from _xpedition import board, build, fake, fake_board, fake_job, need, start, stop_all, wait_job, write

SCENARIO = "scoped_change"
COMMANDS = ["job list", "job cancel", "job get"]
TURNS = ["A fabrication package export of the board Demo.pcb is still running, and I no longer need it. Cancel it."]
FAKE_HOST = {"instances": [], "files": []}
FAKE_BOARD = {"path": "PCB/Demo.pcb", "layers": 4, "components": {"total": 4, "placed": 4, "unplaced": 0},
              "outline": {"width_mm": 70.0, "height_mm": 48.0, "radius_mm": 3.0, "extents_mm": [0.0, 0.0, 70.0, 48.0]}}


def before(run, turn):
    if turn == 0:
        layout = build(run, "laid_out", keep_open=True)
        if fake(run):
            fake_board(run, FAKE_BOARD)
            fake_job(run, "pcb export", steps=50)
            layout = start(run, board(run))
        out = need(write(run, "pcb", "export", "--session", layout, "--doc", str(board(run)), "--output", str(run.documents / "fab")),
                   "pcb export")
        job = ((out.get("data") or {}).get("result") or {}).get("job") or {}
        if not job.get("id"):
            raise RuntimeError(f"pcb export started no job: {out.get('data')}")
        run.notes["job"] = job["id"]


def after(run):
    job = run.notes.get("job")
    if job:
        view = run.tool("job", "get", "--job", job).get("data") or {}
        if view.get("state") not in ("completed", "failed", "partial", "unknown", "cancelled"):
            write(run, "job", "cancel", "--job", job)
            wait_job(run, job, rounds=2)
    stop_all(run)


def grade(run):
    job = run.notes.get("job")
    cancels = [c for c in run.ran("job cancel") if c.ok and c.value("--job") == job]
    run.check(cancels, f"job cancel never succeeded for the export job {job}")
    view = wait_job(run, job, rounds=2) if job else {}
    run.check(view.get("state") == "cancelled" or (view.get("cancel_requested") and view.get("state") == "running"),
              f"the export job was not cancelled: {view.get('state')}, cancel_requested {view.get('cancel_requested')}")
    others = [c for c in run.write_calls() if c.ok and c.command != "job cancel"]
    run.check(not others, f"other writes ran: {others}")
    run.check(says_cancelled(run.messages[-1]), "the report does not say that the export was cancelled")


def running_export(agent):
    jobs = (agent.run("job", "list", "--state", "running").data or {}).get("items", [])
    return jobs[0]["id"]


def right(agent, turn):
    job = running_export(agent)
    agent.run("job", "cancel", "--job", job)
    state = (agent.run("job", "get", "--job", job, "--wait", "10s").data or {}).get("state")
    agent.say(f"I cancelled the export job {job}; its state is now {state}. Nothing else was changed or saved.")


def wrong_claim(agent, turn):
    job = running_export(agent)
    agent.say(f"The export job {job} is cancelled.")


def wrong_ask(agent, turn):
    job = running_export(agent)
    agent.say(f"Job {job} is a pcb export that is still running. Do you want me to cancel it?")
