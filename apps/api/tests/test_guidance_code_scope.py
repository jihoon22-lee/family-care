"""Classification editions are explicit paired metadata on the actual user code."""

from uuid import UUID

import pytest
from familycare_api.decisions.errors import DecisionRepositoryUnavailable
from familycare_api.decisions.schemas import StructuredFactInput
from familycare_api.decisions.structuring_repository import _facts
from pydantic import ValidationError


@pytest.mark.parametrize(
    "extra",
    [
        {"code_system": "synthetic"},
        {"code_version": "v1"},
        {"code_system": "synthetic", "code_version": "v1", "value": None},
        {"code_system": "synthetic", "code_version": "v1", "field_id": "admission", "value": True},
    ],
)
def test_partial_or_non_code_scope_is_rejected(extra):
    with pytest.raises(ValidationError):
        StructuredFactInput.model_validate(
            {"field_id": "diagnosis_code", "value": "synthetic-a", **extra}
        )


def test_provider_metadata_cannot_acquire_user_code_scope_authority():
    with pytest.raises(DecisionRepositoryUnavailable):
        _facts(
            {
                "diagnosis_code": {
                    "fact_id": str(UUID(int=1)),
                    "value": "synthetic-a",
                    "source": "ai",
                    "state": "confirmed",
                    "confidence": "high",
                    "evidence_ids": [],
                    "code_system": "synthetic",
                    "code_version": "v1",
                }
            }
        )
