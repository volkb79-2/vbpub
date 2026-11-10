<!-- origin: 2026-11-04; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/DSTDNS-BENEFIT-ANALYSIS-2026-11-04.md -->
<!-- relation: external-project adoption analysis -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# What dstdns gains most from the vbpub tool estate (read-only analysis, 2026-11-04)

Scope: `<checkout>` at HEAD `c25c53d1` (2026-10-23), read only. Nothing in dstdns was modified, no git writes, no containers.
Tags: **[F]** fact with file:line or command output, **[E]** estimate with reasoning, **[U]** not measured.
Installed here: ciu 7.19.0, run-gate 24.3.0, cmru 7.1.4, nyxloom 0.12.0. Assay 12.0.0 is Unreleased on vbpub main (`assay/CHANGES.md:7-23`). The ciu CLI-surface adoption sits on branch `feat-ciu-clix-20261104` (`ciu/docs/CLI-SURFACE-DECISIONS-20261104.md`).

## 1. How dstdns uses each tool today

**assay**
- [F] Pins the vendored zipapp `tools/assay/assay-9.0.0.pyz`, verdict schema v15 (`run-gate.toml:18-27`, `tests/config/test_assay_pin_integrity.py:22-24`). `requirements.txt:56-58` floors the CLI at `assay>=9.0.0`.
- [F] `.assay-inbox/release.json` (written 2026-11-02) announces **11.0.0** (verdict v17, `landed: []`). It is not adopted. `.assay-inbox/` also holds `CONTRACT.md`, the JS-lane brief (2026-09-01) and the cli-extended brief (2026-10-05).
- [F] `assay.toml` is 5398 lines with 128 lanes (`assay.toml:1`). 47 are R0+R1, 80 are R0+R2, 1 is R0 only (`mock`). Modes: 108 whole_target, 19 changed_lines.
- [F] Adapters: Python 125 lanes, SQL/DDL (B001) 1 lane (`cw2b_schema`, `assay.toml:76-150`), JavaScript (B036) 1 lane (`ui_unit`, `assay.toml:1512-1553`, istanbul via vitest, `fail_under = 100`, changed_lines). R3 canaries exist for JS (B087, `.assay-inbox` log). RIGOR policy line 22 says no R3 cadence is scheduled.
- [F] R2 declaration: 59 lanes carry `jobs = 6`, 16 carry `jobs = 2`, 4 carry `jobs = 4`, 1 carries `jobs = 1` (e.g. `assay.toml:1092`). In assay 11+ the key `jobs` is an alias of `jobs_max` (`assay/docs/CONSUMERS.md:4368`). Four operator families (`compare-swap`, `boolop-swap`, `falsy-swap`, `bool-const-flip`) are split into per-operator sibling lanes (21/20/16/15 lanes).
- [F] **No native R2 lane has ever run green.** `nyxloom-trove/r2-watermark` reads `none ... no-green-coalesced-R2-yet`. COVERAGE-MAP: "R2 was not run in any campaign. 80 lanes ... not measured" (`docs/testing/COVERAGE-MAP.md:22`). Cause: the exec `test-runner` has a private cgroup namespace, so every native R2 lane refuses with `NO_MEASUREMENT/CGROUP_OBSERVATION_UNAVAILABLE` (`assay.toml:13-16`, RIGOR-COVERAGE-POLICY `:72`, audit `docs/summaries/deep-review-2026-10-15/reports/C02-assay-migration.md`: "80 of 80 R2 lanes report available=false").
- [F] The last real mutation evidence is the SQL lane from early September: 192 candidates across 5 DDL files (133+38+13+8), 32/86 done in 120 min, ~230 s per candidate serial, budget raised to 360m, `jobs` lowered 2 to 1 because two concurrent candidates breached the 2 GB `schema-gate-pg` limit (`assay.toml:76-131`, D-376/D-377).

**run-gate**
- [F] SSOT is `run-gate.toml` (851 lines). `[admission]` is enabled (`:10-14`). The shared `[assay]` import lists all lanes by name (`:18-150`). Environments: `test-runner` in exec mode against a persistent runner (`:153`), `bare-host` host mode. Floor `run-gate>=23.10.0` (`requirements.txt:55`); installed 24.3.0.
- [F] Composite `gate` = schema, run-gate-docs, test-runner, assay (the R0 `mock` lane), assay-dlq, frontend-unit, ui_unit, `stop_on = FAIL` (`:311-322`). It is **strictly sequential**.
- [F] `.run-gate/history.json` (1.3 MB, 76 lanes, capped at the last 10 runs per lane, last entry 2026-10-16 00:59) is the only run evidence.

**ciu**
- [F] `ciu>=7.15.2` (`requirements.txt:50`), installed 7.19.0. About 29 stacks, 16 `ciu.defaults.toml.j2` files, `[ciu.worktree] max_concurrent_instances = 5` (`ciu.global.defaults.toml.j2:1565-1570`). Worktrees use `ciu worktree create <name> --base main --up` (`AGENTS.md:79-85`, `nyxloom-trove/GUIDE.md:53-71,156-176`, `README.md:86-92`).
- [F] **The 143-146 `ciu worktree add` lines are all historical.** Non-archive hits are in frozen per-package logs and reviews under `nyxloom-trove/{archive,reports,handoffs,reviews}` and `docs/archive`. The live docs, AGENTS and GUIDE have 0. No older ciu is pinned anywhere. Nothing live breaks from the removal of `worktree add` (`d1eb98770`).

**cmru**
- [F] `cmru.toml` registers dstdns as a product with an installer (`cmru.toml:10-30`). `cmru versions` (14-day window) adoption is plan item CMRU-VERSIONS-ADOPTION (`docs/plan-repair-and-advancement-2026-10.md`, Wave 1). It is not done: 11 floating image tags are still unpinned (GATING-FRESH-DEPLOY F2).

**cgprofile, nyxloom, tester images, pwmcp**
- [F] cgprofile: run-gate profiles lanes through its daemon (`history.json` entries carry `profile_ref.daemon = cgprofile-host-daemon`). The observer path for R2 is not wired (RIGOR `:72`).
- [F] nyxloom: dstdns uses its trove (`nyxloom-trove/`) and `nyxloom.toml` gate pointers, but **dispatch is manual and the daemon is not used** (`CONTROLLER-BRIEF.md`, State).
- [F] Tester images: dstdns builds its own `dstdns/test-runner` FROM `dstdns-app-base` (`tools/test-runner/Dockerfile:4,29`), with pytest-xdist installed (`:102-113`, `requirements.txt:115-118`). It does not use vbpub `tester-base`.
- [F] pwmcp: `ui` lane (60m budget, `run-gate.toml:354-370`) and `.mcp.json`. The browser suite is outside the merge gate (D-393). The `ui` lane is "red by construction" on a Mode-B worktree (COVERAGE-MAP, section 6).

## 2. Time sinks and pain points

| Item | Numbers | Tag |
|---|---|---|
| Composite `gate` wall | 10 recorded runs: 1103-3421 s; the last 4 green runs 1103/1244/1500/1706 s; median 1670 s; 3 of 10 FAIL (all 2026-10-03). Total 5.6 h of the 11.75 h recorded across 76 lanes | F |
| Same tests run twice | `test-runner` (428-806 s) and the R0 assay lane `mock` (448-573 s) execute the same pytest selection by design ("argv must stay identical", `assay.toml:23-28`). At commit `52ff5787`: 428 s plus 448 s of a 1103 s gate | F |
| Other members | schema 159-299 s, ui_unit 72 s, frontend-unit 41 s, assay-dlq 15 s | F |
| Composite is serial | Sum of members equals the gate wall (e.g. 159+428+448+...). schema (PG, light CPU) cannot overlap frontend/ui_unit | F/E |
| Mutation corpus | 80 R2 lanes; sum of declared budgets **3494 min = 58 h** (ceiling, not a measurement); sum of `max_mutants` caps 10780 (largest: `p221_sysop_input_contracts_r2` 1000; worker_io_http_operations 3x400). Real candidate counts are **[U]** except SQL (192) and p214 (50, 0.285 h at 2 workers, `assay.toml:5105-5108`) | F/U |
| SQL R2 | 192 candidates x ~230 s = ~12 h serial; 360 min budget exceeded once (32/86 at 120 min). Parallelism blocked by shared PG memory (D-377) | F |
| Zero-candidate lanes | Several R2 lanes measure `candidate_count=0` (`assay.toml:2050,2311,2468,3658,4255`); `p167_created_by_r2` exits 5 by construction (`:435`) | F |
| Failure causes seen | 26 of 133 retained runs FAIL. Tool-caused: 5 schema-runtime R1 lanes `NO_MEASUREMENT`/`EMPTY_COVERAGE` (M1, ciu label; P243/P244), 7 changed-line lanes `NOT_RUN no-base` (M2, rebased fork point; fixed by ciu `worktree refork`, section 3 item 7). Product: `worker_io_dns_operations` UNCOVERED_LINES (1040/1218 lines) | F |
| Flaky/long vitest lane | `frontend-coverage-direct` 24 s, 31 s, 459 s, 739 s FAIL across 4 runs (vitest worker RPC, RG-45, P193) | F |
| Live lanes | `scale-admission-live` 1146 s, `component-integration` 111-143 s FAIL x2, `vertical-corpus-e2e` FAIL x2 (preconditions M4-M7, repaired in P243) | F |
| ciu stack bring-up | No wall time recorded anywhere. Only `health_timeout=300s` (D-212) and "25 containers" fresh main (COVERAGE-MAP C). Post-merge `fresh-landscape` is a from-scratch bake+up on every main gate (D-673) | U |
| Admission | `admission.waited_s` 0.5-1.2 s on the 4 recent runs, so the cap is not a bottleneck today | F |
| Staleness of evidence | History ends 2026-10-16; no gate has run for ~19 days; dstdns is on assay 9.0.0 while 11.0.0 shipped 2026-11-02 and 12.0.0 is next | F |

## 3. Ranked improvements (what, gain, effort, id, deps)

Effort S = under 1 day, M = 1-3 days, L = a wave. Gains are **[E]** unless noted.

1. **Make native R2 runnable at all: ephemeral runner plus cgprofile observer.** dstdns has 80 R2 lanes and zero measured, so everything below about R2 is moot until this lands. Switch `[environments.test-runner]` to `mode = "ephemeral"` with `resource_observer = "cgprofile"` (run-gate RG-89, release candidate; dstdns plan item EPHEMERAL-LANE-RUNNER and Wave 4 "native assay R2 on the observer path"). Gain: from "R2 refuses" to a first honest campaign; this is the entire R2 value. Effort L. IDs: RG-89 (IN PROGRESS), RG-102 (ephemeral launch duplicates CIU identity/network/secrets; OPEN), CIU-141 (`ciu run`; **deferred to v8** per `CLI-SURFACE-DECISIONS` C13, so RG-102 stays on raw docker for v7). Deps: run-gate 24.4, cgprofile 1.1.x, assay 12. Risk: ephemeral lanes must join the worktree's instance network and reach PG/Redis (dstdns lanes need `SCHEMA_GATE_DSN` etc.).
2. **Parallel native R2 (`assay run --jobs N`, B207 scheduler, run-gate `jobs = "auto"`).** [E] With 0.7-1.2 cores and ~150-470 MiB per candidate (vbpub-measured, COW-PLAN section 0) and the 3-CPU gate cap used on this shared host, 3-4 workers gives ~2.5-3x on CPU-bound pytest R2 lanes. If the 58 h ceiling is representative, wall drops to ~20-25 h for a full cold campaign; a coalesced async run after merge then fits overnight. Effort M for dstdns (rename `jobs` to `jobs_max`, pick `jobs_max` per lane, enforce caps). Ids: B207, PR1b, RG-131/132/143 (worker selection, CPU cap derivation). Deps: item 1, the btrfs `cheap_clone` area (`MDT_COW_ROOT=<cow-root>`, btrfs, 195 GB, 15% used [F]); dstdns's tracked tree is only 2745 files / ~6 MB so clone cost is trivial [F]. Only `--jobs 1` and 3 were verified live (`assay/docs/CONSUMERS.md:4465`).
3. **Stop running the unit suite twice per gate; give pytest lanes xdist workers.** [E] Gate 1103-1706 s to ~600-900 s: one of the two ~430-550 s runs goes away (R0 verdict reused from the sibling run or the command lane dropped in favour of the assay `mock` lane), and run-gate RG-146 `workers = "auto"`/`{workers}` (Unreleased, README `xdist workers for command lanes`) cuts the remaining run ~2-3x on 8 cores. Effort M. Ids: RG-146/147 (exist, Unreleased in 24.4), plus to file **assay**: "adopt an R0 result from a sibling command lane's junit at the same commit" (run-gate `--reuse-from` today needs a direct sequential pytest argv, RIGOR `:70`). Deps: dstdns `xdist_safe` audit; `pytest.ini` already has `--dist=loadfile`; Hypothesis files are `derandomize=True`. Note TS7 (sys.modules contamination) is why dstdns never used `-n`.
4. **Parallel members in a composite `kind = "sequence"`.** [E] schema (160-300 s, PG) overlapping frontend-unit+ui_unit+assay-dlq (~130 s) saves 130-250 s per gate even after item 3. Effort M. Id: to file in run-gate ("sequence groups with `parallel = [...]`, admission aware"); Dep: RG-80 daemon admission counts, memory-PSI-aware (RIGOR "Resource scheduling").
5. **SQL/DDL R2 with per-worker databases.** [E] 192 candidates at ~230 s is ~12 h serial; 3 workers on separate PG databases or containers makes it ~4-5 h. D-377 already designed a DSN-pool lease in `scripts/sql-mutation-gate.sh`. Effort L. Id: to file in assay backlog ("expose worker index/lane lease to the lane env so a SQL R2 command can pick its own DSN") plus verify `worker_index` is available to the command (it is only in progress events, `CONSUMERS.md:4478` [F]). Deps: items 1-2; PG memory cgroup; otherwise keep the SQL lane `jobs_max = 1`.
6. **Confirm kills / record the failing test (B204, B205, v18 B208).** The 11.0.0 notice itself states "a flaky suite test can be credited as a kill in any run". The jobs pilot found 3 disagreements, all explained by a ~5-10% flaky wall-clock test (`reports/ASSAY-JOBS-PILOT-ANALYSIS-2026-11-02.md:32`). dstdns has timing-sensitive tests (RG-45 vitest, `deadline=None` Hypothesis comment about load flakes). Gain: avoids false R2 greens, saves triage time per red or green mismatch; value rises with parallel runs under load. Effort S (assay side, proposed `judge.mutation.confirm_kills`). Deps: 12.0.0.
7. **Move dstdns to ciu 7.20 and clear its recorded "Shortcomings".** [F] GUIDE lists CIU-142 (rebase leaves a spent fork point), CIU-134/138 (rm leaves partial state), CIU-146 (`down --profile` scope), CIU-139/144 (resolve/exec unprimed shell, notice on stdout). All are FIXED or added in 7.18-7.20 (`ciu/CHANGES.md` Unreleased: `worktree refork`, CIU-146/147/139/144). Gain [E]: removes the hand-passed `--base` step for rebased worktrees (the M2 `no-base` class, 7 lanes) and the manual `ciu worktree rm` verification ritual; CIU-147 stops needless full recreates. Effort S. Deps: ciu 7.20 release; migration cost in section 4.
8. **Measured budgets and RAM-aware admission from run-gate profiles (RG-145/144/132).** dstdns sets budgets by hand (x1.5 of plan-estimate, 15 min floors; `RIGOR-COVERAGE-POLICY.md:108+`) and caps gates by flat count while saying the binding constraint is RAM PSI. Per-lane `lane-profile/1` sidecars and contamination flags give p95 RAM/CPU per lane. Gain [E]: safe 3-4 concurrent R0/R1 lanes instead of a flat cap of 2; fewer false `BUDGET_EXCEEDED`. Effort M. Deps: run-gate 24.4, assay 12 (observer-only profile objects).
9. **R3 canary cadence and the first coalesced R2 run in nyxloom/cmru terms.** [F] Policy says no R3 cadence is scheduled. A nyxloom `gate verify` timer (host timer, monthly or after any gate/image change) closes the "instrument is trusted" gap. Effort S. Id: nyxloom (to file as an NL item). Dep: items 1-2 for a trustworthy R2 canary.
10. **cmru `versions` (14-day vetting window) for requirements, npm and OCI tags.** [F] Plan item CMRU-VERSIONS-ADOPTION; 11 floating image tags and one cmru KI (unused vault policies A12-F20). Gain: removes the "latest tag masked for 18 months" class (authentik). Effort M. Deps: none beyond cmru 7.1.4.
11. **vbpub `tester-base` for the ephemeral R2 runner image (RG-105).** The lanes need only python plus the project's test closure; a slim image with cgprofile client and libeatmydata mostly helps vbpub-side gate speed. For dstdns the gain is small, [E] 0-10% (PG is in a separate container and already on tmpfs, so libeatmydata does not reach it). Effort M. Only do it if item 1's ephemeral image needs a rebuild anyway.
12. **pwmcp instance-scoped browser for Mode-B worktrees.** `ui` lane is red by construction in worktrees (COVERAGE-MAP section 6) and out of the merge gate. A worktree-scoped pwmcp with lease would let the `ui` lane enter the gate. Gain: UI regressions caught pre-merge ([U] since `ui` was never measured). Effort L. Id: pwmcp backlog (to file); the 2026-10-27 OCR/console sidecar design is unrelated to this.

## 4. Migration plan onto the new estate

**Facts to verify on dstdns first (read-only or on a throwaway worktree, in this order)**
1. The current main gate is green at all: last evidence is 2026-10-16, HEAD `c25c53d1` is 2026-10-23, nothing since [F]. Run one `run-gate gate` on 9.0.0 and keep the history as the baseline.
2. `tests/config/test_assay_pin_integrity.py:22-24,284-286` hard-codes the sha, manifest and `verdict:15` id. The repin must update the sha256 sidecar, manifest and the schema URN (v17, later v18) in one package. It also checks the `tools/assay/` file set.
3. Stored verdicts: v16 and v17 are hard cuts, so every `.assay/verdict-*.json` and any `assay verify` consumer must be regenerated [F per 11.0.0 notice]. Cost is low: no green R2 campaign exists, and R0/R1 verdicts regenerate on the next gate.
4. `jobs = 6` / `jobs = 2` on 79 lanes: under 11+ it is the `jobs_max` alias and execution stays serial unless `--jobs` is passed, but assay 10.x v16 **refused `jobs != 1`** (`COW-PLAN section 0`). So **skip 10.x; go 9.0.0 to 12.0.0 directly**. Rename to `jobs_max` in the same package (run-gate itself did this for IOBench, `run-gate CHANGES.md:152`).
5. `env_passthrough` / `assay_command` prefixes (`env GIT_CONFIG_COUNT=1 ... safe.directory`, `run-gate.toml` p129/p165/p167 lanes) may be obsolete after RG-99/RG-149 read-only git safety; test one lane before removing.
6. Candidate counts per R2 lane: run `assay plan <lane>` (read-only) on a scratch clone for the 5-10 largest lanes to replace the 58 h ceiling with real numbers [U now].
7. The cheap-clone area for dstdns: `MDT_COW_ROOT` points at `<cow-root>` (btrfs, vbpub area). Decide whether dstdns uses a sibling subvolume area; `<checkout>` is ext4 on the root LV, same as vbpub [F], so only the snapshot workspace, not the worktree, lives on btrfs.

**Order**
1. *Package A, tooling repin (low risk)*: assay 9.0.0 to 12.0.0 pyz plus run-gate floor 24.4, `jobs` to `jobs_max`, pin-integrity test update, regenerate verdicts. Gate on 12.0.0 (it ships under the B169 R2 postponement; R0/R1 lanes unaffected). Risk: the composite's three assay members (`mock`, `assay-dlq`, `ui_unit`) must produce valid v18 verdicts; JS adapter istanbul path (`applications/webapp-ui-react/.assay/coverage-final.json`) is unchanged.
2. *Package B, ciu 7.20 and the cli-extended surface (mechanical)*. Operator says dstdns is migrated later with no legacy concerns, so rename outright. What breaks, counted from non-archive files [F]:
   - `-y` to `--yes`: `scripts/fresh-landscape-gate.sh:217,232,244`, `.vscode/run-deploy.sh:25,28`, and documents/tests that assert strings (`tests/config/test_worktree_env_docs.py:270` expects the text `ciu clean -y`).
   - `ciu profiles` to `ciu show profiles` (AGENTS.md:300, README.md:19,72,80, `infra-global/skywalking/README.md:6`); `ciu layouts`, `ciu health` to `ciu status --gate` (4 hits, `.claude/skills/ciu-cli/SKILL.md`), `ciu graph --format json` to `--json` (3), `ciu capabilities` to `ciu schema capabilities` (2); bare `ciu env` is already unused (0).
   - `-d`/`-f`/prefix flags: `ciu up --dir` stays; check the ~15 `-d` hits are ciu secrets calls.
   - `ciu.global.defaults.toml.j2:348` mentions `ciu health`; comment only.
   - `requirements.txt:50` floor `ciu>=7.15.2` to the 7.20 release; add the cli-extended wheel install order to `env-workspace-setup-generate.sh` (brief `.assay-inbox/vbpub-cli-extended-adoption-brief-2026-10-05.md`: install `cli-extended` first, then `--no-index --find-links`).
   - ~29 stacks need no template change; CIU-147 will recreate each configfile-mounting service once on the first `ciu up`.
   - Gate: `ciu check`, `ciu worktree create --up` on a throwaway name, `fresh-landscape` lane.
3. *Package C, run-gate parallelism without R2 (medium risk)*: xdist `workers` on the pytest lane plus de-duplication of the second suite run. Verify `xdist_safe` (sys.modules contamination, port collisions in `tests/unit`), run twice with workers=1 and auto, compare junit pass sets.
4. *Package D, EPHEMERAL-LANE-RUNNER and the observer (high risk, the long pole)*: `[environments.test-runner] mode = "ephemeral"`, `resource_observer = "cgprofile"`; the persistent test-runner service leaves the steady-state stack (plan item). Risks: network join for PG/Redis/Authentik lanes (RG-102), secrets (`forward_env`), the `/run-gate-state` named volume, uid 1003 recipe (RG-150), and the cgroup2 remount side effect of `mdt doctor`. First proof: one R2 lane (`p214_b3_env_common_r2`, 50 candidates, small) then the 5 `*_r2_*` families. Only then raise `jobs_max`.
5. *Package E, first coalesced R2 campaign at `--jobs 3`* with `confirm_kills` (item 6) and the profile sidecars (item 8); advance `nyxloom-trove/r2-watermark` only on a fully green run (RIGOR `:49-52`). SQL lane stays at `jobs_max = 1` until item 5.
6. *Package F, cmru versions, R3 cadence, budget refit from profiles.* Independent, can run beside C-E.

Host rule while testing: one gate container at a time, `docker update --cpus=3`, nice/ionice (memory `host-shared-with-production-load-rule`).

## 5. Quick wins with no vbpub release (all dstdns-side)

1. Ship dstdns's own repin to the assay 11.0.0 notice or hold for 12.0.0: the notice (`release.json`, sha256 `2f0d77c8...ed2d`) has sat unread since 2026-11-02. Decide one target; do not stop at 10.x/11.0.0 if jobs lanes are wanted (see section 4, step 4).
2. Delete the duplicate unit run: have the composite call the R0 `mock` assay lane only, or `test-runner` only, and keep the other as an on-demand lane. Two fixes needed in `assay.toml:23-28` and `run-gate.toml:167-181` comment/`test_*` guards. [E] saves 430-550 s of ~1100-1700 s per gate. (Do not do this unless the "argv must stay identical" test is updated with it.)
3. Try `-n` with `--dist=loadfile` manually on the `tests/unit` slice in a throwaway worktree to learn whether the suite is xdist-safe before RG-146 lands.
4. Rename `jobs` to `jobs_max` in `assay.toml` (87 lines, `sed`-free: edit via patch tool), and lower the declared 6 to a number the host can sustain (6 workers x ~0.9 cores exceeds the 3-CPU gate cap [E]).
5. Run `assay plan <lane> --file assay.toml` (read-only) for the largest R2 lanes and record candidate counts in COVERAGE-MAP; replace the 58 h budget-sum with a measured total.
6. Prune the 5 R2 lanes with measured `candidate_count=0` per RIGOR "Zero-candidate mutation lanes are deleted" (lines `assay.toml:2050,2311,2468,3658,4255`; keep `p167_created_by_r2`, which is the documented exit-5 case).
7. Use existing `run-gate history <lane>` and `run-gate footprint <lane>` to replace guessed budgets on the 10 slowest lanes (gate, assay, test-runner, schema, frontend-coverage-direct, scale-admission-live).
8. Sweep the `-y` callers (`scripts/fresh-landscape-gate.sh`, `.vscode/run-deploy.sh`) to `--yes` ahead of time: `--yes` has been accepted on `clean`/`up` since long before 7.20, so this is forward compatible [E; verify with `ciu clean --help`].
9. Run `run-gate doctor` and the existing `ciu worktree inspect` fork-point check on the three live worktrees (`.worktrees/{buildkit-config-driven,p220-test-runner-ui,p245-auth-token-path}`) and remove stale ones, as the plan's Wave 4 notes the p220 worktree is stale.

## 6. vbpub-side items filed for dstdns (state check)

- assay (`assay/nyxloom-trove/4-backlog.md`): B001 SQL adapter (RELEASED 2.1.0), B003 zipapp (COMPLETE), B005/B006 (coverage judge, project snapshots; shipped), B036 JS adapter (shipped), B087 JS canary (SHIPPED 9.0.0), B089 (WITHDRAWN, dup of B080), B010 (DONE for assay's scope; remainder in run-gate). Open and relevant: B204/B205 (flaky-kill credit, failing-test id; 11.0.0 known issue; B208 is the v18 carrier).
- run-gate (`run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md`): RG-18 (pg_dump/server version mismatch guard, **OPEN, dstdns-side scope**: `schema-gate.sh` should check it, not run-gate); RG-33/34/49/66/74/76/79/80/98/99/101/103/104 all FIXED or superseded. Open items that gate dstdns's R2 path: RG-89 (observer, in progress), RG-102 (ephemeral duplicates CIU identity, OPEN), RG-132/143 (worker sizing metrics), RG-105 (tester-base default host image).
- ciu (`ciu/KNOWN_ISSUES_TODO_BACKLOG.md`): CIU-34/38/45/48/49/63-69/82 originated from dstdns; the dstdns follow-ups for CIU-48/49 were closed at `dstdns@41898e90`. Fixed by 7.18-7.20 and mentioned as dstdns Shortcomings: CIU-134, 138, 139, 142, 144, 146, 147. Open: CIU-141 (`ciu run`, deferred to v8), CIU-132, CIU-136, CIU-116.
- cmru (`cmru/KNOWN_ISSUES_TODO_BACKLOG.md`): only the 2026-10-04 zombie/init cross-reference to dstdns D-670 (KI-52). No open dstdns-specific cmru item; `cmru versions` adoption is dstdns-owned.

## Caveats

- Run evidence is from 2026-10-03/04 and 2026-10-15/16 only and capped at 10 runs per lane; durations include the ~1 s admission wait. It predates P243-P245 and anything after 2026-10-23.
- Mutation campaign sizes (candidates, survivors) for Python R2 lanes are **not available**: no R2 campaign has run. Section 2 numbers for R2 are budgets and caps, not outcomes.
- Gains in section 3 and 5 are estimates from measured per-candidate RAM/CPU (vbpub `COW-PLAN-OPUS-2026-10-30.md` section 0) and dstdns's own gate timings; none was reproduced on dstdns.
