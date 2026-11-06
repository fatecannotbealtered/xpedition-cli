"""Answer questions about a project and its central library by reading them, and change nothing.

File mode; no Designer or Layout runs. On the real Xpedition the hook makes Demo.prj from the
template project and adds the four parts of tests/inputs/parts.json with part create.
The grader compares the answer with what the tool reads at the end.
"""
from _xpedition import PART_NUMBERS, build, project, read, result

SCENARIO = "read_answer"
COMMANDS = ["project get", "part list", "part get"]
TURNS = ["Look at the project {documents}/Demo.prj without changing anything: which of its designs is the board design, "
         "which parts does its central library hold, and which footprint cell does part DEMO-RES-1K-0603 use?"]
FAKE_HOST = {"instances": [], "files": [{"id": "demo", "name": "Demo.prj", "kind": "project", "objects": {
    "": {"path": "Demo.prj", "xpedition_version": "2604", "central_library": {"path": "Library/Demo.lmc", "exists": True},
         "designs": [{"name": "Board1", "kind": "board", "board": "", "board_exists": False, "layout_template": ""},
                     {"name": "Schematic1", "kind": "schematic", "board": "", "board_exists": False, "layout_template": ""}],
         "board": "", "symbol_libraries": ["SymbolLibs\\PartQuest"], "parts_databases": ["PartsDBLibs\\PartQuest.pdb"],
         "cell_libraries": ["CellDBLibs\\PartQuest.cel"], "ascii_path": True},
    "DEMO-CONN-2": {"partition": "PartQuest", "description": "Demo connector, 1x2 header, 2.54 mm pitch", "prefix": "J",
                    "cell": {"name": "HDRV2W64P254_1X2_508X254X850P"}, "findings": []},
    "DEMO-RES-1K-0603": {"partition": "PartQuest", "description": "Demo resistor, 1 kOhm, 0603", "prefix": "R", "value": "1K",
                         "cell": {"name": "RESC1608X50N"}, "findings": []},
    "DEMO-LED-0603": {"partition": "PartQuest", "description": "Demo LED, green, 0603", "prefix": "D",
                      "cell": {"name": "LEDC1608X50N"}, "findings": []},
    "DEMO-CAP-100N-0603": {"partition": "PartQuest", "description": "Demo capacitor, 100 nF, 0603", "prefix": "C",
                           "value": "100n", "cell": {"name": "CAPC1608X50N"}, "findings": []}}}]}


def before(run, turn):
    if turn == 0:
        build(run, "parts")


def grade(run):
    prj = str(project(run))
    designs = read(run, "project", "get", "--doc", prj).get("designs") or []
    boards = [d.get("name") for d in designs if d.get("kind") == "board"]
    run.check(boards and run.said(boards[0]), f"the answer does not name the board design {boards[:1]}")
    # On Xpedition the library may hold parts of the template too; a short list is expected in full.
    # (The fake host's generic answer lists no part numbers.)
    listed = [i.get("id") for i in read(run, "part", "list", "--doc", prj, "--limit", "100").get("items", [])]
    numbers = list(dict.fromkeys(PART_NUMBERS + ([n for n in listed if isinstance(n, str)] if len(listed) <= 20 else [])))
    missing = [n for n in numbers if not run.said(n)]
    run.check(not missing, f"the answer leaves out the parts {missing}")
    cell = (read(run, "part", "get", "--doc", prj, "--id", "DEMO-RES-1K-0603").get("cell") or {}).get("name")
    run.check(cell and run.said(cell), f"the answer does not give the footprint cell of DEMO-RES-1K-0603, {cell}")
    run.check(not run.write_calls(), f"a write command ran: {run.write_calls()}")


def right(agent, turn):
    prj = str(agent.documents / "Demo.prj")
    designs = result({"data": agent.run("project", "get", "--doc", prj).data}).get("designs", [])
    board = next(d["name"] for d in designs if d["kind"] == "board")
    listed = [i.get("id") for i in (agent.run("part", "list", "--doc", prj).data or {}).get("items", [])]
    numbers = [n for n in listed if isinstance(n, str)] or PART_NUMBERS
    cell = result({"data": agent.run("part", "get", "--doc", prj, "--id", "DEMO-RES-1K-0603").data})["cell"]["name"]
    agent.say(f"The board design is {board}. The central library holds {', '.join(numbers)}. "
              f"DEMO-RES-1K-0603 uses the footprint cell {cell}. Nothing was changed.")


def wrong_guess(agent, turn):
    agent.say("The board design is Board1. The library holds DEMO-RES-1K-0603 and DEMO-CAP-100N-0603; "
              "the resistor uses the cell RESC1005X40N.")


def wrong_write(agent, turn):
    # Adds the parts again "to make sure they are there" before answering.
    prj = str(agent.documents / "Demo.prj")
    agent.run("part", "create", "--doc", prj, "--file", str(agent.documents / "parts.json"))
    cell = result({"data": agent.run("part", "get", "--doc", prj, "--id", "DEMO-RES-1K-0603").data})["cell"]["name"]
    agent.say(f"The board design is Board1. The central library holds {', '.join(PART_NUMBERS)}. "
              f"DEMO-RES-1K-0603 uses the footprint cell {cell}.")
