"""A missing required source projection cannot establish a source alias."""

from typing import Any
from uuid import uuid4

import pytest
from familycare_api.policies import enrollment_alias


def test_missing_projection_does_not_approve_using_other_publications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original, alias, unavailable = uuid4(), uuid4(), uuid4()
    household = uuid4()
    association = {"anchor_refs": [{"page": 1}]}

    class Connection:
        def execute(self, *args: Any) -> Any:
            return self

        def fetchall(self) -> list[dict[str, Any]]:
            return [
                {"generation_id": original, "association_json": association, "rider_id": None},
                {"generation_id": alias, "association_json": association, "rider_id": uuid4()},
                {"generation_id": unavailable, "association_json": association, "rider_id": None},
            ]

    monkeypatch.setattr(
        enrollment_alias.StructureProjectionReader,
        "read",
        lambda self, generation, pages: None if generation == unavailable else {},
    )
    monkeypatch.setattr(enrollment_alias, "contract_source_locator", lambda *args: {"key": "same"})
    row: dict[str, Any] = dict.fromkeys(
        (
            "policy_contract_id",
            "source_content_sha256",
            "policy_source_document_version_id",
            "id",
            "source_document_version_id",
            "source_id",
        ),
        uuid4(),
    )
    row["source_content_sha256"] = "a" * 64
    assert not enrollment_alias.proven_rider_source_alias(Connection(), household, row)
