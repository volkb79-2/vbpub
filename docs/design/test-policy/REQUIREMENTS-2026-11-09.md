<!-- origin: 2026-11-09; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/test-policy-REQUIREMENTS.md -->
<!-- relation: estate test policy requirements -->

# Estate test-run policy (R0-R4 by event): operator interview 2026-11-09
Operator request: generalize and persist the release-gate optimization principles (which tests run when, run-gate config) in the run-gate adoption docs, then check across all repos; consider what optimized guidelines we can define given run-gate and assay; eventually allow runs for R0-R4 but NOT everything on every commit, merge or release.
## Answers
1. **Event tiers (chosen: four tiers; add fuzzing):** T0 edit/commit = focused tests only (agent-local, seconds); T1 pre-merge = project R0 + R1 (changed-line) on the final head; T2 release = R0+R1+R3, reusing identical-tree PASS records, release-prep-only paths excluded (RG-253 design); T3 async on the latest release = R2 mutation (+ R4 where claimed). R2 never blocks a release. **Fuzzing: plan it as an R2-class asynchronous tier (T3), like mutation**: define where fuzz targets/corpora/budgets live and how a fuzz finding becomes a backlog item + R4 red-first regression test.
2. **R4 red-first: optional, reviewer's call** (available, not mandated; reviewer decides per branch).
3. **Where the policy lives:** normative section in run-gate adoption docs (`run-gate-project/CONSUMERS.md` + `LANE-AUTHORING.md`) defining tiers, lane-name conventions and prep-only path declarations, plus a read-only policy check that audits each project's run-gate.toml/assay.toml against it. **Presets + per-project rules:** run-gate ships RECOMMENDED presets; each project defines its own rules against the presets (documented deviations); i.e. per-project, not one central file that overrides.
4. **Audit:** audit AND fix in one pass per project (jobs both report and fix lane config + docs; each reviewed on the other route).
## Related
`reports/gate-reuse-INVESTIGATION.md` + `run-gate-project/docs/DESIGN-verdict-reuse.md` (RG-253), `libraries/test-policy` (existing project, unreleased), `RIGOR-COVERAGE-POLICY` (dstdns), assay rigor levels in `assay/docs/CONSUMERS.md` and DESIGN-GUIDE, lane conventions in `run-gate-project/LANE-AUTHORING.md`.

## Decisions D1-D8 on the Opus tier methodology + lane-rework design (operator, 2026-11-10; design: run-gate-project/docs/DESIGN-lane-rework-2026-11.md on branch test-methodology-20261110, report reports/test-methodology-REPORT.md; independent Opus review in progress)
- **D1 yes:** Assay `whole_target` R1 replaces cmru's separate `coverage` lane at T2 (release).
- **D2 yes:** second probes of one instrument (cmru uncovered-line canary, capture-mode second runs, Rust uncovered-line) move to T3 (async).
- **D3 yes:** build Assay AS-1 (R3 canary uses the lane's own baseline run as control instead of a fresh full suite; opt-in per lane). **D5 OVERRIDE by operator: narrow Assay's installed-wheel R0 scope NOW** (not "measure first"); keep the measurement as evidence in the work package, not as a gate on doing it.
- **D4 yes:** `gate` means T2 everywhere; cmru's R2-including `gate` is retired; the provisional path (R2 deferred, KI-79) becomes the normal release path; R2 stays an async post-release campaign on the latest release.
- **D6 yes:** RG-253 implementation DEFERRED (v1 saves 0 s on today's layout); do the prep-reader split now (the test that reads cmru/CHANGES.md moves into the fast release-preconditions lane).
- **D7 yes:** minimal read-only `run-gate policy-check` (tiers derived from lane content, single `[policy.accepted]` table), built after the config work packages.
- **D8 yes:** Rust R2 = diff-scoped cargo-mutants at T3 against the previous release tag (not pre-merge).
Next: Opus review of the design -> fix -> merge -> implementation work packages (WP-CMRU-1..3, WP-ASSAY-1/2, WP-SMALL, WP-AS1, WP-PC; order and parallel set in the design section) executed by luna/deepseek with an OPUS review per package (operator: 'opus review in this case').
