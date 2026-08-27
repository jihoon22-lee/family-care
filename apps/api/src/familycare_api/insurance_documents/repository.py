"""Direct-psycopg read model for member insurance-document inventory."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, cast
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from familycare_api.common.scope import HouseholdScope
from familycare_api.insurance_documents.domain import (
    DocumentRole,
    DuplicateState,
    InsuranceDocumentComponentRecord,
    InsuranceDocumentSetItemRecord,
    InsuranceDocumentSetRecord,
    InventoryComponent,
    InventoryPolicy,
    InventorySet,
    InventorySetItem,
    MemberInsuranceDocumentInventory,
    PolicyStatus,
    ProcessingState,
    ReviewState,
    UnreadableSource,
    build_member_inventory,
)
from familycare_api.policies.errors import (
    EvidenceInvalid,
    PolicyRepositoryUnavailable,
    PolicyStateConflict,
    VersionConflict,
)


def _database_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise PolicyRepositoryUnavailable
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _processing_state(row: dict[str, Any]) -> ProcessingState:
    item_state = row["item_state"]
    if item_state == "password_required":
        return "PASSWORD_REQUIRED"
    if row.get("ocr_state") == "failed":
        return "OCR_REQUIRED"
    if item_state in {"permanently_failed", "cancelled"}:
        return "FAILED"
    if item_state != "succeeded":
        return "PENDING"
    return "READY"


def _duplicate_state(row: dict[str, Any]) -> DuplicateState:
    if bool(row.get("has_cross_member_copy")):
        return "CROSS_MEMBER_COPY_POSSIBLE"
    if int(row.get("same_member_source_count") or 0) > 1:
        return "SAME_MEMBER_DUPLICATE"
    return "UNIQUE"


def _component(row: dict[str, Any]) -> InventoryComponent:
    return InventoryComponent(
        id=cast(UUID, row["component_id"]),
        document_batch_item_id=cast(UUID, row["document_batch_item_id"]),
        document_version_id=cast(UUID, row["document_version_id"]),
        content_sha256=cast(str, row["content_sha256"]),
        role=cast(DocumentRole, row["role"]),
        page_start=int(row["page_start"]),
        page_end=int(row["page_end"]),
        review_state=cast(ReviewState, row["component_review_state"]),
        processing_state=_processing_state(row),
        duplicate_state=_duplicate_state(row),
    )


_COMPONENT_SELECT = """
    SELECT
        component.id AS component_id,
        component.document_batch_item_id,
        component.document_version_id,
        version.content_sha256,
        component.role,
        component.page_start,
        component.page_end,
        component.review_state AS component_review_state,
        batch_item.state AS item_state,
        batch_item.ocr_state,
        (
            SELECT count(DISTINCT same_item.id)
            FROM document_batch_items AS same_item
            JOIN document_batches AS same_batch ON same_batch.id = same_item.batch_id
            JOIN document_versions AS same_version
              ON same_version.id = same_item.processed_document_version_id
            WHERE same_batch.household_space_id = component.household_space_id
              AND same_batch.family_member_id = component.family_member_id
              AND same_version.content_sha256 = version.content_sha256
        ) AS same_member_source_count,
        EXISTS (
            SELECT 1
            FROM document_batch_items AS other_item
            JOIN document_batches AS other_batch ON other_batch.id = other_item.batch_id
            JOIN document_versions AS other_version
              ON other_version.id = other_item.processed_document_version_id
            WHERE other_batch.household_space_id = component.household_space_id
              AND other_batch.family_member_id <> component.family_member_id
              AND other_version.content_sha256 = version.content_sha256
        ) AS has_cross_member_copy
    FROM insurance_document_components AS component
    JOIN document_versions AS version ON version.id = component.document_version_id
    JOIN document_batch_items AS batch_item
      ON batch_item.id = component.document_batch_item_id
"""

_SET_ITEM_SELECT = _COMPONENT_SELECT.replace(
    "    SELECT\n",
    """    SELECT
        set_item.id AS set_item_id,
        set_item.insurance_document_set_id,
        set_item.match_state,
        set_item.version AS set_item_version,
""",
    1,
)


def _component_record(row: dict[str, Any]) -> InsuranceDocumentComponentRecord:
    return InsuranceDocumentComponentRecord(
        id=cast(UUID, row["id"]),
        document_batch_item_id=cast(UUID, row["document_batch_item_id"]),
        role=cast(DocumentRole, row["role"]),
        page_start=int(row["page_start"]),
        page_end=int(row["page_end"]),
        review_state=cast(ReviewState, row["review_state"]),
        version=int(row["version"]),
    )


def _set_record(row: dict[str, Any]) -> InsuranceDocumentSetRecord:
    return InsuranceDocumentSetRecord(
        id=cast(UUID, row["id"]),
        member_id=cast(UUID, row["family_member_id"]),
        policy_contract_id=cast(UUID | None, row.get("policy_contract_id")),
        insurer_display=cast(str | None, row.get("insurer_display")),
        product_display=cast(str | None, row.get("product_display")),
        display_label=cast(str, row["display_label"]),
        version=int(row["version"]),
    )


def _set_item_record(row: dict[str, Any]) -> InsuranceDocumentSetItemRecord:
    return InsuranceDocumentSetItemRecord(
        id=cast(UUID, row["id"]),
        insurance_document_set_id=cast(UUID, row["insurance_document_set_id"]),
        insurance_document_component_id=cast(UUID, row["insurance_document_component_id"]),
        role=cast(DocumentRole, row["role"]),
        match_state=cast(ReviewState, row["match_state"]),
        version=int(row["version"]),
    )


class InsuranceDocumentRepository:
    """Apply server-owned household and member predicates to the inventory read model."""

    def __init__(self, database_url: str) -> None:
        self.database_url = _database_url(database_url)

    def get_inventory(
        self,
        scope: HouseholdScope,
        member_id: UUID,
    ) -> MemberInsuranceDocumentInventory | None:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                member = connection.execute(
                    """
                    SELECT id FROM family_members
                    WHERE id = %s AND household_space_id = %s AND deleted_at IS NULL
                    """,
                    (member_id, scope.household_space_id),
                ).fetchone()
                if member is None:
                    return None
                policy_rows = connection.execute(
                    """
                    SELECT DISTINCT
                        policy.id,
                        policy.source_document_version_id,
                        version.content_sha256 AS source_content_sha256,
                        source.physical_page AS source_evidence_page,
                        policy.insurer_display,
                        policy.product_display,
                        policy.status,
                        (
                            SELECT count(*)
                            FROM riders
                            WHERE policy_contract_id = policy.id
                              AND household_space_id = policy.household_space_id
                              AND deleted_at IS NULL
                        ) AS rider_count
                    FROM policy_contracts AS policy
                    JOIN policy_parties AS party
                      ON party.policy_contract_id = policy.id
                     AND party.household_space_id = policy.household_space_id
                     AND party.deleted_at IS NULL
                    JOIN document_versions AS version
                      ON version.id = policy.source_document_version_id
                    JOIN evidence AS source ON source.id = policy.source_evidence_id
                    WHERE policy.household_space_id = %s
                      AND policy.deleted_at IS NULL
                      AND party.family_member_id = %s
                    ORDER BY policy.id
                    """,
                    (scope.household_space_id, member_id),
                ).fetchall()
                set_rows = connection.execute(
                    """
                    SELECT id, policy_contract_id, insurer_display, product_display,
                           display_label, version
                    FROM insurance_document_sets
                    WHERE household_space_id = %s AND family_member_id = %s
                      AND deleted_at IS NULL
                    ORDER BY created_at, id
                    """,
                    (scope.household_space_id, member_id),
                ).fetchall()
                item_rows = connection.execute(
                    _SET_ITEM_SELECT
                    + """
                    JOIN insurance_document_set_items AS set_item
                      ON set_item.insurance_document_component_id = component.id
                    JOIN insurance_document_sets AS document_set
                      ON document_set.id = set_item.insurance_document_set_id
                    WHERE set_item.household_space_id = %s
                      AND set_item.family_member_id = %s
                      AND set_item.deleted_at IS NULL
                      AND document_set.deleted_at IS NULL
                      AND component.deleted_at IS NULL
                    ORDER BY set_item.created_at, set_item.id
                    """,
                    (scope.household_space_id, member_id),
                ).fetchall()
                unpaired_rows = connection.execute(
                    _COMPONENT_SELECT
                    + """
                    WHERE component.household_space_id = %s
                      AND component.family_member_id = %s
                      AND component.deleted_at IS NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM insurance_document_set_items AS active_item
                          JOIN insurance_document_sets AS active_set
                            ON active_set.id = active_item.insurance_document_set_id
                          WHERE active_item.insurance_document_component_id = component.id
                            AND active_item.deleted_at IS NULL
                            AND active_item.match_state <> 'REJECTED'
                            AND active_set.deleted_at IS NULL
                      )
                    ORDER BY component.created_at, component.id
                    """,
                    (scope.household_space_id, member_id),
                ).fetchall()
                unreadable_rows = connection.execute(
                    """
                    SELECT item.id AS document_batch_item_id, item.document_kind,
                           item.state AS item_state, item.ocr_state
                    FROM document_batch_items AS item
                    JOIN document_batches AS batch ON batch.id = item.batch_id
                    WHERE batch.household_space_id = %s
                      AND batch.family_member_id = %s
                      AND (
                          item.state IN ('password_required', 'permanently_failed')
                          OR item.ocr_state = 'failed'
                      )
                    ORDER BY item.created_at, item.id
                    """,
                    (scope.household_space_id, member_id),
                ).fetchall()
        except psycopg.Error:
            raise PolicyRepositoryUnavailable from None

        policies = tuple(
            InventoryPolicy(
                id=cast(UUID, row["id"]),
                source_document_version_id=cast(UUID, row["source_document_version_id"]),
                source_content_sha256=cast(str, row["source_content_sha256"]),
                source_evidence_page=int(row["source_evidence_page"]),
                insurer_display=cast(str, row["insurer_display"]),
                product_display=cast(str, row["product_display"]),
                status=cast(PolicyStatus, row["status"]),
                rider_count=int(row["rider_count"]),
            )
            for row in policy_rows
        )
        items_by_set: dict[UUID, list[InventorySetItem]] = defaultdict(list)
        for row in item_rows:
            items_by_set[cast(UUID, row["insurance_document_set_id"])].append(
                InventorySetItem(
                    id=cast(UUID, row["set_item_id"]),
                    version=int(row["set_item_version"]),
                    component=_component(row),
                    match_state=cast(ReviewState, row["match_state"]),
                )
            )
        document_sets = tuple(
            InventorySet(
                id=cast(UUID, row["id"]),
                policy_contract_id=cast(UUID | None, row.get("policy_contract_id")),
                insurer_display=cast(str | None, row.get("insurer_display")),
                product_display=cast(str | None, row.get("product_display")),
                display_label=cast(str, row["display_label"]),
                version=int(row["version"]),
                items=tuple(items_by_set[cast(UUID, row["id"])]),
            )
            for row in set_rows
        )
        return build_member_inventory(
            member_id,
            policies=policies,
            document_sets=document_sets,
            unpaired_components=tuple(_component(row) for row in unpaired_rows),
            unreadable_sources=tuple(self._unreadable_source(row) for row in unreadable_rows),
        )

    @staticmethod
    def _unreadable_source(row: dict[str, Any]) -> UnreadableSource:
        source_kind = cast(DocumentRole, row["document_kind"])
        processing_state = _processing_state(row)
        if processing_state not in {"PASSWORD_REQUIRED", "OCR_REQUIRED", "FAILED"}:
            raise PolicyRepositoryUnavailable
        labels: dict[DocumentRole, str] = {
            "policy": "보험증권 문서",
            "terms": "보험약관 문서",
            "product_explanation": "상품설명서 문서",
            "application": "청약서 문서",
            "supporting": "보조자료 문서",
        }
        return UnreadableSource(
            document_batch_item_id=cast(UUID, row["document_batch_item_id"]),
            source_kind=source_kind,
            display_label=labels[source_kind],
            processing_state=processing_state,
        )

    def create_component(
        self,
        scope: HouseholdScope,
        *,
        actor_id: UUID,
        member_id: UUID,
        document_batch_item_id: UUID,
        role: DocumentRole,
        page_start: int,
        page_end: int,
        evidence_id: UUID | None,
        review_state: ReviewState,
    ) -> InsuranceDocumentComponentRecord | None:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                source = connection.execute(
                    """
                    SELECT item.id AS document_batch_item_id, item.state,
                           version.id AS document_version_id, version.page_count
                    FROM document_batch_items AS item
                    JOIN document_batches AS batch ON batch.id = item.batch_id
                    JOIN document_versions AS version
                      ON version.id = item.processed_document_version_id
                     AND version.document_id = item.document_id
                    WHERE item.id = %s
                      AND batch.household_space_id = %s
                      AND batch.family_member_id = %s
                      AND item.state = 'succeeded'
                    FOR SHARE OF item, batch
                    """,
                    (document_batch_item_id, scope.household_space_id, member_id),
                ).fetchone()
                if source is None:
                    return None
                if page_end > int(source["page_count"]):
                    raise EvidenceInvalid
                self._validate_optional_evidence(
                    connection,
                    scope,
                    evidence_id=evidence_id,
                    document_version_id=cast(UUID, source["document_version_id"]),
                    page_start=page_start,
                    page_end=page_end,
                    require_confirmed=review_state == "USER_CONFIRMED",
                )
                overlap = connection.execute(
                    """
                    SELECT 1
                    FROM insurance_document_components
                    WHERE household_space_id = %s AND family_member_id = %s
                      AND document_version_id = %s AND role = %s
                      AND deleted_at IS NULL
                      AND NOT (page_end < %s OR page_start > %s)
                    LIMIT 1
                    """,
                    (
                        scope.household_space_id,
                        member_id,
                        source["document_version_id"],
                        role,
                        page_start,
                        page_end,
                    ),
                ).fetchone()
                stored_review_state: ReviewState = "CONFLICT" if overlap else review_state
                row = connection.execute(
                    """
                    INSERT INTO insurance_document_components (
                        household_space_id, family_member_id, document_batch_item_id,
                        document_version_id, role, page_start, page_end, evidence_id,
                        review_state, created_by
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, document_batch_item_id, role, page_start, page_end,
                              review_state, version
                    """,
                    (
                        scope.household_space_id,
                        member_id,
                        document_batch_item_id,
                        source["document_version_id"],
                        role,
                        page_start,
                        page_end,
                        evidence_id,
                        stored_review_state,
                        actor_id,
                    ),
                ).fetchone()
        except psycopg.errors.UniqueViolation:
            raise PolicyStateConflict from None
        except psycopg.Error:
            raise PolicyRepositoryUnavailable from None
        if row is None:
            raise PolicyRepositoryUnavailable
        return _component_record(row)

    def create_document_set(
        self,
        scope: HouseholdScope,
        *,
        actor_id: UUID,
        member_id: UUID,
        policy_contract_id: UUID | None,
        insurer_display: str | None,
        product_display: str | None,
        display_label: str,
    ) -> InsuranceDocumentSetRecord | None:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                member = connection.execute(
                    """
                    SELECT id FROM family_members
                    WHERE id = %s AND household_space_id = %s AND deleted_at IS NULL
                    FOR SHARE
                    """,
                    (member_id, scope.household_space_id),
                ).fetchone()
                if member is None:
                    return None
                if policy_contract_id is not None:
                    policy = connection.execute(
                        """
                        SELECT policy.id, policy.insurer_display, policy.product_display
                        FROM policy_contracts AS policy
                        JOIN policy_parties AS party
                          ON party.policy_contract_id = policy.id
                         AND party.household_space_id = policy.household_space_id
                         AND party.deleted_at IS NULL
                        WHERE policy.id = %s AND policy.household_space_id = %s
                          AND party.family_member_id = %s
                          AND policy.deleted_at IS NULL
                        LIMIT 1
                        FOR SHARE OF policy
                        """,
                        (policy_contract_id, scope.household_space_id, member_id),
                    ).fetchone()
                    if policy is None:
                        raise PolicyStateConflict
                    insurer_display = cast(str, policy["insurer_display"])
                    product_display = cast(str, policy["product_display"])
                    display_label = product_display
                row = connection.execute(
                    """
                    INSERT INTO insurance_document_sets (
                        household_space_id, family_member_id, policy_contract_id,
                        insurer_display, product_display, display_label, created_by
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, family_member_id, policy_contract_id, insurer_display,
                              product_display, display_label, version
                    """,
                    (
                        scope.household_space_id,
                        member_id,
                        policy_contract_id,
                        insurer_display,
                        product_display,
                        display_label,
                        actor_id,
                    ),
                ).fetchone()
        except psycopg.errors.UniqueViolation:
            raise PolicyStateConflict from None
        except psycopg.Error:
            raise PolicyRepositoryUnavailable from None
        if row is None:
            raise PolicyRepositoryUnavailable
        return _set_record(row)

    def attach_set_item(
        self,
        scope: HouseholdScope,
        *,
        actor_id: UUID,
        document_set_id: UUID,
        insurance_document_component_id: UUID,
        match_state: ReviewState,
        evidence_id: UUID | None,
        expected_set_version: int,
    ) -> InsuranceDocumentSetItemRecord | None:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                document_set = connection.execute(
                    """
                    SELECT id, family_member_id, policy_contract_id, version
                    FROM insurance_document_sets
                    WHERE id = %s AND household_space_id = %s AND deleted_at IS NULL
                    FOR UPDATE
                    """,
                    (document_set_id, scope.household_space_id),
                ).fetchone()
                if document_set is None:
                    return None
                if int(document_set["version"]) != expected_set_version:
                    raise VersionConflict
                component = connection.execute(
                    """
                    SELECT component.id, component.family_member_id,
                           component.document_batch_item_id,
                           component.document_version_id, component.role,
                           component.page_start, component.page_end,
                           component.review_state
                    FROM insurance_document_components AS component
                    JOIN document_batch_items AS item
                      ON item.id = component.document_batch_item_id
                    JOIN document_batches AS batch ON batch.id = item.batch_id
                    JOIN document_versions AS version
                      ON version.id = component.document_version_id
                     AND version.document_id = item.document_id
                    WHERE component.id = %s
                      AND component.household_space_id = %s
                      AND component.family_member_id = %s
                      AND batch.household_space_id = component.household_space_id
                      AND batch.family_member_id = component.family_member_id
                      AND component.deleted_at IS NULL
                    FOR SHARE OF component, item, batch, version
                    """,
                    (
                        insurance_document_component_id,
                        scope.household_space_id,
                        document_set["family_member_id"],
                    ),
                ).fetchone()
                if component is None:
                    raise PolicyStateConflict
                if (
                    match_state == "USER_CONFIRMED"
                    and component["review_state"] != "USER_CONFIRMED"
                ):
                    raise PolicyStateConflict
                self._validate_optional_evidence(
                    connection,
                    scope,
                    evidence_id=evidence_id,
                    document_version_id=cast(UUID, component["document_version_id"]),
                    page_start=int(component["page_start"]),
                    page_end=int(component["page_end"]),
                    require_confirmed=match_state == "USER_CONFIRMED",
                )
                policy_contract_id = cast(UUID | None, document_set.get("policy_contract_id"))
                if policy_contract_id is not None and component["role"] == "policy":
                    authority = connection.execute(
                        """
                        SELECT 1
                        FROM policy_contracts AS policy
                        JOIN evidence AS source ON source.id = policy.source_evidence_id
                        WHERE policy.id = %s AND policy.household_space_id = %s
                          AND policy.source_document_version_id = %s
                          AND source.physical_page BETWEEN %s AND %s
                          AND policy.deleted_at IS NULL
                        """,
                        (
                            policy_contract_id,
                            scope.household_space_id,
                            component["document_version_id"],
                            component["page_start"],
                            component["page_end"],
                        ),
                    ).fetchone()
                    if authority is None:
                        raise EvidenceInvalid
                confirmed = match_state == "USER_CONFIRMED"
                row = connection.execute(
                    """
                    INSERT INTO insurance_document_set_items (
                        household_space_id, family_member_id, insurance_document_set_id,
                        policy_contract_id, insurance_document_component_id,
                        document_batch_item_id, document_version_id, role, match_state,
                        evidence_id, confirmed_by, confirmed_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        CASE WHEN %s THEN clock_timestamp() ELSE NULL END
                    )
                    RETURNING id, insurance_document_set_id,
                              insurance_document_component_id, role, match_state, version
                    """,
                    (
                        scope.household_space_id,
                        document_set["family_member_id"],
                        document_set_id,
                        policy_contract_id,
                        insurance_document_component_id,
                        component["document_batch_item_id"],
                        component["document_version_id"],
                        component["role"],
                        match_state,
                        evidence_id,
                        actor_id if confirmed else None,
                        confirmed,
                    ),
                ).fetchone()
                connection.execute(
                    """
                    UPDATE insurance_document_sets
                    SET version = version + 1, updated_at = clock_timestamp()
                    WHERE id = %s
                    """,
                    (document_set_id,),
                )
        except psycopg.errors.UniqueViolation:
            raise PolicyStateConflict from None
        except psycopg.Error:
            raise PolicyRepositoryUnavailable from None
        if row is None:
            raise PolicyRepositoryUnavailable
        return _set_item_record(row)

    def detach_set_item(
        self,
        scope: HouseholdScope,
        *,
        item_id: UUID,
        expected_version: int,
    ) -> bool:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                item = connection.execute(
                    """
                    UPDATE insurance_document_set_items
                    SET deleted_at = clock_timestamp(), version = version + 1,
                        updated_at = clock_timestamp()
                    WHERE id = %s AND household_space_id = %s
                      AND version = %s AND deleted_at IS NULL
                    RETURNING insurance_document_set_id
                    """,
                    (item_id, scope.household_space_id, expected_version),
                ).fetchone()
                if item is None:
                    current = connection.execute(
                        """
                        SELECT 1 FROM insurance_document_set_items
                        WHERE id = %s AND household_space_id = %s AND deleted_at IS NULL
                        """,
                        (item_id, scope.household_space_id),
                    ).fetchone()
                    if current is not None:
                        raise VersionConflict
                    return False
                connection.execute(
                    """
                    UPDATE insurance_document_sets
                    SET version = version + 1, updated_at = clock_timestamp()
                    WHERE id = %s AND household_space_id = %s AND deleted_at IS NULL
                    """,
                    (item["insurance_document_set_id"], scope.household_space_id),
                )
                return True
        except psycopg.Error:
            raise PolicyRepositoryUnavailable from None

    def soft_delete_document_set(
        self,
        scope: HouseholdScope,
        *,
        document_set_id: UUID,
        expected_version: int,
    ) -> bool:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                document_set = connection.execute(
                    """
                    UPDATE insurance_document_sets
                    SET deleted_at = clock_timestamp(), version = version + 1,
                        updated_at = clock_timestamp()
                    WHERE id = %s AND household_space_id = %s
                      AND version = %s AND deleted_at IS NULL
                    RETURNING id
                    """,
                    (document_set_id, scope.household_space_id, expected_version),
                ).fetchone()
                if document_set is None:
                    current = connection.execute(
                        """
                        SELECT 1 FROM insurance_document_sets
                        WHERE id = %s AND household_space_id = %s AND deleted_at IS NULL
                        """,
                        (document_set_id, scope.household_space_id),
                    ).fetchone()
                    if current is not None:
                        raise VersionConflict
                    return False
                connection.execute(
                    """
                    UPDATE insurance_document_set_items
                    SET deleted_at = clock_timestamp(), version = version + 1,
                        updated_at = clock_timestamp()
                    WHERE insurance_document_set_id = %s AND household_space_id = %s
                      AND deleted_at IS NULL
                    """,
                    (document_set_id, scope.household_space_id),
                )
                return True
        except psycopg.Error:
            raise PolicyRepositoryUnavailable from None

    @staticmethod
    def _validate_optional_evidence(
        connection: Any,
        scope: HouseholdScope,
        *,
        evidence_id: UUID | None,
        document_version_id: UUID,
        page_start: int,
        page_end: int,
        require_confirmed: bool,
    ) -> None:
        if evidence_id is None:
            return
        evidence = connection.execute(
            """
            SELECT review_state
            FROM evidence
            WHERE id = %s AND household_space_id = %s
              AND document_version_id = %s
              AND physical_page BETWEEN %s AND %s
            """,
            (
                evidence_id,
                scope.household_space_id,
                document_version_id,
                page_start,
                page_end,
            ),
        ).fetchone()
        if evidence is None or (require_confirmed and evidence["review_state"] == "NEEDS_REVIEW"):
            raise EvidenceInvalid


__all__ = ["InsuranceDocumentRepository"]
