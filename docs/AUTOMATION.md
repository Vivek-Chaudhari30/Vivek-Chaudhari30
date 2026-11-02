# Profile README automation

The profile README mixes hand-written content with sections that a small
Python generator refreshes from the GitHub GraphQL API.

## How it fits together

| Path | Purpose |
|---|---|
| `README.md` | The profile. Edit it freely **outside** the `<!-- NAME:START -->` / `<!-- NAME:END -->` markers. |
| `profile.toml` | Username, timezone, featured projects, language exclusions. |
| `profile_stats/github_api.py` | GraphQL client: retries, rate-limit back-off, pagination. |
| `profile_stats/stats.py` | Queries and public-only metric collection. |
| `profile_stats/render.py` | Markdown for each section (stats, language bars, featured projects, activity, timestamp). |
| `profile_stats/readme.py` | Replaces content between markers and nothing else. |
| `profile_stats/__main__.py` | CLI entry point (`python -m profile_stats`). |
| `tests/` | Offline unit tests (no network, no token). |
| `.github/workflows/update-profile.yml` | Runs the tests and the generator, then commits the result. |

Generated blocks: `FEATURED`, `PROFILE-STATS`, `LANGUAGES`, `ACTIVITY`, `LAST-UPDATED`.
Move a block anywhere in the README by moving its marker pair. Don't delete or
duplicate a marker: the generator then exits with an error and changes nothing.

## Metric definitions

All numbers cover **public** activity only. Private repositories and private
contributions are filtered out even when the token can see them.

| Metric | Definition |
|---|---|
| Stars earned | Sum of stargazers on public repos you own and did not fork (profile repo excluded). |
| Commits, all-time | Public commit contributions summed year by year over every year GitHub reports contributions for. |
| Commits, *year* | Public commit contributions from Jan 1 (UTC) of the current year until now. |
| Commits, last 12 months | Public commit contributions in the rolling 365 days before the refresh. |
| Pull requests | All-time count from search `author:<you> is:pr is:public`; merged uses `is:merged`. |
| Issues opened | All-time count from search `author:<you> is:issue is:public`. |
| Code reviews | Public PR review contributions in the last 12 months. |
| Contributed to | GitHub's `repositoriesContributedTo` (public, excluding your own repos), which covers roughly the last year. |
| Languages | Bytes per language from GitHub's linguist across original public repos, minus the exclusions in `profile.toml`. |

Commit counts follow GitHub's contribution rules: commits authored by your
account on a repository's default branch (or `gh-pages`). The workflow commits
as `github-actions[bot]`, so automated refreshes never inflate your numbers.

## When it runs

- **Daily at 9:37 AM America/New_York** (`cron: "37 9 * * *"` with `timezone: "America/New_York"`), so the time stays correct across EST/EDT changes. GitHub may start scheduled runs a few minutes late during busy periods.
- **On every push to `main`**, so edits you make to the README are followed by a refresh. The bot's own pushes use `GITHUB_TOKEN`, which GitHub never lets trigger another workflow run, so there is no loop.
- **Manually**: *Actions → Update profile README → Run workflow*, or `gh workflow run update-profile.yml`.

The "Last successful refresh" stamp only advances when every data source
succeeded. If one source fails (API outage, rate limit), sections that depend
on it keep their previous content, the run logs a warning annotation, and the
rest are still refreshed. If nothing could be fetched, the job fails and the
README is left untouched.

## Scheduled workflows and the 60-day inactivity rule

GitHub disables scheduled workflows in a public repository after 60 days with
no repository activity. Each successful refresh commits a new timestamp, which
normally keeps the repository active. If the schedule is ever disabled anyway
(for example after a long string of failed runs):

1. Open **Actions → Update profile README**.
2. Click **Enable workflow** in the banner.
3. Optionally click **Run workflow** to refresh immediately.

Or from a terminal:

```bash
gh workflow enable update-profile.yml --repo Vivek-Chaudhari30/Vivek-Chaudhari30
gh workflow run update-profile.yml --repo Vivek-Chaudhari30/Vivek-Chaudhari30
```

## Running locally

Requires Python 3.11+ and no third-party packages.

```bash
python -m unittest discover -s tests -t . -v
GITHUB_TOKEN="$(gh auth token)" python -m profile_stats
```

`--check` exits with status 2 if the README would change, without writing it.

## Permissions

The workflow uses the built-in `GITHUB_TOKEN` with `contents: read` by default
and `contents: write` on the single job that pushes the README. No personal
access token, secret, paid runner or external service is needed.
