"""B041(c) -- qualification: a real Vitest run inside a real assay snapshot.

Every earlier JavaScript test -- `test_cli_run_javascript.py`, the R1 end-to-
end module -- drives `assay run` for real, but the LANE COMMAND itself is a
`/bin/sh -c` heredoc that writes `coverage-final.json` directly: a test
double for the producer (A-334's own definition), never a real `vitest`
process. `tester-unified` has no Node toolchain (DESIGN-GUIDE §10), so this
cannot be a registered-gate test either. This module is the missing proof:
skipped everywhere except a real Node/npm environment that explicitly opts
in, it builds an npm cache from the committed `probe-js` lockfile (B041(a)'s
offline-install pattern), materialises a real two-commit git fixture, and
drives the REAL `assay` CLI (`assay.cli.main`, the identical entry point the
installed `assay` console-script wraps) against a REAL `npx --no-install
vitest run --coverage` inside assay's own isolated snapshot -- asserting one
genuine PASS and one genuine FAIL that names the uncovered line.

Running this for real is also what surfaced B049 (A-347): Vitest's own
DEFAULT `coverage.clean = true` silently breaks assay's coverage-artifact
reservation (`safeio.reserve_output` holds a parent-directory descriptor
across the whole command; a tool that deletes and recreates that directory,
rather than writing into the one assay already opened, orphans it), reading
a fully-covered real run as `NO_MEASUREMENT`/`EMPTY_COVERAGE`. Every
`vitest.config.ts` this module writes therefore declares `clean: false` --
required, not a preference; see docs/CONSUMERS.md's own note and B049 for
the mechanism and the measured before/after.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import GitRepo

from assay.cli import main

#: `tests/fixtures/coverage/probe-js/package.json` + `package-lock.json` pin
#: `vitest`/`@vitest/coverage-istanbul` 3.2.4 -- the SAME committed lockfile
#: B036's own fixtures were produced from, reused here so this harness needs
#: no lockfile of its own to keep in sync.
_PROBE_JS = (
    Path(__file__).resolve().parents[1] / "fixtures" / "coverage" / "probe-js"
)

_ENV_REASON = (
    "real-vitest qualification: needs ASSAY_NODE_QUALIFICATION=1 and node/npm "
    "on PATH. tester-unified has no Node toolchain (DESIGN-GUIDE §10), so "
    "this can never be a registered-gate test; it runs by explicit opt-in "
    "wherever Node genuinely is available (this devcontainer included)."
)


def _node_qualification_enabled() -> bool:
    import os

    return (
        os.environ.get("ASSAY_NODE_QUALIFICATION") == "1"
        and shutil.which("node") is not None
        and shutil.which("npm") is not None
    )


pytestmark = pytest.mark.skipif(not _node_qualification_enabled(), reason=_ENV_REASON)

#: B049/A-347 -- `clean: false` is REQUIRED, not a style choice: Vitest's own
#: default (`clean: true`) deletes and recreates `reportsDirectory` before
#: writing, which orphans assay's held reservation and reads a fully-covered
#: run as `EMPTY_COVERAGE`. See the module docstring and docs/CONSUMERS.md.
_VITEST_CONFIG = """\
import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    coverage: {
      provider: 'istanbul',
      reporter: ['json'],
      reportsDirectory: '.assay',
      include: ['src/**'],
      clean: false,
    },
  },
})
"""

_GITIGNORE = "node_modules/\n.assay/\n"

#: B041(a)'s own worked pattern: an OFFLINE install against a pre-populated
#: cache, then the PINNED, `--no-install` runner -- never a bare `npx vitest`,
#: which would fetch an unpinned package from the network the instant the
#: snapshot's own `node_modules` (absent by construction, B041) is missing.
_LANE_TOML = """\
schema_version = 2

[lanes.ui]
scope = "S1"
rigor = ["R0", "R1"]
enforcement = "gate"
argv = ["bash", "-c",
  "npm ci --offline --no-audit --no-fund && npx --no-install vitest run --coverage"]
env = {{ npm_config_cache = "{cache}" }}
env_passthrough = ["PATH", "HOME"]
budget = "5m"
allow_argv_append = false

[lanes.ui.isolation]
snapshot_selection = "repository"

[lanes.ui.judge]
language = "javascript"
source_roots = ["src"]
fail_under = 100.0
allow_excluded = false
base = "{base}"

[lanes.ui.judge.coverage]
format = "coverage-istanbul-json"
artifact = ".assay/coverage-final.json"
producer = "istanbul"
"""

_ADD_ONLY = """\
export function add(a: number, b: number): number {
  return a + b
}
"""

_ADD_ONLY_TEST = """\
import { expect, test } from 'vitest'
import { add } from './app'
test('add', () => {
  expect(add(1, 2)).toBe(3)
})
"""

#: PASS scenario: a second, fully-tested function.
_ADD_AND_MULTIPLY = _ADD_ONLY + """
export function multiply(a: number, b: number): number {
  return a * b
}
"""

_ADD_AND_MULTIPLY_TEST = """\
import { expect, test } from 'vitest'
import { add, multiply } from './app'
test('add', () => {
  expect(add(1, 2)).toBe(3)
})
test('multiply', () => {
  expect(multiply(2, 3)).toBe(6)
})
"""

#: FAIL scenario: an added guard whose defensive branch (line 7, `return -1`)
#: the test never exercises -- the ONE genuinely uncovered line in the diff.
_ADD_AND_GUARD = _ADD_ONLY + """
export function guard(v: number): number {
  if (v < 0) {
    return -1
  }
  return v
}
"""

_ADD_AND_GUARD_TEST = """\
import { expect, test } from 'vitest'
import { add, guard } from './app'
test('add', () => {
  expect(add(1, 2)).toBe(3)
})
test('guard', () => {
  expect(guard(5)).toBe(5)
})
"""


@pytest.fixture(scope="module")
def npm_cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A private, offline-replayable npm cache, populated ONCE (network,
    B009's image-baked-cache doctrine applied at test time rather than image
    build time) from `probe-js`'s own committed lockfile pair. Every lane run
    below points `npm_config_cache` at this SAME directory and installs
    `--offline` -- B041(a)'s pattern, proven against a real registry rather
    than asserted."""
    cache_dir = tmp_path_factory.mktemp("npm-cache")
    build_dir = tmp_path_factory.mktemp("npm-cache-build")
    shutil.copy(_PROBE_JS / "package.json", build_dir / "package.json")
    shutil.copy(_PROBE_JS / "package-lock.json", build_dir / "package-lock.json")
    subprocess.run(
        ["npm", "ci", "--cache", str(cache_dir), "--no-audit", "--no-fund"],
        cwd=build_dir,
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )
    return cache_dir


def _seed_project(repo: GitRepo, *, app_ts: str, app_test_ts: str) -> str:
    """Commit the shared project scaffolding plus one version of the source,
    and return that commit's SHA -- the diff `base`."""
    shutil.copy(_PROBE_JS / "package.json", repo.path / "package.json")
    shutil.copy(_PROBE_JS / "package-lock.json", repo.path / "package-lock.json")
    repo.write("vitest.config.ts", _VITEST_CONFIG)
    repo.write(".gitignore", _GITIGNORE)
    repo.write("src/app.ts", app_ts)
    repo.write("src/app.test.ts", app_test_ts)
    return repo.commit_all("base")


def _advance(repo: GitRepo, *, app_ts: str, app_test_ts: str) -> None:
    repo.write("src/app.ts", app_ts)
    repo.write("src/app.test.ts", app_test_ts)
    repo.commit_all("advance")


def _write_lane(repo: GitRepo, *, cache: Path, base: str) -> Path:
    path = repo.write("assay.toml", _LANE_TOML.format(cache=cache, base=base))
    # A clean INVOKING checkout, not the snapshot: `assay run`'s own
    # preflight refuses `NO_MEASUREMENT`/`DIRTY_TREE` on an untracked
    # assay.toml exactly as it would on any other untracked file.
    repo.commit_all("add assay.toml")
    return path


def _run_assay(path: Path) -> tuple[int, dict]:
    out, err = io.StringIO(), io.StringIO()
    code = main(["run", "ui", "--file", str(path), "--verdict-json", "-"], stdout=out, stderr=err)
    stdout, stderr = out.getvalue(), err.getvalue()
    print(f"$ assay run ui --file {path} --verdict-json -\nexit={code}\nSTDERR:\n{stderr}\nSTDOUT:\n{stdout}")
    return code, json.loads(stdout)


def test_a_real_javascript_lane_passes_end_to_end(git_repo: GitRepo, npm_cache: Path):
    """Real npm, real Vitest, real assay CLI. A fully-covered two-commit
    diff must PASS with exactly the coverage the diff actually has."""
    base = _seed_project(git_repo, app_ts=_ADD_ONLY, app_test_ts=_ADD_ONLY_TEST)
    _advance(git_repo, app_ts=_ADD_AND_MULTIPLY, app_test_ts=_ADD_AND_MULTIPLY_TEST)
    path = _write_lane(git_repo, cache=npm_cache, base=base)

    code, verdict = _run_assay(path)

    assert code == 0, verdict
    assert verdict["outcome"] == "PASS"
    r1 = verdict["claims"][1]
    assert r1["rigor"] == "R1"
    assert r1["status"] == "PASS"
    assert r1["coverage"]["pct"] == 100.0
    # Only `multiply`'s own body statement is executable in istanbul's
    # accounting -- the signature and closing-brace lines are unattributed
    # (A-342's own "function declaration line falls to rule 4"), so a
    # one-line function body measures as exactly 1/1, not 2.
    assert r1["coverage"]["executable"] == 1
    assert r1["coverage"]["covered"] == 1
    assert r1["coverage"]["missing_lines"] == {}


def test_a_real_javascript_lane_fails_and_names_the_uncovered_line(
    git_repo: GitRepo, npm_cache: Path
):
    """The paired failure: a real, genuinely uncovered defensive branch
    (line 7's `return -1`, never reached by the test's only call,
    `guard(5)`) must render FAIL/UNCOVERED_LINES naming exactly that line --
    not a heredoc's idea of what Vitest would say, the real thing."""
    base = _seed_project(git_repo, app_ts=_ADD_ONLY, app_test_ts=_ADD_ONLY_TEST)
    _advance(git_repo, app_ts=_ADD_AND_GUARD, app_test_ts=_ADD_AND_GUARD_TEST)
    path = _write_lane(git_repo, cache=npm_cache, base=base)

    code, verdict = _run_assay(path)

    assert code != 0
    assert verdict["outcome"] == "FAIL"
    r1 = verdict["claims"][1]
    assert r1["rigor"] == "R1"
    assert r1["status"] == "FAIL"
    assert r1["reason_code"] == "UNCOVERED_LINES"
    assert r1["coverage"]["missing_lines"] == {"src/app.ts": [7]}
    assert r1["coverage"]["executable"] == 4
    assert r1["coverage"]["covered"] == 3
