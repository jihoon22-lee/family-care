"""Replay retained enrollment identities for claim deduplication and paid history.

Historical aliases do not become current candidate source variants. No field,
claim snapshot or superseded canonical link is rewritten by this reader.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any
from uuid import UUID

import psycopg

from familycare_api.common.scope import HouseholdScope
from familycare_api.insurance_reconciliation.canonical_repository import (
    CanonicalCoverageLink,
    CanonicalLinkError,
    CanonicalLinkRepository,
    _proposals,
)

_MAX_REFERENCED_COVERAGES = 10000
_MAX_HISTORICAL_RUNS = 32
_MAX_RETAINED_LINKS = 10000


def _proof_anchors(proofs: Any) -> frozenset[str]:
    # Publication versions and corrected ledger values may evolve. The original
    # binding, document and physical enrollment occurrence must remain the same.
    if not isinstance(proofs, (list, tuple)) or not proofs:
        raise CanonicalLinkError
    return frozenset(
        json.dumps(
            {
                "binding": proof["source_binding_id"],
                "document": proof["document_version_id"],
                "locator": proof["physical_locator"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        for proof in proofs
    )


def read_claim_coverage_aliases(
    connection: psycopg.Connection[dict[str, Any]],
    scope: HouseholdScope,
    *,
    family_member_id: UUID,
    rider_ids: tuple[UUID, ...],
    current_links: tuple[CanonicalCoverageLink, ...] = (),
) -> tuple[CanonicalCoverageLink, ...]:
    """Return current and independently replayed, previously saved claim aliases.

    Only private sources actually referenced by this member's claims or history are
    considered. Current links may be supplied from the same transaction's verified
    read. Historical identities still require the entire run's one-to-one and
    complete native source checks, including current overrides and deletions.
    """
    if not rider_ids:
        return ()
    requested = set(rider_ids)
    try:
        current = current_links or CanonicalLinkRepository.read_in_transaction(connection, scope)
        aliases = {
            link.knowledge_coverage_id: link
            for link in current
            if link.family_member_id == family_member_id and link.rider_id in requested
        }
        referenced = connection.execute(
            """
            WITH claimed AS (
              SELECT private_coverage_id FROM claim_cases
              WHERE household_space_id=%(household)s AND family_member_id=%(member)s
                AND private_coverage_id IS NOT NULL
              UNION
              SELECT private_coverage_id FROM claim_history
              WHERE household_space_id=%(household)s AND family_member_id=%(member)s
                AND private_coverage_id IS NOT NULL
            )
            SELECT c.id,c.import_run_id FROM claimed
            JOIN private_knowledge_coverages c ON c.id=claimed.private_coverage_id
              AND c.household_space_id=%(household)s
            JOIN private_knowledge_import_runs run ON run.id=c.import_run_id
              AND run.household_space_id=c.household_space_id
              AND run.state='SUPERSEDED' AND NOT run.is_current
            ORDER BY c.import_run_id,c.id LIMIT %(limit)s
            """,
            {
                "household": scope.household_space_id,
                "member": family_member_id,
                "limit": _MAX_REFERENCED_COVERAGES + 1,
            },
        ).fetchall()
        if len(referenced) > _MAX_REFERENCED_COVERAGES:
            raise CanonicalLinkError
        runs: dict[UUID, set[UUID]] = defaultdict(set)
        for row in referenced:
            runs[row["import_run_id"]].add(row["id"])
        if len(runs) > _MAX_HISTORICAL_RUNS:
            raise CanonicalLinkError
        if not runs:
            return tuple(aliases[key] for key in sorted(aliases))
        saved = connection.execute(
            """
            SELECT knowledge_coverage_id,knowledge_contract_id,import_run_id,
              family_member_id,policy_contract_id,rider_id,proofs
            FROM private_knowledge_canonical_links
            WHERE household_space_id=%s AND family_member_id=%s AND rider_id=ANY(%s)
              AND knowledge_coverage_id=ANY(%s)
            ORDER BY knowledge_coverage_id,id LIMIT %s
            """,
            (
                scope.household_space_id,
                family_member_id,
                list(requested),
                [row["id"] for row in referenced],
                _MAX_RETAINED_LINKS + 1,
            ),
        ).fetchall()
        if len(saved) > _MAX_RETAINED_LINKS:
            raise CanonicalLinkError
        retained: dict[UUID, list[dict[str, Any]]] = defaultdict(list)
        for row in saved:
            retained[row["knowledge_coverage_id"]].append(row)
        for run_id, coverage_ids in runs.items():
            if not coverage_ids.intersection(retained):
                continue
            # Replaying each run separately preserves the one-to-one checks;
            # combining runs would mistake successive versions for duplicates.
            for link in _proposals(connection, scope, import_run_id=run_id):
                if (
                    link.knowledge_coverage_id not in coverage_ids
                    or link.family_member_id != family_member_id
                    or link.rider_id not in requested
                ):
                    continue
                for old in retained[link.knowledge_coverage_id]:
                    if (
                        old["knowledge_contract_id"] == link.knowledge_contract_id
                        and old["import_run_id"] == link.import_run_id
                        and old["policy_contract_id"] == link.policy_contract_id
                        and old["rider_id"] == link.rider_id
                        and _proof_anchors(old["proofs"]) == _proof_anchors(link.proofs)
                    ):
                        aliases[link.knowledge_coverage_id] = link
                        break
        return tuple(aliases[key] for key in sorted(aliases))
    except psycopg.Error, KeyError, ValueError, TypeError:
        raise CanonicalLinkError from None
