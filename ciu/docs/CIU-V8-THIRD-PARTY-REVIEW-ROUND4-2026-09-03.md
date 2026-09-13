# CIU v8 third-party review, round 4 — draft.7 / rev 3.4

## 1 Verdict

Draft.7 is **not implementable as written**. It closes the intended substance of the receipt-provider comparison, cold move, image reference map, acceptance/resource rules, and consumer-vantage decision, but the draft.6 repair moved mutable state and locking onto paths that are not unique to an instance and not stable across releases. The resulting remote state machine can mix two projects' state under the default `/opt/ciu`, can run candidate and current releases under different lock inodes, and cannot make the three pointer changes in S17.4.1 atomic or crash-recoverable. Its containing-worktree rule is contradicted by the mandatory check stage and by the checked-in fixture's literal path, and target `prepare` invokes the Docker sentinel that both mutates a verified release and makes a `docker_optional` host impossible.

The host-enrollment design is also **not implementable in either line from O1–O6**. In both v7 and v8, the supposedly version-pinned `get.py` URL pins only the installer program, while the current template resolves the installed release as `latest` unless `--version` reaches the install path; the proposal's `enroll` grammar has no such flag and step 2 proves only that some `ciu version` exits zero. The current template installs wheels in a private venv without specifying a stable `ciu` launcher, so the required bare remote command is not established either. The trust path executes the unauthenticated downloaded verifier as root before its downstream manifest checks can help, and the optional no-fingerprint path asks the operator to approve attacker-supplied scan output rather than compare a second channel. Key replacement can destroy the only working credential before the new one is accepted; the v7 location aliases the existing host-secret store; v8 `--global` leaves its private key in checkout-local state; and the root mutation of an existing user's `authorized_keys` lacks a safe filesystem transaction.

Of T3-01..T3-10, I count **3 landed, 2 landed but incomplete, 0 not landed, and 5 landed changes that break another binding rule**. Six of the ten new findings are blockers. The necessary corrections do not reopen the model, naming, plain TOML, manifested-release, ciu8-subproject, or in-checkout decisions. T4-01 is a new cost of deriving release state from the selected bundle-directory design, and T4-07 narrowly challenges the stated use of the estate's existing `curl | python3` posture only where it is elevated to an enrollment root of trust.

## 2 Round-3 fix audit

| id | claimed disposition | landed? | round-4 remark |
|---|---|---|---|
| T3-01 | A | landed | S17.4.3–S17.4.4 validate a receipt against the activation-manifest entry for provider `P`; a receipt for `Q` cannot replay as `P`, absent activation ids do not match, and the subject uses the release closure rather than host-local render bytes. `plan` can call the same pure release-closure builder as `push` once build/image facts exist; byte changes correctly produce a different digest and a refusal. The activation manifest's storage and pointer transaction have independent defects in T4-02, but the T3-01 provider/freshness repair itself landed. |
| T3-02 | A′ | landed-and-broke-S2.6/S4.1.4 | Moving mutable host facts, secrets, records, data, hook state, receipts, leases, and evidence out of an immutable release is the right class repair. But `<bundle_dir>/state/` is not instance-scoped and `/opt/ciu` is the default for every inventory row, while release `prepare` still invokes S4.1.4's write-into-checkout Docker sentinel (T4-01, T4-03). The response's assertion that a bundle directory holds one instance is not a normative reservation or checked invariant. S2.3.4 also still says realness records are in the generated file. |
| T3-03 | A | landed-and-broke-S6.2/S15.3 | The containing-worktree boundary, source-relative path normalization, unused inherited judge floor, and release flattening are present; S3.3 explicitly admits the generated `ciu.inherited` table path. Stage 4 still says a build context must be inside the checkout, however, and the fixture's context has one `..` too many (T4-04). |
| T3-04 | A | landed-and-broke-S14.4.1/S14.4.8 | S14.4.9 now makes normal Realization mutators take stack locks and atomic per-lease files avoid lost updates. On a target, current and candidate releases use different checkout/stack directory inodes for the same instance resources, and `CIU_LEASE_FDS` cannot be assumed to survive `sudo`, SSH, or container exec (T4-01, T4-09). |
| T3-05 | A′ | landed | “Live” is operationally defined by **any** container, network, or named volume carrying the old owner token, which includes stopped containers and dangling labelled objects. Refusing that set, minting a new identity/token only when it is empty, and treating copy as `--fresh` makes the cold-move disposition coherent. It gives up live transfer explicitly rather than pretending Docker labels can move. |
| T3-06 | A | landed-and-broke-S18 | The missing APIs, variables, reasons, and probe result are present. S3.8.6 now requires `ciu version --surfaces --json`, but S18's closed command row declares only `ciu version`; neither the surfaces JSON schema nor machine-identifiable document enumeration syntax is specified (T4-10). |
| T3-07 | A | landed-but-incomplete | S17.6.1's reference-level ownership map and release-unique archive tag close the wrong-tag/per-service defects. `docker image prune -a` may remove that tag and image while no container references it; staging alone loads it, and rollback never says to reload the retained archive immediately before create (T4-02). |
| T3-08 | A′ | landed-and-broke-repository-AGENTS-assay-progress/S16.9.4 | Random 128-bit ids, exclusive directories, a run manifest, and withdrawal of CIU-level resume close the unsafe identity and containment defects. S16.7.2/S16.9.4 nevertheless force Assay progress into `<run_dir>/progress.jsonl`, contradicting this repository's mandatory `--resume --progress .assay/progress-<lane>.jsonl`; pruning also depends on an “owning pid” absent from `ciu/run` and unsafe across hosts/PID reuse (T4-09). The response's reasoning cannot override that binding repository directive. |
| T3-09 | A | landed-but-incomplete | S8.5.2a now chooses an instance-network, host-network, or cross-host consumer vantage and requires explicit UDP `probe = "none"`. It never defines the probe image/tool, helper admission and cgroup placement, lifecycle/cleanup, or how one helper is amortized across a wave; those omissions make the required probe unbuildable and can launch ungoverned containers (T4-03). |
| T3-10 | A′ | landed | Mandatory `memory_max` on every finite-capacity admitted lane is a legitimate declared policy, not a substitute for a fact. S13.3.2 now makes every requested hard cap, including `cpu.max` and normalized `io.max`, a read-back ERROR; weights/protections remain the advisory class. The whole-capacity alternative is unnecessary once omission is rejected. |

Counts: **landed 3; landed-but-incomplete 2; not-landed 0; landed-and-broke 5**.

## 3 New findings

### T4-01 — A release has no stable, instance-scoped state or lock namespace

- **Severity:** BLOCKER
- **Where:** S2.6.1–S2.6.3, S7.2, S14.4.1, S14.4.7–S14.4.9, S17.3.3, S17.4.1; [enroll both] proposal §4/O6, v7 S14.3, cmru S6.4, `ciu/cmru.toml`
- **Claim:** Deriving every remote instance's mutable state from `<bundle_dir>/state/` aliases different instances, while using release directories as the instance/stack lock keys fails to serialize two releases of the same instance.
- **Evidence:** S7.2 defaults every host's `bundle_dir` to `/opt/ciu`. S2.6 then places the store, owner record, realness, receipts, leases, evidence, data, and host facts directly in `/opt/ciu/state/`, with no project, `instance_id`, or host component and no rule reserving one bundle directory to one instance. The statement “All releases of one instance share one state root” does not state the converse. **ASSUMPTION:** independent projects/controllers may use the documented default on the same target; the text neither forbids nor detects it. The enrollment dependency adds a second owner of the same path vocabulary: cmru S6.4 gives a system installer root its own `releases/`, `current`, `shared/`, and `venv/`. `ciu/cmru.toml` has no `[project.installer]`, and V8-29/O6 do not choose a disjoint `install_dir_system`. **ASSUMPTION:** if the expected CIU install “under `/opt/ciu/current/`” uses `install_dir_system = "/opt/ciu"`, as the review prompt asks to test, the tool installer and v8 deployment bundle directly own the same `releases/` and `current`; v7 S14.3's default deployment destination is the installer's `/opt/ciu/current` symlink itself. Separately, S14.4.1 locks “the checkout root directory,” and S14.4.9 locks stack directories. `/opt/ciu/releases/A/` and `/opt/ciu/releases/B/` are different inodes, as are their stack directories, although both releases operate on the same compose-project labels, network, volumes, and state. The claim in S14.4.7 that the key is “the same inode for every process on the host” is therefore false for release operation.
- **Failure scenario:** Projects `alpha` and `beta` both enroll `rs1002` with the v8 default `/opt/ciu`. `beta push` replaces `alpha`'s `candidate`, imports its capsule over `/opt/ciu/state/ciu.secrets.toml`, and later switches the shared `current`. Under the colliding installer choice, `get.py enroll` itself atomically points `/opt/ciu/current` at a CIU software release; v8 activation treats that directory as an application release, while v7's default `bundle_dir = "/opt/ciu/current"` syncs deployment files into the installed software release. Even with disjoint bundle paths, controller 1 runs current release `A` while controller 2 prepares candidate `B`; each locks its own release and stack inodes, so `clean`/`up` can remove or replace the same labelled containers underneath the other command.
- **Proposed fix:** Reserve disjoint roots for the CIU tool install and CIU-managed deployments. Make the host deployment namespace explicit and exclusive: for example `<bundle_base>/<project>/<instance_id>/` containing `state`, `releases`, and all pointers, with a persisted owner marker that must equal `{project, instance_id, owner_id}` before any mutation. Treat `bundle_dir` as that instance directory or reject reuse by another owner; give cmru a separately named install root and make overlap a generated-config/gate refusal. Put the canonical remote instance and Realization lock objects under the stable state root (for example `state/locks/instance/` and `state/locks/stacks/<R>/`), not under a release, and make controller-side and target-side `push`/`activate` use those keys. `ciu instance show` should print the resolved deployment namespace, owner, state root, and lock roots.
- **Challenges a settled decision:** **yes, narrowly.** This is a new cost of the selected manifested-release/state-root design. It does not challenge keeping checkout-local state for an ordinary checkout; it rejects only a non-injective remote derivation and release-local lock inodes.

### T4-02 — Activation is neither a pinned transaction nor crash-recoverable

- **Severity:** BLOCKER
- **Where:** S2.1, S2.3.1, S2.6.2–S2.6.3, S14.3, S17.3.3, S17.3.5–S17.3.6, S17.4.1, S17.4.3–S17.4.4, S18
- **Claim:** The activation manifest, candidate selection, image availability, and `current`/`previous` transition do not form one durable compare-and-swap transaction, so a crash or concurrent push can deploy or record a release other than the planned one and can destroy rollback reachability.
- **Evidence:** The release digest itself is computable without transfer by running the same closure/manifest builder over stable inputs, and I do not treat proposal §4.10 item 20's byte-identical hand-flow prerequisite as a new defect. The transaction that consumes that digest is still incomplete. S14.3 classes `activate` as mutating, S18 classes `activate plan` as read-only, and S17.4.1 says `plan` **writes** `ciu.activation.json`. That file is on S2.3.1's ignore list but absent from S2.1 and S2.6.2's exhaustive state-root list; its sender/target path and transfer are unspecified, and two standalone plans can hold shared locks while replacing one apparent pathname. `apply` reads a mutable `candidate`, while a later push may replace it. Step 5 says CIU “atomically switches” `current`, records former current as `previous`, and removes `candidate`, but those are three namespace mutations; no single POSIX rename makes them atomic and no journal/recovery order is given. A second push can install candidate `C` while activation of `B` is running, after which `B`'s step 5 removes `C`. Finally, archive mode loads the release tag only during staging. Docker documents that `docker image prune -a` removes every image not referenced by a container, so a candidate not yet applied or a retained previous release can lose its tag despite the archive remaining in the release ([Docker `image prune`](https://docs.docker.com/reference/cli/docker/image/prune/)).
- **Failure scenario:** Two operators run standalone `plan`; both take the read-only/shared class and race to replace `ciu.activation.json`, so the first operator copies a manifest with the second activation id. Separately, `A` is current and `B` candidate. Activation applies and health-checks `B`; before pointer update, another push replaces candidate with `C`. The first activation follows the live candidate or unconditionally removes it, respectively deploying the unplanned digest or discarding `C`. In another run, the process crashes after `current -> B` but before `previous -> A`; retry observes `B` as former current and overwrites rollback history with `B`. Later `docker image prune -a` removes release `A`'s unique tag; rollback switches no pointer on failure, but its apply has already partially replaced containers and cannot recreate the old image although `A`'s archive is retained.
- **Proposed fix:** Make `plan` and `push` call one pure release-closure builder and refuse when a required build/image fact is absent. Define plan as a mutation or write each manifest by exclusive creation to a unique `activations/<activation_id>.json` path, return that path, and specify how it is transferred. `apply` must resolve `candidate` once, verify it equals the manifest entry, and pass that digest through every step; serialize push/activate under T4-01's stable target lock. Implement pointer state as one atomic generation record (or a write-ahead journal plus deterministic recovery) containing `{current, previous, candidate, activation_id, phase}` and compare-and-swap the expected generation. Remove candidate only if it still equals the applied digest. Before every create, including rollback, verify registry digests and reload/re-tag release archives when the expected image is absent; prune images in the same reachability transaction as releases.
- **Challenges a settled decision:** no. This makes the selected manifest, candidate, current/previous, and archive designs enforce their stated contract.

### T4-03 — Target preparation invokes an impossible and ungoverned helper-container contract

- **Severity:** BLOCKER
- **Where:** S2.6.1, S4.1.2, S4.1.4, S7.2, S8.5.2a, S13.2, S17.4.1, S18.3; repository `AGENTS.md` “Host cgroup placement for spawned containers”
- **Claim:** The mandatory path proof changes a verified release and requires Docker even where Docker is explicitly optional; every new probe/helper container is also missing the image, governance, admission, and cleanup contract required to launch it safely.
- **Evidence:** S4.1.2 says `instance init --host` in a release “only” writes the host file and performs no path comparison. S17.4.1 nevertheless says the same `prepare` invocation proves the release's physical path through S4.1.4. S4.1.4 writes a random sentinel **into the checkout** and starts a throw-away Docker container. That write is neither one of S2.6.1's permitted render outputs nor compatible with the release remaining byte-identical, and `docker_optional` hosts cannot run the container. S18.3 does not list `instance init` as requiring Docker. S8.5.2a similarly mandates instance- and host-network throw-away containers without specifying an image or TCP/HTTP/TLS probe implementation, cgroup parent, hard caps, slice admission, detached `create/start/wait/logs/remove` lifecycle, or cleanup after interruption. This repository's binding rule requires every spawned test/gate/build container to use the declared background cgroup rather than Docker's unconfined default.
- **Failure scenario:** A Docker-less `docker_optional` edge host receives a valid release. `prepare` either skips S4.1.4, violating the stated proof, or invokes Docker and fails before any deployment. On a Docker host the sentinel dirties the verified release, so a later manifest recheck refuses it. During a 22-Realization deployment, an implementation starts one unbounded probe image per edge at Docker's default cgroup; an interruption leaks helpers next to production, or the unpinned image lacks the implementer's chosen probe binary and turns valid reachability into a deployment refusal.
- **Proposed fix:** Separate checkout initialization from release preparation. For a verified target release, derive its logical root from the validated manifest path and do not perform a bind-sentinel write; if daemon-visible paths are needed, probe a temporary file under the stable state root and bind only the relevant state/data path. State explicitly how `docker_optional` satisfies or skips that proof without weakening other hosts. Define one CIU-owned, digest-pinned helper image and a common helper launcher: verified background cgroup, required hard caps, capacity admission, detached lifecycle, captured job status, bounded timeout, and `finally` cleanup. Reuse one appropriately networked helper per host/wave where possible and record its image digest and vantage.
- **Challenges a settled decision:** no. It preserves consumer-vantage probing and path proof while giving them an executable backend.

### T4-04 — The monorepo repair contradicts its check stage and its checked-in example leaves the worktree

- **Severity:** BLOCKER
- **Where:** S1.5, S2.3.1, S3.1.5, S6.2, S15.3 stage 4, S16.11.1, S17.3.1; `v8-dstdns-demo/examples/monorepo/appy/tester/ciu.stack.toml`; fixture README
- **Claim:** The same sibling build context is accepted by S6.2 and refused by S15.3, while the checked-in path does not point at the sibling at all; therefore the promised fixture cannot pass the rules as written.
- **Evidence:** S6.2 deliberately permits `build.context` outside the child checkout root so long as it remains inside the containing Git worktree. S15.3 stage 4 still requires “`build.context` inside the checkout.” Those are opposite outcomes for the exact monorepo pattern. From `examples/monorepo/appy/tester/`, the fixture's `../../../tester-unified` resolves to `examples/tester-unified`, not `examples/monorepo/tester-unified`; the latter requires `../../tester-unified`. The resolved former directory does not exist. The fixture also says its lack of a gitignore is intentional, but S2.3.1 says stage 1 **MUST** prove every output ignored in every Git worktree. Mechanical `git check-ignore` checks at the child roots found, among others, `ciu.resolved.toml`, `ciu.instance.toml`, `ciu.hosts.toml`, and `ciu-gate-evidence/` not ignored. Thus neither child is a passing `ciu check` fixture even before the unimplemented validator runs.
- **Failure scenario:** An implementer follows S6.2 and accepts a correct sibling context; the required stage-4 implementation then emits an ERROR because it is outside `appy`, making the advertised use case impossible. An implementer instead copies the fixture literally and asks Docker to build from nonexistent `examples/tester-unified`; `push` has no image to ship. After correcting the path, `ciu check` still fails stage 1 because required generated files are not ignored.
- **Proposed fix:** Change S15.3 stage 4 to the canonical containing-worktree test of S3.1.5/S6.2, correct the fixture to `../../tester-unified`, add child-effective gitignore coverage, and make V8-28 execute `ciu check` at the root and both children plus an image-context resolution assertion. For release flattening, keep `ciu.inherited.toml` as a generated input and test the exact parsed shape: TOML `[ciu.inherited]` is the nested path `ciu.inherited`, which S3.3 permits; the loader must consume its `sources` metadata rather than accidentally treating it as a consumer `[ciu]` option. A linked worktree or symlinked entry reaches the canonical Git top level; a submodule intentionally has the submodule as its containing worktree and must refuse a parent sibling.
- **Challenges a settled decision:** no. It retains explicit inheritance, nearest-root isolation, and the containing-worktree boundary.

### T4-05 — The enrollment URL pins the installer, not the installed or invoked CIU

- **Severity:** BLOCKER
- **Where:** [enroll both] enrollment proposal §§2–5 and O1/O4/O6; v8 S7.2.4/S17.4.1/S18; v7 S14.7a–S14.7c; cmru `templates/get.py.tmpl` `do_install`, `_install_wheels`, and CLI grammar; KI-24
- **Claim:** Nothing carries the control host's CIU version through `get.py enroll` into the install transaction or proves that exact executable afterward, so the core same-version claim and v8's target `prepare` precondition are false.
- **Evidence:** The printed URL contains `ciu-v<version>`, but the proposed `get.py enroll` flags contain no `--version`. The current template's `do_install` tests `args.version`; when absent it calls `resolve_latest_tag`, and an `enroll` namespace that simply calls it would either lack that attribute or install the then-latest release. The script URL therefore authenticates neither the selected wheel version nor the installed result. `_install_wheels` installs into `<install-root>/venv/bin`, while the proposal defines no system launcher or absolute remote path. **ASSUMPTION:** the future CIU-specific render does not create a separate PATH launcher that neither the current template nor proposal names. Step 2 runs bare `ciu version` and accepts success without parsing or comparing a version/API value. O4 likewise asserts only that `ciu ssh ... -- ciu version` succeeds. A stale, newer, PATH-shadowing, or entirely different `ciu` satisfies that oracle.
- **Failure scenario:** Controller 8.0.0 prints the 8.0.0 `get.py` URL. Before the admin runs it, 8.0.1 is published; `enroll` follows the template's unpinned install branch and installs 8.0.1. The deploy user's PATH still resolves `/usr/local/bin/ciu` 7.x—or has no `ciu` because the wheel lives only in the private venv. Step 2 either falsely certifies 7.x or refuses after a successful install; if it certifies, v8 activation sends draft.7 inputs to a target implementing another schema.
- **Proposed fix:** Make the desired release an explicit authenticated input to the target command (`get.py enroll --version ciu-v8.0.0`) and ensure the installer asset itself is bound to the same release manifest. Define a stable installed launcher, preferably an absolute system-scope path reported by `get.py`, and have step 2 invoke that path. Require `ciu version --json` to return a specified artifact containing exact semantic version, line, and supported API versions; compare it with the expected value before writing inventory. Extend O1/O4/O6 with controlled wrong-version and PATH-shadow binaries that must fail. `--no-install` must require the same exact proof rather than relaxing it.
- **Challenges a settled decision:** no. A two-step, version-pinned flow remains viable; this makes “pinned” an end-to-end comparison rather than a URL label.

### T4-06 — Key creation, rotation, and global storage are not transactional

- **Severity:** BLOCKER
- **Where:** [enroll v7] v7 S14.3a, S14.7a–S14.7c, proposal §§3/5/6; [enroll v8] S2.6.2, S7.2.4, S17.1, S18; proposal §§3/5/6; O1/O4
- **Claim:** `--replace` overwrites the active private key before the new credential is proved, v7 aliases an existing secret-store coordinate, v8 global inventory points back into disposable checkout state, and concurrent lock-free writers can lose each other's changes.
- **Evidence:** Step 1 says `--replace` regenerates the key at the final active path, while step 2 later overwrites the row. That immediately destroys the old credential; `--abort` then removes the same path. There is no pending-generation name or rollback after the target step fails. In v7, S14.3a maps a host-secret entry `ssh_key` to `<repo>/.ciu/secrets/hosts/<name>/ssh_key`, exactly the path S14.7 claims is a new enrollment key namespace. A consumer already using that valid S14 path can have secret bytes overwritten by an OpenSSH private key or vice versa, contradicting proposal N23's “touches no existing S14 path.” In v8, `--global` writes the inventory under `~/.config/ciu/hosts.toml` but still stores the key under the invoking checkout's state root; moving/deleting/cleaning that checkout silently breaks a supposedly global row. S18 calls enrollment lock-free even though key files and a comment-preserving inventory update are shared mutable objects; no cross-process exclusion or compare-and-swap is specified.
- **Failure scenario:** A working controller rotates `rs1002`; step 1 overwrites its only private key, but the console command is mistyped. The old public key remains on the target and the new private key cannot log in, so both completion and automated recovery fail; `--abort` deletes even the new recovery material. Separately, two controllers enroll different names concurrently, both parse the same inventory, and the last round-trip write drops the other's row. A v7 host whose `[deploy.hosts.rs1002.secrets] ssh_key = "GEN_LOCAL:unused"` already exists has its store value replaced by a PEM file.
- **Proposed fix:** Give every attempt a random pending generation under an enrollment-specific namespace. Keep the active key and row unchanged until target authorization and exact-version proof succeed, then atomically switch the inventory to the pending path; `--abort` removes only the named pending generation. Retain the old key until an explicit post-switch revocation or tested rollback. Put v7 enrollment keys outside the S14.3a store namespace, and put v8 `--global` keys under an XDG state directory independent of any checkout. Take a no-follow file lock around key-generation state and the inventory read/modify/write; use temp file, `fsync`, atomic rename, and a generation comparison. Add crash-point, concurrent-writer, replacement-failure, abort, and existing-S14-secret oracles.
- **Challenges a settled decision:** no. This is storage and transaction completion for the accepted two-step rotation shape.

### T4-07 — The mandatory enrollment path does not authenticate its root program

- **Severity:** MAJOR
- **Where:** [enroll both] proposal §§3.3, 4, 5.1, 7 and O1/O3/O4; v8 S7.2.4; v7 S14.7a–S14.7d; cmru `templates/get.py.tmpl` `download_and_verify`
- **Claim:** The target executes a network-fetched Python program as root before establishing its authenticity, and the optional interactive host-key path has no independent fingerprint at all.
- **Evidence:** `curl -fsSL URL | sudo python3 - ...` gives the downloaded bytes root execution authority. The SHA256 sidecar and optional minisign verification happen **inside those bytes** and cover the later bundle, not the verifier itself; the printed command supplies no `--manifest-pubkey`, and the current template explicitly warns and skips minisign when that argument is absent. A version-looking GitHub URL is not by itself immutable: GitHub documents asset/tag immutability as a repository feature that must be enabled for releases ([GitHub immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases), [enabling immutable releases](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/establish-provenance-and-integrity/prevent-release-changes)). O3's assertion that no download happened after a prerequisite failure cannot include the installer fetch, which already completed before Python starts. For host authentication, the explicit console fingerprint path is a real second channel. The fallback without `--fingerprint`, however, merely asks the operator to confirm fingerprints produced by the same unauthenticated network path. The OpenSSH manual warns that keys gathered by `ssh-keyscan` cannot establish authenticity without out-of-band verification ([`ssh-keyscan(1)`](https://man.openbsd.org/ssh-keyscan.1)).
- **Failure scenario:** An attacker controls a mutable release asset/publishing account, the configured mirror, or the HTTPS path with a trusted certificate and serves a modified `get.py`; it runs as root, installs its own SSH key, and prints any reassuring downstream “verified” output. In step 2, an on-path attacker presents its own SSH host key; with no supplied fingerprint CIU displays that key and the operator confirms it, so CIU pins and logs into the attacker. The attacker needs no target host private key in either path.
- **Proposed fix:** Have the already-installed controller print an expected SHA-256 and preferably a signature/public-key identity for `get.py`; download to a mode-0600 temporary file, verify it with a trusted local tool/key **before** `sudo python3`, then execute it. Require immutable releases or a commit/content-addressed mirror but do not substitute that policy for the local hash check. Pass a trusted manifest public key into the subsequent install and make signature absence a refusal for enrollment. Require `--fingerprint` by default; keep scan-and-confirm only behind the already explicit `CIU_SSH_INSECURE_TOFU=1`, labelled as no second channel. Accept a typed `{algorithm, SHA256 fingerprint}` from any policy-supported host key and pin exactly the matching key rather than hardcoding ed25519 in the completion command. Split O3 into “no payload download after prerequisite refusal” and the unavoidable, authenticated installer fetch.
- **Challenges a settled decision:** **yes, narrowly.** The defect is a new unweighed cost of using the estate's existing `curl | python3` posture as a root-level trust bootstrap. It does not reopen the operator's two-step/no-callback choice.

### T4-08 — Root's `authorized_keys` mutation trusts attacker-controlled paths and text

- **Severity:** MAJOR
- **Where:** [enroll both] proposal §§3–4 and 7; v8 S7.2.4; v7 S14.7b/S14.7d; KI-24 O2/O3
- **Claim:** The target-side idempotency contract is line-oriented but not filesystem-safe or grammar-complete, so root can follow a malicious existing user's symlink, corrupt unrelated files, or install a restriction different from the one the controller requested.
- **Evidence:** The target “creates or confirms” a user and then chmods/chowns `~USER/.ssh` and `authorized_keys`, but says nothing about validating the passwd-record home/shell/uid, rejecting symlinks or non-regular files, checking owner/link count, opening relative to a trusted directory descriptor, locking against `sshd`/another enrollment, or committing the append atomically. **ASSUMPTION:** a pre-existing deploy user may be untrusted and control its home contents; the proposal does not restrict enrollment to a newly created account. `--from PATTERN` is inserted into both a shell one-liner and an OpenSSH option whose commas, quotes, backslashes, negation, CIDRs, and host patterns have grammar; no accepted grammar or escaping rule rejects CR/LF and option injection. OpenSSH treats `authorized_keys` as options followed by key type/base64/comment, not as an arbitrary string prefix ([`sshd(8)` authorized_keys format](https://man.openbsd.org/sshd.8)). O2 tests only the happy re-run and modes, and O3 only a missing SSH server.
- **Failure scenario:** Existing user `ops` makes `~ops/.ssh/authorized_keys` a symlink to `/root/.ssh/authorized_keys`. Enrollment follows it as root, changes ownership/mode, and appends the CIU line to root's authorization file. Or a `--from` value containing a quote/newline changes the target shell argv or creates a second unrestricted authorized-key line. Two simultaneous installers both observe the key absent and append duplicates despite the promised once-only result.
- **Proposed fix:** Resolve the user through the system account database and validate a usable absolute home, uid/gid, and login policy. Walk/open the home, `.ssh`, and file with directory descriptors and no-follow semantics; require expected owner, directory/regular type, and safe link count, refusing rather than repairing surprising objects. Lock a dedicated file in the verified `.ssh`, parse authorized-key records, write a complete temp file preserving unrelated bytes, `fsync`, rename atomically, and `fsync` the directory. Define and validate the supported `from=` pattern grammar, construct the OpenSSH record with a real serializer, and shell-quote every printed argument. Add adversarial symlink/hardlink/FIFO, concurrent append, partial failure, pre-existing unrelated key/comment, and hostile-pattern oracles.
- **Challenges a settled decision:** no. Root remains necessary for user creation and system install; the finding constrains what root may trust.

### T4-09 — Process identity is lost at both the lease and run-pruning boundaries

- **Severity:** MAJOR
- **Where:** S14.4.7–S14.4.9, S16.9.4, S18.2
- **Claim:** A numeric `CIU_LEASE_FDS` value is not a transferable lock capability through the wrappers the spec names, and an unstored pid is not a safe owner identity for deciding whether a run directory is complete.
- **Evidence:** S14.4.8 requires the held descriptors to reach `<cmd>` and lets an inner CIU/run-gate reuse them. A local launcher can do that only by clearing close-on-exec and explicitly preserving the fds: Python's subprocess API closes non-standard descriptors by default and requires `pass_fds` to retain selected ones ([Python `subprocess` documentation](https://docs.python.org/3/library/subprocess.html)). `sudo` normally closes descriptors above its close-from boundary unless both an option and policy permit preserving them (the `closefrom_override` policy is documented in the [sudoers manual](https://www.sudo.ws/docs/man/1.9.14/sudoers.man.pdf)); SSH starts a process on another kernel; Docker exec asks the daemon to create a new in-container process. Neither remote mechanism can carry a controller's open file description. Linux `flock` ownership follows the open file table entry, not an environment number ([`flock(2)`](https://www.man7.org/linux/man-pages/man2/flock.2.html)); after a wrapper closes it, the same integer may be reused for an unrelated file. S16.9.4 then says a run whose “owning pid is alive” is never pruned, but `ciu/run` does not contain a pid, host, boot id, process start time, or completion marker. Even a bare pid is reusable. **ASSUMPTION:** evidence may be shared between hosts; the spec does not forbid it, and on such storage a pid cannot identify the owner host.
- **Failure scenario:** `ciu lease acquire --realization tester -- sudo ciu clean` preserves `CIU_LEASE_FDS="tester=7"` but sudo closes fd 7; the new process opens a log at fd 7 and trusts the number as the lease, then mutates without holding the stack lock. With `ssh` or `docker exec`, no equivalent fd exists at all, so the inner process either deadlocks reopening the key or skips exclusion. Separately, run 1 dies; its pid is reused by a long-lived unrelated process, so pruning retains the incomplete directory forever. On shared evidence, host B happens to have that pid and reaches the same wrong conclusion; or a complete run with no representable completion state is pruned while collection is still finishing.
- **Proposed fix:** Scope inherited-fd reuse to a direct same-kernel child. When `CIU_LEASE_FDS` claims a key, validate the fd with `fstat` against the expected stable key's device/inode and a non-blocking lock operation that distinguishes the inherited open description; a missing/mismatched claimed fd is a refusal, never “already held” and never a blocking reopen beneath its parent. With no inherited claim, acquire normally. Provide explicit launcher adapters for Python and sudo where policy permits. For SSH/container transitions, acquire a destination-side lease under T4-01's stable target key and pass a lease identity/status protocol, not an fd. Extend `ciu/run` with `{host_id, boot_id, pid, process_start_ticks}` and an atomic `complete` marker written only after artifacts/verdict are durable; prune complete runs by marker and treat an owner alive only when the full tuple still matches.
- **Challenges a settled decision:** no. Directory `flock` remains the local canonical key; this limits where a kernel capability can truthfully travel.

### T4-10 — The generated-surface rule and enrollment carves do not define a testable public contract

- **Severity:** MAJOR
- **Where:** S3.8.4–S3.8.6, S7.2.4, S18/S18.4; [enroll both] proposal §§3–6, §8 O1–O6; [enroll v7] S14.3/S14.4c/S14.7, proposal N23; V8-29; cmru KI-24; repository `AGENTS.md` “User-facing docs are part of the change”
- **Claim:** The mechanism meant to prevent surface drift is itself outside the closed surface, and the carve/oracles omit incompatible inventory syntax plus the required user documentation and conformance tests.
- **Evidence:** S3.8.6 says the implementation emits `ciu version --surfaces --json`; S18 declares only `ciu version`, and S3.8.5 says every CLI flag comes from the one closed definition. No `ciu/surfaces` JSON fields/version or document markers identify exactly which prose spans a test must parse, so two implementers can emit different valid-looking sets. Enrollment exposes a second unresolved grammar: proposal §5 writes `known_host = "<algo> <base64>"` but parenthetically calls it the `[ADDR]:N` form. V7 S14.4c says the **entry** must contain `[host]:port`, while the current `transport_ssh._known_hosts_file` prepends `ssh_host` or `[ssh_host]:port` to the configured value; following both yields two host tokens and a failed pin. The proposal includes `--global` in its general forms while normative v7 S14.7a/S14.7c omit it. O6 cannot currently run for CIU at all: cmru requires `[project.installer]`, but `ciu/cmru.toml` has none; neither V8-29 nor N23 names the CIU cmru/get.py packaging inputs and disjoint install root in its file list. V8-29 otherwise names only `cli.py`, `hosts.py`, and the cmru template; N23/KI-24 likewise omit CIU's README, `docs/DESIGN-GUIDE`, `docs/CONSUMERS.md`, parseable examples, closed-vocabulary coverage, and anchor checks required by the binding repository doctrine for a new public verb/flags/config row. O1–O6 cannot make those omissions red. Proposal §5.4 also promises “every existing verb works” while §5.3 and §10 deliberately leave `fqdn`, `addresses`, `bundle_dir`, `docker_optional`, and `[activate]` manual.
- **Failure scenario:** One v7 implementer stores the key-only value the current transport expects; another follows S14.4c and stores the full known-hosts line. The latter's transport writes `[host]:2222 [host]:2222 ssh-ed25519 ...` and every connection is refused, although both implementations pass O1–O6. In v8, the conformance test invokes `version --surfaces` but the generated CLI rejects the unknown flag—or the test hand-parses a changed sentence and silently misses a new reason. The code then ships with no adoption example, so an operator reasonably treats enrollment as complete, runs `activate`, and receives an unexplained refusal for a deliberately unfilled host field.
- **Proposed fix:** Add `--surfaces` to the declarative CLI definition and specify one versioned JSON schema containing command/flag pairs, artifact APIs, environment inputs, states/reasons, and result fields; generate the normative enumeration blocks from it or delimit them with machine-stable markers rather than parsing unconstrained prose. Define `known_host` once—prefer key type/base64 only, with the transport constructing the host token—and add non-default-port acceptance against a live OpenSSH server. Decide and normatively declare v7 `--global`. Add the CIU `[project.installer]`, package/bundle inputs, committed `get.py`, asset checks, and a tool-install root statically disjoint from every valid deployment `bundle_dir` to both carve contracts. Expand V8-29, N23, and KI-24 file/oracle lists to include README WHAT, DESIGN-GUIDE WHY, CONSUMERS HOW, loader-parsed current-schema examples, every public vocabulary, and anchor resolution in both affected products. Correct §5.4 to promise only authenticated `ciu ssh` plus an exact installed-CIU proof until the explicitly manual topology/activation fields are supplied.
- **Challenges a settled decision:** no. It enforces the repository's existing documentation and single-definition decisions.

## 4 Fixture and demo re-verification

### Mechanical fixture checks

- All **44** checked-in `*.toml` files under `v8-dstdns-demo/` parse with Python's TOML 1.0 parser; there were zero parse failures.
- The root policy source `examples/monorepo/ciu.toml` has SHA-256 `446f6624d0ac4deef1497e1aa671669d7559142a1df356bbe176ca8eaa357c4b` in this checkout.
- `appy/tester/../../../tester-unified` canonicalizes to `v8-dstdns-demo/examples/tester-unified`, which does not exist. `appy/tester/../../tester-unified` canonicalizes to the checked-in sibling `examples/monorepo/tester-unified`.
- Direct `git check-ignore` probes found the fixture's required `ciu.resolved.toml`, `ciu.instance.toml`, `ciu.hosts.toml`, and evidence paths unignored. This is an S2.3.1 refusal, not merely missing polish.

### Effective child configurations and flattening

`libx` otherwise has the intended semantic result: its nearest root is `libx`; it explicitly inherits the root's governance, `testing.cgroup_slice`, `testing.environments.tester-unified`, and `testing.judge`; its one command lane supplies `memory_max`; and the inherited judge floor is INFO/unused because it has no assay lane (S1.5, S3.1.5, S16.3, S16.6.1). It refuses nothing at the model layer. It does refuse the mandatory stage-1 ignore check described above.

`appy` has the same inherited policy and adds one local exec environment, one command lane, one assay lane, and a local tester Realization. Its `location = "tester"` stays within the child root, as S5.4 requires. With the corrected context, the build source stays within the containing worktree; as checked in, it resolves outside the fixture to a nonexistent directory and fails S6.2/S15.3. The fixture deliberately lacks `assay.toml` and initialized instance files, so it cannot actually execute the assay lane or `push`; proposal §4.10 already records the non-executable-fixture gap and I do not count that known gap again.

For a push of `appy` from this checkout, S3.1.5 requires `ciu.inherited.toml` to parse equivalently to the following effective content (serialization/order may differ, values may not):

```toml
[ciu.inherited]
sources = [{ path = "ciu/docs/v8-dstdns-demo/examples/monorepo/ciu.toml", sha256 = "446f6624d0ac4deef1497e1aa671669d7559142a1df356bbe176ca8eaa357c4b" }]

[governance]
enabled = true
cgroup_parent = "dev-background.slice"
memory_max = "2G"
memory_swap_max = "0"
cpu_weight = 100

[testing]
cgroup_slice = "dev-background.slice"

[testing.judge]
version = ">=4.1"

[testing.environments.tester-unified]
mode = "ephemeral"
image = "tester-unified:local"
forward_env = ["CGROUP_PARENT_DEV_BACKGROUND"]
```

The root's `[project]` is not inherited; neither are any lanes. TOML parses `[ciu.inherited]` as the dotted/nested table path `ciu.inherited`, which is explicitly listed by S3.3. The release uses the flattened tables instead of reopening `../ciu.toml`. The corrected build context itself does not travel under S17.3.1; the project-built image travels by the selected digest/archive mode.

The containing-worktree boundary is coherent for an ordinary checkout and for a linked worktree whose complete monorepo is present: `git rev-parse --show-toplevel` returns that worktree's own top. A symlinked entry canonicalizes back to the physical worktree before the containment test; I verified that shape locally. A Git submodule is deliberately a different containing worktree, so an `appy` submodule cannot reach a Dockerfile in its parent's sibling directory. A non-Git child falls back to its checkout root and likewise cannot use the parent sibling. Thus the claim is not “the sibling works under every Git shape”; it works only when root, child, and sibling are members of the same containing worktree, which is the literal S3.1.5/S6.2 policy.

### Dstdns changed-file audit

- `ciu.instance.generated.toml` and `examples/ciu.instance.generated.joined.toml` now contain instance/build facts only; `ciu.host.toml` contains `[ciu.host]`; and the realness comment points to `ciu.instance.json`. That is consistent with S2.6, S9.4, and S14.2. The stale S2.3.4 phrase “realness records in the generated file” should be corrected but does not change the generated examples.
- The merged-input block in `examples/ciu.resolved.toml.example` correctly shows host facts from the state root and `repo_root`/`physical_repo_root` as derived. Its receipt comment now validates provider host `P` against the activation-manifest entry for `P`. Replaying byte-identical evidence filed as another host `Q` fails the `host == P` test; replaying an older receipt fails `activation_id`; using P's digest/selection for the consumer is no longer required. T3-01's central defect is fixed.
- `ciu.hosts.toml`'s prose says bootstrap is an optional prerequisite hook, but all three real host rows still set `bootstrap = "ciu instance init --host ... && ciu check --layout prod3"`. S17.4.1 runs bootstrap in `<bundle_dir>` where no release is needed, then CIU itself runs those operations as `prepare` in the selected release. The demo therefore duplicates preparation in the wrong working directory and can fail before candidate selection. Remove these bootstrap values unless there is a genuine bundle-level prerequisite. The adjacent comment also says activation commands run in the candidate although bootstrap does not.
- The demo's `known_host` examples include a hostname token (`host ssh-ed25519 ...`), while S7.2.4's enrollment row says key-only and only parenthetically mentions the non-default-port host form. They expose T4-10's grammar mismatch rather than proving either representation.
- No topology declaration changed in draft.6/draft.7. Reapplying S8.4.1 retains five waves covering all 22 Realizations exactly once:

```text
0: cadvisor, github_runner, github_runner_webhook, otel_aggregator,
   otel_collector_node, registry_lightweight, tailscale_node, vault,
   webapp_ui_react, webhook_listener
1: consul_server, db_core, redis_core, reverse_proxy, skywalking
2: authentik, db_init, docker_stats_exporter
3: controller, webapp_server, worker_db
4: worker_io
```

Reapplying S6.3.2 also retains 24 collision-free TCP socket claims:

| host | bind | ports |
|---|---|---|
| gstammtisch | `100.64.0.11` | 5432, 6379, 8200, 8500, 9000, 9010 |
| rs1002 | `0.0.0.0` | 4317, 4318, 4319, 4320, 8080, 8888, 8889, 9558, 13133, 13134 |
| rs1002 | `100.64.0.12` | 8081, 8082, 8083, 11800 |
| tsstammtisch | `0.0.0.0` | 443, 5443, 9000, 9001 |

No wildcard/specific pair on rs1002 shares a port. `[resolved.gates.1]` remains correctly empty; gate 3 requires `controller.controller` healthy plus the internal DLQ Vault fact, with no completion and `probes = []`/`assumed = []`, consistent with the S8.5.5 partition. No host-network endpoint is present, so S8.5.2a's new helper path remains unexercised.

The enrollment proposal itself is clear about what remains manual in §5.3/§10: `fqdn`, `addresses`, `bundle_dir`, `docker_optional`, and `[activate]` (plus v7 `admin`/`secrets`). The sentence in §5.4 that all existing verbs, including v8 push/activate, work immediately is therefore too broad. The only unconditional postcondition is a pinned SSH row and a proved CIU installation; topology- or activation-dependent verbs work only after their existing required fields are supplied.

## 5 Not verified

- There is no CIU v8 implementation and `ciu/get.py` does not yet exist. I could not run the generated S3.8.5/S3.8.6 suite, an actual v8 `ciu check`, a release build/push, activation crash recovery, receipt transport, rollback, host enrollment, or the V8-29/N23/KI-24 gates. The fixture observations are parser/path/gitignore checks against design inputs, not an implementation certification.
- I did not mutate a live SSH account or run the root installer in a target VM. T4-08 derives from the proposal's unrestricted filesystem operations and a hostile pre-existing user; it should be confirmed with disposable symlink/hardlink/FIFO/concurrency fixtures before implementation acceptance.
- I did not run a live `docker image prune -a`/rollback sequence. T4-02 relies on Docker's documented prune reachability rule; an implementation may preserve images by another explicit mechanism, but draft.7 specifies none.
- I did not test a real sudoers policy, SSH server, or Docker daemon for descriptor passage. A local subprocess experiment confirmed that the descriptor disappears under Python's default close-fd behavior and survives with explicit `pass_fds`; T4-09's cross-kernel conclusion is architectural, not a claim about one wrapper version.
- I did not create an actual Git submodule fixture. The submodule result in section 4 follows the normative command's own boundary: `git rev-parse --show-toplevel` inside a submodule returns the submodule worktree, not its superproject.
- I did not promote response §5–§7.3 or proposal §4.10's recorded open items into new findings. Where they are mentioned (the hand-started flow and non-executable fixture), the finding is the additional normative contradiction or checked-in failure, not the already recorded limitation.
- **ASSUMPTION (bundle reuse):** Independent projects/controllers may leave `bundle_dir` at its documented default on the same host. No rule reserves it or detects a different owner; if one bundle per physical host is an unstated deployment prerequisite, it must become a checked rule and the default remains hazardous for the second project.
- **ASSUMPTION (installer root):** The prompt's `/opt/ciu/current/` case corresponds to cmru `install_dir_system = "/opt/ciu"`. The value has not been added to `ciu/cmru.toml`; choosing any other root avoids the literal tool/deployment collision but must be normative and mechanically checked as disjoint.
- **ASSUMPTION (installed launcher):** A CIU-specific cmru render does not add an external PATH launcher absent from the current template and proposal. If packaging creates one, its exact path and ownership still need to be part of the enrollment proof.
- **ASSUMPTION (hostile existing user):** A user that root is asked merely to “confirm” may control its current home contents. If enrollment is intentionally restricted to a newly created, previously nonexistent user, the command must refuse existing users rather than say they are left untouched.
- **ASSUMPTION (shared evidence):** `evidence_dir` may reside on storage visible to more than one host; the spec does not forbid it. PID reuse on one host is independently sufficient for T4-09 even if shared evidence is prohibited.

## 6 Machine summary

```json
{
  "findings": [
    {
      "id": "T4-01",
      "severity": "BLOCKER",
      "where": ["S2.6.1", "S2.6.2", "S2.6.3", "S7.2", "S14.4.1", "S14.4.7", "S14.4.9", "S17.4.1", "[enroll both] proposal O6/v7 S14.3/cmru S6.4"],
      "claim": "Remote mutable state is not scoped to an instance and release-local directory inodes do not serialize two releases operating on that instance.",
      "regression": true,
      "challenges_settled": true
    },
    {
      "id": "T4-02",
      "severity": "BLOCKER",
      "where": ["S2.1", "S2.6.2", "S14.3", "S17.3.3", "S17.3.5", "S17.3.6", "S17.4.1", "S18"],
      "claim": "Activation does not pin candidate and image availability into a durable, crash-recoverable current/previous transaction.",
      "regression": true,
      "challenges_settled": false
    },
    {
      "id": "T4-03",
      "severity": "BLOCKER",
      "where": ["S2.6.1", "S4.1.2", "S4.1.4", "S7.2", "S8.5.2a", "S17.4.1", "S18.3"],
      "claim": "Target preparation's sentinel mutates an immutable release and requires Docker on docker-optional hosts, while helper containers have no governed launch contract.",
      "regression": true,
      "challenges_settled": false
    },
    {
      "id": "T4-04",
      "severity": "BLOCKER",
      "where": ["S2.3.1", "S3.1.5", "S6.2", "S15.3", "examples/monorepo/appy/tester/ciu.stack.toml"],
      "claim": "The monorepo context is accepted by S6.2 and refused by stage 4, while the fixture's literal path misses the sibling and its required outputs are not ignored.",
      "regression": true,
      "challenges_settled": false
    },
    {
      "id": "T4-05",
      "severity": "BLOCKER",
      "where": ["[enroll both] proposal §§2-5/O1/O4/O6", "v8 S7.2.4", "v8 S17.4.1", "v7 S14.7", "cmru templates/get.py.tmpl"],
      "claim": "The enrollment URL pins only the installer script; neither target installation nor the bare CIU executable is pinned and compared with the controller version.",
      "regression": true,
      "challenges_settled": false
    },
    {
      "id": "T4-06",
      "severity": "BLOCKER",
      "where": ["[enroll v7] S14.3a/S14.7", "[enroll v8] S2.6.2/S7.2.4/S17.1", "proposal §§3/5/6"],
      "claim": "Replacement destroys the active key before proof, v7 aliases the host-secret store, v8 global rows depend on checkout keys, and writers have no transaction lock.",
      "regression": true,
      "challenges_settled": false
    },
    {
      "id": "T4-07",
      "severity": "MAJOR",
      "where": ["[enroll both] proposal §§3.3/4/5.1/7/O1/O3/O4", "v8 S7.2.4", "v7 S14.7d", "cmru templates/get.py.tmpl"],
      "claim": "The root installer program is unauthenticated before execution and optional scan confirmation supplies no independent host-key channel.",
      "regression": true,
      "challenges_settled": true
    },
    {
      "id": "T4-08",
      "severity": "MAJOR",
      "where": ["[enroll both] proposal §§3-4/7", "v8 S7.2.4", "v7 S14.7b", "KI-24"],
      "claim": "Root's authorized_keys update can follow hostile filesystem objects or mis-serialize from= text and has no atomic concurrent transaction.",
      "regression": true,
      "challenges_settled": false
    },
    {
      "id": "T4-09",
      "severity": "MAJOR",
      "where": ["S14.4.8", "S14.4.9", "S16.9.4", "S18.2"],
      "claim": "Lease descriptors cannot cross sudo, SSH, or container process boundaries by environment number, and run pruning lacks a safe owner/completion identity.",
      "regression": true,
      "challenges_settled": false
    },
    {
      "id": "T4-10",
      "severity": "MAJOR",
      "where": ["S3.8.4", "S3.8.5", "S3.8.6", "S18", "[enroll both] proposal O1-O6", "V8-29", "N23", "KI-24"],
      "claim": "The surfaces emitter is absent from the closed CLI/schema, inventory syntax conflicts, and the enrollment carves omit binding public-document and conformance work.",
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
