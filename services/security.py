"""Password hashing and opaque bearer tokens, built on the standard library.

Passwords are stored as PBKDF2-HMAC-SHA256 digests with a per-password salt.
API tokens are random secrets that are only ever persisted as SHA-256 digests,
so a leaked database cannot be replayed against the API.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets


PBKDF2_ALGORITHM = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 240_000
SALT_BYTES = 16
TOKEN_BYTES = 32

# NOTE: 用于未知账号的登录尝试，让失败路径与真实校验耗时接近，避免用户名枚举。
_DUMMY_HASH: str | None = None


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    """Return an ``algorithm$iterations$salt$digest`` string safe to store."""
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{PBKDF2_ALGORITHM}${iterations}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time check of a candidate password against a stored digest."""
    try:
        algorithm, iterations_text, salt_text, digest_text = encoded.split("$")
        if algorithm != PBKDF2_ALGORITHM:
            return False
        iterations = int(iterations_text)
        salt = _b64decode(salt_text)
        expected = _b64decode(digest_text)
    except (AttributeError, ValueError):
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(candidate, expected)


def waste_password_comparison(password: str) -> None:
    """Spend the same work as a real verification when the account is unknown."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_urlsafe(16))
    verify_password(password or "", _DUMMY_HASH)


def generate_token() -> str:
    """Create the secret that is handed to the client exactly once."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """Digest used as the token's primary key; never store the raw token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
