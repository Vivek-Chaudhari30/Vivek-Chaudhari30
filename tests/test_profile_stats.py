"""Offline tests for the README generator. Run: python -m unittest discover -s tests"""

from __future__ import annotations

import io
import json
import unittest
import urllib.error
from datetime import datetime, timezone
from email.message import Message
from zoneinfo import ZoneInfo

from profile_stats import render
from profile_stats.__main__ import build_files, build_sections
from profile_stats.github_api import GitHubClient, GitHubError
from profile_stats.readme import MarkerError, replace_block
from profile_stats.stats import Contributions, ExternalPR, ProfileData, Repo, collect, fetch_contributions

NOW = datetime(2026, 11, 1, 14, 37, tzinfo=timezone.utc)
TZ = ZoneInfo("America/New_York")


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener_for(*responses):
    """An urlopen stand-in that returns (or raises) the given items in order."""
    queue = list(responses)
    calls = []

    def opener(request, timeout):
        calls.append(json.loads(request.data))
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResponse(json.dumps(item).encode())

    opener.calls = calls
    return opener


def http_error(code, headers=None):
    msg = Message()
    for key, value in (headers or {}).items():
        msg[key] = value
    return urllib.error.HTTPError("https://api.github.com/graphql", code, "error", msg, None)


def repo(name, stars=0, fork=False, languages=None, pushed="2026-10-01T00:00:00Z", description=""):
    return Repo(
        name=name,
        url=f"https://github.com/me/{name}",
        description=description,
        is_fork=fork,
        is_archived=False,
        stars=stars,
        forks=0,
        pushed_at=datetime.fromisoformat(pushed.replace("Z", "+00:00")),
        primary_language=next(iter(languages or {}), None),
        languages=languages or {},
    )


def pr(repo, state="MERGED", stars=100, title="Fix things", when="2026-10-10T00:00:00Z"):
    at = datetime.fromisoformat(when.replace("Z", "+00:00"))
    return ExternalPR(
        title=title,
        url=f"https://github.com/{repo}/pull/1",
        state=state,
        repo=repo,
        created_at=at,
        merged_at=at if state == "MERGED" else None,
        repo_url=f"https://github.com/{repo}",
        repo_stars=stars,
    )


def sample_data():
    return ProfileData(
        login="me",
        followers=13,
        contribution_years=[2023, 2024, 2025, 2026],
        external_repos_contributed=2,
        repos=[
            repo("me"),  # profile repo: ignored
            repo("alpha", stars=3, languages={"Python": 600, "Cuda": 100}, pushed="2026-10-29T00:00:00Z"),
            repo("beta", stars=2, languages={"TypeScript": 300}, description="Beta & co"),
            repo("forked", stars=50, fork=True, languages={"Rust": 10_000}),
            repo("vendored", languages={"Python": 1_000_000}),
        ],
        commits_all_time=1234,
        this_year=Contributions(commits=300, reviews=1),
        last_12_months=Contributions(commits=320, reviews=4),
        total_prs=10,
        merged_prs=7,
        total_issues=1,
        external_prs=[],
    )


class PercentBarTests(unittest.TestCase):
    def test_bounds_and_width(self):
        self.assertEqual(render.percent_bar(0, 10), "░" * 10)
        self.assertEqual(render.percent_bar(100, 10), "█" * 10)
        self.assertEqual(render.percent_bar(150, 10), "█" * 10)
        self.assertEqual(render.percent_bar(-5, 10), "░" * 10)

    def test_partial_cells(self):
        self.assertEqual(render.percent_bar(25, 10), "██▌░░░░░░░")
        for pct in (0.1, 33.3, 66.6, 99.9):
            self.assertEqual(len(render.percent_bar(pct)), render.BAR_WIDTH)


class ReplaceBlockTests(unittest.TestCase):
    TEXT = "intro\n<!-- X:START -->\nold\n<!-- X:END -->\noutro\n"

    def test_replaces_only_inside_markers(self):
        out = replace_block(self.TEXT, "X", "new \\1 content")
        self.assertEqual(out, "intro\n<!-- X:START -->\nnew \\1 content\n<!-- X:END -->\noutro\n")

    def test_idempotent(self):
        once = replace_block(self.TEXT, "X", "same")
        self.assertEqual(replace_block(once, "X", "same"), once)

    def test_missing_or_duplicate_markers(self):
        with self.assertRaises(MarkerError):
            replace_block("no markers here", "X", "c")
        with self.assertRaises(MarkerError):
            replace_block(self.TEXT * 2, "X", "c")


class RenderTests(unittest.TestCase):
    def test_languages_exclusions_and_percentages(self):
        out = render.render_languages(sample_data(), top=8, exclude_repos=["vendored"], exclude_languages=["Cuda"])
        self.assertIn("Python", out)
        self.assertIn("66.7%", out)  # 600 / 900
        self.assertIn("33.3%", out)
        self.assertNotIn("Rust", out)  # forks are excluded
        self.assertNotIn("Cuda", out)
        self.assertIn("across 2 original public repositories", out)

    def test_languages_other_bucket_and_empty(self):
        out = render.render_languages(sample_data(), top=1, exclude_repos=["vendored"], exclude_languages=[])
        self.assertIn("Other", out)
        empty = ProfileData(login="me", repos=[])
        self.assertIn("No public language data", render.render_languages(empty, 8, [], []))

    def test_stats_labels_time_periods(self):
        out = render.render_stats(sample_data(), NOW, TZ)
        self.assertIn("Stars earned", out)
        self.assertRegex(out, r"Stars earned\s+5\s")  # forks excluded
        self.assertIn("Commits, all-time", out)
        self.assertIn("since 2023", out)
        self.assertIn("Commits, 2026", out)
        self.assertIn("Commits, last 12 months", out)
        self.assertIn("rolling window", out)
        self.assertIn("1,234", out)

    def test_featured_skips_missing_and_escapes(self):
        featured = [{"repo": "beta"}, {"repo": "does-not-exist"}]
        out, warnings = render.render_featured(sample_data(), featured, TZ)
        self.assertIn("[beta](https://github.com/me/beta)", out)
        self.assertIn("Beta &amp; co", out)
        self.assertEqual(len(warnings), 1)
        self.assertTrue(out.startswith("- **[beta]"))

    def test_activity_excludes_profile_repo_and_sorts(self):
        out = render.render_activity(sample_data(), recent=2, tz=TZ)
        self.assertNotIn("[me]", out)
        self.assertLess(out.index("[alpha]"), out.index("[beta]"))

    def test_activity_exclude_repos(self):
        out = render.render_activity(sample_data(), recent=5, tz=TZ, exclude_repos=["Alpha"])
        self.assertNotIn("[alpha]", out)
        self.assertIn("[beta]", out)

    def test_activity_lists_only_repos(self):
        data = sample_data()
        data.external_prs = [pr("other/proj")]
        out = render.render_activity(data, recent=5, tz=TZ)
        self.assertNotIn("other/proj", out)
        self.assertTrue(out.startswith("- [alpha]"))

    def test_fmt_stars(self):
        self.assertEqual(render.fmt_stars(687), "687")
        self.assertEqual(render.fmt_stars(30426), "30.4k")
        self.assertEqual(render.fmt_stars(2000), "2k")

    OSS = [
        pr("big/seo", stars=30426, title="fix: PDF | reports", when="2026-11-13T00:00:00Z"),
        pr("team/bots", stars=687, when="2026-11-08T00:00:00Z"),
        pr("team/bots", stars=687, when="2026-11-10T00:00:00Z"),
        pr("team/bots", state="CLOSED", stars=687, title="refused"),
        pr("friend/class", stars=2, title="class project"),
        pr("team/open", state="OPEN", stars=50, when="2026-11-15T00:00:00Z"),
    ]

    def test_open_source_filters_and_sorts(self):
        data = sample_data()
        data.external_prs = list(self.OSS)
        out = render.render_open_source(data, TZ, min_stars=10, card_path="assets/card.svg")
        self.assertIn('<img src="assets/card.svg" alt="31.1k combined stars across 2', out)
        self.assertNotIn("refused", out)
        self.assertNotIn("class project", out)
        self.assertIn("fix: PDF \\| reports", out)
        self.assertIn("img.shields.io/github/stars/big/seo", out)
        self.assertIn("badge/open-238636", out)
        self.assertIn("badge/merged-8957e5", out)
        self.assertLess(out.index("team/open"), out.index("big/seo"))
        self.assertIn("-user%3Ame", out)

    def test_open_source_max_rows_and_empty(self):
        data = sample_data()
        data.external_prs = [pr(f"o/r{i}") for i in range(10)]
        out = render.render_open_source(data, TZ, max_rows=4)
        self.assertEqual(out.count("| [**o/r"), 4)
        self.assertNotIn("<img src=\"assets", out)  # no card unless configured
        data.external_prs = [pr("o/r", state="CLOSED")]
        self.assertIn("No open-source", render.render_open_source(data, TZ))
        self.assertIn("| [**o/r**]", render.render_open_source(data, TZ, include_closed=True))

    def test_stars_card_counts_distinct_merged_projects(self):
        prs = render.select_open_source(self.OSS, min_stars=10)
        svg = render.render_stars_card(prs)
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn(">31.1K</text>", svg)
        self.assertIn(">3</text>", svg)  # merged PRs (open and closed excluded)
        self.assertIn(">2</text>", svg)  # distinct projects
        self.assertIn("seo ★30.4k · bots ★687", svg)

    def test_shield_escapes_dashes_and_spaces(self):
        url = render.shield("a-b c_d", "fff", label="x")
        self.assertIn("/badge/x-a--b_c__d-fff?style=flat-square", url)

    def test_updated_stamp_in_local_time(self):
        out = render.render_updated(NOW, TZ, None, None)
        self.assertIn("Nov 1, 2026, 9:37 AM EST", out)


class BuildSectionsTests(unittest.TestCase):
    CONFIG = {"username": "me", "timezone": "America/New_York", "featured": [{"repo": "alpha"}]}

    def test_all_sections_when_everything_succeeds(self):
        sections, warnings = build_sections(sample_data(), self.CONFIG, NOW)
        self.assertEqual(
            set(sections), {"PROFILE-STATS", "LANGUAGES", "FEATURED", "ACTIVITY", "OPEN-SOURCE", "LAST-UPDATED"}
        )
        self.assertEqual(warnings, [])

    def test_failed_source_keeps_dependent_sections_and_timestamp(self):
        data = sample_data()
        data.errors["search"] = "boom"
        sections, warnings = build_sections(data, self.CONFIG, NOW)
        self.assertEqual(set(sections), {"LANGUAGES", "FEATURED", "ACTIVITY"})
        self.assertTrue(any("search" in w for w in warnings))
        self.assertEqual(build_files(data, {"open_source": {"card": "c.svg"}}), {})

    def test_build_files_writes_card_only_when_configured(self):
        data = sample_data()
        data.external_prs = [pr("o/r")]
        self.assertEqual(build_files(data, self.CONFIG), {})
        files = build_files(data, {"open_source": {"card": "assets/c.svg"}})
        self.assertEqual(list(files), ["assets/c.svg"])


class ClientTests(unittest.TestCase):
    def client(self, opener):
        return GitHubClient("token", opener=opener, sleep=lambda _: None)

    def test_requires_token(self):
        with self.assertRaises(GitHubError):
            GitHubClient("")

    def test_retries_server_errors_then_succeeds(self):
        opener = opener_for(http_error(502), {"data": {"ok": 1}})
        self.assertEqual(self.client(opener).graphql("q"), {"ok": 1})

    def test_gives_up_after_max_retries(self):
        opener = opener_for(*[http_error(503)] * 5)
        with self.assertRaises(GitHubError):
            self.client(opener).graphql("q")

    def test_forbidden_without_rate_limit_is_not_retried(self):
        opener = opener_for(http_error(403), {"data": {}})
        with self.assertRaises(GitHubError):
            self.client(opener).graphql("q")
        self.assertEqual(len(opener.calls), 1)

    def test_rate_limit_retry_after(self):
        opener = opener_for(http_error(403, {"Retry-After": "1"}), {"data": {"ok": 1}})
        self.assertEqual(self.client(opener).graphql("q"), {"ok": 1})

    def test_graphql_errors_without_data(self):
        opener = opener_for({"errors": [{"message": "bad query"}]})
        with self.assertRaisesRegex(GitHubError, "bad query"):
            self.client(opener).graphql("q")

    def test_paginate_follows_cursor_and_skips_null_nodes(self):
        page1 = {"data": {"c": {"nodes": [{"n": 1}, None], "pageInfo": {"hasNextPage": True, "endCursor": "X"}}}}
        page2 = {"data": {"c": {"nodes": [{"n": 2}], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}
        opener = opener_for(page1, page2)
        nodes = list(self.client(opener).paginate("q", {}, lambda d: d["c"]))
        self.assertEqual(nodes, [{"n": 1}, {"n": 2}])
        self.assertEqual(opener.calls[1]["variables"]["cursor"], "X")


class StatsTests(unittest.TestCase):
    def test_contributions_ignore_private_and_hidden_repos(self):
        by_repo = [
            {"repository": {"isPrivate": False}, "contributions": {"totalCount": 5}},
            {"repository": {"isPrivate": True}, "contributions": {"totalCount": 100}},
            {"repository": None, "contributions": {"totalCount": 100}},
        ]
        collection = {
            "commitContributionsByRepository": by_repo,
            "pullRequestContributionsByRepository": [],
            "issueContributionsByRepository": None,
            "pullRequestReviewContributionsByRepository": by_repo[:1],
        }
        opener = opener_for({"data": {"user": {"contributionsCollection": collection}}})
        client = GitHubClient("t", opener=opener, sleep=lambda _: None)
        result = fetch_contributions(client, "me", NOW, NOW)
        self.assertEqual(result, Contributions(commits=5, pull_requests=0, issues=0, reviews=5))

    def test_collect_records_failures_instead_of_raising(self):
        opener = opener_for(*[urllib.error.URLError("offline")] * 50)
        client = GitHubClient("t", opener=opener, sleep=lambda _: None, max_retries=0)
        data = collect(client, "me", NOW)
        self.assertEqual(set(data.errors), {"profile", "repos", "commits", "search"})


if __name__ == "__main__":
    unittest.main()
