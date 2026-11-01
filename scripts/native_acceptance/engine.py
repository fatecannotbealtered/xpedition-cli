"""A fixed sequential acceptance case; no user command/path passthrough."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from checks import Stop, library, orphan, pad_correspondence, pcb, require, schematic, within
from processes import run as contained_run

PACK = Path(__file__).resolve().parent
ROOT = PACK.parent.parent
COMMANDS = frozenset(
    {
        "version",
        "session inspect",
        "session attach",
        "project create",
        "project inspect",
        "library create",
        "library import",
        "library inspect",
        "schematic build",
        "schematic package",
        "pcb create",
        "pcb update",
        "pcb placement move",
        "operation show",
        "operation resolve",
    }
)


def utc():
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(manifest, include_seed=True):
    if manifest.get("_sealed_manifest_sha256"):
        require(
            digest(PACK / "manifest.json") == manifest["_sealed_manifest_sha256"],
            "E_HASH",
            "The sealed manifest changed during the run",
        )
    checkout = Path(manifest.get("checkout", str(ROOT))).resolve()
    require(checkout.is_dir(), "E_SCOPE", "The fixed candidate checkout is unavailable")
    scratch, results = Path(manifest["scratch_root"]), Path(manifest["results_root"])
    run_root = Path(manifest.get("run_root", str(ROOT)))
    within(scratch, run_root / "xpedition-native-acceptance")
    within(results, run_root / "xpedition-results")
    checked = 0
    for item in manifest["entry_files"]:
        path = PACK / item["name"]
        require(
            path.is_file() and digest(path) == item["sha256"],
            "E_HASH",
            "Acceptance helper changed",
            {"path": str(path)},
        )
        checked += 1
    for item in manifest["pinned_files"] + (manifest["seed_files"] if include_seed else []):
        path = Path(item["source"])
        require(
            path.is_file() and not path.is_symlink(),
            "E_HASH",
            "A pinned input is absent or replaced by a link",
            {"path": str(path)},
        )
        actual = digest(path)
        require(
            actual == item["sha256"],
            "E_HASH",
            "Pinned input/candidate bytes changed",
            {"path": str(path), "expected": item["sha256"], "actual": actual},
        )
        checked += 1
    return {"status": "passed", "files_checked": checked, "native_task_acceptance": "not_run"}


def actor_guard(manifest):
    spec = importlib.util.spec_from_file_location(
        "readonly_runtime_identity", PACK / "identity_probe.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    identity = module.process_identity(
        {"pid": os.getpid(), "parent_pid": os.getppid(), "exe": "python.exe"}
    )
    security = identity.get("security_metadata") or {}
    require(
        security.get("account", "").casefold() == manifest["account"].casefold()
        and security.get("elevated") == 0
        and security.get("restricted_token") is False,
        "E_ACTOR",
        "This entry requires the user's authorized ordinary Windows context; "
        "no native call was attempted",
        {"caller": identity, "COM_probe_attempted": False},
    )
    require(
        str(Path(sys.executable).resolve()).casefold() == manifest["python_executable"].casefold(),
        "E_PYTHON",
        "Use the pinned installed Python executable",
    )
    return identity


def project_seed(manifest):
    seed = Path(manifest["scratch_root"]) / "seed"
    require(not seed.exists(), "E_CONFLICT", "Seed folder exists; a partial run cannot be replayed")
    seed.mkdir()
    for item in manifest["seed_files"]:
        target = within(seed / item["destination"], seed)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item["source"], target)
        require(
            digest(target) == item["sha256"],
            "E_HASH",
            "Seed source changed during the bounded copy",
            {"path": str(target)},
        )
    raw = (seed / "Default.prj").read_text(encoding="utf-8")
    raw = re.sub(
        r"^KEY CentralLibrary .+$",
        'KEY CentralLibrary "' + str(seed / "Library/TemplateLibrary.lmc").replace("\\", "/") + '"',
        raw,
        flags=re.M,
    )
    raw = re.sub(
        r"^KEY DBCFile .+$",
        'KEY DBCFile "' + str(seed / "Library/TemplateLibrary.dbc").replace("\\", "/") + '"',
        raw,
        flags=re.M,
    )
    require("LIST Designs" not in raw, "E_SEED", "The pinned stock seed changed unexpectedly")
    (seed / "Default.prj").write_text(raw, encoding="utf-8")
    return seed / "Default.prj"


class Runner:
    def __init__(self, manifest, caller):
        self.m, self.caller = manifest, caller
        self.scratch, self.results = Path(manifest["scratch_root"]), Path(manifest["results_root"])
        self.deadline = time.monotonic() + manifest["maximum_seconds"]
        self.context = {
            "runner_pid": os.getpid(),
            "started_utc": utc(),
            "applications": {},
            "owned_projects": [
                str(self.scratch / x)
                for x in ("source/QA_SOURCE.prj", "target/QA_ROOT.prj", "orphan/QA_ORPHAN.prj")
            ],
            "owned_boards": [str(self.scratch / "target/PCB/QA_ROOT.pcb")],
            "active_step": None,
        }
        self.calls, self.current = [], "initialization"
        self.summary = {
            "kind": "explicit_ordinary_user_bounded_native_acceptance",
            "started_utc": utc(),
            "caller": caller,
            "candidate_binary_sha256": manifest["binary_sha256"],
            "adapter_sha256": manifest["adapter_sha256"],
            "representative": {"status": "not_run"},
            "orphan": {"status": "not_run"},
            "automatic_retry": False,
            "application_windows": "retained; existing applications are never quit or killed",
            "native_checker": {
                "status": "not_run",
                "completion": None,
                "source": None,
                "version": None,
                "input": None,
                "coverage": None,
            },
            "whole_application_coverage": "unknown",
            "license_expiry": "unknown",
        }
        self.env = os.environ.copy()
        for name in (
            "XPEDITION_OBSERVE_ONLY",
            "XPEDITION_EXPECTED_PID",
            "XPEDITION_EXPECTED_DOMAIN",
            "XPEDITION_NATIVE_COMMAND",
            "PYTHONPATH",
            "XPEDITION_EXPECTED_ADAPTER_BUILD",
        ):
            self.env.pop(name, None)
        self.env.update(
            PYTHONIOENCODING="utf-8",
            PYTHONDONTWRITEBYTECODE="1",
            XPEDITION_CLI_CONFIG_DIR=str(self.scratch / "state"),
            XPEDITION_NATIVE_COMMAND=str(PACK / "adapter.cmd"),
            XPEDITION_CLI_SCOPE_FILE=str(self.scratch / "scope.json"),
            XPEDITION_REQUIRE_EXISTING="1",
            XPEDITION_MGC_WD=str(self.scratch / "wdir"),
            XPEDITION_ACCEPTANCE_MANIFEST_SHA256=manifest.get("_sealed_manifest_sha256", ""),
        )

    def write(self, name, value):
        path = within(self.results / name, self.results)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def checkpoint(self):
        self.write("run-context.json", self.context)
        self.write(
            "progress.json",
            {
                "stage": self.current,
                "calls": self.calls,
                "summary": self.summary,
                "updated_utc": utc(),
            },
        )

    def process(self, label, argv, timeout=240):
        require(len(self.calls) < 96, "E_BOUND", "The fixed maximum process call count was reached")
        remaining = self.deadline - time.monotonic()
        require(remaining > 0, "E_BOUND", "The fixed overall deadline was reached")
        self.current = label
        self.checkpoint()
        verify(self.m, include_seed=False)
        started = utc()
        record = contained_run(
            argv, cwd=Path(self.m["checkout"]), env=self.env, timeout=min(timeout, remaining)
        )
        stem = f"{len(self.calls) + 1:02d}-{label}"
        (self.results / (stem + ".stdout.json")).write_text(record.pop("stdout"), encoding="utf-8")
        (self.results / (stem + ".stderr.txt")).write_text(record.pop("stderr"), encoding="utf-8")
        record.update(
            label=label,
            argv=argv,
            started_utc=started,
            completed_utc=utc(),
            caller_account=self.caller["security_metadata"]["account"],
            stdout=str(self.results / (stem + ".stdout.json")),
            candidate_sha256=self.m["binary_sha256"],
            adapter_sha256=self.m["adapter_sha256"],
        )
        self.calls.append(record)
        self.write(stem + ".run.json", record)
        self.checkpoint()
        print(
            f"[{len(self.calls):02d}] {label}: exit={record['exit_code']} "
            f"timeout={record['timed_out']}",
            flush=True,
        )
        require(
            not record["timed_out"],
            "E_OUTCOME_UNKNOWN",
            "Native call timed out; further writes are stopped and all scratch remains",
            record,
        )
        text = (self.results / (stem + ".stdout.json")).read_text(encoding="utf-8")
        try:
            result = json.loads(text)
        except json.JSONDecodeError as error:
            raise Stop(
                "E_PROTOCOL", "Child output is not a complete JSON response", record
            ) from error
        require(
            record["exit_code"] == 0 and result.get("ok") is True,
            "E_CHILD",
            "A finite candidate/oracle call failed; no retry or next write was issued",
            {"record": record, "reported_error": result.get("error"), "response": result},
        )
        return result["data"]

    def native(self, step):
        self.context["active_step"] = step
        self.checkpoint()
        result = self.process(
            step,
            [self.m["python_executable"], "-B", str(PACK / "native_worker.py"), "--step", step],
            300,
        )
        self.context["active_step"] = None
        if step == "preflight":
            self.context["applications"] = result["applications"]
        self.checkpoint()
        return result

    def cli(self, label, command, options=None, phase=None, token=None):
        require(
            command in COMMANDS,
            "E_ALLOWLIST",
            "Command is not part of the fixed acceptance sequence",
        )
        args = [*self.m.get("cli_prefix", [self.m["binary"]]), *command.split()]
        for name, value in (options or {}).items():
            if type(value) is bool:
                if value:
                    args.append("--" + name)
            else:
                args.extend(("--" + name, str(value)))
        if phase == "plan":
            args.append("--dry-run")
        elif phase == "apply":
            require(
                isinstance(token, str) and token, "E_TOKEN", "A fresh bound plan token is required"
            )
            args.extend(("--confirm", token))
        args.extend(("--format", "json"))
        return self.process(label, args, 720 if phase == "apply" else 300)

    def apply(self, label, command, options):
        self.native("guard")
        plan = self.cli(label + "-plan", command, options, "plan")
        preview = plan.get("preview") or {}
        require(
            preview.get("can_apply") is True and isinstance(plan.get("confirm_token"), str),
            "E_PLAN",
            "Candidate did not provide an applicable bound plan",
        )
        result = self.cli(label + "-apply", command, options, "apply", plan["confirm_token"])
        receipt = result.get("receipt")
        require(
            isinstance(receipt, dict),
            "E_PROTOCOL",
            "The SDK response lacks data.receipt; inspect the recorded response without replaying",
            {"response": result},
        )
        outcome = receipt.get("outcome") or {}
        require(
            outcome.get("execution") == "completed"
            and outcome.get("change") not in ("partial", "unknown"),
            "E_OUTCOME_UNKNOWN",
            "Candidate did not establish a completed, nonpartial change; "
            "stop for independent reconciliation",
            {"receipt": receipt},
        )
        operation_id = receipt.get("operation_id")
        if operation_id:
            operation = self.cli(label + "-operation", "operation show", {"id": operation_id})
            self.write(label + "-durable-operation.json", operation)
        return receipt

    def run(self):
        source, target, fault = [
            self.scratch / p
            for p in ("source/QA_SOURCE.prj", "target/QA_ROOT.prj", "orphan/QA_ORPHAN.prj")
        ]
        inputs = Path(self.m["inputs_root"])
        parts = json.loads((inputs / "parts-L0.json").read_text(encoding="utf-8"))["parts"]
        expected_nets = json.loads(
            (inputs / "expected-connections.json").read_text(encoding="utf-8")
        )
        expected_geometry = json.loads(
            (inputs / "expected-geometry.json").read_text(encoding="utf-8")
        )
        self.cli("candidate-version", "version")
        preflight = self.native("preflight")
        self.write("application-preflight.json", preflight)
        resumed = self.m.get("resume_source")
        built = resumed and resumed.get("stage") == "built"
        template = self.scratch / "seed/Default.prj" if resumed else project_seed(self.m)
        self.write(
            "seed-binding.json",
            {
                "template": str(template),
                "sha256": digest(template),
                "source": "pinned installed Default project/config/library, copied and "
                "rebound only under this scratch root",
                "runtime_initialized": False,
            },
        )
        if not resumed:
            self.apply(
                "create-source",
                "project create",
                {"project": source, "template": template, "design": "Board1"},
            )
        source_info = self.cli("inspect-source", "project inspect", {"project": source})
        source_library = Path(source_info["central_library"]["path"])
        within(source_library, source.parent)
        if resumed:
            readback = self.native("source-library-readback")
            self.write("source-library-reconciliation.json", readback)
            require(readback["valid"], "E_READBACK", "Source library differs from frozen inputs")
            if not built:
                self.cli(
                    "reconcile-source-library",
                    "operation resolve",
                    {
                        "id": resumed["operation_id"],
                        "outcome": "applied",
                        "evidence": str(self.results / "source-library-reconciliation.json"),
                    },
                )
        else:
            self.apply(
                "author-source-library",
                "library create",
                {"project": source, "file": inputs / "parts-L0.json", "dangerous": True},
            )
        source_records = {}
        for part in parts:
            source_records[part["number"]] = library(
                self.cli(
                    "source-" + part["number"],
                    "library inspect",
                    {"project": source, "part": part["number"]},
                ),
                part,
            )
        if not built:
            self.apply(
                "create-target",
                "project create",
                {"project": target, "template": template, "design": "Board1"},
            )
        target_info = self.cli("inspect-target", "project inspect", {"project": target})
        within(Path(target_info["central_library"]["path"]), target.parent)
        if not built:
            self.apply(
                "import-target-library",
                "library import",
                {
                    "project": target,
                    "from": source_library,
                    "parts": "QA_J1X02_P254,QA_R0805_10K",
                    "dangerous": True,
                },
            )
        target_records = {}
        for part in parts:
            number = part["number"]
            target_records[number] = library(
                self.cli(
                    "target-" + number, "library inspect", {"project": target, "part": number}
                ),
                part,
            )
            require(
                source_records[number]["part"] == target_records[number]["part"]
                and source_records[number]["cell"] == target_records[number]["cell"]
                and source_records[number]["padstacks"] == target_records[number]["padstacks"],
                "E_READBACK",
                "Source and imported target library contents differ",
                {"part": number},
            )
        self.write(
            "library-readbacks.json",
            {
                "source": source_records,
                "target": target_records,
                "library_instance_correspondence": "pending PCB getter observation",
            },
        )
        pid = self.context["applications"]["schematic"]["process"]["pid"]
        if not built:
            self.apply(
                "attach-target-designer",
                "session attach",
                {"kind": "schematic", "pid": pid, "project": target},
            )
            self.apply(
                "build-two-part-schematic",
                "schematic build",
                {"project": target, "design": inputs / "design.json", "dangerous": True},
            )
        actual_schematic = self.native("schematic-read")
        comparison = schematic(actual_schematic["snapshot"], expected_nets)
        self.write("independent-schematic-comparison.json", comparison)
        if built:
            self.reconcile(
                "reconcile-schematic",
                resumed["operation_id"],
                "applied",
                self.results / "independent-schematic-comparison.json",
            )
        if self.m.get("orphan_only"):
            self.run_orphan(target, fault, pid)
            return
        if self.m.get("failed_package"):
            package_evidence = self.native("failed-package-readback")
            self.write("failed-package-reconciliation.json", package_evidence)
            self.reconcile(
                "reconcile-failed-package",
                self.m["failed_package"]["operation_id"],
                "partial",
                self.results / "failed-package-reconciliation.json",
            )
        self.apply("materialize-package", "schematic package", {"project": target})
        self.apply(
            "create-owned-board",
            "pcb create",
            {
                "project": target,
                "template": "4 Layer Template",
                "name": "QA_ROOT",
                "design": "Board1",
            },
        )
        pcb_path = self.scratch / "target/PCB/QA_ROOT.pcb"
        pcb_pid = self.context["applications"]["pcb"]["process"]["pid"]
        self.apply(
            "attach-owned-board",
            "session attach",
            {"kind": "pcb", "pid": pcb_pid, "project": pcb_path},
        )
        self.apply(
            "import-schematic-into-board", "pcb update", {"project": target, "design": "Board1"}
        )
        baseline_board = self.native("pcb-read")
        self.write("pre-placement-native-contract.json", baseline_board)
        for refdes in ("J1", "R1"):
            goal = expected_geometry["placements"][refdes]
            self.apply(
                "place-" + refdes,
                "pcb placement move",
                {
                    "project": target,
                    "refdes": refdes,
                    "to": ",".join(str(n) for n in goal["origin_mm"]),
                    "rotate": goal["rotation_deg"],
                },
            )
        actual_board = self.native("pcb-read")
        comparison = pcb(actual_board["snapshot"], expected_geometry, expected_nets, target_records)
        self.write("independent-pcb-comparison.json", comparison)
        # Required pad shape/drill/layer coverage is never silently downgraded.
        self.write(
            "independent-pad-comparison.json",
            pad_correspondence(actual_board["snapshot"], expected_geometry),
        )
        persistence = self.native("persistence")
        schematic(persistence["schematic_reopened"], expected_nets)
        pcb(persistence["pcb_reopened"], expected_geometry, expected_nets, target_records)
        pad_correspondence(persistence["pcb_reopened"], expected_geometry)
        self.write(
            "independent-persistence-comparison.json",
            {
                "status": "passed",
                "source": "fresh getters after owned Save/Close/Open, "
                "independent frozen input comparison",
                "native": persistence,
            },
        )
        self.summary["representative"] = {
            "status": "passed",
            "persistence": "independently_reopened",
            "scope": "frozen two-part library-to-board case; no native DRC claim",
        }
        self.native("release-target")
        self.run_orphan(target, fault, pid)

    def run_orphan(self, target, fault, pid):
        self.apply(
            "create-orphan-project", "project create", {"project": fault, "template": target}
        )
        self.apply(
            "attach-orphan-project",
            "session attach",
            {"kind": "schematic", "pid": pid, "project": fault},
        )
        baseline = self.native("orphan-baseline")
        self.write("independent-orphan-baseline.json", baseline)
        evidence = self.native("orphan-fault")
        self.summary["orphan"] = orphan(
            evidence["before"], evidence["after"], evidence["adapter_error"], evidence["trace"]
        )
        self.write("independent-orphan-comparison.json", self.summary["orphan"])
        self.summary["status"] = "completed_with_deliberately_retained_partial_scratch_case"

    def reconcile(self, label, operation_id, outcome, evidence):
        row = self.cli(label + "-lookup", "operation show", {"id": operation_id})[
            "operation_status"
        ]
        if row.get("resolution"):
            require(
                row["resolution"]["outcome"] == outcome,
                "E_RESUME",
                "The previously reconciled outcome differs",
            )
        else:
            self.cli(
                label,
                "operation resolve",
                {"id": operation_id, "outcome": outcome, "evidence": str(evidence)},
            )

    def finish(self, error=None):
        if error:
            self.summary["status"] = "stopped"
            self.summary["stop"] = {
                "stage": self.current,
                "code": getattr(error, "code", "E_ENTRY"),
                "message": str(error),
                "details": getattr(error, "details", {}),
            }
        self.summary.update(
            completed_utc=utc(),
            calls=self.calls,
            last_stage=self.current,
            actual_readback_source="Only recorded native getters/exported bytes; "
            "expected/input values are never used as actual",
        )
        files = []
        for path in self.scratch.rglob("*"):
            if path.is_file():
                try:
                    within(path, self.scratch)
                    files.append(
                        {"path": str(path), "size": path.stat().st_size, "sha256": digest(path)}
                    )
                except (OSError, Stop) as exc:
                    files.append(
                        {"path": str(path), "sha256": None, "read_error": type(exc).__name__}
                    )
        self.write(
            "scratch-residue.json",
            {
                "observed_utc": utc(),
                "files": files,
                "native_dirty_or_active_state": "See the last successful bound native "
                "observation; no post-failure write, cleanup, app restart or replay is attempted",
                "windows_retained": True,
            },
        )
        self.write("summary.json", self.summary)
        stop = self.summary.get("stop") or {}
        print(
            json.dumps(
                {
                    "status": self.summary["status"],
                    "summary": str(self.results / "summary.json"),
                    "stage": self.current,
                    "stop_code": stop.get("code"),
                    "stop_message": stop.get("message"),
                    "windows_retained": True,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )


def execute(manifest):
    verify(manifest)
    caller = actor_guard(manifest)
    scratch, results = Path(manifest["scratch_root"]), Path(manifest["results_root"])
    require(
        not results.exists() and (manifest.get("resume_source") or not scratch.exists()),
        "E_REPLAY",
        "This run has existing scratch/evidence; preserve it and do not restart the fixed case",
    )
    results.mkdir()
    (results / "started-once.json").write_text(
        json.dumps({"utc": utc(), "caller": caller, "runner_pid": os.getpid()}) + "\n",
        encoding="utf-8",
    )
    scratch.mkdir(exist_ok=bool(manifest.get("resume_source")))
    for name in ("state", "wdir", "com-cache"):
        (scratch / name).mkdir(exist_ok=bool(manifest.get("resume_source")))
    scope = {
        "scope_id": "xpedition-native-user-v1",
        "tool": "xpedition-cli",
        "granted_by": "user task authorization for development and owned native QA projects",
        "granted_at": utc(),
        "expires_at": None,
        "effects": ["observe", "session", "artifact", "change", "persist", "configure"],
        "dangerous": True,
        "roots": [str(scratch)],
        "outputs": [str(scratch), str(results)],
        "destinations": [],
    }
    (scratch / "scope.json").write_text(json.dumps(scope, indent=2) + "\n", encoding="utf-8")
    runner = Runner(manifest, caller)
    try:
        runner.run()
    except BaseException as exc:
        runner.finish(exc)
        return 2
    runner.finish()
    return 0
