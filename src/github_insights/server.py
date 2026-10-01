"""MCP server: GitHub repository insights as tools for AI assistants."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from mcp.server.mcpserver import MCPServer

from .github_client import GitHubClient, parse_github_dt

mcp = MCPServer("github-insights")

__all__ = ["mcp", "main"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age_days(iso: str) -> int:
    return (_now() - parse_github_dt(iso)).days


@mcp.tool()
async def repo_overview(owner: str, repo: str) -> dict[str, Any]:
    """High-level overview of a GitHub repository.

    Returns stars, forks, open issue count, license, topics, language
    breakdown, and how long ago it was last pushed to.
    """
    async with GitHubClient() as client:
        data = await client.get_repo(owner, repo)
        languages = await client.get_languages(owner, repo)
    license_info = data.get("license") or {}
    return {
        "full_name": data["full_name"],
        "description": data.get("description"),
        "url": data["html_url"],
        "stars": data["stargazers_count"],
        "forks": data["forks_count"],
        "watchers": data.get("subscribers_count", 0),
        "open_issues": data["open_issues_count"],
        "default_branch": data["default_branch"],
        "license": license_info.get("spdx_id"),
        "topics": data.get("topics", []),
        "archived": data["archived"],
        "private": data["private"],
        "created_at": data["created_at"],
        "pushed_at": data["pushed_at"],
        "days_since_push": _age_days(data["pushed_at"]),
        "primary_language": data.get("language"),
        "languages": languages,
    }


@mcp.tool()
async def commit_activity(owner: str, repo: str, days: int = 30) -> dict[str, Any]:
    """Commit activity for a repo over the last N days.

    Returns total commits, commits bucketed per ISO week, top authors,
    and the latest commit.
    """
    if days < 1 or days > 365:
        raise ValueError("days must be between 1 and 365")
    since = _now() - timedelta(days=days)
    async with GitHubClient() as client:
        commits = await client.list_commits(owner, repo, since=since)

    per_week: Counter[str] = Counter()
    authors: Counter[str] = Counter()
    for c in commits:
        dt = parse_github_dt(c["commit"]["author"]["date"])
        iso_year, iso_week, _ = dt.isocalendar()
        per_week[f"{iso_year}-W{iso_week:02d}"] += 1
        login = (c.get("author") or {}).get("login") or c["commit"]["author"]["name"]
        authors[login] += 1

    latest = None
    if commits:
        head = commits[0]
        latest = {
            "sha": head["sha"][:7],
            "message": head["commit"]["message"].splitlines()[0][:120],
            "author": (head.get("author") or {}).get("login")
            or head["commit"]["author"]["name"],
            "date": head["commit"]["author"]["date"],
            "url": head["html_url"],
        }

    return {
        "repo": f"{owner}/{repo}",
        "days": days,
        "total_commits": len(commits),
        "commits_per_week": dict(sorted(per_week.items())),
        "top_authors": authors.most_common(5),
        "latest_commit": latest,
    }


@mcp.tool()
async def stale_pull_requests(
    owner: str, repo: str, days: int = 14
) -> dict[str, Any]:
    """Open pull requests that have been waiting longer than N days.

    Useful for spotting review bottlenecks. Sorted oldest first.
    """
    if days < 1:
        raise ValueError("days must be positive")
    async with GitHubClient() as client:
        prs = await client.list_open_pull_requests(owner, repo)

    stale = []
    for pr in prs:
        age = _age_days(pr["created_at"])
        if age >= days:
            stale.append(
                {
                    "number": pr["number"],
                    "title": pr["title"],
                    "author": pr["user"]["login"],
                    "age_days": age,
                    "draft": pr.get("draft", False),
                    "labels": [label["name"] for label in pr.get("labels", [])],
                    "url": pr["html_url"],
                }
            )
    stale.sort(key=lambda p: p["age_days"], reverse=True)
    return {
        "repo": f"{owner}/{repo}",
        "threshold_days": days,
        "open_prs": len(prs),
        "stale_count": len(stale),
        "stale_prs": stale,
    }


@mcp.tool()
async def contributor_stats(
    owner: str, repo: str, days: int = 90, top_n: int = 10
) -> dict[str, Any]:
    """Top contributors by commit count over the last N days."""
    if days < 1 or days > 365:
        raise ValueError("days must be between 1 and 365")
    if top_n < 1 or top_n > 100:
        raise ValueError("top_n must be between 1 and 100")
    since = _now() - timedelta(days=days)
    async with GitHubClient() as client:
        commits = await client.list_commits(owner, repo, since=since, max_pages=10)

    counts: Counter[str] = Counter()
    for c in commits:
        login = (c.get("author") or {}).get("login") or c["commit"]["author"]["name"]
        counts[login] += 1

    ranked = [
        {"login": login, "commits": n}
        for login, n in counts.most_common(top_n)
    ]
    return {
        "repo": f"{owner}/{repo}",
        "days": days,
        "total_commits": len(commits),
        "contributors": ranked,
    }


@mcp.tool()
async def recent_releases(owner: str, repo: str, limit: int = 5) -> dict[str, Any]:
    """Most recent releases for a repo, newest first."""
    if limit < 1 or limit > 20:
        raise ValueError("limit must be between 1 and 20")
    async with GitHubClient() as client:
        releases = await client.list_releases(owner, repo, limit=limit)
    items = []
    for r in releases:
        published = r.get("published_at")
        items.append(
            {
                "tag": r["tag_name"],
                "name": r.get("name"),
                "published_at": published,
                "age_days": _age_days(published) if published else None,
                "draft": r["draft"],
                "prerelease": r["prerelease"],
                "url": r["html_url"],
            }
        )
    return {"repo": f"{owner}/{repo}", "releases": items}


@mcp.tool()
async def issue_backlog(owner: str, repo: str) -> dict[str, Any]:
    """Open issue backlog grouped by label, with the oldest issues surfaced."""
    async with GitHubClient() as client:
        issues = await client.list_open_issues(owner, repo)

    by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unlabeled = 0
    oldest: list[dict[str, Any]] = []
    for issue in issues:
        entry = {
            "number": issue["number"],
            "title": issue["title"],
            "age_days": _age_days(issue["created_at"]),
            "labels": [label["name"] for label in issue.get("labels", [])],
            "url": issue["html_url"],
        }
        oldest.append(entry)
        if entry["labels"]:
            for label in entry["labels"]:
                by_label[label].append(entry)
        else:
            unlabeled += 1

    oldest.sort(key=lambda i: i["age_days"], reverse=True)
    return {
        "repo": f"{owner}/{repo}",
        "open_issues": len(issues),
        "unlabeled": unlabeled,
        "by_label": {label: len(items) for label, items in sorted(by_label.items())},
        "oldest_issues": oldest[:5],
    }


def main() -> None:
    """Run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
