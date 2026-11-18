"""Turn collected profile data into Markdown blocks."""

from __future__ import annotations

import html
from datetime import datetime, timezone
from urllib.parse import quote, quote_plus
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


def _shield_text(text: str) -> str:
    """Escape text for a shields.io static badge path segment."""
    return quote(text.replace("-", "--").replace("_", "__").replace(" ", "_"), safe="")


def shield(message: str, color: str, label: str | None = None, **params: str) -> str:
    parts = ([_shield_text(label)] if label is not None else []) + [_shield_text(message), color]
    query = "&".join(f"{key}={quote(value, safe='')}" for key, value in {"style": "flat-square", **params}.items())
    return f"https://img.shields.io/badge/{'-'.join(parts)}?{query}"


_STATUS_BADGE = {
    "MERGED": ("merged", "8957e5"),
    "OPEN": ("open", "238636"),
    "CLOSED": ("closed", "6e7681"),
}


def all_prs_url(login: str) -> str:
    return "https://github.com/search?type=pullrequests&q=" + quote_plus(
        f"author:{login} is:pr is:public -user:{login}"
    )


def render_open_source(
    data: ProfileData,
    tz: ZoneInfo,
    min_stars: int = 0,
    max_rows: int = 6,
    exclude_repos: list[str] = (),
    include_closed: bool = False,
    card_path: str | None = None,
) -> str:
    prs = select_open_source(data.external_prs or [], min_stars, exclude_repos, include_closed)
    if not prs:
        return "_No open-source contributions to show yet._"

    lines = []
    if card_path:
        summary = open_source_summary(prs)
        alt = (
            f"{fmt_stars(summary['stars'])} combined stars across {summary['projects']} open-source "
            f"projects with {summary['merged']} merged pull requests"
        )
        lines += [f'<a href="{all_prs_url(data.login)}"><img src="{card_path}" alt="{html.escape(alt)}" width="100%"></a>', ""]

    lines += ["| Project | Contribution | Status |", "|---|---|---|"]
    for pr in prs[:max_rows]:
        when = pr.merged_at or pr.created_at
        stars_badge = (
            f"https://img.shields.io/github/stars/{pr.repo}"
            "?style=flat-square&logo=github&label=%E2%98%85&color=f5c518&labelColor=24292f"
        )
        project = (
            f"[**{_cell(pr.repo)}**]({pr.repo_url})<br>"
            f'<a href="{pr.repo_url}/stargazers"><img src="{stars_badge}" alt="{fmt_stars(pr.repo_stars)} stars"></a>'
        )
        text, color = _STATUS_BADGE.get(pr.state, (pr.state.lower(), "6e7681"))
        status = f'<img src="{shield(text, color, logo="git", logoColor="white")}" alt="{text}">'
        if when:
            status += f"<br><sub>{fmt_date(when, tz)}</sub>"
        lines.append(f"| {project} | [{_cell(_truncate(pr.title, 80))}]({pr.url}) | {status} |")
    lines += ["", f"<sub>[All my pull requests to other projects →]({all_prs_url(data.login)})</sub>"]
    return "\n".join(lines)


def open_source_summary(prs: list[ExternalPR]) -> dict:
    merged = [pr for pr in prs if pr.state == "MERGED"]
    projects: dict[str, int] = {}
    for pr in merged:
        projects[pr.repo] = pr.repo_stars
    top = sorted(projects.items(), key=lambda item: -item[1])
    return {"merged": len(merged), "projects": len(projects), "stars": sum(projects.values()), "top": top}


_STAR_PATH = (
    "M12 .587l3.668 7.431 8.2 1.192-5.934 5.787 1.4 8.168L12 18.896l-7.334 3.857 "
    "1.4-8.168L.132 9.21l8.2-1.192z"
)


def render_stars_card(prs: list[ExternalPR]) -> str:
    """A self-contained SVG card with the combined star count as the hero number."""
    summary = open_source_summary(prs)
    stars = fmt_stars(summary["stars"]).upper()
    top = " · ".join(
        f"{name.split('/')[-1]} ★{fmt_stars(count)}" for name, count in summary["top"][:2]
    )
    top = html.escape(_truncate(top, 34))
    merged, projects = summary["merged"], summary["projects"]
    pr_word = "merged PR" if merged == 1 else "merged PRs"
    project_word = "project" if projects == 1 else "projects"
    label = html.escape(f"{stars} combined stars across {projects} {project_word}, {merged} {pr_word}")
    font = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="860" height="190" viewBox="0 0 860 190" role="img" aria-label="{label}">
  <title>{label}</title>
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#1c2128"/>
      <stop offset="1" stop-color="#0d1117"/>
    </linearGradient>
    <linearGradient id="gold" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffe680"/>
      <stop offset="1" stop-color="#f5a623"/>
    </linearGradient>
    <radialGradient id="glow" cx="0.18" cy="0.55" r="0.45">
      <stop offset="0" stop-color="#f5c518" stop-opacity="0.18"/>
      <stop offset="1" stop-color="#f5c518" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect x="0.5" y="0.5" width="859" height="189" rx="16" fill="url(#bg)" stroke="#30363d"/>
  <rect x="0.5" y="0.5" width="859" height="189" rx="16" fill="url(#glow)"/>
  <g font-family="{font}">
    <text x="36" y="42" fill="#8b949e" font-size="13" font-weight="700" letter-spacing="2.5">OPEN SOURCE IMPACT</text>
    <path d="{_STAR_PATH}" transform="translate(34 60) scale(2.9)" fill="url(#gold)"/>
    <text x="120" y="129" fill="url(#gold)" font-size="84" font-weight="800" letter-spacing="-2">{stars}</text>
    <text x="36" y="166" fill="#c9d1d9" font-size="15">combined GitHub stars on projects I've shipped merged code to</text>
    <line x1="572" y1="34" x2="572" y2="156" stroke="#30363d"/>
    <text x="650" y="82" fill="#f0f6fc" font-size="40" font-weight="800" text-anchor="end">{merged}</text>
    <text x="664" y="80" fill="#8b949e" font-size="16">{pr_word}</text>
    <text x="650" y="132" fill="#f0f6fc" font-size="40" font-weight="800" text-anchor="end">{projects}</text>
    <text x="664" y="130" fill="#8b949e" font-size="16">{project_word}</text>
    <text x="604" y="166" fill="#8b949e" font-size="12">{top}</text>
  </g>
</svg>
"""


def render_updated(now: datetime, tz: ZoneInfo, workflow_url: str | None, run_url: str | None) -> str:
    local = now.astimezone(tz)
    hour = local.hour % 12 or 12
    stamp = f"{fmt_date(local, tz)}, {hour}:{local:%M} {'AM' if local.hour < 12 else 'PM'} {local:%Z}"
    source = f'<a href="{workflow_url}">GitHub Actions</a>' if workflow_url else "GitHub Actions"
    run = f' · <a href="{run_url}">run log</a>' if run_url else ""
    return f"<sub>Stats refresh automatically via {source}. Last successful refresh: <b>{stamp}</b>{run}</sub>"
