"""Collect public profile statistics from the GitHub GraphQL API.

Only public data is counted. Every query either asks for PUBLIC objects or
filters out anything with ``isPrivate``, so results are the same whether the
token belongs to the profile owner (local runs) or is the workflow's
``GITHUB_TOKEN``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .github_api import GitHubClient, GitHubError

PROFILE_QUERY = """
query($login: String!) {
  user(login: $login) {
    login
    name
    createdAt
    followers { totalCount }
    contributionsCollection { contributionYears }
    repositoriesContributedTo(
      first: 1
      privacy: PUBLIC
      includeUserRepositories: false
      contributionTypes: [COMMIT, PULL_REQUEST, ISSUE, PULL_REQUEST_REVIEW]
    ) { totalCount }
  }
}
"""

REPOS_QUERY = """
query($login: String!, $cursor: String) {
  user(login: $login) {
    repositories(
      first: 50
      after: $cursor
      privacy: PUBLIC
      ownerAffiliations: OWNER
      orderBy: {field: PUSHED_AT, direction: DESC}
    ) {
      pageInfo { hasNextPage endCursor }
      nodes {
        name
        url
        description
        isFork
        isArchived
        isPrivate
        stargazerCount
        forkCount
        pushedAt
        primaryLanguage { name }
        languages(first: 20, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name } }
        }
      }
    }
  }
}
"""

_BY_REPO = "repository { isPrivate } contributions(first: 1) { totalCount }"

CONTRIBUTIONS_QUERY = f"""
query($login: String!, $from: DateTime!, $to: DateTime!) {{
  user(login: $login) {{
    contributionsCollection(from: $from, to: $to) {{
      commitContributionsByRepository(maxRepositories: 100) {{ {_BY_REPO} }}
      pullRequestContributionsByRepository(maxRepositories: 100) {{ {_BY_REPO} }}
      issueContributionsByRepository(maxRepositories: 100) {{ {_BY_REPO} }}
      pullRequestReviewContributionsByRepository(maxRepositories: 100) {{ {_BY_REPO} }}
    }}
  }}
}}
"""

SEARCH_QUERY = """
query($prs: String!, $merged: String!, $issues: String!, $external: String!) {
  prs: search(query: $prs, type: ISSUE, first: 1) { issueCount }
  merged: search(query: $merged, type: ISSUE, first: 1) { issueCount }
  issues: search(query: $issues, type: ISSUE, first: 1) { issueCount }
  external: search(query: $external, type: ISSUE, first: 3) {
    nodes {
      ... on PullRequest {
        title
        url
        state
        createdAt
        repository { nameWithOwner isPrivate }
      }
    }
  }
}
"""


@dataclass
class Repo:
    name: str
    url: str
    description: str
    is_fork: bool
    is_archived: bool
    stars: int
    forks: int
    pushed_at: datetime | None
    primary_language: str | None
    languages: dict[str, int]


@dataclass
class Contributions:
    commits: int = 0
    pull_requests: int = 0
    issues: int = 0
    reviews: int = 0


@dataclass
class ExternalPR:
    title: str
    url: str
    state: str
    repo: str
    created_at: datetime | None


@dataclass
class ProfileData:
    """Raw facts about a user. A field is None when its fetch failed."""

    login: str
    name: str | None = None
    followers: int | None = None
    contribution_years: list[int] | None = None
    external_repos_contributed: int | None = None
    repos: list[Repo] | None = None
    commits_all_time: int | None = None
    this_year: Contributions | None = None
    last_12_months: Contributions | None = None
    total_prs: int | None = None
    merged_prs: int | None = None
    total_issues: int | None = None
    external_prs: list[ExternalPR] | None = None
    errors: dict[str, str] = field(default_factory=dict)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _gql_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_profile(client: GitHubClient, data: ProfileData) -> None:
    user = client.graphql(PROFILE_QUERY, {"login": data.login}).get("user")
    if user is None:
        raise GitHubError(f"GitHub user {data.login!r} not found")
    data.name = user.get("name")
    data.followers = user["followers"]["totalCount"]
    data.contribution_years = sorted(user["contributionsCollection"]["contributionYears"])
    data.external_repos_contributed = user["repositoriesContributedTo"]["totalCount"]


def fetch_repos(client: GitHubClient, data: ProfileData) -> None:
    repos = []
    nodes = client.paginate(
        REPOS_QUERY,
        {"login": data.login},
        lambda d: (d.get("user") or {}).get("repositories"),
    )
    for node in nodes:
        if node.get("isPrivate"):
            continue
        edges = ((node.get("languages") or {}).get("edges")) or []
        repos.append(
            Repo(
                name=node["name"],
                url=node["url"],
                description=node.get("description") or "",
                is_fork=bool(node.get("isFork")),
                is_archived=bool(node.get("isArchived")),
                stars=node.get("stargazerCount") or 0,
                forks=node.get("forkCount") or 0,
                pushed_at=parse_time(node.get("pushedAt")),
                primary_language=(node.get("primaryLanguage") or {}).get("name"),
                languages={e["node"]["name"]: e["size"] for e in edges if e and e.get("node")},
            )
        )
    data.repos = repos


def fetch_contributions(
    client: GitHubClient, login: str, start: datetime, end: datetime
) -> Contributions:
    """Public contributions in [start, end]. The span must be at most one year."""
    user = client.graphql(
        CONTRIBUTIONS_QUERY, {"login": login, "from": _gql_time(start), "to": _gql_time(end)}
    ).get("user")
    if user is None:
        raise GitHubError(f"GitHub user {login!r} not found")
    collection = user["contributionsCollection"]

    def public_total(key: str) -> int:
        total = 0
        for entry in collection.get(key) or []:
            repo = (entry or {}).get("repository")
            if repo is None or repo.get("isPrivate"):
                continue
            total += entry["contributions"]["totalCount"]
        return total

    return Contributions(
        commits=public_total("commitContributionsByRepository"),
        pull_requests=public_total("pullRequestContributionsByRepository"),
        issues=public_total("issueContributionsByRepository"),
        reviews=public_total("pullRequestReviewContributionsByRepository"),
    )


def fetch_commit_history(client: GitHubClient, data: ProfileData, now: datetime) -> None:
    years = data.contribution_years
    if years is None:
        raise GitHubError("contribution years unavailable")

    total = 0
    for year in years:
        start = datetime(year, 1, 1, tzinfo=timezone.utc)
        end = min(datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc), now)
        if start > now:
            continue
        contributions = fetch_contributions(client, data.login, start, end)
        total += contributions.commits
        if year == now.year:
            data.this_year = contributions
    if data.this_year is None:
        data.this_year = Contributions()
    data.commits_all_time = total
    data.last_12_months = fetch_contributions(client, data.login, now - timedelta(days=365), now)


def fetch_search(client: GitHubClient, data: ProfileData) -> None:
    login = data.login
    result = client.graphql(
        SEARCH_QUERY,
        {
            "prs": f"author:{login} is:pr is:public",
            "merged": f"author:{login} is:pr is:public is:merged",
            "issues": f"author:{login} is:issue is:public",
            "external": f"author:{login} is:pr is:public -user:{login} sort:created-desc",
        },
    )
    data.total_prs = result["prs"]["issueCount"]
    data.merged_prs = result["merged"]["issueCount"]
    data.total_issues = result["issues"]["issueCount"]
    prs = []
    for node in result["external"].get("nodes") or []:
        repo = (node or {}).get("repository")
        if not node or not repo or repo.get("isPrivate"):
            continue
        prs.append(
            ExternalPR(
                title=node["title"],
                url=node["url"],
                state=node["state"],
                repo=repo["nameWithOwner"],
                created_at=parse_time(node.get("createdAt")),
            )
        )
    data.external_prs = prs


def collect(client: GitHubClient, login: str, now: datetime | None = None) -> ProfileData:
    """Fetch everything, recording (not raising) failures per data source."""
    now = now or datetime.now(timezone.utc)
    data = ProfileData(login=login)
    steps = [
        ("profile", lambda: fetch_profile(client, data)),
        ("repos", lambda: fetch_repos(client, data)),
        ("commits", lambda: fetch_commit_history(client, data, now)),
        ("search", lambda: fetch_search(client, data)),
    ]
    for name, step in steps:
        try:
            step()
        except (GitHubError, KeyError, TypeError, ValueError) as exc:
            data.errors[name] = f"{type(exc).__name__}: {exc}"
    return data
