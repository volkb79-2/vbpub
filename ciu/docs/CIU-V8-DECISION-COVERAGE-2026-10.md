# CIU v8 — decision coverage audit (2026-10-03, dstdns D-656)

**Scope.** One row per decision bullet of dstdns `nyxloom-trove/decisions.md` D-647 and D-651..D-655, checked against `CIU-V8-TESTING-GATE-PROPOSAL.md` revision 4.3 (vbpub@c98be059a) and its Appendix R. The decision text was read from the ledger, not from the task summary. "4.3" is the verdict on the revision as audited; "4.4" says what the audit changed (commit `c175e3d23`, proposal §4.3.17 "4.4 amendments", §4.7 X116–X118).

Verdict vocabulary: **yes** (carried faithfully), **partial** (carried, with a gap or an unlabelled extension), **missing**, **contradicted**, **n/a** (not a ciu rule: dstdns-side work, carried only as a pointer).

## 1. Matrix

| # | decision bullet | proposal section(s) | Appendix R row(s) | 4.3 | 4.4 |
|---|---|---|---|---|---|
| 1 | D-647 #1 — merge run-gate into ciu, one gate implementation; v8 reconciled against ciu 7.15 / run-gate first | §4.1.10 Posture; §4.3.17; §4.4 V8-12, V8-19 | header; S16.1; S16.12 | yes | — |
| 2 | D-647 #2 — Mode A retired as a gating mode; worktrees own their test environment | §4.3.1 (D-646 Q2 row); §4.1.7a opening; §4.1.10 | header; S9.5; S16.4 | yes | — |
| 3 | D-647 #3 — borrow vault only, never postgres (later REVISED by D-651 Q9) | §4.1.7a; §4.3.1; §4.7 X111; §4.8 | S9.5; S5.2 | yes (superseded, recorded) | — |
| 4 | D-647 #4 — stack cap is a RAM budget, not a count; admission from measured footprint | §4.1.9 ("no instance count"); §4.1.10a; §4.4 V8-30 | S14.6 (delete `max_concurrent`); S16.6 | yes | — |
| 5 | D-647 #5 — ciu 7.15's instance-id derivation is final | §4.1.4 "The instance id"; §4.7 X98 | S4.1.1; S4.1.2 | yes | — |
| 6 | D-647 #6 — skills ship in the wheel, installed from the installed wheel into `~/.claude/skills` and `~/.agents/skills` | §4.1.15; §4.4 V8-33 | S18 (`ciu skills …`) | yes | — |
| 7 | D-647 #7 — AGENTS.md owns harness-neutral policy; CLAUDE.md shrinks | not a ciu rule | — | n/a | — |
| 8 | D-651 Q1 — "standalone" = the ciu wheel in zero-instance mode; run-gate-project is the main case; the copied-script distribution ends | §4.1.10 Posture ("Standalone"); §4.1.14 | S16.11; header | yes | — |
| 9 | D-651 Q2 — RG-67/73/74/75/76 built in run-gate now with oracles; ported once; oracles become parity tests; run-gate bug-fix-only after the port | §4.1.10 "Build order"; §4.4 V8-19, V8-24, V8-36; §4.11 N24 | S16.1; Appendix D | yes | — |
| 10 | D-651 Q4 — the cgprofile daemon measures and provides data only; decides nothing | §4.1.10a "Roles" | S16.6 | yes | — |
| 11 | D-651 Q4 — the project commits a resource manifest (per-lane and per-container footprints, R-44 shape) | §4.1.10a; §4.5 A7/H; §4.4 V8-30 | S16.9 (footprint manifest) | yes | — |
| 12 | D-651 Q4 — ciu decides whether a start fits; an explicit override can force a start | §4.1.10a "Decide and start" | S16.6 (`--override-admission`); S16.10 | yes | — |
| 13 | D-651 Q4 — controller inference: host-scoped lock + registration with the daemon (later WITHDRAWN by D-655) | §4.3.17 4.3 amendments; §4.7 X102, X115 | S16.6 | yes (withdrawn, recorded) | **Appendix R S14.4.3/S14.4.9, §4.4 V8-11, §4.5 inventory table and the rev 4.0 trace rows still said "the host admission lock": contradicted D-655; fixed (X117)** |
| 14 | D-651 Q5 — unmeasured work is charged its declared `memory_max`; the first measurement replaces the ceiling in the committed manifest | §4.1.10a "Charge" (Unmeasured) | S13.2.1; S16.6 | yes | — |
| 15 | D-651 Q8 — hermetic lanes need no instance init; identity and images derived read-only, nothing written, nothing started; exec lanes and `requires` still need an instance | §4.1.10 Posture | S16.11 | yes | — |
| 16 | D-651 Q9 — committed presets only | §4.1.7a | S9.5 | yes | — |
| 17 | D-651 Q9 — shareability decided per service; worth it only for heavy services (SkyWalking likely) | §4.1.7a "Sharing must pay for itself" | S5.2 | yes | — |
| 18 | D-651 Q9 — a shared service gives each tenant an isolated namespace (vault prefix/mount; postgres its own database and role) | §4.1.7a rules and closed tenant vocabulary | S5.2; S9.5 | yes | — |
| 19 | D-651 Q9 — follow-up: dstdns per-service shareability analysis before CIU-116's preset | §4.1.7a Shortcomings; §4.10 item 32 | — | yes | — |
| 20 | D-651 Q10 — v8 keeps 7.15's path-derived id and the shared `.workspace-instances/` records | §4.1.4; §4.1.9 locks; §4.7 X98 | S4.1.1; S14.4.1; S14.7 | yes | — |
| 21 | D-651 Q10 — owner token (`owner_id`, `ciu.owner`) dropped | §4.1.4; §4.1.9; §4.3.1 K3 | S4.1.1; S4.5; S14.2 | yes | — |
| 22 | D-651 Q10 — deletion guarded by the `ciu.checkout` label and `[deploy] protected` | §4.1.4; §4.1.9 Lifecycle | S4.5; S14.2 | yes | — |
| 23 | D-651 Q10 — add `ciu clean --identity <old>` and repair-in-place (CIU-115) | §4.1.4; §4.1.9 Lifecycle | S4.1.2; S18 | yes | — |
| 24 | D-652 Q3 — cmru keeps its release transaction and calls `ciu gate <lane>`; its tester becomes an inherited ephemeral environment | §4.1.10 Posture (cmru); §4.4 V8-37 | S16.1; S16.4 | yes | — |
| 25 | D-652 Q6 — host facts and the decision about what may be used live in ciu (host-scoped) config; the daemon only measures | §4.1.10a "Host capacity is ciu configuration"; §4.5 D2 | S2.6/S2.7 | yes | — |
| 26 | D-652 Q6 direction — the proposal gets a new revision bringing in 7.12–7.15, RG-55, shipped run-gate features, D-647/D-651/D-652 | header; Source documents; §4.3.17 | all of R.1 | yes | — |
| 27 | D-652 Q7 — hermetic image contract decided in the revision (was open) | §4.3a D; §4.3.17 4.1 amendments | S16.4; S15 stage 12 | yes (decided by D-653) | — |
| 28 | D-652 Q11 — present both CLI/exit contracts (was open) | §4.3.17 4.2 amendments (A/B/C table) | S16.8 | yes (decided by D-654) | — |
| 29 | D-652 Q12 — one `[testing.judge]`: pinned artifact (`command` + `sha256`) or `source = <path>`, plus a version floor; lanes imported from `assay lanes --json` | §4.1.10 example; §4.5 A7; §4.7 K11 | S16.3 | yes | — |
| 30 | D-652 Q13 — atomic per-repo cutover: one commit runs `ciu migrate --gate`, deletes the run-gate files, rewrites pointers; history restarts | §4.1.13 step 7; §4.1.10 | Appendix A; S18 | yes | — |
| 31 | D-652 Q14 — each wheel ships its skills; shared `skills install\|list\|check\|uninstall` from cli-extended (CLI-EXT-05 (filed as CLI-EXT-02)); per-tool adoption follows | §4.1.15; §4.4 V8-33 | S18 | yes | — |
| 32 | D-653 Q7 — worktree mounted at its own path plus the git common dir; images bake no checkout path; `ciu check` validates the contract | §4.1.10 (linked worktrees); §4.3a D; §4.3.17 4.1 amendments | S16.4; S15 stage 12; S6.2/S17.6.1 | yes | — |
| 33 | D-653 Q7 — side finding: test-runner's editable `ddcli` install is dead code (carve TEST-RUNNER-DDCLI-INSTALL) | §4.10 item 36 | — | yes (dstdns work, pointer only) | — |
| 34 | D-653 Q4 — admission charges the WARM (hot + warm) working set, never total/logical memory incl. cold pages | §4.1.10a "Charge"; §4.7 X113 | S16.6; S16.9 (footprint manifest) | yes | — |
| 35 | D-653 Q4 — the manifest records a warm working-set figure; R-44's peak is not it; source is DAMON/CP-6; `memory_max` stays the ceiling | §4.1.10a "Informational fields", "Ceiling" | S16.9 (footprint manifest); S13.2.1 | yes | — |
| 36 | D-653 Q4 (implied) — what is charged when a run was measured but has no DAMON series | §4.1.10a "Measured without DAMON" (charge the measured peak; labelled "the author's reading, not a ruling") | S16.6 ("else the measured peak") | **partial**: an unlabelled-in-R extension the operator never ruled on | §4.10 item 38 added; draft.8 open item O-2 |
| 37 | D-653 Q15 reopened — mdt-writes-`/etc/ciu/host.toml` withdrawn; no hard ciu↔mdt coupling; ciu config may carry host facts | §4.3.17 4.1/4.3 amendments; §4.1.10a; §4.7 X114 | S2.6/S2.7 | yes | — |
| 38 | D-653 Q16 — running without the daemon is normal; policy `count` (per-tier count fallback) or `unbudgeted` (disclosed) in ciu config; never refuse by default | §4.1.10a "Without the daemon" | S2.7; S16.6 | yes | — |
| 39 | D-654 — explicit modes `ephemeral\|exec\|host`; `host` = bare subprocess; no implicit names | §4.1.10; §4.3.17 4.2 amendments; §4.1.14 | S16.4 | yes | — |
| 40 | D-654 — closed exit table PASS 0 / FAIL 1 / ERROR 2 / NOT_RUN 3 / BUDGET 4 | §4.1.10; §4.3.17 4.2 amendments | S16.8 | **partial**: the fourth verdict is `BUDGET_EXCEEDED` in draft.7 and Appendix R, `BUDGET` in D-654/RG-78/X110 | one name, `BUDGET_EXCEEDED`, `BUDGET` recorded as shorthand (X116); alignment of RG-78's text is draft.8 open item O-1 |
| 41 | D-654 — raw code and refusal reason carried in the JSON result and a one-line summary | §4.1.10 | S16.8; S16.9 | yes | — |
| 42 | D-654 — documented mapping from each tool's raw codes to verdicts (pytest 5 → FAIL); no passthrough flag | §4.1.10; §4.3.17 4.2 amendments | S16.8 | yes | — |
| 43 | D-654 — backport now: run-gate RG-78; ciu8 port inherits it as a parity test | §4.1.10; §4.4 V8-24 | S16.8 | yes | — |
| 44 | D-655 — no lock file, no mount, no extra service | §4.1.9 Locks; §4.1.10a "Decide and start" | S14.4.3/S14.4.9; S16.6 | yes in §4.1.9/§4.1.10a, **contradicted** in Appendix R's S14.4.3/S14.4.9 row | fixed (X117) |
| 45 | D-655 — every admitted start is first a Docker object with reservation labels (tier, bytes, source, owner): `created` lane container, `compose create` set, labelled volume for a host-mode lane | §4.1.10a; §4.5 D2 | S4.5; S16.6 | yes | — |
| 46 | D-655 — ciu lists the tier's reservations, orders by Docker creation time and id, starts only if the cumulative charge up to and including its own fits the tier capacity from ciu config; else waits in place keeping its position | §4.1.10a steps 1–4 | S16.6 | yes | — |
| 47 | D-655 — crashed reservations reaped by `ciu instance reap` or the gate's re-attach using the owner tuple | §4.1.10a | S16.6; S16.9 | yes | — |
| 48 | D-655 — replaces the Q4 inference; daemon stays measure-only; CP-15 optional, observability only | §4.1.10a; §4.4 V8-31; §4.7 X115; R.3 | S16.6; R.3 | yes | — |
| 49 | D-655 — constraints: no ciu↔mdt coupling; works without the daemon (Q16 policy + manifest charges); correct across devcontainers and users on one Docker daemon | §4.1.10a | S2.7; S16.6 | yes | — |
| 50 | D-655 — stated shortcomings: work started outside ciu, a second Docker daemon | §4.1.10a Shortcomings | — | yes | — |
| 51 | D-655 — interview complete; Q1–Q16 answered; no open product decision | §4.9; Status | all | yes | Status line now reports draft.8 |

**Counts (rev 4.3 as audited).** 51 rows: **yes 47, partial 2 (rows 36, 40), missing 0, contradicted 1 (row 44; row 13's residue is the same defect), n/a 1 (row 7).**
After rev 4.4: yes 49, partial 1 (row 36, an unruled extension disclosed as open item O-2), missing 0, contradicted 0, n/a 1.

### 1a. Review dispositions (dstdns D-658)

The independent adversarial review (`CIU-V8-ADVERSARIAL-REVIEW-2026-10-03.md`, phase 2 §3.1) disputes 15 of the "yes" verdicts above, and the writer's own row 36 closes. The verdicts below are the honest ones after draft.9 / rev 4.5; the matrix above is the writer's original audit and is kept as the record of it.

| row | writer | review | after draft.9 / rev 4.5 |
|---|---|---|---|
| 5 (D-647 #5, 7.15's derivation is final) | yes | contradicted (V8R-01: lexical vs physical path) | **yes** — S4.1.1 derives from the physical path as `workspace_env.py:1579-1581` does |
| 9 (oracles become parity tests) | yes | partial (V8R-16) | **yes** — S16.12's per-oracle table; RG-67/73/75/76/78 amended (§6) |
| 15 (D-651 Q8 hermetic) | yes | partial (V8R-17: `image_from` in a worktree) | **yes** — S16.11.6 resolution order |
| 18 (tenant isolation) | yes | partial (V8R-07: a joiner reads the primary's credential) | **yes** — S10.1.7, S9.5.8–S9.5.10 |
| 20 (D-651 Q10 path-derived id, shared records) | yes | contradicted/partial (V8R-01, V8R-02, V8R-23) | **partial** — id and lock fixed; the primary still has no library record (open item O-24, an upstream `libraries/worktree` question) |
| 22 (deletion guarded by `ciu.checkout`) | yes | partial (V8R-18) | **yes** — S14.1.5, S4.1.1 |
| 25 (D-652 Q6 host-scoped config) | yes | partial (V8R-06) | **partial** — `PENDING-OPERATOR (V8R-06)` |
| 31 (skills, CLI-EXT-02) | yes | partial (V8R-30) | **yes** — cited as CLI-EXT-05 (filed as CLI-EXT-02), verified in `libraries/cli-extended/BACKLOG.md:34,248` |
| 34 (warm, never peak) | yes | partial (V8R-19) | **yes** — a run without a warm figure is charged `memory_max` |
| 36 (measured without DAMON) | partial | partial (V8R-19) | **yes** — O-2 closed from D-651 Q5 |
| 42 (raw-code mapping) | yes | partial (V8R-14) | **yes** — S16.8.2 maps all six assay outcomes |
| 43 (RG-78 as parity) | yes | partial (V8R-16) | **yes** — the token and the `--resume` oracle are reconciled in S16.12 and RG-78 is amended |
| 45 (`compose create` set) | yes | faithful to the letter, unimplementable (V8R-03) | **pending operator** (V8R-03) |
| 46 (ordered fit) | yes | faithful to the letter, racy (V8R-04) | **pending operator** (V8R-04) |
| 47 (reap by owner tuple) | yes | partial (V8R-05) | **yes** — S16.6.12 |
| 49 (correct across devcontainers and users) | yes | not met (V8R-04, V8R-05, V8R-06) | **pending operator** — V8R-05 fixed; V8R-04 and V8R-06 remain |

**Counts after draft.9 / rev 4.5** (51 rows): **yes 45, partial 2 (rows 20, 25), pending-operator 3 (rows 45, 46, 49), missing 0, contradicted 0, n/a 1.** The five operator questions are rev 4.5 §4.9.

### 1b. D-658 rows (the operator's rulings on the review, 2026-10-03)

D-658 adds decision bullets; each is checked against proposal rev 4.6 and SPEC-V8 draft.9. Verdicts are after draft.9.

| # | D-658 bullet | proposal rev 4.6 | SPEC-V8 draft.9 | verdict |
|---|---|---|---|---|
| 52 | V8R-01: the id is derived from the physical path | §4.1.4, R S4.1.1, X119 | S4.1.1, S4.1.4 | yes |
| 53 | V8R-02: one lock per instance | §4.1.9, X120 | S14.4.1(c), S14.4.7 | yes |
| 54 | the other non-operator findings and O-items applied; run-gate entries amended; CP-16 filed; CLI-EXT-05 | §4.3.17 4.5 amendments | Appendix D.10 | yes |
| 55 | V8R-09 scope ruling: resource management and admission come as **v8.1** | §4.1.10a, §4.4 checkpoint F, X129, Appendix F reference | S21, Appendix F, S21.10 | yes |
| 56 | v8.1 needs **one config switch** that turns admission off so ciu just runs | §4.1.10a opening, §4.5 A1 | S21.1 (`[ciu] admission`, `off | on`, default `off`; S3.4.7; Appendix E) | yes — the default is derived in S21.1.3 and is the operator's-reading "off when unconfigured" (open item O-31 records the alternative) |
| 57 | V8R-09 → 1: the daemon's absence changes only the inputs; the policy applies only to unreadable facts | §4.1.10a, §4.5 D2, X130 | S21.8 | yes |
| 58 | V8R-03: one placeholder per admitted `up` carrying the whole warm charge; wave containers group under it | §4.1.10a, X131 | S21.4.3, S21.4.4, S21.6 | yes (lifecycle specified: create, up-complete, `down`/`clean`, failure, crash) |
| 59 | V8R-04: gap-free tickets `ciu-res-<tier>-<n>` through Docker's atomic name reservation; the settle delay as the alternative | §4.1.10a, X132 | S21.4.2, S21.4.10 | yes — tickets are **containers**, because `docker volume create` under an existing name is idempotent (open item O-29) |
| 60 | V8R-06: host facts stay host-scoped config; `ciu host capacity set` publishes one labelled object per daemon; every run reads it | §4.1.10a, §4.5 D2, X133 | S21.2, S21.3 | yes (missing/differs/who-may-write specified) |
| 61 | V8R-08: fold round 4 first; sign-off covers the 8.0 core plus the v8.1 annex | §4.3.17 4.6 amendments, X134–X140 | Appendix D.11 | yes, with T4-07 pending (below) |

**Counts including D-658** (61 rows): **yes 59, partial 1 (row 20: the primary has no library record, O-24), n/a 1 (row 7).** Rows 25, 45, 46 and 49 of §1a are now **yes** (S21.3, S21.4.4, S21.4.2 and S21.2–S21.6); rows 52–61 are yes. The one open question is round 4's T4-07, which is not a D-record row.

### 1c. Round-4 findings (D-658: fold first)

| finding | disposition in draft.9 | status |
|---|---|---|
| T4-01 | deployment namespace and owner marker; disjoint install root; release lock files | folded |
| T4-02 | pointer record, CAS, single candidate resolution, image reload, unique activation files | folded |
| T4-03 | state-root sentinel; no write into a release; `docker_optional` skip; helper image and launcher | folded |
| T4-04 | stage 4 containing-worktree test; flattening shape; fixture defects listed in R.2 (the demo is not edited) | folded in the spec; the demo fix is its own work |
| T4-05 | `--version` pinned; absolute launcher; exact `ciu version --json` proof | folded |
| T4-06 | pending generations; atomic promotion; `--revoke-old`; XDG for `--global`; locks | folded |
| T4-07 | digest before execution; signature required; typed fingerprint required; scan-and-confirm only under `CIU_SSH_INSECURE_TOFU=1` | folded except the **trust root of the digest and key**, `PENDING-OPERATOR (T4-07)` |
| T4-08 | account database, no-follow walk, lock, atomic rewrite, closed `--from` grammar | folded |
| T4-09 | direct-child descriptors with validation; `CIU_LEASE_ID`; owner tuple; completion marker | folded |
| T4-10 | Appendix E and `--surfaces`; `known_host` grammar; docs inputs; cmru install root | folded |
| T3-08 residue | the progress path follows the estate directive (`.assay/progress-<lane>.jsonl`) | folded |

## 2. Stale-residue sweep (proposal, rev 4.3 text)

| pattern | hits | disposition |
|---|---|---|
| "never postgres", "vault only" | §4.1.7a, §4.3.1, §4.7 X111, §4.8, K10 | all state the ruling as superseded by Q9; no live rule |
| owner token, `owner_id`, `ciu.owner` | §4.1.4, §4.1.9, §4.3.1, X80/X98, §4.8, R.2 | all state it dropped, or name the demo files to fix |
| peak or total memory for admission | §4.1.10a | peak survives only as the measured-without-DAMON fallback (row 36) and as informational R-44 fields |
| lock files / a host admission lock | Appendix R S14.4.3/S14.4.9, V8-11, §4.5 table, rev 4.0 trace rows | **contradicted D-655; fixed in 4.4 (X117)** |
| baked checkout paths | §4.1.10, §4.3a D, §4.10 item 36, R.2 | only as the thing the image may not do |
| an open-question marker on Q1–Q16 | none outside the historical amendment tables | clean |
| literal instance ids (`hox0ju`, `98535c`) | §4.1.4, §4.1.5, §4.5 B, Conventions (`hox0ju`); R.2 (`98535c`) | `hox0ju` replaced by `<instance_id>` (X118); `98535c` stays only in R.2, where it names what is stale in the demo files |

## 3. R.2 — the demo files revision 4.0 makes stale

The list exists (proposal Appendix R.2, 13 rows: `ciu.toml`, `ciu.instance.generated.toml`, `ciu.host.toml`, `ciu.instance.toml`, `examples/ciu.instance.joined.toml`, `examples/ciu.instance.generated.joined.toml`, `examples/ciu.resolved.toml.example`, `tools/test-runner/ciu.stack.toml`, `tools/test-runner/ciu.compose.yml.j2`, `examples/minimal/ciu.toml`, `examples/monorepo/ciu.toml`, `assay.toml`, `README.md`). `v8-dstdns-demo/` was not edited. The demo files also still carry the literal id `98535c`; R.2 already lists that.

## 4. `V8-REALIZATION-GRAPH.md` (scope added by the operator)

The note is a 2026-08-26/09-02 design note about the init graph. Most of it is untouched by D-647/D-651..D-655. What the decisions touch, and what was stale:

| item | touched by | stale text | disposition |
|---|---|---|---|
| status and preface | all of rev 4.x | "against proposal rev 3.0 / SPEC-V8 draft.3" | new preface block against rev 4.4 / draft.8 |
| identity / owner token | D-651 Q10, D-647 #5 | none: the note carries no instance ids and no owner token | recorded, no edit needed |
| gate checkpoints | proposal §4.4 | none: the note names V8-7 as its acceptance narrative; checkpoint B holds V8-7 | preface states V8-7's checkpoint and V8-xx numbering |
| Phase A (RG-67/73–76/78 on the v7 track) | D-651 Q2, D-654 | none: the note has no gate content | preface says so |
| admission by warm footprint, Docker-object reservations | D-647 #4, D-653 Q4, D-655 | the waves are described as "deploy" steps with no admission; the interaction of a wave with admission is not specified anywhere | preface states what is specified (a stack start is charged the containers `ciu up` would start) and that the per-wave interaction is draft.8 open item O-3 |
| cmru calling `ciu gate`, atomic cutover | D-652 Q3, Q13 | none | recorded, no edit needed |
| v7 local state under `<stack>/.ciu/` and `ciu.toml [state]` in the trace | proposal V8-10, V8-9 | trace says secrets materialize to `<stack>/.ciu/secrets/<name>` and Vault init state goes to `infra/vault/ciu.toml [state]` | the trace is v7 history and stays; preface maps it to v8 (`ciu.rendered/`, the store `ciu.secrets.toml`, the state root) |
| realization ordering V8-xx | proposal §4.4 | the preface cites "V8-7" without a map | preface adds the map: V8-3, V8-7, V8-8, V8-32 (checkpoint B) |

## 5. Draft.8 open items

SPEC-V8 draft.8 applies **every** Appendix R row (R.1 has 46 rows; all 46 are applied, traced in SPEC-V8 Appendix C and D.9; two of them ask for no text — S4.1.4 "unchanged", and S14.4.7–S14.4.8's "still to fold" half — and are recorded as carried unchanged). Where Appendix R is silent, ambiguous, or conflicts with a rule it does not mention, draft.8 took the smallest consistent reading and the item is listed here with both texts. Each is a question for the reviewer or the operator; none blocks the draft. "R" is Appendix R or the cited proposal section; "draft.7" is the replaced rule.

**O-1. The fourth verdict's name.** R S16.8: "PASS 0 / FAIL 1 / ERROR 2 / NOT_RUN 3 / BUDGET_EXCEEDED 4". D-654 and run-gate RG-78: "BUDGET 4 (the budget was exceeded and the lane is resumable)". Draft.8 uses `BUDGET_EXCEEDED` (draft.7's and run-gate's LaneResult vocabulary; proposal X116). RG-78's text should be aligned before the parity tests are written.

**O-2. A run measured without DAMON.** R S16.6 / proposal §4.1.10a: "Measured without DAMON (a run whose profile has `damon: null`): the measured peak ... is charged ... This is the author's reading of D-653, not a ruling." D-653: "Total or logical memory, including cold pages, is 'nice to know' but irrelevant to scheduling". The peak counts cold pages. Draft.8 S16.6.6 carries the peak fallback; the operator has not ruled. The alternative is the declared `memory_max` until a DAMON run exists.

**O-3. A wave-by-wave bring-up against admission.** R S16.6: "a `compose create` deploy set" is reserved; `V8-REALIZATION-GRAPH.md` / SPEC-V8 S8.4: "Realizations deploy as units in topological waves". Nothing says whether `ciu up` reserves the whole deploy set once or each wave when it starts, and proposal §4.10 item 37 leaves "how `compose create` reserves a whole deploy set at once" to V8-30's carve. Draft.8 S14.1.2 and S16.6.1 say only "the deploy set's containers".

**O-4. What switches admission on, and the `no_daemon` default.** R S2.7: "the no-daemon policy `no_daemon = \"count\" | \"unbudgeted\"` with per-tier counts"; D-653 Q16: "ciu applies whichever policy its config declares ... Refusing by default (option 2) is not wanted". Neither says what happens with no `capacity` table, or with one that omits `no_daemon`. Draft.8 S2.7.2/S2.7.4: admission is on exactly when `capacity.tiers.<t>` is declared; `no_daemon` is required inside a `capacity` table; with no table nothing is budgeted and each run says so. A `capacity` table on a non-local host row is permitted and unused (S2.7.5).

**O-5. Lock contention inside `ciu gate`.** R S18.1: "exit 4 = contention: a lock, or an admission refusal". R S16.8: "BUDGET_EXCEEDED 4" in the gate's closed table, and "NOT_RUN/no-headroom" for admission. Draft.7 S14.4.4: "Contention: fail fast ... `--wait[=<duration>]` blocks". For `ciu gate` the codes collide: a refused lock is not one of the five verdicts. Draft.8 S16.8 gives the gate only the closed table and S18.1 says the gate uses it instead; a lock the gate cannot take should be an ERROR (2) or a new NOT_RUN reason, which draft.8 does not add.

**O-6. How a lane asks to be placed.** R S16.6: "placement of exec and host lanes"; proposal §4.1.10a: "Exec and host lanes may be **placed** (`--place`)". No lane or project key carries the request. Draft.8 S16.6.7 says only that the request is made through the profiling session.

**O-7. Authored `cgroup_parent` and the host singleton.** SPEC-V8 D.6 (5): "S4's instance-scoped naming convention needs a sanctioned exception class for ... host-level infrastructure ... and v8's governance must not re-place a service whose author has explicitly set `cgroup_parent`". D.7 (5) repeats it. Appendix R's D.6/D.7 row only says to close them "per the shipped daemon". Draft.8 closes D.6/D.7 and records that item (5) is not carried by any R row.

**O-8. The path the instance id is derived from. — RETRACTED (V8R-01).** R S4.1.1 said "lexical path" and draft.8 inverted draft.7's "physical" on the premise that "dstdns's id is derived for `/workspaces/dstdns`, the container path". That premise is false: ciu 7.15.1 hashes the **physical** (daemon-visible) path of the git top level (`workspace_env.py:1579-1581`, `_detect_physical_repo_root`), and the id recorded in dstdns's own generated file is the hash of its recorded `physical_repo_root`, not of the container path. Draft.9 derives from the physical path (S4.1.1, S4.1.4); the cutover is identity-neutral again.

**O-9. `--move` against repair in place.** R S4.1.2: "cold `--move`/`--fresh` without the token steps; repair-in-place of an outdated or mismatching generated file with a WARN naming old and new id". Draft.7 S4.1.2: "Without `--host`, re-running `init` on a checkout whose physical path changed is an ERROR ... unless `--move` is given". If a mismatching file is repaired without a flag, `--move`'s refusal (old-id resources live) is bypassed. Draft.8 S4.1.2: plain `init` repairs with a WARN; `--move` additionally asserts a move and keeps its cold refusal; the reviewer should say whether plain repair must also refuse while old-id resources are live.

**O-10. The tenant-provisioning hook.** R S9.5: "tenant namespaces derived from the joiner's id, created and removed by the provider's hook". Proposal §4.10 item 34: "the provider hook ... is named, not specified. It needs a hookkit entry contract like V8-S12.5's." Draft.8 S9.5.8 states the obligation and no hook contract.

**O-11. Which fields moved from `ciu.instance.json` to the shared record.** R S14.7: "the registry is `libraries/worktree`'s `.workspace-instances/` records ... `ciu.instance.json` is ciu's product record (realness records), not a registry ... the record carries the fork commit". Draft.7 S14.7.1 listed `path`, `label`, `created`, `lease_until`, `claims`, `realness` in `ciu.instance.json`. Draft.8 S14.7.1 keeps `claims` and `realness` there and attributes path, label, creation, lease and fork commit to the library's record; the split is inferred.

**O-12. Host lanes and the slice.** R S16.6: "placement of exec and host lanes". Draft.7 S16.6.2: "`host` lanes run in a child cgroup of the slice". Draft.8 S16.6.2 makes an unplaced host lane uncapped (requested values only), because the slice is invisible from a devcontainer (proposal §4.1.10a "Why ciu needs it").

**O-13. Reservation protocol details.** Proposal §4.10 item 37: "the tie-break on equal creation times (by object id), the recheck cadence, how a waiting `created` object shows in `ciu status`, how `compose create` reserves a whole deploy set at once, and how its agreement under concurrent creation is tested are for V8-30's carve". Draft.8 S16.6.1 fixes the tie-break and requires an agreement test; the other three are open.

**O-14. Names Appendix R implies but does not give.** (a) the per-lane raw-code mapping key (draft.8: `exit_map`); (b) the LaneResult's log-path field (`log_path`); (c) the internals of `admission`, `liveness` and `placement` (defined minimally in S16.9); (d) the failure digest's file (`failure-digest.txt`) and field (`failure_digest`); (e) the minimum measuring window `ciu footprint --write` refuses below ("a declared minimum", proposal §4.1.10a): no key declared in draft.8; (f) ERROR's `reason` is free text naming the fix, not a closed vocabulary.

**O-15. `verdict` against `outcome`.** R S16.8: "`verdict`, the raw `exit_code`, `reason` and the log path in the LaneResult". Draft.7 S16.8/S16.9: the LaneResult key is `outcome`. Draft.8 renames the LaneResult key to `verdict` (the assay verdict file keeps `outcome`); lane-result `api_version` stays 1 because draft.8 has not shipped.

**O-16. The minimum judge.** Draft.7 S16.3: "CIU 8.0.0 declares `4.1.0`" as the oldest assay carrying `lanes --json`, `--resume`, `--progress`, `--require-judge-provenance`. R S16.7.2 passes `--state-dir`, which assay added in 5.2.0 (assay CHANGES). Draft.8 declares `5.2.0`; not stated in any source.

**O-17. T4-10's scope.** R S18: "fold T4-10". T4-10's proposed fix also covers cmru/`get.py` packaging inputs, README/DESIGN-GUIDE/CONSUMERS documentation and a `[project.installer]` for ciu. Draft.8 folds the testable surface (`ciu version --surfaces --json`, `ciu/surfaces`) and the `known_host` grammar only; the rest stays in proposal §4.10 item 28.

**O-18. The "git-family state root".** R S16.7.2: "`--state-dir <git-family state root>/ciu-gate-state/<path>/`". No source defines the directory. Draft.8 S16.7.2: the git common directory for a git checkout, the state root (S2.6) otherwise.

**O-19. Two ways to name the gate tier's slice.** R S16.2: `cgroup_slice` / `cgroup_slice_env` (R-10); R S2.6/S2.7: `capacity.tiers.<t>.slice` / `slice_env`. One fact in two places (invariant I1). Draft.8 S16.2.1 requires the two to agree when both are declared (stage 11 ERROR).

**O-20. Where the pinned judge artifact is in `command`.** R S16.3: "`command` + `sha256`"; proposal §4.1.10 example: `command = ["python3", "tools/assay/assay-7.2.0.pyz"]`, "`sha256 = <64-hex digest of the zipapp>`". Draft.8 S16.3.3: the first element of `command` that is a path to an existing file.

**O-21. `memory_min`: admitted, and charges nothing.** R S13.2.1: "`memory_min` is written to the container's scope and admitted against the slice floor (a protection)". Proposal §4.1.10a: "It protects memory and admits nothing". Draft.8 S13.2.1 reads both as true of two different checks: the floor admission of CIU-94 (against the slice's `memory.min` ceiling) and S16.6's budget admission, to which `memory_min` contributes nothing.

**O-22. Imported assay lanes.** R S16.3: "`import = { environment, lanes }`". RG-76 allows `"all" | [<glob>...]`; proposal §4.5 A7 allows `"all"|[names]`. Draft.8 allows names, not globs.

**O-23. Joins no preset produced.** R S9.5: "joins only through `[ciu.instances.join_presets.<p>]`". Draft.7 S9.5.5 said "a hand-written instance file is equivalent". Draft.8 S9.5.1 makes a joined Realization no preset produced a stage-12 ERROR.

### 5a. Resolutions in draft.9 (dstdns D-658)

The review's phase 2 (§3) classes each item. Resolved by the decisions and the shipped code, without the operator:

| item | resolution | where |
|---|---|---|
| O-1 | keep `BUDGET_EXCEEDED`, and say in S16.8's neighbourhood that it is D-654's "BUDGET"; RG-78 amended | S16.1.3; RG-78 |
| O-2 | closed: no warm figure means unmeasured, so `memory_max` (D-651 Q5); the peak is informational | S16.6.6 |
| O-5 | the gate waits for its locks, bounded by `--admission-wait`, then NOT_RUN/`lock-busy` | S16.5.8, S16.8 |
| O-6 | lane key `place` (bool, `exec`/`host` only), passed as `--place` | S16.5, S16.6.7 |
| O-7 | the daemon's slice is its own stack's `[governance] cgroup_parent` (a per-stack override, S13.2); a consumer reaches the daemon through `[testing.profile] daemon` or the socket, so S4.2 needs no exception class | S13.2, S16.6.10 |
| O-8 | retracted (above) | S4.1.1 |
| O-9 | plain repair refuses while live resources carry the old id and their `ciu.checkout` is this path (`clean --identity` first) or a vanished path (`--move`); it proceeds for a copy | S4.1.2 |
| O-10 | specified: a `tenant` hook phase, tenant paths and delivery, removal timing | S9.5.8–S9.5.10, S10.1.7 |
| O-11 | agreed; the primary's missing library record is O-24 | S14.7.1 |
| O-12 | agreed; its admission needs Docker (S16.6.1) | S16.6.1, S18.3 |
| O-13 | the tie-break and visibility race are **PENDING-OPERATOR (V8R-04)**; counted states and `ciu dev` are fixed | S16.6.1 |
| O-14 | (a)–(d), (f) as named; (e) the minimum window is now `[testing.footprint] min_window` | S16.2.4, S16.9.5 |
| O-15 | agreed: `verdict`; RG-78 uses it too | S16.9 |
| O-16 | 5.2.0 stays for `--state-dir`; the selective flags have per-flag floors (`--reuse-from` ≥ 7.1.0, `--rejudge`/`--rejudge-outcome` ≥ 5.2.0) | S16.7.2 |
| O-17 | the scope question is **PENDING-OPERATOR (V8R-08)** | S7.2.4, S17.4 |
| O-18 | the git-family state root is `<git-common-dir>` for a git checkout (S2.1/S2.6.2 corrected) | S2.1, S2.6.2 |
| O-19 | one source per tier: `capacity.tiers.<t>.slice|slice_env`; `testing.cgroup_slice*`, the global `governance.cgroup_parent` and every hardwired variable removed | S2.7.2, S16.2.1, S13.2 |
| O-20 | agreed | S16.3.3 |
| O-21 | agreed, and the floor check now rides the reservation (`ciu.reservation.floor`), unevaluated without the daemon | S13.2.1 |
| O-22 | agreed: names, not globs | S16.3 |
| O-23 | agreed: a joined Realization no preset produced is an ERROR | S9.5.1 |

Still **PENDING-OPERATOR**: O-3 and O-13's stack reservation unit and ordering (V8R-03, V8R-04), O-4's capacity location and the daemon-absent policy (V8R-06, V8R-09), and O-17 (V8R-08). **New open items:**
- **O-24.** The primary checkout has no `libraries/worktree` lifecycle record (`workspace.py:166-169`), so `primary` is identified by `git worktree list` (S14.1.1). Giving it a record is a library change; `libraries/worktree` has no backlog file to file it in, so it is recorded here for the controller.
- **O-25.** With no gates slice declared, an `ephemeral` lane runs with Docker's default cgroup parent and says so once (S16.2.1). The review says only that the slice is declared once; this fallback is the writer's reading (previously an ERROR).
- **O-26.** RG-75's `init` and `env` service keys, its join of an exec target's network, and RG-76(c) are **retired** in v8 (S16.12), and the entries are amended to match; this is the writer's disposition of the review's "carry or retire".
- **O-27.** `ciu gate exec` returns the command's own exit status, outside the closed table (S16.8.2a); the review offered that or a mapping, and this takes the first.

**R.2 confirmation.** The stale-demo list exists in the proposal (Appendix R.2, 13 files) and is unchanged; `v8-dstdns-demo/` was not edited.

## 6. Upstream backlog amendments (dstdns D-658)

Run-gate's backlog is the legacy single file `run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md`; cgroup-profiler's is the managed `scripts/cgroup-profiler/nyxloom-trove/backlog/`. Status of RG-73/75/76/78 when checked: all four are OPEN (2026-10-03), no run-gate worktree or branch carries their implementation (`git log -- run-gate-project` shows only the backlog commits for them; the open run-gate worktrees are `rg55-followups-run-gate` and `rg56-admission*`), so the amendments land before the build.

- **RG-78:** `BUDGET` versus the spec's `BUDGET_EXCEEDED`; the `--resume` oracle dropped; "missing base" is NOT_RUN/`no-base`; `lock-busy` and `judge-digest` named.
- **RG-73:** the worktree is mounted at its own path plus the git common dir (D-653 Q7 → A); `mount_worktree_at` retired.
- **RG-74:** the non-merge-trunk oracle's "exit 2" becomes NOT_RUN/`no-base`.
- **RG-75:** `env`/`init` service keys and the exec-network join retired; lane services are `ephemeral`-only; charged in the lane's reservation.
- **RG-76:** the `required_env` auto-forward oracle (c) retired (S16.4.5).
- **cgprofile:** the combined `damon.warm_set_bytes` Summary series is filed as CP-16 (checked: CP-6 is the per-class series; CP-15 is the reservation mirror and now optional).

## 7. Draft.9 resolutions of D-658 (round 2) and new open items

Resolved: O-3 and O-13 (the stack reservation unit and ordering: S21.4.2–S21.4.4), O-4 (the capacity location and the daemon-absent policy: S21.2, S21.3, S21.8), O-17 (the scope question: round 4 is folded). New:
- **O-28.** Round 4's v7-line findings (the `get.py` unpinned install, the key path aliasing S14.3a, the unauthenticated root program, `authorized_keys` handling, the `known_host` grammar) are defects of **shipped** CIU-93 and cmru KI-24 code; filing them needs ids allocated in the ciu and cmru backlogs, which this pass does not do (enrollment proposal rev 3 §11 lists them).
- **O-29.** The operator's ruling says tickets come from "Docker's atomic name reservation". The adversarial review recommended volumes; draft.9 uses **containers**, because Docker's volume create is idempotent for an existing name and cannot arbitrate. A ticket therefore needs the helper image on the daemon; an absent image is an unreadable fact (S21.8.2).
- **O-30.** T4-01 reviewer flagged that its fix challenges a settled decision "narrowly" (the state-root/bundle-dir design). Draft.9 changes `bundle_dir`'s default to `/var/lib/ciu` and adds the per-instance namespace; the in-checkout posture is untouched. Listed for the operator to confirm.
- **O-31.** The default of `[ciu] admission` is `off`. The alternative reading of D-658 ("even if configured it needs a switch to turn it off") is `on` whenever a capacity table is declared; draft.9 rejects it because a published capacity object on a shared daemon would then switch every participant on (S21.1.3).
- **O-32.** The demo's stale files (R.2) now include the monorepo fixture's wrong build context, the missing child gitignore coverage and the hosts' `bootstrap` values; the demo is still not edited.
