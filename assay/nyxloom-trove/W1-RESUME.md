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

## In flight — check before spawning anything

Use `ListAgents` first. Do NOT spawn a second implementer or a duplicate
reviewer; resume the existing one by name if it is still listed.

* **implementer (Sonnet 5, serialized — one only):** sent WI-3, the branch model
  and four parsers (§3). Was mid-edit in `src/assay/coverage*.py`.
* **reviewer (Opus, fresh, no inherited context):** adversarial round 3 over §1
  (project-scoped snapshots), §2, §6's isolation block, §7 WI-1, and
  O9/O9b/O15/O17/O18/O19.

## Remaining, in order

1. **Round 3 verdict.** If READY → dispatch WI-1a/1b/1c. If NOT READY → fold the
   findings in as Addendum E, then **STOP: the 3-round cap is reached.** Report
   to the operator; do not open a round 4 and do not switch models.
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
