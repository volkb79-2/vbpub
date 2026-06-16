"""Tests for _semver_key, _bump_from_commits, and bump_version — pure logic, no I/O."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import unittest
from ciu_forge.release import _semver_key
from ciu_forge.version import _bump_from_commits, bump_version, _parse_semver


class TestSemverKey(unittest.TestCase):
    def _lt(self, a, b):
        self.assertLess(_semver_key(a), _semver_key(b))

    def _eq(self, a, b):
        self.assertEqual(_semver_key(a), _semver_key(b))

    def test_patch_increment(self):
        self._lt("1.0.0", "1.0.1")

    def test_minor_double_digit(self):
        # 1.2.0 < 1.10.0 — lexically "10" < "2" but numerically 10 > 2
        self._lt("1.2.0", "1.10.0")

    def test_major(self):
        self._lt("0.9.9", "1.0.0")

    def test_equal(self):
        self._eq("1.2.3", "1.2.3")

    def test_counter_numeric_not_lexical(self):
        # r10 must sort ABOVE r2 (lexical order would invert this)
        self._lt("1.61.0-r2", "1.61.0-r10")

    def test_counter_double_digit(self):
        self._lt("1.61.0-r9", "1.61.0-r10")

    def test_base_before_counter(self):
        # 1.61.0 (no suffix) sorts below 1.61.0-r1
        self._lt("1.61.0", "1.61.0-r1")

    def test_max_picks_highest(self):
        versions = ["1.0.0", "0.9.9", "1.0.1", "0.1.0"]
        self.assertEqual(max(versions, key=_semver_key), "1.0.1")

    def test_max_counter_suffix(self):
        versions = ["1.61.0-r1", "1.61.0-r10", "1.61.0-r2"]
        self.assertEqual(max(versions, key=_semver_key), "1.61.0-r10")

    def test_cross_major_with_counter(self):
        # 2.0.0 beats any 1.x counter
        self._lt("1.99.0-r100", "2.0.0")


class TestBumpFromCommits(unittest.TestCase):
    def test_empty_is_patch(self):
        self.assertEqual(_bump_from_commits([]), "patch")

    def test_fix_is_patch(self):
        self.assertEqual(_bump_from_commits(["fix: handle empty list"]), "patch")

    def test_docs_is_patch(self):
        self.assertEqual(_bump_from_commits(["docs: update readme"]), "patch")

    def test_chore_is_patch(self):
        self.assertEqual(_bump_from_commits(["chore: cleanup"]), "patch")

    def test_feat_is_minor(self):
        self.assertEqual(_bump_from_commits(["feat: add resolver"]), "minor")

    def test_feat_with_scope(self):
        self.assertEqual(_bump_from_commits(["feat(cli): new verb"]), "minor")

    def test_breaking_exclamation_is_major(self):
        self.assertEqual(_bump_from_commits(["feat!: remove old api"]), "major")

    def test_breaking_exclamation_with_scope(self):
        self.assertEqual(_bump_from_commits(["fix(api)!: drop param"]), "major")

    def test_breaking_change_footer(self):
        self.assertEqual(_bump_from_commits(["BREAKING CHANGE: removed --legacy flag"]), "major")

    def test_breaking_beats_feat(self):
        # One breaking commit in a list with feat → still major
        self.assertEqual(_bump_from_commits(["feat: nice thing", "fix!: oops breaking"]), "major")

    def test_feat_beats_fix(self):
        self.assertEqual(_bump_from_commits(["fix: typo", "feat: new feature", "fix: another"]), "minor")

    def test_multiple_patches(self):
        self.assertEqual(_bump_from_commits(["fix: a", "fix: b", "docs: c"]), "patch")


class TestBumpVersion(unittest.TestCase):
    def test_patch(self):
        self.assertEqual(bump_version("1.2.3", "patch"), "1.2.4")

    def test_minor(self):
        self.assertEqual(bump_version("1.2.3", "minor"), "1.3.0")

    def test_major(self):
        self.assertEqual(bump_version("1.2.3", "major"), "2.0.0")

    def test_zero_base(self):
        self.assertEqual(bump_version("0.0.0", "patch"), "0.0.1")

    def test_minor_resets_patch(self):
        self.assertEqual(bump_version("1.2.99", "minor"), "1.3.0")

    def test_major_resets_minor_and_patch(self):
        self.assertEqual(bump_version("1.99.99", "major"), "2.0.0")

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            bump_version("not-a-version", "patch")

    def test_prerelease_suffix_stripped(self):
        # Counter versions (e.g. "1.61.0-r2") are never passed to bump_version;
        # _next_counter_version handles them. But _parse_semver accepts the suffix
        # as a prerelease label and bump_version strips it, bumping cleanly.
        self.assertEqual(bump_version("1.61.0-r2", "patch"), "1.61.1")


if __name__ == "__main__":
    unittest.main()
