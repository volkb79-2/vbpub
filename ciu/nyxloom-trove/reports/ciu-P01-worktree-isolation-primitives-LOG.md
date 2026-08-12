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
the one O1 already mandates. Updated the seven `TestRunningProvenance` tests
to assert on the returned `ProvenanceResult`'s fields instead of a raise/
captured-prose; left `TestKsmToggle`, `TestMemoryProfile`,
`TestExecWrapperEntrypoint`, `TestEntrypointDrift` in that file untouched
(they don't touch provenance). Treated as mechanical test maintenance for an
exactly-specified refactor, not a scope decision — flagged here rather than
silently folded in, per AUTHORING.md's "trust git state" review expectation.

## Gate — final run (after this package's full diff)

See below — pasted after `git commit`, since `nyxloom.coverage_gate` diffs
against committed `HEAD`, not the working tree.
