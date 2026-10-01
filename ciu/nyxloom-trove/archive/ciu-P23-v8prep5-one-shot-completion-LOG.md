# ciu-P23 — V8-PREP-5: one-shot completion semantics

**Handoff:** `nyxloom-trove/handoffs/ciu-P23-v8prep5-one-shot-completion.md`
**Branch:** `feat/ciu-qol-v8prep-wave` · **Base HEAD:** `133747db` (ciu-P22,
confirmed with `git status --porcelain && git log --oneline -3` before any
edit — tree was clean).

---

## Files changed

| File | What |
|---|---|
| `src/ciu/provisioning.py` | `_STACK_RE` extended to `healthy\|completed`; `parse_ref`'s stack branch carries the terminal into `subkind` (`''` for `:healthy`, unchanged; `'completed'` for the new terminal); new `_resolve_declared_stack_path` (shared O4/O5 resolution), `_declared_stack_paths`, `_stack_container_name`, `_one_shot_stack_service` helpers; `lint_graph`'s dependency-graph builder resolves a selector to its declared stack path before adding the edge (O5); `_probe_stack` rewritten: `:completed` branch (Running/ExitCode only, never Health), O2's `[WARN]` on the exit-0-no-healthcheck fallback, O3's one_shot cross-reference `[WARN]`, O4's container-name resolution via the new helper |
| `src/ciu/config_model.py` | `_REF_RE`'s stack alternative extended to `healthy\|completed` (second grammar copy, kept in sync); both "Examples:" error strings (this file and provisioning.py) gain a `stack:db-core:completed` example |
| `src/ciu/deploy_pkg/phases.py` | new `service_one_shot(service: dict) -> bool` (O3), placed adjacent to `service_health_timeout`, mirroring `service_shipped`'s exact validation pattern (default `False`; non-bool -> tagged `[S7.2]` ValueError) |
| `tests/tests/test_ciu_provisioning.py` | +39 test functions (one parametrized to 7 cases) covering O1 (`:completed` parse + probe semantics, including the "never reads Health" gap-closing test), O2 (deprecation warning present/absent), O3 (cross-reference warning: direct match, bare-selector match, no-match, `:completed` suppression, malformed `one_shot` never raises), O4 (slash-free byte-identical regression, slash-bearing resolution via phases/profiles, unknown-path stays broken), O5 (a real constructed cycle via bare selectors that the OLD code would have missed, ambiguous-basename non-regression, full-path-selector regression bar), plus direct unit tests of the four new private helpers' defensive branches |
| `tests/tests/test_ciu_deploy_phases.py` | new `TestServiceOneShot` class (5 test methods, one parametrized to 7 cases); module docstring updated to describe both accessors it now covers |
| `docs/SPEC.md` | S13.2's ref-grammar table gains a `:completed` row; new prose blocks for the O2 deprecation warning, the `:completed` terminal, O4's slash-bearing resolution, and O3's cross-reference (all under S13.2); S13.3's static-lint bullet gains an O5 sub-paragraph; S7.7 (Health & readiness) gains a paragraph documenting `one_shot` as a phase-entry key |
| `docs/CONFIG.md` | `[[deploy.phases.phase_N.services]]` section gains `one_shot = true`'s docs + worked example; the `requires`/`provides` section's grammar table gains `:completed`, a note on slash-bearing selector resolution, and a worked one_shot + `:completed` example (`infra/db-init` migration referenced by `applications/worker`) |
| `CHANGES.md` | Unreleased -> one new Added entry, placed first (newest-first convention this file already uses) |
| `docs/BACKLOG-2026-08-24.md` | CIU-V8-PREP-5 row -> `Status: FIXED-partial`, naming the additive subset shipped, what's NOT shipped (deploy-loop wiring, removing the deprecated fallback), and the false-positive risk closed, stated plainly per O6 |

No file outside `scope.touch` was edited; `scope.forbid` fully respected —
`git diff --stat -- src/ciu/engine.py src/ciu/deploy.py
src/ciu/composefile.py nyxloom-trove/backlog.md nyxloom-trove/decisions.md
nyxloom-trove/roadmap.md` is empty.

---

## Escalate_if check (performed before writing any code)

The one named condition: *"the two provisioning-ref grammars
(`provisioning.py` and `config_model.py`) have already drifted apart before
this package touches them."* Read both files directly before any edit:
`provisioning.py`'s `_STACK_RE = re.compile(r'^stack:([a-zA-Z0-9_/-]+):healthy$')`
and `config_model.py`'s `_REF_RE`'s stack alternative
`r"|stack:[a-zA-Z0-9_/-]+:healthy)"` were byte-identical in shape (same
character class, same `:healthy` suffix, same non-slash-anchored structure
once wrapped by each file's own `fullmatch`). **Not triggered** — both
grammars were extended in lockstep (`healthy` -> `(?:healthy\|completed)` /
`(healthy\|completed)`), and both "Examples:" error strings were updated
together so they stay in sync going forward too.

---

## Design notes

### Why `subkind` stays `''` for `:healthy` instead of becoming `'healthy'`

`ProvisioningRef.subkind` was always `''` for a stack ref (unlike
`vault`/`pg`/`minio`/`consul`, which use it for their real sub-kind).
`test_parse_ref_stack_healthy` (pre-existing, NOT in this package's
`scope.touch` in the sense that changing its assertion would be a real
behavior change, not a mechanical rename) asserts `ref.subkind == ""`. O1's
own oracle text requires "`:healthy` refs parse and behave EXACTLY as
before" — additive, not merely "similar". Setting `subkind = 'healthy'` for
the existing terminal would flip that field's value for every already-valid
config, which is a parse-level behavior change this package must not make.
Instead: `subkind` stays `''` for `:healthy` (byte-identical, confirmed by
`test_parse_ref_stack_healthy_subkind_unchanged`) and becomes `'completed'`
only for the new terminal — `_probe_stack` dispatches on
`parsed.subkind == 'completed'`.

### O3 — why the cross-reference warning is NOT a new `ciu check` static stage

The handoff's own O3 oracle asks for "a ciu check-time cross-reference."
`ciu check`'s static-stage orchestration (`action_check`, `CHECK_STAGES`,
the stage-13 `[service.*]` lint precedent from ciu-P22) lives entirely in
`deploy.py`, which is `scope.forbid` for this package. Both existing callers
of `lint_graph` (in `deploy.py`, at the `provisioning_preflight` and
`action_check` call sites) treat EVERY string `lint_graph` returns as a hard
failure (`raise ValueError` / `report.fail`) — there is no WARN-vs-ERROR
channel in that return value's contract, and I cannot add one without
editing `deploy.py`. So the warning cannot live in `lint_graph`'s result
list without becoming an unwanted hard failure.

The workable, forbid-respecting alternative: `probe_ref`/`_probe_stack`
already runs, UNMODIFIED at both its `deploy.py` call sites
(`provisioning_preflight`'s live-probe loop and `action_check --live`'s live
probe), every time a `:healthy` ref is actually probed. Since `_probe_stack`
is invoked *because* some stack's `requires` references this selector via
`:healthy`, the mere fact of being called with `subkind == ''` already IS
"another stack's `requires` references it via `:healthy`" — no separate
graph reconstruction is needed. I read the target's own declared
`one_shot` value (via the new `_one_shot_stack_service` lookup against
`config.deploy.phases.*`) and print a `[WARN]` when it is `True`, mirroring
O2's exact print-based mechanism. Documented explicitly in SPEC.md S13.2:
this fires during LIVE PROBING (`ciu up`, `ciu check --live`), not as a
static stage — a real, disclosed scope narrowing versus a bare `ciu check`
(no `--live`) never triggering it, which is the direct consequence of
`deploy.py` being off-limits.

A malformed `one_shot` on the matched entry is caught (`except ValueError:
declared_one_shot = False`) so a probe can never raise from this
cross-reference lookup — `_probe_stack` never raises today (every branch
returns a `ProbeResult`), and I did not want to be the first exception to
that invariant. `service_one_shot` itself is NOT wired into
`iter_enabled_services` (unlike `service_health_enabled`): `service_shipped`
is the established precedent for "a boolean toggle validated lazily by its
own specific consumer, not eagerly during generic phase-service selection,"
and the handoff's own phrase "declaration + shape validation only" reads as
sanctioning exactly that scope, not a broader wiring change I was not asked
for.

### O4/O5 — one shared resolution helper, two different `known_paths` sources

Both oracles need the same operation — "does this selector correspond to a
known stack, and if so, which one" — but each is invoked with a different
statement of "known": O4's `_stack_container_name` needs the set of paths
`config` (the merged global config, no active-profile context) can express:
`deploy.phases.*.services[].path` and `deploy.profiles.*.stacks[]`,
aggregated across ALL phases/profiles (`_declared_stack_paths`). O5's
`lint_graph` already has an authoritative, narrower set for free:
`stacks.keys()` (the dict IS `{stack_path: {...}}`, per its own existing
docstring). `_resolve_declared_stack_path(selector, known_paths)` is
therefore a single function taking `known_paths` as a parameter — exact
match wins outright (covers a genuine full path, and a top-level bare stack
with no `/` in its own path); otherwise a selector matching exactly ONE
known path's final segment resolves to it; an ambiguous or unmatched
selector returns `None` so the caller leaves it exactly as unresolved as
today (never a manufactured edge or a reinterpreted container name).

Investigating O5's actual bug (not just re-stating the handoff's
description) mattered: the pre-existing test
`test_lint_graph_detects_simple_cycle` already passes today using FULL-PATH
selectors matching full-path keys directly (e.g. `stacks = {"infra/a":
{"requires": ["stack:infra/b:healthy"], ...}, "infra/b": {...}}`) — so the
handoff's "stack-to-stack dependencies via a full path NEVER enter cycle
detection" is not literally true for THAT shape. Reading `_probe_stack`
(O4's own framing: "every slash-bearing selector is provably broken today")
resolves the apparent contradiction: the ONLY selector form that has ever
worked for real container resolution is a BARE basename (e.g. `db-init`),
never a slash-bearing full path. `lint_graph`'s `stacks` dict, however, is
keyed by FULL repo-relative path (`entry["path"]` in every real
`deploy.py` caller). So a ref written the only way that ever actually
worked for probing (`stack:db-init:healthy` against a stack declared at
`infra/db-init`) is exactly the case that silently fails today (`"db-init"
not in color` -> skipped) — not the full-path-matching-full-path case the
pre-existing test happens to exercise. `test_lint_graph_detects_cycle_via_bare_selector_matching_full_path_keys`
constructs that real-world case and proves it is now caught;
`test_lint_graph_full_path_selector_still_works_unchanged` proves the
pre-existing (already-passing) shape is untouched;
`test_lint_graph_ambiguous_bare_selector_stays_unresolved_no_false_cycle`
proves the fix never manufactures a false cycle out of ambiguous data.

### O4 — the regression-bar test literally diffs the `container_name` call

Per `review_focus`, `test_probe_stack_slash_free_selector_container_name_byte_identical`
monkeypatches `procutil.docker` to CAPTURE the exact container-name argument
docker was invoked with for a slash-free selector, and asserts it equals
`container_name(config, selector)`'s pre-package output
(`"p-t-db-init"`) — not merely that the probe "still passes." A companion
test (`test_probe_stack_slash_bearing_selector_unknown_path_stays_broken`)
proves an UNKNOWN slash-bearing path is passed through utterly unchanged
(`"p-t-some/unknown"`, the literal guaranteed-broken string), so a genuine
typo still surfaces as "container not found" rather than being silently
reinterpreted into some other stack's container.

---

## Oracle-by-oracle evidence

| Oracle | Verdict | Evidence |
|---|---|---|
| **O1** `:completed` terminal | **MET** | Both grammars accept it (`test_parse_ref_stack_completed`, `test_parse_ref_stack_completed_with_slash`); `:healthy` unchanged (`test_parse_ref_stack_healthy_subkind_unchanged` plus every pre-existing `:healthy` test, all still green unmodified). `_probe_stack`'s `:completed` branch: exited-0 satisfied (`test_probe_stack_completed_exited_zero_is_satisfied`), non-zero exit not satisfied (`..._nonzero_exit_is_not_satisfied`), still-running not satisfied (`..._still_running_is_not_satisfied`), missing container not satisfied (`..._container_not_found`). The negative case the review_focus names explicitly — "never reads Health under any code path" — is pinned by `test_probe_stack_completed_never_reads_health_even_when_healthy`: the SAME fixture (`Running: True, Health.Status: "healthy"`) is NOT satisfied via `:completed` but IS satisfied via `:healthy` on the identical state, proving structurally that `:completed`'s branch never consults `Health` (it can't be fooled by a value it never reads). |
| **O2** deprecation warning | **MET** | `test_probe_stack_healthy_oneshot_fallback_warns_deprecated`: behavior unchanged (`satisfied is True`) AND a `[WARN]` naming both the ref and `:completed` appears (via `capsys`). `test_probe_stack_healthy_running_no_healthcheck_does_not_warn` proves the warning is scoped to the exact exit-0 branch, not the sibling no-healthcheck-but-running branch. |
| **O3** `one_shot` key + cross-reference | **MET** | `TestServiceOneShot` (5 methods, 7-way parametrized non-bool case) proves `service_one_shot` mirrors `service_shipped`'s exact pattern. Cross-reference: warns when the target declares `one_shot = true` (`test_probe_stack_healthy_warns_when_target_declares_one_shot`), warns via a bare selector resolving to the same declared path (`..._warns_via_bare_selector_matching_one_shot_stack`), does NOT warn when not declared (`..._no_warning_when_target_not_one_shot`), does NOT warn for `:completed` even against a one_shot target (`test_probe_stack_completed_never_emits_one_shot_cross_reference_warning`), and never raises on a malformed `one_shot` value (`test_probe_stack_healthy_one_shot_malformed_does_not_raise`). The deploy-loop wiring is explicitly NOT shipped (see Design notes) — pinned by absence: no edit touches `deploy.py`, confirmed by the forbid-diff above. |
| **O4** path selector | **MET** | Slash-free regression bar: `test_probe_stack_slash_free_selector_container_name_byte_identical` diffs the exact `container_name` call. Slash-bearing resolution: via `deploy.phases.*` (`test_probe_stack_slash_bearing_selector_resolves_known_declared_path`) and via `deploy.profiles.*.stacks` (`..._via_profile_stacks_list`) — both prove the previously-guaranteed-broken container name is now correct. Unknown path stays broken exactly as before (`..._unknown_path_stays_broken`), satisfying the negative ("leaving the slash-bearing path silently broken while only adding `:completed`" does NOT apply — known paths now work; unknown paths behave identically to pre-package). |
| **O5** `lint_graph` fix | **MET** | `test_lint_graph_detects_cycle_via_bare_selector_matching_full_path_keys` constructs a REAL 2-node cycle using the ref form that actually resolves to containers today (bare names against full-path keys) and asserts it is NOW caught — the review_focus's named requirement, satisfied by construction, not by "doesn't crash." `..._bare_selector_via_completed_also_resolves` proves the fix covers `:completed` too. `..._ambiguous_bare_selector_stays_unresolved_no_false_cycle` and `..._full_path_selector_still_works_unchanged` are the two non-regression companions. |
| **O6** docs | **MET** | SPEC.md S13.2 (ref table + 4 new prose blocks), S13.3 (O5 sub-paragraph), S7.7 (`one_shot` phase-entry key) — sections named above and in the Files-changed table. CONFIG.md gets the worked `one_shot` + `:completed` example named in the oracle text (an `infra/db-init` migration container referenced via `:completed` by `applications/worker`). CHANGES.md's new entry is careful NOT to claim `one_shot` changes deploy-time polling (negative clause), stating explicitly "declaration + shape validation only ... does NOT wire into the deploy loop's own post-up wait behavior" in both CHANGES.md and the backlog row. `docs/BACKLOG-2026-08-24.md`'s V8-PREP-5 row states the additive subset shipped, what's NOT shipped, and the false-positive risk closed, in prose (not just a status flip). |

---

## Escalations

**None triggered.** The one named `escalate_if` condition (the two
provisioning-ref grammars having already drifted apart) was checked by
reading both files directly before any edit — they were byte-identical in
shape, so no BLOCKED report was needed. Both were then extended in lockstep.

---

## Gate output (verbatim, read in a separate step from the run itself)

```
$ .venv/bin/python run-ciu-tests.py
...
src/ciu/config_model.py                            336      0    166      0   100%
src/ciu/deploy.py                                 1592      0    696      0   100%
src/ciu/deploy_pkg/phases.py                        81      0     46      0   100%
src/ciu/provisioning.py                            438      0    214      0   100%
...
--------------------------------------------------------------------------------------------
TOTAL                                             8894      0   3594      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
============================ 2938 passed in 18.19s =============================
```

2938 tests passed (up from the 2888-test baseline confirmed clean before any
edit — +50 from this package: 44 new `def test_` functions across
`test_ciu_provisioning.py` (39) and `test_ciu_deploy_phases.py` (5), two of
which are parametrized to 7 cases each, accounting for the +6 beyond a
1:1 function-to-test-item count), 100% line **and** branch coverage
(`--cov-branch`, `--cov-fail-under=100`), exit code `0`.

**Forbidden-file check** (re-run after all edits, not just before):

```
$ git diff --stat -- src/ciu/engine.py src/ciu/deploy.py src/ciu/composefile.py \
    nyxloom-trove/backlog.md nyxloom-trove/decisions.md nyxloom-trove/roadmap.md
(empty)
```

**Full-suite regression check** (targeted re-run of the two other test files
this package's source changes could plausibly affect, including
`test_spec_contracts.py`, which is NOT in this package's `scope.touch` and
pins the pre-existing one-shot-exit-0 and stack-path provisioning
behaviors):

```
$ .venv/bin/python -m pytest tests/tests/test_ciu_provisioning.py \
    tests/tests/test_ciu_deploy_phases.py tests/tests/test_spec_contracts.py -q
259 passed in 2.74s
```
