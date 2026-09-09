"""The database contract shipped with the installed API distribution.

This must be advanced deliberately with schema changes. The repository test checks
both distributions against Alembic's single head; runtime needs no checkout files.
"""

SUPPORTED_SCHEMA_REVISION = "0064_metadata_proven_prefix"
SCHEMA_REVISION_QUERY = "SELECT version_num FROM public.alembic_version LIMIT 2"
REQUIRED_SCHEMA_QUERY = """
SELECT snapshot.review_job_id, result.result_json
FROM public.claim_case_snapshots AS snapshot
CROSS JOIN public.guidance_review_results AS result
WHERE false
"""
