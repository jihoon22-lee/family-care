"""Argon2id password boundary tests using wholly synthetic values."""

from __future__ import annotations

import pytest
from familycare_api.identity.password import PasswordHasher, PasswordHashError


def test_password_hash_is_argon2id_and_never_round_trips() -> None:
    hasher = PasswordHasher()
    raw = "synthetic-auth-secret-a"

    encoded = hasher.hash(raw)

    assert encoded.startswith("$argon2id$")
    assert hasher.verify(encoded, raw) is True
    assert raw not in encoded


def test_wrong_password_and_malformed_hash_fail_closed() -> None:
    hasher = PasswordHasher()
    encoded = hasher.hash("synthetic-auth-secret-a")

    assert hasher.verify(encoded, "synthetic-auth-secret-b") is False
    assert hasher.verify("not-an-argon2-hash", "synthetic-auth-secret-a") is False


@pytest.mark.parametrize(
    "raw",
    [
        "too-short",
        "x" * 1025,
        "contains\x00nul-and-is-long-enough",
    ],
)
def test_password_policy_rejects_unsafe_values(raw: str) -> None:
    with pytest.raises(PasswordHashError, match="INVALID_PASSWORD"):
        PasswordHasher().hash(raw)


def test_parameter_upgrade_is_reported_without_changing_verification() -> None:
    current = PasswordHasher()
    weaker = PasswordHasher(time_cost=1, memory_cost=8_192, parallelism=1)
    encoded = weaker.hash("synthetic-auth-secret-a")

    result = current.verify_and_check_upgrade(encoded, "synthetic-auth-secret-a")

    assert result.verified is True
    assert result.needs_rehash is True
