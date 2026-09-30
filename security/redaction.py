from __future__ import annotations

import os
import re
from collections.abc import Iterable

REDACTED = "[REDACTED]"
SECRET_NAME_RE = re.compile(
    r"(?:token|secret|password|passwd|api[_-]?key|authorization|database_url)",
    re.IGNORECASE,
)
BEARER_RE = re.compile(r"(?i)(\bBearer\s+)[^\s,;]+")
AUTH_HEADER_RE = re.compile(
    r"(?im)(\b(?:Authorization|x-api-key|x-goog-api-key)\s*[:=]\s*)[^\s,;]+"
)
QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:key|api_key|token|access_token)=)[^&#\s]+"
)
DATABASE_URL_RE = re.compile(
    r"(?i)(\b[a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s@]+(@)"
)
KNOWN_TOKEN_RE = re.compile(
    r"(?i)\b(?:github_pat_[A-Za-z0-9_]+|gh[pousr]_[A-Za-z0-9]+|sk-[A-Za-z0-9_-]{8,})\b"
)
PRIVATE_KEY_BLOCK_RE = re.compile(
    r"-----BEGIN (?P<kind>(?:RSA |EC |OPENSSH |DSA )?)PRIVATE KEY-----"
    r".*?-----END (?P=kind)PRIVATE KEY-----",
    re.DOTALL,
)


def _process_secret_values() -> list[str]:
    values: set[str] = set()
    for name, value in os.environ.items():
        if SECRET_NAME_RE.search(name) and value and len(value) >= 6:
            values.add(value)
    return sorted(values, key=len, reverse=True)


def sanitize_text(
    value: object,
    *,
    secret_values: Iterable[str] | None = None,
    max_length: int = 4_000,
) -> str:
    """Remove common credentials and known process secrets from bounded text."""
    text = str(value)
    secrets = _process_secret_values() if secret_values is None else secret_values
    for secret in sorted({item for item in secrets if item}, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    text = AUTH_HEADER_RE.sub(rf"\1{REDACTED}", text)
    text = BEARER_RE.sub(rf"\1{REDACTED}", text)
    text = QUERY_SECRET_RE.sub(rf"\1{REDACTED}", text)
    text = DATABASE_URL_RE.sub(rf"\1{REDACTED}\2", text)
    text = KNOWN_TOKEN_RE.sub(REDACTED, text)
    text = PRIVATE_KEY_BLOCK_RE.sub(REDACTED, text)
    return text[:max_length]


def sanitize_exception(error: BaseException, *, max_length: int = 2_000) -> str:
    return sanitize_text(
        f"{type(error).__name__}: {error}", max_length=max_length
    )
