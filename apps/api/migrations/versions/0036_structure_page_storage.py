"""Retain large document structures as immutable lossless page payloads."""

from collections.abc import Sequence

from alembic import op

revision: str = "0036_structure_page_storage"
down_revision: str | Sequence[str] | None = "0035_user_identity_proof"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE document_structure_page_payloads (
          generation_id uuid NOT NULL REFERENCES document_structure_generations(id)
            ON DELETE RESTRICT,
          part_number integer NOT NULL CHECK (part_number BETWEEN 0 AND 500),
          payload_json json NOT NULL CHECK (json_typeof(payload_json)='object' AND
            octet_length(payload_json::text) <= 67108864),
          node_ids text[] NOT NULL DEFAULT '{}',
          PRIMARY KEY (generation_id,part_number)
        );
        CREATE INDEX ix_structure_page_node_ids ON document_structure_page_payloads
          USING gin(node_ids);

        CREATE FUNCTION protect_structure_page_payload() RETURNS trigger
          LANGUAGE plpgsql AS $$
        DECLARE source jsonb;
        BEGIN
          IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'document structure pages are immutable' USING ERRCODE='23514';
          END IF;
          SELECT structure_json INTO source FROM document_structure_generations
            WHERE id=NEW.generation_id;
          IF (source->>'storage_layout'='page-v1' AND
              NEW.part_number <= (source->>'stored_page_count')::integer) IS NOT TRUE THEN
            RAISE EXCEPTION 'document structure page scope invalid' USING ERRCODE='23514';
          END IF;
          IF NEW.part_number=0 THEN
            IF NEW.payload_json::jsonb IS DISTINCT FROM source OR cardinality(NEW.node_ids)<>0 THEN
              RAISE EXCEPTION 'document structure header mismatch' USING ERRCODE='23514';
            END IF;
          ELSE
            IF (NEW.payload_json->'page'->>'page_number'=NEW.part_number::text AND
                json_typeof(NEW.payload_json->'nodes')='array' AND
                json_typeof(NEW.payload_json->'node_positions')='array' AND
                json_array_length(NEW.payload_json->'node_positions')=
                  json_array_length(NEW.payload_json->'nodes')) IS NOT TRUE OR
              NEW.node_ids IS DISTINCT FROM ARRAY(
                SELECT node->>'node_id' FROM json_array_elements(NEW.payload_json->'nodes')
                  WITH ORDINALITY AS n(node,position) ORDER BY position
              ) OR EXISTS (
                SELECT 1 FROM json_array_elements(NEW.payload_json->'nodes') node
                WHERE (json_typeof(node->'page_number')='number' AND
                  node->>'page_number'=NEW.part_number::text AND
                  node->>'node_id' ~ '^[0-9a-f]{64}$') IS NOT TRUE
              ) OR cardinality(NEW.node_ids)<>(SELECT count(DISTINCT id)
                FROM unnest(NEW.node_ids) id) THEN
              RAISE EXCEPTION 'document structure page nodes invalid' USING ERRCODE='23514';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_structure_page_payload BEFORE INSERT OR UPDATE OR DELETE
          ON document_structure_page_payloads FOR EACH ROW
          EXECUTE FUNCTION protect_structure_page_payload();

        CREATE FUNCTION validate_structure_page_manifest(requested_generation uuid)
          RETURNS void LANGUAGE plpgsql AS $$
        DECLARE expected integer; source jsonb;
        BEGIN
          SELECT structure_json INTO source FROM document_structure_generations
            WHERE id=requested_generation;
          IF source->>'storage_layout' IS DISTINCT FROM 'page-v1' THEN
            RETURN;
          END IF;
          expected := (source->>'stored_page_count')::integer;
          IF expected IS NULL OR expected NOT BETWEEN 1 AND 500 OR
            (SELECT count(*) FROM document_structure_page_payloads
              WHERE generation_id=requested_generation) <> expected+1 OR
            (SELECT sum(octet_length(payload_json::text)) FROM document_structure_page_payloads
              WHERE generation_id=requested_generation) > 536870912 THEN
            RAISE EXCEPTION 'document structure page manifest incomplete' USING ERRCODE='23514';
          END IF;
        END $$;
        CREATE FUNCTION require_complete_structure_pages() RETURNS trigger
          LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM validate_structure_page_manifest(NEW.id);
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_complete_structure_pages
          AFTER INSERT ON document_structure_generations DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION require_complete_structure_pages();

        CREATE FUNCTION document_structure_nodes_for_pages(
          requested_generation uuid, requested_household uuid, requested_pages integer[])
          RETURNS TABLE(node jsonb, source_position bigint) LANGUAGE sql STABLE AS $$
          SELECT n.node,n.position-1 FROM document_structure_generations g
          CROSS JOIN LATERAL jsonb_array_elements(g.structure_json->'nodes')
            WITH ORDINALITY AS n(node,position)
          WHERE g.id=requested_generation AND g.household_space_id=requested_household
            AND g.structure_json->>'storage_layout' IS DISTINCT FROM 'page-v1'
            AND n.node->>'page_number'=ANY(ARRAY(SELECT p::text FROM unnest(requested_pages) p))
          UNION ALL
          SELECT n.node::jsonb,n.node_position::bigint
          FROM document_structure_generations g
          JOIN document_structure_page_payloads p ON p.generation_id=g.id
            AND p.part_number=ANY(requested_pages) AND p.part_number>0
          CROSS JOIN LATERAL ROWS FROM (
            json_array_elements(p.payload_json->'nodes'),
            json_array_elements_text(p.payload_json->'node_positions')
          ) AS n(node,node_position)
          WHERE g.id=requested_generation AND g.household_space_id=requested_household
            AND g.structure_json->>'storage_layout'='page-v1'
        $$;

        CREATE FUNCTION document_structure_nodes_by_ids(
          requested_generation uuid, requested_household uuid, requested_ids text[])
          RETURNS TABLE(node jsonb, source_position bigint) LANGUAGE sql STABLE AS $$
          SELECT n.node,n.position-1 FROM document_structure_generations g
          CROSS JOIN LATERAL jsonb_array_elements(g.structure_json->'nodes')
            WITH ORDINALITY AS n(node,position)
          WHERE g.id=requested_generation AND g.household_space_id=requested_household
            AND g.structure_json->>'storage_layout' IS DISTINCT FROM 'page-v1'
            AND n.node->>'node_id'=ANY(requested_ids)
          UNION ALL
          SELECT n.node::jsonb,n.node_position::bigint
          FROM document_structure_generations g
          JOIN document_structure_page_payloads p ON p.generation_id=g.id
            AND p.node_ids && requested_ids AND p.part_number>0
          CROSS JOIN LATERAL ROWS FROM (
            json_array_elements(p.payload_json->'nodes'),
            json_array_elements_text(p.payload_json->'node_positions')
          ) AS n(node,node_position)
          WHERE g.id=requested_generation AND g.household_space_id=requested_household
            AND g.structure_json->>'storage_layout'='page-v1'
            AND n.node->>'node_id'=ANY(requested_ids)
        $$;

        CREATE FUNCTION document_structure_projection(
          requested_generation uuid, requested_household uuid, requested_pages integer[])
          RETURNS jsonb LANGUAGE sql STABLE AS $$
          WITH selected AS MATERIALIZED (
            SELECT * FROM document_structure_nodes_for_pages(
              requested_generation,requested_household,requested_pages)
          ), context_ids AS MATERIALIZED (
            SELECT DISTINCT jsonb_array_elements_text(
              COALESCE(node->'context_node_ids','[]'::jsonb)) id FROM selected
          ), nodes AS MATERIALIZED (
            SELECT * FROM selected UNION ALL
            SELECT * FROM document_structure_nodes_by_ids(requested_generation,requested_household,
              ARRAY(SELECT id FROM context_ids)) context
            WHERE context.node->>'node_id' NOT IN (SELECT node->>'node_id' FROM selected)
          ), budget AS MATERIALIZED (
            SELECT g.structure_json->'lineage' AS lineage,
              octet_length((g.structure_json->'lineage')::text) + 32 +
                COALESCE((SELECT sum(octet_length(node::text)+2) FROM nodes),0) <= 67108864
                AS fits
            FROM document_structure_generations g WHERE g.id=requested_generation
              AND g.household_space_id=requested_household
          )
          SELECT CASE WHEN fits THEN jsonb_build_object('lineage',lineage,
            'nodes',COALESCE((SELECT jsonb_agg(node ORDER BY source_position)
              FROM nodes),'[]'::jsonb))
            ELSE NULL END FROM budget
        $$;
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM document_structure_page_payloads) OR EXISTS (
            SELECT 1 FROM document_structure_generations
            WHERE structure_json->>'storage_layout'='page-v1'
          ) THEN
            RAISE EXCEPTION 'paged document structure history must be retained';
          END IF;
        END $$;
        DROP FUNCTION document_structure_projection(uuid,uuid,integer[]);
        DROP FUNCTION document_structure_nodes_by_ids(uuid,uuid,text[]);
        DROP FUNCTION document_structure_nodes_for_pages(uuid,uuid,integer[]);
        DROP TRIGGER trg_complete_structure_pages ON document_structure_generations;
        DROP FUNCTION require_complete_structure_pages();
        DROP FUNCTION validate_structure_page_manifest(uuid);
        DROP TABLE document_structure_page_payloads;
        DROP FUNCTION protect_structure_page_payload();
    """)
