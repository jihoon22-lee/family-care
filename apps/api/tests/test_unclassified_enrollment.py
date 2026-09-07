"""An enrolled but unclassified Rider is not silently treated as indemnity."""

from uuid import uuid4

from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.calculation_repository import CalculationRepository
from familycare_api.decisions.domain import ClaimCandidate
from familycare_api.decisions.schemas import OperationalCandidateResponse


def test_operational_unclassified_candidate_has_no_assumed_benefit_kind() -> None:
    candidate = ClaimCandidate(
        id=uuid4(),
        rider_id=uuid4(),
        rider_type="unknown",
        rider_label="Sample Rider",
        aggregate_result="UNKNOWN",
    )
    result = OperationalCandidateResponse.from_domain(candidate)
    assert result.benefit_kind == "UNKNOWN" and result.calculation is None


def test_unclassified_candidate_never_enters_a_payment_calculator() -> None:
    class NoPaymentConnection:
        def execute(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("unknown classification cannot calculate")

    row = {
        "id": uuid4(),
        "decision_run_id": uuid4(),
        "rider_id": uuid4(),
        "rider_type": "unknown",
        "aggregate_result": "MATCH",
        "required_match_count": 1,
        "required_unknown_count": 0,
        "required_no_match_count": 0,
        "version": 1,
    }
    result = CalculationRepository("postgresql://synthetic")._calculate_candidate(
        NoPaymentConnection(),
        HouseholdScope(uuid4()),
        {},
        row,
        (),
        indemnity_count=0,
        receipt_rows=[],
    )
    assert result is None
