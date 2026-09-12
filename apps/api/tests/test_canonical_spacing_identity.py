"""Whitespace equivalence never chooses between distinct published or raw locations."""

from copy import deepcopy
from dataclasses import replace

import pytest
from familycare_api.insurance_reconciliation.canonical_match import _review_name
from familycare_api.insurance_reconciliation.source_inventory import unique_native_name_location

from apps.api.tests.test_canonical_enrollment_match import _candidate, _id, _match, _private
from apps.api.tests.test_enrollment_wrapped_name import wrapped_locator, wrapped_source


@pytest.mark.parametrize(
    "private_name,native_name",
    [
        ("SampleRider", "Sample Rider"),
        ("Sample Rider", "SampleRider"),
        ("Ｓample\tRider", "SampleRider"),
    ],
)
def test_bound_private_and_native_names_may_differ_only_in_whitespace(private_name, native_name):
    private = _private()
    private["name"] = private["certificate_review"]["name"] = private_name
    expected_review = _review_name(private)
    result = _match(private, candidates=(replace(_candidate(), original_rider_name=native_name),))
    assert result is not None
    assert _review_name(private) == expected_review
    assert (
        result.proofs[0].private_evidence_location
        == private["certificate_review"]["evidence_locations"][0]
    )


@pytest.mark.parametrize("variant", ["location", "contract", "member", "invalid_proof"])
def test_all_spacing_equivalent_published_candidates_compete(variant):
    first = _candidate()
    second = replace(
        first, original_rider_name="SampleRider", publication_candidate_version_id=_id(88)
    )
    if variant == "location":
        second = replace(
            second, physical_locator={**second.physical_locator, "name_bbox": [10, 90, 70, 100]}
        )
    elif variant == "contract":
        second = replace(second, policy_contract_id=_id(89))
    elif variant == "member":
        second = replace(second, family_member_id=_id(90))
    else:
        second = replace(second, conflict=True)
    assert _match(candidates=(first, second)) is None


def test_punctuation_differences_are_not_whitespace_equivalence():
    assert _match(candidates=(replace(_candidate(), original_rider_name="Sample-Rider"),)) is None


def test_wrapped_table_keeps_one_location_in_the_full_native_inventory():
    source = wrapped_source()
    before = deepcopy(source)
    assert unique_native_name_location(source, 1, "Sample Rider", wrapped_locator())
    assert source == before


@pytest.mark.parametrize("text", ["SampleRider", "Sam ple Rider", "Sample\tRider"])
@pytest.mark.parametrize("layer", ["native", "ocr"])
def test_unpublished_spacing_variants_cannot_disappear_from_page_inventory(text, layer):
    source = wrapped_source()
    source["nodes"].append(
        {
            "node_id": "unpublished-variant",
            "kind": "BLOCK",
            "source_layer": layer,
            "page_number": 1,
            "text": text,
            "bbox": [10, 90, 90, 100],
        }
    )
    assert not unique_native_name_location(source, 1, "Sample Rider", wrapped_locator())


def test_unpublished_compact_variant_split_into_raw_fragments_is_checked():
    source = wrapped_source()
    for key, text, box in (
        ("hidden-a", "Sam", [10, 90, 30, 100]),
        ("hidden-b", "pleRider", [33, 90, 90, 100]),
    ):
        source["nodes"].append(
            {
                "node_id": key,
                "kind": "BLOCK",
                "source_layer": "native",
                "page_number": 1,
                "text": text,
                "bbox": box,
            }
        )
    assert not unique_native_name_location(source, 1, "Sample Rider", wrapped_locator())


def test_an_untrusted_wrapped_cell_cannot_hide_a_spacing_variant_behind_an_amount_column():
    source = wrapped_source()
    # Geometric cell streams must inspect raw words even when this view lies.
    source["nodes"].append(
        {
            "node_id": "hidden-cell",
            "kind": "TABLE_ROW",
            "source_layer": "native",
            "page_number": 1,
            "row_role": "data",
            "row_index": 3,
            "text": "Other text",
            "cells": [
                {"row_index": 3, "column_index": 0, "text": "Other text", "bbox": [0, 80, 100, 125]}
            ],
        }
    )
    for key, text, box in (
        ("hidden-top", "Sam", [10, 90, 30, 100]),
        ("intervening-amount", "999", [110, 90, 130, 100]),
        ("hidden-bottom", "pleRider", [10, 104, 70, 114]),
    ):
        source["nodes"].append(
            {
                "node_id": key,
                "kind": "BLOCK",
                "source_layer": "native",
                "page_number": 1,
                "text": text,
                "bbox": box,
            }
        )
    assert not unique_native_name_location(source, 1, "Sample Rider", wrapped_locator())
