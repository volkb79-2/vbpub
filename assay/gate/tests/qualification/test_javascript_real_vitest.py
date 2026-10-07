"""B041(c)/B087 -- qualification: real Vitest runs inside assay snapshots.

Every earlier JavaScript test -- `test_cli_run_javascript.py`, the R1 end-to-
end module -- drives `assay run` for real, but the LANE COMMAND itself is a
`/bin/sh -c` heredoc that writes `coverage-final.json` directly: a test
double for the producer (A-334's own definition), never a real `vitest`
process. `tester-unified` has no Node toolchain (DESIGN-GUIDE §10), so this
cannot be a registered-gate test either. This module is the missing proof:
skipped everywhere except a real Node/npm environment that explicitly opts
in, it builds an npm cache from the committed `probe-js` lockfile (B041(a)'s
offline-install pattern), materialises real two-commit git fixtures, and
drives the REAL `assay` CLI (`assay.cli.main`, the installed
`assay` console-script's entry point) against a REAL `npx --no-install
vitest run --coverage` inside assay's isolated snapshot. The R1 cases assert
coverage PASS/FAIL results; B087's R3 cases assert both canary transforms'
cause-specific outcomes plus survivor and broken-control refusals.

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

from assay.adapters.javascript import JavaScriptAdapter
from assay.cli import main
from gate.tests.support import PROJECT_ROOT, GitRepo

#: `tests/fixtures/coverage/probe-js/package.json` + `package-lock.json` pin
#: Vitest 3.2.4 and both coverage providers. They also pin test-only ESLint
#: and its TypeScript parser for the canary lint oracle. Reusing this committed
#: lockfile means the harness has no second dependency graph to keep in sync.
_PROBE_JS = PROJECT_ROOT / "tests" / "fixtures" / "coverage" / "probe-js"

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
      provider: '__COVERAGE_PROVIDER__',
      reporter: ['json'],
      reportsDirectory: '.assay',
      include: ['src/**'],
      clean: false,
    },
  },
})
"""

_PROBE_VITEST_CONFIG = """\
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    coverage: {
      provider: '__COVERAGE_PROVIDER__',
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
fail_under = {floor}
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


def _seed_project(
    repo: GitRepo,
    *,
    app_ts: str,
    app_test_ts: str,
    extra_sources: dict[str, str] | None = None,
) -> str:
    """Commit the shared project scaffolding plus one version of the source,
    and return that commit's SHA -- the diff `base`."""
    shutil.copy(_PROBE_JS / "package.json", repo.path / "package.json")
    shutil.copy(_PROBE_JS / "package-lock.json", repo.path / "package-lock.json")
    repo.write(
        "vitest.config.ts",
        _VITEST_CONFIG.replace("__COVERAGE_PROVIDER__", "istanbul"),
    )
    repo.write(".gitignore", _GITIGNORE)
    repo.write("src/app.ts", app_ts)
    repo.write("src/app.test.ts", app_test_ts)
    for path, text in (extra_sources or {}).items():
        repo.write(path, text)
    return repo.commit_all("base")


def _advance(repo: GitRepo, *, app_ts: str, app_test_ts: str) -> None:
    repo.write("src/app.ts", app_ts)
    repo.write("src/app.test.ts", app_test_ts)
    repo.commit_all("advance")


def _seed_commonjs_project(
    repo: GitRepo,
    *,
    app_js: str,
    app_test_ts: str,
) -> str:
    """Commit a real CommonJS package plus Vitest/Istanbul scaffolding."""
    package = json.loads((_PROBE_JS / "package.json").read_text(encoding="utf-8"))
    package["type"] = "commonjs"
    repo.write("package.json", json.dumps(package, indent=2) + "\n")
    shutil.copy(_PROBE_JS / "package-lock.json", repo.path / "package-lock.json")
    repo.write(
        "vitest.config.ts",
        _VITEST_CONFIG.replace("__COVERAGE_PROVIDER__", "istanbul"),
    )
    repo.write(".gitignore", _GITIGNORE)
    repo.write("src/app.js", app_js)
    repo.write("src/app.test.ts", app_test_ts)
    return repo.commit_all("commonjs base")


def _advance_commonjs_project(
    repo: GitRepo,
    *,
    app_js: str,
    app_test_ts: str,
) -> None:
    repo.write("src/app.js", app_js)
    repo.write("src/app.test.ts", app_test_ts)
    repo.commit_all("commonjs advance")


def _write_lane(
    repo: GitRepo,
    *,
    cache: Path,
    base: str,
    canary_mechanism: str | None = None,
    canary_target: str = "src/app.ts",
    fail_under: float = 100.0,
    include_r1: bool = True,
) -> Path:
    lane = _LANE_TOML.format(cache=cache, base=base, floor=fail_under)
    if canary_mechanism is not None:
        lane = lane.replace(
            'rigor = ["R0", "R1"]',
            'rigor = ["R0", "R1", "R3"]'
            if include_r1
            else 'rigor = ["R0", "R3"]',
        )
        if not include_r1:
            lane = lane.replace(f"fail_under = {fail_under}\n", "")
            lane = lane.replace(f'base = "{base}"\n', "")
            lane = lane.replace('allow_excluded = false\n', "")
            lane = lane.replace(
                '\n[lanes.ui.judge.coverage]\n'
                'format = "coverage-istanbul-json"\n'
                'artifact = ".assay/coverage-final.json"\n'
                'producer = "istanbul"\n',
                "\n",
            )
        lane += (
            "\n[lanes.ui.judge.canary]\n"
            f'mechanism = "{canary_mechanism}"\n'
            f'target = "{canary_target}"\n'
            'budget_per_attempt = "5m"\n'
        )
    path = repo.write("assay.toml", lane)
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


def _claims_by_rigor(verdict: dict) -> dict[str, dict]:
    return {claim["rigor"]: claim for claim in verdict["claims"]}


def _r3_attempt(verdict: dict) -> tuple[dict, dict]:
    claim = _claims_by_rigor(verdict)["R3"]
    return claim, claim["canary"]["attempts"][0]


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


def test_real_vitest_import_break_canary_is_caught_for_command_failure(
    git_repo: GitRepo, npm_cache: Path
):
    base = _seed_project(git_repo, app_ts=_ADD_ONLY, app_test_ts=_ADD_ONLY_TEST)
    _advance(git_repo, app_ts=_ADD_AND_MULTIPLY, app_test_ts=_ADD_AND_MULTIPLY_TEST)
    path = _write_lane(
        git_repo,
        cache=npm_cache,
        base=base,
        canary_mechanism="import-break",
    )

    code, verdict = _run_assay(path)

    assert code == 0, verdict
    assert verdict["outcome"] == "PASS"
    r3, attempt = _r3_attempt(verdict)
    assert r3["status"] == "PASS"
    assert attempt["control_outcome"] == "PASS"
    assert attempt["transformed_outcome"] == "FAIL"
    assert attempt["expected_reason_code"] == "COMMAND_FAILED"
    assert attempt["observed_reason_code"] == "COMMAND_FAILED"


def test_real_vitest_uncovered_line_canary_is_caught_by_coverage(
    git_repo: GitRepo,
    npm_cache: Path,
):
    base = _seed_project(git_repo, app_ts=_ADD_ONLY, app_test_ts=_ADD_ONLY_TEST)
    _advance(git_repo, app_ts=_ADD_AND_MULTIPLY, app_test_ts=_ADD_AND_MULTIPLY_TEST)
    path = _write_lane(
        git_repo, cache=npm_cache, base=base, canary_mechanism="uncovered-line"
    )

    code, verdict = _run_assay(path)

    assert code == 0, verdict
    assert verdict["outcome"] == "PASS"
    r3, attempt = _r3_attempt(verdict)
    assert r3["status"] == "PASS"
    assert attempt["control_outcome"] == "PASS"
    assert attempt["transformed_outcome"] == "FAIL"
    assert attempt["expected_reason_code"] == "UNCOVERED_LINES"
    assert attempt["observed_reason_code"] == "UNCOVERED_LINES"


def test_real_vitest_uncovered_line_canary_works_for_commonjs_javascript(
    git_repo: GitRepo, npm_cache: Path
):
    """A `.js` target in a CommonJS package must reach the coverage failure,
    not fail on module syntax or target-shadowed standard builtins."""
    add_only = """\
const Object = { defineProperty() { throw new Error('shadowed Object used') } }
const Symbol = () => { throw new Error('shadowed Symbol used') }
function add(a, b) {
  return a + b
}
module.exports = { add }
"""
    add_only_test = """\
import { expect, test } from 'vitest'
import app from './app.js'
test('add', () => {
  expect(app.add(1, 2)).toBe(3)
})
"""
    add_and_multiply = add_only.replace(
        "module.exports = { add }",
        "function multiply(a, b) {\n  return a * b\n}\n"
        "module.exports = { add, multiply }",
    )
    add_and_multiply_test = """\
import { expect, test } from 'vitest'
import app from './app.js'
test('add', () => {
  expect(app.add(1, 2)).toBe(3)
})
test('multiply', () => {
  expect(app.multiply(2, 3)).toBe(6)
})
"""
    base = _seed_commonjs_project(
        git_repo, app_js=add_only, app_test_ts=add_only_test
    )
    _advance_commonjs_project(
        git_repo, app_js=add_and_multiply, app_test_ts=add_and_multiply_test
    )
    path = _write_lane(
        git_repo,
        cache=npm_cache,
        base=base,
        canary_mechanism="uncovered-line",
        canary_target="src/app.js",
    )

    code, verdict = _run_assay(path)

    assert code == 0, verdict
    assert verdict["outcome"] == "PASS"
    claims = _claims_by_rigor(verdict)
    assert claims["R0"]["status"] == "PASS"
    assert claims["R1"]["status"] == "PASS"
    assert claims["R3"]["status"] == "PASS"
    attempt = claims["R3"]["canary"]["attempts"][0]
    assert attempt["control_outcome"] == "PASS"
    assert attempt["transformed_outcome"] == "FAIL"
    assert attempt["expected_reason_code"] == "UNCOVERED_LINES"
    assert attempt["observed_reason_code"] == "UNCOVERED_LINES"


def test_uncovered_line_canary_typechecks_with_no_unused_locals(
    tmp_path: Path, npm_cache: Path
):
    """The portable function expression remains valid TypeScript without an
    export or an unused top-level binding."""
    shutil.copy(_PROBE_JS / "package.json", tmp_path / "package.json")
    shutil.copy(_PROBE_JS / "package-lock.json", tmp_path / "package-lock.json")
    subprocess.run(
        [
            "npm",
            "ci",
            "--offline",
            "--cache",
            str(npm_cache),
            "--no-audit",
            "--no-fund",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )
    source = "export const original = 42\n"
    transformed, _description = JavaScriptAdapter().inject_uncovered_line(source)
    target = tmp_path / "uncovered-canary.ts"
    target.write_text(transformed, encoding="utf-8")
    tsconfig = tmp_path / "tsconfig.json"
    tsconfig.write_text(
        json.dumps(
            {
                "compilerOptions": {
                    "noEmit": True,
                    "strict": True,
                    "noUnusedLocals": True,
                    "noImplicitAny": True,
                    "skipLibCheck": True,
                    "target": "ES2022",
                    "types": [],
                },
                "files": [target.name],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["npx", "--no-install", "tsc", "--project", str(tsconfig)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_uncovered_line_canary_lints_clean_for_js_and_ts(
    tmp_path: Path, npm_cache: Path
):
    """The appended expression survives common lint rules without `void`,
    an unused bare expression, or unused generated bindings. Bad controls
    prove each configured rule is active."""
    shutil.copy(_PROBE_JS / "package.json", tmp_path / "package.json")
    shutil.copy(_PROBE_JS / "package-lock.json", tmp_path / "package-lock.json")
    subprocess.run(
        [
            "npm",
            "ci",
            "--offline",
            "--cache",
            str(npm_cache),
            "--no-audit",
            "--no-fund",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )
    (tmp_path / "eslint.config.mjs").write_text(
        """\
import parser from '@typescript-eslint/parser'

export default [{
  files: ['**/*.js', '**/*.ts'],
  languageOptions: { parser },
  rules: {
    'no-void': 'error',
    'no-unused-expressions': 'error',
    'no-unused-vars': 'error',
  },
}]
""",
        encoding="utf-8",
    )
    sources = {
        "canary.js": "function add(a, b) { return a + b }\nmodule.exports = { add }\n",
        "canary.ts": "export function add(a: number, b: number): number { return a + b }\n",
    }
    good_paths = []
    for name, source in sources.items():
        transformed, _description = JavaScriptAdapter().inject_uncovered_line(source)
        target = tmp_path / name
        target.write_text(transformed, encoding="utf-8")
        good_paths.append(target)
    bad_path = tmp_path / "bad-control.js"
    bad_path.write_text("void 0;\n1 + 1;\nconst unused = 1;\n", encoding="utf-8")

    result = subprocess.run(
        [
            "npx",
            "--no-install",
            "eslint",
            "--format",
            "json",
            *(str(path) for path in (*good_paths, bad_path)),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    reports = {Path(item["filePath"]).name: item for item in json.loads(result.stdout)}
    for path in good_paths:
        assert reports[path.name]["messages"] == []
    bad_rules = {message["ruleId"] for message in reports[bad_path.name]["messages"]}
    assert {"no-void", "no-unused-expressions", "no-unused-vars"} <= bad_rules


@pytest.mark.parametrize("coverage_provider", ["istanbul", "v8"])
def test_live_vitest_provider_report_proves_passed_suite_and_missing_canary_lines(
    tmp_path: Path, npm_cache: Path, coverage_provider: str
):
    """Each real provider must write a successful test report while its
    coverage artifact marks the appended function body as unexecuted."""
    shutil.copy(_PROBE_JS / "package.json", tmp_path / "package.json")
    shutil.copy(_PROBE_JS / "package-lock.json", tmp_path / "package-lock.json")
    source_root = tmp_path / "src"
    shutil.copytree(_PROBE_JS / "src", source_root)
    original = (_PROBE_JS / "src" / "roles.ts").read_text(encoding="utf-8")
    transformed, _description = JavaScriptAdapter().inject_uncovered_line(original)
    injected = source_root / "roles.ts"
    injected.write_text(transformed, encoding="utf-8")
    (tmp_path / "vitest.config.ts").write_text(
        _PROBE_VITEST_CONFIG.replace("__COVERAGE_PROVIDER__", coverage_provider),
        encoding="utf-8",
    )
    subprocess.run(
        [
            "npm",
            "ci",
            "--offline",
            "--cache",
            str(npm_cache),
            "--no-audit",
            "--no-fund",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assay_dir = tmp_path / ".assay"
    assay_dir.mkdir()

    result = subprocess.run(
        [
            "npx",
            "--no-install",
            "vitest",
            "run",
            "--coverage",
            "--reporter=json",
            "--outputFile=.assay/vitest-test-report.json",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    test_report = json.loads(
        (assay_dir / "vitest-test-report.json").read_text(encoding="utf-8")
    )
    assert test_report["numFailedTests"] == 0
    assert test_report["numPassedTests"] > 0
    from assay.coverage import load_coverage_profile

    profile = load_coverage_profile(
        (assay_dir / "coverage-final.json").read_text(encoding="utf-8"),
        declared_format="coverage-istanbul-json",
    )
    (record,) = [
        value for key, value in profile.files.items() if key.endswith("/roles.ts")
    ]
    body = [
        number
        for number, text in enumerate(transformed.splitlines(), start=1)
        if text.strip()
        in (
            "const doubled = value * 2 // assay-canary: executed by no test",
            "return doubled",
        )
    ]
    assert len(body) == 2
    assert set(body) <= record.missing
    assert not (set(body) & record.executed)


def test_uncovered_line_canary_lints_clean_for_js_and_ts(
    tmp_path: Path, npm_cache: Path
):
    """The appended expression survives common lint rules without `void`,
    an unused bare expression, or unused generated bindings. Bad controls
    prove each configured rule is active."""
    shutil.copy(_PROBE_JS / "package.json", tmp_path / "package.json")
    shutil.copy(_PROBE_JS / "package-lock.json", tmp_path / "package-lock.json")
    subprocess.run(
        [
            "npm",
            "ci",
            "--offline",
            "--cache",
            str(npm_cache),
            "--no-audit",
            "--no-fund",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )
    (tmp_path / "eslint.config.mjs").write_text(
        """\
import parser from '@typescript-eslint/parser'

export default [{
  files: ['**/*.js', '**/*.ts'],
  languageOptions: { parser },
  rules: {
    'no-void': 'error',
    'no-unused-expressions': 'error',
    'no-unused-vars': 'error',
  },
}]
""",
        encoding="utf-8",
    )
    sources = {
        "canary.js": "function add(a, b) { return a + b }\nmodule.exports = { add }\n",
        "canary.ts": "export function add(a: number, b: number): number { return a + b }\n",
    }
    good_paths = []
    for name, source in sources.items():
        transformed, _description = JavaScriptAdapter().inject_uncovered_line(source)
        target = tmp_path / name
        target.write_text(transformed, encoding="utf-8")
        good_paths.append(target)
    bad_path = tmp_path / "bad-control.js"
    bad_path.write_text("void 0;\n1 + 1;\nconst unused = 1;\n", encoding="utf-8")

    result = subprocess.run(
        [
            "npx",
            "--no-install",
            "eslint",
            "--format",
            "json",
            *(str(path) for path in (*good_paths, bad_path)),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    reports = {Path(item["filePath"]).name: item for item in json.loads(result.stdout)}
    for path in good_paths:
        assert reports[path.name]["messages"] == []
    bad_rules = {message["ruleId"] for message in reports[bad_path.name]["messages"]}
    assert {"no-void", "no-unused-expressions", "no-unused-vars"} <= bad_rules


def test_an_import_break_not_reached_by_the_tests_is_reported_as_survived(
    git_repo: GitRepo, npm_cache: Path
):
    base = _seed_project(
        git_repo,
        app_ts=_ADD_ONLY,
        app_test_ts=_ADD_ONLY_TEST,
        extra_sources={"src/unimported.ts": "export function dormant() { return 1 }\n"},
    )
    _advance(git_repo, app_ts=_ADD_AND_MULTIPLY, app_test_ts=_ADD_AND_MULTIPLY_TEST)
    path = _write_lane(
        git_repo,
        cache=npm_cache,
        base=base,
        canary_mechanism="import-break",
        canary_target="src/unimported.ts",
    )

    code, verdict = _run_assay(path)

    assert code != 0
    assert verdict["outcome"] == "FAIL"
    r3, attempt = _r3_attempt(verdict)
    assert r3["status"] == "FAIL"
    assert r3["reason_code"] == "CANARY_SURVIVED"
    assert attempt["control_outcome"] == "PASS"
    assert attempt["expected_reason_code"] == "COMMAND_FAILED"
    assert attempt["observed_reason_code"] != "COMMAND_FAILED"


def test_an_uncovered_line_canary_survives_when_the_coverage_floor_is_zero(
    git_repo: GitRepo, npm_cache: Path
):
    base = _seed_project(git_repo, app_ts=_ADD_ONLY, app_test_ts=_ADD_ONLY_TEST)
    _advance(git_repo, app_ts=_ADD_AND_MULTIPLY, app_test_ts=_ADD_AND_MULTIPLY_TEST)
    path = _write_lane(
        git_repo,
        cache=npm_cache,
        base=base,
        canary_mechanism="uncovered-line",
        fail_under=0.0,
    )

    code, verdict = _run_assay(path)

    assert code != 0
    assert verdict["outcome"] == "FAIL"
    r3, attempt = _r3_attempt(verdict)
    assert r3["status"] == "FAIL"
    assert r3["reason_code"] == "CANARY_SURVIVED"
    assert attempt["control_outcome"] == "PASS"
    assert attempt["expected_reason_code"] == "UNCOVERED_LINES"
    assert attempt["transformed_outcome"] == "PASS"


def test_a_broken_control_never_passes_the_r3_canary(
    git_repo: GitRepo, npm_cache: Path
):
    base = _seed_project(git_repo, app_ts=_ADD_ONLY, app_test_ts=_ADD_ONLY_TEST)
    broken_tests = _ADD_AND_MULTIPLY_TEST.replace("toBe(6)", "toBe(7)")
    _advance(git_repo, app_ts=_ADD_AND_MULTIPLY, app_test_ts=broken_tests)
    path = _write_lane(
        git_repo,
        cache=npm_cache,
        base=base,
        canary_mechanism="import-break",
        include_r1=False,
    )

    code, verdict = _run_assay(path)

    assert code != 0
    assert verdict["outcome"] == "FAIL"
    r3, attempt = _r3_attempt(verdict)
    assert r3["status"] == "INCONCLUSIVE"
    assert r3["reason_code"] == "CANARY_INCONCLUSIVE"
    assert attempt["control_outcome"] == "FAIL"
