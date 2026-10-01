"""Tests for the MCP tools (GitHub API is faked; no network)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from github_insights import server
from github_insights.github_client import GitHubClient


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _commit(login: str, days_ago: int, message: str = "fix stuff") -> dict:
    return {
        "sha": "abc123def456",
        "html_url": "https://github.com/o/r/commit/abc123def456",
        "commit": {
            "author": {"name": login, "date": _iso(days_ago)},
            "message": message,
        },
        "author": {"login": login},
    }


class FakeClient:
    """In-memory stand-in for GitHubClient."""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def get_repo(self, owner, repo):
        return {
            "full_name": f"{owner}/{repo}",
            "description": "demo",
            "html_url": f"https://github.com/{owner}/{repo}",
            "stargazers_count": 42,
            "forks_count": 7,
            "subscribers_count": 3,
            "open_issues_count": 5,
            "default_branch": "main",
            "license": {"spdx_id": "MIT"},
            "topics": ["mcp", "ai"],
            "archived": False,
            "private": False,
            "created_at": _iso(400),
            "pushed_at": _iso(2),
            "language": "Python",
        }

    async def get_languages(self, owner, repo):
        return {"Python": 9000, "Shell": 500}

    async def list_commits(self, owner, repo, since=None, max_pages=5):
        return [
            _commit("alice", 1),
            _commit("bob", 3),
            _commit("alice", 10),
        ]

    async def list_open_pull_requests(self, owner, repo):
        return [
            {
                "number": 1,
                "title": "old pr",
                "user": {"login": "alice"},
                "created_at": _iso(30),
                "draft": False,
                "labels": [{"name": "bug"}],
                "html_url": "https://github.com/o/r/pull/1",
            },
            {
                "number": 2,
                "title": "fresh pr",
                "user": {"login": "bob"},
                "created_at": _iso(2),
                "draft": True,
                "labels": [],
                "html_url": "https://github.com/o/r/pull/2",
            },
        ]

    async def list_open_issues(self, owner, repo):
        return [
            {
                "number": 10,
                "title": "labeled issue",
                "created_at": _iso(20),
                "labels": [{"name": "enhancement"}],
                "html_url": "https://github.com/o/r/issues/10",
            },
            {
                "number": 11,
                "title": "plain issue",
                "created_at": _iso(60),
                "labels": [],
                "html_url": "https://github.com/o/r/issues/11",
            },
        ]

    async def list_releases(self, owner, repo, limit=5):
        return [
            {
                "tag_name": "v0.2.0",
                "name": "Second",
                "published_at": _iso(5),
                "draft": False,
                "prerelease": False,
                "html_url": "https://github.com/o/r/releases/tag/v0.2.0",
            },
            {
                "tag_name": "v0.1.0",
                "name": "First",
                "published_at": _iso(40),
                "draft": False,
                "prerelease": False,
                "html_url": "https://github.com/o/r/releases/tag/v0.1.0",
            },
        ][:limit]


@pytest.fixture(autouse=True)
def fake_client(monkeypatch):
    monkeypatch.setattr(server, "GitHubClient", FakeClient)


async def test_repo_overview():
    out = await server.repo_overview("o", "r")
    assert out["full_name"] == "o/r"
    assert out["stars"] == 42
    assert out["license"] == "MIT"
    assert out["languages"] == {"Python": 9000, "Shell": 500}
    assert out["days_since_push"] == 2


async def test_commit_activity_buckets_and_authors():
    out = await server.commit_activity("o", "r", days=30)
    assert out["total_commits"] == 3
    assert sum(out["commits_per_week"].values()) == 3
    assert out["top_authors"][0] == ("alice", 2)
    assert out["latest_commit"]["author"] == "alice"


async def test_commit_activity_rejects_bad_days():
    with pytest.raises(ValueError):
        await server.commit_activity("o", "r", days=0)


async def test_stale_pull_requests_filters_by_age():
    out = await server.stale_pull_requests("o", "r", days=14)
    assert out["open_prs"] == 2
    assert out["stale_count"] == 1
    assert out["stale_prs"][0]["number"] == 1
    assert out["stale_prs"][0]["labels"] == ["bug"]


async def test_contributor_stats_ranks_committers():
    out = await server.contributor_stats("o", "r", days=90, top_n=10)
    assert out["total_commits"] == 3
    assert out["contributors"][0] == {"login": "alice", "commits": 2}


async def test_recent_releases_newest_first():
    out = await server.recent_releases("o", "r", limit=5)
    assert [r["tag"] for r in out["releases"]] == ["v0.2.0", "v0.1.0"]
    assert out["releases"][0]["age_days"] == 5


async def test_issue_backlog_groups_by_label():
    out = await server.issue_backlog("o", "r")
    assert out["open_issues"] == 2
    assert out["unlabeled"] == 1
    assert out["by_label"] == {"enhancement": 1}
    # oldest first
    assert out["oldest_issues"][0]["number"] == 11


class PagingClient(GitHubClient):
    """Feeds canned pages through _paginate without network."""

    def __init__(self, pages):
        super().__init__(token="fake")
        self._pages = pages

    async def _get(self, path, params=None):
        page = (params or {}).get("page", 1)
        return self._pages[page - 1] if page - 1 < len(self._pages) else []


async def test_paginate_aggregates_pages():
    client = PagingClient(pages=[[1, 2], []])
    assert await client._paginate("/x") == [1, 2]


async def test_paginate_stops_on_short_page():
    client = PagingClient(pages=[[1, 2, 3]])
    assert await client._paginate("/x") == [1, 2, 3]
