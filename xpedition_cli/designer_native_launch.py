"""Fixed empty-session launch resources owned by the native owner process.

Import is inert. Actual launch rechecks the OS scene, uses the installed common
launcher without invented flags/project arguments, and fills missing license
settings in its owner process before constructing the child env. No operation
here adopts an old session, activates COM or terminates CAD.
The caller must retain the returned lease through the actual native terminal
boundary. Evidence files are deliberately retained when handles are released.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path

from . import designer_startup as S
from . import native_process as P
from .execution_lifecycle import _plain_path, _private_mkdir
from .startup_input_seal import InputsLease, SealError

SCHEME = "designer-native-empty-launch-v1"
PROJECT_SCHEME = "designer-native-new-setup-project-launch-v1"
STANDARD_SCRIPTS_SHA256 = "8554e7e8cb2420fd4ea68c2f38dfb70dec78f43baaa50eb3b7b19d04a5a4c7ce"
SETUP_FILES = {
    "SystemDesign.prj": "bf09b72abbf7ad3d61114f5c46e20c7073f8cd576ae6a1bb907f85d01ba3bc02",
    "SystemDesign/borders.ini": "59c8b39a565eecf1dae75698cb89791beb4490044cbffc074c9a2c1981b7c81d",
    "SystemDesign/busconts.ini": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "SystemDesign/speccomp.ini": "62f633b20b6f2d184b4fd3bb1a0b360dc73bd1bc7b4bb9ece6aa46043ea76fb5",
}
# Diagnostics write only an append-only marker in this new owner's private
# startup directory, never project data. A marker is NOT identity authority.
# Application is the host's named item. Without Option Explicit, a missing
# named item can be caught/reported as an object error instead of silently
# preventing the entire diagnostic script from compiling. No Eval/Execute.
_DIAGNOSTIC_SCRIPT = b"""Dim hostApplication, executionOwner, phaseError
Sub Mark(stage, code)
    On Error Resume Next
    Dim fs, output
    Set fs = CreateObject("Scripting.FileSystemObject")
    Set output = fs.OpenTextFile("__MARKER_PATH__", 8, True, 0)
    output.WriteLine stage & ":" & CStr(code)
    output.Close
    Set output = Nothing
    Set fs = Nothing
    Err.Clear
End Sub
On Error Resume Next
Call Mark("entered", 0)
Set executionOwner = GetObject("__PRIVATE_OWNER_OBJREF__")
phaseError = Err.Number
Call Mark("broker_bind_returned", phaseError)
If phaseError = 0 Then
    Err.Clear
    Set hostApplication = Application
    phaseError = Err.Number
    Call Mark("application_named_item", phaseError)
    If phaseError = 0 Then
        Err.Clear
        Call executionOwner.OfferStartupApplication(hostApplication, "__CONNECTION_EPOCH__")
        phaseError = Err.Number
        Call Mark("offer_returned", phaseError)
    End If
End If
Set executionOwner = Nothing
Set hostApplication = Nothing
Call Mark("script_tail_reached", 0)
On Error GoTo 0
"""


class LaunchError(ValueError):
    """Fixed safe error: never returns endpoint, private path or raw exception."""


_INPUT_FAILURE_REASONS = frozenset(
    "startup_input_" + suffix
    for suffix in (
        "contract_incomplete",
        "access_mask_mismatch",
        "identity_mismatch",
        "readback_mismatch",
        "security_mismatch",
        "resource_limit",
        "native_api_failed",
        "handle_release_failed",
    )
)


def _static_library_entries(source):
    """Bound enumeration before descending; never follow a nested junction."""
    pending, entries, total = [source], [], 0
    while pending:
        directory = pending.pop()
        for item in sorted(directory.iterdir()):
            info = item.lstat()
            if getattr(info, "st_file_attributes", 0) & 0x400:
                raise LaunchError("static_library_tree_bound_or_reparse_refused")
            if stat.S_ISDIR(info.st_mode):
                pending.append(item)
            elif stat.S_ISREG(info.st_mode):
                total += info.st_size
            else:
                raise LaunchError("static_library_nonregular_entry_refused")
            entries.append(item)
            if len(entries) > 8192 or total > 512 * 1024 * 1024:
                raise LaunchError("static_library_tree_resource_bound")
    return sorted(entries)


def render_script(endpoint: str, connection_epoch: str, *, marker_path=None) -> bytes:
    if not isinstance(endpoint, str) or not re.fullmatch(
        r"objref:[A-Za-z0-9+/=]{32,8192}:", endpoint
    ):
        raise LaunchError("private_objref_moniker_required")
    if not isinstance(connection_epoch, str) or not re.fullmatch(r"[a-f0-9]{32}", connection_epoch):
        raise LaunchError("private_connection_epoch_required")
    body = S.prepare_startup().host_script
    if marker_path is not None:
        path = Path(marker_path)
        if not path.is_absolute() or not re.fullmatch(
            r"[A-Za-z]:[\\/][A-Za-z0-9_./\\-]+", str(path)
        ):
            raise LaunchError("private_plain_marker_path_required")
        if ".." in path.parts:
            raise LaunchError("private_plain_marker_path_required")
        body = _DIAGNOSTIC_SCRIPT.replace(b"__MARKER_PATH__", str(path).encode("ascii"))
    if body.count(b"__PRIVATE_OWNER_OBJREF__") != 1 or body.count(b"__CONNECTION_EPOCH__") != 1:
        raise LaunchError("fixed_startup_template_changed")
    return body.replace(b"__PRIVATE_OWNER_OBJREF__", endpoint.encode("ascii")).replace(
        b"__CONNECTION_EPOCH__", connection_epoch.encode("ascii")
    )


def child_environment(
    home: Path, work: Path, inherited: dict | None = None, *, inputs: Path
) -> dict:
    """Override only this new child's installation/work selection; log no values."""
    if inputs == work or inputs.parent != work.parent:
        raise LaunchError("startup_work_and_sealed_inputs_must_be_distinct_siblings")
    result = dict(os.environ if inherited is None else inherited)

    def put(name, value):
        for key in tuple(result):
            if key.casefold() == name.casefold():
                del result[key]
        result[name] = str(value)

    for name, value in {
        "SDD_HOME": home,
        "XPEDITION_SDD_HOME": home,
        "SDD_PLATFORM": "win64",
        "SDD_VERSION": home.parent.name,
        "MENTOR_ROOT": home.parent,
        "TARGET": home.parent,
        "SDD_WV": "wv",
        "SDD_WG": "wg",
        "SDD_DX": "dx",
        "VCO": "ixw",
        "MGC_WD": work,
        "XPEDITION_MGC_WD": work,
        "MGC_TMPDIR": work,
        "WDIR": os.pathsep.join((str(inputs), str(work), str(home / "standard"))),
    }.items():
        put(name, value)
    # Preserve licensing/authentication values verbatim; no discovery or logging.
    path_key = next((key for key in result if key.casefold() == "path"), "PATH")
    path = result.get(path_key, "")
    additions = [
        home / item
        for item in ("common/win64/bin", "common/win64/lib", "wv/win64/bin", "wv/win64/lib")
    ]
    put("PATH", os.pathsep.join([*(str(item) for item in additions), path]))
    return result


def _work_directory_identity(path):
    """Fresh os.lstat supplies the Windows file index without a data-read pin."""
    observed = path.lstat()
    if (
        not stat.S_ISDIR(observed.st_mode)
        or getattr(observed, "st_file_attributes", 0) & 0x400
        or type(observed.st_dev) is not int
        or type(observed.st_ino) is not int
        or observed.st_dev < 0
        or observed.st_ino <= 0
    ):
        raise LaunchError("startup_writable_work_identity_incomplete")
    return [observed.st_dev, observed.st_ino]


def _work_directory_empty(path):
    with os.scandir(path) as entries:
        return next(entries, None) is None


def _empty_scene():
    rows = P.processes("schematic")
    if rows:
        raise LaunchError("existing_designer_blocks_dedicated_empty_launch")


def _validate_plan(plan):
    if (
        not isinstance(plan, dict)
        or set(plan) != {"scheme", "installation_root", "runtime_directory"}
        or plan.get("scheme") not in {SCHEME, PROJECT_SCHEME}
    ):
        raise LaunchError("fixed_empty_launch_plan_required")
    home, root = Path(plan["installation_root"]), Path(plan["runtime_directory"])
    for path in (home, root):
        if not path.is_absolute() or not re.fullmatch(
            r"[A-Za-z]:[\\/][A-Za-z0-9_./\\-]+", str(path)
        ):
            raise LaunchError("plain_absolute_ascii_launch_path_required")
        if ".." in path.parts:
            raise LaunchError("launch_parent_traversal_refused")
    if home.name != "SDD_HOME" or home.parent.name != "XENTP2604":
        raise LaunchError("audited_current_installation_required")
    return home, root


def constrain_plan_to_owner(plan, owner_root):
    """Pure binding: startup resources must be the fresh owner's direct child."""
    _home, runtime = _validate_plan(plan)
    expected = Path(owner_root) / "startup"
    if os.path.normcase(os.path.abspath(runtime)) != os.path.normcase(os.path.abspath(expected)):
        raise LaunchError("startup_runtime_must_be_new_owner_child")
    return {**plan, "runtime_directory": str(expected)}


class LaunchLease:
    """Original launcher pin plus frozen startup resources, never an auto-Quit."""

    def __init__(self, plan, endpoint, epoch):
        self.home, self.root = _validate_plan(plan)
        self.scheme = plan["scheme"]
        self.work_directory = self.root / "work"
        self._work_identity = None
        self._work_initially_empty = False
        self.project_path = None
        self.project_initialization_observed = None
        self.payload = render_script(
            endpoint,
            epoch,
            marker_path=self.root / "bootstrap-observation.log",
        )
        self.process = self.launcher_pin = self.inputs = None
        self.worker_pin = None
        self.expected_generation = None
        self.launch_attempted = False
        self._released = False
        self.component_stage = "startup.preflight"

    def launch(self):
        # Check reparse ancestors before creating anything, not after mkdir.
        self.root = _plain_path(self.root)
        self.home = _plain_path(self.home)
        _empty_scene()
        standard = self.home / "standard" / "scripts.ini"
        if hashlib.sha256(standard.read_bytes()).hexdigest() != STANDARD_SCRIPTS_SHA256:
            raise LaunchError("audited_standard_startup_configuration_changed")
        executable = self.home / "common/win64/bin/viewdraw.exe"
        worker = self.home / "wv/win64/bin/viewdraw.exe"
        if not executable.is_file() or not worker.is_file():
            raise LaunchError("installed_designer_launcher_or_worker_missing")
        _private_mkdir(self.root)
        if self.scheme == PROJECT_SCHEME:
            self.component_stage = "startup.prepare_project"
            self.project_path = self._prepare_new_setup_project()
        # Assign before any input IO so a partial failure retains its owner.
        # No data-read pin survives creation: the installed reader uses share=0.
        self.component_stage = "startup.create_inputs"
        self.work_directory = _plain_path(self.root / "work")
        _private_mkdir(self.work_directory)  # Existing/old workspaces are never reused.
        self.work_directory = self.work_directory.resolve(strict=True)
        if self.work_directory.parent != self.root.resolve(strict=True):
            raise LaunchError("startup_writable_work_boundary_changed")
        self._work_identity = _work_directory_identity(self.work_directory)
        if not _work_directory_empty(self.work_directory):
            raise LaunchError("startup_writable_work_must_start_empty")
        self._work_initially_empty = True
        self.inputs = InputsLease(self.root / "inputs")
        body = b"[ViewDraw]\nScript#0=identity.vbs\n"
        self.inputs.prepare({"identity.vbs": self.payload, "scripts.ini": body})
        # This runs in the dedicated owner, never in the invoking CLI. Reuse the
        # adapter's read-only configured-license precedence and atomic expansion.
        from .native_com_adapter import _inherit_license_environment

        _inherit_license_environment()
        environment = child_environment(
            self.home, self.work_directory, inputs=self.inputs.directory
        )
        native_home = self.home.parent / "MGC_HOME.ixw"
        if native_home.is_dir():
            environment["MGC_HOME"] = environment["SDD_MGC_HOME"] = str(native_home)
        if not any(key.casefold() == "mgls_license_file" for key in environment):
            installed_license = self.home.parent.parent / "LICENSE.DAT"
            if installed_license.is_file():
                environment["MGLS_LICENSE_FILE"] = str(installed_license)
        self.component_stage = "startup.verify_inputs"
        self.recheck_resources()
        _empty_scene()  # Fresh check immediately before dispatch, not yesterday's audit.
        # No shell, project argument, arbitrary flags, registry edits or services.
        self.launch_attempted = True
        arguments = [str(executable)]
        if self.project_path is not None:
            # Current HKCR designerproject and the installed Dashboard sample
            # both declare this exact form. This is not a guessed CLI switch.
            arguments.extend(("--opensheet", "--", str(self.project_path)))
        self.component_stage = "startup.spawn"
        from .designer_frozen_binding import (
            external_application_dll_search,
            external_child_environment,
        )

        environment = external_child_environment(environment)
        with external_application_dll_search():
            self.process = subprocess.Popen(
                arguments,
                cwd=str(self.work_directory),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        self.component_stage = "startup.pin_launcher"
        self.launcher_pin = P.ProcessHandle("schematic", self.process.pid)
        self.launcher_pin.require_spawned(self.process)
        if Path(self.launcher_pin.identity["executable"]).resolve() != executable.resolve():
            raise LaunchError("original_launched_executable_differs")
        return self

    def _prepare_new_setup_project(self):
        """Copy only reviewed setup inputs; native initialization stays unknown."""
        if P.processes("library"):
            raise LaunchError("library_manager_blocks_static_library_snapshot")
        source = self.home / "standard/templates/dxdesigner/systemdesign"
        destination = self.root.parent / "project"
        _private_mkdir(destination)
        _private_mkdir(destination / "SystemDesign")
        library_source = _plain_path(self.home / "standard/examples/SystemDesign")
        library_destination = destination / "Library"
        entries = _static_library_entries(library_source)
        files = [item for item in entries if item.is_file()]
        if sum(item.stat().st_size for item in files) > 512 * 1024 * 1024:
            raise LaunchError("static_library_byte_bound")
        _private_mkdir(library_destination)
        for item in (entry for entry in entries if entry.is_dir()):
            _private_mkdir(library_destination / item.relative_to(library_source))
        hashes = {}
        for item in files:
            raw = item.read_bytes()
            relative = str(item.relative_to(library_source))
            hashes[relative] = hashlib.sha256(raw).hexdigest()
            with (library_destination / relative).open("xb") as stream:
                stream.write(raw)
        current = _static_library_entries(library_source)
        if current != entries or any(
            hashlib.sha256(item.read_bytes()).hexdigest()
            != hashes[str(item.relative_to(library_source))]
            for item in files
        ):
            raise LaunchError("static_library_changed_during_snapshot")
        for relative, expected in SETUP_FILES.items():
            raw = (source / relative).read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected:
                raise LaunchError("installed_setup_project_input_changed")
            if relative == "SystemDesign.prj":
                original = b"${SDD_HOME}/standard/examples/SystemDesign/SystemDesign.lmc"
                if raw.count(original) != 1:
                    raise LaunchError("setup_project_library_reference_changed")
                raw = raw.replace(
                    original,
                    str(library_destination / "SystemDesign.lmc").encode("ascii"),
                )
            with (destination / relative).open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if hashlib.sha256((source / relative).read_bytes()).hexdigest() != expected:
                raise LaunchError("installed_setup_project_input_changed_during_copy")
        with (destination / "setup-materialization.json").open("x", encoding="utf-8") as stream:
            json.dump(
                {
                    "setup_template_inputs": SETUP_FILES,
                    "static_library_files": hashes,
                    "native_database_initialization_observed": None,
                    "source_library_unchanged_after_copy": True,
                    "installed_library_writes_authorized": False,
                },
                stream,
                indent=2,
            )
        return destination / "SystemDesign.prj"

    def recheck_resources(self):
        if self.inputs is None:
            raise LaunchError("startup_inputs_not_prepared")
        facts = self.inputs.verify_prelaunch()
        if not isinstance(facts, dict) or facts.get("input_contract_verified") is not True:
            raise LaunchError("startup_input_contract_not_verified")
        if (
            not self._work_initially_empty
            or _plain_path(self.work_directory).parent != self.root
            or self.work_directory.name != "work"
            or _work_directory_identity(self.work_directory) != self._work_identity
        ):
            raise LaunchError("startup_writable_work_identity_changed")
        return facts

    def private_work_binding(self):
        """Private MAC evidence only; this writable cache is not a script source grant."""
        if self._work_identity is None:
            return None
        return {
            "directory": str(self.work_directory),
            "parent": str(self.root),
            "file_id": list(self._work_identity),
            "initially_empty": self._work_initially_empty,
            "directory_data_pin_held": False,
        }

    def resolve_generation(self):
        """Use the original handle and two full snapshots; no COM candidate getter."""
        if self.expected_generation is not None:
            if self.worker_pin is None or self.worker_pin.exited():
                raise LaunchError("observed_worker_generation_has_ended")
            return dict(self.expected_generation)
        if self.launcher_pin is None:
            raise LaunchError("original_launcher_pin_unavailable")
        seed = P.capture_startup_seed("schematic", self.launcher_pin)
        expected_image = os.path.normcase(str(self.home / "wv/win64/bin/viewdraw.exe"))
        matches = [
            item
            for item in seed["candidates"]
            if os.path.normcase(item["process_chain"][-1]["executable"]) == expected_image
        ]
        if len(matches) != 1:
            raise LaunchError("fresh_worker_generation_not_yet_unambiguous")
        identity = dict(matches[0]["process_chain"][-1])
        # This proof is process launch ownership only. The broker independently
        # authenticates the callback and host-provided Application before admission.
        self.expected_generation = {
            "pid": identity["pid"],
            "birth_filetime_100ns": identity["creation_time_100ns"],
            "executable": identity["executable"],
        }
        self.worker_pin = P.ProcessHandle("schematic", identity["pid"], identity)
        self.startup_provenance = seed
        return dict(self.expected_generation)

    def snapshot(self):
        if self.expected_generation is None:
            return {"alive": None, "worker_generation_observed": False}
        expected = self.expected_generation
        if self.worker_pin is None:
            raise LaunchError("worker_generation_handle_unavailable")
        return {
            **expected,
            "alive": not self.worker_pin.exited(),
            "launcher_alive": not self.launcher_pin.exited(),
            "worker_generation_observed": True,
        }

    def close(self):
        """Release own handles only after the owner has proved the native boundary.

        No files are deleted or made writable. The retained inputs remain
        immutable; ordinary-user cleanup is not promised. No CAD command,
        signal or termination is performed. Pending calls keep this lease.
        """
        if self._released:
            return
        if self.process is not None and (
            self.worker_pin is None
            or not self.worker_pin.exited()
            or not self.launcher_pin.exited()
        ):
            raise LaunchError("pending_native_generation_retains_launch_resources")
        if self.launch_attempted and self.process is None:
            raise LaunchError("unknown_launch_outcome_retains_launch_resources")
        # Populate Popen.returncode through its original handle before closing
        # that handle; __del__ must not later poll a manually closed handle.
        if self.process is not None and self.process.poll() is None:
            raise LaunchError("original_launcher_exit_not_yet_observed")
        if self.inputs is not None:
            facts = self.inputs.terminal_release()
            if (
                not isinstance(facts, dict)
                or facts.get("resources_released") is not True
                or facts.get("files_removed") is not False
                or facts.get("immutable_files_retained") is not True
            ):
                raise LaunchError("startup_input_handle_release_incomplete")
        if self.worker_pin is not None:
            self.worker_pin.close()
        if self.launcher_pin is not None:
            self.launcher_pin.close()
        if self.process is not None and getattr(self.process, "_handle", None) is not None:
            self.process._handle.Close()
            self.process._handle = None
        self._released = True


def prepare_and_launch(plan: dict, endpoint: str, connection_epoch: str) -> LaunchLease:
    """Explicit native operation for a new empty trial, never an import effect.

    Even failure retains the live lease on its exception so the owner cannot
    discard startup locks after dispatch. The owner must preserve unresolved state.
    """
    lease = LaunchLease(plan, endpoint, connection_epoch)
    try:
        return lease.launch()
    except BaseException as exc:
        error = LaunchError("dedicated_empty_launch_incomplete")
        error.lease = lease
        error.component_stage = lease.component_stage
        category = getattr(exc, "category", None) if isinstance(exc, SealError) else None
        if isinstance(exc, SealError):
            error.component_reason = (
                category
                if isinstance(category, str) and category in _INPUT_FAILURE_REASONS
                else "startup_input_contract_incomplete"
            )
        elif isinstance(exc, LaunchError) and str(exc) == (
            "existing_designer_blocks_dedicated_empty_launch"
        ):
            error.component_reason = str(exc)
        elif isinstance(exc, LaunchError) and str(exc) in {
            "startup_inputs_not_prepared",
            "startup_input_contract_not_verified",
        }:
            error.component_reason = "startup_input_contract_incomplete"
        else:
            error.component_reason = "dedicated_native_launch_incomplete"
        native_error = getattr(exc, "winerror", None)
        error.winerror = native_error if type(native_error) is int else None
        error.dispatched = (
            True if lease.process is not None else None if lease.launch_attempted else False
        )
        raise error from exc
