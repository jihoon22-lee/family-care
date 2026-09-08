"""Read a bounded set of complete source statements, independently of AI fields.

This recognizer supplies semantic observations, never source or edition authority.
Unknown wording stays unresolved, including unparsed exceptions and rounding.
Full matches prevent a quoted number or a prefix from authorizing a different role.
"""

from __future__ import annotations

import re
from typing import Any

MEANING_REVISION = "terms-source-meaning-v1"
_NUMBER = r"(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,12})?"
_INTEGER = r"(?:0|[1-9][0-9]{0,4})"
_CURRENCY = r"[A-Z]{3}"
_ROUNDING = r"half up|half even|up|down"
_IDENTIFIER = r"[a-z0-9][a-z0-9._:-]{0,63}"


def _match(pattern: str, value: str) -> re.Match[str] | None:
    return re.fullmatch(pattern, value)


def _calculation(mode: str, currency: str, rounding: str, **values: str) -> dict[str, Any]:
    return {
        "kind": "calculation",
        "mode": mode,
        "currency": currency,
        "rounding": rounding.replace(" ", "_"),
        **values,
    }


def observe_statement(text: str) -> dict[str, Any] | None:
    """Return only the complete, explicitly supported meaning of one source line."""
    if (
        not isinstance(text, str)
        or not 1 <= len(text) <= 8192
        or any(character in text for character in "\n\r\x00")
    ):
        return None
    value = text.strip()
    match = _match(
        rf"For each payable admission day, pay the insured amount in ({_CURRENCY}); "
        rf"multiply first, then round the total ({_ROUNDING}) to whole currency units\.",
        value,
    )
    if match:
        return _calculation("daily", *match.groups(), basis="insured_amount_per_payable_day")
    match = _match(
        rf"지급 대상 입원일수와 보험가입금액을 곱하여 ({_CURRENCY})로 지급하며, "
        r"곱한 총액을 통화 정수 단위로 (반올림|올림|내림)합니다\.",
        value,
    )
    if match:
        return _calculation(
            "daily",
            match[1],
            {"반올림": "half_up", "올림": "up", "내림": "down"}[match[2]],
            basis="insured_amount_per_payable_day",
        )
    match = _match(
        rf"The benefit is ({_CURRENCY}) ({_NUMBER}), rounded ({_ROUNDING}) "
        r"to whole currency units\.",
        value,
    )
    if match:
        return _calculation("fixed", match[1], match[3], amount=match[2])
    match = _match(
        rf"Pay ({_NUMBER}) of the insured amount in ({_CURRENCY}), "
        rf"rounded ({_ROUNDING}) to whole currency units\.",
        value,
    )
    if match:
        return _calculation(
            "insured_ratio", match[2], match[3], basis="insured_amount", ratio=match[1]
        )
    match = _match(rf"Exclude the first ({_INTEGER}) admission days\.", value) or _match(
        rf"최초 입원 ({_INTEGER})일은 지급일수에서 제외합니다\.",
        value,
    )
    if match and int(match[1]) <= 36500:
        return {"kind": "footnote", "effect": "initial_excluded_days", "days": int(match[1])}
    match = _match(rf"The maximum is ({_INTEGER}) payable days\.", value) or _match(
        rf"지급일수는 최대 ({_INTEGER})일입니다\.",
        value,
    )
    if match:
        return {
            "kind": "limit",
            "measure": "payable_days",
            "value": match[1],
            "unit": "days",
            "currency": None,
        }
    match = _match(
        rf"Deduct ({_CURRENCY}) ({_NUMBER}) from the gross benefit before applying "
        r"the amount cap and rounding; floor at zero\.",
        value,
    )
    if match:
        return {
            "kind": "deductible",
            "amount": match[2],
            "currency": match[1],
            "stage": "before_amount_cap_and_rounding",
        }
    match = _match(rf"The maximum benefit is ({_CURRENCY}) ({_NUMBER})\.", value)
    if match:
        return {
            "kind": "limit",
            "measure": "maximum_amount",
            "value": match[2],
            "unit": "amount",
            "currency": match[1],
        }
    match = _match(
        rf"Medical event (classification|diagnosis code|procedure code) uses ({_IDENTIFIER}) "
        rf"version ({_IDENTIFIER}): ({_IDENTIFIER}(?:, {_IDENTIFIER}){{0,63}})\.",
        value,
    )
    if match:
        codes = match[4].split(", ")
        if len(codes) != len(set(codes)):
            return None
        field = {
            "classification": "classification",
            "diagnosis code": "diagnosis_code",
            "procedure code": "procedure_code",
        }[match[1]]
        return {
            "kind": "classification",
            "field": f"MedicalEvent.{field}",
            "code_system": match[2],
            "code_version": match[3],
            "codes": codes,
        }
    match = _match(
        rf"Eligible admission days range from ({_INTEGER}) to ({_INTEGER}) inclusive\.", value
    )
    if match and int(match[1]) <= int(match[2]):
        return {
            "kind": "condition",
            "rule_kind": "eligibility",
            "field": "MedicalEvent.admission_days",
            "operator": "range",
            "value": [int(match[1]), int(match[2])],
            "unit": "days",
        }
    match = _match(rf"The waiting period is ({_INTEGER}) days from contract start\.", value)
    if match:
        return {
            "kind": "condition",
            "rule_kind": "temporal",
            "field": "PolicyContract.contract_start",
            "operator": "days_since",
            "value": int(match[1]),
            "unit": "days",
        }
    match = _match(rf"Payment requires fewer than ({_INTEGER}) prior occurrences\.", value)
    if match:
        return {
            "kind": "condition",
            "rule_kind": "frequency",
            "field": "ClaimHistory.counted_occurrence",
            "operator": "count_below",
            "value": int(match[1]),
            "unit": "occurrences",
        }
    return None
