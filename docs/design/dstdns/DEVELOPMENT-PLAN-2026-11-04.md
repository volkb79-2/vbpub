<!-- origin: 2026-11-04; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/DSTDNS-DEVELOPMENT-PLAN-2026-11-04.md -->
<!-- relation: external-project development plan -->

# dstdns development plan: parallel tracks, tool dependencies, first release (plan memo, 2026-11-04)

Scope: what dstdns develops in the next two weeks (Day 1 = Thu 2026-11-05, Day 14 = Wed 2026-11-18). It covers which
vbpub tool fixes dstdns waits for and which it works around, and how the controller host runs several dstdns stacks
at once while its production game servers are stopped. Plan only: nothing was changed, and every command run for it
was read-only.
State read at dstdns main `2eeb1e49` and vbpub main `0190edc3c`; train11 was releasing assay when read.

Tags: **[F]** fact with a source, **[E]** estimate, **[D]** decision this memo recommends, **[Q]** operator decision.
`RP` = `<controller-program-dir>/reports`, `DS` = `/workspaces/dstdns`, `VB` = `/workspaces/vbpub`.

---

## Executive summary

**The answer in five lines.**
- dstdns can start almost all product work today. The B3 API and infrastructure slices, AUTH-ALWAYS-ON (P245-P248),
  the Wave-3 wiring, the DST bugfixes and the trivial batch need **no** new tool.
- Only three tool items gate anything dstdns needs in the next two weeks:
  - **cmru 7.2.1 installed** before the first GHCR push. It is released but not installed, and the vbpub root config
    still says `ghcr_packages = ["*"]`.
  - **ciu 7.21.1** (CIU-168..175 plus the review fixes) before the v1001 deploy rehearsal.
  - **assay 12.1.0** (candidate fail-fast) to make R2 cheap. It is releasing now, and 12.0.0 already works.
- Everything else upstream is "do not wait": ephemeral runner, cache slice and overlay, tier-aware admission, RG-75,
  CIU-116, rigor-record.
- The first release is a **pipeline-proof release, `dstdns-v0.1.0`**, around Day 4 **[D]**. `1.0.0` stays reserved
  for the D-001 launch bar.
- The controller carries **5 worktree stacks plus main**, with **3-4 gates** at a time; r1002 and v1001 take R2 and
  mock-lane work.

### (1) Dependency matrix: dstdns work stream -> tool fix or feature it needs -> status/ETA -> can start now?

| # | dstdns work stream | Tool fix / feature it needs | Status / ETA | Start now? |
|---|---|---|---|---|
| 1 | Re-baseline main: fresh bake, `fresh-landscape`, composite `gate`, `release-gate` (main last gated 2026-10-16 [F `.run-gate/history.json` mtime]) | none (ciu 7.21.0, run-gate 25.1.0, assay 12.0.0 installed [F]) | n/a | **yes** (Day 1) |
| 2 | B3 API chain: PAGE-1, PAGE-2, RUNS, CLS-REKEY, RPT, A1-* (`docs/plan-DESIGN-AUTHORITY-wave-b3-2026-09-23.md` §4) | none | n/a | **yes** |
| 3 | B3 shared infra: INF-WF-TRANSPORT, INF-WF-DECL, INF-APPCONFIG-a/b | none | n/a | **yes** |
| 4 | AUTH-ALWAYS-ON: P245 finish (O5-O7), then P246, P247, P248 | none (the Authentik grant set was solved in D-695) | P245 branch at checkpoint 4 (`RP/DSTDNS-FINISH-p245-auth-token-path.md`) | **yes** |
| 5 | DURABLE-DEAD-LETTER (D-682) | none; it waits on dstdns P247 and INF-WF-TRANSPORT | ~Day 8 | wait (on dstdns) |
| 6 | Wave-3 wiring and bugs: WORKER-IO-DNS-COVERAGE, DISCOVERY-BODY-WIRE, GEODNS-FLAGS-WIRE, DST-3/7/8/9/11 | none | n/a | **yes** |
| 7 | Trivial batch and release hygiene: DST-30, DST-34, port binds, GATING-FRESH-DEPLOY residuals, tooling-boundary phase 0 | none | n/a | **yes** |
| 8 | Parallel packages that **change a project image** | ciu **CIU-173**: declared `[ciu.worktree] project_images` (partial fix, on branch `ciu-remote-deploy-20261105`) | ships with ciu 7.21.1, ~Day 3 [E] | **yes with a stub**: point the instance-layer `[images.<key>].ref` at `:local-<instance>` (GUIDE §3.2 Images row, `DS/nyxloom-trove/GUIDE.md:130`) |
| 9 | One-step `ciu worktree create --up` | ciu **CIU-161** (merged as `059541163` on vbpub main; not in `ciu-v7.21.0`) | ships with 7.21.1 | **yes with a stub**: `git worktree add` + `ciu worktree adopt` + `ciu worktree up` (`DS/AGENTS.md:73-81`) |
| 10 | Faster composite: drop the duplicate unit run, add xdist workers | run-gate **RG-146** `workers` | released [F `VB/run-gate-project/CHANGES.md:142,187`] | **yes** |
| 11 | dstdns lanes on r1002/v1001 through remote-gate | host prep: DST-26 (runner image), DST-28 (admission object), DST-29 (host cockpit run-gate 25.0.0); the remote-gate `--base` fix (branch `remote-gate-base-20261105`, unmerged) | half a day, Day 1 | **yes**, after host prep |
| 12 | P11 build side: `wheelhouse.py lock` and `stage`, `verify-images`, `bundle` (never run for real [F P4/P8 reports]) | none (needs network only) | n/a | **yes** |
| 13 | P11 first GHCR push | **cmru >= 7.2.1** installed (KI-117) plus the operator's GHCR write token | 7.2.1 is released, but `/opt/mdt/venv` and `cmru-src-venv` both carry **7.2.0** [F]; vbpub root `cmru.orchestration.toml:275` has `ghcr_packages = ["*"]` [F] | **wait** for the install (minutes) and the token |
| 14 | P11 r1002 qualification: live lanes against the released digests | items 11 and 13, plus a GHCR read token on r1002 | after 0.1.0 | wait (on the release) |
| 15 | P8/P11 v1001 deploy rehearsal (`ciu up --host`) | **ciu 7.21.1**: CIU-168..171, CIU-175 (secret exposure: a non-thin push ships `.ciu/`, `ciu.env` and `.git`), and review findings F1-F5 (`RP/ciu-remote-deploy-REVIEW.md`, verdict FIX FIRST) | fix round + `ciu-gate` lane (10 min) + cmru release, ~Day 3 [E] | **wait**. Stub now: `stage-bundle` plus `ciu check` from an empty directory. **Never** run a non-thin `ciu up --host` on 7.21.0 |
| 16 | Signed bundles | DST-38 (the operator's minisign key) | operator | **yes with a stub**: an unsigned 0.1.0 for staging only; signing is mandatory before any production deploy (P10 design) |
| 17 | Rigor record per release | cmru 7.3.0 `record_step` (rigor C-1/C-2, branches `rigor-c13`/`rigor-c2` unmerged) and the `rigor-record` tool | ETA unknown | **do not wait**: P6's `release-evidence/gate.json` is the interim record |
| 18 | Async R2 on the latest release (62 lanes on `test-runner-r2` [F `run-gate.toml`]) | assay **12.1.0** `judge.mutation.candidate_fail_fast` (WP1), B219/B220/B222 | releasing in train11 now, ~Day 1-2 | **yes** (12.0.0 runs it today; repin when 12.1.0 lands) |
| 19 | `r2_campaign.py` advancing the watermark | vbpub netcup runner must write `<campaign>/verdict.json` (unfiled seam, `RP/dstdns-cmru-p9e-p10-REPORT.md:39`); DST-37 jobs clamp | S, ~Day 3 [E] | **yes with a stub**: the phase-1 supervisor queue; move the watermark by hand from the verified closeouts |
| 20 | Kill confirmation (`--confirm-kills`, DST-39) | assay **B204** (OPEN) | not scheduled | **do not wait**: replay disagreements serially (RIGOR policy) |
| 21 | Native R2 for the 13 service-needing lanes; EPHEMERAL-LANE-RUNNER | run-gate **RG-102** + ciu **CIU-141** `ciu run` (+ RG-73) | OPEN, "after the release wave" [F backlogs] | **wait**; do no dstdns work on it |
| 22 | More than 3 R2 workers per host | tree-cache slice and overlay (Addendum 2: AD2-0..5), RG-163 (shm size) | weeks | **do not wait**: btrfs `cow` at `--jobs 3` (the measured knee) |
| 23 | RAM-aware gate admission | AWP-2/3 (cgpd predicate, run-gate live admission) | weeks | **do not wait**: count admission plus the PSI rule |
| 24 | Delete the hand-rolled throwaway-Postgres scripts | run-gate **RG-75** | OPEN | **do not wait**: the scripts work (P244) |
| 25 | Worktree borrowing main's vault | ciu **CIU-116** | OPEN | **do not wait** (D-666: share nothing) |
| 26 | `cmru versions` gaps: Hub 429, pre-release-only PyPI, >5000-tag repos, digests for only 5 of 21 OCI targets | cmru, **not filed** [F: no match in `VB/cmru/KNOWN_ISSUES_TODO_BACKLOG.md`] | file now | **do not wait**: hand pins with reasons (P3 F1/F3) |

### (2) Recommended parallel tracks (sizing, agents, order)

Sizes are agent-days of implementation plus gate. "Agents" counts concurrent implementers. Each merge gets one fresh
independent reviewer (AGENTS §3 item 6). Model routing follows HANDOFF-STATE-20261105: Sonnet agents, and Codex
`gpt-6-luna` for docker-free implementation steps.

| Track | Content, in order | Size | Agents | Stack | Start |
|---|---|---|---|---|---|
| **T0 Controller** | merges (serial, D-625), post-merge gates, the CONTROLLER-BRIEF rewrite (stale since 2026-10-15), release cuts | continuous | controller | main | Day 1 |
| **P-AUTH** | P245 finish, then P246 (delete the switch), P247 (internal service auth), P248 (edge route) | 1.5 + 2 + 2 + 1 d | 1 | live stack (Authentik) | Day 1 |
| **P-B3a** | PAGE-1, then PAGE-2; later A1-API-EXPLORE, A1-CORPUS | 3 + 3 d | 1 | gate stack | Day 1 |
| **P-B3b** | RUNS, then CLS-REKEY, then RPT (after INF-WF-DECL); later A1-CLS, A1-UI | 1.5 + 4 + 4 d | 1 | gate stack; live for RPT's browser proof | Day 1 |
| **P-INF** | INF-WF-TRANSPORT, then INF-WF-DECL; INF-APPCONFIG-a, then -b; then DURABLE-DEAD-LETTER (after P247), COMMON-CONFIG-RETIRE, A1-AUTH-CONFIG (after P247) | 2 d each | 1-2 | gate stack | Day 1 |
| **P-WIRE** | WORKER-IO-DNS-COVERAGE (removes the only release waiver), DISCOVERY-BODY-WIRE, GEODNS-FLAGS-WIRE, DST-9, DST-7, DST-8, DST-3, DST-11 | 1-2 d each | 1 | gate stack | Day 2 |
| **P-HYG** | DST-34 (BUILD_VERSION = release version) and DST-30, PORT-BIND (OTel collectors and stats-exporter to loopback or gated), GATING-FRESH-DEPLOY F5/F10, tooling-boundary phase 0, the plan's trivial batch, DST-5 | 2-3 d | 1 | gate stack | Day 1 |
| **R-REL** | P11: wheelhouse lock, release-gate on main, waivers, `cmru release dstdns`, r1002 qualification, v1001 rehearsal | 3-4 d | 1 + controller | main + hosts | Day 1 |
| **R-HOST** | remote-gate for dstdns: merge `remote-gate-base-20261105`; DST-26/28/29 on both hosts; v1001 disk (95% [F mutants REPORT]) | 0.5-1 d | 1 | hosts | Day 1 |
| **Q-MUT** | finish mutants-phase1 (dns and http lane verdicts on r1002), merge, DST-35/36 | 1 d | 1 (existing) | r1002 | Day 1 |
| **Q-R2** | async R2 on the latest release; root-cause each survivor, fix, patch release; repin assay 12.1.0 and adopt fail-fast | continuous | 1 | v1001 + r1002 | at 0.1.0 |
| **Q-SPEED** | dedupe `test-runner` vs `mock`, xdist `workers` (RG-146), budget refit from `run-gate footprint`, flake inventory (operator directive) | 2-3 d | 1 | gate stack | Day 2 |

Peak: about 9 implementers, 2 reviewers and the controller. Stacks are the binding resource, not agents (section 4).

**Order (critical path).**
1. Day 1: re-baseline main, install cmru 7.2.1, prepare the hosts, merge mutants-phase1 and docs-zfs.
2. Day 2-3: a full `release-gate` on main.
3. Day 3-4: cut 0.1.0, then run the r1002 qualification.
4. Day 4-5: the v1001 rehearsal (after ciu 7.21.1), then the first R2 campaign on 0.1.0.

The product tracks run beside this from Day 1.

**Hard serialisations to respect** (beyond the B3 plan's own list, its §5.1):
- DST-34 edits `appconfig/bootstrap.py`, so it merges before INF-APPCONFIG-a.
- A1-AUTH-CONFIG merges after P247.
- DURABLE-DEAD-LETTER merges after INF-WF-TRANSPORT (which owns `workflow_dlq`) and after P247.
- RPT merges after CLS-REKEY and INF-WF-DECL.
- INF-APPCONFIG-a keeps `appconfig/loader.py` importable, because `release_r3`'s import-break canary targets it.

### (3) What to WAIT for and what NOT to wait for

**Wait for these. Each blocks one concrete step.**
1. **cmru 7.2.1 installed** in `/opt/mdt/venv` and `cmru-src-venv` (KI-117), or an explicit package list in vbpub
   `cmru.orchestration.toml:275`.
   - Blocks: the first `dstdns-*` GHCR push.
   - Reason: a vbpub `cmru cleanup` under 7.2.0 with `"*"` deletes old versions of every package of the owner,
     dstdns included (memo V1).
2. **ciu 7.21.1.** Blocks: the v1001 rehearsal and any `ciu up --host`. It must carry:
   - CIU-175, so that `.ciu/`, `ciu.env` and `.git` never ship (the secret store);
   - CIU-168/169/170/171;
   - the review fixes, mandatory F1 (`_remove_identity_networks` disconnects foreign endpoints of prefix-matched
     networks) and F3 (`eval "$(ciu env print)"` hides a failed `env print`);
   - CIU-173 (declared `project_images`) and CIU-161.
3. **assay 12.1.0.** Blocks nothing; it is wanted. `candidate_fail_fast` cuts campaign wall time by an estimated
   2.5-4x, because 82-98% of candidates die (`RP/PARALLELISATION-DESIGN-MEMO-2026-11-05.md:13-19`).
   - Adopting it means removing `-x` from the lane argvs that carry it (assay CHANGES WP1).
   - The mutants work put one there: `webapp_server_main` relies on `-x`.

**Do NOT wait for these. Proceed or work around.**
- RG-102 + CIU-141 (ephemeral, `ciu run`): keep exec-mode gate runners; R2 runs on `test-runner-r2` (ephemeral, cgprofile
  observer), proven on r1002 [F P9 REPORT]. The 13 service-needing R2 lanes stay "partial" in the record.
- Cache slice, overlay and tmpfs-worker (AD2, RG-163): btrfs `cow` at `--jobs 3`, which is the measured knee
  (`RP/dstdns-parallelism-REPORT.md`, iobench 2.9x at 3).
- Tier-aware admission (AWP-3): keep count admission; PSI `some avg10` < 25 stays the stop rule (`DS/AGENTS.md:140`).
- RG-75, CIU-116, CIU-173's automatic classification, cmru 7.3.0, rigor-record and B204: work around each as in the
  matrix.
- ciu R2 itself: dstdns never waits on a tool's R2 (section 6).

### (4) Running parallel dstdns stacks on the controller (8 cores, 15.6 GiB RAM, 69 GiB swap [F `/proc/meminfo`])

**Instance identity is ciu's.** Use one managed worktree per package under `DS/.worktrees/<name>`. Each gets its own
instance id, network, compose projects, vault, consul, redis, postgres and runners (GUIDE §3.1-§3.2). Never write an
instance id literally.

**Port strategy: nothing to allocate.**
- Services listen on internal ports inside their instance network.
- Access goes through `ciu resolve` and `ciu exec`, and the devcontainer is attached to the instance network.
- The public edge is hostname-routed and is off until P248 (D-674 interim).
- Worktrees never deploy `observability` or `external` (GUIDE §3.1). Those profiles hold the only all-interface
  publishers that would collide between instances: the two OTel collectors (unconditional) and docker-stats-exporter
  (`default(true)`) (`DS/docs/PORT-STRATEGY.md:84-95`).
- Only main runs observability.

**Two stack classes [E from `DS/docs/RESOURCE-GOVERNANCE.md` §6.1 and the runner limits].**
- **Gate stack** (`[ciu.worktree].up = core, db, runner`):
  - idle: about 1 GiB;
  - during a composite: about +3 GiB (`test-runner` `mem_limit` 3000M; `test-runner-ui` peak 2.6 GiB,
    `tools/test-runner-ui/ciu.defaults.toml.j2:33`, sequential inside the composite).
- **Live stack** (phases 0-5,7 + seeded, no edge or observability):
  - idle: about 2.5-3 GiB once Authentik runs per instance (after P246);
  - plus about 3 GiB during the `release` lane (897 s [F P245]).

**Capacity while the production game servers are stopped.**

| Resource | Ceiling | Why |
|---|---|---|
| Worktree stacks up | **5** (the existing `max_concurrent_instances`, `ciu.global.defaults.toml.j2:1501`), at most **2 live** | 5 idle stacks ≈ 5-7 GiB beside the ~7 GiB the devcontainers and host already use |
| Concurrent gate lanes on the controller | **3**, a 4th only while PSI memory `some avg10` < 10 | the published admission is 4 [F `run-gate admission show`]; 3 composites ≈ +9 GiB, which goes into zswap and swap, as the overcommit design allows |
| Image bakes | **1 at a time** | a bake bursts all 8 cores; the BuildKit cache is shared, so serialising costs little |
| CPU jobs | about **9** single-thread jobs (cores + 1, AGENTS §3.2) | the composite is mostly serial pytest, about 1-2 cores |
| Stop signal | PSI CPU or memory `some avg10` > 25, or heavy swapping | D-696 |

**Stack leasing.**
- Implement first; bring the instance up for the gate (`ciu worktree up <name>`).
- Run `ciu worktree rm <name> --yes` from main (or `ciu down`) when the agent will code for more than an hour.
- Verify teardown by exact prefixes (GUIDE §3.3).
- This keeps 7-9 implementers inside 5 stacks.
- Whether main's primary instance counts against the cap is **unverified**: check `ciu worktree list` before raising it.

**What slows parallel work, and the counter for each.**

| Slowdown | Counter |
|---|---|
| The shared `:local` image tags (CIU-173 gap) | instance-layer ref override now; declare `project_images` in ciu 7.21.1 |
| The composite takes 18-28 min and runs the unit suite twice | Q-SPEED |
| B3 slices share generated files (`api-surface.toml`, both `openapi.json`, `schema.d.ts`) | regenerate at merge, never hand-merge (B3 plan §5.1) |
| `assay.toml` and `run-gate.toml` are hotspots | rebase before merge; the controller merges serially |
| CIU-164: `ciu clean` and `ciu up` fail the root-helper preflight outside `dstdns-devcontainer-vb` | run ciu and run-gate inside that devcontainer |
| Disk is at 86% on the controller (98 GiB free [F]) | prune the orphaned `dstdns/*:latest` tags by exact name after P2's `:local` rename |

**Test-runner containers.**
- Gate lanes run in each worktree's own `test-runner` and `test-runner-ui` (exec mode, D-666).
- R2 runs in the released `dstdns-test-runner` image on the hosts (`test-runner-r2`).
- The hosts also take the R1 whole-target mock lanes once R-HOST is done. The controller keeps the stack-bound lanes
  (schema, live, composite).

**If the game servers come back.** The shared-prod rules return (`DS/nyxloom-trove/DECISIONS-IN-FORCE.md:111`,
MemAvailable > 1.5 GiB, PSI memory avg60 < 10):
- drop to 3 stacks and 2 gates;
- move every R1 batch to the hosts.

### (5) Risks and operator decisions

| # | Item | Recommendation |
|---|---|---|
| Q1 | **First release number: 0.1.0 or 1.0.0.** The memo says 1.0.0 (`RP/DSTDNS-CMRU-ADOPTION-MEMO-2026-11-05.md:343`). However, the MAJOR guard (`DS/scripts/release/gate.py:171-191`) watches all of DDL, the **whole** `appconfig` package and `workflow_contracts`/`workflow_envelopes` (`DS/release/major-guard.toml`). Every B3 INF slice, CLS-REKEY, RPT and DURABLE-DEAD-LETTER trips it. At 1.0.0 that means 2.0.0, 3.0.0 and so on within weeks; at 0.1.0 under today's guard, the first trip forces 1.0.0 (cmru maps `!` to major, `VB/cmru/src/cmru/version.py:327-406`) | **[D] 0.1.0.** Change the guard at 0.x: a contract change needs the **minor** component raised (SemVer §4, no compatibility promise below 1.0). Rule while on 0.x: no `!` subjects; the controller passes `--minor` on a guarded release and records it in the D-record. Optionally file a cmru KI for opt-in 0.x semantics (`!` gives minor while major == 0). Move `rigor-waivers.toml` `expires_before = "1.1.0"` to `"0.3.0"`. A dstdns-only change (S, P-HYG) |
| Q2 | **What 1.0.0 means** | **[D]** the D-001 launch bar (`DECISIONS-IN-FORCE.md` §9): single-host `full`, auth ON with the 3-role matrix (P245-P248), React UI, SkyWalking, Authentik validated; plus B3 closed, the first green R2 watermark and signed bundles |
| Q3 | **What 0.1.0 must contain** | **[D]** today's main plus mutants-phase1 (and P245 only if it is merged by the cut). Required: a green `release-gate` (R0 composite, R1 39 lanes with named waivers, R3 canary), a bundle that passes `ciu check` from an empty directory, private GHCR images with digests, a committed wheelhouse lock, the r1002 qualification, and DST-34. The v1001 rehearsal gates **promotion** (the `stable` pointer), not the tag. No production deploy |
| Q4 | **minisign custody (DST-38)** | the operator generates the key and keeps the private key off every repository and host cockpit (`sign.py` refuses in-repo or group-readable key files). Decide **before 0.2.0**; 0.1.0 may ship unsigned for staging |
| Q5 | **GHCR credentials** | `gh auth refresh -s read:packages,write:packages` on the release cockpit; one fine-grained `read:packages` token per host, delivered as an `ASK_EXTERNAL` host secret. Images stay private (`DS/AGENTS.md:136`) |
| Q6 | **Hold on new capabilities (OW-7) until B3 closes** (`DECISIONS-IN-FORCE.md:181`) | keep the hold for these two weeks; B3 is the product track. Revisit at CP5 |
| Q7 | **Capacity while prod is stopped** | approve 5 stacks + 3-4 gates on the controller; automatic fallback when prod resumes (section 4) |
| Q8 | **B3-RPT model exception** (the plan marks it "Opus-worthy") | approve one Opus carve or implementer for RPT, or accept Sonnet with a full-tier brief |
| Q9 | **Production data across a 0.x minor reset** | no decision needed until production: 0.x is disposable (§4.1a). Memo Q1 (forward migrations) is due before 1.0.0 |
| R1 | The release path was never run end to end: `gate.py` with real services, the `run-gate --json` sequence shape, `wheelhouse.py lock`/`stage`, `verify-images`, bundle push, and a possible `dstdns-app-base:latest` leak to the registry (P4 finding) | budget one fix day in R-REL; the first real run is the proof, not a formality |
| R2 | CIU-175: a non-thin `ciu up --host` on 7.21.0 copies the cockpit secret store to the host | hard rule: no non-thin remote deploy before 7.21.1; keep the P8 runbook's `find` precondition |
| R3 | Public exposure on v1001: the OTel collectors and stats-exporter publish on `<host-address>` (`PORT-STRATEGY.md:87-89`) | the rehearsal excludes `observability` and phase 6, or PORT-BIND merges first |
| R4 | The admission object vanished once (`RP/DSTDNS-FINISH-p245-auth-token-path.md`, finding 1) | `gate.py` refuses without it (fail-closed); file a run-gate item if it recurs |
| R5 | v1001 disk at 95%; the planned host reinstall (new disk layout) would interrupt campaigns | free space in R-HOST; schedule the reinstall between campaigns (R2 resumes by source bytes) |
| R6 | Merge-conflict churn with 7+ parallel packages | serial merges, rebase before merge, generated files regenerated, DDL changes batched per release |

### (6) Two-week schedule and checkpoints

| Day (2026) | Release and infrastructure | Product | Quality | Checkpoint |
|---|---|---|---|---|
| D1 Thu 11-05 | install cmru 7.2.1; merge `remote-gate-base`; DST-26/28/29 on both hosts; fresh bake + `fresh-landscape` + composite on main; wheelhouse lock | P245 O5-O7; PAGE-1, RUNS, INF-WF-TRANSPORT, INF-APPCONFIG-a carved and dispatched; P-HYG starts | mutants-phase1 host verdicts; merge it and docs-zfs | **CP0**: main green from scratch, tool installs done, CONTROLLER-BRIEF rewritten |
| D2 Fri 11-06 | full `release-gate` on main (2.5-4 h); repin assay 12.1.0 when released | P245 merged; P246 starts; P-WIRE starts | Q-SPEED starts | |
| D3 Sat 11-07 | waivers settled; GHCR tokens; ciu 7.21.1 fix round and release (vbpub) | PAGE-1, RUNS in review | first R1 batches on the hosts | **CP1**: release-gate green with named waivers; ciu 7.21.1 released |
| D4 Sun 11-08 | **`cmru release dstdns --set-version 0.1.0`**; r1002 qualification | INF-WF-TRANSPORT merged | | |
| D5 Mon 11-09 | v1001 rehearsal (install, `up --host`, rollback, clean); `stable = 0.1.0` | CLS-REKEY, PAGE-2 start; DST-34 merged | **R2 campaign on 0.1.0** (v1001 + r1002, `--jobs 3`, fail-fast) | **CP2**: 0.1.0 released, staged and promoted; R2 running |
| D6-D8 | CIU-173 `project_images` declared; `ciu worktree create --up` back in AGENTS | P246, P247 merged; INF-WF-DECL, INF-APPCONFIG-b merged; WIRE items merge | survivors from 0.1.0: root cause, then fixes | **CP3 (D8)**: auth on in every instance; phase 6 conditions met except P248 |
| D9-D10 | **0.2.0** (DDL batch: CLS-REKEY, RUN-KIND, ATTEMPT-RUNNING; a minor under the 0.x guard); R2 rebased to 0.2.0 | P248; DURABLE-DEAD-LETTER starts; RPT starts | composite under 15 min (Q-SPEED target [E]) | **CP4 (D10)**: 0.2.0 staged, the WORKER-IO-DNS waiver removed |
| D11-D14 | 0.3.0 or a patch per survivor fix; minisign pinned (Q4) | RPT, PAGE-2, DLQ in review; A1 slices begin | flake inventory closed or filed | **CP5 (D14)**: B3 API chain at least 60% merged; decide the B3 close date, OW-7 unlock and the EPHEMERAL revisit against RG-102/CIU-141 status |

---

## 1. Where dstdns stands (facts)

**Main (`2eeb1e49`).**
- The cmru adoption P1-P10 is merged: config, the `[images.*]` table, `cmru versions` pins, the bake/bundle/pins/wheelhouse
  scripts, preflight and gate, the deploy path, `r2_campaign.py` and `sign.py`.
- Also merged: tools-adoption (assay 12.0.0 pyz, floors `ciu>=7.21.0`, `run-gate>=25.0.0`, `assay>=12.0.0`,
  `DS/requirements.txt:62-79`), P220 (UI runner, Node 26.12.1), the ciu 7.21 surface migration and D-696 (no agent cap).
- Never exercised end to end: `gate.py` against real services, `wheelhouse.py` against real PyPI/GitHub, `verify-images`
  and `bundle` against a real registry, a signed `get.py install`, `r2_campaign.py`
  (P4 `:16`, P6 `:84`, P8 `:58,:67`, P9e/P10 `:15,:29` reports).

**Open branches.**

| Branch | State |
|---|---|
| `p245-auth-token-path` (`bcbd1dd2`) | checkpoint 4: release lane 41 → 23 failed (the D-677 class-b residuals); O5-O7 open |
| `mutants-phase1-20261105` (`76f9d637`) | every locally known survivor killed; host verdicts pending on r1002 |
| `docs-zfs-20261105` | docs only |
| `p215-inf-wf-transport`, `p216-inf-wf-decl`, `p200-b2-*`, `d333-verify` | stale; re-carve, do not revive |

**Gate evidence.**
- `.run-gate/history.json` was last written 2026-10-16.
- `release-r1` ran once (P5), with exactly one red: `worker_io_dns_operations`, waived to `WORKER-IO-DNS-COVERAGE`
  (`DS/release/rigor-waivers.toml`).
- No main stack runs today. The only dstdns containers are an old `test-runner:latest` and the devcontainer.

**R2.**
- 62 lanes are on `test-runner-r2` with the cgprofile observer.
- Phase 1 on r1002 and v1001 found real survivors in landscape_cache, result_storage, webapp_server_main, worker_io
  dns/http and inf_landscape (`RP/dstdns-r2-phase1-RESULTS.md`), all killed on the mutants branch.
- The watermark is still `none`.

**Stale owner documents.**
- `DS/nyxloom-trove/CONTROLLER-BRIEF.md` describes 2026-10-15: its queue item 1 is P244, which merged as D-689.
- `docs/RUN-GATE.md:90` still lists the orphan lanes that `release-r1` now runs.
- T0 rewrites both on Day 1.

## 2. Tool status relevant to dstdns (vbpub, 2026-11-04 evening)

| Tool | Released / installed | Pending, and what dstdns gets |
|---|---|---|
| ciu | 7.21.0 / 7.21.0 | 7.21.1: CIU-161 (on main), CIU-168..175 + `ciu-gate` lane (branch `ciu-remote-deploy-20261105`, review FIX FIRST: F1 P2, F2-F5 P3). CIU-172/174 partial. Not this window: CIU-141, CIU-116 |
| run-gate | 25.1.0 / 25.1.0 (host cockpits 25.0.0, DST-29) | RG-102/RG-73 (ephemeral via ciu), RG-75, RG-163 open; RG-146 xdist shipped |
| assay | 12.0.0 / 12.0.0 (dstdns pyz 12.0.0) | 12.1.0 releasing now: candidate fail-fast (WP1), B219/B220/B222 cheaper materialize, B223 tmpfs-worker (needs RG-163 for container shm), `analyze export`. B204 open |
| cmru | 7.2.1 tagged / **7.2.0 installed** | 7.3.0: KI-118 `exclude_paths`, rigor C-1/C-2 `record_step` (branches unmerged). The `versions` gaps (P3 A-G) are unfiled |
| netcup runner (vbpub `scripts/netcup/runner`) | in use | remote-gate `--base` fix unmerged; `verdict.json` writer and jobs clamp (DST-37) not done |
| mdt host-setup / cgpd | cgpd 2.0 on both hosts; the controller still runs 1.1.0 | the AD2 tier fix and cache slice are weeks out; dstdns R2 runs on the hosts, so it does not need the controller's cgpd |

## 3. Track detail

**P-AUTH (security, first).**
- Finish P245 from its brief (`DS/.worktrees/p245-auth-token-path/nyxloom-trove/reports/dstdns-P245-BRIEF.md`): O5 auth-on
  evidence, O6 per-posture `N passed, 0 skipped`, O7 composite. Then merge.
- P246 to P248 are carved and frozen (D-691..D-693).
- P248 meets EDGE-CONTROLLER-ROUTE and lifts the D-674 interim (the six conditions in D-692).
- Each instance then runs Authentik, which makes the live stack about 1-2 GiB heavier: plan stacks accordingly.

**P-B3a / P-B3b (the product track; B3 closes the OW-7 hold).**
- Use the B3 plan's two-slot order, run as two chains now that D-696 removed the cap. PAGE-1 and RUNS overlap is allowed
  with regenerate-at-merge (B3 §5.1).
- CLS-REKEY and RPT change DDL, so they trip the MAJOR guard (minor under Q1).
- The A1 thin-router slices come last per router. Their oracle is byte-identical OpenAPI.

**P-INF.**
- INF-WF-TRANSPORT before DURABLE-DEAD-LETTER (same `workflow_dlq` ownership).
- INF-APPCONFIG-a/b, then COMMON-CONFIG-RETIRE (worker-db onto `common.appconfig`).
- Every appconfig or workflow-contract slice trips the guard: batch their merges just before a release.

**P-WIRE.**
- WORKER-IO-DNS-COVERAGE first: it removes the only release waiver, and the mutants branch already added dns tests.
- DISCOVERY-BODY-WIRE and GEODNS-FLAGS-WIRE turn empty analytics into data (`domain_services`, `domain_geodns_flags`).
  That is the visible product gain in this window.
- DST-9 (KeyError), DST-7 (contract 500s), DST-8 (convention guards), DST-3 (Authentik hook replay), DST-11 (Redis
  ACL passwords, security).
- DST-6 (`/ws`) after P246.

**P-HYG.**
- DST-34: compose `BUILD_VERSION` from `[product].version`.
- DST-30: serve the version rather than a literal.
- PORT-BIND: loopback or a fail-loud gate for the OTel collectors and the stats-exporter.
- GATING-FRESH-DEPLOY F5/F10.
- Tooling-boundary phase 0: the stack-cap comment, delete `scripts/devcontainer-exec.sh` and `.vscode/ciu-move.sh`.
- The plan's trivial list.
- The 0.x guard change (Q1).

**R-REL (P11).**
1. Lock the wheelhouse.
2. Install cmru 7.2.1.
3. Run `release-gate` on main. Read each verdict separately and run `assay verify` on it.
4. `cmru release dstdns --set-version 0.1.0` (dry run first).
5. r1002 qualification against the digests.
6. v1001 rehearsal after ciu 7.21.1, per the P8 runbook steps 0-6.
7. The promotion commit.
8. Start Q-R2 on the tag.

**Q-R2 (latest release only, AGENTS §3.1).**
- Run each campaign through the supervisor queue with `--jobs` no higher than each lane's `jobs_max` (3; 1 for
  `cw2b_schema`).
- On a newer release: stop at a candidate boundary and restart on the new tag with `--resume`. Kills key on source
  bytes and carry over. Older tags are recorded `R2: superseded`.
- Every survivor gets a root cause, then a fix package, then a patch release.

## 4. Host use across all three hosts

| Host | Takes | Never |
|---|---|---|
| Controller (prod stopped) | 5 worktree stacks + main (full, with observability); composite and live gates; `release-gate`; bakes; the release transaction | `--cgroupns=host`, host-impacting tests, all-container inventories |
| r1002 | R2 campaigns; R1 mock batches through remote-gate; the 0.1.0 qualification (live lanes against digests) | <protected-host>-style protected services; edge changes |
| v1001 | R2 campaigns; the bundle deploy rehearsal (after ciu 7.21.1, no observability and no edge) | a run while disk is at 95% |

Keep about 5 single-thread jobs per host (operator's 500% rule) and stop above PSI 25.

## 5. "ciu lane R2 runs async while further development of ciu continues"

**The release gate does not include R2.** ciu 7.21.1 releases on:
- `ciu-gate` (R0+R1+R3; PASS in 10 min on v1001, `RP/ciu-remote-deploy-REPORT.md:49`);
- the `enroll` lane inside `cmru tester-gate --enable-docker`;
- an independent review.

**R2 then runs async** on the latest ciu tag, lane `ciu` (`VB/ciu/run-gate.toml:12-25`; `assay.toml` `jobs = 1`,
`max_mutants = 10000`, 5 min per candidate).
- It needs the cgroup observer: its last run ERRORed with `CGROUP_OBSERVATION_UNAVAILABLE`.
- It needs assay 12.1.0 `candidate_fail_fast`. Without it, a full-suite run per candidate makes the campaign
  multi-day [E].
- Measure first:
  - `assay plan` (candidate count);
  - time to first failure (M1);
  - two concurrent ciu R0 containers (M4), before raising `jobs` above 1.

**ciu development is never blocked by its R2.** Work continues in parallel worktrees:
- the review F2-F5 items;
- CIU-173 automatic classification;
- CIU-174 live proof;
- CIU-141 `ciu run`;
- a candidate "registry login from the host secret" item (P8 `:60`).

**Each new ciu release moves the campaign to the new tag** (stop, resume on the new tag; unchanged kills carry). This
keeps R2 on the latest release (operator rule), so an older ciu release never gets a retro run. Survivors become a fix
and a patch release.

**dstdns adopts each ciu release at once** (D-672 exemption): raise the `requirements.txt` floor, declare
`project_images`, and switch AGENTS back to `ciu worktree create --up`. It does not wait for ciu's R2 verdict.

## 6. vbpub items to land or file for dstdns (owner: the vbpub controller)

1. Install cmru 7.2.1 in `/opt/mdt/venv`, `cmru-src-venv` and both host cockpits. Until then, give vbpub root
   `ghcr_packages` an explicit list.
2. ciu 7.21.1: fix F1 and F3 (F2, F4 and F5 are cheap, so do them in the same round), then gate and release.
3. Merge `remote-gate-base-20261105`. Netcup runner: write `<campaign>/verdict.json`, and clamp `--jobs` to the lane's
   `jobs_max` (DST-37). Check the next free ids first, then file both.
4. File in cmru: the `versions` gaps from P3 (Hub 429 backoff and target filter; pre-release-only per-target skip;
   >5000-tag repositories; digest recording for every OCI target; offline `check`). Optionally add 0.x conventional
   semantics (Q1).
5. assay 12.1.0: drop the `.assay-inbox/release.json` notice (sha256), so dstdns repins.

## 7. Sources

- dstdns:
  - `AGENTS.md` (§3, §3.1, §3.2, §4.1a)
  - `nyxloom-trove/GUIDE.md` (§3.0-§3.5, §4.4)
  - `nyxloom-trove/DECISIONS-IN-FORCE.md` (§5, §6, §9, §10)
  - `nyxloom-trove/CONTROLLER-BRIEF.md`
  - `nyxloom-trove/backlog/INDEX.md`
  - `docs/{PRODUCT-CAPABILITIES-AND-ROADMAP,OPEN-WORKSTREAMS,plan-repair-and-advancement-2026-10,plan-tooling-boundary,plan-DESIGN-AUTHORITY-wave-b3-2026-09-23,RUN-GATE,RESOURCE-GOVERNANCE,PORT-STRATEGY,CORE-WORKFLOW-STATUS,FUTURE-IMPROVEMENTS}.md`
  - `release/{rigor-waivers,major-guard}.toml`, `scripts/release/gate.py`
  - `ciu.global.defaults.toml.j2:1501`, `requirements.txt`
- Reports (RP):
  - `DSTDNS-CMRU-ADOPTION-MEMO-2026-11-05.md`
  - `dstdns-cmru-p1..p10` REPORTs and REVIEWs
  - `dstdns-r2-phase1-RESULTS.md`, `dstdns-mutants-phase1-{REPORT,CONTINUATION}.md`
  - `DSTDNS-BENEFIT-ANALYSIS-2026-11-04.md`
  - `PARALLELISATION-DESIGN-MEMO-2026-11-05.md` + both addenda
  - `dstdns-parallelism-REPORT.md`
  - `ciu-remote-deploy-{REPORT,REVIEW}.md`
  - `DSTDNS-FINISH-p245-auth-token-path.md`
  - `../HANDOFF-STATE-20261105.md`, `../logs/train*.status`
- vbpub:
  - `{ciu,cmru,assay,run-gate-project}/CHANGES.md` and backlogs
  - `cmru/src/cmru/version.py:327-406`
  - `cmru.orchestration.toml:275`
  - `ciu/{run-gate,assay}.toml`
- Operator memory: `feedback-release-then-async-r2`, `feedback-tests-load-independent-host-owned-limits`,
  `feedback-codex-fallback-implementer`, `feedback-one-fable-per-tool`, `feedback-forward-oriented`.
- Live reads: `/proc/meminfo`, `/proc/pressure/*`, `run-gate admission show`, `ciu worktree list`, the dstdns-named
  `docker ps`, installed tool versions.
