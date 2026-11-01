"""Review-only closed-board diagnostic; the SDK owns its public write loop."""

from .. import close_diagnostic as D
from ..cell_refresh import digest
from .common import confirmed, native, previewed


def close(options):
    bound = options.get("_close_diagnostic_binding")
    if not isinstance(bound, dict) or bound.get("diagnostic_contract") != D.CONTRACT:
        D.refuse("This diagnostic requires a fresh public SDK plan")
    scope = {"operation": D.CONTRACT, "binding_sha256": digest(bound)}
    if options.get("dry_run"):
        return previewed(
            {
                "binding_sha256": digest(bound),
                "binding": bound,
                "changes": [{"action": "Save_once_then_Close_True_once", "board": bound["board"]}],
                "constraints": [
                    "No engine, Go, Open, extra Save/Close or retry",
                    "Unknown terminal: no subsequent CAD request",
                    "Only returned terminal permits two bound read-only frames",
                ],
                "candidate_native_qualified": False,
            },
            scope,
        )
    confirmed(options, scope)
    return native().invoke("close_diagnostic", {"binding": bound}, timeout_seconds=D.WORKER_SECONDS)
