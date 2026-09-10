"""Append source-backed indemnity and conditional reduction compilation."""

from collections.abc import Sequence

from alembic import op

revision: str = "0071_source_calculations"
down_revision: str | Sequence[str] | None = "0070_semantic_activity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
      ALTER TABLE terms_semantic_publications
        DROP CONSTRAINT terms_semantic_publications_compiler_revision_check,
        ADD CONSTRAINT terms_semantic_publications_compiler_revision_check
          CHECK(compiler_revision IN (
            'terms-semantic-compiler-v1','terms-semantic-compiler-v2','terms-semantic-compiler-v3'
          ));
    """)


def downgrade() -> None:
    op.execute("""
      DO $$
      DECLARE new_calculation JSONPATH :=
        '$.** ? (@.op == "if" || @.operation == "if" || @ == "MedicalEvent.reduction_applies")';
      BEGIN
        IF EXISTS(SELECT 1 FROM medical_events
          WHERE facts_json ? 'MedicalEvent.reduction_applies') THEN
          RAISE EXCEPTION 'event reduction history prevents downgrade' USING ERRCODE='23514';
        END IF;
        IF EXISTS(SELECT 1 FROM terms_semantic_publications
          WHERE compiler_revision='terms-semantic-compiler-v3')
          OR EXISTS(SELECT 1 FROM guidance_review_publications
            WHERE compiler_revision='terms-semantic-compiler-v3') THEN
          RAISE EXCEPTION 'source calculation history prevents downgrade' USING ERRCODE='23514';
        END IF;
        IF EXISTS(SELECT 1 FROM private_knowledge_calculation_publications
            WHERE jsonb_path_exists(calculation_json, new_calculation))
          OR EXISTS(SELECT 1 FROM private_knowledge_rule_publications
            WHERE jsonb_path_exists(rule_json, new_calculation))
          OR EXISTS(SELECT 1 FROM private_knowledge_fact_normalizer_publications
            WHERE field_path='MedicalEvent.reduction_applies')
          OR EXISTS(SELECT 1 FROM coverage_rule_versions
            WHERE jsonb_path_exists(expression_json, new_calculation)
              OR input_field_paths ? 'MedicalEvent.reduction_applies')
          OR EXISTS(SELECT 1 FROM decision_runs
            WHERE jsonb_path_exists(local_guidance_json, new_calculation))
          OR EXISTS(SELECT 1 FROM guidance_review_inputs
            WHERE jsonb_path_exists(event_json, new_calculation)
              OR event_json->'facts' ? 'MedicalEvent.reduction_applies')
          OR EXISTS(SELECT 1 FROM guidance_review_results
            WHERE jsonb_path_exists(result_json, new_calculation))
          OR EXISTS(SELECT 1 FROM claim_case_snapshots
            WHERE jsonb_path_exists(candidate_snapshot_json, new_calculation)) THEN
          RAISE EXCEPTION 'conditional calculation history prevents downgrade'
            USING ERRCODE='23514';
        END IF;
      END $$;
      ALTER TABLE terms_semantic_publications
        DROP CONSTRAINT terms_semantic_publications_compiler_revision_check,
        ADD CONSTRAINT terms_semantic_publications_compiler_revision_check
          CHECK(compiler_revision IN ('terms-semantic-compiler-v1','terms-semantic-compiler-v2'));
    """)
