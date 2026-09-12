# run-gate-WAVE-RG55-P2 — adversarial review handoff (RG-55 wave, package P2)

**Reviewer:** FRESH session (Opus, xhigh), never a fork of the implementer or
the controller. **Your job is to BREAK this before merge.** 3-round cap;
fix-verification rounds resume YOUR session (the controller messages you the
repair commit). Records: `run-gate-project/nyxloom-trove/reports/
run-gate-WAVE-RG55-P2-REVIEW-round<n>.md`.

Branch `rg55-run-gate-client`, worktree `/workspaces/vbpub/.worktrees/rg55-run-gate-client`,
project dir `run-gate-project/` (`run_gate.py` → `run-gate.py`, one inode).
Base: `main` at the P0 freeze (`63b928da`). The tip hash is in the dispatch
message. Review the FULL diff `main...<tip>` — every file, every type
(TOML, SPEC, README, CONSUMERS, CHANGES, backlog, tests, fixtures, vendored
pyz + sha256, scripts).

## Phase 1 — BLIND (before any LOG/REPORT/BRIEF)

Read, in this order: the plan of record
(`run-gate-project/nyxloom-trove/WAVE-PLAN-2026-09-12-rg55-profiling.md`
§2 D-1..D-16, §3, §4, §5-P2, §10), the contract
(`run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md` §1–§7) and
`fixtures/rg55/README.md`, the controller log's Rulings section
(`reports/run-gate-WAVE-RG55-CONTROLLER-LOG.md` — RW-5, RW-6, RW-8..RW-12,
RW-17, RW-18 bind this package), the implementer handoff
(`run-gate-WAVE-RG55-P2-HANDOFF.md`), `SPEC.md` R-04, R-08, R-29, R-36,
R-39, R-40, R-41 as they were on `main` (git show main:…) and as changed,
then the diff itself. Form your own view BEFORE the implementer's narrative.
Run your OWN sweeps.

## Phase 2 — RECONCILE against the implementer's claims

Read `run-gate-WAVE-RG55-P2-LOG.md`, `-REPORT.md`, every `-BRIEF-n.md`;
check each claim; list claims you could not verify.

## Attack surface (minimum; add your own)

1. **R-36h / R-04: profiling must never change a verdict or raise.** Hunt
   every path: daemon hang (are the contract §1.5 timeouts really passed to
   `subprocess.run`?), garbage stdout, exit 2/3, `contract: 2`, `docker
   inspect` failure, `docker exec` against an exited container (the bug the
   live probe found — `sample_final`), KeyboardInterrupt during `stop`
   (at-most-once flush, exit status = the signal's), the stall path
   (`stop` BEFORE `docker rm -f` — verify in the shim's argv log),
   follower/`promote_follower`/collected-after-exit, `--fresh`, `--dry-run`
   (starts nothing, prints the plan), `RUN_GATE_PROFILE=off` (RW-17). Plant
   an exception inside `ProfilerClient` and `BasicSampler` and watch the
   lane's exit code stay the lane's.
2. **RW-12 tick shape.** With a basic sampler active the wait tick is 5 s;
   the progress/log-stream watch must poll only every ≥ 30 s (RG-36/RG-41
   stall semantics unchanged): construct a stall and confirm the stall time
   is unchanged vs `main`; no thread introduced for sampling.
3. **§7 arithmetic on the basic path** vs the goldens
   (`summary-basic-v1.json`); the RW-11 12-file list; `sample_final`
   semantics (a failed final read drops, last successful sample stays) —
   is `samples` honest? Ported parsers identical in behaviour to
   `scripts/cgroup-profiler/lib/util.py` (absent → None, `max` → None).
   Plant 6+ mutants in `ResourceAccumulator` and the parsers — every one
   caught; record which test caught each.
4. **Token and argv.** `-e RUN_GATE_PROFILE_SESSION=<token>` on BOTH
   `docker run` and `docker exec`, in the `forward_env` position; the token
   is per invocation (never reused across lanes); no token when profiling is
   off; conjunction lanes carry nothing of their own; the inflight record
   fields; the token never appears in evidence files or logs beyond the
   record.
5. **Exec flow.** `run_exec_lane` Popen rewrite: exit code, inherited
   stdio, R-41 mutex, ≥ 2 samples in a 12 s exec (the red-first test — is
   it real? revert the rewrite and watch it fail), `scope:
   container-shared`, baseline subtraction.
6. **History schema 2.** Migration of a schema-1 store (old entries intact);
   `stats` for the five series (median, absent excluded, `count`);
   `history --json`/table; RG-27's two traps re-proven for the new series;
   private keys stripped.
7. **`footprint` (R-44).** Derivation from PASS + eligible entries only;
   omitted lanes; refusal when nothing eligible; atomic write, sorted keys,
   trailing newline; reserved name; `doctor` divergence math (tolerance
   applied to the median peak, both directions), staleness, INFO when
   absent; `meta.expected` fed into `start`; the `| manifest {n} MiB` tail.
8. **RG-48.** `--cpus` argv, validation, lane-over-environment precedence,
   exec naming-only warning, doctor `-n auto`/`--workers auto` detection
   (false positives on unrelated `-n`?).
9. **RG-53 / RW-5.** `missing_branches` semantics: an arc `[a, b]` with `a`
   changed and untaken counts as uncovered — verify with a crafted coverage
   JSON; partial branches; `branches_total/missed` scoped to changed lines
   (RW-6); 0/0 → SKIPPED (exit 0, `verdict: skipped`, the relation text) and
   `--refuse-empty-diff` → exit 2; the selftest on `main` itself prints
   SKIPPED. **Evasion probe:** add an uncovered branch to `run-gate.py` on a
   scratch commit and confirm the selftest goes RED (the judge is real).
10. **assay lanes (D-11).** `assay.toml` r1/r2 (RW-8: `source_roots = ["."]`,
    `base_source = "request"`, tests excluded by assay, tools judged);
    `assay-r3`'s canary really goes red on a broken `duration_stats` and
    green otherwise (RW-10 shape); pins sha256 verified against the pyz;
    `gate-full`; `cmru.toml` release gate unchanged (`selftest`); run
    `./run-gate.py --base main assay-r1` and `assay-r3` yourself; read the
    r2 verdict and every survivor's justification in the REPORT.
11. **RW-17.** `RUN_GATE_PROFILE` semantics (`on`/`off`/absent/invalid);
    the autouse fixture must not hide production behaviour — every flow has
    a test with profiling ON; `doctor` and `--dry-run` disclose it.
12. **Hollow tests and coverage.** Mutate the subject, watch the test, for
    each new cluster; tests must drive `run_gate.main()` in-process where
    they claim coverage (subprocess-driven tests are invisible to the diff
    judge — check that no new behaviour is covered ONLY that way).
13. **Docs/spec.** SPEC R-43 a–h / R-44 / R-29 / R-36 amendments / the
    RG-41 rule-id backfill / drift fixes / Rev 10 — accurate to the code;
    README, CONSUMERS (daemon = host infra, manifest TRACKED, `.run-gate/`
    ignored), LANE-AUTHORING, CHANGES `[Unreleased]` (BREAKING notes for
    RG-53 and the `footprint` reserved name), backlog RG-55/RG-53/RG-48 →
    FIXED with real evidence, `__revision__ = 41`, `usage()`.

## Live probes (you run them yourself; host rule below)

- The ephemeral basic-path probe (a throwaway project symlinking the tip's
  `run-gate.py`, a `tester-unified:local` command lane holding 100 MiB for
  12 s, NO daemon) → record with `memory.peak_bytes ≥ 100 MiB`, `source:
  memory.peak`, `method: basic`, host PSI filled, the footprint line exact.
- The exec container-shared probe (a `sleep 600` container declared as an
  exec environment; a lane allocating 80 MiB) → `peak_over_baseline_bytes ≥
  70 MiB`, `source: sampled-max`, ≥ 2 samples.
- `history`, `history --json`, `footprint`, `footprint --write` on that
  probe store; `footprint --write` refusal on the bare-host selftest store;
  `doctor` on the project.
- If `cgprofile-host-daemon` happens to be UP on the host at review time,
  one daemon-path run of the ephemeral probe (do not start or stop the
  daemon yourself — P1's reviewer may be using it); otherwise skip and say
  so (P3 integration covers it).
- `finally`: remove your containers.

## Verdict

`ACCEPT` / `ACCEPT-conditional` / `REJECT` with numbered blockers (B1..),
each with file:line evidence and a concrete prescription; non-blocking
findings (S1..) separately; product calls named as decision asks for the
controller, never improvised. Claims you could not verify listed as such.
Write the round file, then return the verdict line first in your message.

## HOST LOAD (binding)

8 cores shared with a production game server; PSI is the signal. pytest
SERIAL only, `nice -n 19 ionice -c 3`; targeted files while iterating, the
whole selftest at most once (plus the assay-r1/r3 lanes once each). ≤ 2
gate containers estate-wide (`docker ps` for `tester-unified:local` first;
a P1 reviewer may hold one); `docker update --cpus=3` after launch; remove
in a `finally`. Never `--cgroupns=host`/`--pid=host`. Never touch
`scripts/cgroup-profiler/`, `ciu/`, `/workspaces/dstdns`. Edit tool only
if you must write (round files); no commits to the branch — repairs are the
implementer's.
