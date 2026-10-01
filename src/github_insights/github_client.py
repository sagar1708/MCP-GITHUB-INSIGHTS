"""Async GitHub REST client used by the MCP tools."""

from __future__ import annotations

import ipaddress
import os
import ssl
from datetime import datetime, timezone
from typing import Any

import httpx

API_BASE = "https://api.github.com"
USER_AGENT = "mcp-github-insights/0.1.0"


class GitHubError(Exception):
    """Raised when the GitHub API returns an error response."""


def parse_github_dt(value: str) -> datetime:
    """Parse a GitHub ISO-8601 timestamp into an aware datetime."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _no_proxy_mount_key(hostname: str) -> str | None:
    """Reproduce the mount key httpx derives from a ``no_proxy`` entry.

    Mirrors ``httpx._utils.get_environment_proxies`` so we can validate keys
    with the public ``httpx.URL`` API. Returns ``None`` for ``"*"`` (which
    disables all proxies and is always kept).
    """
    if hostname == "*":
        return None
    if "://" in hostname:
        return hostname
    try:
        ipaddress.IPv4Address(hostname)
        return f"all://{hostname}"
    except ValueError:
        pass
    try:
        ipaddress.IPv6Address(hostname)
        return f"all://[{hostname}]"
    except ValueError:
        pass
    if hostname.lower() == "localhost":
        return f"all://{hostname}"
    return f"all://*{hostname}"


def _sanitize_no_proxy_env() -> None:
    """Drop ``no_proxy`` entries that crash httpx client construction.

    Some container runtimes ship bracketed IPv6 literals (e.g. ``[::1]``) in
    ``no_proxy``/``NO_PROXY``. httpx turns those into mount keys like
    ``all://*[::1]``, which its URL parser rejects with ``InvalidURL``,
    crashing ``httpx.AsyncClient(...)``. Only entries whose derived mount
    key httpx itself cannot parse are removed.
    """
    for var in ("no_proxy", "NO_PROXY"):
        raw = os.environ.get(var)
        if not raw:
            continue
        keep: list[str] = []
        for entry in raw.split(","):
            entry = entry.strip()
            if not entry:
                continue
            key = _no_proxy_mount_key(entry)
            if key is None:
                keep.append(entry)
                continue
            try:
                httpx.URL(key)
            except Exception:
                continue
            keep.append(entry)
        os.environ[var] = ",".join(keep)


class GitHubClient:
    """Minimal async wrapper around the GitHub REST API.

    Uses ``GITHUB_TOKEN`` from the environment when present (5,000 req/hr);
    without it, requests are unauthenticated (60 req/hr).
    """

    def __init__(
        self,
        token: str | None = None,
        timeout: float = 30.0,
        verify: bool | str | ssl.SSLContext = True,
    ) -> None:
        self._token = token if token is not None else os.environ.get("GITHUB_TOKEN")
        self._timeout = timeout
        self._verify = verify
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> GitHubClient:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        _sanitize_no_proxy_env()
        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            headers=headers,
            timeout=self._timeout,
            verify=self._verify,
        )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        assert self._client is not None, "use 'async with GitHubClient()'"
        resp = await self._client.get(path, params=params)
        if resp.status_code == 404:
            raise GitHubError(f"Not found: {path}")
        if resp.status_code == 403 and resp.headers.get("X-RateLimit-Remaining") == "0":
            reset = resp.headers.get("X-RateLimit-Reset", "?")
            raise GitHubError(
                f"GitHub API rate limit exceeded (resets at unix ts {reset}). "
                "Set GITHUB_TOKEN to raise the limit."
            )
        if resp.status_code >= 400:
            raise GitHubError(f"GitHub API error {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    async def _paginate(
        self, path: str, params: dict[str, Any] | None = None, max_pages: int = 10
    ) -> list[Any]:
        items: list[Any] = []
        page_params = dict(params or {})
        for page in range(1, max_pages + 1):
            page_params["page"] = page
            page_params["per_page"] = 100
            batch = await self._get(path, params=page_params)
            if not isinstance(batch, list) or not batch:
                break
            items.extend(batch)
            if len(batch) < 100:
                break
        return items

    async def get_repo(self, owner: str, repo: str) -> dict[str, Any]:
        return await self._get(f"/repos/{owner}/{repo}")

    async def get_languages(self, owner: str, repo: str) -> dict[str, int]:
        return await self._get(f"/repos/{owner}/{repo}/languages")

    async def list_commits(
        self, owner: str, repo: str, since: datetime | None = None, max_pages: int = 5
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if since is not None:
            params["since"] = since.astimezone(timezone.utc).isoformat()
        return await self._paginate(
            f"/repos/{owner}/{repo}/commits", params=params, max_pages=max_pages
        )

    async def list_open_pull_requests(
        self, owner: str, repo: str
    ) -> list[dict[str, Any]]:
        return await self._paginate(
            f"/repos/{owner}/{repo}/pulls", params={"state": "open"}
        )

    async def list_open_issues(self, owner: str, repo: str) -> list[dict[str, Any]]:
        issues = await self._paginate(
            f"/repos/{owner}/{repo}/issues", params={"state": "open"}
        )
        # The issues endpoint also returns pull requests; keep real issues only.
        return [i for i in issues if "pull_request" not in i]

    async def list_releases(
        self, owner: str, repo: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        releases = await self._paginate(f"/repos/{owner}/{repo}/releases", max_pages=2)
        return releases[:limit]
