"""Stable creation resource planning; no CAD query or execution permission."""

from __future__ import annotations

import copy
import re
from fractions import Fraction

from .designer_source_binding import MAX_BYTES, MAX_FILES
from .errors import CLIError

SCHEME = "project-create-cost-v1"
HARD_CAP_SECONDS = 600
MODEL = {
    "fixed_step_seconds": 180,
    "file_seconds": 30,
    "reference_files": 610,
    "byte_seconds": 30,
    "reference_bytes": 132655280,
    "round_up_seconds": 15,
    "measured_complete_command_seconds": 158.875,
    "measured_source_and_copy_seconds": 19.640,
    "qualification": "bounded resource planning; not a throughput or template qualification",
}


def required_seconds(file_count, total_bytes):
    """Pure cost arithmetic shared by declared preflight and observed source plans.

    Counts do not provide a source identity or authorize a native request.
    """
    if (
        type(file_count) is not int
        or not 1 <= file_count <= MAX_FILES
        or type(total_bytes) is not int
        or not 1 <= total_bytes <= MAX_BYTES
    ):
        raise ValueError("bounded positive source counts required")
    estimate = (
        Fraction(MODEL["fixed_step_seconds"])
        + Fraction(MODEL["file_seconds"] * file_count, MODEL["reference_files"])
        + Fraction(MODEL["byte_seconds"] * total_bytes, MODEL["reference_bytes"])
    )
    quantum = MODEL["round_up_seconds"]
    blocks = (estimate.numerator + quantum * estimate.denominator - 1) // (
        quantum * estimate.denominator
    )
    return quantum * blocks


def cost_for_source(source):
    if (
        type(source) is not dict
        or source.get("scheme") != "setup-template-closure-v1"
        or type(source.get("file_count")) is not int
        or not 1 <= source["file_count"] <= MAX_FILES
        or type(source.get("total_bytes")) is not int
        or not 1 <= source["total_bytes"] <= MAX_BYTES
        or not re.fullmatch(r"[a-f0-9]{64}", str(source.get("closure_sha256")))
    ):
        raise CLIError(
            "E_CONFLICT",
            "Creation requires a complete bounded setup source envelope",
            {"write_attempted": False, "native_request_dispatched": False},
        )
    return {
        "scheme": SCHEME,
        "source": {key: source[key] for key in ("closure_sha256", "file_count", "total_bytes")},
        "required_seconds": required_seconds(source["file_count"], source["total_bytes"]),
        "native_hard_cap_seconds": HARD_CAP_SECONDS,
        "model": copy.deepcopy(MODEL),
    }


def require_cost(source, promised=None):
    cost = cost_for_source(source)
    if promised is not None and (type(promised) is not dict or promised != cost):
        raise CLIError(
            "E_CONFLICT",
            "The signed creation cost differs from the observed source envelope",
            {"write_attempted": False, "native_request_dispatched": False},
        )
    if cost["required_seconds"] > HARD_CAP_SECONDS:
        raise CLIError(
            "E_UNSUPPORTED",
            "This setup source exceeds the bounded creation budget",
            {
                "write_attempted": False,
                "native_request_dispatched": False,
                "project_create_cost": cost,
            },
        )
    return cost
