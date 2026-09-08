"""Event rule reads preserve applicability uncertainty even without executable rules."""

from datetime import date
from uuid import UUID

import pytest
from familycare_api.clauses.terms_change_selection import (
    BaseTermsEdition,
    TermsSelectionScope,
    select_terms_for_event,
)
from familycare_api.decisions.terms import RulesForEvent

from apps.api.tests.test_decision_engine import rule


def _selection():
    return select_terms_for_event(
        TermsSelectionScope(UUID(int=1), UUID(int=3), UUID(int=2), UUID(int=10)),
        date(2025, 7, 1),
        (BaseTermsEdition(UUID(int=81), "UNKNOWN"),),
        (),
    )


def test_no_rules_does_not_discard_the_terms_selection() -> None:
    selected = _selection()
    result = RulesForEvent((), (selected,))
    assert not result and result.terms_selections == (selected,)


def test_rule_iteration_preserves_an_unknown_applicability_judgment() -> None:
    version = rule()
    result = RulesForEvent((version,), (_selection(),), ((version.id, "UNKNOWN"),))
    assert list(result) == [version] and result[0] == version
    assert result[:] == (version,)
    assert result.status_for(version.id) == "UNKNOWN"


def test_a_scoped_selection_cannot_omit_a_returned_rules_judgment() -> None:
    with pytest.raises(ValueError, match="EVENT_RULE_TERMS_INPUT_INVALID"):
        RulesForEvent((rule(),), (_selection(),))


def test_duplicate_or_invalid_rule_judgments_are_rejected() -> None:
    version = rule()
    with pytest.raises(ValueError, match="EVENT_RULE_TERMS_INPUT_INVALID"):
        RulesForEvent((version,), (_selection(),), ((version.id, "MATCH"), (version.id, "UNKNOWN")))
