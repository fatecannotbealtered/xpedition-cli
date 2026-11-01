"""Comparison-only binding for Layout's rebuilt unassigned Net0 reference.

Raw native observations, signatures and mask evidence are never rewritten.  This
module proves a single observed default-reference substitution across a verified
Save/Close/ExactOpen lifecycle.  It is not a general network-ID normalization.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
from typing import Any

from .design_capabilities import UNASSIGNED_NET_NAME, named_net_ownership

PROFILE = "verified-reopen-net0-reference-v1"
MAX_PINS = 16384
MAX_NETS = 16384
HEADER_FIELDS = ("component_uid", "pin_uid", "refdes", "pin", "net", "net_uid")
MEMBER_FIELDS = HEADER_FIELDS[:-1]


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _path(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("A complete absolute native path is required")
    if PureWindowsPath(value).is_absolute():
        return str(PureWindowsPath(value)).casefold()
    if Path(value).is_absolute():
        return os.path.normcase(os.path.normpath(value))
    raise ValueError("A complete absolute native path is required")


def validate_lifecycle(value: Any) -> dict:
    """Validate the caller's native lifecycle facts; no Boolean opt-in suffices."""
    if not isinstance(value, dict):
        raise ValueError("Verified native Save/Close/ExactOpen evidence is required")
    for key in ("save_count", "close_count", "open_count"):
        if type(value.get(key)) is not int or value[key] != 1:
            raise ValueError("One native Save/Close/ExactOpen is required")
    for key in ("save_returned", "close_returned", "closed_observed", "open_returned_document"):
        if value.get(key) is not True:
            raise ValueError(
                "Native Boolean replies, empty close and returned document are required"
            )
    expected, reopened = _path(value.get("expected_path")), _path(value.get("reopened_path"))
    if expected != reopened:
        raise ValueError("Net0 reference equivalence requires the exact same reopened file")
    identities = []
    for key in ("process_before", "process_after"):
        item = value.get(key)
        if not isinstance(item, dict) or type(item.get("pid")) is not int or item["pid"] <= 0:
            raise ValueError("The owned native process identity is incomplete")
        birth = item.get("creation_time_100ns")
        if not isinstance(birth, str) or not birth.isdecimal() or int(birth) <= 0:
            raise ValueError("The native process generation is required")
        identities.append(
            {
                "pid": item["pid"],
                "creation_time_100ns": birth,
                "executable": _path(item.get("executable")),
            }
        )
    if identities[0] != identities[1]:
        raise ValueError("Native PID, creation time or executable changed during reopening")
    return {
        **{key: value[key] for key in ("save_count", "close_count", "open_count")},
        **{
            key: True
            for key in (
                "save_returned",
                "close_returned",
                "closed_observed",
                "open_returned_document",
            )
        },
        "expected_path": expected,
        "reopened_path": reopened,
        "process_before": identities[0],
        "process_after": identities[1],
    }


def _headers(rows: Any) -> list[dict]:
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_PINS:
        raise ValueError("A complete bounded physical pin inventory is required")
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Malformed native pin reference")
        try:
            header = {key: row[key] for key in HEADER_FIELDS}
        except KeyError as error:
            raise ValueError("A physical pin reference field is missing") from error
        if any(not isinstance(value, str) or not value for value in header.values()) or any(
            header[key] == "0" for key in ("component_uid", "pin_uid", "net_uid")
        ):
            raise ValueError("Complete physical pin/owner/network identities are required")
        result.append(header)
    return sorted(result, key=lambda row: (row["component_uid"], row["pin_uid"]))


def prove_net0_reopen(
    before_nets: Any,
    before_pins: Any,
    after_nets: Any,
    after_pins: Any,
    *,
    lifecycle: Any,
) -> dict:
    """Prove an actual default UID change; preserve every raw header in its hash.

    The complete named census and every physical pin membership are compared.
    Callers still verify all geometry and the unchanged raw evidence separately.
    """
    life = validate_lifecycle(lifecycle)
    if not all(
        isinstance(rows, list) and len(rows) <= MAX_NETS for rows in (before_nets, after_nets)
    ):
        raise ValueError("Complete bounded named-net censuses are required")
    before, after = _headers(before_pins), _headers(after_pins)
    old_named = named_net_ownership(before_nets, before)
    new_named = named_net_ownership(after_nets, after)
    if old_named != new_named:
        raise ValueError("A real named network UID/name changed during reopening")
    if len(before) != len(after):
        raise ValueError("Physical pin membership changed during reopening")
    old_default = [row for row in before if row["net"] == UNASSIGNED_NET_NAME]
    new_default = [row for row in after if row["net"] == UNASSIGNED_NET_NAME]
    if not old_default or not new_default:
        raise ValueError("An observed complete Net0 member set is required")
    old_uid, new_uid = old_default[0]["net_uid"], new_default[0]["net_uid"]
    if old_uid == new_uid:
        raise ValueError("No default-reference substitution is present")
    members = []
    for old, new in zip(before, after, strict=True):
        if any(old[key] != new[key] for key in MEMBER_FIELDS):
            raise ValueError("Physical pin/owner/network membership changed during reopening")
        if old["net"] == UNASSIGNED_NET_NAME:
            # named_net_ownership already proved one nonzero disjoint default UID
            # in each full frame; only this exact member is permitted to differ.
            if old["net_uid"] != old_uid or new["net_uid"] != new_uid:
                raise ValueError("Ambiguous default reference")
            members.append({key: old[key] for key in MEMBER_FIELDS})
        elif old["net_uid"] != new["net_uid"]:
            raise ValueError("A real named pin network UID changed")
    named_rows = [{"net": name, "uid": uid} for name, uid in sorted(old_named.items())]
    return {
        "profile": PROFILE,
        "old_uid": old_uid,
        "new_uid": new_uid,
        "physical_pin_count": len(before),
        "default_member_count": len(members),
        "named_net_count": len(named_rows),
        "default_members": members,
        "default_members_sha256": _digest(members),
        "named_net_bindings_sha256": _digest(named_rows),
        "raw_pin_headers_before_sha256": _digest(before),
        "raw_pin_headers_after_sha256": _digest(after),
        # Named-census enumeration order is not identity. Every raw row field
        # remains in these hashes; only its proven unique name/UID orders rows.
        "raw_named_census_before_sha256": _digest(
            sorted(before_nets, key=lambda row: (row["net"], row["uid"]))
        ),
        "raw_named_census_after_sha256": _digest(
            sorted(after_nets, key=lambda row: (row["net"], row["uid"]))
        ),
        "lifecycle": life,
        "lifecycle_sha256": _digest(life),
        "boundary": "comparison only; raw native observations and proofs remain unchanged",
    }


def pin_reference_projector(proof: dict, side: str):
    """Build one bounded local projector; no phase/command cache is retained."""
    if side not in {"before", "after"} or proof.get("profile") != PROFILE:
        raise ValueError("Unsupported default-reference comparison proof")
    members = proof["default_members"]
    if not isinstance(members, list) or not 1 <= len(members) <= MAX_PINS:
        raise ValueError("Default-reference member proof is incomplete")
    if _digest(members) != proof.get("default_members_sha256"):
        raise ValueError("Default-reference member proof changed")
    bound = {tuple(member[key] for key in MEMBER_FIELDS) for member in members}
    if len(bound) != len(members):
        raise ValueError("Default-reference member proof is ambiguous")
    life = validate_lifecycle(proof["lifecycle"])
    if life != proof["lifecycle"] or _digest(life) != proof.get("lifecycle_sha256"):
        raise ValueError("Default-reference lifecycle proof changed")
    old_uid, new_uid = proof["old_uid"], proof["new_uid"]
    if (
        not all(isinstance(uid, str) and uid and uid != "0" for uid in (old_uid, new_uid))
        or old_uid == new_uid
    ):
        raise ValueError("Default-reference substitution is incomplete")

    def project(row: Any) -> dict:
        if not isinstance(row, dict):
            raise ValueError("Malformed pin reference projection")
        header = {key: row[key] for key in HEADER_FIELDS}
        member = tuple(header[key] for key in MEMBER_FIELDS)
        result = copy.deepcopy(row)
        if header["net"] == UNASSIGNED_NET_NAME:
            if member not in bound:
                raise ValueError("Pin reference is outside the exact default member set")
            expected_uid = old_uid if side == "before" else new_uid
            if header["net_uid"] != expected_uid:
                raise ValueError("Pin mirror differs from its complete native reference")
            if side == "after":
                result["net_uid"] = old_uid
        elif header["net_uid"] in {old_uid, new_uid}:
            raise ValueError("A named network aliases a default reference")
        return result

    return project


def project_pin_reference(row: Any, proof: dict, side: str) -> dict:
    """Return a comparison view, mapping only a bound after-frame Net0 reference."""
    return pin_reference_projector(proof, side)(row)
