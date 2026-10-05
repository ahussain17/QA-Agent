from __future__ import annotations

import ipaddress
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable
from urllib.parse import urlparse

SECRET_KEYS = re.compile(
    r"(?i)(authorization|api[-_]?key|token|secret|password|cookie|set-cookie|client_secret)"
)
BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]+")
OPENAI_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")


def validate_base_url(value: str, allow_nonlocal: bool = False) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base URL must be an absolute http(s) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("base URL cannot contain credentials, query, or fragment")
    if parsed.path not in {"", "/"}:
        raise ValueError("base URL must not contain a path")
    host = parsed.hostname.lower().rstrip(".")
    local = host == "localhost"
    try:
        local = local or ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    if not local and not allow_nonlocal:
        raise ValueError("non-loopback targets require --allow-nonlocal")
    return value.rstrip("/")


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): "[REDACTED]" if SECRET_KEYS.search(str(k)) else sanitize(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return OPENAI_KEY.sub("[REDACTED]", BEARER.sub(r"\1[REDACTED]", value))
    return value


def jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def walk(value: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], Any]]:
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk(child, path + (str(key),))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, path + (str(index),))


def find_first(value: Any, aliases: Iterable[str]) -> Any:
    wanted = {a.lower().replace("-", "_") for a in aliases}
    for path, child in walk(value):
        if path and path[-1].lower().replace("-", "_") in wanted and not isinstance(child, (dict, list)):
            return child
    return None


def response_json(response: Any) -> Any:
    try:
        return json.loads(response.text, parse_float=Decimal)
    except Exception:
        text = getattr(response, "text", "")
        return text[:10000]


def stable_json(value: Any) -> str:
    return json.dumps(jsonable(value), sort_keys=True, separators=(",", ":"), default=str)


def exact_json_bytes(value: Any) -> bytes:
    """Encode Decimal as JSON numbers without ever converting through binary float."""
    if value is None:
        return b"null"
    if value is True:
        return b"true"
    if value is False:
        return b"false"
    if isinstance(value, Decimal):
        return format(value, "f").encode("ascii")
    if isinstance(value, int):
        return str(value).encode("ascii")
    if isinstance(value, float):
        raise TypeError("binary floating-point values are forbidden in request bodies")
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False).encode("utf-8")
    if isinstance(value, list):
        return b"[" + b",".join(exact_json_bytes(item) for item in value) + b"]"
    if isinstance(value, tuple):
        return exact_json_bytes(list(value))
    if isinstance(value, dict):
        pieces = []
        for key, item in value.items():
            pieces.append(exact_json_bytes(str(key)) + b":" + exact_json_bytes(item))
        return b"{" + b",".join(pieces) + b"}"
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")
