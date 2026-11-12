"""Password hashing.

Uses scrypt from the standard library rather than adding a dependency. Each
password gets its own random salt, and verification is a constant-time
comparison so a timing side channel cannot leak how much of a hash matched.

Parameters follow the common interactive-login profile. They are stored
alongside each hash so the cost can be raised later without invalidating
existing passwords.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

#: scrypt cost parameters. n is the CPU/memory cost and must be a power of two.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
KEY_LENGTH = 32
SALT_BYTES = 16

#: Enforced at registration. Long enough to matter, short enough to type.
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 256


class PasswordError(ValueError):
    """A password that cannot be accepted. The message is safe to display."""


def validate_password(password: str | None) -> str:
    """Check a candidate password and return it, or raise ``PasswordError``."""
    if password is None or not str(password).strip():
        raise PasswordError("A password is required.")
    candidate = str(password)
    if len(candidate) < MIN_PASSWORD_LENGTH:
        raise PasswordError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )
    if len(candidate) > MAX_PASSWORD_LENGTH:
        raise PasswordError("That password is too long.")
    return candidate


def hash_password(password: str) -> str:
    """Return a self-describing hash string: ``scrypt$n$r$p$salt$key``."""
    salt = secrets.token_hex(SALT_BYTES)
    key = hashlib.scrypt(
        password.encode("utf-8"),
        salt=bytes.fromhex(salt),
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=KEY_LENGTH,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt}${key.hex()}"


def verify_password(password: str | None, stored: str | None) -> bool:
    """Check a password against a stored hash.

    Returns False for any malformed or missing hash rather than raising, so a
    corrupt row cannot be used to distinguish "no such user" from "bad
    password" during login.
    """
    if not password or not stored:
        return False
    try:
        scheme, n, r, p, salt, expected = str(stored).split("$")
        if scheme != "scrypt":
            return False
        key = hashlib.scrypt(
            str(password).encode("utf-8"),
            salt=bytes.fromhex(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected) // 2,
        )
    except (ValueError, TypeError, MemoryError):
        return False
    return hmac.compare_digest(key.hex(), expected)
