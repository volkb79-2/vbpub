# Response to the third-party review of the CIU v8 design set (T-01..T-35) — 2026-09-03

**Input:** `CIU-V8-THIRD-PARTY-REVIEW-2026-09-02.md` (independent reviewer; 35 findings: 11 BLOCKER, 18 MAJOR, 5 MINOR, 1 NOTE; seven alternative designs A–G; a demo walk that derived prod3's 22-Realization closure, five waves and the publication table). The reviewer's verdict: "draft.3 is not implementable as written."
**Method:** every finding was checked against the text of `SPEC-V8.md` draft.3 and the demo before a decision (the checks are quoted per finding). Two findings change decisions the operator had taken; those were put to the operator, who decided (§2). Everything else is the author's call and is reasoned below.
**Output:** `SPEC-V8.md` 8.0.0-**draft.4** (in place; Appendix D lists every changed rule with its finding), `CIU-V8-TESTING-GATE-PROPOSAL.md` **rev 3.1** (§4.3.13, §4.7 X57–X72, §4.10 updated), the demo corrected (README D26–D31), this document.

**Verdict on the verdict.** Accepted. Of 35 findings, 31 hold as stated; 3 hold in part (T-04's mock case was already refused by S5.3.5, T-09's ephemeral case by S10.1.4, T-11's citation of S14.4.5 concerned the rendered file only — but each finding's substance stands); T-35 is documentation and API surface. Every finding produced a change. Draft.4 is draft.3 corrected, not a new model: the entity split, bindings, plain-TOML declarations, derived contracts, structured secrets and the directory lock all survive; what changed is the precision of about forty rules and two deployment mechanisms (releases and receipts).

---

## 1 Disposition table

Legend — **A** accepted as proposed · **A′** accepted with a different fix · **P** partially accepted · **R** rejected (with reason) · **O** operator decision.

| id | sev | disposition | what changed (spec rule) | reasoning |
|---|---|---|---|---|
| T-01 | BLOCKER | A | S4.3.1 | The wording "every `compose_project` unique" was wrong for a per-Realization value; the check is now per namespace: project unique among Realizations, names among services, keys/aliases on the network, at max replica index. |
| T-02 | BLOCKER | A | S1.4 `dns_name`, S7.2, S7.3 | `fqdn` was typed with the single-label grammar; every real FQDN in the demo failed it. |
| T-03 | BLOCKER | A | S6.10 | `service` on a hook entry existed only in prose; the entry object is now closed: `run`, `service`, `provides`, `secrets`, with validation of `service`. |
| T-04 | BLOCKER | P (a, b accepted; c refuted) | S7.3, S7.3.2, S7.6.5, S5.3.5 | (a) `realized_by` now names a LogicalService, so the readiness bearer is its unique variant service even when a Realization backs several capabilities — the reviewer's first option. (b) A `requires` on a `per_host` capability targets the consumer host's copy and is an ERROR when that host does not place it. (c) The claim that S9.3.4 "returns nothing rather than refuse" for a mocked target with data delivery was already false: S5.3.5 refused it; draft.4 also refuses `facts` on such a binding and names the layout. The proposed `optional = true` binding was not adopted: an ordering-only binding to a mocked capability already has no edge and no data, so nothing is left to make optional. |
| T-05 | BLOCKER | A | S8.5.1 | The gate ignored `wait = "started"`; it now evaluates the strongest predicate per edge (`Running`, healthy, completed, facts). |
| T-06 | BLOCKER | A | S19.1–S19.3; demo `examples/minimal/` | Bare `ciu init` wrote deployment tables without a Realization — exactly the invalid zero-instance shape. Bare init now writes the zero-instance skeleton; `--stack` requires `--image` or `--from-compose`; scaffolds must pass `ciu check`; the minimal example says what was hand-added and ships the `.gitignore`. |
| T-07 | BLOCKER | A | S16.9, S18.4 | R-49 unified the envelope in prose only; the LaneResult now carries `status` mapped from `outcome`, plus the `ciu/lane-result` header. |
| T-08 | BLOCKER | A′ | S16.7.2, S16.3 | `--progress` takes a path (run-gate R-38); the path is `<evidence_dir>/<lane>/progress.jsonl` rather than `.assay/…` (no hidden directory). Instead of a fixed floor in every project, CIU declares its own minimum drivable judge (4.1.0 for 8.0.0) and refuses a lower floor; the demo floor is `>=4.1`. The reviewer's capability record is noted for later (a judge `--capabilities` output would let ciu check features rather than versions). |
| T-09 | BLOCKER | A′ | S17.3.2, S17.3.3 | The store row could not represent "transported"; file values were never stored. A per-host **capsule** is built at push: file values read on the sender, vault values pre-fetched when the target lacks a resolution (refusal when the sender cannot reach Vault either), imported on the target as `source = "transport:…"` and never refreshed. `native` excluded; ephemeral cross-host sharing was already an ERROR (S10.1.4). SOPS/Vault Agent (Alternative C) not adopted as requirements — see §3. |
| T-10 | BLOCKER | A′ | S7.4.7, S7.8 step 4a, S11.4 | `host_network` services are never published and `host_port`/`host_bind` are refused on them; same-host consumers resolve them through an injected `extra_hosts: ciu-host:host-gateway` (Docker's supported way to reach the host from a bridge network) rather than "an explicitly selected host address"; cross-host consumers use the admitted host address with the container port. |
| T-11 | BLOCKER | O → P | S2.3.4, S14.4.5, S4.5.3, S18 | The operator kept the in-checkout posture (§2.1). The text no longer overpromises: deleting the non-regenerable files is destructive as in v7; `ciu instance backup|restore` exists; `instance init` adopts still-running containers by their `ciu.checkout` label. The registry alternative (A) is recorded as rejected by decision, not by argument. |
| T-12 | MAJOR | A | S16.4.5; demo | The demo's tester was capped at 800M under 2–4G exec lanes and the `clean` environment lacked `SCHEMA_GATE_PG_DUMP`; the lane's available environment is now defined and the demo fixed (tester 4G, `forward_env`). |
| T-13 | MAJOR | A | S7.8 step 5, S7.4.4; demo | The consul override bound to 127.0.0.1 was the author's own site example — replaced; a cross-host resolution through a loopback-bound host publication is now refused; authentik's 9000 collided with MinIO's 9000 on gstammtisch's mesh address — moved to 9010; `allow_from` is stated to be declared admission data, and a stack that declares it without a template consuming `allow_from_resolved` gets a WARN. Firewall generation was not adopted (CIU does not program hosts). |
| T-14 | MAJOR | A | S8.4.1; demo example | The example waves were hand-written and incomplete; the algorithm is written down (consumer→provider, sibling edges discarded, `1 + max`) and the example regenerated to the reviewer's five-wave derivation, which the rule reproduces. |
| T-15 | MAJOR | A′ | S8.5.5, S5.3.6, S5.2 | Unconsumed selected providers were unchecked. Acceptance now requires every selected service healthy and every one-shot completed, consumers or not. The reviewer's `accept = [...]`/`unchecked = true` became a WARN for seeded/simulated variants with an empty derived contract plus an optional `verify = [facts]` on the LogicalService — the same effect without a second contract vocabulary. |
| T-16 | MAJOR | O → A′ | S8.5.3, S17.4.3–S17.5 | The operator adopted receipts (§2.2). Cross-host facts are accepted only from a valid receipt (same instance, layout and resolved-file digest) carried by `ciu activate`; otherwise `assumed` + WARN, `--require-receipts` for ERROR. Signing was not adopted: receipts travel over the same authenticated SSH channel as the release; a pinned host key already authenticates the source. |
| T-17 | MAJOR | P | S4.3.1, S4.5.1, S4.5.3 | All namespaces are checked at max replica; a `ciu.checkout` label and ownership verification before deletion close the "wrong owner" hazard. UUID authority was not adopted (operator, §2.1); the 24-bit collision within one daemon is detected by the label mismatch and refused. |
| T-18 | MAJOR | P | S4.1.4, S4.5.3 | Path-hash identity stays (operator). The physical path is now **proven** by a sentinel bind before it is written (Docker Desktop and remote daemons refuse honestly); a moved checkout adopts its resources with `--move`. |
| T-19 | MAJOR | A′ | S14.4.3, S9.5.7, S16.5.3, S14.4.6 | Real: `flock` detects no deadlock and a mutual join deadlocked. Locks are acquired in ascending instance-id order over a set resolved before mutation, and the join graph must be acyclic; gate shared-resource locks moved from unlinkable files to the owning stack directory. A SQLite broker was not adopted (the directory locks suffice on the supported local filesystems; NFS is refused). |
| T-20 | MAJOR | A | S16.4, S16.12 | R-56 lifted rule numbers without behaviour: the detached create/start/wait/remove state machine, a private writable git config with `safe.directory`, and the dual mount are now normative text. Alternative E (shared conformance fixtures for both tools) adopted as a plan item (proposal V8-19). |
| T-21 | MAJOR | A | S16.6.1, S16.6.4, S16.6.5, S16.9 | Admission is a locked transaction with reservation files and stale-pid recovery; exec caps are requested values with `resources_applied = null`; per-lane result writes are serialized. |
| T-22 | MAJOR | A | S13.3, S13.3.2, S13.4 | The compose↔cgroup conversions are written (`memswap_limit` = memory + swap; `cpu_shares` from Docker's v2 mapping inverse), values are read back after start, and `requested`/`applied`/`unsupported` are reported separately. Writing the cgroup files directly (the reviewer's preference) was not adopted for 8.0.0: Docker's own adapter plus read-back gives the evidence without CIU owning cgroup placement of containers. |
| T-23 | MAJOR | A′ | S12.1, S12.2, S12.4, S6.10 | Clean allow-listed hook environment, per-entry `secrets`, context rebuilt per entry with i→i+1 state visibility. Validation is not moved to `--live`; it runs only with `--validate-hooks` or inside `ciu up`, in the clean environment, without secrets, and is labelled as executing consumer code — the "side-effect-free" promise is now stated as a convention CIU cannot enforce. |
| T-24 | MAJOR | A | S10.7, S11.4 | Per-service secret directories mounted at `/run/secrets`; refresh by rename + fsync; paths unchanged for applications. |
| T-25 | MAJOR | O → A | S17.3, S17.4 | Manifested releases staged and verified, atomic `current` switch, `previous`, real rollback (§2.2). OCI transport optional later; rsync/scp stay transports. |
| T-26 | MAJOR | A | S6.3.2, S7.4.5, S7.4.6 | Socket claims `(host, protocol, bind, port)` with wildcard overlap, registry reservation and a bind probe. |
| T-27 | MAJOR | A | S11.2, S11.3 | `network_mode/ipc/pid: service:` are rewritten to compose keys; `links`, `extends`, `volumes_from` (and `build`, T-29) are forbidden in templates. |
| T-28 | MAJOR | A | S2.4 | Renamed to a lint: known credential formats and store-value matches are errors; suffix heuristics warn with path-level suppressions; output names the comparisons run and never says "secret-free". |
| T-29 | MAJOR | A | S6.2 `build`, S3.4.3, S17.6, S11.2 | Ownership next to the image: `build = { context, dockerfile, args, target }` on the service; the global vendor list is withdrawn; a string image without `build` is pulled — an explicit policy, not an inference from the name (a namespaced image without `build` WARNs). Twelve demo services declare their build. |
| T-30 | MINOR | A | S6.9.2, S6.8.5 | Overlap checks per service; the symlink case documented; `seed` is an idempotent transaction. |
| T-31 | MINOR | A | S16.2.2, S16.4.2 | `[testing.externals]` (literal URL or named variables, with a probe); zero-instance bindings may target them. |
| T-32 | MINOR | A | S16.2.1 | Recursive, cycle-checked inheritance; paths resolved relative to the declaring file; `ciu testing flatten`. |
| T-33 | MINOR | A | S3.7.1, S12, S16.9, S17, S18.4 | `api`/`api_version` per artifact and a compatibility policy. |
| T-34 | MINOR | A | S7.5, S7.4.6, S8.6 references | Includes recursive; structured publications; the dangling `S8.6.4` references fixed. |
| T-35 | NOTE | P | S3.7.1, S3.7.6, S18 | `resolved.capabilities` index and `ciu query`; names kept (the operator approved them in the previous round). A CUE/Pkl frontend is recorded as an optional later compiler, not part of v8. |

---

## 2 Operator decisions in this round (2026-09-03)

### 2.1 Authoritative state stays in the checkout (T-11, T-17, T-18, T-19; Alternative A)
Asked: registry under the git common dir with UUID identity (recommended); keep in-checkout files (v7 posture); hybrid. **Decided: keep in-checkout files.** Consequences carried into draft.4: the spec states the destructive cases plainly (S2.3.4), adds `instance backup|restore`, proves the physical path (S4.1.4), verifies ownership by a `ciu.checkout` label before deletion (S4.5.3), orders lock acquisition and forbids join cycles (S14.4.3, S9.5.7), and moves gate shared locks to a directory git tracks (S16.5.3). What is *not* solved by this posture, stated for the record: a 24-bit path-hash collision between two checkouts is refused rather than avoided; a moved worktree needs `instance init --move`; `git clean -x` still destroys the store — the same three properties v7 has today.

### 2.2 Manifest releases and activation receipts (T-16, T-25; Alternatives B and D)
Asked: adopt both (recommended); keep rev 3.0 push; releases without receipts. **Decided: adopt both.** Draft.4 S17.3–S17.5 specify the release manifest and closure, staging and verification, the atomic `current`/`previous` switch and real rollback, the per-host secrets capsule, receipts written by `ciu up` and carried by `ciu activate`, and the acceptance rule for remote facts.

---

## 3 Alternative designs — disposition

| alt | disposition | reasoning |
|---|---|---|
| A durable registry + UUID identity | **rejected by operator decision** (§2.1) | The reviewer's argument is sound; the operator weighed it against the estate's visible-files doctrine and the v7 continuity and chose the v7 posture with local fixes. Recorded so it can be reopened if a collision or a `git clean -x` loss actually occurs. |
| B manifested, transactional activation | **adopted** (S17.3–S17.4) | Cheap, standalone (a manifest, a staging directory, a symlink), and it removes a real mixed-tree failure and a fake rollback. OCI artifacts stay optional. |
| C binding directories + SOPS/Vault Agent | **partially adopted** | The atomic directory boundary was adopted for secrets (S10.7). A Service-Binding-style `/run/ciu/bindings/<local>/` projection as a third delivery was not added for 8.0.0 (two deliveries already cover the demo; a third is surface without a consumer yet) — recorded in the proposal's gap list. SOPS/Vault Agent are not required: the capsule (S17.3.2) covers transport, and renewable-secret rendering stays out of scope (S1.1). |
| D digest-bound receipts | **adopted** (S17.4.3–S17.5), unsigned | Receipts ride the authenticated SSH channel; signing keys would be a second trust root for no added assurance in this estate. |
| E shared gate conformance fixtures | **adopted as a plan item** (proposal V8-19) | Both tools keep their runtimes; a versioned fixture package in vbpub (argv builders, black-box scenarios) is what makes "lifted" checkable. |
| F query APIs, optional typed authoring | **adopted** (`ciu query`, `resolved.capabilities`, S18.4); CUE/Pkl compiler deferred | Narrow queries are cheap and stop scripts from depending on TOML layout; a typed frontend is a separate, optional tool. |
| G migration plan with proof obligations | **adopted in shape** (proposal V8-18, S18 `migrate --plan`) | `ciu migrate --plan` emits unresolved decisions and refuses to finalize until answered; the v7-vs-v8 compose comparison is a SHOULD (ciu 7.x is installed beside 8.x during the cutover). |

---

## 4 Corrections to the previous review round (R-nn) that this round exposed
- **R-24** claimed serial activation closed cross-host synchronization; it closed ordering only. Receipts (T-16) close the facts.
- **R-42** proved the directory lock had no split-mutex hole, but left gate lock files and an unordered acquisition (T-19).
- **R-46** looked at the duplicated `instance_id`, not at where the record lives (T-18).
- **R-49** unified the envelope by assertion; the LaneResult was not updated (T-07, T-33).
- **R-56** carried run-gate rules by citation; three of them lost their behaviour (T-20).

---

## 5 Still open after this round
1. A judge capability record (`assay --capabilities`) so CIU can check features rather than a version floor (T-08's better form; needs assay).
2. The binding-directory projection (Alternative C) as an optional third delivery.
3. Signed receipts if activation ever leaves the SSH channel.
4. Direct cgroup writes instead of Docker's adapter (T-22's stronger form) — read-back makes the gap visible first.
5. The reviewer's demand for a machine-executable conformance fixture of the demo: the resolved example is now derived by rule, but the executable fixture is V8-13's job.
6. Everything in the proposal §4.10.

---

## 6 Round 2 — the delta audit of draft.4 (2026-09-03, later)

**Input:** `CIU-V8-THIRD-PARTY-REVIEW-ROUND2-2026-09-03.md` (same reviewer; disposition audit of T-01..T-35: 14 landed, 13 landed-but-incomplete, 8 landed-and-broke-another-rule; new findings T2-01..T2-10: 4 BLOCKER, 6 MAJOR; a demo re-derivation). Verdict: "materially better than draft.3, still not implementable as written." **Output:** `SPEC-V8.md` 8.0.0-**draft.5** (every changed rule in Appendix D §D.2), proposal **rev 3.2** (§4.3.14, §4.7 X73–X84, §4.8, §4.10 items 18–22, §4.11 N22), the demo corrected, this section.

**Verdict on the verdict.** Accepted. All ten findings hold, and every disposition-audit row that was not "landed" produced a change. Two remarks did not hold as stated: Appendix B's minimal `ciu.toml` was valid TOML (`location = "web"` appears once; the T-06 remark about a repeated key was wrong), and the demo's eleven `build` tables are by design — the twelfth service shares an image, and the reviewer's underlying point, that draft.4 could not express a shared project-built image, was right and is fixed under T2-05. No finding reopened an operator decision: T2-08 is a cost of the in-checkout posture whose fix stays inside it.

### 6.1 New findings

| id | sev | disposition | what changed (spec rule) | reasoning |
|---|---|---|---|---|
| T2-01 | BLOCKER | A | S17.4.3, S17.4.4, S17.5, S8.5.3, S8.5.4, S3.7.3, S18 | The whole-file digest included `rendered_at` and `[ciu.host.generated]`, so the validity test could never pass across hosts, and once weakened it would have accepted a receipt from before a reset. The receipt now has a canonical **subject** (instance, layout, host, selection, release or plan digest, `activation_id` minted by `activate apply`), and container incarnations, one-shot exits and per-fact observations in the body; validity compares subjects and requires the consumer's own activation id; a required remote fact without a valid receipt entry is an ERROR by default — `--allow-assumed` replaces `--require-receipts` as the explicit escape. Signing stays out (operator, §3 D). |
| T2-02 | BLOCKER | A | S8.5.5, S5.3.6, S5.2 | The two conjuncts were unsatisfiable for one-shots; acceptance is a partition. The WARN for an empty-contract seeded/simulated selection was no acceptance choice — it is an ERROR unless `verify` or the variant's `unchecked = true` is declared (the reviewer's spelling). |
| T2-03 | BLOCKER | A | S3.4.7, S6.10, S5.4, S16.8, S18, S8.5.4, S19, S15.3 stage 12, S3.8.5 | All eight mismatches confirmed and closed; S3.8.5 makes a conformance test generated from the schema definition part of the definition, so the class fails the build. The proposal's stale spellings were fixed in the same change. |
| T2-04 | BLOCKER | A′ | S6.10, S8.7 step 2, S12.2 | The reviewer's minimal rule (`pre_secrets` consumes nothing) would block the real bootstrap case — a deploy-time registration token fetched with a file- or ask-sourced credential — and a `bootstrap_inputs` phase would be a second vocabulary for the same thing. The phase/source matrix is stated instead: `pre_secrets` entries may list `file`, `ask`, `host` and local `generate` keys, materialized for them ahead of step 3, nothing else; a static check, no new phase. |
| T2-05 | MAJOR | A | S17.3.1, S17.3.3, S17.3.6, S17.4.1, S7.2, S6.2, S3.4.3 | Closure: every non-ignored file under a placed stack plus declared hook `inputs` (the reviewer's "declared inputs" option — "manifest every tracked file under a declared root" would ship unrelated files). Images: registry digest or `docker save` archive, verified on the target; `--images none` recorded and refused at apply. `candidate` pointer, `--release`, CIU-owned `current`/`previous` switch, rollback refusal without `previous`, host `rollback` command withdrawn. Shared image tag: one `build` per reference, other services share it. Push-time materialization of local `generate`/`ask` values (the T-09 audit row). |
| T2-06 | MAJOR | A | S6.3, S6.3.2, S7.4.7, S7.8 4a, S8.5.2a | `listen` declared on host-network endpoints; claims canonicalized (`0.0.0.0` = every IPv4 address; `::` conservatively every address of both families — `bindv6only` is not inspected); a resolution is admitted only where the consumer can reach that address; a live TCP probe before the dependents' wave. |
| T2-07 | MAJOR | A′ | S16.6.1, S16.6.4, S16.6.5, S16.5.7, S16.9.4, S16.7.2, S16.4, S16.10 | The capacity object is the lock: the slice's cgroup directory (host-visible, exists iff the slice does) with a ledger keyed by the cgroup path. For exec targets a different resolution than the reviewer's container-cgroup ledger: an exec target is used by one lane at a time (its stack-directory lock — run-gate RG-39's rule lifted, and the operator's design answer A), so container capacity needs no ledger and "two 3 G lanes in one 4 G tester" cannot arise. Per-run directories with `run_id`, a `last` pointer, pruning of complete runs only, `--resume` so assay finds its progress. |
| T2-08 | MAJOR | A | S4.1.1, S4.1.2, S4.5.1, S4.5.3, S14.8.2, S18 | A 128-bit `owner_id` generated at init, stored in the generated file (it travels with pushed bundles), stamped as `ciu.owner`; ownership and adoption compare it; `--move` requires it; adoption without it only when the daemon proves the path; `ciu instance adopt --owner` for the lost-and-moved case. Accepted as the author's call: it does not reopen §2.1 — the token lives in the checkout and no identity changes. |
| T2-09 | MAJOR | A | S13.3, S13.3.2 | Verified by computation: the ceiling form round-trips all 10 000 weights, nearest rounding fails 4999 of them. `memory_max = "max"` with finite swap writes `memory.swap.max` directly (the `memory_high` path) rather than refusing the combination. Enforceable-cap mismatches abort; weights and soft limits WARN. |
| T2-10 | MAJOR | A | S2.4.2, S2.4.3, S10.2.6 | Every file against every store value; only the values `secret()` actually requested for that service are authorized in that file, and the count of omitted authorized matches is printed. |

### 6.2 Disposition-audit rows that were incomplete or broke a rule

| row | fix |
|---|---|
| T-06 | S18 `init` synopsis (`--image`, `--service`, `--test-argv`); Appendix B needed no change. |
| T-08, T-19, T-32, T-33 | proposal rev 3.2: judge floor `>=4.1`, the S14.4.3 lock order, `[ciu] inherit`, `api = "ciu/lane-result"`. |
| T-09 | S17.3.2 push-time materialization. |
| T-10, T-26 | T2-06. |
| T-11 | S14.8 backup/restore contract. |
| T-12 | S15.3 stage 12 compares with S16.4.5's available environment. |
| T-15 | T2-02. |
| T-16 | T2-01. |
| T-17, T-18 | T2-08. |
| T-21 | T2-07. |
| T-22 | T2-09. |
| T-23 | `env_allow` in S6.10's closed `[hooks]` set; T2-04. |
| T-25, T-29 | T2-05. |
| T-28 | `secret_lint_allow` in S3.4.7; T2-10. |
| T-31 | `external-missing`/`external-down` in S16.8. |

### 6.3 Demo re-derivation
The reviewer's waves, resolutions and socket-claim table agree with the example. The one discrepancy — the fact row in `[resolved.gates.3]` — was a rule gap, not an example error: draft.4 S8.5.2 named only binding `facts`; draft.5 states that minter and pki facts are gate facts too (a consumer would otherwise fail at secret materialization, one step later and with a worse message), which makes the example row correct by rule. `bundle_dir` is now a parent directory (`/opt/ciu`), the three `rollback = "ciu down"` lines went with the key, the stale `realized_by = "tailscale_node"` comment names the LogicalService, `ciu.toml`'s "one level" comment on bundle includes is corrected, the receipt shape in the resolved example shows the subject, and the README records the header-notation convention (D32–D37).

### 6.4 Design answers folded in the same draft
**A** — canonical lock keys and `ciu lease` (S14.4.7–S14.4.8, S16.5.7, S14.7.1, S18). **B** — `[ciu] inherit` (S3.1.5, S3.4.7, S16.2.1, S16.11.1) and the shared tester as a per-project two-file stack over the shared Dockerfile directory (S5.4, S6.2); the handoff note's "shared `location`" was withdrawn once the text showed rendered artifacts and the lock live in the stack directory (proposal X83).

### 6.5 Still open after this round
Items 1–6 of §5 stand. Added: exec parallelism unsupported by design; admission per uid; `plan_digest` requires byte-identical files; image archives are whole tarballs; the monorepo's consumer work (proposal §4.10 items 18–22). A targeted round-3 review of the rules draft.5 introduced (S17.4.3, S17.3.6, S16.6.1, S16.9.4, S3.1.5, S14.4.8, S4.5.3) is the recommended next review, scoped to Appendix D §D.2.

---

## 7 Round 3 — draft.5 (2026-09-03, later still)

**Input:** `CIU-V8-THIRD-PARTY-REVIEW-ROUND3-2026-09-03.md` (same reviewer; round-2 fix audit: 3 landed, 2 incomplete, 5 landed-and-broke; new findings T3-01..T3-10: 6 BLOCKER, 4 MAJOR; the demo re-verified correct at the topology layer). **Output:** `SPEC-V8.md` 8.0.0-**draft.6** (Appendix D §D.3), proposal **rev 3.3** (§4.3.15, §4.7 X85–X94, §4.8, §4.10 items 23–26), the demo (host-file split, receipt and activation comments, `examples/monorepo/`), this section.

**Verdict on the verdict.** Accepted in full: every finding holds against the text. T3-02 is narrower than the defect it found — the same reasoning applies to the store, the data directories, the realness records and the hook state — so draft.6 fixes the class with a state root rather than the instance with a host-facts overlay. T3-05's narrow reopening is accepted as the author's call: a move is cold and a copy is refused; the in-checkout posture, path-derived display identity and the token as a collision mark all stand, so nothing went back to the operator.

### 7.1 Findings

| id | sev | disposition | what changed (spec rule) | reasoning |
|---|---|---|---|---|
| T3-01 | BLOCKER | A | S17.4.1, S17.4.3, S17.4.4, S17.5, S8.5.4 | The activation manifest (`ciu activate plan`, `ciu/activation`) carries the id and, per host, the expected release digest and selection; a receipt is validated against the **provider's** entry, never the consumer's; an absent id never matches. `plan_digest` is withdrawn: a hand-started host computes the digest of the release `push` would build for it, which covers hooks, templates, seeds and declared inputs — the reviewer's "complete declared runtime closure" is exactly the release closure. |
| T3-02 | BLOCKER | A, widened | S2.6, S14.2, S9.4, S10.6, S6.8, S6.10, S17.3.1, S17.3.3, S17.3.4, S17.4.1, S17.4.2, S14.1.1 | The state root; `ciu.host.toml`; realness in the instance record; prepare → apply → health → receipt → switch in both directions; pointers unchanged on failure; no automatic compensation. The reviewer's `<bundle_dir>/state/<instance>/<host>/` became `<bundle_dir>/state/` because a bundle directory holds one instance and a state root is per host by construction. |
| T3-03 | BLOCKER | A | S3.1.5, S6.2, S16.3, S17.3.1, S3.3, demo `examples/monorepo/` | Reach = the containing worktree (git top level), canonicalized; inherited policy flattened into `ciu.inherited.toml` with source digests at push; an inherited judge floor is permitted and unused without an assay lane; the fixture with a zero-instance root, an assay child with a sibling build context, and a command-only child. |
| T3-04 | BLOCKER | A | S14.3, S14.4.3, S14.4.7–S14.4.9, S14.7.1, S18.2 | The lock matrix; the realization-only lease class; lock-free lease records under `ciu-leases/`; `CIU_LEASE_FDS` so a CIU verb inside a lease reuses the inherited descriptor; non-blocking observers. |
| T3-05 | BLOCKER | A (narrow reopening; author's call) | S4.1.1, S4.1.2, S4.5.3, S14.8.2, S18 | Cold move; copies refused; `--fresh`; `adopt --owner` withdrawn; the "copied tree" claim withdrawn; labels never rewritten. The reviewer's alternative — an atomic ownership record outside both copyable trees plus recreation of every labelled resource — was not adopted: it is the registry the operator declined (§2.1) plus a volume migration, for a case (renaming a running deployment) that `down`, `clean`, move, `up` already covers. |
| T3-06 | BLOCKER | A | S3.8.6, S8.5.4, S17.5, S18.2, S18.4 | `probes` with a closed result, `invalid-receipt` and `no-manifest`, the four APIs, three variables; the document's enumerations become a tested output of the implementation. |
| T3-07 | MAJOR | A | S17.6.1, S6.2, S17.3.6 | The reference-level image map before any per-service decision; immutable release tags in archive mode; id comparison before create in none mode. |
| T3-08 | MAJOR | A′ | S16.9.4, S16.7.2, S16.5, S16.10 | Random 128-bit run ids with exclusive creation and a `run.json` manifest, as proposed. `ciu gate --resume` is withdrawn rather than hardened: assay's resume is content-keyed and self-contained, so a CIU resume would resume nothing. The progress path stays in the run directory — a deviation from the run-gate `.assay/progress-<lane>.jsonl` convention stated with its reason (no hidden directories, no shared files) rather than adopted; assay's `--resume` is always passed. |
| T3-09 | MAJOR | A | S8.5.2a, S8.5.4, S6.4 | Probes from the consumer's vantage (a probe container on the instance network with the same `extra_hosts`, a host-namespace helper, the consumer host); `probe = "none"` on the binding for a UDP host-network listener. |
| T3-10 | MAJOR | A | S16.6.1, S16.6.4, S16.5, S15.3, S13.3.2 | `memory_max` required for every admitted lane (reserving the whole remaining capacity for an undeclared lane was not adopted: an undeclared cap on a shared host is a declaration error, not a scheduling policy); every hard cap incl. `cpu.max` and each `io.max` tuple aborts on mismatch; a requested hard cap the host cannot enforce is refused before start. |

### 7.2 Round-2 audit rows
The incomplete rows (T2-03, T2-09) and the landed-and-broke rows (T2-01, T2-05, T2-06, T2-07, T2-08) resolve through 7.1. The reviewer's `flock -n /sys/fs/cgroup` check confirmed that locking the cgroup directory is possible on this host's cgroup2fs; S16.6.1 stands.

### 7.3 Still open
Items 1–6 of §5 and the additions of §6.5 stand; proposal §4.10 items 23–26 are new. Recommended next review: a targeted round 4 on S2.6 (the state root), S17.4 (the activation state machine and manifest), S14.4.9 (the lock matrix) and `examples/monorepo/`.
