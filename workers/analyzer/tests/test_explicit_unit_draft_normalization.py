"""Opt-in unit recovery is a new cited draft, never a replacement verification."""

from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest
from familycare_worker.ai.policy_draft_normalization import (
    EXPLICIT_UNIT_NORMALIZATION_REVISION,
    normalize_policy_draft,
)

from workers.analyzer.tests.test_policy_currency_normalization import _normalize, _table
from workers.analyzer.tests.test_policy_draft_normalization import _batch, _fields, _pack, _reasons


def _source(*, raw=20, wrong_name=None, key=None, **kwargs):
    draft, envelope, nodes = _table(**kwargs)
    draft = draft.model_copy(
        update={
            "fields": tuple(
                field.model_copy(update={"value": raw})
                if field.field_id == "sum_assured"
                else field.model_copy(update={"value": wrong_name})
                if field.field_id == "rider_name" and wrong_name is not None
                else field.model_copy(update={"value": key})
                if field.field_id == "rider_key"
                else field
                for field in draft.fields
                if field.field_id != "rider_key" or key is not None
            )
        }
    )
    return draft, envelope, nodes


def _recover(draft, envelope, nodes):
    return _normalize(draft, envelope, nodes, EXPLICIT_UNIT_NORMALIZATION_REVISION)


def _row(draft, envelope, nodes):
    name_id = _fields(draft)["rider_name"].evidence_ids[0]
    evidence = next(item for item in envelope.evidence if item.evidence_id == name_id)
    return nodes[evidence.node_id], evidence


@pytest.mark.parametrize(
    ("header", "cell", "raw", "expected"),
    [
        ("가입금액(만원)", "20", 20, 200000),
        ("가입금액(천원)", "20", "20", 20000),
        ("가입금액(백만원)", "2.5", "2.5", 2500000),
        ("가입금액(억원)", "0.01", 0.01, 1000000),
        ("가입금액", "20만원", 20, 200000),
    ],
)
def test_explicit_unit_scales_only_new_draft_with_original_amount_citations(
    header, cell, raw, expected
):
    draft, envelope, nodes = _source(amount_header=header, amount_cell=cell, raw=raw)
    original = _batch(
        envelope,
        draft,
        assignments={
            chunk: (draft.candidate_id,)
            for chunk, identity in zip(
                envelope.primary_chunk_ids, envelope.primary_evidence_ids, strict=True
            )
            if identity in _fields(draft)["rider_name"].evidence_ids
        },
    )
    before = original.model_dump_json()
    result = normalize_policy_draft(
        original, envelope, local_nodes=nodes, revision=EXPLICIT_UNIT_NORMALIZATION_REVISION
    )
    assert original.model_dump_json() == before
    assert result.revision == EXPLICIT_UNIT_NORMALIZATION_REVISION and result.partial
    assert len(result.batch.candidates) == 1
    fields = _fields(result.batch.candidates[0])
    assert fields["sum_assured"].value == expected
    assert fields["currency"].value == "KRW"
    assert fields["sum_assured"].evidence_ids == _fields(draft)["sum_assured"].evidence_ids
    assert fields["currency"].evidence_ids == fields["sum_assured"].evidence_ids
    assert fields["rider_name"] == _fields(draft)["rider_name"]
    assert "status" not in result.batch.candidates[0].model_dump()
    assert [
        (a.reason, a.field_id)
        for a in result.adjustments
        if a.reason != "RIDER_KEY_DERIVED_FROM_NAME"
    ] == [
        ("AMOUNT_SCALED_FROM_EXPLICIT_UNIT", "sum_assured"),
        ("CURRENCY_DERIVED_FROM_AMOUNT", "currency"),
    ]


@pytest.mark.parametrize("wrong_name", ["Invented Rider", "SampleRider"])
@pytest.mark.parametrize("with_key", [False, True])
def test_name_and_unscaled_amount_are_restored_atomically_from_the_same_cited_row(
    wrong_name, with_key
):
    draft, envelope, nodes = _source(wrong_name=wrong_name, key=wrong_name if with_key else None)
    original = draft.model_dump_json()
    result = _recover(draft, envelope, nodes)
    fields = _fields(result.batch.candidates[0])
    assert fields["rider_name"].value == fields["rider_key"].value == "Sample Rider"
    assert fields["sum_assured"].value == 200000 and fields["currency"].value == "KRW"
    assert fields["rider_name"].evidence_ids == _fields(draft)["rider_name"].evidence_ids
    assert {"AMOUNT_SCALED_FROM_EXPLICIT_UNIT", "RIDER_NAME_RESTORED_FROM_CITED_ROW"} <= _reasons(
        result
    )
    assert draft.model_dump_json() == original


def test_already_supported_logical_key_is_preserved_when_name_does_not_change():
    draft, envelope, nodes = _source(key="sample-rider")
    result = _recover(draft, envelope, nodes)
    assert _fields(result.batch.candidates[0])["rider_key"] == _fields(draft)["rider_key"]
    assert "AMOUNT_SCALED_FROM_EXPLICIT_UNIT" in _reasons(result)


def test_existing_same_currency_uses_original_amount_citation_after_scaling():
    draft, envelope, nodes = _source(existing="KRW")
    result = _recover(draft, envelope, nodes)
    fields = _fields(result.batch.candidates[0])
    assert fields["currency"].evidence_ids == _fields(draft)["sum_assured"].evidence_ids
    assert "CURRENCY_EVIDENCE_REALIGNED" in _reasons(result)


@pytest.mark.parametrize("revision", [f"policy-draft-normalization-v{i}" for i in range(1, 6)])
def test_previous_revisions_keep_rejecting_the_unscaled_amount(revision):
    draft, envelope, nodes = _source()
    result = _normalize(draft, envelope, nodes, revision)
    assert "AMOUNT_SCALED_FROM_EXPLICIT_UNIT" not in _reasons(result)
    assert all("sum_assured" not in _fields(candidate) for candidate in result.batch.candidates)
    assert any(
        a.reason == "OPTIONAL_FIELD_UNSUPPORTED" and a.field_id == "sum_assured"
        for a in result.adjustments
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"raw": 200000},
        {"raw": 40},
        {"raw": True},
        {"raw": None},
        {"amount_header": "가입금액"},
        {"amount_header": "보험료(만원)"},
        {"amount_header": "가입금액(KRW)"},
        {"amount_cell": "20원"},
        {"amount_cell": "20 또는 30"},
        {"existing": "USD"},
        {"wrong_name": "Invented Rider", "key": "another-key"},
    ],
)
def test_wrong_value_missing_unit_or_conflicting_currency_never_recovers(kwargs):
    draft, envelope, nodes = _source(**kwargs)
    result = _recover(draft, envelope, nodes)
    assert "AMOUNT_SCALED_FROM_EXPLICIT_UNIT" not in _reasons(result)


@pytest.mark.parametrize(
    "variant",
    [
        "ocr_row",
        "ocr_header",
        "row_issue",
        "header_issue",
        "not_schedulable",
        "missing_header",
        "uncited_header",
        "header_span",
        "row_span",
        "boolean_span",
        "cell_overlap",
        "missing_geometry",
        "outside_table_geometry",
        "column_misalignment",
        "different_row_geometry",
        "overlapping_other_row",
        "partial_primary",
        "text_line",
        "terms",
        "minimized_name",
        "minimized_amount",
        "minimized_unit",
        "unpublished_unit",
        "conflicting_header",
        "example",
    ],
)
def test_ambiguous_geometry_context_or_minimization_cannot_authorize_new_values(variant):
    draft, envelope, nodes = _source()
    row, source = _row(draft, envelope, nodes)
    header = next(
        nodes[key] for key in row["context_node_ids"] if nodes[key].get("row_role") == "header"
    )
    if variant == "ocr_row":
        row["source_layer"] = "ocr"
    elif variant == "ocr_header":
        header["source_layer"] = "ocr"
    elif variant in {"row_issue", "header_issue"}:
        (row if variant == "row_issue" else header)["issue_codes"] = ["UNSUPPORTED_STRUCTURE"]
    elif variant == "not_schedulable":
        row["schedulable"] = False
    elif variant == "missing_header":
        row["context_node_ids"] = []
    elif variant == "uncited_header":
        envelope = _pack(
            tuple(item for item in envelope.evidence if item.node_id != header["node_id"])
        )
    elif variant in {"header_span", "row_span", "boolean_span"}:
        target = header if variant == "header_span" else row
        target["cells"][0]["column_span"] = True if variant == "boolean_span" else 2
    elif variant == "cell_overlap":
        row["cells"][1]["bbox"] = row["cells"][0]["bbox"]
    elif variant == "missing_geometry":
        row["cells"][1]["bbox"] = None
    elif variant == "outside_table_geometry":
        row["cells"][1]["bbox"] = [110, 800, 200, 810]
    elif variant == "column_misalignment":
        row["cells"][0]["bbox"], row["cells"][1]["bbox"] = (
            row["cells"][1]["bbox"],
            row["cells"][0]["bbox"],
        )
    elif variant == "different_row_geometry":
        box = row["cells"][1]["bbox"]
        row["cells"][1]["bbox"] = [box[0], box[1] + 30, box[2], box[3] + 30]
    elif variant == "overlapping_other_row":
        other = deepcopy(row)
        other["node_id"] = "e" * 64
        nodes[other["node_id"]] = other
    elif variant == "partial_primary":
        envelope = _pack(
            tuple(
                replace(item, end=item.end - 1) if item == source else item
                for item in envelope.evidence
            )
        )
    elif variant == "text_line":
        row["kind"] = "TEXT_LINE"
    elif variant == "terms":
        envelope = _pack(
            tuple(
                replace(item, source_role="terms", document_kind="terms")
                for item in envelope.evidence
            )
        )
    elif variant in {"minimized_name", "minimized_amount", "minimized_unit"}:
        changed = []
        for item in envelope.evidence:
            text = item.text
            if item.node_id == row["node_id"] and variant != "minimized_unit":
                parts = text.split("\t")
                parts[0 if variant == "minimized_name" else 1] = "[REDACTED]"
                text = "\t".join(parts)
            elif item.node_id == header["node_id"] and variant == "minimized_unit":
                text = text.replace("만원", "[REDACTED]")
            changed.append(replace(item, text=text))
        envelope = _pack(tuple(changed))
    elif variant == "unpublished_unit":
        # The local header must not reintroduce a unit absent from the envelope.
        envelope = _pack(
            tuple(
                replace(item, text=item.text.replace("만원", ""))
                if item.node_id == header["node_id"]
                else item
                for item in envelope.evidence
            )
        )
    elif variant == "conflicting_header":
        alternate = deepcopy(header)
        alternate["node_id"] = "d" * 64
        alternate["cells"][1]["text"] = "보험료(만원)"
        alternate["text"] = "\t".join(cell["text"] for cell in alternate["cells"])
        nodes[alternate["node_id"]] = alternate
        row["context_node_ids"].append(alternate["node_id"])
        original_header = next(
            item for item in envelope.evidence if item.node_id == header["node_id"]
        )
        envelope = _pack(
            (
                *envelope.evidence,
                replace(
                    original_header,
                    evidence_id=uuid4(),
                    node_id=alternate["node_id"],
                    text=alternate["text"],
                    end=len(alternate["text"]),
                    primary=False,
                ),
            )
        )
    else:
        row["text"] += "\n미가입"
        envelope = _pack(
            tuple(
                replace(item, text=row["text"], end=len(row["text"])) if item == source else item
                for item in envelope.evidence
            )
        )
    result = _recover(draft, envelope, nodes)
    assert "AMOUNT_SCALED_FROM_EXPLICIT_UNIT" not in _reasons(result)


@pytest.mark.parametrize("field", ["sum_assured", "rider_name", "both"])
def test_neighbor_or_multiple_primary_citations_cannot_supply_the_pair(field):
    draft, envelope, nodes = _source(second_amount="20")
    primary = [item for item in envelope.evidence if nodes[item.node_id].get("row_role") == "data"]
    assert len(primary) == 2
    draft = draft.model_copy(
        update={
            "fields": tuple(
                item.model_copy(
                    update={
                        "evidence_ids": tuple(p.evidence_id for p in primary)
                        if field == "both"
                        else (primary[1].evidence_id,)
                    }
                )
                if item.field_id == field
                or field == "both"
                and item.field_id in {"rider_name", "sum_assured"}
                else item
                for item in draft.fields
            )
        }
    )
    result = _recover(draft, envelope, nodes)
    assert "AMOUNT_SCALED_FROM_EXPLICIT_UNIT" not in _reasons(result)


def test_no_local_nodes_never_scales_a_number_from_free_text():
    draft, envelope, _nodes = _source()
    result = _recover(draft, envelope, None)
    assert "AMOUNT_SCALED_FROM_EXPLICIT_UNIT" not in _reasons(result)


def test_neighbor_marked_as_context_is_not_accepted_as_same_row_citation():
    draft, envelope, nodes = _source(second_amount="20")
    primary = [item for item in envelope.evidence if nodes[item.node_id].get("row_role") == "data"]
    envelope = _pack(
        tuple(
            replace(item, primary=False) if item == primary[1] else item
            for item in envelope.evidence
        )
    )
    draft = draft.model_copy(
        update={
            "fields": tuple(
                f.model_copy(update={"evidence_ids": (*f.evidence_ids, primary[1].evidence_id)})
                if f.field_id == "sum_assured"
                else f
                for f in draft.fields
            )
        }
    )
    assert "AMOUNT_SCALED_FROM_EXPLICIT_UNIT" not in _reasons(_recover(draft, envelope, nodes))


@pytest.mark.parametrize("missing", ["rider_name", "sum_assured"])
def test_missing_original_field_is_not_invented(missing):
    draft, envelope, nodes = _source()
    draft = draft.model_copy(
        update={"fields": tuple(f for f in draft.fields if f.field_id != missing)}
    )
    assert "AMOUNT_SCALED_FROM_EXPLICIT_UNIT" not in _reasons(_recover(draft, envelope, nodes))
