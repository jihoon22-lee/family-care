"""Propose coverage identity from bound document pages and native enrollment proof.

No database, source-file access, enrollment promotion or contract-link authority
belongs here. The repository must verify current household/contract bindings,
user overrides, complete bound-page source inventory and one-to-one constraints
before accepting any proposal.
Package line numbers are retained verbatim; they are never IR row coordinates.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

_SHA256 = re.compile(r"[0-9a-f]{64}")
_STRATEGY: Literal["UNIQUE_ENROLLED_NAME_ON_BOUND_PAGE"] = "UNIQUE_ENROLLED_NAME_ON_BOUND_PAGE"


@dataclass(frozen=True, repr=False)
class VerifiedDocumentBinding:
    source_binding_id: UUID
    document_version_id: UUID
    content_sha256: str
    page_count: int
    document_kind: str
    conflict: bool = False


@dataclass(frozen=True, repr=False)
class ProgramEnrollmentSource:
    """Repository-verified identity, with original publication authority retained."""

    household_space_id: UUID
    family_member_id: UUID
    policy_contract_id: UUID
    rider_id: UUID
    original_rider_name: str
    document_version_id: UUID
    content_sha256: str
    physical_page: int
    publication_candidate_version_id: UUID
    evidence_id: UUID
    generation_id: UUID
    physical_locator: Mapping[str, Any]
    source_refs: tuple[Mapping[str, Any], ...]
    authority: str = "PROGRAM_VERIFIED"
    source_layer: str = "native"
    conflict: bool = False
    publication_authority: Literal["PROGRAM_VERIFIED", "USER_CONFIRMED"] = "PROGRAM_VERIFIED"
    name_source_candidate_version_id: UUID | None = None


@dataclass(frozen=True, repr=False)
class CanonicalEnrollmentProof:
    source_binding_id: UUID
    private_evidence_location: dict[str, Any]
    publication_candidate_version_id: UUID
    evidence_id: UUID
    generation_id: UUID
    document_version_id: UUID
    physical_locator: dict[str, Any]
    source_refs: tuple[dict[str, Any], ...]
    publication_authority: Literal["PROGRAM_VERIFIED", "USER_CONFIRMED"]
    name_source_candidate_version_id: UUID


@dataclass(frozen=True, repr=False)
class CanonicalEnrollmentMatch:
    """An identity proposal; no enrollment, status or calculation approval."""

    policy_contract_id: UUID
    rider_id: UUID
    family_member_id: UUID
    strategy: Literal["UNIQUE_ENROLLED_NAME_ON_BOUND_PAGE"]
    proofs: tuple[CanonicalEnrollmentProof, ...]


def _name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _uuid(value: object) -> bool:
    return isinstance(value, UUID) and value.int != 0


def _binding_valid(binding: VerifiedDocumentBinding) -> bool:
    return (
        _uuid(binding.source_binding_id)
        and _uuid(binding.document_version_id)
        and _SHA256.fullmatch(binding.content_sha256) is not None
        and type(binding.page_count) is int
        and 1 <= binding.page_count <= 500
        and binding.document_kind == "policy"
        and binding.conflict is False
    )


def _native_box(candidate: ProgramEnrollmentSource) -> tuple[float, ...] | None:
    if (
        candidate.authority != "PROGRAM_VERIFIED"
        or candidate.publication_authority not in {"PROGRAM_VERIFIED", "USER_CONFIRMED"}
        or (
            candidate.name_source_candidate_version_id is not None
            and not _uuid(candidate.name_source_candidate_version_id)
        )
        or candidate.source_layer != "native"
        or candidate.conflict is not False
        or not all(
            _uuid(value)
            for value in (
                candidate.household_space_id,
                candidate.family_member_id,
                candidate.policy_contract_id,
                candidate.rider_id,
                candidate.document_version_id,
                candidate.publication_candidate_version_id,
                candidate.evidence_id,
                candidate.generation_id,
            )
        )
    ):
        return None
    locator = candidate.physical_locator
    if (
        set(locator) != {"schema_version", "content_sha256", "physical_page", "name_bbox"}
        or locator["schema_version"] != "enrollment-physical-v1"
        or locator["content_sha256"] != candidate.content_sha256
        or type(locator["physical_page"]) is not int
        or locator["physical_page"] != candidate.physical_page
    ):
        return None
    raw_box = locator["name_bbox"]
    if not isinstance(raw_box, (list, tuple)) or len(raw_box) != 4:
        return None
    if any(type(part) not in (int, float) or not math.isfinite(part) for part in raw_box):
        return None
    x0, y0, x1, y1 = (float(part) for part in raw_box)
    if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0 or not 1 <= len(candidate.source_refs) <= 64:
        return None
    primary = [
        ref for ref in candidate.source_refs if ref.get("evidence_id") == str(candidate.evidence_id)
    ]
    if len(primary) != 1:
        return None
    ref = primary[0]
    if (
        ref.get("document_version_id") != str(candidate.document_version_id)
        or not _uuid(UUID(ref["extraction_id"]))
        or _SHA256.fullmatch(ref["node_id"]) is None
        or type(ref.get("page")) is not int
        or ref["page"] != candidate.physical_page
        or type(ref.get("start")) is not int
        or type(ref.get("end")) is not int
        or not 0 <= ref["start"] < ref["end"]
        or ref.get("primary") is not True
        or ref.get("source_role") != "policy"
    ):
        return None
    return x0, y0, x1, y1


def _review_name(source: Mapping[str, Any]) -> str | None:
    review = source["certificate_review"]
    if (
        review.get("enrollment_decision") != "MATCH"
        or review.get("component_class") != "BENEFIT_COVERAGE"
        or review.get("evidence_inherited_from_rider_id") is not None
        or source.get("conflict", False) is not False
        or review.get("conflict", False) is not False
        or not isinstance(source.get("name"), str)
        or not isinstance(review.get("name"), str)
        or not _name(source["name"])
        or not _name(review["name"])
    ):
        return None
    if _name(source["name"]) != _name(review["name"]) and not all(
        isinstance(source.get(key), str)
        and 0 < len(source[key]) <= 240
        and source[key] == review.get(key)
        for key in ("canonical_policy_id", "canonical_rider_id")
    ):
        return None
    mapping = source.get("terms_mapping")
    if mapping is not None and (
        mapping.get("mapping_inherited_from_rider_id") is not None
        or mapping.get("enrollment_decision") != "MATCH"
        or mapping.get("component_class") != "BENEFIT_COVERAGE"
    ):
        return None
    for key in ("canonical_policy_id", "canonical_rider_id"):
        if key in source and source[key] != review.get(key):
            return None
    # A canonical label is presentation metadata. Its explicit certificate
    # review supplies the original name after the stable record IDs agree.
    return _name(review["name"])


def match_canonical_enrollment(
    source_record: Mapping[str, Any],
    bindings_by_alias: Mapping[str, VerifiedDocumentBinding],
    candidates: Sequence[ProgramEnrollmentSource],
    *,
    expected_family_member_id: UUID,
) -> CanonicalEnrollmentMatch | None:
    """Resolve each bound page by its unique exact native enrolled name.

    Equal bytes may have different DocumentVersion IDs after reimport. Every
    matching physical occurrence must agree on member, household, policy and
    Rider. Multiple rows on one page remain ambiguous even for the same Rider.
    Whitespace variants compete together; punctuation and monetary values are not
    used to collapse different names or choose between multiple physical locations.
    """
    try:
        if not _uuid(expected_family_member_id) or not 1 <= len(candidates) <= 10000:
            return None
        name = _review_name(source_record)
        if name is None:
            return None
        locations = source_record["certificate_review"]["evidence_locations"]
        if not isinstance(locations, (tuple, list)) or not 1 <= len(locations) <= 128:
            return None
        identities: set[tuple[UUID, UUID, UUID]] = set()
        proofs = []
        for location in locations:
            if (
                set(location) != {"document_alias", "line", "physical_page"}
                or not isinstance(location["document_alias"], str)
                or not location["document_alias"]
                or type(location["line"]) is not int
                or location["line"] < 1
                or type(location["physical_page"]) is not int
            ):
                return None
            binding = bindings_by_alias.get(location["document_alias"])
            if binding is None or not _binding_valid(binding):
                return None
            page = location["physical_page"]
            if not 1 <= page <= binding.page_count:
                return None
            relevant = [
                candidate
                for candidate in candidates
                if candidate.content_sha256 == binding.content_sha256
                and candidate.physical_page == page
                and _name(candidate.original_rider_name).replace(" ", "") == name.replace(" ", "")
            ]
            if not relevant:
                return None
            physical = set()
            for candidate in relevant:
                if candidate.family_member_id != expected_family_member_id:
                    return None
                box = _native_box(candidate)
                if box is None:
                    return None
                physical.add(box)
                identities.add(
                    (candidate.household_space_id, candidate.policy_contract_id, candidate.rider_id)
                )
                proofs.append(
                    CanonicalEnrollmentProof(
                        source_binding_id=binding.source_binding_id,
                        private_evidence_location=deepcopy(dict(location)),
                        publication_candidate_version_id=candidate.publication_candidate_version_id,
                        evidence_id=candidate.evidence_id,
                        generation_id=candidate.generation_id,
                        document_version_id=candidate.document_version_id,
                        physical_locator=deepcopy(dict(candidate.physical_locator)),
                        source_refs=tuple(deepcopy(dict(ref)) for ref in candidate.source_refs),
                        publication_authority=candidate.publication_authority,
                        name_source_candidate_version_id=(
                            candidate.name_source_candidate_version_id
                            or candidate.publication_candidate_version_id
                        ),
                    )
                )
            if len(physical) != 1 or len(identities) != 1:
                return None
        _, policy_id, rider_id = next(iter(identities))
        proofs.sort(
            key=lambda proof: (
                proof.private_evidence_location["document_alias"],
                proof.private_evidence_location["physical_page"],
                proof.private_evidence_location["line"],
                str(proof.source_binding_id),
                str(proof.publication_candidate_version_id),
                str(proof.evidence_id),
                str(proof.document_version_id),
                str(proof.generation_id),
            )
        )
        return CanonicalEnrollmentMatch(
            policy_contract_id=policy_id,
            rider_id=rider_id,
            family_member_id=expected_family_member_id,
            strategy=_STRATEGY,
            proofs=tuple(proofs),
        )
    except KeyError, TypeError, ValueError, AttributeError, OverflowError:
        return None
