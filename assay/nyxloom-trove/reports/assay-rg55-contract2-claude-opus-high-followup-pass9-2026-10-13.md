I couldn't save this as a file, because Write and ExitPlanMode aren't available in this session, so the review is below as text only. Nothing was edited, tested or gated. I only ran read-only `sed`, `grep`, `ls` and `git status` on the tree.

The review is bound to HEAD `336336d8` with tracked-diff SHA-256 `15a5c374…`.

# KI-72 follow-up review (pass 9)

**Verdict:** K-a, K-b, K-c, K-e and the nit are fixed. K-d is half-fixed: the imported Assay is now tied to the checked roots, but the clean check still runs after the import and through the imported code. The patch works with Assay's real `pyproject.toml` and with the linked-worktree checks. I found no High finding; everything below is Low.

## What I confirmed is fixed

- **K-a (py-modules):** each declared module now binds only its own `.py` file, not the whole directory (`mutation_campaign.py:~407-436`). The test at `test_mutation_campaign.py:171` creates an ignored `.pytest_cache` and confirms it's no longer reported.
- **K-b (allow-list):**
  - The backend must be `setuptools.build_meta`.
  - `[tool.setuptools]` keys and `packages.find` keys are each checked against a closed list.
  - `packages` must contain only `find`.
  - Unknown keys, `ext-modules` and `data-files` are refused (tests at :133-137 and :206).
- **K-c (bytecode):** `.pyc` files are allowed only directly inside `__pycache__` (`:439-451`). A stray `src/assay/removed_module.pyc` is refused (test at :275).
- **K-d, first half:** the import path now comes from the same root list the clean check uses (`:750-755`). `_assay_import_is_bound_to_source` (`:454`) compares resolved paths before the baseline (`:762`).
- **K-e (tests):**
  - `where = ['../src']` is now a list entry and reaches the `..` check (test at :125).
  - Passing `assay_source_root=None` explicitly gets a clear `ValueError` (test at :371).
  - The end-to-end test restores `sys.path` in a `finally` block (:329-350).
- **Nit:** the unused `ProcessTreeEvidence` import is gone.
- **Assay's real `pyproject.toml` passes:**
  - The backend matches.
  - The `[tool.setuptools]` keys are `package-dir`, `packages` and `package-data`, all on the allowed list.
  - The roots resolve to `["src", "analysis/src", "pyproject.toml"]`.
  - The live `git status --ignored=matching` shows only `__pycache__/` records, which are filtered out, so there's no false refusal.
- **Linked worktrees still work:** both the status check and the history comparison take paths relative to `assay_git.repo_top(assay_source)` (`:474-481` and `:650-662`).

## Findings

**1. Low: the clean check still runs after Assay is imported, using the imported code** (`mutation_campaign.py:755-768`).
- **Problem:**
  - `import assay` runs the possibly-dirty `__init__.py` and its imports.
  - The check that should catch that dirt then calls `git.run` from the same dirty tree.
- **User impact:** in the Assay development worktree, which is exactly where this runs (`assay/src/assay/*.py` are modified there now), a broken uncommitted edit to `assay/git.py` can return empty status. The campaign would then be certified against a dirty Assay. This is the "check that cannot fail" shape.
- **Fix:**
  - Run `_assay_source_tree_status` before `sys.path[:0]` / `import assay`, using a small subprocess git wrapper. It needs only `rev-parse --show-toplevel` plus `status --porcelain=v1 -z --untracked-files=all --ignored=matching`, run with `--literal-pathspecs -c core.fsmonitor=false`. `_assay_source_package_paths` is already pure and needs no Assay import.
  - Keep the binding check after the import.
  - Add a test: monkeypatch the imported `assay.git.run` to return `""`, and require that the dirty tree is still refused.

**2. Low: resume refusals arrive only after a full baseline run** (`:842`, after the baseline at `:798-839`).
- **Problem:** `_resume_results` uses nothing from the current baseline. Yet every one of its refusals comes only after the baseline pytest run has finished. That includes "evidence already exists; pass --resume", schema 3 or older, a changed source or test closure, an Assay source change between commits (`:648-664`), and the repeated clean check at `:622`.
- **User impact:** a predictable refusal costs a whole baseline run (minutes, on the shared host) and a gate-slice reservation.
- **Fix:** call `_resume_results` right after `jobs` is computed (`~:796`), before the baseline. After the baseline, keep a cheap re-check of `_campaign_tree_status` and `_assay_source_tree_status`, so edits made during the baseline still refuse.

**3. Low, latent: a declared `py-modules` entry is never importable from the checked source** (`:750-755`).
- **Problem:**
  - `assay_source_paths` includes the module file itself (for example `assay/helper.py`), and `run()` puts that file on `sys.path`. A file on `sys.path` has no effect.
  - The module's directory (`package-dir[""]` or `.`) is never added.
  - `_assay_import_is_bound_to_source` checks only the `assay` package.
- **User impact:** if Assay ever declares a py-module, it would be imported from whatever installed copy exists. The root list would claim it is bound when it isn't.
- **Fix:** in `run()`, add only directory roots to `sys.path`; for py-modules, add the parent directory of each module file. Also check each declared module's `__file__` against its bound file. Alternatively, keep refusing py-modules outright until a consumer needs them.

**4. Low, latent: roots are checked by their unresolved path** (`validate_root`, `:367-386`).
- **Problem:**
  - Only the last path component is checked for being a symlink. A `where = ["link/src"]` with `link -> real` inside the source tree passes resolution.
  - The status pathspec stays `assay/link/src`, which is what Git is asked about.
  - The import check compares resolved paths, so it passes.
  - Git either refuses a pathspec that goes through a symlink (an untyped `AssayError` before the baseline) or matches nothing. Matching nothing would be a false acceptance of a dirty `real/src`. I didn't test which one happens.
- **Fix:** require `resolved_root.relative_to(source_root).as_posix() == normalized` (no symlink anywhere on the path), or return the resolved relative path, as the py-modules branch already does.

**5. Low: the recorded `assay_version` isn't tied to the checked source** (`:758`, `:861`; `assay/src/assay/__init__.py:43`).
- **Problem:**
  - `__version__` comes from `importlib.metadata.version("assay")`, which reads the first distribution metadata found on `sys.path`.
  - That's either an allowed, ignored `src/assay.egg-info` (`.egg-info` is on the allow-list at `:445`, and `assay/.gitignore:7` ignores it), which is stale from install time, or whichever Assay the cockpit venv has installed.
- **User impact:** evidence can record, say, `assay_version: 9.x` next to an `assay_source_commit` from the 10.0.0 tree. That's a false label, although resume matching uses the commit, not the version.
- **Fix:** either remove `.egg-info` from the ignored allow-list and record `assay_version` as `git describe --match 'assay-v*'` at `assay_source_commit`, or rename the field to `installed_assay_distribution_version` so it doesn't claim more than it is.

**6. Low (test strength): the refusal tests check only the exception type** (`test_mutation_campaign.py:140-151`).
- **Problem:** all 11 parametrized cases pass on any `RuntimeError`. For example, `'../src'` would still pass if the `..` check were deleted, because the "escapes or is missing" branch also raises `RuntimeError`. That is AGENTS.md anti-pattern #3, "type for behaviour".
- **Also:**
  - The end-to-end test at :303 monkeypatches `assay.__file__` on the already-cached real module, so `run()`'s real import-binding path never runs.
  - The `TypeError` test at :353 now duplicates :371.
- **Fix:**
  - Add a `match=` to each parametrized case.
  - Add the cases `where = [1]`, `where = [""]`, `package-dir = "src"` and `py-modules = [1]`.
  - Add an end-to-end case that removes `assay` from `sys.modules` (using `monkeypatch.delitem`) and imports from a temporary source tree, so the binding check runs for real.

**7. Nits**
- **Dead exclude pathspec** (`:483`): Assay's `git.run` passes `--literal-pathspecs` (`git.py:696`, `:723`). So `:(exclude)assay/.assay` is read as a literal path that matches nothing. It's harmless, because `.assay` isn't under any root, but the line claims an exclusion that doesn't happen. Delete it.
- **Hardcoded layout check** (`:741`): `(assay_source/"src"/"assay").is_dir()` is a fixed-path check that runs before the derived roots. It's harmless, but it would wrongly refuse a valid derived layout. Replace it with "the derived roots contain an `assay/__init__.py`."

## Release-order impact

None of these block the release. Fixing #1 and #2 is cheap and removes the last part of K-d. #3 and #4 can be deferred because they're latent: Assay declares no py-modules and has no symlinked roots. If you defer them, record that in CMRU's backlog under KI-72.

The pass-8 blockers are unchanged: untracked inputs, no exact-tip gates, and missing host acceptance.
HEAD_BEFORE=336336d81b9af9fd7d7a9ad3a4a272ca01c66071
STATUS_BEFORE=a6d38e29fcd32c757c28da4164f0c729cc11a460830c087f7cc9ee5e95f8b3d3  -
DIFF_BEFORE=15a5c3745b9bcf0d9d684ccb9c3eb676aac5e383b413033c7ba52a660b9861d8  -
336336d81b9af9fd7d7a9ad3a4a272ca01c66071
a6d38e29fcd32c757c28da4164f0c729cc11a460830c087f7cc9ee5e95f8b3d3  -
15a5c3745b9bcf0d9d684ccb9c3eb676aac5e383b413033c7ba52a660b9861d8  -
REVIEW_EXIT=0
