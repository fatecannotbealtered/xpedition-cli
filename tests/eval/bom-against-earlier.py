"""Check the bill of materials, write it to a file and say what changed since an earlier BOM.

Session mode, Designer. No Designer or Layout may run when the evaluation starts: on the real
Xpedition the hook makes Demo.prj up to the drawn and saved schematic, leaves Designer open with it
and writes an earlier BOM, old-bom.csv, in the documents folder: C1 there is another capacitor, R1
has another value, R2 is gone since, and D1 is new. after quits that Designer. The fake host holds
no schematic.
"""
import csv

from _report import says_verified
from _xpedition import ask, build, confirm, finally_verified, ok_calls, session_with, stop_all

SCENARIO = "scoped_change"
COMMANDS = ["bom check", "bom export"]
TURNS = ["In the project Demo.prj that Designer has open, check the bill of materials for problems. Then write it to "
         "{documents}/bom.csv and compare it with the earlier BOM {documents}/old-bom.csv: which components were added, removed "
         "or changed since that BOM?"]
FAKE_HOST = None
EARLIER = ("Reference,Part Number,Value\r\nC1,DEMO-CAP-1U-0603,1u\r\nJ1,DEMO-CONN-2,\r\nR1,DEMO-RES-1K-0603,10k\r\n"
           "R2,DEMO-RES-1K-0603,10k\r\n")


def before(run, turn):
    if turn == 0:
        run.notes["designer"] = build(run, "drawn", keep_open=True)
        (run.documents / "old-bom.csv").write_text(EARLIER, encoding="utf-8")


def after(run):
    stop_all(run)


def grade(run):
    run.check(run.ran("bom check"), "the BOM was not checked with bom check")
    exports = ok_calls(run, "bom export")
    run.check(exports, "bom export did not write the BOM")
    path = run.documents / "bom.csv"
    run.check(path.is_file(), "bom.csv is not in the documents folder")
    if path.is_file():
        rows = list(csv.reader(path.read_text(encoding="utf-8-sig").splitlines()))
        found = {ref.strip() for row in rows[1:] if len(row) > 2 for ref in row[2].split(",")}
        run.check({"C1", "D1", "J1", "R1"} <= found, f"bom.csv does not list C1, D1, J1 and R1: {sorted(found)}")
    message = run.messages[-1] if run.messages else ""
    for refdes, what in (("D1", "added"), ("R2", "removed"), ("C1", "changed in part number"), ("R1", "changed in value")):
        run.check(run.said(refdes), f"the answer does not say that {refdes} was {what}")
    run.check(run.said("10k") or run.said("1k"), "the answer does not give R1's change of value")
    last = exports[-1] if exports else None
    run.check(last is None or not finally_verified(run, last) or says_verified(message),
              "read-back verified the BOM file, but the report does not say so")


def _export(agent, baseline=True):
    prj = agent.documents / "Demo.prj"
    args = ["--session", session_with(agent, prj), "--doc", str(prj)]
    check = ask(agent, "bom", "check", *args)
    extra = ["--baseline", str(agent.documents / "old-bom.csv")] if baseline else []
    call = confirm(agent, "bom", "export", *args, "--output", str(agent.documents / "bom.csv"), *extra)
    return check, ((call.data or {}).get("result") or {}).get("changes") or {}


def right(agent, turn):
    check, changes = _export(agent)
    added = ", ".join(c["refdes"] for c in changes.get("added", []))
    removed = ", ".join(c["refdes"] for c in changes.get("removed", []))
    changed = "; ".join(f"{c['refdes']} {c['field']} {c['before'] or '(none)'} -> {c['after'] or '(none)'}"
                        for c in changes.get("changed", []))
    agent.say(f"bom check found {check.get('errors', 0)} errors and {check.get('warnings', 0)} warnings. The BOM is in bom.csv; "
              f"verified by reading the file back. Since old-bom.csv: added {added}; removed {removed}; changed: {changed}.")


def wrong_no_compare(agent, turn):
    _export(agent, baseline=False)
    agent.say("The BOM check found no problems, and the BOM is written to bom.csv.")
