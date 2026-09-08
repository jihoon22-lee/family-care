"""The DB rejects cross-scope and malformed retained event terms metadata."""

import os
import subprocess
import sys
from copy import deepcopy
from datetime import date
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from familycare_api.clauses.terms_change_repository import TermsChangeProjector
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.repository import DecisionRepository
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from apps.api.tests.test_terms_change_integration import (
    _psycopg_url,
    _sources,
    enrollment_database,  # noqa: F401
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from apps.api.tests.test_terms_change_integration import (
    changes_database as changes_database,
)

pytestmark = pytest.mark.integration


def test_downgrade_cannot_remove_existing_selection_history(changes_database: Any) -> None:
    url, job = changes_database
    _sources(url, job)
    # Exercise the original decision-snapshot guard without newer amendment
    # history stopping the downgrade at an earlier migration.
    repository = DecisionRepository(url)
    scope = HouseholdScope(job.household_space_id)
    event = repository.create_medical_event(
        scope,
        family_member_id=job.family_member_id,
        mode="post_treatment",
        situation="Synthetic event",
        event_date=date(2025, 7, 1),
        visit_date=None,
        facts={},
    )
    result = repository.analyze_medical_event(scope, event.id)
    environment = {**os.environ, "FAMILYCARE_DATABASE_URL": url, "TMPDIR": "/tmp"}
    command = [sys.executable, "-m", "alembic", "-c", "apps/api/alembic.ini"]
    try:
        attempted = subprocess.run(
            command + ["downgrade", "0045_terms_changes"],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert attempted.returncode != 0
        assert "event terms history prevents downgrade" in attempted.stderr
    finally:
        restored = subprocess.run(
            command + ["upgrade", "head"],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert restored.returncode == 0
    assert (
        repository.get_decision_result(scope, event.id, event.version).terms_selections
        == result.terms_selections
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "member",
        "household",
        "date",
        "rider",
        "edition",
        "null_status",
        "missing_reason",
        "extra_field",
        "null_snapshot",
        "null_rules",
        "captured_rule",
    ],
)
def test_snapshot_insert_validates_exact_scope_and_shape(
    changes_database: Any, mutation: str
) -> None:
    url, job = changes_database
    sources = _sources(url, job)
    assert TermsChangeProjector(url).refresh_pending() == 1
    repository = DecisionRepository(url)
    scope = HouseholdScope(job.household_space_id)
    event = repository.create_medical_event(
        scope,
        family_member_id=job.family_member_id,
        mode="post_treatment",
        situation="Synthetic event",
        event_date=date(2025, 7, 1),
        visit_date=None,
        facts={},
    )
    result = repository.analyze_medical_event(scope, event.id)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        original = connection.execute(
            "SELECT to_jsonb(r) AS value FROM decision_runs r WHERE id=%s", (result.run_id,)
        ).fetchone()["value"]
        # The success pair is the same captured input under another immutable run ID.
        good = deepcopy(original)
        good["id"] = str(uuid4())
        connection.execute(
            "INSERT INTO decision_runs SELECT (jsonb_populate_record(NULL::decision_runs,%s)).*",
            (Jsonb(good),),
        )
        bad = deepcopy(original)
        bad["id"] = str(uuid4())
        selections = bad["terms_selections_json"]
        target = next(
            s for s in selections if s["scope"]["rider_id"] == str(sources["Sample Rider"])
        )
        if mutation == "member":
            target["scope"]["family_member_id"] = str(uuid4())
        elif mutation == "household":
            target["scope"]["household_space_id"] = str(uuid4())
        elif mutation == "date":
            target["event_date"] = "2025-06-30"
        elif mutation == "rider":
            # Keep valid household/member/contract IDs, but attach this change to another Rider.
            bad["terms_selections_json"] = [target]
            target["scope"]["rider_id"] = str(sources["Another Rider"])
        elif mutation == "edition":
            target["editions"][0]["edition_id"] = str(uuid4())
        elif mutation == "null_status":
            target["editions"][0]["status"] = None
        elif mutation == "missing_reason":
            del target["editions"][0]["reason_codes"]
        elif mutation == "extra_field":
            target["source_text"] = "Synthetic unexpected narrative"
        elif mutation == "null_rules":
            bad["source_rule_version_ids"] = None
        elif mutation == "captured_rule":
            bad["source_rule_version_ids"] = [str(uuid4())]
        else:
            bad["terms_selections_json"] = None
        with pytest.raises(psycopg.IntegrityError), connection.transaction():
            connection.execute(
                "INSERT INTO decision_runs "
                "SELECT (jsonb_populate_record(NULL::decision_runs,%s)).*",
                (Jsonb(bad),),
            )
        connection.execute("UPDATE decision_runs SET stale=true WHERE id=%s", (result.run_id,))
        assert (
            connection.execute(
                "SELECT terms_selections_json FROM decision_runs WHERE id=%s", (result.run_id,)
            ).fetchone()["terms_selections_json"]
            == original["terms_selections_json"]
        )
