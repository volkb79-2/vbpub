# Changelog — run-gate

All notable changes to `run-gate.py` and its estate adoption are recorded
here. The in-file `__revision__` is the drift marker: estate sweeps compare
it, and any consumer holding a COPY (not a symlink) must re-copy when it
moves. Normative behavior lives in SPEC.md; entry-by-entry rationale lives in
KNOWN_ISSUES_TODO_BACKLOG.md and git history.

## [Unreleased]
<!-- hand-written ahead of release; cmru's generator will produce the real dated entry for this range at release time -->

### BREAKING
- **RG-32 — `[lanes.*.pins.*].budget` is refused at load (rev 34, SPEC
  `R-08a`).** The key was silently inert: `run-gate` never read it, and the
  value that actually governs a `kind = "assay"` lane's run time is the
  TARGET `assay.toml`'s own `[lanes.<assay_lane>] budget`. It sat one
  nesting level below a REAL, load-bearing lane-level `budget` that looks
  identical when read — an `R-04`-class defect, measured three times in one
  dstdns session (two independent review agents and the controller each read
  `pins.assay.budget = "90m"` as the bound of a lane whose `assay.toml` said
  `120m`; the numbers had drifted with nothing able to notice).
  - **Migration (one deletion per lane):** delete the `budget` line from
    every `[lanes.<name>.pins.<pin>]` table in your `run-gate.toml`. Nothing
    replaces it — the lane's real budget already lives in your `assay.toml`
    `[lanes.<assay_lane>]`, and run-gate's own lane-level `budget` (one
    level up, no `pins` in the path) is unchanged and still advisory. Until
    the key is removed, the lane refuses at load with a message naming the
    pin, the assay.toml table that owns the value, and the remedy.
  - `pins` tables now validate their keys at all: `sha256` and `version` are
    the only ones accepted, and any other is refused the way an unrecognized
    lane key already was. A pin table that accepted anything is how `budget`
    came to live there.
  - **Known affected consumer:** dstdns (`sql-mutation`,
    `assay-p129-enumeration-cursor`). No vbpub-estate `run-gate.toml`
    declares the key.

### Added
- **RG-34 — `doctor` names an unprefixed script path in a container command
  lane (rev 34, SPEC `R-30b`).** One `[WARN]` per `kind = "command"` lane on
  a non-host environment whose `argv[0]` is a relative path containing `/`
  and not starting with `{worktree}`, naming the lane, the element, the fix
  (`"{worktree}/<path>"`) and the mechanism: a container that mounts only the
  judged worktree (a Mode-B instance's own runner) has nothing at the bare
  repo root the `--workdir` names, so the argv dies with `No such file or
  directory` there while working under a full-repo mount. Measured on dstdns
  P152 — `argv = ["scripts/schema-gate.sh", "{worktree}"]`, the argument
  templated and the script path not, `lane 'schema' exit 127`, 100%
  reproducible. A warning, never a refusal, and it does not change doctor's
  exit code: the same argv is correct under a full-repo mount, and run-gate
  cannot see statically which mount a lane will get. run-gate does not
  rewrite argv — the fix is one edit in the consumer's config. No
  vbpub-estate lane trips it (swept at release).

### Fixed
- **RG-35 — a lane's container is found again after its client dies (rev 34,
  SPEC `R-39`).** A container lane runs detached and is removed by an
  explicit `docker rm -f` in a `finally`. When the CLIENT dies — SIGKILL, a
  devcontainer restart, a harness that reaps a background command (measured
  2026-09-02: the Claude harness killed a detached `cmru release` after 33 s
  and its inner gate container ran to completion unobserved) — that `finally`
  never runs, and until now nothing on disk named the container: exit status,
  evidence and history were lost and the NEXT invocation started a SECOND
  container for the same lane on the same commit.
  - A successful `docker run -d` now writes
    `<project>/.run-gate/inflight/<lane>.json` — container name and id,
    `started_at`, the judged commit, worktree, project dir, the lane's
    verdict/progress paths and `__revision__ ` — under the same store
    discipline the history file already has (per judged worktree × project ×
    lane, git-ignore CHECKED over record + lock + temp, sibling lock +
    atomic rename).
  - A later invocation of the same lane **re-attaches** to a running
    container (`docker logs -f --since <started_at>` + `docker wait`),
    **collects** an exited one and finishes exactly as an attached run
    would, **reports and clears** one the host lost (recording that run as
    `aborted`, never as a pass) and runs fresh, and **refuses (exit 2)** when
    the record judges a different commit — naming both commits and the new
    `--fresh` flag, which removes the named container first and runs anew.
    Every branch is disclosed by name.
  - History records a re-attached or collected run ONCE, with the duration
    measured from the container's start rather than from the seconds the
    client was attached.
  - **New flag `--fresh`** (run path, ephemeral container lanes only —
    refused by name on host lanes, exec lanes and every verb). `--dry-run`
    discloses an inflight record and changes nothing.
  - **Consumer note:** the record lives in the `.run-gate/` directory
    adopters already git-ignore for `history.json`; no config change is
    needed. A project that has NOT ignored it gets one warning per run
    saying re-attach is off, and the lane still runs.

<!-- Post-release housekeeping (assay CHANGES.md precedent): this block is
     CLEARED immediately after a release. cmru generates the dated entry
     below from the commit range but does NOT clear this hand-written block
     itself — leaving content here would republish shipped work as
     "unreleased" on the next cycle. Recurred on run-gate-project as of
     2026-09-01 (rev 24/25/27/28/29/30 content had survived three releases,
     23.1.0/23.2.0/23.2.1; then again the SAME cycle, since cutting
     23.2.2 to ship RG-31's own fix — confirming this really is a manual
     per-release step, not something cmru will ever do for you); cleared
     both times. -->

<!-- cmru: release history -->

## [23.3.0] - 2026-09-02
<!-- cmru: generated -->
<!-- cmru: source-end=b36c6925d1d8ff8bf6fd4b74de8a5bd9f3855dbe -->

### Fixed
- fix(run-gate): RG-33 -- every assay lane runs with --resume and --progress, judge floor refused by name (rev 33, R-38) (0a4862db)
  - Hand-authored detail (folded in post-release, the standing rule): every
    `kind = "assay"` lane is now invoked with `--resume --progress
    .assay/progress-<assay_lane>.jsonl`, unconditionally, on every runner.
    Measured cause: dstdns's `sql-mutation` lane re-tested the first of four
    target files from mutant #1 on three budget-capped retries because the
    argv never carried `--resume`. Both flags are no-ops on a lane without R2
    (assay's own contract), so R0/R1 lanes are unchanged; an R2 lane resumes
    from `.assay/mutation-state/` on retry and streams progress beside its
    verdict under the git-ignored `.assay/`.
  - **Consumer note (breaking for very old pins):** the flags need a judge
    that knows them — assay **>= 2.4.1**. A pin declaring an older `version`
    is refused at argv construction by name (lane, pin, version, floor,
    remedy); a pin without a declared version reaches the judge and fails
    loudly there. Re-pin before adopting rev 33 (in this estate only `cmru`
    was below the floor, at 2.3.0; re-pinned to 4.1.0 in `b36c6925`).

### Documentation
- docs(run-gate): RG-34 — schema lane argv doesn't template {worktree} into its own script path (eeda67ce)
- docs(run-gate): RG-33 — assay mutation lanes never pass --resume, retries restart from scratch (a04c95c2)
- docs(run-gate): RG-32 — pins.assay.budget is silently inert, real value lives in the consumer's assay.toml (8be4c6b9)

## [23.2.2] - 2026-09-01
<!-- cmru: generated -->
<!-- cmru: source-end=fad40555fb0f8125315f3811a8dcd95bea6db9c3 -->

### Fixed
- fix(run-gate): RG-31 -- assay_toolchain_findings routes --worktree through resolve_worktree_scope (rev 32) (0efd062e)

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
