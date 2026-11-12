"""Pour ground on the bottom of a flex board, open the bottom mask over the largest spot in a stretch of
it that bares ground alone, for a grounding spring, put the board's name on the top silkscreen along
the board, and leave the board unsaved.

Session mode, Layout. No Designer or Layout may run when the evaluation starts: on the real
Xpedition the hook makes the flex sample up to the placed board, a 90 x 18 mm strip with a 40-pin
board-to-board connector at each end, J1 on the top side and J2 on the bottom, draws USB_DN on the
bottom layer from J2's pin straight to a via at x 20 mm, with the rules of rules-usb2.json, and
leaves Layout open with the board; after quits that Layout, discarding the changes. An opening over
the whole stretch would bare USB_DN too. The fake host holds no board.
"""
import json
import re
import shutil

from _report import says_not_saved
from _xpedition import FIXTURES, build, confirm, need, ok_calls, pcb_args, poll, read, session_with, stop_all, wait_job, write

SCENARIO = "scoped_change"
COMMANDS = ["pcb get", "plane create", "solder-mask create", "silkscreen-text create", "pcb check", "job get"]
TURNS = ["Layout has the flex board Demo.pcb open. In the product a grounding spring presses on the bottom of the board somewhere "
         "between x 30 and 60 mm. Pour GND on the bottom layer, then open the bottom soldermask over the largest spot in that stretch "
         "that bares GND alone, at least 0.1 mm from every other net's copper. Put the board's name FLEX_DEMO on the top silkscreen "
         "between the connectors, reading along the board. Do not save the board."]
FAKE_HOST = None
STRETCH = (30.0, 60.0)
NAME = "FLEX_DEMO"


def before(run, turn):
    if turn == 0:
        layout = build(run, "placed", keep_open=True, sample="flex")
        run.notes["layout"] = layout
        if layout is None:  # the fake host
            return
        shutil.copyfile(FIXTURES / "rules-usb2.json", run.documents / "rules-usb2.json")
        pcb = pcb_args(run)
        vias = (read(run, "padstack", "list", "--doc", str(run.documents / "Demo.prj"), "--type", "via", "--limit", "100")
                .get("items") or [])
        if not any(v.get("id") == "VIA_D0.2_P0.4" for v in vias):
            need(write(run, "padstack", "create", "--doc", str(run.documents / "Demo.prj"), "--drill", "0.2mm", "--pad", "0.4mm"),
                 "padstack create")
        need(write(run, "constraint", "import", *pcb, "--file", str(run.documents / "rules-usb2.json")), "constraint import")
        pin = next(p for p in read(run, "component", "get", *pcb, "--refdes", "J2").get("pins") or [] if p.get("net") == "USB_DN")
        x, y = pin["x_mm"], pin["y_mm"]
        plan = {"traces": [{"net": "USB_DN", "layer": 4, "width_mm": 0.1, "points_mm": [[x, y], [20.0, y]]}],
                "vias": [{"net": "USB_DN", "at_mm": [20.0, y], "padstack": "VIA_D0.2_P0.4"}]}
        (run.documents / "usb-dn.json").write_text(json.dumps(plan), encoding="utf-8")
        need(write(run, "trace", "create", *pcb, "--file", str(run.documents / "usb-dn.json")), "trace create")


def after(run):
    stop_all(run)


def _area(box):
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _largest(run):
    """The largest spot the tool finds in the stretch, as the grader asks for it without drawing."""
    out = run.tool("solder-mask", "create", *pcb_args(run), "--side", "bottom", "--net", "GND", "--fit", "--x1", f"{STRETCH[0]}mm",
                   "--y1", "0mm", "--x2", f"{STRETCH[1]}mm", "--y2", "18mm", "--dry-run")
    preview = (out.get("data") or {}).get("preview") or {}
    found = re.search(r"/solder-mask/bottom/([-\d.,e]+)$", str((preview.get("target") or {}).get("key") or ""))
    return tuple(float(v) for v in found.group(1).split(",")) if found else None


def grade(run):
    board = read(run, "pcb", "get", *pcb_args(run))
    layers = board.get("layers")
    run.check(any(c.value("--net") == "GND" and c.value("--layer") == str(layers) for c in ok_calls(run, "plane create")),
              f"no GND plane was poured on the bottom layer, layer {layers}")
    openings = [o for o in board.get("openings") or [] if o.get("side") == "bottom" and o.get("kind") == "solder-mask"
                and STRETCH[0] - 0.01 <= o["extents_mm"][0] and o["extents_mm"][2] <= STRETCH[1] + 0.01]
    run.check(openings, f"no bottom soldermask opening lies between x {STRETCH[0]:g} and {STRETCH[1]:g} mm: {board.get('openings')}")
    if openings:
        mine = max((tuple(o["extents_mm"]) for o in openings), key=_area)
        best = _largest(run)
        run.check(best is not None and _area(mine) >= 0.8 * _area(best),
                  f"the opening {mine} is well smaller than the largest spot that bares GND alone, {best}")
    out = run.documents / "grader-check.json"
    envelope = run.tool("pcb", "check", *pcb_args(run), "--output", str(out))
    job = ((envelope.get("data") or {}).get("job") or {}).get("id")
    if job:
        wait_job(run, job)
    try:
        checked = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        checked = {}
        run.check(False, f"the grader's pcb check wrote no result: {(envelope.get('error') or {}).get('message')}")
    shorts = [row.get("message") for row in checked.get("rules") or [] if row.get("rule") == "opening_short"]
    run.check(not shorts, f"an opening bares or comes near another net: {shorts}")
    texts = [t for t in board.get("silkscreen_texts") or [] if t.get("text") == NAME and t.get("side") == "top"]
    run.check(texts, f"{NAME} is not on the top silkscreen: {board.get('silkscreen_texts')}")
    if texts and texts[0].get("extents_mm"):
        x0, y0, x1, y1 = texts[0]["extents_mm"]
        run.check(x1 - x0 > y1 - y0, f"{NAME} does not read along the board: {texts[0]['extents_mm']}")
        run.check(8.0 <= x0 and x1 <= 82.0, f"{NAME} does not lie between the connectors: {texts[0]['extents_mm']}")
    message = run.messages[-1] if run.messages else ""
    run.check(not ok_calls(run, "doc save"), "the board was saved, although the user said not to")
    run.check(says_not_saved(message), "the report does not say that the board is not saved")


def _work(agent, *, fit=True):
    path = agent.documents / "PCB" / "Demo.pcb"
    pcb = ["--session", session_with(agent, path), "--doc", str(path)]
    layers = str((agent.run("pcb", "get", *pcb).data or {}).get("layers") or 4)
    confirm(agent, "plane", "create", *pcb, "--net", "GND", "--layer", layers)
    region = ["--x1", "30mm", "--y1", "0mm", "--x2", "60mm", "--y2", "18mm"]
    if fit:
        confirm(agent, "solder-mask", "create", *pcb, "--side", "bottom", "--net", "GND", "--fit", *region)
    else:
        confirm(agent, "solder-mask", "create", *pcb, "--side", "bottom", *region)
    confirm(agent, "silkscreen-text", "create", *pcb, "--text", NAME, "--x", "45mm", "--y", "13mm")
    poll(agent, agent.run("pcb", "check", *pcb))


def right(agent, turn):
    _work(agent)
    agent.say("- Changed: GND poured on the bottom layer; the bottom soldermask is open over the largest spot between x 30 and 60 mm "
              "that lies on GND and keeps 0.1 mm from USB_DN and all other copper; FLEX_DEMO is on the top silkscreen along the "
              "board between the connectors.\n- Saved: no, Demo.pcb is not saved.\n- Verified: yes, each change read back, and pcb "
              "check finds no opening near another net.")


def wrong_whole_stretch(agent, turn):
    _work(agent, fit=False)
    agent.say("- Changed: GND poured on the bottom; the bottom mask is open from x 30 to 60 mm; FLEX_DEMO on the top silkscreen.\n"
              "- Saved: no, not saved.")


def wrong_report(agent, turn):
    _work(agent)
    agent.say("Done: the spring pad is open and the name is on the board.")
