# ciu-P25 — V8-PREP-7: `env_required` declarations

**Handoff:** `nyxloom-trove/handoffs/ciu-P25-v8prep7-env-required.md`
**Branch:** `feat/ciu-qol-v8prep-wave` · **Base HEAD:** `27e2cd91` (ciu-P24,
confirmed with `git status --porcelain && git log --oneline -3` before any
edit — tree was clean).

**Status: COMPLETE.** Full gate green (3005 passed, 100.00% line+branch
coverage). No `escalate_if` fired. No scope widening.

---

## 1. Reading, before any code

Read the handoff in full, then in order: `docs/BACKLOG-2026-08-24.md`'s
V8-PREP-7 row, `docs/CIU-V8-TESTING-GATE-PROPOSAL.md` §1.20,
`src/ciu/composefile.py`'s `compose_process_env` (the real function, not the
handoff's approximate line citation) and traced its TWO real callers in
`engine.py` (`main_execution`'s native `up` path, step 16; `run_shipped`'s
config-less path) to establish the exact call order relative to secret
materialization, `src/ciu/config_model.py`'s `expand_env_vars_or_fail`
(collective-error style to mirror) and its `render_jinja2_text`/global-chain
render context assembly (`{"env": dict(os.environ ...)}` at line ~450, not
the handoff's approximate ~342 — confirmed against the live commit per the
handoff's own instruction), `composefile.py`'s own two independent
`{"env": dict(os.environ)}` assemblies (`render_compose` ~267,
`render_configfiles`'s per-instance context ~954), and
`src/ciu/workspace_env.py`'s `GENERATED_IDENTITY_KEYS`/`REQUIRED_KEYS_CORE`
constants (lines 48/56, read-only, forbidden file).

## 2. The design puzzle: `engine.py` is forbidden, but it is the only real
   call site with both the config and the checkable env in hand

The handoff's `escalate_if` only worried about `compose_process_env`'s call
order relative to materialization turning out to be different than
documented. Tracing `engine.py` concretely: `compose_process_env` is called
exactly once on the native path, at **Step 16/17**, strictly AFTER
materialization (Step 10), configfile rendering (Step 12), compose template
rendering (Step 13), leak scan (Step 14), and overlay generation (Step 15) —
i.e. genuinely "immediately before the compose invocation." The `escalate_if`
did not fire.

A harder, unstated puzzle surfaced instead: `env_required` is a config
declaration (`[<root>.<service>] env_required = [...]`), read from the
merged stack config keyed by `root_key` — but `compose_process_env`'s real
`engine.py` call site (`composefile.compose_process_env(specs, materialized,
compose_profiles=compose_profiles)`) passes neither the merged config nor
`root_key`, and `engine.py` is in this package's `scope.forbid`. Unlike
ciu-P24's `ciu_context` object (threaded, unchanged, through TWO calls
`engine.py` already makes for the same stack render), `compose_process_env`'s
call site receives no shared mutable object at all that another,
already-wired function could mutate ahead of time — there is no back door
into this specific call.

Reading `docs/BACKLOG-2026-08-24.md`'s sibling V8-PREP entries settled this:
V8-PREP-1 (`ciu.user_tables`), V8-PREP-4 (`local_stack`), and V8-PREP-5
(`one_shot`) are ALL shipped as `Status: FIXED-partial` — additive
groundwork, declaration + validation only, explicitly **NOT** wired into the
live pipeline where doing so would require touching a forbidden file or the
real V8 breaking cutover. V8-PREP-5 in particular: `one_shot = true` is
"declaration + shape validation only — it is NOT wired into the health
[check machinery]." This is the established, deliberate pattern for this
whole wave whenever the real activation point is a forbidden file.

**Resolution:** `compose_process_env` gains two new, fully optional,
keyword-only parameters, `config` and `root_key`. When BOTH are given, the
function shape-validates and presence-checks `env_required` against the
exact env dict it itself just built, before returning it — the only point
in the whole codebase where "the actual environment `docker compose` will
see" exists as a Python object. Omitting either (the ONLY way `engine.py`'s
real call site invokes it today) is a complete no-op — O1's "absent -> zero
behavior change" holds not just for configs that don't declare
`env_required`, but for the CALL SITE itself, today. Wiring `engine.py`'s
Step 16 call to pass its already-computed `merged`/`root_key` through is a
one-line change, explicitly named as deferred to the real V8 cutover in
SPEC.md S8.2a, CONFIG.md, CHANGES.md, and the backlog row — the same honesty
convention P21/P23/P24 already established, not a new one invented here.

This also settles WHY the presence check cannot live anywhere upstream
(`render_configfiles`/`render_compose`, both already wired and already
receive `root_key`+`merged`): their own `{{ env.* }}` Jinja context is
built from bare `os.environ`, which never contains an `expose_env` secret
value — that value exists ONLY inside `compose_process_env`'s returned dict,
injected right before the subprocess call. Checking presence anywhere
upstream of `compose_process_env`, no matter how late in the pipeline, would
reproduce the exact O2 false-failure trap for any var supplied via
`expose_env` — confirmed concretely by
`TestComposeProcessEnvRequired::test_o2_negative_checking_the_pre_materialization_env_false_fails`.
`compose_process_env` is not just A valid integration point, it is the ONLY
correct one.

## 3. Design notes

### Shape validation reuses `_resolve_service_instances`'s opportunistic
   convention, not a new reserved-key list

`resolve_env_required(root)` iterates `root.items()` and treats ANY Mapping
child declaring an `env_required` key as a service — the identical
"opportunistic, any-Mapping-child-with-the-key-I-care-about" convention
ciu-P24's `_resolve_service_instances` already established for `instances`.
No separate list of reserved stack-level keys (`secrets`, `hooks`,
`governance`, ...) is needed: those tables never declare an `env_required`
key of their own, so they are silently skipped exactly like any other
service that didn't declare one.
`TestResolveEnvRequired::test_reserved_looking_table_without_the_key_is_harmless`
pins this directly against `[<root>.secrets]`/`[<root>.hooks]`-shaped input.

### "Present but empty" counts as missing, matching `expand_env_vars_or_fail`

The oracle only mandated mirroring `expand_env_vars_or_fail`'s collective-
error STYLE, not its exact missing-value semantics. I chose to also mirror
its "`None` or `''` both count as missing" rule for `check_env_required`,
since an env var explicitly declared `env_required` but exported empty is
exactly as broken for a downstream container as one that's unset, and
diverging from the codebase's one existing precedent for this class of
check would be a needless inconsistency.
`TestCheckEnvRequired::test_empty_string_value_counts_as_missing` pins this.

### `env_required = []` is valid, not a special case

The oracle requires "a list of non-empty strings" — it does not require the
LIST itself to be non-empty. An empty list vacuously satisfies every
per-entry rule and needs no special-casing in either
`resolve_env_required` or `check_env_required` (an empty list contributes
zero missing entries). `TestResolveEnvRequired::test_empty_list_is_valid_and_harmless`
pins this rather than leaving it as an untested implicit assumption.

### Why no code change was needed to satisfy O4

`{{ env.* }}` in `render_compose`/`render_configfiles`/the global-chain
render is built as `dict(os.environ)` in three independent places today,
none of which this package touches — confirmed by `git diff` showing zero
lines changed in any of the three. O4's test,
`TestEnvRequiredNoNewJinjaMechanism::test_env_dot_star_already_renders_a_required_style_variable`,
renders a template referencing `{{ env.DATABASE_URL }}` against a
monkeypatched `os.environ` entry and confirms it renders — proving the
EXISTING mechanism already serves the exact use case `env_required` exists
to guard, with zero new code.

## 4. Files changed

| File | What |
|---|---|
| `src/ciu/composefile.py` | New `resolve_env_required(root)` (O1 shape validation — list-of-valid-identifier-strings, no per-service duplicates, tagged `[V8-PREP-7]` errors naming the service and bad value); new `check_env_required(env_required, env)` (O2/O3 — collective missing-`(service, var)` error, empty-string-counts-as-missing); `compose_process_env` gains optional keyword-only `config`/`root_key` — when both given, runs the above against its own freshly-built `env` dict before returning; module header docstring (S8.2a) and public-API list updated |
| `tests/tests/test_ciu_composefile.py` | +28 new `def test_` functions (+9 more collected items via two parametrized functions) across 4 new classes: `TestResolveEnvRequired` (O1 — valid/multi-service/non-Mapping-skip/reserved-table-harmless/non-list/invalid-entry-type ×6/invalid-charset ×5/duplicate/empty-list/valid-charset), `TestCheckEnvRequired` (O2/O3 direct-unit — present/empty-no-op/single-missing/empty-string-missing/collective-across-services), `TestComposeProcessEnvRequired` (the real integration point — zero-behavior-change omission variants, O1 malformed-declaration-through-this-entry-point, the O2 expose_env-satisfies positive AND the pre-materialization-environment negative, O3 collective-across-services), `TestEnvRequiredNoNewJinjaMechanism` (O4) |
| `docs/SPEC.md` | New `S8.2a` under `## S8 — Compose execution` — shape rule, the "no new Jinja mechanism" claim with real citations, the exact check-timing rule and WHY (the `expose_env`-never-touches-`os.environ` fact), the machine-identity-keys cross-reference, and an explicit "not yet wired to the live pipeline" statement naming `engine.py` and the deferred one-line V8-cutover change |
| `docs/CONFIG.md` | New `#### [<root>.<service>] env_required = [...]` subsection (sibling of the existing `instances = N` one) with a worked example, a "not a new way to read an env var" callout box, a machine-identity-keys cross-reference to the existing `ciu.env` Key Provenance Table, and a "not yet wired to `ciu up`" callout |
| `CHANGES.md` | New `feat(ciu):` Unreleased entry (non-breaking — nothing existing changes behavior) enumerating the shape rule, the check's exact timing/collective-error style, the "no new Jinja mechanism" + explicit QOL-10 `${VAR:-fallback}` non-overlap statement, and the "not yet wired" gap |
| `docs/BACKLOG-2026-08-24.md` | V8-PREP-7 row: `Status: FIXED-partial`, corrects the original entry's "known machine identity keys auto-injected into Jinja context" wording (they were already available via the pre-existing `{{ env.* }}` mechanism; nothing was "auto-injected" by this package), names the deferred `engine.py` wiring explicitly |

No `scope.forbid` file was touched — confirmed both before writing any code
and again just before this commit:

```
$ git diff --stat -- src/ciu/engine.py src/ciu/deploy.py src/ciu/config_model.py \
    src/ciu/workspace_env.py nyxloom-trove/backlog.md nyxloom-trove/decisions.md \
    nyxloom-trove/roadmap.md
(empty)
```

## 5. Oracle-by-oracle evidence

| Oracle | Verdict | Evidence |
|---|---|---|
| **O1** shape validation | **MET** | Absent anywhere -> `{}`: `test_absent_anywhere_returns_empty_zero_behavior_change`. Valid single/multi-service: `test_single_service_valid_list`, `test_multiple_services_declared`. Non-list value: `test_non_list_value_raises_naming_service_and_value`. Empty-string/non-string/invalid-charset members: `test_invalid_entry_type_raises` (6-way parametrize: `""`, `123`, `None`, `True`, `1.5`, `["nested"]`) and `test_invalid_charset_raises` (5-way parametrize: leading digit, hyphen, space, dot, leading hyphen). Duplicates: `test_duplicate_entry_within_one_service_raises`. Also exercised THROUGH the real integration point: `TestComposeProcessEnvRequired::test_o1_malformed_declaration_raises_through_this_entry_point`. |
| **O2** check timing | **MET** | Positive: `test_o2_expose_env_secret_satisfies_env_required_no_false_failure` — a var supplied ONLY via a secret's `expose_env`, absent from `base` entirely, is NOT flagged missing when checked against `compose_process_env`'s own output. Negative (the exact false-failure this oracle names): `test_o2_negative_checking_the_pre_materialization_env_false_fails` — the SAME variable, checked against the bare pre-materialization environment, DOES raise, proving the wrong check point reproduces the bug this package exists to avoid. |
| **O3** collective error | **MET** | `TestCheckEnvRequired::test_o3_collective_error_names_every_missing_pair_not_just_first` (direct unit — one present, two missing across two services, both named, present one absent from the message) and `TestComposeProcessEnvRequired::test_o3_missing_across_multiple_services_one_error` (through the real entry point). |
| **O4** no new Jinja mechanism | **MET** | `TestEnvRequiredNoNewJinjaMechanism::test_env_dot_star_already_renders_a_required_style_variable` — `{{ env.DATABASE_URL }}` renders correctly via the untouched, pre-existing mechanism. Confirmed via `git diff` that none of the three `{"env": dict(os.environ)}` assemblies in `composefile.py`/`config_model.py` changed. |
| **O5** docs | **MET** | SPEC.md S8.2a (new, own subsection). CONFIG.md's new `env_required` subsection, cross-referencing the `ciu.env` Key Provenance Table for the machine-identity keys rather than duplicating it. CHANGES.md Unreleased entry. `docs/BACKLOG-2026-08-24.md`'s V8-PREP-7 row updated with the additive subset shipped, correcting the stale "auto-injected" wording. Explicit statements, in SPEC.md/CONFIG.md/CHANGES.md/this LOG, that `${VAR:-fallback}` handling is untouched (QOL-10, separate, breaking, deliberately out of scope). |

## 6. Gate output (verbatim, read in a separate step from the run itself)

```
$ .venv/bin/python run-ciu-tests.py
...
src/ciu/composefile.py                             482      0    244      0   100%
...
--------------------------------------------------------------------------------------------
TOTAL                                             8988      0   3658      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
============================ 3005 passed in 19.63s =============================
```

3005 tests passed, exit code `0`. Verified against the true pre-package
baseline by `git stash -u` (stashing every uncommitted change back to the
clean `27e2cd91` tree) and re-collecting: `2968 tests collected`, matching
ciu-P24's own LOG-reported final count exactly, then `git stash pop`
restored this package's work (re-confirmed green immediately after, same
3005/100.00% result). +37 net new test items = 28 new `def test_` functions
(11 `TestResolveEnvRequired` + 5 `TestCheckEnvRequired` + 11
`TestComposeProcessEnvRequired` + 1 `TestEnvRequiredNoNewJinjaMechanism`)
plus +9 from the two parametrized functions (6-way and 5-way, each counted
once above as a single function but collected as N items: `(6-1)+(5-1)=9`).

**Targeted regression check** (every file this package's changes could
plausibly affect):

```
$ .venv/bin/python -m pytest tests/tests/test_ciu_composefile.py \
    tests/tests/test_ciu_documentation_contract.py tests/tests/test_spec_contracts.py \
    tests/tests/test_ciu_test_repo.py -q
250 passed in 5.14s
```

## 7. Commits

1. Implementation + tests + docs (`src/ciu/composefile.py`,
   `tests/tests/test_ciu_composefile.py`, `docs/SPEC.md`, `docs/CONFIG.md`,
   `CHANGES.md`, `docs/BACKLOG-2026-08-24.md`) — one commit, per
   `git commit --only -F - -- <paths>`.
2. This LOG file — a separate commit.

Exact hashes are in this package's final report (read back via `git log
--format=%H`, not predicted ahead of the actual commit).
