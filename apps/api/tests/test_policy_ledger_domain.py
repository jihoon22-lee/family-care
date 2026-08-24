"""Domain and repository contracts for the household-scoped policy ledger."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from decimal import Decimal
from types import TracebackType
from typing import Any, Literal, Self, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from familycare_api.common.evidence import EvidenceRef, EvidenceRepository
from familycare_api.common.scope import (
    HouseholdScope,
    HouseholdScopeUnavailable,
    resolve_household_scope,
)
from familycare_api.common.versions import InvalidVersion, require_expected_version
from familycare_api.policies.errors import EvidenceInvalid, VersionConflict
from familycare_api.policies.repository import PolicyLedgerRepository
from starlette.requests import Request

SYNTHETIC_DATABASE_URL = (
    "postgresql+psycopg://synthetic-ledger:synthetic-only@127.0.0.1:5432/synthetic"
)


class _Result:
    def __init__(self, rows: Sequence[dict[str, Any]] = ()) -> None:
        self.rows = list(rows)

    def fetchone(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self.rows)


class _Connection(AbstractContextManager["_Connection"]):
    def __init__(self, rows: Sequence[dict[str, Any]] = ()) -> None:
        self.rows = list(rows)
        self.queries: list[tuple[str, tuple[Any, ...]]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        del exc_type, exc_value, traceback
        return False

    def execute(
        self,
        query: str,
        params: Sequence[Any] | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> _Result:
        del args, kwargs
        normalized = " ".join(query.split()).lower()
        self.queries.append((normalized, tuple(params or ())))
        return _Result(self.rows)


def _connect_with(connection: _Connection) -> Any:
    def connect(*args: Any, **kwargs: Any) -> _Connection:
        del args, kwargs
        return connection

    return connect


def _request_with_client_scope(scope_id: UUID) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/policies",
            "headers": [(b"x-household-space-id", str(scope_id).encode())],
            "query_string": f"household_space_id={scope_id}".encode(),
        }
    )


def test_default_scope_resolver_fails_closed_and_ignores_client_scope() -> None:
    with pytest.raises(HouseholdScopeUnavailable):
        resolve_household_scope(_request_with_client_scope(uuid4()))


@pytest.mark.parametrize("value", [0, -1, True])
def test_expected_version_must_be_a_positive_non_boolean_integer(value: Any) -> None:
    with pytest.raises(InvalidVersion):
        require_expected_version(value)


def test_evidence_ref_rejects_invalid_page_hash_and_bbox() -> None:
    evidence_id = uuid4()
    document_version_id = uuid4()
    extraction_id = uuid4()

    with pytest.raises(EvidenceInvalid):
        EvidenceRef(
            evidence_id=evidence_id,
            document_version_id=document_version_id,
            extraction_id=extraction_id,
            content_sha256="a" * 64,
            physical_page=0,
            bbox=None,
            review_state="AI_VERIFIED",
        )
    with pytest.raises(EvidenceInvalid):
        EvidenceRef(
            evidence_id=evidence_id,
            document_version_id=document_version_id,
            extraction_id=extraction_id,
            content_sha256="a" * 64,
            physical_page=1,
            bbox=(Decimal("-1"), Decimal("2"), Decimal("3"), Decimal("4")),
            review_state="AI_VERIFIED",
        )
    with pytest.raises(EvidenceInvalid):
        EvidenceRef(
            evidence_id=evidence_id,
            document_version_id=document_version_id,
            extraction_id=extraction_id,
            content_sha256="not-a-hash",
            physical_page=1,
            bbox=None,
            review_state="AI_VERIFIED",
        )
    with pytest.raises(EvidenceInvalid):
        EvidenceRef(
            evidence_id=evidence_id,
            document_version_id=document_version_id,
            extraction_id=extraction_id,
            content_sha256="a" * 64,
            physical_page=1,
            bbox=(Decimal("3"), Decimal("2"), Decimal("1"), Decimal("4")),
            review_state="AI_VERIFIED",
        )


def test_evidence_repository_requires_same_scope_document_extraction_and_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = HouseholdScope(uuid4())
    document_version_id = uuid4()
    extraction_id = uuid4()
    evidence_id = uuid4()
    connection = _Connection(
        [
            {
                "evidence_id": evidence_id,
                "document_version_id": document_version_id,
                "extraction_id": extraction_id,
                "evidence_hash": "a" * 64,
                "document_hash": "a" * 64,
                "document_page_count": 3,
                "extraction_document_version_id": document_version_id,
                "physical_page": 1,
                "x0": None,
                "y0": None,
                "x1": None,
                "y1": None,
                "review_state": "AI_VERIFIED",
                "document_kind": "policy",
                "page_width": Decimal("100"),
                "page_height": Decimal("100"),
            }
        ]
    )
    monkeypatch.setattr(psycopg, "connect", _connect_with(connection))

    evidence = EvidenceRepository(SYNTHETIC_DATABASE_URL).validate_for_document(
        scope,
        evidence_id,
        document_version_id,
    )

    assert evidence.extraction_id == extraction_id
    query, params = connection.queries[0]
    assert "evidence.household_space_id = %s" in query
    assert "document.document_kind = 'policy'" in query
    assert "extraction.document_version_id = evidence.document_version_id" in query
    assert "page.page_number = evidence.physical_page" in query
    assert params == (evidence_id, scope.household_space_id, document_version_id)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("document_hash", "b" * 64),
        ("extraction_document_version_id", uuid4()),
        ("document_kind", "terms"),
        ("physical_page", 0),
        ("review_state", "NEEDS_REVIEW"),
        ("x1", Decimal("101")),
        ("document_page_count", 0),
    ],
)
def test_evidence_repository_rejects_stale_or_non_policy_lineage(
    field: str,
    value: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = HouseholdScope(uuid4())
    document_version_id = uuid4()
    row = {
        "evidence_id": uuid4(),
        "document_version_id": document_version_id,
        "extraction_id": uuid4(),
        "evidence_hash": "a" * 64,
        "document_hash": "a" * 64,
        "document_page_count": 3,
        "extraction_document_version_id": document_version_id,
        "physical_page": 1,
        "x0": None,
        "y0": None,
        "x1": None,
        "y1": None,
        "review_state": "USER_CONFIRMED",
        "document_kind": "policy",
        "page_width": Decimal("100"),
        "page_height": Decimal("100"),
    }
    row[field] = value
    monkeypatch.setattr(psycopg, "connect", _connect_with(_Connection([row])))

    with pytest.raises(EvidenceInvalid):
        EvidenceRepository(SYNTHETIC_DATABASE_URL).validate_for_document(
            scope,
            cast(UUID, row["evidence_id"]),
            document_version_id,
        )


def test_family_member_lookup_is_household_scoped_and_excludes_trash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = HouseholdScope(uuid4())
    member_id = uuid4()
    connection = _Connection(
        [
            {
                "id": member_id,
                "household_space_id": scope.household_space_id,
                "display_name": "Family Member A",
                "internal_alias": "member-a",
                "version": 1,
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
                "deleted_at": None,
            }
        ]
    )
    monkeypatch.setattr(psycopg, "connect", _connect_with(connection))

    member = PolicyLedgerRepository(SYNTHETIC_DATABASE_URL).get_family_member(
        scope,
        member_id,
    )

    assert member is not None and member.id == member_id
    query, params = connection.queries[0]
    assert "household_space_id = %s" in query
    assert "deleted_at is null" in query
    assert params == (member_id, scope.household_space_id)


def test_stale_family_member_update_raises_without_unscoped_followup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = HouseholdScope(uuid4())
    member_id = uuid4()
    connection = _Connection()
    monkeypatch.setattr(psycopg, "connect", _connect_with(connection))

    with pytest.raises(VersionConflict):
        PolicyLedgerRepository(SYNTHETIC_DATABASE_URL).update_family_member(
            scope,
            member_id,
            expected_version=2,
            display_name="Family Member A Updated",
            internal_alias="member-a",
        )

    assert len(connection.queries) == 1
    query, params = connection.queries[0]
    assert "household_space_id = %s" in query
    assert "version = %s" in query
    assert "deleted_at is null" in query
    assert params[-3:] == (member_id, scope.household_space_id, 2)
