"""Rules and the event-specific terms judgments used to read them."""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import overload
from uuid import UUID

from familycare_api.clauses.rules import CoverageRuleVersion
from familycare_api.clauses.terms_change_selection import TermsEventSelection, TermsStatus


@dataclass(frozen=True, slots=True, repr=False)
class RulesForEvent(Sequence[CoverageRuleVersion]):
    """Keep source uncertainty alongside rules, including a read with no rules.

    Repository adapters own source, scope and date verification. Empty terms
    metadata supports existing injected readers; real event readers supply a
    judgment for every returned rule when they retain a selection.
    """

    rules: tuple[CoverageRuleVersion, ...]
    terms_selections: tuple[TermsEventSelection, ...] = ()
    rule_terms_status: tuple[tuple[UUID, TermsStatus], ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.rules, tuple)
            or not isinstance(self.terms_selections, tuple)
            or not isinstance(self.rule_terms_status, tuple)
            or any(not isinstance(rule, CoverageRuleVersion) for rule in self.rules)
            or any(
                not isinstance(selection, TermsEventSelection)
                for selection in self.terms_selections
            )
            or any(
                not isinstance(item, tuple)
                or len(item) != 2
                or not isinstance(item[0], UUID)
                or item[1] not in ("MATCH", "NO_MATCH", "UNKNOWN")
                for item in self.rule_terms_status
            )
        ):
            raise ValueError("EVENT_RULE_TERMS_INPUT_INVALID")
        judgments = dict(self.rule_terms_status)
        if len(judgments) != len(self.rule_terms_status) or (
            self.terms_selections and any(rule.id not in judgments for rule in self.rules)
        ):
            raise ValueError("EVENT_RULE_TERMS_INPUT_INVALID")

    def __len__(self) -> int:
        return len(self.rules)

    def __iter__(self) -> Iterator[CoverageRuleVersion]:
        return iter(self.rules)

    @overload
    def __getitem__(self, index: int) -> CoverageRuleVersion: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[CoverageRuleVersion, ...]: ...

    def __getitem__(
        self, index: int | slice
    ) -> CoverageRuleVersion | tuple[CoverageRuleVersion, ...]:
        return self.rules[index]

    def status_for(self, rule_version_id: UUID) -> TermsStatus:
        return dict(self.rule_terms_status).get(rule_version_id, "MATCH")
