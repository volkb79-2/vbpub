# CMRU release-readiness review — Claude Opus, high effort

- Review session: `a0da0580-ff9d-4c27-8250-5830dce8c8fe` (`cmru-release-readiness-20261013`)
- Route recorded by the CLI: `claude-opus-5`, effort `high` (the CLI exposed this model name; the requested “Opus 5.5” alias was unavailable).
- Checkout: `.worktrees/cgprofile-cp20-contract2-20261012`
- Starting HEAD: `3af2bc23810ea4eee49114c93476cd41c562af13`
- Review scope caveat: the candidate tree was dirty and changed while the review ran. This report is a broad static review of that candidate, not exact-tree release approval. The reviewer reports that it did not run tests, gates, builds, or live host operations.
- Persistent transcript: `/home/vscode/.claude/projects/-workspaces-vbpub--worktrees-cgprofile-cp20-contract2-20261012/a0da0580-ff9d-4c27-8250-5830dce8c8fe.jsonl`

---

# CMRU — independent design & release-readiness review (read-only)

**Candidate:** `/workspaces/vbpub/.worktrees/cgprofile-cp20-contract2-20261012`, branch `cgprofile-cp20-contract2-20261012`. Route/model is not self-attested; the invocation is the route record.

## Review binding

| | value |
|---|---|
| HEAD at start | `3af2bc23810ea4eee49114c93476cd41c562af13` |
| HEAD at end | `3af2bc23810ea4eee49114c93476cd41c562af13` — **unchanged** |
| merge-base with `main` | `aa41988e8b58edb815568f727b3607a7ab59e5c0` |
| committed delta | 38 commits, 299 files, +25083/−4342 |
| dirty delta | 28 tracked files, +1034/−1256 · untracked: `cmru/src/cmru/gate_snapshot.py` (1056 lines) |

**`git status --short` DID change during the review.** At start: 27 modified + 1 untracked. At end: 31 modified + 1 untracked — `cmru/KNOWN_ISSUES_TODO_BACKLOG.md` (+28 lines, a new **KI-76** streamed-`get.py` bootstrap-integrity entry, mtime 20:33Z), `assay/README.md`, `assay/docs/CONSUMERS.md`, `assay/docs/DESIGN-GUIDE.md` became modified while I was reading. Another session is editing this candidate concurrently. I changed nothing: no edits, commits, stashes, checkouts, installs, tests or gates. Findings bind to HEAD `3af2bc238` plus the working tree as read ~19:30–20:35Z; the candidate is not a frozen object.

## Executive summary

The candidate contains a genuinely better design than the one it replaces. The old publisher-secret boundary was in-place masking of a mutable, mounted caller checkout — Sol's finding 1 (a secret created after inventory) was real and unfixable in kind. The replacement (KI-74: a dedicated attached sidecar worktree at the caller's exact commit, the caller's checkout never mounted, a fixed evidence allowlist copied back, a live host-only canary checked from inside the real tester container) removes that race by construction rather than by a wider walk. The supporting Run-Gate work (read-only `hooks/`, read-only `info/`, read-only linked-worktree pointers, uniformly hardened host-side Git readers) closes the host-hook-planting path the design now relies on. KI-70's mutation classification and KI-69's protected release identity are real, verifiable fixes.

**It is not releasable in this state, and not for docs reasons.** Four things block:

1. **The module implementing the exact-HEAD claim is untracked** and is imported from the *caller's* mutable tree, while the gate's own cleanliness check only looks at tracked files. KI-74's own text states the requirement and its own named adversarial oracle is absent. The change declares the rule and violates it.
2. **The candidate cannot pass its own `coverage` lane** (`--cov-fail-under=100` over `src/cmru`, no omit): the new module has two public functions with zero callers repo-wide and no tests, and in the exact-HEAD snapshot the untracked file does not exist, so the test module's import fails.
3. **The gate will fail spuriously on this host.** The snapshot integrity digest hashes *every ref in the shared repository* (measured: 441 refs / 180 branches / 209 tags / 50 remotes) and re-compares it before and after every lane across a 14h budget. Any other agent's commit on any other branch invalidates it, and the refusal blames the tester lane.
4. **No exact-HEAD registered-gate evidence exists** for this revision (Sol's finding 2 stands), the tree is dirty, and the latest Assay gate failed.

Separately, KI-75 is wholly unimplemented — GitHub release bodies are `notes-env-or-default + artifact block` and never read `CHANGES.md` — and KI-58's preserved notes live only in a report file, with a second divergent copy on an unmerged branch.

The broader pattern: complexity is accumulating in the *host-side* half of the gate — caller-tree helper, sidecar lifecycle, lease, manifest, canary, allowlist copy-back, orphan cleanup — to protect a boundary that largely disappears if the gate executed from the snapshot instead of from the caller.

---

## Confirmed defects

### C-1 — BLOCKER — the gate's integrity helper is untracked and loaded from the caller's tree

`cmru/src/cmru/gate_snapshot.py` (untracked `??`, 1056 lines) is imported at `cmru/tools/run_release_gate.py:271` and `:315`. The import resolves against the **caller's** tree: `_load_components` inserts `PROJECT_ROOT / "src"` at `run_release_gate.py:56-62`, and `PROJECT_ROOT` derives from `__file__` — the registered lane is `argv = ["python3", "{worktree}/cmru/tools/run_release_gate.py", "--worktree", "{worktree}"]` (`cmru/run-gate.toml:178`). The gate's cleanliness check is `git status --porcelain=v1 --untracked-files=no` (`gate_snapshot.py:301`), which by construction cannot see it. `cmru/src/cmru/transaction.py:755-760` (dirty) now imports the same module from `remove_workspace`.

**Trigger:** run `./run-gate.py gate` today, or commit the dirty tree without `git add cmru/src/cmru/gate_snapshot.py`.

**Impact:** the gate judges commit X using code not in X. The snapshot is materialized at exact HEAD, so the module is absent from everything the lanes run, while host-side orchestration uses the caller's copy. If the wave is committed without the file, the published wheel lacks `cmru.gate_snapshot` while tracked `transaction.remove_workspace` imports it — a post-tag failure in release cleanup. The `installed-wheel` lane would not catch it: `installed_wheel_smoke.py:180` imports only `cmru`, never the submodule.

KI-74's "Chosen behavior" states the requirement verbatim ("the gate's own executable code and every helper it imports must also be bound to tracked files at the claimed source HEAD"), and its "Oracles" names the missing test: *"Add an adversarial case with an untracked caller-side helper that would alter the gate result."* `cmru/tests/test_release_gate.py` has 21 tests; `grep -n untracked` yields one hit (`:318`, the lane-output assertion). Note the asymmetry: CMRU *does* preflight **Assay's** tree for untracked/ignored non-generated files before its editable install (`tools/check_assay_source.py`, run at `run_release_gate.py:69-88` before anything else) — same hazard class, opposite treatment for CMRU's own source.

**Corrective action/oracle:** commit the module *and* add a host-side preflight enumerating every module the gate imports from the caller tree, refusing unless each is tracked **and** its working-tree blob hash equals HEAD's. Oracle: plant an untracked `cmru/src/cmru/<helper>.py` that a tracked module imports and that would flip the gate result — the gate must refuse before any lane. A wrong implementation that only checks `--untracked-files=no` must fail that test.

### C-2 — BLOCKER — the snapshot ref digest is a superset refusal; it will cry wolf on a healthy estate

`gate_snapshot.py:259-263`:

```python
def _refs_sha256(root: Path) -> str:
    refs = _git(root, "for-each-ref", "--format=%(refname)%00%(objectname)").stdout
    return hashlib.sha256(refs).hexdigest()
```

Captured at admission (`:467` create, `:475` upgrade), asserted unchanged at `:484` ("CMRU gate snapshot refs changed during admission"), persisted into the record and manifest (`:486-489`, `:773`), and re-compared in `validate_lane_outputs:701` ("a release gate lane changed shared Git refs") — which `prepare_lane:736` and `finish_lane:749` call **before and after every one of the five lanes**, across `budget = "14h"` including a `budget = "7h"` mutation lane.

`git for-each-ref` enumerates the **shared** ref store. Measured here: **441 refs — 180 `refs/heads`, 209 `refs/tags`, 50 `refs/remotes`**, with ~160 live worktrees. One commit by any other session on any other branch, a `git fetch`, or a tag changes the digest.

**Impact:** a near-certain **false gate failure** whose message accuses the tester lane. It also defeats the documented resume path ("retained … for safe resume", `cmru/README.md` dirty): on resume the expected digest is the *previous run's*, so `validate_lane_outputs(... expected_refs_sha256=...)` at `:476` fails before anything can be retried. This is AGENTS.md's **superset refusal** plus its corollary — a gate that cries wolf on a healthy estate gets switched off, and then it protects nothing.

**Corrective action/oracle:** scope the comparison to refs the gate owns and a tester could plausibly reach (snapshot branch, source branch, snapshot `HEAD`). Oracle: the gate passes while a *different* worktree commits on an unrelated branch mid-lane; it still fails when an in-container lane writes `refs/heads/<anything>` or moves the snapshot branch — the second half as a real container probe, not a fake-`git` argv assertion.

### C-3 — BLOCKER — the `coverage` lane cannot be green on this candidate

`cmru/run-gate.toml:59` runs `pytest tests -q --cov=src/cmru --cov-branch --cov-fail-under=100`. No coverage `omit` exists (`cmru/pyproject.toml` has no `[tool.coverage*]` table). Two independent reasons it fails:

1. **Dead code, no tests.** `stale_gate_snapshot_records` (`:984`), `remove_stale_gate_snapshot` (`:1003`) and their shared helper `_snapshot_source_is_stale` (`:893`) have **zero references anywhere in the repository** (repo-wide grep excluding `.git`; no doc mentions them either). `cmru/src/cmru/cli.py` has no `gate_snapshot` reference at all. ~120 lines of security-sensitive cleanup logic cannot reach 100% branch coverage.
2. **The module is untracked (C-1).** Inside the exact-HEAD snapshot the file does not exist, so `cmru/tests/test_release_gate.py:244` `from cmru import gate_snapshot` raises `ImportError` and the lane collapses at collection.

**Oracle:** the registered `coverage` lane at the exact candidate commit. Fix by wiring the cleanup to a real verb (C-6 wants it anyway) or deleting it.

### C-4 — HIGH — lease liveness is PID-based, ignores PID namespaces, and bypasses the TTL

`gate_snapshot.py:917-938` (`_gate_lease_is_live`) and `:132-149` (`_stale_gate_lease`). For a holder `cmru-gate:<pid>:<ticks>:<uuid>` the answer is derived purely from the **local** PID namespace and the function returns before reaching the `expires_at_utc` branch — the TTL path is unreachable for CMRU's own holders. `_stale_gate_lease` returns `True` (stale, release it) when `/proc/<pid>/stat` is unreadable and `os.kill(pid, 0)` raises `ProcessLookupError`.

**The trigger exists right now:** `git worktree list` reports `/home/vb/volkb79-2/vbpub/.worktrees/host-cgprofile-build-20261013 e3f13ada4 [host-cgprofile-build-20261013] prunable` — a worktree registered at a **host** path invisible from this devcontainer.

**Impact:** `remove_gate_snapshots_for_source` (`:1029`) and `remove_stale_gate_snapshot` (`:1003`) would release a *live* gate's lease and then `git worktree remove --force` + `git branch -D` its snapshot, destroying a running 14h gate's workspace. Compounded in the library: `libraries/worktree/src/worktree/core.py:1187` `remove_orphan_workspace`'s only orphan criterion is `not _path_exists(current.source_git_root)` — "absence for emptiness", the exact shape AGENTS.md records as CIU-15 ("never validate a namespace-translated path with a local filesystem call"). `_snapshot_source_is_stale:896-898` has the same shape.

**Corrective action/oracle:** make the TTL the floor, not the fallback (dead only if PID evidence says dead **and** the TTL has passed); record the holder's namespace identity so a cross-namespace holder is *indeterminate*; have `remove_orphan_workspace` require positive evidence from the executor (`git worktree list --porcelain` reporting the entry `prunable`). Oracles: a lease whose PID does not exist locally but whose TTL is future refuses cleanup; an orphan whose source is merely invisible refuses while a genuinely pruned one is removed.

### C-5 — MEDIUM — `installed-wheel` is now un-runnable as a component lane, and `--require-live-snapshot` proves less than it says

`cmru/run-gate.toml:41` (dirty) appends `--require-live-snapshot` unconditionally; `installed_wheel_smoke.py:116-144` refuses when the manifest is missing (`:121`) or `state != "running"` (`:129`).

(a) A direct `./run-gate.py installed-wheel` can no longer run at all, while `cmru/README.md` and `docs/CONSUMERS.md` (both dirty) still say "Direct component-lane runs bypass this boundary and should use a secret-free checkout", and `installed_wheel_smoke.py:122` still carries the comment "this lane can also be run directly, outside the aggregate gate". README/CONSUMERS and the shipped argv disagree — a three-document sync violation.

(b) **Name for object.** The claim is "running inside CMRU's active exact-HEAD release gate"; the comparison is "a file in my own tree says `running` and one path is absent". `write_snapshot_manifest` writes the terminal state in a `finally` (`run_release_gate.py:295-302`), so a SIGKILLed gate leaves `state="running"` with its canary already removed in the **caller's** `cmru/.assay/release-gate-snapshot.json` — after which the "required" check passes trivially, forever. The canary-absence test is also satisfied by any reason the path is invisible, not only "the source is not mounted".

**Oracle:** a retained `running` manifest from a killed gate must not satisfy `--require-live-snapshot` (bind the manifest to the live lease holder); and decide/document whether the lane is gate-only.

### C-6 — MEDIUM — retained gate snapshots accumulate with no supported cleanup verb

`open_gate_snapshot:392-396` creates `<source_root>.parent / cmru-gate-<sha256[:20]>` plus a branch of the same name, one per `(source_root, HEAD)`; `close_gate_snapshot:838` only releases the lease. The **only** remover is `transaction.remove_workspace` (`transaction.py:755-760`) — a successful release-candidate cleanup. The documented primary path is a direct `./run-gate.py gate` (`cmru/run-gate.toml:163-166`: *"every consumer (cmru release steps, agents, humans) runs THIS lane"*), which never cleans up.

Every gated commit therefore leaves one permanent worktree and one permanent branch, in a repo already carrying 160 worktrees and 180 branches, against the operator's standing rule and the same shape as open KI-35. AGENTS.md's "Use supported project workflows" requires a product verb; `cli.py` exposes none, and the two functions written for the job are unreachable (C-3). The docs claim cleanup for the release-candidate case and are silent on the common case — a true statement that reads as complete coverage.

**Oracle:** a `cmru` verb lists/removes stale gate snapshots and is tested; two gate runs at different HEADs leave zero `cmru-gate-*` worktrees and branches after it; a snapshot with a live lease refuses.

### C-7 — LOW/MEDIUM — the source canary filename is not gitignored

`plant_source_canary:801` writes `.cmru-gate-canary-<32 hex>.txt` into the caller / release-candidate root; `git check-ignore` reports **NOT IGNORED** (no `cmru-gate`/`canary` pattern in `.gitignore`). `_verify_source` uses `--untracked-files=no` so it tolerates a leftover, but a crashed gate leaves an untracked file inside a release candidate, where the transaction's "reject uncommitted release-path edits" preflight and any `clean_tree = true` check can see it, and where a broad `git add` would capture it. **Oracle:** add the pattern; assert the source's `git status --porcelain --untracked-files=all` is empty after a passing gate **and** after a SIGTERM-interrupted one.

### C-8 — LOW — KI-52's status text is stale

`cmru/KNOWN_ISSUES_TODO_BACKLOG.md:1597` still reads "(b) is bypassed by assay's hermetic git and filed as assay B147", but `assay/nyxloom-trove/4-backlog.md:205` records **B147 as SHIPPED in `assay-v8.0.0`** (2026-10-07, with B145). The caveat weakening KI-52's defence-in-depth no longer applies.

---

## Design concerns

### D-1 — the KI-74 split is more surface than an exact-commit read-only source needs

The exact-HEAD guarantee is assembled from: a caller-tree helper (C-1), a sidecar lifecycle, an 18h lease with PID semantics (C-4), a whole-repo ref digest (C-2), a JSON manifest written at five points, a planted canary (C-5/C-7), a bidirectional evidence allowlist, an ignored-file classifier, a `.run-gate` state validator, and an unreachable orphan cleanup (C-3/C-6). Each piece is defensible; together they are a large host-side trusted computing base guarding a boundary whose root cause is "the gate runs from the caller".

**Simpler alternatives, strongest first:**

1. **Run the orchestrator from the snapshot.** A tiny tracked bootstrap in the caller (verify → create/validate the sidecar → `exec` the snapshot's own `tools/run_release_gate.py --from-snapshot`) makes the exact-HEAD claim *structural*: every helper after the handoff is at the claimed commit by construction, C-1 disappears, and the bootstrap is small enough to review as a unit and bind with a blob-hash preflight. **This is my recommendation.**
2. **Or run it from the installed wheel** built at the exact commit (the `installed-wheel` lane already builds one) — same property, plus it exercises the shipped artifact. Cost: a bootstrap-ordering problem, which is why (1) wins.
3. **Mount the sidecar read-only** where a lane does not legitimately need to write. `prepare_lane`/`finish_lane`/`validate_lane_outputs` exist largely to detect and undo lane writes; prevention would replace detection-and-repair. Run-Gate already demonstrates the pattern (`git_hook_info_mount_flags`).

**Adversarial fixtures still missing** beyond KI-74's own: a lane that writes a ref through the rw common Git dir (C-2's legitimate counterpart); a lane that replaces a seeded evidence file with a plausible PASS verdict while leaving the tree otherwise clean; two concurrent CMRU gates on the same source (`_release_gate_lock` is keyed on `temp_parent`, i.e. `/tmp/.cmru-release-gate.lock` — it serializes gates *on one host* but not across namespaces, the same blind spot as C-4); a symlinked `cmru/.assay` in the source (the code handles this via `_ensure_real_directory`, but untested).

**Ordinary operator mistakes are handled well** — dirty tracked tree, main checkout, tracked secret, tracked evidence path, configured Git `filter`, stale `[Unreleased]` body are all refused *before* lanes start with remedy-naming messages. Preserve that shape through any simplification.

### D-2 — a CMRU security claim rests on an unenforced, unreleased Run-Gate revision

`cmru/docs/DESIGN-GUIDE.md` (dirty) asserts as part of CMRU's own boundary: *"Run-Gate overlays its common `hooks/` directory with a private empty read-only directory and mounts `info/` read-only. This prevents a tester from planting a host hook…"* That behaviour exists only in the **dirty, unreleased** Run-Gate rev 68 (`run-gate.py:18`; `git_hook_info_mount_flags:10413`, `git_worktree_pointer_mount_flags:10466`, `host_git_environment:3871`). Run-Gate records `run_gate_revision` in lane metadata (`:3076`) but **no project declares a minimum** (repo-wide grep: no consumer-side `min_revision`/`require_revision`), and CMRU declares no Run-Gate floor. The unmerged `cmru-bugs62` branch has a commit that does exactly this ("declare run-gate floor 23.10.0") — the idea existed and did not land.

In-repo this is masked because `cmru/run-gate.py` is a symlink to `../run-gate-project/run-gate.py`. That is a **masked default**: the wrong value is invisible in every context you would observe it in, and surfaces only for an out-of-repo consumer, or if Run-Gate's release lands after CMRU's. **Action:** declare and *check* a Run-Gate revision floor from CMRU's gate preflight, with a refusal naming the capability, not just a number.

### D-3 — container-level resource events are unobserved for four of the five release lanes (KI-52 residual)

`run_lane_with_cgroup_event_guard` (`run-gate.py:806`) wraps bare-host, exec **and** container lanes, but `read_current_cgroup_events` (`:725-761`) reads `/proc/self/cgroup` — **run-gate's own** cgroup. Lane containers are placed under `$CGROUP_PARENT_DEV_GATES`, which AGENTS.md documents as a *sibling* of the devcontainer tier, so a tester container's `pids.events`/`memory.events` are not in run-gate's hierarchy at all.

Run-Gate's docs are honest about the scope — `CONSUMERS.md:359` "the counters **in the cgroup containing the run-gate process**", `README:139` "**its** cgroup", `SPEC.md:95` "the run-gate cgroup's". `docs/DESIGN-GUIDE.md:88-104`, however, puts "Process-heavy tests can therefore fill **the container's** process limit" two sentences before "run-gate compares **the same cgroup's** counters", which reads as if the lane's container were measured — worth one clarifying clause.

The residual gap for CMRU: KI-52's (c') in-container event copy-out lives in `cmru/src/cmru/tester_gate.py:166,815-816` — used by `cmru tester-gate`, **not** by the release gate's lanes, which go through Run-Gate. Only `[lanes.mutation]` opts into `resource_observer = "cgprofile"` (`cmru/run-gate.toml:102`). So `installed-wheel`, `assay`, `coverage`, `canary` rely on `--init` + `--pids-limit` and on the command surfacing its own fork/OOM failures. Defensible (all four are short and fail loudly on a broken fork), but CMRU's docs should state which lanes carry container-level event evidence; the cheap improvement is declaring `resource_observer` plus the required `resources.memory`/`resources.pids` on the remaining ephemeral lanes.

**On PID 1 / subreaper specifically, the composition is sound.** Run-Gate passes `--init` on both of its own launches (`:7259`, `:12023`) and refuses to run as container PID 1 (RG-84); the mutation campaign sets `PR_SET_CHILD_SUBREAPER` via `assay.process_tree` (`process_tree.py:39,173-175`), so orphans reparent to the campaign rather than docker-init, and `_candidate_result` records an outliving descendant as `crashed`/`descendant-leak` rather than a kill (`mutation_campaign.py:237-241`). Assay B147 (hermetic Git dropping the image's `gc.autoDetach=false`) is shipped in `assay-v8.0.0`, closing the last known zombie source. The unmonitored path that remains is the one above: a non-mutation container lane whose command swallows a fork or OOM failure and exits 0.

### D-4 — host capacity vs declared lane caps (Sol finding 3, partially addressed)

`modern-debian-tools-python-debug/host-setup/host-setup.env.example:197` still ships `DEV_GATES_MEMORY_MAX=1.5G`, while four CMRU lanes declare `memory = "2g"` (`cmru/run-gate.toml:31,46,66,134`). **Sol's wording complaint is fixed in this candidate**: `run-gate-project/CONSUMERS.md:240-250` (dirty) now states plainly that the shipped `1.5G` example "cannot admit this `2G` R2 lane", that Run-Gate refuses only "when it can read the declared slice's `memory.max` and `memory.current`", and that it "does not prove ancestor or whole-host headroom". Correct fix.

What remains is a **release prerequisite**: record effective `memory.max`/`memory.current` for `dev-gates.slice` and its `dev.slice` ancestor, plus host headroom, before the 7h campaign, so a capacity-induced outcome is not read as a mutation result. Per the contention-agnostic rule, a pressure-affected run is infrastructure/inconclusive and retried, never silently a PASS/FAIL.

### D-5 — release-note ownership (KI-58 / KI-75) end to end

**Where notes come from, traced:** `handlers.py:422` `notes = (os.getenv(args.notes_env) if args.notes_env else None) or f"{args.prefix} {version}"` → `release.py:435`/`:584` `body = (notes or ...) + <artifact, SHA256, verify, resolve-latest block>` → `create_release`/`update_release` (`release.py:156-170`). **Nothing reads `CHANGES.md`.** KI-75 is wholly unimplemented; there is no dry-run preview of the composed body and no refusal for a missing version section, because there is no section lookup.

**Is the env mechanism wired?** `--notes-env` exists for assay (`ASSAY_RELEASE_NOTES`), run-gate (`RUN_GATE_RELEASE_NOTES`), cmru (`CMRU_RELEASE_NOTES`), but **no value is declared** anywhere — `cmru.orchestration.toml [orchestration.defaults.env]` (lines 55-138) sets images, memory, CPU, pids; no `*_RELEASE_NOTES`. Unless the operator exports them by hand, every body in this wave is `"<prefix> <version>"` plus the artifact block. **cgroup-profiler has no path at all**: its push step is `python3 build-push.py --push` (`scripts/cgroup-profiler/cmru.toml:68`), an image publish with no release body — and its `CHANGES.md` currently has **no versioned section whatsoever** (first heading is `## [Unreleased]`) ahead of its first `1.0.0`.

**Do Git history / changelog / GitHub body match?** No. The tagged changelog section is generated from commit subjects; the GitHub body is an unrelated env string. Nothing compares them.

**KI-58's remedy collides with KI-68.** KI-58 says the preserved notes "must be placed in the matching versioned release sections before tagging". KI-68 records that regenerating a generated section whose body contains hand-written lines **silently drops them** unless `source-end` is advanced in the same commit (and the WARN that would make it loud is still open). So the prescribed remedy runs inside a known silent-loss window, on exactly the project (run-gate 23.10.0) where that loss already happened once. Worse, the same notes exist twice: the candidate's `run-gate-project/nyxloom-trove/reports/CMRU-KI58-unreleased-notes-carry-forward-2026-10-13.md`, and `scripts/cgroup-profiler/docs/release-notes-next-draft.md` on the unmerged `cmru-bugs62` — a duplicate/conflicting-notes hazard of exactly the kind KI-75's oracle is meant to exclude.

**Recommended ordering:** implement KI-75 **before** this wave's tags, so the body is `generated install block + the exact ## [X.Y.Z] section` with a `--dry-run` preview and a hard refusal for a missing/malformed section. KI-58 then reduces to a data move with a mechanical oracle instead of a manual paste inside a silent-loss window. Also give `build-push.py` a release-body path or record explicitly that cgprofile ships image-only notes.

**Positive, verified:** `changelog._unreleased_body` (`changelog.py:45-61`) strips HTML comments before the emptiness test, so a cleared-but-explained `[Unreleased]` passes the KI-30 preflight. I confirmed cmru, assay, run-gate-project and cgroup-profiler all currently have comment-only bodies — the *preflight* will pass. That is precisely why the omission is silent.

### D-6 — backlog, worktrees and release dependencies are not coherently represented

I inspected identity and merge status rather than assuming abandonment:

| branch / worktree | status | disposition |
|---|---|---|
| `cmru-w3-prep`, `cmru-wave-2026-10` | ancestors of `main` | merged; retire |
| 4 × `cmru-release-20261006/07-*` (cli-extended ×2, nyxloom, cgroup-profiler) | ancestors of `main` | **retained release candidates — open KI-35**; still occupying `.worktrees/` |
| `cmru-ki52-focused-20261006` | 22 commits, **not** merged | **appears superseded** — `tester-unified/gen-requirements.py` and the `--init`/pids/events work are present in the candidate; confirm, then `cmru abandon` |
| `cmru-inst62` | 4 commits, **not** merged | live, unlanded: installer `render`/`check` verbs, `stable_dir`, `stub_launchers`, wizard questions, re-rendered `ciu/get.py` and `tls-edge/get.py` (+1057-line test). Directly adjacent to the brand-new **KI-76** |
| `cmru-bugs62` | 6 commits, **not** merged | **conflicts with the candidate**: a *different* removal of the masker (`run_release_gate.py` −452 lines), a declared run-gate floor 23.10.0, the KI-68 silent-loss refusal, the KI-59 exact bump-commit skip, a KI-69 scope-based bump, and a second KI-58 carry-forward artifact |

**The specific incoherence:** KI-64/65/73 are marked "superseded by KI-74" and KI-59/KI-68 "open" — both true *only* because `cmru-bugs62` never merged. If the candidate ships as-is, two real fixes that exist on disk stay unshipped, and the estate carries two divergent answers to "how is the gate secret boundary enforced". Decide which wins, record it, cherry-pick or abandon. `cmru-inst62` is independent and should be scheduled, not merged into this wave.

Also: **KI-62** (postponed 6.0.0 R2 campaign) is still "in progress, severity: major (release evidence gap)". The `--postpone-mutation` provisional path still exists (`run_release_gate.py:324-330`, `_record_postponement:165`) and correctly WARNs plus writes a retained marker — it must not be used for this wave while KI-62 is open, or the evidence gap compounds.

---

## Positive evidence (claims I checked and found sound)

- **Gated commit == tagged commit.** `docs/RELEASE-TRANSACTIONS.md:205` — "required tester-unified gate (**again if versioning adds a commit**)". Promotion merges `origin/main` into the candidate `--no-ff`, never rebases/force-pushes, and refuses when `origin/main` touched the released project's own paths (`:227-245`). The classic gate-base trap is addressed by design.
- **`_verify_source` (`gate_snapshot.py:281-361`) checks what it claims:** regular-file `.git` (so a main checkout is refused — message and comparison match), attached branch, hex HEAD, clean *tracked* status, no `assume-unchanged`/`skip-worktree` entries, no tracked `cmru.secret.toml`, no tracked evidence paths, no configured Git `filter` attribute on any tracked path (NUL-delimited, so pathological filenames are handled). Two have tests asserting **no lane launched**.
- **Evidence is validated before it is trusted.** `prepare_lane:736`/`finish_lane:749` call `validate_lane_outputs` *first*, and `test_unapproved_lane_output_fails_and_is_not_copied_back` proves a plausible `{"status":"PASS"}` verdict is **not** copied back when the lane also left an unapproved file. A `finally`-raised validation error replaces a lane's own exit code, so the gate fails closed.
- **All gate evidence paths are gitignored** (`.gitignore:348 .assay/`, `:104 coverage.json`, `:105 junit-coverage.xml`, `:354 .run-gate/`), verified with `git check-ignore -v` — so seeding the snapshot cannot trip Run-Gate's `clean_tree`. A non-obvious coupling that happens to be right.
- **Git pointer reads are hardened:** `O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC`, `S_ISREG` + 4 KiB size checks, `gitdir` cross-verified against the marker, `commondir` optionally pinned, admin dir required under `<common>/worktrees` (`_read_pointer_file:94`, `_linked_git_context:48`), with a FIFO/symlink test.
- **Credentials:** `_lane_environment:107` strips `GITHUB_PUSH_PAT`, `GITHUB_TOKEN`, `CMRU_GIT_AUTH_TOKEN`, the facts variable and `RUN_GATE_EXTRA_MOUNTS`; the baseline facts JSON is scanned for the resolved token before being forwarded (`run_release_gate.py:340`) — a real check, not a comment.
- **KI-70 is genuinely fixed.** `mutation_campaign._candidate_result:214-241` emits `killed` only for `returncode == 1` with no resource-limit event, no timeout and no outliving descendants; timeout → `crashed`/`timeout`, cgroup event → `crashed`/`resource-limit`, leak → `crashed`/`descendant-leak`.
- **KI-52 (a)/(a')/(c') are real.** `--init` on the gate workload and DinD sidecar (`tester_gate.py:651`, `:777`), `--pids-limit` required with no default (`resolve_pids_limit:604`; `CMRU_TESTER_PIDS_LIMIT = "4096"` declared), and an in-container `pids.events`/`memory.events` copy-out whose non-zero `max`/`oom_kill` is an infrastructure failure naming the counter (`:166`, `:815-816`).
- **KI-69 is wired and reserved.** `CMRU_RELEASE_COMMIT`/`CMRU_RELEASE_TAG` passed to tagged artifact phases (`cli.py:2886`), reserved against project declaration (`config.py:789`), cleared from ordinary and cleanup steps (`cli.py:357`, `:1922`).
- **Run-Gate's host-side Git is uniformly hardened** through one helper (`host_git_environment:3871`) applied to every reader I found (`git_out`, `check_clean_tree`, `paths_are_git_ignored`, `git_operation_in_progress`, `head_commit`, `worktree_is_dirty`, `merge_base_of`, `current_branch_ref`, `derive_upstream_base`, `read_git_config_entries`) — one source rather than ten copies. `_make_private_dir` returns a fresh `mkdtemp` per call, so the new `hooks-empty` overlay cannot collide across lanes.
- **Sol's finding 3 wording is corrected** (D-4); Sol's inventory-pruning and incomplete-restoration findings are moot by construction now that masking is gone.

---

## Release blockers, ordered

1. **C-1** — commit `gate_snapshot.py` *and* add the tracked-helper preflight plus KI-74's own named adversarial oracle. Committing the file alone fixes today's instance and leaves the class open.
2. **C-3** — make the `coverage` lane passable.
3. **C-2** — fix the ref-digest scope. Until then the gate cannot reliably run to completion, so blockers 4–7 cannot be evidenced.
4. **Exact-HEAD registered-gate evidence** for CMRU, Assay, Run-Gate/cgprofile and the affected MDT checks. Sol's finding 2 stands: no committed receipt binds a pass to this revision, the tree is dirty (and changed during this review), and the latest Assay gate failed. Nothing here is release-qualified today.
5. **KI-75** — implement before tagging so KI-58 becomes a verifiable data move (D-5). At minimum, do not tag Assay 10.0.0 / Run-Gate / cgprofile 1.0.0 with `*_RELEASE_NOTES` unset and the curated notes sitting only in a report file.
6. **C-4** — cross-namespace lease liveness; it can destroy a live gate's workspace, and this repository already contains the triggering condition.
7. **D-4** — record `dev-gates.slice`/`dev.slice` effective capacity before the 7h campaign, or lower the declared caps.

Non-blocking but belong in the same wave: **C-5**, **C-6**, **C-7**, **C-8**, **D-2**, and the **D-6** branch-reconciliation decision.

## Open questions

1. **C-1/D-1:** commit `gate_snapshot.py` as-is, or move the gate's execution into the snapshot (or the exact-commit wheel)? This changes C-1, C-3 and most of D-1.
2. **D-6:** which implementation of the gate secret boundary wins — the dirty candidate (KI-74 snapshot) or `cmru-bugs62` (masker removal + run-gate floor)? KI-68's and KI-59's fixes exist *only* on the unmerged branch.
3. Is `cmru-ki52-focused-20261006` confirmed superseded, and may it be abandoned via `cmru abandon`?
4. Who owns the four retained `cmru-release-*` worktrees (KI-35), and should they be cleaned before this wave adds more?
5. **C-5:** should `installed-wheel` remain gate-only, or should `--require-live-snapshot` be conditional?
6. **C-4:** is `worktree.remove_orphan_workspace` intended to be usable across PID/mount namespaces at all? If not it should refuse rather than infer.
7. Should this candidate be frozen before qualification? Another session modified it during this review; an exact-HEAD receipt is not meaningful against a moving tree.

## Method and limitations

Read-only throughout: `git` inspection (`status`, `log`, `diff`, `worktree list`, `for-each-ref`, `check-ignore`, `merge-base --is-ancestor`, `ls-files`, `show`), file reads/greps, and `stat`. **No tests, gates, builds, installs, container or service operations were run**, per the cockpit doctrine (whose dirty revision in this candidate strengthens the prohibition) and the review-only instruction. Consequences:

- Every finding is **source- and configuration-derived**. C-3 is established statically from `--cov-fail-under=100`, the absence of a coverage `omit`, and a repo-wide zero-caller/zero-test result — not from a run. C-2's trigger is established from the measured ref inventory (441 refs) and the code's comparison scope, not from an observed failure.
- I did **not** verify live-host facts: no cgroup inspection, no Docker state, no GitHub API reads. D-4 rests on the shipped example file and the declared lane caps.
- I did not re-run Sol's live findings; where I could check its source claims against this tree I did, and I note which are addressed (1 by construction, 3 in wording) and which stand (2; 4 as accepted).
- Other CMRU worktrees were inspected for identity/branch/merge status only; none was modified, cleaned or checked out.

**One correction to an impression formed mid-review:** I initially read Run-Gate's `pids.events`/`memory.events` guard as a mis-scoped check with an over-claiming message. The *code* is mis-scoped relative to what an operator would assume, but README, CONSUMERS and SPEC all state that scope accurately — so it is a documented design boundary (D-3) and a one-clause DESIGN-GUIDE nit, not a false certification.
