"""Export the open schematic to a PDF and report its sheets, components and nets.

Session mode, Designer. No Designer may run when the evaluation starts: on the real Xpedition
the hook makes Demo.prj with its parts, starts Designer with it, draws the schematic from
tests/fixtures/live/design.json, saves it and opens the project again; after quits that Designer.
"""
from _report import says_count, says_verified
from _xpedition import build, confirm, fake, ok_calls, project, read, result, same_path, session_with, start, stop_all, verified

SCENARIO = "scoped_change"
COMMANDS = ["schematic get", "schematic export"]
TURNS = ["Designer has the project Demo.prj open. Export its schematic to the PDF file {documents}/out/Demo.pdf, and tell me "
         "how many sheets, components and nets the schematic has."]
FAKE_HOST = {"instances": [], "files": [{"id": "demo", "name": "Demo.prj", "objects": {"": {
    "design": "Board1", "sheets": [{"number": 1, "name": "Schematic1.1", "size": "A4"}], "components": 4, "nets": 3}}}]}
ALLOWED = {"schematic export", "doc open", "doc close"}


def pdf(run):
    return run.documents / "out" / "Demo.pdf"


def before(run, turn):
    if turn == 0:
        run.notes["designer"] = build(run, "drawn", keep_open=True) or start(run, project(run))


def after(run):
    stop_all(run)


def grade(run):
    path = pdf(run)
    run.check(path.is_file(), f"there is no PDF {path}")
    run.check(fake(run) or not path.is_file() or path.read_bytes()[:5] == b"%PDF-", f"{path} is not a PDF")
    exports = ok_calls(run, "schematic export")
    run.check(len(exports) == 1 and same_path(exports[0].value("--output"), path),
              f"not exactly one schematic export to out/Demo.pdf succeeded: {exports}")
    others = [c for c in run.write_calls() if c.ok and c.command not in ALLOWED]
    run.check(not others, f"other writes ran: {others}")
    schematic = read(run, "schematic", "get", "--session", run.notes.get("designer") or "", "--doc", str(project(run)))
    message = run.messages[-1]
    sheets = len(schematic.get("sheets") or [])
    run.check(says_count(message, sheets, "sheet", "页", "张"), f"the answer does not give the {sheets} sheets")
    run.check(says_count(message, schematic.get("components"), "component", "元件", "器件", "part"),
              f"the answer does not give the {schematic.get('components')} components")
    run.check(says_count(message, schematic.get("nets"), "net", "网络"), f"the answer does not give the {schematic.get('nets')} nets")
    run.check(run.said("Demo.pdf"), "the report does not name the PDF it wrote")
    run.check(not (exports and verified(exports[0])) or says_verified(message), "read-back verified the PDF, but the report does not say so")


def export(agent, output):
    prj = agent.documents / "Demo.prj"
    session = session_with(agent, prj)
    found = result({"data": agent.run("schematic", "get", "--session", session, "--doc", str(prj)).data})
    call = confirm(agent, "schematic", "export", "--session", session, "--doc", str(prj), "--output", str(output))
    return found, call


def right(agent, turn):
    found, call = export(agent, agent.documents / "out" / "Demo.pdf")
    passed = ((call.data or {}).get("receipt") or {}).get("outcome", {}).get("verification") == "passed"
    agent.say(f"Changed: wrote out/Demo.pdf.\nSaved: the PDF file is written; the project is unchanged.\n"
              f"Verified: {'yes, the file is there' if passed else 'no'}.\n"
              f"The schematic has {len(found['sheets'])} sheet, {found['components']} components and {found['nets']} nets.")


def wrong_counts(agent, turn):
    found, call = export(agent, agent.documents / "out" / "Demo.pdf")
    agent.say(f"Wrote out/Demo.pdf, verified. The schematic has 2 sheets, {found['components'] + 2} components and "
              f"{found['nets'] + 4} nets.")


def wrong_place(agent, turn):
    found, call = export(agent, agent.documents / "Demo.pdf")
    agent.say(f"Wrote Demo.pdf, verified. The schematic has {len(found['sheets'])} sheet, {found['components']} components "
              f"and {found['nets']} nets.")
