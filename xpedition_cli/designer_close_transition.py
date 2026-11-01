"""Only a confirmed Designer SaveAll/CloseProject may change its document binding.

The caller supplies its own retained native reference and native lifecycle facts.
No public option can assert these facts. This is not a pre-confirm normalizer.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from .designer_window_identity import _path, document_binding, validate


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def verify(
    before,
    after,
    rows_before,
    rows_after,
    *,
    expected_project,
    close_progress,
    same_retained_iunknown,
    same_client_thread,
):
    result = {
        "accepted": False,
        "kind": "confirmed_designer_close_document_transition_v1",
    }
    try:
        for field in ("save_count", "close_count"):
            if type(close_progress.get(field)) is not int or close_progress[field] != 1:
                return {**result, "reason": "native_save_close_count_incomplete"}
        for field in ("save_acknowledged", "close_returned", "closed_observed"):
            if close_progress.get(field) is not True:
                return {
                    **result,
                    "reason": "native_save_close_empty_observation_incomplete",
                }
        if same_retained_iunknown is not True or same_client_thread is not True:
            return {**result, "reason": "same_retained_application_context_unproved"}
        if rows_before != rows_after:
            return {**result, "reason": "writer_inventory_changed"}
        if not validate(before, rows_before) or not validate(after, rows_after):
            return {
                **result,
                "reason": "formal_window_document_generation_proof_incomplete",
            }
        if before["document"]["open"] is not True or _path(before["document"]["path"]) != _path(
            expected_project
        ):
            return {**result, "reason": "before_document_not_exact_expected_project"}
        if (
            after["document"]["open"] is not False
            or after["document"]["path"] is not None
            or after["document"].get("designs") != []
        ):
            return {**result, "reason": "after_document_not_observed_empty"}
        if any(
            type(value["public_fields"].get("ActiveViewHandle")) is not int
            or value["public_fields"]["ActiveViewHandle"] != 0
            for value in (before, after)
        ):
            return {**result, "reason": "active_child_view_transition_unqualified"}
        predicted = deepcopy(before)
        predicted["document"].update(open=False, path=None, designs=[])
        predicted["designer_window_binding"]["native_document_binding_sha256"] = document_binding(
            predicted["document"]
        )
        if predicted != after:
            return {**result, "reason": "unexpected_identity_or_document_change"}
        return {
            **result,
            "accepted": True,
            "reason": "only_confirmed_project_to_empty_and_recomputed_document_binding_changed",
            "expected_changed_fields": [
                "document.open",
                "document.path",
                "document.designs",
                "designer_window_binding.native_document_binding_sha256",
            ],
            "before_raw_sha256": _digest(before),
            "after_raw_sha256": _digest(after),
            "raw_observations_preserved": True,
        }
    except (KeyError, TypeError, ValueError):
        return {**result, "reason": "controlled_transition_inputs_incomplete"}


def expected_after(before):
    """Called only after verify accepted; compute a copy, never rewrite evidence."""
    result = deepcopy(before)
    result["document"].update(open=False, path=None, designs=[])
    result["designer_window_binding"]["native_document_binding_sha256"] = document_binding(
        result["document"]
    )
    return result
