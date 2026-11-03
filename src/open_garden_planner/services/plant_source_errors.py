"""Structured, UI-independent errors for explicit plant source retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SOURCE_UNSUPPORTED_URL = "SOURCE_UNSUPPORTED_URL"
SOURCE_ROBOTS_DENIED = "SOURCE_ROBOTS_DENIED"
SOURCE_HTTP_ERROR = "SOURCE_HTTP_ERROR"
SOURCE_NETWORK_ERROR = "SOURCE_NETWORK_ERROR"
SOURCE_TIMEOUT = "SOURCE_TIMEOUT"
SOURCE_TOO_LARGE = "SOURCE_TOO_LARGE"
SOURCE_PRODUCT_AMBIGUOUS = "SOURCE_PRODUCT_AMBIGUOUS"
SOURCE_PARSE_ERROR = "SOURCE_PARSE_ERROR"
SOURCE_IDENTITY_MISMATCH = "SOURCE_IDENTITY_MISMATCH"
SOURCE_UNKNOWN = "SOURCE_UNKNOWN"

_SECRET_QUERY_TERMS = (
    "token", "api_key", "apikey", "key", "password", "passwd", "secret",
    "authorization", "auth", "signature", "session", "jwt", "accesscode",
)
_URL_IN_MESSAGE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def sanitize_url(url: object) -> str:
    """Return a useful diagnostic URL with credentials and secret parameters removed."""
    if not isinstance(url, str) or not url:
        return ""
    try:
        parts = urlsplit(url)
        if not parts.scheme or not parts.netloc:
            return ""
        hostname = parts.hostname or ""
        if not hostname:
            return ""
        host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
        try:
            port = parts.port
        except ValueError:
            port = None
        netloc = host + (f":{port}" if port is not None else "")
        safe_query = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            normalized_key = re.sub(r"[^a-z0-9]+", "", key.casefold())
            if any(secret in normalized_key for secret in _SECRET_QUERY_TERMS):
                value = "[redacted]"
            safe_query.append((key, value))
        return urlunsplit((parts.scheme, netloc, parts.path, urlencode(safe_query), ""))
    except (TypeError, ValueError):
        return ""


def sanitize_message(message: object) -> str:
    """Remove URL credentials and query secrets from user-visible diagnostics."""
    if not isinstance(message, str):
        return str(message)

    def replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        trimmed = raw.rstrip(".,;:)]}")
        punctuation = raw[len(trimmed):]
        return sanitize_url(trimmed) + punctuation

    return _URL_IN_MESSAGE.sub(replace, message)


@dataclass(frozen=True, slots=True)
class PlantSourceErrorInfo:
    """Copyable error data that does not depend on Qt or the retrieval worker."""

    code: str
    message: str
    url: str = ""
    http_status: int | None = None
    stage: str = "unknown"
    retryable: bool = False


class PlantSourceError(ValueError):
    """A source failure with a stable code and sanitized diagnostics."""

    def __init__(self, message: str, *, code: str = SOURCE_UNKNOWN, url: str = "",
                 http_status: int | None = None, stage: str = "unknown",
                 retryable: bool = False):
        self.message = sanitize_message(message)
        self.code = code
        self.url = sanitize_url(url)
        self.http_status = http_status
        self.stage = stage
        self.retryable = bool(retryable)
        super().__init__(self.message)

    def to_info(self) -> PlantSourceErrorInfo:
        return PlantSourceErrorInfo(
            code=self.code,
            message=self.message,
            url=self.url,
            http_status=self.http_status,
            stage=self.stage,
            retryable=self.retryable,
        )


def plant_source_error_info(
    error: BaseException | PlantSourceErrorInfo, *, url: str = "",
) -> PlantSourceErrorInfo:
    """Convert any worker failure to the stable DTO used by the Qt boundary."""
    if isinstance(error, PlantSourceErrorInfo):
        cleaned = PlantSourceErrorInfo(
            code=error.code or SOURCE_UNKNOWN,
            message=sanitize_message(error.message),
            url=sanitize_url(error.url),
            http_status=error.http_status,
            stage=error.stage if error.stage in {
                "validation", "robots", "fetch", "redirect", "parse", "identity", "unknown",
            } else "unknown",
            retryable=bool(error.retryable),
        )
        return error if cleaned == error else cleaned
    if isinstance(error, PlantSourceError):
        return error.to_info()
    message = sanitize_message(str(error)) or error.__class__.__name__
    return PlantSourceErrorInfo(
        code=SOURCE_UNKNOWN,
        message=message,
        url=sanitize_url(url),
        stage="unknown",
        retryable=False,
    )


def format_plant_source_error(
    error: BaseException | PlantSourceErrorInfo, *, url: str = "",
) -> str:
    """Format a compact inline diagnostic while retaining the original detail."""
    info = plant_source_error_info(error, url=url)
    status = f" HTTP {info.http_status}" if info.http_status is not None else ""
    return f"[{info.code}{status}] {info.message}"
