"""Each source set remains represented in a combined decision revision."""

from dataclasses import replace
from uuid import UUID

from familycare_api.guidance.domain import GuidanceContext
from familycare_api.guidance.models import GuidanceVersions
from familycare_api.guidance.repository import combine_guidance_contexts


def test_operational_revision_is_retained_when_a_private_context_exists():
    private = GuidanceContext(
        household_space_id=UUID(int=1),
        family_member_id=UUID(int=2),
        coverages=(),
        versions=GuidanceVersions(status_digest="a" * 64),
    )
    operational = replace(private, versions=GuidanceVersions(status_digest="b" * 64))
    first = combine_guidance_contexts(operational, private)
    changed = replace(operational, versions=GuidanceVersions(status_digest="c" * 64))
    second = combine_guidance_contexts(changed, private)
    assert first.versions.status_digest != second.versions.status_digest
    assert combine_guidance_contexts(operational, private).versions == first.versions


def test_partial_private_source_does_not_hide_usable_operational_coverage():
    from familycare_api.guidance.private_adapter import adapt_private_guidance

    from apps.api.tests.test_guidance_canonical_identity import _identity
    from apps.api.tests.test_private_knowledge_engine import _context, _coverage

    complete = adapt_private_guidance(
        _context(replace(_coverage(1, "100"), canonical_identity=_identity()))
    )
    coverage = complete.coverages[0]
    operational = replace(
        complete, coverages=(replace(coverage, ref=coverage.canonical_identity.ref),)
    )
    private = replace(complete, coverages=(replace(coverage, rules=(), calculation=None),))
    result = combine_guidance_contexts(operational, private)
    assert len(result.coverages) == 1
    assert result.coverages[0].rules
    assert result.coverages[0].canonical_identity == coverage.canonical_identity
