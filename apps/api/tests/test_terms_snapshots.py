"""Only bounded, typed event selections can become retained decision metadata."""

from datetime import date
from uuid import UUID

import pytest
from familycare_api.clauses.terms_change_selection import (
    BaseTermsEdition,
    TermsChangeRelation,
    TermsSelectionScope,
    select_terms_for_event,
)
from familycare_api.decisions.terms_snapshots import decode_selections, encode_selections


def _selection():
    scope = TermsSelectionScope(UUID(int=1), UUID(int=2), UUID(int=3), UUID(int=4))
    return select_terms_for_event(
        scope,
        date(2025, 7, 1),
        (BaseTermsEdition(UUID(int=81), "MATCH"),),
        (
            TermsChangeRelation(
                UUID(int=91),
                scope,
                "REPLACE",
                UUID(int=81),
                None,
                None,
                None,
                "UNKNOWN",
                ("CHANGE_TARGET_UNRESOLVED",),
            ),
        ),
    )


def test_selection_snapshot_roundtrip_preserves_partial_target_and_provenance() -> None:
    selections = (_selection(),)
    encoded = encode_selections(selections)
    decoded = decode_selections(encoded)
    assert decoded == selections
    assert decoded[0].scope_uncertainties and decoded[0].uncertain_relation_ids
    encoded[0]["editions"][0]["status"] = "MATCH"
    assert decoded[0].editions[0].status == "UNKNOWN"


@pytest.mark.parametrize(
    "mutation", ["foreign_key", "bad_date", "bad_status", "bad_reason", "duplicate_scope"]
)
def test_invalid_snapshot_shape_cannot_be_reused_as_calculation_authority(mutation: str) -> None:
    encoded = encode_selections((_selection(),))
    if mutation == "foreign_key":
        encoded[0]["source_text"] = "synthetic unexpected text"
    elif mutation == "bad_date":
        encoded[0]["event_date"] = "not-a-date"
    elif mutation == "bad_status":
        encoded[0]["editions"][0]["status"] = "active"
    elif mutation == "bad_reason":
        encoded[0]["editions"][0]["reason_codes"] = ["synthetic unexpected narrative"]
    else:
        encoded.append(encoded[0])
    with pytest.raises(ValueError, match="TERMS_SNAPSHOT_INVALID"):
        decode_selections(encoded)


def test_legacy_run_has_no_invented_terms_selection() -> None:
    assert decode_selections(None) == ()
    assert decode_selections([]) == ()
