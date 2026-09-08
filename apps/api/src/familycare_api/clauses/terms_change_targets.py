"""Resolve bounded, already source-verified change identities to existing IDs.

The caller owns permissions, household/member ownership, currentness, complete
candidate discovery and independent verification against the retained source.
In particular, rider aliases must come from original policy evidence or verified
publication evidence; a current display name is not an alias proof. This module
performs no IO, source verification, enrollment, status or date selection.

CLAUSE remains unresolved until a separate resolver verifies original clause
aliases and the edition-to-contract relationship. Never adapt an unresolved
RIDER/CLAUSE scope to a contract-wide selection merely because its ID is None.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from unicodedata import normalize
from uuid import UUID

TargetStatus = Literal["MATCH", "NO_MATCH", "UNKNOWN"]
_MAX_CANDIDATES = 512
_MAX_ALIASES = 32
_MAX_TOTAL_ALIASES = 4096
_MAX_TEXT = 262144


@dataclass(frozen=True, slots=True, repr=False)
class TermsChangeTargetRequest:
    household_space_id: UUID
    status: TargetStatus
    family_member_id: UUID | None = None
    contract_number_sha256: str | None = None
    insurer_key: str | None = None
    scope_kind: str | None = None
    rider_name_key: str | None = None
    clause_label_key: str | None = None
    operation: str | None = None
    previous_terms_code: str | None = None
    previous_edition_code: str | None = None
    new_terms_code: str | None = None
    new_edition_code: str | None = None
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedContractCandidate:
    policy_contract_id: UUID
    household_space_id: UUID
    family_member_id: UUID
    contract_number_sha256: str
    insurer_key: str


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedRiderCandidate:
    rider_id: UUID
    household_space_id: UUID
    policy_contract_id: UUID
    family_member_id: UUID
    source_alias_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedEditionCandidate:
    terms_edition_id: UUID
    household_space_id: UUID
    insurer_key: str
    terms_code: str
    edition_code: str


@dataclass(frozen=True, slots=True, repr=False)
class ResolvedTermsChangeTargets:
    status: TargetStatus
    reason_codes: tuple[str, ...]
    household_space_id: UUID
    family_member_id: UUID | None
    scope_kind: str | None
    scope_resolved: bool
    policy_contract_id: UUID | None = None
    rider_id: UUID | None = None
    clause_id: UUID | None = None
    previous_edition_id: UUID | None = None
    new_edition_id: UUID | None = None


class TermsChangeTargetError(ValueError):
    def __init__(self) -> None:
        super().__init__("TERMS_CHANGE_TARGET_INPUT_INVALID")


def _key(value: str | None) -> str | None:
    return (
        " ".join(normalize("NFKC", value).casefold().split()) or None if value is not None else None
    )


def _text(value: str) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 240 and _key(value) is not None


def _digest(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _ids(*values: UUID) -> bool:
    return all(isinstance(value, UUID) for value in values)


def _validate(
    request: TermsChangeTargetRequest,
    contracts: Sequence[VerifiedContractCandidate],
    riders: Sequence[VerifiedRiderCandidate],
    editions: Sequence[VerifiedEditionCandidate],
) -> None:
    if (
        not isinstance(request, TermsChangeTargetRequest)
        or not _ids(request.household_space_id)
        or (request.family_member_id is not None and not _ids(request.family_member_id))
        or request.status not in ("MATCH", "NO_MATCH", "UNKNOWN")
        or (
            request.contract_number_sha256 is not None
            and not _digest(request.contract_number_sha256)
        )
        or not isinstance(request.reason_codes, tuple)
        or len(request.reason_codes) > 32
        or any(
            not isinstance(reason, str)
            or not 1 <= len(reason) <= 80
            or not reason.isascii()
            or not all(char.isupper() or char.isdigit() or char == "_" for char in reason)
            for reason in request.reason_codes
        )
    ):
        raise TermsChangeTargetError
    request_text = (
        request.insurer_key,
        request.scope_kind,
        request.rider_name_key,
        request.clause_label_key,
        request.operation,
        request.previous_terms_code,
        request.previous_edition_code,
        request.new_terms_code,
        request.new_edition_code,
    )
    if any(value is not None and not _text(value) for value in request_text):
        raise TermsChangeTargetError
    for candidates in (contracts, riders, editions):
        if not isinstance(candidates, Sequence) or len(candidates) > _MAX_CANDIDATES:
            raise TermsChangeTargetError
    texts = [value for value in request_text if value is not None]
    for contract in contracts:
        if (
            not isinstance(contract, VerifiedContractCandidate)
            or not _ids(
                contract.policy_contract_id, contract.household_space_id, contract.family_member_id
            )
            or not _digest(contract.contract_number_sha256)
            or not _text(contract.insurer_key)
        ):
            raise TermsChangeTargetError
        texts.append(contract.insurer_key)
    alias_count = 0
    for rider in riders:
        if (
            not isinstance(rider, VerifiedRiderCandidate)
            or not _ids(
                rider.rider_id,
                rider.household_space_id,
                rider.policy_contract_id,
                rider.family_member_id,
            )
            or not isinstance(rider.source_alias_keys, tuple)
            or len(rider.source_alias_keys) > _MAX_ALIASES
            or any(not _text(alias) for alias in rider.source_alias_keys)
        ):
            raise TermsChangeTargetError
        alias_count += len(rider.source_alias_keys)
        texts.extend(rider.source_alias_keys)
    for edition in editions:
        if (
            not isinstance(edition, VerifiedEditionCandidate)
            or not _ids(edition.terms_edition_id, edition.household_space_id)
            or any(
                not _text(value)
                for value in (edition.insurer_key, edition.terms_code, edition.edition_code)
            )
        ):
            raise TermsChangeTargetError
        texts.extend((edition.insurer_key, edition.terms_code, edition.edition_code))
    if alias_count > _MAX_TOTAL_ALIASES or sum(len(text) for text in texts) > _MAX_TEXT:
        raise TermsChangeTargetError


def _unique(identifiers: set[UUID], prefix: str, issues: set[str]) -> UUID | None:
    if len(identifiers) == 1:
        return next(iter(identifiers))
    issues.add(f"{prefix}_TARGET_AMBIGUOUS" if identifiers else f"{prefix}_TARGET_UNRESOLVED")
    return None


def _edition(
    request: TermsChangeTargetRequest,
    terms_code: str | None,
    edition_code: str | None,
    candidates: Sequence[VerifiedEditionCandidate],
    prefix: str,
    issues: set[str],
) -> UUID | None:
    insurer, terms, edition = _key(request.insurer_key), _key(terms_code), _key(edition_code)
    identifiers = {
        candidate.terms_edition_id
        for candidate in candidates
        if insurer is not None
        and terms is not None
        and edition is not None
        and candidate.household_space_id == request.household_space_id
        and _key(candidate.insurer_key) == insurer
        and _key(candidate.terms_code) == terms
        and _key(candidate.edition_code) == edition
    }
    return _unique(identifiers, prefix, issues)


def resolve_terms_change_targets(
    request: TermsChangeTargetRequest,
    contracts: Sequence[VerifiedContractCandidate],
    riders: Sequence[VerifiedRiderCandidate],
    editions: Sequence[VerifiedEditionCandidate],
) -> ResolvedTermsChangeTargets:
    """Return unique IDs and partial uncertainty, without creating any targets.

    An empty candidate set is UNKNOWN, not proof that a referenced target cannot
    exist. Input limits fail explicitly, never silently selecting a truncated
    candidate set. Date/interval composition belongs to terms_change_selection.
    """
    _validate(request, contracts, riders, editions)
    if request.status == "NO_MATCH":
        return ResolvedTermsChangeTargets(
            "NO_MATCH",
            tuple(sorted({*request.reason_codes, "SOURCE_CHANGE_NO_MATCH"})),
            request.household_space_id,
            None,
            request.scope_kind,
            False,
        )
    issues: set[str] = set()
    insurer = _key(request.insurer_key)
    contract_ids = {
        candidate.policy_contract_id
        for candidate in contracts
        if request.contract_number_sha256 is not None
        and request.family_member_id is not None
        and insurer is not None
        and candidate.household_space_id == request.household_space_id
        and candidate.family_member_id == request.family_member_id
        and candidate.contract_number_sha256 == request.contract_number_sha256
        and _key(candidate.insurer_key) == insurer
    }
    policy_id = _unique(contract_ids, "CONTRACT", issues)
    rider_id = None
    if request.scope_kind in {"RIDER", "CLAUSE"} and (
        request.scope_kind == "RIDER" or request.rider_name_key is not None
    ):
        rider_ids = {
            candidate.rider_id
            for candidate in riders
            if policy_id is not None
            and request.rider_name_key is not None
            and candidate.household_space_id == request.household_space_id
            and candidate.family_member_id == request.family_member_id
            and candidate.policy_contract_id == policy_id
            and _key(request.rider_name_key)
            in {_key(alias) for alias in candidate.source_alias_keys}
        }
        rider_id = _unique(rider_ids, "RIDER", issues)
    scope_resolved = policy_id is not None and (
        request.scope_kind == "CONTRACT" or (request.scope_kind == "RIDER" and rider_id is not None)
    )
    if request.scope_kind == "CLAUSE":
        issues.add("CLAUSE_TARGET_UNRESOLVED")
    elif request.scope_kind not in {"CONTRACT", "RIDER"}:
        issues.add("CHANGE_SCOPE_UNRESOLVED")
    if (
        request.scope_kind == "CONTRACT" and (request.rider_name_key or request.clause_label_key)
    ) or (request.scope_kind == "RIDER" and request.clause_label_key):
        issues.add("CHANGE_SCOPE_CONFLICT")
        scope_resolved = False
    previous_id = None
    if (
        request.operation == "REPLACE"
        or request.previous_terms_code
        or request.previous_edition_code
    ):
        previous_id = _edition(
            request,
            request.previous_terms_code,
            request.previous_edition_code,
            editions,
            "PREVIOUS_EDITION",
            issues,
        )
    new_id = _edition(
        request, request.new_terms_code, request.new_edition_code, editions, "NEW_EDITION", issues
    )
    if request.operation not in {"ADD", "REPLACE"}:
        issues.add("CHANGE_OPERATION_UNRESOLVED")
    if request.operation == "ADD" and (
        request.previous_terms_code or request.previous_edition_code
    ):
        issues.add("CHANGE_OPERATION_TARGET_CONFLICT")
    if previous_id is not None and previous_id == new_id:
        issues.add("CHANGE_EDITION_TARGET_CONFLICT")
    if request.status == "UNKNOWN":
        issues.add("SOURCE_CHANGE_UNRESOLVED")
    return ResolvedTermsChangeTargets(
        status="UNKNOWN" if issues else "MATCH",
        reason_codes=tuple(sorted(issues | set(request.reason_codes))),
        household_space_id=request.household_space_id,
        family_member_id=request.family_member_id,
        scope_kind=request.scope_kind,
        scope_resolved=scope_resolved,
        policy_contract_id=policy_id,
        rider_id=rider_id,
        previous_edition_id=previous_id,
        new_edition_id=new_id,
    )
