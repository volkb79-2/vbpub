# ciu-P46 — REPORT

Package: `nyxloom-trove/handoffs/ciu-P46-persist-secret-hooks-migration-check.md`
(A1-A5). Branch `worktree-agent-a3aef243215d54b0d`, based on vbpub main
`754cf35c`. Final HEAD at gate time: **`bc23f15d`**. **Not merged to main** —
a fresh adversarial reviewer verifies first, per this repo's pipeline.

## 1. The real gate — verbatim verdict

Command (run from `<worktree>/ciu`, the only place `./run-gate.py` exists):

```
./run-gate.py ciu --worktree /workspaces/vbpub/.claude/worktrees/agent-a3aef243215d54b0d
```

Run TWICE. Verdict read in a separate step from the run each time (output
redirected to a file, then read), never off a piped tail.

### Run 1 — at `af334ee8` — **FAILED**

```
assay-3.2.0.pyz: OK
ciu: FAIL/EXCLUDED_LINES (exit 1)
  commit: af334ee8752ccffe47a8b8c8a382dc1b7bb047ac
  argv: /opt/tester-venv/bin/python run-ciu-tests.py
run-gate: lane 'ciu' failed with exit 1; full container logs preserved at /tmp/run-gate/run-gate-vbpub-ciu-564414-1788308396.log
run-gate: lane 'ciu' exit 1
```

Verdict artifact's R1 claim named the cause exactly:

```json
"reason_code": "EXCLUDED_LINES",
"excluded_lines": {"ciu/src/ciu/secrets/materialize.py": [624, 625, 626, 627]},
"judgment": {"r1": {"allow_excluded": false, "fail_under": 100.0,
                    "mode": "changed_lines", "require_branch": true}}
```

Those four lines were a single `# pragma: no cover` on
`_write_hook_manifest`'s temp-file cleanup. **This is the
`assay-gate-vs-pytest-gap` in the flesh**: `run-ciu-tests.py` (the real
`--cov=ciu --cov-branch --cov-fail-under=100` invocation) reported
`Total coverage: 100.00%` at that same commit, because coverage.py honours
the pragma and assay does not. A `pytest`-green claim would have been wrong.
Fixed in `bc23f15d` by deleting the pragma and covering the branch for real.

### Run 2 — at `bc23f15d` — **PASSED** (the final HEAD)

```
run-gate: admission: lane 'ciu' declares no resources.memory — not memory-accounted (shared-infra rules still apply)
run-gate: rev 32 | lane ciu | env [environments.tester-unified] in central /workspaces/vbpub/.claude/worktrees/agent-a3aef243215d54b0d/run-gate.toml | slice dev-background.slice ($CGROUP_PARENT_DEV_BACKGROUND)
run-gate: ephemeral env (nothing declared)
run-gate: budget 30m (advisory)
assay-3.2.0.pyz: OK
ciu: PASS (exit 0)
  commit: bc23f15d5b3e76d4a34a03dbdde969687bd8a578
  argv: /opt/tester-venv/bin/python run-ciu-tests.py
run-gate: verdict artifact: /workspaces/vbpub/.claude/worktrees/agent-a3aef243215d54b0d/ciu/.assay/verdict-ciu.json
run-gate: lane 'ciu' exit 0
```

Verdict artifact (`.assay/verdict-ciu.json`, schema_version 8), per claim:

```
outcome  PASS   reason_code  (none)   scope S1   declared_rigor ['R0', 'R1']
R0  PASS
R1  PASS  pct=100.0  covered=513/513  branches=82/82  considered=8 files
          excluded_lines={}
judge: assay 3.2.0 zipapp, sha256 bbbed3ef35cb8ac3e62075c62fcdb801b7a668b6fc72aa0180419ac4996b84d6
base: 754cf35cfbddaf7f82a141cdf312ba4bbe0a22f3 (merge-base), source_roots ['src']
```

## 2. Test counts

`run-ciu-tests.py` (the real `--cov=ciu -n auto --dist loadfile --cov-branch
--cov-fail-under=100` invocation) at final HEAD:

**3519 passed**, **100.00% line + branch coverage across every module**
(TOTAL 10162 statements / 4090 branches, 0 missing), zero warnings beyond the
pre-existing third-party `schemathesis` `DeprecationWarning`.

Before this package: 3434 tests. Net **+85**, of which 82 are the two new
files (`test_ciu_hook_persisted_secrets.py`, `test_ciu_migration_check.py`)
and the remainder are net additions inside rewritten provider/hook tests.

## 3. Per-oracle: A1-A5 as actually implemented

| Handoff item | Landed as | Evidence |
|---|---|---|
| A1 `persist:"secret"` kind | `hooks_runner._persist_secret` + `materialize.write_hook_secret` | `test_ciu_hook_persisted_secrets.py` — store path/mode/dir-mode, raw bytes with no trailing newline, `[state]` untouched, config untouched |
| A1 storage reuses `materialize.py` | `_write_store_file`/`_ensure_dir_mode`/`_flock` called verbatim; synthetic `SecretSpec(kind="HOOK")` supplies mode/uid | mode 0440 and store-dir 0700 asserted directly off `stat()` |
| A1 collision is a contract violation | 4th refusal in `_persist_secret`, fed by the engine's own `specs` | `test_persist_secret_refuses_a_name_a_directive_already_declares` |
| A1 `apply_to_config` REJECTED | raised before the `apply` mutation | 2 tests: the refusal, and that the config is still unmutated after it |
| A1 never logged | every refusal names the KEY | asserted per-case: `SECRET_VALUE not in str(excinfo.value)` |
| A1 `ciu secrets list` / `reset` | provenance sidecar → `hook_secret_rows`/`reset_hook_secrets`, wired into `secrets_command` | 4 behavioural tests through `engine.main(["secrets", …])` |
| A1 SPEC clause | new **S9.4a** after S9.4 (S9.4's own signature updated to `"state" \| "secret"`) | `docs/SPEC.md` |
| A2 source #3 hard cutover | `[state]` read deleted; store-file read added; no fallback | `test_state_root_token_is_never_read_s4_16`, `test_token_source_3_ignores_legacy_state_root_token` |
| A2 `[state].initialized` unchanged | fixture still returns it via `persist:"state"` | `test_demo_vault_hook_persists_only_the_non_secret_initialized_flag` |
| A2 SPEC S4.16 text | rewritten + new **S4.16a** (the static requirement F7 enforces) | `docs/SPEC.md` |
| A2 fixture steps 1-3 | hook return deleted, `[state].root_token` removed, docstring rewritten | see §4 for the one open judgment call |
| A2 §B.2 prose + a genuine minting example | §B.2 corrected, new **§B.2a** | `docs/SPEC.md` |
| A2 CONSUMERS migration note | **#20**, named + dated, CIU-54/CIU-75 convention | `docs/CONSUMERS.md` |
| A3 vault-presence stage | `_check_stack_config`, appended stage, `[S13.4d]` message shape as specified | 4 tests incl. both directives and the already-failed-secrets silence |
| A3 runs pre-`ciu up` for free | ordinary stage ⇒ S13.4c picks it up; no new wiring | (falls out; no separate mechanism added) |
| A4 state-secrets stage | `config_model.is_secret_shaped`/`find_secret_shaped_keys` + stage | 4 stage tests + an 18-case heuristic truth table |
| A4 SPEC clause | new **S3.4a**, adjacent to S3.4; nothing renumbered | `docs/SPEC.md` |
| A5 verb + registry | `src/ciu/migration_check.py`, `ciu migration-check` in `cli.py` | 25 tests |
| A5 exactly 3 rules, 4th stays dropped | `RULES` pinned by name in a test | `test_the_registry_is_the_documented_v1_rule_set` |
| A5 no version comparison | proven behaviourally (same findings with `get_cli_version` stubbed to an ancient value), not by inspection | `test_no_detector_reads_an_installed_version` |
| A5 one registry, two entry points | stage calls `run_migration_check`; a test asserts it did | `test_check_stage_walks_the_same_registry_as_the_verb` |
| A5 exit-code split | verb: any finding → non-zero (WARN-only run tested); stage: WARN → note, ERROR → fail (both tested) | 4 tests |
| A5 SPEC clause | new **S13.7** + **S13.4d**, stage table rows for all three | `docs/SPEC.md` |
| Extensible for ciu-P47 | P47 flips one constant; its rule then fires with no registry edit — proven now | `test_retired_overlay_rule_fires_once_the_name_is_no_longer_current` |
| CIU-38 untouched | its row and detail section unmodified; the correction recorded in the backlog header | `git show` of commit 2 |

## 4. Judgment calls the handoff left open

**(a) The reference fixture would have lost source #3 entirely — resolved with
an existing mechanism, not a new one.** The handoff's fixture analysis
(delete the `root_token` return; remove `[state].root_token`) is correct and
was followed. Its unstated consequence: that fixture's `root_token` is
`GEN_LOCAL`-declared, so it materializes to the PROJECT store
(`.ciu/secrets/demo/vault_root_token`), NOT to the per-stack path source #3
now reads — and A1's own uniqueness rule forbids the hook from persisting
that name at all. The demo's later `GEN_TO_VAULT` stacks would therefore have
had no token from any source. Rather than invent mechanism (contradicting the
handoff) or ship CIU's canonical example knowingly broken, I added
`[vault].token_file = ".ciu/secrets/demo/vault_root_token"` to
`test-repo/ciu.global.defaults.toml.j2` — S4.16 **source #2**, an existing
documented mechanism, pointed at the same 0440 store file. This is now the
prescribed migration for any consumer with a directive-backed vault token,
and is stated as such in CHANGES.md §2 and CONSUMERS.md #20. **A reviewer
should sanity-check this specific decision.**

**(b) S2.4.1 is not implemented in v7 — the heuristic was assembled from where
each half actually lives.** The handoff says to reuse "the exact heuristic
already implemented for S2.4.1". Grep proves `S2.4.1` exists only in
`docs/SPEC-V8.md`; nothing implements it. The KEY test therefore comes from
S2.4.1's normative text (last `_`-separated component in the eight-name
list); the VALUE test comes from S3.1a's `scan_override_for_secrets`, which
IS implemented — factored into a shared `is_secret_shaped`, with
`scan_override_for_secrets` now consuming the shared minimum-length constant,
so there is one implementation rather than two that can drift. **One S2.4.1
exclusion was deliberately dropped**: its KV-path-shape test
(`^[a-z0-9_./-]+$`) would exclude `s.demo-token` and most dev-mode Vault
tokens, defeating the rule in its central case; S3.1a's own `"/" in value`
path test is used instead. All 18 cases pinned by a parametrized test.

**(c) Rule 1's "will find nothing today" could not be taken literally.** A
bare existence check on `ciu.global.worktree.toml.j2` would WARN on every real
checkout, about the LIVE file. The detector filters a `RETIRED_OVERLAY_NAMES`
history literal against the current `GLOBAL_CONFIG_WORKTREE_OVERRIDES`
constant, so it is silent while the name is current and goes live the moment
ciu-P47 flips that constant — satisfying the handoff's stated requirement AND
being correct. Both states are tested.

**(d) Rule 3 is silent when `.gitignore` is ABSENT.** A checkout that never ran
`ciu init` is a first-run state, not a stale artifact; firing there would have
added a WARN note to essentially every `ciu check` for no signal. Documented
in the detector's docstring and in S13.7.

**(e) The provenance sidecar is my design, not the handoff's.** The handoff
left the `ciu secrets list` marker shape to the implementer ("your call on the
exact shape"). A store-directory scan was rejected because it cannot
distinguish a hook-persisted file from a stale one left by a removed
declaration, and cannot supply the requested `hook:<script>` attribution.
`.hook-persisted.toml` (names + hook paths only, never values, mode 0600,
inside the already-0700 store dir) is the alternative chosen; it is what makes
`reset` work for these names too.

**(f) `run_hooks` gained a keyword-only parameter.** `declared_secret_names`
defaults to `None`, so every existing positional caller is unaffected; five
in-repo test stubs took exactly five positional parameters and gained `**_kw`.

## 5. What was NOT done (by scope)

- The ciu-P47 overlay split/rename. The registry is left genuinely extensible:
  P47 adds one detector body (or, for rule 1, only flips the filename
  constant) and touches no plumbing.
- CIU-38 — untouched, per the handoff's own correction.
- No merge to `main`. Four commits sit on the worktree branch:
  `8ece3eb5` (feat, code+tests+docs), `af334ee8` (backlog),
  `bc23f15d` (pragma fix — the gate-green commit), and this LOG/REPORT.

---

# Addendum — fix pass for the adversarial review (ACCEPT-conditional)

Input: `nyxloom-trove/reports/ciu-P46-REVIEW.md`. Mechanism verdict on A1-A5
was **ACCEPT** (guards planted-and-fired, 8 hollow-test mutations all caught,
the `token_file` deviation and all five judgment calls independently
re-verified). This pass changed **no A1-A5 mechanism**: 8 blockers were
documentation/consumer-dimension gaps, plus N1, an unambiguous detector bug.
The one design ask (an escape hatch for A4's heuristic) was resolved by the
operator as *ship as-is, no suppression mechanism* — nothing was built for it.

## Gate — run 3, at `fee25e18` — **PASSED**

Required, because B1 changed a pinned test. Same command, verdict again read
in a separate step from the run.

```
run-gate: admission: lane 'ciu' declares no resources.memory — not memory-accounted (shared-infra rules still apply)
run-gate: rev 32 | lane ciu | env [environments.tester-unified] in central /workspaces/vbpub/.claude/worktrees/agent-a3aef243215d54b0d/run-gate.toml | slice dev-background.slice ($CGROUP_PARENT_DEV_BACKGROUND)
run-gate: ephemeral env (nothing declared)
run-gate: budget 30m (advisory)
assay-3.2.0.pyz: OK
ciu: PASS (exit 0)
  commit: fee25e18fd59def2a2ae928c34b911c7822e145f
  argv: /opt/tester-venv/bin/python run-ciu-tests.py
run-gate: verdict artifact: /workspaces/vbpub/.claude/worktrees/agent-a3aef243215d54b0d/ciu/.assay/verdict-ciu.json
run-gate: lane 'ciu' exit 0
```

Verdict artifact:

```
outcome PASS   reason_code (none)   scope S1   declared_rigor ['R0', 'R1']
R0 PASS
R1 PASS  pct=100.0  covered=522/522  branches=82/82  considered=9 files
         excluded_lines={}
```

Full suite at the same HEAD: **3523 passed**, 100.00% line + branch
(was 3519 before this pass; net +4).

## Per-blocker disposition

| Item | Fixed in | Note |
|---|---|---|
| B1 example hook + its 2 pins | `post_compose_example.py`, `test_ciu_hook_examples_deeper13.py`, `test_ciu_shipped_hook_contracts.py` | switched to `persist:"secret"`; new guard asserts the shipped example against the SAME `is_secret_shaped` predicate the stage uses, not a restated rule |
| B2 examples README | `src/ciu/hooks/examples/README.md` | all three false statements + a new "Choosing between the two" section |
| B3 S7.6 message | `deploy.py:479-484` | mirrored `engine.py`'s already-corrected wording |
| B4 test-repo README | `test-repo/README.md` | includes WHY both `[state]` and `persist:"secret"` are wrong for that name |
| B5 CIU.md structured-return block | `docs/CIU.md` | replaced with SPEC §B.2a's production shape + the two-destination split |
| B6 CIU-DEPLOY source #3 | `docs/CIU-DEPLOY.md` | |
| B7 front-door phrase | `README.md` | |
| B8 CHANGES overclaim | `CHANGES.md` | struck "masked, leak-scanned"; states what IS enforced and that scanning is not needed. Grep-verified the only other instance of that phrase (`SPEC.md` §B.2) is CORRECT — it describes a genuinely directive-materialized secret |
| N1 gitignore glob | `migration_check._gitignore_key` + 3 tests | see below |

## N1 — one thing the review did not anticipate

The prescribed `**/` normalization is not just a heuristic: git treats a
pattern with no `/` (or only a trailing one) as matching at any depth, so
`**/ciu.env` and `ciu.env` ignore identical path sets. Implemented
symmetrically.

It cleared four of five findings against ciu's own checkout and left one:
**`ciu.worktree-instance.json`, a TRUE positive** — genuinely absent from
ciu's `.gitignore` since CIU-61 added it to the canonical set. The review's
N1 wording ("so `ciu migration-check` is clean against ciu's own repo")
assumed globs were the whole story. Silencing a real finding to satisfy that
sentence would have been exactly backwards, so the `.gitignore` was fixed
instead, with a comment recording that the new rule found it. Three tests
cover this: the prescribed `**/`-prefixed fixture, a run against the REAL
`.gitignore` (no synthetic fixture would have caught the original bug — the
real file's spellings are the trigger), and a proof that a genuinely dropped
entry still fires, so the normalization did not quietly disable the rule.

**Reviewer-facing note:** the `.gitignore` edit is the one change in this pass
that is not literally in the B1-B8/N1 list. It is the honest route to N1's
stated goal, and it is a one-line hygiene fix to ciu's own repo, not a change
to any shipped behaviour.

## Also checked, not a defect

`src/ciu/hook_templates/post_compose_db.py` uses `persist:"state"` twice —
the same shipped-template class as B1 — but persists two BOOLEANS, which
`is_secret_shaped` correctly ignores. Left unchanged.

## Final state

Six commits on `worktree-agent-a3aef243215d54b0d`, **not merged**:
`8ece3eb5` (feat), `af334ee8` (backlog), `bc23f15d` (pragma fix),
`d58b9dc5` (LOG/REPORT), `fee25e18` (**review fix pass — the gate-green
commit**), and this addendum.
