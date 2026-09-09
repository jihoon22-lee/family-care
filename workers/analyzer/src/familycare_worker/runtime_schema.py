"""The database contract shipped with the installed Worker distribution.

The Worker image does not contain Alembic or API checkout files. Its compiled
revision must agree with the API and migration head, enforced by repository tests.
"""

SUPPORTED_SCHEMA_REVISION = "0064_metadata_proven_prefix"
SCHEMA_REVISION_QUERY = "SELECT version_num FROM public.alembic_version LIMIT 2"
REQUIRED_SCHEMA_QUERY = """
SELECT job.lease_token, job.source_digest, input.sources_json,
       context.scope_digest, proposal.proposal_json
FROM public.guidance_review_jobs AS job
CROSS JOIN public.guidance_review_inputs AS input
CROSS JOIN public.guidance_review_contexts AS context
CROSS JOIN public.guidance_review_proposals AS proposal
WHERE false
"""
