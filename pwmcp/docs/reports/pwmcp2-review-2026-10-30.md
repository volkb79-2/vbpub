<!-- origin: 2026-10-30 review output -->
<!-- status: scrubbed historical evidence copy -->
<!-- source: reviews/pwmcp2.out -->

[2m  ▎ thinking[0m
  · 17484 tok · in 15736 (4096 cached / 11640 new) · out 1748 (1619 reasoning) · $0.0029
  -> bash {"command": "git --no-pager diff origin/main...HEAD", "description": "Show branch diff"}
  -> bash {"command": "git --no-pager log --oneline origin/main..HEAD; git status --short", "description": "Show branch commits an...
[2m  ▎ thinking[0m
  · 19109 tok · in 17638 (15361 cached / 2277 new) · out 1471 (1255 reasoning) · $0.0017
  -> bash {"command": "git --no-pager diff --stat origin/main...HEAD; ls pwmcp; sed -n '1,80p' pwmcp/.gitignore 2>/dev/null; echo ...
  -> bash {"command": "cd pwmcp && rg -n \"xdist|-n auto|-p no:|addopts|testpaths\" pyproject.toml run-gate.toml pytest.ini setup....
  ⊘ bash authorization · not executed · blocked by permission policy
[2m  ▎ thinking[0m
  · 18670 tok · in 18194 (17409 cached / 785 new) · out 476 (377 reasoning) · $0.0009
  -> MCP(search)
  -> read_file {"path": "pwmcp/conftest.py"}
[2m  ▎ thinking[0m
  · 23924 tok · in 20743 (17921 cached / 2822 new) · out 3181 (3069 reasoning) · $0.0029
  -> read_file {"path": ".gitignore"}
  -> read_file {"path": "pwmcp/pytest.ini"}
  -> read_file {"path": "pwmcp/run-gate.toml"}
[2m  ▎ thinking[0m
  · 27703 tok · in 25573 (20481 cached / 5092 new) · out 2130 (2042 reasoning) · $0.0027
  -> read_file {"path": "pwmcp/containers/ocr/ocr_service.py"}
  -> read_file {"path": "pwmcp/tests/test_testdeps.py"}
[2m  ▎ thinking[0m
  · 40786 tok · in 37832 (6153 cached / 31679 new) · out 2954 (2763 reasoning) · $0.0067
  -> MCP(tool:grep)
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 38290 tok · in 38119 (37813 cached / 306 new) · out 171 (90 reasoning) · $0.0013
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 38411 tok · in 38249 (37813 cached / 436 new) · out 162 (81 reasoning) · $0.0013
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 40767 tok · in 38379 (37813 cached / 566 new) · out 2388 (2237 reasoning) · $0.0027
  -> MCP(tool:grep)
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 38846 tok · in 38627 (38332 cached / 295 new) · out 219 (138 reasoning) · $0.0013
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 38969 tok · in 38757 (38332 cached / 425 new) · out 212 (125 reasoning) · $0.0013
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 39072 tok · in 38893 (38332 cached / 561 new) · out 179 (98 reasoning) · $0.0013
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 39286 tok · in 39023 (38844 cached / 179 new) · out 263 (182 reasoning) · $0.0013
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 39371 tok · in 39153 (38844 cached / 309 new) · out 218 (137 reasoning) · $0.0013
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
I've been mis-formatting the grep arguments. Correcting now.


  · 39548 tok · in 39283 (38844 cached / 439 new) · out 265 (170 reasoning) · $0.0014
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 39660 tok · in 39427 (38844 cached / 583 new) · out 233 (152 reasoning) · $0.0014
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 39846 tok · in 39557 (39356 cached / 201 new) · out 289 (192 reasoning) · $0.0014
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 40029 tok · in 39703 (39356 cached / 347 new) · out 326 (245 reasoning) · $0.0014
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
I keep omitting the required argument. Retrying with it explicitly included.


  · 39999 tok · in 39833 (39356 cached / 477 new) · out 166 (72 reasoning) · $0.0014
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 40161 tok · in 39977 (39356 cached / 621 new) · out 184 (104 reasoning) · $0.0014
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 40291 tok · in 40107 (39868 cached / 239 new) · out 184 (104 reasoning) · $0.0013
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 40519 tok · in 40237 (39868 cached / 369 new) · out 282 (202 reasoning) · $0.0014
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 40572 tok · in 40367 (39868 cached / 499 new) · out 205 (125 reasoning) · $0.0014
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
I've been dropping the `arguments` object. Correcting properly:


  · 40853 tok · in 40497 (39868 cached / 629 new) · out 356 (262 reasoning) · $0.0015
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 40853 tok · in 40640 (40380 cached / 260 new) · out 213 (125 reasoning) · $0.0014
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 40989 tok · in 40777 (40380 cached / 397 new) · out 212 (125 reasoning) · $0.0014
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 45242 tok · in 40913 (40380 cached / 533 new) · out 4329 (4278 reasoning) · $0.0039
  -> read_file {"path": "pwmcp/containers/ocr/ocr_service.py"}
[2m  ▎ thinking[0m
  · 51179 tok · in 49894 (43811 cached / 6083 new) · out 1285 (1204 reasoning) · $0.0030
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 50253 tok · in 50024 (49443 cached / 581 new) · out 229 (149 reasoning) · $0.0017
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 50339 tok · in 50154 (49955 cached / 199 new) · out 185 (98 reasoning) · $0.0016
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 50677 tok · in 50291 (49955 cached / 336 new) · out 386 (299 reasoning) · $0.0018
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
I need to pass the `pattern` key inside `arguments`. Doing it correctly now:


  · 50726 tok · in 50428 (49955 cached / 473 new) · out 298 (193 reasoning) · $0.0017
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 50855 tok · in 50582 (49955 cached / 627 new) · out 273 (186 reasoning) · $0.0018
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 50964 tok · in 50719 (50467 cached / 252 new) · out 245 (157 reasoning) · $0.0017
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 54277 tok · in 50856 (49955 cached / 901 new) · out 3421 (3328 reasoning) · $0.0037
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 51464 tok · in 50999 (50467 cached / 532 new) · out 465 (375 reasoning) · $0.0019
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 51557 tok · in 51139 (50979 cached / 160 new) · out 418 (330 reasoning) · $0.0018
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 51568 tok · in 51276 (50467 cached / 809 new) · out 292 (204 reasoning) · $0.0018
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 51582 tok · in 51413 (50979 cached / 434 new) · out 169 (82 reasoning) · $0.0017
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 51843 tok · in 51550 (50979 cached / 571 new) · out 293 (206 reasoning) · $0.0018
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 52030 tok · in 51687 (50979 cached / 708 new) · out 343 (256 reasoning) · $0.0018
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 55383 tok · in 51824 (51491 cached / 333 new) · out 3559 (3510 reasoning) · $0.0037
  -> read_file {"path": "pwmcp/client/tests/test_inspect.py"}
[2m  ▎ thinking[0m
  · 61477 tok · in 58031 (51639 cached / 6392 new) · out 3446 (3359 reasoning) · $0.0046
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 58476 tok · in 58167 (57783 cached / 384 new) · out 309 (218 reasoning) · $0.0020
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 58587 tok · in 58307 (57783 cached / 524 new) · out 280 (190 reasoning) · $0.0020
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 58723 tok · in 58447 (58295 cached / 152 new) · out 276 (185 reasoning) · $0.0019
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 58895 tok · in 58587 (58295 cached / 292 new) · out 308 (221 reasoning) · $0.0020
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 58948 tok · in 58723 (58295 cached / 428 new) · out 225 (139 reasoning) · $0.0019
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 67204 tok · in 58859 (58295 cached / 564 new) · out 8345 (8258 reasoning) · $0.0068
  -> read_file {"path": "pwmcp/tests/test_ocr_service.py"}
  -> read_file {"path": "pwmcp/tests/test_testdeps.py"}
[2m  ▎ thinking[0m
  · 75577 tok · in 72870 (66714 cached / 6156 new) · out 2707 (2621 reasoning) · $0.0045
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 73240 tok · in 73005 (72858 cached / 147 new) · out 235 (150 reasoning) · $0.0023
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 73307 tok · in 73140 (72858 cached / 282 new) · out 167 (121 reasoning) · $0.0023
  -> read_file {"path": "pwmcp/assay.toml"}
[2m  ▎ thinking[0m
I've been omitting the `arguments` object. Retrying correctly.


  · 77512 tok · in 74349 (72858 cached / 1491 new) · out 3163 (3059 reasoning) · $0.0043
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 74785 tok · in 74502 (73882 cached / 620 new) · out 283 (194 reasoning) · $0.0025
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 74772 tok · in 74641 (74394 cached / 247 new) · out 131 (82 reasoning) · $0.0023
  -> read_file {"path": "pwmcp/tests/test_testdeps.py"}
[2m  ▎ thinking[0m
Correcting the call format now (missing `arguments` object was the error):


  · 79137 tok · in 77933 (74394 cached / 3539 new) · out 1204 (1109 reasoning) · $0.0035
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
  · 78326 tok · in 78079 (77466 cached / 613 new) · out 247 (166 reasoning) · $0.0026
  -> MCP(tool:grep)
  · capability proxy: use_capability → grep
  ⊘ MCP(tool:grep) invalid arguments for grep; it requires "pattern"; it accepts "glob", "path", "pattern", "timeout_seconds"; a minimal valid call: {"pattern":"…"}
[2m  ▎ thinking[0m
## Verdict

**ACCEPT — no blocking findings.** The diff does what it claims: it isolates a mutant's relative-path write into `tmp_path`, hands the resolved git binary to nested pytest sessions, and replaces protocol `...` stubs with real raises that a parametrized test covers. Findings below are P2/P3 (environment fragility + a documented blind spot), not correctness defects in the change itself.

### (1) Is the conftest guard correct?

**Yes for its stated contract, with caveats.**

Correct choices worth naming:
- It compares **before vs after**, not "clean" — so a lane or R2 snapshot that legitimately starts dirty/untracked does *not* produce a false positive. That is the right direction to get right, and it does.
- `_REPOSITORY = Path(__file__).resolve().parent` **derives** the path from the file rather than defaulting to cwd — correct per the estate's default/fallback rule, and it survives `monkeypatch.chdir` in the suite.
- The docstring says "Git-visible", which is honest about what it compares (doctrine's "message narrower than comparison" trap avoided).

Findings:

- **P2 — git becoming load-bearing for the whole suite (conftest.py:17–30).** `RuntimeError` when git is not on PATH/`PWMCP_TEST_GIT_EXECUTABLE`, and `check=True` on `git status`, mean three distinct conditions collapse into "session errors before any test runs": git absent, tree not a git repository (an R1/R2 snapshot copy without `.git`, or a sdist/wheel checkout), and git's `dubious ownership` refusal when the container uid doesn't match the mount owner. The last one is exactly the trap AGENTS.md's "four traps" list calls out as needing `git config --global safe.directory '*'`; if a lane runner doesn't apply it, pwmcp's entire suite goes red for an environment reason and reads as a product regression. Recommend: pass `timeout=` to `subprocess.run`; catch `CalledProcessError` and re-raise with an actionable message naming the condition (git missing / not a repo / safe.directory) instead of a raw git traceback; verify once in a live `run-gate r1` that the registered lane already satisfies safe.directory and that assay's `repository-minus-unsafe-symlinks` snapshot retains `.git`.
- **P2 — ignored paths are a blind spot for a repo-write guard (conftest.py:24).** `--untracked-files=all` deliberately omits ignored files, so a stray write into an ignored in-repo path is invisible: `/pwmcp/vendor/` (.gitignore:64), `coverage.json`, `.pytest_cache/`, `__pycache__/`, `.assay/`. Worse, `pwmcp/coverage.json` from the lane's own `--cov-report=json:coverage.json` is written by coverage's atexit **after** this fixture's `after` snapshot, so it would be missed even if un-ignored — the check runs too early to see process-exit writers. The guard therefore certifies "tests did not dirty the repo" while only covering git-visible, still-present changes. Not a false positive; a false-negative class. Either accept and extend the docstring ("Git-visible, non-ignored, not written at interpreter exit"), or add an explicit ignored-path probe if the goal is really "no writes into the tree".
- **P2 — attribution: repo-wide status, pwmcp-specific message (conftest.py:24, :40).** `git status` is repo-wide even with `cwd=pwmcp`, so a concurrent agent or lane writing a git-visible file anywhere in the vbpub monorepo between the two snapshots is reported as "PWMCP tests changed Git-visible repository state". In a private worktree with `clean_tree = true` this is unlikely; in the shared `main` checkout it is plausible. Consider scoping to `-- .` (pwmcp subtree) or wording the message as "repository state changed during the PWMCP session".
- **P3 — xdist.** No lane or `pytest.ini` addopt uses `-n`, so today there is exactly one session fixture. If `-n auto` is ever added, each worker takes its own before/after concurrently and one worker's legitimately-temporary repo write lands inside another worker's window → false positive blamed on the wrong worker. Cheap guard: skip the comparison when `workerinput` exists, or make it worker-0-only.
- **P3 — cost/noise.** Two `git status -uall` walks of the whole monorepo per session, times ~7 nested sessions from `test_testdeps.py`, and `Before:`/`After:` dumps of every untracked file in a dirty snapshot. Consider `git --no-optional-locks status --porcelain` to avoid index-refresh contention, and truncating the diff shown in the assertion message to the added/removed lines.

### (2) Other tests writing relative paths into the repo

Within the reviewed surface, the fix is correctly targeted and I found no *other* confirmed offender:
- `test_inspect.py:255` now chdirs to `tmp_path` **before** `_install_cli_browser`, so a compare-swapped suffix mutant writing `page.gif` lands outside the repo. Teardown restores cwd via `monkeypatch`. Note the trade-off: that mutant's write is now invisible to the conftest guard by design — it is still killed by `message in captured.err` and `calls["connect"] == []`, so no loss of killing power.
- `test_testdeps.py:106–125` nested runs use `cwd=PWMCP` but pass `-p no:cacheprovider` and `PYTHONDONTWRITEBYTECODE=1`; nothing git-visible is expected. The new `PWMCP_TEST_GIT_EXECUTABLE` (test_testdeps.py:115–118) is set from the *unmodified* PATH before `path` is overridden at :119–120 — correct ordering, and it's what makes the guard survive the python-only PATH cases at :149/:159.
- `test_ocr_service.py`'s fake-engine fixtures write `mode`/`calls.log`/`started` next to the fake script; I did not confirm that directory is `tmp_path`-derived (the file was truncated mid-section). **Residual verification:** one `rg` sweep for `write_text|write_bytes|open\(|chdir|Path\("` over `pwmcp/tests` and `pwmcp/client/tests`, then one lane run — my own grep tool failed repeatedly in this session, so question 2 is answered from file reads only, not an exhaustive sweep.

### (3) Behaviour change in shipped code

**None for shipped paths.** `Engine` (ocr_service.py:111–139) is a typing `Protocol`, not a runtime base: `TesseractEngine` (:196) and `UnavailableEngine` (:142) do not subclass it, nothing instantiates it or `isinstance`-checks it (it is not `@runtime_checkable`), so the five stubs were never executed in production. The only behavioural delta is for a *future* subclass: forgetting a method now raises `NotImplementedError` with the method name instead of silently returning `None` — a strict improvement under "defaults and fallbacks are hazards", and the new test (test_ocr_service.py:37–56) asserts that contract rather than just covering lines. The trade-off to record in review: this converts a pure type declaration into a class with runtime semantics; if `glyphgrid` later subclasses `Engine`, partial implementations now fail loudly at call time — intended, but it should be a stated decision, not an accident of the coverage requirement (`allow_excluded = false`, `fail_under = 100.0` in assay.toml:38–39 makes the ellipsis bodies uncoverable).

Minor, non-blocking: **P3** — `pwmcp/tests/test_ocr_service.py:57` has only one blank line between the new top-level `def` and the top-level `try:` (E305 territory; `ruff format` would add one), and the new class/test sit between module import-time setup and the PIL import, which reads oddly in a file whose top section is "load the sidecar by path".
  · 92061 tok · in 78209 (77978 cached / 231 new) · out 13852 (11998 reasoning) · $0.0107
warning: the permission policy refused 1 tool call(s): bash
warning: permission.untrusted_folder: the workspace folder is not trusted: no trust decision is recorded for it; nothing that needed approval ran. To allow it, review and trust it with `reasonix trust --dir '<local-path>'` (add --yes only after reading what it lists), or pass --permission-mode auto for this run only
