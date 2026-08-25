"""Memory-only batch password scope tests."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from familycare_worker.imports.password_scope import PasswordScope

SYNTHETIC_BATCH_ID = UUID("00000000-0000-4000-8000-000000000005")
SYNTHETIC_ITEM_ID_A = UUID("00000000-0000-4000-8000-000000000101")
SYNTHETIC_ITEM_ID_B = UUID("00000000-0000-4000-8000-000000000102")
SYNTHETIC_PASSWORD = "synthetic-batch-password"
SYNTHETIC_REPLACEMENT_PASSWORD = "synthetic-replacement-password"


def _scope(
    *,
    password: str = SYNTHETIC_PASSWORD,
    expires_at: datetime | None = None,
) -> PasswordScope:
    return PasswordScope(
        batch_id=SYNTHETIC_BATCH_ID,
        password=password,
        expires_at=expires_at or datetime.now(UTC) + timedelta(minutes=1),
    )


def test_one_batch_password_is_reused_for_two_item_ids() -> None:
    scope = _scope()

    assert scope.password_for(SYNTHETIC_ITEM_ID_A) == SYNTHETIC_PASSWORD
    assert scope.password_for(SYNTHETIC_ITEM_ID_B) == SYNTHETIC_PASSWORD


def test_replace_updates_password_and_expiry_without_retaining_old_value() -> None:
    now = datetime.now(UTC)
    scope = _scope()
    scope.replace(
        SYNTHETIC_REPLACEMENT_PASSWORD,
        expires_at=now + timedelta(minutes=1),
    )

    assert scope.password_for(SYNTHETIC_ITEM_ID_A) == SYNTHETIC_REPLACEMENT_PASSWORD
    assert SYNTHETIC_PASSWORD not in repr(scope)

    scope.replace("synthetic-expired-password", expires_at=now - timedelta(seconds=1))
    assert scope.password_for(SYNTHETIC_ITEM_ID_A) is None
    assert scope.password_for(SYNTHETIC_ITEM_ID_B) is None


def test_dispose_removes_password_for_every_item_and_is_idempotent() -> None:
    scope = _scope(password=SYNTHETIC_REPLACEMENT_PASSWORD)

    scope.dispose()
    scope.dispose()

    assert scope.password_for(SYNTHETIC_ITEM_ID_A) is None
    assert scope.password_for(SYNTHETIC_ITEM_ID_B) is None
    assert SYNTHETIC_PASSWORD not in repr(scope)
    assert SYNTHETIC_REPLACEMENT_PASSWORD not in repr(scope)


def test_scope_repr_logs_and_validation_errors_never_include_password(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    scope = _scope()

    assert SYNTHETIC_PASSWORD not in repr(scope)
    with pytest.raises(ValueError) as raised:
        scope.replace("", expires_at=datetime.now(UTC) + timedelta(minutes=1))

    assert SYNTHETIC_PASSWORD not in str(raised.value)
    assert SYNTHETIC_PASSWORD not in repr(raised.value)
    assert SYNTHETIC_PASSWORD not in caplog.text
