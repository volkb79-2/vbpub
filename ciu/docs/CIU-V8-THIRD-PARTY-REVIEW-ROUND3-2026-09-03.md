# CIU v8 third-party review, round 3 — draft.5 / rev 3.2

## 1 Verdict

Draft.5 closes the literal failures behind the acceptance partition, empty-contract acknowledgement, pre-secret source ordering, secret-lint exemption, CPU-weight inverse, and the original eight closed-surface mismatches. It is substantially closer than draft.4, but it is still **not implementable as written**. Of the ten round-2 findings, I count 3 as landed, 2 as landed but incomplete, none as not landed, and 5 as changes that introduced a contradictory rule.

Six new defects are blockers. A provider receipt cannot satisfy a consumer's host-specific release subject (S17.3.1, S17.4.3–S17.4.4); the first activation has no valid bootstrap path and target initialization mutates a verified release (S14.2.3, S17.3–S17.4); the prescribed monorepo tester and policy-inheritance pattern violates the child root and judge rules (S1.5.1, S3.1.5, S6.2, S16.3); the stack-directory lock is called canonical without being acquired by the CIU operations it is meant to exclude (S14.4.3, S14.4.7–S14.4.8); a normal checkout move has no coherent identity transition and Docker cannot perform the required in-place relabel (S4.1–S4.5); and draft.5 already introduces machine states, APIs, and environment inputs outside the single closed definition that S3.8.5 says guards them.

The owner-token decision needs a narrow reopening. The token is useful against an unrelated checkout that merely collides on the 24-bit id, but a copied checkout or restored backup copies the token too, and a live move cannot “re-stamp” immutable Docker labels. This does not by itself require abandoning checkout-local authoritative state; it does require withdrawing the claim that the current token proves an exclusive move and choosing a realizable live-move or refuse-live-move protocol.

The demo remains correct at the topology layer: all 38 TOML files parse, its five waves cover all 22 Realizations, its 24 socket claims do not collide, its three sample bindings and two shown gate rows agree with the revised derivation, and 11 build tables plus one service sharing the mock-target image is the intended count. Its receipt comment now demonstrates T3-01, and it has no fixture for inheritance, activation, lease interoperability, move, host-network probing, or resume safety.

## 2 Round-2 fix audit

### The ten round-2 findings

| id | claimed disposition | landed? | remark |
|---|---|---|---|
| T2-01 | A | landed-and-broke-S17.4.4 | The whole-file digest is gone, the body is stronger, and strict receipt handling is now the default. The replacement compares a provider's host-specific receipt with the consumer's different host and different per-host release digest; hand-started receipts still have no freshness nonce (T3-01). |
| T2-02 | A | landed | S8.5.5 is now an actual partition: non-one-shots are healthy, one-shots are completed, and neither is judged twice. S5.2/S5.3.6 makes `verify` or `unchecked = true` mandatory for a selected prepared variant with an empty contract. |
| T2-03 | A | landed-but-incomplete | The eight draft.4 mismatches are closed and S3.8.5 states the right generated-test obligation. Draft.5 immediately adds `unprobed`, `ciu/backup`, `XDG_RUNTIME_DIR`, and `XDG_STATE_HOME` without closing the corresponding result/API/environment surfaces (T3-06). |
| T2-04 | A′ | landed | The S6.10/S8.7/S12.2 phase/source matrix is implementable: a `pre_secrets` entry can consume only the four source classes CIU can materialize before step 3. |
| T2-05 | A | landed-and-broke-S14.2.3/S17.4.1 | Closure, image transport, candidate selection, and CIU-owned pointers now exist. The verified release contains the generated file that target bootstrap must modify; bootstrap cannot address a first candidate; rollback exposes its pointer before the rollback apply succeeds; and image ownership is still contradictory (T3-02, T3-07). |
| T2-06 | A | landed-and-broke-S8.5.2a/S8.5.4 | `listen` and address-set collision semantics landed. The live probe runs from CIU's namespace rather than the consumer's, and the mandated UDP `unprobed` result has no output field or vocabulary (T3-09, T3-06). |
| T2-07 | A′ | landed-and-broke-S14.4.7/S16.7.2/S16.9.4 | Slice-keyed admission, exec-target serialization, and per-run directories landed. Canonical stack locks do not exclude ordinary CIU mutators; run ids and resume targets are not safe identities; and the progress path contradicts both Assay's resume mechanism and the estate directive (T3-04, T3-08). |
| T2-08 | A | landed-and-broke-S4.1.2/S4.5.3 | A random owner token blocks an unrelated hash collision. It does not distinguish a move from a copy, the move's `instance_id` transition is unstated, and the required Docker-resource re-stamp is not an available operation (T3-05). |
| T2-09 | A | landed-but-incomplete | The ceiling inverse round-trips all 10,000 CPU weights, and finite swap with unlimited memory has a direct-write path. `cpu.max` remains WARN-only on mismatch, the hard `io.max` limits are not read back, and admission accepts a lane for which no `memory_max` reservation is defined (T3-10). |
| T2-10 | A | landed | S2.4.2 now compares every rendered file with every store value and suppresses only values requested by the matching service/template. The output accounts for those suppressions. |

Counts for this table: **landed 3; landed-but-incomplete 2; not-landed 0; landed-and-broke 5**.

### Response §6.2 audit rows

| response row | landed? | round-3 remark |
|---|---|---|
| T-06 | landed | S18 now shows `init`'s `--image`, `--service`, and `--test-argv` inputs. The response's correction that Appendix B was already valid is accepted. |
| T-08, T-19, T-32, T-33 | landed | The specifically stale proposal spellings are corrected: judge floor, lock order, recursive `[ciu] inherit`, and `api = "ciu/lane-result"`. The new inheritance and lease mechanisms have independent failures in T3-03/T3-04. |
| T-09 | landed | S17.3.2 materializes absent local `generate` and `ask` values on the sender before building the capsule. |
| T-10, T-26 | landed-and-broke-S8.5.2a | The wildcard/listener contradiction is closed; the replacement probe observes from the wrong namespace (T3-09). |
| T-11 | landed-but-incomplete | S14.8 now specifies contents, destination, modes, consistency intent, refusal, and restore selection. Its `ciu/backup` API and `XDG_STATE_HOME` input are absent from S18's closed surfaces (T3-06). |
| T-12 | landed | Stage 12 now compares required variables with the complete S16.4.5 available environment. |
| T-15 | landed | The acceptance partition and explicit empty-contract choice close this row. |
| T-16 | landed-and-broke-S17.4.4 | Receipt strictness and evidence content landed; cross-host validity does not (T3-01). |
| T-17, T-18 | landed-and-broke-S4.1.2/S4.5.3 | The token closes the fresh-collision case but not a normal move, copied tree, or immutable resource labels (T3-05). |
| T-21 | landed-and-broke-S14.4.7/S16.9.4 | The original capacity-key and shared-output defects are addressed; lock interoperability and run/resume identity are not (T3-04, T3-08). |
| T-22 | landed-but-incomplete | The two named conversion errors are fixed. Hard CPU and I/O enforcement and reservation completeness remain false (T3-10). |
| T-23 | landed | `env_allow` is in S6.10 and the pre-secret ordering is now explicit. |
| T-25, T-29 | landed-and-broke-S17.4.1/S17.6 | The new release and shared-image mechanisms exist, but their activation and exact-image rules are contradictory (T3-02, T3-07). |
| T-28 | landed | `secret_lint_allow` is in S3.4.7 and the file-wide exemption is gone. |
| T-31 | landed | `external-missing` and `external-down` are now in S16.8. |

## 3 New findings

### T3-01 — Cross-host receipts are compared with the wrong subject

- **Severity:** BLOCKER
- **Where:** S17.3.1, S17.4.3, S17.4.4, S17.5; `v8-dstdns-demo/examples/ciu.resolved.toml.example`
- **Claim:** A receipt produced by host A cannot satisfy host B under the stated validity rule, while the hand-started form still permits stale replay.
- **Evidence:** S17.3.1 builds a separate closure and manifest **per target host**, so hosts with different placements normally have different `release_digest` values. S17.4.3 also puts `host` in the canonical subject. S17.4.4 nevertheless requires a provider receipt's release digest to equal “the consumer's release digest” and then says the consumer “compares subjects.” Literal whole-subject equality fails because `host(A) != host(B)`; fieldwise equality as enumerated fails because `release_digest(A) != release_digest(B)` and does not check `host` at all. The demo repeats the defect: the `rs1002` subject contains `host = "rs1002"` and then says the next host accepts it when the subject equals “its own.” For hand-started `up`, both activation ids are absent, so equality supplies no freshness. Its `plan_digest` covers declarations and `ciu.stack.toml`, but not the compose/config templates, hooks, seeds, and declared hook inputs whose behavior produced the facts.
- **Failure scenario:** `gstammtisch` deploys its database-only release with digest `A`, proves `pg:role/controller`, and emits a receipt. `rs1002` deploys its application release with digest `B`. Strict S17.4.4 rejects the valid database receipt because `A != B`, so the supported serial activation stops. If an implementer instead ignores host-specific fields to make it pass, a receipt filed under the wrong provider host can be accepted. Separately, two hand-started runs with absent activation ids accept a receipt from before a hook change that removed the role, because the changed hook is outside `plan_digest`.
- **Proposed fix:** Make `activate apply` create one activation manifest with a non-null `activation_id` and an expected entry per host: `{host, release_digest, selection}`. A consumer validates a provider receipt against the **provider's** entry in that manifest, then checks that the receipt actually contains the required provider service/fact; it never compares the provider's host or digest with its own. Require a non-null shared activation id for every cross-host receipt. A hand-started multi-host `up` must be given an explicit common id plus a receipt directory, or refuse remote facts; absent must never compare equal to absent. If `plan_digest` remains, hash the complete declared runtime closure, not only declaration/stack TOML files.
- **Challenges a settled decision:** no. This keeps manifested releases, receipts, unsigned SSH transport, and strict-by-default handling.

### T3-02 — The activation state machine cannot perform a first deploy without invalidating its release

- **Severity:** BLOCKER
- **Where:** S14.2.3, S17.3.1, S17.3.3–S17.3.4, S17.4.1–S17.4.2; `v8-dstdns-demo/ciu.hosts.toml`
- **Claim:** Host-local initialization and pointer transitions are ordered so that the first activation has no valid input tree and a failed rollback can become `current`.
- **Evidence:** S17.3.1 includes `ciu.instance.generated.toml` among files hashed by the release manifest. S17.3.3 verifies those bytes and renames the staging directory into the content-addressed release. S14.2.3/S17.3.4 then require the target to regenerate `[ciu.host.generated]`, changing a manifested file after verification. The only named mechanism is `bootstrap`, but S17.4.1 runs bootstrap in `current`; a first push creates only `candidate`, explicitly leaving `current` untouched. `apply` runs in the candidate before the successful switch and does not first run bootstrap, so it sees the sender's host facts or no valid target facts. The demo comments say activation commands run in the candidate while the normative rule says bootstrap and health run in current. Finally, rollback swaps `current`/`previous` **before** running the old release's apply command, unlike forward apply, which switches only after success.
- **Failure scenario:** On a new `rs1002`, push verifies candidate `B`; there is no `current`, so bootstrap cannot run. Applying `B` either renders with the sender's hostname, uid, Docker gid, and paths, or edits `ciu.instance.generated.toml` and makes release `B` no longer match its manifest/digest. Later, rollback swaps to release `A`; `A`'s apply fails halfway, but `current` still advertises `A` even though the running deployment is mixed or remains `B`.
- **Proposed fix:** Keep releases immutable. Store host-local generated facts outside the release, for example under `<bundle_dir>/state/<instance>/<host>/`, and merge that explicit overlay when rendering a selected release. Define `prepare(candidate, host)` to create/validate that state before first apply; bootstrap must address `candidate` or an explicit `--release`, not only `current`. For both forward apply and rollback: prepare target → apply target → health/receipt → atomically update pointers. On any failure, leave pointers unchanged and report whether runtime compensation succeeded. The release manifest must hash only immutable inputs; mutable host state must be separately versioned and checked.
- **Challenges a settled decision:** no. This completes the selected manifest/current/previous design instead of replacing it.

### T3-03 — The prescribed monorepo inheritance pattern cannot pass its own root, build, release, and judge rules

- **Severity:** BLOCKER
- **Where:** S1.5.1–S1.5.2, S3.1.5, S5.4, S6.2, S16.2.1, S16.3, S16.11.1, S17.3.1, S17.4.3; proposal §4.3.14 and §4.10 item 22
- **Claim:** The explicit child-project pattern added in rev 3.2 is refused locally and has no reproducible remote layout.
- **Evidence:** S1.5 makes `/workspaces/vbpub/ciu` the root when its own `ciu.toml` is nearest. Proposal §4.10 item 22 then prescribes a child `tester/` stack with `build.context = "../../tester-unified"`; from `/workspaces/vbpub/ciu/tester`, that resolves to `/workspaces/vbpub/tester-unified`, outside the CIU checkout root and is therefore an S6.2 error. `[ciu] inherit = "../ciu.toml"` likewise reads a file outside the child root. S3.1.5 permits that read, but S17.3 gives no staging mapping that preserves `../ciu.toml`: from `/opt/ciu/releases/<digest>/ciu.toml`, it resolves to the shared `/opt/ciu/releases/ciu.toml`, outside the immutable release. S17.4.3's “repo-relative” plan entry is similarly undefined for a file outside the CIU root. Separately, the root is said to carry a shared judge floor, but S16.3 forbids `[testing.judge]` unless that effective project has an assay lane. A child with command-only lanes inherits a table it cannot delete under S3.1.2 and is rejected.
- **Failure scenario:** Add exactly the proposed root `ciu.toml`, child `ciu/ciu.toml`, and `ciu/tester/ciu.stack.toml` to vbpub. `ciu check` in the child rejects the shared build context for leaving its root. If that check is weakened, a pushed release cannot find `../ciu.toml` on the target without one mutable parent file shared by every release. A command-only sibling fails stage 12 merely because it inherited the estate's judge floor.
- **Proposed fix:** Keep nearest-root isolation and explicit inheritance, but define an explicit import closure. Imported policy files and build roots may be inside the containing Git worktree, are canonicalized at load time, receive manifest paths under an immutable release namespace, and never remain runtime `../` references. Alternatively, flatten inherited policy into a generated release input and record its source digest. Let an inherited judge floor be present but unused when the local project has no assay lane; require and enforce it only when an effective lane is `kind = "assay"`. Add one executable fixture with a zero-instance root, one assay child, one command-only child, and the sibling tester build context.
- **Challenges a settled decision:** no. This does not add walk-up discovery, a meta-root deploy set, `autostart`, or a shared stack `location`.

### T3-04 — The “canonical” stack lock does not serialize the CIU operations it names

- **Severity:** BLOCKER
- **Where:** S14.3, S14.4.3, S14.4.7–S14.4.8, S14.7.1, S16.5.3, S16.5.7
- **Claim:** Two conforming implementations can either bypass the realization lease or serialize the entire instance, and realization leases can race while updating their shared record.
- **Evidence:** S14.4.7 promises that a third party taking `flock <stack dir>` serializes against CIU. S16.5.3/S16.5.7 make gates acquire that key, but no rule says `up`, `down`, `clean`, `dev`, `render`, or hooks that mutate a Realization acquire its stack directory. S14.4.3 instead says “there is no … per-stack lock” after describing only “gate shared-resource locks.” S14.3 classifies `lease acquire --exclusive` as a mutating verb, normally implying the instance's exclusive root lock, while S14.4.8 says `--realization` takes the stack lock **instead of** the instance lock. If the class wins, a realization lease unnecessarily blocks the whole instance; if S14.4.8 wins, ordinary mutators can run through it. The latter also lets leases for two Realizations concurrently rewrite the one `ciu.instance.json` `leases[]` array; S14.4.8 names neither the root nor registry lock for that write.
- **Failure scenario:** run-gate holds the advertised stack-directory lock for `test_runner` while executing a test. Another process runs `ciu up` or `ciu clean`: it obtains the checkout-root lock, never contends on `test_runner`'s directory, and recreates/removes the container under the test. Or two `ciu lease acquire --realization` calls both read `leases[]`, append themselves, and atomically replace the JSON; the second write loses the first holder, so `lease status` falsely reports the resource free of one live user.
- **Proposed fix:** Add a verb/resource lock matrix. Every operation that reads or mutates live resources of Realization `R` must acquire `R`'s stack-directory lock; CIU operations acquire root/registry first and all needed stack keys in sorted order, while an external realization-only lease takes just the one published key. State explicitly that `--realization` is exempt from the general root-lock class. Serialize `ciu.instance.json` updates with the registry lock after the stack lock in the one global order, or store each lease as a separate atomic record whose filename is not a lock key. Make `status` and `wait` observers that do not first take the lock they are supposed to observe.
- **Challenges a settled decision:** no. It makes the selected canonical-key/lease answer true.

### T3-05 — The owner token neither defines a normal move nor proves that a checkout was moved rather than copied

- **Severity:** BLOCKER
- **Where:** S4.1.1–S4.1.2, S4.5.1–S4.5.3, S14.8.2; response §6.1 T2-08
- **Claim:** `--move` requires an in-place resource mutation Docker does not support, and the token being presented is not exclusive evidence of continuity.
- **Evidence:** A normal path change changes the S4.1.1 hash-derived `instance_id`, but S4.1.2 never says whether `--move` preserves the old id (violating path derivation) or adopts resources under the new id (whose `ciu.instance` labels no longer match). It only says to re-stamp `ciu.checkout` on every resource. Docker documents that object labels on images, containers, local daemons, volumes, and networks are static for the object's lifetime; changing them requires recreating the object ([Docker object labels](https://docs.docker.com/engine/manage-resources/labels/)). Named volumes cannot be recreated transparently merely to edit a label. Moreover, the token is stored in `ciu.instance.generated.toml`, included by a normal recursive copy and by S14.8 backup/restore. Both the original and the copy can therefore present it. S4.5.3's claim that the token defends a “copied tree” is false.
- **Failure scenario:** `cp -a /work/a /work/b` copies the generated file while A's database volume and containers remain live. B runs the prescribed `instance init --move`; it has the same token, so the stated proof accepts it even though A still exists. B cannot perform the required relabel in place; if it merely updates its file, both trees can pass owner checks and issue destructive commands against the same resources. A backup restored at C creates the same ambiguity.
- **Proposed fix:** First choose move semantics. The safe minimal rule is: `--move` is allowed only when no live resource carries the old owner; it derives the new identity and writes new state, while a live move is refused with a recovery procedure. If live adoption is required, it needs an atomic transfer: lock/prove the old and new checkout, compare-and-swap an ownership record outside the two copyable trees, mint a new token, and define recreation/migration for every statically labelled container, network, and volume. Merely rotating the checkout file is insufficient because the resource labels cannot follow it. Remove “copied tree” from the guarantee until that protocol exists.
- **Challenges a settled decision:** **yes, narrowly.** The 128-bit token is still useful for collision detection, but the claim that it proves a live move within purely copied checkout state must be reopened. This finding does not by itself reject checkout-local secrets/configuration or path-derived display identity.

### T3-06 — The generated closed-surface guard already fails on draft.5's own additions

- **Severity:** BLOCKER
- **Where:** S3.8.4–S3.8.5, S8.5.2a–S8.5.4, S14.8.1, S16.6.1, S18.2, S18.4
- **Claim:** The specification promises one generated definition for every public state and input while simultaneously requiring values absent from that definition.
- **Evidence:** S8.5.2a requires UDP host-network probes to be recorded as `unprobed`, but S8.5.4 offers only `healthy`, `completed`, `facts`, and `assumed`, with no probe-result field or `unprobed` vocabulary. Under `--allow-assumed`, an invalid receipt that contains the requested fact is neither S8.5.4 reason `no-receipt` nor `not-in-receipt`. S14.8.1 introduces the machine artifact `api = "ciu/backup"`, but S18.4's API list omits it even though unknown APIs must be refused. S14.8.1 reads `XDG_STATE_HOME` and S16.6.1 reads `XDG_RUNTIME_DIR`, while S18.2 omits both and concludes, “No other variable influences behavior.” These are exactly the cross-surface failures S3.8.5 says the generated conformance test makes impossible.
- **Failure scenario:** Generate a reader's accepted API vocabulary from S18.4, then ask it to restore a conforming S14.8 backup: it refuses `ciu/backup` as unknown. For a UDP host-network dependency, one implementation drops the required `unprobed` observation because the gate schema has no slot, while another invents `probes = [...]`; both can claim to implement different halves of the spec. Two invocations with different `XDG_RUNTIME_DIR` values use different admission ledgers despite S18.2 telling an operator that the variable cannot affect behavior.
- **Proposed fix:** Put all artifacts, environment inputs, flags, fields, states, and reasons in the single source S3.8.4 promises and generate every enumerating paragraph from it. Add `ciu/backup`; add `XDG_STATE_HOME` and `XDG_RUNTIME_DIR`; define a gate `probes` row with a closed result such as `passed | unprobed` and protocol; add an `invalid-receipt` assumption reason or normalize invalid receipts explicitly to `no-receipt`. The S3.8.5 test must instantiate the source definition itself and compare the generated S8/S18 documentation, not rely on authors to update both.
- **Challenges a settled decision:** no.

### T3-07 — Image ownership and activation still permit the wrong bytes under the right tag

- **Severity:** MAJOR
- **Where:** S3.4.3, S6.2, S17.3.3, S17.3.6, S17.6
- **Claim:** The shared-build classification gives opposite answers per service, and archive/none activation does not bind Compose to the verified image id.
- **Evidence:** S3.4.3 and the S6.2 `build` row say a reference is project-built when exactly one service sharing it declares `build`. The S6.2 `image` row says “without `build` the image is pulled (vendor-owned),” and S17.6 says “every other image is pulled,” which classifies the non-owner service the other way. In archive mode, staging verifies the loaded image id but renders Compose with the mutable tag. In `--images none` mode, apply refuses only when the image is **missing**, not when the tag exists and points at a different id. Docker distinguishes mutable named references from content-addressed digest pulls ([image tags](https://docs.docker.com/reference/cli/docker/image/tag/), [pulling by digest](https://docs.docker.com/reference/cli/docker/image/pull/)); the staging-time id check does not freeze a tag until apply.
- **Failure scenario:** The demo builds `dstdns/mock-targets:latest` from `mock_targets.dns`; a literal S6.2/S17.6 implementation treats `mock_targets.http` as vendor-owned and pulls the same tag, replacing the local build. On a remote host, archive staging verifies id `A`; another build retags `dstdns/mock-targets:latest` to id `B` before apply, and Compose starts `B`. With `--images none`, a pre-existing tag to `B` is “present,” so the only stated refusal does not fire.
- **Proposed fix:** Build one normalized reference-level image map before processing services: `{reference, ownership, build_owner, expected_image_id, repository_digest?}`. Every service sharing the reference reads that row; remove the per-service “without build = vendor” wording. Registry mode renders the repository digest. Archive mode assigns and renders a release-unique immutable local reference (or the exact image id) after load. `--images none` must inspect and compare the exact expected id/digest immediately before create, not merely test presence.
- **Challenges a settled decision:** no.

### T3-08 — Run ids and resume directories are neither unique, contained, nor bound to the run being resumed

- **Severity:** MAJOR
- **Where:** S16.5, S16.7.2, S16.9.4, S16.10; repository `AGENTS.md` “Every assay lane resumes and reports progress”; Assay `docs/CONSUMERS.md` “Resume and shard a long mutation lane”
- **Claim:** A user-controlled resume id can select an unrelated path or stale execution, and the stated reason for reusing the progress file is not Assay's resume contract.
- **Evidence:** S16.9.4 defines `run_id` only as RFC-3339-basic start time plus pid, with no exclusive-create rule; duplicate execution of one lane in one process can generate the same id if the timestamp resolution collides. `--resume [<run_id>]` has no grammar, directory containment check, ownership manifest, or equality check for lane definition, commit, request base, environment image, argv, or judge. S16.5 still says artifacts are copied into `evidence_dir/<lane>/`, contradicting S16.9.4's per-run path. More fundamentally, S16.7.2 says reuse lets “assay find its own progress,” but Assay persists resume records under `.assay/mutation-state/`, keyed by candidate content; `--progress` is an append-only telemetry destination, not resume input. The repo-wide operator directive additionally requires `--progress .assay/progress-<lane>.jsonl`, whereas S16.7.2 says progress lives in the run directory and “never” in `.assay`.
- **Failure scenario:** A sequence names the same fast lane twice in one process and both invocations resolve the same second/pid id; the second overwrites or appends to the first run's logs and verdict despite the “nothing … could share” guarantee. An operator supplies `--resume ../../other-lane/runs/x`; a straightforward join escapes the intended lane. Even with containment, resuming after `HEAD`, the environment image, or assay lane changed mixes old progress/artifacts with a new verdict while the LaneResult presents one run identity.
- **Proposed fix:** Generate a 128-bit random run id and create its directory with exclusive no-follow semantics. Accept resume ids only in the generated grammar, open them relative to the lane's `runs` directory by directory descriptor, reject symlinks/escape, and require a run manifest matching `{project, instance, lane, lane-definition digest, HEAD, request base, environment image/id, argv or assay lane, judge version}`. Update S16.5's artifact path. Keep Assay's always-on `--resume`, but follow the estate progress path directive; define CIU `--resume` as reuse of CIU evidence/container state, not as what enables Assay candidate resume.
- **Challenges a settled decision:** no.

### T3-09 — A host-network probe observes CIU's namespace, not the consumer's route

- **Severity:** MAJOR
- **Where:** S7.8 step 4a, S7.8.7, S8.5.2a, S8.5.5
- **Claim:** The live probe can reject a reachable endpoint or approve a path the actual consumer cannot use.
- **Evidence:** A bridge consumer resolves a same-host host-network provider to the injected name `ciu-host`; that alias exists in the consumer's Compose block, not in the native CIU process or necessarily in CIU's devcontainer. A host-network consumer resolves it to `127.0.0.1`, which is the host only from the host network namespace; from a devcontainer it is the devcontainer. S8.5.2a nevertheless connects to “the resolved address from CIU's vantage point.” S7.8.7 defines special behavior for CIU's own bindings but does not make CIU share every consumer's namespace. UDP is not probed at all yet acceptance contains no explicit unchecked-listener choice.
- **Failure scenario:** Controller is a bridge container and a host-network provider listens on the host wildcard. Controller can reach `ciu-host:PORT`, but native CIU cannot resolve `ciu-host`, so it times out and blocks a healthy wave. Conversely, CIU running natively can reach host loopback while a bridge consumer cannot; a careless implementation probes `127.0.0.1`, passes, and starts a consumer whose actual `ciu-host` route fails. In a devcontainer, the loopback cases reverse again.
- **Proposed fix:** Probe from a namespace with the same route as the consumer. Before its wave, use a short-lived probe container attached to the instance network with the same `host-gateway` alias for a bridge consumer; use a host-namespace helper for a host-network consumer; use the actual cross-host address for a remote consumer. Record `{consumer_vantage, endpoint, protocol, address, result}`. For UDP, require a declared application probe or an explicit `unchecked = true`-style acknowledgement; do not silently equate “unprobed” with accepted reachability.
- **Challenges a settled decision:** no.

### T3-10 — Resource admission and read-back still permit an unreserved or unapplied hard cap

- **Severity:** MAJOR
- **Where:** S13.1–S13.3.2, S13.4, S16.5, S16.6.1–S16.6.2; Appendix D §D.2 T2-09
- **Claim:** The design can admit unbounded work as zero demand and continue after hard CPU or I/O limits were not applied.
- **Evidence:** A lane's `resources` is an optional subset of `RK`, so `memory_max` may be absent. S16.6.1 nevertheless sums “the `memory_max` of every live reservation” without defining the reservation value for such a lane, while S16.6.2 starts the host/ephemeral process with only declared caps. Treating absence as zero admits an uncapped process; no other derivation exists. For deployment governance, S13.3.2 makes read-back mismatch an ERROR only for `memory.max`, `memory.swap.max`, and `pids.max`; the hard CPU quota `cpu.max` is WARN-only. The declared `io_read_iops_max`, `io_write_iops_max`, `io_read_bps_max`, and `io_write_bps_max` map to `blkio_config`, but S13.3.2 does not read back `io.max` at all. Appendix D's claim that an enforceable-cap mismatch aborts is therefore wider than the rule.
- **Failure scenario:** A host lane omits `memory_max`, reserves no defined bytes, and consumes nearly the whole 4 GiB slice while a declared 3 GiB lane is admitted beside it; the kernel kills one despite a successful headroom transaction. Separately, a daemon ignores an injected `cpu_max = "100000 100000"` or I/O cap; CIU emits at most WARN (or no comparison), reports a successful deployment, and leaves a workload unbounded on the mixed-use host.
- **Proposed fix:** Require `memory_max` for every admitted `host` and `ephemeral` lane when the slice is finite. If omission is intentionally supported, reserve the entire remaining capacity and serialize it; never invent zero. Read back every hard cap, including `cpu.max` and each normalized `io.max` tuple, and abort on mismatch. Keep WARN only for weights/protections whose exact realization is advisory, and make `unsupported` an explicit pre-start refusal whenever the consumer requested a hard cap.
- **Challenges a settled decision:** no.

## 4 Demo re-verification

### Mechanical checks

- All **38** checked-in `*.toml` files under `v8-dstdns-demo/` parse with Python's TOML 1.0 parser; there were zero parse failures.
- There are **11** `build` tables. `infra/mock-targets/ciu.stack.toml` declares the one build owner for `dstdns/mock-targets:latest` on `dns`; `http` names the same image without a second build. No compose template has an active `build:` key.
- Active declarations contain no `published_on`, no `realized_by = "tailscale_node"`, no `bundle_dir = "/opt/ciu/current"`, and no host `rollback` key. The only search hits are retrospective README prose. All three hosts use `bundle_dir = "/opt/ciu"` and expose only `bootstrap`, `apply`, and `health` commands.
- The current demo adds no host-network endpoint, so S6.3's new `listen` key and T3-09 are not exercised.

### Waves

No graph-affecting demo declaration changed in draft.5. Reapplying S8.4.1 still yields all 22 Realizations exactly once:

```text
0: cadvisor, github_runner, github_runner_webhook, otel_aggregator,
   otel_collector_node, registry_lightweight, tailscale_node, vault,
   webapp_ui_react, webhook_listener
1: consul_server, db_core, redis_core, reverse_proxy, skywalking
2: authentik, db_init, docker_stats_exporter
3: controller, webapp_server, worker_db
4: worker_io
```

### Three sample bindings

| binding | `prod3` result | `local` result | verdict |
|---|---|---|---|
| `controller.controller.database -> main_db.sql` | mesh `100.64.0.11:5432`, env `POSTGRES_HOST/PORT`, requires `tailscale_node` | instance network, `dstdns-98535c-db-core-postgres:5432`, no derived requirement | agrees with S7.8 and the example |
| `reverse_proxy.nginx.controller -> controller.http` | mesh `http://100.64.0.12:8083`, separate `/api/controller`, requires `tailscale_node` | instance `http://dstdns-98535c-controller:8080`, same separate path | agrees with S7.8 and the example |
| pseudo-consumer `ciu.vault -> vault.api` from rs1002 | mesh `http://100.64.0.11:8200`, requires `tailscale_node` | instance `http://dstdns-98535c-vault:8200` | agrees with S7.8.7 and the example |

### Socket claims and gates

The 24 deduplicated claims remain collision-free under S6.3.2:

| host | bind | TCP host ports |
|---|---|---|
| gstammtisch | `100.64.0.11` | 5432, 6379, 8200, 8500, 9000, 9010 |
| rs1002 | `0.0.0.0` | 4317, 4318, 4319, 4320, 8080, 8888, 8889, 9558, 13133, 13134 |
| rs1002 | `100.64.0.12` | 8081, 8082, 8083, 11800 |
| tsstammtisch | `0.0.0.0` | 443, 5443, 9000, 9001 |

No wildcard/specific-address pair on rs1002 shares a port. Authentik remains on mesh port 9010 while MinIO remains on 9000.

`[resolved.gates.1]` is correctly empty on rs1002. `[resolved.gates.3]` correctly has `healthy = ["controller.controller"]`, no completion, and the minter fact `vault:secret/internal/internal_dlq_token@controller.controller`: draft.5 S8.5.2 now explicitly makes secret-minter and PKI facts gate facts. `assumed = []` is the strict-path result.

The tester is still capped at 4 GiB, equal to the largest exec-lane request. The clean environment supplies `SCHEMA_GATE_PG_DUMP` through `forward_env` and `POSTGRES_HOST/PORT` through its binding, so the corrected S16.4.5/stage-12 comparison accepts it.

### Round-3 discrepancies

- The receipt example includes `host = "rs1002"` and a host release digest, then says the next host compares that subject with “its own.” That is T3-01, not a harmless abbreviated shape.
- `ciu.hosts.toml` comments that activation commands run in the candidate, while S17.4.1 runs bootstrap and health in `current`; the first-deploy consequence is T3-02.
- The demo does not instantiate the monorepo inheritance pattern, a release manifest/image archive, `ciu lease`, `--move`, a host-network endpoint, or `ciu gate --resume`. It therefore neither confirms nor refutes T3-03 through T3-10.

## 5 Not verified

- There is still no CIU v8 implementation, so I could not execute `ciu check --graph`, generate the S3.8.5 conformance suite, push an actual release, activate/rollback over SSH, or inspect a produced receipt/LaneResult. The demo checks above parse and derive the checked-in design inputs; they are not an executable-v8 certification. Proposal §4.10 item 17 already records that limitation, so it is not a new finding.
- I did not run a registry/archive transfer or a live Docker move. T3-05 relies on Docker's documented static-label contract, and T3-07 on its documented distinction between tag references and content digests. No claim is made about an untested registry product.
- I did test `flock -n /sys/fs/cgroup true` in this environment and read its exit status directly: it returned 0 on `cgroup2fs`. I therefore do **not** claim that locking a cgroup directory is inherently unsupported. T3-04 concerns which processes acquire the key; T3-10 concerns the reservation value.
- I did not repeat proposal §4.10's known gaps, including intentional exec non-parallelism, per-uid admission, byte-identical hand-start plan agreement, whole-tar image archives, and the fact that monorepo consumer files have not landed. T3-01/T3-03 are different: the supported release flow compares different per-host digests, and the prescribed future monorepo files are rejected by the normative rules even after they land.
- **ASSUMPTION (Docker backend):** CIU's Docker/Compose target obeys the Docker Engine label and image-reference contracts cited above. A non-Docker adapter would need its own resource-mutation and immutable-image rules.
- **ASSUMPTION (demo graph):** `prod3` uses the checked-in live selections, site layer, and host-port overrides shown by the example; HTTP/HTTPS claims are TCP. This is the same explicit input set used in round 2.

## 6 Machine summary

```json
{
  "findings": [
    {
      "id": "T3-01",
      "severity": "BLOCKER",
      "where": ["S17.3.1", "S17.4.3", "S17.4.4", "S17.5"],
      "claim": "Cross-host receipts are compared with the consumer's different host-specific release subject, while hand-started receipts have no freshness nonce.",
      "regression": true,
      "challenges_settled": false
    },
    {
      "id": "T3-02",
      "severity": "BLOCKER",
      "where": ["S14.2.3", "S17.3.1", "S17.3.3", "S17.3.4", "S17.4.1", "S17.4.2"],
      "claim": "The first activation cannot initialize target-local facts without modifying its verified release, and rollback exposes the pointer before apply succeeds.",
      "regression": true,
      "challenges_settled": false
    },
    {
      "id": "T3-03",
      "severity": "BLOCKER",
      "where": ["S1.5.1", "S3.1.5", "S6.2", "S16.2.1", "S16.3", "S17.3.1"],
      "claim": "The prescribed monorepo inheritance and shared-build pattern is outside the child root, lacks a release mapping, and can inherit a forbidden judge table.",
      "regression": true,
      "challenges_settled": false
    },
    {
      "id": "T3-04",
      "severity": "BLOCKER",
      "where": ["S14.3", "S14.4.3", "S14.4.7", "S14.4.8", "S14.7.1", "S16.5.3", "S16.5.7"],
      "claim": "The canonical stack lock is not required of ordinary CIU resource mutators and concurrent realization leases race on one instance record.",
      "regression": true,
      "challenges_settled": false
    },
    {
      "id": "T3-05",
      "severity": "BLOCKER",
      "where": ["S4.1.1", "S4.1.2", "S4.5.1", "S4.5.3", "S14.8.2"],
      "claim": "The owner token defines neither the instance-id transition nor a realizable resource relabel and is copied with the tree it is supposed to distinguish.",
      "regression": true,
      "challenges_settled": true
    },
    {
      "id": "T3-06",
      "severity": "BLOCKER",
      "where": ["S3.8.4", "S3.8.5", "S8.5.2a", "S8.5.4", "S14.8.1", "S16.6.1", "S18.2", "S18.4"],
      "claim": "Draft.5 adds required states, APIs, and environment inputs outside the single closed definition its conformance rule promises.",
      "regression": true,
      "challenges_settled": false
    },
    {
      "id": "T3-07",
      "severity": "MAJOR",
      "where": ["S3.4.3", "S6.2", "S17.3.3", "S17.3.6", "S17.6"],
      "claim": "Shared-image ownership is contradictory and archive or none activation can run a different image id under the verified tag.",
      "regression": true,
      "challenges_settled": false
    },
    {
      "id": "T3-08",
      "severity": "MAJOR",
      "where": ["S16.5", "S16.7.2", "S16.9.4", "S16.10"],
      "claim": "Run ids and resume directories are neither collision-proof, path-contained, nor bound to the execution being resumed.",
      "regression": true,
      "challenges_settled": false
    },
    {
      "id": "T3-09",
      "severity": "MAJOR",
      "where": ["S7.8", "S7.8.7", "S8.5.2a", "S8.5.5"],
      "claim": "Host-network reachability is probed from CIU's namespace rather than the consumer's route and UDP can pass unproved.",
      "regression": true,
      "challenges_settled": false
    },
    {
      "id": "T3-10",
      "severity": "MAJOR",
      "where": ["S13.1", "S13.3.2", "S13.4", "S16.5", "S16.6.1", "S16.6.2"],
      "claim": "Admission has no reservation for a lane without memory_max, while unapplied hard CPU and I/O limits do not abort.",
      "regression": true,
      "challenges_settled": false
    }
  ],
  "dispositions": {
    "landed": 3,
    "incomplete": 2,
    "not_landed": 0,
    "broke": 5
  }
}
```
