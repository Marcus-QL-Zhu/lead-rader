"""Small, deterministic boundary for operational errors and persisted bundles."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit


_EXACT_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "proxy_authorization",
        "cookie",
        "set_cookie",
        "header",
        "headers",
        "request_header",
        "request_headers",
        "response_header",
        "response_headers",
        "password",
        "passwd",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "auth_token",
        "bearer_token",
        "session_token",
        "csrf_token",
        "api_key",
        "apikey",
        "x_api_key",
        "app_secret",
        "client_secret",
        "credential",
        "credentials",
        "feishu_app_secret",
        "metaso_api_key",
        "minimax_api_key",
        "aws_access_key_id",
        "aws_secret_access_key",
        "aws_session_token",
        "x_amz_credential",
        "x_amz_signature",
        "x_amz_security_token",
        "github_token",
        "gitlab_token",
        "private_token",
        "oauth_token",
    }
)
_NON_SECRET_KEY_SUFFIXES = frozenset(
    {
        "at",
        "count",
        "date",
        "digest",
        "fingerprint",
        "hash",
        "length",
        "sha1",
        "sha256",
        "sha512",
        "size",
        "status",
        "time",
        "timestamp",
        "total",
        "version",
    }
)
_COMPACT_SENSITIVE_SUFFIXES = (
    "feishuappsecret",
    "metasoapikey",
    "minimaxapikey",
    "awsaccesskeyid",
    "awssecretaccesskey",
    "awssessiontoken",
    "githubtoken",
    "gitlabtoken",
    "privateaccesstoken",
    "oauthaccesstoken",
    "accesstoken",
    "refreshtoken",
    "clientsecret",
    "appsecret",
    "apikey",
)
_SENSITIVE_KEY_COMPONENT = re.compile(
    r"(?:^|_)(?:password|passwd|credential|credentials|authorization)(?:$|_)"
)
_SENSITIVE_URL_QUERY_KEYS = frozenset(
    {
        "authorization",
        "auth",
        "credential",
        "credentials",
        "password",
        "passwd",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "auth_token",
        "bearer_token",
        "session_token",
        "csrf_token",
        "api_key",
        "apikey",
        "x_api_key",
        "app_secret",
        "client_secret",
        "signature",
        "sig",
        "key",
        "access_key",
        "access_key_id",
        "secret_key",
        "secret_access_key",
        "security_token",
        "x_amz_credential",
        "x_amz_signature",
        "x_amz_security_token",
        "github_token",
        "gitlab_token",
        "private_token",
        "oauth_token",
        "google_access_id",
        "x_goog_credential",
        "x_goog_signature",
        "x_goog_security_token",
    }
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?ix)"
    r"(?P<key>"
    r"(?:feishu[._ -]?app[._ -]?secret|metaso[._ -]?api[._ -]?key|"
    r"minimax[._ -]?api[._ -]?key|aws[._ -]?secret[._ -]?access[._ -]?key|"
    r"aws[._ -]?access[._ -]?key[._ -]?id|aws[._ -]?session[._ -]?token|"
    r"x[._ -]?amz[._ -]?(?:credential|signature|security[._ -]?token)|"
    r"github[._ -]?token|gitlab[._ -]?token|private[._ -]?token|"
    r"oauth[._ -]?token|proxy[._ -]?authorization|authorization|"
    r"password|passwd|secret|access[._ -]?token|refresh[._ -]?token|"
    r"id[._ -]?token|auth[._ -]?token|bearer[._ -]?token|"
    r"session[._ -]?token|csrf[._ -]?token|api[._ -]?key|apikey|"
    r"x[._ -]?api[._ -]?key|app[._ -]?secret|client[._ -]?secret|"
    r"credential(?:s)?|token)"
    r")"
    r"(?P<separator>\s*[\"']?\s*[:=]\s*)"
    r"(?:[bB])?"
    r"(?:(?P<quote>[\"'])(?P<quoted>.*?)(?P=quote)|"
    r"(?P<plain>[^\s,;\"'}&]+))"
)
_BEARER_VALUE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_HEADER_ASSIGNMENT = re.compile(
    r"(?ix)"
    r"(?P<prefix>[\"']?(?:"
    r"(?:proxy[._ -]?)?authorization|(?:set[._ -]?)?cookie|"
    r"x[._ -]?api[._ -]?key|api[._ -]?key|x[._ -]?auth[._ -]?token|"
    r"x[._ -]?amz[._ -]?security[._ -]?token|"
    r"(?:request[._ -]?|response[._ -]?)?headers?"
    r")[\"']?\s*[:=]\s*)"
    r"(?:[bB])?"
    r"(?:(?P<quote>[\"'])(?P<quoted>.*?)(?P=quote)|"
    r"(?P<plain>[^\r\n|}]+))"
)
_QUERY_VALUE = re.compile(
    r"(?i)([?&](?:key|access[_-]?token|refresh[_-]?token|password|passwd|"
    r"api[_-]?key|apikey|x[_-]?api[_-]?key|app[_-]?secret|client[_-]?secret|"
    r"secret|token|x[_-]?amz[_-]?(?:credential|signature|security[_-]?token)|"
    r"github[_-]?token|gitlab[_-]?token|private[_-]?token|oauth[_-]?token)=)"
    r"[^&#\s]+"
)
_EMAIL_VALUE = re.compile(
    r"(?i)(?<![A-Z0-9._%+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![A-Z0-9._%+-])"
)
# A slash may delimit digit groups (``20/7946-0958``), but a whitespace-
# surrounded slash commonly separates several values in one diagnostic.  Do
# not let one phone match consume the next phone through that delimiter.
_PHONE_SEPARATOR = r"(?:[ .()\-]*|(?<!\s)/(?!\s))"
_CN_MOBILE_VALUE = re.compile(
    rf"(?<!\d)(?:(?:(?:\+|00)\s*86|86){_PHONE_SEPARATOR})?"
    rf"1[3-9](?:{_PHONE_SEPARATOR}\d){{9}}(?!\d)"
)
_INTERNATIONAL_PHONE_VALUE = re.compile(
    rf"(?<![\w])(?:\+|00){_PHONE_SEPARATOR}[1-9]\d{{0,2}}"
    rf"(?:{_PHONE_SEPARATOR}\d){{6,14}}(?![\w])"
)
_CN_LANDLINE_VALUE = re.compile(
    rf"(?<!\d)(?:(?:(?:\+|00)\s*86|86){_PHONE_SEPARATOR})?"
    rf"(?:\(?0\d{{2,3}}\)?|\(?\d{{2,3}}\)?)"
    rf"{_PHONE_SEPARATOR}[2-9](?:{_PHONE_SEPARATOR}\d){{6,7}}"
    rf"(?:{_PHONE_SEPARATOR}(?:ext\.?|x|\u8f6c){_PHONE_SEPARATOR}\d{{1,6}})?"
    r"(?!\d)",
    re.I,
)
_SLASHED_PHONE_CANDIDATE = re.compile(
    r"(?<![\w])\+?\d[\d ()\-]*(?:\s*/\s*\d[\d ()\-]*)+(?![\w])"
)
_PRC_ID_VALUE = re.compile(
    r"(?i)(?<![0-9A-Z])(?:\d{17}[0-9X]|\d{15})(?![0-9A-Z])"
)
_TOKEN_SHAPE = re.compile(
    r"(?<![A-Za-z0-9])(?:sk-[A-Za-z0-9_-]{8,}|gh[opusr]_[A-Za-z0-9]{8,}|"
    r"glpat-[A-Za-z0-9_-]{8,}|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|"
    r"(?:eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}))(?![A-Za-z0-9])"
)
_DIAGNOSTIC_KEY = re.compile(
    r"(?i)(?:^|[_-])(?:error|errors|exception|exceptions|trace|traces|"
    r"diagnostic|diagnostics|failure|failures)(?:$|[_-])"
)
_RAW_DIAGNOSTIC_KEY = re.compile(
    r"(?i)^(?:raw_(?:completion|response|output|prompt)|"
    r"(?:first|repair|model|llm|provider)_(?:completion|response|output)|"
    r"prompt|request_payload|response_payload)$"
)
_PROTECTED_BUSINESS_KEYS = frozenset(
    {"public_payload", "liepin_payload", "liepin_payload_json", "payload_hash"}
)
_ABSOLUTE_URL = re.compile(r"(?i)https?://[^\s<>\"'\])}]+")
_ERROR_CLASS_NAME = re.compile(
    r"[A-Z][A-Za-z0-9_.-]{0,79}(?:Error|Exception|Failure|Warning|"
    r"Unavailable|Timeout|Denied|Rejected|Invalid|NotFound|Aborted|Status)"
)
_OPAQUE_FIELD = re.compile(
    r"(?i)(?:^|_)(?:id|ids|hash|sha(?:1|256|512)?|digest|fingerprint|"
    r"version|date|timestamp|time|at|count|status)(?:$|_)"
)
_STRUCTURED_TEXT_FIELD = re.compile(
    r"(?i)^(?:error_class|model_identity|generation_model|provider|processor|"
    r"source_id|source_article_id|adapter_id|delivery_channel|event_type|"
    r"phase|mode|reason_code|code)$"
)
_URL_FIELD = re.compile(
    r"(?i)(?:^|_)(?:url|urls|uri|uris|link|links)$"
)


def _normalized_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().casefold()).strip("_")


def _is_sensitive_key(value: object) -> bool:
    key = _normalized_key(value)
    if not key:
        return False
    # Operational metadata derived from a credential (for example
    # ``token_count`` or ``credential_hash``) is not itself a credential.
    # Check the terminal namespace segment before broad secret-name matching.
    if key.rsplit("_", 1)[-1] in _NON_SECRET_KEY_SUFFIXES:
        return False
    if key in _EXACT_SENSITIVE_KEYS:
        return True
    if _SENSITIVE_KEY_COMPONENT.search(key) or "secret_access_key" in key:
        return True
    compact = key.replace("_", "")
    if compact.endswith(_COMPACT_SENSITIVE_SUFFIXES):
        return True
    if key.endswith(
        (
            "_app_secret",
            "_client_secret",
            "_api_key",
            "_apikey",
            "_secret",
            "_token",
            "_cookie",
            "_cookies",
        )
    ):
        return True
    segments = key.split("_")
    return bool(segments and segments[-1] in {"secret", "token", "cookie", "cookies"})


def _is_sensitive_url_query_key(value: object) -> bool:
    key = _normalized_key(value)
    compact = key.replace("_", "")
    return (
        key in _SENSITIVE_URL_QUERY_KEYS
        or _is_sensitive_key(key)
        or key.endswith(("_signature", "_security_token", "_credential"))
        or compact.endswith(("accesskeyid", "secretaccesskey"))
    )


def sanitize_url(value: object, *, limit: int | None = None) -> str:
    """Remove URL credentials, sensitive parameters and fragments.

    This helper belongs at persistence/reporting boundaries.  Callers must keep
    using the original URL for the HTTP request itself so signed or tokenized
    endpoints are not broken before collection.
    """

    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
    except ValueError:
        safe = _QUERY_VALUE.sub(r"\1[redacted]", text)
        return safe if limit is None else safe[:limit]
    if parsed.scheme.casefold() not in {"http", "https"}:
        safe = _QUERY_VALUE.sub(r"\1[redacted]", text)
        return safe if limit is None else safe[:limit]

    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    try:
        port = parsed.port
    except ValueError:
        port = None
    netloc = hostname
    if port is not None:
        netloc = f"{netloc}:{port}"
    query = urlencode(
        [
            (
                key,
                _redact_pii(
                    _TOKEN_SHAPE.sub(
                        "[redacted-token]",
                        _BEARER_VALUE.sub("Bearer [redacted]", item),
                    )
                ),
            )
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if not _is_sensitive_url_query_key(key)
        ],
        doseq=True,
        quote_via=quote,
        safe="/:@!$'()*+,;=-._~",
    )
    safe = urlunsplit((parsed.scheme.casefold(), netloc, parsed.path, query, ""))
    return safe if limit is None else safe[:limit]


def _sanitize_embedded_urls(value: str) -> str:
    return _ABSOLUTE_URL.sub(lambda match: sanitize_url(match.group(0)), value)


def _looks_like_opaque_token(value: str) -> bool:
    text = value.strip()
    if _ERROR_CLASS_NAME.fullmatch(text):
        return False
    if _TOKEN_SHAPE.fullmatch(text):
        return True
    if not re.fullmatch(r"[A-Za-z0-9._~+/=-]{16,}", text):
        return False
    if re.search(r"(?:Error|Exception|Failure|Warning)$", text):
        return False
    return True


def _redact_header_assignments(text: str) -> str:
    return _HEADER_ASSIGNMENT.sub(
        lambda match: f"{match.group('prefix')}[redacted]",
        text,
    )


def _redact_sensitive_assignments(text: str) -> str:
    return _SENSITIVE_ASSIGNMENT.sub(
        lambda match: (
            f"{match.group('key')}{match.group('separator')}[redacted]"
        ),
        text,
    )


def _redact_pii(text: str) -> str:
    def redact_slashed(match: re.Match[str]) -> str:
        raw = match.group(0)
        digits = re.sub(r"\D", "", raw)
        phone_prefix = (
            raw.lstrip().startswith("+")
            or digits.startswith("00")
            or (len(digits) >= 10 and digits.startswith("0"))
            or (
                len(digits) >= 10
                and len(digits) > 1
                and digits[0] == "1"
                and digits[1] in "3456789"
            )
        )
        return "[redacted-phone]" if len(digits) >= 7 and phone_prefix else raw

    text = _SLASHED_PHONE_CANDIDATE.sub(redact_slashed, text)
    text = _INTERNATIONAL_PHONE_VALUE.sub("[redacted-phone]", text)
    text = _CN_MOBILE_VALUE.sub("[redacted-phone]", text)
    text = _CN_LANDLINE_VALUE.sub("[redacted-phone]", text)
    text = _PRC_ID_VALUE.sub("[redacted-id]", text)
    return _EMAIL_VALUE.sub("[redacted-email]", text)


def safe_error_class(error: object) -> str:
    if isinstance(error, BaseException):
        return type(error).__name__[:80]
    text = str(error or "").strip()
    candidate = text.split(":", 1)[0].strip()
    if _ERROR_CLASS_NAME.fullmatch(candidate):
        return candidate
    if _looks_like_opaque_token(text):
        return "OperationalError"
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,79}", candidate):
        return candidate
    return "OperationalError"


def sanitize_text(value: object, *, limit: int = 240) -> str:
    if isinstance(value, (bytes, bytearray, memoryview)):
        text = bytes(value).decode("utf-8", errors="replace")
    else:
        text = str(value or "")
    if _looks_like_opaque_token(text.strip()) and not _SENSITIVE_ASSIGNMENT.search(text):
        return "[redacted-token]"[:limit]
    # Header-shaped credentials need to be removed before line breaks are
    # normalized.  The generic key/value rule would otherwise redact only the
    # word ``Basic`` from ``Authorization: Basic <base64>`` and leave the
    # actual credential behind.  Cookie values may contain several secret
    # pairs, so the whole header value is operationally unsafe.
    text = _redact_header_assignments(text)
    text = text.replace("\r", " ").replace("\n", " ").strip()
    # Redact a complete bearer credential before the generic key/value rule.
    # Otherwise ``Authorization: Bearer <token>`` would redact only the word
    # ``Bearer`` and leave the credential behind.
    text = _BEARER_VALUE.sub("Bearer [redacted]", text)
    text = _QUERY_VALUE.sub(r"\1[redacted]", text)
    text = _redact_sensitive_assignments(text)
    text = _redact_pii(text)
    text = _TOKEN_SHAPE.sub("[redacted-token]", text)
    text = _sanitize_embedded_urls(text)
    return text[:limit]


def _redact_preserving_text(
    value: str, *, limit: int | None = None, redact_pii: bool = False
) -> str:
    """Redact secrets without normalizing business-content whitespace."""

    text = _redact_header_assignments(value)
    text = _BEARER_VALUE.sub("Bearer [redacted]", text)
    text = _QUERY_VALUE.sub(r"\1[redacted]", text)
    text = _redact_sensitive_assignments(text)
    text = _TOKEN_SHAPE.sub("[redacted-token]", text)
    text = _sanitize_embedded_urls(text)
    if redact_pii:
        text = _redact_pii(text)
    return text if limit is None else text[:limit]


def safe_error(error: object) -> dict[str, str]:
    """Return class plus bounded/redacted diagnostic text."""

    return {
        "error_class": safe_error_class(error),
        "detail": sanitize_text(error),
    }


def sanitize_tree(
    value: Any,
    *,
    string_limit: int | None = None,
    redact_pii: bool = False,
    _redact_opaque_tokens: bool = False,
) -> Any:
    """Recursively redact secret-shaped keys and values before persistence/output."""

    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            normalized_key = _normalized_key(key)
            if normalized_key in _PROTECTED_BUSINESS_KEYS:
                output[key] = raw_value
            elif _is_sensitive_key(key):
                output[key] = "[redacted]"
            elif _RAW_DIAGNOSTIC_KEY.fullmatch(normalized_key):
                output[key] = "[redacted]"
            elif (
                redact_pii
                and _OPAQUE_FIELD.search(_normalized_key(key))
                and isinstance(raw_value, (str, int, float, list, tuple))
            ):
                output[key] = raw_value
            elif (
                redact_pii
                and _STRUCTURED_TEXT_FIELD.fullmatch(_normalized_key(key))
                and isinstance(raw_value, str)
            ):
                output[key] = _redact_preserving_text(
                    raw_value,
                    redact_pii=False,
                )
            elif _URL_FIELD.search(normalized_key):
                # URL path segments often contain numeric article IDs that
                # resemble phone numbers (for example ``2026-08-12-7``).
                # Generic PII regexes must not corrupt those stable identities.
                # ``sanitize_url`` still removes credentials, sensitive query
                # parameters, and fragments at the persistence boundary.
                if isinstance(raw_value, str):
                    output[key] = sanitize_url(raw_value, limit=string_limit)
                elif isinstance(raw_value, (list, tuple)):
                    output[key] = [
                        sanitize_url(item, limit=string_limit)
                        if isinstance(item, str)
                        else sanitize_tree(
                            item,
                            string_limit=string_limit,
                            redact_pii=redact_pii,
                            _redact_opaque_tokens=_redact_opaque_tokens,
                        )
                        for item in raw_value
                    ]
                else:
                    output[key] = sanitize_tree(
                        raw_value,
                        string_limit=string_limit,
                        redact_pii=redact_pii,
                        _redact_opaque_tokens=_redact_opaque_tokens,
                    )
            elif _DIAGNOSTIC_KEY.search(normalized_key):
                output[key] = sanitize_tree(
                    raw_value,
                    string_limit=string_limit,
                    redact_pii=True,
                    _redact_opaque_tokens=True,
                )
            else:
                output[key] = sanitize_tree(
                    raw_value,
                    string_limit=string_limit,
                    redact_pii=redact_pii,
                    _redact_opaque_tokens=_redact_opaque_tokens,
                )
        return output
    if isinstance(value, (list, tuple)):
        return [
            sanitize_tree(
                item,
                string_limit=string_limit,
                redact_pii=redact_pii,
                _redact_opaque_tokens=_redact_opaque_tokens,
            )
            for item in value
        ]
    if isinstance(value, (bytes, bytearray, memoryview)):
        text = bytes(value).decode("utf-8", errors="replace")
        if _redact_opaque_tokens and _looks_like_opaque_token(text.strip()):
            return "[redacted-token]"
        return _redact_preserving_text(
            text,
            limit=string_limit,
            redact_pii=redact_pii,
        )
    if isinstance(value, str):
        if _redact_opaque_tokens and _looks_like_opaque_token(value.strip()):
            return "[redacted-token]"
        return _redact_preserving_text(
            value,
            limit=string_limit,
            redact_pii=redact_pii,
        )
    return value


def sanitize_diagnostic_fields(value: Any) -> Any:
    """Redact only diagnostic subtrees while preserving business payload text.

    Pipeline checkpoints intentionally contain both business artifacts (whose
    exact text and hashes must not change) and operational error/trace fields.
    A key-aware traversal gives those two data classes different treatment.
    """

    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            normalized_key = _normalized_key(key)
            if normalized_key in _PROTECTED_BUSINESS_KEYS:
                output[key] = raw_value
            elif _is_sensitive_key(key):
                output[key] = "[redacted]"
            elif _RAW_DIAGNOSTIC_KEY.fullmatch(normalized_key):
                output[key] = "[redacted]"
            elif _DIAGNOSTIC_KEY.search(normalized_key):
                output[key] = sanitize_tree(
                    raw_value,
                    string_limit=4000,
                    redact_pii=True,
                    _redact_opaque_tokens=True,
                )
            else:
                output[key] = sanitize_diagnostic_fields(raw_value)
        return output
    if isinstance(value, list):
        return [sanitize_diagnostic_fields(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_diagnostic_fields(item) for item in value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return _redact_preserving_text(
            bytes(value).decode("utf-8", errors="replace"),
            redact_pii=False,
        )
    if isinstance(value, str):
        return _redact_preserving_text(value, redact_pii=False)
    return value


__all__ = [
    "safe_error",
    "safe_error_class",
    "sanitize_diagnostic_fields",
    "sanitize_text",
    "sanitize_tree",
    "sanitize_url",
]
