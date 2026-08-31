# Changelog — run-gate

All notable changes to `run-gate.py` and its estate adoption are recorded
here. The in-file `__revision__` is the drift marker: estate sweeps compare
it, and any consumer holding a COPY (not a symlink) must re-copy when it
moves. Normative behavior lives in SPEC.md; entry-by-entry rationale lives in
KNOWN_ISSUES_TODO_BACKLOG.md and git history.

## [Unreleased]
<!-- hand-written ahead of release; cmru's generator will produce the real dated entry for this range at release time -->

### Changed
- **Adversarial-review round on the P02 bundle (rev 29).** The `command -v`
  fitness probe is now BATCHED per environment over the union of every lane's
  tools — it was one container per lane, so SPEC `R-30`'s "at most one probe
  container per assay environment" was quantitatively false (measured: 4
  containers for 3 lanes on one environment). A test now owns that count.
  And the three places still promising that `--dry-run` starts nothing —
  SPEC `R-28`, `usage()`, the argparse help — now say what it does start: an
  assay lane's read-only inventory probe, which is what resolves the base the
  printed plan must show. `--dry-run`'s real promise is that **no judged lane
  starts**. Same correction applied to `doctor` in CONSUMERS, README and
  `cmd_doctor`'s own docstring, which still called itself "pure
  recomposition".

### Added
- **RG-27 — lane invocation history + the `history` query verb (rev 30).**
  Lane cost was informal: an operator noticed a lane "took a while" and the
  observation died with the terminal scrollback, so a provisional-merge /
  defer-heavy-rigor policy could only ever be a guess (dstdns declined to
  adopt one blind on 2026-08-25, D-204). run-gate is the layer that actually
  starts each lane, so it now records what it already sees. Two slots per
  lane, with deliberately different contracts: **`latest`** holds the most
  recent invocation *whatever happened to it* (pass, fail, tool error,
  Ctrl-C, dirty tree, mid-rebase) for diagnostics, and **`history`** is a
  curated trend series keyed by (lane, commit), bounded to the last
  `[history] keep` commits (default 10). A run joins history only if it
  completed with its own exit status, on a clean tree, with no git operation
  in flight, at a resolvable commit — every exclusion records its reason,
  and "could not determine" excludes rather than assuming clean. A
  **completed fail DOES join** (its duration is real cost) but is stored with
  its outcome and reported as a SPLIT statistic, because a red lane
  short-circuits; the headline statistic is the **median**, never the mean,
  since one slow outlier reading as the lane's permanent cost is the exact
  trap this entry named. Query it with `./run-gate.py history [LANE]
  [--json]`. Storage is `<project>/.run-gate/history.json` **inside the
  judged tree** — per (worktree × project), which is the concurrency answer:
  parallel worktree gates address different files and never contend, while
  two lanes of one project serialize on a sibling `.run-gate/history.lock`
  (sibling because the store is replaced by rename) with a bounded wait —
  telemetry must never hang a gate. The store MUST be git-ignored and that is
  CHECKED, not documented: run-gate refuses to write an un-ignored store and
  prints the remedy rather than dirtying the tree for the next lane's
  clean-tree check. Recording is best-effort throughout — every failure is
  one warning line and the lane's exit status is untouched. run-gate
  measures; it decides no rigor/defer policy. SPEC `R-36` (+`R-01`/`R-06`/
  `R-08` amendments); README "Environment mechanics"; CONSUMERS "What each
  lane costs". Retriage of ciu **CIU-55** (superseded pointer there).

  Round-2 review fixes folded in before merge: `history` honors `--worktree`
  on the READ side too (it previously reported the invoking checkout's store
  no matter which tree was asked about — silently), and refuses an override
  that names no git work tree rather than answering "no data"; flushing a
  record is now at-most-once, so a Ctrl-C landing inside the telemetry write
  surfaces as the KeyboardInterrupt instead of a `KeyError` traceback from
  the second flush; and `--json`, which was accepted and ignored everywhere
  but `history`, is now refused by name elsewhere (`--list --json` used to
  hand a TSV to a caller asking for JSON).

  > **BREAKING (load-time): a lane named `history`.** `history` is a CLI verb
  > now, so it joins `doctor`/`validate-pointers` as a RESERVED lane name and
  > `[lanes.history]` is refused when the config loads. No project in this
  > estate declares one (all ten `run-gate.toml` files checked); a
  > copied-script consumer repo that does must rename the lane — it was
  > already unreachable, since the verb would have won — before taking rev 30.
  > Migration is one line, and it fails loudly at load, never silently.
- **RG-26 — `--base REF` passthrough to `assay run --request-base`
  (rev 28).** assay 3.0.0's `judge.base_source = "request"` (B019) had been
  unusable from every consumer: such a lane refuses without
  `--request-base`, and run-gate had no flag to supply one. `run-gate <lane>
  --base REF` now reaches a delegating assay lane as `--request-base REF`;
  omitted, the ref is the judged worktree's `git merge-base HEAD
  @{upstream}`, and a tree with no upstream refuses (exit 2) rather than
  guessing. Delegation is DERIVED from `assay lanes --json` (RG-25's shared
  probe) — **no new `run-gate.toml` key**, so the fact keeps exactly one
  spelling; the cost is one short read-only inventory probe per assay lane
  invocation. Conjunction lanes propagate it through a `{base}` token in
  their own argv, mirroring RG-1's `{worktree}` rule. Every non-delegating
  case refuses by name: an assay lane with a different `base_source`, a
  command lane with no `{base}` token, or a judge too old to answer while
  `--base` is given (naming assay 3.2.0/B044). Without `--base` an older
  judge behaves exactly as before. `--dry-run` and the R-05 disclosure show
  the resolved ref and the appended flag. SPEC `R-35`; CONSUMERS "Lanes that
  take their comparison base from the gate"; absorbs the ciu v8 proposal's
  §4.11 N12.
- **RG-28 — an assay lane on the built-in `host` environment no longer
  raises `KeyError('argv')` (rev 28).** Found while implementing RG-26: the
  validator accepts `kind = "assay"` with `environment = "host"`, but
  `run_host_lane` indexed `lane["argv"]` unconditionally — a traceback for a
  legal config, which `R-04` calls a defect. It now builds the same assay
  inner the two container runners do. SPEC `R-19`.
- **RG-25 — assay-lane toolchain fitness in `doctor`/`--check-env`
  (rev 27).** For every `kind = "assay"` lane, run-gate asks the JUDGE what
  the lane needs (`<assay_command> lanes --json --file assay.toml`, assay
  ≥ 3.2.0 / B044) INSIDE the lane's environment and checks that environment
  for it — it still never parses `assay.toml`. `build_env_probe_argv()` is
  the single in-environment probe builder, reusing `resolve_container_name()`
  and `physical_path()`/`dual_mount_flags()`, so no second `docker
  run`/`docker exec` argv shape exists. Tools checked = `external_tools` ∪
  `argv0` (read from the inventory) ∪ the `language` toolchain (`javascript`
  → node, npm; `go` → go — a table that exists only because assay 3.2.0
  reports `external_tools: []` for every shipped adapter and states the
  language fact in prose; an unmapped language is reported with a caveat,
  never as "nothing needed"). `[FAIL]` names lane, tool and environment, or
  an `assay_lane` the judge does not declare; every "could not determine" is
  `[SKIP]` with its reason, so an assay older than B044 can never turn a
  healthy project red. `doctor` counts SKIPs in its summary; `--check-env`
  exits 2 on a toolchain FAIL while its env-drift half stays advisory.
  **`doctor` and `--check-env` now START CONTAINERS** for this check —
  fitness can only be observed, not read. They are short-lived and read-only,
  judge nothing, write nothing, and are bounded at one inventory probe per
  (environment, `assay_command`) plus **one batched `command -v` probe per
  environment** (not per lane); a project with no assay lane starts none.
  SPEC `R-34`, `R-01`, `R-30`; CONSUMERS `kind = "assay"` section; README.
- **RG-21 — `doctor` names the linked-worktree host-lane git view (rev 26).**
  A linked worktree's `.git` is a FILE pointing at an absolute gitdir under
  the main checkout. A host lane that delegates to a harness bind-mounting
  only the judged tree by host path (srdm's covergate) therefore fails with
  `not a git repository: <gitdir>` mid-run. run-gate is not the defect —
  `{worktree}` forwarding and exit-status passthrough are correct, and its own
  container lanes dual-mount the repo root (`R-23`) — so this is a `[WARN]`
  naming the worktree, the gitdir, the exact symptom and three remedies, not a
  refusal, and it does not move doctor's exit code. Scoped to projects that
  declare a host lane (the only kind that can reach such a harness); with a
  host lane and a plain checkout the same check records `[OK]` so a reader can
  tell it ran. SPEC `R-30a`; CONSUMERS "Host lanes that delegate to a
  host-path-mounting harness" (three pasteable harness-side fixes).

### Fixed
- **RG-23 — env forwarding: breaking change documented + the drift sweep
  widened (rev 25).** The exec-mode forwarding loop's hardcoded
  `MOCK_MODE`/`RUN_LIVE_TESTS` pair was replaced by declarative `forward_env`
  with no migration pass and no note; consumers relying on either name
  silently stopped receiving it, and the symptom is a false GREEN (a suite
  that skips its live tests on the flag's absence exits 0 having run none).
  The change is now stated as breaking with its migration in SPEC `R-24a`,
  CONSUMERS ("BREAKING CHANGE — migrate if you use `mode = "exec"`") and the
  README. The implicit names do NOT return. `--check-env` is now AST-based
  (`R-24b`): it additionally sees a literal handed to the project's own
  env-reader helper — `_env_flag_enabled("RUN_LIVE_TESTS")` whose body does
  `os.getenv(name, "")`, exactly the shape the old line regex could not see —
  plus `setdefault`/`pop`/`"X" in os.environ`, with bound-method parameter
  offsets accounted for. It stays advisory (exit 0). An unparseable file is
  reported by name and falls back to the line regex rather than being counted
  as clean. Estate audit: no vbpub project declares `mode = "exec"` or
  `forward_env`, so the confirmed blast radius is dstdns alone (its own
  repo's fix). A regression test keeps that audit from silently regressing.
- **RG-24 — exec-mode container resolution is worktree-scoped (rev 24).**
  `resolve_container_name()` now prefers the JUDGED worktree's own
  `ciu.global.toml` over the shared-`.git`-owning repo's, falling back to the
  repo's unchanged when the worktree has none. A multi-instance ("Mode-B")
  worktree with its own rendered config, network and runner used to have its
  lane exec'd into the MAIN checkout's container — a partial, believable
  failure (the inner `cd` still found the right files; only the container's
  baked network/env were wrong). The pre-execution disclosure now names the
  resolution scope (`judged worktree:` / `repo:`) and the file used, and a
  missing-config refusal names both candidate paths. SPEC `R-14a`;
  CONSUMERS "Python app estate with its own runner".
- **RG-30 — `doctor`/`--check-env` honor `--worktree` (rev 31).** Both
  verbs passed `None` to `resolve_repo_and_worktree` instead of the
  caller's `--worktree`, so `doctor --worktree B` silently reported the
  INVOKING tree's answers under B's name — including the `R-30a`
  worktree-specific host-lane git-view WARN, exactly the kind of per-tree
  answer that legitimately differs between trees. `history`'s own
  `--worktree` fix (RG-27 B1) closed the identical read-scope hazard for
  that verb; this closes the last remaining instance estate-wide, with the
  same disclosure discipline (the report NAMES the tree it describes). New
  shared `resolve_worktree_scope()` resolves and validates the override
  (a bad one is refused by name — never silently answers for a
  nonexistent tree). `doctor`'s per-tree checks (git identity, `R-30a`,
  mountinfo) resolve it inside the SAME try/except the "git" check already
  wraps every failure in, so a bad override becomes a `[FAIL] git` record
  — never a false `[OK]` on the RG-21 check that follows it. The shared
  assay-toolchain probe (`assay_toolchain_findings`, used by both `doctor`
  check 5 and `--check-env`) relocates BOTH the `repo`/`worktree` it mounts
  AND the `cd` target it runs inside — mounting the selected tree while
  `cd`ing into the invoking checkout's path would not probe the selected
  tree, it would run against a directory the probe container never
  mounted. `--check-env`'s own env-drift scan follows the override for its
  Python-source scan too, and — having no per-check ledger to degrade
  into — refuses upfront on a bad `--worktree` rather than scanning
  nothing under the wrong tree's name. SPEC `R-37` (`R-37a`/`R-37b`/
  `R-37c`); README "Effective tree"; CONSUMERS "`--worktree` redirects the
  whole report, not just the run".

<!-- Post-release housekeeping (assay CHANGES.md precedent): this block is
     CLEARED immediately after a release. cmru generates the dated entry
     below from the commit range but does NOT clear this hand-written block
     itself — leaving content here would republish shipped work as
     "unreleased" on the next cycle. -->

<!-- cmru: release history -->

## [23.2.1] - 2026-08-31
<!-- cmru: generated -->
<!-- cmru: source-end=fe09688572dc7d744ba81b6b471eb4908599ffa6 -->

### Fixed
- fix(run-gate): RG-30 -- doctor/--check-env honor --worktree (rev 31) (89ca96ba)

### Changed
- backlog(run-gate): file RG-31 -- assay_toolchain_findings bypasses RG-30's validated worktree resolution (da535655)

### Documentation
- docs(run-gate): RG-30 FIXED + run-gate-P04 LOG/REPORT (60512539)

## [23.2.0] - 2026-08-31
<!-- cmru: generated -->
<!-- cmru: source-end=7c47a70710eac58697641d9ee2444a1ea0db8af3 -->

### Added
- feat(run-gate): RG-27 -- lane invocation history + the `history` query verb (rev 30) (1687b60d)

### Fixed
- fix(run-gate): RG-27 round-2 review -- B1 history read scope, B2 at-most-once flush, S1/S2 (0e6d0ea4)
- fix(run-gate): RG-27 -- record inline in main(), and cover the wiring in-process (afcdb39f)

### Changed
- backlog(run-gate): file RG-30 -- doctor/--check-env ignore --worktree, same pattern RG-27 just closed for history (5df35ce4)

### Documentation
- docs(run-gate): RG-27 -- P03 LOG/REPORT carry the real round-2 gate verdict (56d98572)
- docs(run-gate): RG-27 FIXED + run-gate-P03 LOG/REPORT (dbaccfe1)

### Testing
- test(run-gate): RG-27 -- in-process cover for the B1/S1 main() dispatch branches (dc3b1490)

## [23.1.0] - 2026-08-31
<!-- cmru: generated -->
<!-- cmru: source-end=1f47601c1b69a3503c4d94caca3ca90c373f8e0b -->

### Added
- feat(run-gate): RG-26 -- --base REF reaches a delegating assay lane as --request-base (7b30bc49)
- feat(run-gate): RG-25 -- doctor/--check-env preflight assay-lane toolchain fitness (9a403da3)
- feat(run-gate): RG-21 -- doctor names the linked-worktree host-lane git view (9adf11fc)

### Fixed
- fix(cmru,run-gate): RG-29 -- cmru/run-gate.toml's assay pin still named the vanished 2.2.0 sidecar (0ad5372d)
- fix(run-gate): P02 review round -- batch the fitness probe (B2), tell the truth about what dry-run and doctor start (B1/B3) (2f266885)
- fix(run-gate): RG-23 -- declare the env-forward breaking change and widen the drift sweep (c55f5748)
- fix(run-gate): RG-24 -- exec-mode container names resolve from the judged worktree (bd1a3f85)

### Changed
- backlog(ciu,run-gate): file CIU-75 -- backport v8 F2 identity source (breaking, ciu 7.6); retriage CIU-55 -> RG-27 -- gate invocation history + query verb (a78a0046)
- backlog(run-gate,ciu,assay): RG-25/RG-26 -- backport ciu CIU-72 (b)/(c) to the current gate; CIU-73 needs no code (b2884e76)
- backlog(ciu,run-gate): file CIU-71 -- build-context project-directory gap; RG-24 -- exec-mode container resolution is repo-scoped not worktree-scoped (92ae1917)

### Documentation
- docs(run-gate): usage() names the doctor/--check-env checks this bundle added (08783d09)
- docs(run-gate): P02 bundle LOG + REPORT (RG-21/23/24/25/26) (e8a6a34b)
- docs(assay): file the 2.1.0->2.3.0 review-gap audit and its backlog (B030-B034, RG-23) (142143a4)

## [23.0.0] - 2026-08-24
<!-- cmru: generated -->
<!-- cmru: source-end=f8178d9b0b821405f4f0fb8831d710056352193f -->

### Added
- feat(run-gate): adopt estate release orchestration with a diff-coverage floor (b6ec5d6a)

### Fixed
- fix(run-gate): resolve adversarial-review findings on the release-adoption program (db173082)
- fix(run-gate): run the selftest lane in host mode, not tester-unified (ca023b78)
- fix(run-gate): scope the selftest diff-coverage floor to run-gate.py alone (1c1d2fda)
- fix(run-gate): RG-22 — safe.directory global-config write survives pre-existing entries (9ad6388f)

### Changed
- backlog(run-gate): RG-22 — safe.directory overwrite fails when global config has multiple entries (2174d22e)

## [22] - 2026-08-24 — RG-sweep program complete

Backlog entries RG-1..RG-20 implemented (RG-18 excepted — dstdns-side scope,
see its backlog body), adversarially reviewed by two independent fresh
reviewers (findings fixed in rev 21–22 commits), then verified end-to-end.
Merged to main as `vbpub@91df32ee` (--no-ff, no conflicts).

### Rev map (one backlog item each unless noted)
- rev 4 RG-15 — assay lanes execute in the selected worktree
- rev 5 RG-11 — reserved exit codes: 2 config/refusal, 3 infrastructure
- rev 6 RG-4 — pins.version actually checked (whole-token grammar)
- rev 7 RG-16 — central configs may define shared lanes
- rev 8 RG-3 — dual-mount degeneracy fix
- rev 9 RG-5 — `{worktree}` substitution hardened (quoting/injection)
- rev 10 RG-6 — exec-mode refusal names the project-agnostic remedy
- rev 11 RG-17/RG-19 — required_env preflight + forwarding log + --check-env
- rev 12 RG-1 — conjunction override guard (`--worktree`/`--allow-dirty`)
- rev 13 RG-12 — failing-container evidence preserved (mode 0600)
- rev 14 RG-10 — artifacts key + unconditional verdict/evidence disclosure
- rev 15 RG-2 — `validate-pointers` verb + estate linkage meta-tests
- rev 16 RG-8 — `--dry-run` plan rehearsal on all three runners
- rev 17 RG-20 — resource-aware admission (slice budget from cgroupfs,
  shared-infra locks, lane `resources` key)
- rev 18 RG-9 — `doctor` preflight verb
- rev 19 RG-14 — wheel as second artifact (version derived from __revision__)
- rev 20 RG-13 — docs sweep + estate adoption retro ×9 projects
- rev 21–22 — adversarial-review hardening (SPEC header Rev 5 lists the
  rule-level deltas R-02..R-32)

### Verification ledger (2026-08-24, phase B/C)
- Suite: `pytest tests -q` → **205 passed** on the branch pre-merge and again
  on shared main post-merge.
- `--list`: all 9 consuming projects, rc=0.
- Every declared lane executed end-to-end (16 total): mdt/smoke, plesk/smoke,
  topos/py-compile, topos/topos-suite (2922 passed, diff-coverage 100% floor),
  pwmcp/tests (8 passed), nyxloom/tester-unified (diff-coverage 100% floor),
  srdm/unit, srdm/e2e (full systemd suite), ciu/ciu, cmru/{assay, coverage
  (1675 passed, 100%), canary, mutation, gate}.
- Assay judgment read SEPARATELY from each verdict artifact (LESSONS L4):
  assay claims R0–R3 PASS; ciu claims R0–R1 PASS (its declared rigor);
  cmru claim R0 PASS.
- cmru/gate conjunction rc=0 with every sub-invocation carrying explicit
  `--worktree {worktree}` — RG-1's guard exercised against a real daemon.
- cmru/mutation took its honest self-skip path (no changed src since
  cmru-v5.0.0), proving the KI-18 base-resolution contract.
- srdm/coverage **fails from a linked worktree** (covergate git plumbing hits
  the main checkout's gitdir, which gate.sh's single-subtree mount does not
  carry) and **passes on main** — environment topology, not a run-gate defect;
  run-gate's own exit-status passthrough and clean-tree refusal behaved
  correctly throughout. Filed as RG-21 with candidate directions.
- Merge held one foreign uncommitted edit (ciu/docs CIU-V7 proposal notes)
  via tagged stash round-trip; restored untouched afterwards.

### Re-copy obligation (drift marker = 22)
All nine in-repo consumers invoke `../run-gate-project/run-gate.py` through a
SYMLINK — zero re-copies owed inside vbpub. Any out-of-repo COPY of an older
revision (none known today) must re-copy before its next gate run.
