"""Compare already source-validated metadata without weakening earlier facts."""

from typing import Any


def metadata_refines(previous: dict[str, Any], proposed: dict[str, Any]) -> bool:
    """Require a strict improvement; callers separately protect scope and user history."""
    try:
        role = previous["role"]
        if role not in {"policy", "terms", "product_explanation", "application", "amendment"}:
            return False
        if role != proposed["role"]:
            return False
        old_start, old_end = previous["page_start"], previous["page_end"]
        new_start, new_end = proposed["page_start"], proposed["page_end"]
        if any(type(value) is not int for value in (old_start, old_end, new_start, new_end)):
            return False
        if not 1 <= new_start <= old_start <= old_end <= new_end <= 500:
            return False
        expanded = (old_start, old_end) != (new_start, new_end)
        if expanded and role in {"policy", "application", "amendment"}:
            return False
        old_bad = set(previous["conflicting_fields"]) | set(previous["unresolved_fields"])
        new_bad = set(proposed["conflicting_fields"]) | set(proposed["unresolved_fields"])
        old_facts = {
            (fact["field"], fact["value"])
            for fact in previous["facts"]
            if fact["field"] not in old_bad
        }
        new_facts = {
            (fact["field"], fact["value"])
            for fact in proposed["facts"]
            if fact["field"] not in new_bad
        }
        return old_facts <= new_facts and (expanded or bool(new_facts - old_facts))
    except KeyError, TypeError:
        return False
