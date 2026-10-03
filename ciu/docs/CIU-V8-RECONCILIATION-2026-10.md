# CIU v8 — reconciliation with ciu 7.15 / run-gate 23.9 and the D-647 rulings (2026-10)

- **Date:** 2026-10-03
- **Kind:** architect's reconciliation memo. It is input to the next v8 rewrite, not normative text. `SPEC-V8.md` (8.0.0-draft.7) and `CIU-V8-TESTING-GATE-PROPOSAL.md` (rev 3.4) are deliberately left unedited; §7 lists what changes in them once §6 is answered.
- **Fixed inputs (operator, dstdns `nyxloom-trove/decisions.md` D-645..D-647):**
  1. One gate implementation: run-gate is merged into ciu.
  2. Worktrees own their test environment, and Mode A is retired as a gating mode.
  3. A worktree borrows only declared shared infra. For dstdns that is vault only, never postgres.
  4. Stack admission is a RAM budget from measured per-container footprints, not a count.
  5. ciu 7.15's base36 instance id is final.
  6. Caps live in central config: gates, stacks, agents.
  7. Tool skills ship in each tool's wheel and install into `~/.claude/skills` and `~/.agents/skills`.
  8. AGENTS §4.2a (derive, read, fail, never invent) and §4.1a (disposable landscape, fresh first).
- **Sources read for current state:**
  - ciu: `ciu/CHANGES.md` 7.11.0→7.15.1 plus `[Unreleased]`, `KNOWN_ISSUES_TODO_BACKLOG.md` CIU-80..119, `src/ciu/workspace.py`, and the shared `libraries/worktree` (SPEC + `core.py`).
  - run-gate: `run-gate-project/SPEC.md` R-00..R-44, the `CHANGES.md` `[Unreleased]` and 23.8/23.9 sections, and the `KNOWN_ISSUES_TODO_BACKLOG.md` status table plus RG-55/56/67/70/73..76.
  - estate and consumers: `run-gate.root.toml`, vbpub `AGENTS.md` (gate sections), cmru `docs/SPEC.md` (tester-gate and release gate), dstdns `run-gate.toml`, `tools/test-runner/ciu.compose.yml.j2`, `scripts/{gate-slot,gate-base,schema-gate}.sh`, and `docs/proposals/TOOLING-BOUNDARY-2026-10.md`.
  - v8 set: the round-4 review (T4-01..T4-10, **not yet folded**), the response §1/§5/§6.5/§7.3, the proposal §4.1.10/§4.4/§4.5-H/§4.9/§4.10, SPEC-V8 S1.5, S2.6, S4, S9.5, S13, S14.4–S14.7, S16 (in full), Appendix D.4–D.7, and the demo `ciu.toml` `[testing]` and `examples/ciu.instance.joined.toml`.
- **Not read in full:** SPEC-V8 S5–S8, S10–S12, S17 and the adversarial review beyond R-01. Rows about those sections rely on the round-4 audit and are terse.
- **UNVERIFIED** marks a claim derived but not executed. Nothing was run: no gate, no container, no source edit.

---

## 1. Summary

1. **The v8 gate design is a run-gate subset from 2026-09-02, and run-gate has since moved on.** SPEC-V8 S16.12 carries eight run-gate rules by reference. run-gate now has rules that v8 does not mention:
   - re-attach after client death (R-39, RG-35);
   - progress- and log-judged liveness with `stall_timeout` (R-40, R-40f);
   - per-lane profiling against the cgprofile daemon (R-43, RG-55/57/60);
   - the committed footprint manifest and the `footprint` verb (R-44);
   - durable mutation resume across worktrees via `--state-dir` (RG-38/RG-49);
   - the worktree fork-point base (R-35a, RG-51, ciu CIU-106);
   - assay source mode for vbpub-internal lanes (R-08 rev 11);
   - the `host`/`bare-host` flip (R-42, RG-43);
   - the `run-gate.root.toml` rename and the dedicated `dev-gates` slice (vbpub@41c1cafb3).

   Several S16 rules are now **contradicted** by that behaviour, not merely incomplete. A "merge" therefore cannot mean "implement S16 as written". It means **port run-gate's current behaviour into the v8 entity model and delete what v8 makes redundant.** §3 states that plan.
2. **Posture inversion.** The proposal's R-01 posture is that run-gate's code is lifted and run-gate stays maintained in parallel (proposal §4.1.10, V8-21 "cross-referenced, not folded", V8-24 shared conformance fixtures). D-647 #1 contradicts it. The underlying 2026-09-02 constraint, "every tool usable standalone, no hard dependency", still has to be answered rather than silently dropped (§6 Q1).
3. **Admission as specified cannot work on this host, and it is keyed to the wrong quantity.**
   - S16.6.1 flocks and reads the slice's cgroup directory. From the devcontainer, where every gate starts, `/proc/self/cgroup` is `0::/` and `dev.slice` is absent (live probe 2026-10-03, the consumer input; proposal §4.10 gap 5 "owed").
   - S14.6 caps stacks with a count, which D-647 #4 rules out.
   - The only host-visible, cross-project vantage that exists is the cgprofile host daemon (RG-55 contract, root, `cgroupns=host`). RG-56 already designs admission on its registry. **One RAM ledger in the daemon, used by `ciu up` and `ciu gate` alike,** is the reconciled design (§3.6).
4. **Identity must be re-specified from code, not from the draft.**
   - 7.15 derives `sha256(lexically-canonical physical path) mod 36^6` as 6 base36 characters, in the shared `libraries/worktree` (`core.py:322`).
   - Nested ciu roots compose `<workspace_id>-<root_instance_id>` (`ciu/src/ciu/workspace.py:283-294`).
   - Records live in `<git-common-dir>/.workspace-instances/`, shared with cmru (vbpub@d1eb98770, vbpub@b9cad87c3).
   - SPEC-V8 S4.1.1 (6 hex), S4.5 (owner token), S14.4.1 (lock = checkout directory) and S14.7 (registry `ciu.instance.json` in the state root) all diverge from it.
5. **The v8 set still has unresolved items:**
   - Round 4 is unfolded: 6 BLOCKERs and 4 MAJORs. Only T4-09 touches the gate (run pruning by pid, `CIU_LEASE_FDS`).
   - Appendix D.5, D.6 and D.7 are operator notes that "must be weighed" and were never folded.
   - Proposal §4.9 still says "Open product decisions: None".
6. **dstdns's real use cases fit v8 once five additions are made.** No new top-level concept is needed. The additions are:
   - hermetic lanes in `ephemeral` environments with instance-scoped `image_from` and a correct linked-worktree mount (RG-73, CIU-117);
   - lane-scoped throwaway services (RG-75);
   - project-level join presets (CIU-116);
   - judge-lane import (RG-76);
   - trunk-merge base with sequence-lane base propagation (RG-74).

   With them, dstdns deletes `gate-slot.sh`, `gate-base.sh`, `run-gate.toml` (2131 lines, 142 lanes), the provisioning halves of `schema-gate.sh`/`sql-mutation-gate.sh`, the five `pNNN-assay-schema.sh`, and the test-runner compose `.git` block (§5).
7. **There are 14 interview questions (§6).** Five of them (Q1, Q2, Q4, Q8, Q10) block the S16/S4/S14 rewrite outright.

---

## 2. Delta inventory

Legend:
- **a** — still valid;
- **b** — already shipped in v7.x since the v8 docs (cite);
- **c** — contradicted by current ciu/run-gate behaviour or an operator ruling (cite);
- **d** — open, never resolved across the review rounds (cite).

Several rows carry more than one tag.

### 2.1 Posture, planning set and process

| v8 item | tag | evidence / note |
|---|---|---|
| Proposal §4.1.10 / R-01: "run-gate's functionality lifted; run-gate stays standalone, maintained in parallel" | **c** | D-647 #1 (one implementation). The underlying "usable standalone, no hard deps" constraint (adversarial review §1 item 1, 2026-09-02) is still unanswered → §6 Q1. |
| V8-21 "run-gate SPEC cross-referenced, not folded"; V8-24 shared gate-conformance fixtures between `ciu gate` and run-gate | **c** | Same ruling. V8-24 survives only as the **port oracle** (§3.8), not as a standing parity contract. |
| V8-19 neighbour alignment (run-gate reads `ciu.resolved.toml`); proposal §4.10 gap 8; RG-37 | **c** (moot) | With one implementation, there is no second reader to align. |
| Implementation home `vbpub/ciu8` (operator 2026-09-03; handoff note) | **a** | Still valid. ciu8 holds only a trove: P001 carve REJECTed round 1 (vbpub@8dd525cc0), and the pointer was commented out (vbpub@7cd835a06). The gate now joins ciu8's scope (§3.1). |
| Proposal §4.9 "Open product decisions: None" | **c** | The consumer input §"Open product decisions" and D-646 opened five; D-647 closed some; §6 lists those still open. |
| Round 4 (T4-01..T4-10): handoff "next session step 1", never done | **d** | `grep T4- SPEC-V8.md` has 1 hit (the D.5 preamble). Proposal and response have 0. The round-4 fix audit also downgrades T3-02/T3-03/T3-04/T3-06/T3-08 to "landed-and-broke". |
| Appendix D.5 (owner_id too convoluted; protection flags instead), D.6 (RG-55 profiling), D.7 (dev-gates slice, daemon placement/liveness/watcher) | **d** | Each is marked "pending — not yet a change". |
| Consumer input 2026-10 (CIU-116..119, RG-67/70 notes, RG-73..76, NL-29) | **d** | Filed and not folded (handoff step 5). |

### 2.2 Identity, instances, locks (S4, S14)

| v8 rule | tag | evidence / note |
|---|---|---|
| S4.1.1 `instance_id` = first 6 hex of SHA-256 of the physical path | **c** | 7.15 uses 6 base36 of `sha256 mod 36^6` (`libraries/worktree/src/worktree/core.py:310-331`, vbpub@d1eb98770). D-647 #5 makes that final. CIU-119 covers it. Every `98535c` literal in the spec, proposal and demo is stale. |
| S4.1.1 identity of a nested root (monorepo child): "the checkout's physical path", where the checkout root is the nearest `ciu.toml` (S1.5.1) | **c** | 7.15 composes `<workspace_id>-<root_instance_id>` when the ciu root is not the git checkout root (`ciu/src/ciu/workspace.py:283-294`). A v8 that hashes the child dir alone changes every child id at cutover. UNVERIFIED that any estate monorepo child currently runs a stack under the composed form. |
| S4.1.1 owner token `owner_id`; S4.5.1 `ciu.owner` label; S4.5.3 adoption/refusal | **d** | D.5: the operator judged it too convoluted. With base36 the space is about 31 bits (36^6 ≈ 2.2e9, versus 24 bits for 6-hex), which is close to D.5's "8 hex" ask. The shared library refuses two live records claiming one id (worktree SPEC "Identity and paths"). → §6 Q10. |
| S4.1.2 cold `--move`, `--fresh` for copies | **a** / **d** | Coherent per round-4 T3-05 "landed". CIU-115 is unaddressed: operator ruling vbpub@e9eb0aac5 says an outdated-format record is **repaired in place, not refused**. v8 states no repair-in-place for its own generated files. CIU-119 (3) also asks for `ciu clean --identity <old>`, and nothing in v8 can address a retired identity (the D-645 fallout: 32 containers and 3 volumes removed with raw `docker rm`). |
| S4.1.4 sentinel bind proves the physical path | **d** | T4-03 (BLOCKER): it mutates an immutable release and needs Docker on docker-optional hosts. The gate side is unaffected. |
| S4.2 derivation, S4.3 uniqueness, S4.4 identities as data | **a** | Still the right design, and it kills dstdns's five derivations of the runner name (CIU-118). S4.4 needs a **query/exec verb for any instance**: `ciu instance exec --env` covers gate environments only (S14.6.3; CIU-118 **d**). |
| S4.5.1 fixed `ciu.*` labels, enumeration by label | **a** | It is also what makes `ciu clean --identity <old>` implementable. |
| — protection of a flagged instance against its own owner | **b** | `[deploy] protected = true` guards `--stop`/`--clean` (CIU-105, vbpub@98957a129, 7.13.0). v8 has no equivalent (D.5 second half). |
| — `environment_tag = $INSTANCE_ID` scaffold default | **b** | CIU-104, vbpub@9ce4bd32 (7.13.0). It is subsumed by S4.2 (no consumer-chosen tag). |
| — worktree record stores the fork commit | **b** | CIU-106, vbpub@d683f6085 (7.13.0); run-gate R-35a uses it. v8 has no counterpart (S14.7.1's record has no `fork_commit`). |
| S14.4.1 instance lock = `flock` on the checkout root directory | **c** / **d** | 7.15 locks a root-offset file in the shared git-family state dir (`workspace.py:41-62`), and the library's family lock serializes allocation (worktree SPEC "Record and locking rules"). Round-4 T4-01 (BLOCKER) says release-local directory inodes do not serialize two releases of one instance. v8 must choose one lock authority (§3.5). |
| S14.4.7 canonical keys; S14.4.8 `ciu lease`, `CIU_LEASE_FDS` | **a** / **d** | Keys valid. T4-09 (MAJOR): fd inheritance does not survive sudo/ssh/`docker exec`. Its proposed fix is `fstat`-validated inheritance for same-kernel children and a lease protocol otherwise. |
| S14.6 `[ciu.instances] max_concurrent` (count) + `lease_ttl_hours` | **c** | D-647 #4 (RAM budget, not a count). v7 has both a count (`[ciu.worktree] max_concurrent_instances`, `worktree.py:4532`) and a **memory.min floor admission** (CIU-94/95, vbpub@a4f5aa94, 7.12.0) that reads the slice's `MemoryMin` from cgroupfs. That read has the same devcontainer-visibility problem. |
| S14.6.3 `ciu instance exec --env <e>` (gate environments only; drops `[ciu.worktree.exec_targets]`) | **d** | CIU-118 (generic service exec) and RG-70 (exec in a lane environment) are both unmet. One verb should serve both (§3.3). |
| S14.7 registry `ciu.instance.json` in the state root + `ciu-instances.lock` in the git common dir | **c** | 7.15 and cmru share `libraries/worktree` records under `.git/.workspace-instances/` (versioned JSON, atomic, leases). The library SPEC says an adapter "must not implement a second generic Git lifecycle or lease authority". v8 must **adopt the shared library** as its registry, or the operator must rule otherwise. |
| — linked-worktree shared-infra reuse (v7 S16.1, four CLI flags) | **c** / **d** | D-647 #3 + CIU-116: the reuse a worktree may make must be a committed project declaration. v8 S9.5 joins are per instance only. |

### 2.3 Topology, joins, governance, images (S6, S9, S13, S17)

| v8 rule | tag | evidence / note |
|---|---|---|
| S9.5 joined Realizations (per-instance, `ciu instance add --join`) | **a** / **d** | Mechanism valid. A project-level preset is missing (CIU-116). |
| Demo `examples/ciu.instance.joined.toml` joins vault, consul, redis, **main_db**, object_store, app_schema, idp, tracing, otel | **c** | D-647 #3: vault only, never postgres. The demo must show `vault` only. |
| S13.2.1 "`memory_min` is preflight-only … never written to a cgroup" | **c** | CIU-94 (7.12.0) writes `MemoryMin` to the container's transient scope via `systemctl set-property --runtime` and admits against the slice's `MemoryMin`. |
| S13.2 `io_*_iops_max` "`0` = derive from the baseline" | **b** | Matches v7 CIU-110 (vbpub@7f83941de, `[Unreleased]`): omitted = uncapped, explicit `0` = derive. CIU-111 (rework IO governance around io.cost calibration) is open (**d**). |
| S13.2 `cgroup_parent` `""` = `$CGROUP_PARENT_DEV_BACKGROUND` (stacks) | **a** | Stacks stay in the background tier (estate AGENTS "Host cgroup placement"). |
| S6.2 / S17.6.1 image references | **d** | CIU-117: project-built tags are not instance-scoped. A linked worktree's bake overwrites the primary's tag. Also blocks "a worktree may change its own test runtime". |
| S17.x releases/activation, S2.6 state root, S7.2.4 enrollment | **b** / **d** | v7 enrollment backport shipped (CIU-93, vbpub@b8b174b1, 7.12.0) with CIU-99/100 open. Round 4 T4-01..T4-08 and T4-10 are unfolded. Out of gate scope; listed because they block the same rewrite. |
| S18 CLI (closed verb set, exit codes) | **d** | T4-10 (`ciu version --surfaces --json` undeclared). New since: estate CLI version compatibility and universal parser diagnostics (vbpub@05f373a40, vbpub@aa0e69fad, 7.15.0) and `libraries/cli-extended` (CIU-114). v8 S18 does not know them. |
| — tool skills | **d** | D-647 #6. Skills currently live in source trees (`ciu/.claude/skills/ciu-cli/`, `run-gate-project/.claude/skills/run-gate-cli/`, vbpub@33c0b0c2, 7.14.0), not in wheels. No install verb exists, and v8 has no surface for it. |

### 2.4 The gate (S16) against run-gate 23.9 (rev 46)

| v8 rule | tag | evidence / note |
|---|---|---|
| S16.1 model: lane = command \| assay \| sequence in an environment with preconditions, caps, admission, LaneResult | **a** | Still the right shape. |
| S16.2 `cgroup_slice` default = `governance.cgroup_parent` | **c** | run-gate R-10: declared > `cgroup_slice_env` > `$CGROUP_PARENT_DEV_GATES`, never a literal. The dev-gates migration (vbpub@41c1cafb3) reserves the background tier for stacks. D.7 (1) already asks for `$CGROUP_PARENT_DEV_GATES`. The demo's literal `cgroup_slice = "ciu-gate.slice"` breaks §4.2a. |
| S16.2.1 inheritance via `[ciu] inherit` (environments, judge, slice; lanes never inherited) | **a** | It replaces `run-gate.root.toml` strict-ancestor lookup (R-22 "central config"; renamed in vbpub@41c1cafb3). The estate root file holds one environment and no lanes, so dropping inherited lanes costs nothing. |
| S16.2.2 externals | **a** | — |
| S16.3 judge = version floor + mandatory `judge_provenance`; ciu minimum 4.1.0 | **a** / **c** | Floor and provenance valid (assay is at 7.x). Two current judge modes are missing: **explicit command + sha256 pin** (external consumers; dstdns pins `tools/assay/assay-7.2.0.pyz`), and **source mode** (vbpub-internal, run-gate R-08 rev 11 / `[Unreleased]` "Source-backed Assay consumers"). → §6 Q12. |
| S16.4 environments `ephemeral \| exec \| host`; S16.4.1 implicit `host` | **c** | run-gate R-42 (RG-43): the built-in `host` is a **container** with the default image, and `bare-host` is the subprocess. The same name has opposite meanings in the two tools. → §6 Q11. |
| S16.4 `image_from` (LogicalService → variant's image) | **a** / **d** | This is RG-73(a)'s v8 form. It needs CIU-117 so a worktree's `image_from` yields its own tag. |
| S16.4.3 mount proof (exec) + S16.12 dual mount (ephemeral) | **d** | For a **linked worktree**, the gitfile names `<primary>/.git/worktrees/<w>`, and v8 mounts only the checkout. git breaks inside the lane: dstdns measured exit 128 and assay ERROR/GIT_FAILED, false greens included (`tools/test-runner/ciu.compose.yml.j2:84-124`). RG-73(b) asks for the worktree at the image's canonical root. That collides with the common-dir mount when the image root equals the primary's path (§3.4; derived, UNVERIFIED by execution). |
| S16.4.5 available environment; stage-12 subset check | **a** | run-gate R-24/R-24a/RG-23 are the same idea. RG-76(c) (auto-forward `required_env`) is compatible. |
| S16.5 lane keys | **c** / **d** | Missing: `stall_timeout` (R-40c/R-40f, legal on command lanes since RG-41) or D.7's liveness replacement; `profile` (R-43h, D.6 (4)); `services` (RG-75); extra-argv passthrough (RG-71). `budget` is "enforced" in v8 and advisory in run-gate (R-20); D.7's "budgets are ceilings, not detectors" makes v8's enforcement right. RG-63 still needs a rule: the budget clock starts **after** admission and lock wait. |
| S16.5.3 `resources.shared` → stack-directory lock | **a** | It replaces R-29's named shared-infra locks. |
| S16.5.4 sequence lanes | **a** / **d** | They replace dstdns's `bash -c "… && run-gate …"` composite and the four nested-`run-gate` aggregate lanes (`p196-r1`, `p201-controller`, …). Base propagation when `--base` is **omitted** (resolve once, pass to every `base_source = "request"` member) is unstated (RG-74(b)). |
| S16.5.5 `--request-base` only to `base_source = "request"` lanes | **a** | = R-35/RG-26. |
| S16.5.6 provenance | **a** | — |
| S16.5.7 one lane per exec target | **a** (reaffirmed) | §4.10 gap 18's "real consumer" arrived (dstdns D-619/D-636, RG-67). The reconciled answer keeps N=1 and moves hermetic lanes to `ephemeral`. RG-67 exec N>1 is a v7-only bridge. |
| S16.6.1 admission: flock + ledger on the slice cgroup directory, per uid, sum of `memory_max` | **c** | (1) The slice is invisible from the devcontainer (consumer input; RG-67 note). (2) D-647 #4 wants measured footprints, not declared maxima. (3) The ledger is per uid and per host-path. The cgprofile daemon registry already spans projects and worktrees (RG-55; RG-56 OPEN). (4) D.6 (3) and D.7 (2) ask S16.6.1 to converge on that registry. → §3.6, §6 Q4–Q6. |
| S16.6.4 exec lanes: caps "requested only" | **a** / **d** | D.7 (3): daemon placement into a leaf `rg-<token>` would give exec/host lanes real caps. Nesting inside a container scope is impossible, so exec stays requested-only. Unfolded. |
| S16.7.1 `assay lanes --json` is the only interface | **a** / **d** | Still right. Lane **import** (RG-76(b)) is the next step, removing dstdns's 118 restated assay lanes. |
| S16.7.2 `--progress <run_dir>/progress.jsonl` | **d** | Round 4 audit of T3-08: "landed-and-broke", because it contradicts estate AGENTS' mandatory `.assay/progress-<lane>.jsonl` (R-38). Under one implementation ciu owns that rule, so the contradiction is resolved by rewriting AGENTS at cutover, not by v8 yielding (§3.7). |
| S16.7.2 "assay resumes from its own state under the judged tree's `.assay/mutation-state/`, which CIU neither reads nor manages"; proposal §4.10 gap 23 | **c** / **b** | run-gate passes `--state-dir <repo>/.run-gate/assay-state/<project-relative-path>/` on every assay lane (RG-38, rev 37, using assay B066; RG-49 for Mode-B ownership). The state survives a thrown-away worktree, which v8's text would lose. Gap 23's "it is an assay change" has shipped. |
| S16.7.2 default base = merge-base of `HEAD` and `@{upstream}` | **c** | RG-51 / CIU-106: that is the stale-origin hazard. Current order: `--base` > the worktree record's fork commit (proved still an ancestor) > … RG-74(a) adds the trunk-merge first parent. |
| S16.8 outcome vocabulary; exit codes PASS 0 / FAIL 1 / ERROR 2 / NOT_RUN 3 / BUDGET 4 | **c** | run-gate R-04/RG-11: a lane's own status passes through, 2 = config/refusal, 3 = infrastructure failure. Consumers depend on it (nyxloom pointers, `gate-slot.sh`'s exit-75 hazard). → §6 Q11. |
| S16.9 LaneResult; S16.9.4 run dirs, `run.json`, prune "owning pid alive" | **a** / **d** | T4-09: `run.json` has no pid/host/boot id/start ticks and no completion marker. `resources_measured` (D.6 (2)) and `liveness` (D.7 (4)) are unfolded. |
| S16.9.2 `--list` shows last outcome, duration and median | **c** | run-gate R-01: `--list` is three tab-separated columns, "never extended (consumers parse it)". Split into `--list` (machine) and `--list --json` / `history` (§3.3). |
| S16.10 CLI | **d** | Missing: `history` (R-36), `footprint` (R-44), `exec` (RG-70), `--fresh` (re-attach override, R-39), and lane extra-args (RG-71). `validate-pointers` → `ciu check --gates` is in the proposal (§4.5-H), not the spec. |
| S16.11 zero-instance projects | **a** | Load-bearing. 18 of the 19 estate run-gate consumers use a symlinked `run-gate.py` against the inherited `tester-unified` ephemeral environment, and only dstdns uses exec mode (survey 2026-10-03). 14 of those 18 have no ciu stack at all, and the other 4 (topos, nyxloom, pwmcp, cgroup-profiler) have one but gate without it. This is the honest form of "standalone" (§6 Q1). |
| S16.12 "rules lifted by reference" (R-04, R-19a, R-23, R-15/17, R-26, R-36, R-25 moot, R-14a retired) | **d** | Not lifted, and with no v8 home: R-39 re-attach, R-40 liveness, R-41 (→ S16.5.7, fine), R-43/R-44 profiling and footprint, R-30a/b/c doctor checks, R-34 toolchain fitness, R-35a/b base rules, R-38 resume and progress, R-08a pin-table strictness, RG-44 gone-signal case-folding, RG-47/RG-65 config resolution from `--worktree`, not the CWD (v8 S1.5 root resolution must carry that oracle). |
| — client death mid-lane | **d** | v8: "removal in a `finally`". run-gate R-39 found the `finally` never runs on SIGKILL or a devcontainer restart. The container keeps going and the next run duplicates it. D.7 (5) makes the daemon the watcher that kills. Neither v8 text covers collection or re-attach. |
| — throwaway services per lane | **d** | RG-75. v8 S16.4 has `binds` to deployed services and externals only. |
| — exec a command in a lane environment | **d** | RG-70; CIU-118. |
| — `--reuse-from` / `--rejudge` passthrough | **d** | RG-66. |
| — footprint of FAIL runs; per-lane footprint write | **d** | RG-68, RG-69. |
| — failure digest of a failed assay lane | **d** | RG-72. |

### 2.5 Proposal §4.10 known gaps

| gap | tag | note |
|---|---|---|
| 5 — cgroup slices inside the devcontainer, live probe owed | **c** (answered) | The probe was taken: the slice is invisible. Admission must go through the daemon. |
| 8 — run-gate alignment | **c** (moot) | One implementation. |
| 14 — state posture costs (`git clean -x`, `--move`, collision refused) | **a** | Reopen only if the shared-library registry (§2.2 S14.7) changes the posture. |
| 15 — judge capability record | **d** | Still open (response §5 item 1). |
| 18 — exec parallelism, revisit with a real consumer | **a** (consumer arrived, answer unchanged) | §2.4 S16.5.7. |
| 19 — admission across users | **c** | The daemon registry is host-wide, so admission across users comes for free (§3.6). |
| 22 — monorepo consumer work | **d** | Unchanged. Additionally, the vbpub root now has `run-gate.root.toml`, which becomes the zero-instance root `ciu.toml` `[testing.environments.tester-unified]`. |
| 23 — hidden `.assay/` state | **b** | `--state-dir` shipped (RG-38). |
| everything else | **a** / **d** | Not gate-relevant; carried as is. |

---

## 3. Merge architecture

### 3.1 What "run-gate merged into ciu" means concretely

The gate is a subsystem of the ciu v8 implementation (`ciu8/src/ciu8/gate/`, renamed with the package at the 8.0.0 cutover). Its behaviour is **today's run-gate behaviour, re-expressed in the v8 entity model**: environments name LogicalServices, identity is data, locks are the canonical keys, config is `[testing.*]` in `ciu.toml`. After cutover there is one implementation:
- `run-gate-project/` is archived;
- the `run-gate` console script and the copied or symlinked `run-gate.py` disappear;
- the estate's `run-gate.root.toml` becomes a zero-instance root `ciu.toml`.

run-gate is a single 8841-line file (`run_gate.py`). The port is a **copy-and-adapt into modules**, consistent with the ciu8 rule "reusable v7 code copied and adapted, never imported". It is not a rewrite from S16's text, because S16 is the older and smaller contract.

### 3.2 Module shape

| module (`ciu8/gate/`) | carries (run-gate rule → v8 rule) |
|---|---|
| `model.py` | `[testing.*]` table-spec rows feeding V8-1's single declarative table-spec (closed keys, R-04 one-line errors naming key and file, R-08a pin strictness) |
| `resolve.py` | environment resolution. `exec_in` → derived identity → mount proof (R-14a retired, R-24 worktree-awareness kept by construction). `image_from` → resolved image reference, instance-scoped (CIU-117). Slice resolution R-10 (declared > `cgroup_slice_env` > `$CGROUP_PARENT_DEV_GATES`, LoadState check R-11). Physical root via mountinfo (R-12, shared with S4.1.4). |
| `mounts.py` | checkout dual mount (R-23), the linked-worktree git common-dir mount derived from the gitfile (§3.4), `extra_mounts` guard, git isolation (R-19a) |
| `run/detached.py` | create → start → logs → `docker wait` → collect → rm (R-15/R-17/R-26), with the **inflight record folded into `run.json`** and re-attach (R-39), including the T4-09 owner tuple and completion marker |
| `run/exec.py`, `run/host.py` | exec lanes under the S16.5.7 stack-directory lock (R-41 → S14.4.7 key); subprocess lanes (R-19) |
| `services.py` | lane-scoped throwaway services (RG-75): run-scoped names and labels, ready/init, env delivery, removed in the same `finally`, reaped on re-attach |
| `judge.py` | assay adapter. `assay lanes --json` (S16.7.1, R-25/26/34), lane import (RG-76), judge acquisition (pin / source / floor, §6 Q12), argv with `--require-judge-provenance --resume --state-dir --progress` (R-38, RG-38), `--request-base` (R-35), `--reuse-from`/`--rejudge` (RG-66), failure digest (RG-72) |
| `base.py` | comparison base: `--base` > worktree fork commit (CIU-106/R-35a) > trunk-merge first parent (RG-74(a)) > refuse. No `@{upstream}` fallback. Gate-safe charset (R-35b). Resolved once per sequence (RG-74(b)). |
| `admission.py` | the RAM ledger client (§3.6), count fallback (RG-67), `--admission-wait`, budget clock after admission (RG-63) |
| `profile.py` | cgprofile daemon client per the frozen RG-55 contract (`ctl start/stop/status`), session token export, `resources_measured`, liveness verdicts (D.6, D.7), rusage fallback (R-43i) |
| `evidence.py` | run directories, LaneResult, `last`, history (R-36, S16.9), footprint manifest (R-44, RG-68/69), 0600 on failure |
| `cli.py` | the verbs below |

### 3.3 CLI (kept / renamed / dropped)

| v8 | from run-gate | note |
|---|---|---|
| `ciu gate <lane>… [--worktree P] [--base REF] [--allow-dirty] [--dry-run] [--fresh] [--admission-wait D] [--json] [-- extra-args]` | `run-gate <lane> …` | `--fresh` overrides re-attach (R-39). `-- extra-args` is appended to a command lane's argv when the lane declares `extra_args = true` (RG-71). |
| `ciu gate --list` (3-column TSV, stable) / `--list --json` (with last outcome, median duration, median peak) | `--list` | Splits S16.9.2 from R-01's frozen machine format. |
| `ciu gate history [lane] [--json]` | `history` | — |
| `ciu gate footprint [lane] [--write] [--json]` | `footprint` | Also writes FAIL-run measurements (RG-68) and per-lane writes (RG-69). |
| `ciu gate doctor [--worktree]` | `doctor` | Adds daemon reachability, slice visibility, and the ledger view. |
| `ciu gate exec [--env E] [--worktree P] -- cmd` | — (RG-70) | **Replaces** S14.6.3 `ciu instance exec --env`, so there is one verb. |
| `ciu exec <realization>[:<svc>] -- cmd`, `ciu resolve … --json` | — (CIU-118) | Generic service exec and query, any instance. Not part of the gate, listed because it shares the mount-proof code. |
| `ciu check --gates [POINTER_FILE]` | `validate-pointers` | Proposal §4.5-H. |
| `--check-env` | `--check-env` | kept |
| — | `RUN_GATE_EXTRA_MOUNTS`, `RUN_GATE_MOUNT_ALIAS`, `RUN_GATE_LOCK_DIR`, `RUN_GATE_PROFILE`, `RUN_GATE_HOST_IMAGE` | Dropped. Declared keys replace them (`extra_mounts`, `[testing.profile]`, environment `image`). Test-only roots become `CIU_GATE_*_ROOT`. |
| — | `--version` copied-script revision (R-00, R-31 two-tier identity) | Dropped. One wheel version. |

Exit codes are §6 Q11.

### 3.4 Worktree-owned hermetic environments: the mount rule

A linked worktree's `.git` is a file naming `<common-dir>/worktrees/<w>`, an absolute path inside the primary checkout. So:

- **v8 as written** mounts the checkout only. git fails inside the lane. dstdns measured exit 128 and GIT_FAILED, with git-backed tests SKIPping under exit 0 (`tools/test-runner/ciu.compose.yml.j2:84-124` comment).
- **RG-73(b) as filed** mounts the worktree at the image's canonical root (`/workspaces/dstdns`). The worktree's own `.git` file then sits at `/workspaces/dstdns/.git`, exactly where the common-dir mount must go. Both cannot be mounted at one path (derived from git's gitfile semantics; UNVERIFIED by execution). It works only with `GIT_DIR`/`GIT_COMMON_DIR` overrides, which every tool inside the lane must honour.
- **Recommended:** mount the worktree at its own path, both namespaces if they differ (R-23), and mount the git common dir read-write at the path the gitfile names. That is derived from the gitfile, never configured, and is exactly dstdns's working Mode-B compose block, generalized. Read-write is needed because assay's repository snapshot runs `git worktree add`. The image must not bake a checkout path. Environments get an `env` table with `{worktree}` substitution (`env = { PYTHONPATH = "{worktree}:{worktree}/scripts" }`), and `workdir` defaults to the checkout path. ciu injects the same mounts into a persistent `exec_in` tester of a linked-worktree instance (`[ciu_stack.<svc>] mount_checkout = true`), so dstdns deletes its compose block too. → §6 Q7.

### 3.5 One lock and registry authority

v8 adopts `libraries/worktree` as its registry and family lock:
- records in `.git/.workspace-instances/`, shared with cmru;
- allocation collision refused with both paths named.

It keeps the canonical keys of S14.4.7 for runtime exclusion:
- the instance key is the 7.15 root lock in the git family state dir (`workspace.py:41-62`), or the checkout directory where no git family exists;
- the Realization key is the stack-directory flock.

This removes `ciu.instance.json` and `ciu-instances.lock` as second authorities. T4-01 (release scoping) is then answered by keying a release's instance lock on the release's stable state root, not on a release directory inode. A carve can take that without an interview, because it follows from the library SPEC's "must not implement a second … lease authority".

### 3.6 Admission and caps: one RAM ledger

D-647 #4 and the observed slice invisibility point to the same component.

- **Ledger.** The cgprofile host daemon is root, `cgroupns=host`, and a host singleton in `cgprofile.slice` (D.7 (5)). It already registers every profiled lane across projects and worktrees (RG-55). It becomes the admission ledger for **both** `ciu gate` lanes and `ciu up` containers. RG-56's go/wait/refuse contract is the prototype, so build it once, in the merged gate.
- **Capacity (READ, never invented).** The capacity is each slice's `memory.max` (`dev-gates.slice` for lanes, `dev-background.slice` for stacks), provisioned by mdt host-setup and read by the daemon, which can see it. A slice whose `memory.max` is `max` has no budget, and admission then needs the declared host reserve (§6 Q6). Memory PSI (`full avg10`) is the pressure signal. Swap usage is never one (operator ruling, D.6).
- **Demand (measured first).**
  - A lane's expected peak is its committed footprint (`ciu-gate.footprint.json`, R-44 shape, p90 of `memory_peak` over the history window).
  - A stack's expected peak is the sum of its containers' measured footprints. These are recorded the same way by `ciu up` sessions that the daemon profiles: `ciu.footprint.json` per instance kind, committed.
  - Unmeasured demand is §6 Q5.
  - `memory_max` stays the **ceiling** (S16.6.1's "no reservation without a declared size" holds) and is applied to the cgroup.
- **Fallback.** When the daemon is absent, admission degrades to a **declared count per environment** (RG-67's `max_concurrent`, default 1 for exec and unset for ephemeral) and is disclosed. The count is a fallback, never the policy.
- **Where caps live.**
  - The host budget and reserve are host facts: ciu's host-scoped config, `~/.config/ciu/host.toml` (UNVERIFIED that this file exists in v7; v8 S2.6 has `ciu.host.toml` per instance).
  - Footprints are project facts, committed.
  - Count fallbacks sit next to the environment they bound.
  - Agent caps stay in nyxloom `[policy]` (NL-29). ciu does not dispatch agents.
  - → §6 Q6.

This deletes S14.6 `max_concurrent`, `[ciu.worktree] max_concurrent_instances`, S16.6.1's per-uid file ledger, and dstdns's `gate-slot.sh`. CIU-94's `memory.min` floor stays as a *protection* write (it reserves memory and admits nothing else).

### 3.7 Assay integration (decided without an interview, reversible)

- **Progress:** `<run_dir>/progress.jsonl`. No two invocations share a file, and it is gitignored. Estate AGENTS' `.assay/progress-<lane>.jsonl` rule is rewritten at cutover; it was run-gate's rule, and ciu now owns the gate. This answers T3-08's round-4 objection.
- **Resume state:** `--state-dir <state root of the git family>/ciu-gate-state/<project-relative-path>/`, durable across thrown-away worktrees (RG-38 semantics kept, path renamed).
- **Judge provenance:** always required (S16.3.2, unchanged).
- **Lanes:** imported from `assay lanes --json`. An explicit `[testing.lanes.<n>]` overrides by name, and `--list` marks imported lanes.

### 3.8 Migration path

**Phase A: dstdns relief, now.** v7 run-gate gets RG-67 and RG-73(a) + (b-as-§3.4). The decision is whether RG-74/75/76 are built in run-gate first (§6 Q2). Under "B" they are built in run-gate with the oracles already filed, so the port has executable parity tests.

**Phase B: the ciu8 gate port.**
- Port module by module (§3.2).
- V8-24's conformance fixtures become the **port oracle**: the black-box scenarios run against both until the port is accepted, then against ciu8 only.
- run-gate goes bug-fix-only from the moment the ciu8 gate passes them.
- That window is the only time two implementations exist. It is a build window, not a supported dual path.

**Phase C: cutover at 8.0.0, per consumer, atomic.**
- `ciu migrate --gate` converts:
  - `run-gate.toml` → `[testing.*]`, collapsing imported assay lanes and pins into `[testing.judge]`;
  - `host` → a declared ephemeral environment;
  - `bare-host` → `mode = "host"`;
  - `bash -c "run-gate a && run-gate b"` conjunctions → `kind = "sequence"`;
  - nyxloom `[gates.*]` pointers → `ciu gate <lane> --worktree {worktree}`.
- It prints a residue report for anything it cannot convert: argv that is shell text, scripts that start containers.
- The consumer commits the converted config, deletes `run-gate.toml`, the `run-gate.py` symlink and `.run-gate/` (history restarts; it is evidence, not state), and re-measures footprints.
- AGENTS §4.1 holds: each repo has one gate path at every commit. The estate is converted repo by repo, so cross-repo skew is bounded by "ciu8 installed alongside ciu7". That works because ciu8 is a different console script until the rename.

---

## 4. Config surface after the merge

| concern | home | written by |
|---|---|---|
| lanes, environments, services, judge, sequences | `ciu.toml [testing.*]` (project) | consumer |
| estate-shared environments, judge, slice env | zero-instance root `ciu.toml` + `[ciu] inherit` | estate |
| footprints (lanes, stacks) | `ciu-gate.footprint.json`, `ciu.footprint.json` (committed) | `ciu gate footprint --write` / `ciu footprint --write` |
| RAM budget, reserve, PSI threshold | read from slices by the daemon; reserve/threshold in host-scoped ciu config | host-setup (mdt) / operator |
| count fallbacks | `[testing.environments.<e>] max_concurrent` | consumer |
| join presets (what a worktree may borrow) | `ciu.toml [instances.join_presets.<p>]` + `default_join_preset` | consumer |
| trunk name | `ciu.toml [project] trunk` | consumer |
| profiling knobs | `[testing.profile]` (mirrors run-gate `[profile]`), lane `profile = false` | consumer |
| agent concurrency | nyxloom `[policy]` | consumer (nyxloom) |

---

## 5. dstdns use-case walk

The sketch shows the target `[testing]`. Names are illustrative, and images and keys follow §3.

```toml
[project]
trunk = "main"

[testing]
cgroup_slice_env = "CGROUP_PARENT_DEV_GATES"

[testing.judge]                       # §6 Q12 option A
version = ">=7.2"
command = ["python3", "tools/assay/assay-7.2.0.pyz"]
sha256  = "tools/assay/assay-7.2.0.pyz.sha256"
import  = { environment = "hermetic", lanes = "all" }   # 118 lanes, 1 block

[testing.environments.hermetic]
mode = "ephemeral"
image_from = "tester"                 # instance-scoped tag in a worktree (CIU-117)
env = { PYTHONPATH = "{worktree}:{worktree}/scripts" }
max_concurrent = 2                    # fallback only; the daemon ledger normally decides

[testing.environments.hermetic.services.pg]       # RG-75
image_from = "main_db"                # the declared engine: timescale/timescaledb-ha:pg18.4-ts2.27.1
tmpfs = ["/home/postgres/pgdata"]
ready = ["pg_isready", "-U", "postgres"]
expose_env = { SCHEMA_GATE_DSN = "postgresql://postgres:{secret}@{host}:{port}/schemagate" }

[testing.environments.live]
mode = "exec"
exec_in = "tester"
forward_env = ["RUN_LIVE_TESTS"]

[testing.lanes.unit]
kind = "command"; environment = "hermetic"
argv = ["pytest", "tests/unit", "…", "-q"]; resources = { memory_max = "2G" }

[testing.lanes.schema]
kind = "command"; environment = "hermetic"; services = ["pg"]
argv = ["{worktree}/scripts/schema-apply.sh", "{worktree}"]   # DDL apply stays dstdns

[testing.lanes.release]
kind = "command"; environment = "live"; required_env = ["RUN_LIVE_TESTS"]
requires = { healthy = ["controller", "webapp_server", "worker_io"] }; require_provenance = true

[testing.lanes.gate]
kind = "sequence"
lanes = ["schema", "unit", "assay", "assay-dlq", "frontend-unit", "ui_unit"]

[instances.join_presets.live]
reference = "primary"
services = ["vault"]                  # D-647 #3 — never main_db
```

| use case | works? | how / dstdns writes | dstdns deletes |
|---|---|---|---|
| **1. Worktree-owned hermetic lanes in ephemeral runners** | yes, with §3.4 + CIU-117 + RG-76 | Each lane gets its own container and cgroup on the worktree's own image tag. No stack and no Mode A. Whether the worktree needs `instance init` first is §6 Q8. | `scripts/gate-slot.sh`; GUIDE §1 flock ritual; the 11 `cd {worktree}` prefixes; Mode-A text in GUIDE §3.1; `tests/config/test_assay_pin_integrity.py`'s exact-set duty; ~70% of `run-gate.toml` |
| **2. Live lanes in a Mode-B stack borrowing vault** | yes, with CIU-116 presets | `ciu instance init --join-preset live && ciu up` (the bundle set the package develops). The tester is an `exec_in` target with injected checkout and common-dir mounts (§3.4). S16.5.7 serializes live lanes per tester. | the `.git` block in `tools/test-runner/ciu.compose.yml.j2:84-124`; GUIDE §3.4b `<TBD>` flags |
| **3. Post-merge composite on main** | yes, with RG-74 | `ciu gate gate` on main. The base is the trunk-merge first parent (`trunk = "main"`), resolved once for the sequence. Hermetic members run in ephemeral containers on main's tag; live members run in main's tester. | `scripts/gate-base.sh` (79 lines, 93 references); the `GATE_BASE` plumbing at `run-gate.toml:511`; the "no `--base` on the composite" rule in AGENTS §6.1 |
| **4. Schema lane on the app's own postgres engine** | yes, with RG-75 | `image_from = "main_db"` reads the **declared** engine image from the resolved config, so no running stack is needed. That is stronger than `schema-gate.sh:94`'s read of a running container, and it satisfies §4.2a (derive). No docker socket is mounted into the lane. | provisioning halves of `schema-gate.sh` and `sql-mutation-gate.sh`; `p128/p129/p165/p167/p201-assay-schema.sh`; the `P1xx_*` `forward_env`; `RUN_GATE_EXTRA_MOUNTS=/var/run/docker.sock…` |
| **5. Concurrent gates under a RAM budget** | yes, with §3.6 (daemon) | Nothing in normal operation. Footprints are committed after `ciu gate footprint --write`. `max_concurrent = 2` stays as the disclosed fallback. | the D-570/D-636 "≤2 gates" prose as SSOT; `max_concurrent_instances` in `ciu.global.defaults.toml.j2` |
| **6. Footprint measurement** | yes | Ephemeral lanes are measured exactly (own cgroup). Exec live lanes are measured container-wide with baseline subtraction, attributable because S16.5.7 runs one lane at a time (D.6 (1)). Stacks are measured per container by the daemon during `ciu up` sessions. | RG-67's "refuse `footprint --write` from overlapped runs" concern disappears for ephemeral lanes |
| **7. Coverage and mutation judging via assay** | yes | Imported lanes. Durable `--state-dir` across worktrees. Progress in the run dir. Liveness from the progress stream; the daemon kills a stalled lane (D.7). `--reuse-from`/`--rejudge` passthrough (RG-66). | per-lane pins; the `assay_command` restatement |
| **8. Release via cmru** | yes, if Q3 = A | The cmru release transaction runs `ciu gate <release-lane>` in its isolated release worktree. That worktree is a linked worktree, so it gets its own base36 id and tag. Hermetic lanes need no stack; mutation resume survives via `--state-dir`. vbpub projects replace `cmru tester-gate …` steps with `ciu gate <lane>` on the inherited `tester-unified` environment. dstdns has no `[project.dstdns.gate]` today; it adds one. | — (dstdns); `cmru tester-gate` steps estate-wide |

The remaining dstdns-only phase-0 hygiene (from TOOLING-BOUNDARY §6) is independent of v8 and not repeated here.

---

## 6. Interview questions

Each question lists the options with the recommendation first, then the trade-off.

**Q1 — What does "standalone" mean once the gate is in ciu?** *(blocks S16.11, proposal §4.1.10)*
- **A (rec):** The gate ships only in the ciu wheel. "Standalone" means zero-instance mode: a project with only `[project]` + `[testing]`, no stack, docker only for container lanes. The copied-script distribution ends.
- B: The gate is a separate stdlib-only distribution `ciu-gate` built from the ciu repo, which ciu depends on.
- C: Keep `run-gate` as a thin front-end that execs `ciu gate`.
- *Trade-off:* A costs external adopters a pip install of ciu (three runtime deps) instead of a copied file. Every estate consumer already uses a symlink plus an installed toolchain. B keeps the zero-dependency property at the cost of a second release artifact and a wheel boundary to police. C is the dual path AGENTS §4.1 forbids.

**Q2 — Where are RG-67/73/74/75/76 built?** *(blocks the build order)*
- **A (rec):** In run-gate v7 now, with the filed oracles, then ported once into the ciu8 gate. The oracles become the port's parity fixtures, and run-gate goes bug-fix-only after the port passes them.
- B: Freeze run-gate now and build only in ciu8. dstdns waits for ciu8 (P001 not yet accepted).
- C: Build an interim `ciu gate` in ciu v7.
- *Trade-off:* A pays for building twice but relieves dstdns immediately and gives the port executable parity. B is the cleanest code history and leaves dstdns on Mode A and `gate-slot.sh` for months. C creates a third implementation.

**Q3 — Does `cmru tester-gate` fold into `ciu gate`?**
- **A (rec):** Yes. cmru keeps the release transaction and calls `ciu gate <lane>`. tester-unified becomes an inherited ephemeral environment.
- B: Keep it as is.
- C: cmru calls ciu's gate library in-process.
- *Trade-off:* A removes one of the six gate spawners vbpub AGENTS lists, and cmru then depends on ciu at release time. B keeps two places that place and cap a test container. C couples cmru to ciu internals.

**Q4 — What is the admission authority?** *(blocks S16.6, S14.6)*
- **A (rec):** The cgprofile host daemon's registry is the single RAM ledger for gate lanes and stack containers. When the daemon is absent, admission falls back to a declared count, disclosed.
- B: S16.6.1 as written (slice-directory flock + per-uid file ledger), plus a count fallback.
- C: Counts only.
- *Trade-off:* A is the only option that sees the slice from the devcontainer and across projects and users. It makes a host daemon load-bearing for admission, though not for running. B admits nothing on this host. C contradicts D-647 #4.

**Q5 — What does admission charge for a lane or stack with no measured footprint?**
- **A (rec):** Its declared `memory_max`, the ceiling. A first run is admitted against the ceiling, and its measurement replaces the ceiling from the next run.
- B: Unmeasured work runs only when nothing else is admitted (solo).
- C: Declared values only; footprints are advisory.
- *Trade-off:* A is conservative and self-correcting. B throttles every new lane and stack to serial until measured. C ignores the measurement D-647 #4 asks for.

**Q6 — Where do the budget and the caps live?**
- **A (rec):** The budget is READ from each slice's `memory.max` by the daemon. The host reserve and PSI threshold are in host-scoped ciu config. Footprints are committed per project. Count fallbacks sit on the environment. Agent caps stay in nyxloom `[policy]`. No count survives for stacks.
- B: Everything in the project `ciu.toml`.
- C: One estate `capacity.toml` read by ciu and nyxloom.
- *Trade-off:* A puts each fact where its authority is: host facts with the host, measured facts with the code. B makes every project restate host facts that drift (today's 5 versus "2-3"). C creates a cross-tool file with no single owner.

**Q7 — How is a linked worktree mounted in a hermetic container?**
- **A (rec):** The worktree at its own path, plus the git common dir at the gitfile's path, derived. Images must not bake a checkout path, and `[testing.environments.<e>] env` with `{worktree}` supplies `PYTHONPATH` and the like.
- B: RG-73(b) `mount_worktree_at = <image root>`, with `GIT_DIR`/`GIT_COMMON_DIR` overrides.
- C: Support both.
- *Trade-off:* A requires a dstdns Dockerfile edit (drop the baked `PYTHONPATH`/`WORKDIR`) and is git-native. B keeps images unchanged but collides with the common-dir mount when the image root is the primary's path, and depends on every in-lane tool honouring the overrides. C is two code paths.

**Q8 — Must a worktree be an initialized instance to run hermetic lanes?** *(blocks S16.11/S14.4.2's "gate refuses when not rendered")*
- **A (rec):** No. For ephemeral and host lanes the gate derives what it needs read-only: the identity from the path, the resolved image references. It writes no instance files and starts no containers. Exec lanes and `requires` still need a rendered, up instance.
- B: Require an explicit `ciu instance init && ciu render`, fail fast.
- C: The gate runs init and render itself.
- *Trade-off:* A makes "a worktree owns its test environment" free, with no stack and no state. It needs a pure, side-effect-free resolve path. B adds a ceremony every dispatch must remember. C writes state implicitly, and S4.1.4's sentinel bind starts a container.

**Q9 — How does a worktree borrow shared infra?**
- **A (rec):** Project-level join presets in `ciu.toml` are the only join mechanism for linked worktrees, chosen by `--join-preset` or `default_join_preset`. Per-instance ad-hoc `--join` is removed. dstdns declares `live = ["vault"]`.
- B: Presets plus ad-hoc per-instance joins.
- C: Presets plus a project denylist (`never_join = ["main_db"]`).
- *Trade-off:* A makes D-647 #3 enforceable by `ciu check` and leaves no per-invocation choice. It is less flexible for an experiment, which then edits the committed preset. B reintroduces the unvalidated hand-typed path CIU-116 is about. C is weaker than an allowlist.

**Q10 — Identity and ownership in v8.** *(blocks S4.1, S4.5, S14.7)*
- **A (rec):** Adopt 7.15 verbatim: the shared-library derivation, the nested-root composition, and the `.workspace-instances/` records. Drop `owner_id` and `ciu.owner`. Keep the `ciu.checkout` label check before deletion and `[deploy] protected` (CIU-105). Add `ciu clean --identity <old>` and repair-in-place of outdated records (CIU-115).
- B: Keep draft.7's owner token on top of the 7.15 derivation.
- C: Adopt the derivation but keep v8's own registry.
- *Trade-off:* A is identity-neutral at cutover and has one registry with cmru; it gives up the token's defence against a stale clone at the same path (narrow, D.5). B keeps machinery the operator called convoluted. C is two registries, against the library's own rule.

**Q11 — Whose CLI surface wins at cutover?**
- **A (rec):** v8's. Explicit `mode = ephemeral|exec|host` with no implicit environment names. Exit codes PASS 0 / FAIL 1 / ERROR 2 / NOT_RUN 3 / BUDGET 4, with the lane's own exit code in the LaneResult. A stable 3-column `--list`. `ciu migrate --gate` rewrites run-gate's `host`/`bare-host` and the nyxloom pointers.
- B: run-gate's: lane-exit passthrough, 2/3 refusal codes, built-in `host` = container.
- C: v8 names with run-gate exit codes.
- *Trade-off:* A gives one closed vocabulary that scripts can branch on (NOT_RUN versus FAIL). Every pointer and wrapper is rewritten once, mechanically. B preserves scripts but keeps `host` meaning a container, which v8's own text contradicts. C is a mixed contract.

**Q12 — How is the judge acquired?** *(blocks S16.3)*
- **A (rec):** `[testing.judge]` holds exactly one of `command` + `sha256` (pinned artifact, external consumers) or `source = "<path>"` (estate-internal, installed from the judged worktree), plus a `version` floor. Lanes are imported from `assay lanes --json`, with per-lane override.
- B: Floor only; the environment image must provide the judge.
- C: Per-lane, as today.
- *Trade-off:* A preserves both current modes with one declaration (dstdns: 236 version sites → 1). B makes the image the pin, which is weaker provenance than a sha256, and breaks dstdns's zipapp. C is the boilerplate RG-76 is about.

**Q13 — How do consumers cut over?**
- **A (rec):** Per repo, atomically: one commit that runs `ciu migrate --gate`, deletes `run-gate.toml`, `run-gate.py` and `.run-gate/`, rewrites the pointers, and re-measures footprints. History restarts.
- B: Staged lane-by-lane with both tools live in one repo.
- C: Per worktree opt-in.
- *Trade-off:* A obeys §4.1 and loses lane history (evidence, regenerable). B is the dual path §4.1 forbids. C makes the gate's behaviour depend on which checkout you are in.

**Q14 — How do tool skills install from the wheel?**
- **A (rec):** `ciu skills install [--harness claude|agents|all]` copies the wheel's package-data skills into `~/.claude/skills` and `~/.agents/skills`, idempotently. mdt's devcontainer finalize runs it, and `ciu doctor` reports a version drift. The same verb shape goes to each tool.
- B: mdt copies from site-packages by convention.
- C: Symlinks into site-packages.
- *Trade-off:* A gives one owner per tool and works in a dstdns-only checkout. B puts tool knowledge in mdt. C breaks on venv recreation and is the symlink dependency D-647 #6 rules out. Wheels cannot run post-install hooks, so some explicit step is unavoidable.

---

## 7. What changes in SPEC-V8.md (and the proposal) once §6 is answered

Each item lists the rule and the change under the recommended answers. A non-recommended answer changes the same rules differently.

- **Header / posture.** Proposal §4.1.10, §4.3.2, V8-19, V8-21, V8-24: "one gate implementation; run-gate archived at 8.0.0" (Q1, Q2). §4.9 lists the decisions taken in this interview.
- **S1.5.** Config is resolved from the selected checkout (`--worktree`), never the CWD; the RG-47/RG-65 oracle.
- **S4.1.1–S4.1.2.** 7.15 derivation verbatim, including the nested-root composition. Repair-in-place of outdated records. `ciu clean --identity` (Q10).
- **S4.5.** Delete `ciu.owner` and the token adoption rules. Add the protection flag (CIU-105 shape) (Q10).
- **S6.2 / S17.6.1.** Instance-scoped project-built tags (CIU-117). `mount_checkout` for exec-target services (Q7).
- **S9.5.** Project join presets as the only join path for linked worktrees. The demo joins vault only (Q9).
- **S13.2.1.** `memory_min` is written to the container scope and admitted (CIU-94 parity).
- **S14.4 / S14.7.** The registry and family lock are `libraries/worktree`. Instance key = 7.15 root lock; stack-directory key unchanged. Fold T4-01 and T4-09.
- **S14.6.** Delete `max_concurrent`. Stack admission moves to S16.6's ledger (Q4–Q6). `lease_ttl_hours` stays.
- **S14.6.3.** Replaced by `ciu gate exec`. Add `ciu exec` / `ciu resolve` (CIU-118).
- **S16.2.** `cgroup_slice` / `cgroup_slice_env` per R-10, defaulting to `$CGROUP_PARENT_DEV_GATES`, never a literal. Add `[testing.profile]` and `[project] trunk`.
- **S16.3.** Judge modes and lane import (Q12).
- **S16.4.** No implicit `host`. Add environment `env` with `{worktree}`, `services` (RG-75), and `max_concurrent` (count fallback). Add the linked-worktree mount rule (Q7).
- **S16.5.** Add `stall_timeout` or D.7 liveness, `profile`, `services`, `extra_args`. The budget starts after admission (RG-63). The sequence resolves the base once (RG-74(b)).
- **S16.6.** Rewrite admission on the daemon ledger, with measured demand, ceilings and the count fallback (Q4–Q6). Fold D.6 (3) and D.7 (2)–(3).
- **S16.7.2.** `--state-dir` durable path; progress in the run dir. The base order is `--base` > fork commit > trunk-merge first parent > refuse. Passthrough for `--reuse-from`/`--rejudge`.
- **S16.8.** Exit table per Q11.
- **S16.9.** `run.json` gains the owner tuple and completion marker (T4-09), re-attach (R-39), `resources_measured`, `liveness`, and the failure digest (RG-72).
- **S16.10.** Verbs per §3.3.
- **S16.11.** Hermetic lanes in instance-bearing projects without init (Q8).
- **S16.12.** Replace "rules lifted by reference" with the full port map (§2.4 last rows).
- **S18.** Add `ciu skills install` (Q14), `gate history|footprint|exec`, `exec`, `resolve`, and the estate CLI-compat surfaces. Fold T4-10.
- **Appendix A (`ciu migrate`).** Add the `--gate` conversion of §3.8 Phase C, identity-neutral.
- **Appendix D.** Close D.5, D.6 and D.7. Add the round-4 rule map (D.8) and this reconciliation (D.9).
- **Demo.** Move `unit`, the assay R0–R2 lanes and `ui-unit` to the ephemeral environment. Make `tester` exec live lanes only. Replace `98535c` (consumer input item 4; CIU-119 (4)). Change `cgroup_slice` to `cgroup_slice_env`. Make `schema` use a lane service, not `schema-gate.sh` in exec.

Round-4 blockers outside the gate (T4-01..T4-08, T4-10) still need their own fold. This memo does not resolve them.
