"""The database contract shipped with the installed Worker distribution.

The Worker image does not contain Alembic or API checkout files. Its compiled
revision must agree with the API and migration head, enforced by repository tests.
"""

SUPPORTED_SCHEMA_REVISION = "0082_proven_draft_context"
SCHEMA_REVISION_QUERY = "SELECT version_num FROM public.alembic_version LIMIT 2"
REQUIRED_SCHEMA_QUERY = """
SELECT job.lease_token, job.source_digest, input.sources_json,
       context.scope_digest, proposal.proposal_json,
       policy.processing_mode, policy.resubmission_of_job_id, policy.source_generation_id,
       public.policy_structuring_source_current(policy.id),
       replay.source_response_hash, replay.normalized_batch_json,
       replay.normalization_revision, replay.origin
FROM public.guidance_review_jobs AS job
CROSS JOIN public.guidance_review_inputs AS input
CROSS JOIN public.guidance_review_contexts AS context
CROSS JOIN public.guidance_review_proposals AS proposal
CROSS JOIN public.policy_structuring_jobs AS policy
CROSS JOIN public.policy_range_replay_sources AS replay
WHERE false
"""
