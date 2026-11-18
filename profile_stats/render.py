"""Turn collected profile data into Markdown blocks."""

from __future__ import annotations

import html
from datetime import datetime, timezone
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from .stats import ExternalPR, ProfileData, Repo

BAR_WIDTH = 25
_PARTIAL_BLOCKS = " ▏▎▍▌▋▊▉"
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def percent_bar(percent: float, width: int = BAR_WIDTH) -> str:
    """A fixed-width bar with 1/8-cell resolution, e.g. ``████▌░░░░``."""
    percent = max(0.0, min(100.0, percent))
    eighths = round(percent / 100 * width * 8)
    full, remainder = divmod(eighths, 8)
    bar = "█" * full
    if remainder:
        bar += _PARTIAL_BLOCKS[remainder]
    return bar + "░" * (width - len(bar))


def fmt_int(value: int) -> str:
    return f"{value:,}"


def fmt_month_day(value: datetime, tz: ZoneInfo) -> str:
    local = value.astimezone(tz)
    return f"{local:%b} {local.day}"


def fmt_date(value: datetime, tz: ZoneInfo) -> str:
    return f"{fmt_month_day(value, tz)}, {value.astimezone(tz).year}"


def original_repos(data: ProfileData) -> list[Repo]:
    """Public repositories the user owns and did not fork, excluding the profile repo."""
    return [r for r in data.repos or [] if not r.is_fork and r.name.lower() != data.login.lower()]


def aggregate_languages(
    repos: list[Repo], exclude_repos: list[str], exclude_languages: list[str]
) -> list[tuple[str, int]]:
    skip_repos = {name.lower() for name in exclude_repos}
    skip_langs = {name.lower() for name in exclude_languages}
    totals: dict[str, int] = {}
    for repo in repos:
        if repo.name.lower() in skip_repos:
            continue
        for language, size in repo.languages.items():
            if language.lower() in skip_langs:
                continue
            totals[language] = totals.get(language, 0) + size
    return sorted(totals.items(), key=lambda item: (-item[1], item[0]))


def render_stats(data: ProfileData, now: datetime, tz: ZoneInfo) -> str:
    repos = original_repos(data)
    stars = sum(r.stars for r in repos)
    years = data.contribution_years or [now.year]
    this_year = data.this_year
    last_year = data.last_12_months
    assert this_year is not None and last_year is not None

    year_start = datetime(now.year, 1, 1, tzinfo=tz)
    rows = [
        ("Stars earned", stars, f"across {len(repos)} original public repos"),
        ("Commits, all-time", data.commits_all_time, f"public, since {years[0]}"),
        (f"Commits, {now.year}", this_year.commits, f"{fmt_month_day(year_start, tz)} – {fmt_month_day(now, tz)}"),
        ("Commits, last 12 months", last_year.commits, "rolling window"),
        ("Pull requests", data.total_prs, f"{fmt_int(data.merged_prs or 0)} merged, all-time"),
        ("Issues opened", data.total_issues, "all-time"),
        ("Code reviews", last_year.reviews, "last 12 months"),
        ("Contributed to", data.external_repos_contributed, "other people's public repos (recent)"),
        ("Followers", data.followers, ""),
    ]
    label_width = max(len(label) for label, _, _ in rows)
    value_width = max(len(fmt_int(value or 0)) for _, value, _ in rows)
    lines = [
        f"{label:<{label_width}}  {fmt_int(value or 0):>{value_width}}   {note}".rstrip()
        for label, value, note in rows
    ]
    return "\n".join(
        [
            "```text",
            *lines,
            "```",
            "",
            "<sub>Public activity only. Commits follow GitHub's contribution rules "
            "(commits authored by this account on a repository's default branch); "
            "automated README refresh commits are made by github-actions[bot] and are not counted.</sub>",
        ]
    )


def render_languages(
    data: ProfileData,
    top: int,
    exclude_repos: list[str],
    exclude_languages: list[str],
    note: str = "",
) -> str:
    languages = aggregate_languages(original_repos(data), exclude_repos, exclude_languages)
    total = sum(size for _, size in languages)
    if total == 0:
        return "_No public language data yet._"

    shown = languages[:top]
    other = total - sum(size for _, size in shown)
    if other > 0:
        shown.append(("Other", other))
    name_width = max(len(name) for name, _ in shown)
    lines = [f"{name:<{name_width}}  {percent_bar(size / total * 100)}  {size / total * 100:5.1f}%" for name, size in shown]
    skip = {name.lower() for name in exclude_repos}
    counted = sum(1 for r in original_repos(data) if r.name.lower() not in skip)
    caption = f"By bytes of code across {counted} original public repositories."
    if note:
        caption += f" {html.escape(note)}"
    return "\n".join(["```text", *lines, "```", "", f"<sub>{caption}</sub>"])


def render_featured(data: ProfileData, featured: list[dict], tz: ZoneInfo) -> tuple[str, list[str]]:
    by_name = {r.name.lower(): r for r in data.repos or []}
    warnings = []
    lines = []
    for item in featured:
        repo = by_name.get(item["repo"].lower())
        if repo is None:
            warnings.append(f"Featured repo {item['repo']!r} is missing or not public; skipped.")
            continue
        meta = [repo.primary_language or "", f"★ {fmt_int(repo.stars)}" if repo.stars else ""]
        if repo.pushed_at:
            meta.append(f"updated {repo.pushed_at.astimezone(tz):%b %Y}")
        title = html.escape(item.get("name") or repo.name)
        description = html.escape(item.get("description") or repo.description)
        lines.append(f"- **[{title}]({repo.url})** — {description} <sub>{' · '.join(m for m in meta if m)}</sub>")
    if not lines:
        return "_No featured projects available._", warnings
    return "\n".join(lines), warnings


def _truncate(text: str, limit: int = 90) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def render_activity(data: ProfileData, recent: int, tz: ZoneInfo, exclude_repos: list[str] = ()) -> str:
    skip = {name.lower() for name in exclude_repos}
    repos = [r for r in original_repos(data) if r.pushed_at and r.name.lower() not in skip]
    repos.sort(key=lambda r: r.pushed_at, reverse=True)
    lines = []
    for repo in repos[:recent]:
        description = f" — {html.escape(_truncate(repo.description))}" if repo.description else ""
        lines.append(f"- [{repo.name}]({repo.url}){description} <sub>· {fmt_date(repo.pushed_at, tz)}</sub>")
    return "\n".join(lines) or "_No public repositories yet._"


def fmt_stars(value: int) -> str:
    """Compact star count: 687 -> 687, 30426 -> 30.4k."""
    if value < 1000:
        return str(value)
    return f"{value / 1000:.1f}".rstrip("0").rstrip(".") + "k"


def select_open_source(
    prs: list[ExternalPR],
    min_stars: int = 0,
    exclude_repos: list[str] = (),
    include_closed: bool = False,
) -> list[ExternalPR]:
    """PRs worth showcasing: public projects above a star floor, merged or still open."""
    skip = {name.lower() for name in exclude_repos}
    keep = {"MERGED", "OPEN"} | ({"CLOSED"} if include_closed else set())
    chosen = [
        pr
        for pr in prs
        if pr.state in keep and pr.repo_stars >= min_stars and pr.repo.lower() not in skip
    ]
    return sorted(chosen, key=lambda pr: pr.merged_at or pr.created_at or _EPOCH, reverse=True)


def _cell(text: str) -> str:
    return html.escape(text).replace("|", "\\|")


def render_open_source(
    data: ProfileData,
    tz: ZoneInfo,
    min_stars: int = 0,
    max_rows: int = 6,
    exclude_repos: list[str] = (),
    include_closed: bool = False,
) -> str:
    prs = select_open_source(data.external_prs or [], min_stars, exclude_repos, include_closed)
    if not prs:
        return "_No open-source contributions to show yet._"

    merged = [pr for pr in prs if pr.state == "MERGED"]
    merged_projects = {pr.repo: pr.repo_stars for pr in merged}
    all_prs = (
        "https://github.com/search?type=pullrequests&q="
        + quote_plus(f"author:{data.login} is:pr is:public -user:{data.login}")
    )
    summary = []
    if merged:
        plural = "s" if len(merged) != 1 else ""
        projects = "s" if len(merged_projects) != 1 else ""
        summary.append(f"**{len(merged)} merged PR{plural}** into **{len(merged_projects)} project{projects}**")
        stars = sum(merged_projects.values())
        if stars:
            summary.append(f"**{fmt_stars(stars)}★** combined")
    summary.append(f"[all pull requests →]({all_prs})")

    status_label = {"MERGED": "✅ Merged", "OPEN": "🟡 Open", "CLOSED": "Closed"}
    lines = [
        " · ".join(summary),
        "",
        "| Project | Contribution | Status |",
        "|---|---|---|",
    ]
    for pr in prs[:max_rows]:
        when = pr.merged_at or pr.created_at
        stars = f" <sub>★ {fmt_stars(pr.repo_stars)}</sub>" if pr.repo_stars else ""
        status = status_label.get(pr.state, pr.state.title())
        if when:
            status += f" <sub>{fmt_date(when, tz)}</sub>"
        lines.append(
            f"| [{_cell(pr.repo)}]({pr.repo_url}){stars} "
            f"| [{_cell(_truncate(pr.title, 80))}]({pr.url}) "
            f"| {status} |"
        )
    return "\n".join(lines)


def render_updated(now: datetime, tz: ZoneInfo, workflow_url: str | None, run_url: str | None) -> str:
    local = now.astimezone(tz)
    hour = local.hour % 12 or 12
    stamp = f"{fmt_date(local, tz)}, {hour}:{local:%M} {'AM' if local.hour < 12 else 'PM'} {local:%Z}"
    source = f'<a href="{workflow_url}">GitHub Actions</a>' if workflow_url else "GitHub Actions"
    run = f' · <a href="{run_url}">run log</a>' if run_url else ""
    return f"<sub>Stats refresh automatically via {source}. Last successful refresh: <b>{stamp}</b>{run}</sub>"
