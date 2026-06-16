"""Tests for detect_changed_projects, _next_counter_version, _latest_tag_for_prefix.

Uses a real temp git repo — no mocking of git subprocess calls. This validates the
change-detection logic (S12.2) including path isolation and conventional-commits bump.
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cmru.version import (
    _latest_tag_for_prefix,
    _next_counter_version,
    bump_version,
    detect_changed_projects,
)


# ---------------------------------------------------------------------------
# Temp git repo helpers
# ---------------------------------------------------------------------------

def _git(*args, cwd):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {args} failed:\n{r.stderr}")
    return r.stdout.strip()


class _TempRepo:
    """Context manager: creates a minimal, configured git repo in a temp dir."""
    def __enter__(self):
        self.root = Path(tempfile.mkdtemp(prefix="cmru_test_"))
        _git("init", cwd=self.root)
        _git("config", "user.email", "test@cmru.test", cwd=self.root)
        _git("config", "user.name", "CIU Forge Test", cwd=self.root)
        # Seed an initial commit so tags have something to attach to
        (self.root / "README.md").write_text("init\n")
        _git("add", "README.md", cwd=self.root)
        _git("commit", "-m", "chore: initial commit", cwd=self.root)
        return self.root

    def __exit__(self, *_):
        shutil.rmtree(self.root, ignore_errors=True)


def _commit(repo: Path, msg: str, files: dict | None = None) -> None:
    """Write files (default: a single dummy.txt) and commit with msg."""
    if files is None:
        files = {"dummy.txt": msg}
    for name, content in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(content)
        _git("add", name, cwd=repo)
    _git("commit", "-m", msg, cwd=repo)


def _tag(repo: Path, name: str) -> None:
    _git("tag", "-a", name, "-m", f"release {name}", cwd=repo)


# ---------------------------------------------------------------------------
# Minimal config stand-ins (mirror real ProjectConfig/VersionConfig shape)
# ---------------------------------------------------------------------------

class _VersionCfg:
    def __init__(self, strategy="scm", bump="conventional", paths=None, base_version="1.0.0"):
        self.strategy = strategy
        self.bump = bump
        self.paths = paths
        self.base_version = base_version


class _ProjCfg:
    def __init__(self, prefix, cwd, paths=None, bump="conventional"):
        self.prefix = prefix
        self.cwd = cwd
        self.paths = paths or [cwd]
        self.version = _VersionCfg(bump=bump, paths=paths or [cwd])


# ---------------------------------------------------------------------------
# _next_counter_version
# ---------------------------------------------------------------------------

class TestNextCounterVersion(unittest.TestCase):
    def test_no_existing_tags_starts_r1(self):
        with _TempRepo() as repo:
            self.assertEqual(_next_counter_version(repo, "pwmcp-v", "1.61.0"), "1.61.0-r1")

    def test_increments_from_r1_to_r2(self):
        with _TempRepo() as repo:
            _tag(repo, "pwmcp-v1.61.0-r1")
            self.assertEqual(_next_counter_version(repo, "pwmcp-v", "1.61.0"), "1.61.0-r2")

    def test_numeric_max_not_lexical(self):
        # r9 + r10 exist → next must be r11, not r10 (lexical "r9" > "r10" is wrong)
        with _TempRepo() as repo:
            for i in [1, 9, 10]:
                _tag(repo, f"pwmcp-v1.0.0-r{i}")
            self.assertEqual(_next_counter_version(repo, "pwmcp-v", "1.0.0"), "1.0.0-r11")

    def test_different_base_version_is_independent(self):
        # r3 exists for 1.0.0 but not for 2.0.0
        with _TempRepo() as repo:
            _tag(repo, "pwmcp-v1.0.0-r3")
            self.assertEqual(_next_counter_version(repo, "pwmcp-v", "2.0.0"), "2.0.0-r1")


# ---------------------------------------------------------------------------
# _latest_tag_for_prefix
# ---------------------------------------------------------------------------

class TestLatestTagForPrefix(unittest.TestCase):
    def test_no_tags(self):
        with _TempRepo() as repo:
            self.assertIsNone(_latest_tag_for_prefix(repo, "ciu-v"))

    def test_single_tag(self):
        with _TempRepo() as repo:
            _tag(repo, "ciu-v1.0.0")
            self.assertEqual(_latest_tag_for_prefix(repo, "ciu-v"), "ciu-v1.0.0")

    def test_picks_highest_semver(self):
        with _TempRepo() as repo:
            for ver in ["0.9.0", "1.0.0", "0.10.0"]:
                _tag(repo, f"ciu-v{ver}")
            self.assertEqual(_latest_tag_for_prefix(repo, "ciu-v"), "ciu-v1.0.0")

    def test_counter_suffix_numeric_sort(self):
        with _TempRepo() as repo:
            for tag in ["pwmcp-v1.61.0-r1", "pwmcp-v1.61.0-r10", "pwmcp-v1.61.0-r2"]:
                _tag(repo, tag)
            self.assertEqual(_latest_tag_for_prefix(repo, "pwmcp-v"), "pwmcp-v1.61.0-r10")

    def test_ignores_other_prefixes(self):
        with _TempRepo() as repo:
            _tag(repo, "pwmcp-v2.0.0")
            self.assertIsNone(_latest_tag_for_prefix(repo, "ciu-v"))


# ---------------------------------------------------------------------------
# detect_changed_projects
# ---------------------------------------------------------------------------

class TestDetectChangedProjects(unittest.TestCase):
    def test_no_prior_tag_is_first_release(self):
        """A project with no tag is always included (first release)."""
        with _TempRepo() as repo:
            projects = {"ciu": _ProjCfg("ciu-v", "ciu")}
            changed = detect_changed_projects(repo, projects)
            self.assertEqual([n for n, *_ in changed], ["ciu"])

    def test_no_changes_after_tag_excluded(self):
        """No commits since last tag → project skipped."""
        with _TempRepo() as repo:
            _commit(repo, "feat: initial ciu", {"ciu/main.py": "# ciu"})
            _tag(repo, "ciu-v0.1.0")
            projects = {"ciu": _ProjCfg("ciu-v", "ciu", paths=["ciu"])}
            self.assertEqual(detect_changed_projects(repo, projects), [])

    def test_changes_after_tag_included(self):
        """Commits touching project paths since last tag → project included."""
        with _TempRepo() as repo:
            _commit(repo, "feat: initial ciu", {"ciu/main.py": "# ciu"})
            _tag(repo, "ciu-v0.1.0")
            _commit(repo, "fix: patch something", {"ciu/main.py": "# patched"})
            projects = {"ciu": _ProjCfg("ciu-v", "ciu", paths=["ciu"])}
            changed = detect_changed_projects(repo, projects)
            self.assertIn("ciu", [n for n, *_ in changed])

    def test_bump_conventional_feat(self):
        with _TempRepo() as repo:
            _commit(repo, "chore: init pkg", {"pkg/init.py": ""})
            _tag(repo, "pkg-v0.1.0")
            _commit(repo, "feat: add something", {"pkg/init.py": "# new"})
            projects = {"pkg": _ProjCfg("pkg-v", "pkg", paths=["pkg"])}
            changed = detect_changed_projects(repo, projects)
            self.assertEqual(len(changed), 1)
            _, _, _, bump = changed[0]
            self.assertEqual(bump, "minor")

    def test_bump_conventional_breaking(self):
        with _TempRepo() as repo:
            _commit(repo, "chore: init pkg", {"pkg/init.py": ""})
            _tag(repo, "pkg-v1.0.0")
            _commit(repo, "feat!: breaking api change", {"pkg/init.py": "# break"})
            projects = {"pkg": _ProjCfg("pkg-v", "pkg", paths=["pkg"])}
            changed = detect_changed_projects(repo, projects)
            _, _, _, bump = changed[0]
            self.assertEqual(bump, "major")

    def test_bump_patch_rule_overrides_conventional(self):
        """bump='patch' in config → always patch, even for feat: commits."""
        with _TempRepo() as repo:
            _commit(repo, "chore: init pkg", {"pkg/init.py": ""})
            _tag(repo, "pkg-v1.0.0")
            _commit(repo, "feat: something", {"pkg/init.py": "# feat"})
            projects = {"pkg": _ProjCfg("pkg-v", "pkg", paths=["pkg"], bump="patch")}
            changed = detect_changed_projects(repo, projects)
            _, _, _, bump = changed[0]
            self.assertEqual(bump, "patch")

    def test_path_isolation(self):
        """Commits outside the project's paths do NOT trigger it."""
        with _TempRepo() as repo:
            _commit(repo, "feat: init ciu", {"ciu/main.py": ""})
            _tag(repo, "ciu-v0.1.0")
            # Change only 'other', not 'ciu'
            _commit(repo, "feat: other change", {"other/lib.py": ""})
            projects = {"ciu": _ProjCfg("ciu-v", "ciu", paths=["ciu"])}
            self.assertEqual(detect_changed_projects(repo, projects), [])

    def test_shared_path_triggers_both_projects(self):
        """A shared-dep path listed by both projects triggers both when changed."""
        with _TempRepo() as repo:
            _commit(repo, "chore: init", {"ciu/main.py": "", "shared/lib.py": ""})
            _tag(repo, "ciu-v0.1.0")
            _tag(repo, "pkg-v0.1.0")
            _commit(repo, "fix: shared lib update", {"shared/lib.py": "# updated"})
            projects = {
                "ciu": _ProjCfg("ciu-v", "ciu", paths=["ciu", "shared"]),
                "pkg": _ProjCfg("pkg-v", "pkg", paths=["pkg", "shared"]),
            }
            changed = detect_changed_projects(repo, projects)
            names = [n for n, *_ in changed]
            self.assertIn("ciu", names)
            self.assertIn("pkg", names)

    def test_multiple_projects_only_changed_appears(self):
        """Two projects tagged; only the one with new commits appears."""
        with _TempRepo() as repo:
            _commit(repo, "feat: init both", {"ciu/main.py": "", "tls/main.py": ""})
            _tag(repo, "ciu-v0.1.0")
            _tag(repo, "tls-edge-v0.1.0")
            _commit(repo, "fix: only ciu touched", {"ciu/main.py": "# fix"})
            projects = {
                "ciu": _ProjCfg("ciu-v", "ciu", paths=["ciu"]),
                "tls-edge": _ProjCfg("tls-edge-v", "tls", paths=["tls"]),
            }
            changed = detect_changed_projects(repo, projects)
            names = [n for n, *_ in changed]
            self.assertIn("ciu", names)
            self.assertNotIn("tls-edge", names)

    def test_last_tag_is_correct(self):
        """The returned last_tag matches the highest-semver prior tag."""
        with _TempRepo() as repo:
            _commit(repo, "chore: init", {"pkg/a.py": ""})
            _tag(repo, "pkg-v0.1.0")
            _commit(repo, "fix: patch", {"pkg/a.py": "# p"})
            _tag(repo, "pkg-v0.1.1")
            _commit(repo, "feat: feature", {"pkg/a.py": "# f"})
            projects = {"pkg": _ProjCfg("pkg-v", "pkg", paths=["pkg"])}
            changed = detect_changed_projects(repo, projects)
            self.assertEqual(len(changed), 1)
            _, _, last_tag, _ = changed[0]
            self.assertEqual(last_tag, "pkg-v0.1.1")


# ---------------------------------------------------------------------------
# bump_version + counter integration
# ---------------------------------------------------------------------------

class TestBumpVersionCounterIntegration(unittest.TestCase):
    """Verify that the bump_version + _next_counter_version pair produces the right tags."""

    def test_scm_bump_chain(self):
        self.assertEqual(bump_version("0.1.0", "patch"), "0.1.1")
        self.assertEqual(bump_version("0.1.1", "minor"), "0.2.0")
        self.assertEqual(bump_version("0.2.0", "major"), "1.0.0")

    def test_counter_chain(self):
        with _TempRepo() as repo:
            for i in range(1, 4):
                _tag(repo, f"pwmcp-v1.61.0-r{i}")
            next_ver = _next_counter_version(repo, "pwmcp-v", "1.61.0")
            self.assertEqual(next_ver, "1.61.0-r4")


if __name__ == "__main__":
    unittest.main()
