# ciu-P41 — REPORT

Four bundled backlog items, four separable commit groups, on branch
`fix/ciu-P41-checkpoint1-remainder` (worktree
`.worktrees/ciu-P41-checkpoint1-remainder`), based on vbpub main `858766d1`.

| item | entries | outcome | commit |
|---|---|---|---|
| 1 | CIU-67 + CIU-68 | **DONE** | `ebe45c39` |
| 2 | CIU-64 + CIU-65 | **DONE** | `e2e18f92` |
| 3 | CIU-66 | **BLOCKED — needs controller review before merge** | `2ee94f32` (record only, no code) |
| 4 | CIU-62 | **DONE** | `39d092c3` |

---

## READ THIS FIRST — the gate is RED on arrival, for three reasons that are not mine

`./run-gate.py ciu` at the package's own base commit `858766d1`, with a clean
tree (assay snapshots COMMITTED objects only, so this is main as-is):

```
ciu: FAIL/COMMAND_FAILED (exit 1)
  commit: 858766d1059b3557ce9f962e72cfafb95e43d123
  argv: /opt/tester-venv/bin/python run-ciu-tests.py

Required test coverage of 100% reached. Total coverage: 100.00%
=========================== short test summary info ============================
FAILED tests/tests/test_ciu_deploy_actions.py::test_check_suppresses_bytecode_writes_while_importing_hooks
FAILED tests/tests/test_ciu_deploy_actions.py::test_check_restores_the_bytecode_flag_after_a_failed_import
FAILED tests/tests/test_ciu_worktree_reap.py::TestLeaseLifecycleChangesTheNextSurvey::test_re_expiring_after_an_extend_becomes_lease_expired_again
================== 3 failed, 3258 passed in 171.65s (0:02:51) ==================
```

R1 (coverage) PASSES at baseline and after every commit. R0 fails on three
pre-existing tests:

1. + 2. **The two bytecode tests are coupled to the gate's own declared
   environment.** `assay.toml [lanes.ciu]` declares
   `env = { PYTHONPATH = "src", PYTHONDONTWRITEBYTECODE = "1" }`, so
   `sys.dont_write_bytecode` is already `True` when the process starts. Both
   tests assert `sys.dont_write_bytecode is False` after `action_check`
   restores it — the product code restores it CORRECTLY, to `True`, and the
   assertion's hardcoded `False` is what is wrong. They pass in the
   devcontainer venv (no such env var) and fail only under the real gate —
   exactly the "pytest green ≠ gate green" gap. **Not filed in the backlog
   that I could find; recommend filing.** The fix is two lines (assert
   restoration to the value observed before the call), but it is in neither
   this package's four items nor its scope, and five other implementers are
   mid-flight against this same file.
3. **The lease test is already filed as CIU-76** (`apply_lease` has no `now:`
   override, so its frozen-clock fixture rots as real time advances). Fixing
   it is a product signature change with its own package.

I deliberately did NOT fix any of the three. Fixing (3) is out of scope and
already owned; fixing (1)+(2) alone would still leave the gate red, so it
would buy no green verdict while risking a conflict with concurrent
packages. **Every per-item verdict below is therefore read as: the same three
failures, no new ones, coverage still 100%** — never as a bare exit code.

Net across the package, measured at the gate: **3258 → 3319 passing**
(+61 tests; 3261 → 3322 collected), coverage 100.00% line AND branch
throughout. In the devcontainer venv the same tree is 3321 passed / 1 failed
— the two bytecode tests pass there because `PYTHONDONTWRITEBYTECODE` is not
set, which is precisely the gap that hides them from a local run.

---

## Item 4 — CIU-62 (commit `39d092c3`)

`parse_workspace_env` fails three unrelated ways. `OSError` is the read;
`UnicodeDecodeError` is a non-UTF-8 byte; `WorkspaceEnvError` is a malformed
entry. The last two are SIBLING `ValueError` subclasses, so naming either one
catches neither the other nor `OSError`.

I re-derived every site by grepping the clause shapes rather than trusting
the filed line numbers — all of them had moved. Final map:

| site (current) | was | now |
|---|---|---|
| `worktree.py` `_preflight_shared_infra_for_add` (S16.1) | `except OSError` | `(OSError, UnicodeDecodeError, WorkspaceEnvError)` |
| `worktree.py` `_clean_in` (S16) | `except OSError` | same triple |
| `worktree.py` `connect_shared_infra_after_up` (S16.1) | `except OSError` | same triple |
| `worktree.py` `_resolve_budget_candidates` (S16.3) | `(OSError, WorkspaceEnvError)` | same triple |
| `deploy.py` `_workspace_identity` | `(WorkspaceEnvError, OSError)` | same triple |
| `deploy.py` `_workspace_identity_network` | `except WorkspaceEnvError` → `""` | **semantics change, below** |
| `worktree.py` `_sanitized_target_env`, `_runtime_identity` | `(OSError, ValueError)` | **left alone** |

Ordering matches the shipped precedent at `cli.py:805`
(`(OSError, UnicodeDecodeError, WorkspaceEnvError)`, added by ciu-P33 when
this class was first noticed).

The two `(OSError, ValueError)` sites are left alone as the entry advises.
Narrowing them to the explicit triple would be a behavior change (a stray
`ValueError` from elsewhere would newly escape) with no defect behind it.

### The `deploy.py:2842` decision (the entry's own flagged design call)

**Decision: an ABSENT `ciu.env` still returns `""`; a PRESENT but unreadable
one now RAISES, and `action_clean` reports it and fails the clean.**

Widening this site to catch three types instead of one would have been the
easy move, and it would have been wrong. Two conditions were collapsing into
the same `""`:

- *this checkout has no generated record* — legitimate; `ciu env generate`
  was never run here, and there genuinely is no workspace identity network;
- *the record exists but CIU could not parse it* — INDETERMINATE.

Folding the second into the first is AGENTS.md's absence-for-emptiness
anti-pattern, and here it had teeth. `action_clean` uses `identity_network`
twice: to decide what to REMOVE, and (via `target_networks`) to decide what
the clause-5 post-clean survivor check even looks at. A network that was
never resolved is dropped from both in one move — so an instance clean could
print the S6.4a zero-objects invariant as satisfied over its own surviving
network. That is precisely the failure S6.4a was written to end ("v1's
'network removal is NOT performed' posture is withdrawn — it leaked one
identity-scoped network per teardown while printing `clean complete`").

The surrounding code already had the right answer and this site was the
outlier: the volume pass, the per-project network enumeration and the
post-clean container enumeration ALL treat indeterminacy as an error that
fails the clean, with the in-code note "never fold 'could not enumerate' into
'nothing to remove'" (review B3). `_workspace_identity_network` now raises
`ValueError` (the same type those siblings raise), and `action_clean` prints
`workspace identity network unresolvable (S6.4a): …`, sets `rc = 1`, and
continues so the rest of the clean still runs — the same shape as its
siblings, not a new one.

The legitimate state is constructed as its own test
(`test_identity_env_absent_still_reads_as_no_network`), because a refusal
whose condition also matches an ordinary healthy state is a superset refusal
and gets switched off.

**One existing test asserted the OLD contract verbatim** —
`test_identity_env_parse_failure_reads_as_no_network`, docstring "An
UNPARSEABLE ciu.env names no removable network — and says nothing." It is
replaced by three tests encoding the new contract, with the reversal and its
reason stated in the docstring so the next reader does not treat it as drift.

Documented in SPEC S6.4a clause 1 and CHANGES.md (Fixed + Adoption notes:
"Action needed if your checkout has a corrupt `ciu.env`").

### Also noted, not changed

`engine.py:1338` (`except WorkspaceEnvError`) and `engine.py:1489`
(`(WorkspaceEnvError, OSError)`) are the same class of clause around
`ciu.env`-reading code, and CIU-62 does not list them (it enumerates six
sites in `worktree.py`/`deploy.py`). I left them alone rather than silently
widening the entry's scope. They are worth a line on CIU-62 or on CIU-75,
which already counts `engine.py` 948/1182/1484 among its 12 `ciu.env` call
sites.

### Gate — item 4, commit `39d092c3`

```
ciu: FAIL/COMMAND_FAILED (exit 1)
  commit: 39d092c3a297256296f75ee876c6a3ae49fcfc64
  argv: /opt/tester-venv/bin/python run-ciu-tests.py

TOTAL                                             9682      0   3948      0   100%
Required test coverage of 100% reached. Total coverage: 100.00%
=========================== short test summary info ============================
FAILED tests/tests/test_ciu_deploy_actions.py::test_check_suppresses_bytecode_writes_while_importing_hooks
FAILED tests/tests/test_ciu_deploy_actions.py::test_check_restores_the_bytecode_flag_after_a_failed_import
FAILED tests/tests/test_ciu_worktree_reap.py::TestLeaseLifecycleChangesTheNextSurvey::test_re_expiring_after_an_extend_becomes_lease_expired_again
======================= 3 failed, 3269 passed in 55.41s ========================
```

R0 FAIL (the three above), R1 PASS at 100.0. Same three as baseline, +11
passing.

---

## Item 2 — CIU-64 + CIU-65 (commit `e2e18f92`)

### CIU-65 — the severity-string strictness decision ("your call", decided)

**Decision: `str(value).strip().upper()`, matched against exactly
`{"WARN", "ERROR"}`; anything else is REFUSED as its own ERROR finding
naming the accepted vocabulary, never defaulted.**

Three sub-decisions, each with a reason:

1. **Case- and whitespace-insensitive.** Not a coin flip — it is the
   normalization `warn_policy._validate_exit_on` already applies to this
   exact vocabulary (`str(value).strip().upper()`). Two spellings of one
   vocabulary in one codebase would be the drift this estate keeps paying
   for.
2. **An unrecognized severity raises rather than defaulting — in EITHER
   direction.** Defaulting to ERROR would merely be noisy. Defaulting to
   WARN, or accepting anything truthy as WARN, would let a hook author's
   typo (`"warning"`, `"Error!"`) silently downgrade a blocking finding to an
   advisory note — a masked default, invisible in every run that does not
   happen to hit it, and the run that hits it looks identical to a healthy
   one. So it fails loudly AND tells the author what the two accepted values
   are. `test_check_stage9_unknown_severity_is_refused_not_guessed` pins
   this.
3. **The finding vocabulary is the SUBSET `{WARN, ERROR}`, not
   `warn_policy`'s `{WARN, ERROR, NEVER}`.** `NEVER` is a *threshold*
   ("abort at nothing"), not a property a finding can have. A hook declaring
   `NEVER` is refused like any other unknown value, with its own test.

Two shapes are accepted per finding: a bare message string (ERROR —
unchanged, so no existing hook changes weight) and a 2-element `tuple` **or
`list`**. Lists are accepted deliberately: a hook assembling findings from
JSON or a comprehension naturally produces lists, and refusing them would be
a trap with no safety value. Anything else — a 3-tuple, an int, an object —
falls through to `("ERROR", str(item))`, which is exactly what the pre-CIU-65
loop did to it, so an odd return value keeps failing closed rather than
becoming newly acceptable.

**Deliberate divergence from the backlog entry's proposal, for review.**
CIU-65's filed text proposes that "`ciu check`'s own exit code and CIU-64's
proposed `ciu up` preflight both key off `should_exit_on` against the
finding's severity". I did NOT wire routing through
`ciu.exit_on`/`$CIU_EXIT_ON`, and the dispatch brief agrees ("Route `WARN`
findings to `report.note`"; "WARN-severity findings should NOT block
`ciu up`"). Reason: `_CheckReport`'s `.fail`/`.note` split IS the severity
mechanism for `ciu check`, and keying it off ambient config would mean the
same config and the same hooks produce a different machine-readable `--json`
verdict depending on shell state — the exact ambient-state coupling
S9.3/CIU-41 removed from hooks. `ciu.exit_on` governs CIU's own runtime warn
sites (DESIGN-NOTES D6), not a hook's static findings. Stated in SPEC S9.5
and CONSUMERS §14 so it is not re-litigated. **If the controller wants the
backlog's version instead, this is the decision to overturn.**

`_CheckReport.note()` gained a `hook=` kwarg (and `_emit_check_report` prints
it) so a WARN names its hook exactly as an ERROR does — otherwise the two
tiers would carry different provenance for the same kind of finding.

### CIU-64 — placement and shape

`check_preflight` runs `action_check(..., live=False, json_output=False)` and
raises `ValueError` on a non-zero return, which `engine._exit_code_for` maps
to exit 2 — the same class and the same shape as the `[S7.x]`
provisioning-graph refusal the brief named as the model.

Decisions taken:

- **It runs FIRST among `ciu up`'s preflights**, before vault/producer/
  provisioning/registry/governance. An operator with a config defect gets one
  complete S13.4a report rather than being stopped by whichever narrower
  check fires first.
- **It runs under `--dry-run` too.** A dry run exists to find exactly this
  class of defect, and `action_check` is side-effect-free either way.
- **It reuses the `rendered` selection** the deploy preflights already
  computed — one render, not two. (Pinned by
  `test_check_reuses_rendered_selection_from_prior_deploy`, which asserts the
  exact action trace; I updated its expectation and said why in-place.)
- **`--skip-check` announces itself** with a `[WARN]` naming what was
  skipped. A silently skipped gate is a gate that is not there.
- `ciu up --dir <stack>` (single-stack mode) is NOT covered: the preflight
  sits in the multi-stack `deploy_needs_preflight` block where every other
  preflight lives. Extending it to `--dir` is a separate question about a
  different code path; flagging it rather than doing it silently.

Docs (mandatory per AGENTS.md, not a follow-up): SPEC S9.5 rewritten — it
still asserted `validate_config` is called "**only** during `ciu check` and
**never** during `ciu up`", which CIU-64 makes false — plus a new **S13.4c**,
the S13.4a `--json` envelope note about notes now carrying `stack`/`hook`,
and S19.1's signature mention. README feature 7. CONSUMERS §14 carried the
identical "never during `ciu up`" claim and now shows the new preflight, a
pasteable WARN example, and the severity rules. `ciu up --help`. CHANGES.md
with two Adoption/Migration bullets.

### Gate — item 2, commit `e2e18f92`

```
ciu: FAIL/COMMAND_FAILED (exit 1)
  commit: e2e18f92a0cc6ecc36cfe42768c7e1184960e89a
  argv: /opt/tester-venv/bin/python run-ciu-tests.py

                               9712      0   3960      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
=========================== short test summary info ============================
FAILED tests/tests/test_ciu_deploy_actions.py::test_check_suppresses_bytecode_writes_while_importing_hooks
FAILED tests/tests/test_ciu_deploy_actions.py::test_check_restores_the_bytecode_flag_after_a_failed_import
FAILED tests/tests/test_ciu_worktree_reap.py::TestLeaseLifecycleChangesTheNextSurvey::test_re_expiring_after_an_extend_becomes_lease_expired_again
======================= 3 failed, 3286 passed in 52.80s ========================
```

R0 FAIL (the three above), R1 PASS at 100.0. Same three as baseline.

---

## Item 3 — CIU-66 — BLOCKED (commit `2ee94f32`, record only)

Invoking the brief's "If blocked" clause. The change as briefed — a required
`stack_name` parameter on `deploy.container_name()` so the name becomes
`{project}-{env_tag}-{stack_name}-{service_name}` — cannot be landed inside
`src/ciu/` + `tests/`, and landing it there would be actively destructive.

**`container_name()` does not name anything.** It MIRRORS a convention that
CONSUMER-AUTHORED Jinja compose templates implement literally, and ciu only
ever reads the result back:

```jinja
{# src/ciu/templates/stack.compose.yml.j2:7 — ciu's OWN scaffold #}
container_name: {{ deploy.project_name }}-{{ deploy.environment_tag }}-{{ @@ROOT_KEY@@.app.name }}
```

The same literal appears in all six `test-repo/` fixtures
(`infra/vault`, `infra/db-core`, `infra/redis-core`, `applications/workers`,
`applications/app-config`). The grep the brief demanded:

```
$ grep -rn "\"container_name\"\|'container_name'" src/ciu/
src/ciu/deploy.py:1351:            cname = definition.get("container_name")
```

One hit, and it is a READ of the rendered compose model
(`resolve_selection_health_containers`). ciu never writes the key.

So changing the function alone makes every ciu lookup compute a name no
container has. All five call sites break at once, for every existing consumer
and for ciu's own fixtures:

| call site | what it would break |
|---|---|
| `deploy.py:1220` `run_health_gate` | (test-only today — no product caller) |
| `provisioning.py:352/392` `_probe_pg`/`_probe_minio` | `pg:`/`minio:` provisioning probes |
| `provisioning.py:503` `_stack_container_name` | every `stack:*:healthy\|completed` ref |
| `worktree.py:1073` | CIU-52 `--shared-infra-ref-services` resolution |

**Blocking sub-finding: the new name is not expressible today.**
`render_ciu_context` (`deploy_pkg/profiles.py:364`) exposes exactly
`selected_profiles` and `deployed_stacks`. There is no "the stack this render
belongs to" fact anywhere in the template context, so a consumer template
*cannot* emit `{project}-{env}-{stack}-{service}` even if it wanted to. Step
one of any real fix is a new PUBLIC template fact, not a signature change.

A real CIU-66 therefore needs, at minimum: (1) a per-stack identity fact in
the render context; (2) ciu's own scaffold template and all six `test-repo/`
compose templates updated; (3) a migration for every consumer's templates
(dstdns has 31) — with either a transition where ciu resolves BOTH name
shapes, or a flag day; (4) a stack qualifier in the
`--shared-infra-ref-services alias=service` CLI grammar, itself a
user-facing break documented in CONSUMERS §8; (5) `_probe_pg`/`_probe_minio`'s
literal `postgres`/`minio` keys, which CIU-70 already tracks as the same
coupling.

Two facts argue for doing it at the v8 cut rather than now, and the backlog
entry says both itself: CIU-66 is **"Currently latent, not live"** (no
duplicate service name exists in dstdns today), and its own filed text says
the fix "should land as one change to CIU-51's `qname()` signature ...
otherwise v8 ships two independent stack-qualification schemes ... that could
drift from each other."

**Recommendation:** re-scope CIU-66 as a v8 identity package alongside
CIU-51/CIU-50/CIU-70, opening with the render-context fact. Nothing was
changed in this package. Needs controller review before merge.

---

## Item 1 — CIU-67 + CIU-68 (commit `ebe45c39`)

### CIU-67 — the budget is DERIVED, not defaulted

`[deploy.health].timeout` is the Docker `HEALTHCHECK` field (one probe
attempt). It was also the S7.7 gate's overall budget. A container's gate
budget now resolves most-specific-first:

1. the phase entry's `health_timeout` — the existing per-service escape
   hatch, unchanged and still the winner;
2. the new `[deploy.health].gate_timeout`;
3. otherwise DERIVED per container from its own rendered healthcheck as
   `start_period + retries × interval`.

**Why `start_period + retries × interval` and not just `start_period`** (the
backlog proposes the latter): a container only reports healthy on a
successful probe, and probes land on `interval` boundaries. A budget of
exactly `start_period` can expire one interval before the first post-grace
probe even runs. `start_period + retries × interval` is Docker's own worst
case for a container still legitimately converging — the full grace period
during which failures do not count, plus the consecutive retries a post-grace
probe sequence needs to become conclusive.

Fields the healthcheck omits use Docker's documented defaults (interval 30s,
retries 3, start_period 0s) — READ facts about what the daemon will do, not
invented numbers. A service with no healthcheck gets the same 90s and never
waits on it: it classifies `no-healthcheck`, which is in
`health._READY_STATUSES` and resolves on the gate's first poll.

`resolve_gate_timeout_s` returns `None` when `gate_timeout` is undeclared,
never `0` — an absent key is not a value, and `None` is what selects the
derivation.

The reproduction is pinned verbatim as a test: `timeout = "5s"` beside a
service declaring `start_period: 240s, interval: 10s, retries: 3` must
resolve to **270s**, not 5s. **No existing test pinned the old coupling**,
which is why the defect shipped — worth noting for the reviewer.

### CIU-68(a) — self-selecting, and the flags are discoverable

`health_after_phase` moved to after `rendered` exists (the answer is derived
from the rendered selection) and now also turns on when
`selection_stack_health_requirement` finds a `stack:*:healthy|completed`
requirement, announcing the ref responsible. Self-selecting: a selection with
no such ref pays nothing. `--deploy`, `--healthcheck` and `--check` are now
listed in `ciu up --help` under a new Actions block, per the entry's explicit
"document it regardless" instruction.

The helper tolerates a stack whose root table cannot be resolved (skips it)
rather than raising from a "should the gate run?" question — that stack is
refused loudly moments later by `provisioning_preflight`'s own
`validate_stack_shape` call on the same config, so nothing is hidden.

### CIU-68(b) — only "on track" gets to wait

`ProbeResult` gained `retryable: bool = False`. The default keeps every
existing construction fail-promptly, which is correct for them. It is set
`True` in exactly two places, both meaning "a normal deployment reaches the
satisfied state from here":

- `stack:*:healthy` whose `Health.Status == "starting"` (inside its own
  `start_period`);
- `stack:*:completed` whose container is still running.

**I included the `:completed`/still-running case although the brief named
only `starting`.** It is the identical mistake in the other terminal — a
one-shot job that has not finished YET is not a job that failed — and
CIU-68's own trigger condition names both ref terminals. Flagging it as a
deliberate, documented extension rather than scope creep.

Everything else still fails promptly: absent container, `unhealthy`,
non-zero exit, docker unavailable, unparseable state. This matters as much as
the poll itself — a poll that waited on everything would turn every real
misconfiguration into a long silent stall, and there is a test asserting the
non-retryable path never sleeps at all.

**A string-sniffing implementation was rejected**: deciding retryability by
matching `"starting"` in `result.reason` is AGENTS.md's "type for behaviour"
anti-pattern (two different causes producing one indistinguishable signal).
The flag is set at the branch that knows the condition.

Budget: `gate_timeout` when declared, else the same 90s Docker-derived
default, at the gate's own 5s cadence, with ONE shared deadline for the whole
phase's probe pass (so ten retryable requirements do not each get a fresh
full budget in sequence). It does NOT derive per container the way S7.7's
gate does, and the docstring says why: this preflight probes a container in
an EARLIER phase's stack, whose compose model this phase's render does not
carry, so the per-container derivation genuinely is not available here.

`test_ciu_cli_parser.py`'s per-verb-help leak sentinel changed from
`--deploy` to `--stop`: it asserted `--deploy` was ABSENT from
`ciu up --help`, which is the exact absence CIU-68 identifies as the
discoverability defect. The test's real intent (the legacy `ciu-deploy`
argparse surface must not leak through) is preserved with a sentinel that
still holds.

### Gate — item 1, commit `ebe45c39`

```
ciu: FAIL/COMMAND_FAILED (exit 1)
  commit: ebe45c39e5c0639b3c5b9ff67cb54e1529812ad3
  argv: /opt/tester-venv/bin/python run-ciu-tests.py

TOTAL                                             9778      0   3990      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
=========================== short test summary info ============================
FAILED tests/tests/test_ciu_deploy_actions.py::test_check_suppresses_bytecode_writes_while_importing_hooks
FAILED tests/tests/test_ciu_deploy_actions.py::test_check_restores_the_bytecode_flag_after_a_failed_import
FAILED tests/tests/test_ciu_worktree_reap.py::TestLeaseLifecycleChangesTheNextSurvey::test_re_expiring_after_an_extend_becomes_lease_expired_again
======================= 3 failed, 3319 passed in 28.20s ========================
```

R0 FAIL (the three above), R1 PASS at 100.0. Same three as baseline; `src/ciu/deploy.py`
and `src/ciu/provisioning.py` both at 100% line AND branch with every new
arc covered.

---

## Not done / for the reviewer

- **CIU-66 is BLOCKED** and needs a controller decision before merge (above).
- **CIU-65's `should_exit_on` divergence** from the backlog's proposed text is
  deliberate and reasoned above; overturn it there if the controller
  disagrees.
- **The two bytecode-flag test failures are unfiled** and keep this lane red
  for everyone. Recommend filing.
- **`ciu up --dir <stack>` does not get CIU-64's preflight** — a different
  code path, flagged rather than silently extended.
- **`engine.py:1338` / `engine.py:1489`** are CIU-62-shaped clauses the entry
  does not list; left alone, worth adding to CIU-62 or CIU-75.
- The shipped reference hook
  (`src/ciu/hook_templates/post_compose_db.py::validate_config`) still
  returns a flat `list[str]`. That is still correct and blocking under
  CIU-65, so nothing is broken; demonstrating a WARN there would force a
  `template_revision` bump and was out of the brief's file list.

---

# Post-review closeout (2026-08-31)

Verdict on the five-commit package: **ACCEPT-conditional**. All three flagged
design departures and the CIU-66 BLOCKED call were independently verified and
endorsed. Three blockers, all addressed below.

## The "gate is RED on arrival" section above is now HISTORY, not a caveat

It described the state at base `858766d1`. The rebase onto `384993b6` brings
in fixes for **all three** of those failures:

- the two `dont_write_bytecode` failures I reported as *unfiled* were in fact
  filed and fixed as **CIU-78** in `aa6cf1fd`, which landed after my branch
  point. My recommendation to file them was correct but already actioned; my
  claim that they were unfiled was stale, not wrong-at-the-time.
- the lease failure is **CIU-76**, fixed in the ciu-P36 merge `384993b6`.

So the post-rebase verdict is read as a plain PASS, not as "the same three
failures". The numbers in the per-item gate sections above remain exactly as
run and are left untouched as the record of what those commits were gated at.

## Blocker 1 — rebase onto current main

`git rebase main` (`858766d1` → `384993b6`, 14 commits): **clean, no
conflicts**. My `worktree.py` hunks are disjoint from ciu-P36's, and I touch
neither of the two tests `aa6cf1fd` rewrote. New hashes are tabulated in the
LOG.

## Blocker 2 — `engine.py`'s S3.12 identity read (commit `50e63cf8`)

Accepted without reservation: it is the same defect shape I fixed at
`deploy.py`'s `_workspace_identity`, in the function that is that one's
real-run twin. `except (WorkspaceEnvError, OSError)` →
`(OSError, UnicodeDecodeError, WorkspaceEnvError)`.

**On the reviewer's `None`-vs-unreadable point.** The reviewer is right that
telling a hook "no identity" when the truth is "identity unreadable" is the
same absence-for-emptiness confusion I fixed at
`_workspace_identity_network`. I deliberately kept the `{}` degradation here
anyway, and the reason is that this site is not that site:

- `_workspace_identity_network`'s two conditions had DIFFERENT correct
  answers, and conflating them let `ciu clean` under-clean silently. Raising
  was the only way to stop that.
- This site's contract is symmetry with `deploy._workspace_identity`, which
  documents `{}` and which the `ciu check` preflight uses to build the SAME
  two `HookContext` fields. If the real run raised where the preflight
  degrades, a hook's `validate_config` would see an identity its own `run()`
  never will — the exact divergence S3.12/CIU-44 exists to prevent, and a
  worse failure than the one being avoided.

Making BOTH raise is a coherent alternative and a bigger change (it turns an
unreadable `ciu.env` into a hard `ciu up`/`ciu check` refusal); I did not
take it unilaterally. Flagging it as the open question if the controller
wants the stricter posture.

**Overclaim corrected.** `a52086a2`'s subject says "every ciu.env reader now
catches all three read failures". False when written: seven narrow sites, not
six. No interactive rebase is available here, so the bad message is left
buried (AGENTS.md: land a correct new commit rather than repair a buried
one) and the accurate statement is in `50e63cf8`'s message, CHANGES.md, the
LOG and here.

**Controlled wrong implementation, run by hand**: restoring the pre-fix
clause fails the non-UTF-8 case and passes the malformed-entry case — exactly
the gap profile CIU-62 attributes to `(OSError, WorkspaceEnvError)`.

## Blocker 3 — the backlog (commit `64cfbe61`)

All six rows marked, following `aa6cf1fd`/`4b471e63`'s convention (the
RESOLUTION column changes; the description column is left intact), plus the
top-of-file "Last updated" narrative.

- **CIU-62, CIU-64, CIU-65, CIU-67, CIU-68 → `FIXED — ciu-P41: …`**, each
  carrying what landed, the tests, and the controlled wrong implementation.
- **CIU-67 and CIU-68's rows name their DEPARTURES from their own filed
  proposals** (`start_period + retries × interval` rather than bare
  `start_period`; retryability extended to `:completed`/still-running), with
  the reasoning, so neither looks like drift later.
- **CIU-65's row records the `should_exit_on` decoupling as an explicit
  "DECISION, do not 'fix' this back in"** with its reasoning — the entry's
  own original proposal says the opposite, so without this a future agent
  would wire it back in as a missing piece.
- **CIU-66 → `OPEN — BLOCKED (ciu-P41)`**, carrying the blast radius that
  previously lived only in this report: the one-hit `container_name` write
  grep, the five call sites that break, **the four hand-assembled sites the
  reviewer found** (`dev.py:299`, `engine.py:1516`,
  `deploy_pkg/health.py:265`, `deploy.py:3124`/`:3878`) which would silently
  DIVERGE rather than fail loudly, and the `render_ciu_context` gap. It names
  the concrete next step: **expose a per-stack identity fact in
  `render_ciu_context` as its own small additive package** — prerequisite for
  both CIU-66 and CIU-51's `qname()`. Not built here, per the coordinator.

## Post-review gate — commit `64cfbe61` — **PASS**

`64cfbe61` is the last commit that touches code or the backlog; the only
commit after it is this docs-only REPORT/LOG update, so
`git diff 64cfbe61 HEAD --stat` lists nothing but this file and the LOG.
That is the gated tree.

`./run-gate.py ciu --worktree /workspaces/vbpub/.worktrees/ciu-P41-checkpoint1-remainder`,
verdict read in a SEPARATE step from the run (LESSONS L4), never off a pipe
tail:

```
run-gate: rev 23 | lane ciu | env [environments.tester-unified] | slice dev-background.slice
assay-2.3.0.pyz: OK
ciu: PASS (exit 0)
  commit: 64cfbe61c461ca863edb81a8a287593aa85e7e31
  argv: /opt/tester-venv/bin/python run-ciu-tests.py
run-gate: verdict artifact: .../ciu/.assay/verdict-ciu.json
run-gate: lane 'ciu' exit 0
EXIT=0
```

From the verdict artifact itself:

```
outcome: PASS | exit_code: 0 | commit: 64cfbe61c461ca863edb81a8a287593aa85e7e31
  R0 PASS
  R1 PASS  pct=100.0  branch=reported
R1 coverage: pct=100.0 considered=5 covered=241 executable=241 branches=42/42
files_missing_coverage: []
judgment.r1: fail_under=100.0, mode=changed_lines, require_branch=true,
             allow_excluded=false, base=a6e6ebe6
```

**Green, with zero failures — the first plain PASS this package has had**, and
the three arrival-state failures are gone because the rebase carries their
fixes (CIU-78, CIU-76), not because anything was suppressed. Whole-project
run in the devcontainer venv on the same tree for cross-check: **3326 passed,
0 failed**, `TOTAL 9778 statements / 0 missing / 3990 branches / 0 partial =
100%`, with `src/ciu/engine.py` at 100% including the newly widened clause.

## Final state

| | |
|---|---|
| branch | `fix/ciu-P41-checkpoint1-remainder` |
| based on | `384993b6` (current main) |
| last code/backlog commit | **`64cfbe61`** — gated PASS |
| commits | 7 (5 replayed by the rebase + the `engine.py` fix + the backlog/report closeout) |
| items | CIU-62, CIU-64, CIU-65, CIU-67, CIU-68 FIXED · CIU-66 BLOCKED with its analysis in the backlog |

Still open for the controller, none of them blocking this merge:

- **CIU-66's next step** is named in its backlog row (expose a per-stack
  identity fact in `render_ciu_context`, its own small additive package).
  Not built here, per the coordinator.
- **`ciu up --dir <stack>`** does not get CIU-64's preflight — different code
  path.
- **Whether `deploy._workspace_identity` / `engine.py`'s S3.12 read should
  RAISE rather than degrade to `{}`** on an unreadable `ciu.env` — a coherent
  stricter posture, deliberately not taken unilaterally (reasoning under
  blocker 2 above).

---

# Review round 2 (2026-08-31)

Round-1 blockers verified closed. Round 2: one merge hazard, one ruling.

## Blocker — the rebase onto CIU-70 (`e936dd70`)

The `deploy.py` probe-loop conflict is the real one, and the warning about it
was well founded: **either side taken wholesale silently reverts the other**,
and both reverts are invisible to the other change's tests. Resolved exactly
as directed — my bounded-retry structure, with `stacks=probe_graph` on BOTH
`probe_ref` calls, initial and in-loop.

The two other `deploy.py` hunks were flagged as benign adjacent insertions,
and they are — but with a wrinkle worth recording: P40's `provisioning_graph`
and my `selection_stack_health_requirement` share a
`try: validate_stack_shape(stack_cfg) / except ValueError: continue` tail,
which git chose as the common context. A "keep both blocks" resolution done
by eye welds the head of one function onto the body of the other and still
parses. Both were reconstructed in full and checked individually.

**The merged loop was code no gate run had ever executed** — and it broke
four of my own tests on the first local run, because the bounded-poll
fixture's `probe_ref` stub did not accept CIU-70's `stacks=` kwarg. That is
the concrete vindication of insisting on a fresh verdict rather than reusing
the round-1 one.

I turned it into an oracle rather than just repairing the stub:
`test_the_resolution_graph_reaches_every_reprobe_not_just_the_first` asserts
every probe — initial AND each re-probe — receives the same CIU-70 graph.
This is the one assertion that catches the plausible-but-wrong resolution
(graph on the first call, dropped on the retry), which passes every CIU-70
test because none of them retry, passes every call-counting CIU-68 test, and
fails only live on the retry path — the exact path the original `starting`
failure lived on. Controlled wrong implementation run by hand: it yields
`[graph, None, None]` and reds that test alone.

## Ruling — warn on the HookContext identity degradation

Implemented as ruled: the `{}` degradation stays at both sites (that symmetry
is what stops a preflight from seeing an identity the real run will not), and
both now warn, naming the unreadable file and the `ciu env generate` repair.

**One correction the ruling could not have anticipated, and it matters.**
Written as `warn(...)` — the literal 2-line change — it immediately reddened
three existing tests with `JSONDecodeError`. `deploy.warn()` prints to
**stdout**, and `ciu check --json` puts only its versioned JSON document
there (S13.4a). A `[WARN]` line ahead of it corrupts every machine consumer's
parse: a real regression, not a test artifact. The deploy-side warning
therefore goes to **stderr**, matching the split `ciu graph --format json`
already documents; `engine.py`'s twin stays on stdout, its own idiom, with no
machine-readable stdout channel to protect. Both code comments state the
asymmetry and its reason so it does not read as an inconsistency later.

An ABSENT `ciu.env` stays silent — it is a legitimate state, and a warning on
every unprovisioned workspace is a warning nobody reads. Pinned as its own
parametrized case, alongside the load-bearing assertion that **stdout stays
empty**, which is what will catch a future revert to `warn()`.

**CIU-80 filed** for the stricter variant, with both candidate shapes ((a)
both sites raise; (b) `HookContext` gains a third `identity_unreadable`
state), the tradeoff of each, and — stated as MANDATORY — that the two sites
must change as a PAIR, because fixing one alone reintroduces exactly the
preflight-vs-real-run divergence CIU-62 avoided. Cross-referenced from
CIU-62's own row and from CHANGES.md.

## Round-2 gate — commit `<R2_HASH>` — **<R2_VERDICT>**

<!-- R2_GATE -->
