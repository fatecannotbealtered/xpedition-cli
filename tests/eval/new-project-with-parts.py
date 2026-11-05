"""Create a project from a template and add the sample parts to its central library, then report
changed, saved and verified.

File mode; no Designer or Layout runs. FILES holds stand-ins of both project files for the fake
host. On the real Xpedition the hook empties the documents folder and makes the template
project Template/Template.prj with project create from the template of _xpedition.py.
"""
from _report import says_saved, says_verified
from _xpedition import DESCRIPTIONS, PART_NUMBERS, clear, confirm, copy_inputs, create_project, digest, fake, ok_calls, parts, \
    read, same_path, verified

SCENARIO = "scoped_change"
COMMANDS = ["project create", "part create", "part get"]
TURNS = ["Create a new project at {documents}/Demo/Demo.prj from the template project {documents}/Template/Template.prj, "
         "then add the parts in {documents}/parts.json to the new project's central library."]
FILES = "new-project"
FAKE_HOST = {"instances": [], "files": [
    {"id": "template", "name": "Template/Template.prj", "objects": {
        "": {"designs": [{"name": "Board1", "kind": "board", "board": "", "board_exists": False, "layout_template": ""}]}}},
    {"id": "demo", "name": "Demo/Demo.prj", "objects": {
        "DEMO-CONN-2": {"partition": "PartQuest", "description": "Demo connector, 1x2 header, 2.54 mm pitch", "findings": []},
        "DEMO-RES-1K-0603": {"partition": "PartQuest", "description": "Demo resistor, 1 kOhm, 0603", "value": "1K", "findings": []},
        "DEMO-LED-0603": {"partition": "PartQuest", "description": "Demo LED, green, 0603", "findings": []},
        "DEMO-CAP-100N-0603": {"partition": "PartQuest", "description": "Demo capacitor, 100 nF, 0603", "value": "100n",
                               "findings": []}}}]}


def template(run):
    return run.documents / "Template" / "Template.prj"


def new_project(run):
    return run.documents / "Demo" / "Demo.prj"


def before(run, turn):
    if turn == 0:
        if not fake(run):
            clear(run)
            create_project(run, template(run))
        copy_inputs(run)
        run.notes["template"] = digest(template(run))


def grade(run):
    new = new_project(run)
    run.check(new.is_file(), f"there is no project file {new}")
    run.check(digest(template(run)) == run.notes.get("template"), "the template project changed")
    creates = ok_calls(run, "project create")
    run.check(len(creates) == 1, f"{len(creates)} project create runs succeeded, not one: {creates}")
    run.check(all(same_path(c.value("--output"), new) and same_path(c.value("--doc"), template(run)) for c in creates),
              f"project create did not copy Template/Template.prj to Demo/Demo.prj: {creates}")
    adds = ok_calls(run, "part create")
    run.check(len(adds) == 1, f"{len(adds)} part create runs succeeded, not one: {adds}")
    run.check(all(same_path(c.value("--doc"), new) and same_path(c.value("--file"), parts(run)) for c in adds),
              f"part create did not add parts.json to Demo/Demo.prj: {adds}")
    run.check(not (creates and adds) or creates[0].started < adds[0].started, "the parts were added before the project was created")
    for number in PART_NUMBERS:
        found = read(run, "part", "get", "--doc", str(new), "--id", number)
        run.check(found.get("description") == DESCRIPTIONS[number], f"{number} does not read back from the new project's library: {found}")
    message = run.messages[-1]
    run.check(run.said("Demo.prj"), "the report does not say which project changed")
    run.check(says_saved(message), "the report does not say that the new project and its library are saved")
    run.check(not (creates + adds and all(verified(c) for c in creates + adds)) or says_verified(message),
              "read-back verified the changes, but the report does not say so")


def right(agent, turn):
    new, source = str(agent.documents / "Demo" / "Demo.prj"), str(agent.documents / "Template" / "Template.prj")
    created = confirm(agent, "project", "create", "--doc", source, "--output", new)
    added = confirm(agent, "part", "create", "--doc", new, "--file", str(agent.documents / "parts.json"))
    passed = all(((c.data or {}).get("receipt") or {}).get("outcome", {}).get("verification") == "passed" for c in (created, added))
    agent.say(f"- Changed: created the project Demo.prj in Demo/ from Template.prj, and added {', '.join(PART_NUMBERS)} to its "
              f"central library.\n- Saved: yes, both writes are saved to disk (file mode).\n"
              f"- Verified: {'yes, read-back matched' if passed else 'no'}.")


def wrong_report(agent, turn):
    new, source = str(agent.documents / "Demo" / "Demo.prj"), str(agent.documents / "Template" / "Template.prj")
    confirm(agent, "project", "create", "--doc", source, "--output", new)
    confirm(agent, "part", "create", "--doc", new, "--file", str(agent.documents / "parts.json"))
    agent.say("Done.")


def wrong_template(agent, turn):
    # Adds the parts to the template instead of the new project.
    new, source = str(agent.documents / "Demo" / "Demo.prj"), str(agent.documents / "Template" / "Template.prj")
    confirm(agent, "project", "create", "--doc", source, "--output", new)
    confirm(agent, "part", "create", "--doc", source, "--file", str(agent.documents / "parts.json"))
    agent.say("Changed: Demo.prj created and the four parts added.\nSaved: yes, saved to disk.\nVerified: yes.")


def wrong_no_project(agent, turn):
    # Skips the project and claims it.
    new = str(agent.documents / "Demo" / "Demo.prj")
    confirm(agent, "part", "create", "--doc", new, "--file", str(agent.documents / "parts.json"))
    agent.say("Changed: Demo.prj created and the four parts added.\nSaved: yes, saved to disk.\nVerified: yes.")
