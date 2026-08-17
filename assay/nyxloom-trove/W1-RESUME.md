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

## THE REVIEW CAP IS REACHED — awaiting an operator decision on B006(a)

Round 3 (Opus, fresh context) returned **NOT READY — 8 blocking, 14
non-blocking**, so §1 has now failed three consecutive independent reviews.
Per the standing rule this stops here: no round 4, no model switch.

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

## Remaining, in order (B006(a) blocked at the top)

1. **STOPPED — operator decision required on B006(a).** Do not dispatch work
   item 1. See the block above.
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
6. **Controller's own adversarial review** — by writing real inputs to disk and
   driving the SHIPPED entry points, never by reading the diff and never by
   re-running the implementer's fixtures. Then one Opus xhigh review of the
   implementation.
7. **Gate green on the branch**, run in the foreground with the exit code
   captured honestly — never `| tail`, which returns tail's status.
8. **Merge `--no-ff` into `main`**, then gate green again on `main`.
9. **`cmru release --project assay --dry-run`**, output shown to the operator.
   **STOP THERE.**

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

* review round 3 returns NOT READY (cap reached);
* anything outward-facing: `git push`, publishing a release, posting anywhere;
* a finding that changes the wave's SCOPE rather than its detail;
* the backlog changes under us again;
* an oracle in the contract turns out to be unwritable honestly — report it,
  never weaken it into one that cannot fail.

## Done means

All of B005 and B006 implemented per the contract, gate green on the branch and
again on `main` after a `--no-ff` merge, the release dry-run shown, and O9b
recorded as deferred with its blocker named rather than quietly dropped.
