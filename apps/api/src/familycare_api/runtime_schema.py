"""The database contract shipped with the installed API distribution.

This must be advanced deliberately with schema changes. The repository test checks
both distributions against Alembic's single head; runtime needs no checkout files.
"""

SUPPORTED_SCHEMA_REVISION = "0078_policy_currency_proof"
SCHEMA_REVISION_QUERY = "SELECT version_num FROM public.alembic_version LIMIT 2"
REQUIRED_SCHEMA_QUERY = """
SELECT snapshot.review_job_id, result.result_json,
       policy.processing_mode, policy.resubmission_of_job_id, policy.source_generation_id,
       public.policy_structuring_source_current(policy.id),
       replay.source_response_hash, replay.normalized_batch_json,
       replay.normalization_revision, replay.origin, publication.source_identity_json
FROM public.claim_case_snapshots AS snapshot
CROSS JOIN public.guidance_review_results AS result
CROSS JOIN public.policy_structuring_jobs AS policy
CROSS JOIN public.policy_range_replay_sources AS replay
CROSS JOIN public.range_enrollment_publications AS publication
WHERE false
"""
