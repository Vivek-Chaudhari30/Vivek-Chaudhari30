"""Minimal GitHub GraphQL client with retries, rate-limit handling and pagination.

Uses only the standard library so the workflow needs no third-party packages.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Iterator

GRAPHQL_URL = "https://api.github.com/graphql"
USER_AGENT = "profile-stats-generator"

# Never sleep longer than this waiting for a rate limit to reset; fail instead.
MAX_RATE_LIMIT_WAIT = 90


class GitHubError(RuntimeError):
    """Raised when the API cannot return usable data."""


class GitHubClient:
    def __init__(
        self,
        token: str,
        *,
        max_retries: int = 4,
        timeout: float = 30,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not token:
            raise GitHubError("No GitHub token provided (set GITHUB_TOKEN).")
        self._token = token
        self._max_retries = max_retries
        self._timeout = timeout
        self._open = opener
        self._sleep = sleep

    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run a query and return its ``data`` object.

        Partial results (``data`` plus ``errors``) are returned as long as
        ``data`` is present; callers must tolerate null nodes.
        """
        body = json.dumps({"query": query, "variables": variables or {}}).encode()
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            request = urllib.request.Request(
                GRAPHQL_URL,
                data=body,
                method="POST",
                headers={
                    "Authorization": f"bearer {self._token}",
                    "Content-Type": "application/json",
                    "User-Agent": USER_AGENT,
                },
            )
            try:
                with self._open(request, timeout=self._timeout) as response:
                    payload = json.loads(response.read().decode())
            except urllib.error.HTTPError as exc:
                wait = self._retry_delay(exc.code, exc.headers, attempt)
                if wait is None:
                    raise GitHubError(f"GitHub API returned HTTP {exc.code}: {exc.reason}") from exc
                last_error = exc
                self._sleep(wait)
                continue
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                self._sleep(_backoff(attempt))
                continue

            errors = payload.get("errors") or []
            if any(e.get("type") == "RATE_LIMITED" for e in errors):
                last_error = GitHubError("GraphQL rate limit exceeded")
                self._sleep(min(60, _backoff(attempt) * 4))
                continue

            data = payload.get("data")
            if data is None:
                messages = "; ".join(e.get("message", "unknown error") for e in errors)
                raise GitHubError(f"GraphQL query failed: {messages or 'empty response'}")
            return data

        raise GitHubError(f"GitHub API unavailable after {self._max_retries + 1} attempts: {last_error}")

    def paginate(
        self,
        query: str,
        variables: dict[str, Any],
        connection: Callable[[dict[str, Any]], dict[str, Any] | None],
        *,
        max_pages: int = 50,
    ) -> Iterator[dict[str, Any]]:
        """Yield every node of a connection that takes a ``$cursor`` variable."""
        cursor = None
        for _ in range(max_pages):
            data = self.graphql(query, {**variables, "cursor": cursor})
            conn = connection(data)
            if conn is None:
                raise GitHubError("Expected connection missing from response")
            for node in conn.get("nodes") or []:
                if node is not None:
                    yield node
            page = conn.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                return
            cursor = page.get("endCursor")
        raise GitHubError(f"Pagination exceeded {max_pages} pages")

    def _retry_delay(self, status: int, headers: Any, attempt: int) -> float | None:
        if attempt >= self._max_retries:
            return None
        if status in (403, 429):
            retry_after = headers.get("Retry-After") if headers else None
            if retry_after and retry_after.isdigit():
                return min(int(retry_after), MAX_RATE_LIMIT_WAIT)
            if headers and headers.get("X-RateLimit-Remaining") == "0":
                reset = headers.get("X-RateLimit-Reset", "")
                wait = int(reset) - time.time() + 1 if reset.isdigit() else MAX_RATE_LIMIT_WAIT + 1
                return wait if wait <= MAX_RATE_LIMIT_WAIT else None
            # 403 without rate-limit headers is a permissions problem: don't retry.
            return _backoff(attempt) if status == 429 else None
        if status >= 500:
            return _backoff(attempt)
        return None


def _backoff(attempt: int) -> float:
    return float(2**attempt)
