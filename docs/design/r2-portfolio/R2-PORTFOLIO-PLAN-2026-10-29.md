<!-- origin: 2026-10-29; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: R2-PORTFOLIO-PLAN.md -->
<!-- relation: estate mutation portfolio -->

# R2 portfolio plan — 2026-10-29

## Scope and evidence rules

This is a read-only plan for Assay, CIU, Nyxloom, PWMCP, IOBench, CGroup Profiler, cli-extended, Topos, TLS Edge, and TrueNAS/tnt after merge. CMRU and Run-Gate are excluded because their diff-scoped R2 work is already done. The operator’s CMRU whole-source waiver remains deferred debt.

I count a release as mutation-judged only when a completed R2 verdict is recorded for that release tag’s relevant project lane. A campaign on an untagged commit, a narrow pilot, an incomplete run, or an R2 waiver does not qualify a later release.

All candidate counts below are plan-only results for the non-merge source commit d2e16daa1db029c76bd632c39edf625bbdd5fc8a (parent 2e62261a1d3450dd0cbbbaa5349fb8d7fe22ffbb), the origin/main tip used by the permitted scratch worktree. Each plan used the selected worktree’s Assay source and assay plan --json. No candidate was executed, no tests or campaigns were started by this survey, and this survey did not launch or use a tester container. Temporary scratch-only lane overlays were removed after their plans. A count produced by an overlay is explicitly labeled and is not the current declared lane.

ETA and windows use the requested 3 minutes per candidate. “Windows” means resumable windows bounded by the inner Assay lane budget, not separate fresh campaigns; this excludes setup, R0/R1/R3, survivor triage, and fixed overhead. Per-candidate caps and earlier measured rates can make actual time longer. Risk rank is relative impact if a mutation survives or a gate gives a false result; it is not a finding that the current code is defective.

## Portfolio

| Risk | Tool | R2/mutation lane and declared budget | Last mutation-judged release tag | Base for next release-level R2 | Plan-only candidates on d2e16… | 3-minute ETA / windows | Why the impact is ranked here |
|---:|---|---|---|---:|---:|---|---|
| 1 | Assay | Run-Gate self-qualification → Assay self-qualification; outer 8h / inner 5h; whole_target, cap 10,000 | None. assay-v10.0.0 explicitly waived R2 and claims no pass. | Whole-source. No completed full self-qualification tag to diff from. | 6,797 | 339h51m / 68 × 5h | This is the shared mutation judge. A false pass can qualify every consumer that trusts it. |
| 2 | CIU | Run-Gate/Assay ciu; 8h / 8h; cap 10,000; current Assay base is main | ciu-v7.15.2 (28/28 killed; R0–R3 PASS). Latest release is v7.18.0. | Diff from ciu-v7.15.2. The lane must first delegate a request base; its current static base=main refuses Run-Gate --base. | 571 with scratch overlay base ciu-v7.15.2. The current declared main base planned 0, an empty scope. | 28h33m / 4 × 8h | Worktree allocation, gate qualification, and release lifecycle errors can corrupt a checkout or certify the wrong tree. |
| 3 | CGroup Profiler | Run-Gate/Assay r2; 24h / 24h; current cap 2,000 and request-base scope | None. Latest change record is 1.1.0 with R2 waived; the last located full-source run failed on an untagged tree (125 survivors). | Whole-source: no released full-source R2 verdict. Lane needs whole_target plus complete targets and a higher cap. | 2,275 from a scratch whole-target overlay. Current declared request-base plan: 1,693; not whole-source. | 113h45m / 5 × 24h | Host cgroup placement and resource guards protect other workloads; a surviving regression can leave work unbounded or mis-placed. |
| 4 | PWMCP | Run-Gate/Assay r2; 4h / 4h; whole_target, cap 1,000, 900s per candidate | None. Latest release is pwmcp-v1.64.0-r3; the last located complete run was 83/83 on untagged commit d770556c. | Whole-source. | 384 | 19h12m / 5 × 4h | It combines release image build/publish machinery with browser automation and credential handling. |
| 5 | TLS Edge | No run-gate.toml or assay.toml R2/mutation lane. | None; latest release tag tls-edge-v2.0.0 has no located R2 judgment. | Whole-source after a lane is declared. | N/A — no lane to plan. | N/A | TLS termination, ingress exposure, and the Docker API proxy are security boundaries. |
| 6 | IOBench | Run-Gate command lane r2 invokes Assay lane iobench; 360m / 360m; whole_target, cap 10,000 | None. iobench-v0.1.0 has no recorded R2 verdict. | Whole-source. | 1,220 | 61h / 11 × 6h | The code controls destructive storage tests and safety/restore behavior. |
| 7 | TrueNAS/tnt (after merge) | Candidate branch has r0-r1, ux-focused, and live-v27 command lanes; no assay.toml or R2 lane. | None; not merged or released. | Whole-source after merge and an R2 lane is declared. | N/A — no lane to plan. | N/A | The product handles privileged TrueNAS storage/API operations; mutation coverage should be added before release. |
| 8 | cli-extended | Run-Gate r2 → Assay cli-extended; 720m / 720m; whole_target, cap 3,000 | None. Latest cli-extended-v0.5.0 has no current R2 verdict. The last located complete campaign was 1,015/1,015 on untagged commit 75c007e3. | Whole-source. | 1,456 | 72h48m / 7 × 12h | Shared CLI parsing, config, constraints, and diagnostics affect many downstream tools. |
| 9 | Nyxloom | Run-Gate/Assay session-extract; 6h / 6h; fixed base 126ccc39e151e33cc7bbcaa18bf765f9c9cd7dd1; cap 350, per-candidate 120s. This lane covers only session_extract. | None. Latest nyxloom-v0.11.1 has no R2 verdict. The last located completed R2 campaign was 43/43 on untagged commit 8df26ed (2026-10-01). | Whole-source for a first release-level judgment. The current narrow lane cannot provide it. | 336 for the narrow session-extract lane only; no whole-source plan from the declared lane. | 16h48m / 3 × 6h for that narrow plan only | Session/transcript extraction handles sensitive content and structured tool/control records. |
| 10 | Topos | No mutation lane. Current Run-Gate lanes are topos-suite (30m command) and py-compile (2m command). | None; latest topos-v0.4.2 has no located R2 judgment. | Whole-source after a mutation lane is declared. | N/A — no lane to plan. | N/A | System and service inspection behavior matters, but no registered R2 lane currently claims it. |

### Important scope and history notes

- Assay’s primary lane is the 6,797-candidate full-source self-qualification. There is also an Assay analysis-r2 lane (955 candidates, whole_target, 60m inner budget), but it is not a full self-qualification lane; Run-Gate offers only the bounded analysis-r2-pilot (140m). At 3m/candidate that separate lane would be 47h45m / 48 × 60m. It must not be presented as an R2 qualification of the release. B105 history records an earlier attempt stopping at 15 of 3,760 candidates, and historical estimates were about 505–560 seconds per candidate. Thus the requested 3-minute estimate is especially optimistic for Assay. B110/B131 pilot and GO criteria remain prerequisites before scheduling a full run.
- CIU’s current base=main plan is empty on this snapshot, not a pass. The temporary planning overlay replaced that base with ciu-v7.15.2 and found 571 candidates. Before a campaign, change the lane to base_source=request (remove the static base) so Run-Gate can pass --base ciu-v7.15.2 to Assay. Then re-plan the actual committed campaign tree.
- CGroup Profiler’s declared base eb3af770eb16362cff4c39388193c0180c2d370c produces 1,693 candidates on this snapshot, but that is the existing changed-source lane. For whole-source planning, a request base before the first tracked lib source commit (77dff4428d37eca51c381b3085c41ddb4f133bb3) safely refused because the Git diff exceeded Assay’s 64 MiB output bound. A scratch-only whole_target overlay enumerating its lib Python targets returned 2,275 candidates in 24 candidate-bearing files. The current 2,000 cap would refuse this set; set a reviewed cap at least 2,275 (prefer headroom, e.g. 2,500) when the lane is redesigned.
- A pre-existing Nyxloom session-extract Run-Gate job was already active when checked at 2026-10-29 09:18:35 UTC. Exact container name: run-gate-vbpub-session-extract-1836731-1793253398; its separate worktree is .worktrees/nyxloom-usage-20261028. The last progress record at 09:18:22 showed 495/1,201 candidates. This is a different worktree/run than the scratch plan’s 336-candidate plan. Do not start a duplicate; obtain that exact tree’s terminal verdict first. This task did not start it.
- TNT status was read from the unmerged candidate worktree .worktrees/tnt-codex-20261028. Its current lanes do not include mutation judgment. Do not infer a future lane name or budget.

Source pointers: assay/CHANGES.md (v10 waiver); assay/nyxloom-trove/4-backlog.md (B105, B110/B131, B169); ciu/KNOWN_ISSUES_TODO_BACKLOG.md (2026-10-05 exact R2 pass); nyxloom/nyxloom-trove/reports/nyxloom-P113-REPORT.md and nyxloom-SUCCESSOR-2026-10-REPORT.md; pwmcp/docs/reviews/pwmcp-coverage-modernization-review-2026-09-20.md and pwmcp/KNOWN_ISSUES_TODO_BACKLOG.md; scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-P1-SURVIVOR-FINAL-SOL-REVIEW-20261004.md and backlog CP-30; libraries/cli-extended/docs/CLI-SURFACE-IMPLEMENTATION-PLAN.md and docs/CX-BACKLOG-2026-10-REPORT.md; project assay.toml/run-gate.toml files named in the table.

## Recommended sequence

1. Do not duplicate Nyxloom’s active run. Review its terminal evidence on its own exact tree when complete; it cannot qualify a release-wide Nyxloom R2 because the lane is package-scoped.
2. First ready pair after CIU’s request-base lane change: CIU on r2.lock and PWMCP on r2b.lock. This covers the release/worktree gate and the build/publish automation.
3. Next pair after CGroup’s whole-source lane/cap update: CGroup Profiler on r2.lock and IOBench on r2b.lock.
4. Run cli-extended whole-source on r2.lock. Pair it with a newly declared whole-source Nyxloom lane on r2b.lock only after the existing Nyxloom job finishes.
5. After merge and lane adoption, pair TLS Edge and TrueNAS/tnt across the two locks; Topos also needs a declared mutation lane before it can be scheduled.
6. Run Assay’s full self-qualification only after B110/B131 provide a GO and a measured capacity plan. Use a single lane at a time because the current 6,797 count implies roughly 340 hours even at the requested optimistic rate.

The commands below are future instructions only. They create no worktrees and start no campaign now. Create the named worktrees from the then-current integrated origin/main after each required lane change is merged, then run from the project directory with the monorepo worktree root passed to Run-Gate. Use the released Run-Gate CLI; do not replace these with direct Docker commands.

### Pair 1 — after CIU base delegation is merged

Worktree setup:

    git -C /workspaces/vbpub worktree add -b r2-portfolio-ciu-20261029 /workspaces/vbpub/.worktrees/r2-portfolio-ciu-20261029 origin/main
    git -C /workspaces/vbpub worktree add -b r2-portfolio-pwmcp-20261029 /workspaces/vbpub/.worktrees/r2-portfolio-pwmcp-20261029 origin/main

CIU uses the last judged release tag as its comparison base. The updated CIU Assay lane must declare base_source=request before this command accepts --base.

    cd /workspaces/vbpub/.worktrees/r2-portfolio-ciu-20261029/ciu
    flock <controller-program-dir>/r2.lock <controller-home>/.venv/bin/run-gate ciu --worktree /workspaces/vbpub/.worktrees/r2-portfolio-ciu-20261029 --base ciu-v7.15.2

PWMCP has no qualifying release R2 tag, so this is whole-source.

    cd /workspaces/vbpub/.worktrees/r2-portfolio-pwmcp-20261029/pwmcp
    flock <controller-program-dir>/r2b.lock <controller-home>/.venv/bin/run-gate r2 --worktree /workspaces/vbpub/.worktrees/r2-portfolio-pwmcp-20261029

### Pair 2 — after CGroup Profiler’s whole-source lane update is merged

The lane must select the full lib target set, use whole_target, and raise max_mutants to at least 2,275 before launch.

    git -C /workspaces/vbpub worktree add -b r2-portfolio-cgroup-profiler-20261029 /workspaces/vbpub/.worktrees/r2-portfolio-cgroup-profiler-20261029 origin/main
    git -C /workspaces/vbpub worktree add -b r2-portfolio-iobench-20261029 /workspaces/vbpub/.worktrees/r2-portfolio-iobench-20261029 origin/main

    cd /workspaces/vbpub/.worktrees/r2-portfolio-cgroup-profiler-20261029/scripts/cgroup-profiler
    flock <controller-program-dir>/r2.lock <controller-home>/.venv/bin/run-gate r2 --worktree /workspaces/vbpub/.worktrees/r2-portfolio-cgroup-profiler-20261029

    cd /workspaces/vbpub/.worktrees/r2-portfolio-iobench-20261029/libraries/iobench
    flock <controller-program-dir>/r2b.lock <controller-home>/.venv/bin/run-gate r2 --worktree /workspaces/vbpub/.worktrees/r2-portfolio-iobench-20261029

### Pair 3 — once the active Nyxloom run finishes and a whole-source Nyxloom lane exists

cli-extended’s current whole-source lane can run now. The Nyxloom command is intentionally withheld until its lane is declared; the existing session-extract lane is not the release-level scope requested here.

    git -C /workspaces/vbpub worktree add -b r2-portfolio-cli-extended-20261029 /workspaces/vbpub/.worktrees/r2-portfolio-cli-extended-20261029 origin/main

    cd /workspaces/vbpub/.worktrees/r2-portfolio-cli-extended-20261029/libraries/cli-extended
    flock <controller-program-dir>/r2.lock <controller-home>/.venv/bin/run-gate r2 --worktree /workspaces/vbpub/.worktrees/r2-portfolio-cli-extended-20261029

After a full Nyxloom mutation lane is added, use a separate worktree named .worktrees/r2-portfolio-nyxloom-20261029 and the exact lane name/budget declared by that merged run-gate.toml. Do not substitute session-extract for whole-source.

### Later queues

- Topos: create .worktrees/r2-portfolio-topos-20261029 after adding and documenting its mutation lane. No valid R2 command exists today.
- TLS Edge: create .worktrees/r2-portfolio-tls-edge-20261029 after adding and documenting an R2 lane. No valid R2 command exists today.
- TrueNAS/tnt: after merge, create .worktrees/r2-portfolio-tnt-20261029 from the merged integration tip, declare and document the mutation lane, then use that exact lane. The current candidate worktree cannot be treated as a released base.
- Assay: after the GO decision and capacity work, create .worktrees/r2-portfolio-assay-20261029 and run its full-source lane alone:

    git -C /workspaces/vbpub worktree add -b r2-portfolio-assay-20261029 /workspaces/vbpub/.worktrees/r2-portfolio-assay-20261029 origin/main
    cd /workspaces/vbpub/.worktrees/r2-portfolio-assay-20261029/assay
    flock <controller-program-dir>/r2.lock <controller-home>/.venv/bin/run-gate self-qualification --worktree /workspaces/vbpub/.worktrees/r2-portfolio-assay-20261029

## Read-only completion record

The main checkout remained at its pre-existing state (main one commit behind origin/main, with its pre-existing untracked PWMCP console-capture and Netcup files). No project files were modified. The only worktree created was the explicitly permitted scratch plan worktree, which stayed clean; the requested plan was written outside the repository. This survey did not run tests, start a campaign or container, merge, push, or release.
