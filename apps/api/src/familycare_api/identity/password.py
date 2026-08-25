"""Argon2id hashing with a bounded, value-free failure surface."""

from __future__ import annotations

from dataclasses import dataclass

from argon2 import PasswordHasher as Argon2PasswordHasher
from argon2 import Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_MIN_PASSWORD_LENGTH = 16
_MAX_PASSWORD_LENGTH = 1024


class PasswordHashError(ValueError):
    """Reject invalid password input without retaining or echoing it."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class PasswordVerification:
    """Verification result kept separate from any replacement hash."""

    verified: bool
    needs_rehash: bool


class PasswordHasher:
    """Hash and verify local administrator passwords with Argon2id."""

    def __init__(
        self,
        *,
        time_cost: int = 3,
        memory_cost: int = 65_536,
        parallelism: int = 2,
    ) -> None:
        self._hasher = Argon2PasswordHasher(
            time_cost=time_cost,
            memory_cost=memory_cost,
            parallelism=parallelism,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )

    @staticmethod
    def validate(raw_password: str) -> None:
        if (
            not isinstance(raw_password, str)
            or not _MIN_PASSWORD_LENGTH <= len(raw_password) <= _MAX_PASSWORD_LENGTH
            or "\x00" in raw_password
        ):
            raise PasswordHashError("INVALID_PASSWORD")

    def hash(self, raw_password: str) -> str:
        self.validate(raw_password)
        return self._hasher.hash(raw_password)

    def verify(self, encoded_hash: str, raw_password: str) -> bool:
        return self.verify_and_check_upgrade(encoded_hash, raw_password).verified

    def verify_and_check_upgrade(
        self,
        encoded_hash: str,
        raw_password: str,
    ) -> PasswordVerification:
        try:
            self.validate(raw_password)
            verified = self._hasher.verify(encoded_hash, raw_password)
        except PasswordHashError, InvalidHashError, VerificationError, VerifyMismatchError:
            return PasswordVerification(verified=False, needs_rehash=False)
        return PasswordVerification(
            verified=verified,
            needs_rehash=verified and self._hasher.check_needs_rehash(encoded_hash),
        )


__all__ = ["PasswordHashError", "PasswordHasher", "PasswordVerification"]
