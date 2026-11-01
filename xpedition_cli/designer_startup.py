"""Offline preparation for a CLI-owned Designer session, never a CAD launcher.

The installed startup samples establish the host-provided Application entry.
They do not establish exclusive configuration selection or no-instance-reuse.
Those missing terms block native launch, not this module's pure preparation.
Nothing here reads configuration, creates runtime files, imports COM or starts
a process. The retained running-Run experiment is not a fallback.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .errors import CLIError

SCHEME = "designer-dedicated-startup-v1"
SCRIPT_NAME = "cli-designer-startup.vbs"

# Proposed private bundle, not installed or an assertion of loader precedence.
# OfferStartupApplication is our proposed broker method, not a Siemens API.
_SCRIPT = b"""Option Explicit
Dim hostApplication, executionOwner
On Error Resume Next
Set hostApplication = Application
If Err.Number = 0 Then
    Set executionOwner = GetObject("__PRIVATE_OWNER_OBJREF__")
    If Err.Number = 0 Then
        Call executionOwner.OfferStartupApplication(hostApplication, "__CONNECTION_EPOCH__")
    End If
End If
Set executionOwner = Nothing
Set hostApplication = Nothing
On Error GoTo 0
"""
_INI = ("[ViewDraw]\nScript#0=" + SCRIPT_NAME + "\n").encode("ascii")


@dataclass(frozen=True)
class StartupPreparation:
    """Immutable, unrendered bytes; contains no endpoint or connection state."""

    scripts_ini: bytes = field(default=_INI, init=False)
    host_script: bytes = field(default=_SCRIPT, init=False)

    def manifest(self) -> dict:
        return {
            "scheme": SCHEME,
            "mode": "offline_preparation",
            "native_launch_enabled": False,
            "materialized": False,
            "private_endpoint_rendered": False,
            "files": [
                {
                    "name": name,
                    "bytes": len(body),
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "status": "unrendered_proposal",
                }
                for name, body in (
                    ("scripts.ini", self.scripts_ini),
                    (SCRIPT_NAME, self.host_script),
                )
            ],
        }


def prepare_startup() -> StartupPreparation:
    """Pure template preparation, not authorization to stage or activate it."""
    return StartupPreparation()


def startup_readiness() -> dict:
    """Bounded facts/qualification gaps, with no caller-supplied enable flag."""
    return {
        "scheme": SCHEME,
        "target_product": "Xpedition Enterprise 2604 / ViewDraw 151.0",
        "recommended_entry": "private_startup_script_host_Application",
        "native_launch_enabled": False,
        "native_connection_qualified": False,
        "arbitrary_running_instance_takeover": "unsupported",
        "runtime_ExecuteCommand_Run": "not_used",
        "global_configuration_changes": False,
        "installed_components": [],
        "evidence": {
            "startup_host_Application": "installed_sample",
            "scripts_ini_ViewDraw_entry": "installed_sample",
            "private_configuration_exclusive_selection": "unverified",
            "launcher_does_not_reuse_existing_Designer": "unverified",
            "host_object_transport_and_owner_apartment": "native_unverified",
            "connection_command_lifetime": "offline_model_only",
        },
        "native_launch_blockers": [
            "exclusive_startup_configuration_selection_unproven",
            "no_existing_instance_reuse_unproven",
            "authenticated_host_object_transport_unqualified",
        ],
        "history_is_not_a_prerequisite": True,
        "legacy_unknown_adoption": False,
        "preparation": prepare_startup().manifest(),
    }


def require_dedicated_designer():
    """Refuse native start/takeover before original state, OS, tokens or COM."""
    raise CLIError(
        "E_HUMAN_REQUIRED",
        "Dedicated Designer startup is prepared offline but is not native-qualified; "
        "running-instance takeover is unsupported",
        {
            "action": "other",
            "resume": 'xpedition-cli reference --command "session start"',
            "next_action": "Review the dedicated startup experiment's configuration "
            "isolation, no-instance-reuse and authenticated host-handoff prerequisites. "
            "Do not retry runtime Run, adopt an old instance or replay an unknown.",
            "write_attempted": False,
            "state_write_attempted": False,
            "native_request_dispatched": False,
            "native_execution_enabled": False,
            "startup_readiness": startup_readiness(),
        },
    )
