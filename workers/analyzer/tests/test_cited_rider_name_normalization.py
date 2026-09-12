"""Restore only the exact enrolled name from an already-cited native amount row."""

from dataclasses import replace

import pytest
from familycare_worker.ai.policy_draft_normalization import (
    AMOUNT_CURRENCY_NORMALIZATION_REVISION,
    TABLE_NAME_NORMALIZATION_REVISION,
)

from workers.analyzer.tests.test_policy_currency_normalization import _normalize, _table
from workers.analyzer.tests.test_policy_draft_normalization import _fields, _pack, _reasons


def _mistyped(name="Invented Rider", *, key=None, **kwargs):
    draft, envelope, nodes = _table(**kwargs)
    draft = draft.model_copy(
        update={
            "fields": tuple(
                f.model_copy(update={"value": name})
                if f.field_id == "rider_name"
                else f.model_copy(update={"value": key})
                if f.field_id == "rider_key"
                else f
                for f in draft.fields
                if f.field_id != "rider_key" or key is not None
            )
        }
    )
    return draft, envelope, nodes


@pytest.mark.parametrize("name", ["SampleRider", "Sam ple Rider", "Invented Rider"])
@pytest.mark.parametrize("key", [None, "same_as_name"])
def test_same_cited_row_restores_name_without_changing_original_or_granting_approval(name, key):
    draft, envelope, nodes = _mistyped(name, key=name if key else None)
    original = draft.model_dump_json()
    result = _normalize(draft, envelope, nodes, TABLE_NAME_NORMALIZATION_REVISION)
    assert len(result.batch.candidates) == 1
    fields = _fields(result.batch.candidates[0])
    assert fields["rider_name"].value == fields["rider_key"].value == "Sample Rider"
    assert fields["sum_assured"].value == 200000 and fields["currency"].value == "KRW"
    assert fields["rider_name"].evidence_ids == _fields(draft)["rider_name"].evidence_ids
    assert "RIDER_NAME_RESTORED_FROM_CITED_ROW" in _reasons(result)
    assert "status" not in result.batch.candidates[0].model_dump()
    assert draft.model_dump_json() == original


def test_previous_currency_revision_still_excludes_wrong_name():
    draft, envelope, nodes = _mistyped()
    result = _normalize(draft, envelope, nodes, AMOUNT_CURRENCY_NORMALIZATION_REVISION)
    assert result.batch.candidates == ()
    assert "RIDER_NAME_RESTORED_FROM_CITED_ROW" not in _reasons(result)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"amount_cell": "40"},
        {"amount_header": "보험료(만원)"},
        {"amount_header": "가입금액"},
    ],
)
def test_name_repair_requires_same_independently_proven_amount(kwargs):
    draft, envelope, nodes = _mistyped(**kwargs)
    result = _normalize(draft, envelope, nodes, TABLE_NAME_NORMALIZATION_REVISION)
    assert result.batch.candidates == ()
    assert "RIDER_NAME_RESTORED_FROM_CITED_ROW" not in _reasons(result)


def test_missing_amount_does_not_redirect_name_only_candidate():
    draft, envelope, nodes = _mistyped()
    draft = draft.model_copy(
        update={"fields": tuple(f for f in draft.fields if f.field_id != "sum_assured")}
    )
    result = _normalize(draft, envelope, nodes, TABLE_NAME_NORMALIZATION_REVISION)
    assert result.batch.candidates == ()


def test_existing_different_logical_key_is_not_silently_redirected():
    draft, envelope, nodes = _mistyped(key="another-rider-key")
    result = _normalize(draft, envelope, nodes, TABLE_NAME_NORMALIZATION_REVISION)
    assert result.batch.candidates == ()


@pytest.mark.parametrize("variant", ["ocr", "terms", "two_rows", "missing_header", "example"])
def test_unsupported_or_ambiguous_source_cannot_supply_restored_name(variant):
    draft, envelope, nodes = _mistyped(second_amount="20" if variant == "two_rows" else None)
    row = next(e for e in envelope.evidence if nodes[e.node_id].get("row_role") == "data")
    if variant == "ocr":
        nodes[row.node_id]["source_layer"] = "ocr"
    elif variant == "terms":
        envelope = _pack(
            tuple(replace(e, source_role="terms", document_kind="terms") for e in envelope.evidence)
        )
    elif variant == "two_rows":
        ids = tuple(
            e.evidence_id for e in envelope.evidence if nodes[e.node_id].get("row_role") == "data"
        )
        draft = draft.model_copy(
            update={
                "fields": tuple(
                    f.model_copy(update={"evidence_ids": ids}) if f.field_id == "rider_name" else f
                    for f in draft.fields
                )
            }
        )
    elif variant == "missing_header":
        nodes[row.node_id]["context_node_ids"] = []
    else:
        header = next(
            nodes[key]
            for key in nodes[row.node_id]["context_node_ids"]
            if nodes[key].get("row_role") == "header"
        )
        header["text"] += "\n미가입"
    result = _normalize(draft, envelope, nodes, TABLE_NAME_NORMALIZATION_REVISION)
    assert result.batch.candidates == ()
    assert "RIDER_NAME_RESTORED_FROM_CITED_ROW" not in _reasons(result)


def test_restoring_local_table_name_cannot_reintroduce_minimized_text():
    draft, envelope, nodes = _mistyped()
    envelope = _pack(
        tuple(
            replace(e, text=e.text.replace("Sample Rider", "[REDACTED]")) for e in envelope.evidence
        )
    )
    result = _normalize(draft, envelope, nodes, TABLE_NAME_NORMALIZATION_REVISION)
    assert result.batch.candidates == ()
    assert "RIDER_NAME_RESTORED_FROM_CITED_ROW" not in _reasons(result)
