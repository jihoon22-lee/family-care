"""A retained currency repair uses the existing amount's exact source proof."""

from dataclasses import replace

import pytest
from familycare_worker.ai.policy_draft_normalization import (
    AMOUNT_CURRENCY_NORMALIZATION_REVISION,
    SOURCE_SCOPED_NORMALIZATION_REVISION,
    normalize_policy_draft,
)
from familycare_worker.ai.schemas import StructurerCandidate

from workers.analyzer.tests.test_policy_draft_normalization import (
    _batch,
    _candidate,
    _envelope,
    _fields,
    _pack,
    _reasons,
)
from workers.analyzer.tests.test_policy_table_grounding import _input


def _table(*, existing=None, **kwargs):
    candidate, evidence, nodes, header = _input(**kwargs)
    fields = tuple(f for f in candidate.fields if f.field_id != "currency")
    if existing is not None:
        currency = _fields(candidate)["currency"].model_copy(
            update={"value": existing, "evidence_ids": (header,)}
        )
        fields = (*fields, currency)
    draft = StructurerCandidate(
        schema_version="1",
        candidate_id=candidate.candidate_id,
        candidate_kind="rider",
        fields=fields,
    )
    envelope = _pack(evidence)
    return draft, envelope, nodes


def _normalize(draft, envelope, nodes=None, revision=AMOUNT_CURRENCY_NORMALIZATION_REVISION):
    return normalize_policy_draft(
        _batch(
            envelope,
            draft,
            assignments={
                key: (draft.candidate_id,)
                for key, evidence_id in zip(
                    envelope.primary_chunk_ids, envelope.primary_evidence_ids, strict=True
                )
                if any(evidence_id in f.evidence_ids for f in draft.fields)
            },
        ),
        envelope,
        local_nodes=nodes,
        revision=revision,
    )


@pytest.mark.parametrize("existing", [None, "KRW"])
def test_currency_uses_amount_row_and_keeps_original_draft(existing):
    draft, envelope, nodes = _table(existing=existing)
    original = draft.model_dump_json()
    result = _normalize(draft, envelope, nodes)
    fields = _fields(result.batch.candidates[0])
    assert fields["currency"].value == "KRW"
    assert fields["currency"].evidence_ids == fields["sum_assured"].evidence_ids
    assert fields["sum_assured"] == _fields(draft)["sum_assured"]
    assert draft.model_dump_json() == original
    reason = "CURRENCY_DERIVED_FROM_AMOUNT" if existing is None else "CURRENCY_EVIDENCE_REALIGNED"
    assert reason in _reasons(result)
    assert "status" not in result.batch.candidates[0].model_dump()


def test_conflicting_supplied_currency_is_not_overwritten():
    draft, envelope, nodes = _table(existing="USD")
    result = _normalize(draft, envelope, nodes)
    assert "currency" not in _fields(result.batch.candidates[0])
    assert "CURRENCY_EVIDENCE_REALIGNED" not in _reasons(result)
    assert _fields(draft)["currency"].value == "USD"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"amount_header": "가입금액"},
        {"amount_header": "월보험료(만원)"},
        {"amount_cell": "40"},
        {"amount_cell": "20원"},
    ],
)
def test_missing_unit_wrong_column_or_amount_cannot_supply_currency(kwargs):
    draft, envelope, nodes = _table(**kwargs)
    result = _normalize(draft, envelope, nodes)
    assert all("currency" not in _fields(c) for c in result.batch.candidates)
    assert "CURRENCY_DERIVED_FROM_AMOUNT" not in _reasons(result)


def test_another_data_row_cannot_supply_currency():
    draft, envelope, nodes = _table(second_amount="40")
    rows = [e for e in envelope.evidence if nodes[e.node_id].get("row_role") == "data"]
    draft = draft.model_copy(
        update={
            "fields": tuple(
                f.model_copy(update={"value": 400000, "evidence_ids": (rows[1].evidence_id,)})
                if f.field_id == "sum_assured"
                else f
                for f in draft.fields
            )
        }
    )
    result = _normalize(draft, envelope, nodes)
    assert "CURRENCY_DERIVED_FROM_AMOUNT" not in _reasons(result)


def test_terms_cannot_supply_an_enrolled_currency():
    draft, envelope, nodes = _table()
    envelope = _pack(
        tuple(replace(e, source_role="terms", document_kind="terms") for e in envelope.evidence)
    )
    result = _normalize(draft, envelope, nodes)
    assert result.batch.candidates == ()
    assert "CURRENCY_DERIVED_FROM_AMOUNT" not in _reasons(result)


@pytest.mark.parametrize("existing", [None, "KRW"])
def test_previous_normalization_does_not_gain_new_currency_authority(existing):
    draft, envelope, nodes = _table(existing=existing)
    result = _normalize(draft, envelope, nodes, SOURCE_SCOPED_NORMALIZATION_REVISION)
    assert "currency" not in _fields(result.batch.candidates[0])
    assert not _reasons(result) & {"CURRENCY_DERIVED_FROM_AMOUNT", "CURRENCY_EVIDENCE_REALIGNED"}


def test_new_revision_keeps_certificate_title_and_unconfirmed_issuer_support():
    envelope = _envelope("Sample Plan_보험증권")
    draft = _candidate(envelope.evidence[0], product_name="Sample Plan")
    result = _normalize(draft, envelope)
    assert _fields(result.batch.candidates[0])["product_name"].value == "Sample Plan"
    assert "ISSUER_UNCONFIRMED" in _reasons(result)
