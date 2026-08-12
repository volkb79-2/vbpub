# LOG — ciu-P01-worktree-isolation-primitives

Implementer: Sonnet 5 (xhigh). Handoff:
`nyxloom-trove/handoffs/ciu-P01-worktree-isolation-primitives.md`.
`input_revision`: `1a891facc6936419b67f2876c1eafb6eeb0862d4`; this worktree's
actual starting HEAD was `202d2925` (a later commit on the same branch that
only updated the handoff's own `input_revision` pointer — no `src/` delta
between the two).

## Baseline (before any code change)

Ambient shell env in this sandbox carries a contaminated `REPO_ROOT`/
`PHYSICAL_REPO_ROOT` pointing at a sibling repo (`/workspaces/dstdns`) — this
devcontainer artifact is unrelated to CIU-10 (already FIXED) and is not part
of this package; every command below therefore runs with
`env -u REPO_ROOT -u PHYSICAL_REPO_ROOT` so the measurement reflects the code,
not the sandbox.

`PYTHONPATH=src python3 run-ciu-tests.py` (the project's own gate helper,
`[gates.tester-unified]`'s second half without the docker wrapper — no docker
socket reachable from here either):

```
1749 passed, 8 warnings in 13.98s
...
TOTAL   5882 stmts  262 miss  2324 branch  17 partial  96% cover
FAIL Required test coverage of 100% not reached. Total coverage: 95.72%
src/ciu/worktree.py   94  94   38   0    0%   35-286   <- zero coverage, as documented
```

`PYTHONPATH=../nyxloom/src python3 -m nyxloom.coverage_gate --repo . --base
main --coverage-json coverage.json --source src/ciu` (the changed-line gate):

```
diff-coverage NO MEASUREMENT: resolved base (202d292501fd) IS HEAD -- there is
no delta to measure.
```

i.e. HEAD == `main` at start, exactly as the task brief said to verify. The
blanket-100% shortfall above is 100% pre-existing (mainly `worktree.py`'s
documented zero coverage, `ksm.py` 68%, and scattered gaps in `cli.py`/
`composefile.py`/`deploy.py`/`governance.py` this package never touches) —
not this package's problem per the handoff's own note on `worktree.py`.

## What was built

### O1 / CIU-20 — machine-readable provenance verdict (contract class: **2b**)

All data grammar, field order, and the six-value `overall` decision table
were fully specified in the observable; nothing here required inventing an
interface. What *was* real construction work: `deploy.verify_running_provenance`
is a genuine behavioural rewrite, not a wrapper — it now returns a
`ProvenanceResult` dataclass (`schema_version, instance, commit_under_test,
tree_state, containers, overall`) and NEVER raises or prints; `_running_containers`
now returns `None` on enumeration failure vs `[]` on a successful empty
enumeration (previously collapsed, which is the exact false-green CIU-20
names). `cli._provenance` became the SOLE place deciding prose/raise/warn
from that verdict, added `--json` (`store_true`, matching `ciu diagnose
--json`'s shape exactly), and reproduces the OLD prose byte-for-byte for
every case that was already correct (identity-refused, config-load-failure,
`not-verified-unknown` silence, `not-verified-dirty` warn text, `mismatch`
raise text, `verified-match` "provenance OK" line) — the only genuinely NEW
prose is `not-verified-no-evidence`'s warning, since that case did not exist
as a distinguishable state before (it used to silently read as
`verified-match`).

- `src/ciu/deploy.py`: `ContainerProvenance`, `ProvenanceResult`,
  `verify_running_provenance` (rewritten), `_running_containers` (return type
  changed `list -> Optional[list]`).
- `src/ciu/cli.py`: `_provenance` (rewritten — `--json` flag, six-branch
  dispatch on `result.overall`).
- Verified against the seven frozen fixtures directly (parsed-JSON equality,
  `json.load` both sides — see `test_ciu_provenance_json.py`'s
  `TestProvenanceResultGrammar` and the ad-hoc script run during development,
  which matched all seven before any test file existed).
- Docker seam: `monkeypatch.setattr(deploy.procutil, "docker", fake)` —
  the exact seam named in the handoff (precedent:
  `tests/tests/test_ciu_deploy_actions.py:1368`).

### O2 / CIU-21 — in-container image revision (contract class: **2b**)

Also fully specified (exact injection site, exact append-not-assign
requirement, exact unconditional-vs-gated ruling, exact early-return guard
string). The real construction work was locating exactly where the map has to
be BUILT (engine.py, which already imports `procutil` and is docker-aware)
vs. where it's CONSUMED (composefile.py, which must gain no docker import) —
and getting the docstring-documented blast radius right (a stack with only an
`image_revisions` map now gets an overlay where it previously had none, so
`reset_service`'s `overlay_path.exists()` branch newly fires for it — noted
in both the SPEC addition and here, not silently absorbed).

- `src/ciu/composefile.py`: `generate_overlay` gained `image_revisions=`
  keyword; early-return guard gained the `and not image_revisions` conjunct
  (matched the handoff's quoted target string exactly); new unconditional
  injection block placed OUTSIDE the `governance.enabled` gate, using the
  same append-never-clobber pattern (`svc.setdefault("environment",
  []).append(...)`) governance's own KSM injection already established.
  Verified zero `docker`/`procutil`/`subprocess` imports added (pinned by a
  test that reads the module's own source).
- `src/ciu/engine.py`: new `_build_image_revisions(compose_yaml_text)`
  helper, called once at Step 15 immediately before `generate_overlay`; local
  `from . import deploy as deploy_mod` inside the function (avoids the
  module-level circular import — `deploy.py` already does `from . import
  engine` at import time).
- Required discriminator built and tested:
  governance-DISABLED + two services with DIFFERENT baked labels
  (`test_unconditional_with_governance_disabled_two_distinct_labels`) — the
  only shape that catches the "smuggle the map through the `governance` dict"
  false-PASS.
- `exempt_services` does NOT exempt the injection — tested directly
  (governed service gets cgroup_parent+CIU_IMAGE_REVISION, exempt service
  gets CIU_IMAGE_REVISION only).
- Append-vs-clobber tested alongside a REAL KSM `LD_PRELOAD` injection (both
  survive in one service's `environment` list).

### O4 / CIU-23 — namespaced data isolation (contract class: **2d** — real,
named design freedom within a falsifiable observable)

**The injectable provisioner IS the test seam**: `worktree.DataIsolationProvisioner`
(a `Protocol` with `provision(entity, profile) -> dsn` / `drop(entity,
profile) -> None`), injected via a new `provisioner=` keyword on both
`worktree.add` and `worktree.remove`. Every behavioural test
(naming/ordering/force/idempotency/collision) drives a `FakeProvisioner` test
double defined in `test_ciu_worktree.py`. `worktree.PostgresProvisioner`
(a thin `docker exec <container> psql` wrapper, no new dependency — mirrors
`provisioning.py`'s existing `pg:` probe idiom) ships as the real default
(`worktree._default_provisioner()`), unit-tested for command SHAPE only (its
own class, mocked `procutil.docker`) — never against a live database. Filed
**CIU-26** to own that deferred real-server proof, per the handoff's own
instruction.

Design choices made (this oracle's actual freedom):

- **Entity naming**: `f"ciu_{instance_id}"`, a pure function of `INSTANCE_ID`
  (read from the freshly-generated `ciu.env` via the existing
  `workspace_env.parse_workspace_env`), computed ONCE in `worktree.py` — never
  reimplemented per-provisioner (including the fake), so the naming rule has
  one owner. Verified with the REQUIRED negative-clause case: two SEPARATE
  temp git repos (different physical paths) both add a worktree named
  `"shared-name"` — asserted the two `provision()` calls received DIFFERENT
  entity names (`test_two_different_physical_clones_same_worktree_name_no_collision`).
- **`--data-isolation <profile>`**: `profile` is passed through opaquely to
  the provisioner (for `PostgresProvisioner`, the target container name,
  default `"postgres"` when empty) — kept minimal rather than inventing a new
  `[data_isolation.*]` global config table the oracle never asked for.
- **Connection identity → `ciu.env`**: `CIU_DATA_ISOLATION_ENTITY` /
  `_PROFILE` / `_DSN`, appended with the SAME credential-bearing /
  never-`env_passthrough` warning text in both the file itself and
  `docs/SPEC.md` §S16.2 (per the handoff's explicit "both" requirement).
- **`remove()` ordering**: `_drop_data_isolation_in` runs BEFORE `_clean_in`
  (new first step); no-ops cleanly when the worktree was never given
  `--data-isolation` (no `ciu.env`, or no `CIU_DATA_ISOLATION_ENTITY` in it).
- **Force semantics — the round-3 correction**: `_clean_in`'s own `force`
  path is silent (verified: `test_remove_failed_clean_force_proceeds_silently`
  asserts empty stdout); the data-isolation drop's force path is NOT — a
  masked failure prints `[WARN]`, names the entity, and states it is now the
  operator's problem
  (`test_force_masks_failed_drop_with_a_warning_naming_the_entity`).
- **Terminal-state / retry contract** — the REQUIRED case (drop SUCCEEDS ×
  `_clean_in` FAILS × `force=False`): tested end-to-end in
  `test_drop_success_then_clean_fail_force_false_aborts_and_retry_is_idempotent`
  — first call aborts with the entity already dropped and `ciu.env` (naming
  the now-dead DSN) intact; the SAME test then retries `remove()` and asserts
  the drop call re-runs (idempotent no-op against the FakeProvisioner's own
  "already absent" state) before `_clean_in` succeeds and the checkout is
  actually removed.

## Where each oracle's contract class landed vs. the dispatch note

The handoff called O1/O2 "2b (constrained implementation against a fully
specified target)" and O4 "2d (real design freedom within a falsifiable
observable)" — both held exactly as described. Nothing in any of the three
required inventing an externally-visible interface, default, or bound beyond
what O4 explicitly delegated (the provisioner Protocol's shape and the
`--data-isolation` profile's meaning).

## Scope note: one file edited beyond the literal `scope.touch` list

`tests/tests/test_ciu_provenance_and_ksm_toggle.py` is NOT in `scope.touch`
(only the four NEW test files are), but its existing `TestRunningProvenance`
class calls `deploy.verify_running_provenance` directly and asserted the OLD
raising contract (`pytest.raises(ValueError, ...)`, an `ignore_mismatch=`
kwarg, `capsys`-captured prose from inside the function). O1's mandated
signature change ("never raising internally... never bare `None`") is
incompatible with that contract by construction — this is not a case of
inventing a new interface, it is the DIRECT, fully-specified consequence of
the one O1 already mandates. The class went from TEN tests to NINE: nine
migrated to assert on the returned `ProvenanceResult`'s fields instead of a
raise/captured-prose, and one — `test_ignore_mismatch_downgrades_to_a_warning`
— was DELETED outright, not migrated, because the behaviour it asserted
(`verify_running_provenance` itself printing a warning on the ignore-mismatch
path) no longer exists on this function at all under O1's contract; the
equivalent assertion now belongs to, and lives in,
`test_ciu_provenance_json.py`'s CLI-layer coverage of `cli._provenance`.
Deleting an assertion is exactly the kind of thing a scope note has to
disclose explicitly rather than let a paraphrase like "updated" quietly
cover for — its disappearance from this file is directly connected to how
the "provenance OK" line dropping out of `cli._provenance`'s
`--ignore-mismatch` path (fixed after independent review; see the dedicated
section below) went unnoticed the first time: the one test that used to
exercise that exact combination was migrated away rather than re-homed
alongside the behaviour it covered. Left `TestKsmToggle`, `TestMemoryProfile`,
`TestExecWrapperEntrypoint`, `TestEntrypointDrift` in that file untouched
(they don't touch provenance). Treated as mechanical test maintenance for an
exactly-specified refactor, not a scope decision — flagged here rather than
silently folded in, per AUTHORING.md's "trust git state" review expectation.

## Gate — round 1 (after this package's full diff, committed; superseded —
see "Round 2" below for the post-review, actually-final numbers)

`env -u REPO_ROOT -u PHYSICAL_REPO_ROOT PYTHONPATH=src python3 -m pytest
tests -q -n auto`:

```
1827 passed, 8 warnings in 6.79s
```

(1749 at baseline; +78 net from four new test files and the extended
`TestRunningProvenance` class — zero failures, zero regressions.)

`PYTHONPATH=../nyxloom/src python3 -m nyxloom.coverage_gate --repo . --base
main --coverage-json coverage.json --source src/ciu` — the REAL,
changed-line-scoped gate this package is actually judged against:

```
diff-coverage OK: 172/172 changed executable lines covered (100.0% ≥ 100.0% floor)
```

One iteration was needed to get there: the first pass at 168/172 failed with
`diff-coverage FAIL: 5 changed line(s) are EXCLUDED from coverage by
'pragma: no cover'` — not a literal pragma I wrote, but coverage.py's own
DEFAULT `exclude_lines` regex for bare `...` Protocol-stub bodies
(`DataIsolationProvisioner.provision`/`.drop`), which the gate treats
identically to a hand-added dodge (GA5's own stated policy: any excluded
changed line fails unless `--allow-excluded`, a project-wide gate-argv
change out of this package's scope). Fixed by giving both stub bodies a
real, non-excluded body (`raise NotImplementedError(...)`) and adding two
tests that instantiate a trivial subclass and assert the refusal — real
coverage, not a workaround. See the second commit.

The blanket, whole-repo `run-ciu-tests.py --cov-fail-under=100` step
(the OTHER half of `[gates.tester-unified]`'s argv) still reports <100%
overall (98.00%, TOTAL 6019 stmts/125 miss) — entirely PRE-EXISTING gaps
this package does not touch (`ksm.py` 68%, scattered lines in `cli.py`/
`composefile.py`/`deploy.py`/`governance.py`, and `worktree.py`'s remaining
untouched S16 lines — `_generate_env_in`/`_clean_in` subprocess bodies and
three git-failure `raise` branches that require simulating a real git/
subprocess failure, none of them lines this package's diff modified).
Verified line-by-line against `git show main:...` that every one of these
was ALREADY in the baseline's missing list at its pre-shift line number —
none are new. This blanket check was already failing at the true baseline
(95.72%) before any code in this package changed; the two-step gate's
actually-authoritative half (`nyxloom.coverage_gate`, changed-line-scoped)
is the one reported above as passing.

## Round 2 — independent review fixes

Verdict: REJECT, two concrete defects. Both fixed; nothing else touched
(everything else, including all seven fixtures, the O2 governance-disabled
discriminator, the O4 collision test, and the KSM-toggle scope excursion,
was independently confirmed correct).

### Defect 1 (O4) — `PostgresProvisioner.provision()` could not actually work

Run live against a real Postgres, `psql -tAc "SELECT ... WHERE NOT EXISTS
(...) \gexec"` fails with a syntax error 100% of the time: `-c` cannot mix a
SQL statement with a `\gexec` meta-command in one argument. Every prior test
of this path only asserted `argv[:3] == ["exec", "postgres", "psql"]` — argv
shape, never the SQL content or how it was delivered — so the break was
invisible to the suite.

Fix, exactly as the review prescribed (feed the SQL on stdin, not `-c`;
never fall back to a bare `CREATE DATABASE`, which would break the
re-provisioning idempotency `DataIsolationProvisioner.provision`'s own
contract requires):

- `src/ciu/procutil.py`: `run_cmd` (and `docker`, which forwards `**kw`
  unchanged) gained a new `input: str | None = None` keyword, passed straight
  through to `subprocess.run`. Purely additive — `None` is identical to the
  parameter not existing, so every pre-existing caller (100+ call sites) is
  unaffected. This one file is outside the original `scope.touch`, but the
  review's own prescribed fix is not expressible without it: `run_cmd` had no
  way to pipe stdin to a child process at all before this.
- `src/ciu/worktree.py`: `PostgresProvisioner` gained `_psql_script(container,
  sql)`, running `docker exec -i <container> psql -U <user> -tA -f -` with
  the SQL passed as `input=`. `-i` is required — without it `docker exec`
  leaves stdin closed, so `-f -` would read EOF immediately: a *silent*
  no-op that creates nothing, which is arguably worse than the original
  syntax-error failure because it exits 0. `provision()` now calls
  `_psql_script`, not `_psql`. `drop()` is UNCHANGED — a single
  `DROP DATABASE IF EXISTS "<entity>"` has no meta-command to mix with `-c`,
  and the review already confirmed it idempotent and correct.
- `tests/tests/test_ciu_worktree.py`:
  `test_provision_uses_docker_exec_psql_against_the_named_profile` rewritten
  to capture `(cmd, kw)` instead of just `cmd`, and now asserts: `-i` present,
  no `-c` anywhere in argv, argv ends `-f -`, no argv token contains
  `gexec` (i.e. the meta-command is NOT in argv at all), and the actual SQL
  delivered via `kw["input"]` contains the entity name, `CREATE DATABASE`,
  `WHERE NOT EXISTS`, and `\gexec` — the exact shape the old test could not
  have caught a syntax-broken implementation with.

### Defect 2 (O1) — the "provenance OK" line silently dropped after `--ignore-mismatch`

O1's own text promises `cli._provenance` is "BYTE-IDENTICAL in [prose]
behaviour when `--json` is absent." The OLD CLI, on `--ignore-mismatch` over
a genuine mismatch, warned AND then fell through to print
`provenance OK — running containers match <rev>` (self-contradictory, but
that is what it did: the old `verify_running_provenance` warned-and-returned
normally on that path with no exception, so the old `cli._provenance`
resumed past its `try/except` and hit the unconditional `print("provenance
OK...")` at the bottom). The rewritten `cli._provenance` instead returned
immediately after the warning, silently dropping that second line — a real
behavioural change a script `grep`-ing stdout for "provenance OK" would see
as a changed verdict, and one the LOG and `docs/SPEC.md` §S17.3 both
(inaccurately, at the time) described as unchanged.

**Choice: (a) — restored the exact old behaviour**, rather than (b) keep the
new behaviour and rewrite the docs to disclose a deviation. Reasoning: O1's
byte-identical promise is explicit and was the whole basis on which the
fixture/grammar work was reviewed and accepted; "arguably better" (the
reviewer's own words for the new, non-contradictory behaviour) is a product
judgement call this package was never asked to make, and introducing it
silently — discovered only because a review happened to run the CLI live —
is a worse outcome than reproducing an admittedly odd old behaviour exactly.
If the self-contradictory "WARN then OK" output is worth fixing on its own
merits, that is a follow-up someone can decide on deliberately, not a
side-effect of an unrelated refactor.

- `src/ciu/cli.py`: the `mismatch` branch's `if opts.ignore_mismatch: warn(...);
  return 0` became `if not opts.ignore_mismatch: print(...); return 2` with
  no `return` in the `ignore_mismatch` case — it now falls through to the
  same trailing `print("provenance OK — ...")` that `verified-match` also
  reaches, exactly mirroring the old control flow (warn-and-fall-through vs.
  warn-and-return were never actually equivalent; only the former is
  byte-identical to the original).
- `tests/tests/test_ciu_provenance_json.py`:
  `test_mismatch_ignore_mismatch_downgrades_to_warning_exit_0` now asserts
  `"provenance OK — running containers match abc12345" in out` in addition to
  the `[WARN]` line, so this exact regression cannot silently recur.
- `docs/SPEC.md` §S17.3 needed NO correction: its claim of byte-identical
  behaviour is now actually true again (it was the CODE that drifted from
  the doc, not the doc that overclaimed).

### Minor items

- `src/ciu/composefile.py`'s module-docstring `Public API` index line for
  `generate_overlay` now lists `image_revisions` in its keyword-argument
  summary (it was added to the real signature in round 1 but the docstring
  index line was missed).
- This LOG's own "Scope note" section corrected: `TestRunningProvenance`
  went from TEN tests to NINE, not "seven" — nine migrated,
  `test_ignore_mismatch_downgrades_to_a_warning` deleted outright (see that
  section above for why, and its direct connection to Defect 2 above).

## Gate — round 2 (final, after the review fixes above, committed)

`env -u REPO_ROOT -u PHYSICAL_REPO_ROOT PYTHONPATH=src python3 -m pytest
tests -q -n auto`:

```
1827 passed, 8 warnings in 6.31s
```

(Same count as round 1 — this round modified two existing tests' assertions
rather than adding new ones; zero failures, zero regressions.)

`PYTHONPATH=../nyxloom/src python3 -m nyxloom.coverage_gate --repo . --base
main --coverage-json coverage.json --source src/ciu`:

```
diff-coverage OK: 176/176 changed executable lines covered (100.0% ≥ 100.0% floor)
```

176 vs round 1's 172 — the 4-line delta is `procutil.py`'s new `input`
parameter plus `_psql_script`'s real lines in `worktree.py`, all exercised
by the rewritten test. No exclusion-flag failure this time (no new `...`
stub bodies were added). Committed as `78e9cc44`; `git diff-tree
--no-commit-id --name-only -r 78e9cc44` confirms exactly the seven intended
files and nothing swept in from vbpub's known concurrent-committer race.

## Commits

1. `3a71328e` `ciu: CIU-20/21/23 — provenance verdict, in-container revision,
   worktree data isolation` — the full implementation + tests + docs.
2. `10f802ab` `ciu: fix DataIsolationProvisioner stub bodies to avoid
   coverage-gate exclusion` — the coverage-gate fix from round 1.
3. `78e9cc44` `ciu: round 2 — fix PostgresProvisioner stdin delivery and
   restore byte-identical --ignore-mismatch prose` — the two review defects
   + minor items above.

## Nothing was BLOCKED

None of the three `escalate_if` triggers fired: O2's map reached
`generate_overlay` as data with no docker import added to `composefile.py`;
O4's fake-provisioner seam correctly discriminates a collision-safe
implementation from a name-keyed one (the required two-clone test actually
exercises this); O1's `not-verified-no-evidence`/`containers: null` grammar
was producible entirely within `deploy.py`. No externally-visible interface,
default, or bound needed inventing beyond what O4 explicitly delegated (the
provisioner Protocol's shape, and the `--data-isolation` profile's meaning).
