# CIU v8 third-party review, round 2 — draft.4 / rev 3.1

## 1 Verdict

Draft.4 is materially better than draft.3, but it is still **not implementable as written**. Of the 35 dispositions, I count 14 as landed, 13 as landed but incomplete, none as wholly absent, and 8 as changes that introduced or exposed a contradictory rule. Four defects are blockers: the receipt validity test cannot succeed reproducibly and does not bind sufficient evidence (S3.7.1, S14.2, S17.4.4); acceptance simultaneously requires a one-shot to be running and exited (S8.5.5, S8.6.3); several new keys, flags, states, and validation rules are outside their own closed vocabularies (S3.4.7, S6.10, S8.5.4, S16.8, S18); and `pre_secrets` hooks may request values that the pipeline materializes only after the hook runs (S8.7, S12.2).

The release (S17.3–S17.4), socket (S6.3.2/S7.4.7), gate-admission (S16.6), ownership (S4.5.3), governance (S13.3), and secret-lint (S2.4.2) fixes also need correction before implementation. The operator's two decisions do not need to be reopened wholesale: manifested releases and receipts remain the right direction, and checkout-local state can retain path-derived display identity. The required changes are to give releases a computable closure and image transport, receipts a portable and fresh subject, and moved-checkout adoption an ownership token that a hash-colliding checkout cannot forge merely by passing `--move`.

The revised demo's core derivation is now sound: the `prod3` closure has all 22 Realizations in the stated five waves (S8.4.1), the three sample resolutions agree with S7.8, and its 24 current socket claims do not collide under S6.3.2. The demo still exposes the shared-image build-ownership defect (S3.4.3/S6.2) and one unsupported gate fact row (S8.5.2).

## 2 Disposition audit

| id | claimed disposition | landed? | remark |
|---|---|---|---|
| T-01 | A | landed | S4.3.1 now checks each actual namespace and permits one `compose_project` shared by the services and replicas of one Realization. |
| T-02 | A | landed | S1.4 defines `dns_name`, and S7.2/S7.3 apply it to FQDNs. |
| T-03 | A | landed | S6.10 closes each hook entry over `run`, `service`, `provides`, and `secrets`, and validates `service`. The separate root-level `env_allow` omission belongs to T-23/T2-03. |
| T-04 | P | landed | S7.3 makes `realized_by` a LogicalService, S7.6.5 resolves endpoint-less `per_host` requirements on the consumer host, and S5.3.5 now refuses facts as well as data delivery to a mock. The rejection of `optional` is justified: an ordering-only mock binding has no unresolved data or fact. |
| T-05 | A | landed | S8.5.1 preserves `Running` for `started` and takes the strongest predicate when another edge requires more. |
| T-06 | A | landed-but-incomplete | S19 fixes the invalid zero-instance scaffold, and the checked-in minimal files parse. S18's CLI synopsis still omits `--image`, `--service`, and the test-command input, while Appendix B repeats `location = "web"`, making its normative TOML snippet invalid. |
| T-07 | A | landed | S16.9 defines `status` from `outcome` and carries the `ciu/lane-result` header required by S18.4. |
| T-08 | A′ | landed-but-incomplete | S16.3 and S16.7.2 correctly require assay 4.1.0 and a path after `--progress`; the capability-record deferral is reasonable. Proposal §4.1.10 still shows `version = ">=2.4"`, which the normative rule refuses. |
| T-09 | A′ | landed-but-incomplete | S17.3.2 distinguishes transported values and avoids refreshing them, so SOPS/Vault Agent need not be mandatory. A first push does not say how an absent local `generate` or `ask` value becomes the “stored value” put in the capsule; the remote-only services in the demo exercise that case. |
| T-10 | A′ | landed-but-incomplete | S7.4.7 removes illegal port injection and S7.8 step 4a supplies a host address. It still declares no listener address and proves no listener, so a loopback-only host-network process remains unreachable through `ciu-host` or a remote host address (T2-06). |
| T-11 | O → P | landed-but-incomplete | S2.3.4 now states the destructive posture honestly, which fixes the false recovery promise without reopening the operator's decision. The new backup contract is only a verb name: its default destination, manifest, modes, consistency boundary, overwrite/refusal behavior, and restore selection are unspecified. |
| T-12 | A | landed-and-broke-S15.3 | S16.4.5 and the demo correctly count binding variables and exec-container variables, but stage 12 still requires `env_required` to be a subset of `forward_env` alone; it rejects the demo's binding-provided `POSTGRES_HOST` and `POSTGRES_PORT` (T2-03). |
| T-13 | A | landed | S7.8 refuses an unreachable loopback host publication, S7.4.4 labels `allow_from` as declared data, the site override is gone, and Authentik now claims 9010 rather than MinIO's 9000. |
| T-14 | A | landed | S8.4.1 is deterministic and reproduces the five-wave, 22-Realization `prod3` list. The gate-fact discrepancy is in S8.5, not the wave algorithm. |
| T-15 | A′ | landed-and-broke-S8.5.5 | Selected leaves are now inspected, but S8.5.5 literally requires every service to be healthy and every one-shot to have exited, which is impossible for a completed one-shot. Also, a WARN plus optional `verify` is not equivalent to requiring an explicit acceptance choice for an empty-contract seeded/simulated variant (T2-02). |
| T-16 | O → A′ | landed-and-broke-S17.4.4 | Receipts are the right mechanism, and unsigned transport over the already authenticated SSH channel is a defensible boundary. The full resolved-file digest is non-reproducible across hosts and renders, release/selection binding is incomplete, stale receipts are replayable, and missing proof still passes by default (T2-01). |
| T-17 | P | landed-but-incomplete | S4.3.1 and the new labels cover the omitted namespaces and normal ownership checks. The claim that a 24-bit collision is always refused is defeated by S4.5.3's unauthenticated `--move` escape (T2-08). |
| T-18 | P | landed-and-broke-S4.5.3 | The sentinel proves daemon path visibility, not continuity of ownership. `--move` uses no fact that distinguishes a moved checkout from a different checkout with the same 24-bit hash, so it bypasses the collision refusal (T2-08). |
| T-19 | A′ | landed-but-incomplete | S14.4.3's all-instance ascending order, eager acquisition, acyclic joins, and directory locks are sufficient on supported filesystems; SQLite is not required. Proposal §4.1.9 still says `instance → registry → joined reference`, the old deadlocking order. |
| T-20 | A | landed | S16.4/S16.12 now state detached create/start/wait/collect/remove behavior, a writable private Git config, and the dual mount by behavior rather than citation. |
| T-21 | A | landed-but-incomplete | Reservations and final-result serialization exist, and exec caps are honestly reported as requested. The reservation namespace does not match exec-container capacity, and per-run logs, verdicts, progress, and artifacts still share one lane path (T2-07). |
| T-22 | A | landed-but-incomplete | Requested/applied/unsupported and read-back are useful, so direct cgroup ownership need not be adopted yet. The stated inverse rounds in the wrong direction for roughly half the weights, and finite swap with unlimited memory is silently converted to unlimited swap (T2-09). |
| T-23 | A′ | landed-and-broke-S6.10 | The clean environment, opt-in validation, per-entry secrets, and sequential state visibility are good; moving validation to `--live` is unnecessary. S12.1 adds `[hooks].env_allow` while S6.10's closed root omits it, and the new secret list is impossible in `pre_secrets` for sources materialized in step 3 (T2-03/T2-04). |
| T-24 | A | landed | A directory bind plus same-directory temp/rename/fsync gives readers old-or-new complete files and preserves `/run/secrets/<key>`. |
| T-25 | O → A | landed-but-incomplete | Staging, verification, and `current`/`previous` are present, but the closure cannot be calculated for arbitrary hooks, image bytes are not delivered, no candidate digest is selected by the activation interface, and the demo still declares `rollback = "ciu down"` (T2-05). |
| T-26 | A | landed-and-broke-S6.3.2 | Structured claims fix address-specific collisions in the demo, but S7.4.7 emits `bind = "*"` while S6.3.2 recognizes only `0.0.0.0` as a wildcard; the claimed overlap check therefore misses the new claim form (T2-06). |
| T-27 | A | landed | S11.2 rewrites the three embedded service-reference fields, refuses unknown/replicated ambiguity, and S11.3 excludes the non-rewritable legacy mechanisms. |
| T-28 | A | landed-and-broke-S3.4.7 | The output is now honestly a lint and exact store matches remain errors. Its new `[ciu].secret_lint_allow` key is absent from S3.4.7's closed set, and S2.4.2 exempts an entire config file rather than only its deliberately delivered secret values (T2-03/T2-10). |
| T-29 | A | landed-but-incomplete | Service-local `build` removes name inference, but it cannot represent two services consuming one project-built image: the demo declares a build only on `mock_targets.dns`, so S3.4.3 classifies `mock_targets.http` as pulled. Remote delivery of built images is also absent (T2-05). |
| T-30 | A | landed | S6.9.2 makes mount overlap per-service, states the image-symlink consequence, and S6.8.5 gives seeding a no-overwrite transaction. |
| T-31 | A | landed-and-broke-S16.8 | S16.2.2 supplies the missing zero-instance external binding, but its `external-missing` and `external-down` outcomes are absent from S16.8's closed NOT_RUN reason vocabulary (T2-03). |
| T-32 | A | landed-but-incomplete | S16.2.1 itself is recursive, path-relative to the declaring file, and cycle checked. Proposal §4.1.10 and §4.10 item 10 still say “one level”/“forbidden,” so the design set gives both answers. |
| T-33 | A | landed-but-incomplete | The normative artifacts now have per-API headers and a compatibility rule. Proposal §4.1.10 still specifies `schema_version: 2`, §4.8 says that spelling was dropped, and several other proposal rows still call it the common API. |
| T-34 | A | landed | S7.5 defines recursive includes once, publications are structured, and no active rule refers to nonexistent S8.6.4 (the only remaining text is Appendix D's historical statement). |
| T-35 | P | landed | S3.7.6 supplies narrow queries and S3.7.1 adds a capability-first index. Keeping the approved entity names does not invalidate that fix. |

## 3 New findings

### T2-01 — A receipt has neither a reproducible subject nor sufficient freshness

- **Severity:** BLOCKER
- **Where:** S3.7.1, S3.7.3, S14.2, S17.4.3, S17.4.4, S17.5
- **Claim:** S17.4.4's digest comparison normally cannot match, and weakening it enough to match would accept receipts that are from the wrong release or runtime incarnation.
- **Evidence:** S17.4.4 compares the provider's SHA-256 of all `ciu.resolved.toml` with “the consumer's own render of the provider host.” The file includes `rendered_at` (S3.7.1) and `[ciu.host.generated]` values such as hostname, uid/gid, Docker gid, environment type, repo root, and physical root (S14.2); the consumer neither knows all those provider-local facts nor can reproduce the timestamp. This also contradicts S3.7.3's claim that the file is regenerated identically. The validity predicate names instance, layout, and full-file digest, but not an explicit equality check for the receipt's optional release digest or full selection, despite the following sentence promising both. It carries no activation nonce or container incarnation. Finally, S17.5 lets absent proof become `assumed` plus WARN by default, so the original false-fact scenario still deploys unless the operator remembered `--require-receipts`.
- **Failure scenario:** `gstammtisch` produces a valid receipt. `rs1002` renders that host a second later and necessarily gets a different `rendered_at`; if it substitutes its own generated facts, those differ too, so correct evidence is rejected. An implementer who omits those volatile fields can instead accept an old receipt after PostgreSQL was reset and restarted from the same declarations: TCP is reachable, the old receipt still lists `pg:role/controller`, and the consumer starts although the role is gone.
- **Proposed fix:** Do not hash `ciu.resolved.toml`. Define a canonical `receipt_subject` and hash canonical JSON containing `{provider_release_digest, instance_id, layout, provider_host, resolved.services}` plus, if desired, a portable deployment-plan digest derived only from manifested declarations, inventory, layout, and selection. Exclude `rendered_at`, all `[ciu.host.generated]` values, read-backs, and runtime observations. `activate` must mint a per-activation nonce, pass it to each host, and require the receipt to echo it. A receipt must contain that subject, explicit per-service state with image/container incarnation, per-one-shot exit evidence, and per-fact `{fact, provider_service, probe_result, observed_at}` records, plus publications and assumptions. The consumer compares the subject with the activation plan and trusts only explicit proved entries received over the authenticated channel; it does not pretend to reproduce provider-local runtime data. Missing proof for a required fact/completion must refuse by default, with an explicit `--allow-assumed` escape rather than the reverse.
- **Challenges a settled decision:** no; this completes the adopted receipt design and does not require signing while SSH remains the transport.

### T2-02 — Acceptance makes every successful one-shot fail and still leaves empty prepared variants opt-in

- **Severity:** BLOCKER
- **Where:** S5.3.6, S8.5.1, S8.5.5, S8.6.3
- **Claim:** The final acceptance predicate is internally unsatisfiable for one-shots, while its `verify` alternative does not force an acceptance contract for an unconsumed seeded/simulated capability.
- **Evidence:** S8.5.5 requires “every service ... is healthy per S8.6.3 at the end” **and** “every `one_shot` service completed with exit 0.” S8.6.3 defines ordinary no-healthcheck health as `Running`; a completed one-shot has exited and is not running. S8.5.1 says completed substitutes for healthy in a gate, but S8.5.5 does not make that partition. Separately, S5.3.6 emits only a WARN for an empty prepared/simulated contract and makes `verify` optional; S15.2 says WARNs do not abort a mutating verb.
- **Failure scenario:** In the demo, `vault.init`, `db_core.postgres_init`, and `db_init.runner` exit 0. The literal first conjunct marks each unhealthy, so no conforming `ciu up` can succeed. If an implementation silently interprets completed as healthy, a selected seeded leaf with a green process healthcheck, empty contract, and no `verify` still passes after only a non-fatal warning even when its prepared data is absent.
- **Proposed fix:** Rewrite S8.5.5 as a partition: every enabled non-one-shot must be healthy; every enabled one-shot must have exited 0; no service must satisfy both. For a selected `seeded` or `simulated` variant with an empty derived contract, require either a non-empty `verify` list or an explicit `unchecked = true` acknowledgement on that variant; do not describe an optional check and non-fatal warning as equivalent to an acceptance declaration.
- **Challenges a settled decision:** no.

### T2-03 — Draft.4's new public surface is outside its own closed schema, CLI, and result vocabulary

- **Severity:** BLOCKER
- **Where:** S2.4.1, S3.4.7, S3.8.1, S5.4, S6.10, S8.5.3-S8.5.4, S12.1, S15.3 stage 12, S16.2.2, S16.4.5, S16.8, S17.5, S18, S19
- **Claim:** Several features added by draft.4 are simultaneously required by one rule and rejected or unrepresentable by another closed rule.
- **Evidence:** The concrete mismatches are: (1) S2.4.1 introduces `[ciu].secret_lint_allow`, absent from S3.4.7; (2) S12.1 introduces `[hooks].env_allow`, while S6.10 lists only the three phase arrays; (3) S8.5.3 permits `probe` on an external Realization, absent from S5.4's external key set; (4) S16.2.2 emits `external-missing`/`external-down`, absent from S16.8; (5) S17.5 requires `ciu up --receipts|--require-receipts`, absent from S18's `up` synopsis; (6) S17.5 writes `assumed`, absent from S8.5.4's gate shape; (7) S19 requires `--image`, `--service`, and a test-command input that S18 does not expose; and (8) S16.4.5 defines available variables as a union including bindings/container environment, but S15.3 stage 12 compares only with `forward_env`.
- **Failure scenario:** The demo's `sql-mutation` lane obtains `POSTGRES_HOST`/`POSTGRES_PORT` from `clean.binds.db`; S16.4.5 accepts it, while stage 12 rejects it. A user adding the documented lint suppression or hook allow-list gets an unknown-key ERROR. A zero-instance external probe cannot encode its NOT_RUN reason, and the documented strict receipt flow has no conforming CLI invocation.
- **Proposed fix:** Apply S3.8.4 literally: add every new key, flag, result field, and closed vocabulary value to the one declarative schema source, generate the CLI/schema/docs from it, and add a table-driven conformance test that parses one positive and one negative example for each. Specifically add the keys and reasons above; make stage 12 compare `env_required` to S16.4.5's `available_environment`; add `assumed` with a defined element schema; and make S18 expose every S17/S19 flag. Remove the stale proposal spellings (`>=2.4`, one-level inheritance, `schema_version`, and the old lock order) in the same change.
- **Challenges a settled decision:** no.

### T2-04 — A `pre_secrets` hook can request only secrets that do not exist yet

- **Severity:** BLOCKER
- **Where:** S6.10, S8.7 steps 2-3, S10.1, S12.2
- **Claim:** The per-entry `secrets` capability has no defined value for `pre_secrets`, because the pipeline materializes all declared sources after that phase.
- **Evidence:** S12.2 promises every listed key with its “materialized value.” S8.7 runs `pre_secrets` in step 2 and materializes secrets in step 3. S6.10 permits the same `secrets` list on every phase without a source/phase restriction. A `from = "hook"` value is produced by this phase itself; `file`, `ask`, `generate`, `host`, and `vault` values have not reached step 3.
- **Failure scenario:** A bootstrap hook declares `secrets = ["bootstrap_credential"]`, with that key sourced from a file. One implementation reads the file early, another passes no key, and a third rejects at runtime; all can claim the literal pipeline supports them. A `from = "hook"` key listed on its own producing entry is circular.
- **Proposed fix:** Define a phase/source matrix. The minimal safe rule is that `pre_secrets` entries MUST have `secrets = []`; they may emit `from = "hook"` values but consume none. If a real bootstrap use case needs inputs, introduce an explicitly named `bootstrap_inputs` phase whose allowed non-Vault sources are materialized first, with cycles statically rejected. `pre_compose` and `post_compose` may receive only keys already materialized by step 3.
- **Challenges a settled decision:** no.

### T2-05 — A manifested release is not yet a computable or remotely runnable closure

- **Severity:** MAJOR
- **Where:** S3.4.2-S3.4.3, S6.2, S17.3.1-S17.3.3, S17.4.1, S17.6; demo `ciu.hosts.toml`; demo `infra/mock-targets/ciu.stack.toml:12-22`
- **Claim:** The new release transaction verifies the files it happened to collect but neither defines a complete input/image closure nor an unambiguous activation/rollback target.
- **Evidence:** S17.3.1 requires every file “a template or hook references,” but hooks are arbitrary programs and declare no input list, so exclusion intersection cannot be decided mechanically. The manifest lists image references/digests, while S17.3.3 transfers only the release and capsule; S17.6 neither pushes images nor exports them. The demo has `project.registry.url = ""`, so its project-built images exist only in the sender daemon. S17.4.1 names `<digest>` but the activation CLI has no digest/candidate argument or candidate pointer; its opening sentence says to run `[activate].rollback`, while the same rule says rollback switches and runs the previous release's `apply`. All demo hosts still declare `rollback = "ciu down"`. Also, S3.4.3 makes build ownership per service, but `mock_targets.dns` and `.http` share one tag while only `dns` has `build`; there are 11 build tables, not the claimed 12.
- **Failure scenario:** On the first `prod3` push, the target receives Compose files naming `dstdns/controller:latest` and ten other locally built tags but no image bytes and no registry location, so activation cannot create the containers. A hook that computes `fixtures/{host}.json` omits that file without a declared closure edge. If apply later fails and the demo's rollback command runs, it merely takes the deployment down instead of applying the previous release.
- **Proposed fix:** Add declared `inputs = [...]` to every hook/config generator (or conservatively manifest every tracked file under an explicitly declared root), and make exclusions operate on that declared closure. Model each built image as one named build artifact that services reference, which handles shared tags. Require either a digest-addressed registry push/pull or an OCI archive in the release, verify it on target, and render Compose with the verified digest. `push` must record a per-host candidate digest; `activate apply --release <digest>` (or an atomic `candidate` pointer) must select it explicitly. Activation itself owns the `current`/`previous` switch; consumer commands are hooks around that state machine, not alternative definitions of it. Refuse rollback when `previous` is absent and always run the previous release's `apply`. Define push-time materialization/refusal for absent local `generate`/`ask` capsule entries.
- **Challenges a settled decision:** no; it completes the operator's manifested-release choice.

### T2-06 — Host-network endpoints use an unrecognized wildcard and an unproved address

- **Severity:** MAJOR
- **Where:** S6.3.2, S7.4.7, S7.8 step 4a
- **Claim:** A host-network endpoint can evade collision detection and resolve to an interface on which its process is not listening.
- **Evidence:** S7.4.7 emits `{ bind = "*" }`; S6.3.2's overlap relation recognizes equality, `0.0.0.0`, or equal resolved network addresses, but never `*` (and defines no IPv6 wildcard rule). The same endpoint is forbidden from declaring `host_bind`, yet step 4a assumes it is reachable at the bridge's `host-gateway` and cross-host at the selected network address. No key or probe establishes the process's listen address.
- **Failure scenario:** A host-network service with endpoint 8080 binds only `127.0.0.1`. A bridge consumer receives `ciu-host:8080` and fails, while a remote consumer receives `100.64.0.x:8080` and also fails. If another service claims `100.64.0.x:8080`, literal S6.3.2 does not collide that address with `*`, so the static certification is false.
- **Proposed fix:** Give host-network endpoints an explicit `listen` address/family, derive the resolution only when that address is reachable from the consumer, and live-probe it before admitting dependents. Canonicalize claims to IPv4/IPv6 address sets; define `0.0.0.0` and `::` overlap using the host's `bindv6only` behavior, or conservatively make either overlap every address of its family. If `*` remains an abstract spelling, S6.3.2 must explicitly make it overlap all concrete binds.
- **Challenges a settled decision:** no.

### T2-07 — Gate serialization protects neither the capacity domain nor the run artifacts

- **Severity:** MAJOR
- **Where:** S16.4, S16.6.1, S16.6.4-S16.6.5, S16.7.2, S16.9.1
- **Claim:** The reservation lock is keyed by an evidence directory rather than the cgroup being reserved, and concurrent invocations still write the same evidence paths.
- **Evidence:** S16.6.1 always locks `evidence_dir` and compares with the slice's `memory.max`; S16.6.4 instead says an exec lane is counted against its target container's `memory.max`. Two evidence-directory overrides or worktree-local evidence directories can reserve the same slice independently. Conversely, unrelated exec targets in one evidence directory are summed together. S16.6.5 serializes only LaneResult/history writes; S16.4 and S16.7.2 use shared `<lane>/stdout.log`, `verdict.json`, and `progress.jsonl`, and `artifacts` also land under the same lane directory. Gate processes intentionally coexist under S14.3's shared lock.
- **Failure scenario:** Two simultaneous `ciu gate sql-mutation` invocations use different `CIU_GATE_EVIDENCE_DIR` values but the same cgroup slice, so both admit the full remaining memory. Two invocations using the same directory avoid that race but both append/write the same progress, verdict, log, and artifact names; the later final-result lock cannot reconstruct which bytes belong to which run. For exec lanes, two requests of 3G against one 4G tester may be admitted from a much larger slice even though their actual shared container cannot supply 6G.
- **Proposed fix:** Key admission state and its lock by the actual capacity object: a stable cgroup inode/path for host/ephemeral lanes and the target container cgroup for exec lanes. Put the ledger in a host-visible runtime location independent of project/evidence overrides, and record reservations by that key. Give every invocation an immutable run id and write all output under `<evidence_dir>/<lane>/runs/<run-id>/`; after collection, atomically replace a `last` pointer/LaneResult and prune complete run directories under the lane lock.
- **Challenges a settled decision:** no.

### T2-08 — `--move` turns collision refusal into unauthenticated takeover

- **Severity:** MAJOR
- **Where:** S4.1.2, S4.5.1, S4.5.3; response §2.1
- **Claim:** The ownership label detects a path-hash collision only until the colliding checkout supplies the same flag a legitimate move supplies.
- **Evidence:** S4.5.3 explicitly treats a different `ciu.checkout` as “a 24-bit hash collision or a moved checkout” and refuses “unless `--move` is given.” No persistent fact distinguishes those cases. The sentinel in S4.1.4 proves only that the daemon can see the new path.
- **Failure scenario:** Checkout B happens to share checkout A's six-hex id while A's containers are running. B's init reports the mismatch; its operator assumes the checkout was renamed and follows the printed `--move` remedy. B can then relabel/adopt A's resources, and a later B `clean` removes them despite the response's claim that collisions are refused.
- **Proposed fix:** Keep the readable path-derived `instance_id`, but generate a 128-bit `owner_id` once, store it in the moved checkout's generated state, and stamp it on every resource. `--move` may update `ciu.checkout` only when the caller presents the matching owner token and holds the old/new instance locks; a fresh colliding checkout has no token and remains refused. Recovery after lost state must be a separate explicit command with stronger proof, not the ordinary move path.
- **Challenges a settled decision:** yes — this is a new cost of the checkout-local/path-hash decision the operator did not weigh, but the proposed token stays in the checkout and does not replace the chosen display identity or require a global registry.

### T2-09 — Two stated Compose-to-cgroup conversions do not produce the requested values

- **Severity:** MAJOR
- **Where:** S13.1, S13.3, S13.3.2-S13.4
- **Claim:** Nearest rounding in the inverse CPU mapping undershoots many declared weights, and `memory_max = "max"` discards a finite swap maximum.
- **Evidence:** Using S13.3's own forward map, `cpu_weight = 100` gives nearest-rounded `cpu_shares = 2597`, then `1 + floor((2597-2)*9999/262142) = 99`, not 100; weight 2 similarly maps back to 1. Ceiling, not nearest rounding, is required to select the first shares value in a weight bucket. S13.3 also says “`max` on either side yields `-1`”: for `memory_max = "max", memory_swap_max = "1G"`, Compose receives `memswap_limit = -1`, and Docker applies unlimited swap rather than 1G. A WARN after read-back leaves the resource policy unapplied.
- **Failure scenario:** The demo's `cpu_weight = 100` produces a permanent requested/applied mismatch on every governed service. A third-party project that deliberately leaves RAM unlimited but caps swap at 1G instead gets unlimited swap and can pressure the mixed-use host despite a green deployment.
- **Proposed fix:** Define the CPU inverse with integer ceiling: `shares = 2 + ceil((weight - 1) * 262142 / 9999)`, clamp to 2..262144, and test all 10,000 weights through the forward map. When memory is unlimited and swap finite, either write `memory.swap.max` directly after placement or refuse that combination as unrepresentable by the chosen Docker adapter. A mismatch in an enforceable requested safety cap must abort, not merely WARN; `unsupported` remains the explicit non-application state.
- **Challenges a settled decision:** no; Docker may remain the first adapter if its representable domain is stated honestly.

### T2-10 — One intentional config-file secret exempts every accidental secret in that file

- **Severity:** MAJOR
- **Where:** S2.4.2, S10.2.6
- **Claim:** The exact store-value scan has a file-wide exception where it needs a value-specific exception.
- **Evidence:** S2.4.2 exempts “a config file whose service declares a `delivery = "configfile"` secret.” It does not exempt only matches of the particular keys that were deliberately delivered to that particular template. S10.2.6 permits such files to contain those values, but gives no reason to suppress comparisons against the rest of the store.
- **Failure scenario:** A service deliberately renders its database password and accidentally renders the Vault root token through a context bug. Because the service has one configfile-delivered secret, the whole artifact skips the exact comparison and survives; the strongest part of the lint silently misses the highest-impact leak.
- **Proposed fix:** Compare every rendered config file against every store value. Suppress only a match whose store coordinate is a `configfile`-delivered secret declared for that service and actually requested through `secret("<key>")`; every other match remains an ERROR and deletes the artifact. Report both the compared count and the exact number of authorized value matches omitted from findings.
- **Challenges a settled decision:** no.

## 4 Demo re-derivation

**ASSUMPTION:** `prod3` uses the committed live variants, the site layer shown in the demo, no uncommented instance host-port override, and the literal S8.4.1 orientation `consumer -> provider`; same-Realization edges are discarded and `wait = "none"` contributes only derived network/PKI edges. HTTP and HTTPS socket claims use TCP. These are the inputs the example says it depicts.

### Waves

The selected bundles contain 24 LogicalServices and collapse to 22 Realizations. Recomputing bind, fact, secret-to-Vault, secret-to-minter, and cross-host mesh edges gives exactly the revised example:

```text
0: cadvisor, github_runner, github_runner_webhook, otel_aggregator,
   otel_collector_node, registry_lightweight, tailscale_node, vault,
   webapp_ui_react, webhook_listener
1: consul_server, db_core, redis_core, reverse_proxy, skywalking
2: authentik, db_init, docker_stats_exporter
3: controller, webapp_server, worker_db
4: worker_io
```

No Realization is missing and no alternative wave assignment is needed under S8.4.1.

### Three binding resolutions

| binding | `prod3` | `local` | comparison |
|---|---|---|---|
| `controller.controller.database -> main_db.sql` | `mesh`, `100.64.0.11:5432`, TCP, env variables `POSTGRES_HOST/PORT`, `requires=["tailscale_node"]` | `instance`, `dstdns-98535c-db-core-postgres:5432`, no derived requirement | agrees with S7.8 and the example |
| `reverse_proxy.nginx.controller -> controller.http` | `mesh`, `http://100.64.0.12:8083`, `path=/api/controller`, template delivery, `requires=["tailscale_node"]` | `instance`, `http://dstdns-98535c-controller:8080`, same separate path, no requirement | agrees with S7.8 and the example |
| `ciu.vault -> vault.api` as seen from `rs1002` | `mesh`, `http://100.64.0.11:8200`, delivery none, `requires=["tailscale_node"]` | `instance`, `http://dstdns-98535c-vault:8200`, no requirement | agrees with S7.8.7 and the example |

### `prod3` socket claims

Claims below are deduplicated by endpoint: several bindings may cause the same derived claim. `network(mesh)` means the concrete host mesh address, not a symbolic `published_on` value.

| host | endpoint | scope/bind | host -> container/protocol |
|---|---|---|---|
| gstammtisch | `db_core.postgres.sql` | `network(mesh) 100.64.0.11` | 5432 -> 5432/tcp |
| gstammtisch | `redis_core.redis.redis` | `network(mesh) 100.64.0.11` | 6379 -> 6379/tcp |
| gstammtisch | `vault.vault.api` | `network(mesh) 100.64.0.11` | 8200 -> 8200/tcp |
| gstammtisch | `consul_server.consul.http` | `network(mesh) 100.64.0.11` | 8500 -> 8500/tcp |
| gstammtisch | `db_core.minio.s3` | `network(mesh) 100.64.0.11` | 9000 -> 9000/tcp |
| gstammtisch | `authentik.server.http` | `network(mesh) 100.64.0.11` | 9010 -> 9000/tcp |
| rs1002 | `otel_aggregator.collector.otlp_grpc` | `host 0.0.0.0` | 4317 -> 4317/tcp |
| rs1002 | `otel_aggregator.collector.otlp_http` | `host 0.0.0.0` | 4318 -> 4318/tcp |
| rs1002 | `otel_collector_node.collector.otlp_grpc` | `host 0.0.0.0` | 4319 -> 4317/tcp |
| rs1002 | `otel_collector_node.collector.otlp_http` | `host 0.0.0.0` | 4320 -> 4318/tcp |
| rs1002 | `cadvisor.cadvisor.http` | `host 0.0.0.0` | 8080 -> 8080/tcp |
| rs1002 | `webapp_server.server.http` | `network(mesh) 100.64.0.12` | 8081 -> 8080/tcp |
| rs1002 | `webapp_ui_react.ui.http` | `network(mesh) 100.64.0.12` | 8082 -> 80/tcp |
| rs1002 | `controller.controller.http` | `network(mesh) 100.64.0.12` | 8083 -> 8080/tcp |
| rs1002 | `otel_aggregator.collector.metrics` | `host 0.0.0.0` | 8888 -> 8888/tcp |
| rs1002 | `otel_collector_node.collector.metrics` | `host 0.0.0.0` | 8889 -> 8888/tcp |
| rs1002 | `docker_stats_exporter.exporter.metrics` | `host 0.0.0.0` | 9558 -> 9558/tcp |
| rs1002 | `skywalking.oap.grpc` | `network(mesh) 100.64.0.12` | 11800 -> 11800/tcp |
| rs1002 | `otel_aggregator.collector.health` | `host 0.0.0.0` | 13133 -> 13133/tcp |
| rs1002 | `otel_collector_node.collector.health` | `host 0.0.0.0` | 13134 -> 13133/tcp |
| tsstammtisch | `reverse_proxy.nginx.https` | `host 0.0.0.0` | 443 -> 443/tcp |
| tsstammtisch | `registry_lightweight.tls_proxy.tls` | `host 0.0.0.0` | 5443 -> 443/tcp |
| tsstammtisch | `webhook_listener.webhook.http` | `host 0.0.0.0` | 9000 -> 9000/tcp |
| tsstammtisch | `github_runner_webhook.webhook.http` | `host 0.0.0.0` | 9001 -> 9000/tcp |

There is no collision in this concrete table. Authentik's mesh claim is now 9010 while MinIO remains 9000. On `rs1002`, the wildcard host claims and mesh-specific claims have disjoint ports: 4317, 4318, 4319, 4320, 8080, 8888, 8889, 9558, 13133, and 13134 versus 8081, 8082, 8083, and 11800. The demo's host-network Tailscale service declares no endpoint, so it produces no `bind = "*"` claim; T2-06 is a rule defect, not a current demo collision.

### Gates on `rs1002`

`[resolved.gates.1]` is correctly empty: no later-wave edge asks a service in `rs1002`'s wave 1 (`skywalking`) for a predicate; its tracing consumers use `wait = "none"`.

`[resolved.gates.3].healthy = ["controller.controller"]` and `completed = []` are correct because wave-4 `worker_io` has a default-healthy binding to Controller. **ASSUMPTION:** S8.5.2's phrase “binding `facts`” is exhaustive. Under that literal rule, `facts` is `[]`: `vault:secret/internal/internal_dlq_token` comes from a derived secret-to-minter edge, not a binding's `facts` list. The example's fact row is therefore unsupported. If the intent is to probe minter facts too, S8.5.2 must say so and define them as gate facts; then the example row is correct and the assumption is retired. With receipts supplied, `assumed = []` is expected.

### Testing, builds, and stale forms

- The tester's `memory_max = "4G"` now equals the largest exec request (schema, 4G), so S16.6.4's individual-cap check passes. The `clean` environment supplies `SCHEMA_GATE_PG_DUMP` via `forward_env` and `POSTGRES_HOST/PORT` via a binding, which passes S16.4.5 but fails the stale stage-12 comparison in T2-03. The project and test-runner build argument both use the 4.1 floor.
- All 38 checked-in `*.toml` files parse. There is no declaration ending in `.toml.j2`, no `published_on`, and the actual network declarations use LogicalServices: `mesh_node` and `reverse_proxy`. The stale `realized_by = "tailscale_node"` occurrence is only a comment in `infra/tailscale-node/ciu.stack.toml`, but it should still be corrected because it teaches the withdrawn form.
- No compose template contains an active `build:` block. There are **11**, not 12, service `build` tables: controller, webapp-server, webapp-ui-react, worker-db, worker-io, ddcli, test-runner, db-core-seeded, docker-stats-exporter, mock-targets DNS, and webhook-listener. `mock_targets.http` uses the same `dstdns/mock-targets:latest` tag but has no build table, exposing T2-05/T-29 rather than constituting a twelfth declaration.
- The checked-in minimal project is valid TOML and accurately labels its hand-added endpoint/healthcheck. The SPEC Appendix B snippet is not valid TOML because it repeats `location`, and S18 does not show the S19 invocation's complete option set.
- The demo README and many file headers still call themselves draft.3/rev 3.0; `ciu.toml` still describes bundle inclusion as “one level”; proposal §4.1.9, §4.1.10, and §4.10 retain the old lock order, one-level inheritance, `schema_version`, and judge floor. These do not change the re-derived graph, but they make the rev-3.1 design set internally stale.
- Every demo host declares `rollback = "ciu down"`, so the worked deployment does not demonstrate S17.4.1's promised previous-release apply. Its `bundle_dir = "/opt/ciu/current"` also makes the new symlink path `/opt/ciu/current/current`; if `/opt/ciu/current` was intended to remain the symlink, the inventory needs a parent such as `/opt/ciu`.

## 5 Not verified

- No CIU v8 implementation exists, so I could not run `ciu check --graph`, render a canonical artifact, execute a gate, build a release, or perform Docker/SSH acceptance. I independently parsed the available TOML and re-derived the graph/claims from the normative algorithms instead.
- The demo deliberately omits hook programs, referenced application config templates, Dockerfiles, and build contexts. I therefore did not verify hook behavior, rendered config contents, image builds, or the manifest closure against real bytes; proposal §4.10 already records the omitted demo programs/executable conformance fixture, so that omission is not a new finding.
- I did not live-probe `host-gateway`, Docker's cgroup read-back, Docker Desktop/remote-daemon sentinel behavior, NFS directory locks, or remote activation. T2-06 and T2-09 follow from contradictions/math in the specified rules, not an unreported live-platform result.
- **ASSUMPTION (waves/claims):** `prod3` uses live variants, the shown site layer, no active host-port override, HTTP/HTTPS map to TCP, and S8.4.1's consumer-to-provider orientation is exhaustive.
- **ASSUMPTION (gate facts):** S8.5.2's explicit “binding `facts`” limits gate fact probes; if secret-to-minter facts are intended too, the rule must say so.
- **ASSUMPTION (receipt trust):** the operator's settled boundary holds: receipts remain on authenticated, pinned-host-key SSH transport. I did not require signing and did not evaluate a non-SSH activation channel.

## 6 Machine summary

```json
{
  "findings": [
    {
      "id": "T2-01",
      "severity": "BLOCKER",
      "where": ["S3.7.1", "S3.7.3", "S14.2", "S17.4.3", "S17.4.4", "S17.5"],
      "claim": "A receipt has neither a reproducible subject nor sufficient release and runtime freshness.",
      "regression": true,
      "challenges_settled": false
    },
    {
      "id": "T2-02",
      "severity": "BLOCKER",
      "where": ["S5.3.6", "S8.5.5", "S8.6.3"],
      "claim": "Acceptance makes successful one-shots fail and leaves empty prepared variants semantically opt-in.",
      "regression": true,
      "challenges_settled": false
    },
    {
      "id": "T2-03",
      "severity": "BLOCKER",
      "where": ["S2.4.1", "S3.4.7", "S5.4", "S6.10", "S15.3", "S16.4.5", "S16.8", "S18", "S19"],
      "claim": "Draft.4's new public keys, flags, states, and validation rule are outside their own closed surfaces.",
      "regression": true,
      "challenges_settled": false
    },
    {
      "id": "T2-04",
      "severity": "BLOCKER",
      "where": ["S6.10", "S8.7", "S10.1", "S12.2"],
      "claim": "A pre_secrets hook may request values that are materialized only after it runs.",
      "regression": true,
      "challenges_settled": false
    },
    {
      "id": "T2-05",
      "severity": "MAJOR",
      "where": ["S3.4.3", "S6.2", "S17.3.1", "S17.3.3", "S17.4.1", "S17.6"],
      "claim": "A manifested release is not yet a computable or remotely runnable file, image, secret, and activation closure.",
      "regression": true,
      "challenges_settled": false
    },
    {
      "id": "T2-06",
      "severity": "MAJOR",
      "where": ["S6.3.2", "S7.4.7", "S7.8"],
      "claim": "Host-network endpoints use an unrecognized wildcard and an unproved listener address.",
      "regression": true,
      "challenges_settled": false
    },
    {
      "id": "T2-07",
      "severity": "MAJOR",
      "where": ["S16.4", "S16.6.1", "S16.6.4", "S16.6.5", "S16.7.2", "S16.9.1"],
      "claim": "Gate serialization protects neither the actual capacity domain nor per-run evidence artifacts.",
      "regression": true,
      "challenges_settled": false
    },
    {
      "id": "T2-08",
      "severity": "MAJOR",
      "where": ["S4.1.2", "S4.5.3"],
      "claim": "The unauthenticated --move exception turns a detected path-hash collision into resource takeover.",
      "regression": true,
      "challenges_settled": true
    },
    {
      "id": "T2-09",
      "severity": "MAJOR",
      "where": ["S13.3", "S13.3.2", "S13.4"],
      "claim": "The CPU inverse and unlimited-memory swap conversion do not produce the requested cgroup values.",
      "regression": true,
      "challenges_settled": false
    },
    {
      "id": "T2-10",
      "severity": "MAJOR",
      "where": ["S2.4.2", "S10.2.6"],
      "claim": "One intentional config-file secret exempts every accidental store value in that file.",
      "regression": true,
      "challenges_settled": false
    }
  ],
  "dispositions": {
    "landed": 14,
    "incomplete": 13,
    "not_landed": 0,
    "broke": 8
  }
}
```
