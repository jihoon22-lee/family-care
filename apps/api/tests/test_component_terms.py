"""Component boundaries remain narrower than a composite PDF's identity."""

from dataclasses import replace

import pytest
from familycare_api.clauses.links import validate_rider_clause_link
from familycare_api.clauses.schemas import TermsEditionResponse
from familycare_api.common.scope import HouseholdScope

from apps.api.tests.test_rider_clause_rules import _context, _id, _reason


@pytest.mark.parametrize("start,end", [(1, 1), (3, 4)])
def test_clause_from_another_component_cannot_validate(start: int, end: int) -> None:
    context = _context()
    edition = replace(
        context.terms_edition,
        source_component_id=_id(900),
        source_page_start=start,
        source_page_end=end,
        source_period_verified=True,
    )
    assert _reason(replace(context, terms_edition=edition)) == "CLAUSE_DOCUMENT_MISMATCH"


def test_clause_inside_component_preserves_legacy_validation() -> None:
    context = _context()
    edition = replace(
        context.terms_edition,
        source_component_id=_id(900),
        source_page_start=2,
        source_page_end=3,
        source_period_verified=True,
    )
    validate_rider_clause_link(
        HouseholdScope(context.policy_household_space_id), replace(context, terms_edition=edition)
    )
    response = TermsEditionResponse.from_domain(edition).model_dump()
    assert response["source_component_id"] == _id(900)
    assert response["source_page_start"] == 2 and response["source_page_end"] == 3


def test_component_edition_without_proven_period_is_not_an_all_time_edition() -> None:
    context = _context()
    edition = replace(
        context.terms_edition,
        source_component_id=_id(900),
        source_page_start=2,
        source_page_end=3,
        applicability_start=None,
        applicability_end=None,
    )
    assert _reason(replace(context, terms_edition=edition)) == "TERMS_EDITION_NOT_APPLICABLE"


@pytest.mark.parametrize("start,end", [(None, None), (1, None), (0, 1), (3, 2), (True, 3)])
def test_component_edition_requires_complete_physical_bounds(
    start: int | None, end: int | None
) -> None:
    with pytest.raises(ValueError):
        replace(
            _context().terms_edition,
            source_component_id=_id(900),
            source_page_start=start,
            source_page_end=end,
        )


def test_proven_applicability_uses_codes_and_references_without_display_or_date_gate() -> None:
    context = _context()
    edition = replace(
        context.terms_edition,
        source_component_id=_id(900),
        source_page_start=2,
        source_page_end=3,
        product_key="sample-other-display",
        applicability_start=None,
        applicability_end=None,
    )
    validate_rider_clause_link(
        HouseholdScope(context.policy_household_space_id),
        replace(
            context, terms_edition=edition, contract_date=None, program_applicability_verified=True
        ),
    )


def test_applicability_contradiction_cannot_fall_back_to_legacy_display_matching() -> None:
    context = _context()
    edition = replace(
        context.terms_edition,
        source_component_id=_id(900),
        source_page_start=2,
        source_page_end=3,
        source_period_verified=True,
    )
    assert (
        _reason(replace(context, terms_edition=edition, program_applicability_blocked=True))
        == "TERMS_EDITION_NOT_APPLICABLE"
    )
