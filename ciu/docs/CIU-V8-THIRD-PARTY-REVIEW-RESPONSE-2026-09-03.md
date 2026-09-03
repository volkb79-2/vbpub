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
