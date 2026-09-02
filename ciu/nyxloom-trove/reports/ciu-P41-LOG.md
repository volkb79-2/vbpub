# ciu-P41 — LOG

Package: CIU-67+CIU-68 (item 1), CIU-64+CIU-65 (item 2), CIU-66 (item 3),
CIU-62 (item 4). Worktree `.worktrees/ciu-P41-checkpoint1-remainder`, branch
`fix/ciu-P41-checkpoint1-remainder`, based on vbpub main `858766d1`.

One entry per commit, newest last. Gate verdicts live in
`ciu-P41-REPORT.md`, pasted verbatim per item.

---

## Baseline (before any commit) — the gate is RED on arrival

`./run-gate.py ciu --worktree /workspaces/vbpub/.worktrees/ciu-P41-checkpoint1-remainder`
at `858766d1` (no changes of mine in the tree — assay snapshots COMMITTED
objects only, so the run is of main as-is):

    ciu: FAIL/COMMAND_FAILED (exit 1)
      commit: 858766d1059b3557ce9f962e72cfafb95e43d123
    3 failed, 3258 passed in 171.65s
    Required test coverage of 100% reached. Total coverage: 100.00%

    FAILED tests/tests/test_ciu_deploy_actions.py::test_check_suppresses_bytecode_writes_while_importing_hooks
    FAILED tests/tests/test_ciu_deploy_actions.py::test_check_restores_the_bytecode_flag_after_a_failed_import
    FAILED tests/tests/test_ciu_worktree_reap.py::TestLeaseLifecycleChangesTheNextSurvey::test_re_expiring_after_an_extend_becomes_lease_expired_again

None of the three is mine, and none is fixable inside this package's scope
(analysis in the REPORT). Every per-item gate verdict below is therefore read
as **"the same three failures, no new ones, coverage still 100%"**, never as
a bare exit code.

---

## Commit 1 — `39d092c3` — item 4, CIU-62

`fix(ciu): CIU-62 -- every ciu.env reader now catches all three read
failures; clean stops folding indeterminacy into "no network"`

Five narrow `except` clauses widened to name all three failure modes of
`parse_workspace_env` (`OSError`, `UnicodeDecodeError`, `WorkspaceEnvError`);
the sixth site was a semantics decision, taken deliberately and recorded in
the REPORT. Line numbers in the CIU-62 entry had all moved; re-derived by
grepping the clause shapes. Two `(OSError, ValueError)` sites left alone as
the entry advises.

Files: `src/ciu/worktree.py`, `src/ciu/deploy.py`, `docs/SPEC.md` (S6.4a
clause 1), `CHANGES.md`, and five test files (11 new tests, one existing test
replaced because it asserted the pre-fix contract verbatim).

Gate after this commit: `39d092c3`, **3 failed / 3269 passed**, coverage
100.00% — the same three baseline failures, +11 passing. Verbatim in REPORT.

---

## Commit 2 — item 3, CIU-66 — **BLOCKED, no code change**

Recorded under the brief's "If blocked" clause. The real scope is
dramatically different from the brief's description, and the briefed change
would be actively destructive if landed as specified. Full analysis in the
REPORT; the short version:

`deploy.container_name()` does not NAME anything. It MIRRORS a naming
convention that CONSUMER-AUTHORED Jinja compose templates implement
literally, and ciu only ever reads the result back:

    # src/ciu/templates/stack.compose.yml.j2:7  (ciu's own scaffold)
    container_name: {{ deploy.project_name }}-{{ deploy.environment_tag }}-{{ @@ROOT_KEY@@.app.name }}

Verified: `grep -rn "\"container_name\"\|'container_name'" src/ciu/` returns
exactly ONE hit, `deploy.py:1351` — `definition.get("container_name")`, a
READ of the rendered compose. ciu never writes the key. So changing
`container_name()` to emit `{project}-{env}-{stack}-{service}` without
changing what the templates emit makes every ciu lookup compute a name no
container has: the S7.7 health gate, the `stack:`/`pg:`/`minio:` provisioning
probes, and CIU-52's `--shared-infra-ref-services` resolution all break at
once, for every existing consumer and for ciu's own `test-repo/` fixtures.

Blocking sub-finding: a template cannot express the new shape today.
`render_ciu_context` (`deploy_pkg/profiles.py:364`) exposes exactly
`selected_profiles` and `deployed_stacks` — there is no "the stack this
render belongs to" fact anywhere in the template context. The stack
qualifier CIU-66 wants does not exist as a renderable value, so step one of
any real fix is a new public template fact, not a signature change.

Left uncommitted-as-code on purpose. Needs controller review before merge.

---

## Commit 3 — `e2e18f92` — item 2, CIU-65 + CIU-64

`feat(ciu): CIU-65 -- validate_config findings carry a severity; CIU-64 --
`ciu up` runs `ciu check` itself, by default`

CIU-65 first because CIU-64 consumes its severity vocabulary. `_CheckReport`
already had `.fail`/`.note`; the hook-result loop simply never reached for
`.note`. `note()` gained a `hook=` kwarg so a WARN names its hook exactly as
an ERROR does. `classify_hook_finding` is the new one-finding classifier.

CIU-64 inserts `check_preflight` into BOTH `ciu up` preflight blocks (the
real one and the `--dry-run` one), first among the preflights, reusing the
`rendered` selection they already computed. `--skip-check` added beside
`--no-preflight`.

Two existing tests encoded contracts this changes and were updated with the
reason in-place: `test_check_reuses_rendered_selection_from_prior_deploy`
(exact action trace, now carries the preflight check).

Files: `src/ciu/deploy.py`, `src/ciu/cli.py`, `docs/SPEC.md` (S9.5 rewritten,
new S13.4c, S13.4a's `--json` note, S19.1), `README.md`, `docs/CONSUMERS.md`
§14, `CHANGES.md`, 3 test files (18 new tests, 1 new file).

Gate after this commit: `e2e18f92`, **3 failed / 3286 passed**, coverage
100.00% — the same three baseline failures. Verbatim in REPORT.

---

## Commit 4 — `ebe45c39` — item 1, CIU-67 + CIU-68

`fix(ciu): CIU-67 -- a distinct gate_timeout key, derived per container;
CIU-68 -- the health gate self-selects and a `starting` dependency is waited
for`

CIU-67: new `[deploy.health].gate_timeout`; `derive_gate_budget_s` reads each
container's own compose healthcheck. `resolve_selection_health_containers`'s
`default_timeout_s` became `float | None`, where None means "derive".

CIU-68(a): `health_after_phase` moved after `rendered` exists and now also
turns on from `selection_stack_health_requirement`. (b): `ProbeResult` gained
`retryable`; `provisioning_preflight` polls only retryable results, one
shared deadline per phase.

`test_ciu_cli_parser.py`'s per-verb-help leak sentinel changed from
`--deploy` to `--stop` — it asserted `--deploy` was ABSENT from
`ciu up --help`, the exact absence CIU-68 calls a defect.

Files: `src/ciu/deploy.py`, `src/ciu/provisioning.py`, `src/ciu/cli.py`,
`docs/SPEC.md` (S7.7), `docs/CONFIG.md`, `CHANGES.md`, 2 test files (33 new
tests in one new file).

Gate after this commit: `ebe45c39`, **3 failed / 3319 passed**, coverage
100.00% — the same three baseline failures. Verbatim in REPORT.

---

# Review closeout (ACCEPT-conditional → three blockers)

The package was reviewed at the five commits above and returned
**ACCEPT-conditional**. All three flagged design departures
(`start_period + retries × interval`; extending retryability to
`:completed`; NOT wiring severity through `should_exit_on`) were
independently verified and endorsed, as was CIU-66's BLOCKED call — with the
reviewer finding the blast radius WIDER than I reported: four more sites
hand-assemble the `{project}-{env_tag}-{service}` shape without calling
`container_name()` at all, and so would silently DIVERGE rather than break
loudly (`dev.py:299`, `engine.py:1516`, `deploy_pkg/health.py:265`,
`deploy.py:3124`/`:3878`).

## Blocker 1 — stale base (rebase)

The branch was 14 commits behind main, missing `aa6cf1fd` (CIU-78 — the fix
for the two `dont_write_bytecode` failures I had reported as unfiled) and
`384993b6` (ciu-P36 merge, carrying CIU-76's fix — the third).

`git rebase main` onto `384993b6`: **clean, no conflicts**, as the reviewer
predicted. All five commits replayed; every hash changed:

| item | old | new |
|---|---|---|
| 4 — CIU-62 | `39d092c3` | `a52086a2` |
| 3 — CIU-66 blocked record | `2ee94f32` | `5c113ac3` |
| 2 — CIU-64 + CIU-65 | `e2e18f92` | `78c48772` |
| 1 — CIU-67 + CIU-68 | `ebe45c39` | `19936edd` |
| LOG + REPORT | `81a3d3b0` | `967733e4` |

**All three of the pre-existing failures I documented are fixed by the
rebase**, so the arrival-state analysis at the top of this LOG is now
history, not a live caveat — the post-rebase gate is expected GREEN, and is
read as such rather than as "same three failures".

## Commit 6 — `50e63cf8` — item 4, CIU-62 — blocker 2

`fix(ciu): CIU-62 -- engine.py's S3.12 identity read was a missed site;
correct the earlier commit's "every ciu.env reader" claim`

`engine.py`'s `except (WorkspaceEnvError, OSError)` around the S3.12
hook-identity read is the REAL-RUN twin of `deploy.py`'s
`_workspace_identity` — same two `HookContext` fields, same file, same exact
path — and carried the byte-for-byte identical gap. It is NOT in CIU-62's
own six-site list, and I recorded it in the REPORT as "noted, not changed"
instead of fixing it, which was the wrong call. Widened to
`(OSError, UnicodeDecodeError, WorkspaceEnvError)`.

`a52086a2`'s subject claims "every ciu.env reader now catches all three read
failures". That was false when written — there were seven narrow sites, not
six. No interactive rebase is available in this environment, so the bad
message is left buried per AGENTS.md and corrected in `50e63cf8`'s own
message, CHANGES.md, this LOG and the REPORT.

`engine.py:1338` is untouched: `except WorkspaceEnvError: raise` re-raises
and swallows nothing — not this class of defect (reviewer independently
confirmed).

Tests: two parametrized cases through the full engine with a REAL
unparseable `ciu.env`, plus a hook probe recording what `ctx` was actually
told, so the oracle is "no traceback escaped AND the hook saw `None|None`".
**Controlled wrong implementation verified by hand**: with the pre-fix clause
restored (`git checkout HEAD -- src/ciu/engine.py`), the non-UTF-8 case FAILS
and the malformed-entry case PASSES — precisely the gap profile CIU-62
attributes to that clause shape.

## Commit 7 — `64cfbe61` — backlog — blocker 3

`backlog(ciu): mark CIU-62/64/65/67/68 FIXED, CIU-66 OPEN — BLOCKED -- ciu-P41`

All six rows had still read OPEN. Marked per `aa6cf1fd`/`4b471e63`'s
convention (the RESOLUTION column is what changes; the description column is
left alone), plus the top-of-file "Last updated" narrative.

CIU-66's row is the substantive one: rewritten to `OPEN — BLOCKED
(ciu-P41)` carrying the blast-radius analysis that previously lived only in
the REPORT — the single `container_name` write-site grep result, the five
call sites that would break, the four hand-assembled sites the REVIEWER
found that would silently diverge, and the `render_ciu_context` gap — and
naming the concrete next step: exposing a per-stack identity fact in
`render_ciu_context` as its own small additive package, prerequisite for
both CIU-66 and CIU-51's `qname()`. A finding that lives only in a report is
a finding nobody acts on.

CIU-65's row records the `should_exit_on` decoupling as an explicit
**DECISION, do not "fix" this back in**, with the reasoning, since the
entry's own original proposal says the opposite.

Gate after this commit: **`ciu: PASS (exit 0)` at `64cfbe61`**, R0 PASS +
R1 PASS at 100.0% (changed-lines mode, branch required, `allow_excluded`
false, zero files missing coverage). Verbatim, plus the verdict-artifact
detail, in the REPORT's "Post-review gate" section. `64cfbe61` is the last
commit touching code or the backlog; only a docs-only LOG/REPORT commit
follows it.

---

# Review round 2 — rebase onto CIU-70, and the degradation-warning ruling

Round-1 blockers all verified closed. Round 2 raised one merge hazard and one
product ruling.

## The rebase onto `e936dd70` — one dangerous conflict, two benign

Main advanced past my base by 12 commits, adding ciu-P40's **CIU-70** merge.
`git rebase main` conflicted in two files.

**`deploy.py`, hunk 3 — the dangerous one.** CIU-70 and CIU-68(b) both edit
the SAME statement in the per-phase probe loop, and the two sides are
orthogonal changes to it:

- main/P40: `probe_ref(ref, config, repo_root, stacks=probe_graph)` — the
  graph a probe resolves its target container from;
- mine/P41: the bounded retry wrapping that call.

Taking either side wholesale silently reverts the other — CIU-70's absence
fails every `pg:`/`minio:` ref closed with "no requires/provides graph
given"; CIU-68(b)'s absence restores the one-shot probe that failed a fresh
`ciu up` on a `starting` dependency. Resolved as directed: **my loop
structure, with `stacks=probe_graph` threaded through BOTH probe calls** —
the initial one and the in-loop re-probe — with an in-code comment saying why
neither side may be dropped.

**`deploy.py`, hunks 1–2 — benign adjacent insertions.** P40's
`provisioning_graph` and my `_REQUIREMENT_POLL_INTERVAL_S` /
`resolve_requirement_poll_budget_s` / `_STACK_HEALTH_REF_RE` /
`selection_stack_health_requirement` were inserted at the same point and
happen to share a `try: validate_stack_shape(stack_cfg) / except ValueError:
continue` tail, which git used as the common context — so the naive
resolution silently welds half of one function onto the other. Both blocks
reconstructed in full and verified: `provisioning_graph` returns `graph`,
`selection_stack_health_requirement` returns the ref or `None`, and
`deploy.py` parses.

**`KNOWN_ISSUES_TODO_BACKLOG.md` — routine.** Both narrative headers kept,
mine promoted to "Last updated" and P37's demoted to "Previously" (the file's
own pattern). All rows from both sides verified present with the right
status, including P40's own `CIU-70 | FIXED — ciu-P40`. Unescaped-pipe count
per touched row still 4, so no cell broke the table.

**The merged loop is code no gate run had ever executed**, exactly as
flagged. It also broke four of my own tests immediately: the bounded-poll
fixture's `probe_ref` stub did not accept CIU-70's `stacks=` kwarg. Fixed,
and turned into an asset — see the new test below.

## Commit 9 — `27ab3574` — the degradation-warning ruling + CIU-80

Both HookContext identity sites now WARN on the `{}` degradation, keeping the
degradation itself (the preflight/real-run symmetry is the point).

**One thing the ruling could not have anticipated, and it is the interesting
part:** writing this with `deploy.warn()` immediately reddened three existing
tests with `JSONDecodeError`. `warn()` prints to **stdout**, and under
`ciu check --json` the versioned JSON document is the only thing that path
may put on stdout (S13.4a) — the warning would have corrupted every machine
consumer's parse. Routed to **stderr** instead, matching the split
`ciu graph --format json` already documents. `engine.py`'s twin stays on
stdout: its own idiom, and no machine-readable stdout channel to protect.
The asymmetry and its reason are stated in both code comments.

An ABSENT `ciu.env` stays silent — a legitimate state, and a warning on every
unprovisioned workspace is a warning nobody reads.

New test `test_the_resolution_graph_reaches_every_reprobe_not_just_the_first`
pins the merge itself: threading `stacks` through the first probe and
dropping it on the re-probe passes every CIU-70 test (none retry) and every
call-counting CIU-68 test, and fails only live, on the retry path. Controlled
wrong implementation verified by hand — it yields `[graph, None, None]` and
reds that one test.

CIU-80 filed for the stricter variant, recording that the two sites must
change as a PAIR.

Gate after this commit (and after the docs commit `75e54875` that followed
it): **`ciu: PASS (exit 0)` at `75e54875`**, R0 PASS + R1 PASS at 100.0%
(285/285 lines, 42/42 branches, base `e936dd70` — current main). Verbatim in
the REPORT.
