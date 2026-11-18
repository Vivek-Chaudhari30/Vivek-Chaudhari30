"""CLI: regenerate the dynamic README sections.

    GITHUB_TOKEN=... python -m profile_stats [--readme README.md] [--config profile.toml] [--check]

Each section is rewritten only when every data source it needs was fetched
successfully; otherwise its previous content is kept and a warning is printed.
The "last refresh" stamp is only advanced when all sections succeed.
Exit codes: 0 = done (possibly with warnings), 1 = nothing could be refreshed
or the README is malformed, 2 = --check found the README out of date.
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import render
from .github_api import GitHubClient, GitHubError
from .readme import MarkerError, replace_block
from .stats import ProfileData, collect


def warn(message: str) -> None:
    # Rendered as an annotation in GitHub Actions, plain text elsewhere.
    prefix = "::warning::" if os.getenv("GITHUB_ACTIONS") == "true" else "warning: "
    print(f"{prefix}{message}", file=sys.stderr)


def load_config(path: Path) -> dict:
    with path.open("rb") as fh:
        config = tomllib.load(fh)
    if not config.get("username"):
        raise ValueError(f"{path}: 'username' is required")
    return config


def build_sections(data: ProfileData, config: dict, now: datetime) -> tuple[dict[str, str], list[str]]:
    """Return {marker name: markdown} for every section whose inputs are available."""
    tz = ZoneInfo(config.get("timezone", "UTC"))
    lang_cfg = config.get("languages", {})
    activity_cfg = config.get("activity", {})
    oss_cfg = config.get("open_source", {})
    failed = set(data.errors)
    warnings = [f"Could not fetch {name}: {err}" for name, err in data.errors.items()]
    sections: dict[str, str] = {}

    def needs(*sources: str) -> bool:
        return not failed.intersection(sources)

    if needs("profile", "repos", "commits", "search"):
        sections["PROFILE-STATS"] = render.render_stats(data, now, tz)
    if needs("repos"):
        sections["LANGUAGES"] = render.render_languages(
            data,
            top=lang_cfg.get("top", 8),
            exclude_repos=lang_cfg.get("exclude_repos", []),
            exclude_languages=lang_cfg.get("exclude_languages", []),
            note=lang_cfg.get("note", ""),
        )
        featured, featured_warnings = render.render_featured(data, config.get("featured", []), tz)
        sections["FEATURED"] = featured
        warnings += featured_warnings
    if needs("repos"):
        sections["ACTIVITY"] = render.render_activity(
            data, activity_cfg.get("recent_repos", 5), tz, activity_cfg.get("exclude_repos", [])
        )
    if needs("search"):
        sections["OPEN-SOURCE"] = render.render_open_source(
            data,
            tz,
            min_stars=oss_cfg.get("min_stars", 0),
            max_rows=oss_cfg.get("max_rows", 6),
            exclude_repos=oss_cfg.get("exclude_repos", []),
            include_closed=oss_cfg.get("include_closed", False),
            card_path=oss_cfg.get("card"),
        )
    if not failed:
        server = os.getenv("GITHUB_SERVER_URL", "https://github.com")
        repo = os.getenv("GITHUB_REPOSITORY")
        workflow_url = f"{server}/{repo}/actions/workflows/update-profile.yml" if repo else None
        run_url = f"{server}/{repo}/actions/runs/{os.environ['GITHUB_RUN_ID']}" if repo and os.getenv("GITHUB_RUN_ID") else None
        sections["LAST-UPDATED"] = render.render_updated(now, tz, workflow_url, run_url)
    return sections, warnings


def build_files(data: ProfileData, config: dict) -> dict[str, str]:
    """Return {relative path: content} for generated asset files (the stars card)."""
    oss_cfg = config.get("open_source", {})
    card = oss_cfg.get("card")
    if not card or "search" in data.errors:
        return {}
    prs = render.select_open_source(
        data.external_prs or [],
        oss_cfg.get("min_stars", 0),
        oss_cfg.get("exclude_repos", []),
        oss_cfg.get("include_closed", False),
    )
    return {card: render.render_stars_card(prs)} if prs else {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    parser.add_argument("--config", type=Path, default=Path("profile.toml"))
    parser.add_argument("--check", action="store_true", help="exit 2 if the README would change; don't write")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        original = args.readme.read_text(encoding="utf-8")
        client = GitHubClient(os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "")
    except (OSError, ValueError, tomllib.TOMLDecodeError, GitHubError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    data = collect(client, config["username"], now)
    sections, warnings = build_sections(data, config, now)
    for message in warnings:
        warn(message)
    if not sections:
        print("error: no section could be refreshed; README left unchanged.", file=sys.stderr)
        return 1

    updated = original
    try:
        for name, content in sections.items():
            updated = replace_block(updated, name, content)
    except MarkerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    base = args.readme.parent
    changed_files = {}
    for rel, content in build_files(data, config).items():
        path = base / rel
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            changed_files[path] = content

    if updated == original and not changed_files:
        print("README already up to date.")
        return 0
    if args.check:
        print("README is out of date.")
        return 2
    for path, content in changed_files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"Wrote {path}")
    if updated != original:
        args.readme.write_text(updated, encoding="utf-8")
        print(f"Updated sections: {', '.join(sections)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
