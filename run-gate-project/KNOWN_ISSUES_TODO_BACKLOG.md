# run-gate — known issues, TODO, and backlog

Created 2026-08-22 from the adversarial review of the estate-wide adoption
wave (`vbpub@4c6eb2b6..91959b3a`: eight projects adopted run-gate as SSOT
test definition, consumers shrunk to thin pointers, full gate sweep green).
Two independent fresh-eyes reviewers (correctness + consumer UX) plus a CLI
audit; every entry below was confirmed in source before filing. IDs are
`RG-N`, allocated sequentially in this file (no other allocator exists).

Normative behavior belongs in [`SPEC.md`](SPEC.md) (§9 already tracks some
deferred items — cross-references noted per entry). A FIXED entry means code,
tests, and docs landed together. Relationship to SPEC §9's own open items:
budget enforcement (RG-7 context), async long lanes, dstdns adoption, and
Docker-probe slice verification are NOT re-filed here; they stay owned by
SPEC §9.

## Status

| ID | Summary | Severity | Status |
|---|---|---|---|
| RG-1 | Conjunction lanes silently drop `--worktree` and `--allow-dirty` — a daemon override can vanish into a false PASS | Major | FIXED 2026-08-24 |
| RG-2 | Pointer↔lane linkage untested estate-wide: meta-tests certify `run-gate.toml` while the daemon executes the trove pointer | Major | FIXED 2026-08-24 |
| RG-3 | Dual-mount degenerates outside the cockpit namespace (`phys == repo` collapses both mounts) | Minor | FIXED 2026-08-24 |
| RG-4 | `pins.version` is validated but never checked — provenance theater claiming more than the check performs | Minor | FIXED 2026-08-24 |
| RG-5 | `{worktree}` textual substitution: quoting/injection surface, doubled sites in pointer argvs | Minor | FIXED 2026-08-24 |
| RG-6 | Exec-mode refusal prescribes a ciu-specific remedy for every project | Minor | FIXED 2026-08-24 |
| RG-7 | `usage()`/`--list` do not surface the environment contract or lane metadata (budget, clean_tree, description) | Minor | FIXED 2026-08-24 |
| RG-8 | No `--dry-run`: resolved docker argv/mounts/slice cannot be inspected without executing | Enhancement | FIXED 2026-08-24 |
| RG-9 | No `doctor` preflight subcommand | Enhancement | FIXED 2026-08-24 |
| RG-10 | Verdict/evidence artifact path printed only for ephemeral-container assay lanes; exec-mode prints nothing | Minor | FIXED 2026-08-24 |
| RG-11 | Uniform exit code 1 for every refusal — scripting cannot distinguish config error / dirty refusal / infrastructure failure | Minor | FIXED 2026-08-24 |
| RG-12 | Failing-container evidence destroyed: only last stderr line kept, container removed in `finally` | Minor | FIXED 2026-08-24 |
| RG-13 | Docs gaps: no end-to-end worked example; gitignore obligation unstated; adoption step 4 executed by zero projects; no root-level discovery; budget↔timeout drift unguarded | Minor | FIXED 2026-08-24 |
| RG-14 | Release model: wheel as second artifact beside the canonical script | Enhancement | FIXED 2026-08-24 |
| RG-15 | Assay lanes must execute in the selected worktree, not the invoking checkout | Major | FIXED 2026-08-24 |
| RG-16 | Central configs should be allowed to define shared lanes | Major | FIXED 2026-08-24 |
| RG-17 | required-env forwarding completeness (schema-oracle credentials silently dropped) | Major | FIXED 2026-08-24 |
| RG-18 | no pg_dump/PostgreSQL version-mismatch guard for schema lanes | Minor | OPEN — dstdns-side scope (schema-gate.sh), not run-gate.py; see body |
| RG-19 | schema-lane credential propagation must be verified by the gate, not by test failure | Major | FIXED 2026-08-24 |
| RG-20 | replace global gate flock with resource-aware admission | Enhancement | FIXED 2026-08-24 |
| RG-21 | linked-worktree checkouts break host-path-mapped lanes (srdm covergate evidence) | Minor | FIXED 2026-08-31 (rev 26) — directions 2+3 (doctor warning + docs); direction 1 is harness-side, not run-gate's to build |
| RG-22 | `git config --global safe.directory "*"` fails when global config already has safe.directory entries | Minor | FIXED 2026-08-24 |
| RG-23 | exec-mode's hardcoded env-forward allowlist was dropped with no consumer migration; unmigrated consumers silently stop forwarding `RUN_LIVE_TESTS`/`MOCK_MODE` | Major | FIXED 2026-08-31 (rev 25) — run-gate half; dstdns half open in its own repo |
| RG-24 | `resolve_container_name()` derives an exec-mode container's name from the shared-`.git`-owning repo's `ciu.global.toml`, never the judged worktree's own — a multi-instance (Mode-B) worktree's live lane silently targets the WRONG deployed container | Major | FIXED 2026-08-31 (rev 24) |
| RG-25 | `doctor`/`--check-env` cannot see that an assay lane's language needs a toolchain (node, go helper) in its environment — consume `assay lanes --json` (assay B044) for a per-lane fitness check; backport of ciu CIU-72 (b) | Enhancement | FIXED 2026-08-31 (rev 27) |
| RG-26 | no `--base REF` passthrough to `assay run --request-base` — assay B019 (≥ 3.0.0) unusable from the gate; delegating lanes DERIVED from `assay lanes --json`, no new lane key; backport of ciu CIU-72 (c), absorbs v8 proposal N12 | Major | FIXED 2026-08-31 (rev 28) |
| RG-28 | `run_host_lane` raised `KeyError('argv')` for a `kind = "assay"` lane on the built-in `host` environment — a config the validator ACCEPTS, so a traceback for a legal declaration (R-04) | Minor | FIXED 2026-08-31 (rev 28) |
| RG-29 | `cmru/run-gate.toml [lanes.assay]` pins a sidecar (`tools/assay/assay-2.2.0.pyz.sha256`) that no longer exists — cmru vendored 2.3.0 — which makes run-gate-project's OWN gate lane red via `validate-pointers` | Major | FIXED 2026-08-31 — cmru-side config (`cmru/run-gate.toml`), all four filename sites moved to 2.3.0 |
| RG-27 | run-gate has no persisted per-lane-per-commit invocation history and no query verb — a controller deciding sync-vs-async/defer rigor has no data; retriaged from ciu CIU-55 (2026-08-25) to run-gate, which is the layer with direct invocation visibility in the current (pre-v8) architecture | Enhancement | FIXED 2026-08-31 (rev 30, run-gate-P03) — `history [LANE] [--json]` verb; store `<project>/.run-gate/history.json`, per (judged worktree × project) |
| RG-30 | `doctor` and `--check-env` both pass `None` to `resolve_repo_and_worktree` (`run-gate.py:1789`, `:2068`) instead of the caller's `--worktree` value, so `doctor --worktree B` silently reports the INVOKING tree's answers, not B's — including RG-21's worktree-specific host-lane git-view WARN, which is exactly the per-tree answer that can legitimately differ | Medium | FIXED 2026-08-31 (rev 31, run-gate-P04) — new shared `resolve_worktree_scope()` (validates a real git worktree, refuses by name otherwise); `doctor`'s per-tree checks (git identity, RG-21 warning, mountinfo) and the shared `assay_toolchain_findings()` probe's `cd` target (used by both `doctor` and `--check-env`) now resolve/relocate under `--worktree`, disclosed in the report; `--check-env`'s env-drift scan follows it too and refuses upfront on a bad override (no per-check ledger to degrade into). SPEC `R-37` (`R-37a`/`R-37b`/`R-37c`). `./run-gate.py selftest` green: 394 passed, 2 skipped, diff-coverage 22/22 = 100.0%, exit 0 (commit `929be064`) |
| RG-31 | `assay_toolchain_findings()`'s own `resolve_repo_and_worktree` call (the toolchain-fitness probe shared by `doctor` check 5 and `--check-env`) still takes the RAW `worktree_override` string, not RG-30's new validated `resolve_worktree_scope()` — so a bad `--worktree` combined with an assay lane present degrades safely (a `[SKIP]` on that check, no false-`[OK]`) but with a MISLEADING reason string (blames "an assay older than 3.2.0" rather than naming the real `--worktree` problem, which `doctor` check 3 already reported correctly two checks earlier in the same report) | Low | FIXED 2026-09-01 (rev 32) — routed through `resolve_worktree_scope()`, the same validated resolver check 3 and `--check-env` already use; a bad override now raises the identical `GateError` and the existing per-lane `except GateError` SKIP handler reports the real cause, never a guess about assay's version. New regression test `test_bad_worktree_skip_names_the_real_problem_not_assay_version` asserts the SKIP line repeats check 3's own "not a directory" cause and never says "older than 3.2.0". `./run-gate.py selftest --allow-dirty` green: 395 passed, 2 skipped, diff-coverage 0/0 = 100.0% (pre-commit run), exit 0 |
| RG-32 | `[lanes.*.pins.*].budget` is silently inert — run-gate never read it, the governing value is the target `assay.toml`'s own `[lanes.<assay_lane>] budget`, and the key sits one nesting level below a REAL lane-level `budget` that reads identically (misread three times in one dstdns session) | Major | FIXED 2026-09-02 (rev 34, SPEC `R-08a`) — **BREAKING**: refused at load by name with the owner and the remedy; pin tables now validate their keys (`sha256`, `version`, nothing else), and a misplaced key that is itself a LANE key is named as one ("move it, do not delete it"); migration is TWO rounds over 18 of 35 dstdns lanes as measured 2026-09-03 (parsed, RW-13/RW-30; re-measure command in CHANGES), in CHANGES |
| RG-33 | `kind = "assay"` mutation lanes never receive `--resume` (or `--progress`), so a budget-capped retry re-tests every mutant from #1 — dstdns `sql-mutation`, three 120-minute retries spent on the first of four target files, `.assay/mutation-state/` never written | Major | FIXED 2026-09-02 (rev 33, SPEC `R-38`) — every assay-kind invocation now carries `--resume --progress .assay/progress-<assay_lane>.jsonl` unconditionally (no-ops without R2, per assay's own contract); a pin declaring a judge older than 2.4.1 refuses by name at argv construction; five new tests in `TestResumeAndProgressAlways` including the executed host-runner argv and the dry-run docker argv line; assay's own gate script mirrors it in the assay wave |
| RG-34 | a `kind = "command"` container lane whose `argv[0]` is a bare relative script path resolves against the container's `--workdir`, so it dies with `exit 127` in any container that mounts only the judged worktree (dstdns P152's `schema` lane, 100% reproducible) while working under the shared full-repo mount | Major | FIXED 2026-09-02 (rev 34, SPEC `R-30b`) — run-gate's half: `doctor` names the lane, the element, the fix and the mechanism; a WARNING, never a refusal, and run-gate never rewrites a consumer's argv. CLOSED 2026-09-03 (RW-26): the argv edit itself is dstdns-side, so the live `scale-admission` hit is a line in the dstdns notification, not an acceptance box run-gate can never tick |
| RG-35 | a lane's container outlives a dead run-gate client (`docker run -d` … `rm -f` in a `finally` the client never reaches), but nothing re-attaches: exit status, evidence and history are lost and the next invocation starts a DUPLICATE container for the same lane — the one-gate rule broken by the tool | Major | FIXED 2026-09-02 (rev 34, SPEC `R-39`) — `.run-gate/inflight/<lane>.json`, automatic re-attach/collect/report-lost, `--fresh` escape, commit mismatch refused |
| RG-36 | the only liveness signal for a long assay lane was a guessed total `budget`; progress made rate/ETA/stall observable but run-gate initially read none of it | Major | FIXED 2026-09-02 (rev 34, SPEC `R-40`) — progress disclosure and `stall_timeout`; **budget enforcement was added in rev 49 under RG-63** and now bounds execution time after admission and runner-lock acquisition |
| RG-37 | exec-mode container derivation (`run-gate.py` `resolve_container_name`, R-14a) reads `deploy.project_name` + `deploy.environment_tag` (fallback `deploy.network_name`) from the consumer's rendered `ciu.global.toml`; a CIU v8 checkout (SPEC-V8 draft.3, ciu CIU-92) renders `ciu.resolved.toml` instead, with identities as data under `[resolved.identities.<realization>.<service>] container_name`, and has no `deploy` table — every dstdns exec lane would fail container resolution the day dstdns moves to v8, while the operator decided (2026-09-02) that run-gate STAYS maintained in parallel with `ciu gate` and is "aligned with future changes in ciu v8" | Major | OPEN 2026-09-02 — filed from the v8 design review (ciu `docs/CIU-V8-ADVERSARIAL-REVIEW-2026-09-02.md` R-01, proposal §4.4 V8-19 / §4.11 N18): additive lookup order — when `ciu.resolved.toml` exists in the judged checkout, resolve `environments.<n>.container_name` (or a new `exec_in = "<realization>.<service>"` key) through `resolved.identities`, otherwise keep the v7 path; `kind = "sequence"` in-process conjunction lanes (N21) are the second alignment item |
| RG-42 | `TestPointerLinkageEstate`'s estate-wide sweep (`glob("*/nyxloom-trove/nyxloom.toml")`) certifies every subproject's declared gate pointers against real lanes on EVERY run-gate-project selftest, with no way for a subproject that is mid-bootstrap to say "not yet" — the ciu8 carve (`ciu8-P001`) hand-declared `[gates.tester-unified]` pointing at a `run-gate.toml` its own Part A bootstrap contract creates LATER, by design (mirrors `ciu/nyxloom-trove/nyxloom.toml`'s own pattern), which makes run-gate-project's OWN registered gate red for every consumer from the moment that trove is committed until the subproject's bootstrap actually lands — confirmed NOT caused by this wave or by RG-39 (reproduced identically on main's tip immediately before RG-39's own merge, `471703ee`) | Medium | OPEN 2026-09-03 — self-resolving once `ciu8-P001`'s Part A bootstrap creates `ciu8/run-gate.toml` (tracked separately in ciu8's own trove, not here); if a subproject bootstrap regularly outlives one gate cycle, the estate sweep could gain an opt-out (e.g. a `bootstrapping = true` key in `[project]`, skipped by `ESTATE_DOCS` until cleared) rather than every consumer tolerating a red selftest meanwhile — not built, no second occurrence yet to justify the mechanism |
| RG-43 | `'host' becomes a container default, 'bare-host' is the literal old behavior` (rev 36, SPEC `R-42`) landed on `debian-install-update` (commit `e7d5cd7c`) tagged `RG-39`/`R-39` in its own comments and `__revision__` history at merge time — a real collision with main's OWN unrelated RG-39 (exec-mode internal mutual exclusion, rev 35, SPEC `R-41`), caught and renumbered while merging `origin/main` into that branch (2026-09-03) rather than at the point of origin. The feature itself: `environment = "host"` (default) now resolves to a synthetic container environment instead of literal bare execution; `environment = "bare-host"` is the new name for the old behavior | Major | FIXED (the flip itself; SEE OPEN NOTE) 2026-09-02 (rev 36 numbering as of the 2026-09-03 merge) — the flip's own commit message says explicitly: "NOT done here, deliberately: the estate-wide sweep across every other `environment = "host"` lane (~14 projects) this flip affects." That sweep has not happened as of this filing — every one of those ~14 projects' `host` lanes now runs containerized instead of bare the moment this branch reaches main, unverified. **2026-09-08 (run-gate-P05):** the sweep gap reached run-gate-project's OWN code too — `main()`'s `--fresh` refusal (`not env` branch) is now reached only by `environment = "bare-host"` (the built-in host env resolves non-empty post-flip), but the refusal MESSAGE still said "the built-in host environment" and `TestFreshFlagScope::test_a_host_lane_refuses_it` still constructed an `environment = "host"` lane to exercise it — silently testing NOTHING once RG-43 shipped (the refusal never fires for `host` anymore; the test passed only because `--fresh` on a fresh `host` lane happens to also exit non-zero for an unrelated reason, a real container-launching lane trying to reach `ghcr.io`, which coincidentally matched `== 2` on THIS host's docker access but is environment-dependent and not what the test claims to prove). Fixed: message corrected to name `'bare-host'`, the test renamed and reconfigured to actually exercise it, and a new regression guard (`test_a_host_lane_no_longer_refuses_it`, fully docker-mocked) pins that `environment = "host"` genuinely does NOT refuse `--fresh` post-RG-43, so a future accidental re-widening of the `not env` check is caught. Found while getting run-gate-project's own `./run-gate.py selftest` gate green for the RG-44/RG-38/RG-40 batch — this is the estate-wide sweep's remaining scope (~14 external projects) still open, unrelated to this internal fix |
| RG-41 | a container `kind = "command"` lane has NO liveness signal at all: `stall_timeout` is refused there (rev 34, R-40c — it is judged from a progress file only an assay lane writes), so the lane shape most likely to hang is the one run-gate cannot bound except by a `budget` it never enforces | Major | FIXED 2026-09-08 (rev 38, run-gate-P06) — `stall_timeout` is legal on a `kind = "command"` container lane too now, judged from log-stream silence via a new `LogStreamWatch` (mirrors `ProgressWatch`'s poll-and-report shape, sourced from a background thread pumping `docker logs --timestamps -f`'s stdout). The pass-through stays live and in order (RW-9's own stated cost of this item); source disclosed by name on the fresh, re-attach and follow paths alike (`print_lane_bounds`, one site). An assay lane's behavior is unchanged. Round-1 adversarial review found the re-attach case was NOT actually correct (a hung container's replayed backlog read as `age ≈ 0`, silently granting amnesty) — fixed via the same wall-clock-translation mechanism RW-27 already proved for `ProgressWatch`'s mtime, using docker's own per-line `--timestamps` stamp. Proven end to end against a real container poll loop (a dedicated fake-docker log-stream shim) plus a red-first-proven unit suite for `LogStreamWatch`, including the re-attach-staleness case itself. Follower stall-detection (a pre-existing, unrelated gap RG-41 only widens exposure to) filed separately as RG-46 |
| RG-40 | `tools/coverage_gate.py` takes its changed-line numbers from `git diff base..HEAD` (committed) but its coverage from the file ON DISK, so running the `selftest` lane with `--allow-dirty` over an uncommitted change reports lines as uncovered that are covered — the two are offset by whatever the working tree added above them | Medium | FIXED 2026-09-08 (rev 37, run-gate-P05) — `_git_added_lines` now diffs `base_rev` alone (working tree, not `HEAD`): byte-identical to the old result on a clean tree, correct on a dirty one, no dirty/clean branch to keep in sync. Red-first proven with a real temp-git-repo regression test |
| RG-38 | resume state lives under the JUDGED project root, so a fresh worktree per run (cmru release transaction, Mode-B instances) loses it and a retry restarts from mutant #1 despite `--resume` | Medium | FIXED 2026-09-08 (rev 37, run-gate-P05) — assay B066 (`--state-dir`) shipped in assay-v5.2.0, unblocking this; every assay-kind lane on all three runners (container, exec, bare-host) now passes `--state-dir <repo>/.run-gate/assay-state/<project>/`, `repo` (the checkout owning the shared `.git`) being durable by construction even when the judged worktree is not; an older pin refuses by name (R-38's own floor extended to 5.2.0). Verified via the real, executed judge argv (fake-assay end-to-end test); the resume mechanics themselves are assay's own, already covered by assay's suite |
| RG-44 | `GONE_SIGNALS` matches docker's "gone" stderr case-sensitively; this docker version emits lowercase and the container-truly-gone case is never recognized | Major | FIXED 2026-09-08 (rev 37, run-gate-P05) — case-folded the match (`signal.lower() in stderr.lower()`); a genuinely gone container on this host's docker no longer wedges the lane with a stale inflight record. Red-first proven with a regression test reproducing the exact reported wording |
| RG-45 | a `vitest`-backed lane (`kind = "command"` or `kind = "assay"` coverage) can exit non-zero purely from vitest's own internal worker/main RPC heartbeat (birpc, hardcoded 60s timeout, no config path in any pool type) tripping under HOST-WIDE multi-tenant CPU contention across DIFFERENT repos' containers — RG-39's exec lock only serializes SAME-container access within one tool, it does not bound the SUM of concurrently-active gate containers' CPU quotas against the host's real core count | Major | OPEN 2026-09-08 — reproduced 5/5 identical (dstdns P176, `frontend-unit`+`ui_unit`, all real tests green every time); NOT run-gate's to fix — traced to assay's own R0 exit-code-only evaluation, moved to assay as B078 (design: `assay/nyxloom-trove/R0-STRUCTURED-REPORT-DESIGN.md`), see disposition in prose section below |
| RG-46 | a FOLLOWER (a client re-attaching to a lane whose inflight record names a still-alive owner) performs no independent stall detection of its own — `follow_container` arms neither `ProgressWatch` nor `LogStreamWatch`, by design (RW-14: "does NOT remove the container... all three belong to the client that started the run"), so if the OWNER is killed before its own `stall_timeout` fires, the follower just blocks on `docker wait`/`docker logs -f` forever, with nothing left to notice the container is silent | Minor | OPEN 2026-09-08 — found during RG-41's round-2 adversarial review; pre-existing (the same gap already applied to any assay lane declaring `stall_timeout` — `follow_container` has never armed a watch), RG-41 only widens exposure by lane COUNT (5 command lanes vs 3 assay lanes, RG-41's own backlog count). Not fixed as part of RG-41: a follower deciding to act on a stall it detects independently is a real design question (does it save evidence and `rm -f` a container it does not own? at minimum it would need the SAME owner-liveness re-check `promote_follower` already does before acting) that deserves its own scoped decision, not a silent addition to an unrelated item |
| RG-39 | run-gate has no internal mutual exclusion around the `docker exec`/`docker run` it performs into a resolved container, so every consumer must remember to wrap each invocation in its own `flock` (dstdns `GUIDE.md` §1) or two lanes racing the SAME container silently contaminate each other's evidence — but `resolve_container_name()` (the same function RG-37 tracks) already computes the exact container identity BEFORE that exec, every single call, so the tool already has everything it needs to serialize itself | Medium | FIXED 2026-09-03 (rev 35, SPEC `R-41`) — refinements (1) and (2) below, built exactly as specified; refinement (3) deliberately NOT built (RG-37, the v8 `ciu.resolved.toml` container-identity path, doesn't exist yet). New `acquire_exec_lock()` takes `/tmp/run-gate-exec-<container>.lock` (RG-20's `_open_lockfile()` discipline, now factored into a shared helper) on the container name `resolve_container_name()` resolves — resolved ONCE in `main()`, threaded into `run_exec_lane()` (no longer re-derived there) so the lock key and the `docker exec` target can never drift apart. Acquired strictly after `acquire_shared_locks()`'s locks in `main()`'s dispatch, released from the SAME `finally` (`exec_lock_fd`, closed before the shared-infra fds — LIFO, not load-bearing). `LOCK_EX` blocking with a `waiting for container '<name>' — another gate holds <path>` line; `--dry-run` prints the planned lock (name + path) and never blocks. Five new tests in `TestExecModeMutex`: same-container serialization (thread-raced, proven genuinely red pre-fix — a leaked lock fd on that test's own assertion failure path self-deadlocked the NEXT test via flock()'s per-open-file-description semantics, fixed with a try/finally, unrelated to the shipped fix itself), isolated containers never contend, `--dry-run` never blocks, the lock releases even when the lane raises (finally path), and a direct ordering assertion (shared-infra locks acquired before the exec lock); a sixth test (added after the first `selftest` run below caught it uncovered) exercises `acquire_exec_lock()`'s OSError branch, in-process (a `run_tool()` subprocess, RG-20's own precedent's pattern, is invisible to this suite's coverage instrumentation). Red-first proven: a scoped `git stash` of `run-gate.py` alone (fix reverted, tests kept) reproduced 3/5 new tests failing for the expected reasons before the fix, restored clean after. `./run-gate.py selftest` green (post-commit `2c6b2bbc` + a same-day coverage follow-up): 495 passed, 2 skipped, diff-coverage 25/25 = 100.0% (≥ 100.0% floor), exit 0. Originally filed from dstdns (D-321/D-339/D-321-correction): acquire an internal `flock` keyed by the resolved container name (or `${project_name}-${environment_tag}`, the same pair `resolve_container_name()` already reads) around the exec/run call itself, so a caller-side `flock` is no longer required for correctness, only for pre-emptive scheduling (e.g. a caller who wants to skip a busy container rather than block). A genuinely independent container (different `project_name`/`environment_tag`, including a Mode-B instance) naturally gets a distinct lock name and runs unblocked; two consumers that resolve to the SAME container (main's shared instance, or ciu's `--shared-infra-ref-services`) naturally serialize correctly with no caller coordination needed. Cross-reference RG-37: whichever container-identity resolution path RG-37 adds for `ciu.resolved.toml` (v8) should feed the SAME lock key, not a second scheme. **2026-09-03 (ciu v8 design, SPEC-V8 draft.5 / proposal rev 3.2 §4.11 N22): buildable as described, with three refinements.** (1) Exec mode only — an ephemeral `docker run` container is per invocation, there is nothing to serialize. (2) Take the lock AFTER `acquire_shared_locks()`' sorted shared-infra locks and release it in the same `finally` — a fixed global order (shared-infra, then the exec target) so no ABBA with RG-20 is possible; `/tmp/run-gate-exec-<container>.lock` with RG-20's 0600+O_NOFOLLOW discipline, LOCK_EX blocking with a "waiting for container X — another gate holds …" line, dry runs plan but never block (`acquire_shared_locks` is the pattern to copy); hold across the whole `run_exec_lane()` including evidence collection, and keep `flush_run_record` outside it (RG-27). (3) Alignment with v8: once RG-37 reads `ciu.resolved.toml`, key the lock on the owning Realization's **stack directory** (`[realization.<R>] location` of the container's owner, `flock` on the directory) instead of a name — draft.5 S14.4.7 declares the checkout root and the stack directory the ONLY canonical lock keys, `ciu gate` exec lanes take that same directory lock (S16.5.7) and `ciu lease acquire --realization` exposes it, so v7 run-gate and v8 ciu serialize against each other during the cutover; the name-keyed `/tmp` file is the v7-only form. The caller-side `flock` of dstdns GUIDE §1 stays valid as an outer lock (always acquired first → consistent order) and becomes optional for correctness |
| RG-47 | `--worktree` selected the judged files but not `run-gate.toml`, so main's lanes could judge a worktree and hide lanes that existed only there | Major | FIXED 2026-10-04 (rev 47): project and inherited config resolve from the selected worktree, including monorepo-relative projects; missing project config refuses; path and SHA-256 are printed/recorded. Merged duplicate RG-65 here |
| RG-48 | lane worker count could disagree with the environment CPU cap when `resources.cpus` was absent | Major | FIXED 2026-09-12 — `doctor` names the missing cap; environment-level `resources.cpus` now supplies the shared limit |
| RG-49 | `--state-dir` setup fails against root-owned parents in partial-bind worktree containers | Major | FIXED 2026-10-04 (rev 50), collision follow-up rev 54: read-only state-root preflight and durable mount; external project keys use SHA-256 to avoid path-separator collisions |
| RG-50 | B065's first-candidate rate calculation produced a negative rate from Assay's `-1` baseline | Minor | FIXED 2026-09-10 |
| RG-51 | delegated lanes could derive their default comparison base from stale upstream state | Major | FIXED 2026-09-11 (rev 40), with ciu CIU-106 |
| RG-52 | composite-lane base substitution used unquoted shell text | Major | FIXED 2026-09-11 (rev 40) |
| RG-53 | `coverage_gate.py` ignored `missing_branches` while claiming branch coverage | Minor | FIXED 2026-09-12 (RG-55 wave) |
| RG-54 | Assay's merge-commit base behavior contradicted the apparent resolved base | Major | CLOSED 2026-09-30 — first-parent behavior is deliberate and false PASS is blocked |
| RG-55 | no per-lane resource profile was measured or persisted | Major | FIXED 2026-09-12 |
| RG-56 | profiler-registry admission has no capacity control | Minor | OPEN |
| RG-57 | bare-host lanes did not record resource profiles | Minor | FIXED 2026-09-12 |
| RG-58 | bare-host `stall_timeout` declarations had no config-load warning | Minor | FIXED 2026-09-12 |
| RG-59 | daemon-absent profiling warning named the wrong cause | Minor | FIXED 2026-09-12 |
| RG-60 | exec-lane profiling had no inflight recovery record | Major | FIXED 2026-09-12 |
| RG-61 | RG-55 wave left documentation drift | Minor | FIXED 2026-09-12 |
| RG-62 | two order-/timing-sensitive selftest flakes were found live | Minor | OPEN |
| RG-63 | assay lane budget included time queued on the exec lock | Major | FIXED 2026-10-04 (rev 49): budget starts after admission and runner locks |
| RG-64 | caller-side lock checks could not reliably diagnose actual container occupancy | Minor | FIXED 2026-10-04 (rev 51): `status` reads internal exec-lock holders/waiters, selected-tree inflight records, and daemon-wide admission state |
| RG-65 | duplicate of RG-47: worktree-only lanes were hidden by invoking-CWD config resolution | Major | MERGED INTO RG-47 2026-10-04 (rev 47) |
| RG-66 | no way to pass assay's `--reuse-from` / `--rejudge` through `run-gate <lane>`, so assay 7.1+ provenance-safe selective R2 reruns cannot be used via the gate | Minor | FIXED 2026-10-04 (rev 49): assay-only selective flags are forwarded |
| RG-67 | no per-environment (per-runner-container) invocation limit, and a composite lane does not declare which runners its members use, so consumers hand-hold whole-invocation flocks and the composite over-holds a second runner — **(a) WITHDRAWN 2026-10-03 (D-667); (b) remains** | Minor | FIXED 2026-10-04 (rev 49): (a) remains withdrawn; (b) uses native serial sequences |
| RG-68 | `footprint` only counts PASS runs; a completed FAIL (for example surviving mutants) is a valid resource measurement, so first-run budget calibration stalls on a red first run | Minor | FIXED 2026-10-04 (rev 49): completed profiled FAIL is opt-in with `--include-failed` |
| RG-69 | `footprint --write` could not update one lane without regenerating the whole manifest | Minor | FIXED 2026-10-04 (rev 49): repeatable `--lane` merges selected entries |
| RG-70 | no canonical way to run a repository script inside a worktree's test-runner | Minor | ABSORBED into ciu CIU-118 `ciu exec` (implementation tracked by CIU-118; no separate run-gate v7 work) |
| RG-71 | schema lane could not target one file for fast iteration | Minor | FIXED 2026-10-04 (rev 49): `accepts_args = true` enables `--` command args |
| RG-72 | failed assay lanes left no durable failure evidence before the next run overwrote it | Minor | FIXED 2026-10-04 (rev 49): digest plus bounded failed-run archive |
| RG-73 | an `ephemeral` environment cannot stand in for a per-worktree runner: literal `image`, and the judged worktree is not mounted at the image's canonical root | Major | OPEN 2026-10-03 |
| RG-74 | post-merge trunk base (`HEAD^1`) and composite-member base propagation are consumer scripts (dstdns `gate-base.sh`), not run-gate derivations | Minor | FIXED 2026-10-04 (rev 49): `[project].trunk` and native sequence base propagation |
| RG-75 | no lane-scoped throwaway service (database): schema/mutation lanes hand-provision and tear down their own Postgres | Major | OPEN 2026-10-03 |
| RG-76 | external-assay consumers restate judge command, pin and one lane block per assay lane (dstdns: 118 identical pin blocks); import lanes from `assay lanes --json` | Minor | FIXED 2026-10-04 (rev 49): v8-shaped `{ environment, lanes }` import |
| RG-77 | the per-assay-lane `--state-dir` contract (RG-38) and its root-owned-parent repair (RG-49) are in no SPEC rule or skill, so consumers restate them in their own instruction files | Minor | FIXED 2026-10-04 (rev 49): shipped state contract documented; RG-49 implementation is tracked separately |
| RG-78 | adopt the ciu v8 closed exit table and explicit environment modes in run-gate now (backport, operator ruling D-654): lane exit passthrough overlaps the 2/3 refusal codes, and the built-in `host` environment is a container | Major | FIXED 2026-10-04 (rev 49): single finish path, AST guard, byte-status lane oracle |
| RG-79 | exec-mode resolution **silently falls back to main's runner** when the judged worktree has no rendered ciu config (a shadowing default, AGENTS §4.2a); the worktree's own test-runner is the design (D-647 #2, D-666), so run-gate must refuse and name "start this worktree's own test-runner" (reframed 2026-10-03; originally filed as a stray-render defect) | Major | FIXED 2026-10-04 (rev 47; same implementation as RG-47) |
| RG-80 | no daemon-wide cap on concurrent gates: the cross-worktree cap is a consumer flock wrapper (dstdns `gate-slot.sh`); build SPEC-V8 S21's count mode (Docker-name tickets, tombstones, deadlines, run marker, published `ciu-admission-<g>` object) behind an off-by-default switch, so the wrapper retires before v8 | Major | FIXED 2026-10-04 (rev 49; operability rev 51): ticket/publish and owner/reaping packages; read-only status view and enabled-policy doctor checks |
| RG-81 | internal source-backed Assay lanes fail because editable installs omit artifact `judge_provenance`; verify selected source, bind the verdict commit, and preserve source/artifact mode across re-attachment | Major | IN PROGRESS (rev 54; Review #11 findings addressed, package gate pending) |
| RG-82 | Adopt cli-extended (unified adoption, order 7 of 8): full grammar re-registration of the 11k-line single-module launcher, real wheel dependency (CX-D1, CX-D12) | Enhancement | OPEN — planned (filed 2026-10-05 as RG-81, renumbered at the merge with main's RG-81; requires cli-extended 0.2.0) |
| RG-83 | `tests/test_run_gate.py`'s `install_fake_assay` writes its fake `assay`/`assay.real` into the FIRST ENTRY OF THE REAL `$PATH` (the operator's `~/.local/bin`) when a test has not already prepended a tmp shim dir | Major | OPEN (filed 2026-10-05 as RG-82, renumbered at merge; leaked files observed 2026-10-04 14:36, not deleted) |
| RG-84 | run-gate runs as an unreaping PID 1 (no init) and keeps reporting lane verdicts after the container hits `pids.max`; refuse PID 1, and treat `pids.events`/`memory.events` increments as infrastructure errors | Major | IN PROGRESS (rev 55; PID 1 and low-pids live acceptance PASS; registered selftest pending; contaminated R2 outcomes discarded) |
---

## RG-1 — conjunction lanes silently drop `--worktree` and `--allow-dirty`

**Found by:** adversarial correctness review of the adoption wave, 2026-08-22.

### Observed mechanism

`cmru/run-gate.toml` `[lanes.gate]` (the conjunction pattern introduced by the
adoption and endorsed by CONSUMERS.md "Gate-conjunction lanes") invokes
sub-lanes as bare sibling calls:

```
argv = ["bash", "-c", "./run-gate.py assay && ./run-gate.py coverage && …"]
```

The argv contains **no `{worktree}` token**, so `substitute_worktree`
(`run-gate.py::substitute_worktree`) is a no-op, and argparse flags given to
the parent invocation are not forwarded — each sub-process re-derives the
judged worktree from *its own* cwd/git toplevel (`resolve_repo_and_worktree`)
and receives none of the parent's `--worktree`/`--allow-dirty`.

### Reproduction

```bash
cd /repo/cmru                       # any checkout whose toplevel is NOT the attempt tree
./run-gate.py gate --worktree /attempts/w1 --allow-dirty
# parent lane: clean_tree=false → no check anywhere
# sub-lanes:   judge /repo (clean), print `lane 'gate' exit 0`
# /attempts/w1 was never tested — silent false PASS
```

Not reachable through today's shipped pointer shape (`cd {worktree}/cmru &&
exec ./run-gate.py --worktree {worktree} gate` makes cd-target ≡ override),
which is why the sweep was green — but SPEC R-02's daemon case ("daemon
substitutes its attempt path textually before invoking") is precisely the
invocation class that breaks, and every future conjunction copies this one.

### Why run-gate owns it

Flag semantics are run-gate's contract (R-02/R-03); the tool accepts an
override and then lets a config pattern discard it silently. That is the
estate's masked-default hazard class ("a default is legitimate only when…
if this default is wrong, does anything fail loudly?" — nothing does here).

### Proposed contract (either side suffices; both are compatible)

1. **Forward:** conjunction-conventional substitution token, e.g. sub-invocations
   written as `./run-gate.py --worktree {worktree} assay && …` (cmru-side,
   mechanical), and/or
2. **Reject:** if `<lane>`'s argv contains neither `{worktree}` nor any
   sub-invocation marker, error when `--worktree`/`--allow-dirty` are passed
   to a lane whose kind would ignore them — loud, naming the lane.

### Oracles

- Parent invoked with `--worktree W` ⇒ every sub-lane judges W (assert via a
  fake inner command recording its cwd/toplevel).
- Controlled wrong implementation: today's argv under `--worktree W` must
  fail oracle 1.
- `--allow-dirty` reaches sub-lanes (dirty sub-lane proceeds instead of
  refusing).

**FIXED 2026-08-24** (both halves, per interview; sequenced after RG-15 as
planned):
1. **Forward:** `cmru/run-gate.toml [lanes.gate]` now writes
   `./run-gate.py --worktree {worktree} <sub>` for EVERY sub-invocation —
   the reference shape, mirrored in CONSUMERS' conjunction recipe.
2. **Reject:** SPEC `R-25` — a CONTAINER command lane (ephemeral or exec)
   invoked with `--worktree` whose argv has no `{worktree}` token refuses
   (exit 2) before execution. Assay lanes relocate automatically (R-21) and
   host lanes relocate via cwd, so both are exempt.

Discovery worth recording: post-RG-15 the BARE host conjunction is safe BY
CONSTRUCTION (the host runner's cwd relocates into the override tree, so
bare sub-calls derive the same toplevel) — the residual hazard is exactly
the container class, which the guard covers. `--allow-dirty` is NOT
forwarded by any mechanism: conjunctions that want dirty-tolerance write it
per sub-call explicitly; anything else would silently weaken sub-lanes'
clean-tree checks (the loud-refusal direction is the safe one). Oracles:
forwarded shape judges W (nested fixture records the SUB-lane's docker run);
controlled wrong shape (token-less ephemeral lane under --worktree) refuses
with docker never invoked. Tests: TestConjunctionOverrideGuard x5.

**SPEC owner:** §2 (R-02/R-03) + §5 execution contract; CONSUMERS.md
conjunction recipe must change with it.

## RG-2 — pointer↔lane linkage untested: the dispatched artifact is certified by no test

**Found by:** adversarial correctness review, 2026-08-22 (second reviewer
independently flagged the weakened assertion form).

### Observed mechanism

Post-adoption, what nyxloomd actually executes is each project's *pointer*
in `nyxloom-trove/nyxloom.toml [gates.*].argv`
(`… cd {worktree}/<proj> && exec ./run-gate.py --worktree {worktree} <lane>`).
The meta-tests updated during adoption were repointed at `run-gate.toml`
(the SSOT) — correct for mechanics coverage, but **no test reads the
pointer**: renaming a lane in `run-gate.toml` while updating the tests in the
same commit leaves the suite green and every daemon dispatch dying on
`unknown lane '<name>'`. Additionally, `assay/tests/test_cgroup_parent.py::
test_nyxloom_gate_uses_verified_value_without_a_literal_slice` replaced exact
list equality with substring checks (`"run-gate.py" in pointer`), which a
broken pointer (`echo run-gate.py tester-unified`; `cd …/assayX`;
`tester-unified-typo`) satisfies trivially.

Affected today: ciu, assay, topos pointers (each `nyxloom.toml [gates.*]`),
and cmru's `[steps.run-tests]` pointer (same linkage, different consumer).

### Reproduction

```bash
# in any adopted project, e.g. topos:
sed -i 's/topos-suite/topos-suiteX/' topos/run-gate.toml   # rename lane, keep pointer
pytest topos/tests/test_gate_environment.py -q             # green
cd topos && ./run-gate.py topos-suite                      # unknown lane — loud, but only live
```

### Why run-gate owns it

The linkage contract (pointer names a real lane, with the canonical flag
shape) is run-gate's vocabulary. Per-project tests can enforce it, but only
if run-gate defines what a correct pointer IS.

### Proposed contract

A pointer-validation helper, exposed either as `run-gate.py validate-pointers
<path-to-trove-toml>` or as a documented assertion recipe, checking:
pointer parses → contains exactly one `cd <project-dir>` target matching this
project → invokes `./run-gate.py --worktree {worktree} <lane>` → `<lane> ∈
[lanes]`. Then one such test per adopted project. Substring assertions in
assay's meta-test restored to structural checks (parse the pointer string).

### Oracles

- Renamed-lane scenario above must go RED at test time, not dispatch time.
- Controlled wrong implementation: pointer with `assayX` dir or missing
  `--worktree` fails validation.

**SPEC owner:** §2 (CLI contract gains validate verb or documented recipe);
CONSUMERS.md adoption steps gain "add the linkage test".

**FIXED 2026-08-24** (SPEC `R-27`): new verb `run-gate.py validate-pointers
CONSUMER.toml [--root DIR]`. Schema-agnostic: it walks any parsed TOML and
certifies EVERY run-gate invocation it finds (an argv-style list whose first
element is run-gate.py is joined into one pointer — cmru's list-form release
step). Per invocation it enforces exactly one `{worktree}`-relative cd
target whose project has a run-gate.toml (no cd → the document's own
directory when that IS a project), `--worktree {worktree}` present whenever
the pointer substitutes `{worktree}` at all (the RG-1 false-PASS class,
caught at test time), and exactly one positional lane name that EXISTS in
the effective lane set — loaded with the REAL parser including central
inheritance, never a second parse. Exit 2 on any defect; documents that
never invoke run-gate (srdm's gate.sh pointers) are trivially clean. Estate
linkage now runs on every suite run: TestPointerLinkage ×9 construction pins
(renamed-lane oracle goes RED at test time) + TestPointerLinkageEstate —
all five trove nyxloom.toml files plus cmru.toml certified against their
SSOT lanes (6 real invocations, all green today). assay's meta-test
substring assertion restored to STRUCTURAL checks: exact cd target, exact
token list `--worktree {worktree} tester-unified`, lane-exists-in-SSOT.
CONSUMERS adoption gains step 3a ("Certify the linkage").

## RG-3 — dual-mount degenerates outside the cockpit namespace

**Found by:** adversarial correctness review, 2026-08-22.

`run_container_lane` mounts `-v <phys>:<phys> -v <phys>:<repo>`
(`physical_path` derived from `/proc/self/mountinfo`). Inside the devcontainer
phys=`/home/vb/volkb79-2/vbpub`, repo=`/workspaces/vbpub` → two distinct
views, reproducing AGENTS trap #2. On a bare host `/.dockerenv` is absent and
`physical_path` returns the path unchanged (`phys == repo`) → both `-v` flags
collapse and containers see ONLY the host path, whereas every pre-adoption
consumer argv pinned `-v /home/vb/volkb79-2/vbpub:/workspaces/vbpub`
unconditionally. Latent (all current triggers run inside the cockpit) but a
silent divergence from the documented four-traps recipe the tool embodies.

**Proposed contract:** derive the second namespace view from mountinfo when
present; when absent, either declare the constraint loudly at startup
("container lanes assume the devcontainer namespace alias; found none") or
accept an explicit env fact. Never collapse silently.

**FIXED 2026-08-24** (explicit env fact — the "5b" explicitness choice):
`dual_mount_flags(repo, phys)` emits the two `-v` views only when they are
DISTINCT. When `phys == repo` (bare host — mountinfo offers no alias) the
lane refuses (exit 2, message names "collapse") unless
`$RUN_GATE_MOUNT_ALIAS='<host>=<namespace>'` declares the second view;
malformed entries and a host side ≠ repo root are refused by name. The alias
only ever changes the container-side path of the SECOND view — both flags
always bind-mount the same physical tree. SPEC `R-23`; README path-
namespaces bullet and CONSUMERS resolution-order paragraph amended. Test
note: the end-to-end refusal is driven in-process (`run_gate.main`) because
subprocess runs derive REAL mountinfo views and this devcontainer's `/tmp`
bind mount hides the bare-host collapse from them.

**Oracle:** simulated mountinfo without an alias → loud refusal or explicit
single-mount notice naming both paths tried; controlled wrong implementation
(today's silent collapse) fails it.

## RG-4 — `pins.version` is validated but never checked (provenance theater)

**Found independently by BOTH reviewers**, 2026-08-22.

`_validate_lane` requires `pins.*.version` as a string (run-gate.py ~:136);
nothing ever reads it (`build_assay_inner` uses only `sha256`). Both shipped
configs declare `version = "2.1.0"` (ciu/run-gate.toml, cmru/run-gate.toml),
and CONSUMERS.md's example comment claims it is "verified against the judge
the image carries" — the message states a conclusion the code never performs.
This is the cmru KI-12 anti-pattern class ("a check's message states a
conclusion; the comparison is narrower").

**Reproduction:** set `version = "9.9.9"` in either config → lane runs
identically green; sha256 sidecar still guards bytes, but the declared
version is fiction.

**Proposed contract:** make it honest — either (a) mechanical: after pin
verify, run `<assay_command> --version` (or read the pyz's embedded version)
inside the container and fail on mismatch with the declared value; or (b)
rename the key to `note`/drop it from the schema and examples. (a) preferred:
cheap, stdlib, closes the gap.

**Oracles:** mismatched version → lane refuses naming both values; equal →
silent; controlled wrong implementation (today's no-check) fails oracle 1.

**FIXED 2026-08-24** (option a, mechanical): `build_assay_inner` gains an
in-lane probe per pin declaring `version` — `<assay_command> --version` must
succeed and its output match the declaration, else the lane exits 2 naming
both values. Empty declarations rejected at validation. Oracles include a
LIVE shell execution of the generated inner (fake artifact reporting the
wrong/right version). SPEC R-08 + CONSUMERS schema comment updated: declaring
`version` asserts the `--version` convention.

## RG-5 — `{worktree}` textual substitution: quoting/injection surface

**Found by:** adversarial correctness review, 2026-08-22.

Pointers embed `{worktree}` twice into a `bash -c` STRING
(`cd {worktree}/ciu && exec ./run-gate.py --worktree {worktree} ciu`); a path
containing spaces/shell metacharacters word-splits or executes. Old argvs had
one site and equally held `docker run`, so no privilege boundary changed —
but the surface doubled, and the daemon controls paths only by convention.
Hardening options: reject paths outside `^[A-Za-z0-9_./ -]+$` at the daemon
boundary; or move the worktree out of band entirely (env var consumed by
run-gate, e.g. `RUN_GATE_WORKTREE`, reducing pointers to
`cd "$PWD" && exec ./run-gate.py <lane>`). The latter also shrinks RG-2's
linkage surface.

**SPEC owner:** R-02 (substitution contract).

**FIXED 2026-08-24** (charset guard now; env-var migration deferred to the
CIU-V7 cutover, per interview): `check_worktree_charset(worktree)` enforces
`^[A-Za-z0-9_./][A-Za-z0-9_./-]*$` on the RESOLVED worktree before any lane
runs — every kind uniformly (the daemon pointer recipe embeds `{worktree}`
into bash strings regardless of what an individual lane does with it).
Refusal is exit 2 naming the offending characters and the reason; mid-path
leading-dash components are deliberately ALLOWED (absolute paths always
start with `/`, so the flag look-alike hazard never materializes — tighter
than the backlog's sketch, which wrongly admitted spaces). SPEC R-02
amended; README "Gate-safe paths" bullet; CONSUMERS adoption step 3.
Revisit at V7 cutover: out-of-band `RUN_GATE_WORKTREE` to shrink pointer
argvs (and RG-2's linkage surface) entirely.

## RG-6 — exec-mode refusal prescribes a ciu-specific remedy for every project

**Found by:** consumer-UX review, 2026-08-22.

`run_exec_lane`'s not-running refusal hardcodes `start it via 'ciu up --dir
tools/test-runner' or the project's runner lifecycle command` (run-gate.py
~:462-467) for ANY exec-mode project. dstdns — the stated exec-mode adopter —
gets told to run a ciu directory that does not exist there. Violates the
estate rule that a remedy message must prescribe a CORRECT fix.

**Fix:** derive the suggestion from `name_src` (already in hand): declared
`container_name` → "declare/start it in your deployment authority";
ciu.global.toml-derived → name that file and `ciu render`/`ciu up` as
applicable. **Oracle:** exec lane with stopped container → message names the
source actually used; dstdns-shaped project never sees ciu's command.

**FIXED 2026-08-24** (remedy derived from resolution source, per the entry):
`resolve_container_name` now returns `(name, source, start_remedy)` — a
declared `container_name` yields "start it via YOUR project's deployment
authority", ciu-derived names yield `ciu render`/`ciu up` naming the config
file used. The not-running refusal interpolates the remedy verbatim; the
hardcoded `ciu up --dir tools/test-runner` is gone. SPEC R-14a amended;
CONSUMERS dstdns recipe notes the rule. Oracle covered both ways: declared-
name refusal asserts `"ciu"` appears NOWHERE in stderr; ciu-derived refusal
asserts the lifecycle AND `ciu.global.toml` are named.

## RG-7 — usage()/`--list` hide the environment contract and lane metadata

**Found by:** consumer-UX review + CLI audit, 2026-08-22.

`usage()` prints rev, two usage lines, and a lanes table of
`name/kind/environment` only; the flags section documents neither semantics
nor caveats; the environment contract is invisible until first failure:

- `$CGROUP_PARENT_DEV_GATES` required for container lanes (absent = hard
  error at runtime);
- `RUN_GATE_EXTRA_MOUNTS` colon-separated `host=container` pairs (ephemeral
  lanes only) — documented only in SPEC R-14b;
- `--allow-dirty` says nothing about assay lanes enforcing their OWN
  clean-tree rule regardless (two-layer refusal confuses: user passes the
  flag they were told about, assay refuses mid-streamed-logs);
- budgets/clean_tree/memory exist per lane but appear nowhere;
- exec-mode passthrough allowlist (`MOCK_MODE`, `RUN_LIVE_TESTS`) lives only
  in a code comment; ephemeral lanes have no arbitrary-env mechanism at all.

**Proposed:** ENVIRONMENT section in usage(); table gains budget +
clean_tree columns; optional `description` lane key (validated, shown);
document the gitignore obligation for command-kind artifacts (see RG-13).
Schema change additive; one parser owns it.

**FIXED 2026-08-24:** `usage()` gains FLAGS (`--worktree`; `--allow-dirty`
with the explicit two-layer caveat that assay still enforces its own
clean-tree rule) and ENVIRONMENT CONTRACT sections naming all three
variables the tool reads — `CGROUP_PARENT_DEV_GATES`,
`RUN_GATE_EXTRA_MOUNTS`, `RUN_GATE_MOUNT_ALIAS` — with failure semantics.
The human lane table now shows `clean_tree`, advisory `budget`, `memory`,
and a new validated optional `description` key (one line, `--help` only).
`--list` stays THREE columns by design: it is the machine-readable contract
(CONSUMERS anti-goal) and never grows columns. SPEC R-01/R-08 amended;
CONSUMERS schema comment updated. Gitignore obligation for command-kind
artifacts lands with RG-10/RG-13. Tests: TestUsageEnvironmentContract x5.

**Current contract (rev 49, 2026-10-04):** the lane table still displays
`budget`; it is a hard run-gate wall-clock limit that starts after admission
and runner-lock waits (RG-63/RG-78), not an advisory value.

## RG-8 — no `--dry-run`

**Enhancement, 2026-08-22 (CLI audit; wanted repeatedly during the adoption
sweep).** The docker argv/mounts/slice/env are fully assembled before
execution (`run_container_lane` prints them, then runs). Add `--dry-run`:
print the plan (image, slice+source, mounts incl. extra mounts, memory, inner
command) and exit 0 without `docker run`. Invaluable for debugging adoption
(mount/slice mistakes) cheaply; zero new machinery.

**Oracle:** `--dry-run <container lane>` performs no `docker run` (fake
docker records argv), prints the identical argv the live run would use.

**FIXED 2026-08-24** (SPEC `R-28`): `--dry-run` on every lane kind. The
flag is a REHEARSAL, not a bypass: all preflights run exactly as live
(config, required-env, worktree resolution + charset guard,
override-reachability, clean-tree — `--allow-dirty` composes), then the
runners return the fully assembled plan and exit 0 instead of executing.
Container lanes print the identical docker argv (same assembly code path;
only `--name` differs by pid/epoch — the oracle normalizes it in tests);
exec lanes rehearse name resolution AND the runner-running check (a stopped
runner gives its real exit-2 refusal); host lanes print argv + cwd. No
evidence-path disclosure on dry runs — nothing ran, nothing landed. Tests:
TestDryRun ×6 including the oracle (live vs dry argv equality with name
normalized) and both preflight rehearsals.

## RG-9 — no `doctor` preflight subcommand

**Enhancement, 2026-08-22 (consumer-UX review).** Recompose existing checks
into `./run-gate.py doctor`: docker present; slice resolvable (var or
declared) + LoadState where systemd reachable; physical-path derivability
from mountinfo; git identity/safe.directory writability; referenced images
exist locally. One command turns four first-contact failure classes into a
preflight a newcomer runs once. All inputs already implemented — pure
recomposition, stdlib only.

**FIXED 2026-08-24** (SPEC `R-30`): `./run-gate.py doctor`. One
`[OK]/[WARN]/[FAIL]` line per check + summary; exit 2 iff any FAIL. Checks:
docker present; per-environment slice resolution + LoadState where systemd
reachable (distinct envs deduplicated); git worktree resolution; mountinfo
derivability (bare-host view = WARN naming `$RUN_GATE_MOUNT_ALIAS`, per
RG-3); `/tmp` writability for GIT_CONFIG_GLOBAL; referenced images present
locally (advisory WARN — a missing image may legitimately pull). Doctor
runs nothing and must itself survive a broken host: a preflight that
tracebacks on exactly the machine that needs it defeats its purpose, so an
unrunnable git is a `[FAIL] git` line, not a traceback. Tests: TestDoctor
×6 (healthy all-OK, unresolvable-slice refusal, missing-image advisory,
docker-absent failure, host-only skip, mountinfo always reported).

## RG-10 — verdict/evidence path printed only for ephemeral-container assay lanes

**Found by:** consumer-UX review, 2026-08-22 (R-18 intent: "print WHERE the
verdict artifact lives").

`run_container_lane` prints `.assay/verdict-<lane>.json` post-run;
`run_exec_lane` prints nothing equivalent; command-kind lanes' evidence paths
(`.assay/mutation-cmru.json`, `.assay/coverage-canary-cmru.json`) exist only
inside opaque argv strings. After a green run a consumer is often not told
where evidence landed.

**Proposed:** optional `artifacts = ["path", …]` lane key (validated, printed
on every lane exit, `{worktree}`-substituted), defaulting to the assay-verdict
convention for assay-kind lanes in both runner modes. Backfill cmru's three
evidence paths.

**FIXED 2026-08-24** (SPEC `R-08` + `R-18` amendment): `artifacts` is a
validated lane key (non-empty list of non-empty strings); new
`print_lane_artifacts` runs after EVERY lane exit in ALL THREE runners —
ephemeral, exec, host — any kind, success or failure. Assay lanes always
disclose `.assay/verdict-<assay_lane>.json` resolved against the EFFECTIVE
project dir (the ephemeral runner's inline print was replaced by the helper,
so the path is now worktree-correct under `--worktree` too, not just
present); declared entries are `{worktree}`-substituted,
absolute-or-project-relative, and deduplicated against the verdict
convention. A failed lane still names its evidence — that is exactly when
the reader needs the paths. cmru's three evidence paths backfilled as
declared `artifacts`. Tests: TestArtifactsDisclosure ×8 (ephemeral command
lane disclosure, verdict dedup, `{worktree}` substitution, exec assay
verdict, host lane, failed-lane disclosure, invalid-key rejection ×2).

## RG-11 — uniform exit code 1 for every refusal

**Found by:** consumer-UX review, 2026-08-22.

All `GateError`s exit 1 (`main()`'s except handler): config errors, dirty-tree
refusals, docker failures, unknown lanes are indistinguishable to scripts.
Messages carry the information; machines don't. With CI fan-out consuming
`--list` (CONSUMERS.md) this becomes load-bearing.

**Proposed reserved codes:** 2 = configuration/refusal (incl. dirty tree,
unknown lane), 3 = execution-infrastructure failure; document in usage().
Cheap now, breaking later.

**FIXED 2026-08-24** (SPEC R-04 amended): `GateError.exit_code` = 2
(configuration/refusal), `GateInfraError` = 3 (docker absent/failing, git
failures, mountinfo underivation, unreadable wait status). Documented in
usage() with a red-guard test; all ten exit-code pins reclassified.

## RG-12 — failing-container evidence destroyed

**Found by:** consumer-UX review, 2026-08-22.

On failed `docker run` only the LAST stderr line is kept
(`detail[0]`); image-pull/network failures are multi-line and the interesting
line is rarely last. On lane failure the container is `rm -f`'d in `finally`
— post-mortem diagnosis = rerun and stare.

**Proposed:** before removal, copy logs to `/tmp/run-gate/<container>.log`,
print the path on failure; keep ≥ last N stderr lines in the immediate
message. **Oracle:** forced-fail lane leaves a readable log at the printed
path after the container is gone.

**FIXED 2026-08-24** (SPEC `R-26`): `save_container_logs` copies the full
logs to `$RUN_GATE_EVIDENCE_DIR/<container>.log` (default `/tmp/run-gate`)
BEFORE every `rm -f`; a failed lane prints the preserved path, and the
oracle holds — the file exists and reads back after removal. A failed
`docker run` also preserves partial logs and its refusal now shows up to
the last 10 stderr lines (indented block) instead of only the last one.
Capture is best-effort: failure to capture never changes the lane's exit
status, it only downgrades the message to "could NOT be captured".
Exec-mode containers are externally owned — never removed, never captured.
Tests: forced-fail lane (wait 7 → passthrough 7 + readable log),
run-failure with TWO stderr lines both present in the refusal, and the
evidence-dir override honored.

## RG-13 — docs gaps (CONSUMERS.md + adoption hygiene)

**Found by:** consumer-UX review, 2026-08-22. Five items, one entry because
they share the same fix surface:

1. **No end-to-end worked example** stitching run-gate × assay: obtaining the
   pyz + sidecar, minimal R0 `assay.toml`, `kind="assay"` lane with pins,
   consumer pointer, first run, reading `.assay/verdict-*.json`. Both halves
   exist separately (CONSUMERS orchestration layer defers to assay's docs;
   assay/docs/CONSUMERS.md covers judgment) — nobody stitches them. Half a
   page of glue.
2. **Gitignore obligation unstated:** command-kind lanes writing artifacts
   into the tree (`.assay/`, `coverage.json`) depend on those being ignored
   or the NEXT lane's clean-tree check refuses mysteriously. The monorepo
   root ignores both for internal projects; copied-script repos must
   replicate — say so.
3. **Adoption step 4 executed by zero projects:** CONSUMERS.md requires one
   AGENTS.md/README line naming `./run-gate.py` as canonical entrypoint; no
   adopted project has any (verified by ls). Retro-execute during the next
   touch of each project.
4. **No root-level discovery affordance:** repo-root has central
   `run-gate.root.toml` but no pointer down to "cd <project> && ./run-gate.py
   --list"; add one line to the root README.
5. **Budget↔timeout drift unguarded:** every project pairs run-gate `budget`
   with a consumer `timeout_seconds` by manual sync; assay pioneered the
   assert-it test pattern (`test_self_lane.py`). Replicate per project or
   provide an estate sweep.

Related but separate: assay's own CONSUMERS.md teaches the superseded
pre-adoption cmru wiring — filed as assay B011, not here.

**FIXED 2026-08-24 (rev 21) — all five items, closed LAST as the estate
retro:**

1. **Worked example added** to CONSUMERS.md ("Worked example — run-gate ×
   assay, end to end"): pyz + sidecar acquisition → minimal R0 `assay.toml`
   (template keys verbatim) → `kind="assay"` lane with pins → canonical
   consumer pointer → first run → reading/verifying
   `.assay/verdict-<lane>.json`. R1+ adoption noted as an assay.toml-only
   edit.
2. **Gitignore obligation stated** as adoption step 5: copied-script repos
   must replicate the monorepo root's ignores for every path their lanes
   write (union of declared `artifacts` lists = checklist), else the NEXT
   lane's clean-tree check refuses on yesterday's evidence.
3. **Step 4 retro-executed ×9 adopters** (assay, ciu, cmru, nyxloom, topos,
   pwmcp, shared-ramdisk-depot-manager, modern-debian-tools-python-debug,
   plesk-mailbox-create). Deviation with reason: no project carries an
   AGENTS.md, so the canonical-entrypoint line landed in each project's own
   README under "## Testing" (srdm's existing Testing section amended to
   lead with it).
4. **Root-level discovery added**: vbpub root README gained the
   `cd <project> && ./run-gate.py --list` line pointing at CONSUMERS.md.
5. **Budget↔timeout drift guarded by test**, estate-wide:
   `TestEstateBudgetTimeoutPairing` loads each nyxloom-trove project's lanes
   with the REAL parser and asserts consumer `timeout_seconds >= lane
   budget` wherever a gate argv names the lane as a whole token (8 live
   pairings across assay/ciu/nyxloom/topos/srdm; cmru.toml steps carry no
   timeout field — nothing to pair; srdm canary-run.sh names no lane —
   skipped by construction). The sweep caught ONE real drift on its first
   run and it was reconciled: srdm `[gates.privileged-e2e]` timeout 2400s
   truncated the `e2e` lane whose budget is 60m → widened to 3600s with a
   comment naming this rule. Rule documented in CONSUMERS.md ("Consumer
   timeouts must not cut lanes short") + SPEC `R-32`.

With this entry the RG sweep (RG-1…RG-20) is complete.

## RG-14 — release model: wheel as second artifact beside the canonical script

**Enhancement, 2026-08-22 (operator question, answered in review session).**

Keep symlink-in-monorepo (internal) + copy-external as PRIMARY —
zero-install-on-fresh-clone is the design win and must not regress. Add a
wheel as a SECOND artifact: pyproject wrapping the single stdlib module with
a console-script entry point, tags `run-gate-vX.Y.Z`, published through
cmru's wheel-publish like assay/ciu. Serves pip-managed external repos and
CI images wanting it baked. Discipline: a test asserting wheel version ≡
in-file `__revision__` so copies' drift marker stays truthful; CONSUMERS.md
must state the script remains canonical and the wheel never becomes required.
A pyz zipapp was considered and rejected: adds packaging without covering
anything the wheel doesn't.

**Oracles:** fresh clone with zero installs runs `./run-gate.py --list`
(script path, unchanged); `pip install`ed wheel exposes `run-gate` console
script with identical behavior; version-mismatch between wheel and script
fails the discipline test.

**FIXED 2026-08-24 (rev 20).** `pyproject.toml` wraps the module for
setuptools (toolchain pinned exactly, assay/ciu precedent). The hyphenated
filename cannot be a py_module, so a committed symlink
`run_gate.py -> run-gate.py` gives the build an importable name for the SAME
bytes (dereferenced at copy time); the wheel ships only that module plus
dist-info, byte-identical to the canonical script. Console script
`run-gate = run_gate:main`. Version discipline made structural rather than
test-compared: `[tool.setuptools.dynamic] version = {attr =
"run_gate.__revision__"}` DERIVES the wheel version from the script at build
time — dual bookkeeping (and therefore drift) is impossible by construction,
and tests/test_run_gate.py::TestWheelPackaging pins the derivation plus
builds/installs the wheel in-suite asserting identical `--list` output
between copied script and installed console script. Deliberate deviation:
the entry's `run-gate-vX.Y.Z` tag shape becomes `run-gate-v<derived>` (e.g.
`run-gate-v20`) because the script's whole version story is the bare
revision integer until semver meaning exists; the enforced invariant is
tag-body == wheel version. CONSUMERS.md gained "Distribution — script first,
wheel second"; SPEC §7 rewritten + `R-31`. First release: tag
`run-gate-v21` after merge (the RG-13 rev bump rides along), publish via
cmru's wheel-publish.

**AMENDED 2026-08-24 (release-adoption program).** The `__revision__`-attr
version coupling above is SUPERSEDED, not the wheel-as-second-artifact
design: `bump_version("22")` is unparseable by cmru's conventional-commit
version automation, and an integer counter cannot drive semver. The wheel's
version is now DERIVED from the git tag by setuptools-scm
(`[tool.setuptools_scm]`, matching ciu/cmru/assay/topos/nyxloom exactly);
`__revision__` stays the copy-drift marker but is no longer the version
SOURCE. Two tiers, two jobs — see CONSUMERS.md's "Distribution" section.
SPEC `R-31` rewritten; new `R-33` covers the estate release-orchestration
registration this necessitated. Load-bearing consequence: because the
pre-existing tag `run-gate-v22` itself parses as version `22` under the new
tag pattern, the first real semver release must be numbered `>= v23` or
version ordering inverts.

## RG-15 — assay lanes must execute in the selected worktree, not the invoking checkout

**Filed 2026-08-23 (dstdns repair program; reproduced with linked worktrees).**

### The observation

`run_container_lane` and `run_exec_lane` build assay lanes with the project/config
checkout (`project_dir`) instead of the selected `--worktree`. Invoking
`./run-gate.py <assay-lane> --worktree <linked-worktree>` therefore judged the wrong
tree: dstdns saw `verdict`/`commit` from main while intending to test a feature branch,
and locally committed wrapper fixes were silently not exercised.

### Required behavior

For both command and assay lanes, all user-declared execution paths resolve against the
effective judged tree:

```text
effective_tree = --worktree if provided else invocation toplevel
```

Assay lanes must:
- `cd` into `<effective_tree>`;
- verify pinned artifacts relative to it;
- run assay with its `<effective_tree>`-relative config;
- write verdict/coverage artifacts under `<effective_tree>/.assay/`.

### Oracle

Linked worktree A at commit A plus checkout B at commit B:
`./run-gate.py <assay-lane> --worktree A` must record A's HEAD and write artifacts under
A. Exit-status-only tests are insufficient; assert verdict commit + artifact location.

**FIXED 2026-08-24** (`R-21` in SPEC Rev 3): `effective_project_dir` relocates the
project into the judged tree for both runner modes AND host-lane cwd; pin
verification and verdict paths follow. Oracle landed as `TestEffectiveTreeExecution`
(container assay, exec assay, host cwd, identity-without-override,
outside-toplevel refusal).

## RG-16 — central configs should be allowed to define shared lanes

**Filed 2026-08-23 (same session).**

Current validation rejects any lanes table in a central repo-root config (“central defines
environment facts only”). That blocks the intended estate pattern where every package uses
the identical lane pointer without copying definitions.

### Required policy

Allow central configs to define shared environments AND shared lanes, keeping:

- project entries shadow central entries by name deterministically;
- central lane paths must exist in every consumer project or validation fails;
- malformed central/project tables still fail loudly.

If compatibility is desired, gate via explicit config (e.g. schema_version bump or
`[options] allow_central_lanes = true`) rather than guessing intent from shape.

Local fix reference: dstdns controller branch commit `7b17d331`
(`central and lanes and not envs` interim guard), superseded by this requirement.

**FIXED 2026-08-24** (unconditional admission, per interview): central
`[lanes.*]` schema-validated and inherited; `merge_lanes` shadows by name
wholesale; per-consumer pin-sidecar existence enforced at load naming both
files; argv strings deliberately never stat'd (they are shell text — a check
narrower than its message is the KI-12 class). usage()/`--list` show the
effective set with `*` marking inherited entries; SPEC R-22 + §1 amended,
CONSUMERS central-defaults section rewritten with a real shared-lane recipe.

## RG-17 — env forwarding allowlists silently drop schema-oracle credentials (SCHEMA_GATE_PW)

**Filed 2026-08-23 (dstdns repair program; consumer evidence from P121/P126 sessions).**

### The observation

dstdns' central `run-gate.root.toml` declared
`forward_env = ["SCHEMA_GATE_DSN", "SCHEMA_GATE_PG_DUMP"]` but omitted `SCHEMA_GATE_PW`.
The schema lane's `as_role` fixture reads `os.environ["SCHEMA_GATE_PW"]`; when absent, the
privilege oracles could not connect as service roles. The mutation helper's equivalence run
reported green while privilege assertions never executed — the exact hollow-green failure
mode the schema lane exists to prevent. Fix required adding `SCHEMA_GATE_PW` (and later
`SCHEMA_GATE_PG_IMAGE`) to the allowlist; each omission was discovered only by manual diff.

### Why this is a run-gate defect class, not a one-off typo

Allowlist-based forwarding is the right security model, but it has no completeness check:
a credential consumed by tests but forgotten in `forward_env` fails silently or, worse,
fails only some assertions. The tool accepts the config without verifying that declared
lane targets consume what they need.

### Proposed contract (either suffices; both compatible)

1. **Declared-consumption check:** lanes may declare
   `required_env = ["NAME", ...]`. Validation refuses to start if any name is missing from
   the resolved environment after forwarding, with an error naming the lane and variable.
2. **Drift sweep:** a lint mode that scans test source for `os.environ[...]` /
   `getenv` literals and warns when such names are neither forwarded nor declared as not
   required.

### Oracles

- Lane declares `required_env = ["X"]`; invoke with X unset ⇒ refuse before execution.
- Controlled wrong implementation: remove `forward_env` entry for a required var ⇒ oracle 1 fires.

**FIXED 2026-08-24** (both proposals, compatible, per the "5b explicitness"
interview choice): lanes gained a validated `required_env` key; the gate
refuses (exit 2) before ANY execution when a declared name is absent or
empty in the invoking environment (RG-19 preflight), and — container lanes
only — when a required name is NOT on the environment's forward_env
allowlist at all (the completeness check this entry demanded: such a
requirement could never reach the lane). The advisory drift sweep is
`--check-env`: scans the project's Python sources for `os.environ[...]` /
`os.environ.get(...)` / `getenv(...)` literals and flags names covered by
neither allowlist nor required_env; heuristic by nature, so it WARNS and
exits 0 — enforcement lives in required_env + preflight. SPEC R-24;
CONSUMERS env-facts paragraph + schema comment. Oracle covered: unset ⇒
refusal with docker never invoked; empty string counts as absent.

## RG-18 — no pg_dump/PostgreSQL version-mismatch guard for schema lanes

**Filed 2026-08-23 (dstdns repair program; consumer evidence: pg_dump 17.11 client vs TimescaleDB PG18 server produced equivalence artifacts that failed silently inside assay snapshots).**

### The observation

dstdns' SQL mutation lane runs `pg_dump` *inside* the server container to guarantee
client/server version match, but the general schema-gate path allowed a runner-baked
pg_dump of a different major version. Mismatches surfaced only as unexplained equivalence
failures during archive inspection — the tooling never named the version pair.

### Proposed contract

`schema-gate.sh` (and any lane that pairs dump client with server) must:

1. read `SHOW server_version_num` from the target server;
2. resolve the matching `pg_dump` binary path (container-internal preferred);
3. refuse loudly with both versions in the error when majors differ;
4. record the resolved versions in any emitted artifact/log line.

### Oracles

- Server PG18 + client PG17 ⇒ refusal naming both versions.
- Matching pair proceeds and records versions in output.

**Sweep-audit note (2026-08-24):** stays OPEN by scope, not neglect — the
guard belongs in `schema-gate.sh` / the dstdns schema lane, not in
run-gate.py; run-gate has no dump/server pairing to guard. Tracked for the
dstdns adoption of these gates.

## RG-19 — schema-lane credential propagation must be verified by the gate, not by test failure

**Filed 2026-08-23 (same evidence as RG-17).**

Schema lanes that provision roles need role passwords (`SCHEMA_GATE_PW`) forwarded into
the runner. When omitted, privilege assertions cannot connect; depending on assertion
shape this is either a loud fixture error or — worse — silently skipped coverage inside an
otherwise green run.

### Required behavior

1. Gate config validation: any lane whose argv/tests reference provisioning credentials
   must declare them in `forward_env`; a static sweep/lint flags undeclared references
   (see RG-17).
2. Runtime preflight: before executing schema tests, verify required credential env vars
   are non-empty; otherwise fail fast naming the missing variable and its consumer.
3. Verdict/log lines record which forwarding keys were present at start (names only,
   never values).

### Oracle

Controlled wrong implementation: remove `SCHEMA_GATE_PW` from forwarding ⇒ gate refuses
pre-execution naming it, instead of tests failing mid-run or skipping.

**FIXED 2026-08-24** (jointly with RG-17, see there): the runtime preflight
is `preflight_required_env` — presence + non-emptiness verified before any
execution, refusal naming lane and variable. The forwarding record is
`log_forwarded_env`, printed at every container-lane start: which
forward_env keys were present and which declared-but-absent — NAMES ONLY,
never values. Discovered during implementation and fixed in the same
entry: the R-05 docker-argv print would have echoed credential VALUES via
`-e KEY=value`; it now masks forwarded payloads (`KEY=<redacted>`) so the
mechanics stay visible without leaking secrets into logs. Test asserts the
sentinel value appears nowhere in the run output.

## RG-20 — replace global gate flock with resource-aware admission

**Filed 2026-08-23 (dstdns repair program; motivated by multi-stack CIU v6+ and cmru's memory-governance pattern).**

### The observation

The current single-gate-at-a-time flock (`/tmp/<project>-testrunner.lock`) serialises all
gates globally, even when they target fully isolated CIU instances with separate networks
and volumes. With multi-stack, this is unnecessarily restrictive: two worktree instances
with independent PG/Redis can run their suites concurrently without contention.

Conversely, the flock does NOT protect against the real hazard — memory pressure. Two
concurrent gates each consuming 2 GB on a host running live services WILL degrade prod,
regardless of whether they share a database.

### The real constraint hierarchy

| Resource | Contention risk | Correct control |
|----------|----------------|----------------|
| CPU | Low (cgroup weights handle fair sharing) | cpu.weight per lane |
| RAM | HIGH (memory bursts cascade into live services) | mem_limit + memswap_limit per lane; sum concurrent lanes against host budget |
| I/O | Medium (heavy DDL/test IO starves other workloads) | io.weight per lane |
| Shared state (same DB volume, same Redis) | HIGH (data corruption / flaky results) | serialize via instance/service-name lock |

CPU weights are sufficient because Linux cgroup CPU scheduling provides proportional
fair-sharing under contention without throttling when idle. RAM is the actual bottleneck
because swap absorbs bursts but cannot prevent OOM cascades into co-resident live
services when combined usage exceeds physical+swap.

cmru's proven pattern (`CMRU_TESTER_MEMORY = "1g"`, `CMRU_TESTER_MEMORY_SWAP = "16g"`)
demonstrates the right shape: tight RAM prevents pressure cascades; ample swap absorbs
transient bursts without OOM kills.

### Proposed contract

Replace global flock with resource-aware admission:

1. Each lane declares `resources.memory`, `resources.io_weight`, `resources.cpu_weight`
   (defaults from config or rigor preset).
2. Gate admission checks:
   - concurrent lanes' summed `resources.memory` fits within the dev-tier slice budget;
   - no shared-infra collision (two lanes targeting the same rendered service name
     cannot run concurrently).
3. Fully isolated instances (separate networks + separate volumes + separate PG/Redis)
   run in parallel freely.
4. Shared-infra serialization uses an instance/service-scoped lock
   (`/tmp/<project>-<service>-gate.lock`), not a global project lock.

### Oracles

- Two isolated instances with disjoint resources ⇒ both gates run concurrently.
- Two gates sharing the same PG instance ⇒ second waits for first.
- Combined declared memory exceeds host dev-tier budget ⇒ second refuses with message
  naming current consumers and required headroom.

**FIXED 2026-08-24** (SPEC `R-29`; interview choice: full §5.7-shaped
admission MINUS rigor presets, budget DERIVED from the slice's cgroupfs
memory.max). Note: run-gate.py itself never had the global flock — that was
the retired dstdns `testing-exec.sh` shim — so this entry IMPLEMENTS
admission rather than removing a lock. Lane key `[lanes.<name>.resources]`:
`memory` (supersedes top-level `memory`; declaring both refused),
`memory_swap` (docker `--memory-swap`, cmru's tight-RAM/ample-swap pattern),
`cpu_weight`/`io_weight` (validated + printed ADVISORY — docker has no
portable cgroup-v2 flag; pretending otherwise would be enforcement theater;
CIU V7 §5.7 owns cgroup-adjacent enforcement), `shared` (service names).
Memory admission reads kernel truth at admission time:
`slice/memory.current + declared <= slice/memory.max` under
`$RUN_GATE_CGROUPFS_ROOT` (default /sys/fs/cgroup, systemd dash-nesting
resolved) — counts EVERYTHING in the slice (other gates AND live services),
so no cross-process bookkeeping can drift. Over budget → exit 2 refusal
naming usage/budget/need/overage; no derivable ceiling → loud warning,
shared-infra-only admission. Shared-infra: per-name flock at
`/tmp/run-gate-shared-<name>.lock`; second gate WAITS with a notice then
proceeds; isolated names never meet. Locks acquired after all fast-fail
preflights, released in finally; `--dry-run` plans but never blocks. Slice
resolution moved from runner into main() so admission and execution share
one resolution. cmru backfilled: all four container lanes declare
1g/16g (the proven CMRU_TESTER_MEMORY values). Tests: TestResourceAdmission
×17 including all three oracles (over-budget refusal with numbers,
same-service serialization with a real held flock, unbounded-slice
degradation).

## RG-21 — linked-worktree checkouts break host-path-mapped lanes (srdm covergate evidence)

**Filed 2026-08-24 (phase-B verification of this sweep; evidence: srdm
coverage lane run from `.worktrees/run-gate-rg-sweep`).**

### The observation

run-gate's `{worktree}` forwarding places lane execution in the selected
worktree correctly, and exit-status passthrough stayed honest — this is NOT a
run-gate.py defect. But a downstream harness that bind-mounts the repo into a
container by HOST path mounts only its own `$repo_root` subtree. From a linked
worktree that subtree is the worktree itself, whose `.git` FILE points at an
absolute gitdir under the MAIN checkout; when that path is not inside the
mount, every in-container git plumbing call fails:

```
covergate: git rev-list --parents -n 1 HEAD failed: exit status 128:
fatal: not a git repository: /workspaces/vbpub/.git/worktrees/run-gate-rg-sweep
```

Same family: `SRDM_HOST_REPO_ROOT` cannot be auto-derived for a worktree path
(the devcontainer's docker inspect maps only `/workspaces/vbpub`), so it must
be exported by hand. Evidence site:
`shared-ramdisk-depot-manager/tools/gate.sh` (`repo_root` = worktree toplevel;
single `-v "$host_repo_root:$repo_root"` mount). On the main checkout the same
lane passes — `.git` is a directory inside the mount.

### Candidate directions

1. Harness-side (real fix): also mount the common gitdir into the container
   (`-v <main>/.git:<expected path>`) or resolve the worktree gitdir and hand
   `GIT_DIR` to the container explicitly.
2. run-gate-side (narrow): `doctor` could WARN when a host-path-mapped lane
   runs from a linked worktree whose gitdir lies outside `{worktree}`
   (detection is cheap: `.git` is a file, not a directory). Listing verbs and
   non-git lanes stay unaffected either way.
3. Document: until one of the above lands, host-path-mapped lanes are
   main-checkout-only when the tree is a linked worktree.

### Oracles

- From a linked worktree, srdm coverage passes (today it fails with the
  gitdir error above).
- `doctor` names the condition before the lane fails mid-run.

**FIXED 2026-08-31 (rev 26) — directions 2 and 3. Direction 1 is deliberately
NOT taken here.**

Direction 1 (mount the common gitdir / hand over `GIT_DIR`) is the real fix
and it is HARNESS-side: the `docker run` that mounts only `$repo_root`
belongs to `shared-ramdisk-depot-manager/tools/gate.sh`, not to run-gate,
which owns neither that argv nor srdm's repo. Building it here would mean
run-gate reaching into a consumer's own container construction — the exact
inversion the one-parser design (D-110) exists to prevent. What run-gate CAN
own is telling the operator before the lane dies mid-run, and telling every
future harness author how to fix their own mount.

- **Direction 2 (`doctor` warning), `R-30a`:** `linked_worktree_gitdir()`
  returns the absolute gitdir when `<worktree>/.git` is a FILE whose target
  lies OUTSIDE the tree, and `None` for both benign shapes (plain checkout;
  gitfile pointing inside the tree — that one travels with any mount, so
  reporting it would be a false alarm). `doctor` emits ONE `[WARN]` naming
  the worktree, the gitdir, the exact symptom (`not a git repository:
  <gitdir>`) and three remedies. It never moves doctor's exit code:
  run-gate is not defective here, and a warning that overstated itself into
  a refusal would block a lane that works fine on the main checkout.
  **Scoped to projects declaring an `environment = "host"` lane** — the only
  kind that can reach such a harness, since run-gate's own container/exec
  lanes dual-mount the REPO root (`R-23`) and cannot hit this. With a host
  lane and a plain checkout the check records `[OK]`, so a reader can tell
  it ran rather than inferring health from silence.
- **Direction 3 (document):** CONSUMERS "Host lanes that delegate to a
  host-path-mounting harness (RG-21)" — the real srdm error verbatim, the
  doctor line, and THREE pasteable harness-side fixes (mount
  `--git-common-dir`, export `GIT_DIR`, or declare the lane main-checkout-
  only in its `description`), plus the `SRDM_HOST_REPO_ROOT` note that a
  worktree's host path cannot be auto-derived from `docker inspect`.

Tests: `TestLinkedWorktreeHostLaneWarning` ×7 — the gitdir helper in all four
shapes (plain checkout, real `git worktree add`, gitfile pointing inside the
tree, gitfile with no `gitdir:` line) and doctor in all three states (linked
worktree + host lane → WARN naming the symptom and the remedies; plain
checkout + host lane → OK; linked worktree, container lane only → the check
does not appear at all).


## RG-22 — `git config --global safe.directory "*"` fails when global config already has safe.directory entries

**Filed:** 2026-08-24, from dstdns P126/P127 adoption (linked worktrees with
per-worktree instance runners).

### The bug

`build_assay_inner()` and `build_command_inner()` both emit:

```python
shlex.join(["git", "config", "--global", "safe.directory", "*"])
```

Git's `--replace-all` is the correct mode here, but the code omits it. When
`~/.gitconfig` already contains one or more `safe.directory` entries (which is
the NORMAL state after any prior gate run in a multi-worktree estate, or after
any tool that adds a project-specific entry), this command fails:

```
error: cannot overwrite multiple values with a single value
       Use a regexp, --add or --replace-all to change safe.directory.
```

With `set -euo pipefail`, the inner script exits 129 immediately. The lane
reports exit 5 (run-gate's generic failure), and the operator sees only the
cryptic git error — no indication that the fix is trivial.

### When it fires

1. First gate run: works (no pre-existing entry → single-value write succeeds).
2. A second run in a DIFFERENT linked worktree whose runner shares `/root`
   but has a different `.git/config`: also works (still one value).
3. Any scenario where another tool (CIU hooks, IDE integration, or a previous
   run-gate invocation using a DIFFECT gitconfig) has already added a
   project-specific `safe.directory` path alongside `*`: FAILS.

This last case is the dstdns trigger: CIU's per-instance provisioning writes
`safe.directory = /workspaces/dstdns/.worktrees/<name>` into the shared
`/root/.gitconfig`. The next run-gate lane then hits "multiple values" on its
own `*` write.

### Why it matters

The failure is silent in the sense that the error message points at git's
config syntax rather than at run-gate's own missing flag. An operator seeing
"cannot overwrite multiple values" has no reason to suspect a one-line fix in
the harness. Worse, the error is INTERMITTENT from the operator's view: it
appears only after some other tool has populated the config, so the same
command can pass and fail depending on what ran before it.

### Fix

Add `--replace-all` to both call sites:

```diff
-shlex.join(["git", "config", "--global", "safe.directory", "*"]),
+shlex.join(["git", "config", "--global", "--replace-all", "safe.directory", "*"]),
```

Two locations: `build_assay_inner()` (~line 1145) and
`build_command_inner()` (~line 1186). The semantic intent is already "make
this the ONLY safe.directory value for this ephemeral gitconfig" — which is
exactly what `--replace-all` does.

### Alternative considered

Use `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_0` env vars instead of writing a file,
sidestepping the overwrite problem entirely:

```python
parts.append("export GIT_CONFIG_COUNT=1")
parts.append("export GIT_CONFIG_KEY_0=safe.directory")
parts.append("export GIT_CONFIG_VALUE_0=*")
```

This avoids mutating any persistent state, which is arguably cleaner (the
gitconfig at `/tmp/run-gate-gitconfig` exists solely as a side-channel for
this one directive). However, `--replace-all` is the minimal change and keeps
the existing file-based mechanism intact.

### Oracle

```bash
# Pre-populate the global config with an extra entry
git config --global --add safe.directory "/some/project"
# Then invoke any exec-mode lane; before fix: exit 129 with "cannot overwrite"
# After fix: passes cleanly
```

**FIXED 2026-08-24 (rev 23).** `--replace-all` added to both call sites, as
proposed above; the `GIT_CONFIG_COUNT` alternative was not taken (minimal
change, no behavior change to the isolated-gitconfig mechanism). SPEC `R-19a`
now states the write is idempotent under pre-existing entries. Oracle landed
as `test_safe_directory_write_survives_preexisting_entries`, which
pre-populates the real isolated gitconfig with two entries and runs the built
inner command as a live subprocess (fails pre-fix with "cannot overwrite
multiple values", passes after).

## RG-23 — exec-mode's hardcoded env-forward allowlist was dropped with no consumer migration; unmigrated consumers silently stop forwarding `RUN_LIVE_TESTS`/`MOCK_MODE`

**Filed:** 2026-08-25, from the vbpub controller's assay 2.1.0→2.3.0
review-gap audit (`vbpub/assay/nyxloom-trove/reports/assay-review-gap-audit-2026-08-25.md`
§5, finding ba-D — filed against assay's commit `ba8908d6` because that
commit's SQL/infrastructure-forwarding work is what motivated this change,
but the defect itself is entirely inside `run-gate.py`).

### The bug

`run_exec_lane` (`run-gate.py:1394`, at `ba8908d6`) replaced a hardcoded
allowlist with a declaration-driven one:

```diff
-for key in ("MOCK_MODE", "RUN_LIVE_TESTS", CGROUP_ENV_VAR):
+for key in (CGROUP_ENV_VAR, *env.get("forward_env", [])):
```

No consumer `.toml` was migrated to add `RUN_LIVE_TESTS`/`MOCK_MODE` to its
`forward_env` list, and neither `SPEC.md` nor `CONSUMERS.md` records the
removal as a breaking change requiring migration. Still live on `main` today
— unchanged since `ba8908d6`.

### Confirmed consumer impact (dstdns)

`/workspaces/dstdns/run-gate.toml`'s `[environments.test-runner]`
`forward_env` (line 12) lists 13 names; `RUN_LIVE_TESTS` is not among them.
`[lanes.release]`'s own comment (line 189) still reads "Consumer must set
RUN_LIVE_TESTS=1 (forwarded by run-gate exec-mode)" — true before this
commit, false since. Reproduced by replaying the exec-lane env-forwarding
logic against the real dstdns `run-gate.toml`: `RUN_LIVE_TESTS` is not among
the `-e` flags the container actually receives.

**Failure scenario:** an operator exports `RUN_LIVE_TESTS=1` as the lane's
own comment instructs and runs `release`
(`pytest -m 'integration or observability or e2e or infra'`). The variable
never enters the container; dstdns's `tests/conftest.py` (`:588`, `:611-613`)
skips every selected test on its absence; an all-skipped pytest run exits 0.
**The lane reports GREEN having executed no live test** — a silent false
green on the exact class of test the lane exists to run.

Neither of run-gate's own safety nets catches it: the `release` lane
declares no `required_env` (so `preflight_required_env` never fires), and
`--check-env`'s `ENV_REF_RE` needs a string literal inside `getenv(...)`,
while dstdns reads the flag through `os.getenv(name, "")` wrapped in a
helper (`_env_flag_enabled(name)`), which the regex does not match.

### Why this is run-gate's bug, not (only) a consumer config gap

The allowlist→declaration change is a breaking API change for every exec-mode
consumer that relied on the two implicit names, shipped with no migration
pass across consumers and no CONSUMERS.md/SPEC.md note that `forward_env`
must now explicitly list them. A silent false-green on a live-test lane is
exactly the failure class run-gate's own `--check-env`/`required_env`
machinery exists to catch, and it does not catch this one.

### Fix

Two independent halves:
1. **run-gate-project (this repo):** document the breaking change in
   `SPEC.md`/`CONSUMERS.md` explicitly (the two names are no longer
   implicit — every consumer relying on them must add them to its own
   `forward_env`), and consider whether `--check-env` should be extended to
   catch the `os.getenv(name, ...)`-wrapped-in-a-helper shape dstdns uses
   (or document that limitation explicitly so consumers know a bare-regex
   check will not see the flag).
2. **dstdns (separate repo, not fixed here):** add `RUN_LIVE_TESTS` to
   `[environments.test-runner]`'s `forward_env`, and preferably add it to
   `[lanes.release]`'s `required_env` so a missing value refuses loudly
   instead of silently skipping every selected test.

### Oracle

A real exec-mode lane, driven through the installed `run-gate.py`, whose
declared `environment_command`/test suite asserts a forwarded env var is
present — before this fix: absent when relying on the old implicit names;
after: present once `forward_env` is corrected, refused loudly by
`required_env` if omitted.

### Acceptance

- [ ] `SPEC.md`/`CONSUMERS.md` document the breaking change and the
      migration every exec-mode consumer must make;
- [ ] a decision recorded on whether `--check-env` should be extended to
      catch helper-wrapped `getenv` reads, or documented as a known
      limitation;
- [ ] every vbpub-estate exec-mode consumer's `forward_env` audited for
      env vars it relied on implicitly before `ba8908d6`;
- [ ] dstdns's own fix (see above) tracked and confirmed landed — cross-repo
      pointer, not owned here.

**FIXED 2026-08-31 (rev 25) — the run-gate half. The dstdns half stays OPEN
in its own repo (cross-repo pointer, deliberately not owned here).**

1. *Breaking change documented, with the migration.* SPEC `R-24a` (forwarding
   is DECLARED, never implicit — `CGROUP_PARENT_DEV_GATES` is the sole
   exception, being infrastructure the tool itself owns), CONSUMERS "BREAKING
   CHANGE — migrate if you use `mode = "exec"`" (a pasteable two-half
   migration: `forward_env` restores the old behaviour, `required_env` is
   what converts the silent-skip into a loud refusal), README env-forwarding
   bullet. The implicit names deliberately do NOT return: reinstating them
   would re-create the shadowing default the declarative key removed. The
   entry's own preferred shape — document + consider extending `--check-env`
   — is what shipped; the allowlist was NOT restored.

2. *Decision on `--check-env` (the entry's open question): EXTENDED, not
   documented away.* A sweep whose comparison is narrower than its message
   issues a false certification, which is worse than no check (AGENTS "a
   check is only as strong as what it actually compares") — and this one had
   already certified a clean bill of health over the exact variable whose
   absence made the lane green. `scan_env_references()` replaces the line
   regex with an AST pass that sees `os.environ[...]`,
   `.get/.setdefault/.pop`, `getenv`, `"X" in os.environ`, and a literal
   handed to the project's own env-reader helper (a function reading the
   environment through one of its parameters — dstdns's
   `_env_flag_enabled("RUN_LIVE_TESTS")` shape), with bound-method parameter
   offsets accounted for so a method never reports a name taken from the
   wrong argument position. It remains ADVISORY (exit 0) and `R-24b`
   documents what it still cannot see (runtime-assembled names, non-Python
   sources) so a clean sweep reads as evidence, not a certificate. An
   unparseable file is named and falls back to the old regex — "could not
   read it" is never rendered as "there is nothing there".

3. *Estate audit performed.* At rev 25 NO vbpub project declares
   `mode = "exec"`, none declares `forward_env`, and none references
   `MOCK_MODE`/`RUN_LIVE_TESTS` in any `run-gate.toml` — the estate-side
   blast radius is empty and the confirmed impact is dstdns alone. The audit
   is kept as a TEST (`TestEstateExecForwardEnvAudit`) rather than a note, so
   a future estate exec-mode adopter that reacquires the assumption fails
   here instead of shipping a false green.

Tests: `TestEnvReferenceScan` ×9 (every read shape, the helper oracle, the
async/bound-method offset, the positional-only position, lookalike dict reads
NOT reported, too-few-arguments call sites, `SyntaxError` propagation, and
both end-to-end `--check-env` paths) + `TestEstateExecForwardEnvAudit`.

## RG-24 — `resolve_container_name()` reads `ciu.global.toml` from the shared-`.git`-owning repo, never from the judged worktree, so a multi-instance (Mode-B) worktree's exec-mode lane silently targets the WRONG deployed container

**Filed:** 2026-08-30, dstdns-P147b (`dstdns@1171d8d3`,
`nyxloom-trove/decisions.md` D-247; worktree
`/workspaces/dstdns/.worktrees/p147b-vertical-corpus-e2e`).

### The bug

`resolve_container_name()` (`run_gate.py:1319-1361`), for an exec-mode
environment with no declared `container_name`, derives the container name
from `repo / "ciu.global.toml"`'s `[deploy] project_name`+`environment_tag`
(or, failing that, `network_name` stripped of `-network`). `repo` here is
NOT the judged worktree — per `resolve_repo_and_worktree()`'s own
docstring, `repo` is deliberately "the checkout owning the shared `.git`"
(worktrees live under it), i.e. the MAIN checkout, for ANY git worktree,
regardless of the `--worktree` CLI argument. This split (`repo` for
git-object-store concerns, `worktree` for judged-tree concerns) is the
right design for locating SOURCE CODE — a linked worktree shares one
object store with its main checkout — but it is the WRONG source for
locating a LIVE DEPLOYED CONTAINER's name under any consumer that runs a
genuinely separate, per-worktree deployment (dstdns's own "Mode-B"
pattern, `nyxloom-trove/GUIDE.md` §3: `ciu worktree adopt` gives a
worktree its OWN isolated stack, its OWN rendered `ciu.global.toml` with a
worktree-specific `project_name`/`environment_tag`, and its OWN persistent
`test-runner` container on its OWN docker network).

### Reproduced

A dstdns Mode-B worktree at
`/workspaces/dstdns/.worktrees/p147b-vertical-corpus-e2e` has its own
correctly-rendered `ciu.global.toml`
(`project_name = "p147b-vertical-corpus-e2e"`, `environment_tag =
"8a6bc3"`) and its own deployed, healthy
`p147b-vertical-corpus-e2e-8a6bc3-test-runner` container on its own
network. Running `run-gate <live-lane> --worktree
/workspaces/dstdns/.worktrees/p147b-vertical-corpus-e2e` from within that
worktree (so the worktree's OWN `run-gate.toml` is correctly loaded for
lane/environment table lookup — that half works) nonetheless executed the
lane's pytest INSIDE `dstdns-98535c-test-runner` — the MAIN landscape's
own, separately-deployed, pre-existing `test-runner` container —
confirmed by the failing test's own `controller_url` fixture resolving to
`http://dstdns-98535c-controller:8080` (the MAIN landscape's controller
alias) rather than `http://p147b-vertical-corpus-e2e-8a6bc3-controller:8080`
(this worktree's own). `resolve_container_name()` had derived
`dstdns-98535c-test-runner` from the MAIN checkout's `ciu.global.toml`
(`project_name = "dstdns"`, `environment_tag = "98535c"`), exactly as its
current logic dictates.

**Why the failure mode is partial, not total, and easy to miss:** both
`test-runner` containers bind-mount the SAME host repo root (`.worktrees/`
is a subdirectory of it in both), so the lane's own `cd {worktree} &&
pytest ...` argv still `cd`s to and collects the CORRECT test files even
when exec'd into the wrong container — only that container's OWN baked
runtime environment (network attachment, env vars such as
`CONTROLLER_URL`) is wrong. A lane with no live-network dependency
(dstdns's own MOCK_MODE fast lane; a schema lane provisioning its own
throwaway Postgres) produces IDENTICAL, believable results regardless of
which container ran it — only a lane depending on THIS worktree's own
live, instance-scoped network resources exposes the defect. Worse, a
fixture that shells out to `docker exec <container-name-from-config> ...`
and returns only `.stdout` (discarding `.stderr`/`.returncode`) can fail
completely SILENTLY under this misrouting — an empty string, not a loud
crash — if the wrongly-resolved config also causes it to target a
nonexistent resource in the wrong deployment (observed independently as a
dstdns-side defect, `nyxloom-trove/decisions.md` D-246, compounding this
one's symptoms in the same live run).

### Why no existing mechanism helps

An explicit `container_name` on `[environments.test-runner]` is the only
current escape hatch, and it does not fit: it would have to be hardcoded
to ONE instance's own generated name (`p147b-vertical-corpus-e2e-8a6bc3-
test-runner`) in the TRACKED `run-gate.toml`, which is shared by every
OTHER lane using that environment AND by every future worktree/instance —
correct for exactly one running instance, wrong for the very next one
created (the same class of hazard dstdns's own AGENTS.md §4.2a names for
a "shadowing default": a literal standing in for a value that has an
authoritative source elsewhere).

### Fix

`resolve_container_name()` should read `ciu.global.toml` relative to the
JUDGED WORKTREE (the function already receives enough context — or could
receive the `worktree` parameter already threaded through
`run_exec_lane`'s own call site — to do this) for THIS ONE purpose:
deriving a live deployed container's name. `repo`-relative resolution
remains correct for everything else `resolve_repo_and_worktree()` serves
(e.g. pin-sidecar existence checks, which are legitimately about the
shared object store's own tree, not a live deployment). A worktree with no
own `ciu.global.toml` (i.e. not itself a `ciu worktree adopt`-managed
Mode-B instance) should fall back to the current `repo`-relative
resolution unchanged — this is an ADDITIVE precedence fix, not a
replacement.

### Workaround used (disclosed, not a fix)

This package's own live-lane evidence was gathered via a direct `docker
exec -w <worktree> <correct-instance-test-runner-container> bash -c
'<the lane's own argv, verbatim>'` — the identical command `run-gate`
would run, against the container `run-gate` should have chosen — rather
than trusting `run-gate`'s own container resolution for this one
worktree-scoped live lane.

### Acceptance

- [ ] `resolve_container_name()` (or its caller) accepts/derives the
      judged worktree and prefers `<worktree>/ciu.global.toml` over
      `<repo>/ciu.global.toml` when the former exists and differs;
- [ ] a regression test constructs two `ciu.global.toml`s (one at a fake
      "repo", one at a fake "worktree" beneath it) with different
      `project_name`/`environment_tag` and asserts the worktree's own
      config wins;
- [ ] `SPEC.md`/`CONSUMERS.md` document the worktree-vs-repo distinction
      for this one resolution path explicitly, since it is easy to
      conflate with the (correct, unchanged) repo-relative resolution used
      elsewhere.

**FIXED 2026-08-31 (rev 24).** `resolve_container_name()` takes the judged
`worktree` (already threaded through `run_exec_lane`) and resolves
`<worktree>/ciu.global.toml` → `<repo>/ciu.global.toml` in that order;
a declared `container_name` remains the top of the precedence chain
(unchanged). Additive, exactly as the entry asks: a worktree without its own
config keeps repo-relative resolution byte-for-byte. Two visibility changes
came with it, because the defect's real cost was that "which config decided
this" was never printed: the resolution source now carries a SCOPE label
(`judged worktree:` / `repo:` + the path), and it is now part of the
pre-execution `container …` disclosure line (R-05), not only of the
not-running refusal. A missing-config refusal names BOTH candidate paths when
they differ — naming one would send an operator to `ciu render` the wrong
tree. The disclosed workaround (`docker exec` by hand into the correct
instance) is NOT reproduced anywhere in the tool; it was evidence-gathering,
not a design. Tests: `TestWorktreeScopedContainerName` ×5 — the regression
oracle runs with BOTH containers present and running in `docker ps`, since a
test where only the right one exists would pass against the buggy code by
`not running` refusal rather than by correct resolution. SPEC `R-14a`;
CONSUMERS "Python app estate with its own runner" (worked disclosure line +
an explicit "do not pin `container_name` as a workaround" warning).

## RG-25 — `doctor`/`--check-env` cannot see that an assay lane's LANGUAGE needs a toolchain in its environment; consume `assay lanes --json` (assay B044) for a per-lane fitness check

**Filed:** 2026-08-30, from the assay 3.1.0 design review
(`assay/nyxloom-trove/reports/assay-3.1-js-adapter-design-review-2026-08-30.md`
§4 D3) — the current-gate backport of ciu **CIU-72 (b)**; backportable
because the gate on the current schema IS run-gate and the change is
additive (the v8 proposal's own §4.11 "ship now" class). **SPEC
ownership:** §2 `R-01` (`--check-env`, `doctor`), `R-05` (disclosure); §5
execution contract (preflight). Code: `cmd_doctor` (`run-gate.py:964`),
`cmd_check_env` (`run-gate.py:1598`), `build_assay_inner`
(`run-gate.py:1141-1180`, the existing in-environment `--version` probe).

### The gap

run-gate reads nothing from `assay.toml` — the `assay_lane` name is a
string it passes through (`build_assay_inner`). `doctor` checks docker,
per-environment resolution and slice state (`run-gate.py:975-1020`);
`--check-env` checks the env-forward contract (RG-17/19/23). Neither can
know that `[lanes.ui-unit] kind = "assay"` → `assay.toml
[lanes.ui_unit] judge.language = "javascript"` needs `node`/`npm` on PATH
inside `environment = "test-runner"`, or that a future Go lane needs
assay's statement-position helper (assay A-239). Today the first sign is
the lane itself: `NO_MEASUREMENT`/`MISSING_EXTERNAL_TOOL` at best; with
`npx` and no `--no-install`, an unpinned registry fetch inside the gate
container (assay B041). The fact is knowable statically; assay B044
(`assay lanes --json`) makes it askable without run-gate ever parsing
`assay.toml` — it asks the judge, the same way it already asks
`--version`.

### Fix (additive; `schema_version = 1` unchanged)

- In `cmd_doctor` and `cmd_check_env`, for every `kind = "assay"` lane whose
  environment resolves: run `<assay_command> lanes --json --file assay.toml`
  INSIDE the lane's environment through the SAME exec/ephemeral probe path
  the pin `--version` check uses (one docker construction site, never a
  second), find the entry named `assay_lane`, and `command -v` each of its
  `external_tools` and its `argv0` inside the environment. Report in
  doctor's existing `record()` format: `[OK] lane 'ui-unit' toolchain:
  node, npm` / `[FAIL] lane 'ui-unit' needs 'node' in environment
  'test-runner'`; `--check-env` exits 2 on a FAIL (its existing severity
  for a broken contract).
- The named `assay_lane` missing from the inventory → `[FAIL] assay lane
  'ui_unit' not declared in assay.toml` — a run-time refusal becomes a
  doctor finding (validate-pointers' spirit).
- Judge unreachable, or an assay without `--json` (older than B044) →
  `[SKIP]` naming why; NEVER a FAIL for an older judge — the pin declares
  the version, run-gate must not require a floor it never declared.
  `inventory_schema != 1` → `[SKIP]` with the value.

### Acceptance

- [ ] `doctor` and `--check-env` emit the per-lane lines; tests with a fake
      `assay_command` script: inventory with `external_tools = ["node"]`
      against a fake environment lacking `node` → FAIL naming
      lane/tool/environment; with `node` → OK; script without `--json` →
      SKIP; lane absent from the inventory → FAIL;
- [ ] grep proves ONE in-environment probe builder shared with the pin
      probe (no second `docker exec`/`docker run` argv construction);
- [ ] SPEC §2 `R-01` (`--check-env`/`doctor`) updated; CONSUMERS
      `kind = "assay"` section names the check beside the closure note
      added 2026-08-30.

**FIXED 2026-08-31 (rev 27), SPEC `R-34` (+ `R-01`, `R-30` amendments).**
`assay_toolchain_findings()` emits one `(status, topic, detail)` per assay
lane; `cmd_doctor` feeds them to its existing `record()` and `cmd_check_env`
prints them and exits 2 on a FAIL. `build_env_probe_argv()` is the single
in-environment probe builder (reusing `resolve_container_name()` for exec and
`physical_path()`/`dual_mount_flags()` for ephemeral); a test asserts by
source inspection that the only functions constructing a `docker run`/`docker
exec` argv are `run_container_lane`, `run_exec_lane` and
`build_env_probe_argv`. Ephemeral probes carry `--cgroup-parent` like any
container this tool starts, and SKIP rather than run unconfined where no
slice is derivable.

**Deviation from the entry's letter, and why (flagged for controller
review).** The entry says to `command -v` "each of its `external_tools` and
its `argv0`". Implemented as `external_tools` ∪ `argv0` ∪ a `language`
toolchain table, because assay 3.2.0's own `docs/CONSUMERS.md` states that
`external_tools` is `()` for EVERY shipped adapter today and that "a gate
consumer should not build a `MISSING_EXTERNAL_TOOL` preflight around this
field expecting it to name node/npm for a javascript lane — that check today
has to come from `language` itself". Following the letter alone would have
shipped a check that reports a clean bill of health for a JavaScript lane in
an environment with no Node — precisely the gap this entry was filed for, and
precisely the false-certification class AGENTS forbids. The entry's OWN
example output (`[OK] lane 'ui-unit' toolchain: node, npm`) is only reachable
via `language`, so the two readings agree on the outcome. The table is kept
minimal (`javascript`, `go` — the two assay documents), and an unmapped
language attaches an explicit caveat to the line rather than being silently
read as "nothing needed".

Deliberately NOT implemented (not in the entry's contract; noted for a future
entry): `rigor` vs `rigor_reachable` — the inventory also exposes rigor levels
a lane declares that THIS assay build cannot reach for its language, which is
a second preflightable mid-run refusal. It belongs in its own entry rather
than smuggled in here.

Tests: `TestAssayToolchainFitness` ×18, driven through a docker shim that
actually EXECUTES the probe script on the host (a shim echoing canned output
would pin construction, not acceptance — the substitute-interpreter failure
this whole project exists to kill). Covered: missing `external_tools` → FAIL
naming lane/tool/environment; present → OK listing them; `language =
"javascript"` against a constructed Node-less PATH → FAIL naming both, then
node-only → FAIL naming only npm, then both → OK (the devcontainer has real
node/npm in `/usr/bin` beside `bash`, so the absent case had to be built);
lane absent from the inventory → FAIL naming what IS declared; judge without
`--json` → SKIP; non-JSON → SKIP; `inventory_schema = 2` → SKIP with the
value; `command -v` transport failure → SKIP, never a clean bill; `host`
environment → SKIP; docker absent → SKIP; no slice derivable → SKIP; exec
environment probes via `docker exec` and starts no container; `--check-env`
exit 2/0; a command-lane project emits no toolchain line at all; and the
one-probe-builder source assertion.

## RG-26 — no `--base REF` passthrough to `assay run --request-base`: B019 (assay ≥ 3.0.0) is unusable from the gate; derive the delegating lanes from `assay lanes --json`, not a new lane key

**Filed:** 2026-08-30, from the assay 3.1.0 design review (§4 D3) — the
current-gate backport of ciu **CIU-72 (c)**; absorbs the v8 proposal's
§4.11 **N12**, which was never filed here (N12 cited "RG-24", but RG-24 is
the exec-mode container-resolution bug; the proposal row now points here).
**SPEC ownership:** §2 CLI (beside `R-02`), `R-05`; §5 assay invocation
(`build_assay_inner`, `run-gate.py:1178-1179`); RG-1's conjunction
propagation rule.

### The gap

`build_assay_inner` always emits `assay run <lane> --file assay.toml
--verdict-json …`. assay 3.0.0 shipped `judge.base_source = "request"` +
`--request-base REF` (B019/A-328): a changed-line lane that leaves
`judge.base` out and takes the comparison base from the gate — the shape
every PR-scoped lane wants (the v8 demo's `p129_enumeration_cursor` shows
it). Such a lane invoked WITHOUT `--request-base` refuses
`ERROR`/`BAD_LANE_CONFIG` by design (assay never falls back to `HEAD`).
run-gate has no `--base` flag, so no consumer can adopt B019 until v8's
`ciu gate` — months of a shipped judge feature sitting unusable.

### Fix (additive)

- `run-gate <lane> [--base REF]`. For a `kind = "assay"` lane: query
  `<assay_command> lanes --json --file assay.toml` in the environment
  (RG-25's shared probe); if the named lane reports `base_source ==
  "request"`, append `--request-base <REF>` to the assay argv, where `REF`
  is `--base` if given, else the judged worktree's `git merge-base HEAD
  @{upstream}`; no upstream → exit 2 `run-gate: lane 'x' delegates its
  comparison base; pass --base REF (worktree has no upstream)`. A lane that
  does NOT delegate, invoked with `--base` → exit 2 naming the lane (assay
  would refuse anyway; refuse earlier and clearer). Conjunction lanes
  propagate `--base` to every sub-invocation (RG-1's rule: an override
  given to the gate reaches every sub-lane).
- **No new `run-gate.toml` key.** The fact lives in `assay.toml` and is
  DERIVED, so the current gate never restates it (v8's S16.5 `request_base`
  restatement is CIU-72 (c)'s concern there; v7 gets the one-spelling
  property for free).
- Judge without `--json` and no `--base` → behaviour unchanged; judge
  without `--json` and `--base` given → exit 2 naming the assay version that
  first carries the inventory (B044).
- `--dry-run` shows the resolved `REF` and the appended flag; `R-05`'s
  pre-execution disclosure prints it.

### Acceptance

- [ ] `--base` accepted on lane and conjunction invocations and propagated;
- [ ] tests: delegating lane + `--base` → assay argv carries
      `--request-base REF`; delegating lane without `--base`, with and
      without an upstream; non-delegating lane + `--base` → exit 2; judge
      without `--json` in both shapes;
- [ ] `--dry-run` and the disclosure show it; SPEC §2/§5 and the CONSUMERS
      worked example updated (a `base_source = "request"` lane beside the
      existing one);
- [ ] N12's row in `ciu/docs/CIU-V8-TESTING-GATE-PROPOSAL.md` §4.11 points
      here (done 2026-08-30).

**FIXED 2026-08-31 (rev 28), SPEC `R-35`.** `--base REF` is accepted on every
lane invocation; `plan_comparison_base()` decides what (if anything) the lane
gets, and every refusal is exit 2 naming the lane.

- **Delegation is DERIVED, not declared.** `assay_inventory_entry()` reuses
  RG-25's probe; a lane reporting `base_source == "request"` gets
  `--request-base <REF>` appended to its assay argv. No `run-gate.toml` key
  was added, so v7 gets the one-spelling property the v8 proposal's S16.5
  restatement has to work for.
- **Cost, disclosed rather than hidden:** because delegation must be known
  even when `--base` is absent (a delegating lane invoked bare needs the
  merge-base default), the inventory probe runs for EVERY `kind = "assay"`
  lane invocation. It is short, read-only (`assay lanes` executes nothing)
  and uses `R-34`'s single builder. SPEC `R-35` and CONSUMERS both state it.
- **Default ref** is the judged worktree's `git merge-base HEAD @{upstream}`,
  via `derive_upstream_base()` — deliberately not `git_out()`, because a
  missing upstream is an ordinary state to report, not infrastructure to
  abort on. No upstream → the entry's exact refusal. There is no fallback to
  `HEAD` or a default branch name.
- **Conjunction propagation** uses `R-25`'s mechanism rather than inventing a
  second one: a `{base}` token in the conjunction lane's own argv,
  substituted into every sub-invocation, resolved by the same policy (so a
  `{base}` lane on an upstream-less tree refuses instead of substituting an
  empty string). A command lane WITHOUT the token, given `--base`, refuses —
  the two rules would otherwise contradict each other for a conjunction,
  which is a command lane that does not itself delegate.
- **Older judge:** with `--base`, exit 2 naming assay `3.2.0` (B044) as the
  version carrying the inventory; without `--base`, behaviour is byte-for-byte
  unchanged.

Tests: `TestComparisonBasePassthrough` ×14 — delegating + `--base`;
delegating with a real upstream (asserting the actual merge-base SHA reaches
the argv); delegating without an upstream → the exact refusal; non-delegating
+ `--base` → exit 2 naming the declared `base_source`, with NO judged run
started; non-delegating without `--base` → unchanged; old judge both ways;
lane absent from the inventory + `--base`; conjunction propagation (asserted
at fd level, since the sub-shell's stdout is not a Python write);
conjunction without upstream; command lane without the token; a `host`-
environment assay lane probing locally with no docker at all; `--dry-run`
disclosing the ref while starting no judged container; and the substitution
leaving `{base}` alone when no base was resolved.

The test-suite helpers `lane_runs()`/`lane_execs()` were added because an
assay lane now issues a probe before the judged run — `docker_runs(log)[0]`
is no longer necessarily the lane, and four existing tests were silently
asserting against the probe instead (one of them, `test_exec_assay_lane_
judges_selected_worktree`, still PASSED against the probe because both
scripts `cd` to the same tree; it now asserts `--verdict-json` to pin the
judged exec specifically).


---

## RG-27 — persisted per-lane-per-commit invocation history (bounded window) + a query verb, machine- and human-readable

**Filed by:** operator directive, 2026-08-31, retriaging ciu CIU-55 (originally
filed `dstdns/nyxloom-trove/decisions.md` D-204, 2026-08-25). CIU-55 argued CIU
should own this because it holds the per-instance identity and an existing
persisted-state file (`ciu.global.toml`); the operator's re-read: in the
CURRENT (pre-v8, pre-gate-absorption) architecture run-gate is the layer that
actually invokes each lane and already has direct, unmediated visibility into
start/stop timestamps and exit status — CIU would have to wrap or intercept
run-gate's own invocation loop to get the same data first-hand. v8's §4.3.2
absorption of run-gate into `ciu gate` eventually collapses this distinction,
but that is not-yet-implemented; this entry fixes the gate consumers actually
run today. CIU-55 is retained as a pointer to this entry, not deleted (its
"why CIU owns it" reasoning is a real recorded design discussion, now
superseded).

### The gap

Lane duration and outcome are informal: an operator or controller notices a
lane "took a while" from wall-clock observation while waiting on it, and the
observation is lost once the terminal scrolls past it. Nothing durable
records, per lane: how long THIS run took, on THIS commit; how that compares
to recent runs of the same lane; which lanes are cheap-to-always-run vs.
expensive-enough-to-consider deferring. Without this, a provisional-merge /
defer-heavy-rigor policy is a guess dressed as a decision (dstdns explicitly
declined to adopt such a policy blind on 2026-08-25, D-204, for exactly this
reason).

### Proposed contract

- **History**: keyed by (lane, commit), bounded to the last **N commits per
  lane** (default 10, operator-configurable). A rolling window — the oldest
  entry is evicted once the bound is exceeded, never unbounded growth. Each
  history entry: duration, outcome (pass/fail), start timestamp, worktree/
  instance identity.
- **Latest**: a single always-current slot per lane holding the most recent
  invocation's result **regardless of outcome** — pass, fail, error, or
  aborted/interrupted — and regardless of whether that invocation ran
  against a clean, committed HEAD (a dirty-tree or mid-rebase run still
  updates "latest"). Always available to the caller via the query verb.
- **Latest does not feed history**: an unsuccessful or aborted run updates
  "latest" for immediate diagnostics but is NOT appended to the bounded
  per-commit history log — history is a curated trend series for "what does
  this lane typically cost", and an aborted run has no stable duration/
  commit pairing worth keeping in that series. (Flagged as a design call for
  the implementer to confirm: whether a *completed* fail — a real commit ran
  the full lane and it failed — still belongs in history alongside passes,
  since its duration is real data even though its outcome is not a pass.
  Current reading: yes, completed fails join history; only aborted/
  interrupted/dirty-tree runs are excluded.)
- **Query verb**: run-gate gains a subcommand to display/query this data in
  both human-readable (default, a table) and machine-readable (`--json`)
  form — at minimum: latest result for a lane, and its bounded history.
  Exact verb name/flags are an implementer design call, following this
  project's existing `doctor`/`--dry-run`/`--list` subcommand conventions.
- **Storage**: a run-gate-owned, gitignored, per-instance file (location is
  a design call — sibling to how ciu treats `ciu.global.toml` as its
  per-instance persisted-state surface, but run-gate has no existing
  equivalent to extend, so this is greenfield). Concurrent-write safety
  matters: two worktrees' gates can run simultaneously against lanes that
  key into the same file if the storage location is not itself per-instance
  scoped — needs an explicit design answer, not an assumption.
- Explicitly OUT of scope: run-gate does not decide any rigor/defer POLICY
  itself — it measures and persists; a controller (dstdns or otherwise)
  reads the data and decides.

### Oracles

Not yet written — needs a design pass first (storage location, concurrent-
instance write safety, exact verb/flag names, and the completed-fail-vs-
history question flagged above).

- Controlled wrong implementation to watch for: recording only the latest
  invocation with no rolling stat (the same trap CIU-55 flagged) — a single
  slow outlier run would look like the lane's permanent cost, defeating the
  point of informed decision-making.
- Controlled wrong implementation to watch for: an aborted/dirty-tree run
  silently corrupting the bounded history's commit-keyed entries (e.g.
  overwriting a real commit's history slot with a dirty-tree duration).

**SPEC ownership:** new surface — no existing `SPEC.md` section owns
invocation-timing telemetry. Cross-reference: ciu CIU-55 (superseded pointer,
`ciu/KNOWN_ISSUES_TODO_BACKLOG.md`).

### FIXED 2026-08-31 (rev 30, package `run-gate-P03`)

Landed as `SPEC.md` **`R-36` (a-i)** with `R-01`/`R-06`/`R-08` amendments.
79 new tests; `./run-gate.py selftest` green (359 passed, 2 skipped,
diff-coverage **229/229 = 100%**, exit 0). Full record:
`nyxloom-trove/reports/run-gate-P03-{LOG,REPORT}.md`. Several things here
were left to implementer judgment — what was actually decided, and why:

**Verb: `history [LANE] [--json]`** (`R-36i`). A positional verb, not a flag,
because it reads the world and reports — the `doctor` shape; flags in this
project are discovery surfaces (`--list`, `--check-env`, `--dry-run`). No
LANE reports every declared lane; an unknown LANE refuses (exit 2) naming the
known lanes and the config path, exactly like the run path. `--json` mirrors
the existing machine/human split. `history` joins `doctor` and
`validate-pointers` as a RESERVED lane name (`R-08`). Rejected: `stats` (the
most-used answer is a single record, not a statistic) and `timings` (drops
the outcome half of the record).

**Storage: `<effective project dir>/.run-gate/history.json`, JSON**
(`R-36f`). "Per-instance" resolves to **per (judged worktree × project)**,
and it is DERIVED rather than invented: `R-21` already relocates the
effective project dir into the judged tree, so `--worktree B` writes B's
measurement into B's store, and lane-name collisions across the estate
(`selftest` exists in several projects) cannot merge. Anchoring at `repo`
instead — the checkout owning the shared `.git`, i.e. MAIN for every linked
worktree — was rejected as both the contention hazard AND the same false
attribution `R-21` exists to prevent. Format is JSON because run-gate is
stdlib-only and the stdlib has no TOML *writer* (`tomllib` is read-only);
hand-rolling TOML emission for a file rewritten after every run is a bug
farm. `/tmp/run-gate/` (the evidence-dir neighbourhood) was rejected:
evidence is a post-mortem for one failure, history is a series that must
accumulate over days, and `/tmp` is tmpfs on many hosts — the series would
silently reset. No env-var relocation override was added.

**Concurrency: scope first, arbitration second** (`R-36f`). Cross-worktree
contention is eliminated by construction — two worktrees address two files
and never meet; a layout with no collision beats a lock that arbitrates one.
The residual case is real (two lanes of ONE project in ONE tree) and is
serialized by an exclusive `flock` on a **sibling** `.run-gate/history.lock`
(`O_NOFOLLOW`, 0600) held across the whole read-modify-write, plus
write-temp-then-`os.replace`. Sibling because the store is REPLACED by
rename: a lock on the store guards an inode nobody writes next. Atomic
rename is also what lets the query verb read with **no lock at all**. The
wait is BOUNDED (5s), unlike `R-29`'s shared-infra lock which blocks forever
on purpose — that one protects the correctness of the run, this one protects
a measurement, and a gate that hangs to write telemetry has inverted the
priority.

**Gitignore obligation made executable** (`R-36g`). Writing into the judged
tree would otherwise leave it dirty for the NEXT lane's clean-tree check, so
run-gate asks git before every write and REFUSES to write an un-ignored
store, naming the remedy. Two details were verified against real git rather
than assumed, and both would have shipped as silent defects: the query names
the FILES, never the bare directory (`git check-ignore .run-gate` answers
"not ignored" while the directory does not exist yet, even under a
`.run-gate/` pattern — recording would have been dead on every project's
first run and alive on the second), and the verdict is read from the REPORTED
PATHS, never the exit status (`git check-ignore a b` exits 0 when ANY
argument matches — the false-certification shape AGENTS.md names, which would
certify a store whose LOCK file still dirties the tree). Root `.gitignore`
gained `.run-gate/`; CONSUMERS adoption step 5 names it for copied-script
repos.

**Open question 1 — does a COMPLETED fail belong in history? YES, with a
qualification** (`R-36c`). Agreeing with this entry's own reading, but the
plain "yes" is not safe: a failing lane can SHORT-CIRCUIT (this project's own
`pytest && coverage_gate` never reaches the gate when pytest is red), so
fails and passes are not samples of the same quantity and averaging them
understates the lane's cost in exactly the direction that makes a "cheap,
always run it" call wrong. Resolution: fails join history CARRYING their
outcome, and the reported statistic is SPLIT (`stats.passes` vs
`stats.completed`), both published in both output forms. run-gate hands over
both series and picks neither — picking is policy, which is out of scope.
This paid off immediately: the store's first two real entries are this
package's own gate runs, and the failing one took 56.4s against the passing
one's 47.7s (the coverage gate ran to a verdict rather than short-circuiting)
— a pass-only series loses that point, a merged series reports 52.0s as "what
selftest costs" when the answer for a passing run is 47.7s.

**Open question 2 — what else is excluded, and how "unknown" is handled**
(`R-36b`). Eligibility is a conjunction: completed with its own exit status,
clean tree, no git operation in flight (`rebase-merge`, `rebase-apply`,
`MERGE_HEAD`, `CHERRY_PICK_HEAD`, `REVERT_HEAD`, `BISECT_LOG`), resolvable
commit. Each failure records its reason on the entry, visible in both output
forms. "Could not determine" EXCLUDES — a wrong trend entry is invisible, a
missing one shows up in `count`. Dirtiness is sampled independently of the
lane's `clean_tree` POLICY (the test is whether the tree WAS dirty, never
whether dirt was permitted — otherwise every `clean_tree = false` lane's
series silently halves) and BEFORE the lane runs (a lane that leaves
artifacts must not retro-disqualify its own valid measurement).

**Two further calls the entry did not ask about but which the contract
needs.** Keyed by (lane, commit) means a re-run of the same commit REPLACES
its entry and moves to the tail (eviction = least recently measured);
appending would let ten re-runs of one commit evict nine other commits from a
ten-deep window. And the headline statistic is the **MEDIAN**, never the
mean, with min/max/count — the trap named below is one slow outlier reading
as the lane's permanent cost, and the mean is precisely the statistic that
permits it; `max` still publishes the outlier.

**Retention bound**: `[history] keep` (integer ≥ 1, default 10), a new
top-level config table; a project's shadows the central one entirely per
`R-09`. Declared config, not an env var: how much trend to keep is an
auditable decision, unlike the per-instance data it bounds.

**Recording discipline** (`R-36e`/`R-36h`). An invocation begins at the
clean-tree refusal and ends at the lane's own exit status; earlier failures
are configuration errors naming no invocation and record nothing, and
`--dry-run` records nothing. Refusals and aborts inside the window update
`latest` and re-raise/return unchanged. Every recorder failure — un-ignored
store, held lock past the bound, corrupt store, write error — degrades to ONE
warning line on stderr (never a traceback, `R-04`) and never changes the
lane's exit status.

**Both named controlled-wrong-implementations are caught**, verified by a
20-mutant probe campaign against the real source (all caught, zero survivors,
source restored byte-identical). Trap 1 is caught at both levels it can
occur: structurally (`TestHistoryRollingSeries::
test_series_survives_across_commits_not_just_the_last` — a latest-only store
keeps 1 entry where 3 commits ran) and statistically
(`::test_one_slow_outlier_does_not_become_the_typical_cost` — the reported
median is 10.0s where the mean would be 40.0s, i.e. (10+10+100)/3; `max`
still publishes the 100.0s outlier, which is the separate point that an
outlier stays visible rather than being discarded). Trap 2's literal form is
`TestHistoryEligibilityGuard::test_dirty_run_never_overwrites_the_commits_history_entry`
(clean 10.0s pass on commit C, then a DIRTY 999.0s run on the same C: C's
entry still reads 10.0s, `latest` moved to 999.0s with
`history_eligible: false` and a reason naming the dirt), with siblings for
the aborted, errored, mid-rebase and indeterminate routes to the same
corruption.

**Round-2 review fixes** (adversarial review returned ACCEPT-conditional; two
blockers, both fixed here, each with its own mutant):

- **B1 — `history` ignored `--worktree` and answered with the WRONG tree's
  data**, silently: `cmd_history` ran before `resolve_repo_and_worktree` and
  got the raw project dir, so the write side honored the flag (`R-36f`) and
  the read side did not. Fixed by HONORING it — the verb reads the selected
  tree's store and DISCLOSES which tree it describes (`tree:` line;
  `worktree_scope` in JSON). Resolution is opt-in so an unflagged query stays
  git-free. Writing the error-path test found a second hole in the same fix:
  a READ has no downstream to fail in, so an unresolvable override used to
  compute a store path under a nonexistent tree and answer "(not written
  yet)" — B1 through the error path. A non-directory now refuses (exit 2) and
  a non-work-tree refuses (exit 3, carrying git's own line).
- **B2 — Ctrl-C during the telemetry write became an uncaught `KeyError`.**
  The normal-path flush sits inside the tool's own exception scope and is not
  instantaneous (it spawns `git check-ignore` and can wait up to the 5s lock
  bound), so an interrupt landing there reached the abort handler, which
  flushed the same already-consumed record and raised — replacing the real
  signal with a traceback `R-36h`/`R-04` both forbid. Flushing is now
  at-most-once with the claim staked BEFORE the work, plus a None-safe start
  stamp; a record with no measured duration is refused by the eligibility
  conjunction (clause 1) rather than stored.
- **S1** — `--json` was accepted and ignored outside `history` (`--list
  --json` handed a TSV to a caller asking for JSON). It is now refused by
  name, the same rule as RG-1's `--worktree` and RG-26's `--base`.
- **S2** — the reserved-lane-name change is a LOAD-TIME breaking change for
  any consumer with a `[lanes.history]`; zero estate projects have one, but
  it is now flagged as a BREAKING CHANGE block in CHANGES.md and CONSUMERS.md
  the way RG-23's was.

**Out of scope, honored:** run-gate decides no rigor/defer POLICY. It
measures and persists; a controller reads and decides. Follow-ups a consumer
may want (age-based retention, an estate-wide roll-up, a `--worktree`-aware
query, and the v8-absorption reopening of the scoping question) are named in
the REPORT §7 and deliberately not filed as entries without a real consumer
asking.

## RG-28 — `run_host_lane` raised `KeyError('argv')` for `kind = "assay"` on the built-in `host` environment

**Filed and FIXED 2026-08-31 (rev 28)**, found while implementing RG-26.

`_validate_lane` accepts `kind = "assay"` with `environment = "host"` — no
rule forbids it, and the assay/host combination is a reasonable shape for a
project whose judge runs on the machine (run-gate-project's own selftest lane
is a host lane; an assay-judged sibling would look exactly like this).
`run_host_lane` nonetheless did `substitute_worktree(lane["argv"], …)`
unconditionally, so such a lane died with a `KeyError('argv')` traceback —
`R-04` names a traceback for a config/usage error a defect outright. It now
builds the same assay inner the two container runners build
(`build_assay_inner`), whose `GIT_CONFIG_GLOBAL` isolation (`R-19a`) keeps
the `safe.directory` write out of the operator's own git config. SPEC `R-19`
amended. Covered by `TestComparisonBasePassthrough::
test_host_assay_lane_probes_locally_without_docker`, which drives a host
assay lane end to end with docker never invoked.

## RG-29 — `cmru/run-gate.toml` pins a vanished assay sidecar, which turns run-gate-project's OWN gate red

**Filed:** 2026-08-31, from the run-gate-P02 bundle (RG-21/23/24/25/26). Not
fixed by that package: the defect is in `cmru/run-gate.toml`, outside that
project's scope, and its own scope statement forbade touching it.

**Fixed:** 2026-08-31, directly on `main` by the controller (same pattern as
the earlier `ciu/run-gate.toml` assay-pin fix). All four sites named below
moved from `2.2.0` to `2.3.0`; `sha256sum -c` verified against the vendored
`cmru/tools/assay/assay-2.3.0.pyz` before committing;
`./run-gate.py validate-pointers ../cmru/cmru.toml` confirmed `OK`.

### The bug

`run-gate-project`'s gate lane (`./run-gate.py selftest`) includes
`TestPointerLinkageEstate::test_cmru_release_step_names_a_real_lane`, which
runs `validate-pointers ../cmru/cmru.toml`. That fails:

```
run-gate: DEFECT steps.run-tests.commands[0].argv[argv]: loading
../cmru/run-gate.toml: lane '[lanes.assay]': pin 'assay' sidecar
tools/assay/assay-2.2.0.pyz.sha256 does not exist in this project (../cmru)
— vendor it or shadow the lane
run-gate: validate-pointers FAILED: 1 defect(s) across 0 invocation(s)
```

`cmru/tools/assay/` contains `assay-2.3.0.pyz` + `assay-2.3.0.pyz.sha256`;
`cmru/run-gate.toml` still declares the **2.2.0** sidecar. Confirmed
pre-existing on `main` at `858766d1` (identical failure from the primary
checkout and from a fresh worktree, and `git ls-tree` shows only the 2.3.0
pair tracked).

**Why it matters beyond cmru:** because the selftest lane's argv is
`pytest … && coverage_gate`, a red pytest SHORT-CIRCUITS the diff-coverage
floor — so while this is broken, run-gate's own gate cannot report a coverage
verdict at all, and any consumer reading `./run-gate.py selftest`'s exit
status sees red for a reason unrelated to run-gate.

### Fix (cmru-side)

**FOUR things move together, not one.** `cmru/run-gate.toml` names the
vanished artifact in four places, and bumping only the pin leaves the lane
broken in a different way — it would then verify a 2.3.0 sha256 and
immediately try to execute an `assay-2.2.0.pyz` that is not there:

1. `[lanes.assay.pins.assay] sha256` → `tools/assay/assay-2.3.0.pyz.sha256`;
2. `[lanes.assay.pins.assay] version` → `2.3.0` (it is VERIFIED in-lane
   against `<assay_command> --version`, RG-4/`R-08`, so a stale value fails
   the lane loudly rather than silently);
3. `[lanes.assay] assay_command` (`cmru/run-gate.toml:21`) → the literal
   filename `tools/assay/assay-2.3.0.pyz`;
4. `[lanes.mutation] argv` (`cmru/run-gate.toml:55`) → its
   `--assay-zipapp tools/assay/assay-2.2.0.pyz` carries a fourth copy of the
   same filename, inside a free-form shell string that `validate-pointers`
   deliberately never stats (`R-22`: argv strings are shell text, not
   declared paths), so nothing will tell you about this one.

Alternatively vendor the 2.2.0 pair back. Then re-run
`run-gate-project/run-gate.py selftest`.

### Acceptance

- [x] `./run-gate.py validate-pointers ../cmru/cmru.toml` exits 0;
- [x] `grep -n 'assay-2\.2\.0' cmru/run-gate.toml` returns nothing — pin AND
      `assay_command` both moved;
- [x] `run-gate-project`'s `selftest` lane is green end to end, including
      the diff-coverage step that currently never executes.

## RG-32 — `pins.assay.budget` in `run-gate.toml` is silently inert; the governing value lives only in the consuming lane's `assay.toml`

**Found 2026-09-02 in dstdns**, three independent times in one session
(two different Opus code-review agents plus the controller), on the SAME
`kind = "assay"` lane, each initially misreading which `budget` value
governed a long-running mutation-testing gate before checking with
`tomllib` instead of eyeballing `sed` output.

### The bug

For a `kind = "assay"` lane, `run-gate.toml` accepts a `budget` key
directly under `[lanes.<name>.pins.assay]` (adjacent to `version`, `sha256`,
`clean_tree`) — the shape looks like a normal, load-bearing lane setting,
and both consumer authors and reviewers plausibly assume it constrains or
documents the lane's run time. It does not: `run-gate` never reads
`pins.assay.budget` for anything. The actual governing value, if any, is
whatever the target `assay.toml`'s own `[lanes.<assay_lane>]` block declares
as `budget` — a SEPARATE file, SEPARATE key path, silently unconnected to
the one in `run-gate.toml`. Confirmed via `tomllib` on dstdns's own
`run-gate.toml`:

```
sql-mutation                    lane.budget=None    pins.assay.budget='90m'
assay-p129-enumeration-cursor   lane.budget=None    pins.assay.budget='10m'
```

Both `90m` and `10m` are dead text. `dstdns`'s `assay.toml` separately
declares `budget = "120m"` for the `cw2b_schema` lane `sql-mutation` points
at (raised from `90m` in an unrelated, later, per-project decision) — the
two numbers drifted apart with nothing to notice or prevent it.

**Why it matters beyond one misreading.** `kind = "command"` lanes (e.g.
`scale-admission`, `schema`) DO carry a genuine lane-level `budget` (no
`pins` sub-table), which some consumer-side tests key off of directly
(dstdns's `test_o11b` asserts `timeout_seconds == _budget_to_seconds(budget)`
against that real value). The two shapes look identical at a glance —
same key name, same-looking TOML nesting one level apart — so a reviewer or
implementer has no local signal that one is real and the other is
decorative. This is a `R-04`-class defect (a config value indistinguishable
from a real setting through normal reading, silently doing nothing) rather
than a cosmetic nit.

### Initial proposed fixes (superseded by the D-666 amendment below)

Either (a) `_validate_lane` rejects an unrecognized `budget` key under
`[lanes.*.pins.assay]` outright (a `kind = "assay"` lane's budget, if
run-gate is meant to enforce one at all, belongs at the lane level like
`kind = "command"` lanes, not inside `pins.assay`), or (b) if the intent was
always "the consuming assay.toml owns budget enforcement, run-gate's copy is
purely informational," rename the key (e.g. `budget_hint`) so it cannot be
mistaken for an enforced value, and add a `validate-pointers`-style check
that a declared `budget_hint` still matches the target `assay.toml` lane's
real `budget` at least at declaration time (catching exactly the kind of
silent 90m/120m drift found here).

### Acceptance

- [x] A `pins.assay.budget` key (however named going forward) either
      enforces something real or cannot be typo'd/misread as if it did;
- [~] `validate-pointers` (or an equivalent check) catches a declared value
      that has drifted from the target `assay.toml` lane's real `budget`;
      — **deliberately not built**, see the status note: option (b) was
      rejected, so there is no declared value left to drift.
- [ ] Existing dstdns lanes with a stale `pins.assay.budget` (`sql-mutation`
      `90m` vs real `120m`; likely others — not exhaustively swept from this
      report) get a follow-up cleanup pass once the mechanism is fixed here.
      — **dstdns-side**, and now forced rather than optional: the key
      refuses at load from rev 34, so dstdns must delete it before upgrading
      (controller notifies dstdns-23 at release).

### Status — FIXED 2026-09-02 (rev 34, SPEC `R-08a`), BREAKING

Option **(a) refuse**, per ruling RW-7 of the "resumable, observable gate"
wave. Option (b) — rename to `budget_hint` + a `validate-pointers` drift
check — was rejected on the record: the cross-check would be a SECOND
reading of an assay-owned fact, which `R-35` already forbids for the
comparison base, and a decorative key is still a key every reviewer has to
learn to ignore.

`[lanes.<name>.pins.<pin>]` now validates its keys at all: `sha256` and
`version`, nothing else. `budget` gets its own message rather than the
generic unknown-key one, because the person reading it needs to be told
where the value they meant actually lives:

```
<file> [lanes.sql-mutation].pins.assay: pin 'assay' declares 'budget' —
run-gate never enforced it; the lane's budget lives in the consumer's
assay.toml [lanes.cw2b_schema] (delete this key; the lane-level run-gate
'budget' stays advisory)
```

The generic unknown-key refusal is the durable half of the fix: a pin table
that accepted anything is HOW `budget` came to live there. Four tests in
`TestPinKeysAreValidated`, including one that pins the surviving
lookalike — a real lane-level `budget` still loads and stays advisory.

No vbpub-estate `run-gate.toml` declares the key (swept 2026-09-02, all
eleven configs parsed).

**Amended 2026-09-02 after adversarial review round 1 (B1, ruling RW-13).**
The first consumer-impact sweep was a text `grep`, which cannot tell a
lane-level `budget` from one inside a pin table — the exact nesting confusion
this item is about. PARSED with the rev-34 loader over every dstdns lane:

- **18 of 35 lanes as measured on 2026-09-03** refuse at load, not 2:
  `assay-dlq`, `assay`, `sql-mutation`, `assay-p129-enumeration-cursor`,
  `worker-execution-admission`,
  `worker-execution-admission-r2-{compare,boolop,flips,falsy}`,
  `assay-p169-op-override-projection`,
  `assay-p169-op-override-projection-r2-{compare,boolop,falsy}`,
  `assay-p166-result-dedup`,
  `assay-p166-result-dedup-r2-{compare,boolop,flips,falsy}`. It was 13 of 29
  on 2026-09-02 (dstdns merged the `assay-p166-result-dedup` family in
  between). **Re-measured, not trusted** — the command lives in CHANGES
  `[Unreleased]` beside this entry (RW-30): a hard-coded count of a peer
  repo's config cannot stay true between a review and a merge.
- The migration is **two rounds**: the `budget` refusal fires first and masks
  every other misplaced key in the same table. Four of those lanes
  (`assay-dlq`, `assay`, `sql-mutation`, `assay-p129-enumeration-cursor`)
  then refuse again on `clean_tree = false`, which sits in the same
  misplaced position.
- **A misplaced key that is itself a legal LANE key is now named as one**, so
  the fix does not merely rename its own defect class: `'clean_tree' is a
  lane key; it belongs one level up in [lanes.<n>], where it is load-bearing
  — move it, do not delete it (under a pin table it has never done anything,
  so the lane has been running with the default instead)`. `budget` is
  excluded from that clause and keeps its own message: it is the one
  misplaced key whose remedy really is deletion. Three further tests in
  `TestPinKeysAreValidated` cover the new clause, the plural form and
  `budget`'s precedence over it.
- **Found in passing, and it is dstdns's to file:** those four lanes'
  `clean_tree = false` has been inert since it was written — they have been
  running with `clean_tree = true`.

## RG-33 — `sql-mutation`-style assay mutation lanes never pass `--resume`, so a budget-exceeded retry re-tests everything from scratch

### What's wrong

`assay run` supports `--resume` (persists completed mutation candidates
under `.assay/mutation-state/`, keyed by a deterministic id derived from the
mutated file's path, exact source bytes, mutated byte span, replacement
bytes, and operator — CONSUMERS.md §"Resume and shard a long mutation
lane"). A real source change produces a different id, so `--resume` never
masks a genuine change; it silently re-executes whatever no longer matches.

`run-gate` never passes `--resume` (or `--progress`) when invoking an
assay-kind mutation lane. Confirmed live on dstdns's `sql-mutation` lane
(2026-09-02, retry r3): the actual docker exec argv was `python3
tools/assay/assay-4.0.0.pyz run cw2b_schema --file assay.toml
--verdict-json .assay/verdict-cw2b_schema.json` — no `--resume` anywhere.
That attempt ran its full 120-minute budget, hit `budget_exceeded` on
172 mutants of the FIRST of 4 target files, and ended
`ERROR`/`EXEC_FAILED`. Checking the worktree's `.assay/` directory:
`mutation-state/` does not exist at all, confirming `--resume` has never
been used on this lane — every one of that lane's retries so far has
started file 1 over from mutant #1.

### Why it matters

A mutation lane whose budget is already tight (raised 90m→120m once
already for exactly this reason, per the target `assay.toml`'s own history)
throws away a full budget window's worth of real progress on every retry
that doesn't finish in time — the exact scenario `--resume` exists to
avoid. On a host under any contention (the discovery context here: a
shared dev/test host also running a production workload, CPU-starved per
`/proc/pressure/cpu`), a lane that structurally cannot finish in one
budget window can never finish at all under the current wiring, no matter
how many times it's retried.

### Proposed fix

Have `run-gate` pass `--resume` unconditionally for every `kind = "assay"`
lane invocation whose target declares an `R2` (mutation) rigor — it is
safe by construction (resume never trusts a record whose source no longer
matches) and there is no real scenario where re-testing already-verified
mutants from scratch is the desired behavior. Optionally also wire
`--progress <path-outside-the-worktree>` for observability, per
CONSUMERS.md's own guidance to keep it off the tracked/judged tree.

### Acceptance

- [ ] A `kind = "assay"` lane with `R2` in its target's `declared_rigor`
      gets `--resume` in its constructed argv, verified via the actual
      docker exec command line, not just the TOML declaration;
- [ ] A controlled two-attempt test (first attempt killed/budget-capped
      mid-sweep, second attempt re-run against the same commit) shows the
      second attempt's candidate count for already-completed mutants come
      back from `.assay/mutation-state/` rather than re-executing;
- [ ] A genuine source change between attempts (one target file edited)
      still re-executes every candidate touching that file — resume must
      never mask a real regression.

### Source

`dstdns` P165 (`nyxloom-trove/decisions.md` D-318/D-319), discovered while
investigating why its `sql-mutation` retry needed a full budget window
without finishing even one of its four target files.

### Status — FIXED 2026-09-02 (rev 33, SPEC `R-38`)

Operator directive the same day (vbpub session): "all assay lanes make use of
`resume` and `progress`". Landed as `build_assay_inner` appending `--resume
--progress .assay/progress-<assay_lane>.jsonl` to EVERY assay-kind
invocation — unconditionally rather than rigor-gated, because assay ignores
both on a lane without R2 (its `--progress` help says so, and resume state
is only touched by the mutation sweep), so the proposal's "whose target
declares R2" condition would have been a second reading of an assay-owned
fact for no behavioural gain. Acceptance: (1) DONE — five tests in
`TestResumeAndProgressAlways`, including the executed argv on the host
runner (RG-28's echo judge) and the dry-run docker argv line; (2) and (3)
are assay's contract, not run-gate's, and are proven in assay's own suite
(`tests/test_mutation_resume_sharding.py`,
`tests/test_mutation_progress_budget_plan.py`; the candidate id folds in the
file's exact bytes, so an edited file re-executes every candidate touching
it) — run-gate's suite has no R2 lane and a two-attempt R2 run here would be
a copy of that suite. Judge floor: `--resume` is assay 2.4.0, `--progress`
2.4.1; a pin declaring an older `version` now refuses by name at argv
construction (cmru was the only estate consumer below it, at 2.3.0 —
re-pinned in the same package). Not fixed on the assay side: assay's OWN
gate (`assay/tools/tester-unified-gate.sh`) invokes `assay run` directly,
not through run-gate — routed to the running assay wave to mirror.

## RG-34 — `schema` lane's argv doesn't template `{worktree}` into its own script path, breaking any Mode-B worktree with a dedicated (non-shared) test-runner container

### What's wrong

`run-gate.toml`'s `[lanes.schema]`:

```toml
argv = ["scripts/schema-gate.sh", "{worktree}"]
```

Only the ARGUMENT is templated with `{worktree}`; the script path itself
(`scripts/schema-gate.sh`) is a bare relative string, resolved against
whatever `--workdir` the docker exec uses. The sibling `p128-schema-lineage`
lane gets this right: `argv = ["bash", "{worktree}/scripts/p128-assay-schema.sh"]`.

For MAIN's shared `test-runner` container this is invisible: it bind-mounts
the WHOLE host repo root at `/workspaces/dstdns`
(`docker inspect`: `/home/vb/.../dstdns -> /workspaces/dstdns`), so
`--workdir /workspaces/dstdns` + bare `scripts/schema-gate.sh` happens to
resolve correctly regardless of which worktree's tests are actually being
judged. A Mode-B instance's own DEDICATED test-runner container (one per
worktree, not the shared one) mounts only that worktree's subtree, remapped
to the SAME container path: `docker inspect` on
`p152-...-test-runner` shows `/home/vb/.../dstdns/.worktrees/p152-... ->
/workspaces/dstdns/.worktrees/p152-...` — nothing is mounted at bare
`/workspaces/dstdns` in that container at all. `scripts/schema-gate.sh`
relative to `--workdir /workspaces/dstdns` then resolves to a path that
genuinely doesn't exist in that container's filesystem, even though the
identical file exists both on the host and inside the container under its
real mount point. Confirmed live 2026-09-02: `run-gate gate --worktree
.../p152-real-fault-harness-restart-matrix` failed immediately —
`bash: line 1: scripts/schema-gate.sh: No such file or directory`,
`lane 'schema' exit 127`, `lane 'gate' exit 127` — while
`docker exec p152-...-test-runner sh -c 'ls scripts/schema-gate.sh'`
(relative to the container's own default cwd, which IS its worktree root)
finds the same file without issue.

### Why now, not always

This traces to TODAY's retirement of `./scripts/testing-exec.sh`
(dstdns `CLAUDE.md`, 2026-09-02): the old Mode-B gate path ran
`testing-exec.sh` FROM INSIDE the worktree with the worktree's own `ciu.env`
sourced (GUIDE.md §3.4, "VALIDATED 2026-07-16"), so a bare relative script
path always resolved correctly by construction — cwd itself was already the
worktree root. The direct `run-gate <lane> --worktree` replacement path
never carried that same-cwd guarantee forward for lanes whose argv wasn't
already `{worktree}`-prefixed on the script path.

### Impact

Total, silent-until-hit: the `schema` lane (and therefore the composite
`gate` lane, which runs it) cannot pass against ANY Mode-B worktree with its
own dedicated test-runner container, ever — not flaky, not budget-related,
100% reproducible. Confirmed on `dstdns` P152.

### Proposed fix

Template `{worktree}` into the script-path element of `[lanes.schema]`'s
argv too, matching `p128-schema-lineage`'s already-correct pattern:
`argv = ["{worktree}/scripts/schema-gate.sh", "{worktree}"]`. More
generally: audit every `kind = "command"` lane's argv for the same
class of defect (a relative script path NOT prefixed with `{worktree}`,
relying on `--workdir` alone) — `schema` was found only because P152
happened to be the first Mode-B package to exercise the composite `gate`
lane's schema sub-lane against a dedicated container since
`testing-exec.sh`'s retirement today.

### Acceptance

- [x] `[lanes.schema]`'s argv resolves correctly against a worktree whose
      test-runner container mounts only that worktree's own subtree (not
      the full repo root) — verified via a real Mode-B dedicated-container
      run, not just main's shared-container case;
      — **dstdns-side**: the argv lives in dstdns's `run-gate.toml`, and
      run-gate deliberately does not rewrite a consumer's declared command.
      **Done at `dstdns@65582354`** (the P152 merge): that lane now reads
      `argv = ["{worktree}/scripts/schema-gate.sh", "{worktree}"]` with an
      RG-34 comment above it.
      (The one dstdns lane that still trips the new WARN,
      `[lanes.scale-admission]`, is NOT a box here — see the close note
      below, RW-26.)
- [x] Every other `kind = "command"` lane's argv is swept for the same
      unprefixed-script-path pattern — for the vbpub estate, and by
      `doctor` from now on for every consumer;
- [x] A regression test (or `doctor`/`validate-pointers`-style static check)
      catches a lane argv whose first element lacks `{worktree}` when its
      environment is `test-runner` and it takes a `--worktree` argument.

### Status — FIXED 2026-09-02 (rev 34, SPEC `R-30b`) — run-gate's half

Ruling RW-8: **`doctor` warns; run-gate does not rewrite argv.** The argv fix
itself is the consumer's (dstdns P152), and it is one edit; a tool that
silently rewrote a declared command would be a worse defect than the one it
patched. A refusal was rejected too: the same argv is CORRECT under a
full-repo mount, and which mount a lane gets is not visible to run-gate
statically — a check that broke working consumers to prevent a hazard that
may not apply to them would be switched off, and then it protects nothing
(`R-30a`'s own reasoning).

`doctor` now emits ONE `[WARN]` per `kind = "command"` lane on a NON-host
environment whose `argv[0]` is a relative path containing `/` and not
starting with `{worktree}`:

```
run-gate: doctor: [WARN] lane 'scale-admission' argv[0] (RG-34): 'scripts/schema-gate.sh'
is a RELATIVE path, resolved against the container's --workdir instead of the
judged tree — declare it '{worktree}/scripts/schema-gate.sh'. A container that
mounts ONLY the judged worktree (a Mode-B instance's own runner, not the
shared one) has nothing at the bare repo root --workdir names, so this argv
dies there with 'No such file or directory' while working under a full-repo
mount. A warning, not a refusal: which mount the lane gets is not visible to
run-gate statically
```

It reads the DECLARATION only, so it still answers for a lane whose
environment failed to resolve, and with at least one container command lane
and nothing to flag it records one `[OK]` so a reader can tell it ran.
Doctor's exit code is unchanged by it. Six tests in
`TestDoctorNamesUnprefixedScriptPaths`, including the three shapes that must
NOT warn (`{worktree}`-anchored, absolute, bare command name) and the two
lane kinds outside the check (host lanes, whose cwd is the effective project
dir; assay lanes, which have no argv of their own).

**Estate sweep, 2026-09-02** (`tomllib` over every `*/run-gate.toml` in
vbpub, not `grep`): no vbpub lane trips the check.

**Which dstdns lane still trips it (corrected, review round 1 S7).** The
lane this item was FILED from — `[lanes.schema]` — was fixed in the P152
merge itself (`dstdns@65582354`) and now reads
`argv = ["{worktree}/scripts/schema-gate.sh", "{worktree}"]`. Parsing every
dstdns lane with `tomllib` (2026-09-02, re-run 2026-09-03) gives exactly one
hit, and it is a different lane:

```
RG34-FLAG scale-admission scripts/schema-gate.sh | env test-runner
```

(`/workspaces/dstdns/run-gate.toml:81`, `argv = ["scripts/schema-gate.sh",
"{worktree}", "tests/schema/test_scale_admission.py"]`.) The notification to
dstdns names that lane. RG-34 therefore lands with a LIVE consumer hit
rather than an already-fixed one — the transcript above is written for
`scale-admission` accordingly.

**CLOSED as FIXED, 2026-09-03 (RW-26).** run-gate's half is the `doctor`
WARN and it is shipped; that is the whole of what this item can deliver. The
`scale-admission` hit is a fact for the **dstdns notification** and a dstdns
filing, not an acceptance box in run-gate's backlog: the argv lives in
dstdns's `run-gate.toml`, run-gate deliberately never rewrites a consumer's
declared command, and a box run-gate can never tick makes a closed item read
as open forever. Re-measured 2026-09-03 with `tomllib` over every dstdns
lane: still exactly ONE hit, still `scale-admission`.

### Source

`dstdns` P152 (`nyxloom-trove/decisions.md` D-319), discovered live while
finishing P152's own composite gate run under the operator's single-stack
host-contention directive.

### Second independent reproduction, BROADER consequence (dstdns P156, 2026-09-02)

Same root fact (a Mode-B worktree's own dedicated test-runner container
mounts ONLY that worktree's subtree, never the main checkout's `.git`), a
DIFFERENT and wider-reaching symptom than the schema-lane argv bug above:
running `run-gate test-runner --worktree <p156 worktree>` (the disclosed
RG-24 `docker exec` workaround, since run-gate's own container resolution
still targets the MAIN landscape per RG-24, still open) surfaced 12 failed
+ 14 errors across FIVE unrelated files, ALL tracing to the identical cause
— a bare `subprocess.run(["git", ...], cwd=REPO_ROOT, ...)` where
`REPO_ROOT` is `Path(__file__).resolve().parents[N]` (correctly resolving
to the worktree checkout itself, NOT the P152 script-path bug) but that
worktree's `.git` FILE names a `gitdir:` target
(`/workspaces/dstdns/.git/worktrees/<name>`) unreachable from inside the
container:
```
fatal: not a git repository: /workspaces/dstdns/.git/worktrees/p156-scale-admission-live-bounds
```
Files hit: `tests/config/test_lane_membership_census.py` (2 tests, `git
ls-files`), `tests/config/test_workflow_stream_sources_agree.py` (all 14
nodes error, `git ls-files '*.toml' '*.j2'`), `tests/config/
test_doc_hygiene.py` (2 tests), `tests/config/
test_legacy_config_system_is_gone.py` (6 tests), `tests/config/
test_service_identity_inline.py` (2 tests, `git show <rev>:<path>`).
Confirmed as environmental, not a P156 regression, via the discriminating
test the acceptance criteria above already call for: the SAME 26 test
nodes, run unchanged against `main`'s shared Mode-A test-runner (full repo
root mounted, real `.git`), all PASS. Zero of these five files are P152's
`schema-gate.sh`, so this is proof the mount gap is not scoped to one
script — it is ANY in-container `git` subprocess call, repo-wide, and this
backlog's own SPEC.md `R-23` claim ("run-gate's OWN container lanes are
unaffected, because R-23 dual-mounts the REPO root") does not hold for this
call path: R-23 only fires when run-gate itself constructs the container's
mounts, and RG-24's disclosed Mode-B `docker exec` workaround never goes
through that construction — it execs into a container ciu already deployed
with its own (single-mount) compose definition.

**Where the real fix belongs, most likely NOT here:** the test-runner
container's mount set is a dstdns-owned artifact
(`tools/test-runner/ciu.compose.yml.j2`), not something run-gate
constructs for the RG-24 exec path — so the durable fix is almost
certainly a second, read-only bind mount of the main checkout's `.git`
into every Mode-B dedicated test-runner (git worktrees are DESIGNED to
share one object database this way; it would not compromise per-worktree
file isolation). Filed here rather than only as a dstdns-local note because
this entry is where the mount-scope fact already lives, and because R-23's
"container lanes are unaffected" claim in SPEC.md needs a caveat for the
RG-24 exec-into-existing-container path specifically, independent of
whichever repo lands the compose-mount fix. dstdns-local workaround applied
in the interim, scoped to exactly one already-in-scope file (`test_
lane_membership_census.py`): a cheap `git rev-parse --git-dir` liveness
probe, `pytest.mark.skipif` on the one test needing `git ls-files`, honest
`reason=` naming this exact gap — mirroring `test_run_gate_pointer_linkage.
py`'s own "skip, don't fail, when the environment genuinely can't" pattern.
The other four files were left unfixed (out of P156's own scope; this
finding is the report of that decision, not a claim they were repaired).

## RG-35 — a lane's container outlives a dead run-gate client, but nothing re-attaches; a restart starts a duplicate

**Filed 2026-09-02 (vbpub controller session), from the operator's ask to make
dstdns's "progress artifact + re-attachable runs + unbounded budget" pattern
the default for assay/run-gate too. This is the re-attach leg.**

### What's wrong

`run_container_lane` does `docker run -d --name <lane-pid-ts>` →
`docker logs -f` → `docker wait` → `docker rm -f` in a `finally`
(`run-gate.py:2500-2554`). When the CLIENT dies — SIGKILL, a devcontainer
restart, a harness that reaps a background command (measured the same day:
the Claude harness killed a detached-by-mistake `cmru release` after 33 s
and its inner gate container ran to completion unobserved) — the container
keeps running and the judge still writes `.assay/verdict-<lane>.json` into
the bind-mounted worktree, but nobody collects the exit status, no evidence
is captured (RG-12), no history record is written (RG-27), and the next
invocation of the same lane on the same worktree starts a SECOND container
— the one-gate-at-a-time rule broken by the tool itself, on a host that
shares 8 cores with a production game server (load 85 that afternoon).

### Proposed fix

- On a successful `docker run -d`, write `.run-gate/inflight/<lane>.json`
  (git-ignored, R-36's store discipline): container name and id,
  `started_at`, judged commit, worktree, verdict path, progress path.
- On invocation, if an inflight record names a container that still
  EXISTS: re-attach — `docker logs -f --since <recorded>` + `docker wait` —
  instead of starting one, disclosed as
  `run-gate: re-attached to <name> (started <t>, <elapsed> ago)`; if it has
  already exited: collect exit code, logs (evidence on failure) and the
  verdict, finish the run exactly as an attached one would, then remove the
  container. `--fresh` forces a new run (removes the old container first,
  disclosed by name). The record is cleared in the same `finally` that
  removes the container.
- RG-27 history records a re-attached run once, with the real duration from
  `started_at`.

### Acceptance

- [x] kill -9 the client mid-lane (fake docker AND one live probe — the
      live one is recorded in the wave REPORT): the container finishes; a
      second invocation prints the re-attach line, yields the container's
      real exit code, records history once, and starts NO second container;
- [x] an inflight record whose container is gone (host reboot) is reported
      and cleared, never silently ignored;
- [x] `--dry-run` discloses an existing inflight record.

### Status — FIXED 2026-09-02 (rev 34, SPEC `R-39`)

Landed under the "resumable, observable gate" wave (RW-1..RW-3 of
`/workspaces/vbpub/run-gate-project/nyxloom-trove/WAVE-PROMPT-2026-09-02-resumable-gate.md`
— the MAIN checkout's, not this branch's, whose `nyxloom-trove/` holds
`reports/` only (review round 1 N5); decision D4 of the
post-v10 plan — automatic re-attach with `--fresh` as the escape, not an
`--attach` flag).

**Red proof (the controlled wrong implementation was the shipped rev 33).**
`TestReattachAcrossADeadClient` drives a real client to its death against a
STATEFUL fake docker (`fake_docker_stateful`: `run -d` creates a container,
`inspect` answers from it or exits 1 like `No such object`, `wait` returns
the recorded code, `rm -f` destroys it, a `.hang` marker makes `logs -f`
block). Against rev 33 (measured on a detached worktree at `f6d3a858`):

```
AssertionError: a SECOND container was started for a lane that already had
one running: [… '--name', 'run-gate-repo-suite-1779582-1788385365' …],
             [… '--name', 'run-gate-repo-suite-1779695-1788385369' …]
assert 2 == 1
```

Same test post-fix: one `docker run`, `run-gate: re-attached to
run-gate-repo-suite-…`, exit 0, container removed, record cleared.

**What landed.** `.run-gate/inflight/<lane>.json` written on a successful
`docker run -d` (R-39a); `resolve_inflight()` taking the five-way decision
before anything is built (R-39b: re-attach / collect / report-lost-and-run /
refuse on a commit mismatch / `--fresh`); `await_container()` as the single
finish shared by all three arrival paths (R-39c); `--fresh` refused by name
on host lanes, exec lanes and every verb (R-39d). History records such a run
once, with the duration from the container's own start (RW-3).

**Live acceptance probe** (a fake-docker argv proves construction, not
acceptance — AGENTS.md): one real `tester-unified:local` run, client killed
mid-lane, second invocation re-attaches. Run under the host's
one-container-at-a-time rule; transcript in
`nyxloom-trove/reports/run-gate-WAVE-RESUMABLE-REPORT.md`.

## RG-36 — liveness judged from the progress file, not from a guessed wall budget: `stall_timeout` and ETA disclosure for assay lanes

**Filed 2026-09-02, same ask as RG-35; this is the "unbounded budget by
convention" leg.**

### What's wrong

`budget` is advisory in run-gate (`run-gate.py:2519`, printed, never
enforced) and a hard lane-wide bound in assay (`LANE_TIMEOUT`). The only way
to bound a long mutation lane today is to guess a TOTAL number: dstdns raised
`sql-mutation` from 90m to 120m once and it still could not finish a
budget window (RG-33's transcript). Since rev 33 every assay lane writes
`.assay/progress-<lane>.jsonl` — per-candidate events carrying
`candidate_index` / `candidate_total` — so health can be judged from
progress instead: rate, ETA, and stall.

### Proposed fix

- While the container runs, tail the progress file and print
  `run-gate: progress <lane>: candidate 37/172, 1.9/min, ETA 71m` at a fixed
  interval (disclosure only, R-05). No progress file (an R0/R1 lane, or a
  judge that writes none) is disclosed once and never treated as a fault.
- Optional lane key `stall_timeout = "15m"`: the lane is stopped (`docker rm
  -f`, evidence saved, exit 3 naming the stall and the last event seen) only
  when the container is still running AND no event has been appended for
  that long — never on total elapsed time. `budget` stays advisory; the
  documented shape for a mutation lane becomes a generous assay `budget` +
  `judge.mutation.budget_per_candidate` + run-gate `stall_timeout`.
- Dependency: assay's events carry no timestamp today (`_progress_event`,
  `mutation.py:755`), so ETA uses run-gate's own clock from the first event
  it observed and stall uses the file's mtime — coarse but measured; assay
  B065 (per-event `emitted_at` / `elapsed_s`) makes both exact.

### Acceptance

- [x] a progress file advancing under a fake judge produces the ETA line
      with the right arithmetic; a frozen file + running container trips
      the stall at the configured time with evidence saved and exit 3;
- [x] no `stall_timeout` declared → behaviour unchanged;
- [x] an R0/R1 lane (no events) never stalls and says why, once.

### Status — FIXED 2026-09-02 (rev 34, SPEC `R-40`) — the COARSE half

Rulings RW-4/RW-5/RW-6. The "exact timing" half waits on assay **B065**
(per-event `emitted_at`/`elapsed_s`) and is E-3 of the post-v10 plan; the
code here already PREFERS an event's `elapsed_s` where one exists, so B065
makes the same implementation exact without a rewrite.

**Disclosure (`R-40a`/`R-40b`).** While an assay lane's container runs, the
file R-38 already asks for is read every `PROGRESS_POLL_SECONDS = 30` (a
module constant with its reason: 30 s judges a 15-minute `stall_timeout` to
within 3% and costs a 4-hour lane 480 `stat()`s) and, when something changed,
one line is printed:

```
run-gate: progress sql-mutation: candidate 29/172, 1.9/min, ETA 75m
```

The FIRST observation prints the count alone — one event and no clock in the
file is a baseline, not a measurement, and inventing a rate there would be
the guess this replaces. No file, a header-only file (R0/R1), or a torn last
line yields `progress <lane>: no candidate events (not an R2 lane, or the
judge writes none)` exactly ONCE, and is healthy.

**`stall_timeout` (`R-40c`).** Optional lane key, the `budget` grammar,
assay lanes only. The lane is stopped only when the container is STILL
RUNNING and the file has not advanced for that long — and "still running" is
structural, not asserted: the check runs only inside the poll of a `docker
logs -f` that has not returned. Stop = `docker rm -f`, evidence saved, exit
3:

```
run-gate: lane 'mutation' STALLED: the container is still RUNNING but
progress-cw2b_schema.jsonl has not advanced for 900s (stall_timeout 900s);
last event seen: candidate 37/172. The container was removed; container logs
preserved at /tmp/run-gate/run-gate-....log
```

Never on total elapsed time — `budget` stays advisory, its print unchanged,
and the two are disclosed side by side saying which is which. Declared on a
`kind = "command"` lane the key is REFUSED at load: it could never do
anything there, which is exactly RG-32's defect one key over.

**Correction, 2026-10-04 (rev 49, RG-63):** the historical proposal above
predates enforcement. `budget` is now a hard wall-clock limit. Its clock
starts after admission and runner locks are acquired, so queue wait does not
consume execution time; `stall_timeout` remains the separate silence bound.

**Tests** (22): `TestProgressWatch` drives the arithmetic and the silence
rule on a substituted clock (rate from run-gate's clock, rate from a
B065-shaped `elapsed_s`, no-change prints nothing, no-events disclosed once,
header-only, torn line, stall at exactly the configured age, movement resets
the stall clock, no `stall_timeout` = disclosure only, absent total = no
ETA); `TestStallTimeoutLaneKey` the validation and the two disclosures;
`TestStallEndToEnd` the real container loop through `main()` — a frozen file
under a blocking `docker logs -f` exits 3 with the container removed and the
inflight record cleared, and a file a thread keeps advancing never stalls.

## RG-38 — resume state does not survive an ephemeral worktree (cmru release worktrees, Mode-B worktrees)

**Filed 2026-09-02, same ask; this is the durability half of the resume leg.**

### What's wrong

assay writes `.assay/mutation-state/<candidate-id>.json` under the JUDGED
project root (`mutation.py:797-806`). With a persistent worktree (dstdns's
main checkout) retries now resume (rev 33). cmru's release transaction and
dstdns's Mode-B instances create a FRESH worktree per run, so a retried
release or Mode-B mutation lane starts from mutant #1 despite `--resume`.
Candidate ids fold in the file's exact bytes, span, replacement and
operator, so state is safe to share across worktrees and commits by
construction: a record either matches the candidate exactly or is ignored.

### Proposed fix

Bind-mount a per-repo durable directory (`<repo>/.run-gate/assay-state/
<project>/`, git-ignored) at the container path assay writes to. That needs
assay to accept a state location (`--state-dir`, assay **B066**) because the
path is derived from `project_root` today; until B066 ships, a disclosed
copy-in / copy-out of `.assay/mutation-state/` around the lane is the
fallback. Once B007 (multi-target canary) and F015 (R4) land, the same
directory holds their per-target / per-attempt records.

### Acceptance

- [ ] two invocations on two DIFFERENT worktrees of the same commit: the
      second's progress file shows `event: resume` with `resumed_total > 0`;
- [ ] a source edit between them re-executes every candidate touching the
      edited file (resume must never mask a change — RG-33's third item,
      inherited).

### Source (RG-35, RG-36, RG-38)

Operator, 2026-09-02, after dstdns's own drive()/drive_corpus() fix (progress
JSONL a caller polls; run ids persisted and re-attached; an unbounded budget
once health comes from the progress file): "can we make this a default
pattern/best practice to be used with assay/run-gate as well?" The three
legs map onto RG-35 (re-attach), RG-36 (progress-judged liveness) and
RG-38 + assay B065/B066/B067 (durable resume state, timestamped events,
per-unit bounds). R0/R1 lanes are one command each and cannot resume below
that grain by construction; canary (R3) and red-first (R4) have mutation's
per-unit shape and get the same mechanism through assay B064/B066.

### Note (dstdns controller, 2026-09-08) — the blocking dependency has shipped, this is now buildable

Assay **B066 (`--state-dir PATH`) shipped in assay-v5.2.0** (2026-09-08,
`CHANGES.md` "`--state-dir PATH`: resume state that outlives its
worktree"): the store's ROOT is now the consumer's choice (default
unchanged, byte-for-byte), the record's NAME still folds the source
file's exact bytes/span/replacement/operator (so a shared store is safe
by construction, exactly as this entry's own "Proposed fix" already
assumed), and a `--state-dir` inside the judged tree that git can see is
refused before any work (never a silent `DIRTY_TREE` surprise on the
NEXT run). Confirmed this session that run-gate's own mutation-lane argv
construction (`run-gate.py`'s RG-33 site, `run_argv = [..., "run", ...,
"--resume", "--progress", progress]`) still does NOT pass `--state-dir` —
`.assay/` under the judged worktree remains the hardcoded root, so this
entry's actual defect is unchanged and still live. The dependency this
entry was explicitly waiting on is the only thing that changed: nothing
upstream blocks implementing the proposed fix (bind-mount a per-repo
durable directory + pass `--state-dir` in the same argv construction)
anymore. Flagged for priority — dstdns has hit the exact symptom this
entry describes multiple times (`sql-mutation`, three retries, state
never written, cited above) and would adopt the fix immediately once it
ships.

### Resolution — FIXED 2026-09-08 (run-gate-P05)

`build_assay_inner` now takes `repo: Path` (threaded through all three
runners — `run_container_lane`, `run_exec_lane`, `run_bare_host_lane` — the
last of which did not carry `repo` at all before this, added here) and
unconditionally appends `--state-dir <repo>/.run-gate/assay-state/
<project's path relative to repo>/` to every assay-kind lane's `run` argv,
right after `--progress` and before RG-26's `--request-base`. `mkdir -p`
runs on that directory in the same inner script that already creates
`.assay`. Proven via `TestResumeAndProgressAlways::test_state_dir_is_
created_and_points_outside_the_judged_tree` (builder-level) and
`test_the_executed_judge_receives_both_flags` (real subprocess execution
against a real repo/proj git fixture, asserting the EXACT computed path in
the judge's own echoed argv). Full suite green (638 passed, one
pre-existing unrelated failure — `TestFreshFlagScope::test_a_host_lane_
refuses_it`, reproduces identically on a clean, unmodified checkout, an
environment-dependent docker-pull assumption unrelated to this fix — since
fixed in the same batch, see the RG-43 table note).

**Round-2 adversarial review found three real gaps, all fixed same-batch:**
(1) `ASSAY_FLAG_FLOOR` had not actually been raised to 5.2.0 despite this
note already claiming it was — a pin between 2.4.1 and 5.1.x cleared the
stale floor and failed inside the already-started container instead of at
construction. (2) the state-dir key was a bare `project_dir.name` — two
projects sharing a directory basename could collide on one shared store;
now keyed by the project's full path relative to `repo` via a new
`assay_state_dir()`, which `build_assay_inner` and the extended (now
3-tuple) `assay_artifact_paths()` both call, so the two constructions can
never drift apart. (3) the state directory was absent from RG-10's own
unconditional evidence-disclosure (`print_lane_artifacts`) — `repo`
threaded through it and its two callers (`await_container`,
`follow_container`); every assay-kind lane run now prints `run-gate: state
directory: <path>`. Three new regression tests added; full suite green
again (643 passed); real gate (`./run-gate.py selftest`) green on the
clean committed tree (diff-coverage 100%).

## RG-40 — `coverage_gate.py` reports misleading uncovered lines on a dirty tree

**Found 2026-09-02** while implementing the rev-34 wave, twice, each time
costing a full gate round (~70 s of suite plus the investigation).

### What's wrong

`tools/coverage_gate.py` derives the set of changed lines from
`git diff --relative --unified=0 <base> HEAD -- <source>` — **committed**
state — while `coverage.json` describes the file **on disk**. With a clean
tree those agree. With `--allow-dirty` over an uncommitted change they do
not: every line below the working tree's insertions is offset, so the gate
reports lines as uncovered that the suite covered, and (worse, silently)
would report lines as covered that are not.

Measured, same source in both runs, one commit apart:

```
dirty:      diff-coverage FAIL: 175/177 changed executable lines covered (98.9%)
            Uncovered changed lines: run-gate.py: [3070, 3365]
committed:  diff-coverage OK:   153/153 changed executable lines covered (100.0%)
```

`3070` and `3365` are HEAD-side numbers pointing at lines that, in the file
coverage actually measured, are 22 lines further down.

### Why it matters

`--allow-dirty` is the DOCUMENTED way to gate work in progress (every wave
prompt in this estate uses it), and the number it prints in that mode is not
just imprecise — it names specific line numbers to go and test, which is an
instruction to do the wrong thing. The two rounds this cost were both spent
writing tests for lines that were already covered.

### Proposed fix

Either (a) when the judged tree is dirty, diff the WORKING TREE (`git diff
<base>` with no `HEAD`, plus untracked-file handling) so both halves
describe the same bytes; or (b) detect the mismatch (`git status
--porcelain` on the source path) and DISCLOSE it — "diff-coverage measured
against committed state while the tree is dirty; line numbers may not
correspond" — or refuse outright, which is this estate's usual answer to a
number that cannot be trusted (R-04). (a) is the useful one; (b) is the
minimum.

### Acceptance

- [ ] The `selftest` lane run with `--allow-dirty` over an uncommitted change
      reports the SAME uncovered set as the same code reports once committed;
- [ ] Or, if the answer is (b), the run says plainly that its line numbers
      describe the committed tree and the coverage describes the working one.

### Source

run-gate rev 34's own implementation wave
(`nyxloom-trove/reports/run-gate-WAVE-RESUMABLE-LOG.md`, entries E5 and E7).

### Resolution — FIXED 2026-09-08 (run-gate-P05), oracle (a)

`_git_added_lines` now runs `git diff --relative --unified=0 <base_rev> --
<source>` — a single-ref diff, which compares `base_rev` to the WORKING
TREE directly (staged + unstaged), not to committed `HEAD`. On a clean
tree this is byte-identical to the old two-ref form (working tree == HEAD
there); on a dirty one it reports every added line at its real, on-disk
position — the same position coverage.json's own line-number keys already
describe, since coverage.py measures the bytes actually on disk. No
dirty/clean branch to keep in sync. Red-first proven with a real temp-git
-repo regression test (`test_git_added_lines_reports_the_working_trees_
own_line_numbers_dirty`): a committed addition pushed 2 lines by an
uncommitted prepend reproduces the exact reported mismatch (98.9% dirty
vs 100.0% committed) without the fix, and reports the correct line
numbers with it.

## RG-41 — a container `kind = "command"` lane has no liveness signal: judge silence from the LOG STREAM

**Filed 2026-09-02 by controller ruling RW-9**, out of the rev-34 wave's own
decision ask: `stall_timeout` was refused on command lanes, and the refusal
STANDS — but the gap it leaves is real and is this item.

### What's wrong

`R-40c` bounds a lane by SILENCE in `.assay/progress-<assay_lane>.jsonl`.
Only an assay lane writes that file, so `stall_timeout` on a
`kind = "command"` lane could never do anything and is refused at load —
correctly, because an inert key that reads like a real one is exactly
`R-08a`'s defect (RG-32), and run-gate does not guess a second signal it was
never given.

The consequence is that the lane shape MOST likely to hang has no bound at
all. A container command lane is an arbitrary consumer command (a pytest
suite, a schema gate, a conjunction of sub-lanes) running detached on a host
shared with a production workload; the only thing declared about its
duration is `budget`, which run-gate prints and never enforces (`R-15`
disclosure, not a bound). A wedged suite therefore holds a gate container
until a human notices — the failure mode RG-36 was filed to end, still open
for the majority of the estate's lanes.

### Measured evidence

run-gate's own refusal message, rev 34 (`_validate_lane`):

```
<file> [lanes.<n>]: 'stall_timeout' is judged from
.assay/progress-<assay_lane>.jsonl, which only a kind = "assay" lane writes —
a command lane has no progress file and could never stall by this rule; use
the command's own timeout instead (R-40)
```

Scope of the gap, counted with `tomllib` over every `*/run-gate.toml` in
vbpub (2026-09-02): **5 container `kind = "command"` lanes vs 3 container
assay lanes** (plus 9 host lanes, which run in-process and are outside this
question entirely). So `stall_timeout` is available to the minority of the
containerised lanes, and unavailable to the majority — including every
gate-conjunction lane, which is a `kind = "command"` lane by construction
(CONSUMERS "Gate-conjunction lanes") and is the shape nyxloom's daemon
invokes.

### Proposed fix (RW-9)

run-gate ALREADY tails the container's output: `await_container` streams
`docker logs -f` for the whole run. The ARRIVAL TIME of the last line, on
run-gate's own clock, carries exactly the semantics `R-40` gives the
progress file's mtime — a lane that has printed nothing for 15 minutes is
silent in the same sense, and "silence, never total elapsed" is the same
rule. So:

- make `stall_timeout` legal on a container `kind = "command"` lane, judged
  from log-stream silence;
- DISCLOSE the source at start, since the two signals differ in what they
  can miss: `run-gate: stall_timeout 15m (source: progress file)` vs
  `(source: log stream)`. A lane that legitimately prints nothing for long
  stretches (a single silent compile) is the log stream's known blind spot
  and the reason the source must be named, not inferred;
- keep the assay-lane behaviour exactly as `R-40` defines it — the progress
  file stays the better signal where it exists, and an assay lane must not
  silently downgrade to the weaker one;
- the stop, the evidence, the exit code and the message shape are `R-40c`'s
  already: `docker rm -f`, evidence saved, exit 3, naming the stall, the
  last thing seen and the age.

The implementation seam is one line: the poll loop in `await_container`
already runs every `PROGRESS_POLL_SECONDS` and already owns the process
whose output would be timestamped. Reading the stream's arrival times means
`docker logs -f` can no longer inherit run-gate's stdout untouched — that is
the real cost of this item and the reason it is not a footnote to RG-36.

### Acceptance

- [ ] A container command lane declaring `stall_timeout` loads, and its run
      discloses `source: log stream` at start;
- [ ] A lane whose container is running and has printed nothing for the
      declared window is stopped: `docker rm -f`, evidence saved, exit 3,
      naming the age and the last line seen;
- [ ] A lane that keeps printing is never stopped, and its output still
      reaches the operator's terminal unbuffered and in order (the
      pass-through must not regress into a captured-then-replayed stream);
- [ ] An assay lane is unchanged — it still judges from the progress file
      and discloses `source: progress file`, never downgrading to the log
      stream because a file has not appeared yet;
- [ ] **The source-of-signal line is printed on the RE-ATTACH and FOLLOW
      paths too, not only on the fresh one** (controller ruling RW-18, out
      of review round 1's S6). Rev 34's first cut printed `budget` and
      `stall_timeout` on the fresh path alone, so a re-attached lane was
      stopped against a `stall_timeout` its own invocation had never
      mentioned; rev 34 fixes that (`print_lane_bounds`, called from all
      three paths), and this item inherits the rule rather than the defect.
      The `source:` clause belongs on the same line, so adding it to
      `print_lane_bounds` covers every path by construction — a second print
      site is how the two drift apart again.

### Status — FIXED 2026-09-08 (rev 38, run-gate-P06)

Implemented per the proposed fix above, exactly: `_validate_lane`'s
kind-based refusal is gone (`stall_timeout` now validates by the same
duration grammar as `budget`, regardless of `kind`); a new `LogStreamWatch`
class (immediately after `ProgressWatch`) gives a `kind = "command"`
container lane the SAME "silence, never total elapsed" semantics off its
own log stream, timed by line-arrival instead of file-mtime. Constructed
inside `await_container` itself (not by its callers, unlike
`ProgressWatch`/`make_progress_watch`) since it needs the live `Popen`
handle on the `docker logs -f` process — captured (`stdout=PIPE`) only when
a command lane actually declares `stall_timeout`, every other lane keeping
the zero-overhead inherited-stdout path unchanged. A background thread
re-prints each line to run-gate's own stdout as it arrives and records its
arrival time; `LogStreamWatch.join()` drains anything still in flight
before `await_container`'s own status lines print, so the pass-through
never regresses into a captured-then-replayed stream and "lane X exit 0"
cannot outrun the container's own last few lines. `print_lane_bounds`
discloses the source (`progress file` vs `log stream`) on the fresh,
re-attach and follow paths alike, one print site, per the acceptance
criterion above.

Verified: a red-first-proven unit suite for `LogStreamWatch` (arrival
tracking, stall detection with age/last-line, the never-prints-anything
case, ordered live pass-through, `join()` draining, and the pipe-severed
mid-read exception path); end-to-end tests through `main()` against a
dedicated fake-docker log-stream shim (`fake_docker_logstream`, kept
separate from the widely-shared `fake_docker_stateful` so nothing here
could regress RG-35/R-39's own fixture) covering: source disclosure on the
fresh path, a silent lane stopped with evidence/exit 3/last-line, a lane
kept alive by periodic output never stopped with its lines proven to print
live and in order, and source disclosure on the re-attach and follow paths.
100% diff-coverage on the real `selftest` gate; full suite green (657
passed).

Adversarial review (round 1, folded into this SAME unreleased rev) found a
real bug in the above and fixed it here: **the re-attach case was not
actually correct.** `LogStreamWatch` timed every line's arrival by its OWN
clock, which is right for a FRESH lane but wrong for a re-attach — `docker
logs -f` REPLAYS a hung container's entire backlog in a burst, so a lane
that has been silent for hours arrived at the watch within milliseconds of
construction and read as `age ≈ 0`, silently granting a fresh full stall
window to the exact case `stall_timeout` exists to catch (CONFIRMED by
direct reproduction). Fixed the same way `ProgressWatch` already fixed its
own analogous re-attach gap (RW-27, review round 2 G1): `await_container`
now invokes `docker logs -f --timestamps` for a watched command lane, and
`LogStreamWatch` reads each line's own RFC3339Nano stamp (parsed via the
same `parse_docker_timestamp` RW-17 already trusts) and translates it into
the watch's own clock domain via the IDENTICAL arithmetic `ProgressWatch`
uses for a file's mtime — the timestamp prefix is stripped before the line
is re-printed, so the pass-through stays byte-identical to a lane with no
`stall_timeout`. Two secondary findings from the same round also fixed:
`LogStreamWatch.join()` now returns whether the drain actually finished,
and `await_container` discloses (WARNING, stderr) rather than silently
proceeds when it did not; and a real read-then-emit race in the NEW
`fake_docker_logstream` test shim (a line appended between a `wc -l` count
and a separate `tail` read could be re-emitted) was fixed by reading the
stream once per check, with the "keeps printing" end-to-end test
strengthened from substring containment to an exact, order-checked line
list so a recurrence would fail loudly rather than pass by accident. A
follower's own lack of independent stall detection was identified as a
real, pre-existing gap RG-41 merely widens exposure to — filed separately
as **RG-46**, not fixed here (see its own entry for why: it is a design
question about follower authority, not a rev-38 oversight).

A confirming round-2 review of the round-1 fix above (folded into this SAME
unreleased rev) found a real bug IN the fix itself, triple-independently
reproduced: `log_watch.join()` was called on the STALL branch, but a
stalled container's pump thread is, BY DEFINITION, still blocked reading a
pipe with nothing more coming (that is what "stalled" means) — nothing
unblocks that read until `finally`'s `proc.terminate()` runs, AFTER the
join call. The bounded `join(timeout=2.0)` therefore timed out on EVERY
single stall, not the rare host-contention case the disclosure was written
for, printing a confusing (and, separately, malformed — a doubled
apostrophe from `{lane_name!r}` already quoting the name before a literal
`'s` was appended) WARNING alongside every genuine STALLED message. Fixed
by moving the drain+disclosure to the non-stalled completion branch only,
where the ordering concern it protects against (a trailing container line
racing this function's own status prints) actually exists — the stalled
branch's own message prints to stderr and never races the pump's stdout
lines, so there was nothing to protect there in the first place. A new
assertion (the WARNING text must be ABSENT) was added to the existing
silent-lane stall test, red-first proven against the pre-fix code. Two
further round-2 findings also addressed: `_translate`'s wall-clock-
translation arithmetic was duplicated verbatim from `ProgressWatch`'s own
mtime-seeding line — extracted into a single shared `_seed_from_wall_clock`
helper both watchers now call, so a future correction cannot silently apply
to only one; and a new construction-level test
(`test_the_real_docker_logs_call_requests_timestamps`) pins that the real
`docker logs -f --timestamps` argv is actually issued for a watched command
lane — closing the blind spot where `TestLogStreamWatch`'s direct
`FakeContainerProc` construction would never notice the flag silently
dropped from `await_container`'s own `Popen` call. Two more round-2
findings (recomputing the wall-clock translation on every line rather than
once, like `ProgressWatch`; and `_split` trusting docker's `--timestamps`
prefix without verifying the flag is honored) were considered and
deliberately NOT changed: the per-line recompute is self-correcting against
a wall-clock step between lines (a translate-once design would instead
freeze whatever skew existed at its one seeding moment), and an
unrecognized `--timestamps` flag fails the whole `docker logs` invocation
loudly (Docker CLI flag parsing is strict, not silently ignored) rather
than producing the silent-misparse scenario the finding described.

A round-3 confirming review of the round-2 fix (folded into this SAME
unreleased rev) explicitly ACCEPTED the join()-relocation as correct —
independently reproduced the round-2 bug via mutation testing (reverting
only that hunk reproduces the exact spurious WARNING, doubled apostrophe
included) and confirmed the negative test and the new construction-level
`--timestamps` test are both real, not decorative — and confirmed both
"deliberately not fixed" round-2 items hold up independently. Its own
requested broader sweep surfaced real residual gaps, all addressed here:
(1) the stall branch never joined the pump thread even AFTER `finally`'s
`proc.terminate()`/`proc.wait()` had genuinely unblocked its read, leaving
thread lifecycle unsynchronized with this function's return — sharpest
under IN-PROCESS reuse (this project's own test suite calling `main()`
repeatedly in one interpreter, where a straggler thread can land a line in
the NEXT test's capture). `finally` now joins unconditionally (no
disclosure needed there — by that point the process really is gone).
Proven by tracking the CALL rather than thread liveness afterward: against
this fixture's own fast teardown the thread often finishes on its own
before anything checks, which the reviewer's own empirical check already
found (PLAUSIBLE, not reproduced live) — a call-tracking test is
deterministic where a liveness-diff test is not. (2) `LogStreamWatch._pump`'s
`except (OSError, ValueError): pass` also catches `UnicodeDecodeError` (a
`ValueError` subclass) from `text=True`'s STRICT decoding — a single
non-UTF-8 byte anywhere in a container's real output silently ends the
pump thread, freezing its liveness signal so a perfectly healthy lane
eventually reads as falsely stalled with nothing pointing at the real
cause. Fixed with `errors="replace"` on the `Popen` construction (never
fatal on something the container did, the same discipline
`ProgressWatch`'s torn-line handling already uses); proven against a REAL
subprocess (the `FakeContainerProc` unit tests bypass Python's
text-decoding machinery entirely, since their `stdout` is already an
in-memory string iterator). (3) `join()`'s own docstring now names its
CALLER PRECONDITION explicitly (only safe once the process feeding the
pipe is confirmed no longer producing output) — named for RG-46's own
future follower stall detection, a concrete next caller that cannot copy
`await_container`'s terminate-then-join structure (RW-14 forbids a
follower from `terminate()`ing a container it does not own) and would
otherwise have no way to discover the constraint from the class itself.
Two nits also fixed: a stale `__revision__` comment still claiming the two
watches "are never both armed" (the in-code comment already correctly
qualified this in round 1) now matches; the new construction-level test's
unused `capsys` fixture param (a copy-paste artifact) was removed, and the
stall test's WARNING-absence assertion now checks both stdout and stderr,
not stderr alone. One finding was assessed and left as pre-existing,
out of scope: `_seed_from_wall_clock`'s clamp only guards one direction of
clock skew (source clock ahead of this host) — a pre-existing property of
`ProgressWatch`'s own RW-27 mechanism this round only extracted into a
shared function, not introduced by it, and affecting both watchers
equally; not filed separately given how narrow and pre-existing it is.

A round-4 confirming review of the round-3 fix (folded into this SAME
unreleased rev), scoped tightly to the round-3 diff alone per its own
closing-round framing, ACCEPTED both fixes as correct (confirmed the
`finally`-join placement empirically via the same red-first method used
here, and confirmed `errors="replace"` cannot land a replacement character
inside the parsed timestamp portion in any way that produces a NEW failure
mode — a malformed prefix already falls back to arrival-time, the same
path a stray `U+FFFD` there would take). It found two non-blocking test
gaps and one optional latency note, all closed or triaged here: the new
non-UTF-8 test proved `LogStreamWatch` tolerates bad bytes when correctly
configured but never pinned that `await_container`'s REAL `Popen` call
actually passes `errors="replace"` (confirmed empirically: removing it
from the real call site left the full suite green) — closed with a new
construction-level test tracking the real `subprocess.Popen` call directly
(the same "construction proves construction" philosophy as the
`--timestamps` argv pin, applied to a kwarg `_docker_calls` cannot see),
red-first proven. A second, fresh instance of the exact "unused `capsys`"
copy-paste artifact this same rev's own commit message already claimed to
have cleaned up elsewhere was found and removed. The third finding — the
`finally` block's unconditional join can add ANOTHER bounded wait on the
non-stalled path if the first, disclosed join already failed to fully
drain — was assessed and left as-is: it only adds latency in an already-
disclosed, already-anomalous (real host contention) case, and giving the
thread MORE chance to finish before the function returns is the correct
direction for the concern this round's own fix exists to close.

## RG-44 — `GONE_SIGNALS` matches docker's "gone" stderr case-sensitively; this docker version emits lowercase and the container-truly-gone case is never recognized

### Observed mechanism and reproduction

`run-gate.py:1512` declares:

```python
GONE_SIGNALS = ("No such object", "No such container")
```

checked at `run-gate.py:1609` via `if any(signal in stderr for signal in
GONE_SIGNALS)` — a case-sensitive substring test. On this host's docker
version, `docker inspect <gone-name>` prints:

```
error: no such object: <name>
```

lowercase `no such object`, which never matches either `GONE_SIGNALS`
entry. Reproduced live (nyxloom-P103 post-merge verification,
2026-09-08): a `tester-unified` lane's client process was killed by the
harness's own low-memory guard mid-run (the container itself survived,
`docker run -d`); the operator manually `docker rm -f`'d the finished
container once its verdict had already been recorded. The next invocation
— including with `--fresh`, whose whole purpose is this exact case —
refused with the same message every time:

```
run-gate: docker inspect could not answer for container <name> (exit 1):
error: no such object: <name> — that is not a 'No such object' answer, so
run-gate will not read it as one: the inflight record is untouched and
nothing was recorded. Fix docker and re-run — if the container is still
running, the record can still find it
```

The error text quotes `GONE_SIGNALS[0]` verbatim ("No such object") right
next to the actual stderr it failed to match ("no such object") — the two
strings differ only in the leading letter's case, visible in the message
itself once you diff them character-by-character.

Workaround used: manually deleted the stale
`.run-gate/inflight/tester-unified.json` record (safe here only because
the container's actual verdict had already landed in
`.assay/verdict-tester-unified.json` before the record went stale) and
re-ran clean.

### Why this matters

This is exactly the class of bug RG-35/RG-39's own commit history (rev 34
onward) is full of hardening against: a stale inflight record silently
blocking every future invocation of a lane, on a host that runs ONE gate
container at a time by policy — a single false negative here wedges the
lane indefinitely for every client until someone finds and deletes the
record by hand, which is not something an unattended/automated caller can
safely do (deleting a record for a container that is NOT actually gone
would let two clients run concurrently, the exact hazard R-39e's owner-pid
check exists to prevent).

### Proposed contract

Make the gone-detection resilient to docker's own message casing rather
than depending on an exact string match against one docker version's
wording. Options, not mutually exclusive:
1. Case-fold the comparison (`signal.lower() in stderr.lower()`) — cheapest,
   but still brittle against a docker version that rewords the message
   entirely rather than just re-casing it.
2. Prefer `docker inspect`'s EXIT CODE plus a distinguishing marker docker
   guarantees more stably than its prose (if one exists across supported
   versions) over a hardcoded English string at all.
3. At minimum, log the exact docker CLI version this host reports
   alongside the mismatch, so a future occurrence is diagnosable without
   needing to reproduce the exact stderr by hand.

### Oracles

- A `docker inspect` stderr of `error: no such object: NAME` (this host's
  actual wording, lowercase) is recognized as GONE, not as an ambiguous
  failure — on both the plain re-attach path and `--fresh`.
- A genuinely ambiguous docker failure (daemon unreachable, permission
  denied) is still NOT treated as gone — the fix must not widen the match
  into false positives, which would reintroduce the R-39e concurrent-run
  hazard the strict check exists to prevent.
- Regression coverage should pin the exact docker version(s) this project's
  CI/gate containers actually run, not just assert against a hand-picked
  string literal that happens to satisfy today's code.

### SPEC ownership

`run-gate.py`'s `GONE_SIGNALS`/gone-detection logic (rev 35, RG-35/R-39e's
owner-pid hardening); this project's own `KNOWN_ISSUES_TODO_BACKLOG.md`.

### Provenance

Found during nyxloom-P103's post-merge gate verification, 2026-09-08 — not
nyxloom's own bug, filed here per estate cross-repo convention rather than
worked around locally and forgotten.

### Status — FIXED 2026-09-08 (run-gate-P05)

Case-folded the match: `stderr.lower()` compared against `signal.lower()`
for each of `GONE_SIGNALS`, both directions widened together (a future
docker version emitting either casing still matches; the phrase itself
must still appear, so this widens WHAT casing matches, never what
matches). Red-first proven with `test_a_gone_container_is_recognized_
regardless_of_docker_stderr_casing`, which overrides the shared fake-
docker shim's `inspect` branch with this host's exact reported wording
(`error: no such object: NAME`, lowercase) and asserts the same recovery
path (cleared, reported, lane runs fresh) the existing exact-case test
proves — not merely that the comparison returns true in isolation.
Reproduces the exact bug (wedged, exit 3, record untouched) without the
fix; passes with it.

## RG-45 — a vitest-backed lane can spuriously fail from vitest's own internal RPC heartbeat under host-wide multi-tenant CPU contention, independent of test correctness

### Observed mechanism

vitest 3.2.7's own internal worker/main RPC channel (`birpc`, the same
one every `[vitest-worker]: Timeout calling "..."` message names) has a
hardcoded `DEFAULT_TIMEOUT = 6e4` (60s, `vitest/dist/chunks/index.B521nVV-.js:3`)
with no path through any documented `vitest.config.ts` option for either
pool type — traced `createRuntimeRpc` (`rpc.-pEldfrD.js`) →
`worker.getRpcOptions(ctx)` (`worker.js`) → both `createThreadsRpcOptions`
and `createForksRpcOptions` (`utils.CAioKnHs.js`): neither sets a `timeout`
field, so the 60s default always applies. When a worker's periodic
`onTaskUpdate` progress-report call to the main/orchestrator process isn't
acknowledged within 60s, vitest throws this as an "Unhandled Error" and
`process.exitCode = 1` — **regardless of whether every actual test in the
run passed.** A `dangerouslyIgnoreUnhandledErrors` config option exists but
is a blanket, config-wide suppressor of ALL unhandled errors (including
genuine async application bugs — the same channel that surfaces a real
leaked-promise/re-entrant-callback defect), so a consuming project
disabling it to work around this specific benign case would also blind
itself to real future defects of the same shape.

### Reproduction (dstdns P176, 2026-09-08)

Reproduced 4/4 identical failures in one session against dstdns's
`[lanes.frontend-unit]` (`kind = "command"`, `npm ci && npm run typecheck
&& npx vitest run`) and `[lanes.ui_unit]` (`kind = "assay"`, coverage mode,
`npx --no-install vitest run --coverage`): 1 full `run-gate gate` composite
run + 3 standalone `frontend-unit` retries + 1 standalone `ui_unit`
attempt, **every single one** showing all real tests green (140/140 or
154/154 depending on merge state) with exactly the same 1-2 unhandled
`onTaskUpdate` timeout errors and a non-zero exit. This is not an
occasional flake under current host conditions — it was deterministic
across every attempt made.

Root-caused past the point of "the container's own CPU quota is too low"
(the package's own `vitest.config.ts` already caps `poolOptions.threads.
maxThreads` to match the container's real `cpu.max` cgroup quota — a
genuine, separate, measured improvement, but insufficient alone):

1. Snapshotted `/sys/fs/cgroup/cpu.stat` immediately before/after an
   isolated, failing single-file run (99.25s wall clock): `nr_throttled`
   and `throttled_usec` had **zero delta** — the container was never
   actually throttled by its own quota during this specific failure.
2. `node --cpu-prof`'d the actual test-execution process during a failing
   run: **99.3% of sampled time was `(idle)`** — the process was waiting
   on a pending RPC/timer callback, not CPU-bound computation.
3. Confirmed a concrete, independently-running sibling gate from a
   DIFFERENT repo (this repo's own `tester-unified` lane, via a `cmru
   release` invocation) active via `ps`/`pgrep` during one dstdns failure,
   coincident with a load-average spike to 13.15 on an 8-core host.

### Why run-gate (not vitest, not the consuming project) is the right layer

vitest's own timeout is out of any consumer's reach and unlikely to change
upstream on this project's timeline. The consuming project (dstdns) cannot
fix host-wide CPU oversubscription from inside a single package's
`vitest.config.ts` — it already did everything available to it (matching
its OWN container's thread pool to its OWN cgroup quota). What's missing is
coordination ABOVE the single-container level: RG-39's `acquire_exec_lock()`
already serializes access to the SAME resolved container (same
project/environment), so two lanes racing one container never contaminate
each other — but it has no notion of the SUM of CPU quotas across
DIFFERENT containers (different repos, different `project_name`s) exceeding
the host's real core count. Each container's own quota is individually
correct and individually respected (point 1 above proves this); the
oversubscription happens ACROSS containers, a dimension no single
container's `cpu.max` can see or bound.

### Candidate directions (not prescribing — flagging for judgment)

1. A lane-level "retry on this specific failure signature" policy,
   distinct from RG-33's existing mutation-lane `--resume` (that is a
   mutation-STATE concept; this is a plain process-exit-code retry): detect
   the `[vitest-worker]: Timeout calling` signature in a `kind = "command"`
   or `kind = "assay"` lane's stderr, and — ONLY when every reported test
   count shows 0 failures — offer a single automatic re-run rather than a
   hard fail, disclosed as a retry in the report (never silent).
2. A host-level (not per-repo) coordination primitive above RG-39's
   per-container exec lock: something that tracks the SUM of active gate
   containers' declared CPU quotas against the host's real core count
   across REPOS (dstdns, this repo, groop, nyxloom, ...), and either queues
   or warns before oversubscribing — RG-39's lock key is `resolve_container_
   name()`'s per-project identity, which is exactly the wrong granularity
   for this (it prevents same-container races, not cross-container
   oversubscription).
3. Nothing to fix in `run-gate` itself if the estate's operating model is
   "the operator manages host-wide concurrency by policy, not tooling" (the
   host is documented as intentionally shared, per dstdns's own "Host
   shared with prod game server" / D-338 3-core-per-gate-container cap) —
   in that case this entry's value is purely the disclosure + evidence
   trail for future triage, so a future occurrence is recognized instantly
   rather than re-diagnosed from scratch.

### SPEC ownership

Not yet assigned a SPEC section — this is a genuinely new dimension (cross-
container host CPU oversubscription) that RG-39/R-41 (same-container mutual
exclusion) and RG-36/R-40/RG-41 (assay-lane liveness/stall signals) do not
cover; a maintainer call on whether this belongs in SPEC §9's async-long-
lanes tracking or a new section.

### Provenance

`dstdns` P176 (`nyxloom-trove/reports/dstdns-P176-LOG.md`, entries dated
2026-09-08 "CPU-quota mismatch diagnosed and fixed..." and "First full
composite gate run..."), filed here per the estate cross-repo convention
(findings about a TOOL are filed in the tool's own backlog, never worked
around locally and forgotten).

### Note (dstdns controller, 2026-09-08, 5th reproduction) — reproduces under QUIET load too, weakening the pure heavy-contention framing

Deliberately re-ran the same composite `run-gate gate` at branch head
`ed76cd5b` under confirmed quiet-ish host conditions (load average
2.5–4.4 on this 8-core host, no sibling gate process observed via `ps`)
specifically to test whether the failure clears when contention is low —
it did not. **5th identical reproduction**: `frontend-unit` exit 1,
154/154 real tests passed, exactly 2 unhandled `onTaskUpdate` timeout
errors, same signature as all 4 prior attempts. This is meaningfully
different evidence from the first 4 (all coincident with or shortly after
heavier contention): a load average of ~4/8 with no named competing gate
process is not remotely the 13.15 spike that coincided with the earlier
failures, yet the failure reproduced anyway. Does not refute the
birpc-starvation mechanism outright (a lower-but-nonzero contention floor,
or some other periodic host activity below `ps`'s sampling resolution, is
still consistent with it) but does weaken candidate direction 3's
"nothing to fix, purely an operator-policy question" framing — the trigger
condition looks lower than "another repo's full gate actively running,"
which matters for how each candidate direction is prioritized. dstdns
disposition recorded as `decisions.md` D-404: proceeding to code review
with this gap disclosed rather than blocking on it further, since the
signature is proven independent of application/test correctness across
all 5 attempts.

### Disposition (2026-09-08) — not run-gate's to fix; moved to assay

Traced past the three candidate directions above: the actual defect is
assay's own R0 evaluation (`runner.py`'s `execute_command`, A-073), which
judges the wrapped target command by its raw exit code alone — the same
blind spot reproduces identically whether the lane is `kind = "command"`
(no judgment layer at all) or `kind = "assay"` (`[lanes.ui_unit]`
reproduced the identical failure), confirming this is not a run-gate-layer
gap. run-gate itself needs no change: assay's own exit-code contract
(`Outcome`/`EXIT_CODES`) is untouched by the proposed fix, so run-gate
keeps trusting it exactly as today, and that trust becomes correct once
assay's R0 stops trusting the wrapped target's bare exit code. Design doc:
`vbpub/assay/nyxloom-trove/R0-STRUCTURED-REPORT-DESIGN.md`; tracked as
assay B078. This entry stays OPEN here as the pointer/provenance record,
not because run-gate.py itself needs a fix.

### Status — CLOSED 2026-09-30 (resolved in Assay B078; no run-gate fix)

Assay B078 owns the defect: R0 now accepts a nonzero wrapped-command exit
only when the declared structured report proves the complete test run had no
failures. Its live Vitest `onTaskUpdate` reproduction and report-completeness
oracles are recorded in `assay/nyxloom-trove/reports/assay-WAVE-B078-REPORT.md`.
B078 is merged and included in released `assay-v7.1.1`. run-gate remains a
pass-through for Assay's verdict; it must not retry or suppress the Vitest
signature itself. This closes RG-45 as a run-gate responsibility while
preserving this row as the cross-project provenance pointer. Any later defect
in report parsing or completeness belongs in Assay.

## RG-47 — `run-gate.toml` resolves relative to the invoking process's CWD, not `--worktree`; a stale/diverged config from outside the target worktree silently wins

### Observed mechanism and reproduction

`run-gate <lane> --worktree PATH` scopes the JUDGED tree and the exec
target (which container/worktree the lane actually runs against) to
`PATH` — but it resolves its OWN `run-gate.toml` (lane definitions,
`[lanes.*.pins.assay]` versions, everything) relative to the INVOKING
PROCESS's current working directory, not to `--worktree`. The two are
silently assumed to agree, and normally do, because a worktree's
`run-gate.toml` is a checked-out copy of the same file at whatever
commit its branch is on — so in the common case CWD-config and
`--worktree`-config are byte-identical and the divergence is invisible.

Reproduced live (dstdns, 2026-09-08, during the assay 5.2.0 -> 6.1.0
pin bump on `main`): an ad-hoc `run-gate ui_unit --worktree
/workspaces/dstdns/.worktrees/p179-edge-typing --base ...`, run from
the MAIN checkout (CWD = `/workspaces/dstdns`), picked up **main's**
already-upgraded `run-gate.toml` (pinned to `assay-6.1.0.pyz`) instead
of the worktree's own still-5.2.0 copy, and failed looking for an
artifact that worktree never had:

```
sha256sum: tools/assay/assay-6.1.0.pyz.sha256: No such file or directory
```

(first symptom; a config edited to tolerate the missing pin instead
produces a spurious `NO_MEASUREMENT/EMPTY_COVERAGE` further downstream,
which is the more dangerous failure mode — it looks like a real gate
result, not a configuration error). `cd`-ing into the worktree before
the same invocation fixed it immediately: config and judged tree agree
again.

### Why this matters

This is the same *class* of bug as RG-24/RG-27..RG-31/RG-34 (a
sub-mechanism silently resolving from the repo/CWD instead of the
validated `--worktree` scope) — but for the config file itself, which
is more fundamental than any of those: every lane definition, every
pin version, every `[lanes.gate]` composite comes from it. Two
concrete hazards:
1. **Silent wrong-version pin.** A multi-package wave routinely has
   several worktrees each mid-migration on a tool-version bump (like
   this one); an ad-hoc invocation from the wrong CWD does not refuse
   or warn — it just judges against a DIFFERENT lane definition than
   the one committed on the target branch, and nothing in the output
   says so.
2. **It only ever surfaces when the two configs have actually
   diverged**, which is rare and momentary (the exact window a pin
   bump is in flight) — so a project can go a long time between
   reproductions and each one looks like an unrelated, unreproducible
   flake rather than a structural CWD-vs-`--worktree` gap.

### Resolution

With `--worktree W`, resolve the project directory as W plus the
project's path relative to the shared Git repository root. This keeps
monorepo projects scoped to their matching directory in W. Load the
project config and inherited root config from that selected tree. If
the project's `run-gate.toml` is missing, refuse rather than reading
the invoking checkout's config. Print the selected config path in the
run header and store that path and its SHA-256 in the history record.

The same worktree boundary applies to exec-mode runner selection:
`ciu.global.toml` is read only from the judged worktree. Missing config
or a stopped derived runner refuses with the instruction to start that
worktree's own test-runner. A declared literal `container_name` remains
an explicit shared-runner choice.

### Oracles

- Invoke from a checkout whose config differs from W's and prove a
  lane declared only in W is selected and run from W's config.
- Use a nested monorepo project and prove project and inherited root
  configs both come from the corresponding paths under W.
- Omit W's project config while main has one and prove run-gate refuses
  with W's missing path; it must not list or run main's lane.
- Prove the header names the selected config and the history record
  contains that path and the SHA-256 of the bytes parsed. Change only
  the root config and prove its own recorded path and digest follow W.
- With `--worktree W`, prove `ciu.global.toml` in W selects W's runner
  even when main declares another runner. If W's config is absent,
  refuse with the own-runner remedy and issue no `docker exec`; if its
  runner is stopped, use the same remedy before attempting exec.
- A declared literal `container_name` remains the explicit shared-runner
  exception.

### Status — FIXED 2026-10-04 (rev 47; RG-65 merged here)

The project and inherited configs now resolve from the selected
worktree, preserving a nested project's repository-relative path.
Missing project config refuses. The config path is printed and the
parsed config's SHA-256 and path are saved in run history. Exec mode
uses only the worktree's `ciu.global.toml`, and checks that its derived
runner is running before execution. Regression coverage exercises
worktree-only lanes, monorepo config inheritance, missing-config
refusal, recorded provenance, no main-runner fallback, and stopped
runner refusal.

RG-65's independent reproduction added an important oracle: a green
shared lane declared identically in main and W does not prove that a
worktree-only lane resolves correctly. That reproduction and its false
green wrapper report are merged into this issue; the wrapper's exit-0
was not attributed to run-gate itself.

### SPEC ownership

`run-gate.py`'s own `run-gate.toml` discovery/load path; this
project's `KNOWN_ISSUES_TODO_BACKLOG.md`.

### Provenance

Found during dstdns's assay 6.1.0 adoption (P176-P179 core-workflow
repair wave), 2026-09-08 — documented locally first as a standing
operational rule (`decisions.md` D-431 addendum: "cd into the target
worktree before any ad-hoc invocation from outside it"), filed here per
the estate's own cross-repo convention rather than left as a
workaround-only lesson.

---

## RG-48 — an environment that declares no `resources.cpus` leaves the lane's worker count and the operator's CPU cap to be decided in two places that contradict each other

> **ID note:** filed from the `nyxloom-P109` worktree, whose base predates
> main's RG-47. Confirmed at merge time (2026-09-09) that 48 was still free
> on `main` — no renumbering needed.

**Found by:** nyxloom-P109 gate runs, 2026-09-09.

`[environments.tester-unified]` in the monorepo-root `run-gate.root.toml` declares
`image` only — no `resources.cpus`. The gate container therefore starts
**CPU-uncapped**, restrained only by `$CGROUP_PARENT_DEV_GATES`
(`dev-gates.slice`), which deprioritises but does not bound it. Because
this host is shared with a live production game server, every agent prompt
carries a standing rule to run `docker update --cpus=3` on any container it
launches, immediately after launch. Meanwhile the lane's judged argv is
`pytest tests -n auto`, documented in nyxloom's `assay.toml` as "the measured
optimum on this 8-core host" — i.e. 8 workers.

So the two halves of the decision live in different files owned by different
people, and they disagree by 2.7x. Measured consequence on nyxloom at commit
`16c3e361`, same tree every time:

| container cap | outcome |
| --- | --- |
| `--cpus=3` | `FAIL / COMMAND_FAILED` — 2 failures in `tests/test_behavioral.py`, a file the branch under test did not touch |
| `--cpus=3` (retry) | `BUDGET_EXCEEDED / LANE_TIMEOUT` at `budget = "30m"`; container pinned at 295% of its 300% ceiling for the duration |
| `--cpus=6` | **PASS**, 9m24s |

The failing tests are bounded `for _ in range(20)` daemon-tick loops driving
real subprocesses; oversubscribed, they exhaust their tick budget before the
state they poll for lands. Controls at the identical commit: full suite serial
= 3932 passed; full suite `-n 4` = 0 failures; `test_behavioral.py` alone under
xdist = 14 passed.

**Addendum, same day, at `--cpus=6`.** Two further runs at a later commit on
the same branch, four minutes apart, both at the lane-matching cap:

| host load at start | outcome |
| --- | --- |
| ~6, rising to ~15 during the run | `FAIL / COMMAND_FAILED` — one failure, `test_behavioral.py::test_fake_approved_review_reaches_merge_ready` (a sibling of the two above). R1 passed at 100.0% |
| ~8 | **PASS**, 2m10s |

Two things follow. First, the cap is only half the problem: at the *correct*
cap the lane still goes red once unrelated work pushes the 8-core box past
~15, so declaring `resources.cpus` bounds the gate's own footprint but does
not make the lane robust against the rest of the host. The other half belongs
to the tests — bounded tick loops measure wall-clock progress with no bound on
what shares the machine. Second, note the wall times: **9m24s versus 2m10s at
the same cap on the same lane**, a 4x spread driven purely by ambient load,
which is also why `budget = "30m"` is not a meaningful timeout here.

The damage is not the slowness, it is that **the first failure mode is a
plausible-looking red gate on files the change never touched** — an implementer
who trusts it goes hunting a nonexistent regression, and one who doesn't trust
it has learned to discount red gates. (RG-45 is the neighbouring problem —
host-wide contention across *different* repos' containers — and was moved to
assay as B078. This one is narrower and entirely inside run-gate's own config:
a single repo's environment and lane contradicting each other with no
contention from anyone else required.)

**Possible shapes, not yet decided:** declare `resources.cpus` on the
environment so run-gate applies the cap itself and one file owns the number;
and/or let a lane declare the worker count it needs (or derive `-n` from the
environment's declared cpus) so `-n auto` cannot read the *host's* core count
through a cgroup that does not grant it. A cheaper interim: have run-gate warn
when a lane's argv contains `-n auto` while its environment declares no cpu
bound, since that is precisely the configuration whose behaviour depends on
what an operator does to the container out-of-band after launch.

### Status — FIXED 2026-09-12 (RG-55 wave, package P2)

Both halves this entry's own "possible shapes" named, landed together.
`resources.cpus` (lane `[lanes.<n>.resources]`, decimal string, docker's
own `--cpus` grammar) → a real `docker run --cpus <n>` on ephemeral
container lanes — the SAME argv position `--memory` already occupies. An
environment-level fallback, `[environments.<e>.resources] cpus = …` (the
ONLY resources key an environment accepts), so a project can own the
number in ONE place when every lane on an environment should share it,
without repeating it per lane; a lane's own value still wins when both
declare one. The cheaper interim ALSO landed, not instead: `doctor` warns
by name when a container lane's argv contains `-n auto`/`--workers auto`
and neither the lane nor its environment declares `cpus` — the exact
"configuration whose behaviour depends on what an operator does to the
container out-of-band" this entry named. Exec lanes get the pre-existing
naming-only WARNING (`R-29`'s rule — docker exec can neither place nor cap
work) rather than a refusal, extended to cover a `cpus`-only declaration
(the check already fired on any truthy `resources`, but had never been
proven against `cpus` alone before this landing). SPEC `R-29` amended.
This entry's OWN measured 2.7x-disagreement case (`-n auto` against an
uncapped environment) is exactly what a project now closes by declaring
`resources.cpus` on `[environments.tester-unified]` — the FIX is
available; whether nyxloom's own config adopts it is a separate,
per-consumer step this entry does not track.

## RG-49 — RG-38's `--state-dir` fix `mkdir -p`s against a root-owned synthetic parent in a Mode-B (partial-bind-mount) worktree container

**Found by:** dstdns-P175 implementer dispatch, 2026-09-09, worker-io
`worker-execution-admission` R1 lane, first attempt.

**Corroborated independently, 2026-09-11:** dstdns-P93's `run-gate gate`
composite hit the identical failure on its `assay` lane (`p93-xfail-inventory-burndown-42f8ce-test-runner`,
same "cannot create directory '/workspaces/dstdns/.run-gate'" message),
cascading `assay` → `gate` exit 1. Not a one-off — the same `docker exec -u root
mkdir -p /workspaces/dstdns/.run-gate && chown <uid>:<gid>` workaround
(top-level `.run-gate` only, not the full `assay-state/<path>` tree — the
app user can `mkdir -p` its own subdirs once the parent is writable) fixed
it a second time, in a different worktree/container. Two independent hits
in 3 days is enough to treat this as a standing Mode-B gate-reliability gap,
not an edge case — worth prioritizing over the `--resume` cache-keying gap
(B088) if only one gets picked up first.

### What's wrong

RG-38's shipped fix (`run-gate-P05`, above) unconditionally appends
`--state-dir <repo>/.run-gate/assay-state/<project-relative-path>/` to every
assay-kind lane's argv and `mkdir -p`s that directory in the same inner
script that already creates `.assay`. That assumes `<repo>` (here,
`/workspaces/dstdns`) is a real, writable-by-the-app-user directory inside
the container. It is not, for a **Mode-B isolated worktree**
(`nyxloom-trove/GUIDE.md` §3.4): that worktree's own dedicated
`test-runner` container (per `tools/test-runner/ciu.compose.yml`) bind-mounts
only `.worktrees/<branch>` and `.git`, both individually, at
`/workspaces/dstdns`. Docker has no directory to bind those two paths
*under* until it auto-creates the missing parent (`/workspaces/dstdns`
itself) inside the container's filesystem layer — and it creates that
synthetic parent `root:root 0755`. The container's actual runtime user is
correctly non-root (uid 1003 in this instance), so `mkdir -p
/workspaces/dstdns/.run-gate/...` fails outright:

```
mkdir: cannot create directory '/workspaces/dstdns/.run-gate': Permission denied
```

This is the same Docker behavior AGENTS.md §4.2a anti-pattern #2 already
names for the SOURCE side of a bind mount (a missing bind source silently
becomes an empty root-owned directory, ciu CIU-14) — RG-49 is the identical
mechanism hitting the DESTINATION/parent side of a *partial* multi-path bind
onto one container mountpoint, which RG-38's fix did not have in view
because its own acceptance criteria (two full-tree worktrees of one repo)
never exercises a partial bind.

**Worked around by the implementer, out of its own scope** (no tracked file
edited — not a fix, a pre-flight): `docker exec -u root` pre-created
`/workspaces/dstdns/.run-gate/assay-state/.worktrees/<branch>` and
`chown -R <uid>:<gid>` it before the first R1 attempt. That is a manual,
per-worktree, per-run step today; nothing in `ciu`'s Mode-B compose
generation or run-gate's `--state-dir` construction does it automatically.

### Status — FIXED 2026-10-04 (run-gate rev 50; originally filed 2026-09-09)

The preflight now runs in the lane's own environment before Assay. It checks
the durable root and deepest existing keyed-state ancestor as the lane user;
an unavailable root is NOT_RUN/`state-mount`, while an indeterminate probe is
an infrastructure ERROR. The registered `tester-unified` selftest passed
(`1530 passed, 1 skipped`; changed-line coverage `198/198` and branches
`86/86`). A fresh Dstdns worktree's own test-runner then passed its first
`assay` lane without manual setup (exit 0, 618.365 s).

**Recurrence 2026-10-03 (dstdns-P234, PRIORITY EVIDENCE — third independent hit).**
Installed run-gate `23.9.2.dev1126+g998a43552` (latest vbpub main at the time). A Mode-B worktree
instance (`p234-silo`, its own test-runner) failed the composite's `assay` lane with
`mkdir /workspaces/dstdns/.run-gate: Permission denied`; the implementer worked around it by creating
the directory as root inside that worktree's runner. This matters more now: dstdns is moving
toward worktree-owned test environments as the default (dstdns D-646, run-gate RG-73), which makes
Mode B the common path rather than the exception.

**Amendment 2026-10-04 (dstdns D-666; fourth hit; supersedes "Proposed fix" and extends "Acceptance").**

*Scope correction.* "Mode B" is no longer an isolated-worktree special case. Since dstdns D-666
(2026-10-03) a worktree is ALWAYS judged in its own test-runner (`ciu up --dir tools/test-runner`
inside the worktree; judging in main's runner is retired), and that runner mounts only the worktree
and the git common dir. Every freshly created worktree runner therefore fails every `kind = "assay"`
lane. The earlier passes only worked because somebody had created the directory as root by hand.

*Fourth hit (dstdns-P239 code review, 2026-10-03 23:25).* The P239 worktree runner was recreated at
23:22. `run-gate ui_unit --worktree <p239>` printed `mkdir: cannot create directory
'/workspaces/dstdns/.run-gate': Permission denied` and ended `lane 'ui_unit' exit 1`, while the lane's
`.assay/verdict-ui_unit.json` still held the PREVIOUS run's PASS (23:18). Inside both live worktree
runners (`p239-wallclock-*`, `p241-worktree-env-*`) `/workspaces/dstdns` is `root:root` and holds no
`.run-gate`.

*Second defect in the same symptom: the refusal is reported as a lane failure.* The `mkdir` fails
inside the lane's inner script, so run-gate reports exit 1, the code of a lane that ran and failed.
A reader (or wrapper) sees a red test lane, not a missing environment precondition. Together with
the stale verdict file left behind, this reads as "the tests failed" (cf. RG-72, RG-78).

*Why the earlier proposals are rejected.* The root-context `mkdir`/`chown` and
container-start `chown` proposals create the directory in the CONTAINER's
writable layer. The resume state then dies with the runner, which is exactly
the durability RG-38 was built for ("`repo` ... durable by construction even
when the judged worktree is not"). A root step also widens what every lane's
inner script runs as.

*Amended contract (dstdns D-666; replaces "Proposed fix").*
1. **Preflight, not mid-lane failure.** Before executing an assay-kind lane, run-gate probes, in the
   lane's own environment, that the durable state root exists and is writable, and that the deepest
   existing directory on the keyed state path is writable by the lane user. If not,
   it refuses before execution with NOT_RUN 3/`state-mount` (RG-78). The message names the path, the
   container and the remedy: "mount `<repo>/.run-gate` into
   this environment, or declare `state_root`". `--dry-run` shows the probe.
2. **The state root is declarable.** `[environments.<e>] state_root = "<container path>"`, default
   `<repo>/.run-gate` (today's RG-38 value, unchanged for full-tree mounts). A consumer whose runner
   mounts the durable state elsewhere names it, and run-gate never guesses or invents a directory.
3. **The durable directory belongs to the consumer's environment.** run-gate does not create mounts
   for exec environments. It documents that an exec runner must mount `<repo>/.run-gate` (or the
   declared root) read-write, and `doctor` checks it per environment (R-37 per-tree scope).

*Consumer decision and live acceptance (dstdns D-666).* A fresh CIU-managed
worktree (`run-gate-rg49-live-20261004`) mounted main's `<repo>/.run-gate`
read-write into its own `test-runner`. The first `assay` lane passed and wrote
the keyed state directory without manual in-container setup. Dstdns keeps this
mount as its required durable root: it matches run-gate's default
`state_root` path, and the per-worktree key keeps resume state isolated while
the common root survives runner recreation. Dstdns documents the required
mount and removed the obsolete workaround/shortcoming label in
`dstdns@305d8519`. The `docker exec -u root mkdir` pre-flights used in
P175/P93/P234/P239 remain testing-only, not the workflow.

*Acceptance (replaces the superseded pre-amendment list).*
- [x] A fresh Dstdns worktree runner with the durable root mounted read-write
      ran its first assay-kind lane without manual setup; run-gate reported
      `/workspaces/dstdns/.run-gate/assay-state/.worktrees/run-gate-rg49-live-20261004`
      as its state directory and the lane passed (exit 0).
- [x] Unavailable or unwritable roots/ancestors refuse before Assay as NOT_RUN
      3/`state-mount`, without creating or chowning the root or descendants.
      Oracles: `test_state_root_probe_is_read_only_and_checks_directory_and_write_access`,
      `test_state_root_probe_does_not_create_missing_root_or_state_descendants`,
      `test_missing_state_root_under_synthetic_readonly_repo_refuses_without_mkdir`,
      and `test_state_root_preflight_refuses_before_assay_and_uses_exact_runner`.
- [x] Refusals name the environment, container-visible path, and mount remedy;
      `doctor` reports once per assay environment. Oracles:
      `test_state_mount_refusal_names_ephemeral_or_host_runner` and
      `test_doctor_checks_state_root_once_per_assay_environment`.
- [x] A declared `state_root` replaces the default mount root in state-dir argv,
      and `--dry-run` discloses its probe and resulting path. Oracles:
      `test_declared_state_root_replaces_only_the_mount_root` and
      `test_state_root_dry_run_discloses_probe_without_running_it`.
- [x] The durability oracle recreates the runner while preserving the declared
      mount and observes resume state; its container-layer control loses state
      after recreation: `test_resume_state_survives_runner_recreation_not_container_layer`.
- [x] RG-38's two-full-tree-worktree behavior remains covered by the registered
      selftest; all 1530 tests passed, with 1 skipped.

---

## RG-50 — `ProgressWatch._rate_per_min`'s B065 own-clock branch divides by an index of 0 (the first real candidate) and produces a nonsensical negative rate for assay's own `-1` baseline sentinel

A native R2 mutation lane's `candidate_index` is 0-based, so the FIRST
candidate to finish carries `candidate_index: 0`. B065's own-clock branch
(`index / elapsed * 60.0`, gated only on `elapsed > 0`) evaluated this as a
literal `0.0`, not "nothing measured yet" — and `_report`'s ETA line
(`(total - index) / rate`) then divided by that zero and crashed the whole
gate run with an unhandled `ZeroDivisionError`, killing the run BEFORE any
verdict was produced (the underlying assay container itself had already
completed candidate 0 cleanly; only run-gate's own client-side progress
print crashed):

```
File "run-gate.py", line 3345, in _report
    print(f"{head}, {rate:.1f}/min, ETA {(total - index) / rate:.0f}m", ...)
ZeroDivisionError: division by zero
```

A related, non-crashing cosmetic defect from the SAME unguarded branch:
assay's progress schema emits a `"baseline"` event (the pre-sweep full-suite
run, proving the lane is green before any mutant) carrying `candidate_index:
-1` as a sentinel, not a real candidate. Un-guarded, this produced lines
like `candidate -1/81, -3.3/min, ETA -25m` — negative rate and ETA, silently
wrong rather than caught.

### Provenance

Found running the real `session-extract` mutation lane in nyxloom (a vbpub
sibling project) while verifying an unrelated fix, 2026-09-10 — not
nyxloom's own bug, filed here per estate cross-repo convention.

### Status — FIXED 2026-09-10

`_rate_per_min`'s B065 branch now also requires `index > 0` before treating
`index / elapsed` as a real measurement; `index <= 0` (the first real
candidate at 0, or assay's `-1` baseline sentinel) falls through to the
existing `None` path, which `_report` already renders as the bare
`candidate N/M` count with no rate/ETA — the same degrade-gracefully shape
already used for "no clock yet" and "the file moved but the candidate
didn't." Two regression tests added to `TestProgressWatch`
(`test_the_first_real_candidate_at_index_zero_yields_no_rate_not_a_crash`,
`test_a_baseline_sentinel_at_index_negative_one_yields_no_rate`); full
suite green (668 passed, 3 pre-existing unrelated skips).

**Correction to this entry's first cut**: every in-monorepo consumer's own
`run-gate.py` (nyxloom, assay, ciu, topos, pwmcp, cmru,
plesk-mailbox-create, shared-ramdisk-depot-manager,
modern-debian-tools-python-debug) is in fact a real SYMLINK to
`../run-gate-project/run-gate.py` (`ls -la` confirmed on several), not an
independent copy — this file's own header note about vendored copies
needing re-sync describes consumers OUTSIDE this monorepo (dstdns and
beyond), which this fix does not reach automatically. Every in-repo
consumer already sees this fix the moment this commit lands; nothing to
re-sync there. (An earlier draft of this paragraph incorrectly `cp`'d over
one of these symlinks' target file directly, momentarily dirtying an
unrelated worktree's git state with a duplicate of this same fix — caught
and reverted before it was ever committed anywhere.)

## RG-51 — a delegating lane's DEFAULT comparison base (`--base` omitted) is `merge-base HEAD @{upstream}`, the identical stale-origin hazard `judge.base` literals have, just relocated into run-gate itself

`resolve_comparison_base` (run-gate.py:2629) is explicit that a lane
declaring `judge.base_source = "request"` and invoked WITHOUT `--base`
falls back to `derive_upstream_base(worktree)` — `merge-base HEAD
@{upstream}` — rather than refusing. The docstring frames this as
deliberate ("otherwise the judged tree's own merge-base with its
upstream. No fallback to HEAD or to a default branch name"), treating
`@{upstream}` as a legitimate default rather than a guess.

That's only true while `@{upstream}` (typically a remote-tracking ref
like `origin/main`) stays in sync with local work. Under any workflow
that batches commits locally before pushing — this estate's own standing
"not pushed yet, flagged separately" policy, a long review cycle, a slow
CI queue — `@{upstream}` drifts behind without anyone touching a single
line of config, and the DEFAULT path silently reproduces the exact
`judge.base = "origin/main"` staleness hazard (see assay's own README,
"Pitfall: a `judge.base` literal pointing at a remote-tracking ref rots
silently", added 2026-09-11) — just one layer removed, inside run-gate's
own fallback instead of a project's `assay.toml`. A lane author who
migrates to `base_source = "request"` specifically to escape that
pitfall gets no protection unless every real invocation remembers to
pass `--base` explicitly, every time, forever.

### Provenance

Found 2026-09-11 investigating why nyxloom's and ciu's `tester-unified`/
`ciu` lanes' `judge.base = "origin/main"` had gone stale (85+ commits)
under this exact policy — operator asked why a worktree/feature-branch
gate run couldn't just compare against the local branch it forked from,
which led to reading `resolve_base`'s merge-base semantics and this
fallback path. Not itself the cause of that incident (neither lane
declared `base_source = "request"`), but the same root hazard, one layer
down, and next in line to bite the first lane that migrates to request-
delegation without also building an always-inject-`--base` wrapper
(dstdns's `scripts/gate-base.sh` is the one working precedent for this —
not itself vbpub's to adopt wholesale, but the shape of what closes this
gap).

### Status — FIXED 2026-09-11 (rev 40), third direction, with ciu CIU-106

Round 3's residual is closed. It could not be closed inside run-gate
alone: reconstructing a fork point from `base_ref` is impossible once the
base has absorbed the branch, so **ciu CIU-106** now records
`fork_point_sha` at `worktree create` time and run-gate requires
`merge-base(base_ref, HEAD)` to still EQUAL it.

Why equality rather than the "refuse when merge-base is a strict
descendant of the fork" this entry originally proposed: a base that gains
UNRELATED commits leaves `merge-base` exactly where it was, so equality
already admits the healthy long-lived worktree, and any inequality — in
either direction — means shared history moved and the record is spent.
Simpler, and with no ordering assumption to get wrong.

Equality does NOT subsume the "base already contains HEAD" clause, which
was checked against the real function rather than derived: a worktree
that has committed nothing of its own has `fork == merge-base == HEAD`,
which passes equality and would judge zero lines. Both clauses ship.

run-gate also now hands the judge the verified fork COMMIT rather than
the branch name, which closes round 3's remaining SHOULD-FIX (a
re-resolution window between run-gate's check and the judge's own
`merge-base` minutes later).

### What was built (third direction)

The third candidate below turned out to need no new plumbing at all: the
provenance IS recorded today. `ciu worktree create|add` writes
`ciu.worktree-instance.json` at the managed worktree's CIU root, and its
top-level `base_ref` string is the exact `--base` ref the worktree was
forked from (default `main`); `ciu worktree adopt` writes the adopted
checkout's HEAD into the same field.

`resolve_comparison_base` now consults that record BETWEEN the winning
`--base` flag and the unchanged `@{upstream}` fallback:

- Read as a FILE FORMAT — stdlib `json`, well-known filename — never a
  Python import from `ciu`. The two are separate projects and this
  launcher must run on a fresh clone with zero installs.
- Both the judged worktree root and the effective project dir inside it
  are checked, in that order: ciu writes the record at the CIU root,
  "which can be below the Git worktree root in a monorepo" — i.e. at
  `<worktree>/<project>/`, the shape every vbpub consumer has.
- **The recorded ref is used only when it is gate-safe ref text, the
  record's `branch` still matches the tree, git resolves it to a LOCAL
  branch there, `merge-base(base, HEAD)` still equals the recorded
  `fork_point_sha`, that branch does NOT already contain the tree's
  HEAD, and the fork point and HEAD do not have the same TREE.** See
  "Review findings" below — each clause closes a way the record would
  have been WORSE than the `@{upstream}` it displaces, and three of them
  were false-greens found only by review.
- **What is handed downstream is the verified fork COMMIT**, not the
  branch name: equality has just proven the two resolve to the same
  object, and an OID cannot be moved by a concurrent session in the
  window between this check and the judge's own `merge-base`.
- Every failure mode degrades to the pre-existing `@{upstream}` path:
  absent, unreadable, permission-denied, a non-regular file (a FIFO
  would block the read forever), not JSON, not an object,
  `RecursionError` from deeply nested JSON (a `RuntimeError`, NOT a
  `ValueError`), missing/non-string/blank `base_ref`, and every
  rejection above. A better default, never a requirement — a malformed
  record must not abort a gate run.
- `--base` still wins outright, and the primary checkout is bit-for-bit
  unchanged (`adopt` structurally refuses the primary worktree, and the
  record is git-excluded, so it can never arrive there by checkout).
- Disclosure runs BOTH ways, because half of this entry was that
  staleness must be visible: a winning record is named together with
  the `@{upstream}` ref it displaced (`run-gate: <record> pins this
  tree's fork point at <sha> (its base_ref still resolves there) —
  using that COMMIT instead of merge-base HEAD @{upstream} (<sha>)`,
  plus `(from ciu.worktree-instance.json fork point)` on the
  comparison-base line), and a record that is found and REJECTED prints
  `run-gate: ignoring <record>: <why>`. A silently ignored record would
  be the same silence this entry objects to, in a new place.
- One deliberate widening: a tree with a USABLE record but NO upstream
  now resolves instead of refusing. Nothing is guessed — the ref is one
  ciu wrote down at creation, git has just resolved it to a live
  branch, and its merge-base with HEAD has been proven unmoved since.
  Only the base STRING changes here; what the judge does with it is the
  judge's own contract (see RG-54 for where that contract is weaker
  than this rule).
- **Usability cliff, accepted knowingly:** merging the base INTO the
  branch, or rebasing onto it, moves the merge-base and therefore spends
  the record — the feature goes inert (falls back to `@{upstream}`)
  exactly where an operator was keeping a long-lived worktree current.
  The fallback errs WIDE, so this is a loss of benefit, not a new
  hazard; distinguishing it from a base that absorbed the branch needs
  another recorded fact, one level up in ciu again. Documented in
  CONSUMERS.md rather than worked around.

### Review findings — all THREE cuts of this fix were false-greens

Three independent adversarial review rounds, each a fresh agent, each
finding one BLOCKING defect IN THE FIX. All three were in the same
direction — narrowing the judged diff, the direction that PASSES — and
none was visible from the tests the previous cut shipped with. Each fix
moved the collapse one step further out rather than removing it.

The lesson worth carrying: for a change that picks a comparison BASE,
"does it produce an answer" is not the property to test; "does the
answer still leave the branch's real work to judge" is. And when three
successive rounds find the same class, the premise is the suspect, not
the predicate.

#### Round 5 — reviewing round 4's own fixes; no fifth false-green, two real defects

A fresh reviewer, spanning both projects, took round 4's fixes as its
surface — the classic place a regression hides. It swept every path
reaching `_finish_allocation` (four interrupt points, reproduced against
real temp repos), confirmed no create path leaves the field permanently
absent and no path captures without the reset, and ran 15 graph shapes
(criss-cross, octopus, sibling-merge, base rewound, HEAD rewound,
ambiguous tag/branch, `branch: null`, detached HEAD, …) through the real
function with an oracle asserting no branch-unique commit is an ancestor
of the returned base. Every acceptance held. **No fifth false green** —
and the structural reason is worth writing down: the function returns the
verified merge-base, which by construction cannot contain a commit unique
to the branch, so an accepted record is exactly `--base <base_ref>`
evaluated at check time and pinned to an OID. It can never be NARROWER.

Two real defects, both reproduced before being accepted:

- **A graph clause is not the same question as "is there work".** Every
  clause here asks the commit graph something. A branch that committed
  work and then REVERTED it has a real fork point, an unmoved merge-base
  and ancestry in neither direction — all clauses pass — and still
  produces an EMPTY diff, which `tools/coverage_gate.py` scores as
  `0/0 = 100%`. Nothing ESCAPES the judge (there is none to escape), so
  this is not the false-green class the other clauses exist for; it is
  another inlet into RG-53. Closed with `git diff --quiet <fork> HEAD`,
  fail-closed, for the cost of falling back to a base that has something
  to judge. It logically subsumes the containment clause (containment
  implies identical trees, never the converse — checked against the real
  functions, not derived); containment is kept, and kept first, because it
  names a far more common cause and answers from the graph.
- **ciu `adopt()` could leave a record that destroys work on resume.** It
  wrote the instance record and then called `_write_worktree_overlay`
  unguarded, while `ensure()` reads `recovery_status in (None,
  "checkout-incomplete")` as "needs a checkout" — so a failed overlay
  write left a record whose resume ran `git reset --hard <the adopted
  HEAD>` in the operator's own worktree, discarding every commit made
  there since, and stamped a `fork_point_sha` onto an adopt-shaped record
  the docs promise never carries one. Pre-existing in its destructive
  half; CIU-106 made the second half visible. Wrapped and marked
  (red-proven: pre-fix, the resume really does lose the commit). The
  structural half no `try`/`except` can reach — a SIGKILL between the
  record write and the marker — is filed as ciu **CIU-107**.

Also corrected from the same round: stale quotes of the pre-round-3
disclosure message in this entry and in the `rev 40` comment; a docstring
still saying RG-52 "is NOT fixed here"; a CONSUMERS.md claim that a
recorded ref "self-updates" and that assay applies merge-base to whatever
it is handed (both false since round 3 and RG-54 respectively); clause
numbering that ran 1,2,3,5,4,6; and clause 2's rationale, which is now
defence-in-depth (the function returns a COMMIT, so a recorded string can
no longer reach shell text) plus two live concerns of its own.

#### Round 4 — no fourth false-green; two real SHOULD-FIXes, both reproduced

The first round to clear. A fresh reviewer spanning both projects
derived the four-clause chain independently, then mutation-tested it
(10 run-gate mutants, 11 ciu mutants) and confirmed 0 missing lines and
0 missing branches in both new regions. No BLOCKING defect.

Two SHOULD-FIXes, each reproduced against the real code before being
accepted:

- **The fork point was captured in the wrong place.** `create()`
  resolved `git rev-parse <base>` in `repo_root` right after
  `git worktree add --no-checkout`. But it is the later
  `git reset --hard record.base_ref` inside `_finish_allocation` that
  actually sets the new branch's tip — and it re-resolves `base_ref`
  then. A commit landing on `main` in between (another session; this
  estate runs several) makes the recorded SHA differ from the tip the
  worktree really forked from, and run-gate's equality clause then
  refuses a perfectly healthy record. Capture moved into
  `_finish_allocation`, immediately after that reset, reading the
  worktree's own `HEAD^{commit}`. Regression test races a real commit
  onto `main` from inside a patched `_write_worktree_overlay`.
- **The "has MOVED" refusal asserted a cause that may not have
  happened.** The same inequality is produced by the base absorbing the
  branch, by the branch merging the base in, by a rebase, or by a record
  describing a different tree. Telling an operator to tear down a
  worktree for a reason that did not occur is its own defect; the
  message now enumerates the possibilities instead.

#### Round 3 — the guard is exact-containment only; ONE follow-up commit re-opens it

`git merge-base --is-ancestor HEAD <base>` answers "is the diff empty
RIGHT NOW", not "does this base still leave the branch's work to judge".
It fires only at the instant `merge-base(base, HEAD) == HEAD`. Add one
commit to a merged-but-not-torn-down worktree and the guard reports
"genuinely divergent — usable", while `merge-base` sits on the PRE-MERGE
BRANCH TIP: everything the branch did before the merge silently leaves
the changed-line set.

Reproduced independently against the real `recorded_worktree_base`
(branch off `main`, 3 lines of work, `--no-ff` merge into `main`, then
one follow-up commit):

```
STATE 1 (merged, nothing new)      -> rejected, "already contains this tree's HEAD"
STATE 2 (merged + ONE more commit) -> ACCEPTED 'main', no warning
   merge-base(main, HEAD) = the old branch tip, not the fork
   judged diff for the branch's own file vs recorded base : (empty)
   judged diff for the branch's own file vs TRUE fork     : 3 insertions
```

Reachability is not hypothetical: three of the seven real ciu worktrees
here (`hypothesis-followups`, `mattermost-stale-test-fix`,
`render-qa-prefix-fix`) are merged-and-not-torn-down with
`base_ref = "main"` and zero commits since — i.e. each is exactly ONE
commit away from state 2, with 5-9 files of branch work that would
vanish. Adding a follow-up commit is the natural next act in a worktree
you deliberately kept. Neither consumer catches it: assay's
`check_base_is_head` only refuses when the resolved base EQUALS HEAD,
and both assay and `tools/coverage_gate.py` score a zero denominator as
100% (RG-53).

**Why this is not fixable with another predicate.** In the
fast-forward-merge case there is NO local signal distinguishing "the
base absorbed my work" from "I forked here": `merge-base` is the maximal
commit shared by base and HEAD in both situations, and ciu's record does
not carry the fork commit. A rule that separates them needs a fact that
is not written down today.

**What made this sound — DONE.** `ciu worktree create|add` knows the fork
commit at creation and now records its OID alongside `base_ref`
(**CIU-106**, implemented in the same change as this fix). run-gate
requires `merge-base(base_ref, HEAD)` to still EQUAL it — equality, not
the "strict descendant" test this paragraph originally proposed, because
a base gaining UNRELATED commits leaves `merge-base` unmoved and so
equality already admits the healthy case with no ordering assumption to
get wrong. Fail-closed on a missing or malformed `fork_point_sha`
(`adopt` never records one, nor did any ciu before CIU-106), so an
existing worktree keeps its old `@{upstream}` behaviour and SAYS so until
it is recreated.

**Still worth doing, and not implemented:** disclose the resulting RANGE
(`… → N commits / M files to judge`) so a near-empty judgment is visible
rather than merely refused. Every collapse this entry describes is now
REFUSED with a reason, so this is no longer a correctness gap — but it
would have made all three rounds' defects self-evident at the first real
run, which is worth something on its own.

Round 3 also found, and these ARE fixed on the branch: the FIFO alarm
guard sat on the test that no longer reaches `read_instance_record`'s
`is_file()` check rather than the one that does, so a regression hung
instead of failing; four documents claimed clause 5 subsumes the
frozen-SHA `adopt` case when in fact clause 4 catches it (the same
false-security-property pattern round 2 found in five other places); a
leading `-` was inside the recorded-ref charset; and the option-like-ref
test attributed its refusal to `--end-of-options` when `--verify` is
what actually refuses. Round 3's remaining SHOULD-FIX is
also closed: run-gate hands the judge the verified fork COMMIT rather
than a mutable branch name, so there is no re-resolution window between
its check and the judge's own `merge-base` minutes later, and a
no-common-history base now falls back here instead of hard-erroring in
both downstream consumers.

#### Round 2 — a live branch is NECESSARY but NOT SUFFICIENT

The round-1 fix accepted any `base_ref` resolving to a branch other than
the tree's own. That still collapses whenever **HEAD is an ancestor of
the base**: a worktree whose work has been merged into `main` (or fast-
forwarded onto it) and not torn down has a perfectly real `base_ref =
"main"` that local `main` has since absorbed, so `merge-base(main,
HEAD) == HEAD` and there is nothing left to judge. This was not
hypothetical — **4 of the 7 real ciu worktrees in this estate were in
exactly that state** when the check was written (`hypothesis-followups`,
`mattermost-stale-test-fix`, `render-qa-prefix-fix`,
`session-extract-gate`), i.e. the majority.

Downstream an assay lane would hit assay's own `BASE_IS_HEAD` refusal
(`measurability.py`) three layers down, naming neither the record nor
the remedy; a `kind = "command"` lane has NO such guard, and
`tools/coverage_gate.py` scores zero changed lines as `0/0 = 100%` —
a silent pass (see RG-53). Fixed with `base_already_contains_head`
(`git merge-base --is-ancestor HEAD <base>`, FAIL-CLOSED on any error),
which also subsumes the round-1 own-branch and literal-`HEAD` cases.
dstdns's `scripts/gate-base.sh` case 3 is the estate's prior art for
this exact refusal.

Same round, also fixed: `current_branch_ref` had no `try/except` at all,
so a non-UTF-8 branch name (git permits one) or a missing `git` raised
straight out of it and aborted the gate run — breaking the "nothing here
raises" contract the code and SPEC both asserted; `refs/remotes/` was
accepted, which contradicted the entry's own rationale (a remote-tracking
ref is precisely what RG-51 exists to stop defaulting to) and is now
refused; the "requiring git to resolve it disposes of every injection
hazard" claim was FALSE — git ref names legally permit `;`, backticks,
`$` and more, and `git branch 'main;touch$IFS/tmp/PWNED'` is accepted and
executes through `{base}`, so the reader now applies a conservative
allow-list before asking git and the pre-existing hole is filed as
**RG-52**; and the "report the FIRST rejection" branch was the one
uncovered branch in the change, which the selftest lane could not see —
filed as **RG-53**.

#### Round 1 — the first cut was a false-green

Independent adversarial review of the first implementation (which used
`base_ref` verbatim whenever it was a non-blank string) found a BLOCKING
defect, reproduced from a scratch repo:

`ciu worktree adopt` writes `base_ref = git rev-parse HEAD` of the
ADOPTED CHECKOUT — the tip of the work already on that branch, not a
fork point (`ciu/src/ciu/worktree.py`, `adopt()`). Because that commit
is an ANCESTOR of HEAD, `merge-base(base_ref, HEAD)` resolves to the
commit itself, so every line committed BEFORE the adopt leaves the
changed-line set. Branch off `main`(A), commit B and C, adopt at C,
commit D: the old path judged 3 changed lines, the first cut judged 1 —
and running the gate immediately after the adopt makes the base HEAD
itself, i.e. **zero changed lines and a trivially passing lane**. The
recorded SHA is always a descendant of (or equal to) the old fork
point, so the judged set was always narrower or equal, never wider:
strictly the false-green direction, and strictly worse than the
`@{upstream}` it displaced. The backlog's own "a frozen SHA … is at
best a stopgap, not a fix" applied to the fix itself.

Fixed by requiring git to resolve `base_ref` to a branch, which also
disposes of a tag or deleted/renamed ref (frozen or absent the same
way) and the literal `HEAD`. Also fixed from the same review:
`RecursionError` escaping the `(OSError, ValueError)` guard on deeply
nested JSON, a FIFO at the record path blocking `read_text` forever
with nothing disclosed, a stale record from a REUSED worktree (`git
checkout -B other origin/release-1` leaves a record naming the first
task's base) now refused on the `branch` cross-check ciu's own reader
already performs, and the silent-rejection gap above. Round 2 then
found that "a branch" was still not enough.

The other two directions stay unimplemented and are NOT superseded; they
address a case this fix does not (a non-ciu worktree, or a plain checkout
whose `@{upstream}` has rotted):

- Warn loudly (stderr, not just the existing `run-gate: comparison base
  {ref} (from {source})` line) when the DEFAULT path fires and the
  resolved base is more than some threshold of commits behind the
  branch's own local upstream-of-record (e.g. `main`), so staleness is
  visible the moment it happens rather than discovered later via an
  unrelated `EXCLUDED_LINES` failure.
- Or: require an explicit `--base` for every `base_source = "request"`
  lane (remove the silent default entirely) — matches assay's own stated
  philosophy ("a changed-line judgment whose base was guessed is not a
  changed-line judgment") more literally, at the cost of breaking any
  existing invocation that currently relies on the implicit
  `@{upstream}` fallback.
- Or: default to the worktree's own creating branch's fork point (`ciu
  worktree add --base <ref>`'s own `<ref>`, if recoverable) instead of
  `@{upstream}` — closer to "what this worktree actually forked from"
  without needing a per-invocation flag, but needs that provenance to
  actually be recorded and recoverable at gate time, which is not
  confirmed to exist today. **← taken; the provenance does exist.**

## RG-52 — a comparison base is substituted into a conjunction lane's inner `bash -c` as UNQUOTED SHELL TEXT

A `kind = "command"` lane declares base propagation with a `{base}`
token in its own argv (`R-25`/`R-35`), e.g.

```toml
argv = ["bash", "-c", "./run-gate.py --base {base} cursor && ./run-gate.py unit"]
```

`substitute_worktree` replaces the token inside that STRING, and
`build_command_inner`'s `shlex.join` then quotes the whole element for
the OUTER shell — but the element **is** the script the inner `bash -c`
re-parses, so metacharacters inside the substituted ref are interpreted
there. A base of `main;touch /tmp/PWNED` yields

```
bash -c './run-gate.py --base main;touch /tmp/PWNED cursor && ...'
```

and on a `bare-host` lane that executes on the real host. Demonstrated
against the real functions during RG-51's round-2 review, with both a
`;` and a backtick payload.

### Provenance and scope

Predates RG-51: the reachable source today is `--base` on the operator's
own command line (RG-26), which is low-severity — an operator who types a
metacharacter into their own flag has other ways to run the same command.
RG-51 was reviewed specifically for whether it WIDENS this, because a
`ciu.worktree-instance.json` is a git-excluded file `git status` never
shows and could travel with a copied worktree. It does not: RG-51's
reader applies a conservative allow-list (`GATE_SAFE_BASE_RE`,
`[A-Za-z0-9._/+@-]`) to the recorded ref BEFORE git is asked, and a
branch legally named `main;touch$IFS/tmp/PWNED` — git accepts that name —
is refused with `is not gate-safe ref text`. `check_worktree_charset`
(RG-5) is the same precedent for `--worktree` paths; the fix below makes
that one regex serve the `--base` path too.

### Status — FIXED 2026-09-11 (rev 40), first direction taken

`check_base_charset` refuses any comparison base outside
`GATE_SAFE_BASE_RE` (`[A-Za-z0-9._/+@]` then `[A-Za-z0-9._/+@-]*`) before
it can reach `substitute_worktree`, `{base}`, `--request-base` or any
inner shell. It is the exact counterpart of `check_worktree_charset`
(`R-5`) for the other value consumer pointers embed into shell strings,
and it is deliberately NARROWER than git's own ref grammar: git accepts
`;`, backticks, `$`, `|`, `&` and quotes in a ref name, this gate does
not.

Two asymmetries, both deliberate:
- **`--base` REFUSES (exit 2); a `ciu.worktree-instance.json` `base_ref`
  DEGRADES** to the `@{upstream}` fallback with a disclosure line. An
  operator's explicit argument that cannot be honoured safely is a
  refusal; a file must never be able to abort a gate run (RG-51).
  Both share the one regex, so the two paths cannot drift apart.
- **A leading `-` is refused on POSITION grounds**, with its own
  message: `-` is legal later in a ref, so listing it as an "offending
  character" would misdescribe the problem. `R-5` makes the identical
  distinction for `{worktree}`, and the hazard is the same — a
  sub-invoked `./run-gate.py --base -weird` parses it as an option.

Why this direction over the other candidate (substituting with
`shlex.quote` when the token appears inside a `bash -c`-style element):
"which argv element is a script" is not something run-gate can know in
general — a lane author can put `{base}` inside single quotes, inside a
heredoc, or in an element that is never re-parsed at all — so quoting
would be right in some lanes and visibly wrong in others. The charset
refusal is decidable without knowing the lane's shape, costs nothing at
runtime, and matches a precedent this project already ships. The
narrower rule also means no real base shape is affected: branch names,
remote-tracking refs, tags, slashed and dotted names and raw SHAs all
pass unchanged.

Not closed by this, and deliberately out of scope: a lane author can
still write a `{base}`-carrying argv whose quoting is wrong for some
OTHER reason. The charset guarantees the substituted VALUE is inert, not
that the surrounding script is well written.

### Provenance of the fix

Filed from RG-51's round-2 review, then folded in before RG-51's own
release on an explicit operator size call (RG-53, the other finding from
that round, was judged too large — it changes `coverage_gate.py`'s
pass/fail semantics for every `--cov-branch` lane in the estate — and
stays OPEN for its own cycle).

## RG-53 — `tools/coverage_gate.py` never reads `missing_branches`, so the selftest lane's `--cov-branch` is decorative for the DIFF judge

`run-gate.toml`'s `selftest` lane runs pytest with `--cov-branch` and
this project's own README/backlog describe the enforced floor as
"changed-line coverage (line+branch)". The judge does not implement the
second half: `_verdict` reads only `executed_lines` and `missing_lines`
from the coverage JSON (`tools/coverage_gate.py`, around the
`_validate_cov_record` call) and never looks at `missing_branches`. A
changed line whose `if` has an untaken arm therefore scores as fully
covered.

Found 2026-09-11 by RG-51's round-2 review, which located a real uncovered
BRANCH in that change (`recorded_worktree_base`'s "report the FIRST
rejection" arm — the `if rejected is None:` false path) that the lane
reported as 100% clean. The branch was then covered by a new test, but
the judge gap remains: the same class would slip through again.

Related, same file: `pct = 100.0 if total_changed_exec == 0 else …` — the
0/0-is-100% trap (assay A-026/A-035, and TESTING-METHODOLOGY's first
"Definition of done" line). It is correct for a diff that genuinely
changes no executable line, but it is also exactly what turns a
degenerate comparison base into a SILENT green (see RG-51's round-2
finding, where a merged-but-not-torn-down worktree produced
`merge-base == HEAD`, hence zero changed lines, hence `0/0 OK`).

### Status — FIXED 2026-09-12 (RG-55 wave, package P2), BREAKING

Both directions landed together, in `tools/coverage_gate.py`:
- `_validate_cov_record` now also validates the OPTIONAL `missing_branches`/
  `executed_branches` keys (list of `[source_line, target_line]` int pairs —
  coverage.py's own JSON shape, `jsonreport.py` `_convert_branch_arcs`).
  `evaluate()` builds a per-source-line branch-arc map (`_branch_maps`) and
  counts a changed line as uncovered when it executed but left an arm
  untaken, not only when it never ran. `Verdict` gains `branches_total`,
  `branches_missed` (both scoped to the changed+executable line set only —
  reported BESIDE the line counts, never a second whole-file denominator)
  and `branch_partial_lines` (which uncovered lines ran but had a missed
  arm, vs. never ran at all); the CLI's OK/FAIL lines print the branch
  tally and mark branch-partial lines `(branch)` in the FAIL listing.
- `total_changed_exec == 0` is now reported as **SKIPPED** by the CLI
  (`main()`, exit 0) — `Verdict.verdict == "skipped"`, never `"ok"`, never
  a bare `100.0%` line — naming the resolved base and how HEAD relates to
  it. `evaluate()` itself is UNCHANGED in this respect (still a pure
  0/0-is-100% classifier producing the same `pct`/`passed`; only the new
  `Verdict.skipped`/`.verdict` fields distinguish the case) — the SKIPPED
  reporting is deliberately CLI-level so existing direct callers of
  `evaluate()` are unaffected; `_base_relation` (git ancestry/count) and
  `_empty_diff_notice` (pure formatter) are the two small helpers the CLI
  calls, each tested independently. Hard refusal (exit 2, naming the three
  known routes to a false 0/0) is OPT-IN via the new `--refuse-empty-diff`
  flag; `--allow-empty-diff` no longer exists. `run-gate.toml`'s `selftest`
  argv is UNCHANGED — on `main` itself it now prints SKIPPED and exits 0;
  on a branch whose diff touches `run-gate.py` it judges those lines
  normally, same as every other consumer.

### Rework — RW-5 (2026-09-12, RG-55 wave controller ruling)

This entry's FIRST landing (above) made a `changed_executable == 0`
verdict a hard refusal (exit 2) by default, gated behind
`--allow-empty-diff`. That put THIS PROJECT'S OWN `selftest` lane
permanently red on `main` (merge-base(main, HEAD) == HEAD there, so the
diff-coverage phase is always 0/0) and would have blocked every
`cmru release` of this project, whose release gate IS the selftest lane.
RW-5 corrected the design: the false-green hazard RG-51/RG-54 describe is
a WRONG BASE hiding real source changes, and the judge cannot tell that
apart from "this change genuinely touches no source line under
`--source`" by the zero alone — so instead of either silently passing
(the pre-RG-53 bug) or refusing outright (this entry's first landing),
the zero is made VISIBLE and NAMED: a distinct `diff-coverage SKIPPED: ...`
stdout line (exit 0) naming the resolved base and the exact base/HEAD
relationship (`HEAD is on the base`, or `HEAD is N commits ahead of the
base; the diff touches no executable source line`). The hard refusal
becomes opt-in (`--refuse-empty-diff`) for a consumer that wants it. The
wrong-base guard for a case that DOES matter stays RG-51's fork-point
rule, unaffected by this rework.

Evidence: `tests/test_coverage_gate.py` grew from 20 to 23 tests (net —
5 tests from the first landing tied to `--allow-empty-diff`/
`_check_nonempty_diff` were removed and replaced with 8 covering the new
design: `Verdict.verdict` tri-state for both the 0/0 and nonzero cases,
`_base_relation`'s two shapes against a real tmp_path repo, the
`_empty_diff_notice` pure formatter for both the default SKIPPED and
`--refuse-empty-diff` outcomes, an end-to-end `main()` pair proving the
default exit-0/stdout SKIPPED behavior and the opt-in exit-2/stderr
refusal, and the arg-parser default for the renamed flag) — `pytest
tests/test_coverage_gate.py -q` → 23 passed. **BREAKING** for every
consumer of the vendored judge (topos pattern, RG-53's own analysis
above): re-copying `tools/coverage_gate.py` picks up all three semantic
changes (branch awareness, the SKIPPED 0/0 design, and the renamed/
inverted-default empty-diff flag); see `CHANGES.md` `[Unreleased]` for the
consumer-facing note. SPEC amendment (a rule id under the R-33
diff-coverage floor family) lands with this wave's other spec/backlog
pass (C8).

## RG-54 — whatever base run-gate resolves, assay DISCARDS it when HEAD is a merge commit and judges against HEAD's first parent instead

run-gate's whole comparison-base path — `--base`, the RG-51 recorded fork
point, `@{upstream}` — decides one thing: which ref string to hand the
judge. What the judge does with it is assay's contract, and assay's
`resolve_base` (`assay/src/assay/git.py`, `base_resolution_mode`, B008)
does NOT always compute `merge-base(base, HEAD)`. When HEAD is a merge
commit it returns HEAD's **first parent** and discards the supplied base
entirely.

The consequence is the same class RG-51 exists to close, one layer
further down: a branch that merged a sibling branch in and then ran the
gate on that merge commit has the whole second parent's contribution
outside the judged diff, no matter how correct the base run-gate picked.
`tools/coverage_gate.py`'s own `_resolve_base` has the identical
first-parent rule, so the vendored thin gate behaves the same way.

**Observed in an artifact, not only derived.** RG-51's own pre-merge
`main`-into-branch merge produced exactly this, on both projects' gates,
in the same minute it was filed:

```json
"resolved": {"base": "d8b6f70a…", "base_resolution": "first-parent"}
"coverage": {"covered": 0, "executable": 0, "branches_total": 0, "pct": 100.0}
```

`ciu`'s lane reported `PASS` having judged nothing, and run-gate's own
`selftest` printed `diff-coverage OK: 0/0 changed executable lines
covered (100.0% ≥ 100.0% floor)` on the same commit. Nothing was wrong
with either change — the merge genuinely brought in no work of its own on
those paths — but the two verdicts are indistinguishable, in the
artifact, from a merge that brought in a great deal. That is the whole
item: `base_resolution` names the behaviour honestly and a reader who
does not look at that field cannot tell the cases apart. The meaningful
measurements for that change were the ones on its last NON-merge commit
(124/124 and 26/26 lines, 8/8 branches).

Found 2026-09-11 by RG-51's round-4 review, as a BOUNDARY note rather
than a defect in that change: it predates RG-51, is unaffected by it,
and reproduces identically under `--base` and under `@{upstream}`. RG-51
therefore documents its rule as "sound at run-gate's OWN boundary only"
(`SPEC.md` R-35a) rather than claiming an end-to-end guarantee it cannot
make.

### Status — CLOSED 2026-09-30 (first-parent is deliberate; false PASS is blocked)

The first-parent policy itself remains deliberate and unchanged. Assay B008
resolved the silent-certification defect by recording
`judgment.resolved.base_resolution = "first-parent"` on merge-commit HEADs and
documenting that field for consumers; B008 is in released `assay-v7.1.1`.
run-gate rev 41 (23.7.0) also made its own empty changed-line coverage result
refuse by default, naming merge-commit first-parent resolution as a route to
0/0; only an explicit `--allow-empty-diff` permits that result. Native Assay
R2 with no selected mutants is `INCONCLUSIVE/NO_MUTANTS`, not PASS. Thus the
behavior remains visible and cannot certify an unjudged diff as green. This
closes the false-certification issue without changing Assay's intentional
merge semantics. A future request to judge sibling-branch work at a merge tip
with merge-base semantics is a new product decision, not an open RG-54 fix.

Historical disposition before closure:

- The first-parent rule is deliberate in assay (B008) — for a merge that
  only brings in an already-judged upstream, first-parent is the RIGHT
  base, and merge-base would judge nothing.
- The bad case is specifically a merge of a SIBLING topic branch whose
  work has never been judged. Distinguishing the two needs a fact about
  the second parent's provenance that neither tool currently records.
- A cheap partial: run-gate could DISCLOSE, when it has resolved a base
  and HEAD is a merge commit, that the judge may ignore it — the same
  "make the staleness visible" half that RG-51 insisted on. That is
  run-gate's to do and does not touch assay's semantics.
- The real fix, if there is one, belongs in assay's backlog next to
  B008; file it there before implementing anything here.

## RG-55 — no per-lane resource-usage profile (peak RSS, hot-set, CPU, wall-clock) is measured or persisted, so concurrent-lane scheduling has no footprint signal to schedule against

Filed from a dstdns consumer session, 2026-09-12, while scoping a
test-improvement program that raises how many lanes/stacks may run
concurrently on a host shared with co-resident production workloads.

`.assay/` already persists a per-lane snapshot — `progress-<lane>.jsonl`
(live progress, survives a resume) and `verdict-<lane>.json` (the last
verdict, pass/fail plus coverage/mutation numbers) — but only the LATEST
run, and neither carries anything about the run's actual resource
footprint: peak RSS, hot memory-access set size, CPU count actually used,
wall-clock duration. A consumer scheduling several lanes concurrently
today has only a flat concurrency count to reason with (dstdns: "N
concurrent stacks, M concurrent gates"), which cannot distinguish a lane
using 1 CPU / 100MB from one using 2 CPU / 700MB peak / 200MB hot-set —
either wastes spare capacity by under-packing, or risks real memory
thrashing by over-packing, because the actual constraint that matters
(memory pressure causing thrashing, not raw usage — a host can tolerate
tens of GB of swap and full CPU saturation just fine, but must never let
`/proc/pressure/memory` start climbing) has no data feeding a scheduling
decision at all.

Two tools already exist in this estate for exactly this measurement and
are not yet wired into any lane-execution path: `scripts/cgroup-profiler/`
(cgroup-scoped resource accounting) and `scripts/damon-analysis/`
(`damon_cli.py`, DAMON-based memory access-pattern profiling, already
consumed by `topos`'s own CLI dispatch — `topos/src/topos/damon`). Neither
speaks to run-gate/assay's lane-execution machinery today.

### Status — FIXED 2026-09-12 (RG-55 wave, package P2)

Scoped and shipped as a TWO-package wave (contract-first, per the plan of
record `WAVE-PLAN-2026-09-12-rg55-profiling.md`): P1, the cgroup-profiler
daemon (`cgprofile serve`/`ctl`, its own worktree/backlog), and P2, this
package — the run-gate CLIENT. Answers the three open design questions
this entry originally left open:

- **Where it lives:** run-gate proper, per the entry's own reasoning
  (it owns the lane-dispatch entry point) — NOT assay, NOT a thin wrapper.
- **What is captured, and how:** a resource profile (peak memory
  [+baseline, p90], DAMON hot-set, CPU cores, memory-full stall) wrapping
  the lane's actual cgroup — precisely via the daemon (`docker exec
  cgprofile-host-daemon cgprofile ctl ...`, `RG55-INTERFACE-CONTRACT.md`)
  when reachable, or a coarser in-lane cgroup sample (`method: "basic"`)
  when it is not — never nothing, short of profiling being disabled
  outright. Persisted as an EXTENSION of the existing history mechanism
  (RG-27's `history.json`, schema 2 — `resources`/`profile_error`/
  `profile_ref` per entry, five new series alongside duration), exactly
  the "short history, not one noisy sample" shape this entry asked for.
  `run-gate.footprint.json` (SPEC `R-44`) is the distilled, COMMITTED
  form — a stable estimate a scheduler (or a human sizing
  `resources.memory`/`resources.cpus`) reads instead of one run.
- **Live acceptance, real docker** (this package's own numeric criteria):
  an ephemeral lane allocating ≥ 100 MiB, no daemon present (basic-path
  fallback), measured a peak of **115523584 bytes (110.17 MiB)**,
  `source: "memory.peak"`. An exec-mode (`container-shared`) lane
  allocating 80 MiB on a persistent runner measured
  `peak_over_baseline_bytes` of **88612864 bytes (84.51 MiB)** over a
  90.14 MiB baseline (`source: "sampled-max"`), with ≥ 2 samples taken
  mid-run. Both against this package's own acceptance thresholds (≥ 100
  MiB / ≥ 70 MiB respectively) — full transcripts in
  `nyxloom-trove/reports/run-gate-WAVE-RG55-P2-REPORT.md`.

Admission control on top of this data (the PSI-gated "wait/refuse before
starting lane N" mechanism this entry's own bullets sketched) is
DELIBERATELY not part of this fix — filed forward as **RG-56**, the next
wave, once real footprint data exists to set a threshold against instead
of a guessed one (the same "measure first, decide later" posture RG-27
itself was filed under). This entry's dstdns cross-reference
(`docs/testing/RIGOR-COVERAGE-POLICY.md` "Resource-profiling and
scheduling") is RG-56's to apply, not this package's.

## RG-56 — admission control on the profiler registry (next wave after RG-55)

**Provenance:** RG-55 plan D-6 (`run-gate-project/nyxloom-trove/WAVE-PLAN-2026-09-12-rg55-profiling.md`),
operator interview 2026-09-12 ("swap usage is never the gate; PSI is").

### Mechanism

Inputs: the committed `run-gate.footprint.json` (expected footprint per
lane, RG-55's own D-9), `cgprofile ctl status` (the live daemon's session
registry — sessions across ALL projects/worktrees, the cross-project
registry RG-45's direction 2 already asked for — plus host and per-slice
PSI), and the lane's own `resources` declaration (RG-48). None of these
exist as an admission SIGNAL today; RG-55 only measures and records them.

### Proposed contract

Before starting a lane, run-gate WAITS (bounded, with a periodic notice)
while host memory PSI `full avg10` is above a configured threshold, or
while the sum of live sessions' expected peaks (from each session's stored
`meta.expected`) plus this lane's own expected peak exceeds a declared
headroom for the slice; on the bound expiring, REFUSES (exit 2) naming the
readings that caused it (the PSI value, or the sessions and their expected
peaks that summed over headroom). `--allow-pressure` bypasses the wait/
refusal for one invocation (disclosed). `--dry-run` reports the admission
decision it WOULD make without ever waiting.

Explicitly NOT built as part of RG-55: run-gate decides no policy until
real footprint data exists (the estate's R-36-style "measure first, decide
later" posture) and any threshold set before real data is a guess dressed
as a decision — the same trap RG-27's own filing named. RG-55 wires the
registry and the footprint manifest; RG-56 is the wave that spends them on
an actual go/wait/refuse decision.

**Cross-reference:** SPEC-V8 S16.6 (ciu gate's admission ledger, keyed on
the slice cgroup directory) should consume the SAME registry once it
exists, rather than growing an independent one — see SPEC-V8.md Appendix
D.6.

### Oracles (sketch, not yet written — needs the real registry first)

- A fake daemon registry with two live sessions (each carrying a stored
  `expected.memory_peak_median_bytes`) and a footprint manifest for the
  candidate lane → assert the three outcomes (admitted immediately, waited
  then admitted once a live session's PSI notice clears, refused after the
  bound naming the specific readings).
- The PSI-threshold path alone, against a fake `/proc/pressure/memory` that
  starts above threshold and later drops.
- `--dry-run` never blocks regardless of the fake registry's contents.
- `--allow-pressure` bypasses a refusal that would otherwise fire, and the
  bypass is disclosed in the lane's stdout.

### Status — OPEN

## RG-57 — bare-host lanes record no resource profile (RG-55 v1 gap)

**Provenance:** RG-55 plan D-5 (bare-host lanes explicitly out of v1 scope;
"pid attribution through the env token is filed as a follow-up").

### Mechanism

run-gate itself runs inside a devcontainer with its own private cgroup
namespace (`cgroupns=private`, `0::/`); a bare-host lane's child process is
a plain `subprocess`/`Popen` in that SAME pid namespace, but the daemon
(`cgprofile-host-daemon`) runs `--pid=host` on the bare metal host, one
level further out — from inside the devcontainer there is no host-visible
pid for that child to hand the daemon, so `--target containerid:<lane
pid>` has nothing correct to name; the daemon cannot be pointed at "this
bare-host child" by pid the way it is pointed at a container by cgroup.

### Proposed contract

The same env token run-gate already generates for exec/ephemeral lanes
(`RUN_GATE_PROFILE_SESSION`, contract §4.1) is exported into the bare-host
child's environment too. run-gate then calls `ctl start --target
containerid:<the devcontainer's OWN container id>` (read from
`/etc/hostname` or a self `docker inspect`, resolved once per run-gate
process) with `--scope container-shared` and the token — the daemon's
existing token-subtree resolver (contract §4.3: scan `cgroup.procs` pids'
`/proc/<pid>/environ`, follow descendants) then attributes exactly the
bare-host lane's own pids inside the devcontainer's cgroup, the same
mechanism exec-mode already relies on. The reported cgroup numbers
(memory, CPU, PSI) are disclosed as devcontainer-wide, not lane-exclusive
(the devcontainer may be running other work concurrently) — same caveat
`container-shared` scope already carries for exec lanes, not a new one.

**Alternative for hosts with no daemon reachable at all:**
`os.wait4()` on the lane's own child, recorded as `method: "rusage"` in the
summary (`ru_maxrss * 1024` converts Linux KiB to bytes; `ru_utime` and
`ru_stime` are that child's exact CPU seconds) — coarser than the daemon path
(no cgroup, PSI, DAMON, or sampling series) but requires nothing beyond the
stdlib and works even with the daemon down.

Conjunction lanes stay unprofiled by design either way (RG-55 plan §3.1:
"members record their own"); this entry is about the leaf `bare-host`
lane, not the conjunction wrapping it.

### Oracles (sketch)

- A fake `docker inspect` of "self" plus a fake token-subtree registry →
  assert `ctl start` is called with `--target containerid:<devcontainer id>
  --scope container-shared --token <the exported token>` and the returned
  summary is stored exactly as `container-shared` numbers are today.
- The daemon-absent path: assert `os.wait4()` on the lane's own child becomes a
  `method: "rusage"` summary with `ru_maxrss * 1024` bytes and the correct
  key subset (no `damon`, `host.slice`, cgroup, or pressure data — i.e.
  `null`, never fabricated).

### Status — FIXED 2026-09-12 (RG-55 wave, package P4, `c37b6e94`), SPEC
`R-43i`

Both halves shipped as filed (RW-27b): daemon path via self container id +
token, scope always `container-shared`, disclosed DEVCONTAINER-WIDE;
daemon-absent path is `os.wait4()` on the lane's own child,
`method: "rusage"`, `memory.source: "rusage-maxrss"`,
`memory.peak_bytes = ru_maxrss * 1024`, `scope: null` (a design decision —
rusage measures via `wait4()`, not a cgroup read, so no contract `scope` value
is honest), everything else `wait4()` cannot supply left `null`, NEVER falls
back to a `BasicSampler` on this path.
`RESOURCE_SERIES_GETTERS`/`series_stats`/`_lane_stats`/
`build_footprint_manifest` needed zero code changes; `build_footprint_
manifest` gained one new `source` key, disclosed by `footprint`/`doctor`
only when it is `"rusage-maxrss"`. Live acceptance: this project's own
`footprint --write` (all five lanes bare-host) stopped refusing after one
real profiled run (see `CONSUMERS.md` "The footprint manifest" for the
transcript). Tests: `tests/test_run_gate.py` `TestBareHostProfilingWiring`
(rewritten, incl. both R-36h exception plants; recounted at the RW-46/S5
tip: 17 — B1/RW-43's rusage rewrite and RW-46b's floor_bytes wiring each
added more since RG-61's own "actually 7" correction) +
`TestFootprintVerbCLI.test_bare_host_rusage_run_makes_write_stop_refusing`.

**P4 final review repair (2026-09-15):** self-container discovery no longer
certifies a Docker object merely because `/etc/hostname` resolves as its
name. It requires a full 64-hex id and equality between this process's mount
namespace and the candidate's namespace read through bounded `docker exec`;
failure is disclosed as indeterminate and takes the rusage path. Rusage
duration now uses a monotonic interval instead of subtracting whole-second UTC
display stamps, and a `wait4()` failure preserves the earlier fallback reason,
adds its own reason, and truthfully reports that no profile was recorded.

## RG-58 — a bare-host lane declaring `stall_timeout` gets no warning at config-load time

**Provenance:** RG-55 wave P2 review round 1 S1/D5
(`run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-REVIEW-round1.md`).
RW-23b (controller ruling) removed the misleading `stall_timeout` key from
the one lane that had it (`assay-r2`) and corrected its comment, but left
D5's own question — refuse, warn, or accept as documented-inert — unruled;
review round 2 confirmed "the concrete hazard is gone ... what remains is
a genuine open product question, correctly deferred."

### Mechanism

`run_bare_host_lane` (`run-gate.py`) is a plain `subprocess.run` with no
`ProgressWatch`, no `LogStreamWatch`, no timer of any kind; a
`stall_timeout` key on a `kind = "command"`/bare-host lane's config is
accepted at load and then silently INERT for the lane's entire run —
nothing bounds a stuck-but-still-running invocation the way
`stall_timeout` does on a real container/exec lane. A future bare-host
lane's author, copying a container lane's config as a template, can
declare `stall_timeout = "20m"` and reasonably believe it is enforced; it
is not, and nothing tells them so beyond a comment on a DIFFERENT lane.

### Proposed contract

D5's own three options, still open:
1. **Refuse at config-load** (exit 2) when a bare-host lane declares
   `stall_timeout` — loudest and safest, at the cost of breaking a config
   someone wrote believing the comment.
2. **Warn once** (at load, or via a `doctor` check mirroring `R-30a`'s own
   precedent for a similarly cheap, load-time-computable config-shape
   warning) naming the inert key and the reason, without refusing.
3. **Accept as documented-inert** — the RW-23b comment already states the
   rule on the one lane that had it; no further code change. Weakest
   guarantee: a comment is read only by someone who goes looking, and only
   on that one lane.

No option is picked here; this is the decision D5 raised and RW-23
explicitly did not rule on.

### Oracles (sketch)

- A bare-host lane config declaring `stall_timeout` → assert the chosen
  behavior (refusal at load naming the lane and key; or a `doctor`/load-
  time WARN naming both; option 3 needs no new oracle beyond the existing
  comment).

### Status — FIXED 2026-09-12 (RG-55 wave, package P4, `e698835f`), SPEC
`R-30c`

RW-27a: option 2 (warn, never refuse). A bare-host lane declaring
`stall_timeout` now gets ONE load-time `run-gate: WARNING lane <name>:
stall_timeout is inert on a bare-host lane` (`_validate_lane`) and a
matching `doctor` WARN (new "2d" per-lane check), both from the SAME
shared `bare_host_stall_timeout_inert_reason()` so the two surfaces cannot
drift apart. Config still loads; exit code unchanged; container/exec
lanes untouched. Tests: `tests/test_run_gate.py`
`TestBareHostStallTimeoutWarning` (3 tests — recounted at the RW-46/S5
tip; unchanged since RG-61's own "actually 3" correction).

## RG-59 — the live-run daemon-absent warning names the wrong cause ("produced unparsable stdout" instead of "container ... is not running")

**Provenance:** RG-55 wave P2 review round 1 S6; confirmed unchanged
through fix round 1 (explicitly deferred: "the LIVE-RUN warning's
wrong-cause text ... is UNCHANGED — deferred") and review round 2 ("S6 ...
still exactly as in round 1; I saw the same text on every probe this
round").

### Mechanism

When the `cgprofile-host-daemon` container is not running (today's
default state in this environment), `ProfilerClient._ctl`'s `docker exec`
fails; its stdout is empty/non-JSON, so the generic `json.JSONDecodeError`
branch fires and the LIVE-run warning reads: `run-gate: WARNING
profiling: \`cgprofile ctl version\` produced unparsable stdout (exit 1);
stderr: Error response from daemon: container <id> is not running — basic
in-lane sampling only`. The real cause is present only inside the
`stderr_tail` fragment, not named as the headline reason — a consumer has
to parse docker's own error text to understand what to do next.
`doctor`'s OWN daemon-absence text (a separate code path,
`run-gate.py`'s `cmd_doctor`) is already good ("profiler daemon not
running — basic in-lane sampling only (start it: cd
scripts/cgroup-profiler && ciu up)"); only the LIVE-run warning has the
wrong-cause text.

### Proposed contract

When `_ctl`'s `docker exec` failure's `stderr_tail` matches docker's own
"No such container" / "is not running" text, the live-run warning should
say so and name the remedy — the same text `doctor` already uses — rather
than reporting a generic "unparsable stdout", which should stay reserved
for an ACTUALLY malformed daemon response from a daemon that IS running.

### Oracles (sketch)

- A fake `docker exec` returning docker's real "... is not running"
  stderr with non-JSON/empty stdout → assert the live-run warning names
  "daemon ... not running" (matching `doctor`'s own wording), not
  "produced unparsable stdout".
- A genuinely malformed (non-JSON, non-docker-error) stdout from a
  RUNNING daemon → assert the existing "produced unparsable stdout"
  wording is preserved for that case.

### Status — FIXED 2026-09-12 (RG-55 wave, package P4, `b5e4a9c6`)

`ProfilerClient._ctl`'s `json.JSONDecodeError` branch initially matched
docker-owned stderr to tell a daemon-absent `docker exec` failure apart from
a running daemon's genuinely malformed response. **P4 final review repair
(2026-09-15):** the FIXED implementation now requires both a Docker-owned
prefix and specific missing/stopped-container wording. Exit 125 alone and a
prefixed permission/transport failure are indeterminate and never receive the
"not running ... ciu up" remedy; 126/127 retain their distinct broken-image/
PATH reason. `doctor` likewise distinguishes a failed `docker ps` from an
empty successful result. `daemon_not_running_reason()` remains the shared
wording only for positively established absence; "produced unparsable
stdout" remains the running-daemon malformed-output case.

## RG-60 — an exec lane's profiling session has no inflight/recovery record if the client dies mid-run

**Provenance:** RG-55 wave P2 review round 1 S13, code half (the doc half
— `SPEC.md` R-43f/R-43a claiming the token IS recorded for exec lanes —
is corrected directly in this close-out, not filed; review round 2: "The
*code* half ... is a new code path and correctly deferred: backlog.").

### Mechanism

`write_inflight_record` (`run-gate.py`) is called only from
`run_container_lane`; `run_exec_lane` writes no inflight record at all. A
container lane whose run-gate client dies mid-run leaves a recovery
record naming the profiling session (`profile_token`/`profile_daemon`/
etc.) so a later invocation can reconcile or clean it up; an exec lane in
the identical situation leaves nothing on disk for anything to recover or
reconcile against if the client process dies between starting the
daemon session and finishing it.

### Proposed contract

`run_exec_lane` gains the same `write_inflight_record` call
`run_container_lane` already makes, at the equivalent point in its own
lifecycle (after the profiling token/session is established, before the
exec begins), and the equivalent clearing call in its own success/failure
paths. The record's shape is already defined by the existing mechanism;
this wires it into the second lane kind that currently lacks it, not a
new design.

### Oracles (sketch)

- An exec lane run with profiling enabled → assert an inflight record is
  written before the `docker exec` begins and cleared in the `finally`,
  mirroring the existing `TestAwaitContainerProfilingWiring`-style
  coverage `run_container_lane` already has.
- A killed exec-lane client (simulated) → assert the inflight record
  survives on disk, matching the container-lane recovery path's existing
  behavior.

### Status — FIXED 2026-09-12 (RG-55 wave, package P4, `a6716422`), SPEC
`R-43a`/`R-43f` amended

`run_exec_lane` now writes the same inflight record `run_container_lane`
writes (schema/lane/container/container_id/owner/commit/worktree/
profile_token/profile_daemon/profile_session), written after the
profiling session (if any) is established and before the `docker exec`
begins, cleared in the same `finally` that finishes profiling —
unconditionally, profiled or not (RW-1's own rule: a client can die
mid-run either way). Not wired into `resolve_inflight`/re-attach — RG-60's
own scope is the record existing to be FOUND, not a new re-attach design.
Tests: `tests/test_run_gate.py` `TestExecLaneInflightRecord` (4 tests as
of the RW-46/S5 tip: record-exists-at-exec-time via a `Popen` spy, the
daemon-path-only `profile_session` compare (added by B2/RW-43), killed-
client survival, and profiling-disabled coverage). **S3 (round-1
review):** the killed-client test originally hand-wrote a payload with
`write_inflight_record()` and read it straight back — it never called
`run_exec_lane` at all, so it stayed green with this whole feature fully
reverted. Rewritten to mirror the container lane's own precedent
(`TestReattachAcrossADeadClient`): a REAL client subprocess against a
real (shimmed) exec-mode project, killed mid-`docker exec`, the record
read back from OUTSIDE that process — proven to fail when the write is
reverted.

**P4 final review repair (2026-09-15):** the container runner's foreign-record
branch originally printed “refusing” but returned the same `None` sentinel as
“no record,” so the caller immediately started a fresh container and overwrote
the record. It is now a terminal exit-2 refusal for live and `--fresh`
invocations; dry-run names that exact outcome and also stops there. Regression
tests assert no lane launch and byte-identical preservation of the record.

## RG-61 — SPEC/README/CONSUMERS/CHANGES/backlog documentation drift left over from the RG-55 wave (S14 remainder)

**Provenance:** RG-55 wave P2 review round 1 S14 (consolidated list); fix
round 1 explicitly deferred all of it beyond the B2/R-44d fix ("pure
prose/doc drift, no test pins them, and this round's time budget went to
the 5 blockers + the RW-23 items with real behavioural consequences
first"); review round 2 spot-checked the highest-value items and
confirmed each still present at the ACCEPTed tip ("S14 remainder ... I
spot-checked the highest-value items and they are indeed unfixed").

### Mechanism

Eight separate drift items, none affecting behavior, re-verified present
at this close-out's own starting tip (`f08080e7`):
1. `SPEC.md:555` and `:1660` cross-reference **`R-44`** for `doctor`'s
   "profiler" check; `R-44a`-`e` are entirely the footprint manifest, and
   `R-30` (Doctor) is untouched — the C7 deliverable (the `doctor`
   profiler check itself) has no rule id of its own.
2. `R-30` still says the summary counts "all four" statuses; the code has
   emitted a fifth, `INFO`, since before this wave.
3. The RG-53 SPEC amendment promised at this file's own RG-53 entry never
   landed (`R-33` untouched), and `SPEC.md:756` (`R-35a`) still states the
   now-false "scores `0/0` as 100% — a silent false green" (made false by
   RW-5's own 0/0 rework this wave).
4. `[profile]`/`[footprint]` are documented as prose only, nowhere as a
   structured key-by-key block — `CONSUMERS.md` (the adoption contract,
   which gives `[history]` and `[lanes.<n>.resources]` their own
   full-schema blocks) has no equivalent `[profile]` block; `RUN_GATE_
   PROFILE` is documented there as `off`-only, while `on`-over-lane-opt-
   out and the by-name-value refusal appear only in SPEC/`usage()`.
5. `CONSUMERS.md`'s `footprint --write` transcript is fabricated from the
   contract's golden fixture and shows an impossible run (all five lanes
   of this project are bare-host, so `--write` refuses, confirmed live);
   its column layout also does not match `print_footprint_report`'s real
   output.
6. `CHANGES.md`'s `[Unreleased]` header comment still reads "Verified
   empty as of 2026-09-11's release" above 100+ new lines added since,
   with no `__revision__` drift-marker bump note (every prior dated entry
   has one).
7. `usage()` (`run-gate.py`) omits `RUN_GATE_PROC_ROOT` while listing its
   sibling `RUN_GATE_CGROUPFS_ROOT`; `SPEC.md:178`'s lane-schema text
   still points the `profile` key at `R-43g` (it is `R-43h` — README
   already gets it right); `SPEC.md:50`'s "both had shipped in code"
   claim (`resources`/`cpus`) is inaccurate for the `cpus` half.
8. Backlog RG-53's own evidence cites a stale test-count delta, and this
   file's FIXED entries generally cite no commit hashes; LOG test-count
   claims in two places do not match the tests actually added.

### Proposed contract

A documentation-only sweep (no behavior change, no new tests beyond what
a prose fix needs) correcting all eight items in place, the same shape
C8's own revision-41 sweep used for the rest of this file family. Low
individual risk, moderate total size (eight file-shaped edits across
`SPEC.md`/`CONSUMERS.md`/`usage()`/`CHANGES.md`/this backlog file) —
better suited to its own small package or a documentation-sweep session
than folding into another wave's close-out.

### Oracles (sketch)

None needed beyond the existing prose-consistency review this class of
fix always gets — no behavior changes, so no new pytest coverage is
implied by fixing any of the eight items.

### Status — FIXED 2026-09-12 (RG-55 wave, package P4, this commit)

All eight items fixed in place: (1) new SPEC `R-30c` for `doctor`'s
profiler check; (2) `R-30`'s status-line count corrected to five
(`INFO`); (3) the RG-51 narrative's stale `0/0 -> 100%` text corrected to
describe RW-5's actual SKIPPED-verdict design (two spots); (4)
`CONSUMERS.md` gained full `[profile]`/`[footprint]` schema blocks and the
`RUN_GATE_PROFILE` `on`-override + by-name-refusal behavior; (5)
`CONSUMERS.md`'s fabricated `footprint --write` transcript replaced with a
real one from this project's own profiled run (RG-57 makes this possible
for the first time — see "The footprint manifest"); (6) the `[Unreleased]`
header comment corrected and every entry under it now names its own RG id
and the rev-42 bump; (7) `usage()` gained `RUN_GATE_PROC_ROOT`, the stale
`R-43g`/`R-43h` lane-schema cross-reference fixed, the "both had shipped
in code" claim corrected for `resources.cpus`; (8) this package's own five
FIXED entries (RG-57/58/59/60/61) got commit hashes above, and its own
LOG file's test-count claims were audited and TWO real mismatches were
found and corrected (`TestBareHostStallTimeoutWarning`: claimed 4,
actually 3; `TestBareHostProfilingWiring`: claimed 9, actually 7) — this
entry's own "physician heal thyself" invitation, taken literally. **These
two counts were true AT `c37b6e94`, not at any later tip** — round-1
review's own S5 caught this drift again (B2/RW-43's rusage rewrite and
RW-46b's floor_bytes wiring each added more `TestBareHostProfilingWiring`
tests after this correction was written); see the RG-57/RG-58/RG-60
entries above for the counts recounted at the RW-46/S5 tip. A test count
in prose rots the moment the next commit touches that class — treat every
number above as "true when written," not a live invariant.

## RG-62 — two pre-existing order-/timing-sensitive test flakes found live while gating P4 (RW-46)

**Provenance:** found live during RW-46/session 5's `assay-r1` gate
verification (3 attempts, 2 distinct failures, neither touching any file
this package's own diff modified). Filed here (this package's own
backlog) per this session's own dispatch instructions and the estate
convention that a tool's own defects, found while working in it, are
recorded in the tool's backlog — never worked around locally without a
record.

## RG-65 — duplicate config-resolution defect merged into RG-47

This entry's independent 2026-09-18 reproduction and its worktree-only-lane
oracle are incorporated into [RG-47](#rg-47). The background wrapper's
reported exit 0 was wrapper behavior; it is not attributed to run-gate.

---

## RG-64 — caller-side lock checks can mistake queued container execution for a hang

**Provenance:** found live 2026-09-18 during dstdns Track B Wave B2 (same
six-package concurrent-container wave as RG-63), by a package (`p194-b2-io-fault`)
diagnosing why its own backgrounded gate run never produced a verdict.

### What's wrong

dstdns's own dispatch convention has each package take a caller-side
per-instance lock file (`/tmp/<repo>-<instance>-testrunner.lock`) before
running a gate, intended to serialize concurrent packages' access to a
shared `test-runner` container. Separately, `run-gate` itself always takes
its own internal exec lock (`/tmp/run-gate-exec-<container>.lock`,
referenced elsewhere in this file as the RG-63 mechanism) before actually
touching the container. These are two independent locks with no relationship
enforced between them:

- A package holding the CONVENTIONAL lock is not guaranteed exclusive
  container access — a sibling package that never took the convention's
  lock (e.g. dispatched without that instruction, or simply not following
  it) can still be mid-execution via run-gate's own internal lock alone,
  and the first package's gate will queue invisibly behind it.
- Checking `fuser`/liveness on the CONVENTIONAL lock alone is therefore not
  a reliable way to distinguish "queued behind a real holder" from "never
  acquired, actually stuck" — it will only correctly diagnose contention
  when the actual occupier happens to also be using the same convention.
  The reporting package got a correct diagnosis once this way purely
  because that specific sibling used the convention too; it does not
  generalize, and the package's own BRIEF nearly propagated the false
  confidence forward to its successor before self-correcting.

The package's own concrete incident: a backgrounded `run-gate` invocation
held its conventional instance lock the entire time but produced ZERO
executed tests for 30+ minutes; the actual blocker was a sibling package
mid-run in the shared container holding run-gate's own internal exec lock
(`/tmp/run-gate-exec-dstdns-98535c-test-runner.lock`), which the
conventional-lock check never surfaced.

### Why it matters

`run-gate`'s own internal exec lock is the only universally-taken,
authoritative signal of real container occupancy (it is architecturally
guaranteed by `run-gate` itself, not opt-in). Any dispatch convention, doc,
or agent guidance that tells a caller to check its OWN convention's lock as
a proxy for "is the container free" is checking the wrong thing — it can
report false negatives (looks free, isn't) whenever a sibling invocation
does not participate in that same convention.

### Proposed fix

- Document explicitly (in `run-gate`'s own docs, and in any consumer's
  dispatch guidance) that `/tmp/run-gate-exec-<container>.lock` is the ONLY
  authoritative liveness signal for a shared container, and that a
  caller-side convention lock is a scheduling nicety, never a substitute.
- Consider whether `run-gate` should expose a documented, first-class way
  to query current exec-lock holder/queue depth directly (rather than a
  caller having to `fuser` a raw lock file path and interpret PIDs itself),
  so consumers don't need to reason about lock internals at all.
- Not fixed by this session; dstdns's own dispatch prompts/BRIEFs should be
  corrected to check the internal exec lock, not the convention lock, when
  diagnosing "is my gate actually queued or stuck" — a dstdns-side action
  item, tracked in that project's own controller record.


### Status — FIXED 2026-10-04 (rev 51)

`run-gate status [--worktree PATH] [--json]` reads the selected project's
inflight records, maps `/tmp/run-gate-exec-<container>.lock` device/inode
pairs to `/proc/locks` holders and waiters, and reads the shared Docker
admission object, live tickets, owner/deadline labels, and tombstones. A
controlled regression oracle holds only the internal exec lock; status reports
its holder without creating a caller-side wrapper lock. Unreadable sources
produce a partial JSON document and ERROR/2, never an empty result.

## RG-63 — an assay lane's `LANE_TIMEOUT` budget appears to include exec-lock queue-wait time under multi-package contention, not just execution time

**Provenance:** found live 2026-09-18 during dstdns Track B Wave B2, six
concurrent T1 implementer packages sharing ONE `test-runner` container
(`dstdns-98535c-test-runner`). Filed here per the standing convention (a
tool defect found while working in it goes in the tool's own backlog, not
worked around locally).

### What's wrong

A package's (`p195-b2-ctl`) `assay` lane failed `BUDGET_EXCEEDED/LANE_TIMEOUT`
("the lane-wide deadline expired") against its declared `budget = "30m"`.
The run-gate log for that attempt shows it first blocked on a SECOND,
finer-grained lock before the mutation run itself began:

```
run-gate: lane 'assay': waiting for container 'dstdns-98535c-test-runner' —
another gate holds /tmp/run-gate-exec-dstdns-98535c-test-runner.lock
```

five other T1 packages' own gates/assay lanes were concurrently contending
for the same shared container's exec lock at that moment. The package's own
after-the-fact measurement (not run-gate's own diagnostics, which don't
surface this breakdown): `main`'s history for this lane runs in ~450-470s;
this attempt ran 1887.6s against the 1800s (30m) budget — and the lane's own
footprint recorded 1.19 cores avg sustained (i.e. genuinely executing, not
merely parked once it started) plus 158.5s stalled on memory (full). Whether
the exec-lock queue-wait itself is counted inside the 1800s deadline, or the
deadline only started once execution began (making the ~4x overrun purely
instrumentation+memory-stall cost), is not something either the log or the
`.assay/progress-*.jsonl` heartbeat distinguishes — the package correctly
declined to assert either reading and is retrying the lane alone instead.

### Why it matters

RG-36 (fixed) added progress-file disclosure so a genuinely-slow mutation
lane's health can be judged from candidate-rate/ETA rather than a guessed
wall budget — but that's about the lane's OWN execution taking long. This is
a different failure shape: a healthy, normal-duration lane can be pushed
over its fixed budget purely by how many OTHER packages are queued on the
same shared container's exec lock, with no signal distinguishing
"contention ate my budget" from "my mutation run is genuinely slow this
time" in the lane's own output. On a host running several concurrent
gate-dispatched packages against one shared container (this project's own
normal multi-package wave pattern), this makes `LANE_TIMEOUT` an
ambiguous verdict rather than a real judgment.

### Proposed fix

- Emit the exec-lock acquisition wait duration explicitly in the lane's own
  log/progress output (e.g. `run-gate: lane 'assay': exec lock acquired
  after 812s wait`), separate from execution time, so a reader (human or
  agent) doesn't have to reconstruct it from `main`'s history and the
  lane's own footprint sampling.
- Consider whether the `LANE_TIMEOUT` deadline should start counting only
  once the exec lock is actually acquired (execution time only), rather
  than from lane invocation (which bundles in an unbounded, contention-
  dependent queue wait) — or, if deliberate, document that choice explicitly
  so a BUDGET_EXCEEDED verdict's cause is knowable without independent
  forensics.
- Not fixed by this session; not touched by any Wave B2 package's own diff.

### Update 2026-09-18 (same day, later) — a THIRD attempt on a genuinely quiet host also timed out; the pure-contention explanation is likely wrong, or at least incomplete

The same package (`p195-b2-ctl`) retried this lane a third time, deliberately timed against a host with `some avg10=0.14` memory PSI (confirmed quiet, no other package's docker exec active at launch) — and it **still** hit `BUDGET_EXCEEDED/LANE_TIMEOUT`, this time recording only **0.36 cores avg** (i.e. genuinely NOT CPU-bound this run, unlike attempt 1's 1.19 cores avg). The package's own corrected diagnosis, arrived at by abandoning its own earlier (more convenient) contention explanation once the evidence stopped fitting it: `test-runner` runs a *superset* of the paths this lane covers in 535s, while `assay` covers *fewer* paths and still exceeds 1800s — and the lane's own ~450-470s historical baseline predates roughly a week of six Wave B2 packages each adding tests to the very suite this lane instruments. The working hypothesis is that the instrumented suite has organically outgrown its 30-minute budget over the course of the wave, independent of any single package's diff (the reporting package's own statement coverage in the measured modules has zero delta, confirmed via AST comparison) and independent of concurrent-exec-lock contention (this attempt's low core-average argues against CPU contention specifically, though it doesn't rule out I/O-level contention the controller's own host-wide PSI checks that same day did still show elevated).

**The decisive experiment — running this exact lane against `main` (no package's diff applied) on an equally quiet host — is named but was deliberately NOT run**, correctly left to whoever owns the shared config (the controller, or this backlog's resolver) rather than a package agent altering shared infrastructure to explain away its own gate. Recorded here rather than left only in the dstdns session record so the next person to hit this (in this project or another consumer) has both data points (contended AND quiet-host timeouts) rather than re-deriving the contention half of the story from scratch.

**Sharpened proposed fix**: in addition to the exec-lock-wait disclosure above, a `budget` that hasn't been re-measured against a growing instrumented suite is exactly the "unbounded budget by convention" shape RG-36 was partly written to close — worth checking on this project's own next assay-lane budget review whether `[lanes.assay]`-shaped budgets anywhere in this estate are still sized against a stale baseline. Not fixed by this session.

### Update 2026-09-18 (same day, later still yet again) — likely the actual root cause: an R2 lane re-runs its FULL R1-scoped argv once PER MUTATION CANDIDATE, so a slow baseline argv multiplies straight past any budget

A third package in the same wave (`p195-b2-ctl`/P201) measured, rather than guessed, the actual per-candidate cost of one of its own R2 lanes (`p201_controller_main`): the lane's declared argv (inherited from its R1 baseline) takes **533s to execute once**. assay's R2/mutation mode re-executes that SAME argv once per surviving-candidate probe — ten mutants at ~533s each is **~90 minutes of real, necessary work** against a 20-minute budget. This is not a queue-wait artifact and not a stale-budget artifact: it reproduces on an uncontended lane, with a budget that was never touched, purely because the declared per-mutant test command is broad (the whole R1 target set) rather than narrow (just the fast unit tests that actually exercise the mutated lines). The package's own conclusion, which reads as the most concrete, actionable explanation of the whole RG-63 thread to date: **this class of `BUDGET_EXCEEDED` is not fixable by tuning `budget` or `max_mutants`** — the fix has to be a narrower per-mutant argv declared at the lane level, so each candidate's re-run cost is seconds, not many hundreds of seconds. Everything in this backlog entry's four earlier updates (queue-wait ambiguity, "suite outgrew its budget," the LANE_TIMEOUT-vs-`assay.toml`-budget mismatch) is consistent with — and possibly fully explained by — this same underlying mechanism: R2 lane argvs across this wave were declared by copying the R1 lane's own (broad, whole-module) argv, and multiplying any broad argv's runtime by a double-digit candidate count was never going to fit a 20-45 minute budget regardless of what number is in the config.

**Sharpest proposed fix yet**: document explicitly, wherever R2/mutation lane declaration is taught (this project's own lane-authoring docs, `nyxloom` AUTHORING.md's R2-lane guidance, or both), that a mutation lane's argv must be scoped to the FASTEST test subset that still exercises the mutated code — never simply copy-pasted from the R1 lane's own (whole-module, broader-coverage) argv — and that `budget` should be sized as `candidate_count × per-candidate-argv-runtime × safety-margin`, not picked independent of that arithmetic. Consider whether `assay` itself could warn (not block) at lane-declaration or first-run time when a lane's own measured per-candidate cost times its candidate count would obviously blow its configured budget, so this is caught before a wasted 20-90 minute run rather than after. Not fixed by this session.

**CORRECTION 2026-09-18 (same day, later) — the 533s figure above was contention-inflated; re-measured on a quiet container at 70s.** The SAME package re-measured the SAME 17-file argv once host contention had eased: 70s, not 533s — a ~7.6x difference. Ten candidates at 70s each is ~12 minutes quiet, comfortably inside a 20-45m budget without any narrowing at all. **This materially changes the conclusion above**: the per-candidate-argv-cost mechanism is still real and mathematically sound (multiplying any argv time by candidate count is a genuine cost, and narrowing the argv is still a correct, independently-verified improvement — see below), but it was NOT, on its own, sufficient to explain the original 90-minute-vs-20-minute gap this update reported. Contention was the dominant term in that specific measurement, putting this finding back closer to this entry's ORIGINAL 2026-09-18 hypothesis (queue-wait/host-load inflating a lane's effective per-candidate cost) rather than superseding it. **The corrected picture: both mechanisms are real and compound** — a broad argv makes each candidate more expensive, and contention makes each candidate more expensive on top of that; neither alone was "the" root cause. Any downstream fix (dstdns's own D-498, this session) that narrowed an argv anyway remains a real, valid improvement (verified via branch-coverage-equivalence, not just line-coverage, catching a partial-arc regression a naive narrowing would have silently introduced) — narrowing an argv is good practice regardless of which mechanism dominates on a given run. But do not carry forward "argv breadth alone explains BUDGET_EXCEEDED" as settled; both this entry's contention-based updates and its argv-based updates are each partial explanations of the same wave's failures.

### Update 2026-09-18 (same day, later still) — a fourth data point: raising the assay-level `budget` had ZERO effect, pointing at a separate, un-configured timeout surface

A different package in the same wave (`p196-b2-db-ops`) hit `BUDGET_EXCEEDED/LANE_TIMEOUT` on its `worker_db_main_r2_compare` lane after having ALREADY raised that lane's own `assay.toml` `budget` from 20m to 45m specifically to get ahead of this class of failure (commit `5c3c8222`, disclosed and reasoned, not a cover-up). The run still failed at **~21.7 minutes elapsed** (`.assay/progress-worker_db_main_r2_compare.jsonl`: `end` event at `elapsed_s=1256.8`, `verdict_written` at `elapsed_s=1302.8`, `exit_code=4`, `reason_code="LANE_TIMEOUT"`) — well under half of the newly-configured 45-minute (2700s) budget, and the progress file shows all 13 declared mutation candidates actually ran to individual completion (9 killed, 3 survived, 1 internally bucketed `budget_exceeded`) before the overall verdict was still stamped as budget-exceeded.

This is a sharper data point than either of the two above: it isn't ambiguous between queue-wait and execution time, because the number that mattered here (~21-22 min) did not move at all when the package doubled-plus its own `assay.toml` lane budget. That strongly suggests `LANE_TIMEOUT` as a *reason_code* is being enforced by a **separate timeout surface** the `assay.toml` per-lane `budget` field does not control at all — most likely a run-gate exec-lock/wrapper-level env var or config default (plausibly literally named something like `LANE_TIMEOUT`) that sits outside `assay.toml` and was never touched by either package's own budget bump. If so, every package's per-lane `budget` increase this wave (P196's 20m→45m, and any other package's similar move) has been sizing a knob that isn't the one actually cutting the run off — worth checking directly against the run-gate/exec-lock source for a hardcoded or separately-configured ~20-minute default before assuming any `assay.toml` budget value is load-bearing for this failure mode at all. Not fixed by this session; P196 was asked to investigate which config surface is actually enforcing the cutoff and report back.

**1. `TestEstateBudgetTimeoutPairing::test_estate_pairing_sweep_is_alive`
is order-dependent.** `PAIRINGS_SEEN` (a class-level `list`) is populated
incrementally by the class's OTHER, parametrized test
(`test_consumer_timeouts_never_cut_lanes_short`, one instance per sibling
`nyxloom-trove/nyxloom.toml` found under `RUN_GATE_DIR.parent`) as each
instance runs; the aggregate test then asserts `len(PAIRINGS_SEEN) >= 3`.
This implicitly assumes ALL parametrized instances run to completion
BEFORE the aggregate check — true only under pytest's default
definition-order collection. `pytest-randomly` 5.0.0 is installed and
active for this project's own `tests -q` invocation (no `-p no:randomly`
anywhere in this repo's pytest config or in the `selftest`/`assay-r*`
lane argv), so EVERY invocation gets a fresh random seed and CAN place
the aggregate test before enough of its sibling instances have run,
under-counting `PAIRINGS_SEEN` and failing an otherwise-healthy estate.
Reproduced directly: `python3 -m pytest tests/test_run_gate.py -k
TestEstateBudgetTimeoutPairing -q` run 3 times in immediate succession —
PASS, PASS, FAIL (`estate-wide pairing collapsed to
[('nyxloom', 'tester-unified'), ('ciu', 'tester-unified')]`) — same code,
same tree, different random seed each time. **Prescription:** either
(a) make the aggregate a `pytest.fixture(scope="class", autouse=True)`
teardown (runs after every test in the class regardless of order), or
(b) mark the parametrized test and the aggregate with an explicit
`@pytest.mark.order` (pytest-order) / a session-scoped fixture ensuring
collection order, or (c) simplest: pin `-p no:randomly` for this one test
module/class via a local `pytestmark`. Not fixed by this session
(out of P4's RG-57..61 scope; not touched by this package's diff).

**2. `TestHistoryEligibilityGuard.test_tree_state_is_sampled_before_the_
lane_not_after` is a floating-point timing flake under host load.**
`rec["_started_monotonic"] = time.monotonic() - 4.0` then asserts
`duration_seconds == 4.0` exactly — observed failing as `4.001 == 4.0`
during a loaded moment on this shared host (this session's own gate
attempts + sibling RG-55 packages' concurrent pytest/gate runs).
**Prescription:** `pytest.approx(4.0, abs=0.05)` or similar tolerance,
matching this suite's OWN `pytest.approx` precedent used elsewhere for
timing-derived assertions. Not fixed by this session (same reasoning as
above).

Both are genuine, reproducible, PRE-EXISTING defects — neither is new,
neither touches code this package's diff modified, and both were
confirmed non-deterministic (pass on a retry) before being recorded here
rather than "fixed" by silently retrying past them without a trace.

### Update 2026-09-18 (same wave, sixth package) — a fifth data point corroborating the ORIGINAL queue-wait-inside-the-budget-clock hypothesis, with a nuance: partial real progress before the aggregate clock still tripped

A sixth package in the same wave (`p199-b2-io-main`) hit
`BUDGET_EXCEEDED/LANE_TIMEOUT` on its `worker_io_main_r2_compare` lane
(`python:compare-swap` over two small target files, only 8 total
candidates — nowhere near `max_mutants=200`, so this was never a
candidate-volume problem). The verdict bucketed **2 killed, 1 survived,
5 `budget_exceeded`** — i.e. real, individual candidate execution
genuinely happened and completed for 3 of the 8 (one of them a genuine,
non-equivalent surviving mutant, later fixed with a new test), yet the
LANE still tripped `LANE_TIMEOUT` overall. The lane's declared aggregate
`budget` was `"60m"`; the invoking host-side `run-gate` process itself
had been launched (and left legitimately queued on the shared
container's exec lock, `/tmp/run-gate-exec-<container>.lock`, contended
by five OTHER concurrently-dispatched packages in this same wave) hours
before assay ever got a chance to execute a single candidate. Re-running
the SAME lane once the exec lock actually freed up (assay's own
`--resume` picking up from `.assay/mutation-state/`, only re-testing the
one candidate whose source had changed via the fix) completed normally,
comfortably inside budget.

This is a straightforward, low-ambiguity corroboration of the entry's
ORIGINAL 2026-09-18 hypothesis (queue-wait counted inside the lane-wide
deadline, not just execution time) — it does not by itself distinguish
between "the deadline clock starts at host-side process launch" vs.
"starts at container-exec-lock acquisition", since both readings predict
the observed outcome here equally well. The nuance worth recording
separately from the other data points in this thread: **partial genuine
completion (3/8 candidates individually judged, including a real
survivor) can still end in an aggregate `LANE_TIMEOUT`** — so seeing SOME
real candidate outcomes in a `BUDGET_EXCEEDED` verdict's bucket counts is
not, by itself, evidence against the queue-wait-inside-budget mechanism;
a reader should not conclude "the lane was genuinely almost done" just
because a few candidates completed before the clock tripped. Not fixed
by this package's diff (same reasoning as every other update in this
thread) — filed as a note per the standing convention (a second/Nth
reproduction of the SAME underlying defect goes here, not a new entry).

### Status — FIXED 2026-10-04 (rev 49): the hard assay budget starts after admission, following shared-infra and exec-runner locks. Lock-wait time no longer consumes the execution budget.

## RG-66 — no pass-through for assay's `--reuse-from` / `--rejudge`

**Provenance:** found in dstdns 2026-09-30 (R2 campaign planning, `nyxloom-trove/decisions.md` D-569..D-573, dstdns@682c7ef4); run-gate 23.9.2.dev305, assay 7.2.0.

**Observed:** `build_assay_inner` (`run_gate.py` ~l.6250-6340) constructs the assay argv from the lane declaration plus a fixed set of flags (`--resume --progress --state-dir`, base, verdict). The request surface (`run-gate <lane> [--worktree] [--allow-dirty] [--base] [--fresh]`) has no way to add assay's `--reuse-from PRIOR_VERDICT` (assay 7.1.0, B106, provenance-safe reuse of killed native mutants) or `--rejudge ID` / `--rejudge-outcome` (assay 5.2.0, B091). Assay 7.1+ selective reruns therefore cannot be driven through the gate; the only routes are invoking `assay run` by hand (a manual path AGENTS.md 4.9 forbids) or a custom lane.

**Why run-gate owns it:** run-gate owns the argv of an assay lane and refuses unknown flags; a consumer cannot append to it.

**Proposed contract:** explicit, named request flags (for example `--reuse-from PATH`, `--rejudge ID` repeatable, `--rejudge-outcome BUCKET`), each valid only for `kind = "assay"` lanes that declare R2, refused by name elsewhere, and forwarded verbatim. `--reuse-from` paths must be resolved into the container namespace (the verdict must be readable inside the lane's environment). Bump the `ASSAY_FLAG_FLOOR` to the release that knows the flags when used, refusing by name otherwise. The reuse source and rejudge ids must appear in the run record so history shows the run was selective.

**Oracles:** `--dry-run` shows the forwarded flags; a command lane given `--reuse-from` refuses; a judge older than the floor refuses at construction; a controlled wrong implementation that forwards the flags for every lane kind must fail the refusal test.

**Spec owner:** SPEC (assay lane argv construction, R-38).

### Status — FIXED 2026-10-04 (rev 49). Run-gate forwards `--reuse-from`, repeatable `--rejudge`, and `--rejudge-outcome` only to assay lanes, validates the reuse path inside the judged worktree, records the request, and enforces the 5.2.0 judge floor.

## RG-67 — no per-environment invocation limit; composite lanes do not declare their runners

**Provenance:** found in dstdns 2026-09-30 while carving P220, which adds a second runner container (`test-runner-ui`); `nyxloom-trove/decisions.md` D-573.

**Observed:** RG-39 (FIXED, `acquire_exec_lock`) serializes exec-mode invocations per resolved container with a blocking exclusive lock. Two gaps remain. (1) There is no declarable limit N per environment: a runner that can safely host two lanes is either exclusive (N=1) or unguarded, and consumers add their own whole-invocation `flock` (dstdns GUIDE section 1) to get a cap. (2) A composite lane (`[lanes.gate]`, `environment = "host"`) does not declare which runner environments its members use, so a consumer wrapping the composite in one flock over-holds: it blocks a concurrent lane that only needs the second runner, since run-gate cannot tell the wrapper which locks the composite will take.

**Why run-gate owns it:** it is the only component that knows, at resolution time, which container each member of a composite will execute in.

**Proposed contract:** (a) an optional `max_concurrent` per environment in `run-gate.toml` (default 1, preserving RG-39), implemented as N lock slots keyed by container. (b) `run-gate <composite> --dry-run` and `--list` print the transitive set of environments the composite's members use, and run-gate takes each member's exec lock itself, so a consumer needs no outer flock for correctness. Keep the RG-39 ordering rule (fixed global order) to avoid ABBA.

**Oracles:** with `max_concurrent = 2` a third invocation blocks and two run; a composite using runner A does not block a lane using runner B; `--dry-run` lists the composite's environments; a controlled wrong implementation that keeps a single global lock must fail the second-runner test.

**Spec owner:** SPEC R-41 (exec lock).

**Consumer evidence (dstdns 2026-10-01, `dstdns@5cf2ed55`, decisions D-619/D-620):** the operator authorised two concurrent gates on the shared `dstdns-98535c-test-runner`: the container ceiling is 3000M, a mock suite peaks at about 1.3–1.5 GB and uses about 0.9 cores, the host has 8 cores, and CPU PSI was about 5–8%. Six gates were queued behind N=1. A caller-side two-slot semaphore did nothing alone, because R-41's exec lock kept the second run "waiting for container". The working consumer workaround, `scripts/gate-slot.sh`, gives slot 2 its own `RUN_GATE_LOCK_DIR`. That is safe there only because the project declares no RG-20 shared-infra locks; under a project that declares them, the knob would also silently split the shared-infra mutex. This hazard is the strongest argument for (a): `max_concurrent` keyed per container, which leaves the shared-infra locks single. A second gap belongs with (a): with N>1, the per-invocation resource profile (RG-27 history, `footprint`) measures a container cgroup shared by both runs. Either record `concurrent_with` in the history entry, or refuse `footprint --write` from a run that overlapped another.

**Consumer evidence, 2026-10-03 (dstdns tooling-boundary pass, `dstdns/docs/proposals/TOOLING-BOUNDARY-2026-10.md`).** Three further facts for whoever builds this:
- **The workaround has two latent defects that a native cap would not.** `dstdns/scripts/gate-slot.sh:44,47` treats a wrapped command's exit 75 as "slot busy" and silently re-runs the whole gate, because run-gate passes the lane's own status through (R-04/RG-11). And `:41` gives slot 2 a fixed `RUN_GATE_LOCK_DIR=/tmp/run-gate-slot2` that is not keyed by instance. Two different projects or instances using the same wrapper would share one slot-2 exec-lock namespace.
- **The cap now has three hand-maintained homes in the consumer:** the wrapper's slot count, the D-570/D-636 decision records, and the runner's `mem_limit` sizing comment (`tools/test-runner/ciu.defaults.toml.j2:43-45`). Under (a), the count would live once, in `run-gate.toml`, next to the environment it bounds.
- **Placement of the count.** (a) should apply to `mode = "ephemeral"` environments too, not only exec. R-29's memory admission cannot see the gates slice from the devcontainer, where every gate is launched. Live 2026-10-03: `/proc/self/cgroup` reads `0::/` and `/sys/fs/cgroup/dev.slice` is absent. R-29 therefore degrades to "admission by shared-infra rules only", and the only working bound on concurrent ephemeral lanes is a count (until RG-56 admits through the profiler daemon). Proposed: `max_concurrent` (default 1 for exec; default unbounded for ephemeral) per environment, enforced as N slot locks keyed by container for exec and by environment name plus project for ephemeral.
- **v8 conflict to resolve.** SPEC-V8 S16.5.7 says an exec target is used by one lane at a time "and a project that wants two lanes inside one container at once has no way to say so". Proposal §4.10 gap 18 adds "revisit only with a real consumer". dstdns is that consumer (D-619/D-636). The recommended resolution (dstdns memo, Q2): keep S16.5.7 for v8, and move dstdns's hermetic lanes to ephemeral environments (RG-73). Then N>1 is needed only as a v7 bridge for the exec runner. The ephemeral-environment count (third bullet) is needed in v8 as well, as the S16.6.1 fallback where the slice cgroup directory is invisible.
- **v8: absorb** (as S16.6's count fallback; exec N>1 stays v7-only).


**Amendment 2 (2026-10-03, dstdns D-658 rulings; SPEC-V8 draft.9 splits v8.0 from v8.1; still not built):** the v7 build of RG-67 (a per-environment count) is unchanged. Its **v8 absorption moves to v8.1**: v8.0 keeps an exec target at one lane at a time (S16.5.7) and has no per-environment count, and ephemeral concurrency is v8.1's admission (SPEC-V8 S21: gap-free name tickets, a byte budget, `unreadable_policy = count` only for unreadable facts, one switch `[ciu] admission`, default off). The max_concurrent oracle joins the v8.1 parity set.

**Amendment 3 (2026-10-03, dstdns D-661; SPEC-V8 draft.10 S21; still not built):** Amendment 2's claim that the v8 absorption moves to v8.1 is **superseded**. The daemon-wide cap on concurrent gates is **8.0**: SPEC-V8 S21's count mode (gap-free name tickets `ciu-res-gates-<n>` with tombstones, the published object `ciu-admission-<g>` carrying `tiers.gates.max_concurrent`, `ciu host admission set|show`, deadline and group reaping), behind the grouped switch `[admission] enabled` (default `false`). The v7 build of RG-67 (a per-environment count) is unchanged, an exec target stays at one lane at a time (S16.5.7), and only the byte budget (warm-set charge, `usable`, PSI, floors, the stack placeholder) stays v8.1. The `max_concurrent` oracle (`max_concurrent = 2`: a third invocation blocks, two run) is carried into the **8.0** parity set, run with admission enabled and the limit published, across two worktrees on one Docker daemon. dstdns retires its `gate-slot.sh` wrapper (D-570/D-636) at its gate cutover, in the same commit that enables admission and publishes the limit (SPEC-V8 Appendix A step 8).

**Amendment 4 (2026-10-03, dstdns D-666; reviewed against SPEC-V8 draft.11; still not built):** the daemon-wide cap that dstdns needs is **RG-80** (the count mode of SPEC-V8 S21, Docker-name tickets), not this entry: Amendment 3's sentence "dstdns retires its `gate-slot.sh` wrapper at its gate cutover" is superseded by D-666 (retire it **before** v8, by RG-80), and the `max_concurrent = 2` oracle that Amendment 3 carried into the 8.0 parity set belongs to RG-80's oracle 1. What stays here is (a), a per-environment N for **one persistent exec runner** that several lanes share, as a v7 feature for a consumer that has such a runner; dstdns no longer has one, because every worktree owns its own runner (D-647 #2, D-666: Mode A, judging in main's runner, is retired), so the 'consumer evidence' above (a shared `dstdns-<id>-test-runner` hosting two gates) describes the retired mode. (b) (composite lanes declare their runners) is unaffected. v8 keeps S16.5.7 (an exec target serves one lane at a time) and absorbs the daemon-wide count as S21.4.5 (RG-80), not as a per-environment count.

**Amendment 5 (2026-10-03, dstdns D-667; controller ruling on the r5 review §9.3 and §10 Q3): (a) is WITHDRAWN.** The per-environment N > 1 for one shared exec runner has no consumer since D-666 (dstdns, its only consumer, runs one runner per worktree; Mode A is retired) and it contradicts SPEC-V8 S16.5.7 (an exec target serves one lane at a time), so it would be a v7-only feature with nothing to port. It leaves run-gate's Phase A build list (proposal N24, D-651 Q2). The daemon-wide cap is RG-80. This file has only OPEN and FIXED statuses, so the entry stays **OPEN for (b)** (composite lanes declaring their runners, kept only while a composite consumer remains) and its index row says (a) is withdrawn; if (b) is also dropped the entry should be closed by a later note rather than deleted.

### Status — FIXED 2026-10-04 (rev 49) for the remaining composite item. Native `kind = "sequence"` declares ordered member lanes; run-gate resolves each member runner and runs serially under one composite invocation. D-667 withdrew per-environment N; no such cap was built.

## RG-68 — `footprint` ignores completed FAIL runs

**Provenance:** found in dstdns 2026-09-30 (first-run budget calibration for new R2 lanes, D-572 section 3).

**Observed:** `build_footprint_manifest` (`run_gate.py` ~l.3974-3990) distills PASS plus history-eligible entries for the numeric series; a lane whose only completed runs are FAILs (for example an R2 lane that finished with surviving mutants) is omitted from the manifest ("absent means unknown"), though `completed_runs` counts fails. A red first run of a new lane is a full, valid resource measurement (peak RSS, CPU, wall-clock), yet `run-gate footprint` reports nothing, so the consumer's first-run budget has nothing to calibrate against until a run goes green, which for a mutation lane may take many runs.

**Why run-gate owns it:** it owns the profile store and the eligibility rule.

**Proposed contract:** keep PASS as the default population for the committed manifest, but let `footprint` (report mode) include completed FAIL runs whose profile is complete (a measured `resources`, run reached its natural end, not timeout/kill/infra error), labelled `outcome=FAIL` in every line, and add a flag (for example `--include-failed`) for the manifest write so the choice is explicit. A TIMEOUT or aborted run stays excluded (its peak is a floor, not a measurement).

**Oracles:** a lane with one completed FAIL and no PASS reports a measured footprint under `--include-failed` and stays omitted without it; a killed run is never included; a controlled wrong implementation that includes timeouts must fail.

**Spec owner:** SPEC R-44 (footprint manifest).

### Status — FIXED 2026-10-04 (rev 49). `footprint` can include history-eligible completed profiled FAIL runs with explicit `--include-failed`; timeout, abort, dirty, infrastructure, and unprofiled runs remain excluded.

## RG-69 — `footprint --write` has no per-lane mode: a package cannot record one new lane's footprint without regenerating the whole tracked manifest

**Provenance:** found in dstdns 2026-09-30, P224 (adds a lane); `nyxloom-trove/decisions.md` D-572 section 3. run-gate rev 46.

**Observed:** `run-gate footprint --help`: "A LANE filter queries one lane's numbers but is REFUSED together with --write (a partial write would silently drop every other lane's data)" (`cmd_footprint`, `run_gate.py` ~l.4104-4140: `fail("footprint --write does not accept a LANE filter ...")`). `--write` rewrites all of `run-gate.footprint.json`, a TRACKED file, from the local history store. A package that adds one lane and wants its first footprint committed must regenerate every lane's entry from whatever the current checkout's store holds (a worktree store may lack other lanes' history), producing a large unrelated diff and risking dropped entries.

**Why it matters:** dstdns makes `footprint --write` controller-only after merge to avoid exactly that conflict-prone whole-file rewrite, so a new lane's manifest entry lands a step late and packages cannot carry their own calibration.

**Proposed fix direction:** `footprint --write --lane X` (repeatable) that reads the existing manifest and replaces or adds only the named lanes' entries, leaving every other entry byte-identical (the refusal's stated reason, silent drop, does not apply to a merge). Refuse by name when the named lane has no completed, profiled run; refuse when the existing manifest is absent or unparseable rather than writing a one-lane file silently.

**Oracles:** with a two-lane manifest, `--write --lane B` changes only B's entry (other entry bytes equal); unknown/unprofiled lane refuses with exit 2; a missing manifest refuses; a controlled wrong implementation that writes only the named lane (dropping others) must fail the first oracle.

**Spec owner:** SPEC R-44 (footprint manifest).

### Status — FIXED 2026-10-04 (rev 49). Repeatable `footprint --write --lane NAME` merges selected lane entries into an existing schema-1 manifest and preserves other entries.

## RG-70 — no canonical way to exec a repo script or command inside a worktree's test-runner (the app-runtime dependency closure)

**Provenance:** found in dstdns 2026-09-30, P224 (needs `scripts/regen-openapi.py` run in the app's dependency closure against the package worktree). run-gate rev 46.

**Observed:** run-gate's request surface is `<lane> [--worktree] [--allow-dirty] [--base] [--fresh]` plus `--list/doctor/history/footprint/validate-pointers`; `main()` (`run_gate.py` ~l.8444) has no verb that runs an arbitrary command in a lane environment. A task that must run a repo script in the runner's dependency closure (the cockpit venv carries different pins, so it is not equivalent) has no sanctioned path: implementers improvise `docker exec <container> ...`, guessing the container name (RG-24 shows the name resolution is worktree-sensitive, Mode-A shared vs Mode-B per-worktree) and the in-container path of a worktree (Mode-B mounts only the worktree subtree). The one declared-lane route requires a committed lane per script.

**Why it matters:** AGENTS.md 4.9 makes run-gate the canonical tool and forbids manual alternatives, yet nothing canonical exists here, so the mandated behaviour cannot be followed; improvised `docker exec` also bypasses the exec lock (RG-39) and profiler.

**Proposed fix direction:** `run-gate exec [--environment ENV] --worktree <wt> -- <cmd...>`: resolves the environment's container exactly as a lane would (same worktree-aware resolution as RG-24), translates the worktree path into the container namespace, takes the RG-39 exec lock, runs with the lane env forwarding, and returns the command's exit status. Default environment: the project's primary runner. Alternatively, document a `kind = "command"` lane idiom with a caller-supplied argv (less general). Refuse by name when the environment is not a container environment.

**Oracles:** `run-gate exec --worktree <wt> -- python -c 'import sys; print(sys.prefix)'` runs in the runner, not the host; a Mode-B worktree resolves to its own container; `--dry-run` prints the resolved container and translated cwd; a concurrent lane on the same container is serialized by the lock; a controlled wrong implementation that runs on the host must fail the first oracle.

**Spec owner:** SPEC (exec-mode environments, R-41).

**Consumer evidence, 2026-10-03 (dstdns tooling-boundary pass).** The improvised "find the runner and `docker exec`/`docker run` a clone of it" pattern is already copied five times in dstdns: `scripts/p128-assay-schema.sh`, `p129-assay-schema.sh:41-51`, `p165-assay-schema.sh:47-92`, `p167-assay-schema.sh`, `p201-assay-schema.sh:100-145`. Each one derives the runner's name itself (see also `scripts/schema-gate.sh:214-219` and `scripts/config_helper.py:151,180`), five derivations in total. Each then reads the image, binds, network and cgroup parent back off the running container with `docker inspect`. `p129:34-39` even re-implements ciu's instance-id hash (`sha256(path)[:6]`), which ciu 7.15's base36 derivation (vbpub@d1eb98770) has made silently wrong. The per-package `P1xx_TEST_RUNNER`/`P1xx_PHYSICAL_REPO_ROOT` names hand-listed in dstdns's `[environments.test-runner] forward_env` (`run-gate.toml:12-21`) are this pattern's plumbing. dstdns would delete all of it given RG-70 plus RG-75 (lane-scoped throwaway services). ciu already ships the container-resolution half for managed worktrees (`ciu worktree exec --target`, S16.7, with a mount proof). RG-70 should reuse that resolution rather than add a fourth one. **v8: absorb** (`ciu instance exec --env`, SPEC-V8 S14.6.3).

**Controller ruling (2026-10-04): absorbed into ciu CIU-118.** The supported
`ciu exec` path owns this capability and its dstdns caller migration; RG-70 is
not a separate run-gate v7 feature. CIU-118 tracks the implementation, so
this entry is not marked FIXED until that work lands.


## RG-71 — the `schema` lane cannot take a single-file target: iterating on one `tests/schema/` test costs the full lane every time

**Provenance:** found in dstdns 2026-09-30 (schema-test authoring in the P2xx packages). run-gate rev 46.

**Observed:** dstdns's `[lanes.schema]` is a fixed-argv command lane (`["{worktree}/scripts/schema-gate.sh", "{worktree}"]`). The script itself accepts an optional second argument `pytest-target` (header: "scripts/schema-gate.sh <worktree-path> [pytest-target], defaults to tests/schema"), but run-gate has no way to forward a per-invocation argument into a declared lane's argv, so the only way to run one new test is to invoke the script by hand, bypassing run-gate (AGENTS.md 4.9 forbids that as a first choice) and its lock, profiler and history. Every iteration therefore pays the full schema lane (throwaway PG provisioning plus the whole suite).

**Why it matters:** an implementer adding one `tests/schema/` test iterates many times; the canonical path makes each iteration a full-lane run, which pushes agents to the manual path.

**Proposed fix direction:** a generic, declared pass-through for command lanes: lane key `accepts_args = true` (or `passthrough = "pytest"`) plus CLI `run-gate <lane> [...] -- <args>` appended to the lane argv after `{worktree}` substitution, refused by name for lanes that do not opt in and for composite and assay lanes. Such ad-hoc runs are recorded in history as `selective` and never count toward the footprint manifest or a ship signal (a single-file run is not a gate). The throwaway-DB provisioning stays in the script, so the isolation is unchanged.

**Oracles:** `run-gate schema --worktree <wt> -- tests/schema/test_x.py::test_y` runs only that node and still provisions and disposes the throwaway DB; the same flag on a non-opted lane refuses by name; history marks the run selective; a controlled wrong implementation that lets a selective run satisfy the gate lane's freshness check must fail.

**Spec owner:** SPEC (lane argv construction).

### Status — FIXED 2026-10-04 (rev 49). Command lanes may declare `accepts_args = true`; invocation arguments after `--` are appended after the lane argv. Assay and composite command lanes refuse them.

## RG-72 — a failed `kind = "assay"` lane leaves no failing-test evidence in the gate log, and the next run overwrites its verdict

**Provenance:** found in dstdns 2026-10-02 (package P231). run-gate rev 46, assay 7.2.0. dstdns@10f9df30, branch `p231-doc-truth`.

**Observed:**
- A composite `run-gate gate` failed its `assay` member. The log shows only `mock: FAIL/COMMAND_FAILED (exit 1)` and `lane 'assay' exit 1`. It carries no failing node ids, no error kind and no pytest summary line.
- The implementer read the detail (5 errors in `tests/config/test_fault_boundary_completeness.py`) from `.assay/verdict-mock.json` before re-running.
- The green re-run then rewrote `.assay/verdict-mock.json` and `.assay/progress-mock.jsonl` at the same fixed paths. Nothing kept the failing run's evidence.
- The failure looked like a load-induced flake: the same run's test-runner member peaked at 3598 MiB and stalled 35.8 s on memory with two gates sharing one container. Without the verdict, the flake can no longer be characterised. Was it an error kind, a timeout or an OOM-kill?

**Why it matters:**
- A flake is a defect to diagnose (dstdns withdrew its flake-retry allowances). The canonical retry destroys the only evidence.
- Agents that keep a log of each run still lose the detail, because the log never had it.

**Proposed fix direction (either; both preferred):**
- (a) On a non-PASS assay verdict, run-gate prints a bounded failure digest into the lane log: the verdict status, the first N failing or erroring node ids with their exception class, and the pytest summary line.
- (b) run-gate copies a non-PASS verdict, and its progress file, to a run-keyed path, e.g. `.assay/failed/<lane>-<run-id>.json` or under the run-gate history store, before the next run of that lane can overwrite it. The run record names that path.

**Oracles:**
- A lane forced to fail (one asserting-false test) prints that node id and `AssertionError` in the gate log.
- After a subsequent green run of the same lane, the failed run's verdict is still readable at the path the failed run's record names.
- A PASS run writes no digest and no archived copy.

**Spec owner:** SPEC (assay-kind lane result handling) and the run-history store.

### Status — FIXED 2026-10-04 (rev 49). Non-PASS assay verdicts print a bounded digest and archive current verdict/progress under `.run-gate/failed/<lane>/<run_id>/`, retaining ten per lane and recording the archive path.

## RG-73 — an `ephemeral` environment cannot stand in for a per-worktree runner: its image is a fixed literal and the judged worktree is not mounted at the image's canonical root

**Provenance:** dstdns tooling-boundary pass, 2026-10-03 (`dstdns/docs/proposals/TOOLING-BOUNDARY-2026-10.md` Q1). run-gate rev 46, ciu 7.15.1.

**Observed:**
- dstdns judges almost every worktree inside main's one persistent exec runner (GUIDE §3 "Mode A"). That runner bakes main's identity: `REPO_ROOT={{ ciu.repo_root }}` and `PYTHONPATH={{repo_root}}:{{repo_root}}/scripts` (`tools/test-runner/ciu.compose.yml.j2:41` and `:29-57`).
- Consequences of that bake:
  - Every worktree lane must `cd {worktree}` (`run-gate.toml`, 11 lanes) and must defend against main's `REPO_ROOT` (D-362; `scripts/gate-slot.sh:33-37` pins it by hand).
  - A worktree can never change the test runtime it is judged in, because the image and its dependency closure are main's.
- The worktree-owned alternative today is a full Mode-B ciu stack with its own exec runner. It costs a stack (vault, consul, redis, postgres) per worktree on a host capped at 2-3 stacks (dstdns GUIDE §3.0).
- run-gate's own `mode = "ephemeral"` (R-07/R-15) would remove the stack entirely for hermetic lanes (mock suite, assay R0-R2, frontend unit, schema with a throwaway DB). Two gaps stop dstdns from using it:
  1. `[environments.<n>] image` is a literal string. A worktree that rebuilt its runner image (new pins in `requirements.txt`) has no way to name its own tag, and it must not overwrite the shared `:latest` (dstdns GUIDE §3.2 "Images"; ciu CIU-117).
  2. R-15 dual-mounts the REPO at its physical and namespace paths. The image's canonical root (`WORKDIR /workspaces/dstdns`, `tools/test-runner/Dockerfile:167`, with `PYTHONPATH` baked to it) is therefore main's checkout, not the judged worktree. A lane that imports by `PYTHONPATH` silently imports main's copy of `libs/` and `scripts/`. UNVERIFIED whether the importlib import mode (`pytest.ini`) masks this for every dstdns lane; the hazard class is D-362's.

**Why run-gate owns it:** it constructs the ephemeral container's argv (image, mounts, workdir). A consumer cannot change them.

**Proposed contract:**
- (a) `image` may be derived per checkout. Proposed form: `image_from_ciu = "<stack>.<service>"`, resolved through the judged checkout's rendered ciu config (the RG-24/RG-37 resolution path). With v8, this becomes `image_from` (SPEC-V8 S16.4). A literal stays legal. The resolved image reference and its id are recorded in the run record.
- (b) `mount_worktree_at = "<abs path>"`: the judged worktree, never the repo root, is bind-mounted at that path. It becomes the container workdir, and `{worktree}` substitutes to it. The shared `.git` is mounted where the worktree's gitfile expects it (dstdns already does this in compose, `ciu.compose.yml.j2:84-124`). Refuse when the path collides with another declared mount.
- (c) `doctor` warns when an ephemeral environment's image declares `WORKDIR`/`PYTHONPATH` under a path that is not `mount_worktree_at`.

**Oracles:**
- A lane `python -c 'import scripts, sys; print(scripts.__file__)'` run with `--worktree W` prints a path under W's mount, not main's.
- With `image_from_ciu`, a worktree whose rendered config names `dstdns/test-runner:w1` runs that image; `--dry-run` shows it.
- Two worktrees run the same ephemeral lane concurrently with no shared container.
- A controlled wrong implementation that keeps R-15's repo-root dual mount while also mounting the worktree at `mount_worktree_at` must fail the first oracle when main's checkout carries a different `scripts/__init__.py`.

**Spec owner:** SPEC R-07 (environment keys), R-15 (container lanes), R-23 (mounts).

**v8: absorb** (S16.4 `image_from`; the mount rule belongs in S16.4.3's mount proof).

**Amendment (2026-10-03, ciu v8 adversarial review, dstdns D-658; SPEC-V8 draft.9 S16.12 is the per-oracle table; not yet built, amended before the build):** drop proposed contract (b) `mount_worktree_at`. dstdns D-653 Q7 decided option A: the worktree is mounted at **its own path** plus the git common directory (read-write, at its own path, derived from the gitfile and its `commondir`); an image may bake no checkout path or content (`WORKDIR`, `PYTHONPATH`, a `COPY` of imported sources, an editable install of a copied tree), and `doctor` (c) checks that instead. `workdir` defaults to the checkout's own path and `{worktree}` is that path. Oracles 1, 2 (as `image_from`), 3 and 4 stand; in a linked worktree `image_from` resolves to the instance-scoped tag when present locally, else the primary's declared reference, and records the image id in the run record. Reference: ciu `SPEC-V8.md` S16.4.6, S16.4.9, S16.11.6.

**Amendment 2 (2026-10-03, dstdns D-666; reviewed against SPEC-V8 draft.11; still not built):** the premise of Observed is corrected, not the contract. Judging every worktree inside main's one persistent runner ("Mode A") is the **retired** mode (D-647 #2), and the worktree-owned runner is not "a full Mode-B ciu stack ... a stack (vault, consul, redis, postgres) per worktree": it is **one stack**, the project's `tools/test-runner`, started in the worktree with `ciu up --dir tools/test-runner` (shared image, zero rebuild; dstdns GUIDE §3.4), whose cost is an idle container (D-666's resource note); and the lane `cd {worktree}` and the `REPO_ROOT` pin in `gate-slot.sh` were Mode A's defences. This entry's contract (an `ephemeral` environment whose image is derived per checkout, `image_from`) therefore stays as the **stackless** alternative for hermetic lanes — it lets a worktree change its runtime without starting any container of its own — and is no longer the route out of Mode A. The decision that a judged worktree resolves **its own** runner, never main's, is RG-79's. Everything in Amendment 1 (own-path mount plus the git common directory, S16.4.6, S16.4.9, S16.11.6) is current in draft.11; the v8.2 split (remote deployment) does not touch it.

## RG-74 — the post-merge trunk base and the composite-member base are consumer scripts, not run-gate derivations

**Provenance:** dstdns tooling-boundary pass, 2026-10-03. run-gate rev 46.

**Observed:**
- RG-51 (FIXED, rev 40) made a worktree lane's default base the ciu-recorded fork point (CIU-106). Two cases remain consumer-scripted. dstdns's `scripts/gate-base.sh` (79 lines, 93 references) covers both.
- **(1) Post-merge trunk gate.** On the trunk, with a `--no-ff` merge commit at HEAD, the correct base is `HEAD^1` (`gate-base.sh:109-111`). run-gate derives nothing there, because no worktree record exists on the trunk. assay's first-parent rule (RG-54) only coincidentally agrees, and only for merge commits.
- **(2) Composite members.** dstdns's composite `[lanes.gate]` (`run-gate.toml:511`) is a string conjunction. It must compute `GATE_BASE="$(bash {worktree}/scripts/gate-base.sh {worktree})"` itself and pass it to the one changed-line member, because a conjunction's members never see the parent's resolution. `--base` is refused on the composite by design (dstdns AGENTS §6.1).
- `gate-base.sh` also hardcodes the trunk name `main`, with an `origin/main` fallback (`:95-97`). That is the RG-51 stale-remote hazard again, in consumer code.

**Why run-gate owns it:** it already owns comparison-base resolution (`resolve_comparison_base`, R-35, RG-51) and the composite's invocation. Every consumer with a merge-commit trunk workflow re-writes the same three cases.

**Proposed contract:**
- (a) An additional resolution step after the RG-51 record and before `@{upstream}`: if the judged tree's HEAD is a merge commit on the declared trunk, the base is `HEAD^1`. The trunk name is declared once (`[project] trunk = "main"` or the central config), never guessed. Recorded as `base_source = "trunk-merge-first-parent"`.
- (b) `kind = "sequence"` lanes (RG-37's second alignment item, ciu proposal N21) resolve the base once and pass it to every member whose assay lane reports `base_source = "request"`. This removes the need for `GATE_BASE` plumbing.
- (c) A non-merge HEAD on the trunk refuses by name, which is gate-base.sh's case 3.

**Oracles:**
- On a trunk whose HEAD is a merge commit M, `--dry-run` reports base `M^1` with source `trunk-merge-first-parent`.
- A sequence lane passes that base to its `base_source = "request"` member only.
- A non-merge trunk HEAD refuses with exit 2.
- A controlled wrong implementation using `merge-base HEAD trunk` (which equals HEAD on the trunk) must fail the first oracle.

**Spec owner:** SPEC R-35 (comparison base), R-25 (conjunctions) / the sequence-lane section once written.

**v8: absorb** (SPEC-V8 S16.5.4/S16.5.5: sequence lanes and request-base pass-through. The trunk-merge step is missing there too).

**Amendment (2026-10-03, ciu v8 adversarial review, dstdns D-658; SPEC-V8 draft.9 S16.12 is the per-oracle table; not yet built, amended before the build):** oracle 3 ("a non-merge trunk HEAD refuses with exit 2") becomes NOT_RUN/`no-base`, exit 3, after RG-78: a base that cannot be derived is a precondition refused before execution, not a configuration error. The same holds for `--base` outside R-35b's charset. Reference: ciu `SPEC-V8.md` S16.7.4, S16.8.

### Status — FIXED 2026-10-04 (rev 49). `[project].trunk` derives a merge-at-trunk first-parent base or returns NOT_RUN/`no-base`; native sequences resolve once and pass it only to delegating members.

## RG-75 — no lane-scoped throwaway service (database): every schema/mutation lane provisions and tears down its own Postgres by hand

**Provenance:** dstdns tooling-boundary pass, 2026-10-03. Related: RG-18 (pg_dump version guard, OPEN, scoped dstdns-side), RG-70.

**Observed:**
- dstdns has seven hand-rolled copies of "start a throwaway Postgres on the instance network, wait for it, create roles, apply DDL, export a DSN, run tests, remove it":
  - `scripts/schema-gate.sh` (220 lines; `:94` image read off the running stack, `:102` literal `dstdns-schemagate-$$`, `:144-149` `docker run` with tmpfs, `:160-165` readiness loop)
  - `scripts/sql-mutation-gate.sh` (177)
  - `scripts/p128-`, `p129-`, `p165-`, `p167-` and `p201-assay-schema.sh` (119-292 lines each, each copied from the previous one)
- They run inside the exec runner and spawn sibling containers through the mounted docker socket. Each one re-derives the runner's identity, network and cgroup parent (RG-70 note). Each one owns its own cleanup on signal paths. `p167-assay-schema.sh:14-24` documents that the copied derivation branch is dead and broken.
- None of this is dstdns-specific. Any project with a relational schema needs a disposable database for schema and mutation lanes.

**Why run-gate owns it:** run-gate owns the lane lifecycle (start, budget, kill, evidence, `finally` cleanup, inflight record and re-attach, RG-35). A sidecar started by a lane script escapes all of it. A killed client leaves the sidecar running, which nothing reaps.

**Proposed contract:**
- `[environments.<n>.services.<s>]` (or per lane) with:
  - `image` (literal or `image_from_ciu`, RG-73)
  - `tmpfs`
  - `env`
  - `ready` (argv run in the service until exit 0, bounded)
  - `init` (argv run once after ready, e.g. a DDL apply script from `{worktree}`)
  - `expose_env` (e.g. `DSN = "postgresql://...@{host}:{port}/db"`, delivered to the lane)
- The service gets a run-scoped name and network, joins the lane container's network (ephemeral) or the target's network (exec), and is placed in the same slice.
- It is started after admission and removed in the same `finally` as the lane. It is recorded in the inflight record so that a re-attaching client (RG-35) can remove it.
- The `pg_dump` client-version guard (RG-18) becomes a `ready`-time check that the declared client matches the service's server major.

**Oracles:**
- A lane declaring a pg service sees `DSN` and can connect.
- After the lane (PASS, FAIL, budget kill), no container carrying the run's label remains.
- A client killed mid-lane leaves the sidecar, and the next invocation of the lane removes it.
- Two concurrent invocations get distinct services.
- A controlled wrong implementation that removes the service only on PASS must fail the second oracle.

**Spec owner:** SPEC §5 (execution contract), new subsection.

**v8: absorb** (SPEC-V8 S16.4 ephemeral environments with `binds`; a lane-scoped Realization or `[testing.externals]`-style typed service is the natural home).

**Amendment (2026-10-03, ciu v8 adversarial review, dstdns D-658; SPEC-V8 draft.9 S16.12 is the per-oracle table; not yet built, amended before the build):** align with ciu `SPEC-V8.md` S16.4.7. (1) Drop the service keys `env` and `init`: the lane's own argv applies DDL from `{worktree}`; a service has `image`/`image_from`, `tmpfs`, `ready`, `expose_env`, `resources`. (2) Drop the join of an exec target's network: lane services are an `ephemeral`-environment feature, started on the lane's network; an `exec` or `host` environment declaring `services` refuses. (3) `resources.memory_max` is mandatory unless a manifest entry exists, and the service's charge is part of the lane's admission reservation. (4) `expose_env` values may use `{host}`, `{port}` and `{secret}` only. The oracles on `DSN` delivery, removal after PASS, FAIL and budget kill, a killed client's sidecar removed by the next run, and distinct services per concurrent run stand.

**Amendment 2 (2026-10-03, dstdns D-658 rulings; SPEC-V8 draft.9 splits v8.0 from v8.1; still not built):** a lane service's memory is charged inside the lane's admission ticket only when v8.1 admission is on (S21.5.1); with admission off (the default) the service has `resources.memory_max` as its cap and nothing else. The 8.0 oracles are unchanged.

## RG-76 — an external-assay consumer must restate the judge command, its pin and one lane block per assay lane: dstdns's `run-gate.toml` is ~70% boilerplate

**Provenance:** dstdns tooling-boundary pass, 2026-10-03 (`dstdns/docs/proposals/TOOLING-BOUNDARY-2026-10.md` Q3). run-gate rev 46, assay 7.2.0.

**Observed:**
- dstdns's `run-gate.toml` has 2131 lines and 142 lanes. Of those, 118 are `kind = "assay"` lanes, and every one of them carries an identical `[lanes.X.pins.assay] version = "7.2.0", sha256 = "tools/assay/assay-7.2.0.pyz.sha256"` block.
- 102 of them also carry `assay_command = ["python3", "tools/assay/assay-7.2.0.pyz"]`. The other 16 prefix that command with the same `env GIT_CONFIG_COUNT=1 ... safe.directory ...` triple.
- The judge version string is repeated in more than 236 places, so a judge upgrade is a 236-site edit.
- 69 lanes are mutation families (`<target>-r2-{compare,boolop,flips,falsy}`) that differ only in `assay_lane`, which already names a lane that `assay.toml` declares.
- Per-package variables are listed twice: once in `required_env` and again in the environment's `forward_env` (`run-gate.toml:12-21`).
- vbpub-internal projects avoid all of this through source mode (R-08: `assay_command` and `pins` omitted). External consumers cannot use source mode.

**Why run-gate owns it:**
- run-gate already reads `assay lanes --json` (RG-25/RG-26) to derive each lane's `base_source` and toolchain. The lane list itself is therefore derivable from the same document. A consumer restating it creates a second source of truth that drifts. dstdns keeps a meta-test, `tests/config/test_assay_pin_integrity.py`, just to hold the two in step.

**Proposed contract:**
- (a) A top-level `[assay]` table holding `command`, `pins` and `environment`, inherited by every `kind = "assay"` lane that does not override it.
- (b) `[assay] import = "all" | [<glob>...]` auto-declares one `kind = "assay"` lane per lane that `assay lanes --json` reports. An explicit `[lanes.<n>]` may still override an imported lane by name. `--list` marks imported lanes as such.
- (c) A lane's `required_env` is forwarded automatically in its environment; `forward_env` then becomes only the extra set.

**Oracles:**
- With (a) and (b), a fixture `assay.toml` with three lanes and a `run-gate.toml` holding only `[assay]` lists three runnable lanes, each verifying the single pin.
- An explicit lane override wins.
- A lane removed from `assay.toml` disappears from `--list` with no edit to `run-gate.toml`.
- A `required_env` variable reaches the container without being listed in `forward_env`.
- A controlled wrong implementation that imports lanes but skips pin verification for imported lanes must fail the first oracle (by tampering with the sha256 sidecar).

**Spec owner:** SPEC R-06/R-08 (config schema), R-24 (required_env).

**v8: absorb** (`[testing.judge]` already declares the judge once, S16.3; lane import from `assay lanes --json` belongs in S16.5/S16.7).

**Amendment (2026-10-03, ciu v8 adversarial review, dstdns D-658; SPEC-V8 draft.9 S16.12 is the per-oracle table; not yet built, amended before the build):** drop (c) and its oracle ("a `required_env` variable reaches the container without being listed in `forward_env`"). ciu `SPEC-V8.md` S16.4.5 requires a lane's `required_env` to be a subset of the environment's available set (`forward_env` ∪ `env` ∪ binding variables ∪ lane-service `expose_env` ∪ the fixed `CIU_*` variables), checked by `ciu check`, and an auto-forward would make that check vacuous. (a) and (b) and their oracles stand, with names only (no globs) in `import`.

**Amendment 2 (2026-10-03, dstdns D-666; reviewed against SPEC-V8 draft.11; still not built):** key mapping for the v8 absorption, so the v7 build and the port stay one shape. v7 `[assay] command` + `pins` (a version and a `sha256` **sidecar file**, e.g. `tools/assay/assay-7.2.0.pyz.sha256`) is v8 `[testing.judge] command` + an inline `sha256` (the digest, not a path; S16.3 (a)); an estate-internal consumer's `source` mode is `[testing.judge] source`; `[assay] environment` and `import = "all" | [names]` is `[testing.judge] import = { environment = "<e>", lanes = "all" | [<names>] }`, names only (S16.3). Oracle 1's "tampering with the sha256 sidecar" is, in v8, a digest that differs from `testing.judge.sha256`, NOT_RUN/`judge-digest` (S16.3.3). The `[testing.judge]` version floor (`version`) has no v7 `[assay]` key: run-gate's own pin check (RG-33's judge floor) is its v7 form. Nothing else in Amendment 1 changes.

### Status — FIXED 2026-10-04 (rev 49). `[assay].import = { environment = "...", lanes = "all" | [names] }` imports from `assay lanes --json` with the shared command and pin. The nested import shape matches CIU v8.

## RG-77 — the assay-lane `--state-dir` contract (RG-38) and its repair (RG-49) are documented nowhere a consumer reads

**Provenance:** dstdns P235 (agent-instructions restructure, D-648), 2026-10-03. run-gate rev 46.

**Observed:**
- SPEC `R-38` documents the unconditional `--resume --progress .assay/progress-<lane>.jsonl` (RG-33). But it never mentions `--state-dir <repo>/.run-gate/assay-state/<project-relative-path>/`, which RG-38 added to every assay-kind lane on all three runner kinds. `grep -c state-dir SPEC.md README.md CONSUMERS.md LANE-AUTHORING.md` gives 0 for each file. Only CHANGES.md and this backlog carry it.
- At filing, RG-49's proposed repair (prove containment, then `chown` a root-owned synthetic parent in a partial-bind-mount worktree container) was likewise only in CHANGES.md and backlog prose. D-666 superseded that repair on 2026-10-04 with a read-only preflight and a declared durable mount root; container-layer `chown` is rejected because it loses resume state when the runner is recreated.
- The `run-gate-cli` skill mentions none of resume, progress or state-dir.
- dstdns therefore had to carry two paragraphs restating this in its own AGENTS.md §6.1, including "a fresh worktree keeps resume state" and "a permission error under `.run-gate/assay-state/` means check the run-gate version first". P235 cut them to a pointer (D-648 "cut now, file gaps"). That pointer currently lands on the skill and CHANGES.md, so a consumer can learn the behavior only from the changelog.

**Why run-gate owns it:** run-gate constructs the argv, so the state-dir location, its keying (the checkout owning the shared `.git`, plus the project-relative path) and its durability across throwaway worktrees are run-gate's contract, not the consumer's.

**Initial proposed contract (superseded by the D-666 amendment below):**
- Extend SPEC `R-38` (or add a sibling rule) to state the `--state-dir` argument and its location derivation. It should also say why that location survives a deleted worktree, state the initial containment-then-chown proposal, and name the exact refusal a failure produces.
- Add a "Resume and progress" section to the `run-gate-cli` skill covering the three flags, where to tail progress, and the version floor.
- Add a one-paragraph mention to CONSUMERS.md.

**Oracles:**
- A doc-drift test asserts that every flag appended unconditionally by the assay argv builder (`--resume`, `--progress`, `--state-dir`) is named in SPEC and in the skill.
- A controlled wrong implementation, a new unconditional flag added to the builder without a SPEC mention, must fail that test.

**Spec owner:** SPEC R-38.

**v8: absorb** (the v8 gate inherits the argv builder, so the same drift test belongs there).

**Amendment (2026-10-04, D-666):** RG-49 now uses a read-only preflight in the lane's environment, `[environments.<name>].state_root` for a durable mount at another container path, and NOT_RUN/`state-mount` when the root or deepest existing state ancestor is unavailable or unwritable. The environment owner provides the durable read-write mount; run-gate creates only the per-project descendants beneath it. An indeterminate probe is ERROR, never evidence that the root is writable.

### Status — FIXED 2026-10-04 (rev 50): SPEC, skill, README, DESIGN-GUIDE, and CONSUMERS document `--resume`, `--progress`, `--state-dir`, the 5.2.0 floor, durable path, `state_root`, and preflight result. RG-49 implementation, registered gate, and fresh Dstdns worktree-runner acceptance are complete.

**Revision 54 correction:** project paths inside the checkout keep their
existing relative state key. For an external project path, run-gate hashes the
resolved absolute path with SHA-256 rather than replacing slashes with hyphens;
the latter collided for distinct paths such as `/a-b/c` and `/a/b-c`.

## RG-78 — adopt v8's closed exit table and explicit environment modes now, not at the ciu8 cutover

**Provenance:**
- dstdns D-654: the operator chose v8 Q11 option A and directed a backport into run-gate, because ciu v8 is far away.
- ciu v8 proposal rev 4.1 (vbpub@1602bbbac) §4.1.10 and Q11.
- The analysis behind the choice is in dstdns `nyxloom-trove/decisions.md` D-654.

**Observed:**
- `run-gate <lane>` passes the lane's own exit code through. RG-11 reserved 2 (configuration/refusal) and 3 (infrastructure) for gate refusals, so a lane exiting 2 (pytest "interrupted"/usage error) or 3 cannot be told apart from a gate refusal, and pytest's 5 ("no tests collected") passes through as an unexplained non-zero.
- This is the bug class behind dstdns `gate-slot.sh`'s exit-75 rerun (D-646): the wrapper's own code collided with a lane's.
- Agents misread raw lane codes repeatedly: vitest exiting 1 with every test passing (RG-45), and "killed" statuses that mask a finished run.
- The built-in `host` environment is a container, while `bare-host` is the subprocess. That is a standing naming trap.

**Why run-gate owns it:** the gate is the only component that knows both the lane's raw result and its own refusal reasons. Every consumer (nyxloom gate pointers, cmru, CI, shell wrappers, agents) otherwise reconstructs the distinction from logs.

**Proposed contract (v8 S16 Q11 option A, verbatim semantics):**
- **Exit table, closed:**
  - PASS 0;
  - FAIL 1 (the lane ran and failed, including a mapped "collected nothing");
  - ERROR 2 (the gate could not run the lane because of config or infrastructure; replaces RG-11's 2/3);
  - NOT_RUN 3 (a precondition refused before execution: dirty tree, missing base, admission);
  - BUDGET 4 (the budget was exceeded and the lane is resumable).
  - No other code ever leaves run-gate.
- **LaneResult:** the JSON result (`--json`) and a one-line human summary always carry `verdict`, the lane's raw `exit_code`, `reason` (for ERROR/NOT_RUN, naming the fix), and the log path.
- **Raw-code mapping:** a documented table per lane kind. For example: pytest 5 → FAIL; a vitest non-zero with every test passing stays FAIL but `reason` names RG-45; a timeout or kill → BUDGET or ERROR as appropriate. The table is declared per lane where it differs from the default.
- **Environment modes:** each environment declares `mode = "ephemeral" | "exec" | "host"`, and implicit built-in names go away. `host` means a bare subprocess.
- **Cutover (D-652 Q13 style, atomic):** a `run-gate migrate` (or a documented one-shot) rewrites consumer `run-gate.toml` environments and lists wrapper scripts that branch on raw codes. Pin tests are reclassified. The CHANGES entry carries a consumer checklist: nyxloom `[gates.*]` pointers (0/non-0, unaffected), dstdns `gate-slot.sh` (deleted by RG-67) and `gate-base.sh`, CI workflows, cmru tester-gate.

**Oracles:**
- A lane exiting 2 yields FAIL with `exit_code: 2`, distinguishable from an ERROR refusal.
- A pytest lane collecting nothing yields FAIL with a reason.
- A dirty tree yields NOT_RUN.
- A budget overrun yields BUDGET, and `--resume` then continues.
- No code outside 0–4 can be produced (fuzz the lane's exit across 0–255).
- An environment without `mode` refuses with ERROR, naming the key.
- A controlled wrong implementation that passes the raw code through for FAIL must fail the first oracle.

**Spec owner:** SPEC R-04 (exit codes), R-18 (verdict discipline), R-06 (environments). The ciu8 port inherits the same table (parity test).

**Amendment (2026-10-03, ciu v8 adversarial review, dstdns D-658; SPEC-V8 draft.9 S16.12 is the per-oracle table; not yet built, amended before the build):** (1) The fourth verdict is `BUDGET_EXCEEDED`, not `BUDGET`: it is the token assay emits and the ciu `SPEC-V8.md` S16.8 vocabulary; "BUDGET 4" above is shorthand for it, and the LaneResult key is `verdict`. (2) Drop the oracle "a budget overrun yields BUDGET, and `--resume` then continues": v8 has no `ciu gate --resume` (assay resumes itself, S16.7.2), so the oracle is "a budget overrun yields `BUDGET_EXCEEDED` and the lane is resumable by assay's own state". (3) "missing base" is NOT_RUN with reason `no-base`; a lock the gate cannot take within `--admission-wait` is NOT_RUN/`lock-busy`; a judge-artifact digest mismatch is NOT_RUN/`judge-digest`; a verdict without judge provenance is ERROR. (4) assay's six outcomes map `NO_MEASUREMENT` and `INCONCLUSIVE` to FAIL, keeping the raw outcome as `assay_outcome`. (5) A multi-lane invocation exits as a sequence does (PASS iff all passed, else the first non-PASS in argument order); `gate exec` returns the command's own status and is outside the table; sub-verbs (`history`, `footprint`, `doctor`, `--list`) use the general table. Reference: ciu `SPEC-V8.md` S16.8, S16.8.2, S16.8.2a.

**Amendment 2 (2026-10-03, dstdns D-658 rulings; SPEC-V8 draft.9 splits v8.0 from v8.1; still not built):** the closed exit table and the explicit modes stay **8.0** scope (the backport of D-654 is not delayed). Corrections to Amendment 1: a lock the gate cannot take is bounded by `--lock-wait D` (default 10 m), not `--admission-wait`, and expires as NOT_RUN/`lock-busy`; the NOT_RUN reason `no-headroom` and exit 4 as an admission refusal belong to **v8.1** (SPEC-V8 S21.4.6) and do not exist when admission is off; the closed reasons of 8.0 are `realness-mismatch`, `service-down`, `environment-down`, `environment-mismatch`, `env-missing`, `external-missing`, `external-down`, `dirty-tree`, `lock-busy`, `no-base`, `judge-floor`, `judge-digest`, `provenance-mismatch` (SPEC-V8 Appendix E). Oracles that exercise admission move to the v8.1 parity set.

**Amendment 3 (2026-10-03, dstdns D-661; SPEC-V8 draft.10 S21; still not built):** Amendment 2 moved the NOT_RUN reason `no-headroom` to v8.1; with the count mode in 8.0 it is an **8.0** reason: a lane whose ticket waits past `--admission-wait` (default 10 m), or whose published limit is unreadable under `unreadable_policy = refuse`, is NOT_RUN/`no-headroom`, **exit 3** in the gate's closed table (the NOT_RUN class; 4 stays BUDGET_EXCEEDED). The closed reasons of 8.0 are therefore `realness-mismatch`, `service-down`, `environment-down`, `environment-mismatch`, `env-missing`, `external-missing`, `external-down`, `dirty-tree`, `no-headroom`, `lock-busy`, `no-base`, `judge-floor`, `judge-digest`, `provenance-mismatch` (SPEC-V8 Appendix E, `not_run_reasons`). Exit code 4 for `ciu up`/`ciu dev` as an admission refusal and the stack placeholder remain v8.1 (S21.4.4, S21.4.6). The flags `--admission-wait D` and `--override-admission` belong to `ciu gate` in 8.0 and are accepted and ignored with one notice while admission is disabled (S21.1.3), so a pointer or CI line that passes them never breaks.

**Amendment 4 (2026-10-03, dstdns D-666; reviewed against SPEC-V8 draft.11; still not built):** (1) the "Cutover" checklist line "dstdns `gate-slot.sh` (deleted by RG-67)" is corrected: `gate-slot.sh` is deleted by **RG-80** (the count mode), before v8. (2) `no-headroom` (NOT_RUN, exit 3, Amendment 3) exists in the v7 closed reason set only once RG-80 lands; until then run-gate never produces it, and the accepted-and-ignored flags `--admission-wait` and `--override-admission` do not exist in the v7 CLI either (they arrive with RG-80, accepted-and-ignored while its switch is off, S21.1.3). (3) RG-79's refusal (no rendered config in the judged worktree; the worktree's own runner is down) is ERROR/exit 2 on the v7 line and, after this entry, `environment-down` NOT_RUN/exit 3 for a derived runner that is not running (S16.4 `exec`: "NOT_RUN/`environment-down` when not healthy"), while a worktree with **no rendered config at all** stays a configuration refusal (ERROR, exit 2; S16.11, S1.5.3). Draft.11's v8.2 split (remote deployment) changes nothing here; the closed reason list is unchanged (`not_run_reasons`, 14 values).

### Status — FIXED 2026-10-04 (rev 49). Normal dispatch returns `LaneResult` through `finish()`, command exit bytes map to PASS/FAIL without escaping, and every environment declares one of the three modes.

## RG-79 — exec-mode resolution silently falls back to main's runner when the judged worktree has no rendered ciu config; it must refuse and name "start this worktree's own test-runner"

*Originally filed 2026-10-03 as: "exec-mode container resolution keys on the presence of a rendered `ciu.global.toml` in the judged worktree, so a stray render breaks every exec lane".*

**Reframe (2026-10-03, dstdns D-666; supersedes the framing of everything below, which stays as filed).** The per-worktree runner is the **design**, not an accident: a worktree contains its own test-runner and ciu starts it there (`ciu up --dir <test-runner stack>` in that worktree; shared image, zero rebuild; dstdns GUIDE §3.4), and `ciu worktree` gives the worktree a unique instance id for exactly that purpose (D-647 #2 retired Mode A, which borrowed main's runner; the P224/P236–P240 gates that ran in Mode A tested worktree code but in the retired mode). The defect is the **opposite** of what this entry first said. In `resolve_container_name` the line `if worktree_toml.is_file(): global_toml = worktree_toml` *else the repo's* is a **silent fallback to main's runner**: when the judged worktree has no rendered config, run-gate quietly judges the worktree inside a runner that sits outside the worktree and bakes main's identity. That is a shadowing default (AGENTS §4.2a anti-pattern 1: a literal standing in for a value that has an authoritative source), and the stray-render symptom below is only how it was noticed: a worktree that *does* have a rendered config names its **own** runner, which is right; the refusal's remedy ("`ciu render` ... then `ciu up`") was wrong because it did not say *which* runner to start. Not a Mode-B-only matter: a gate verdict must always name the runner it judged in.

**Corrected contract (replaces "Proposed contract" below).**
- (a) Exec-mode resolution for an environment with no declared `container_name` reads the **judged worktree's own** rendered ciu config and nothing else. There is **no fallback to the repo's config**. The `runner_scope` key proposed below is withdrawn: it added the retired mode back as an option (SPEC-V8 S16.4 `exec_in` resolves the judged checkout's own identities, S1.5.3, with no such scope).
- (b) When the judged worktree has no rendered config, run-gate **refuses** (exit 2, a configuration refusal) naming the remedy exactly: *start this worktree's own test-runner — `ciu up --dir <test-runner stack> --deploy --healthcheck` in that worktree*, and names the worktree path it looked in. When the config exists but the runner it names is not running, the refusal names the same remedy and the container it derived.
- (c) A project that deliberately shares one runner declares it: `container_name = "<literal>"` on the environment (unchanged by this entry), or, in v8, nothing (S16.5.7: one exec target, one lane at a time).

**Oracles (corrected; the RG-24 Mode-B oracle stays).**
1. **RG-24 regression (kept):** a lane judged with `--worktree W`, where W's own rendered `ciu.global.toml` names its own runner, `--dry-run` shows W's runner and the source line names W's file; the lane execs into W's runner and never into main's.
2. W has **no** rendered config and main's runner is up: the lane refuses, exit 2, the text contains W's path and the words `start this worktree's own test-runner` and `ciu up --dir`; it does **not** exec into main's runner.
3. W has a rendered config whose runner is down: the lane refuses with the same remedy, naming the derived container.
4. An environment with a declared literal `container_name` is unaffected: W with no rendered config still execs into that container.
5. A controlled wrong implementation that falls back to the repo config when W has none fails oracle 2 (the original defect); one that keeps the fallback only when the derived container is down fails oracle 3.

### Status — FIXED 2026-10-04 (rev 47; implemented with RG-47)

Exec-mode resolution now reads only the judged worktree's `ciu.global.toml`;
there is no fallback to main. Missing worktree config and a stopped derived
runner both refuse before `docker exec`, with the remedy to start that
worktree's own test-runner using `ciu up --dir <test-runner stack> --deploy --healthcheck`.
A declared literal `container_name` remains the explicit shared-runner choice.

*Related:* RG-80 (the daemon-wide gate cap that replaces the Mode-A runner's two-slot wrapper), RG-73 (the stackless ephemeral alternative), RG-24 (FIXED, the origin of the config lookup); dstdns P241 (`WORKTREE-OWN-TEST-ENV`: GUIDE §3.4, the dispatch templates and skills; the validated recipe) and the proposal row N29 (`CIU-V8-TESTING-GATE-PROPOSAL.md` §4.11).

*Original filing follows unchanged.*


**Provenance:** dstdns P240 carve (SCHEMA-LANE-SELF-DERIVE), 2026-10-03; dstdns D-664 recorded the trap. run-gate rev 46, ciu 7.15.1. CHANGES.md checked: RG-24 introduced the rule, nothing since changes it.

**Observed (source-grounded, `resolve_container_name`, `run-gate.py` ~7729):**
- `worktree_toml = worktree / "ciu.global.toml"`; `if worktree_toml.is_file(): global_toml = worktree_toml`, else the repo's. The docstring says "a worktree that is not itself an adopted instance falls back", but the test applied is file presence, not adoption.
- `ciu.global.toml` is a gitignored RENDERED file. `ciu render`, `ciu up --dir ... --dry-run` and a half-finished `ciu up` all write it into the worktree they run in. A worktree judged in the project's shared runner (dstdns Mode A, the default) then resolves `<project>-<tag>-test-runner` from the worktree's own render, names a container that was never started, and the lane refuses with exit 2.
- The refusal's remedy (`ciu_remedy`: "'ciu render' if stale, then 'ciu up'") is wrong for that project: it prescribes starting a per-worktree stack that the project deliberately does not run (host capped at 2-3 stacks).
- `ciu.worktree-instance.json` cannot be the discriminator: `ciu worktree create` writes it for every managed worktree, Mode A included.

**Why run-gate owns it:** the which-config choice is run-gate's own rule (RG-24); a consumer cannot influence it except by deleting a file that a ciu verb is entitled to write.

**Proposed contract:**
- An environment key (name open, for example `runner_scope = "repo" | "worktree"`) states which tree owns the runner. Default stays today's behavior; dstdns declares `repo` until it adopts per-worktree runners (RG-73). Explicit declaration, not presence sniffing.
- Whatever the scope, when the resolved container is not running and the config it was derived from sits in the judged worktree while another candidate exists (repo-scoped file present), the refusal NAMES the file and both remedies (start that runner, or delete the stray render / declare `repo` scope). It never prescribes only `ciu up`.

**Oracles:**
- A lane judged with `--worktree W`, where W holds a rendered `ciu.global.toml` and `runner_scope = "repo"`, execs into the repo-resolved container; `--dry-run` shows its name and the source line names the repo file.
- Same W with the default scope and a non-running derived container refuses with exit 2, and the text contains the full path of W's `ciu.global.toml` and the word `delete` or `scope`.
- A controlled wrong implementation that silently falls back to the repo config whenever the derived container is not running must fail a third oracle: W declares scope `worktree` and its runner is down, and the lane must still refuse.

**Note (dstdns P240 carve review):** the refusal exit stays 2 (a configuration refusal, not a lane verdict); the existing RG-24 Mode-B behavior (the worktree's own config wins when scope is `worktree`) is the regression oracle for the default path.

**Spec owner:** SPEC (exec-mode container resolution, RG-24 rule).

**v8: absorb** (explicit environment modes, RG-78).

## RG-80 — no daemon-wide cap on concurrent gates: the cross-worktree cap is a consumer-written flock wrapper, so every project re-writes it and the Docker daemon, the one object all gates share, is never consulted

**Provenance:** dstdns D-666 (2026-10-03: "the Docker-name ticket count cap as a run-gate feature, retiring `gate-slot.sh` before v8"), D-661 (SPEC-V8 S21's count mode is 8.0), D-570/D-636 (the 2-gate cap), run-gate rev 46. CHANGES.md checked: rev 17 (RG-20) added a resource-aware memory admission from cgroupfs, and RG-55/RG-56 profiling and admission are filed against the profiler daemon; nothing counts concurrent gates through Docker, and RG-67 (OPEN) is a per-environment lock count. Not a duplicate of either.

**Observed:**
- dstdns caps concurrent gates at two with `scripts/gate-slot.sh`: a two-slot semaphore of lock files under `/tmp`, keyed by the main checkout's instance prefix, wrapped around every `run-gate` call. Its own header lists what it is not: not a fairness queue (a waiter polls every 5 s), not usable from a linked worktree's copy (it refuses; the cap would be lifted), and slot 2 needs a hand-set `RUN_GATE_LOCK_DIR` because R-41's exec lock is fixed at one. RG-67's consumer evidence records two latent defects (a wrapped command's exit 75 is read as "slot busy" and the whole gate silently re-runs; slot 2's lock directory is not keyed by instance) and the three hand-maintained homes of the number.
- A lock file counts only the processes that share that filesystem. The devcontainers, the host shell and CI do not share `/tmp`; the **Docker daemon** is the one object every gate on the host can see, which is why SPEC-V8 S21 orders admission through Docker object names and takes no lock.
- With every worktree owning its own test-runner (D-666), the per-container exec lock (RG-39/R-41) no longer bounds anything across worktrees, so the cap must be daemon-wide.

**Why run-gate owns it:** it starts every gate lane, knows its budget, owner and cleanup path (`finally`, inflight record, re-attach, R-39), and is the only component that can release a ticket on every exit path. A wrapper cannot see a lane's budget or its re-attach.

**Proposed contract (the count mode as SPEC-V8 draft.11 S21 states it for 8.0; names are SPEC-V8's, so the v8 port inherits them and a v7 run-gate and a ciu8 gate can share one daemon):**
- (a) **Switch, default off.** A top-level `[admission]` table in `run-gate.toml` with the one closed key `enabled` (default `false`; S21.1.2). With `false` run-gate behaves exactly as today: no ticket, no wait, no Docker call for admission, the LaneResult records `admission = null`, and `--admission-wait` / `--override-admission` are accepted and ignored with one notice (S21.1.3), so a pointer or CI line that passes them never breaks. `[admission]` is never inherited from the central config.
- (b) **The published object.** `run-gate admission set [--replace] --max-concurrent N | show` publishes one `created`, never-started container `ciu-admission-<g>` (`<g>` a generation) with the labels `ciu.admission.tiers.gates.max_concurrent`, `ciu.admission.unreadable_policy` (`unbudgeted` default | `refuse`), `ciu.admission.generation` and `ciu.admission.owner`; a new generation is created and **every older generation removed in the same step** (S21.3.1); `set` refuses a remote Docker endpoint and an object not carrying `ciu.admission.generation`. Every enabled run reads the object fresh at each fit check; when none exists the limit is an unreadable fact: one notice (`run run-gate admission set`), then `unreadable_policy`. (The v8 form reads the limit from the local host row `[hosts.<h>.admission]`, S21.2; run-gate has no host inventory, so the number is typed once on the verb.)
- (c) **Tickets.** **Label values are fixed** (SPEC-V8 S21.4.8, Appendix E `label_value_grammar`, cited verbatim; R5-04): `ciu.reservation.owner` is compact JSON, keys sorted, with exactly `boot_id`, `host`, `lane`, `pid`, `pid_ns`, `run_id`, `start_ticks` (`pid` and `start_ticks` integers, the rest strings); `ciu.reservation.deadline` is a decimal integer, epoch seconds UTC; `ciu.admission.owner` is compact sorted JSON with exactly `ciu_version`, `host`, `time` (integer epoch seconds UTC), `user`; counts and generations are decimal integers; `override` is `true`. A label that does not parse makes its ticket count as live and is reported, never released. An enabled gate lane first takes a ticket: a `created`, never-started container `ciu-res-gates-<n>`, `n` = the highest visible number (tombstones included) + 1, atomic by `docker create --name`; on a name conflict wait (bounded 30 s) until the name is listed and re-list, or retry the same `n` when it is free. Release = **remove**, except the highest ticket, which is **started so that it exits at once** and stays as an `exited` tombstone (so no number is ever reused); a tombstone is removed once a higher ticket is visible (S21.4.2). Labels `ciu.reservation.{tier,kind,owner,deadline,scheme,group,override}`. The fit check counts the live (`created`) tickets numbered up to and including its own and starts only if the count is at most `max_concurrent`; otherwise it waits in place, rechecking every few seconds with a notice (`waiting for a gate slot: 1 of 1 in use`), up to `--admission-wait D` (default 10 m), then releases its ticket and the lane is NOT_RUN/`no-headroom` (exit 3, RG-78's class). `--override-admission` starts anyway, disclosed and labelled.
- (d) **Groups, marker, reaping.** The lane's own container and its lane services (RG-75) carry `ciu.reservation.group=<ticket>` and are never counted. On admission the owner creates the run marker `ciu-run-<ticket>` with the true run deadline (admission + the lane's `budget`, or 24 h, + 1 h grace), also written to `run.json`. A ticket is abandoned when its owner is provably dead in the reader's own PID namespace (the owner tuple `{ host, boot_id, pid_ns, pid, start_ticks }`; run-gate already has `pid_ns_inode`, R-39a/R-39e), or it is past its short wait deadline with no marker, or its marker is past its deadline, **and** no member of its group other than the marker holds memory. Every recheck runs the janitor, which **stops** (never removes, never collects) the running members of a group whose marker deadline has passed, found by label, and then releases the ticket as (c) says, never removing the highest (S21.6.1–S21.6.5). **A waiter never collects** (R5-05): `run.json`, the evidence and the inflight record (`.run-gate/inflight/`) live in the owning checkout, which a waiter in another worktree cannot see; collection (R-39) stays with the owning lane's next invocation. Liveness bound: with `max_concurrent = 1` one killed gate holds the slot for nothing (same PID namespace), its wait deadline (before admission) or its run deadline (while running).
- (e) **The ticket image has no default, and it is also the object's image** (R5-06): `run-gate admission set` creates `ciu-admission-<g>` from `ticket_image` too. SPEC-V8 builds tickets from the ciu helper image (S8.5.2b), which does not exist before ciu8. For the v7 build `[admission] ticket_image` names an image already present locally whose entrypoint can be set to a binary that exits 0 at once (the project's own test-runner image works, `--entrypoint /bin/true`); when `enabled = true` and it is absent or unusable run-gate refuses (AGENTS §4.2a: a default would be a silent invention). The v8 port replaces it with the helper image digest; **names and labels do not change.**
- (f) **Outputs.** The LaneResult carries `admission = { tier, ticket, waited_s, override }`; a refusal, a `no-headroom` or a wait is printed with the readings. A host that has no Docker needs `enabled = false` (S21.4.7).
- (g) **Build order** (R5-06): RG-78 (the closed exit table) lands before RG-80, or RG-80 carries RG-78's exit 3 for `no-headroom` as its one reason.
- (h) **Out of scope (v8.1):** bytes, `usable`, PSI, the stack tier and placeholders, the charge and the footprint manifest. A v8.1 participant sharing the daemon counts an 8.0/v7 ticket as 1 and charges it nothing (S21.4.5).

**Consumer follow-through (dstdns, separate package):** set `[admission] enabled = true` and `ticket_image`, run `run-gate admission set --max-concurrent 2` once per daemon, delete `scripts/gate-slot.sh` and every call of it, and remove the cap's prose homes (D-570/D-636 records stay as history; GUIDE §1/§3.4, AGENTS, the runner `mem_limit` comment).

**Oracles:**
1. `max_concurrent = 2`, enabled, the object published: two gates started from **two different worktrees and two different PID namespaces** run, a third waits (`waiting for a gate slot: 2 of 2 in use`) and starts when one ends (the 8.0 half of RG-67's `max_concurrent` oracle, now across worktrees).
2. Ordering: N invocations started at the same instant with `max_concurrent = 1` run strictly one at a time, in ticket order, and no two live tickets ever share a number (inject a slow `docker create` so the creation-time window is exercised).
3. Liveness: `kill -9` of the only slot's holder frees the slot at the next waiter's recheck when they share a PID namespace; killed before admission, after its wait deadline; killed while running, at the marker deadline, where the waiter **stops** the expired container and releases the ticket without collecting it, and the owning lane's next invocation collects it (an injectable clock; the waiter runs in a worktree that cannot see the owner's checkout).
4. Monotony: after the highest ticket releases, the next ticket's number is greater than every number ever visible (a tombstone exists); a tombstone is removed once a higher ticket is visible.
5. `enabled = false` with an object published: no ticket is created and Docker is not invoked for admission (assert no Docker call), the flags are ignored with one notice, `admission = null`.
6. Enabled with no object published: one notice and `unreadable_policy` applied (`refuse` → NOT_RUN/`no-headroom`).
7. The ticket is released on PASS, FAIL, a budget kill and SIGTERM.
8. **Cross-tool interoperability (R5-04):** with a ciu8-format ticket and a run-gate-format ticket on one daemon (each tool creating tickets with the other's helper image), each tool's janitor releases the other's **abandoned** ticket and neither releases the other's **live** one; the labels of both parse under the one grammar (a fixture of the `label_value_grammar` surface), and a ticket whose owner label is malformed is counted live and reported. **Same-namespace case (r6):** a ticket of the other tool whose owner is dead **in the same PID namespace** is freed at the next recheck, which holds only if both tools write the pinned forms (`pid_ns` = decimal inode string of `/proc/self/ns/pid`, `boot_id` = stripped `/proc/sys/kernel/random/boot_id`, `host` = unqualified `gethostname()`; SPEC-V8 S21.4.8); a fixture with a differing form must fall back to the deadline path and never release a live ticket.
9. A controlled wrong implementation that orders by Docker's `Created` time fails oracle 2; one that **removes** the highest ticket instead of tombstoning it fails oracle 4; one that reads the limit from the local config instead of the published object fails a test with two worktrees whose configs differ.

**Spec owner:** SPEC (execution contract, admission: new subsection); contract of record SPEC-V8 draft.11 S21.1–S21.4, S21.6, S21.8, S21.9.

**v8: absorb.** This is ciu8's V8-38 (checkpoint D, `gate/admission.py`); the oracles above are its parity tests. Proposal row N28 (`CIU-V8-TESTING-GATE-PROPOSAL.md` §4.11). Related: RG-67 (Amendment 4), RG-78 (the `no-headroom` reason), RG-79.

### Status — FIXED 2026-10-04 (rev 49) in two implementation packages: (1) Docker-name CAS ticket allocation/release, published generations, disabled-by-default switch and budget start at admission; (2) owner PID-namespace proof, wait/run deadlines, group reaping, tombstone cleanup and the shared v8 label fixture. Byte admission, stack tickets and v8.1 policy stay out of scope.

### Amendment 1 (2026-10-04): operator status and doctor preflight

`status` provides a read-only view of the local Docker daemon's published
count cap and ticket queue, alongside run-gate's internal exec locks and the
selected project's inflight records. It decodes live tickets and tombstones
with the shared label grammar; it never calls the janitor or any Docker
mutation. `doctor`, when `[admission] enabled = true`, verifies that the
configured ticket image is local using image inspect only, that a valid
`ciu-admission-<g>` object is published, and that `max_concurrent` is a
readable positive integer. Each ordinary setup failure names its repair
command or exact config field. If a visible object's identity labels are
unreadable, doctor names that object and reports that `admission set` refuses
to replace it; the daemon owner must resolve that specific object before
publishing another cap.

**Status — FIXED 2026-10-04 (rev 51).** The `status` JSON fixture covers one
running ticket, one queued ticket, and one dead-owner ticket; doctor oracles
cover missing image, missing publication, and unreadable cap. The shared
module remains independent of run-gate globals for direct CIU v8 porting.

## RG-81 — source-backed Assay lanes reject the verdict shape of an editable install

**Observed:** internal vbpub lanes omit `assay_command` and pins, then install
Assay from the selected worktree with `pip install -e`. Assay intentionally
omits `judge_provenance` for a source checkout because there is no built
artifact to hash. Run-gate nevertheless required artifact provenance for every
Assay verdict, so healthy internal source lanes failed after the judge ran.

**Contract:** a fresh attempt clears its previous verdict before setup begins.
Source mode uses isolated Python to reject a consumer-local `assay` module or
package without executing it, while allowing a namespace-only directory,
then checks that the installed package spec resolves to the selected tree's
`assay/src/assay/__init__.py`. Both the check and actual `assay.cli` invocation
use `-I`, so a `PYTHONPATH` shadow cannot replace the verified package. The
source command invokes `assay.cli` through that same interpreter and requires
a non-empty `assay_version` plus a Git `commit` matching the selected run
commit. Run-Gate accepts full 40-hex SHA-1 and 64-hex SHA-256 IDs at this
identity boundary; Assay's P22 snapshot source still requires SHA-1 object
storage for high-rigor snapshot lanes. External explicit-command mode
continues to require full artifact `judge_provenance`; no artifact digest is

synthesized for editable source. Integer inflight schema 2 stores `source` or
`artifact`, and re-attachment uses that launch-time mode. Schema 2 requires
the mode key; Assay records also require non-empty verdict and progress paths,
while non-Assay records cannot carry Assay artifact paths. On container-runner
records, corrupted mode or path combinations, including artifact paths other
than the exact derived lane paths, refuse before inventory Docker probes and
admission. A command lane with a null mode and an Assay verdict path refuses
as well. The launch mode is attached to the run
record only after the recorded container is proven to be followed, re-attached,
or collected, so a lost artifact-mode container cannot override the source
mode of its fresh replacement. If a follower is promoted, the private mode
stays on the run record until `_dispatch` parses the verdict; `finish()` then
records the parsed outcome without that private field. `--fresh`
discards the old result only after its container is confirmed stopped. For an
older-schema record marked with a non-container runner, `--fresh` refuses as
well; its lifecycle owner must confirm the run is over before the recovery
record is removed. If the current lane kind changed away from Assay, its
recorded mode also refuses reinterpretation as a command result. Private mode
state does not enter history or the parsed public record.

For recovery safety, a container-runner record left after an Assay lane
changes to host or exec mode refuses before imported inventory, admission, or
execution. Ephemeral inventory probes use the configured user and extra
mounts. Sequence-name records with malformed identity modes fail closed with
recovery guidance.

**Oracles:** a matching internal source verdict maps all six closed Assay
outcomes; missing identity and a verdict for a different commit are ERROR; a
same-commit previous PASS is deleted before a fresh attempt; external
artifact mode without judge provenance stays ERROR; a local `assay` shadow
that forges `__file__` is rejected without running its marker; a local
`pip.py` cannot replace pip; and an `assay` executable on `PATH` cannot
replace the checked Python module. A malicious `PYTHONPATH` package cannot
replace the verified module or execute its marker; a namespace-only consumer
directory passes, while a regular local package refuses without execution.
The source identity path accepts the 64-hex commit read from a real
SHA-256-format Git repository; this does not claim Assay high-rigor snapshot
support for SHA-256. Re-attachment follows the recorded identity mode after
config changes and refuses missing mode, substituted/NUL artifact paths, or a
changed lane kind before Docker access; a command record with a null mode and
Assay path refuses before Docker too. Imported-lane contract validation
refuses before the inventory Docker probe. Lost-container recovery
keeps today's mode, and promoted-follower parsing succeeds with the recorded
mode while history/public state omit it. An older-schema exec-runner record
refuses `--fresh` without touching the runner. Unknown-schema recovery advice
offers `--fresh` only for ephemeral-container lanes; host and exec lanes name
the lifecycle-owner confirmation and recovery-record removal path instead.
Sequence nodes and all members are checked before imported Assay inventory or
admission; an old record under a lane now configured as a sequence refuses.
Foreign-runner records refuse even when `--fresh` is requested. The source
identity construction assertion now matches the `PathFinder` implementation,
and `CHANGES.md` names rev 53 and the schema-1 recovery requirement. Null and
floating-point schema versions refuse as malformed.

**Rev 53 oracles:** a container-runner Assay record surviving a switch to
host or exec mode refuses before imported inventory and remains on disk until
the prior container's lifecycle owner confirms it stopped. Ephemeral inventory
probe argv includes every configured extra mount and the lane's configured
container user. Sequence-root records with list or object identity-mode values
return a closed recovery refusal before inventory or admission.

**Files:** `run-gate.py`, `tests/test_run_gate.py`, `SPEC.md`, `README.md`,
`docs/DESIGN-GUIDE.md`, `CONSUMERS.md`, `CHANGES.md`, and this backlog entry.

### Status — IN PROGRESS (rev 54; Review #11 findings addressed, package gate pending)

## RG-82 — Adopt cli-extended (unified adoption, order 7 of 8)

**Status: OPEN — planned (filed 2026-10-05 by the cli-extended unified-adoption program, W10, as RG-81; renumbered RG-82 when merged with main, whose RG-81 is the source-backed Assay lane entry above).**

**Source documents.** [`libraries/cli-extended/docs/PROGRAM-2026-10-UNIFIED-ADOPTION.md`](../libraries/cli-extended/docs/PROGRAM-2026-10-UNIFIED-ADOPTION.md) (decisions CX-D1..CX-D12, section "W10 - planned adoptions"; CX-D12: run-gate fully adopts, independent of the v8 merge into ciu) and [`libraries/cli-extended/docs/ADOPTION-CHECKLIST.md`](../libraries/cli-extended/docs/ADOPTION-CHECKLIST.md) (AC-01..AC-25). **Dependency:** cli-extended 0.2.0 released first (W8, the controller). Not executed in the program's session.

**Observed mechanism (verified in source).** `run-gate.py` is one 11302-line module (`run_gate.py` is a committed symlink to it, `pyproject.toml` `py-modules = ["run_gate", "run_gate_admission"]`, console script `run-gate = "run_gate:main"`, `dependencies = []` at `:34` with the comment "stdlib-only is the design win"). The same file is the gate entrypoint of other projects through committed symlinks: `assay/run-gate.py`, `ciu/run-gate.py` and `pwmcp/run-gate.py` all point at `../run-gate-project/run-gate.py`. It contains no `cli_extended` reference. (Line numbers below were taken at filing time, before main's rev 51–54 changes; re-locate by content.)

- Grammar and parser (AC-03, AC-04, AC-07): `RunGateArgumentParser(argparse.ArgumentParser)` at `run-gate.py:311`; one `_dispatch` builds `RunGateArgumentParser(add_help=False, prog=PROG)` at `:10468` with an overloaded positional `lane` (and `target`) that also selects the sub-commands `validate-pointers` (`:10646`) and `doctor` (`:10722`), 25 `add_argument(` calls, and flag-style modes (`--version`, `--list`, `--check-env`, `admission set|show`, `status`, ...). CX-D12 requires the whole grammar re-registered (real verbs/arguments/options), not wrapped.
- Version (AC-01): `--version` is a `store_true` flag (`:10473`) printing `run-gate rev <__revision__>` (`:10550-10551`) where `__revision__` is a hand-bumped integer (`:17`); the wheel's version comes from setuptools-scm (`pyproject.toml` `dynamic = ["version"]`, tag regex `run-gate-v*`). The library's resolver requires a semver string from installed metadata and/or a VERSION file and has no literal fallback; the carve must decide how the integer revision (used in provenance and docs, e.g. "rev 49") coexists with it (library change request against cli-extended if it cannot).
- Exception boundary (AC-10, AC-11): `main()` at `:11261` installs a SIGTERM-as-exit handler and wraps `_dispatch` in its own try/except around `SystemExit`/`KeyboardInterrupt` (five `SystemExit` mentions); the module has 16 `except Exception` sites (e.g. `:1901, :1936, :2314, :2549, :2574, :3936, :7018, :8432, :8518, :9210, :9424, :9539, :9557, :9666, :9750, :9810`), most of them deliberate lane-isolation guards that must be classified one by one. The closed exit-code table (RG-78, "closed exit mapping" in the module docstring) must survive: `unexpected_exceptions="report"` exits 1, so the carve must map the table to `CliFailure` exit codes explicitly. No `--traceback` exists.
- `--json` (AC-05): `main()` decides on the literal test `"--json" not in arguments` (`:11273`) before parsing; this is a hand-rolled copy of a library-owned control.
- `--dry-run` / `--yes` (AC-05, AC-12): own `--dry-run` at `:10527` (RG-8 resolved argv/mounts/slice without executing); no `--yes` found. Becomes `VerbSpec(dry_run=True)` with the same output.
- `sys.path` (AC-25): `run-gate.py:148-151` inserts its own module directory (to import `run_gate_admission` when loaded as a console script); none toward `libraries/cli-extended`. The zero-install property is a documented design requirement (the RG-51 comment near the top of the file states the launcher "must run on a fresh clone with zero installs"): a real wheel dependency (CX-D1) and the symlinked copies in assay/ciu/pwmcp mean the carve must say how a bare checkout obtains `cli_extended` (CX-D3: installed library for scripts, zipimport from the verified release wheel where no pip exists), without weakening the existing zero-install guarantee silently.
- Skills (AC-19): `.claude/skills/run-gate-cli/SKILL.md` is the source tree; move to package data and register `skills`.
- Doctor (AC-20): `run-gate doctor` exists (RG-9, handler dispatched at `:10722`); it becomes the shared `doctor` verb with its per-lane/toolchain checks as named checks.
- Tests (AC-23): `tests/test_run_gate.py:474` (`run_tool`), `:725` and `tests/test_run_gate_admission.py:655` each call `subprocess.run([sys.executable, <run-gate.py or symlink>, ...])`, with the module-level symlink fixture at `tests/test_run_gate.py:54-56`; these are the candidates for `invoke_script(home=..., ...)`. See RG-83: the same suite already leaked into the real home once.

**Common shape (tick each, cite the AC row).**

- [ ] `[project].dependencies` gains `cli-extended>=0.2.0` (currently `[]`); no vendoring; `py-modules` unchanged unless the carve splits the module (AC-24).
- [ ] Resolve the zero-install question for the script and its three symlinked copies (CX-D3); no `sys.path`/`PYTHONPATH` onto the library in project sources (AC-25).
- [ ] `CliIdentity.resolve(...)` and an explicit story for `__revision__` (AC-01, AC-02).
- [ ] Re-register the full grammar (CX-D12): lane/target arguments, `doctor`, `validate-pointers`, `admission`, `status`, `--list`, `--check-env`, `--json`, `--dry-run`, ... as verbs and options; delete `RunGateArgumentParser` (AC-03, AC-04, AC-05, AC-06, AC-07).
- [ ] `unexpected_exceptions="report"` with the closed exit table preserved; classify the 16 `except Exception` sites (AC-10, AC-11).
- [ ] Surface lifecycle: review/manifest/spec, `surface check` (AC-16, AC-17, AC-18).
- [ ] Skills via `register_skills_verbs` (AC-19); `doctor` as the shared verb (AC-20).
- [ ] Tests: `assert_cli_contract`, `invoke_script` (fixing RG-83), plugin if a catalog exists (AC-21, AC-22, AC-23).

**Acceptance.** `cli-extended audit` reports no `fail`; `cli-extended surface check` passes; run-gate's own registered gate passes (`run-gate.toml`; read the verdict in a separate step); a released run-gate version is deployed (merge + `cmru release` + devcontainer install; remember "two same-project releases leave two artifact dirs"). The assay, ciu and pwmcp symlinks keep working from a fresh clone.

**Oracles.** For every existing lane in the estate, `run-gate <lane> --dry-run` output is byte-identical before and after (golden); every refusal keeps its exit code from the RG-78 table; a controlled wrong implementation that maps an unexpected exception to exit 1 where the table says another code fails the exit-table test; `python3 assay/run-gate.py --version` (through the symlink) works in a checkout with no installed `cli_extended`, or the documented CX-D3 bootstrap is the only extra step.

## RG-83 — `install_fake_assay` writes its fake `assay` and `assay.real` into the first entry of the real `$PATH` (the operator's `~/.local/bin`) in tests that have not isolated PATH

**Status: OPEN (filed 2026-10-05 by the cli-extended unified-adoption program, W10, as RG-82; renumbered RG-83 at the merge with main; severity Major: a test run replaces or shadows the operator's real tool on the host).**

**Observed (source and filesystem; line numbers at filing time).**

- `tests/test_run_gate.py:355` is `path = shim_dir_of(monkeypatch) / name` inside `install_fake_assay` (def at `:348`), and `shim_dir_of` (`:418-419`) is `return Path(os.environ["PATH"].split(":")[0])`: the first `$PATH` entry, whatever it is. `:360` writes `<name>.real` and `:362` writes the wrapper `<name>` there, both with `chmod +x`. Nothing in `install_fake_assay` creates an isolated directory or sets `PATH`.
- The first entry is only a throwaway directory when a test has already called a fake-docker fixture, which does `monkeypatch.setenv("PATH", f"{shim_dir}:{os.environ['PATH']}")` (`:280`). Two call sites do not: `TestRG76AssayLaneImports._project` (class at `:8411`, `def _project` at `:8424`) calls `install_fake_assay(...)` at `:8455` with no prior PATH isolation, and `test_import_all_drops_a_lane_removed_from_inventory` calls it again at `:8504`. In the devcontainer `$PATH` starts with `/home/vscode/.local/bin`, so those tests write there. `_judge` (`:6509`) has eleven or more callers (e.g. `:6517, :6533, :7825`); the ones read (`:6513-6517`) call `fake_docker_executing` first, so they are safe only by call order, and the remaining callers were not individually audited when this was filed.
- On 2026-10-04 14:36 the real directory held `~/.local/bin/assay` (633 bytes, mode 755, the wrapper that begins `if [ "$1" != "run" ]; then exec "$0.real" "$@"; fi`) and `~/.local/bin/assay.real` (391 bytes, mode 744), whose body is the `INVENTORY` JSON of `TestRG76AssayLaneImports` (lanes `alpha`, `beta`, `gamma`, `assay_version` 7.2.0), exactly what `:8455` writes. Both were still present when this entry was filed. They were NOT deleted (operator instruction); the operator decides. **Update 2026-10-05:** with operator approval both files were verified (same fake wrapper and three-lane inventory) and removed. They had been the ONLY `assay` on the devcontainer `PATH`, and no real assay was installed in `~/.venv`, so every `assay` call on the cockpit had been answered by the stub. assay 7.2.0 was reinstalled into `~/.venv` from the sha256-verified release wheel. The test defect itself remains open.

**Why it matters.** Any shell on the host that resolves `assay` through `~/.local/bin` runs the fake (it prints a three-lane inventory for `lanes --json` and fabricates a PASS/FAIL verdict file for `run`), and a genuine `assay` installed at that path would have been overwritten (not determined: whether one existed). A gate that calls `assay` could therefore be judged by a stub.

**Proposed fix direction.** `install_fake_assay` must never write into an inherited directory: create `tmp_path / "bin"` (or take the directory as a required argument), prepend it with `monkeypatch.setenv("PATH", ...)`, and point `HOME`/`XDG_*` at `tmp_path`. This is what `cli_extended.testing.invoke_script(..., home=...)` already enforces for subprocess tests (AC-23); the in-process variant needs the same guarantee. Add a suite-level guard (an autouse fixture or `conftest` check) that fails the session if `$HOME/.local/bin` gains or changes any file during the run.

**Oracles.** After the full `tests/test_run_gate.py` run with a sentinel `HOME`, `$HOME/.local/bin` is byte-for-byte unchanged (compare a before/after listing with hashes); the guard fixture fails a deliberately wrong test that still writes to `PATH.split(":")[0]` without isolating `PATH`; the existing 28 `install_fake_assay` call sites (29 text matches including its `def`) still pass; controlled wrong implementation: isolating only `HOME` while leaving `PATH` untouched fails the first oracle.

**Related:** RG-82 (the cli-extended `invoke_script(home=...)` adoption), the cli-extended program's oracle rule B ("never write to the real `$HOME`; a run-gate test leaked a fake `assay` into the real `~/.local/bin` on 2026-10-04").

## RG-84 — run-gate runs happily as an unreaping PID 1 and reports lane verdicts from a container that can no longer fork

**Status:** IN PROGRESS (filed 2026-10-05 as RG-83, renumbered RG-84 at the merge with main; rev 55 implementation is present; live PID 1 and low-pids acceptance passed; registered selftest remains pending). See the [live acceptance report](nyxloom-trove/reports/run-gate-RG84-live-acceptance-2026-10-05.md).

**Observed.** `cmru tester-gate` started `tester-unified:local` without `--init` (cmru KI-52), so `./run-gate.py --base main assay-r2` ran as PID 1 of container `pedantic_antonelli`. git's detached auto-maintenance orphaned one `git` per commit to that PID 1, which never reaps; 19,108 zombies filled `pids.max` (19,115/19,117) at 03:11Z. The operator's contamination notice requires discarding the campaign's Assay state and progress and invalidates all post-03:11Z candidate outcomes and its final verdict; none are evidence for this entry. run-gate's own `docker run` launches already pass `--init` (`run-gate.py:5873`, `:9856` at filing time); the gap is run-gate *being* PID 1 under someone else's launcher.

**Expected / fix.**
- (a) At start-up, when `os.getpid() == 1`, refuse with an infrastructure error naming the remedy ("run-gate is PID 1 with no init: start the container with `--init` (docker) or `init: true` (compose)"). An explicit opt-out is not provided: an unreaping PID 1 is not a safe supervisor for process-heavy lanes.
- (b) Before and after each real lane, read `pids.events` (`max`) and `memory.events` (`oom_kill`) from the cgroup containing run-gate. Any increase makes the lane verdict an infrastructure error regardless of its raw exit status (generic guard; assay B145 is the in-judge guard). Missing, malformed, moved, or unreadable cgroup data is also an infrastructure error; dry runs do not sample counters.

**Oracles.** The live PID 1 refusal and low-`--pids-limit` checks passed in a detached `tester-unified` container; the latter's command returned zero while `pids.events:max` increased, and run-gate returned ERROR with raw `exit_code: 0` retained. Tests cover a changed `memory.events:oom_kill` counter, unchanged counters, unreadable files, moved cgroups, and raw-status preservation. The registered `selftest` lane with 100% changed-line coverage remains pending.

**Related:** cmru KI-52 (launcher without `--init`; git `maintenance.autoDetach` mechanism and image hardening), assay B145, dstdns D-670 TEST-RUNNER-INIT (same mechanism under a `sleep infinity` runner).
