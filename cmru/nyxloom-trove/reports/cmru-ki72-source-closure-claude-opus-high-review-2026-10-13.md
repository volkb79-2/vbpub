# CMRU KI-72 source-closure review

- Model: `claude-opus-5-5`, effort `high`
- Session: `38b7def2-daed-4157-af53-25bc9b354fde`
- HEAD before/after: `336336d81b9af9fd7d7a9ad3a4a272ca01c66071` / `336336d81b9af9fd7d7a9ad3a4a272ca01c66071`
- Status before/after identical: `True`
- Content snapshot before/after identical: `True`
- Diff snapshot SHA-256: `fe87689ca79fe03664f372655f05cace644d5c4c566d7171d4ef892829120aa7`
- Claude exit: `0`

# CMRU KI-72 review: not ready to ship. The registered mutation lane will refuse every run

I made no edits, commits, test runs or gate runs. I used only read-only `git` commands, file reads and `grep`.

## Snapshots

| | before (2026-10-13T12:21:35Z) | after (2026-10-13T12:25:37Z) |
|---|---|---|
| HEAD | `336336d81b9af9fd7d7a9ad3a4a272ca01c66071` | same |
| `git status --porcelain` sha256 | `acd1c160…3d51be9` | same |
| `git diff` sha256 | `1a944b0b…d3a4a272ca` → full `1a944b0b189ab20ac6d974c9058661637f444f0efda3c71666d72d8dbfa36cd3` | same |
| `git diff -- cmru/` sha256 | `4a9cad314bb5593ed8acb670ffba600eba1098e664f7fc0b7c2fa9a09f6a8006` | same |
| `mutation_campaign.py` sha256 | `bf7cd6b5…88791d83` (1191 lines) | same |

The status lists one untracked report stub that wasn't there in the previous session: `cmru/nyxloom-trove/reports/cmru-ki72-source-closure-claude-opus-high-review-2026-10-13.md`. It isn't part of the reviewed code.

## Findings

### BLOCKER 1 — The registered mutation lane always refuses because of Run-Gate's own state files (confirmed)

- **Where:** `cmru/tools/mutation_campaign.py:304-316` (`_campaign_tree_status`), `:573-591` (`_is_generated_ignored_path`), and the refusal at `:885-886`.
- **What the check covers:** the status pathspec is all of `cmru/`, minus only `cmru/.assay`, plus `libraries/worktree` and the fixture files. With `include_ignored=True` it lists every ignored file in that area. Anything not on the allowlist (`__pycache__/*.pyc`, `*.egg-info`, `.pytest_cache`, `.coverage*`) makes the result non-empty, and the campaign refuses.
- **What Run-Gate puts there:** Run-Gate keeps its state in `<project>/.run-gate/` (`run-gate.py:4272-4273`). It writes the record for the running lane to `cmru/.run-gate/inflight/mutation.json` plus `inflight.lock` (`run-gate.py:4944-4960`). History goes to `history.json` and `history.lock`. Run-Gate also requires `.run-gate/` to be gitignored (`:4885`). That directory sits inside the judged worktree, which the lane bind-mounts.
- **Measured in this worktree:** the same ignored-file listing over the campaign's pathspecs, minus pycache and pytest-cache entries, returns `cmru/.run-gate/{history.json,history.lock}` and `cmru/.run-gate/inflight/inflight.lock`.
- **Failure:** every `./run-gate.py mutation`, and the release gate's mutation step, raises "mutation campaign requires committed source, tests, and pytest configuration". This happens even on a fresh checkout, because the inflight record exists while the lane runs.
- **A second guaranteed trigger in the release flow:** `run_release_gate.py:549` runs `coverage` before `mutation`. The coverage lane (`run-gate.toml:58`) leaves `cmru/coverage.json` and `cmru/junit-coverage.xml` in the worktree. Both are gitignored (`.gitignore:104-105`) and neither is on the allowlist.
- **Latent triggers:** `libraries/worktree/.assay/` and `libraries/worktree/.run-gate/` are not excluded either, so running that library's own lanes in the same worktree would also refuse CMRU.
- **Why it's also a message problem:** the refusal blames "committed source, tests" for Run-Gate bookkeeping. That is the "superset refusal" anti-pattern. The backlog entry correctly says "pending registered gate", but this lane cannot pass as written. No test constructs this ordinary state: `test_mutation_campaign.py:318` covers only `.pytest_cache`.

### HIGH 2 — The candidate loop has no source or HEAD binding after the baseline (confirmed by code path)

- **Where:** `mutation_campaign.py:994-999` rechecks status and both HEADs once, after the baseline. `:1015` computes `test_manifest` once.
- **The gap:** every candidate then re-copies the live tree (`:1052`, `copy_project_fixture`). The only per-candidate check is the mutated target's text (`:1057`). Nothing is rechecked before `status = "passed"` (`:1170`).
- **Failure:** during a run that lasts hours, someone edits `cmru/tests/…` or `libraries/worktree/src/…` in the bind-mounted worktree, or switches branches. Later candidates then run against tests or libraries that differ from the recorded `head` and `test_manifest`. Kills produced that way are written as passed evidence for `head`. A later resume would catch edits that persist (the manifest is byte-hashed), but a run that finishes in one go never compares again.
- **Assay source:** it is also unchecked after the baseline, though only lazily-imported modules are exposed.
- **Fix:** freeze one fixture template right after the post-baseline check and copy every candidate from it. Alternatively, recheck `_campaign_tree_status`, `_assay_source_tree_status`, both HEADs and `_test_manifest` after each candidate and before the final status. That costs a few git calls per candidate, against pytest runs of seconds to minutes.

### MEDIUM 3 — Allowlisted "generated" files can change what candidates execute (hypothesis; needs exotic or adversarial input)

- `copy_project_fixture` copies all of `cmru/` with `copytree`, ignored files included (`project_fixture.py:73-74`).
- **egg-info:** an accepted `cmru/src/*.egg-info` lands on the candidates' `PYTHONPATH=src`. `importlib.metadata` then sees it, and pytest auto-loads any `pytest11` entry points it declares. `installed_wheel_smoke.py:100-104` already documents that a stray `cmru.egg-info` changes later processes.
- **pyc files:** an `unchecked-hash` pyc in `__pycache__` runs without any comparison to its source, in candidates and in the campaign's own Assay import. Python still reads pycs when `PYTHONDONTWRITEBYTECODE=1` is set.
- **Doc impact:** the claim that these files are inert generated caches is not proven.
- **Fix:** build fixtures only from tracked paths (`git ls-files`), which the clean-status check already proves equal HEAD. Set `sys.pycache_prefix` and `PYTHONPYCACHEPREFIX` to a fresh temporary directory before importing Assay and running candidates. With both, ignored files can't affect execution, and the ignored-file check can shrink to "ignored `*.py`, `*.pth`, `conftest.py`, `.so`, or metadata under importable roots" — which also resolves Blocker 1 without growing the allowlist.

### MEDIUM 4 — The docs describe the refusal more narrowly than the code applies it

- README (diff hunk around `:737`), SPEC, DESIGN-GUIDE and KI-72 all say ignored *source or test* files refuse, and that "coverage data" is allowed.
- The code actually refuses **any** ignored file in the closure except four filename shapes. That includes Run-Gate state, `coverage.json` and `junit-coverage.xml`, which an operator would reasonably read as "coverage data".
- Once Blocker 1 is fixed, the docs need to state the exact rule. Today they promise a narrower refusal than the code enforces.

### LOW

- **L1 — unsanitized git in `_test_manifest`:** `_test_manifest` (`:639`) still uses plain `git ls-files` with the inherited environment (`GIT_DIR`, `GIT_INDEX_FILE`, global config), unlike the new independent boundary. A redirected index could fingerprint a different file list. Route it through `_git_command_context`.
- **L2 — dead legacy branches:** `:763-777` (missing `test_manifest`, or missing `fixture_files`) can't be reached by any evidence this writer produces now that resume requires schema 5. They only widen what a hand-edited schema-5 file can get away with.
- **L3 — tests don't prove "before import":**
  - `:877` and `:903` raise the same message, so `test_mutation_campaign.py:369` can't show the dirty-tree refusal happened before the import.
  - The binding tests patch `assay.__file__` in-process.
  - No test covers the post-baseline recheck: changing HEAD or source during the baseline.
- **L4 — misleading resume message:** `_changed_since` returns True when `previous_head` is not an ancestor (for example, after a rebase). The refusal message then says "predates a product-source change". It still fails closed.

## Checked and found sound

- **Independent pre-import status (`:877`):** runs before `sys.path` insertion and import. Git runs with a replaced environment, no system or global config, no replace objects, and `fsmonitor`, hooks and excludesFile disabled. It refuses even if Assay's own git layer lies, which `:238` tests.
- **Index flags (`:406-428`):** `ls-files -v` marks assume-unchanged entries in lowercase and skip-worktree entries as `S`, and both refuse. The `:(exclude)` magic also works with `ls-files`.
- **Package roots (`:469-570`):** the current pyproject resolves to `src`, `analysis/src` and `pyproject.toml`; `package-dir {"": "src"}` is deduplicated. Unknown setuptools keys (`py-modules`, `data-files`, `dynamic`), named `package-dir` mappings, globs, absolute paths, `..`, symlinked roots or components, and symlinked entries all refuse.
- **Import binding (`:594-609`):** the editable finder is appended to `sys.meta_path`, so the `sys.path[:0]` insertion wins, and the `__file__` check confirms it.
- **Linked worktrees:** `--absolute-git-dir` follows `commondir`. `safe.directory` behaves the same as Assay's existing boundary, so there is no new uid failure mode.
- **Generated caches on the Assay side:** the egg-info written by the lane's editable install and runtime `__pycache__` files are tolerated, so Assay itself causes no false refusal. Blocker 1 is the false refusal.
- **Resume and baseline ordering:** resume validation, including the schema-5 refusal, now runs before the baseline (`:933` versus `:949`), and `:419` tests it.
- **Post-baseline rechecks:** cleanliness and both HEADs are rechecked after the baseline (`:994-999`).
- **Schema 5:** the writer and reader agree. Schemas 1–4 refuse with a correct "archive or remove" remedy. No schema-4 file exists in this worktree's `cmru/.assay/`.

## Residual risks and design options

1. **Trust starts after the lane has already run worktree code.** The lane runs `pip install --editable ../assay` and a self-test before the campaign. An untracked `assay/setup.py` or `setup.cfg` is outside the closure, but it runs during that pip step and could plant a `.pth` file that executes when the campaign interpreter starts. Option: add a pre-install `--check-source-closure` invocation, or drop the editable install from the mutation lane.
2. **A clean status is not a byte comparison.** A clean or smudge filter set in repo config, or `.gitattributes` EOL normalization, can make modified Assay or CMRU source look clean. CMRU's own closure is byte-hashed only at resume time; Assay's is never byte-hashed. Option: hash the Assay package roots into the evidence and compare those digests on resume.
3. **Preferred fix for 1–3 together:** build candidates from tracked files only, freeze one template after the baseline, and redirect the pycache prefix. Then the allowlist only has to cover files the campaign process itself imports, and Blocker 1 disappears without special cases for Run-Gate or coverage files.

## Verdict

Blocker 1 must be fixed, and its legitimate state constructed in a test (Run-Gate's inflight and history files plus coverage-lane outputs present), before the registered gate can produce KI-72 evidence. High 2 should land in the same change.
