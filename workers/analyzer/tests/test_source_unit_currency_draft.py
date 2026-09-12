"""An exact printed scale in raw currency is recoverable only in a new v8 draft."""

from copy import deepcopy
from dataclasses import replace

import pytest
from familycare_worker.ai.policy_draft_normalization import (
    PolicyDraftInvalid,
    _explicit_unit_draft,
)
from familycare_worker.ai.policy_draft_recovery import (
    PROVEN_CONTEXT_NORMALIZATION_REVISION,
    SOURCE_UNIT_CURRENCY_NORMALIZATION_REVISION,
    normalize_policy_response,
)

from workers.analyzer.tests.test_explicit_unit_draft_normalization import _row, _source
from workers.analyzer.tests.test_policy_currency_normalization import _normalize
from workers.analyzer.tests.test_policy_draft_normalization import _fields, _pack, _reasons
from workers.analyzer.tests.test_policy_draft_recovery import _orphan, _table_raw


def _recover(raw, envelope, nodes):
    return normalize_policy_response(
        raw,
        envelope,
        local_nodes=nodes,
        revision=SOURCE_UNIT_CURRENCY_NORMALIZATION_REVISION,
    )


@pytest.mark.parametrize(
    ("unit", "expected"),
    [("천원", 20000), ("만원", 200000), ("백만원", 20000000), ("억원", 2000000000)],
)
def test_exact_source_unit_currency_is_scaled_before_v7_context_and_mapping(unit, expected):
    draft, envelope, nodes = _source(existing=unit, amount_header=f"가입금액({unit})")
    raw, envelope, nodes = _table_raw(draft, envelope, nodes)
    _orphan(raw, 0)
    before, nodes_before = deepcopy(raw), deepcopy(nodes)
    result = _recover(raw, envelope, nodes)
    assert raw == before and nodes == nodes_before
    assert result.revision == SOURCE_UNIT_CURRENCY_NORMALIZATION_REVISION and result.partial
    assert len(result.batch.candidates) == 1
    candidate = result.batch.candidates[0]
    fields = _fields(candidate)
    assert fields["sum_assured"].value == expected
    assert fields["currency"].value == "KRW"
    assert fields["rider_name"].value == _fields(draft)["rider_name"].value
    assert set(_fields(draft)["sum_assured"].evidence_ids) <= set(
        fields["sum_assured"].evidence_ids
    )
    assert fields["currency"].evidence_ids == fields["sum_assured"].evidence_ids
    assert all(
        key in {e.evidence_id for e in envelope.evidence} for key in fields["currency"].evidence_ids
    )
    assert {
        "AMOUNT_SCALED_FROM_EXPLICIT_UNIT",
        "CURRENCY_NORMALIZED_FROM_SOURCE_UNIT",
        "CANDIDATE_RANGE_RECONCILED",
        "FIELD_CONTEXT_EVIDENCE_PROVEN",
    } <= _reasons(result)
    currency_changes = [
        a for a in result.adjustments if a.reason == "CURRENCY_NORMALIZED_FROM_SOURCE_UNIT"
    ]
    assert len(currency_changes) == 1
    assert currency_changes[0].candidate_id == draft.candidate_id
    assert currency_changes[0].field_id == "currency"
    assert "status" not in candidate.model_dump()
    assert "provider_request_ids" not in candidate.model_dump()


def test_currency_conversion_keeps_original_row_citations_before_context_enrichment():
    draft, envelope, nodes = _source(existing="만원")
    adjustments = []
    restored = _explicit_unit_draft(
        draft, envelope, nodes, adjustments, allow_source_unit_currency=True
    )
    fields = _fields(restored)
    assert fields["sum_assured"].evidence_ids == _fields(draft)["sum_assured"].evidence_ids
    assert fields["currency"].evidence_ids == _fields(draft)["sum_assured"].evidence_ids
    assert _fields(draft)["currency"].value == "만원"
    assert _fields(draft)["sum_assured"].value == 20
    assert [(a.reason, a.field_id) for a in adjustments] == [
        ("AMOUNT_SCALED_FROM_EXPLICIT_UNIT", "sum_assured"),
        ("CURRENCY_NORMALIZED_FROM_SOURCE_UNIT", "currency"),
    ]


@pytest.mark.parametrize("currency", ["USD", "EUR", "JPY", "천원", "원", " 만원", "만원 ", "만 원"])
def test_foreign_mismatched_or_similar_currency_is_not_inferred(currency):
    draft, envelope, nodes = _source(existing=currency)
    raw, envelope, nodes = _table_raw(draft, envelope, nodes)
    before = deepcopy(raw)
    result = _recover(raw, envelope, nodes)
    assert "CURRENCY_NORMALIZED_FROM_SOURCE_UNIT" not in _reasons(result)
    assert "AMOUNT_SCALED_FROM_EXPLICIT_UNIT" not in _reasons(result)
    assert raw == before


@pytest.mark.parametrize("unit", ["USD", "EUR", "JPY", "KRW", "원"])
def test_only_the_four_explicit_korean_scales_opt_in(unit):
    draft, envelope, nodes = _source(existing=unit, amount_header=f"가입금액({unit})")
    raw, envelope, nodes = _table_raw(draft, envelope, nodes)
    result = _recover(raw, envelope, nodes)
    assert "CURRENCY_NORMALIZED_FROM_SOURCE_UNIT" not in _reasons(result)
    assert "AMOUNT_SCALED_FROM_EXPLICIT_UNIT" not in _reasons(result)


@pytest.mark.parametrize(
    "variant", ["missing_header", "missing_unit", "hidden_unit", "ocr", "terms"]
)
def test_unit_requires_existing_minimized_native_policy_context(variant):
    draft, envelope, nodes = _source(existing="만원")
    row, _ = _row(draft, envelope, nodes)
    header = nodes[row["context_node_ids"][0]]
    if variant == "missing_header":
        row["context_node_ids"] = []
    elif variant == "missing_unit":
        header["cells"][1]["text"] = "가입금액"
        header["text"] = "\t".join(cell["text"] for cell in header["cells"])
        envelope = _pack(
            tuple(
                replace(e, text=header["text"], end=len(header["text"]))
                if e.node_id == header["node_id"]
                else e
                for e in envelope.evidence
            )
        )
    elif variant == "hidden_unit":
        envelope = _pack(
            tuple(
                replace(e, text=e.text.replace("만원", "[REDACTED]"))
                if e.node_id == header["node_id"]
                else e
                for e in envelope.evidence
            )
        )
    elif variant == "ocr":
        row["source_layer"] = "ocr"
    else:
        envelope = _pack(
            tuple(replace(e, source_role="terms", document_kind="terms") for e in envelope.evidence)
        )
    raw, envelope, nodes = _table_raw(draft, envelope, nodes)
    result = _recover(raw, envelope, nodes)
    assert "CURRENCY_NORMALIZED_FROM_SOURCE_UNIT" not in _reasons(result)
    assert "AMOUNT_SCALED_FROM_EXPLICIT_UNIT" not in _reasons(result)


def test_default_and_explicit_v7_still_discard_the_unscaled_pair():
    draft, envelope, nodes = _source(existing="만원")
    raw, envelope, nodes = _table_raw(draft, envelope, nodes)
    default = normalize_policy_response(raw, envelope, local_nodes=nodes)
    explicit = normalize_policy_response(
        raw, envelope, local_nodes=nodes, revision=PROVEN_CONTEXT_NORMALIZATION_REVISION
    )
    assert default == explicit
    assert default.revision == "policy-draft-normalization-v7"
    assert "CURRENCY_NORMALIZED_FROM_SOURCE_UNIT" not in _reasons(default)
    assert "AMOUNT_SCALED_FROM_EXPLICIT_UNIT" not in _reasons(default)
    assert all("sum_assured" not in _fields(c) for c in default.batch.candidates)
    adjustments = []
    assert _explicit_unit_draft(draft, envelope, nodes, adjustments) is draft
    assert adjustments == []


@pytest.mark.parametrize("revision", [f"policy-draft-normalization-v{i}" for i in range(1, 7)])
def test_v1_through_v6_keep_historical_currency_meaning(revision):
    draft, envelope, nodes = _source(existing="만원")
    result = _normalize(draft, envelope, nodes, revision)
    assert result.revision == revision
    assert "CURRENCY_NORMALIZED_FROM_SOURCE_UNIT" not in _reasons(result)
    assert "AMOUNT_SCALED_FROM_EXPLICIT_UNIT" not in _reasons(result)


@pytest.mark.parametrize("currency", [None, "KRW"])
def test_already_supported_currency_keeps_v7_output_and_adjustments(currency):
    draft, envelope, nodes = _source(existing=currency)
    raw, envelope, nodes = _table_raw(draft, envelope, nodes)
    old = normalize_policy_response(raw, envelope, local_nodes=nodes)
    new = _recover(raw, envelope, nodes)
    assert new.batch == old.batch and new.adjustments == old.adjustments
    assert new.partial == old.partial
    assert "CURRENCY_NORMALIZED_FROM_SOURCE_UNIT" not in _reasons(new)


@pytest.mark.parametrize(
    "revision", ["policy-draft-normalization-v6", "policy-draft-normalization-v9", ""]
)
def test_recovery_rejects_unsupported_revision(revision):
    draft, envelope, nodes = _source(existing="만원")
    raw, envelope, nodes = _table_raw(draft, envelope, nodes)
    with pytest.raises(PolicyDraftInvalid, match="POLICY_DRAFT_INVALID"):
        normalize_policy_response(raw, envelope, local_nodes=nodes, revision=revision)
