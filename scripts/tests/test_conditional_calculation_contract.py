"""The neutral calculation schema distinguishes a Boolean predicate from money."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

from scripts.check_document_contracts import validate_schema_instance


def schema() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(
            (
                Path(__file__).resolve().parents[2]
                / "packages/contracts/schemas/rider-clause-rules.v1.schema.json"
            ).read_text()
        ),
    )


def calculation() -> dict[str, Any]:
    return {
        "op": "if",
        "args": [
            {"field": "MedicalEvent.reduction_applies"},
            {"value": 0.5},
            {"value": 1},
        ],
    }


def test_conditional_tuple_keeps_a_boolean_field_and_two_numeric_branches() -> None:
    document = schema()
    assert not validate_schema_instance(
        document["$defs"]["RuleCalculation"], calculation(), root_schema=document
    )


@pytest.mark.parametrize(
    "fault",
    ["number_predicate", "boolean_branch", "boolean_arithmetic", "extra_branch", "rounding"],
)
def test_neutral_contract_rejects_wrong_conditional_roles(fault: str) -> None:
    document = schema()
    value = deepcopy(calculation())
    if fault == "number_predicate":
        value["args"][0] = {"value": 1}
    elif fault == "boolean_branch":
        value["args"][1] = {"field": "MedicalEvent.reduction_applies"}
    elif fault == "boolean_arithmetic":
        value["op"] = "multiply"
    elif fault == "extra_branch":
        value["args"].append({"value": 2})
    else:
        value["rounding"] = "half_up"
    assert validate_schema_instance(
        document["$defs"]["RuleCalculation"], value, root_schema=document
    )
