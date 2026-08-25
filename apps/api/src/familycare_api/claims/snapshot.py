"""Build immutable, privacy-bounded ClaimCase result snapshots.

ClaimCase snapshots are deliberately narrower than the objects used by the
decision engine.  A decision result can contain natural-language medical
input, fact values, receipt metadata, and document internals.  A claim
snapshot keeps only normalized identifiers, versions, tri-state outcomes,
reason codes, calculation metadata, and page-level Evidence references.

The module accepts domain objects and mapping-shaped values so that the
repository boundary does not need to serialize a live domain object first.
Only explicitly allow-listed attributes are copied.  Mappings are scanned for
forbidden keys before they are persisted; object attributes that are not part
of an allow-list are ignored.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

_MISSING = object()

# These names are intentionally conservative.  ``input_field_paths`` and
# ``document_kind`` are allowed metadata; actual source paths, document body,
# OCR, receipt references, and medical narrative are not.
_FORBIDDEN_KEYS = frozenset(
    {
        "absolute_path",
        "body",
        "diagnosis",
        "document_body",
        "document_text",
        "external_document_id",
        "file",
        "file_name",
        "filepath",
        "image",
        "image_bytes",
        "medical_text",
        "note",
        "ocr",
        "ocr_text",
        "password",
        "path",
        "prescription",
        "raw_text",
        "receipt",
        "receipt_lines",
        "receipt_note",
        "receipt_number",
        "situation",
        "source_path",
        "text",
        "user_note",
    }
)


class SnapshotPrivacyError(ValueError):
    """A snapshot input crossed the claim privacy boundary."""

    code = "FORBIDDEN_SNAPSHOT_FIELD"

    def __init__(self, code: str = code) -> None:
        self.code = code
        # Do not include the rejected key.  Exception text can reach logs.
        super().__init__(code)


class SnapshotValidationError(ValueError):
    """A snapshot value cannot be represented by the JSON contract."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _normalized_key(key: object) -> str:
    if not isinstance(key, str):
        raise SnapshotValidationError("SNAPSHOT_KEY_NOT_STRING")
    return "".join(character for character in key.casefold() if character.isalnum())


def _is_forbidden_key(key: object) -> bool:
    normalized = _normalized_key(key)
    return normalized in {_normalized_key(item) for item in _FORBIDDEN_KEYS}


def _reject_forbidden_keys(value: object) -> None:
    """Reject forbidden keys in mapping-shaped inputs before selection."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            if _is_forbidden_key(key):
                raise SnapshotPrivacyError
            _reject_forbidden_keys(child)
    elif isinstance(value, list | tuple):
        for child in value:
            _reject_forbidden_keys(child)


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise SnapshotValidationError("SNAPSHOT_DECIMAL_NOT_FINITE")
    normalized = value.normalize()
    if normalized == 0:
        return "0"
    return format(normalized, "f")


def _json_value(value: object) -> object:
    """Convert supported values into detached JSON-compatible primitives."""

    if value is None or isinstance(value, str | bool | int):
        if isinstance(value, bool | int) and isinstance(value, bool):
            return value
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SnapshotValidationError("SNAPSHOT_FLOAT_NOT_FINITE")
        return value
    if isinstance(value, Mapping):
        _reject_forbidden_keys(value)
        converted: dict[str, object] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise SnapshotValidationError("SNAPSHOT_KEY_NOT_STRING")
            converted[key] = _json_value(child)
        return converted
    if isinstance(value, list | tuple):
        return [_json_value(child) for child in value]
    raise SnapshotValidationError("SNAPSHOT_VALUE_UNSUPPORTED")


def canonical_json(payload: Mapping[str, object]) -> str:
    """Return deterministic compact JSON for a snapshot payload."""

    if not isinstance(payload, Mapping):
        raise SnapshotValidationError("SNAPSHOT_PAYLOAD_NOT_OBJECT")
    _reject_forbidden_keys(payload)
    converted = _json_value(payload)
    if not isinstance(converted, dict):  # pragma: no cover - guarded above.
        raise SnapshotValidationError("SNAPSHOT_PAYLOAD_NOT_OBJECT")
    return json.dumps(
        converted,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def snapshot_sha256(payload: Mapping[str, object]) -> str:
    """Hash canonical snapshot JSON with a stable lower-case SHA-256 digest."""

    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return _FrozenDict({str(key): _freeze(child) for key, child in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(child) for child in value)
    return value


class _FrozenDict(dict[str, object]):
    """JSON-serializable dict that rejects ordinary mutation operations."""

    def __setitem__(self, key: str, value: object) -> None:
        raise TypeError("immutable snapshot mapping")

    def __delitem__(self, key: str) -> None:
        raise TypeError("immutable snapshot mapping")

    def clear(self) -> None:
        raise TypeError("immutable snapshot mapping")

    def pop(self, key: str, default: object = _MISSING) -> object:
        raise TypeError("immutable snapshot mapping")

    def popitem(self) -> tuple[str, object]:
        raise TypeError("immutable snapshot mapping")

    def setdefault(self, key: str, default: object = None) -> object:
        raise TypeError("immutable snapshot mapping")

    def update(self, *args: object, **kwargs: object) -> None:
        raise TypeError("immutable snapshot mapping")

    def __ior__(self, other: object, /) -> _FrozenDict:  # type: ignore[override,misc]
        raise TypeError("immutable snapshot mapping")

    def __deepcopy__(self, memo: dict[int, object]) -> _FrozenDict:
        copied = _FrozenDict()
        memo[id(self)] = copied
        for key, value in self.items():
            dict.__setitem__(copied, key, deepcopy(value, memo))
        return copied


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    return value


def _attribute(value: object, name: str, default: object = _MISSING) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _as_sequence(value: object) -> tuple[object, ...]:
    if value is None or value is _MISSING:
        return ()
    if isinstance(value, str | bytes | bytearray):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(value)
    return (value,)


def _id_value(value: object) -> str | None:
    if value is _MISSING or value is None:
        return None
    if isinstance(value, UUID):
        if value.int == 0:
            raise SnapshotValidationError("SNAPSHOT_ID_ZERO")
        return str(value)
    if isinstance(value, str) and value:
        return value
    raise SnapshotValidationError("SNAPSHOT_ID_INVALID")


def _scalar(value: object) -> object:
    if value is _MISSING:
        return None
    return _json_value(value)


def _put(payload: dict[str, object], key: str, value: object) -> None:
    if value is not _MISSING and value is not None:
        payload[key] = _scalar(value)


def _put_id(payload: dict[str, object], key: str, value: object) -> None:
    identifier = _id_value(value)
    if identifier is not None:
        payload[key] = identifier


def _string_tuple(value: object) -> list[str]:
    result: list[str] = []
    for item in _as_sequence(value):
        if not isinstance(item, str) or not item:
            raise SnapshotValidationError("SNAPSHOT_REASON_CODE_INVALID")
        result.append(item)
    return result


def _evidence_payload(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        _reject_forbidden_keys(value)
    payload: dict[str, object] = {}
    _put_id(payload, "evidence_id", _attribute(value, "evidence_id", _attribute(value, "id")))
    _put_id(payload, "document_version_id", _attribute(value, "document_version_id"))
    _put_id(payload, "extraction_id", _attribute(value, "extraction_id"))
    _put(payload, "content_sha256", _attribute(value, "content_sha256"))
    _put(payload, "physical_page", _attribute(value, "physical_page"))
    bbox = _attribute(value, "bbox")
    if bbox is not _MISSING and bbox is not None:
        payload["bbox"] = [_scalar(item) for item in _as_sequence(bbox)]
    _put(payload, "review_state", _attribute(value, "review_state"))
    return payload


def _evaluation_payload(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        _reject_forbidden_keys(value)
    payload: dict[str, object] = {}
    _put_id(payload, "id", _attribute(value, "id"))
    _put_id(payload, "rider_id", _attribute(value, "rider_id"))
    _put_id(
        payload,
        "rule_version_id",
        _attribute(value, "rule_version_id", _attribute(value, "coverage_rule_version_id")),
    )
    for key in ("result", "required", "reason_code", "evaluator_version"):
        _put(payload, key, _attribute(value, key))
    for key in ("fact_paths", "missing_fields", "conflicting_fields"):
        raw = _attribute(value, key)
        if raw is not _MISSING and raw is not None:
            payload[key] = _string_tuple(raw)
    evidence_ids = _attribute(value, "evidence_ids")
    if evidence_ids is not _MISSING and evidence_ids is not None:
        payload["evidence_ids"] = [
            identifier for item in _as_sequence(evidence_ids) if (identifier := _id_value(item))
        ]
    return payload


def _candidate_payload(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        _reject_forbidden_keys(value)
    payload: dict[str, object] = {}
    for key in ("id", "rider_id", "decision_run_id"):
        _put_id(payload, key, _attribute(value, key))
    for key in (
        "aggregate_result",
        "rider_type",
        "rider_label",
        "version",
    ):
        _put(payload, key, _attribute(value, key))
    for key in (
        "hold_reason_codes",
        "required_match_count",
        "required_unknown_count",
        "required_no_match_count",
    ):
        raw = _attribute(value, key)
        if raw is _MISSING or raw is None:
            continue
        if key == "hold_reason_codes":
            payload[key] = _string_tuple(raw)
        else:
            payload[key] = _scalar(raw)
    evaluations = _as_sequence(_attribute(value, "evaluations"))
    if evaluations:
        payload["evaluations"] = [_evaluation_payload(item) for item in evaluations]
    questions = _as_sequence(_attribute(value, "questions"))
    if questions:
        normalized_questions: list[dict[str, object]] = []
        for question in questions:
            if isinstance(question, Mapping):
                _reject_forbidden_keys(question)
            item: dict[str, object] = {}
            _put(item, "field_path", _attribute(question, "field_path"))
            _put(item, "reason_code", _attribute(question, "reason_code"))
            normalized_questions.append(item)
        payload["questions"] = normalized_questions
    return payload


def _candidate_snapshot(result: object) -> dict[str, object]:
    if isinstance(result, Mapping):
        _reject_forbidden_keys(result)
    payload: dict[str, object] = {"schema_version": "claim-candidate-snapshot-v1"}
    for key in (
        "run_id",
        "medical_event_id",
    ):
        _put_id(payload, key, _attribute(result, key))
    for key in (
        "event_version",
        "engine_version",
        "rule_set_version",
        "policy_snapshot_at",
        "stale",
    ):
        _put(payload, key, _attribute(result, key))
    candidates = _as_sequence(_attribute(result, "candidates"))
    payload["candidates"] = [_candidate_payload(item) for item in candidates]
    evaluations = _as_sequence(_attribute(result, "evaluations"))
    payload["evaluations"] = [_evaluation_payload(item) for item in evaluations]
    return payload


def _rule_payload(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        _reject_forbidden_keys(value)
    payload: dict[str, object] = {}
    for key in (
        "id",
        "coverage_rule_id",
        "candidate_version_id",
    ):
        _put_id(payload, key, _attribute(value, key))
    for key in (
        "version_number",
        "schema_version",
        "rule_kind",
        "required",
        "result_reason_code",
        "review_state",
        "executable",
        "generator_version",
        "verifier_version",
        "published_at",
    ):
        _put(payload, key, _attribute(value, key))
    for key in ("input_field_paths",):
        raw = _attribute(value, key)
        if raw is not _MISSING and raw is not None:
            payload[key] = _string_tuple(raw)
    rule_document = _attribute(value, "rule_document")
    if rule_document is not _MISSING and rule_document is not None:
        if not isinstance(rule_document, Mapping):
            raise SnapshotValidationError("SNAPSHOT_RULE_DOCUMENT_INVALID")
        _reject_forbidden_keys(rule_document)
        payload["rule_document"] = _json_value(rule_document)
    evidence = _as_sequence(_attribute(value, "evidence"))
    if evidence:
        payload["evidence_ids"] = [
            identifier
            for item in evidence
            if (identifier := _id_value(_attribute(item, "evidence_id", _attribute(item, "id"))))
        ]
    return payload


def _rule_snapshot(result: object, rule_versions: object) -> dict[str, object]:
    versions = _as_sequence(rule_versions)
    if not versions:
        # A result still records the rule-set version and evaluated rule IDs,
        # even when the caller did not load complete CoverageRuleVersion rows.
        derived: dict[str, dict[str, object]] = {}
        for item in _as_sequence(_attribute(result, "evaluations")):
            rule_id = _id_value(
                _attribute(item, "rule_version_id", _attribute(item, "coverage_rule_version_id"))
            )
            if rule_id is not None:
                derived.setdefault(rule_id, {"id": rule_id})
        versions = tuple(derived.values())
    payload: dict[str, object] = {
        "schema_version": "claim-rule-snapshot-v1",
        "versions": [_rule_payload(item) for item in versions],
    }
    _put(payload, "rule_set_version", _attribute(result, "rule_set_version"))
    return payload


def _policy_payload(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        _reject_forbidden_keys(value)
    payload: dict[str, object] = {}
    for key in ("policy_id", "rider_id"):
        _put_id(payload, key, _attribute(value, key, _attribute(value, "id")))
    for key in (
        "effective_status",
        "rider_type",
        "rider_label",
        "contract_start",
        "contract_end",
        "rider_coverage_start",
        "rider_coverage_end",
        "rider_status",
        "insured_amount",
        "currency",
        "renewable",
        "status_checked_at",
    ):
        _put(payload, key, _attribute(value, key))
    evidence_ids = _attribute(value, "evidence_ids")
    if evidence_ids is not _MISSING and evidence_ids is not None:
        payload["evidence_ids"] = [
            identifier for item in _as_sequence(evidence_ids) if (identifier := _id_value(item))
        ]
    return payload


def _policy_snapshot(result: object, policy_snapshot: object) -> dict[str, object]:
    values = _as_sequence(policy_snapshot)
    if not values:
        values = _as_sequence(_attribute(result, "policy_snapshots"))
    return {
        "schema_version": "claim-policy-snapshot-v1",
        "snapshots": [_policy_payload(item) for item in values],
    }


def _money_payload(value: object) -> dict[str, object] | None:
    if value is _MISSING or value is None:
        return None
    if isinstance(value, Mapping):
        _reject_forbidden_keys(value)
    amount = _attribute(value, "amount")
    currency = _attribute(value, "currency")
    if amount is _MISSING or currency is _MISSING:
        raise SnapshotValidationError("SNAPSHOT_MONEY_INVALID")
    return {"amount": _scalar(amount), "currency": _scalar(currency)}


def _calculation_step(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        _reject_forbidden_keys(value)
    payload: dict[str, object] = {}
    for key in ("step_number", "operation", "rounding_rule", "reason_code"):
        _put(payload, key, _attribute(value, key))
    for key in ("input_amount", "output_amount"):
        money = _money_payload(_attribute(value, key))
        if money is not None:
            payload[key] = money
    return payload


def _one_calculation_snapshot(calculation: object) -> dict[str, object]:
    if calculation is None or calculation is _MISSING:
        return {}
    if isinstance(calculation, Mapping):
        _reject_forbidden_keys(calculation)
    payload: dict[str, object] = {}
    for key in (
        "calculation_id",
        "claim_candidate_id",
        "rule_version_id",
    ):
        _put_id(payload, key, _attribute(calculation, key))
    for key in (
        "kind",
        "status",
        "applied_rate",
        "rounding_rule",
        "engine_version",
        "version",
    ):
        _put(payload, key, _attribute(calculation, key))
    for key in (
        "confirmed",
        "additional",
        "excluded",
        "deductible",
        "applied_limit",
    ):
        money = _money_payload(_attribute(calculation, key))
        if money is not None:
            payload[key] = money
    for key in ("hold_reason_codes", "excluded_reason_codes"):
        raw = _attribute(calculation, key)
        if raw is not _MISSING and raw is not None:
            payload[key] = _string_tuple(raw)
    evidence_ids = _attribute(calculation, "evidence_ids")
    if evidence_ids is not _MISSING and evidence_ids is not None:
        payload["evidence_ids"] = [
            identifier for item in _as_sequence(evidence_ids) if (identifier := _id_value(item))
        ]
    steps = _as_sequence(_attribute(calculation, "steps"))
    if steps:
        payload["steps"] = [_calculation_step(item) for item in steps]
    return payload


def _calculation_snapshot(calculation: object) -> dict[str, object]:
    if calculation is None or calculation is _MISSING:
        values: tuple[object, ...] = ()
    elif isinstance(calculation, Mapping):
        values = (calculation,)
    else:
        values = _as_sequence(calculation)
    return {
        "schema_version": "claim-calculation-snapshot-v1",
        "calculations": [_one_calculation_snapshot(item) for item in values],
    }


def _evidence_snapshot(result: object, evidence: object) -> dict[str, object]:
    values = list(_as_sequence(evidence))
    if not values:
        for candidate in _as_sequence(_attribute(result, "candidates")):
            for evaluation in _as_sequence(_attribute(candidate, "evaluations")):
                values.extend(_as_sequence(_attribute(evaluation, "evidence")))
        values.extend(
            item
            for evaluation in _as_sequence(_attribute(result, "evaluations"))
            for item in _as_sequence(_attribute(evaluation, "evidence"))
        )
    deduped: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in values:
        normalized = _evidence_payload(item)
        identifier = normalized.get("evidence_id")
        if isinstance(identifier, str):
            if identifier in seen:
                continue
            seen.add(identifier)
        deduped.append(normalized)
    return {
        "schema_version": "claim-evidence-snapshot-v1",
        "evidence": deduped,
    }


@dataclass(frozen=True, slots=True)
class ClaimCaseSnapshot:
    """Deeply immutable claim snapshot suitable for append-only storage."""

    snapshot_version: int
    candidate_snapshot: Mapping[str, object]
    rule_snapshot: Mapping[str, object]
    policy_snapshot: Mapping[str, object]
    evidence_snapshot: Mapping[str, object]
    calculation_snapshot: Mapping[str, object]
    snapshot_sha256: str
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if isinstance(self.snapshot_version, bool) or self.snapshot_version < 1:
            raise SnapshotValidationError("SNAPSHOT_VERSION_INVALID")
        components = (
            self.candidate_snapshot,
            self.rule_snapshot,
            self.policy_snapshot,
            self.evidence_snapshot,
            self.calculation_snapshot,
        )
        frozen_components: list[Mapping[str, object]] = []
        for component in components:
            if not isinstance(component, Mapping):
                raise SnapshotValidationError("SNAPSHOT_COMPONENT_NOT_OBJECT")
            converted = _json_value(component)
            if not isinstance(converted, dict):  # pragma: no cover - guarded above.
                raise SnapshotValidationError("SNAPSHOT_COMPONENT_NOT_OBJECT")
            frozen_components.append(_freeze(converted))  # type: ignore[arg-type]
        for name, component in zip(
            (
                "candidate_snapshot",
                "rule_snapshot",
                "policy_snapshot",
                "evidence_snapshot",
                "calculation_snapshot",
            ),
            frozen_components,
            strict=True,
        ):
            object.__setattr__(self, name, component)
        if (
            not isinstance(self.snapshot_sha256, str)
            or len(self.snapshot_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.snapshot_sha256)
        ):
            raise SnapshotValidationError("SNAPSHOT_HASH_INVALID")
        if self.snapshot_sha256 != snapshot_sha256(self.payload()):
            raise SnapshotValidationError("SNAPSHOT_HASH_MISMATCH")

    @property
    def candidate_snapshot_json(self) -> Mapping[str, object]:
        return self.candidate_snapshot

    @property
    def rule_snapshot_json(self) -> Mapping[str, object]:
        return self.rule_snapshot

    @property
    def policy_snapshot_json(self) -> Mapping[str, object]:
        return self.policy_snapshot

    @property
    def evidence_snapshot_json(self) -> Mapping[str, object]:
        return self.evidence_snapshot

    @property
    def calculation_snapshot_json(self) -> Mapping[str, object]:
        return self.calculation_snapshot

    def payload(self) -> dict[str, object]:
        """Return a detached JSON-compatible payload without its hash."""

        return {
            "candidate_snapshot": _thaw(self.candidate_snapshot),
            "rule_snapshot": _thaw(self.rule_snapshot),
            "policy_snapshot": _thaw(self.policy_snapshot),
            "evidence_snapshot": _thaw(self.evidence_snapshot),
            "calculation_snapshot": _thaw(self.calculation_snapshot),
        }

    def persistence_values(self) -> dict[str, object]:
        """Return detached component values for a PostgreSQL JSONB adapter."""

        return {
            "snapshot_version": self.snapshot_version,
            **self.payload(),
            "snapshot_sha256": self.snapshot_sha256,
            "created_at": self.created_at,
        }


def build_claim_snapshot(
    result: object,
    calculation: object | None = None,
    *,
    policy_snapshot: object = (),
    rule_versions: object = (),
    evidence: object = (),
    snapshot_version: int = 1,
    created_at: datetime | None = None,
) -> ClaimCaseSnapshot:
    """Build one sanitized immutable snapshot at ClaimCase creation time."""

    # Mapping sources are scanned before any allow-list projection.  This
    # prevents a caller from accidentally persisting a forbidden nested field.
    for source in (result, calculation, policy_snapshot, rule_versions, evidence):
        _reject_forbidden_keys(source)
    candidate = _candidate_snapshot(result)
    rule = _rule_snapshot(result, rule_versions)
    policy = _policy_snapshot(result, policy_snapshot)
    evidence_payload = _evidence_snapshot(result, evidence)
    calculation_payload = _calculation_snapshot(calculation)
    payload = {
        "candidate_snapshot": candidate,
        "rule_snapshot": rule,
        "policy_snapshot": policy,
        "evidence_snapshot": evidence_payload,
        "calculation_snapshot": calculation_payload,
    }
    return ClaimCaseSnapshot(
        snapshot_version=snapshot_version,
        candidate_snapshot=candidate,
        rule_snapshot=rule,
        policy_snapshot=policy,
        evidence_snapshot=evidence_payload,
        calculation_snapshot=calculation_payload,
        snapshot_sha256=snapshot_sha256(payload),
        created_at=created_at,
    )


__all__ = [
    "ClaimCaseSnapshot",
    "SnapshotPrivacyError",
    "SnapshotValidationError",
    "build_claim_snapshot",
    "canonical_json",
    "snapshot_sha256",
]
