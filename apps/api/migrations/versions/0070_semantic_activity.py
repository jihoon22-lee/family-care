"""Admit explicit treatment activity compilation while preserving prior publications."""

from collections.abc import Sequence

from alembic import op

revision: str = "0070_semantic_activity"
down_revision: str | Sequence[str] | None = "0069_policy_draft_replay"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
      ALTER TABLE terms_semantic_publications
        DROP CONSTRAINT terms_semantic_publications_compiler_revision_check,
        ADD CONSTRAINT terms_semantic_publications_compiler_revision_check
          CHECK(compiler_revision IN ('terms-semantic-compiler-v1','terms-semantic-compiler-v2'));
    """)


def downgrade() -> None:
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM medical_event_fact_versions
          WHERE facts_json ? 'diagnosis_confirmed'
            OR jsonb_path_exists(questions_json,
              '$[*] ? (@.field_id == "diagnosis_confirmed")')) THEN
          RAISE EXCEPTION 'event diagnosis history prevents downgrade' USING ERRCODE='23514';
        END IF;
        IF EXISTS(SELECT 1 FROM terms_semantic_publications
          WHERE compiler_revision='terms-semantic-compiler-v2') THEN
          RAISE EXCEPTION 'semantic activity history prevents downgrade' USING ERRCODE='23514';
        END IF;
      END $$;
      ALTER TABLE terms_semantic_publications
        DROP CONSTRAINT terms_semantic_publications_compiler_revision_check,
        ADD CONSTRAINT terms_semantic_publications_compiler_revision_check
          CHECK(compiler_revision='terms-semantic-compiler-v1');
    """)
