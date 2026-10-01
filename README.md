# mcp-github-insights

An [MCP](https://modelcontextprotocol.io) server that exposes GitHub repository
insights as tools for AI assistants — repo health, commit activity, stale PRs,
contributor stats, releases, and issue backlogs.

Point any MCP client (Claude Desktop, Cursor, VS Code, Claude Code) at it and
ask things like:

- "Give me an overview of `sagar1708/mcp-github-insights`"
- "Which PRs have been stale for more than 2 weeks?"
- "Who are the top contributors in the last 90 days?"

## Tools

| Tool | What it does |
|---|---|
| `repo_overview` | Stars, forks, license, topics, languages, last push |
| `commit_activity` | Commits per week, top authors, latest commit (default 30d) |
| `stale_pull_requests` | Open PRs older than N days, oldest first (default 14d) |
| `contributor_stats` | Top contributors by commit count (default 90d) |
| `recent_releases` | Latest releases, newest first |
| `issue_backlog` | Open issues grouped by label + oldest issues |

## Quickstart

```bash
pip install -e .
```

Set a token for higher rate limits (optional — works without one at 60 req/hr):

```bash
export GITHUB_TOKEN=ghp_...
```

Run the server (stdio transport):

```bash
github-insights-mcp
```

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "github-insights": {
      "command": "github-insights-mcp",
      "env": { "GITHUB_TOKEN": "ghp_..." }
    }
  }
}
```

### Cursor / VS Code

Same shape in your MCP settings:

```json
{
  "mcpServers": {
    "github-insights": {
      "command": "github-insights-mcp",
      "env": { "GITHUB_TOKEN": "ghp_..." }
    }
  }
}
```

## Development

```bash
pip install -e ".[test]"
pytest
```

## Roadmap

- PR review turnaround stats (median time to merge)
- Code churn / hot files from commit patches
- Team dashboards via scheduled digests

## License

MIT
