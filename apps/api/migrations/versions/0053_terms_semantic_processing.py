"""Retain full source processing manifests and resumable local graph receipts."""

from collections.abc import Sequence

from alembic import op

revision: str = "0053_terms_semantic_processing"
down_revision: str | Sequence[str] | None = "0052_terms_semantic_knowledge"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(r"""
      ALTER TABLE terms_semantic_publications DROP CONSTRAINT
        terms_semantic_publications_candidate_id_verifier_revision__key;
      ALTER TABLE terms_semantic_publications ADD CONSTRAINT uq_terms_semantic_source_proof
        UNIQUE(candidate_id,verifier_revision,compiler_revision,proof_sha256);
      CREATE TABLE terms_semantic_processing_runs(
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        household_space_id UUID NOT NULL REFERENCES household_spaces(id) ON DELETE RESTRICT,
        terms_edition_id UUID NOT NULL REFERENCES terms_editions(id) ON DELETE RESTRICT,
        input_context JSONB NOT NULL,
        input_digest TEXT NOT NULL CHECK(input_digest ~ '^[0-9a-f]{64}$'),
        planner_revision TEXT NOT NULL CHECK(length(planner_revision) BETWEEN 1 AND 128),
        manifest_json JSONB NOT NULL CHECK(jsonb_typeof(manifest_json)='object'
          AND octet_length(manifest_json::text)<=8388608),
        graph_hashes TEXT[] NOT NULL CHECK(cardinality(graph_hashes)<=16384),
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(terms_edition_id,input_digest,planner_revision)
      );
      CREATE TABLE terms_semantic_processing_outputs(
        run_id UUID NOT NULL REFERENCES terms_semantic_processing_runs(id) ON DELETE RESTRICT,
        graph_ordinal INTEGER NOT NULL CHECK(graph_ordinal>=0),
        publication_id UUID NOT NULL REFERENCES terms_semantic_publications(id) ON DELETE RESTRICT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(run_id,graph_ordinal)
      );
      CREATE TABLE terms_semantic_processing_completions(
        run_id UUID PRIMARY KEY REFERENCES terms_semantic_processing_runs(id) ON DELETE RESTRICT,
        outcome TEXT NOT NULL CHECK(outcome IN ('COMPLETE','PARTIAL','UNRESOLVED')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
      );
      CREATE TABLE terms_semantic_processing_failures(
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        household_space_id UUID NOT NULL REFERENCES household_spaces(id) ON DELETE RESTRICT,
        terms_edition_id UUID NOT NULL REFERENCES terms_editions(id) ON DELETE RESTRICT,
        input_context JSONB NOT NULL,
        input_digest TEXT NOT NULL CHECK(input_digest ~ '^[0-9a-f]{64}$'),
        planner_revision TEXT NOT NULL CHECK(length(planner_revision) BETWEEN 1 AND 128),
        error_code TEXT NOT NULL DEFAULT 'TERMS_SEMANTIC_PROCESSING_FAILED'
          CHECK(error_code='TERMS_SEMANTIC_PROCESSING_FAILED'),
        retry_after TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
      );
      CREATE INDEX terms_semantic_processing_retry ON terms_semantic_processing_failures
        (terms_edition_id,input_digest,planner_revision,retry_after DESC);
      CREATE FUNCTION protect_terms_semantic_processing() RETURNS TRIGGER LANGUAGE plpgsql AS $$
      DECLARE parent terms_semantic_processing_runs; candidate terms_semantic_candidates;
      BEGIN
        IF TG_OP<>'INSERT' THEN
          RAISE EXCEPTION 'semantic processing history is immutable' USING ERRCODE='23514';
        END IF;
        IF TG_TABLE_NAME IN (
            'terms_semantic_processing_runs','terms_semantic_processing_failures') THEN
          IF NEW.input_context IS DISTINCT FROM terms_semantic_input_context(
              NEW.terms_edition_id,NEW.household_space_id)
            OR NEW.input_digest<>
              encode(sha256(convert_to(NEW.input_context::text,'UTF8')),'hex') THEN
            RAISE EXCEPTION 'semantic processing source changed' USING ERRCODE='23514';
          END IF;
          IF TG_TABLE_NAME='terms_semantic_processing_runs' THEN
            IF EXISTS(SELECT 1 FROM unnest(NEW.graph_hashes) h
                WHERE h IS NULL OR h !~ '^[0-9a-f]{64}$') THEN
              RAISE EXCEPTION 'semantic processing graph hash invalid' USING ERRCODE='23514';
            END IF;
          END IF;
        ELSE
          SELECT * INTO parent FROM terms_semantic_processing_runs WHERE id=NEW.run_id;
          IF NOT FOUND OR parent.input_context IS DISTINCT FROM terms_semantic_input_context(
              parent.terms_edition_id,parent.household_space_id) THEN
            RAISE EXCEPTION 'semantic processing source changed' USING ERRCODE='23514';
          END IF;
          IF TG_TABLE_NAME='terms_semantic_processing_outputs' THEN
            SELECT c.* INTO candidate FROM terms_semantic_publications p
              JOIN terms_semantic_candidates c ON c.id=p.candidate_id WHERE p.id=NEW.publication_id;
            IF NOT FOUND OR candidate.household_space_id<>parent.household_space_id
              OR candidate.terms_edition_id<>parent.terms_edition_id
              OR candidate.input_digest<>parent.input_digest
              OR NEW.graph_ordinal>=cardinality(parent.graph_hashes)
              OR candidate.graph_sha256<>parent.graph_hashes[NEW.graph_ordinal+1] THEN
              RAISE EXCEPTION 'semantic processing output mismatch' USING ERRCODE='23514';
            END IF;
          ELSIF (SELECT count(*) FROM terms_semantic_processing_outputs
              WHERE run_id=NEW.run_id)<>cardinality(parent.graph_hashes) THEN
            RAISE EXCEPTION 'semantic processing outputs incomplete' USING ERRCODE='23514';
          END IF;
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER semantic_processing_run_guard BEFORE INSERT OR UPDATE OR DELETE
        ON terms_semantic_processing_runs FOR EACH ROW
        EXECUTE FUNCTION protect_terms_semantic_processing();
      CREATE TRIGGER semantic_processing_output_guard BEFORE INSERT OR UPDATE OR DELETE
        ON terms_semantic_processing_outputs FOR EACH ROW
        EXECUTE FUNCTION protect_terms_semantic_processing();
      CREATE TRIGGER semantic_processing_completion_guard BEFORE INSERT OR UPDATE OR DELETE
        ON terms_semantic_processing_completions FOR EACH ROW
        EXECUTE FUNCTION protect_terms_semantic_processing();
      CREATE TRIGGER semantic_processing_failure_guard BEFORE INSERT OR UPDATE OR DELETE
        ON terms_semantic_processing_failures FOR EACH ROW
        EXECUTE FUNCTION protect_terms_semantic_processing();
      COMMENT ON TABLE terms_semantic_processing_completions IS
        'Processing audit, never execution authority; readers replay original sources';
    """)


def downgrade() -> None:
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM terms_semantic_processing_runs)
          OR EXISTS(SELECT 1 FROM terms_semantic_processing_failures) THEN
          RAISE EXCEPTION 'semantic processing history prevents downgrade' USING ERRCODE='23514';
        END IF;
        IF EXISTS(SELECT 1 FROM terms_semantic_publications
            GROUP BY candidate_id,verifier_revision,compiler_revision HAVING count(*)>1) THEN
          RAISE EXCEPTION 'semantic source proofs prevent downgrade' USING ERRCODE='23514';
        END IF;
      END $$;
      DROP TABLE terms_semantic_processing_completions;
      DROP TABLE terms_semantic_processing_failures;
      DROP TABLE terms_semantic_processing_outputs;
      DROP TABLE terms_semantic_processing_runs;
      DROP FUNCTION protect_terms_semantic_processing();
      ALTER TABLE terms_semantic_publications DROP CONSTRAINT uq_terms_semantic_source_proof;
      ALTER TABLE terms_semantic_publications ADD CONSTRAINT
        terms_semantic_publications_candidate_id_verifier_revision__key
        UNIQUE(candidate_id,verifier_revision,compiler_revision);
    """)
