from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from .utils import exact_json_bytes, response_json, sanitize, validate_base_url


@dataclass
class ApiResult:
    timestamp: datetime
    method: str
    url: str
    request_body: Any
    status_code: int | None
    response_body: Any
    duration_ms: float
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status_code is not None and 200 <= self.status_code < 300


class ActionLimitExceeded(RuntimeError):
    pass


class ApiClient:
    """Bounded client that accepts only relative paths under one validated origin."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        max_actions: int = 100,
        allow_nonlocal: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = validate_base_url(base_url, allow_nonlocal)
        self.max_actions = max_actions
        self.action_count = 0
        self.history: list[ApiResult] = []
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
            headers={"User-Agent": "p2p-qa/0.1"},
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "ApiClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _safe_url(self, path: str) -> str:
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("API request path must be root-relative")
        target = urljoin(self.base_url + "/", path.lstrip("/"))
        expected = urlparse(self.base_url)
        actual = urlparse(target)
        if (actual.scheme, actual.hostname, actual.port) != (
            expected.scheme,
            expected.hostname,
            expected.port,
        ):
            raise ValueError("request escaped configured base URL")
        return target

    def request(self, method: str, path: str, *, json: Any = None) -> ApiResult:
        if self.action_count >= self.max_actions:
            raise ActionLimitExceeded(f"maximum action count {self.max_actions} reached")
        target = self._safe_url(path)
        self.action_count += 1
        started_at = datetime.now(timezone.utc)
        before = time.perf_counter()
        try:
            kwargs = {}
            if json is not None:
                kwargs = {
                    "content": exact_json_bytes(json),
                    "headers": {"Content-Type": "application/json"},
                }
            response = self._http.request(method.upper(), path, **kwargs)
            result = ApiResult(
                timestamp=started_at,
                method=method.upper(),
                url=target,
                request_body=sanitize(json),
                status_code=response.status_code,
                response_body=sanitize(response_json(response)),
                duration_ms=round((time.perf_counter() - before) * 1000, 3),
            )
        except httpx.HTTPError as exc:
            result = ApiResult(
                timestamp=started_at,
                method=method.upper(),
                url=target,
                request_body=sanitize(json),
                status_code=None,
                response_body=None,
                duration_ms=round((time.perf_counter() - before) * 1000, 3),
                error=f"{type(exc).__name__}: {exc}",
            )
        self.history.append(result)
        return result
