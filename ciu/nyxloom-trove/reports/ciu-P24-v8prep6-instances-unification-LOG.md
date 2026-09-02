# ciu-P24 — V8-PREP-6: unified `instances = N` fan-out

**Handoff:** `nyxloom-trove/handoffs/ciu-P24-v8prep6-instances-unification.md`
**Branch:** `feat/ciu-qol-v8prep-wave` · **Base HEAD:** `9b731c78` (ciu-P23,
confirmed with `git status --porcelain && git log --oneline -3` before any
edit — tree was clean).

**Status: COMPLETE (ACCEPT-conditional review fix applied, §9).** Full gate
green (2968 passed, 100.00% line+branch coverage). One controller-authorized
scope widening along the way (§3) — same pattern as this wave's
ciu-P15/P17/P32 episodes.

---

## 1. Reading, before any code

Read the handoff in full, then in order: `docs/BACKLOG-2026-08-24.md`'s
V8-PREP-6 row, `docs/CIU-V8-TESTING-GATE-PROPOSAL.md` §1.19,
`src/ciu/composefile.py` in full (the per-configfile `instances` validation,
`instance_index`/`instance_id` injection, `_configfile_mount_services`'s
exact-key/base-selector/WARN logic, `render_compose`'s S3.12 `ciu_context`
merge), and `src/ciu/config_model.py`'s parallel `_make_render_context`
channel (read-only — forbidden to touch) to understand the CONFIG-rendering
side of the same fact-injection pattern. Also traced every real caller of
`render_configfiles`/`render_compose`/`generate_overlay` in `engine.py` and
`deploy.py` (both forbidden, both read-only) to establish exactly which
object references are shared across which call sites — this mattered a lot
(§4).

## 2. The architectural puzzle the `escalate_if` anticipated, and why it did
   not fire

The handoff's one `escalate_if` worried that the `selected_profiles`/
`deployed_stacks` injection channel "cannot accept a third key without a
change to a FORBIDDEN file." Investigating concretely: `render_compose`
takes `ciu_context: dict | None` but no `root_key` — it cannot compute
`ciu.instances` on its own (it doesn't know which top-level key of
`guarded_config` is the stack root). Its only caller with the real answer,
`engine.py`, is forbidden. The way through, verified by reading `engine.py`
line-by-line: `ciu_context` is a single function PARAMETER, never
reassigned, used identically (same object reference, not a copy) at BOTH
the `render_configfiles` call (Step 12) and the LATER `render_compose` call
(Step 13) for the same stack render. So `render_configfiles` — which
already receives `root_key` and the full config — can compute the resolved
instances map and MUTATE the shared `ciu_context` dict in place; by the
time `render_compose` runs moments later on the identical object, its
UNCHANGED existing merge logic (`merged_ciu.update(ciu_context)`) carries
`instances` forward with zero code change to that merge itself. No parallel
injection mechanism was invented; the existing S3.12 channel was reused
exactly as instructed. Verified this is genuinely the same object (not a
`.copy()`) by reading `deploy.py`'s `action_deploy`, too: `ciu_ctx =
profiles_pkg.render_ciu_context(profile, selection)` is built ONCE per `ciu
up` invocation and threaded, unchanged, through EVERY stack/phase in that
run — which surfaced a second hazard (§4) the handoff didn't anticipate.

## 3. Scope widening — a real blast-radius find + a real upgrade hazard

Running the full gate after implementing O1-O4 the first time (a version
where a declared service-level `instances` silently became the DEFAULT for
any configfile omitting its own key) failed exactly one test, outside
`scope.touch`: `tests/tests/test_ciu_test_repo.py::
test_workers_stack_configfile_fans_out_and_dev_profile`. Root cause: the
`applications/workers` test-repo demo ALREADY declares `[workers.worker]
instances = 2` — pre-existing, read only by its own hand-rolled compose
loop and directly by `config.toml.j2` — and its ONE configfile section
omits its own `instances` key, relying entirely on the pre-existing S5.3
base-selector fan-out (a single shared render, mounted into both replicas
at overlay time). Making the service-level key a meaningful O1 default made
`render_configfiles` silently split that ONE render into TWO, changing
`len(mounts)` and every downstream mount name — exactly the shape the
proposal itself names as the thing V8 replaces, but ALSO exactly the shape
any real external consumer using `instances` purely to drive its own
compose loop would already have.

STOPPED per my own operating instructions (an out-of-scope test pinning
behavior my change legitimately alters) rather than editing
`tests/tests/test_ciu_test_repo.py` or the test-repo fixture unilaterally.
Reported the exact failure, root cause, and three options to the
controller. The controller:

1. **Authorized widening `scope.touch`** by four files (commit `2c842ba0`,
   amending the handoff) — `test-repo/applications/workers/
   {ciu.defaults.toml.j2,ciu.compose.yml.j2,config.toml.j2}` and
   `tests/tests/test_ciu_test_repo.py` — to migrate the demo to the new
   unified pattern and update its pinning test.
2. **Identified a further, more general hazard** from my own report's own
   wording ("any existing consumer stack that already uses a service-level
   `instances` key purely to drive its own compose loop... will silently
   start double-rendering configfiles") as the exact class of
   silent-behavior-change-on-upgrade this whole wave refuses rather than
   guesses at (repo-root precedence ciu-P32, identity-tuple precedence
   ciu-P28/29) — and directed a NEW refusal (S7.5e) closing it for
   everyone, not just this one demo.

This changed O1's actual shipped semantics from the handoff's original
wording ("it is the DEFAULT for every configfile... that OMITS its own
`instances` key") to a strictly more conservative rule: **the service-level
value never silently applies to an omitting configfile; it must be
restated explicitly, or refuses.** This is a deliberate, controller-directed
refinement of O1's original oracle text, not a unilateral reinterpretation —
recorded here, in SPEC.md S7.5e, and in CHANGES.md's migration note, exactly
as instructed, rather than folded silently into O1/O2's description.

## 4. Design notes

### Why `_resolve_service_instances` takes `root: Mapping`, not `(root_key, config)`

Its first draft re-derived `root = config.get(root_key)` and re-checked
`isinstance(root, Mapping)` — but `render_configfiles` ALWAYS calls it after
already confirming that itself, making that branch structurally
unreachable and therefore uncoverable at 100% branch coverage without an
artificial direct-unit test on a private helper (this test file's existing
convention tests everything through the public API). Simplified the
signature to take the already-validated `root` directly, removing the dead
branch rather than adding a test to explain it away.

### The migration-safety refusal's exact trigger, and why it is narrower than "always refuse"

S7.5e refuses ONLY when (a) a configfile omits its own `instances` key AND
(b) the SERVICE declares an EXPLICIT `instances` value **> 1**. It does
**not** fire for: a service declaring `instances = 1` (below the fan-out
threshold — indistinguishable from no default at all, so refusing would be
pure friction with no hazard behind it); a configfile that has no
service-level sibling at all (the pre-existing, untouched, single-configfile
per-service-max tie-break for `ciu.instances`, which carries no dedicated
oracle and predates this refusal); or a configfile that explicitly restates
`instances` (whether it agrees — success — or disagrees — the pre-existing
O1 disagreement refusal, a different tagged error). Verified this precise
scoping is not accidental by writing `TestMigrationSafetyRefusal` with all
four adjacent cases (collision, explicit-restatement success, no-service-
default success, service-default-of-1 success) rather than only the
positive collision case.

### The `ciu_context["instances"]` pop-vs-set asymmetry (cross-stack leak)

`deploy.py`'s `action_deploy` builds `ciu_ctx` ONCE and threads the literal
same object through `_run_stack` for EVERY stack across EVERY phase of one
`ciu up` invocation (§2). A first draft only ever SET
`ciu_context["instances"]` when non-empty, which meant stack A's fan-out map
would silently survive, stale, into stack B's `render_compose` call if
stack B declared no instances of its own — a real cross-stack contamination
bug, not merely a theoretical one, since the object is provably shared.
Fixed by explicitly `pop`-ping the key when this stack's own resolution is
empty, so every call leaves the shared object in the CORRECT state for
itself regardless of what an earlier stack's call left behind.
`test_cross_stack_reuse_does_not_leak_stale_instances` reproduces this with
two real stacks sharing one `ciu_context` dict, in the same order engine.py
actually calls them.

### O3's check lives inside `render_compose`, not `generate_overlay`

`generate_overlay` is where the EXISTING, opposite-direction S5.3 WARN
lives, and was the obvious first place to look for O3 too. But its real
engine.py call site never receives `ciu_context` at all, and its only
`configfile_mounts` parameter doesn't carry per-service resolved-instances
data for services with NO configfile (the pure compose-only fan-out case
O2 explicitly requires supporting). `render_compose`, by contrast, already
receives the merged `ciu_context` (with `instances` already populated by
render_configfiles's mutation, §2) and already has the freshly-rendered
compose text in hand at the exact moment it would return — so the check
runs there, immediately after a successful render, using the same
`_compose_service_blocks` helper `_configfile_mount_services` already uses
to parse the rendered YAML. `_configfile_mount_services` itself is
untouched — confirmed via `git diff` showing zero lines changed in it.

## 5. Files changed

| File | What |
|---|---|
| `src/ciu/composefile.py` | `_validate_positive_instances` (shared rule, used by both the pre-existing per-configfile check and the new service-level one); `_resolve_service_instances` (O1 resolution + disagreement refusal); `render_configfiles` calls it up front, applies S7.5e's refusal for an omitting configfile under an explicit service default > 1, and mutates the caller's `ciu_context` (set-or-pop) with the O2 fan-out map; `render_compose` runs the new `_check_instance_service_keys` (O3) post-render when `ciu.instances` is non-empty; extensive docstring updates on both public functions describing the new behavior and the reasoning behind the mutation-based channel |
| `tests/tests/test_ciu_composefile.py` | +26 test functions: `TestServiceLevelInstancesDefault` (O1 agreement/disagreement/validation/no-configfile cases), `TestMigrationSafetyRefusal` (S7.5e — the exact `applications/workers` collision, both remedies, the N=1 non-trigger), `TestCiuInstancesContextInjection` (O2 — presence/absence, multi-service filtering, cross-stack non-leakage), `TestInstanceServiceKeyRefusal` (O3 — bare-key refusal, missing-index refusal, correct pass, scoping to declared services only), `TestEndToEndInstancesWiring` (the real two-call engine.py sequence, including the literal O5 worked-example loop) |
| `test-repo/applications/workers/ciu.defaults.toml.j2` | Migrated to the new unified pattern (controller-authorized, §3): `[workers.worker.configfile.main]` now explicitly restates `instances = 2` (the required S7.5e opt-in); header comment rewritten with an explicit migration note explaining why |
| `test-repo/applications/workers/ciu.compose.yml.j2` | Loop bound changed from `workers.worker.instances` to `ciu.instances.worker` (O2) — a small, natural change, not a rewrite |
| `test-repo/applications/workers/config.toml.j2` | Header comment updated (rendered per-instance now, not once-shared); added `instance_id = "{{ instance_id }}"` as a live demonstration that this context fact is now genuinely per-instance |
| `tests/tests/test_ciu_test_repo.py` | `test_workers_stack_configfile_fans_out_and_dev_profile` updated: expects 2 mounts (`worker-1`/`worker-2`, `main-1`/`main-2`), asserts each has its OWN bind-mount source (no longer shared, the opposite of the pre-migration assertion), dev-profile assertions unchanged |
| `docs/SPEC.md` | New `S7.5d` (service-level declaration, agreement anchor, `ciu.instances`, O3's duplicate-mount refusal) and `S7.5e` (the migration-safety refusal, its own named subsection per the controller's instruction) under `## S7` |
| `docs/CONFIG.md` | `[<root>.<service>.configfile.<name>]` section gains the `instances` key row; new `#### [<root>.<service>] instances = N` subsection with a worked example and an explicit "this is NOT a silent default" callout box |
| `docs/CONSUMERS.md` | New `## 16. Fan a service out by instances instead of hand-rolling a compose loop (V8-PREP-6)` — worked example using the sanctioned `{% for i in range(1, ciu.instances.api + 1) %}` / `api-{{ i }}` loop shape, plus the same S7.5e warning box |
| `CHANGES.md` | New `feat(ciu)!:` Unreleased entry (breaking-change marker, matching ciu-P32's precedent, since this changes previously-succeeding renders into refusals for one real config shape) with a dedicated "Upgrade note" paragraph per the controller's instruction — not folded into the O1/O2 prose |
| `docs/BACKLOG-2026-08-24.md` | V8-PREP-6 row: `Status: FIXED-partial`, states plainly CIU does not auto-generate compose service blocks, names the migration-safety refusal and what remains deferred |
| `nyxloom-trove/handoffs/ciu-P24-v8prep6-instances-unification.md` | `scope.touch` widened by 4 files (commit `2c842ba0`, separate from the implementation commit) |

No `scope.forbid` file was touched — confirmed both before writing any code
and again just before this commit:

```
$ git diff --stat -- src/ciu/engine.py src/ciu/deploy.py src/ciu/provisioning.py \
    src/ciu/config_model.py nyxloom-trove/backlog.md nyxloom-trove/decisions.md \
    nyxloom-trove/roadmap.md
(empty)
```

## 6. Oracle-by-oracle evidence

| Oracle | Verdict | Evidence |
|---|---|---|
| **O1** service-level default + disagreement refusal | **MET (refined, §3)** | Agreement: `test_service_level_default_explicitly_restated_fans_out`. Disagreement: `test_service_level_and_configfile_level_disagree_refuses` (names service, configfile, and both values — asserted literally). Invalid values (`0`, `-1`, `"3"`, `True`) at service level: parametrized `test_service_level_invalid_instances_raises`. No-configfile-at-all service declaration: `test_service_level_instances_with_no_configfile_at_all` + its invalid-value companion. Sibling-configfile non-contamination (pre-existing behavior untouched): `test_configfile_own_value_never_defaults_a_sibling_configfile`. The refined "no silent default application" half: `TestMigrationSafetyRefusal`'s four tests (§4). |
| **O2** `ciu.instances` compose context | **MET** | Absence when nothing declares instances: `test_no_instances_anywhere_zero_behavior_change`, `test_instances_of_exactly_one_absent_from_ciu_instances`. Presence filtered to >1, multi-service: `test_multiple_services_only_multi_instance_ones_present`. Cross-stack non-leakage (the shared-object hazard, §4): `test_cross_stack_reuse_does_not_leak_stale_instances`. End-to-end through the real two-call sequence, including the literal worked-example loop: `TestEndToEndInstancesWiring::test_service_level_default_flows_through_to_compose_success`. |
| **O3** duplicate-mount post-render refusal | **MET** | Bare-key refusal naming the service and declared count: `test_bare_key_with_declared_instances_refuses`. Missing-instance-key refusal: `test_missing_instance_key_refuses`. Correct enumeration passes: `test_correctly_enumerated_instances_passes`. Scoping — an unrelated numeric-looking service is untouched: `test_unrelated_numeric_looking_service_not_touched`. `ciu_context=None` / no `instances` key: both skip-the-check paths tested. The pre-existing opposite-direction WARN (`_configfile_mount_services`) is verified untouched: zero lines of that function appear in `git diff`. |
| **O4** tests | **MET** | 26 new test functions across 5 classes (§5), all named per the oracle's own enumerated cases; the "constructs the duplicate-mount failure end-to-end" requirement is met by `TestEndToEndInstancesWiring` chaining the real `render_configfiles` → `render_compose` sequence with one shared `ciu_context` object, exactly as `engine.py` does it. |
| **O5** docs | **MET** | SPEC.md S7.5d/S7.5e (new, own subsections, not folded into an existing one). CONFIG.md's configfile section + new `instances` subsection. CONSUMERS.md §16's worked example uses the exact sanctioned loop convention (matches S7.5b's `<service>-<index>` naming — no invented convention). States plainly, in both CHANGES.md and the backlog row, that CIU does not auto-generate compose service blocks. |

## 7. Gate output (verbatim, read in a separate step from the run itself)

```
$ .venv/bin/python run-ciu-tests.py
...
src/ciu/composefile.py                             444      0    214      0   100%
src/ciu/config_model.py                            336      0    166      0   100%
src/ciu/deploy.py                                 1592      0    696      0   100%
src/ciu/engine.py                                  887      0    292      0   100%
...
--------------------------------------------------------------------------------------------
TOTAL                                             8950      0   3628      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
====================== 2967 passed, 14 warnings in 22.50s ======================
```

2967 tests passed — verified against the true pre-package baseline by
`git stash -u` (stashing every uncommitted change back to the clean
`9b731c78`/`2c842ba0` tree) and re-collecting: `2938 tests collected`,
matching ciu-P23's own LOG-reported final count exactly, then `git stash
pop` restored this package's work (re-confirmed green immediately after).
+29 net new test items = the +26 new `def test_` functions in
`test_ciu_composefile.py` (82 -> 108, confirmed by `grep -c` before/after)
plus +3 from `test_service_level_invalid_instances_raises`'s 4-way
`@pytest.mark.parametrize` (1 function, 4 collected items). 100% line
**and** branch coverage (`--cov-branch`, `--cov-fail-under=100`), exit
code `0`.

**Targeted regression check** (every file this package's changes could
plausibly affect, including `test_spec_contracts.py` and
`test_ciu_documentation_contract.py` — the latter caught a wrong
cross-document anchor in my first draft of CONSUMERS.md's new worked
example, fixed to point at S7's real heading anchor rather than a
non-existent per-bullet one):

```
$ .venv/bin/python -m pytest tests/tests/test_ciu_composefile.py \
    tests/tests/test_ciu_test_repo.py tests/tests/test_ciu_render_selection_context.py \
    tests/tests/test_ciu_documentation_contract.py tests/tests/test_spec_contracts.py -q
223 passed in 6.28s
```

## 8. Commits

1. `2c842ba0` — `docs(ciu): amend ciu-P24 -- widen scope for a real
   silent-upgrade hazard` (the scope.touch widening, committed separately
   per the controller's instruction, before any of the newly-authorized
   files were touched).
2. `e26d08f7` — the full implementation/test/docs/fixture-migration diff
   from §5 (this package's main commit).
3. `1ddfdfba` — this LOG file, first version.
4. §9's review-fix commit and its LOG update, both listed at the end of §9.

## 9. Review fix — the fresh-assignment half of the leak-guard wasn't pinned

**Verdict on the shipped code: ACCEPT-conditional.** The reviewer confirmed
`ciu_context["instances"] = instances_for_ciu` (a fresh, replacing
assignment) is the CORRECT behavior — no code defect — but found the test
suite only pinned HALF of it. They mutated the assignment to
`ciu_context.setdefault("instances", {}).update(instances_for_ciu)` (an
accumulate-instead-of-replace refactor) and got all 127 tests in this file
passing, then proved the mutant is a REAL leak by hand: with that mutation,
stack A's `{'worker': 3}` survives inside stack B's OWN non-empty map,
producing `{'worker': 3, 'other': 2}` in stack B's compose render instead
of stack B's own `{'other': 2}`. My existing
`test_cross_stack_reuse_does_not_leak_stale_instances` only exercises the
POP-when-empty half (stack B declares NOTHING); it never constructs the
case where BOTH stacks declare their own, DIFFERENT, non-empty maps — so it
cannot distinguish "replace" from "merge" and passed unchanged under the
mutant.

**Fix:** one new test,
`TestCiuInstancesContextInjection::test_cross_stack_reuse_replaces_not_merges_when_both_declare`
(`tests/tests/test_ciu_composefile.py`) — stack A declares
`[mystack.worker] instances = 3`, stack B (a SEPARATE stack directory,
same shared `ciu_context` object, same call pattern as the existing
cross-stack test) declares its own, unrelated `[mystack.other] instances =
2`; asserts `shared_ciu_context["instances"] == {"other": 2}` after stack
B's render — i.e. ONLY stack B's own service key, `"worker"` from stack A
gone entirely. Verified by hand this test actually catches the reviewer's
exact mutant (applied it to a scratch copy of `composefile.py`, re-ran just
this test, confirmed it fails with
`AssertionError: assert {'worker': 3, 'other': 2} == {'other': 2}` — the
literal leak the reviewer described — then restored the real source and
re-confirmed byte-identical via `diff`).

Also added the S3.12-lineage comment the coordinator asked for, right above
the fresh-assignment/pop code in `render_configfiles`: the shared-object
leak class this guards against is the SAME one
`selected_profiles`/`deployed_stacks` (S3.12) was built to close, now
recurring for `instances` because it rides the identical shared
`ciu_context` object — and spelled out explicitly that a `setdefault(...
).update(...)`-style accumulate is the wrong fix for either half (empty OR
non-empty), not just something to avoid in the empty case. No functional
code change — `ciu_context["instances"] = instances_for_ciu` was already
correct; only the comment and the new test changed in
`src/ciu/composefile.py`'s neighborhood.

### Gate output (verbatim, post-fix)

```
$ .venv/bin/python run-ciu-tests.py
...
--------------------------------------------------------------------------------------------
TOTAL                                             8950      0   3628      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
====================== 2968 passed, 6 warnings in 23.07s =======================
```

2968 passed (up from 2967 by exactly the one new test added), 100.00% line
+branch coverage, exit code `0`. Isolated re-run of just the affected file
first (`tests/tests/test_ciu_composefile.py -q -k cross_stack`): 2 passed,
confirming both the pre-existing pop-guard test and the new replace-guard
test pass together.

### Commits (this fix)

1. `git commit --only -F - -- src/ciu/composefile.py
   tests/tests/test_ciu_composefile.py` — the comment update + new test,
   on top of `1ddfdfba`.
2. This LOG update, committed separately (`docs(ciu):` prefix).

Exact hashes are in this package's final report (read back via `git log
--format=%H`, not predicted ahead of the actual commit).
