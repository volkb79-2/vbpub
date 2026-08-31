# ciu-P37 — CIU-71: `docker compose --project-directory <repo_root>` — LOG

Backlog entry: `KNOWN_ISSUES_TODO_BACKLOG.md` `## CIU-71 — a stack's relative
build.context resolves against the compose file's own directory, not the
repo root, because ciu never passes --project-directory`.

Worktree: `/workspaces/vbpub/.worktrees/ciu-P37-compose-project-directory/ciu`
Branch: `fix/ciu-P37-compose-project-directory`
HEAD at start (base branch tip before any rebase): `a78a0046` (backlog(ciu,
run-gate): file CIU-75 ...).

**Note on hash churn:** this commit was authored once and rebased twice onto
a fast-moving `main` (see "Rebases" below) — its CONTENT never changed, only
its hash, each time because of an upstream fix landing under me while
gating. The final, real hash is `7e4e34f3dc86b39ce501a67494812ef3b0531a1d`;
earlier hashes (`5395a333...`) appear in this LOG's own drafting history
and in the FIRST (superseded) gate run below — recorded for the audit trail,
not as separate commits still on the branch.

## Commit 1 — `7e4e34f3dc86b39ce501a67494812ef3b0531a1d`
(authored as `5395a333ccf24ead989e083127e0b65af238e93b`, byte-identical
content, rebased twice — see "Rebases" below)

`fix(ciu): CIU-71 -- docker compose invocations pass --project-directory <repo_root>`

Exhaustive sweep for `["docker", "compose"` (and other compose-invoking
helpers) confirmed the backlog's own finding: exactly two real invocation
sites construct a `docker compose ... up`/`down` argv, both in
`src/ciu/engine.py`:

- `execute_docker_compose_with_logs(file_args, *, cwd, env, project)` — the
  ONE function both `main_execution` (native `up`, S8.1) and `run_shipped`
  (`--shipped` passthrough, S8.6) call to build `docker compose -p <project>
  <file_args> up -d`. `cwd` is the STACK dir (confirmed: `main_execution`
  passes `cwd=working_dir`, and `repo_root` is a SEPARATE, already-resolved
  variable in the same scope, from `REPO_ROOT`/`--define-root` — never
  transformed after that resolution). Fixed by adding `repo_root: Path` as a
  new REQUIRED keyword-only parameter and inserting
  `--project-directory <repo_root resolved>` into the constructed argv.
  Both call sites (`main_execution`, `run_shipped`) updated to pass
  `repo_root=repo_root` (both already had a `repo_root` variable in scope —
  no new resolution path added, per the task's constraint).
- `reset_service`'s `down_cmd` construction (the `docker compose ... down -v
  --remove-orphans` step). `repo_root: Optional[Path] = None` was ALREADY a
  parameter, used only in the identity-project fallback branch (when
  `deploy.project_name`/`environment_tag` are absent). Extended: a NEW
  unconditional guard (`if repo_root is None: raise ValueError(...)`) runs
  regardless of which branch resolved `compose_project`, since
  `--project-directory` is needed either way — kept `repo_root` optional at
  the type level (so the existing "project_name missing" / "label_prefix
  missing" validation-ordering tests are unaffected) but effectively
  required at runtime.

No other compose-invoking site exists: `deploy.py` delegates ALL per-stack
compose lifecycle exclusively to `engine.main_execution` / `engine.run_shipped`
/ `engine.reset_service` (confirmed by grep — its own `procutil.docker(...)`
calls are all `ps`/`network`/`volume`/`rm` inspection/cleanup, never
`compose ... up`/`down`). Two adjacent findings, deliberately NOT fixed here
(out of CIU-71's scope, recorded in the REPORT):
- `src/ciu/dev.py:_build_dev_image` (`ciu dev`) runs a plain `docker build`
  with a stack-relative `context` (no `--project-directory` equivalent even
  exists for bare `docker build`) — same DEFECT CLASS, different command,
  different fix shape. Not filed as new backlog (deferred; see REPORT).
- `src/ciu/cli.py:_bake` (`ciu bake`, `docker buildx bake`) has no `-f`/cwd
  control either, but is explicitly documented as "byte-identical to the
  pre-existing v1 behaviour" and is not a `docker compose` invocation at all
  (bake reads `docker-bake.hcl` by convention, not a compose file's
  `build.context`) — out of scope, not a compose-invocation-family bug.

Existing tests updated for the new required `repo_root` parameter
(all previously passed with the OLD signature; each is a real, executing
call site, not merely constructor coverage):
`tests/tests/test_ciu_compose_project.py` (extended, 2 new tests: argv
position + value assertions for both `execute_docker_compose_with_logs` and
`reset_service`'s down), `tests/tests/test_ciu_reset_service.py` (1 new
guard test), `tests/tests/test_ciu_worktree_lifecycle.py`,
`tests/tests/test_ciu_engine_execution_boundaries.py` (3 call sites),
`tests/tests/test_ciu_engine_branch101.py`,
`tests/tests/test_ciu_engine_remaining_boundaries.py` (exact-dict assertion
gained the new `repo_root` key).

New dedicated reproduction test:
`tests/tests/test_ciu_compose_project_directory.py` — builds a REAL
directory tree shaped exactly like the dstdns-P147b live repro (a repo root
carrying `tests/fixtures/mock_data`, a stack whose Dockerfile `COPY`s that
repo-root-relative path, and an already-"rendered" `ciu.compose.yml` with
`build.context = "."`), calls the REAL `execute_docker_compose_with_logs`
(only `subprocess.Popen` stubbed, to capture rather than run the argv — the
ciu suite is deliberately Docker-free, matching `tester-unified`'s "no
Docker socket" boundary), applies Compose's own documented
`--project-directory` resolution rule to the captured argv, and asserts the
Dockerfile's COPY source is reachable from the resolved build context. A
second test covers the `--shipped` path the same way. Manually verified
(see REPORT) that reverting the fix makes both tests fail — the first with
`resolved_context` == the stack dir instead of the repo root and the COPY
source unreachable, the second the same shape.

Docs (per AGENTS.md's "user-facing docs are part of the change"):
`docs/SPEC.md` gains **S8.1a** (new normative subsection under `## S8 —
Compose execution`, right after S8.1, since it is a bullet about the
literal invocation argv S8.1 already states), cross-referenced from S8.6's
prose and S8.7's opening sentence. `README.md`'s existing "DooD / path
correctness for free" bullet (item 4 of "Why CIU over a plain
docker-compose.yml") extended to cover `build.context`, since it is the
same "path correctness" claim the bullet already makes for bind mounts.
`docs/CONSUMERS.md` gains a new numbered **§18** (the file has no separate
TOC to update) with a worked repo-root-relative `build_context` example and
an explicit migration note for a consumer who added a stack-relative
workaround (`build_context = "../.."`) for the pre-fix bug — that workaround
now double-resolves and must be reverted to the plain form. `docs/CONFIG.md`
was checked and NOT touched: `build_context` is arbitrary stack-author TOML
consumed by the STACK's own Jinja template, never a CIU-reserved config key,
so there is nothing there to update. `docs/FEATURES.md` has a matching
"DooD path correctness" table row (S1.4/S1.9) that could analogously
mention S8.1a, but the task's explicit doc scope named only SPEC/README/
CONSUMERS — left untouched by instruction, noted here rather than expanded
on my own judgment.

## Pre-existing, unrelated findings encountered while gating (NOT fixed by
this package; all three either already filed, or independently filed and
fixed, by other concurrent implementers before this package's final gate
run — see REPORT for the full trace)

- `KNOWN_ISSUES_TODO_BACKLOG.md` **CIU-76** — `apply_lease` has no `now:`
  override (real-wall-clock-only), making
  `tests/tests/test_ciu_worktree_reap.py::TestLeaseLifecycleChangesTheNextSurvey::test_re_expiring_after_an_extend_becomes_lease_expired_again`
  permanently date-dependent. Confirmed independently (own reproduction,
  root-caused in `worktree.acquire_lease`/`apply_lease`, and reproduced on
  a plain local venv AND inside the real `tester-unified` container) before
  discovering CIU-76 already named the exact same test and root cause.
  Still OPEN as of this package's final gate run — the ONE remaining
  failure in the real gate verdict below, unrelated to CIU-71.
- `KNOWN_ISSUES_TODO_BACKLOG.md` **CIU-77** — the vendored self-test assay
  pin (`run-gate.toml [lanes.ciu.pins.assay]`) was stale (`2.2.0` while the
  vendored artifact was already `assay-2.3.0.pyz`) — hit live as a gate
  refusal (`pin 'assay' version mismatch`) on my worktree's original base
  commit; fixed on `main` by `b8102bc2` before I rebased onto it. CIU-77
  itself (assay 2.3.0 is 3 majors behind the REAL current 3.2.0) remains
  open by deliberate operator sequencing, unrelated to CIU-71.
- `KNOWN_ISSUES_TODO_BACKLOG.md` **CIU-78** (filing COLLISION, own filing
  discarded) — two `test_ciu_deploy_actions.py` tests hardcode
  `sys.dont_write_bytecode is False`, which is wrong specifically inside
  `tester-unified` (its assay lane declares `PYTHONDONTWRITEBYTECODE=1`,
  confirmed in `assay.toml`'s own `[lanes.ciu] env` table AND in the
  verdict JSON's `env_effective`). I diagnosed and root-caused this myself
  (reproduced with `PYTHONDONTWRITEBYTECODE=1 pytest ...` locally, and
  independently in the FIRST real gate run below) and drafted a
  `KNOWN_ISSUES_TODO_BACKLOG.md` CIU-78 entry — but before committing it,
  a freshness re-check (`git log --oneline HEAD..main`) found `main` had
  ALREADY gained `aa6cf1fd fix(ciu): CIU-78 -- ...`, landed by another
  agent between my two gate runs, whose own commit message says
  "Independently found and filed by both ciu-P36 and ciu-P38" — i.e. THREE
  implementers (P36, P38, and me/P37) hit this same environment defect
  independently. Discarded my own drafted entry (`git checkout --
  KNOWN_ISSUES_TODO_BACKLOG.md`, never committed) and took the fix via
  rebase instead of filing a duplicate.

## Rebases

Rebased onto `main` TWICE, both clean (no conflicts), both because a
sibling implementer's fix landed on `main` while this package's own gate
run was in flight — never because of any conflict with CIU-71's own files:

1. First rebase (`a78a0046` -> `858766d1`): picked up `b8102bc2` (CIU-77's
   pin-string fix), needed because the FIRST real gate attempt refused with
   `pin 'assay' version mismatch: declared 2.2.0, artifact reports: assay
   2.3.0` against the pre-rebase pin.
2. Second rebase (`858766d1` -> `aa6cf1fd`): picked up `aa6cf1fd` (CIU-78's
   fix, see above), needed because the SECOND real gate attempt (post
   rebase 1) failed R0 with exactly the two `sys.dont_write_bytecode`
   failures this package had independently root-caused and was about to
   file as CIU-78 itself.

Each rebase moved only the commit's HASH (`5395a333` -> `7e4e34f3` after
rebase 2; there was no intermediate hash recorded for rebase 1's landing
since it happened before the pre-rebase-1 hash was captured in this LOG) —
`git show --stat` confirms byte-identical file content across rebases.

## Gate

Three real `./run-gate.py ciu --worktree ...` attempts, in order:

1. Refused immediately: dirty tree (before the first commit existed).
2. Refused immediately: stale assay pin (`declared 2.2.0, artifact reports
   2.3.0`) — resolved by rebase 1.
3. Ran to completion, FAIL/COMMAND_FAILED: two `sys.dont_write_bytecode`
   failures (this package's own CIU-78 finding, pre-fix) + the already-known
   CIU-76 — resolved (for the first two) by rebase 2.
4. Final, authoritative run against `7e4e34f3` (this package's actual
   content, on top of every upstream fix): FAIL/COMMAND_FAILED, R1
   (100% line+branch coverage) PASS, exactly ONE failure —
   `test_re_expiring_after_an_extend_becomes_lease_expired_again`
   (CIU-76, open, unrelated). See REPORT for the verbatim verdict (read in
   a separate step from its invocation, per AGENTS.md's "read the exit
   status from the job, never the wrapper").

## Commit 3 — `19f5aebee4d2f9243b310c5b100ff36eff661354`

`docs(ciu): CIU-71 review fixes -- dockerfile/.env relocation, correct the
inverted stack-dir-relative claim`

Independent adversarial review (ACCEPT-conditional; mechanism confirmed
correct via a live `docker compose` acceptance probe, no `engine.py`
behavior change required) found 4 blockers, all documentation/backlog, plus
2 decision asks. Fixed blockers 1-3 in this commit (docs-only, plus one
already-safe print-string change); see REPORT's "Review round 2" section
for the full write-up, including every claim independently re-verified
against a real `docker compose config`/`build`/`.env` probe before being
written down (transcripts pasted in the REPORT, not taken on the reviewer's
word):

- **Blocker 1** (`docs/CONSUMERS.md` §18's worked example didn't actually
  build — live-proven by the reviewer, re-confirmed by me): added
  `dockerfile: infra/mock-targets/Dockerfile` to the worked example and
  stated explicitly (in §18 and SPEC S8.1a) that Compose resolves
  `dockerfile` relative to `context`, not `--project-directory` directly —
  the same rule `src/ciu/dev.py`'s `_build_dev_image` already applies.
- **Blocker 2** (S8.1a's own justification was factually inverted — it
  claimed CIU's other paths are already repo-root-relative; the code says
  stack-dir-relative): re-read the cited source myself (`engine.py`'s
  `create_hostdirs`, `secrets/materialize.py`'s `ASK_FILE` handling,
  `composefile.py`'s configfile schema/template resolution — all confirmed
  `stack_dir /`-relative, never `repo_root /`) and replaced the inverted
  claim in all 4 locations: `docs/SPEC.md` S8.1a, `README.md`, `docs/
  CONSUMERS.md` §18, `engine.py`'s `execute_docker_compose_with_logs`
  docstring.
- **Blocker 3** (`--project-directory` also relocates `docker compose`'s
  bare `.env` lookup — undocumented, live-proven): documented in S8.1a and
  §18's migration note. **Decision (asked explicitly by the reviewer, not
  left undecided): accept the relocation, do NOT add a second `--env-file
  <stack_dir>/.env` flag.** CIU itself never relies on bare `.env` (always
  passes `env=compose_env` explicitly, S8.2 — confirmed live that this
  already outranks either `.env` file for compose's own interpolation); a
  stack-local `.env` is a pattern CIU doesn't itself support; and a second
  existence-conditional flag would add exactly the kind of implicit,
  file-presence-dependent behavior CIU's own secrets/configfile design
  otherwise avoids.

Also (non-blocking, addressed while in the area): `engine.py`'s `--shipped`
dry-run message now includes `--project-directory` (the pre-existing `-p`
omission there is out of scope, left alone per the review's own note).

## Commit 4 — backlog/report closeout (hash recorded once committed below)

Blocker 4 (backlog untouched + REPORT self-contradiction): marked
`KNOWN_ISSUES_TODO_BACKLOG.md`'s CIU-71 row `FIXED — ciu-P37: ...`,
matching ciu-P36's `4b471e63` convention (row rewritten in place — the OLD
"OPEN" status/rationale text is fully replaced, not appended-to, after an
initial mechanical mistake was caught and corrected during editing: a
first pass inserted the new FIXED text but left the old OPEN detail text
trailing after it on the same row; re-did the edit from the row's original
(pre-round-1) text via `git show HEAD:` to get a clean base, split ONLY at
the Severity/Status column boundary, and confirmed the row's pipe count
matches its neighbors before committing). Updated the "Last updated" header
block the same way (new top paragraph, demoting the prior one to
"Previously, ..."). Fixed `nyxloom-trove/reports/ciu-P37-REPORT.md`'s own
header, which had prematurely claimed CIU-71 was "closed" while the
"Scope discipline" section said the opposite — corrected to describe round
1 accurately and point at round 2's actual closeout.

**Decision ask 1** (`src/ciu/dev.py`'s identical `build.context`/`dockerfile`
defect in `ciu dev`'s plain `docker build` invocation): filed as **CIU-79**
(re-verified free immediately before writing:
`git log --oneline HEAD..main` showed only ciu-P36's already-known,
non-overlapping merge plus one unrelated assay-docs commit — see REPORT's
"Rebase check" for the full file-overlap verification) — table row, using
this package's own round-1 REPORT analysis as the body, per the review's
instruction. Not fixed: different command (`docker build`, no
`--project-directory` equivalent exists), different fix shape (resolve
`context` to an absolute repo-root-relative path before building the argv,
not add a flag) — genuinely a separate package.

**Decision ask 2** (does blocker 2's inverted-claim correction change which
fix — (a) the flag, vs (b) document stack-relative — CIU-71 should have
chosen?): No. Fix (a) was prescribed by the carve/task itself, not a
unilateral choice I made between (a) and (b) — so there was no "wrong call"
to revisit. The corrected rationale (CIU's other paths are stack-dir-
relative, not repo-root-relative) doesn't change WHICH fix is right, only
WHY: (a) remains correct on independent grounds — a Dockerfile `COPY` of a
repo-shared asset genuinely needs the repo root regardless of what
convention CIU's OTHER paths happen to follow. Recorded here, with eyes
open, per the review's request not to leave this dangling.

## Gate, round 2

Re-ran the real gate against the round-2 tip
(`2329d1ba8637b293379b4584c0739055b9876786`): FAIL/COMMAND_FAILED overall
(exit 1), R1 (100% line+branch coverage of `src/ciu`) PASS, exactly ONE
test failure — `test_re_expiring_after_an_extend_becomes_lease_expired_again`
(CIU-76, still open on this branch: its actual fix landed on `main` via
ciu-P36's later merge `384993b6`, which this branch deliberately does not
include per the "no rebase needed" guidance, re-verified via `git
merge-base HEAD main` still resolving to the unchanged `aa6cf1fd`).
Identical failure count and identical single failure to round 1's gate run
— round 2's doc/backlog/message-string-only changes introduced no
regressions. See REPORT for the verbatim verdict, read in a separate step
from its invocation.

## Commit 5/6 — review round 3 (.env framing fix + rebase onto current main)

A second independent adversarial review pass returned ACCEPT-conditional
on round 2, with two more small items — both addressed here, on the same
branch, no new review round required per the coordinator.

**Item 1 — `docs/CONSUMERS.md` §18's `.env` paragraph was itself backwards.**
Round 2's own fix said a stack-local `.env` is "SHADOWED by a repo-root
`.env`, if one exists" and told a migrator to "confirm no repo-root `.env`
now shadows it" — implying the stack-local file only stops mattering when
a repo-root `.env` happens to exist. Wrong: `docker compose` looks for
`.env` ONLY in `--project-directory` (now the repo root) — it never falls
back to the compose file's own directory. A stack-local `.env` is dropped
UNCONDITIONALLY, present or not, on the repo-root side. Live-reproduced
before writing the fix (not taken on the reviewer's word): with a
stack-local `.env` only (`FOO=from_stack_env`) and NO repo-root `.env`,
`docker compose config` resolves `FOO` unset post-fix — not "shadowed by"
anything, just gone. Fixed the paragraph and the migration-note bullet in
`docs/CONSUMERS.md` §18 to say the stack-local `.env` "stops being read at
all" and instructs moving its values into CIU's own config/secret
mechanisms or a repo-root `.env`, never "confirm nothing shadows it".
Independently re-checked `docs/SPEC.md` S8.1a's own `.env` paragraph (the
reviewer's claim that it needed no change): it says relocation "changes
which `.env` a stack picks up" without ever framing it as conditional on a
repo-root `.env`'s existence — confirmed accurate as written, left
untouched. Committed separately (`cbe4e6f5`, later rebased to `d7830f9e`)
so the docs-wording fix is its own reviewable unit.

**Item 2 — rebase onto current `main`.** Round 2 deliberately did NOT
rebase (merge-base stayed `aa6cf1fd`), per the earlier "not needed"
guidance, leaving CIU-76 (fixed upstream via ciu-P36's `384993b6` merge)
still failing on this branch as a known, unrelated, pre-existing gap. Main
has since advanced past `384993b6` to `25d02d94` (one further unrelated
assay-docs commit, `git log --oneline 384993b6..main` confirms no `ciu/`
files touched). Re-verified there was still no dangerous file overlap
(`git log --name-only 384993b6..main -- ciu/` returned nothing) before
rebasing. `git rebase main` hit exactly one conflict, in
`KNOWN_ISSUES_TODO_BACKLOG.md`'s "Last updated" header paragraph (both
this branch and ciu-P36's merge had rewritten that same top paragraph) —
resolved by keeping this branch's CIU-71/CIU-79 paragraph as the new
"Last updated" (it is chronologically last), demoting ciu-P36's
CIU-69/CIU-76 paragraph to a new "Previously, 2026-08-31" entry ahead of
the existing CIU-78 and CIU-76/77-filed entries, and adding one sentence to
the CIU-71 paragraph noting the round-3 `.env`-framing correction. No table
rows conflicted (CIU-71/74/76/79 rows each appear exactly once,
pipe-counts verified). `git rebase --continue` completed clean; `git
status --short` empty; `git merge-base HEAD main` now resolves to
`25d02d94`, matching `main`'s own tip exactly.

**Real gate, round 3 (post-rebase, final).** Ran
`./run-gate.py ciu --worktree /workspaces/vbpub/.worktrees/ciu-P37-compose-project-directory`
from inside `ciu/` against the post-rebase tip. Verdict read in a separate
step (`.assay/verdict-ciu.json` via `json.load`, never the piped stdout
tail): **`outcome: PASS`, `exit_code: 0`.** R0: `status: PASS`,
`verified_by_assay: true`. R1: `status: PASS`, `pct: 100.0`, 22/22
executable lines, 2/2 branches, `verified_by_assay: true`. R1's
`judgment.resolved.base` is `25d02d9446146b107e60e238506a779618d5fb30` —
confirming the coverage judgment recomputed its changed-lines base against
the NEW main tip post-rebase, not a stale one. This is the first fully
green gate run across all three rounds: CIU-76, the sole R0 failure in
rounds 1 and 2, is gone now that the branch carries its upstream fix.
Verbatim run-gate stdout and the full verdict JSON are in the REPORT's
"Real gate — round 3" section.
