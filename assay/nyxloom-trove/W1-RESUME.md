# Wave 1 — live resume point

**This file is the loop's state.** Update it at the end of every iteration,
before doing anything else that could be interrupted. If it disagrees with this
session's memory, this file wins; if it disagrees with `decisions.md`, decisions
wins and this file is stale.

* **Worktree:** `/workspaces/vbpub/.worktrees/assay-B005-B006-coverage-v6`
* **Branch:** `assay-B005-B006-coverage-v6`, based on `main` `c66b67a0`,
  with `main`'s B006 rewrite merged at `0791d9c4`
* **Contract:** `nyxloom-trove/W1-CARVE-branch-coverage-and-whole-target.md`
  (Addenda A–D supersede the body where they disagree)
* **Requirement, authoritative over the contract:** `nyxloom-trove/4-backlog.md`
  items B005 and B006
* **Rulings:** `decisions.md` A-257…A-267

## Done

| what | evidence |
|---|---|
| carve spec + 8 real coverage fixtures | `4286e501`, `39fa7af2` |
| fixtures kept out of the project's own suite | `af918715` |
| carver's own corrections (Addendum A) | `77c40ee7` |
| review round 1 folded in (9 blocking) | `59af6b4b` |
| WI-0 — decisions A-257…A-265 | `6bd75c0c` |
| main's B006 rewrite adapted, A-266 | `571cf2b5` |
| review round 2 folded in (13 blocking), A-267 | `0a96dc7e` |
| **registered gate green on this branch** | `ASSAY_REGISTERED_GATE_COMPLETE=1`, exit 0, at `59af6b4b` |
| WI-3 — branch model + all four parsers (§3) | `759bea03`, `bd99bb7a`; 2532 passed, 100% line+branch on all six touched modules |

**WI-3 independently verified by the controller**, not taken from the report:
the eight real fixtures are untouched; the three formats parse `sample.py` to
the SAME per-line arcs `{5:(1,2), 11:(1,2), 12:(2,2), 18:(0,2)}` = 4/8, matching
coverage.py's own `num_branches`/`covered_branches`; `check_sample.py` gets `{}`
rather than `None` in the branch-tracking lcov (the mixed-artifact trap); the
negative-`dst` exit arc parses. Eight adversarial probes against the shipped
parser: the CONTROL is accepted, `meta`-deleted-with-arcs is accepted as
`reported` (A-265's authority rule — the case that must NOT refuse), and the
coherent tamper (duplicate covered arc with the summary bumped to match) is
REFUSED, closing review round 1's finding 3 in the shipped code.
Caveat recorded: the orphan-arc probe was caught by the totals cross-check
before the model's invariant 3, so that probe does not isolate invariant 3.

**Carried into WI-4:** O3's second half — a `require_branch = true` lane over a
go-cover artifact refusing with `NO_MEASUREMENT`/`BRANCH_UNAVAILABLE` — is
judge-level and could not exist yet. The parser half is proven. Do not let it
fall between the two work items.

## In flight — check before spawning anything

Use `ListAgents` first. Do NOT spawn a second implementer or a duplicate
reviewer; resume the existing one by name if it is still listed.

* **implementer (Sonnet 5, serialized — one only):** sent WI-3, the branch model
  and four parsers (§3). Was mid-edit in `src/assay/coverage*.py`.
* **reviewer (Opus, fresh, no inherited context):** adversarial round 3 over §1
  (project-scoped snapshots), §2, §6's isolation block, §7 WI-1, and
  O9/O9b/O15/O17/O18/O19.

## UNBLOCKED 2026-08-17 — round budget reset, CMRU writable, origin/main current

Three things changed at once and all three widen what this wave can finish:

* **the review budget was reset.** Round 3's NOT READY counts as the FIRST review
  of the post-`c7bc9b59` design, not the third of the old one. **Two rounds
  remain**, and B006(a) stays in wave 1 rather than being split out. All 8
  blocking findings are already folded in as decisions (Addendum E, A-268) — the
  next review starts from the revised §1, not from a question;
* **CMRU's lanes and rigor are ours to edit.** O9b stops being deferred and
  becomes this capability's real acceptance test. CMRU is at 100% lines
  (6060/6060) and branches (2184/2184), 29/29 mutants killed, with a canary that
  fails for the coverage reason — the best R1/R2/R3 consumer available;
* **`origin/main` is current**, so `cmru release`'s "N commits ahead" guard is
  gone and the release step at the end of this wave is reachable. The operator
  has authorised releasing BOTH cmru and assay.

Round 3 (Opus, fresh context) returned **NOT READY — 8 blocking, 14
non-blocking**, and its two structural findings are the reason §1 changed shape:

The two that change what the work IS, rather than how it is written:

* **§1.3 makes work item 1c unbuildable.** Pinning `project_prefix` to the
  project's own repo-relative path means no loadable `assay.toml` can produce a
  source root, artifact, canary target or cwd outside the boundary — every one
  is already contained by `config.py:1280`/`:998`/`_load_canary` and
  `runner.py:1113`. 1c's headline deliverable is five refusal branches that
  cannot fire, in a project that forbids `pragma: no cover`. And the rule cannot
  be enforced where §1.2 puts it: `config.py` imports no git, so the project's
  repo-relative path is unknown at load.
* **The narrowed claim STILL over-reaches.** A-267 says assay never materialises
  an out-of-scope path. True — but the command can recreate one, demonstrated
  with stock git against the retained closure B006.3 requires:
  `git checkout -- <path>` after clearing its skip bit, or `git worktree add`.
  A suite that runs `git stash`/`git worktree add` hits it by accident. So the
  honest property is narrower again: *assay* never materialises it.

Also blocking: only `skip-worktree` survives `_verify` + the post-run dirt check
+ `write-tree`, and §1 names no mechanism at all (measured — narrowing the index
makes `git status` report `D .gitignore` for every omitted entry); the
`materialisation` enum's two values do not separate the four situations §6 says
it exists to separate; `cli.py:355`/`:385` must emit a REQUIRED `isolation`
object with no boundary in scope; §2's `judgment.r1.coverage_artifact` claim is
false on both refusal paths and contradicts A-264 in the wave that establishes
it; and O18 asks the implementer to mutate an object §1.8 requires to be
immutable.

## Remaining, in order

1. **Review round 2-of-3 on the revised §1 — IN FLIGHT** (fresh Opus, no
   inherited context; codex is unavailable). Dispatched at `465393d3` and told
   not to re-report the 8 already folded in, but to ask whether each fix worked
   or merely relocated the problem, what the fixes introduced, and whether
   CMRU's real R1/R2/R3 lane is actually achievable. It was also asked to test
   the `skip-worktree` mechanism ruling empirically in a scratch repo, since
   §1.3 now states it as measured fact.
   If READY → dispatch WI-1. If NOT READY → fold the findings in, then ONE more
   round; after that, stop and report. Dispatching WI-1 without a READY verdict
   is allowed only if every remaining finding is non-blocking.
2. **WI-1a/1b/1c** — project-scoped snapshot: config + `ResolvedSnapshotBoundary`,
   isolation materialisation, runner preflight. Three commits.
3. **WI-2** — the artifact parent chain inside the snapshot (§2).
4. **WI-4** — the v6 contract: §4, §5, §6 together. Migration by the four typed
   buckets (43 transform / 7 preserve byte-identical / 12 hand-edit / 3 must not
   change); the classifier scans THREE patterns (`"schema_version": 5`,
   `VERDICT_SCHEMA_VERSION`, `urn:assay:schema:verdict:5`) and refuses to run on
   a file it cannot place. Deselection list for P33's locked suite is derived by
   MEASUREMENT — run it under v6, list every red, classify each.
5. **WI-5** — docs, `STATE.md`, backlog status lines AND their prose sections,
   the nyxloom spine, and a LOG under `reports/`.
6. **WI-6 — CMRU's real R1/R2/R3 lane** (O9b). Write `cmru/assay.toml`'s
   higher-rigor lane with project scope: source roots, coverage argv + artifact,
   `fail_under = 100.0`, `require_branch = true`, the mutation policy, and the
   canary. `inputs` must be derived by SWEEPING CMRU's suite for repo-root
   dependencies (`parents[2]` and friends) — at least `cmru.project.sample.toml`
   and `cmru.release.sh`, the latter actually executed by
   `test_release_wrapper.py`. Remove CMRU's temporary whole-source coverage
   stopgap only once this lane is green, and say so in its commit.
7. **Controller's own adversarial review** — by writing real inputs to disk and
   driving the SHIPPED entry points, never by reading the diff and never by
   re-running the implementer's fixtures. Then one Opus review of the
   implementation.
8. **Gate green on the branch**, run in the foreground with the exit code
   captured honestly — never `| tail`, which returns tail's status.
9. **Merge `--no-ff` into `main`**, then gate green again on `main`.
10. **Release, authorised 2026-08-17.** `cmru release --project assay
    --dry-run` first, then the real release for **both cmru and assay**. Show
    the dry-run output before publishing. This is the step that lets CMRU pin a
    released assay and drop its stopgap.
    * **Confirm the `.pyz` asset actually published**, not just the wheel.
      dstdns consumes the ZIPAPP (`tools/assay/assay-<v>.pyz`), so a release
      that ships only the wheel is useless to it. That asset rides cmru's
      `wheel-publish --extra-asset` (A-249/A-250).
10b. **Notify the dstdns agent — same filesystem, no git, no push.** After the
    release lands, write `/workspaces/dstdns/.assay-inbox/release.json`. Its
    schema is tracked at `/workspaces/dstdns/.assay-inbox/CONTRACT.md` — READ IT
    at the time rather than trusting this summary. One JSON object:
    `version`, `tag`, `artifact_filename`, **`sha256` of the `.pyz` (REQUIRED —
    dstdns re-vendors the artifact and verifies it against this)**,
    `download_url`, optional `manifest_url`, `landed`, `notes`, `written_at`.
    Take the hash from the release's own `.sha256` sidecar rather than
    recomputing by hand.
    `landed` is the field that changes what dstdns DOES: `B005` lets it retire
    its `--cov-fail-under=100`-in-argv stopgap (D-044) for a real attested
    floor, and `B006` lets it drop both substrate work-arounds — the removed
    nginx symlink (D-045.1) and the tracked `.assay/.gitkeep` (D-045.2). So
    list only what genuinely shipped; naming an item that did not land would
    have dstdns tear out a work-around it still needs.
    The directory is gitignored on dstdns's side precisely so this drop cannot
    dirty its tree and break its own assay gate — do not commit anything there.
    Cross-machine fallback (not needed here, we share a filesystem):
    `SendMessage` the dstdns loop session, discovered via `ListAgents`.
11. **Then wave 2 and wave 3, as originally agreed:** B004 (provenance as
    VERIFIED evidence — its ciu blocker CIU-20 is FIXED, `ciu provenance --json`
    ships) → release; then B001/P34 (the SQL/DDL adapter), whose plan gets its
    own adversarial review before any dispatch.

## Standing rules

* `git commit --only -- <paths>` with `-F <msgfile>`. Never `add`+`commit`,
  never `reset`/`rebase`/`--amend`. A concurrent committer shares this repo.
* Editor for edits, never `sed -i` or a script that writes files — a hook blocks
  those and a blocked write looks like success.
* Locked carve assets under `carve-assets/P2x/` and `P33/` are frozen evidence.
  Deselect with a written justification; never edit one to make a gate pass.
* Nothing under `tests/fixtures/coverage/` may be edited.
* 100% line and branch coverage on new code, zero `pragma: no cover`, every test
  differential.
* A stated pass/fail count is not evidence (A-232): paste real command output.
* Commit before gating — `assay run` refuses a dirty tree and the gate's first
  step IS an `assay run`.
* Expect exactly one pre-existing red in the devcontainer:
  `test_standalone.py::test_a_real_pass_matches_the_documented_r0_pass_shape`.
  Do not "fix" it.

## STOP and ask the operator

* both remaining review rounds return NOT READY (the budget is then spent);
* an oracle in the contract turns out to be unwritable honestly — report it,
  never weaken it into one that cannot fail;
* a finding that changes the wave's SCOPE rather than its detail;
* the backlog changes under us again (it has twice);
* anything outward-facing BEYOND the authorised release of cmru and assay —
  a force push, a release of another product, posting anywhere.

**Authorised without asking, 2026-08-17:** editing any file in vbpub including
CMRU's lanes and rigor; merging this branch to `main`; releasing cmru and assay
via `cmru release` after the dry-run is shown.

## Done means

All of B005 and B006 implemented per the contract; **CMRU making genuine
R1/R2/R3 claims** with the Topos `/etc/passwd` fixture still tracked (O9b, no
longer deferred); gate green on the branch and again on `main` after a `--no-ff`
merge; cmru and assay released; and every deferral or limitation recorded with
its reason rather than quietly dropped.

Then wave 2 (B004) and wave 3 (B001/P34) continue in the same shape.
