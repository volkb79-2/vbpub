# CMRU exact-tip review, repair, and release-wave inventory — 2026-10-17

## 0. Identity and route evidence

| fact | value |
|---|---|
| worktree | `/workspaces/vbpub/.worktrees/cmru-local-candidate-main-20261017`, branch `cmru-local-candidate-main-20261017` |
| starting commit | `d8d5e9250624ecaeb919163726ed978ac3811663` (verified `git rev-parse HEAD`; `git status --short` empty) |
| comparison tag | `cmru-v6.1.0` (annotated `f13879cc1f29`) → commit `2b94cb4334b418cfb962a3e87de63c7d46a7ab6e`; latest `cmru-v*` stable tag; ancestor of the candidate |
| fix commits | `a2b7dfa9f33c` `fix(cmru): exact-tip review fixes for the 6.2.0 candidate`; `0cfaf7909e60` `fix(cmru): follow CommonMark code spans when folding release notes` (fix-review M1–M7); `5268e2e85483` `test(cmru): cover multi-line code spans and an unclosed final backtick` — final **code** tip `5268e2e8548321d1ef199a52d01c85130badf944` |
| final commit | the report commit directly on top of `5268e2e85` (`git log -1 -- cmru/nyxloom-trove/reports/cmru-opus-high-review-and-release-wave-inventory-20261017.md`); it adds this file and a KI-67 backlog note (docs only), so every gate and wheel fact below binds to `5268e2e85`'s code |
| final tree state | clean: `git status --short` was empty at `5268e2e85` before and after the final coverage run; ignored build output from the local wheel build moved to `/tmp/cmru-stale-build-output-20261017/` (see §6) |
| route | controller review: Claude Opus (`claude-opus-5-5`), effort **high** as assigned by the caller — the caller owns route evidence (AGENTS.md "Model routing"); this line is a self-report, not route proof. Read-only fan-out reviewers dispatched by this controller with `model: sonnet` (default effort): docs/spec/backlog matrix, non-KI-82 correctness, tester-unified commit, and an adversarial review of the fix commit. Release-wave inventory: `/tmp/vbpub-release-wave-worktree-inventory-20261017.md`, produced by the caller's Sonnet/high read-only run (not dispatched by me). |
| delta size | `git diff --stat cmru-v6.1.0..d8d5e9250 -- cmru`: 124 files, +30 793/−1 874; 428 commits in range, 5 touch `cmru/` (`f0a4dab47` is a squash "integrate local release candidate work") |

This is the controller's own review plus Sonnet sub-reviews. It is **not** an independent
review in the doctrine sense (§9 says what remains).

## 1. Executive decision

**The candidate is not releasable as-is, and the blockers are mostly not CMRU code.**

1. **CMRU-scoped defects found and fixed in `a2b7dfa9f` + `0cfaf7909` + `5268e2e85`** (all with
   behavioral oracles that fail on the pre-fix code, verified by reverting each fix locally):
   - **[major] Pending release notes silently corrupted** (`release_notes._mask_html_comments`):
     an `[Unreleased]` note quoting `` `<!-- … -->` `` in inline code was blanked in the tagged
     section and its "comment" hoisted back into `[Unreleased]`; an unclosed `` `<!--` `` deleted
     every following entry. Immutable release notes and migration guidance could be lost.
   - **[major] Shell injection through the from-zero guide's `eval`**
     (`tools/cli_extended_release_identity.py`): remote `latest.json` `version` was printed
     unvalidated and `url` only prefix-checked, both unquoted, then `eval "$identity"`.
   - **[major, KI-82 oracle 2] Detached `HEAD` was not refused as detached**: the worktree
     library reports a detached checkout as branch `"HEAD"` and `git check-ref-format
     refs/heads/HEAD` exits 0, so `if not branch` never fired; the release failed later with a raw
     `git rev-parse refs/heads/HEAD failed` (exit 1). Fail-closed but wrong condition named; the
     existing test mocked `branch=None` (hollow against the real library).
   - **[major, release-blocking procedure] `gate-provisional` is hard-bound to 6.2.0 while the
     candidate computes 6.1.1**: `cmru status cmru` → `cmru-v6.1.1` (patch; non-conventional
     subjects), `POSTPONEMENT_RELEASE_VERSIONS = {"KI-79": "6.2.0"}`
     (`tools/run_release_gate.py:1047`). A default `cmru release cmru` would prepare and push a
     candidate, then refuse in its gate. No document said `--minor`. Now documented in README,
     CONSUMERS, SPEC, RELEASE-TRANSACTIONS, KI-79 and CHANGES.
   - [minor] publisher tokens reached the unverified `run-gate --version` probe; host Assay
     preflight child not isolated; tester-unified build context admitted local
     `build/`/`dist/`/`*.egg-info`; controller provenance refusal had no remedy and the README
     `cmru[interactive]` install produced exactly that refusal; feature-source resume message
     named `origin/main`.
   - Docs/spec/backlog: SPEC S16.4 still specified the superseded in-place secret masking
     (rewritten to KI-74); SPEC lacked the KI-78 fold, wheel-bound controller and
     `CMRU_RELEASE_NOTES_SOURCE` clauses; RELEASE-TRANSACTIONS claimed contract-2 observation on
     every lane and an image pin the root config does not declare; stale KI-52/53/62/81/82
     statuses; dangling KI-78 test references; CHANGES omitted KI-59/68/69/70/71/72/75.
2. **Cross-estate blockers (outside approved scope, not fixed, not relabelled):** the five known
   coverage-lane failures plus one new intentional guard (§6). All six are the same fact: CMRU's
   candidate tree presumes Run-Gate-wave content (retired `run-gate.py` copies, Nyxloom/CGProfile
   wheel-builder recipes, Run-Gate `release_candidate.py`, root `tester-unified` `image_env`
   from `cd673fecd`) that exists only in the unreleased TRI→RG93→CGP lineage.
3. **KI-82 design and state machine: sound.** No unsafe ref handling found: no feature-source path
   pushes `origin/main`, moves local `main`, runs local-main sync, or pushes the caller's source
   branch; the candidate push uses empty-ref then expected-old-value leases with an ancestry
   check; retirement requires the exact final candidate tip in fetched `origin/main` and
   rechecks tags (§3).
4. **No blocker remains inside CMRU-scoped code at `5268e2e85`** to my review's depth, but
   the registered aggregate `gate`/`gate-provisional` was not run (it cannot pass while the
   coverage lane is red on cross-estate checks, and it would start a multi-lane sequence on a
   shared production host), and the fix commits have had only a Sonnet adversarial pass (§9).

## 2. Consistency matrix (backlog / SPEC / docs / CLI / code / tests)

Line numbers are at `a2b7dfa9f` unless marked `d8:` (`0cfaf7909` later moved the SPEC fold/controller clauses to S4.8/S4.9 after S4.6 and grew `release_notes.py`). "fixed" = repaired in `a2b7dfa9f`.

| # | behavior | backlog | SPEC | DESIGN-GUIDE | README | CONSUMERS | CLI review/surface | implementation | tests | state |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `release --from-worktree` | KI-82 L2002 | S-CLI.5e ~L386-445 | L1254-1292 | L650-676 | L1279-1325 | `cli-review.toml:2407-2417` active/accept, signature matches surface | `cli.py:5815` preflight, `:5947` launcher | `test_feature_worktree_preflight_contracts.py`, `test_release_end_to_end_real_git.py::test_feature_worktree_release_keeps_both_main_tips_unchanged…` | detached-HEAD defect fixed; KI-82 "S-CLI.9 still needs cases" was stale — fixed |
| 2 | `--secret-file` + main-tree advice | KI-82 | S2.4 ~L1697-1710 | L1282-1292 | L672-675 | L1205-1209 | `cli-review.toml:2420-2430` | `config.py:912-945`, `cli.py:886` | mocked advice tests + new real-Git advice test (mode-000 file proves no read) | consistent |
| 3 | feature candidate retirement after PR-head deletion | KI-82/KI-35 | S-CLI.8 | L1274-1281 | L662-665 | L1308-1316 | verb description `cli.py` OptionSpec | `cli.py:6813-7040`, `transaction.py:2892` | `test_cli_abandon.py` | consistent |
| 4 | KI-78 fold `[Unreleased]` into tagged section | KI-78 L1935 | **was absent** → new S4.8 | L1031 | L538 | L1532 | — | `changelog.py:86`, `release_notes.py:77-140` | `test_unreleased_notes.py` (+5 new) | inline-code corruption fixed; SPEC gap fixed |
| 5 | `handler oci-image-release` | KI-78 | S4.7 | L1045-1160 | L534-600 | L1053-1147 | route rows active | `handlers.py`, `release.py` | `test_oci_image_release.py` | consistent (KI-78 dangling test names fixed) |
| 6 | KI-75 tagged-section release bodies | KI-75 L1847 | S1279 table, S4.7, **`CMRU_RELEASE_NOTES_SOURCE` now defined** L1750 | L978-1044 | L538+ | L979-1052 | — | `release_notes.py`, `cli.py:383` | `test_release_notes.py` | SPEC definition gap fixed; CHANGES line added |
| 7 | `gate-provisional` / KI-79 | KI-79 L1919 | provisional section ~L1783 (+ version binding, manifest authority) | L640-690 | L901-910 | L764-780 | n/a | `run_release_gate.py:1047, 1120-1135, 1136-1196` | `test_release_gate_provisional.py` | 6.2.0 binding undocumented — fixed |
| 8 | KI-81 interpreter switch | KI-81 L1987 | L1757-1770 | L903-976 | L905-930 | L918-960 | — | `run_release_gate.py` `_ensure_gate_environment` | `test_release_gate_interpreter.py` | backlog lacked version-floor follow-up — fixed |
| 9 | KI-74 exact-HEAD snapshot gate | KI-74 L1837 | **S16.4 described removed masking** → rewritten L3230 | L784-902 | L985-1012 | L961-978 | `gate-snapshots` rows active | `gate_snapshot.py`, `run_release_gate.py` | `test_gate_snapshot_*`, `test_release_gate.py` | fixed |
| 10 | Run-Gate floor 24.0.0, tester image pin | KI-77 | now in S16.4 | L835-885 | L883-901 | L744-762 | — | `run_release_gate.py:36, 415-470, 1246-1253` | `test_release_gate.py` (+1 token-scrub test) | pin claim false at this tree → docs corrected (§6) |
| 11 | KI-69 `CMRU_RELEASE_COMMIT/TAG` | KI-69 L1791 | L1742-1750 | L500-528 | L778 | L1097 | — | `config.py:780-801`, `cli.py:375-380` | release-flow tests | CHANGES line added |
| 12 | KI-70/72 mutation schema 5 | KI-70/72 | L3171-3190 | L687-725 | L959-969 | L832-860 | — | `tools/mutation_campaign.py` | `tools/test_mutation_campaign.py` | CHANGES line added |
| 13 | KI-71/80 scoped bumps | KI-71/80 | L2904 | L1201-1215 | L221-223 | L1164 | — | `version.py` | `test_version_git.py` | CHANGES line added (KI-71) |
| 14 | KI-59/68 bump-skip / stale-section refusal | KI-59/68 | — | L1037 | — | L1006-1011 | — | `changelog.py` | `test_changelog_hand_edits.py` | RT heading contradicted body — fixed; CHANGES line added |
| 15 | wheel-bound, non-editable controller | none | **was absent** → new S4.9 | L1294 | L607, L886 | L747 | — | `release_controller.py` | `test_release_controller.py` | SPEC gap + remedy message fixed |
| 16 | first release without prior tag | none | provisional section | L667-686 | L938-945 | L779-790 | — | `run_release_gate.py` | `test_release_gate.py` | consistent |
| 17 | from-zero bootstrap | none | — | — | 2 refs | 2 refs | — | `build-initial-standalone.sh`, `tools/cli_extended_release_identity.py` | `test_bootstrap_from_zero_sequence.py` (+10 new) | eval injection fixed; Run-Gate inputs guard added (red, §6) |
| 18 | `wheel-build` slice proof | none | L1278 | L403-423 | L470 | L442-450 | — | `handlers.py` | `test_wheel_build_placement.py` | consistent |
| 19 | `publish --from-checkout` origin-tag check | none | L1268 | L424-434 | L473 | L453 | — | `cli.py`, `handlers.py` | build-output tests | consistent |
| 20 | nested-wheel first-party dependency check | KI-83 open | — | — | — | — | — | not implemented | none | open, minor (not a 6.2.0 blocker) |

All 115 markdown links under `cmru/` plus the bootstrap guide resolved (GitHub anchors) at
`d8d5e9250`; the 367 CLI cases in `cli-surface.json` match the 367 rows in `cli-review.toml` by id
and signature (Sonnet matrix review). `tests/test_docs_config_examples.py`,
`tests/test_cli_spec_inventory.py` and `tests/test_release_notes.py` pass after the doc edits.

## 3. KI-82 state-machine review

### 3.1 Source selection (launcher, `cli.py:5815-5944`, `5947-6416`)

| requirement | code | verdict |
|---|---|---|
| clean caller worktree (tracked + untracked, submodules) | `status --porcelain=v1 --untracked-files=all --ignore-submodules=none`, before and after fetch | OK (ignored files allowed by design) |
| named, non-main branch | `not branch or branch == "HEAD"` (fixed), `branch == "main"`, `check-ref-format` | **fixed** (was `not branch` only) |
| caller and selected projects in the same worktree | `discover_git_context(cwd)` vs `transaction_root` and common dir | OK |
| exact fetched `origin/<same branch>` == HEAD | `fetch_origin_branch` (`transaction.py:1296`): ls-remote → forced fetch to `refs/remotes/origin/<b>` → rev-parse → ls-remote again, refuses if moved | OK; remedy text distinguishes behind / ahead / diverged |
| race: branch switch, ref move or edit during fetch | full re-discovery + `refs/heads/<b>` + second status after fetch | OK |
| `--allow-uncommitted`, `--ahead-check-ref`/`--ref` refused | launcher `:5957-5966`, resume `:6098-6106` | OK (now documented in CONSUMERS) |
| one Git family | `project_git_family_groups` | OK |
| ahead/diverged from main accepted; no main fetch/compare | `assert_local_main_not_ahead` skipped when `feature_source_branch` set (`:6203`) | OK |
| whole preflight under `transaction.release_lock` | `:6040` | OK |

### 3.2 PR candidate naming and pushes

- PR candidate = the generated transaction branch `cmru-release-<YYYYMMDD_HHMMSS>-<scope>-<identity>`,
  same name locally and on origin; the caller's `feature/foo` is only the pinned source
  (`source.json` sidecar: `source_ref`, `source_commit == workspace.base`). Matches the accepted
  KI-82 decision ("two distinct branch pairs") and CHANGES' "same-named generated candidate".
- Collision: `create_workspace(require_remote_branch_absent=True)` → `assert_origin_branch_absent`
  before the worktree exists (`transaction.py:1283`); `feature_source=True` is refused unless that
  preflight is requested.
- First push: `--force-with-lease=<ref>:` (must be absent); a remote that appeared after allocation
  at another commit refuses; one at exactly local HEAD (crash between push and marker) is adopted
  without a write (`transaction.py:3330-3365`). Refresh: remote must be an ancestor of local HEAD,
  then `--force-with-lease=<ref>:<observed>` (`:3366-3388`) — a non-fast-forward or moved ref
  refuses; a vanished ref refuses rather than recreate.
- `require_feature_source=True` on every child push: a lost `source.json` refuses instead of
  falling back to the default `--force` push; the shared-worktree label `cmru.source_mode=worktree`
  makes a missing sidecar an error in `read_release_source`.
- **No source-branch push and no `main` mutation in feature mode:** the only `HEAD:refs/heads/main`
  pushes are `promote_workspace` (skipped by `finish_candidate` in feature mode, `cli.py:3110`) and
  the legacy `revert_promotion` (no caller). `_sync_local_main_and_report` is guarded by
  `not feature_source` on all three launcher exits (`:6346, 6368, 6406`). The e2e test proves
  local `main`, `origin/main`, `feature/…` (local and origin) unchanged and the tag at the
  retained candidate tip.

### 3.3 Resume, partial publish, retirement

- Resume binds to the retained `source.json`; `--from-worktree --resume` on a default candidate
  refuses; a fully published feature candidate refuses resume and points to `abandon` (`:6164`).
  With results, the durable tip is `origin/<candidate>` (ancestor of local), not `origin/main`
  (`cli.py:3973-3993`; the error message now names that ref).
- Successful completion requires local candidate HEAD == `origin/<candidate>` == progress record,
  else `UnsafeRecord` with the candidate retained (`:6311-6325`). Warns that the release is public
  before the PR merges and that squash/rebase do not preserve the tag.
- Abandon: partial feature results refuse; full results require the ownership marker, exact
  candidate tip == progress == remote candidate, a fresh fetch of `main` and the candidate, and
  `merge-base --is-ancestor <candidate tip> <origin/main>`; a deleted PR head is accepted only
  with that proof (`cli.py:6813-6940`, `transaction.py:2892-3010`). Tags are rechecked against the
  inspection snapshot; tags and assets are never deleted. The inventory's note that
  `cmru-validation-context` keeps an extra `allow_missing_remote_candidate` guard the candidate
  dropped is correct but harmless: the earlier refusal at `transaction.py` "origin candidate ref is
  missing but its removal was not recorded" makes that branch unreachable.
- Published tag SHA recoverability: the tag itself always holds the commit; the retained candidate
  branch holds it until a merge/fast-forward puts it in `main`. Real instance in the estate: the
  nested PWMCP transaction `cmru-release-20261016_044619-pwmcp-rji18k` at `6cfe7531e` = public tag
  `pwmcp-v1.63.0-r3`, in neither `main` nor `origin/main` (verified) — do not retire it.

### 3.4 Credentials

- `--secret-file` is read in the launcher only; the child receives the private handoff
  (`config.py:921-933` returns before any file read) and `--secret-file` is dropped from child
  argv (`cli.py:3357`). Explicit path must be a regular file; environment-token precedence and the
  project overlay are unchanged.
- Advice (`cli.py:886`) only `is_file()`s the single non-prunable, non-bare `main` worktree's
  corresponding root path; ambiguity/absence yields generic advice. New real-Git test makes the
  file mode 000 and asserts the advice names it — proving no read.

### 3.5 Residual KI-82 notes (not defects)

- Feature-mode release still fetches nothing about `origin/main` — by design; consequently the PR
  may conflict with `main`, which is the operator's merge-time concern.
- The candidate is public before the PR merges (accepted tradeoff, documented in all three docs).

## 4. Other findings (non-KI-82)

| id | severity | finding | disposition |
|---|---|---|---|
| C1 | major | inline-code HTML comment corrupts folded notes (`release_notes.py` masker) | **fixed** + 5 tests (4 fail on d8) |
| C2 | major | `eval` of unquoted remote pointer data (`cli_extended_release_identity.py`) | **fixed**: exact asset-URL regex, version==wheel version, Dockerfile pin 64-hex, `shlex.quote`; 8 tests (7 fail on d8); real pointer (`cli-extended-v0.4.0`) fits the shape |
| C3 | minor-major | "pins every lane to the inspected image ID" false: root `[environments.tester-unified]` has no `image_env` (only `cmru-mutation` does); other lanes detect a retag by ID recheck, they are not pinned | docs + code comment corrected; root fix is `cd673fecd` in the Run-Gate wave (breaks published run-gate 23.10.0) — out of scope; guarded (§6) |
| C4 | minor | provisional marker is written before cleanup; a SIGKILL during cleanup can leave marker + `running` manifest | documented: manifest `state` is authoritative, `running` is never a pass (SPEC). `incomplete` IS reachable (canary planting failure), so the reported state-list mismatch was wrong |
| C5 | minor | host Assay preflight inspects caller `assay/` (superset refusal) and child not `-I -B` | `-I -B` **fixed**; superset refusal left as design note (in-container re-check is authoritative) |
| C6 | minor | in-place `python -m build` reuses stale `build/lib` | image side **fixed** (`.dockerignore`); bootstrap-script refusal of `cmru/build` reverted because the builder writes that directory into the live tree, so the refusal would block every rerun. Release transactions build in a fresh worktree and are unaffected. Open design item: build out-of-tree or clean `build/` in `wheel-build` |
| C7 | minor | README by-name `cmru[interactive]` install gives no `direct_url.json` → real release refuses with no remedy | **fixed** (README + message) |
| D1 | design | lane-writable common `.git/config` is not digested; host Git later reads local config | Run-Gate mount scope (KI-77 follow-up); recorded |
| D2 | design | retained snapshot anchors go stale after a legitimate local reset | recorded |
| D3 | design | tag→notes commit binding resolves the tag twice | recorded (no exploit path found) |
| D5 | minor | `run-gate --version` ran with publisher tokens in env before identity verification | **fixed** + test |
| T1 | medium | `.dockerignore` admitted local `build/`, `dist/`, `*.egg-info` | **fixed** + 9 tests |
| T2 | medium | CHANGES/bootstrap claimed `RUN_GATE_TESTER_*` checks that no code in this tree reads (they are in `run-gate-project/tools/release_candidate.py` on `run-gate-rg93-git-metadata-20261013` / `cgprofile-release-20261015`) | CHANGES reworded; guide states the dependency; guard test (red, §6) |
| T3 | low | `fetch-cli-extended.py` treats `--expect-sha256 ""` as absent | not fixed (tester-unified scope; final `sha256sum -c` and `build-image.sh` still fail loudly) |
| T4 | low | `build-image.sh` resolves relative `--wheel-dir` against repo root | not fixed (documented examples run from root) |

Old resolved findings re-checked and still resolved: KI-78 Sol review items 1–6 (read-back after
every write, pre-PATCH target re-check, unknown-outcome reporting, validator before `prepare`,
explicit registry), KI-79 finalization (cleanup decides success), KI-81 one-shot isolated
re-exec.

## 5. Direct fixes (`a2b7dfa9f`, `0cfaf7909`, `5268e2e85`)

Second set (`0cfaf7909`, from the Sonnet adversarial review of `a2b7dfa9f`): one
`_scan_html_comments` scanner returns exact comment spans (M1: a quoted `<!--` no longer pairs
with a later real comment), follows a code span across lines of its paragraph (M2), treats an
escaped backtick as literal (M3) and never scans a fence opener as inline code; SPEC sentence
fix (M4); fold/controller clauses renumbered S4.8/S4.9 with the inline-code rule, and README,
DESIGN-GUIDE, CONSUMERS state how comments and quoted markers fold (M5); `read_release_source`
refuses `refs/heads/HEAD` (M6); the guard counts only quoted literals and refuses a missing root
config (M7). `5268e2e85` adds the two cases that restore 100% branch coverage.

First set — code: `cli.py:5839` (detached HEAD), `cli.py` resume durable-ref message, `transaction.py:842`
(source record refuses `HEAD`), `release_notes.py:77-120` (`_code_span_end`, opaque inline code),
`release_controller.py` (remedy), `tools/cli_extended_release_identity.py:33-48, 100-145`,
`tools/run_release_gate.py:423` (token scrub), `:842` (`-I -B`), image-pin comment,
`.dockerignore:93-98`.

Docs: README (6.2.0 `--minor`, wheel-file `interactive` install), CONSUMERS (6.2.0 binding,
refused flags in feature mode), SPEC (S16.4 rewrite; S4.8; S4.9; `CMRU_RELEASE_NOTES_SOURCE`;
tracking-id/version binding; manifest authority), RELEASE-TRANSACTIONS (lane observation fact,
KI-68 heading, truthful image pin, wave-only root `image_env`), KNOWN_ISSUES (KI-52/53 shipped in
6.0.0, KI-62 current option, KI-63 historical oracle, KI-78 test refs, KI-79 release command,
KI-81 follow-up, KI-82 CLI-case sentence), CHANGES (fixes + KI-59/68/69/70/71/72/75 entries),
`run-gate.toml` header comment, `docs/BOOTSTRAP-FROM-ZERO.md` (Run-Gate source dependency).

Tests added (each confirmed failing on the `d8d5e9250` code where it is a regression oracle):
`test_feature_preflight_refuses_a_real_detached_head_as_detached`,
`test_main_tree_secret_advice_finds_the_real_main_checkout_without_reading_it`,
`test_release_source_writer_refuses_main_and_unmatched_commits[HEAD-…]`,
`test_a_comment_quoted_in_inline_code_is_folded_verbatim_and_never_hoisted[4 cases]`,
`test_an_unmatched_backtick_leaves_a_following_comment_a_real_comment` (control),
`test_a_hostile_or_inconsistent_pointer_is_refused_before_anything_reaches_eval[6 cases]`,
`test_the_identity_output_is_safe_to_eval` (control), `test_a_malformed_dockerfile_pin_is_refused`,
`test_the_unverified_run_gate_launcher_never_receives_publisher_tokens`,
`test_dockerignore_keeps_local_build_output_out_of_the_wheel_source_trees[7 cases]`,
`test_dockerignore_build_output_rules_hide_no_committed_context_path`,
`test_every_run_gate_tester_input_the_guide_passes_is_read_by_run_gate_source` (guard, red);
second set: `test_inline_code_follows_commonmark_spans_and_comments_are_exact[6 cases]` (4 fail on
`a2b7dfa9f`), `test_an_unclosed_comment_is_masked_to_the_end_but_never_reported_as_a_comment`,
`test_a_span_across_three_lines_is_opaque_and_an_unclosed_final_backtick_is_literal`.

## 6. Gate results and honest limitations

Registered lane: `run-gate coverage` (cmru/run-gate.toml `[lanes.coverage]`, tester-unified,
installed `run-gate 24.0.0` — a local, unreleased build; latest public tag is `run-gate-v23.10.0`).
Exit status read from the job (`echo EXIT=$?` appended), not the wrapper.

| run | commit | tree | result | coverage | lane verdict |
|---|---|---|---|---|---|
| baseline (operator-reported) | latest prior run (`.run-gate/history.json` binds it to `894a06741`) | — | 5 313 passed / 6 skipped / 5 failed | 100% (14 436/14 436; 6 142/6 142) | FAIL |
| 1 | `a2b7dfa9f` | **polluted**: ignored `cmru/cmru.egg-info` + `cmru/build` from the 05:08 local wheel build | 5 335 passed / 6 skipped / 9 failed | 100% | FAIL |
| 2 | `a2b7dfa9f` | clean (leftovers moved to `/tmp/cmru-stale-build-output-20261017/`) | 5 338 / 6 / 6 failed | 100% (14 446/14 446 stmts, 6 146/6 146 branches) | FAIL |
| 3 | `0cfaf7909` | clean | 5 345 / 6 / 6 failed | **99.97%** (4 stmts, 2 branches in the new scanner) | FAIL |
| 4 (final) | `5268e2e85` | clean | **5 346 passed / 6 skipped / 6 failed** | **100.00% (14 485/14 485 statements; 6 162/6 162 branches)** | **FAIL** (exit 1) |

Final-run log: `/tmp/run-gate/lanes/coverage/932b3114f93a55c8e67f0ac9175f3580.log`; artifacts
`cmru/coverage.json`, `cmru/junit-coverage.xml` (git-ignored).

The six final failures, none relabelled:
1. `test_tester_unified_image.py::test_estate_has_no_tracked_run_gate_script_copies_or_symlinks` — 18 root/project `run-gate.py` copies still tracked (Run-Gate retirement wave).
2–4. `test_nyxloom_dockerfile_copies_all_cli_extended_wheel_inputs`, `test_nyxloom_cli_extended_wheel_source_tree_has_usable_copied_modules`, `test_nyxloom_final_image_installs_wheels_from_the_checked_build_stage` — Nyxloom Dockerfile wheel-builder/image assumptions.
5. `test_tester_unified_build_instructions_use_the_exact_wheel_builder` — `scripts/cgroup-profiler/tools/gate.sh` (and `nyxloom/tools/remote-mutation-audit-host.sh`, `ciu/scripts/check_run_gate_wheel.py`) still prescribe a raw `docker build -f tester-unified/Dockerfile`, which the `d8d5e9250` Dockerfile no longer supports without `build-image.sh` (Sonnet tester-unified review F1, confirmed by these assertions).
6. `test_bootstrap_from_zero_sequence.py::test_every_run_gate_tester_input_the_guide_passes_is_read_by_run_gate_source` — **new, intentional**: the from-zero guide passes `RUN_GATE_TESTER_CLI_EXTENDED_RESOLVE`, `…_SHA256`, `RUN_GATE_TESTER_UNIFIED_BASE_IMAGE` and `RUN_GATE_TESTER_UNIFIED_IMAGE`, but no Run-Gate source here reads the first three and the root `tester-unified` declares no `image_env`. Verified green-by-construction on `cgprofile-release-20261015` (all four read in `run-gate-project/tools/release_candidate.py`; root `image_env` present via `cd673fecd`).

Run 1's three extra failures (`test_step_one_reaches_a_working_first_cmru…`,
`test_cli_review_cases.py::test_real_executable_obeys_help_version_and_parse_contract`,
`test_w2_pkg2_delegates.py::test_python_dash_m_handlers_and_cmru_handler_share_one_builder`)
were environmental: in-process metadata read `6.1.1.dev428+gd8d5e9250` from the stale ignored
`cmru/cmru.egg-info` in the judged checkout (the lane mounts the live worktree), while
subprocesses saw `0.0.0+tester.unified`. They passed once the leftovers were moved aside (run 2).
This is the KI-67 shape — ignored build metadata in a judged tree — now filed under KI-67: an in-place `wheel-build` in a worktree that is later gated produces false
failures (or, in principle, false passes) unless `cmru/build` and `*.egg-info` are cleaned first.

Not run, with reasons:
- Aggregate `gate` / `gate-provisional` (bare-host conjunction: ref-probe, installed-wheel(-live),
  Assay R0/R1/R3, coverage, canary): it contains the coverage lane, which is red on the six
  cross-estate checks, so it cannot pass on this tree; running it would only add a long,
  multi-container sequence on a shared production host. It must run on the integrated tree (§10).
- R2 mutation: excluded by instruction (long campaign); KI-79 defers it for 6.2.0 anyway.
- Live checks the worklog still owes (real in-transaction `gate-provisional`, from-zero
  rehearsal, GitHub release-body byte preservation) remain open.

## 7. Local wheel

Built with the supported handler from this candidate: `cmru handler wheel-build --cwd cmru`
(installed controller at the time: `cmru 6.1.1.dev428+gd8d5e9250`; `handlers.py` is unchanged by
the fix commits), builder image `wheel-builder:local`
(`sha256:89453d1cd691bdf450dce8589fb7bb17a97ed99820f9045ee1759d05efa2b617`), exit 0
(`/tmp/cmru-wheel-build.log`). The ignored `cmru/build` from the 05:08 build had been moved aside
before this build, so no stale `build/lib` content could be packaged.

| fact | value |
|---|---|
| file | `cmru/dist/cmru-6.1.1.dev431+g5268e2e85-py3-none-any.whl` (git-ignored; previous dev428 wheel copied to `/tmp/cmru-prev-wheel/`) |
| version | `6.1.1.dev431+g5268e2e85` (setuptools-scm; dev/local version — a local candidate, not a release) |
| SHA-256 | `599ad80f99cd1c17da38ff9656bea0863e0c23400951763e1d15ad224ff42e8a` |
| metadata | `Name: cmru`, `Requires-Python: >=3.11`, `Requires-Dist: cli-extended>=0.3.0` (+ test/interactive/versions-templates extras) |
| content check | every packaged module equals its `git show HEAD:` blob; no untracked module in the wheel; no tracked `.py` missing; contains `_scan_html_comments` |
| install | `/home/vscode/.venv/bin/python -m pip install --no-deps --force-reinstall <wheel path>` → exit 0 (not `~/.local`) |
| verification | `which -a cmru` → only `/home/vscode/.venv/bin/cmru`; `cmru --version` → `cmru 6.1.1.dev431+g5268e2e85`; `import cmru` → `/home/vscode/.venv/lib/python3.14/site-packages/cmru/__init__.py`; `worktree` from the same site-packages; `direct_url.json` sha256 = file SHA-256; `installed_release_controller_identity()` → same version and wheel SHA-256 (a real `cmru release` would accept this controller); `pip check` reports no cmru issue |

## 8. Release-wave inventory (cross-checked)

Source: `/tmp/vbpub-release-wave-worktree-inventory-20261017.md` (Sonnet/high, read-only; 145
registered worktrees, 138 local branches, 8 stashes). Claims I re-verified with Git:

| claim | check | result |
|---|---|---|
| candidate = local `main` b3277b36e + 6 | `rev-list --left-right --count main...d8d5e9250` | `0 6` ✔ |
| local main vs origin/main ahead 46 / behind 41; candidate vs origin/main 52/41 | `rev-list` | ✔ (`41 52`) |
| LC17 `cmru-local-candidate-20261017` = origin/main + 4, dirty | `rev-list origin/main...cd260f00e` = `0 4`; 39 status lines | ✔ (not touched) |
| CGP `f4933a2d5` merge-base with main b92efaea3, 1/203 | candidate…CGP `7 203` | ✔ |
| `cmru-origin-feature-release` HEAD == `cgprofile-release` HEAD | both `f4933a2d5` | ✔ |
| nested PWMCP candidate `6cfe7531e` = tag `pwmcp-v1.63.0-r3`, not in main/origin/main, on origin candidate branch | `rev-parse`, `merge-base --is-ancestor`, `ls-remote` | ✔ |
| `cmru-inst62` installer work absorbed nowhere | `git grep stub_launchers` in d8, CGP, main: 0 | ✔ |
| VAL vs d8 production delta is cli.py 5 lines + transaction.py 2-line guard | `git diff d8d5e9250 e2f1563e2 -- cmru/src/cmru/{cli,transaction}.py` | ✔ (guard unreachable in d8, §3.3) |
| `RUN_GATE_TESTER_*` consumer and root `image_env` only in the wave lineage | `git grep` on `run-gate-rg93…`, `cgprofile-release…`; `git branch --contains cd673fecd` | ✔ |

Corrections to the inventory:
- §6.3 "S-CLI.9 reviewed semantic cases for `--from-worktree`/`--secret-file` still missing":
  stale — the rows exist and were accepted in `894a06741`; backlog fixed.
- §6.3 "KI-79 defers R2 to `assay-first-release-r2`": only for a first release. CMRU has
  `cmru-v6.1.0`, so the recorded follow-up lane is `gate`.
- §6.3 should add: the CMRU release command must be `cmru release cmru --minor` (6.2.0 binding).

### 8.1 CMRU ancestry and purpose

| worktree / ref | HEAD | relation | completed purpose | remaining | cleanup |
|---|---|---|---|---|---|
| `cmru-local-candidate-main-20261017` (target) | `a2b7dfa9f` + report | main + 8 | KI-78/79/80/81/82 + this review's fixes | §10 steps | keep |
| `cmru-local-candidate-20261017` (LC17) | `cd260f00e` dirty 37M/2?? | origin/main + 4 | same CMRU work on origin lineage | 11 dirty files not in d8 (owner to examine) | hands off (operator) |
| `cmru-validation-context-20261017` (VAL) | `e2f1563e2` detached, 27 staged | CGP + 2 | CGP-based predecessor | superseded by target | C3 after target gate accepted |
| `/tmp/cmru-merge-scratch-20261017` | `e60e0e289` detached | ancestor of VAL | scratch merge | none | C2 |
| `cmru-origin-feature-release-20261016` | `f4933a2d5`, 13 M | = CGP | older KI-82 draft (13 lines not verbatim in d8) | verify residue | C3 |
| `cmru-release-readiness-20261015` | `2b7f27501`, 2 M | in RG93/CGP | zero-release bootstrap; older KI-81 design prose | 48 prose lines | C3 (+ `stash@{0}` holds its pre-sync Run-Gate edits) |
| `cmru-release-fixes-20261013` | `482b2ee83` | ancestor of TRI | KI-75/78 first implementation | none | C2 |
| `cmru-gate-ad61a1146ae971f80547` | `670f99d46` | ancestor of TRI | gate worktree; ignored evidence | archive `.assay`/`.run-gate` | C2 |
| `cmru-bugs62` | `70dff0adf` | 6 commits unique | KI-58/59/64/65/68 (ported or superseded) | 5 files not carried | C3 |
| `cmru-ki52-focused-20261006` | `2a0e0706d` | 22 unique | KI-52 (shipped 6.0.0) | none known | C3 |
| `cmru-inst62` | `18d50209d` | 4 unique, absorbed nowhere | installer render/check verbs, `stub_launchers` | keep/ship/drop decision; its "KI-69" collides with the candidate's KI-69 | not a candidate |
| nested `cmru-release-20261016_044619-pwmcp-rji18k` | `6cfe7531e` | public tag commit only on candidate branch | PWMCP 1.63.0-r3 | merge into main first | not a candidate |

### 8.2 Cleanup preconditions (from inventory §7, accepted)

Act only through supported verbs (`ciu worktree …`, `cmru cleanup/abandon`); re-check
`git status --porcelain --ignored` and archive `.assay/`, `.run-gate/`, logs and evidence first
(CIU-145); re-verify ancestry against a ref that exists on origin (local `main` is not on origin);
delete a branch only after its tip is reachable from a retained ref or tag. Class counts: C1 = 53
(absorbed into local main — which itself is not on origin yet), C2 = 11 (absorbed only in the
unreleased TRI/CGP lineage), C3 = 39 (residue to disposition). **Sole-copy uncommitted work to
preserve before anything else:** `cgprofile-release-20261015` (CP-24/25/26, 13 M + 65 untracked),
`run-gate-rg93-git-metadata-20261013` (~3 000 lines: CIU-145 fix + worktree schema v6, Run-Gate
B108 phase 2, Assay B108 docs), `stash@{0}`, and the small untracked reports listed in inventory
§1 item 5. No cleanup was performed by this review.

## 9. Independent-review status

- Controller (this report): full KI-82 path read; other areas through Sonnet sub-reviews whose
  confirmed findings I re-verified (repros for C1, C2, C3, C4, detached HEAD, 6.2.0 binding).
- Sonnet adversarial review of `a2b7dfa9f` found no blocker and seven minor/nit items
  (M1 comment pairing after a quoted opener, M2 multi-line code spans, M3 escaped backticks, M4 a
  broken SPEC sentence, M5 inline-code rule missing from user docs and S4.6a/b numbering, M6
  reader/writer `HEAD` symmetry, M7 loose guard); all seven fixed in `0cfaf7909` with tests, and
  it confirmed items 1–9 of the first set (including that the real `latest.json` fits the new URL
  shape, `--minor` gives 6.2.0, and every S16.4 claim maps to code). `0cfaf7909`/`5268e2e85`
  have **not** had a second review.
- Still required by doctrine before merge: an independent review (fresh reviewer, not this
  controller) of `cmru-v6.1.0..<final>` — at minimum of `a2b7dfa9f` and KI-82 — and the
  registered aggregate gate on the final integrated tree.

## 10. Ordered remaining steps: CGProfile → Run-Gate → CMRU → CIU/Assay

0. **Preserve** the sole-copy uncommitted trees (`cgprofile-release-20261015`,
   `run-gate-rg93-git-metadata-20261013`, `stash@{0}`, untracked reports) on dedicated branches
   or archives. Decide the integration route for the TRI→RG93→CGP lineage (merge into `main`, or
   KI-82 feature-source releases) and reconcile local `main` (+46) with `origin/main` (+41).
1. **CGProfile 1.0.0** from the integrated lineage: commit-or-park CP-24..26; exact-tip registered
   gate; `cmru release cgroup-profiler --set-version 1.0.0` with a controller that has the
   needed features (a bootstrap wheel built from the candidate; published `cmru-v6.1.0` lacks
   `--from-worktree`).
2. **Run-Gate 24.0.0**: integrate RG-93..RG-104 (+ decide the dirty B108-phase-2/schema-v6 work),
   including `run-gate-project/tools/release_candidate.py`, the root `tester-unified`
   `image_env` (`cd673fecd`), the retired `run-gate.py` copies, and the CGProfile/Nyxloom
   wheel-builder recipes; exact-tip gate; release; rebuild `tester-unified` from the published
   wheel.
3. **CMRU 6.2.0**: rebase/merge this candidate onto the integrated main; the six cross-estate
   coverage checks must then turn green (they encode exactly step 2's content); run the
   registered `coverage` and `gate-provisional` lanes on that exact tree; obtain an independent
   review; flip the "pending" caveats (README L675, CONSUMERS L1281, DESIGN-GUIDE L1291,
   CONTRIBUTING L59-64, SKILL.md L77-78, CHANGES KI-82 line) in the release-prep commit; release
   with `cmru release cmru --minor`; then run the full `gate` R2 follow-up at `cmru-v6.2.0` and
   restore the release step to `gate` (KI-79). Decide `cmru-inst62`; KI-83 stays open.
4. **CIU** (independent of Assay): CIU-135 gate/review; integrate or decide CIU-134
   (`ciu-134-root-retry-20261014`); land CIU-145 from the rg93 dirty tree before any bulk prune.
5. **Assay 10.0.0**: exact-tip gate for the Assay 10 / contract-2 items; integrate
   `assay-release-hardening-20261013` (1821c8e66) or drop it; B108 phase 2.
6. Only after each release: act on the matching inventory §7 rows under §8.2's preconditions.
