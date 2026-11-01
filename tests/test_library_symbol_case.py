"""Historical native-recorded library tests omitted from the public snapshot."""

from __future__ import annotations

import pytest


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_public_design_library_reads_real_catalog_parts_without_rewriting_identity():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_exact_identity_has_priority_over_a_case_variant():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_only_unique_case_fallback_preserves_actual_symbol_record():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_duplicate_exact_or_case_candidates_are_refused_even_if_identical():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_conflicting_case_aliases_are_not_selected_by_order():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_unqualified_symbol_does_not_choose_between_partitions():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_symbol_and_part_number_matching_add_no_fuzzy_or_pn_normalization():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_case_fallback_rejects_unicode_compatibility_matches_but_keeps_exact_identity():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_public_resolver_refuses_duplicate_pn_records_before_choosing_a_symbol():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_file_symbol_lookup_uses_same_unique_case_rule():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_file_symbol_lookup_refuses_ambiguous_case_candidates():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_ambiguous_error_has_bounded_identity_only_evidence():
    """Original historical coverage is not reproduced in this snapshot."""
    pass
