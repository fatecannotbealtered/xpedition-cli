"""What the questions share: the sample project, the hooks that prepare Xpedition, and reading results.

Every question works on the fake host and on the real Xpedition.

On the real Xpedition an Xpedition project cannot come from the repository, so ``build`` makes
the sample project in the question's documents folder with the tool itself, up to the stage the
question needs: ``doc create`` in a Designer session from Xpedition's stock setup template,
``part create`` with tests/inputs/parts.json, the schematic drawn from tests/inputs/design.json
and saved, ``packager run`` and ``pcb create``, and the laid-out board. Designer and Layout are
started by the hooks with ``session open`` and quit by ``after`` with ``session close``, so no
Designer or Layout may run when the evaluation starts: the tool can drive only one of each
(knowledge entry ``designer-one-instance-only``). The hooks follow every long job to its end,
the session and document lifecycle commands included.

On the fake host the question's ``FAKE_HOST`` holds the project as the real Xpedition would
show it, each document with its kind, and ``build`` only copies the input files; ``start`` tells
the fake host the kind of the documents the new instance opens. A read of the fake host answers with the
generic shape ``{"items": [{"key": ..., <fields>}]}``; ``result`` takes the fields out of it,
so a grader reads both hosts the same way.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parents[1] / "inputs"
STAGES = ("project", "parts", "drawn", "board", "laid_out")
FINAL_JOB_STATES = {"completed", "failed", "partial", "unknown", "cancelled"}
START_WAIT_S = 300.0
OUTLINE = {"width": 70.0, "height": 48.0, "radius": 3.0}


def _parts() -> dict[str, Any]:
    return json.loads((FIXTURES / "parts.json").read_text(encoding="utf-8"))


PARTS = _parts()["parts"]
PART_NUMBERS = [part["number"] for part in PARTS]
DESCRIPTIONS = {part["number"]: part["description"] for part in PARTS}


class SetupError(RuntimeError):
    """A hook could not prepare Xpedition; the question stops with this reason."""


# ------------------------------------------------------------------ where things are


def fake(run: Any) -> bool:
    """Whether the fake host stands in for Xpedition: only there ``run.host`` answers."""
    if "_fake" not in run.notes:
        try:
            run.host("state")
            run.notes["_fake"] = True
        except RuntimeError:
            run.notes["_fake"] = False
    return run.notes["_fake"]


def project(run: Any) -> Path:
    return run.documents / "Demo.prj"


def board(run: Any) -> Path:
    return run.documents / "PCB" / "Demo.pcb"


def design(run: Any) -> Path:
    return run.documents / "design.json"


def parts(run: Any) -> Path:
    return run.documents / "parts.json"


def same_path(a: Any, b: Any) -> bool:
    return bool(a) and bool(b) and os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def path_of(key: str) -> str:
    """The file of a target key such as ``file:///D:/x/fab/board.gbr``."""
    return key.split("#", 1)[0][len("file:///"):] if key.startswith("file:///") else key


def inside(path: Any, folder: Path) -> bool:
    """Whether a file is the folder or lies in it."""
    target = Path(os.path.abspath(str(path)))
    return same_path(target, folder) or any(same_path(parent, folder) for parent in target.parents)


def digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def board_data(path: Path) -> str | None:
    """A digest of what a save of the board writes: the files of its ``Layout`` folder on the real
    Xpedition, which rewrites the board's ``.pcb`` whenever it opens it; the board file on the
    fake host."""
    folder = path.parent / "Layout"
    if not folder.is_dir():
        return digest(path)
    whole = hashlib.sha256()
    for item in sorted(p for p in folder.rglob("*") if p.is_file()):
        whole.update(item.name.encode("utf-8") + item.read_bytes())
    return whole.hexdigest()


# ------------------------------------------------------------------ reading results


def result(envelope: Any) -> dict[str, Any]:
    """The fields a read returned. The fake host's generic answer wraps them in one item that
    names the document's target key; the real adapter returns them as they are."""
    data = envelope.get("data") if isinstance(envelope, dict) else None
    if not isinstance(data, dict):
        return {}
    items = data.get("items")
    if isinstance(items, list) and len(items) == 1 and isinstance(items[0], dict) \
            and str(items[0].get("key", "")).startswith(("file:", "untitled:")):
        return items[0]
    return data


def read(run: Any, *args: str) -> dict[str, Any]:
    """Read through the tool as the grader; a failed read is a failed check."""
    out = run.tool(*args)
    run.check(out.get("ok"), f"the grader's `{' '.join(args[:2])}` failed: {(out.get('error') or {}).get('message')}")
    return result(out) if out.get("ok") else {}


def ok_calls(run: Any, command: str, turn: int | None = None) -> list[Any]:
    """The Agent's successful runs of a write command, without dry runs."""
    return [c for c in run.write_calls(turn) if c.command == command and c.ok]


def receipt(call: Any) -> dict[str, Any]:
    return ((call.data or {}).get("receipt") or {}) if call is not None else {}


def verified(call: Any) -> bool:
    return (receipt(call).get("outcome") or {}).get("verification") == "passed"


def finally_verified(run: Any, call: Any) -> bool:
    """Whether a write's read-back passed: in the receipt of the long job it started, once that
    job has ended, or in its own receipt."""
    job = job_of(call)
    final = (wait_job(run, job).get("receipt") or {}) if job else receipt(call)
    return (final.get("outcome") or {}).get("verification") == "passed"


def read_result(run: Any, call: Any) -> dict[str, Any]:
    """The fields an Agent's read returned; for a long read, those its job ended with."""
    data = (call.data or {}) if call is not None else {}
    job = (data.get("job") or {}).get("id")
    if job:
        return result({"data": wait_job(run, job).get("result") or {}})
    return result({"data": data})


def observed(call: Any) -> dict[str, Any]:
    """The fields a write's read-back observed, from every target of its receipt in one dict."""
    merged: dict[str, Any] = {}
    for values in (receipt(call).get("observed") or {}).values():
        if isinstance(values, dict):
            merged.update(values)
    return merged


def job_of(call: Any) -> str | None:
    """The long job a write started, if it started one."""
    job = (((call.data or {}).get("result") or {}).get("job") or {}) if call is not None else {}
    return job.get("id")


def ended(run: Any, call: Any) -> dict[str, Any]:
    """The result of a call, or of the long job it started once that job has ended."""
    job = job_of(call)
    if job is None:
        return ((call.data or {}).get("result") or {}) if call is not None else {}
    return wait_job(run, job).get("result") or {}


def wait_job(run: Any, job_id: str, rounds: int = 10) -> dict[str, Any]:
    """The job's view once it has ended, or its last view after ``rounds`` waits of 60 s."""
    view: dict[str, Any] = {}
    for _ in range(rounds):
        out = run.tool("job", "get", "--job", job_id, "--wait", "60s")
        view = out.get("data") or {}
        if not out.get("ok") or view.get("state") in FINAL_JOB_STATES:
            break
    return view


def outline_of(board_result: dict[str, Any]) -> tuple[Any, Any, Any]:
    outline = board_result.get("outline") or {}
    return outline.get("width_mm"), outline.get("height_mm"), outline.get("radius_mm")


def near(value: Any, wanted: float, tolerance: float = 0.01) -> bool:
    return isinstance(value, (int, float)) and abs(value - wanted) <= tolerance


# ------------------------------------------------------------------ writing as a hook


def write(run: Any, *args: str) -> dict[str, Any]:
    """Run a write as a hook, confirming it with its token (and --dangerous when its tier asks).
    A long job is followed to its end; the envelope returned then holds the job's receipt and
    result, or its error."""
    out = run.tool(*args)
    error = out.get("error") or {}
    if not out.get("ok") and error.get("code") == "E_CONFIRMATION_REQUIRED":
        details = error.get("details") or {}
        dangerous = ["--dangerous"] if (details.get("preview") or {}).get("tier") == "dangerous" else []
        out = run.tool(*args, "--confirm", details["confirm_token"], *dangerous)
    job = ((out.get("data") or {}).get("result") or {}).get("job") if out.get("ok") else None
    if not job:
        return out
    view = wait_job(run, job["id"])
    done = view.get("state") == "completed" and bool(view.get("ok"))
    final: dict[str, Any] = {"ok": done, "data": {"receipt": view.get("receipt"), "result": view.get("result") or {}, "job": view}}
    if not done:
        final["error"] = {"code": view.get("code") or "E_UNKNOWN", "message": f"job {job['id']} ended {view.get('state')}"}
    return final


def need(out: dict[str, Any], what: str) -> dict[str, Any]:
    if not out.get("ok"):
        error = out.get("error") or {}
        raise SetupError(f"{what} failed: {error.get('code')}: {error.get('message')}")
    return out


def start(run: Any, path: Path | None) -> str:
    """Start Designer (for a .prj, or with no project for None) or Layout (for a .pcb) with the
    file open, wait until it is ready, and remember it for ``stop_all``."""
    if fake(run):
        # The fake host cannot tell a project from a board by its file name: say which one opens.
        run.host("set_kind", kind="board" if path is not None and path.suffix.lower() == ".pcb" else "project")
    what = f"session open --doc {path.name}" if path else "session open --kind project"
    out = need(write(run, "session", "open", *(["--doc", str(path)] if path else ["--kind", "project"])), what)
    session = ((out.get("data") or {}).get("result") or {}).get("id")
    if not session:
        raise SetupError(f"{what} named no session")
    run.notes.setdefault("sessions", []).append(session)
    deadline = time.monotonic() + START_WAIT_S
    while True:
        view = run.tool("session", "get", "--session", session).get("data") or {}
        if view.get("state") == "ready":
            break
        if view.get("state") in ("closed", "lost", "unreachable") or time.monotonic() > deadline:
            raise SetupError(f"session {session} is {view.get('state')}, not ready, after {what}")
        time.sleep(3)
    if path and not any(same_path(d.get("path"), path) for d in view.get("documents", [])):
        need(write(run, "doc", "open", "--session", session, "--doc", str(path)), f"doc open {path.name}")
    return session


def stop_all(run: Any) -> None:
    """Quit every instance the hooks started, discarding what is unsaved; for ``after``."""
    for session in reversed(run.notes.get("sessions", [])):
        try:
            write(run, "session", "close", "--session", session, "--discard")
        except Exception:  # noqa: BLE001 - after must go on
            pass
    run.notes["sessions"] = []


def fake_board(run: Any, fields: dict[str, Any]) -> None:
    """Fake host only: the board file the fake host opens, holding what pcb get and pcb check read."""
    path = board(run)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"objects": {"": fields}}, sort_keys=True), encoding="utf-8")


# ------------------------------------------------------------------ the sample project on Xpedition


def clear(run: Any) -> None:
    """Real Xpedition only: empty the documents folder; what FILES copied there stands in for
    the fake host, and doc create needs an empty folder."""
    for item in run.documents.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()


def create_project(run: Any, output: Path, *, keep_open: bool = False) -> str | None:
    """Real Xpedition: make a project with doc create in a Designer the hook starts, from
    Xpedition's stock setup template. With ``keep_open`` that Designer stays open with the new
    project and its session is returned; otherwise it is quit."""
    designer = start(run, None)
    need(write(run, "doc", "create", "--session", designer, "--kind", "project", "--path", str(output)), "doc create in Designer")
    if keep_open:
        return designer
    stop_all(run)
    return None


def copy_inputs(run: Any) -> None:
    for name in ("parts.json", "design.json"):
        shutil.copyfile(FIXTURES / name, run.documents / name)


def build(run: Any, upto: str, *, keep_open: bool = False) -> str | None:
    """Real Xpedition: make the sample project Demo.prj in the documents folder up to a stage of
    STAGES. With ``keep_open``, the last instance used stays open with the file (``drawn``:
    Designer with the project, reopened after the save; ``laid_out``: Layout with the saved
    board), and its session is returned. Fake host: copy the input files only."""
    if fake(run):
        copy_inputs(run)
        return None
    level = STAGES.index(upto)
    clear(run)
    create_project(run, project(run))
    copy_inputs(run)
    prj = str(project(run))
    if level >= STAGES.index("parts"):
        need(write(run, "part", "create", "--doc", prj, "--file", str(parts(run))), "part create")
    if level >= STAGES.index("drawn"):
        designer = start(run, project(run))
        need(write(run, "schematic", "create", "--session", designer, "--doc", prj, "--design", str(design(run))),
             "schematic create")
        # doc save also opens the project again, so Designer then reads the design right
        # (knowledge entry designer-reopen-after-sheet-changes).
        need(write(run, "doc", "save", "--session", designer, "--doc", prj), "doc save of the schematic")
        if keep_open and upto == "drawn":
            return designer
        stop_all(run)  # file-mode writes are refused while Designer has the project open
    if level >= STAGES.index("board"):
        need(write(run, "packager", "run", "--doc", prj), "packager run")
        need(write(run, "pcb", "create", "--doc", prj), "pcb create")
    if level >= STAGES.index("laid_out"):
        layout = start(run, board(run))
        pcb = ["--session", layout, "--doc", str(board(run))]
        need(write(run, "forward-annotation", "run", *pcb), "forward-annotation run")
        need(write(run, "pcb", "update", *pcb, "--width", "70mm", "--height", "48mm", "--radius", "3mm"), "pcb update")
        need(write(run, "placement", "run", *pcb, "--design", str(design(run))), "placement run")
        need(write(run, "plane", "create", *pcb, "--net", "GND", "--layer", "2"), "plane create")
        need(write(run, "router", "run", *pcb), "router run")
        need(write(run, "doc", "save", *pcb), "doc save of the board")
        if keep_open:
            return layout
        stop_all(run)
    return None


# ------------------------------------------------------------------ scripted agents


def confirm(agent: Any, *args: str) -> Any:
    """What the write recipe says: run, then confirm with the token, adding --dangerous when the
    preview's tier is dangerous."""
    call = agent.run(*args)
    if not call.ok and call.code == "E_CONFIRMATION_REQUIRED":
        dangerous = ["--dangerous"] if (call.details.get("preview") or {}).get("tier") == "dangerous" else []
        call = agent.run(*args, "--confirm", call.details["confirm_token"], *dangerous)
    return call


def lifecycle(agent: Any, *args: str) -> Any:
    """A session or document lifecycle command, confirmed and followed to the end of its job."""
    return poll(agent, confirm(agent, *args))


def ask(agent: Any, *args: str) -> dict[str, Any]:
    """A read, followed to the end of its job when it is a long one: the fields it returned."""
    call = agent.run(*args)
    if (call.data or {}).get("job"):
        return result({"data": (poll(agent, call).data or {}).get("result") or {}})
    return result({"data": call.data})


def poll(agent: Any, call: Any) -> Any:
    """Run the job's poll command until the job ends; the last poll, or the call without a job."""
    job = ((call.data or {}).get("result") or {}).get("job") or (call.data or {}).get("job")
    if not job:
        return call
    last = call
    for _ in range(10):
        last = agent.line(job["poll"])
        if (last.data or {}).get("state") in FINAL_JOB_STATES:
            break
    return last


def session_with(agent: Any, path: Path) -> str:
    """The session that has the file open, from session list."""
    for item in (agent.run("session", "list").data or {}).get("items", []):
        if any(same_path(d.get("path"), path) for d in item.get("documents", [])):
            return item["id"]
    raise RuntimeError(f"no session has {path.name} open")
