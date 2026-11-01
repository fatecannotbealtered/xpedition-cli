"""Comparison-only Net0 references after one verified component-trace reopen.

Every native mask receipt is first checked against its original raw state.
Only the separate design comparison view may use the old default reference.
This module neither authorizes a write nor manufactures native mask evidence.
"""

from __future__ import annotations

import copy
from collections import Counter

from . import native_pad_mask as M
from . import net0_reopen_identity as N
from .cell_refresh import digest
from .design_capabilities import UNASSIGNED_NET_NAME

HEADER_FIELDS = N.HEADER_FIELDS


def _headers(state):
    materials, identities = state["all_pin_pad_material"], state["pin_identities"]
    if not isinstance(materials, list) or not isinstance(identities, list):
        raise ValueError("Complete physical pin and material identities are required")
    headers = [{key: row[key] for key in HEADER_FIELDS} for row in materials]
    if Counter(tuple(row[key] for key in HEADER_FIELDS) for row in identities) != Counter(
        tuple(row[key] for key in HEADER_FIELDS) for row in headers
    ):
        raise ValueError("Complete pin/material projections disagree")
    if len({(row["component_uid"], row["pin_uid"]) for row in headers}) != len(headers):
        raise ValueError("Physical pin identities are duplicated")
    return headers


def default_uid(state):
    """Read a default reference without treating it as a conducting network."""
    values = {row["net_uid"] for row in _headers(state) if row["net"] == UNASSIGNED_NET_NAME}
    if len(values) > 1:
        raise ValueError("Default pin references are ambiguous")
    return next(iter(values), None)


def _raw_design(state):
    result = copy.deepcopy(state)
    result.pop("native_mask_proof", None)
    return result


def _project(state, proof, side):
    """Keep every design field; replace only four bound pin-reference mirrors."""
    result = _raw_design(state)
    projector = N.pin_reference_projector(proof, side)
    headers = _headers(state)
    by_pin = {(row["component_uid"], row["pin_uid"]): row for row in headers}
    for key in ("all_pin_pad_material", "pin_identities"):
        result[key] = [projector(row) for row in result[key]]
    geometry = result["geometry"]
    for projection in ("pins", "geometry"):
        observed = set()
        for component in geometry["components"]:
            for row in component[projection]:
                key = component["uid"], row["uid"]
                if key in observed or key not in by_pin:
                    raise ValueError("Complete geometry pin mirrors are missing or duplicated")
                header = {
                    "component_uid": component["uid"],
                    "pin_uid": row["uid"],
                    "refdes": component["refdes"],
                    "pin": row["number"],
                    "net": row["net"],
                    "net_uid": row["net_uid"],
                }
                if header != by_pin[key]:
                    raise ValueError("Geometry pin mirror differs from complete raw material")
                row["net_uid"] = projector(header)["net_uid"]
                observed.add(key)
        if observed != set(by_pin):
            raise ValueError("Geometry projection does not cover every physical pin")
    return result


def compare_reopened(reference_state, reopened_state, request, lifecycle):
    """Recompute raw evidence and the entire qualified after-edit/reopen comparison.

    The reference is the already accepted *after-edit* state, not the pre-write
    state. Thus no added, removed or changed copper/geometry is allowed here.
    ``None`` means the original strict comparison was sufficient, not lifecycle
    qualification. No-op callers must never invoke a default-reference change.
    """
    for state in (reference_state, reopened_state):
        verified = M.verify_component_evidence(state, request)
        if verified.get("valid") is not True:
            raise ValueError("Unmodified native mask evidence did not verify: " + str(verified))
    before_view, after_view = _raw_design(reference_state), _raw_design(reopened_state)
    if before_view == after_view:
        return None
    old_uid, new_uid = default_uid(reference_state), default_uid(reopened_state)
    if old_uid is None or new_uid is None or old_uid == new_uid:
        raise ValueError("Reopened complete protected state differs without a Net0 substitution")
    before_nets = reference_state["geometry"]["model"]["connectivity"]["nets"]
    after_nets = reopened_state["geometry"]["model"]["connectivity"]["nets"]
    proof = N.prove_net0_reopen(
        before_nets,
        _headers(reference_state),
        after_nets,
        _headers(reopened_state),
        lifecycle=lifecycle,
    )
    old_view, new_view = (
        _project(reference_state, proof, "before"),
        _project(reopened_state, proof, "after"),
    )
    if old_view != new_view:
        raise ValueError("Non-reference design, copper or material changed on reopening")
    return {
        "reference_state": copy.deepcopy(reference_state),
        "lifecycle": copy.deepcopy(proof["lifecycle"]),
        "identity_proof": proof,
        "raw_design_before_sha256": digest(before_view),
        "raw_design_after_sha256": digest(after_view),
        "comparison_design_sha256": digest(old_view),
    }


def protection_projection(reopened_state, request, comparison):
    """Independently reconstruct a stored comparison before using its projection."""
    if not isinstance(comparison, dict):
        raise ValueError("A recomputable qualified reopen comparison is required")
    rebuilt = compare_reopened(
        comparison["reference_state"], reopened_state, request, comparison["lifecycle"]
    )
    if rebuilt is None or rebuilt != comparison:
        raise ValueError("The qualified default-reference comparison changed")
    return _project(reopened_state, rebuilt["identity_proof"], "after")
