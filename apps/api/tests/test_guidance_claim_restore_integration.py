"""Restore respects verified coverage aliases and the claim creation event lock."""

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any
from uuid import UUID

import psycopg
import pytest
from familycare_api.claims.errors import ClaimInvalid
from familycare_api.claims.repository import ClaimRepository

from apps.api.tests.test_guidance_claim_concurrency_integration import (
    LinkedPrivateClaim,
    _psycopg_url,
    enrollment_database,  # noqa: F401
    native_database,  # noqa: F401
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from apps.api.tests.test_guidance_claim_concurrency_integration import (
    linked_private_claim as linked_private_claim,
)

pytestmark = pytest.mark.integration


def _snapshot(linked: LinkedPrivateClaim) -> tuple[Any, ...]:
    with psycopg.connect(_psycopg_url(linked.saved.url)) as connection:
        row = connection.execute(
            "SELECT candidate_snapshot_json,rule_snapshot_json,policy_snapshot_json,"
            "evidence_snapshot_json,calculation_snapshot_json,snapshot_sha256 "
            "FROM claim_case_snapshots WHERE claim_case_id=%s",
            (linked.original.id,),
        ).fetchone()
    assert row is not None
    return row


def _active_ids(linked: LinkedPrivateClaim) -> list[UUID]:
    with psycopg.connect(_psycopg_url(linked.saved.url)) as connection:
        return [
            row[0]
            for row in connection.execute(
                "SELECT id FROM claim_cases WHERE household_space_id=%s "
                "AND medical_event_id=%s AND deleted_at IS NULL ORDER BY id",
                (linked.saved.scope.household_space_id, linked.saved.event_id),
            ).fetchall()
        ]


def _delete_original(linked: LinkedPrivateClaim) -> int:
    claims = ClaimRepository(linked.saved.url)
    claims.soft_delete_claim_case(
        linked.saved.scope, linked.original.id, expected_version=linked.original.version
    )
    return int(
        claims.get_claim_case(linked.saved.scope, linked.original.id, deleted_only=True)["version"]
    )


def _create_operational(linked: LinkedPrivateClaim) -> dict[str, object]:
    return ClaimRepository(linked.saved.url).create_guidance_claim_case(
        linked.saved.scope,
        linked.saved.event_id,
        run_id=linked.linked_run_id,
        expected_event_version=1,
        coverage=linked.operational_ref,
    )


def test_restore_rejects_active_operational_alias_and_preserves_private_snapshot(
    linked_private_claim: LinkedPrivateClaim,
) -> None:
    linked = linked_private_claim
    before = _snapshot(linked)
    deleted_version = _delete_original(linked)
    replacement = _create_operational(linked)
    assert replacement["id"] != linked.original.id

    with pytest.raises(ClaimInvalid):
        ClaimRepository(linked.saved.url).restore_claim_case(
            linked.saved.scope, linked.original.id, expected_version=deleted_version
        )

    retained = ClaimRepository(linked.saved.url).get_claim_case(
        linked.saved.scope, linked.original.id, deleted_only=True
    )
    assert retained["version"] == deleted_version
    assert _active_ids(linked) == [replacement["id"]]
    assert _snapshot(linked) == before


def test_restore_without_active_alias_preserves_original_claim_and_snapshot(
    linked_private_claim: LinkedPrivateClaim,
) -> None:
    linked = linked_private_claim
    before = _snapshot(linked)
    deleted_version = _delete_original(linked)

    restored = ClaimRepository(linked.saved.url).restore_claim_case(
        linked.saved.scope, linked.original.id, expected_version=deleted_version
    )

    assert restored["id"] == linked.original.id
    assert restored["version"] == deleted_version + 1
    assert _active_ids(linked) == [linked.original.id]
    assert _create_operational(linked)["id"] == linked.original.id
    assert _snapshot(linked) == before


def test_restore_and_alias_creation_share_event_lock_and_leave_one_active_claim(
    linked_private_claim: LinkedPrivateClaim, monkeypatch: pytest.MonkeyPatch
) -> None:
    linked = linked_private_claim
    before = _snapshot(linked)
    deleted_version = _delete_original(linked)
    monkeypatch.setenv("PGAPPNAME", "synthetic-claim-restore-race")
    monkeypatch.setenv("PGOPTIONS", "-c statement_timeout=15000 -c lock_timeout=10000")
    ready = Barrier(3)

    def create() -> dict[str, object]:
        ready.wait(timeout=10)
        return _create_operational(linked)

    def restore() -> dict[str, object] | None:
        ready.wait(timeout=10)
        try:
            return ClaimRepository(linked.saved.url).restore_claim_case(
                linked.saved.scope, linked.original.id, expected_version=deleted_version
            )
        except ClaimInvalid:
            return None

    with psycopg.connect(_psycopg_url(linked.saved.url)) as blocker:
        blocker.execute(
            "SELECT id FROM medical_events WHERE id=%s FOR UPDATE", (linked.saved.event_id,)
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            created = pool.submit(create)
            restored = pool.submit(restore)
            ready.wait(timeout=10)
            try:
                with psycopg.connect(_psycopg_url(linked.saved.url), autocommit=True) as observer:
                    deadline = time.monotonic() + 8
                    while time.monotonic() < deadline:
                        waiting = observer.execute(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE datname=current_database() "
                            "AND application_name='synthetic-claim-restore-race' "
                            "AND wait_event_type='Lock'",
                        ).fetchone()[0]
                        if waiting == 2 or restored.done():
                            break
                        time.sleep(0.01)
                    assert waiting == 2, "restore and creation must both wait on the event lock"
            finally:
                blocker.commit()
            created_claim = created.result(timeout=20)
            restored_claim = restored.result(timeout=20)

    assert _active_ids(linked) == [created_claim["id"]]
    if restored_claim is not None:
        assert restored_claim["id"] == created_claim["id"] == linked.original.id
    else:
        assert created_claim["id"] != linked.original.id
    assert _snapshot(linked) == before
