"""One explicit original Code column, never a prose or generic table interpreter.

The region must come from the independently scoped source-layout observer. Row
provenance, exact header membership and every row are required. Recognition may
exceed transport/DSL citation budgets; callers retain that whole bundle unresolved
instead of compiling a prefix. Other columns, repeated headers and code ranges are
outside this finite subset.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from familycare_api.clauses.source_regions import ClauseSourceSpan
from familycare_api.terms_knowledge.source_layout import SemanticSourceRegion

_IDENTIFIER = r"[a-z0-9][a-z0-9._:-]{0,63}"
_DECLARATION = re.compile(
    rf"Medical event (classification|diagnosis code|procedure code) uses ({_IDENTIFIER}) "
    rf"version ({_IDENTIFIER}); eligible codes are listed in the following table\."
)
_CODE = re.compile(_IDENTIFIER)
_RANGE = re.compile(r"^(?:[a-z]*[0-9][a-z0-9.]*)(?:-|:)(?:[a-z]*[0-9][a-z0-9.]*)$")


@dataclass(frozen=True, slots=True, repr=False)
class ClassificationTableWitness:
    payload: dict[str, Any]
    spans: tuple[ClauseSourceSpan, ...]

    @property
    def statement(self) -> str:
        return "\n".join(span.text for span in self.spans)


def observe_classification_table(region: SemanticSourceRegion) -> ClassificationTableWitness | None:
    """Observe all 1..64 exact code rows following one declaration and Code header."""
    if not region.complete or region.kind != "appendix":
        return None
    spans = region.body_spans
    declarations = [(index, _DECLARATION.fullmatch(span.text)) for index, span in enumerate(spans)]
    matched = [(index, match) for index, match in declarations if match is not None]
    if len(matched) != 1:
        return None
    index, declaration = matched[0]
    rows = {row.node_id: row for row in region.table_rows}
    if len(rows) != len(region.table_rows) or index + 2 >= len(spans):
        return None
    if spans[index].node_id in rows:
        return None
    header_span = spans[index + 1]
    header = rows.get(header_span.node_id)
    if (
        header is None
        or header.row_role != "header"
        or header.column_indices != (0,)
        or header.header_node_ids
        or header_span.text != "Code"
    ):
        return None
    witness = [spans[index], header_span]
    codes = []
    represented_table_nodes = {header_span.node_id}
    for span in spans[index + 2 :]:
        row = rows.get(span.node_id)
        if row is None:
            break
        if (
            row.row_role != "data"
            or row.column_indices != (0,)
            or row.header_node_ids != (header_span.node_id,)
            or span.node_id in represented_table_nodes
            or not _CODE.fullmatch(span.text)
            or ".." in span.text
            or _RANGE.fullmatch(span.text)
            or span.text in codes
        ):
            return None
        represented_table_nodes.add(span.node_id)
        witness.append(span)
        codes.append(span.text)
    if not 1 <= len(codes) <= 64 or represented_table_nodes != set(rows):
        return None
    field = {
        "classification": "classification",
        "diagnosis code": "diagnosis_code",
        "procedure code": "procedure_code",
    }[declaration[1]]
    return ClassificationTableWitness(
        {
            "kind": "classification",
            "field": f"MedicalEvent.{field}",
            "code_system": declaration[2],
            "code_version": declaration[3],
            "codes": codes,
        },
        tuple(witness),
    )
