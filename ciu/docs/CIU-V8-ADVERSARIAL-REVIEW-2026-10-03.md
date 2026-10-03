# CIU v8 specification set: adversarial review, 2026-10-03

**Reviewed:** `SPEC-V8.md` 8.0.0-draft.8, `CIU-V8-TESTING-GATE-PROPOSAL.md` revision 4.4 (Appendix R, §4.1.4, §4.1.7a, §4.1.9, §4.1.10a, §4.3.17, §4.7 X96–X118, §4.9, §4.10) and `V8-REALIZATION-GRAPH.md` revision 3. These are vbpub c175e3d23..06e2142d8, local and not pushed.
**Checked against:**
- dstdns `nyxloom-trove/decisions.md` D-646, D-647 and D-651..D-657.
- ciu 7.15.1 source: `src/ciu/workspace.py`, `src/ciu/workspace_env.py`, `src/ciu/worktree.py`, `libraries/worktree/src/worktree/core.py` and its `SPEC.md`. Also `ciu/CHANGES.md` and backlog entries CIU-116 and CIU-119.
- run-gate rev 46: `SPEC.md` R-39/R-39e and backlog entries RG-67, RG-73..RG-78.
- assay `src/assay/errors.py` (the `Outcome` enum).
- cgroup-profiler: `lib/summary.py`, backlog `INDEX.md` and CP-15.
- cli-extended `BACKLOG.md`.
- dstdns's own `ciu.instance.generated.toml`, used as a live witness.

**Reviewer:** a fresh blind session. Phase 1 was written before `CIU-V8-DECISION-COVERAGE-2026-10.md` was opened; phase 2 (§3) reconciles against it.
**Severity scale:** the same as `CIU-V8-ADVERSARIAL-REVIEW-2026-09-02.md`.
- **BLOCKER:** a contradiction with shipped reality or a decision, or an unimplementable rule.
- **HIGH:** a design defect with a concrete failure.
- **MEDIUM:** an inconsistency or gap that an implementer would hit.
- **LOW:** wording, citations, or leftovers.

The task's MAJOR maps to HIGH. **OPERATOR** marks a fix that needs an operator ruling; each such finding carries a closed question.

---

## 1 Verdict: **NOT-READY** for sign-off as the ciu8 acceptance reference

**What holds.** The decision record is carried in substance almost everywhere:
- one gate implementation;
- explicit modes;
- the closed five-verdict table;
- hermetic lanes without an instance;
- committed join presets with a closed tenant vocabulary;
- one `[testing.judge]`;
- the atomic per-repo cutover;
- cmru calling `ciu gate`;
- skills in the wheel;
- the owner token gone, with `protected` in its place;
- warm-set charges with `memory_max` as the unmeasured charge and the ceiling;
- Docker-object reservations with no lock file;
- the no-daemon state as configured policy.

The graph (S5, S8) is untouched by the reconciliation and is still sound.

**Why it is not ready.** There are three BLOCKERs:
- **V8R-01 and V8R-02** are shipped-reality errors in the two rules the cutover's identity neutrality rests on. The id is derived from the wrong path. The "instance lock" adopted from 7.15 is in fact a family-wide lock. Both can be fixed from the decisions, without the operator.
- **V8R-03:** the stack half of the D-655 reservation protocol cannot be implemented as worded for a multi-wave deploy set. This needs an operator ruling.

Five HIGH findings remain:
- two concern the core correctness claim of D-655 ("every ciu that agrees on the order makes the same decision, across devcontainers and users"): the visibility race (V8R-04) and owner proof across PID namespaces (V8R-05);
- one is where the capacity lives (V8R-06);
- one is tenant semantics (V8R-07);
- one is the unfolded round-4 blockers (V8R-08).

**Count:** 34 findings: **BLOCKER 3, HIGH 5, MEDIUM 14, LOW 12.**
Six findings need the operator: V8R-03, V8R-04, V8R-06, V8R-08 and V8R-09, plus one acceptance-scope question inside V8R-08. They are listed in §4. Everything else can be fixed by the writer from the decisions and the shipped code.

---

## 2 Findings

### 2.1 Summary table

| id | sev | location | finding (one line) | operator |
|---|---|---|---|---|
| V8R-01 | BLOCKER | SPEC S4.1.1, S4.1.4, S14.8.2; proposal §4.1.4, R S4.1.1 | the id is derived from the lexical checkout path, but 7.15.1 hashes the **physical** (daemon-visible) path after resolving symlinks; the cutover is not identity-neutral | — |
| V8R-02 | BLOCKER | SPEC S14.4.1, S14.4.3, S9.5.4, S14.4.7; proposal §4.1.9 | 7.15's root lock is keyed by the root **offset** only, so it is family-wide, not per instance. Every worktree serializes on it, and a join to the primary deadlocks on itself | — |
| V8R-03 | BLOCKER | SPEC S14.1.2, S16.6.1(1); O-3, O-13 | "the deploy set's containers from `compose create`" cannot be created before the earlier waves run. Per-wave reservations hold and wait, and interleaved per-object order can stall two stacks that would each fit alone | **OPERATOR** |
| V8R-04 | HIGH | SPEC S16.6.1 (2)–(3) | creation-time order is not visibility order: a reservation with an earlier `Created` can be invisible to a later fit check, so both start. Volumes have no object id for the tie-break | **OPERATOR** |
| V8R-05 | HIGH | SPEC S4.5.1, S16.9.4, S16.9.6, S14.6.2, S16.12 | the owner tuples omit `pid_ns` (and differ from each other). A reaper in another devcontainer reads a live owner as dead (R-39e's hijack). Abandoned reservations from a vanished namespace can never be proven dead | — |
| V8R-06 | HIGH | SPEC S2.7.1, S7.2, S17.1; D-652 Q6, D-655 | capacity lives in a per-checkout or per-user file, so ciu runs on one daemon can disagree on `usable` and `no_daemon`, or on whether to reserve at all | **OPERATOR** |
| V8R-07 | HIGH | SPEC S5.2.5, S9.5.8, S10.1.5, S6.10, S14.1.4; O-10 | tenants have no hook phase and no lock, no secret-path prefixing (a joiner's `generate store=vault` reads or overwrites the primary's value), no delivery of the tenant name, and clean semantics that delete data | — |
| V8R-08 | HIGH | SPEC D.8, S4.1.4, S7.2.4, S17.4; proposal §4.10 item 28 | the acceptance reference still carries round 4's unfolded findings, including blockers, and a rule (S4.1.4's sentinel bind) that round 4 showed cannot be carried out | **OPERATOR** |
| V8R-09 | MEDIUM | SPEC S16.6.9, S2.7.2, S14.6 | after D-655 the byte budget no longer needs the daemon, yet its absence switches to a stack/lane **count** (what D-647 #4 rejected). Two processes that differ on daemon visibility apply different policies to one order | **OPERATOR** |
| V8R-10 | MEDIUM | SPEC S16.6.1(1), S16.6.6, S16.4.7 | an `exec` lane has no reservation object, and a lane service's memory is charged nowhere | — |
| V8R-11 | MEDIUM | SPEC S16.6.1, S14.1.3, S18 (`dev`) | which object states count is undefined: `down` stops but keeps containers and exited one-shots stay, so capacity leaks. `ciu dev` starts containers without admission | — |
| V8R-12 | MEDIUM | SPEC S16.6.1 (3)–(4), S16.6.9 | a head reservation that alone exceeds `usable` blocks the tier for the whole admission wait. PSI is read per process, so "same order, same decision" fails. Daemon readings have no freshness bound | — |
| V8R-13 | MEDIUM | SPEC S16.2.1, S2.7.2, S13.2, S18.2; I1, I2, I5; O-19 | the gate slice can be declared in four places and the stack slice in two, with no consistency check for stacks. Hardwired mdt variables are configuration sources that S18.2 says do not exist | — |
| V8R-14 | MEDIUM | SPEC S16.7.3, S16.8.2 | assay has six outcomes; `NO_MEASUREMENT` and `INCONCLUSIVE` have no verdict and no exit code | — |
| V8R-15 | MEDIUM | SPEC S16.8, S16.10, S16.7.4, S16.3.2–S16.3.3, S14.4.4; O-5 | the closed exit table is not closed over: multi-lane runs, `gate exec`, sub-verbs, lock contention, base refusals (RG-74 says 2, RG-78 says 3), and `judge-provenance` as both NOT_RUN and ERROR | — |
| V8R-16 | MEDIUM | SPEC S16.1.1, S16.12, R S16.8; run-gate RG-67/73/75/76/78 | several filed oracles that are declared to be "the port's parity tests" cannot pass against v8 as written | — |
| V8R-17 | MEDIUM | SPEC S16.11.6, S16.4 (`image_from`), S17.6.1; D-651 Q8 | in a linked worktree, `image_from` names `<tag>-<instance_id>`, which exists only after `ciu build`, a mutating verb that needs `instance init` | — |
| V8R-18 | MEDIUM | SPEC S14.1.5, S4.1.1, S4.5.4 | `ciu clean --identity` deletes by `ciu.instance` alone, bypassing the ownership check D-651 Q10 keeps. Collisions are detected only inside one family's records | — |
| V8R-19 | MEDIUM | SPEC S16.6.6; O-2; D-651 Q5, D-653 Q4 | the measured-peak charge is not covered by any decision, and the decisions already answer it: no warm figure means unmeasured, so `memory_max` | — |
| V8R-20 | MEDIUM | SPEC Appendix A step 8, S3.2.6, S2.3.2; proposal §4.4 (`ciu8`) | the "per repo, atomic, while ciu8 is a separate console script" gate cutover is impossible in a repo whose stacks are still on v7 files, and the pointers are rewritten to a command (`ciu gate`) that does not exist during the window | — |
| V8R-21 | MEDIUM | SPEC S16.11.2, S18.3 vs S16.6.1(1) | a host-mode lane under admission needs Docker (its reservation is a volume), but the spec says Docker is needed only for `ephemeral` and `exec` | — |
| V8R-22 | MEDIUM | SPEC S13.2.1; O-21 | the `memory_min` floor admission is a separate decide-then-start step outside the reservation order (a TOCTOU), and it reads a slice the devcontainer cannot see | — |
| V8R-23 | LOW | SPEC S14.1.1, S14.7.1–S14.7.2, S4.1.1 | the primary checkout has no `libraries/worktree` record in 7.15, yet the spec writes one and relies on one | — |
| V8R-24 | LOW | SPEC S2.1, S2.6.2 vs S16.7.2; O-18 | `ciu-gate-state/` is in the state root in one rule and in the git common dir in another | — |
| V8R-25 | LOW | SPEC S16.4.9, S6.2.4 | "mounts the git common directory … at the path that file names": the gitfile names `<common>/worktrees/<w>`, not the common dir | — |
| V8R-26 | LOW | SPEC S16.6.1, S2.7.3, S18.2 | admission orders on whatever Docker endpoint the client uses, but reads RAM, PSI and capacity from the local machine | — |
| V8R-27 | LOW | SPEC S2.7.4, S16.5, S16.6.4, S16.6.6, S15 stages 11/12 | when `memory_max` is required is stated three ways. "stays the hard ceiling" is false when `[governance] enabled = false` | — |
| V8R-28 | LOW | SPEC S16.9.5, S14.3 | `ciu.footprint.json` is rewritten by two gate-class verbs with no write serialization | — |
| V8R-29 | LOW | SPEC S16.3 (minimum 5.2.0) vs S16.7.2 (`--reuse-from`, `--rejudge`) | a floor-satisfying judge rejects the selective flags ciu forwards (assay ≥ 7.1, RG-66) | — |
| V8R-30 | LOW | SPEC S18 (`skills` row); proposal §4.4 V8-33, source list | CLI-EXT-02 was renumbered to **CLI-EXT-05** at merge 583694b88; CLI-EXT-02 is now another item | — |
| V8R-31 | LOW | SPEC S4.1.1, S4.1.2, S4.5.4, S6.3.2 vs Appendix B | literal ids in refusal examples, against the spec's own "`<instance_id>` in every example" rule | — |
| V8R-32 | LOW | SPEC S14.3, S4.1.1; proposal §4.10 items 15, 38 | dangling or wrong cross-references | — |
| V8R-33 | LOW | proposal R.3, §4.10 item 35; cgprofile backlog | the combined `damon.warm_set_bytes` series the charge needs is filed nowhere upstream | — |
| V8R-34 | LOW | `V8-REALIZATION-GRAPH.md` "Still open" | the body still lists contract conformance and `pg:schema/*` as "not planned anywhere", which its own preface and draft.8 contradict | — |

### 2.2 Details

#### V8R-01: BLOCKER. The instance id is derived from the wrong path

- **Where:**
  - SPEC S4.1.1: "`sha256(lexically canonical absolute path of the checkout) mod 36^6`, where the path is normalized for `.` and `..` but never resolved through symlinks".
  - SPEC S4.1.4: `physical_repo_root` is "used for bind-mount sources and mount proofs (S16.4.3), **never for the instance id** (S4.1.1)".
  - Proposal §4.1.4 and Appendix R S4.1.1: "base36, lexical path".
  - The coverage memo, O-8: "dstdns's id is derived for `/workspaces/dstdns`, the container path."
- **Shipped reality (7.15.1):**
  - `workspace_env.py:1578-1581`:
    ```python
    physical_git_root = _detect_physical_repo_root(git_root)
    workspace_id = shared.workspace_id_for_path(physical_git_root)
    root_instance_id = shared.workspace_id_for_path(physical_root)
    ```
    Here `repo_root = repo_root.resolve()` (symlinks resolved) at `:1562`.
  - `workspace.py` `context_for_root` hashes `physical_path(git_root, …)`.
  - `libraries/worktree` `create_workspace` hashes `physical_worktree_path` (or an explicit `identity_path`), `core.py:850-864`.
  - The library function is "lexically canonical" only in that it normalizes its *input* without probing the filesystem. Its caller passes the physical path.
- **Live witness (reproducible, no literal id needed).**
  - The id recorded in dstdns's `ciu.instance.generated.toml` equals `base36(sha256(physical_repo_root) mod 36^6)` for the `physical_repo_root` recorded in the same file.
  - It does **not** equal the same function over `/workspaces/dstdns`.
  - Check with `python3 -c 'import hashlib;…'` over both strings.
  - CIU-119's oracle 1 ("a v8 `instance init` in a checkout whose v7.15 record says X derives X") therefore **fails** under the text, in every devcontainer.
- **Also contradicts the spec itself.** S14.8.2: "the id derived from this checkout's proven physical path (S4.1.1)".
- **Consequence:** Appendix A step 1 ("identity-neutral … no instance changes id") is false for every devcontainer checkout. Every container, network and named volume would be orphaned at the cutover, which is the D-645 incident class.
- **Fix (no operator needed: D-647 #5 says "7.15's derivation is final"):**
  - S4.1.1: the id is `workspace_id_for_path` over the **physical (daemon-visible) path of the git top level**, after the logical path is symlink-resolved in this namespace and translated through the physical-root mapping. A ciu root below the top level composes `<that id>-<id of the root's physical path>`, and the suffix is omitted when the two are equal (`root_identity_suffix`).
  - S4.1.4: delete "never for the instance id". State that the physical root is established **before** the id is derived. State the fallback 7.15 uses when the namespace is unprovable: the logical path stands in, as `physical_path` passes it through.
  - `ciu.checkout` (S4.5.1) is "the physical path the id was derived from" (draft.7's wording).
  - Make the same correction in proposal §4.1.4, Appendix R and the coverage memo's O-8.
  - State the consequence: a remount of the same tree at another host path is a move.

#### V8R-02: BLOCKER. The adopted "instance lock" is a family-wide lock

- **Where:**
  - SPEC S14.4.1(b): "The **instance lock** is 7.15's root lock: a `flock` on `<git-common-dir>/.workspace-instances/ciu-root-offset-<key>.lock`, keyed by the ciu root's offset in the checkout".
  - S14.4.3: own instance exclusive, then joined references shared, "in ascending `instance_id` order".
  - S9.5.4: "the reference's instance lock shared (its root-lock file, S14.4.1)".
  - S14.3: the gate holds it shared "for the duration of the lanes".
- **Shipped reality:** `workspace.py:41-62`:
  ```python
  offset_key = hashlib.sha256(str(context.ciu_root_offset).encode("utf-8")).hexdigest()[:16]
  lock_path = context.workspace.git_common_dir / shared.WORKSPACE_RECORD_DIR / f"ciu-root-offset-{offset_key}.lock"
  ```
  The offset is `root.relative_to(git_root)`, which is `.` for the root of **every** worktree, and the git common dir is shared by the family. So every worktree of dstdns resolves the same file. In 7.15 this lock serializes generated-facts allocation for a root offset (`worktree.py:3863-3868`), which is a family-level operation. It is not a per-instance mutex.
- **Failure scenarios:**
  1. A two-hour mutation lane in worktree A holds the lock shared. Every `ciu up`, `down`, `clean` and `render` in every other worktree and in the primary blocks for two hours, or fails fast. That is the opposite of D-647 #2's "worktrees own their test environment".
  2. A worktree that joins the primary (the default-preset case) runs `up` and takes LOCK_EX on the file. It then opens the "reference's" lock, the **same file**, and asks for LOCK_SH. flock locks belong to open file descriptions, so the second request blocks on the first forever. S14.4.3's ordering argument assumes two distinct locks.
  3. S14.4.7 tells third parties to lock "the root-lock file" as the instance key. A `ciu lease acquire --shared` in one worktree therefore stalls all of them.
- **Fix (no operator needed: X99's intent was "adopt the shipped lock"; no per-instance lock ships, so one must be named):**
  - The instance lock is a flock on `<git-common-dir>/.workspace-instances/ciu-instance-<instance_id>.lock`, keyed by the full composed id. The state-root fallback stays as it is.
  - The 7.15 offset lock keeps its own role: allocating a root's generated facts across the family, taken with the family lock.
  - S14.4.1's sentence "ciu adds none beside them" must change to name the new file.
  - The library's "no second lifecycle or lease authority" rule (`libraries/worktree/SPEC.md` "an adapter … must not implement a second generic Git lifecycle or lease authority") does not forbid a per-instance mutex.
  - Correct proposal §4.1.9, Appendix R S14.4.1 and memo §3.5 to match.

#### V8R-03: BLOCKER, OPERATOR. The stack reservation via `compose create` cannot be implemented as worded

- **Where:**
  - SPEC S14.1.2: "the deploy set's containers are created first, as labelled reservations, and started only when their charge fits".
  - S16.6.1(1): "for a stack, the deploy set's containers from `compose create`".
  - D-655: "the stack's containers from `compose create`".
- **Why it cannot be implemented:**
  - A container's environment, mounts and image are fixed when it is created.
  - For wave k+1, S8.7 runs `pre_secrets` hooks (step 2), materializes secrets, including `from = "vault"` values fetched from a Vault that wave 0 brings up (step 3), runs `pre_compose` hooks whose `state` feeds the render (step 4), and then renders compose (step 6).
  - None of that exists before waves ≤ k are healthy. For dstdns's five-wave graph (`V8-REALIZATION-GRAPH.md` trace), the later waves' containers cannot be created up front.
  - The proposal knows the question is open (§4.10 item 37, "how `compose create` reserves a whole deploy set at once"; memo O-3, O-13), but treats it as a carve detail. It is not a detail: no carve can satisfy the text.
- **The two literal readings both fail:**
  - **Reserve per wave.** Stacks A and B each get wave 0 admitted and started. Both then wait for wave 1 capacity that the other's running wave 0 holds. This is hold-and-wait, which ends only when `--admission-wait` expires, and it leaves two half-deployed stacks.
  - **Per-object order with whole-set creation (if it were possible).** Two concurrent `compose create` runs interleave their containers in creation order. The fit check is per object ("up to and including its own"), so A's last container's prefix includes part of B, and B's includes all of A. When A+B > usable but A alone fits, **both** can wait until expiry. No rule says which of a stack's objects is "its own reservation".
- **Fix: closed question to the operator.** How is a stack start reserved?
  - **(a) Recommended.** One labelled placeholder object per admitted `up`: a volume, D-655's own host-lane form. It carries the whole deploy set's charge, is created before wave 0 and is removed by `down` or `clean`. Containers created wave by wave carry `ciu.reservation.group=<placeholder>` and are not counted separately.
  - **(b)** Keep per-container reservations, but `ciu up` creates every container of every wave as a stub with dummy configuration, then recreates each wave's containers. Creation time then changes, so this needs (a)'s group label anyway.
  - **(c)** Reserve per wave and accept partial deploys. Add a rule that a stack waiting on wave k+1 releases its earlier waves (a `down`) at expiry.
  - Option (a) keeps D-655's medium (a labelled Docker object), its order and its "no lock file". It changes only "the stack's containers" to "one object standing for the stack's containers".

#### V8R-04: HIGH, OPERATOR. Creation-time order is not visibility order

- **Where:** S16.6.1:
  - (2): "lists every reservation of the tier … orders them by Docker's creation time, then by object id";
  - (3): "the cumulative charge … up to and including its own";
  - "Every ciu that reads the same order makes the same decision".
- **The race:**
  - Docker gives no guarantee that an object is listable at the instant its `Created` time is stamped. In moby the timestamp is taken when the container object is constructed, before the read-write layer, volume setup and registration in the store. Only then does `docker ps -a` show it.
  - B stamps `Created = t0`, and its create is still in flight. A stamps `Created = t1 > t0`, finishes, lists, does not see B, finds itself first, fits, and starts. B then lists, sees itself before A, sums only itself, fits, and starts.
  - The tier is over-committed. Both decisions were "correct" for the lists they saw. The conformance test S16.6.1 asks for ("concurrent runs agree") samples timing, so it cannot catch this window reliably.
- **Secondary defects:**
  - Host-lane reservations are **volumes**. Volumes have no object id (they are keyed by name), and their `CreatedAt` has one-second precision. They are mixed in one tier order with containers stamped in nanoseconds. The S16.6.1 tie-break is undefined for them.
- **Fix: closed question to the operator.** How does the order become race-free?
  - **(a)** A settle delay: decide only on a listing taken at least T after one's own `Created`. This is simple and probabilistic, with no bound Docker guarantees.
  - **(b) Recommended.** Gap-free tickets through Docker's atomic name reservation. Each reservation is a volume named `ciu-res-<tier>-<n>` with `n` = the highest visible + 1. On a name conflict (409), the creator **waits until that name is listed** and then retries with n+1. Ordering is by `n`. By induction, every lower ticket was visible before any higher one was created, so the fit check sees all of them. This also gives every reservation kind one object kind, which fixes the volume tie-break.
  - **(c)** Accept the race as a stated limit, with PSI and the kernel ceilings as the backstop.
  - Option (b) composes with V8R-03(a).

#### V8R-05: HIGH. Owner proof across PID namespaces, reap scope, and abandoned reservations

- **Where:**
  - S4.5.1: `ciu.reservation.owner` = "(instance, lane or realization, run id, pid, boot id)".
  - S16.9.4: `run.json` owner = `{ pid, host, boot_id, start_ticks }`.
  - S16.6.1 and S14.6.2: reap "once the `ciu.reservation.owner` tuple proves the owner gone".
  - S16.12: "Re-attach (R-39): S16.9.6".
- **Shipped reality (run-gate R-39a/R-39e):** the owner is `owner_pid + owner_start + boot_id + pid_ns`. In R-39e's words, "`boot_id` cannot catch this on its own: it is host-GLOBAL, so two clients in two containers … each read the other's live owner as dead". The rule is "**Another PID namespace → liveness UNKNOWN → treated as ALIVE**", and a live owner is followed, never hijacked.
- **Failures:**
  1. **Hijack.** A `ciu instance reap` in devcontainer B sees a reservation from devcontainer A. The pid is not in B's namespace and the boot id matches, so the owner is "proven gone". B removes a reservation, or a lane container, that a live client still owns. That is exactly the cross-devcontainer case D-655 claims to handle.
  2. **The inverse, once unknown means alive.** A client in a devcontainer that was deleted leaves a `created` reservation that **nothing can ever prove dead**. In a strict FIFO it holds its place, and its charge, forever.
  3. The two tuples differ: the label lacks start ticks, `pid_ns` and host. The port map claims R-39, but R-39e (follow, never hijack) is not carried.
  4. S14.6.2 lets `reap` remove *any* reservation object whose owner is gone, including a running lane container. That defeats S16.9.6's re-attach and loses the evidence R-39 exists to keep.
- **Fix (resolvable: R-39 is the ported behaviour; D-655 says "uses the owner tuple to prove the owner gone"):**
  - Use one owner tuple in both places: `{pid, start_ticks, boot_id, pid_ns, host}`. A different `pid_ns` means unknown, which means alive.
  - Add a label `ciu.reservation.deadline`:
    - for a `created` reservation, its creation time + its own `--admission-wait` + a grace period. The owner removes its own reservation at expiry (S16.6.1(4)), so a `created` object past its deadline is abandoned **by construction**, whoever reads it.
    - for a host-lane volume, its start + `budget` + grace. This makes `budget` mandatory for host lanes under admission.
  - `reap` removes only reservations past their deadline, plus objects whose owner is proven dead in this namespace. Running lane containers stay with re-attach.
  - CP-15's `--ttl` was the same idea and was dropped along with CP-15.

#### V8R-06: HIGH, OPERATOR. Capacity is not host-scoped, so participants can disagree

- **Where:**
  - S2.7.1: capacity is "in the **local host's row** of the host inventory … `[hosts.<h>.capacity]` in `ciu.hosts.toml` or in the user-global `~/.config/ciu/hosts.toml`".
  - S17.1: "`ciu.hosts.toml` in the checkout root, else … `~/.config/ciu/hosts.toml`; the first found is used entirely (no merging)".
  - S19.2: `ciu init --stack` writes a per-checkout `ciu.hosts.toml`.
  - S2.7.4: "Admission … is on for a tier exactly when `capacity.tiers.<t>` is declared".
- **Against:**
  - D-652 Q6: "host information … go into ciu (**host-scoped**) config".
  - D-655: "correct across devcontainers and users on one Docker daemon".
  - S16.6.1: "Every ciu that reads the same order makes the same decision".
- **Failure:**
  - The checkout-local file is gitignored, and every deployable project has one. Each checkout and each user therefore declares its own `usable` and `no_daemon`, or no capacity at all.
  - Two participants on one daemon then compute different fits over the same order. A participant without a `capacity` table never decides, and it is unclear whether it creates reservations at all. If it does not, its work is invisible to everyone else's order.
  - Agreement on the order is necessary but not sufficient. The capacity must be shared too.
- **Fix: closed question to the operator.** Where does the one capacity per Docker daemon live?
  - **(a) Recommended.** On the daemon itself: a labelled Docker object, for example a volume `ciu-capacity` whose labels carry the tiers. A ciu verb (`ciu host capacity set|show`) writes it, and every participant reads it. This is the one medium D-655 already established that all participants share. It needs no file, no mount and no mdt.
  - **(b)** Keep the inventory row, but every reservation carries the capacity it was decided under (`ciu.reservation.capacity`), and a participant whose capacity differs from an earlier reservation's refuses with an ERROR.
  - **(c)** Accept per-environment capacity. This is the proposal's rejected Q15 option B, so D-655's cross-environment claim is withdrawn.
  - Under any option: every ciu-started object carries reservation labels **whether or not this participant budgets**. When no capacity is declared, it still creates the reservation with `override` semantics.

#### V8R-07: HIGH. Tenant namespaces are named but not specified

- **Where:**
  - S5.2.5 and S9.5.8: "the provider's hook creates the namespace when the joiner is initialized, and removes it at the joiner's `ciu clean`".
  - S6.10: the closed hook phases are `pre_secrets | pre_compose | post_compose`.
  - S10.1.5: a vault `path` resolves to "a key of `[vault.paths]` … or a literal KV path", the same for every instance.
  - S10.1 `generate` with `store = "vault"`: "only when the Vault path is absent, then read".
  - Proposal §4.10 item 34 and memo O-10 acknowledge only the provisioning hook.
- **What an implementer hits on day one:**
  1. **No phase and no lock.** The "provider's hook" is not one of the closed phases. It runs at the *joiner's* `instance init`, a verb that holds no lock on the reference. It mutates the reference's postgres or vault while the reference may be running `clean --vanilla`.
  2. **Secrets are not prefixed.** A joiner's `from = "generate", store = "vault", path = "db/postgres/controller_password"` resolves to the same KV path as the primary's. Under "generate once, when absent, then read", the joiner silently **reads the primary's credential**, and two joiners share one. The tenant isolation that D-651 Q9 requires ("a per-instance mount or prefix") is defeated by S10's path rule.
  3. **No delivery.** A `pg-database` tenant's database and role names are derived from the joiner's id. Bindings deliver `host`, `port`, `url`, `path` and TLS only (S6.4, S7.8.1), so the joiner's application cannot learn its database name.
  4. **Clean semantics.** Plain `ciu clean` keeps the store and `ciu-data/` (S14.1.4), but S9.5.8 drops the tenant database at plain `clean`. That destroys exactly the data `clean` promises to keep. Removing a worktree without `clean` (`git worktree remove`) leaks the namespace forever.
- **Fix (resolvable within D-651 Q9):**
  - A `tenant` hook phase with `create` and `remove` entries. It runs with the reference's stack-directory lock for the shared Realization held exclusively, and it receives a context with `tenant`, `joiner_instance_id` and the share kind.
  - S10.1.5: for a `vault-prefix` or `vault-mount` tenant, every vault path a joiner resolves is prefixed with or mounted under its tenant.
  - The resolution gains a `tenant` field, delivered as `<prefix>_TENANT` and in the template.
  - Removal happens at `clean --vanilla`, `instance remove` and `reap`.
  - `ciu check` stage 12 refuses `share` on a service whose Realization declares no `tenant` hook.

#### V8R-08: HIGH, OPERATOR. The acceptance reference carries unfolded blockers

- **Where:**
  - SPEC D.8: "Not folded: T4-02 … T4-03 … T4-04 … T4-05..T4-08 …, the descriptor half of T4-09, T4-10's packaging".
  - D.9's last rows mark S4.1.4 and S14.4.7–S14.4.8 "carried unchanged … still to fold".
  - S4.1.4 still mandates the sentinel bind, which T4-03 showed is an "impossible and ungoverned helper-container contract".
  - The header defers open questions to `CIU-V8-DECISION-COVERAGE-2026-10.md` §5.
- **Why it matters for sign-off:**
  - V8-1..V8-37 name `SPEC-V8.md` as "the acceptance reference for every row" (proposal §4.4).
  - Signing it off makes round 4's six blockers acceptance criteria by default, and a rule known to be unimplementable becomes a test target.
  - Its open-items list sits outside the normative text.
- **Closed question to the operator.** What does sign-off cover?
  - **(a)** Fold round 4 (T4-02..T4-10) as draft.9 before sign-off.
  - **(b) Recommended.** Sign off draft.9, the draft.8 text with this review's fixes. S4.1.4's sentinel bind, S7.2.4 enrollment and S17.4 activation are marked **provisional, outside V8 acceptance** until round 4 is folded, and the O-list is folded into the spec or closed.
  - **(c)** Sign off as is.

#### V8R-09: MEDIUM, OPERATOR. The no-daemon policy no longer follows from D-655

- **Where:**
  - S16.6.9: without the daemon, apply `count` ("at most `max_concurrent` concurrent lanes or stacks per tier") or `unbudgeted`.
  - S14.6: "There is no instance count … the host is limited by memory, not by a number of stacks".
  - S2.7.2 has `max_concurrent` per tier.
- **The tension:**
  - D-653 Q16 was answered under revision 4.0, where the daemon was the admission ledger.
  - After D-655 the byte budget needs no daemon: charges come from the committed manifest, RAM from `/proc/meminfo`, PSI from `/proc/pressure/memory` (S16.6.1 already allows this) and `usable` from config.
  - The spec nevertheless discards a working byte budget when the daemon is absent and falls back to a count, the thing D-647 #4 ruled out ("a RAM budget, not a count").
  - Daemon visibility is per process: one devcontainer may reach the socket while another does not. Two participants can then apply bytes and count to the same order.
- **Closed question to the operator.** What does daemon absence change?
  - **(a) Recommended.** Only the inputs. The byte budget runs whenever charges and `usable` are known. `no_daemon` (renamed `unmeasured_policy`) applies only when a needed fact is unreadable, for example a slice-relative `usable` with no readable slice.
  - **(b)** Keep D-653 Q16 literally, and require every participant on one daemon to agree on daemon presence, for example through a label on the capacity object (V8R-06(a)).
  - **(c)** Keep the text as it is.

#### V8R-10: MEDIUM. An `exec` lane has no reservation object, and lane services are charged nowhere

- **Evidence:**
  - S16.6.1(1) lists the object kinds: the lane container, the stack containers, and a volume for a host lane.
  - S16.6.6 says an exec lane "is charged to the tier its memory lands in (the stack tier, or the gate tier when placed)", but no object carries that charge.
  - S16.4.7 lane services are "started after admission" with their own `resources`, and their memory is in no reservation. A tmpfs Postgres is the RG-75 case, and DAMON vaddr misses tmpfs anyway (proposal §4.1.10a Shortcomings).
- **Fix:**
  - An exec lane reserves with a labelled volume, like a host lane, in the tier S16.6.6 names.
  - A lane's reservation bytes include each started service's charge: its manifest entry, or the `memory_max` from its `resources`. An unmeasured service without `resources.memory_max` is a stage-12 ERROR when admission is on.

#### V8R-11: MEDIUM. Which object states count is undefined, and `dev` bypasses admission

- **Evidence:**
  - S16.6.1: "A reservation ends when its object is removed (… the stack is brought down)".
  - S14.1.3: "`down` stops containers … everything else stays on disk".
  - A completed `one_shot` stays `exited` for the life of the stack (S8.6.3).
  - `ciu dev --realization r` (S18) "render + `compose up`" is not in S14.1.2's admission.
- **Failure:** stopped stacks and exited one-shots keep their charge forever, and the tier fills with nothing running. A `dev` loop starts unreserved containers that the order cannot see.
- **Fix:**
  - Count objects in the states `created` (a waiting reservation), `running`, `restarting` and `paused`.
  - Exclude `exited` and `dead`.
  - Under V8R-03(a), the placeholder counts until `down` removes it.
  - `ciu dev` is admitted like `up`.

#### V8R-12: MEDIUM. Head-of-line blocking, PSI per reader, and stale readings

- **Evidence:**
  - S16.6.1(3)–(4): wait "in place, keeping its position", for 10 minutes by default.
  - The fit includes "memory PSI full avg10 ≤ psi_full_avg10_max", read by each process at its own time.
- **Failures:**
  1. A reservation whose charge alone exceeds `usable` can never start. It blocks every later reservation of the tier for the whole wait, and each retry repeats the block.
  2. PSI makes the decision depend on when each process reads it. Capacity stays safe, because later reservations still count the earlier ones, but the claim "same order ⇒ same decision" is false and an early waiter can starve while later ones start in PSI dips.
  3. Daemon readings (`ctl host`) have no freshness bound. A wedged daemon either blocks admission or feeds it an old snapshot.
- **Fix:**
  - A reservation whose charge exceeds the tier's `usable` is refused immediately: NOT_RUN/`no-headroom`, or exit 4 with no wait.
  - State that PSI is a per-reader deferral that keeps capacity safe but not fairness, or apply the PSI condition only to the earliest waiting reservation.
  - A daemon reading older than one recheck interval, or one that times out, is treated as unreadable, and the decision falls back to `/proc`.

#### V8R-13: MEDIUM. Slice sources are duplicated and ambient

- **Evidence:**
  - The gate slice can come from four places: `testing.cgroup_slice`, `testing.cgroup_slice_env`, `$CGROUP_PARENT_DEV_GATES` (S16.2.1) and `capacity.tiers.gates.slice|slice_env` (S2.7.2). They are reconciled by "MUST name the same one (stage 11 ERROR)", which is I1 violated and then patched (memo O-19).
  - The stack slice can come from two: `governance.cgroup_parent`, where `""` means `$CGROUP_PARENT_DEV_BACKGROUND` (S13.2), and `capacity.tiers.stacks.slice`. There is **no** equality rule, so admission can budget one slice while the containers land in another.
  - S18.2 lists both mdt variables as inputs and then says: "No other variable influences behavior; **none is a configuration source**" (I5: "no ambient process environment as a configuration source").
  - A hardwired default variable name is the kind of default that shadows a fact (I2).
- **Fix:**
  - One source per tier: `capacity.tiers.<t>.slice|slice_env`.
  - `testing.cgroup_slice*` and `governance.cgroup_parent` are derived from it, or removed.
  - Drop the hardwired `$CGROUP_PARENT_DEV_*` fallbacks. An mdt host declares `slice_env = "CGROUP_PARENT_DEV_GATES"` once in its host row, which D-653 explicitly allows.
  - Note for parity: run-gate R-10 defaults to the variable. The port re-expresses that default as the host-row declaration.

#### V8R-14: MEDIUM. The assay outcome mapping is not total

- **Evidence:**
  - S16.7.3: "The lane's verdict is the assay verdict's `outcome`, mapped by S16.8.2".
  - S16.8.2 maps only "the verdict file's `outcome`".
  - assay's `Outcome` (`assay/src/assay/errors.py:41-49`) has six members: `PASS`, `FAIL`, `ERROR`, `NO_MEASUREMENT`, `BUDGET_EXCEEDED` and `INCONCLUSIVE`. Its docstring says "2-5 are all not-a-pass and all block a merge".
  - `NO_MEASUREMENT` and `INCONCLUSIVE` have no verdict, so no exit code leaves the closed table for them.
- **Fix (resolvable from D-654's "documented mapping"):** `NO_MEASUREMENT` → FAIL and `INCONCLUSIVE` → FAIL, with the raw outcome kept as `assay_outcome` in the LaneResult. Add the row to S16.8.2's table.

#### V8R-15: MEDIUM. The closed exit table is not closed over every path

- **Multi-lane runs.** `ciu gate a b` (S16.10 accepts `[<lane>…]`) has no aggregation rule. Recommend the sequence rule: PASS iff all passed, else the first non-PASS verdict in argument order.
- **`ciu gate exec -- <cmd>`.** "no other code ever leaves the gate", yet the command's own exit status is the useful result. Recommend: `gate exec` is outside the table and returns the command's code, stated as such. Otherwise it maps the command's code like a `command` lane.
- **Sub-verbs** (`history`, `footprint`, `doctor`, `--list`): which table applies is undefined.
- **Lock contention** (memo O-5):
  - S14.4.4 says fail fast unless `--wait`, but S16.10 has no `--wait`.
  - S16.5 starts the budget "after admission and lock wait", so the gate evidently waits.
  - Recommend, following run-gate's RG-39/R-41 behaviour ("waiting for container"): the gate waits for its locks, bounded by `--admission-wait`. On expiry it reports NOT_RUN with a new reason `lock-busy`.
- **Base refusals** (S16.7.4: no base, a non-merge trunk HEAD, the charset) have no verdict. RG-74's oracle says "refuses with exit 2" and RG-78 lists "missing base" under NOT_RUN 3. Pick one; recommend NOT_RUN with a new reason `no-base`, since it is a precondition.
- **Judge provenance and digest.** `judge-provenance` is in the NOT_RUN reasons, while S16.3.2, S16.8's ERROR definition and S16.8.2 make a verdict without provenance an ERROR. A judge digest mismatch (S16.3.3) has no class at all. Recommend: delete `judge-provenance` from the NOT_RUN reasons, and add `judge-digest` as a NOT_RUN reason (a precondition, refused before execution).

#### V8R-16: MEDIUM. Filed oracles declared to be parity tests cannot pass against v8

- **Evidence:** S16.1.1 and Appendix R S16.8 say the oracles of RG-67/73/74/75/76 and RG-78 "become the port's parity tests". Against the v8 text:
  - **RG-78**: "A budget overrun yields BUDGET, and `--resume` then continues". v8 has no `--resume` (S16.9.4), and the token is `BUDGET_EXCEEDED`. RG-78 also lists "missing base" under NOT_RUN (V8R-15).
  - **RG-67**: "with `max_concurrent = 2` a third invocation blocks and two run", per environment. v8 has no per-environment count: exec stays at 1 (S16.5.7), and the tier count exists only under `no_daemon = count`.
  - **RG-73**: still proposes `mount_worktree_at`, option B of D-653 Q7, which was rejected.
  - **RG-75**: has `env` and `init` service keys and joins the target's network for exec lanes. S16.4.7 has neither key and says "on the lane's network".
  - **RG-76(c)**: "A `required_env` variable reaches the container without being listed in `forward_env`". S16.4.5 requires `required_env` ⊆ (`forward_env` ∪ …).
  - These items are being built **now** in run-gate (D-651 Q2), so their oracles ship before the port.
- **Fix:**
  - S16.12 gains a per-oracle table: carried, re-expressed as a named rule, or retired by D-65x.
  - The RG-73, RG-75, RG-76 and RG-78 backlog entries are amended **before** they are built. The amendment covers the RG-73 mount, the RG-78 token and `--resume` oracle, and a decision on RG-75's `init`/`env` and RG-76(c) in v8: carry or retire.

#### V8R-17: MEDIUM. A hermetic lane with `image_from` in a linked worktree needs init after all

- **Evidence:**
  - S16.11.6: "an `ephemeral` or `host` lane with no `requires` and no binding … runs without `ciu instance init`".
  - S16.4: `image_from` names "in a linked worktree's instance … the instance-scoped reference of S17.6.1".
  - S17.6.1: every `project` reference is tagged `name:tag-<instance_id>` in a linked worktree.
  - That tag exists only after `ciu build`, which is mutating, and S3.1.4 requires the instance file for it.
- **Failure:** D-651 Q8 holds only for literal images or the primary. dstdns's hermetic lanes on `image_from = "test_runner"` fail in every fresh worktree.
- **Fix (resolvable within Q8's intent, "image references derived read-only"):** resolve `image_from` in this order:
  1. the instance-scoped tag, when present locally;
  2. else the primary's declared reference.

  Disclose the choice and record the image id in `run.json`. A worktree that wants its own runtime runs `ciu build` (and therefore init) first.

#### V8R-18: MEDIUM. `clean --identity` bypasses the ownership guard, and collision detection is narrow

- **Evidence:**
  - S14.1.5: "removes exactly the containers, networks and named volumes whose `ciu.instance` label is the retired id `<old>`, and nothing else".
  - S4.5.4: "A mutating verb that removes … MUST verify … `ciu.project` … **and** … `ciu.checkout`".
  - D-651 Q10: "Deletion stays guarded by the `ciu.checkout` label".
  - With a ~31-bit id over every clone and project on a daemon, `--identity <old>` can delete another project's resources, or another clone's.
  - S4.1.1 refuses collisions only between "two live records of one git family". The primary has no record (V8R-23), and other clones are other families.
- **Fix:**
  - `clean --identity` also filters on `ciu.project`.
  - It refuses resources whose `ciu.checkout` names an existing path other than this checkout, unless `--force-foreign` is given, and it honours `protected`.
  - The allocation check also scans the daemon's `ciu.instance=<id>` labels for a different `ciu.checkout`.

#### V8R-19: MEDIUM. The peak fallback charge (decision fidelity: "warm, never peak")

- **Evidence:**
  - S16.6.6: "a run **measured without DAMON** … is charged its measured peak".
  - D-653 Q4: "Total or logical memory, including cold pages, is 'nice to know' but irrelevant to scheduling".
  - D-651 Q5: unmeasured work is charged `memory_max`.
- **Resolution, without the operator:**
  - A peak-only run has no warm figure. For the warm charge it is therefore *unmeasured*, and D-651 Q5 already rules its charge: `memory_max`.
  - The peak stays informational (R-44's fields).
  - This closes memo O-2 and proposal §4.10 item 38 from the decisions.

#### V8R-20: MEDIUM. The gate cutover cannot be done "per repo, while ciu8 is a separate console script"

- **Evidence:**
  - Appendix A step 8 rewrites the pointers to "`ciu gate <lane> --worktree {worktree}`".
  - Proposal §4.4: the console script is `ciu8` until 8.0.0.
  - S3.4.1 requires `revision = 8`, S3.2.6 refuses `.j2` declaration files, and S2.3.2 refuses `.ciu/`.
  - So a ciu8 gate cannot load a repo whose stacks are still on v7 files. For a repo with Realizations (dstdns), the "gate cutover" is the whole v8 migration, steps 1–7. And `ciu gate` does not exist on the PATH during the window.
- **Fix:**
  - State the precondition: steps 1–7 come first, unless the project is zero-instance.
  - State the command name the pointers get during the window (`ciu8 gate`), and the rename at 8.0.0 as a second mechanical rewrite.
  - Alternatively, allow a gate-only v8 `ciu.toml` beside v7 files and say how S3.2.6 exempts them.

#### V8R-21: MEDIUM. Host-mode lanes need Docker under admission

- **Evidence:** S16.11.2 says the gate "needs `docker` only when an `ephemeral` environment is used". S18.3 says Docker is needed "by `gate` only for lanes whose environment is `ephemeral` or `exec`". Yet S16.6.1(1) says a host-mode lane reserves with "a labelled volume".
- **Fix:** with admission on, every lane needs Docker for its reservation. Without Docker, the lane is NOT_RUN/`environment-down`, or it runs unreserved under the `unbudgeted` disclosure. State which.

#### V8R-22: MEDIUM. The floor admission is a second, unordered decide-and-start

- **Evidence:** S13.2.1: "Before a stack starts, ciu sums the live `memory_min` claims of the target slice's current occupants and refuses … when admitting this deploy set's floors would push the total past the slice's own `memory.min` ceiling".
- **Failure:**
  - Two concurrent `up` runs both sum, both fit and both start. That is the TOCTOU that D-651 Q4 and D-655 exist to remove.
  - The slice's `memory.min` is "read live from cgroupfs", which the devcontainer cannot see (proposal §4.1.10a live probe).
- **Fix:**
  - Floors ride the same reservation: add a `ciu.reservation.floor` label, summed over the order like `bytes`.
  - The ceiling is read through the daemon. Without the daemon, the floor check is reported as not evaluated.

#### V8R-23: LOW. The primary checkout has no library record

S14.1.1 says "on a git checkout writes the instance record", and S14.7.2 relies on `primary` labels and leases. But 7.15 `workspace.py:166-169` says: "The shared library has no product record for a primary checkout … lifecycle records are written only for linked worktrees". State how the primary is identified (`list_git_worktrees`: the first non-bare entry) and whether v8 writes a record for it. The latter is a library change and should be filed.

#### V8R-24: LOW. Where the gate state lives

- S2.1's row says `ciu-gate-state/` is "in the git family's shared state directory (S16.7.2); **state root**". S2.6.2 lists it among the state-root files, and the state root is the checkout root for a git checkout. S16.7.2 (and memo O-18) puts it under the git common dir.
- **Fix:** S2.1 and S2.6.2 should say "for a git checkout, `<git-common-dir>/ciu-gate-state/`; otherwise the state root".
- Also note that `.workspace-instances/` and `ciu-gate-state/` live inside `.git`. That is consistent with S2.3.3, which is about the checkout, but not with I5's unqualified "No hidden directories". Qualify I5.

#### V8R-25: LOW. The git mount wording for linked worktrees

S16.4.9 and S6.2.4 say: "the **git common directory**, read-write, at the path that file names". The gitfile names `<git-common-dir>/worktrees/<w>`. The common dir is reached through that directory's `commondir` file. Fix: "mount `<git-common-dir>` (derived from the gitfile and its `commondir`) at its own path". Also note that read-write exposes every sibling worktree's records, locks and gate state to the lane.

#### V8R-26: LOW. Remote Docker endpoints

Admission orders on whichever daemon the client reaches (`DOCKER_HOST`, a context). S2.7.3 reads RAM and PSI from `/proc` of the local machine, and capacity from the `local = true` row. With a remote endpoint, the decision mixes two hosts. Fix: refuse admission unless the endpoint is the local daemon, or read `MemTotal` from `docker info`. S18.2 should list `DOCKER_HOST` and `DOCKER_CONTEXT` as inputs.

#### V8R-27: LOW. The `memory_max` rule is stated three ways

There are three conditions for when `memory_max` is required:
- S2.7.4: "a charge source … nor a ceiling";
- S16.5 and stage 12: "required whenever the lane's tier has a finite capacity";
- S16.6.4: "whenever the container's `memory.max` is finite".

In addition, "`memory_max` stays the **hard ceiling** applied to the cgroup whatever the charge" (S16.6.6) is false for stacks when `[governance] enabled = false`: nothing is injected, yet the value is charged. Consolidate into one rule, and state that admission of the stacks tier requires governance enabled, or that the charge is then unenforced.

#### V8R-28: LOW. `ciu.footprint.json` writes are not serialized

`ciu gate footprint --write` and `ciu footprint --write` are both gate-class verbs (S14.3), and both rewrite one committed file. The S16.6.5 lane locks do not cover it. Fix: write it under an exclusive flock on the file's directory entry, as temp file plus rename, merging only the named entries (RG-69).

#### V8R-29: LOW. The minimum judge versus the selective flags

S16.3 sets the minimum judge at 5.2.0. S16.7.2 forwards `--reuse-from`, `--rejudge` and `--rejudge-outcome`, which RG-66 says need assay 7.1 or later. Fix: per-flag floors, and refuse the flags by name when the judge is older.

#### V8R-30: LOW. A renumbered backlog id

- **Where:** SPEC S18 (`skills` row: "CLI-EXT-02"), proposal §4.4 V8-33, and the source list ("CLI-EXT-02 (vbpub@ab52242e8)").
- At merge 583694b88 the skills verb group became **CLI-EXT-05** (`libraries/cli-extended/BACKLOG.md:248`). CLI-EXT-02 is now "evaluate declarative conditional option constraints".
- D-652 is an append-only record, so cite the new id with the old one in a note.

#### V8R-31: LOW. Literal ids in the spec's own examples

`k3x9aq` and `3a9f2c` appear in S4.1.1 and S4.1.2, `dstdns-k3x9aq-vault` in S4.5.4, and `a1b2c3` in S6.3.2. Appendix B says: "Instance ids in every example of this document are written `<instance_id>`: a literal id in a normative example is a defect". Use `<old_id>` and `<new_id>`.

#### V8R-32: LOW. Cross-references

- S14.3: "`gate` for a hermetic lane that needs no instance (S16.11.5)" → S16.11.6.
- S4.1.1: "`ciu clean --identity` (S14.1.4)" → S14.1.5.
- Proposal §4.10 item 38: "SPEC-V8 draft.8 S16.6.2" → S16.6.6.
- Proposal §4.10 item 15: "its own minimum (4.1.0)" → 5.2.0 (S16.3).

#### V8R-33: LOW. An upstream dependency nobody has filed

S16.9.5 and R.3 make `damon.warm_set_bytes` (a combined per-sample hot+warm series) the target `source`. cgprofile's Summary has only per-class `hot_bytes` and `warm_bytes` (`lib/summary.py:651-652`), and its backlog index has no entry for the combined series (CP-15 is the reservation mirror). The cross-repo convention is that it gets filed: `nyxloom backlog` for cgroup-profiler.

#### V8R-34: LOW. The realization graph's "Still open" body is stale

The body's "Still open" section says "Contract conformance at config time … **not planned anywhere yet**" and that the `pg:schema/*` ref kind "still doesn't exist". Its own preface table maps both to draft.3 (stage 5; S5.6 `pg:schema/*`). Mark the section historical in place, as was done for the v7 trace.

---

## 3 Phase 2: reconciliation with `CIU-V8-DECISION-COVERAGE-2026-10.md` §5

Key: (a) found independently; (b) real, missed by this review; (c) disagree; (d) resolvable from the decisions without the operator.

| O | verdict | note |
|---|---|---|
| O-1 verdict name | (a) V8R-16; (d) | Keep `BUDGET_EXCEEDED`: assay emits that token, so assay lanes need no translation. Add one sentence to S16.8 saying it is D-654's "BUDGET". RG-78's text and its `--resume` oracle must be amended before RG-78 is built. |
| O-2 measured without DAMON | (a) V8R-19; (d) | No warm figure means unmeasured, so `memory_max` (D-651 Q5). D-653 rules out the peak. Not an operator question. |
| O-3 wave against admission | (a) V8R-03 | Stronger than the memo states. Whole-set `compose create` is impossible for multi-wave deploy sets, per-wave reservations hold and wait, and per-object order stalls interleaved stacks. **OPERATOR** (V8R-03). |
| O-4 what switches admission on | (a) V8R-06, V8R-09 | The writer's reading ("on iff `capacity.tiers.<t>` declared; `no_daemon` required") is locally consistent. But capacity is per checkout, so "on" differs between participants on one daemon. Reservations must be created regardless (V8R-06). |
| O-5 lock contention in the gate | (a) V8R-15; (d) | run-gate waits ("waiting for container", RG-39/R-41), and S16.1.1 lets run-gate's behaviour govern. So: wait, bounded by `--admission-wait`, then NOT_RUN/`lock-busy`. |
| O-6 how a lane asks to be placed | (b); (d) | Real; this review missed it. Add a lane key `place` (bool, `exec`/`host` only, default false), passed to `ctl start` as `--place`. |
| O-7 authored `cgroup_parent`, host singleton | (b); (d) | Real; this review missed it. (1) The slice is already expressible: the daemon's own stack file sets `[governance] cgroup_parent = "cgprofile.slice"` (per-stack shallow merge, S6.10/S13.2). The template prohibition does not need an exception. (2) The fixed name is a producer concern. The consumer reaches the daemon through `[testing.profile] daemon` (configurable) or the socket carrier, so S4.2.3 needs no exception class. State both. |
| O-8 path the id is derived from | **(c)** V8R-01 | The memo's premise ("dstdns's id is derived for `/workspaces/dstdns`") is false. The recorded id is the hash of the recorded `physical_repo_root`, not of the container path. 7.15 derives from the physical path (`workspace_env.py:1579-1581`), so draft.7's "physical" was right and draft.8 inverted it. |
| O-9 `--move` against repair in place | (b); (d) | Real; missed. Plain repair must refuse when live resources carry the old id **and** their `ciu.checkout` is this path (a derivation change; run `clean --identity` first) or a path that no longer exists (a move; use `--move`). When the labels name another existing checkout (a copy), repair proceeds. This follows S4.5.4 and D-651 Q10. |
| O-10 tenant hook | (a) V8R-07 | The hook is the smaller part. Secret-path prefixing, tenant-name delivery, the lock and the clean semantics are also missing. |
| O-11 record field split | (d); see V8R-23 | The writer's split matches the library. What is missing is the primary's record. |
| O-12 host lanes and the slice | (d) | Agree that an unplaced host lane is honestly uncapped. Its admission still needs Docker (V8R-21). |
| O-13 protocol details | (a) V8R-03, V8R-04, V8R-11 | The visibility race (V8R-04) is not a detail. A timing-sampled agreement test cannot prove the claim it is meant to prove. |
| O-14 implied names | (d); (e) noted | (a)–(d) and (f) are fine as named. (e), the minimum measuring window, is a proposal feature with no key: add `[testing.footprint] min_window` or drop the sentence. |
| O-15 `verdict` vs `outcome` | (d) | Agree. RG-78 also uses `verdict`. |
| O-16 minimum judge | (d) plus V8R-29 | 5.2.0 is right for `--state-dir`. The selective flags need per-flag floors. |
| O-17 T4-10 scope | (a) V8R-08 | Part of the acceptance-scope question. |
| O-18 git-family state root | (a) V8R-24 | The definition is fine. S2.1 and S2.6.2 contradict it. |
| O-19 two slice declarations | (a) V8R-13 | The equality check patches an I1 violation, and the stack tier has no check at all. One source per tier. |
| O-20 pinned artifact in `command` | (d) | Agree. |
| O-21 `memory_min` | (d) plus V8R-22 | The reading is right. The floor check is itself an unordered decide-and-start. |
| O-22 import by names | (d) | Agree. No RG-76 oracle uses globs. |
| O-23 joins no preset produced | (d) | Agree: D-651 Q9 says "committed presets only". |

### 3.1 Matrix rows whose "yes" I do not accept

| row | memo says | this review | why |
|---|---|---|---|
| 5 (D-647 #5) | yes | **contradicted** | V8R-01: the text derives from a different path than 7.15. |
| 9 (oracles become parity tests) | yes | partial | V8R-16: five oracles cannot pass as written. |
| 15 (D-651 Q8 hermetic) | yes | partial | V8R-17: `image_from` in a worktree requires build and init. |
| 18 (tenant isolation) | yes | partial | V8R-07: secret paths are not prefixed. A joiner reads the primary's credential. |
| 20 (D-651 Q10 path-derived id and shared records) | yes | **contradicted / partial** | V8R-01 (the path) and V8R-02 (the lock is family-wide), plus V8R-23 (no primary record). |
| 22 (deletion guarded by `ciu.checkout`) | yes | partial | V8R-18: `clean --identity` bypasses it. |
| 25 (D-652 Q6 host-scoped config) | yes | partial | V8R-06: the inventory row is per checkout or per user, not per host. |
| 31 (CLI-EXT-02) | yes | partial | V8R-30: wrong id after the renumbering. |
| 34 (warm, never peak) | yes | partial | V8R-19: the same defect as row 36, which the memo scores separately. |
| 42 (raw-code mapping) | yes | partial | V8R-14: two assay outcomes are unmapped. |
| 43 (RG-78 as parity) | yes | partial | V8R-16: `--resume` and the token. |
| 45 (`compose create` set) | yes | faithful to the letter, but **unimplementable** | V8R-03. |
| 46 (ordered fit) | yes | faithful to the letter, but **racy** | V8R-04. |
| 47 (reap by owner tuple) | yes | partial | V8R-05: the tuple cannot prove death across namespaces, and R-39e is dropped. |
| 49 (correct across devcontainers and users) | yes | **not met** | V8R-04, V8R-05, V8R-06. |

Agreed rows: 1–4, 6–8, 10–14, 16, 17, 19, 21, 23, 24, 26–30, 32, 33, 35–41, 44, 48, 50, 51. Row 40 is agreed with the rev 4.4 fix plus O-1's amendment.

---

## 4 Operator decisions still needed

1. **V8R-03: How is a stack start reserved?**
   - (a) **Recommended:** one labelled placeholder object per admitted `up`, carrying the deploy set's whole charge. Wave containers are grouped under it.
   - (b) Per-container stubs created up front, then recreated, with a group label.
   - (c) Per-wave reservations, releasing earlier waves at expiry.
2. **V8R-04: How is the reservation order made race-free?**
   - (a) A settle delay after one's own creation.
   - (b) **Recommended:** gap-free tickets through Docker's atomic name reservation (`ciu-res-<tier>-<n>`; on a name conflict, wait until the name is listed).
   - (c) Accept the race as a stated limit behind PSI and the kernel ceilings.
3. **V8R-06: Where does the one capacity per Docker daemon live?**
   - (a) **Recommended:** a labelled Docker object on the daemon, written by `ciu host capacity set`.
   - (b) The per-checkout inventory row, with each reservation labelled with its capacity and a mismatch refused.
   - (c) Per-environment capacity, withdrawing D-655's cross-environment claim.
4. **V8R-09: What does the absence of the cgprofile daemon change?**
   - (a) **Recommended:** only the inputs. The byte budget runs whenever charges and `usable` are known, and the `count`/`unbudgeted` policy applies only to unreadable facts.
   - (b) D-653 Q16 literally, with daemon presence agreed through a shared label.
   - (c) The text as written: absence means count or unbudgeted.
5. **V8R-08: What does sign-off cover?**
   - (a) Fold round 4 (T4-02..T4-10) first, as draft.9.
   - (b) **Recommended:** sign off draft.9 (draft.8 plus this review's fixes), with S4.1.4's sentinel bind, S7.2.4 enrollment and S17.4 activation marked provisional and outside V8 acceptance, and the O-list folded into the spec.
   - (c) Sign off as is.

Everything else is resolvable from the decisions and the shipped code by the writer. That includes the three blockers' other two, V8R-01 and V8R-02, and memo items O-2, O-5..O-7 and O-9.

---

## 5 r2 fix-verify (2026-10-03, after dstdns D-658)

**Checked:**
- vbpub round 1, `76b0d79a6..d58b01ca7`.
- vbpub round 2: `899de6d21`, `3a1a64925`, `986cf4036`, `05138a08c`, `c8def3121`, `306e5285b`, `97a59b575`.
- The documents: SPEC-V8 8.0.0-draft.9, the proposal rev 4.6, `CIU-HOST-ENROLLMENT-PROPOSAL.md` rev 3, the realization graph, the coverage memo §1a–§1c and §5–§7, the RG-67/73/74/75/76/78 amendments, and CP-15/CP-16.
- D-658, read verbatim from the ledger.
- The round-4 fold, against `CIU-V8-THIRD-PARTY-REVIEW-ROUND4-2026-09-03.md`.

**Unchanged in this round:** the fix-verify reads only the text. Shipped-reality facts from round 1 were not re-measured.

### 5.1 Dispositions V8R-01..V8R-34

| id | status | note |
|---|---|---|
| V8R-01 | **closed** | S4.1.1 and S4.1.4: the physical path, established before the id, with the 7.15 fallback and remount-as-move stated. S14.8.2 is consistent. But see R2-04 for a new namespace defect in the repair rule. |
| V8R-02 | **closed** | S14.4.1(c): `ciu-instance-<instance_id>.lock`. The root-offset lock is confined to allocation, and S14.4.7 and S9.5.4 follow. |
| V8R-03 | closed, with a residue | S21.4.4: one placeholder per `up` with a full lifecycle. A re-`up` double-charges (R2-08). |
| V8R-04 | closed, with a residue | S21.4.2: name tickets, waiting until a conflicting name is listed. Number reuse breaks the gap-free induction (R2-03). |
| V8R-05 | closed in v8.1, with a residue in 8.0 | S16.9.7 has one tuple with `pid_ns`, and unknown means alive. 8.0 has no deadline, so a dead owner in another namespace wedges a lane (R2-06). The exec/host deadline cannot be written to an immutable label (R2-09). |
| V8R-06 | closed, with a residue | S21.3: the capacity object (see §5.3). |
| V8R-07 | closed | The `tenant` phase is S9.5.10. Prefixing is S10.1.7, `tenant` delivery is S7.8.1 and S6.4, and removal timing is S9.5.8. Residue: R2-13. |
| V8R-08 | closed | Round 4 is folded (§5.4); T4-07 is pending with the operator. |
| V8R-09 | closed | S21.8, per D-658. |
| V8R-10 | closed | S21.4.3: an exec or host lane holds a ticket with an empty group, and lane services are charged in the ticket. D.10's row still says "volume" (R2-16). |
| V8R-11 | closed | Group membership states decide what counts, and `dev` is admitted. |
| V8R-12 | closed | S21.4.6 (immediate refusal, PSI as a per-reader deferral) and S21.2.3 (freshness). |
| V8R-13 | closed | Slices are declared once per tier (S16.2.1, S13.2 `cgroup_parent[_env]`). No variable is hardwired, and S21.2 declares no slice. A small residue in S16.2.1's inheritable list (R2-16). |
| V8R-14 | closed | S16.8.2 maps all six assay outcomes, and `assay_outcome` is kept. |
| V8R-15 | closed | S16.8.2a, `--lock-wait` with NOT_RUN/`lock-busy`, `no-base` and `judge-digest`. `judge-provenance` is removed. |
| V8R-16 | closed | S16.12's per-oracle table. RG-67/73/74/75/76/78 are amended before any build (verified in the backlog diff). |
| V8R-17 | closed | S16.11.6's resolution order. |
| V8R-18 | closed, with a residue | S14.1.5 adds `ciu.project`, `--force-foreign` and `protected`. Its "existing path" test asks the wrong namespace (R2-04). |
| V8R-19 | closed | S21.5.1: no warm figure means `memory_max`, and the peak is informational. |
| V8R-20 | closed, with a residue | Appendix A step 8 states the precondition and the `ciu8 gate` pointers. Its next sentence still says `ciu gate` (R2-16). |
| V8R-21 | closed | S16.11.2 and S21.4.7. |
| V8R-22 | closed | S21.7: floors ride the ticket and are read through the daemon; without it they are "not evaluated". |
| V8R-23 | open, acknowledged | O-24, a question for upstream `libraries/worktree`. Acceptable as an open item. |
| V8R-24 | closed | S2.6.2. |
| V8R-25 | closed | S16.4.9. |
| V8R-26 | closed | S21.2.4. The `DOCKER_HOST` and `DOCKER_CONTEXT` inputs are in Appendix E. |
| V8R-27 | **regressed** | `memory_max` is now mandatory on **every** lane (S16.5, S16.6.13). Appendix B's minimal lane and S19.1's scaffold declare none, and stage 12 still says "when its tier's capacity is finite", a v8.1 concept (R2-05). |
| V8R-28 | closed | S16.9.5 and S21.5.2. |
| V8R-29 | closed | S16.7.2 has per-flag floors (7.1.0 for B106). |
| V8R-30 | closed | CLI-EXT-05 throughout, verified at `BACKLOG.md:248`. |
| V8R-31 | regressed (LOW) | A new literal id appears in S2.6.5's refusal example (`/var/lib/ciu/alpha-…`). |
| V8R-32 | closed | |
| V8R-33 | closed | CP-16 is filed. |
| V8R-34 | closed | The graph's "Still open" section is marked historical. |

### 5.2 D-658 fidelity

- **8.0/8.1 split.** It is faithful in structure: S21, Appendix F, and the `_v81` surfaces in Appendix E.
  - I grepped every `S21.*` and `(v8.1)` reference in S1–S20 (about 40 sites). All but two are conditional ("with v8.1 admission on …") or pointers.
  - **Two are real dependencies:**
    - S16.9.6 and S16.9.7: re-attach and abandonment use "a deadline passed" and "the owner's own deadline (S21.6)". 8.0 has no deadline (R2-06).
    - Stage 12 makes `memory_max` conditional on "its tier's capacity", which is S21.2 (R2-05).
  - **Consumer consequence of the split (R2-12).** RG-67's ephemeral count moved to v8.1, so 8.0 has no cap on concurrent gates at all.
- **The switch, `[ciu] admission = off|on`, default `off`.**
  - It is read through `ciu.toml`, `ciu.site.toml` and `ciu.instance.toml` and is never inherited (S3.4.7, S3.1.5, `ciu_keys_v81`). That is correct and consistent.
  - **Is "configured ≠ enabled" sound?** D-658's words are "even if configured it needs a single config switch to turn it off". The plain reading is: when configured, admission is on, and the switch turns it off. The writer inverted the default.
  - The writer's argument, S21.1.3(b), is that a published capacity object would switch every participant on. That argument holds only under a rule the ruling did not require: that the *shared object* counts as "configured". If "configured" means *this participant's own host row declares `capacity`*, the object can never enable anyone. The literal reading is then safe too.
  - So the inversion is not forced. It is a legitimate reading but an operator call (O-31, §6).
  - **Independent defect (R2-10).** With `off`, the admission flags are *refused* (`[S21.1] admission is off`). Flipping the switch to `off` then breaks every caller that passes `--admission-wait`, such as nyxloom pointers, cmru and CI. That contradicts "turn it off and have ciu just run things". The flags should be ignored, with one notice.
- **V8R-09 → (1).** S21.8 is faithful: the byte budget runs whenever its facts are known, and the policy applies only to unreadable facts.
- **V8R-03 → placeholder.** Faithful (S21.4.4). The lifecycle is complete except for re-`up` (R2-08).
- **V8R-04 → tickets.**
  - Faithful, and **stronger** than D-658's paraphrase. The ledger says "on a conflict, the next number is taken"; the spec makes the creator wait until the conflicting name is listed. That wait is what the gap-free proof needs, and the plain "take the next number" would have reopened the race. I agree with the spec over the paraphrase. The ledger should be read with the spec's wording.
  - Settle-delay is recorded as the weaker alternative (S21.4.10), as ruled.
  - Defect: number reuse (R2-03).
- **V8R-06 → published object.** Faithful (S21.3). Assessed in §5.3.
- **V8R-08 → round 4 folded first.** Faithful (§5.4).

### 5.3 The capacity object against the local table: is "object wins with warning" safe?

**For agreement, yes.** One denominator per daemon is exactly what V8R-06 asked for. The generation scheme takes the highest generation visible, and the name conflict gives the next one. That is sound for a single writer, and for concurrent `set` it is sound enough because readers re-read on every fit.

**Two gaps (R2-11):**
1. **Run-time validation.** Object values are validated only by `set` and by the publisher's stage 11 (S21.3.5). At run time the object wins without being checked against *this* machine's readable ceilings, for example after a RAM or slice change.
   - Fix: every fit check validates the object against the readable RAM and slice `memory.max`. An object above a ceiling is an unreadable fact and goes to S21.8's policy, never a silent overcommit.
2. **A missing object brings V8R-06 back.**
   - S21.3.3: with no object, each run uses its own local table, so two participants with different tables disagree until someone runs `set`.
   - Fix: with `admission = on` and no object, `usable` is **unreadable** (S21.8), unless the run is the only owner with tickets on the daemon. Or require `set` before the first `on` run.

A stricter local table being overridden by a larger object is intended. The WARN plus stage 11 is the right signal.

### 5.4 Round-4 fold, T4-01..T4-10, against the round-4 review's proposed fixes

| finding | verdict | note |
|---|---|---|
| T4-01 | folded, with a gap | Namespace, owner marker, disjoint install root and release lock files all match. The round-4 owner marker was `{project, instance_id, owner_id}`. With the owner token dropped (D-651 Q10), `owner.json` holds only `{project, instance_id}`, so two senders whose ids collide (a ~31-bit space, different clones) silently share a target namespace. Add the sender's `ciu.checkout` and controller host to `owner.json`, with an explicit `--adopt` for a deliberate controller move (R2-15). |
| T4-02 | folded, but with a **new deadlock** | The pointer record, CAS on generation, single candidate resolution and image reload all match. But "`apply` holds [the instance lock] for the whole host transaction" (S17.4.0). Step 2's `ciu instance init --host` and step 3's `[activate] apply` command (typically `ciu up`) are separate processes, and both are mutating verbs that take the **same** lock exclusively (S14.3), so they block forever (R2-01). |
| T4-03 | folded, but with a **regression** | The state-root sentinel, no write into the release and the `docker_optional` skip match. The helper launcher refuses without a declared stack slice (S8.5.2b), and governance is off by default, so `instance init` (whose sentinel needs a helper) and every S8.5.2a probe fail in any project without `[governance]`, including Appendix B's minimal one (R2-02). The sentinel wording inverts bind source and target, and Docker silently creates a missing bind source (R2-14). |
| T4-04 | folded | Stage 4's containing-worktree test and the flattening shape match. The fixture is in R.2 and is demo work. |
| T4-05 | folded | `--version` is mandatory, the launcher is absolute, and there is an exact version proof. |
| T4-06 | folded | Pending generations, promotion, `--revoke-old`, XDG for `--global` and locks. |
| T4-07 | folded except the trust root | That part is correctly marked `PENDING-OPERATOR`. I agree it is the operator's call (§6 Q1). |
| T4-08 | folded | Account database, no-follow walk, lock, atomic rewrite and the `--from` grammar. |
| T4-09 | folded, with a gap | The fd half, the owner tuple and the completion marker match. The `CIU_LEASE_ID` half fails across `docker exec`: the destination is in another PID namespace, so S16.9.7 says its owner tuple **cannot** be matched, yet S14.4.8 requires "owner tuple … matching". And when the destination shares the lock inode (the git common dir is mounted read-write per S16.4.9, so `ciu-instance-<id>.lock` is visible), "acquires its own lease under the destination's stable key" blocks on the source's own lock (R2-07). |
| T4-10 | folded | Appendix E plus `--surfaces`, the `known_host` grammar, docs inputs and the install root. |

### 5.5 Regressions and new findings

| id | sev | location | finding | minimal fix |
|---|---|---|---|---|
| R2-01 | **HIGH** | S17.4.0, S17.4.1 steps 2–3, S14.3 | `activate apply` holds the target's instance lock "for the whole host transaction". The nested `ciu instance init --host` and the host's `[activate] apply` command (`ciu up`) take the same lock exclusively from other processes, so they deadlock. | Give the pointer record its **own** lock (`<ns>/locks/pointers.lock`), held by `apply` and `push`. The nested verbs take the instance lock themselves. Alternatively, pass the held descriptor to direct children through `CIU_LEASE_FDS`, but the host commands are arbitrary shell. |
| R2-02 | **HIGH** | S8.5.2b, S4.1.4, S13.2 | The helper is refused without `governance.cgroup_parent`, and governance defaults to disabled. So `ciu instance init` (sentinel) and all S8.5.2a probes fail in a project without governance, including Appendix B's `ciu init && ciu instance init && ciu up`. | Use the stack slice when one is declared. Otherwise run at Docker's default parent under the launcher's fixed hard caps (which are what bound it), disclosed once. |
| R2-03 | **HIGH** (v8.1) | S21.4.2 ("a number may be reused … harmless") | With reuse, the gap-free induction breaks. A lists `{…,5}` and picks 6. Ticket 5 is removed, B lists `{…,4}`, picks 5 and creates it. A's fit check can run while B's 5 is still in flight and invisible, so A misses it, and B does not count A. Both start. The conflict wait also has no bound if the conflicting create fails and its name is freed without ever being listed. | Make numbers monotone. Release the highest ticket as a **tombstone** (start the helper so the ticket is `exited`; tombstones carry no charge), and remove a tombstone only once a higher ticket is visible. The janitor treats abandoned tickets the same way. Bound the wait: wait until the name is listed **or free**, and if free, retry the same `n`. |
| R2-04 | MEDIUM | S4.1.2 (b), S14.1.5 | The rules decide "a path that no longer exists" and "an existing path" on `ciu.checkout`, which is now the **physical** path. From a devcontainer, a host path does not exist locally, so every copy reads as a move and every foreign clone as removable: the wrong-namespace class of AGENTS §4.2a and the library's `physical_path` contract. | Decide by the git family's records and the daemon's labels, never by local `exists()` on a physical path. Or translate through the physical-root mapping first and treat an untranslatable path as "unknown → refuse". |
| R2-05 | MEDIUM | S16.5, S16.6.13 vs Appendix B, S19.1, S15 stage 12 | `memory_max` is "mandatory on every lane", but the minimal example and the scaffold omit it, and stage 12 still conditions it on tier capacity (v8.1). | Make it mandatory for `ephemeral` and placed lanes (where it is applied) and optional for unplaced `exec` and `host` lanes (where it is only requested). With v8.1 `on`, it becomes mandatory as the charge (S21.5.3). Fix stage 12, Appendix B and S19.1 to match. |
| R2-06 | MEDIUM | S16.9.6, S16.9.7 | 8.0 re-attach uses "a deadline passed" and "the owner's own deadline (S21.6)", but 8.0 has no deadline. A lane whose owner died in another PID namespace (for example a deleted devcontainer) is followed forever, `--fresh` refuses, and the lane is wedged. | Put the deadline in `run.json`, which is mutable and so can be written after start: start + `budget` + grace. Make it 8.0. An **exited** container is always collectable under the lane lock (S16.6.5). v8.1 tickets copy the deadline. |
| R2-07 | MEDIUM | S14.4.8 (`CIU_LEASE_ID`) vs S16.9.7, S16.4.9 | Across `docker exec`, the owner tuple cannot be matched, and the destination may share the lock inode and block on the source's lock. | Validate `CIU_LEASE_ID` by the record's existence plus a `LOCK_NB` probe that **fails** (proof the key is held), not by owner tuple. The destination then does **not** re-acquire a key whose inode equals the claimed one. |
| R2-08 | MEDIUM (v8.1) | S21.4.4 | A second `up` of a running stack takes a second placeholder. Unchanged containers keep the old group label (labels are immutable, and compose does not recreate them), so both placeholders hold memory: a permanent double charge until `down`. | One placeholder per instance and tier. `up` finds its own (by `ciu.instance`, kind `stack`) and keeps it when the charge did not grow. Otherwise it takes a new ticket for the whole charge, re-labels by recreating, and releases the old one once its group is empty. Or specify that `down` and `clean` remove every placeholder of the instance. |
| R2-09 | MEDIUM (v8.1) | S21.6.1 | The deadline of a `host` or `exec` lane is "its start plus `budget` plus grace", but the label is written at ticket creation, before the admission wait, and labels are immutable. | Deadline = creation + `--admission-wait` + `budget` + grace (an upper bound). Or take the deadline from `run.json` (R2-06). |
| R2-10 | MEDIUM | S21.1.2 | With `admission = off`, `--admission-wait` and `--override-admission` are refused, so switching admission off breaks every caller that passes them. | Accept and ignore them with one notice ("admission is off"). Keep the error only for `ciu footprint` and `ciu host capacity`, which are meaningless off. |
| R2-11 | MEDIUM (v8.1) | S21.3.3–S21.3.4 | No run-time validation of the object, and a missing object brings V8R-06 back (§5.3). | See §5.3. |
| R2-12 | MEDIUM | S16.5.7, RG-67 amendment 2, AGENTS §3.8 | 8.0 has no concurrency cap for ephemeral gates: RG-67's absorption moved to v8.1, and admission is off by default. dstdns's gate cap (D-570, D-636; "caps are config") would have no tool home at an 8.0 cutover. | **OPERATOR** (§6 Q3). |
| R2-13 | LOW | S9.5.10, S5.2.5 | Two gaps. (1) Whose copy of the provider's `tenant` hook runs: the joiner's checkout (possibly another commit) or the reference's? (2) How a `pg-database` tenant's role password reaches both the hook and the joiner's consumers (the hook's `secrets` are the provider stack's). | Run the **reference's** copy, from the reference checkout, under its lock. The tenant credential is a joiner secret with `store = "vault"` under the tenant prefix (S10.1.7), passed to the hook context as `tenant_secrets`. |
| R2-14 | LOW | S4.1.4 | The sentinel text says `<checkout>/ciu-data` is "bind-mounted read-only **at** the candidate path's `ciu-data`". That inverts source and target: the *source* must be the candidate (daemon-side) path. A `-v` bind also creates a missing host source as an empty directory, as root. | The source is the candidate path. Use `--mount type=bind`, which refuses a missing source. |
| R2-15 | LOW | S2.6.5 | `owner.json` lacks sender identity (§5.4, T4-01). | Add `checkout` and `controller`, and an explicit adopt flag. |
| R2-16 | LOW | various | Residues: <ul><li>a literal id in S2.6.5;</li><li>Appendix A step 8's second sentence still says `ciu gate`;</li><li>S21.10 lists S16.6.13 as moved, but it still exists;</li><li>D.10's V8R-10 row says "volume";</li><li>S20's header still says "draft.8" and lacks the new ids `[S21.3]` (capacity object refusals), `[S21.6]` (`budget` mandatory), `[S16.6]` (`place` on `ephemeral`) and `[S16.4]` (`services` outside `ephemeral`);</li><li>S16.2.1's inheritable list omits `testing.cgroup_slice_env`, which S3.1.5 lists and S16.2.1 itself says inherits;</li><li>S16.6.4 still says "requested **admission** values";</li><li>the helper's tier is unnamed in S21.4.1.</li></ul> | Edit each in place. |

### 5.6 Views on the writer's flagged items

- **O-28 (round 4's v7-line defects left unfiled).** I disagree with leaving them.
  - They are defects of shipped CIU-93 and cmru KI-24 code. T4-07 (an unauthenticated root program) and T4-08 (root following attacker paths in `authorized_keys`) are security defects in a released path.
  - The cross-repo rule (user CLAUDE.md; AGENTS §3.12, §4.9) is to file them in the owning backlog, and `nyxloom backlog` allocates the ids, so "needs ids allocated" is not a blocker.
  - File them before sign-off. This is not an operator question.
- **O-29 (containers, not volumes, for tickets).** I agree, and I correct my round-1 recommendation.
  - `docker volume create` with an existing name succeeds idempotently, so a volume cannot arbitrate. Only container names conflict.
  - The cost (the helper image must be present) is handled as an unreadable fact. That is acceptable, but the helper image needs a pull-or-fail rule for air-gapped hosts.
- **O-30 (the T4-01 `bundle_dir` default change to `/var/lib/ciu`, plus the per-instance namespace).**
  - Sound, and the narrow cost round 4 itself named. It is FHS-appropriate for state, and disjoint from `install_dir_system` (S2.6.6).
  - Confirm it as part of sign-off rather than as a separate fork. Add R2-15's sender identity.
- **O-31 (the switch default).** This is the operator's call; see §5.2 and §6 Q2.

### 5.7 r2 verdict: **READY-WITH-FIXES**

**What is now sound.** All three round-1 blockers are closed: the id is derived from the physical path, there is one lock per instance, and the stack reservation is one placeholder. Every round-1 operator question has been ruled and carried. Round 4 is folded.

**What blocks sign-off.**
- **Three HIGH findings** must be fixed first. None needs a design decision:
  - R2-01, the activation deadlock (8.0);
  - R2-02, helpers without a slice break `instance init` and the minimal example (8.0);
  - R2-03, ticket-number reuse breaks the gap-free order (v8.1).
- **Nine MEDIUM findings, R2-04..R2-12.** R2-12 needs the operator.
- **Seven LOW findings, R2-13..R2-16** (R2-16 bundles eight residues).

A short r3 spot-check of R2-01..R2-03 and R2-05..R2-09 is enough; a full round is not needed. Sign-off also needs the operator answers below.

**Count this round:** HIGH 3, MEDIUM 9, LOW 4 (R2-01..R2-16). The V8R list has 2 regressions (V8R-27 as R2-05, V8R-31 as R2-16).

## 6 Operator decisions still needed (after r2)

1. **T4-07: the trust root of the enrollment installer.** Where do `--installer-sha256` and the manifest public key come from?
   - (a) **Recommended:** the controller's own installed ciu release carries them. `ciu host enroll` prints the digest and key for the pinned `--version`. The trust is the controller's install, which is already trusted to run as the operator.
   - (b) An operator-configured key in host-scoped ciu config is required, and the release's key is not trusted by itself.
   - (c) Keep today's `curl | python3` posture behind immutable GitHub releases, with no local verification.
2. **O-31: the `[ciu] admission` default.**
   - (a) `off` unless set to `on` (draft.9). The safest default; work by participants who never opt in stays invisible to the order.
   - (b) **Recommended, the literal reading of D-658:** on when **this participant's own** host row declares `capacity`; an explicit `off` at any layer wins; the published object never enables anyone.
   - (c) `on` whenever a published capacity object exists on the daemon.
3. **R2-12: a gate cap for 8.0 consumers, now that RG-67's count is v8.1.**
   - (a) **Recommended:** dstdns's (and any capped consumer's) gate cutover waits for v8.1 with `admission = on`. 8.0 ships without a gate cap, stated in Appendix A.
   - (b) 8.0 keeps RG-67's per-environment `max_concurrent` as a stopgap count, retired by v8.1.
   - (c) Accept no tool-enforced gate cap until v8.1, with consumers keeping their own wrappers.

---

## 7 r3 spot-check (2026-10-03, after dstdns D-661)

**Checked:**
- vbpub round 3: `d773e0398`, `6ccfffb98`, `fe0fb5364`.
- vbpub round 4: `1fe6e7564`, `1db165afd`, `811565b7e`, `52b688c78`.
- The documents: SPEC-V8 **draft.10** (S21.0–S21.9, S16.9.6–S16.9.7, S17.4.0, S8.5.2b, S14.4.8, S7.2.4, Appendix E), the proposal rev 4.8 (§4.1.10b, §4.9), the CIU-122/123 and KI-49/50 filings, and the RG-67/RG-78 amendment 3.
- D-661, read verbatim from the scratchpad copy.

The scope is the spot-check proposed in §5.7 plus the D-661 implementation. It is not a full round.

### 7.1 Spot-check of R2-01..R2-03 and R2-05..R2-09

| id | status | evidence and residue |
|---|---|---|
| R2-01 | **closed** | S17.4.0: `<ns>/locks/pointers.lock` guards only the record. The nested verbs take the instance lock themselves, and the lock order is pointers, then instance, then stack. Nit: "a push waits (S14.4.4)", but S14.4.4 fails fast unless `--wait`. Say that `push` waits on the pointers lock by default. |
| R2-02 | **closed** | S8.5.2b: the helper uses the stack slice when one is declared, otherwise Docker's default parent under the fixed caps, with one notice. Appendix B's flow works. |
| R2-03 | **closed in S21.4.2**, residue R3-02 | Numbers are monotone through `exited` tombstones. The conflict wait is bounded at 30 s, with a retry of the same `n` when the name is free. My induction check holds: the visible maximum never disappears, so a number is never taken twice. **But** S21.6.2 says the janitor and `reap` "remove abandoned tickets", which would delete the highest one and reopen reuse (R3-02). |
| R2-05 | **closed** | `memory_max` is mandatory on `ephemeral` and placed lanes, optional on unplaced `exec` and `host` lanes, and mandatory on every lane only under the v8.1 byte budget. Stage 12 and Appendix B are consistent. |
| R2-06 | **closed**, minor residue | `run.json` carries a `deadline` (start + `budget` + 1 h, with 24 h for a lane that has no `budget`). An exited container is always collectable. A running one past its deadline is stopped and collected. Residue: a lane with no `budget` that legitimately runs longer than 25 h is **stopped by the next invocation**, even when its owner is alive in another namespace. State the 24 h default in S16.5's `budget` row, or exempt a lane with no budget from being stopped (collect only when exited). |
| R2-07 | **closed** | `CIU_LEASE_ID` is validated by the record plus a failing `LOCK_NB` probe, with a same-inode test that prevents re-acquiring. Residual, acceptable: a failing probe proves *someone* holds the key, not that the source does. |
| R2-08 | **closed** (v8.1) | A second `up` finds the instance's placeholder and tickets only the increase. `down` releases every placeholder of the instance. |
| R2-09 | **closed as worded, but causes R3-01** | The lane-ticket deadline is the upper bound creation + `--admission-wait` + `budget` (or 24 h) + 1 h. |

### 7.2 D-661 fidelity

- **T4-07 → (a): faithful.**
  - The wheel carries `ciu/enroll_trust.json` (`installer_sha256` and `manifest_pubkey` per version). `enroll --version X` refuses an unknown `X` and prints both values with their source.
  - The flag `--installer-sha256` is gone. A mirror changes only where the file is fetched from.
  - `PENDING-OPERATOR` is gone from the spec and the enrollment proposal; the one remaining hit in the proposal is a historical trace row.
  - LOW: the example table lists `7.15.1` as enrollable. The 7.15 `get.py enroll` is exactly what KI-49 says lacks verification, so a 7.15.1 entry should exist only once KI-49 is fixed in a 7.15.x release. Otherwise a v8 controller enrolls a v7 target through the unverified path.
- **O-31 → default off, grouped: faithful.**
  - Participant scope is `[admission] enabled`, a top-level table, layered, never inherited, default `false` (S3.3 row, S21.1.2).
  - Host scope is `[hosts.<h>.admission]`, published as `ciu-admission-<g>`.
  - With `false`, nothing is read or ticketed, and the flags are accepted and ignored with a notice, which closes R2-10.
  - Governance is explicitly unaffected.
- **R2-12 → (2), count mode in 8.0: faithful, with one narrowing (see §7.4(a)) and the defects below.**
  - **Self-contained?** Yes. Every rule the count mode needs is untagged 8.0: tickets and tombstones (S21.4.2), the count fit (S21.4.5), waiting (S21.4.6), labels (S21.4.8), deadlines and reaping (S21.6), the object's count subset (S21.3), the 8.0 part of unreadable facts (S21.8.2), and the helper image (S8.5.2b).
  - I found no 8.0 rule that cites a v8.1-tagged rule for behaviour. Appendix E's 8.0 surfaces (`admission_*`, `reservation_labels`, `gate_flags` with the two admission flags, `no-headroom` in `not_run_reasons`) agree with the text.
  - **Race-free?** The ordering is: monotone numbers, a bounded conflict wait, and a count over live tickets ≤ own `n`. That is race-free, with one wording hole (R3-02). Liveness is not satisfied (R3-01).
  - **Can an 8.0 and a v8.1 participant share one daemon?** The object is shared safely: unknown labels are ignored. Tickets are shared with two gaps (R3-03).

### 7.3 New findings

| id | sev | location | finding | minimal fix |
|---|---|---|---|---|
| R3-01 | **HIGH** | S21.6.1, S21.4.5 (count mode, 8.0) | A **crashed waiter holds a slot for hours.** A ticket counts as live while `created`, whether it is waiting or admitted, because the two states are indistinguishable. A client killed while waiting or running has a deadline of creation + wait + `budget` (or **24 h**) + 1 h. It can never be proven dead from another PID namespace, and a restarted devcontainer is one. So with `max_concurrent = 1` or `2` (dstdns's cap), one SIGKILLed gate stalls every gate on the daemon for `budget` + 1 h, or 25 h, and every waiter times out as `no-headroom`. That is worse than the `gate-slot.sh` it is meant to retire. `reap` cannot help, because abandonment is by deadline only. | Mark admission with a group member. On admission the owner creates a `created` **run marker** `ciu-run-<ticket>` in the ticket's group, labelled with the **true** run deadline: admission time + `budget` (or 24 h) + grace, now known, which also makes R2-09's upper bound unnecessary. Abandonment becomes: past the **ticket's** short deadline (creation + `--admission-wait` + grace) and the group is empty (a crashed waiter, freed within about an hour), **or** the run marker is past its deadline and no group member holds memory. Also allow abandonment when the owner tuple is provably dead **in the reader's own PID namespace** (the common single-devcontainer case: freed at once). |
| R3-02 | MEDIUM | S21.6.2 vs S21.4.2 | `reap`, re-attach and the janitor "remove abandoned tickets". Removing the **highest** one reopens number reuse, the R2-03 race. | Replace "remove" with "release (S21.4.2: removed, or tombstoned when it is the highest)" in S21.6.1–S21.6.2 and S16.9.6. |
| R3-03 | MEDIUM | S21.4.2 (last sentence), S21.4.5, S21.4.9 | Mixing 8.0 and v8.1 participants on one daemon: (1) an 8.0 participant takes a gates ticket only when a **count** is published, so with only `usable` published its lanes are invisible to v8.1's byte budget; (2) 8.0 `up` never takes a stacks ticket, so 8.0 stacks are invisible to v8.1's stacks tier; (3) a live 8.0 ticket without `bytes` is "an unreadable byte fact", so under `unreadable_policy = refuse` every v8.1 lane is NOT_RUN while any 8.0 gate runs. | (1) An enabled 8.0 participant takes a gates ticket whenever the object publishes **any** gates limit; the cost is one Docker round trip. (2) State 8.0 stacks as a limit in S21.4.9. (3) A foreign ticket without `bytes` is charged nothing by the byte walk but still counts. The walk discloses "byte budget partial: N tickets from 8.0 participants" and never triggers `refuse`. |
| R3-04 | MEDIUM | S21.1.6 | `ciu check` validates the admission key sets "only when `enabled` is true", which includes the **participant** table. A typo such as `[admission] enabeld = true` is never reported, and admission silently stays off: a silent default (I2) on the switch itself. | Always validate the participant `[admission]` table (S3.8.1). Exempt only the host scope while disabled. |
| R3-05 | LOW | S21.0, §4.1.10b | For a newcomer: the plain summary does not say that **editing `[hosts.<h>.admission]` changes nothing until `ciu host admission set --replace`** (the object wins). Nor does it say that `unreadable_policy` is taken from the local row only when no object exists. | Add both as a sentence each to S21.0 and §4.1.10b. |
| R3-06 | LOW | S21.3.1 | Readers take the highest generation they see. A manually deleted newest generation silently brings back an older, superseded limit. | `set` removes every older generation at once (not "afterwards"). A reader that sees more than one generation WARNs. |
| R3-07 | LOW | S7.2.4 example table | `7.15.1` is enrollable while KI-49 (an unverified v7 enroll) is open (§7.2). | Drop it from the example, or condition it on KI-49's fix. |

**Readability.** Read as a newcomer, §4.1.10b and S21.0 are clear:
- two scopes, one name;
- governance is separate from admission;
- a start unit and what it costs;
- a worked race with a waiting notice;
- the disabled participant shown as uncounted.

The layout rationale (the alternatives that lost) is persuasive. R3-05 is the only gap I would expect a new user to fall into.

### 7.4 The writer's four new questions (proposal §4.9 / memo §9)

- **(a) The count-only subset of the object is 8.0.** I agree; this is not an operator fork.
  - D-661 asks for "at most N live gate tickets **per Docker daemon**". A daemon-wide cap needs a daemon-wide source.
  - A count read from each participant's own host row would give two limits on one daemon, which is V8R-06, already ruled by D-658 ("published to the daemon").
  - The narrowing of D-661's "capacity objects … stay v8.1" to the byte keys follows from that. Record it as derived.
- **(b) The gates tier only in 8.0.** I agree. A stack count would bring back the instance count D-647 #4 removed.
  - Helpers are not counted in 8.0 and are bounded by their fixed caps.
  - In v8.1 a helper rides its caller's ticket. That is sound, because a helper's 64 MB fits within its caller's charge.
- **(c) `unreadable_policy = unbudgeted | refuse`.** I agree. The default `unbudgeted` keeps D-653 Q16 ("refusing by default not wanted"), and `count` would now say nothing, since the count is the published limit itself. Add R3-03(3) so that `refuse` is not tripped by another version's tickets.
- **(d) `ciu host admission set|show` ignore `enabled`.** I agree. They publish and inspect host facts; they are not runs. `show` printing the caller's own switch beside the object is the right aid.

### 7.5 r3 verdict: **READY-WITH-FIXES**

**Spot-check result.** All eight spot-checked R2 findings are closed in the text. R2-03 and R2-06 have small residues: R3-02 and the 25 h stop.

**D-661** is implemented faithfully, the 8.0 count mode is self-contained, and its ordering is race-free once R3-02's wording is fixed.

**Before sign-off:**
- **R3-01 (HIGH).** A crashed waiter or runner holds a count slot for hours. The fix is local, a run marker plus same-namespace death proof, and needs no ruling.
- **R3-02..R3-04 (MEDIUM).**

Then a one-paragraph check of R3-01 is enough; no further round is needed. Count this round: HIGH 1, MEDIUM 3, LOW 3.

**Operator decisions still needed:** none. The writer's four new items, (a)–(d) above, are sound and follow from D-658 and D-661; record them as derived, with no fork.

---

## 8 r4 final (2026-10-03; vbpub `c514c704a`, `48af4f54b`; SPEC-V8 draft.10, proposal rev 4.8)

### 8.1 What I checked, against the draft.10 text

**R3-01 is closed.**
- **The run marker.** S21.6.1 creates `ciu-run-<ticket>` on admission, before any lane container starts. Its `deadline` is the true run deadline (admission + `budget`, or 24 h, + 1 h), the same value is written to `run.json`, and the marker is never counted.
- **Abandonment.** S21.6.2 abandons a ticket in three cases, always provided no group member other than the marker holds memory:
  - (a) the owner is dead in the reader's own PID namespace; a foreign namespace still means alive;
  - (b) the ticket is past its short wait deadline (creation + `--admission-wait` + 5 min) and has no marker. The owner gives up at its own `--admission-wait`, so a live waiter is never released early. The 5-minute grace covers the step between admission and marker creation;
  - (c) the marker is past its deadline.
- **The bound and the janitor.** S21.4.6 runs the janitor on every recheck. S21.6.5's bound follows: nothing in the shared-namespace case, about the wait plus 5 minutes for a killed waiter, and the run deadline for a killed runner, and then only while its container still holds memory. That last limit is stated and is honest.
- **Lock wait.** Admission is the last step, after the locks (S14.4.3), so lock wait cannot eat into a ticket's wait deadline.

**R3-02 through R3-07 are closed.**
- **R3-02:** S21.6.3: every cleanup path *releases* (tombstones the highest) and never removes.
- **R3-03:** an 8.0 participant takes a gates ticket whenever any gates limit is published. 8.0 stacks are stated as invisible to the stacks tier. A foreign ticket without `bytes` is charged nothing, disclosed as a partial byte budget, and never triggers `refuse`.
- **R3-04:** S21.1.6 always validates the participant `[admission]` table.
- **R3-05:** S21.0 and §4.1.10b say that host-row edits need `set --replace`, and when `unreadable_policy` is read from the row.
- **R3-06:** S21.3.1 removes every older generation in the same step, and a reader WARNs when it sees more than one.
- **R3-07:** the enrollment table no longer lists the unverified 7.15.1.

**Residues are closed.**
- **The 25 h stop (R2-06):** S16.9.6 now stops a running lane past its deadline only when the lane declared a `budget`.
- **The `push` wait (R2-01 nit):** S17.4.0 is consistent.

### 8.2 One wording nit, not blocking

S21.4.6 has a waiter run "the collection of expired runs of S16.9.6 for the tier's lanes". A waiter in another worktree cannot write a different checkout's LaneResult and evidence. It should only **stop** the expired group members and **release** the ticket, leaving collection to the owning lane's next invocation.

### 8.3 Verdict: **READY for operator sign-off**

The verdict covers the 8.0 core plus the v8.1 annex, per D-658 and D-661.

**Remaining items:**
- the S21.4.6 wording nit above, which can be folded at implementation;
- the acknowledged upstream open item O-24 (no `libraries/worktree` record for the primary checkout);
- the demo rewrite listed in R.2.

None of them needs an operator decision.
