"""Finite local statement interpretation with original character offsets.

This is an input helper, not a clinical classifier or an enrollment authority.
The caller merges explicit structured facts and binds activity-scoped observations
to its event. In particular, a negated surgery does not negate other performed
treatment. Billed/estimated costs are never eligible expenses or benefit amounts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

INTERPRETATION_REVISION = "local-situation-v1"
MAX_SITUATION_CHARS = 2000
_MAX_SUBJECT_TERMS = 32
_MAX_CLAUSES = 128
Activity = Literal["admission", "outpatient", "surgery"]
FactState = Literal["CONFIRMED", "SCENARIO", "UNKNOWN"]
ScopeState = Literal["AFFIRMED", "NEGATED", "PLANNED", "UNKNOWN"]
FactValue = bool | int | str | Decimal | date | None


class InterpretationError(ValueError):
    def __init__(self) -> None:
        super().__init__("LOCAL_INTERPRETATION_INPUT_INVALID")


@dataclass(frozen=True, slots=True, repr=False)
class SourceSpan:
    start: int
    end: int


@dataclass(frozen=True, slots=True, repr=False)
class MatchScope:
    state: ScopeState
    reason_codes: tuple[str, ...]
    span: SourceSpan


@dataclass(frozen=True, slots=True, repr=False)
class InterpretedFact:
    field_path: str
    value: FactValue
    state: FactState
    spans: tuple[SourceSpan, ...]
    reason_codes: tuple[str, ...]
    activity: Activity | None = None
    currency: str | None = None
    provenance: Literal["EXPLICIT_LOCAL"] = "EXPLICIT_LOCAL"


@dataclass(frozen=True, slots=True, repr=False)
class InterpretationIssue:
    span: SourceSpan
    reason_codes: tuple[str, ...]
    field_path: str | None = None


@dataclass(frozen=True, slots=True, repr=False)
class SituationInterpretation:
    facts: tuple[InterpretedFact, ...]
    issues: tuple[InterpretationIssue, ...]
    revision: Literal["local-situation-v1"] = "local-situation-v1"


@dataclass(frozen=True, slots=True, repr=False)
class _Clause:
    span: SourceSpan
    reasons: tuple[str, ...]
    dates: tuple[tuple[SourceSpan, date], ...]
    date_role: Literal["event", "visit"] | None = None


_BREAK = re.compile(
    r"[;!?。！？\n]+|(?<!\d)[.,]|[.,](?!\d)|"
    r"(?:않은\s+(?:상태로|후에?|뒤에?|채로)|없이|했(?:으며|고)|하였고|았고|었고|않고|지만|으나)(?=\s|$)|"
    r"\b(?:but|whereas|however|and)\b",
    re.IGNORECASE,
)
_ACTIVITIES: tuple[tuple[Activity, str, re.Pattern[str]], ...] = (
    (
        "admission",
        "MedicalEvent.admission",
        re.compile(
            r"(?<![가-힣A-Za-z])입원|\b(?:admission|admitted|hospitali[sz]ed|hospitali[sz]ation|inpatient)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "outpatient",
        "MedicalEvent.outpatient",
        re.compile(
            r"(?<![가-힣A-Za-z])(?:외래|통원)|\boutpatient\b",
            re.IGNORECASE,
        ),
    ),
    (
        "surgery",
        "MedicalEvent.performed",
        re.compile(
            r"(?<![가-힣A-Za-z])수술|\bsurgery\b|\boperation\b",
            re.IGNORECASE,
        ),
    ),
)
_NEGATED = re.compile(
    r"않|안(?=\s*(?:했|하|받|할))|(?<![가-힣])안\s|없|아니|아닙|아님|미(?:시행|실시)|"
    r"\b(?:no|not|never|without|denied)\b|\b(?:did|was|were|has|have|had|is|are|do|does)n['’]t\b",
    re.IGNORECASE,
)
_PLANNED = re.compile(
    r"예정|계획|예약|내일|다음\s*(?:주|달)|"
    r"\b(?:planned|planning|scheduled|will|tomorrow|next\s+(?:week|month)|going\s+to)\b",
    re.IGNORECASE,
)
_UNCERTAIN = re.compile(
    r"모르|몰라|여부|인지|불확실|의심|가능성|만약|가정|(?:필요|권유)|"
    r"(?:했|하였|받았)(?:으면|나요|습니까|는지|을까)|"
    r"\b(?:unknown|uncertain|whether|maybe|might|if|need|needs|needed|recommend|recommended)\b|"
    r"\bnot\s+(?:sure|only)\b",
    re.IGNORECASE,
)
_DOCUMENT = re.compile(
    r"보험|가입금액|보장|약관|\b(?:coverage|benefit|policy|premium|insured)\b",
    re.IGNORECASE,
)
_PAST = re.compile(
    r"작년|지난해|지난달|과거|예전에|\b(?:last\s+(?:year|month)|previously|history\s+of)\b",
    re.IGNORECASE,
)
_CURRENT = re.compile(r"이번|현재|지금|오늘|\b(?:now|today|this\s+time)\b", re.IGNORECASE)
_RELATIVE_DATE = re.compile(
    r"오늘|어제|내일|작년|지난해|지난달|다음\s*(?:주|달)|"
    r"\b(?:today|yesterday|tomorrow|last\s+(?:year|month)|next\s+(?:week|month))\b",
    re.IGNORECASE,
)
_FAMILY_ROLE = re.compile(
    r"(?<![가-힣A-Za-z])(?:어머니|아버지|엄마|아빠|부모|형|누나|언니|동생|배우자|남편|아내|아들|딸|자녀|친구)|"
    r"\b(?:mother|father|brother|sister|wife|husband|spouse|son|daughter|friend|family\s+member)\b",
    re.IGNORECASE,
)
_FULL_DATE = re.compile(
    r"(?<!\d)(?:(?P<iy>\d{4})-(?P<im>\d{1,2})-(?P<id>\d{1,2})|"
    r"(?P<ky>\d{4})년\s*(?P<km>\d{1,2})월\s*(?P<kd>\d{1,2})일)(?!\d)"
)
_VISIT_DATE_ROLE = re.compile(r"진료일|방문일|\bvisit\s+date\b", re.IGNORECASE)
_EVENT_DATE_ROLE = re.compile(r"사건일|\bevent\s+date\b", re.IGNORECASE)
_AMBIGUOUS_DATE = re.compile(
    r"(?<!\d)\d{1,4}/\d{1,2}(?:/\d{1,4})?(?!\d)|"
    r"(?<!\d)\d{4}\.\d{1,2}\.\d{1,2}(?!\d)|"
    r"(?<!\d)\d{1,2}월\s*\d{1,2}일"
)
_DISCUSSION = re.compile(
    r"상담|설명|문의|안내|교육|검사|검진|"
    r"\b(?:consultation|discussion|information|counseling|testing|screening|test)\b",
    re.IGNORECASE,
)
_PERFORMED: dict[Activity, re.Pattern[str]] = {
    "admission": re.compile(
        r"입원(?:을|은)?\s*(?:했|하였|중|하고\s*있|해\s*있)|"
        r"입원\s*(?:기간|일수)(?:은|는|:)?\s*\d+\s*일(?:입니다|이었습니다|이었다)|"
        r"\b(?:admitted|hospitali[sz]ed)\b",
        re.IGNORECASE,
    ),
    "outpatient": re.compile(
        r"(?:통원|외래)(?:\s*(?:진료|치료))?(?:를|을|는|은)?\s*(?:받았|받고|했|하였|다녀왔|방문했)|"
        r"\b(?:had|received|completed|underwent)\s+(?:an?\s+)?outpatient\s+(?:treatment|care|visit)\b|"
        r"\boutpatient\s+(?:treatment|care|visit)\s+(?:was\s+)?(?:performed|completed)\b",
        re.IGNORECASE,
    ),
    "surgery": re.compile(
        r"수술(?:을|은|를)?\s*(?:(?:무사히|성공적으로)\s*)?(?:받았|받고|했|하였|진행했|시행했|완료했|끝났|마쳤)|"
        r"\b(?:had|underwent|received)\s+(?:an?\s+)?(?:surgery|operation)\b|"
        r"\b(?:surgery|operation)\s+(?:was\s+)?(?:performed|completed)\b",
        re.IGNORECASE,
    ),
}
_DURATION = re.compile(
    r"입원\s*(?:기간|일수)(?:은|는|:)?\s*(?P<label>\d+)\s*일|"
    r"(?<![\w.+-])(?P<before>\d+)\s*일(?:간|동안)?\s*입원|"
    r"\b(?:admitted|hospitali[sz]ed)\s+for\s+(?P<english>\d+)\s+days?\b",
    re.IGNORECASE,
)
_COST_ROLE = re.compile(
    r"진료비|치료비|의료비|병원비|\b(?:medical\s+(?:cost|bill)|hospital\s+bill|billed\s+amount)\b",
    re.IGNORECASE,
)
_BILLED = re.compile(
    r"실제|결제|지불|납부|영수증|청구된|\b(?:billed|paid|charged|actual)\b", re.IGNORECASE
)
_ESTIMATED = re.compile(r"예상|추정|견적|\b(?:estimated|estimate|expected)\b", re.IGNORECASE)
_APPROXIMATE = re.compile(
    r"대략|정도|(?<![가-힣])약\s|\b(?:about|approximately|roughly)\b", re.IGNORECASE
)
_NUMBER = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
_MONEY = re.compile(
    rf"(?<![\w.,+-])(?P<prefix>KRW|USD|EUR|GBP|₩)\s*(?P<pnum>{_NUMBER})(?!\w|[.,]\d)|"
    rf"(?<![\w.,+-])(?P<snum>{_NUMBER})\s*(?P<suffix>만\s*원|원|KRW|USD|EUR|GBP)(?![A-Za-z])",
    re.IGNORECASE,
)
_NUMERIC_RANGE = re.compile(
    r"\d\s*(?:만\s*원|원|일|KRW|USD|EUR|GBP)?\s*(?:[-~]|to|부터|에서)\s*"
    r"(?:KRW|USD|EUR|GBP)?\s*\d",
    re.IGNORECASE,
)


def _validate(
    text: str,
    selected: tuple[str, ...],
    others: tuple[str, ...],
    event_date: date | None,
) -> None:
    if type(text) is not str or not 1 <= len(text) <= MAX_SITUATION_CHARS or not text.strip():
        raise InterpretationError
    if event_date is not None and type(event_date) is not date:
        raise InterpretationError
    if any(
        not isinstance(terms, tuple)
        or len(terms) > _MAX_SUBJECT_TERMS
        or any(
            type(term) is not str or not 1 <= len(term) <= 160 or not term.strip() for term in terms
        )
        for terms in (selected, others)
    ):
        raise InterpretationError


def _term_spans(text: str, terms: tuple[str, ...]) -> tuple[SourceSpan, ...]:
    return tuple(
        SourceSpan(match.start(), match.end())
        for term in terms
        for match in re.finditer(
            r"(?<![\w])" + re.escape(term) + r"(?=$|[^\w]|(?:은|는|이|가|께서|에게|의|도)(?=\s|$))",
            text,
            re.IGNORECASE,
        )
    )


def _masked_subjects(text: str, terms: tuple[str, ...]) -> str:
    characters = list(text)
    for span in _term_spans(text, terms):
        characters[span.start : span.end] = " " * (span.end - span.start)
    return "".join(characters)


def _dates(text: str, start: int = 0) -> tuple[tuple[SourceSpan, date | None], ...]:
    result: list[tuple[SourceSpan, date | None]] = []
    for match in _FULL_DATE.finditer(text):
        try:
            value = date(
                int(match["iy"] or match["ky"]),
                int(match["im"] or match["km"]),
                int(match["id"] or match["kd"]),
            )
        except ValueError:
            value = None
        result.append((SourceSpan(start + match.start(), start + match.end()), value))
    return tuple(result)


def _clauses(
    text: str,
    selected: tuple[str, ...],
    others: tuple[str, ...],
    event_date: date | None,
) -> tuple[_Clause, ...]:
    lexical = _masked_subjects(text, (*selected, *others))
    spans = []
    start = 0
    for match in _BREAK.finditer(lexical):
        spans.append(SourceSpan(start, match.end()))
        start = match.end()
    if start < len(text):
        spans.append(SourceSpan(start, len(text)))
    if len(spans) > _MAX_CLAUSES:
        raise InterpretationError
    all_dates = {
        value
        for span in spans
        if not _VISIT_DATE_ROLE.search(lexical[span.start : span.end])
        for _, value in _dates(lexical[span.start : span.end])
        if value is not None
    }
    subject_reason: str | None = None
    prior_date: date | None = None
    old_event = False
    result = []
    for span in spans:
        body = text[span.start : span.end]
        selected_spans, other_spans = _term_spans(body, selected), _term_spans(body, others)
        role_other = any(
            not any(s.start <= match.start() and match.end() <= s.end for s in selected_spans)
            for match in _FAMILY_ROLE.finditer(body)
        )
        if selected_spans and (other_spans or role_other):
            subject_reason = "LOCAL_SUBJECT_UNRESOLVED"
        elif selected_spans:
            subject_reason = None
        elif other_spans or role_other:
            subject_reason = "LOCAL_OTHER_SUBJECT" if other_spans else "LOCAL_SUBJECT_UNRESOLVED"
        body = lexical[span.start : span.end]
        is_visit = bool(_VISIT_DATE_ROLE.search(body))
        is_event = bool(_EVENT_DATE_ROLE.search(body))
        reasons = [subject_reason] if subject_reason else []
        dates = _dates(body, span.start)
        valid_dates = tuple((location, value) for location, value in dates if value is not None)
        ambiguous = any(
            not any(
                location.start <= span.start + match.start()
                and span.start + match.end() <= location.end
                for location, _ in dates
            )
            for match in _AMBIGUOUS_DATE.finditer(body)
        )
        if ambiguous or any(value is None for _, value in dates) or (is_visit and is_event):
            reasons.append("LOCAL_DATE_UNRESOLVED")
        if valid_dates and not is_visit:
            prior_date = valid_dates[-1][1]
        if _CURRENT.search(body):
            old_event = False
        if _PAST.search(body):
            old_event = bool(_CURRENT.search(lexical))
        if not is_visit and (
            old_event
            or (event_date is not None and prior_date is not None and prior_date != event_date)
            or (event_date is None and len(all_dates) > 1)
            or len({value for _, value in valid_dates}) > 1
        ):
            reasons.append("LOCAL_OTHER_EVENT_TIME")
        result.append(
            _Clause(
                span,
                tuple(dict.fromkeys(reasons)),
                valid_dates,
                "visit" if is_visit else "event" if is_event else None,
            )
        )
    return tuple(result)


def _guard(text: str, span: SourceSpan, clauses: tuple[_Clause, ...]) -> MatchScope:
    clause = next(
        (item for item in clauses if item.span.start <= span.start < span.end <= item.span.end),
        None,
    )
    if clause is None:
        return MatchScope("UNKNOWN", ("LOCAL_MATCH_SCOPE_UNRESOLVED",), span)
    if clause.reasons:
        return MatchScope("UNKNOWN", clause.reasons, span)
    body = text[clause.span.start : clause.span.end]
    after_end = min(
        (
            clause.span.start + match.start()
            for _, _, pattern in _ACTIVITIES
            for match in pattern.finditer(body)
            if clause.span.start + match.start() >= span.end
        ),
        default=clause.span.end,
    )
    after = text[span.end : after_end]
    before = text[clause.span.start : span.start]
    discussion = _DISCUSSION.search(after)
    if (
        discussion is not None
        and after[: discussion.start()].strip()
        in {"", "을", "를", "의", "에", "에 대한", "관련", "을 위한"}
    ) or re.search(
        r"\b(?:consultation|discussion|information)\s+(?:about|regarding)\s*$",
        before,
        re.IGNORECASE,
    ):
        return MatchScope("UNKNOWN", ("LOCAL_ACTIVITY_UNRESOLVED",), span)
    if _UNCERTAIN.search(body) or "?" in body or "？" in body:
        return MatchScope("UNKNOWN", ("LOCAL_STATEMENT_UNCERTAIN",), span)
    if _DOCUMENT.search(body):
        return MatchScope("UNKNOWN", ("LOCAL_DOCUMENT_CONTEXT",), span)
    if len(tuple(_NEGATED.finditer(body))) > 1:
        return MatchScope("UNKNOWN", ("LOCAL_STATEMENT_UNCERTAIN",), span)
    if _NEGATED.search(body) and _PLANNED.search(body):
        return MatchScope("UNKNOWN", ("LOCAL_ACTIVITY_UNRESOLVED",), span)
    if _NEGATED.search(body):
        return MatchScope("NEGATED", ("LOCAL_ACTIVITY_NEGATED",), span)
    if _PLANNED.search(body):
        return MatchScope("PLANNED", ("LOCAL_ACTIVITY_PLANNED",), span)
    return MatchScope("AFFIRMED", ("LOCAL_STATEMENT_AFFIRMED",), span)


def guard_normalizer_match(
    text: str,
    start: int,
    end: int,
    *,
    selected_subject_terms: tuple[str, ...] = (),
    other_subject_terms: tuple[str, ...] = (),
    event_date: date | None = None,
) -> MatchScope:
    """Gate an exact original-text match; only AFFIRMED may support a positive normalizer.

    A NEGATED match is not a negative clinical code. The caller must not infer a
    diagnosis or translate an unrecognized negative statement into a code.
    """
    _validate(text, selected_subject_terms, other_subject_terms, event_date)
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
        raise InterpretationError
    lexical = _masked_subjects(text, (*selected_subject_terms, *other_subject_terms))
    if lexical[start:end] != text[start:end]:
        return MatchScope("UNKNOWN", ("LOCAL_SUBJECT_REFERENCE",), SourceSpan(start, end))
    return _guard(
        lexical,
        SourceSpan(start, end),
        _clauses(text, selected_subject_terms, other_subject_terms, event_date),
    )


def _activity_facts(
    text: str, clause: _Clause, clauses: tuple[_Clause, ...]
) -> list[InterpretedFact]:
    body = text[clause.span.start : clause.span.end]
    result = []
    for activity, field_path, pattern in _ACTIVITIES:
        for match in pattern.finditer(body):
            span = SourceSpan(clause.span.start + match.start(), clause.span.start + match.end())
            scoped = _guard(text, span, clauses)
            value: bool | None = None
            state: FactState = "UNKNOWN"
            reasons = scoped.reason_codes
            if scoped.state == "NEGATED":
                value, state = False, "CONFIRMED"
            elif scoped.state == "PLANNED":
                state = "SCENARIO"
            elif scoped.state == "AFFIRMED" and any(
                asserted.start() <= match.start() and match.end() <= asserted.end()
                for asserted in _PERFORMED[activity].finditer(body)
            ):
                value, state, reasons = True, "CONFIRMED", ("LOCAL_ACTIVITY_PERFORMED",)
            elif scoped.state == "AFFIRMED":
                reasons = ("LOCAL_ACTIVITY_UNRESOLVED",)
            result.append(
                InterpretedFact(field_path, value, state, (clause.span,), reasons, activity)
            )
            if activity == "surgery" and value is True:
                result.append(
                    InterpretedFact(
                        "MedicalEvent.treatment_kind",
                        "surgery",
                        state,
                        (clause.span,),
                        reasons,
                        activity,
                    )
                )
    for match in _DURATION.finditer(body):
        span = SourceSpan(clause.span.start + match.start(), clause.span.start + match.end())
        scoped = _guard(text, span, clauses)
        days = int(match["label"] or match["before"] or match["english"])
        admission = next(
            (item for item in result if item.field_path == "MedicalEvent.admission"), None
        )
        valid = 0 <= days <= 36500 and not any(
            interval.start() < match.end() and match.start() < interval.end()
            for interval in _NUMERIC_RANGE.finditer(body)
        )
        state = (
            "CONFIRMED"
            if scoped.state == "AFFIRMED"
            and valid
            and admission is not None
            and admission.value is True
            else "SCENARIO"
            if scoped.state == "PLANNED" and valid
            else "UNKNOWN"
        )
        result.append(
            InterpretedFact(
                "MedicalEvent.admission_days",
                days if state != "UNKNOWN" else None,
                state,
                (span,),
                ("LOCAL_ADMISSION_DAYS",)
                if state == "CONFIRMED"
                else (
                    scoped.reason_codes
                    if scoped.state != "AFFIRMED"
                    else ("LOCAL_DURATION_UNRESOLVED",)
                ),
                "admission",
            )
        )
    return result


def _cost_fact(text: str, clause: _Clause, clauses: tuple[_Clause, ...]) -> InterpretedFact | None:
    body = text[clause.span.start : clause.span.end]
    if not _COST_ROLE.search(body) or _DOCUMENT.search(body):
        return None
    estimated = bool(_ESTIMATED.search(body))
    if not estimated and not _BILLED.search(body):
        return None
    path = "Receipt.estimated_amount" if estimated else "Receipt.billed_amount"
    matches = tuple(_MONEY.finditer(body))
    scoped = _guard(text, clause.span, clauses)
    if (
        len(matches) != 1
        or any(
            interval.start() < amount.end() and amount.start() < interval.end()
            for amount in matches
            for interval in _NUMERIC_RANGE.finditer(body)
        )
        or scoped.state in ("UNKNOWN", "NEGATED")
        or (not estimated and scoped.state == "PLANNED")
        or _APPROXIMATE.search(body)
    ):
        return InterpretedFact(
            path,
            None,
            "UNKNOWN",
            (clause.span,),
            scoped.reason_codes
            if scoped.state in ("UNKNOWN", "NEGATED")
            else ("LOCAL_COST_UNRESOLVED",),
        )
    match = matches[0]
    raw = (match["pnum"] or match["snum"]).replace(",", "")
    currency = (match["prefix"] or match["suffix"]).upper().replace(" ", "")
    if len(raw) > 16:
        return InterpretedFact(path, None, "UNKNOWN", (clause.span,), ("LOCAL_COST_UNRESOLVED",))
    amount = Decimal(raw) * (10000 if currency == "만원" else 1)
    if amount > Decimal("999999999999"):
        return InterpretedFact(path, None, "UNKNOWN", (clause.span,), ("LOCAL_COST_UNRESOLVED",))
    if currency in ("원", "만원", "₩"):
        currency = "KRW"
    return InterpretedFact(
        path,
        amount,
        "SCENARIO" if estimated else "CONFIRMED",
        (clause.span,),
        ("LOCAL_ESTIMATED_COST",) if estimated else ("LOCAL_BILLED_COST",),
        currency=currency,
    )


def _merge(facts: list[InterpretedFact]) -> tuple[InterpretedFact, ...]:
    grouped: dict[tuple[str, Activity | None], list[InterpretedFact]] = {}
    for item in facts:
        grouped.setdefault((item.field_path, item.activity), []).append(item)
    result = []
    excluded = {"LOCAL_OTHER_SUBJECT", "LOCAL_SUBJECT_UNRESOLVED", "LOCAL_OTHER_EVENT_TIME"}
    for items in grouped.values():
        relevant = [
            item
            for item in items
            if not (item.state == "UNKNOWN" and set(item.reason_codes) <= excluded)
        ] or items
        first = relevant[0]
        spans = tuple(dict.fromkeys(span for item in relevant for span in item.spans))
        reasons = tuple(dict.fromkeys(reason for item in relevant for reason in item.reason_codes))
        if any(
            (item.value, item.state, item.currency)
            != (
                first.value,
                first.state,
                first.currency,
            )
            for item in relevant
        ):
            first = InterpretedFact(
                first.field_path,
                None,
                "UNKNOWN",
                spans,
                ("LOCAL_FACT_CONFLICT",),
                first.activity,
            )
        else:
            first = InterpretedFact(
                first.field_path,
                first.value,
                first.state,
                spans,
                reasons,
                first.activity,
                first.currency,
            )
        result.append(first)
    return tuple(result)


def interpret_situation(
    text: str,
    *,
    selected_subject_terms: tuple[str, ...] = (),
    other_subject_terms: tuple[str, ...] = (),
    event_date: date | None = None,
) -> SituationInterpretation:
    """Observe supported explicit facts; never use the clock or invent missing facts.

    Offsets address the unchanged input using Python character indices. The return
    value contains no original text or names. Activity must be retained when using
    MedicalEvent.performed; it currently describes surgery only, not all care.
    """
    _validate(text, selected_subject_terms, other_subject_terms, event_date)
    clauses = _clauses(text, selected_subject_terms, other_subject_terms, event_date)
    lexical = _masked_subjects(text, (*selected_subject_terms, *other_subject_terms))
    facts = []
    issues = []
    for clause in clauses:
        facts.extend(_activity_facts(lexical, clause, clauses))
        cost = _cost_fact(lexical, clause, clauses)
        if cost is not None:
            facts.append(cost)
        body = lexical[clause.span.start : clause.span.end]
        for span, value in clause.dates:
            path = (
                "MedicalEvent.visit_date"
                if clause.date_role == "visit"
                else "MedicalEvent.event_date"
            )
            reasons = clause.reasons
            if clause.date_role != "visit" and event_date is not None and value != event_date:
                issues.append(
                    InterpretationIssue(
                        span, ("LOCAL_OTHER_EVENT_TIME",), "MedicalEvent.event_date"
                    )
                )
                continue
            if clause.date_role is None and not any(
                pattern.search(body) for _, _, pattern in _ACTIVITIES
            ):
                continue
            scoped = _guard(lexical, span, clauses)
            state: FactState = (
                "UNKNOWN"
                if reasons or scoped.state == "UNKNOWN"
                else "SCENARIO"
                if scoped.state == "PLANNED"
                else "CONFIRMED"
            )
            facts.append(
                InterpretedFact(
                    path,
                    value if state != "UNKNOWN" else None,
                    state,
                    (span,),
                    reasons
                    or (scoped.reason_codes if state == "UNKNOWN" else ("LOCAL_EXPLICIT_DATE",)),
                )
            )
        if "LOCAL_DATE_UNRESOLVED" in clause.reasons or _RELATIVE_DATE.search(body):
            issues.append(
                InterpretationIssue(
                    clause.span, ("LOCAL_DATE_UNRESOLVED",), "MedicalEvent.event_date"
                )
            )
    for item in facts:
        if item.state == "UNKNOWN":
            issues.extend(
                InterpretationIssue(span, item.reason_codes, item.field_path) for span in item.spans
            )
    if not facts:
        issues.append(
            InterpretationIssue(SourceSpan(0, len(text)), ("LOCAL_SITUATION_UNRESOLVED",))
        )
    return SituationInterpretation(_merge(facts), tuple(dict.fromkeys(issues)))
