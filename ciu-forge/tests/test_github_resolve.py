"""Tests for GitHubReleases.resolve_latest — list_releases mocked, no network."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import unittest
from unittest.mock import patch
from ciu_forge.release import GitHubReleases


def _rel(tag, *, draft=False, prerelease=False, assets=None):
    return {
        "tag_name": tag,
        "draft": draft,
        "prerelease": prerelease,
        "assets": assets or [],
    }


class TestResolveLatest(unittest.TestCase):
    def setUp(self):
        self.gh = GitHubReleases("owner", "repo", "token")

    def _resolve(self, releases, prefix):
        with patch.object(self.gh, "list_releases", return_value=releases):
            return self.gh.resolve_latest(prefix)

    # -- baseline cases ---------------------------------------------------------

    def test_empty_releases(self):
        self.assertIsNone(self._resolve([], "ciu"))

    def test_single_release(self):
        r = self._resolve([_rel("ciu-v1.0.0")], "ciu")
        self.assertEqual(r["version"], "1.0.0")
        self.assertEqual(r["tag"], "ciu-v1.0.0")

    def test_picks_highest_semver(self):
        releases = [_rel("ciu-v1.0.0"), _rel("ciu-v1.0.2"), _rel("ciu-v1.0.1")]
        r = self._resolve(releases, "ciu")
        self.assertEqual(r["version"], "1.0.2")

    def test_minor_double_digit(self):
        releases = [_rel("ciu-v1.2.0"), _rel("ciu-v1.10.0"), _rel("ciu-v1.9.0")]
        r = self._resolve(releases, "ciu")
        self.assertEqual(r["version"], "1.10.0")

    # -- counter suffix ---------------------------------------------------------

    def test_counter_suffix_numeric_sort(self):
        # r10 must win over r2 (lexical "r10" < "r2")
        releases = [_rel("pwmcp-v1.61.0-r2"), _rel("pwmcp-v1.61.0-r10"), _rel("pwmcp-v1.61.0-r1")]
        r = self._resolve(releases, "pwmcp")
        self.assertEqual(r["version"], "1.61.0-r10")

    def test_counter_with_newer_base(self):
        releases = [_rel("pwmcp-v1.61.0-r10"), _rel("pwmcp-v1.62.0-r1")]
        r = self._resolve(releases, "pwmcp")
        self.assertEqual(r["version"], "1.62.0-r1")

    # -- exclusions -------------------------------------------------------------

    def test_skips_draft(self):
        releases = [_rel("ciu-v1.0.1", draft=True), _rel("ciu-v1.0.0")]
        r = self._resolve(releases, "ciu")
        self.assertEqual(r["version"], "1.0.0")

    def test_skips_prerelease(self):
        releases = [_rel("ciu-v2.0.0-rc1", prerelease=True), _rel("ciu-v1.0.0")]
        r = self._resolve(releases, "ciu")
        self.assertEqual(r["version"], "1.0.0")

    def test_skips_latest_pointer_tag(self):
        # "ciu-latest" has no "-v" separator — must be ignored
        releases = [_rel("ciu-latest"), _rel("ciu-v1.0.0")]
        r = self._resolve(releases, "ciu")
        self.assertEqual(r["version"], "1.0.0")

    def test_no_match_returns_none(self):
        releases = [_rel("other-v5.0.0")]
        self.assertIsNone(self._resolve(releases, "ciu"))

    # -- prefix isolation -------------------------------------------------------

    def test_prefix_isolation(self):
        # pwmcp releases must not leak into ciu resolution
        releases = [_rel("pwmcp-v2.0.0"), _rel("ciu-v1.0.0")]
        r = self._resolve(releases, "ciu")
        self.assertEqual(r["version"], "1.0.0")

    def test_prefix_isolation_reversed(self):
        releases = [_rel("pwmcp-v2.0.0"), _rel("ciu-v1.0.0")]
        r = self._resolve(releases, "pwmcp")
        self.assertEqual(r["version"], "2.0.0")

    # -- assets -----------------------------------------------------------------

    def test_asset_names_and_urls(self):
        assets = [
            {"name": "ciu_forge-0.1.0-py3-none-any.whl",
             "browser_download_url": "https://example.com/ciu.whl"},
            {"name": "ciu_forge-0.1.0-py3-none-any.whl.sha256",
             "browser_download_url": "https://example.com/ciu.whl.sha256"},
        ]
        releases = [_rel("ciu-forge-v0.1.0", assets=assets)]
        r = self._resolve(releases, "ciu-forge")
        self.assertEqual(len(r["assets"]), 2)
        self.assertEqual(r["assets"][0]["name"], "ciu_forge-0.1.0-py3-none-any.whl")

    def test_no_assets_returns_empty_list(self):
        releases = [_rel("ciu-v1.0.0")]
        r = self._resolve(releases, "ciu")
        self.assertEqual(r["assets"], [])


if __name__ == "__main__":
    unittest.main()
