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
| 31 | D-652 Q14 — each wheel ships its skills; shared `skills install\|list\|check\|uninstall` from cli-extended (CLI-EXT-02); per-tool adoption follows | §4.1.15; §4.4 V8-33 | S18 | yes | — |
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

(Appended after the SPEC-V8 draft.8 work; see the end of this file.)
