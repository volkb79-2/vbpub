# nyxloom-P103-ciu-governance-standalone-roots -- LOG

Chronological record. Implementer: fresh Sonnet 5 session.
Worktree: `/workspaces/vbpub/.worktrees/nyxloom-p103-ciu-governance` (branch
`nyxloom-p103-ciu-governance`). nyxloom package at `<worktree>/nyxloom`;
sibling `pwmcp` project at `<worktree>/pwmcp`, same monorepo checkout.

## Orientation

- `git log --oneline -3` in `<worktree>/nyxloom`: HEAD was `6322251b`
  ("carve(nyxloom-P103): final freeze after review ACCEPT"), matching the
  dispatch instruction exactly. No STOP condition.
- Read the handoff
  (`nyxloom-trove/handoffs/nyxloom-P103-ciu-governance-standalone-roots.md`)
  in full (935 lines), including the "Why this package exists" section, the
  reverse-dependency sweep table, the Memory sizing rationale, the Probe log,
  Environment setup, and the Gate argv section explaining why
  `tester-unified` cannot prove any oracle here.
- **No `nyxloom-trove/reports/nyxloom-P103-CARVE-REVIEW.md` file exists in
  this worktree** (checked via `find`, and via `git log --all --diff-filter=A
  -- '*P103*CARVE-REVIEW*'`, which returns nothing). The carve's git history
  (`c526e735` initial carve through `6322251b` final freeze) shows FOUR
  repair rounds folded directly into carve commits ("repair all 6
  carve-review blockers", "repair round-2 blockers R1-R4 + nits N1-N7",
  "repair round-3 blockers S1-S2 + restore L10 margin") rather than a
  separately-committed review report. The handoff document itself carries
  the full reasoning inline (Work item comments, Memory sizing section,
  Probe log), which was read in full in place of the missing file. Noting
  this as a dispatch-instruction/repo-reality mismatch, not a blocker.
- Pre-edit `escalate_if` sweep, all from the worktree root unless noted:
  - `git grep -n '\[governance\]' -- nyxloom pwmcp`: only prose/handoff/backlog
    hits (docs narrative, the handoff's own text, NL-6's own prose, INDEX.md's
    title copy) -- zero live `[governance]` table declarations. No trigger.
  - `ciu version` -> `7.11.0`, matching the carve's probed version exactly.
  - `grep -n governance pwmcp/ciu.global.toml.j2` -> no hits (still silent).
  - `grep -c 'trust = "operator"' nyxloom/routes.host.toml` -> `8`.
  - `grep -n max_active_tasks nyxloom/nyxloom-trove/nyxloom.toml` -> `max_active_tasks = 5`.
  - `dev-background.slice`'s live MemoryMax could not be directly queried
    from inside this devcontainer (no host cgroup namespace access, systemd
    not PID 1 -- the same constraint the handoff's own probe log documents).
    Confirmed no `host-setup.env` exists anywhere under
    `modern-debian-tools-python-debug/host-setup/` (only the `.example`), so
    the 8G figure remains derived from that example file exactly as the
    handoff states, with no evidence of divergence found.
  - None of the `escalate_if` triggers fired.
- Read all files named in "Context to read first" (both `ciu.global.defaults.toml.j2`
  files in full, `pwmcp-instance/ciu.defaults.toml.j2`, `nyxloomd/ciu.defaults.toml.j2`
  and `ciu.toml`, `ntfy/{ciu.compose.yml.j2,docker-compose.yml,ciu.defaults.toml.j2,README.md}`,
  `docs/plan-resource-governance.md`'s D-G0a/D-G3/D-G4 sections, NL-6's full
  body, and `ciu/src/ciu/governance.py` to confirm `GOVERNANCE_DEFAULTS`,
  `resolve_cgroup_parent`, and the `[S15.2]` error text cited in Work item 1's
  pinned comment) before making any edit.
- `docker ps`/`uptime` before starting: host load ~5.4/6.9/7.5 (three
  averages); a different package's `tester-unified:local` container
  (`nice_sinoussi`) was up, plus a large unrelated fleet of other packages'
  containers (dstdns stacks, a buildkitd, several test-runner/db/UI
  containers). None of this package's oracles start any container
  (`--dry-run` only per the handoff), so no wait was needed before the
  config-edit phase.

## `c703cd04` -- fix(nyxloom+pwmcp): nyxloom-P103 -- declare [governance] on both ciu roots, retire fictional nyxloom.slice

Work items 1, 1b, 2, 2b, 3(a-f), 4, all committed together as one coherent
edit cluster (per the handoff's own contract_class 2d framing -- every edit
is a pinned, verbatim block against a named anchor).

- **Work item 1a**: inserted the pinned `[governance]` comment+table block
  (verbatim, byte-for-byte against the handoff's pinned text) into
  `nyxloom/ciu.global.defaults.toml.j2` immediately before `[ciu]`, one blank
  line after.
- **Work item 1b**: inserted the same block into
  `pwmcp/ciu.global.defaults.toml.j2` before its `[ciu]` table, with
  `mem_limit = "4g"`. Placed the extra BUILD-TEST-ONLY/mem_limit-rationale
  comment paragraph FIRST in the header (before the shared "Resource
  governance (ciu S15.10)" text) -- the handoff's wording ("this extra first
  line inside the comment header") is ambiguous about exact position; chose
  the reading that puts the pwmcp-specific framing at the top, mirroring how
  the file's own pre-existing header already opens with its own
  BUILD-TEST-ONLY statement. Noted as a judgment call in the REPORT.
- **Work item 2**: added `[pwmcp.governance] mem_limit = "4g"` (pinned
  comment+table) to the end of `pwmcp-instance/ciu.defaults.toml.j2`, after
  `[pwmcp.tunables]` -- the handoff did not pin an exact anchor for this one,
  so it was appended at the file's natural end.
- **Work item 2b + 3d**: in `nyxloomd/ciu.defaults.toml.j2` (same file),
  deleted the `# systemd slice for the nyxloom service family` comment +
  `cgroup_parent = "nyxloom.slice"` line under `[nyxloomd.runtime]`, and
  appended the pinned `[nyxloomd.governance] mem_limit = "6g"` block after
  `[nyxloomd.logging]`.
- **Work item 3a**: deleted the `{% if ntfy.runtime.cgroup_parent %}` /
  `cgroup_parent: {{ ntfy.runtime.cgroup_parent }}` / `{% endif %}` three-line
  Jinja block from `ntfy/ciu.compose.yml.j2`.
- **Work item 3b**: REPLACED (not deleted) the `cgroup_parent: nyxloom.slice`
  line in `ntfy/docker-compose.yml` with the pinned 15-line inline governance
  fragment (comment + cgroup_parent/mem_limit/memswap_limit/mem_reservation/
  blkio_config), verbatim.
- **Work item 3c**: deleted the `# systemd slice for the nyxloom service
  family.` comment + `cgroup_parent = "nyxloom.slice"` line under
  `[ntfy.runtime]` in `ntfy/ciu.defaults.toml.j2`.
- **Work item 3e**: deleted the `cgroup_parent = "nyxloom.slice"` line in
  `nyxloomd/ciu.toml`'s `[nyxloomd.runtime]` table, keeping
  `run_as_uid`/`run_as_gid`/`docker_gid`.
- **Work item 3f**: reworded `ntfy/README.md` line 67's `` `nyxloom.slice`
  cgroup `` to `` `dev-background.slice` cgroup (via ciu governance; inline
  on the plain-compose path) ``, verbatim per the handoff's pinned
  replacement fragment.
- **Work item 4**: appended the pinned dated correction note (as a `>`
  blockquote) after BOTH occurrences of the D-G0a block (before `## D-G9` and
  before `## D-G2` respectively) in `docs/plan-resource-governance.md`, and
  two FURTHER correction notes at D-G3 (after the `dstdns -> besteffort.slice`
  sentence, before "So yes: the same image can run...") and at D-G4 (after
  the `besteffort.slice`-quoting paragraph, before "Per-container values
  remain..."). Left the narrative bodies themselves untouched per the
  handoff's "append, do NOT rewrite" instruction, and left `device`'s still-
  true half of the D-G0a blocks alone.

**Verified before committing** (see the REPORT for full verbatim output):
`ciu up --profile default --dry-run --define-root "$PWD"` -> exit 0, both
stacks' `[GOVERNANCE]` lines report `cgroup_parent=dev-background.slice`,
`device=/dev/vda (explicit)`, `services_injected=1 exempt=0`, with distinct
`mem_limit` (2g / 6g); `ciu up --profile tools --dry-run` -> `pwmcp-instance`
reports `mem_limit=4g`; `cd ../pwmcp && ciu up --dir . --dry-run` -> reports
`mem_limit=4g` and created (then torn down)
`pwmcp-016b19-network`/disconnected `dstdns-devcontainer-vb`. All static
greps (O1's committed literals, O2's four `! grep -q cgroup_parent` checks,
O2(iii)'s worktree-root `git grep -l 'nyxloom\.slice'`, O2b's inline-fragment
checks) passed on the first attempt.

**O5 mutation-checked breaks, run by hand against this commit's state before
it was made** (full transcripts in the REPORT):

- **Break (a)** (`enabled = false` on the nyxloom root table): re-ran O1 --
  both stacks show no injected `cgroup_parent` (ntfy's overlay carries only
  its configfile volume bind, grep count 0; nyxloomd's overlay is not even
  created -- `generate_overlay` returns `None` when there is nothing to
  inject, matching the handoff's own documented baseline behaviour). **One
  finding**: the handoff's oracle text says the `[GOVERNANCE]` log line is
  "absent entirely" when disabled; the actual ciu 7.11.0 behaviour prints
  `[GOVERNANCE] disabled ([<root>.governance].enabled is false)` for any
  stack that declares a `[<root>.governance]` table (nyxloomd does, via Work
  item 2b) -- a log line IS present, just without injection details. This
  does not weaken the oracle (the cgroup_parent/mem_limit absence is what
  actually matters and is confirmed), but is a factual correction to the
  handoff's stated expectation, recorded here per the handoff's own "treat a
  surprise as a FINDING, not implementer error" instruction. Restored
  `enabled = true`, re-ran O1 -- green again (both mem_limit values back:
  2g/6g).
- **Break (b), five runs, exact order, with a process lesson**: (i) deleted
  the `[governance]` block from `ciu.global.defaults.toml.j2`; (ii) wrote it
  verbatim into an untracked `ciu.global.toml.j2` (confirmed via `git status
  --short` showing `??`); (iii) ran O1 -- PASSED (both stacks' `[GOVERNANCE]`
  lines showed `enabled`, overlay `cgroup_parent` present) -- the false-PASS
  this break exists to record; (iv) deleted ONLY the untracked
  `ciu.global.toml.j2`, keeping the defaults-file deletion -- re-ran O1: the
  DEPLOY itself still exits 0, but the oracle's own assertions now FAIL
  correctly (ntfy's overlay carries no `cgroup_parent`; nyxloomd's overlay
  file does not exist at all, matching the true baseline). **Process
  finding, self-caught**: on the FIRST attempt at step (iv)'s check, a stale
  `nyxloomd/.ci/ciu.compose.overlay.yml` left over from step (iii)'s run
  (which `generate_overlay`'s early-return-`None` path does not delete or
  overwrite when governance resolves to disabled) was read and produced a
  false "still injected" reading. Traced to source
  (`composefile.py:1391-1393`'s `if not materialized and not
  configfile_mounts and not governance_injections and not image_revisions:
  return None`) via a direct Python invocation of
  `governance.resolve_stack_governance`/`resolve_config` confirming
  `enabled` resolves to `False` for a bare stack-level `mem_limit` table with
  no root layer present. Deleted all `.ci`/`.ciu` directories and re-ran
  cleanly -- confirmed FAIL as intended. **Also self-caught**: (v)
  `git checkout -- ciu.global.defaults.toml.j2` reverted the file to the
  committed HEAD baseline (`6322251b`, i.e. BEFORE Work item 1a), not to "the
  fix", because Work item 1a had not yet been committed at the point this
  break sequence was run -- the handoff's own step (v) wording only works
  correctly once the fix is a real commit. Re-applied Work item 1a's block by
  hand (verified byte-identical via `git diff` against the version committed
  moments later), then re-ran O1 -- green again. **Lesson for future
  packages of this shape**: run O5's `git checkout --`-based restore steps
  AFTER the fix is committed, exactly as the P101 precedent did (commit
  first, mutate-and-restore against that commit), never before.

Cleaned all generated artifacts (`<stack>/.ciu/`, rendered `ciu.compose.yml`s,
`ciu.global.toml`) and confirmed `git status` clean of them before this
commit. `nyxloomd/ciu.toml`'s two expected render hunks (new
`[nyxloomd.governance] mem_limit = "6g"`, stripped CR-16 comment) were seen
on every render and discarded via `git checkout --` + re-applying Work item
3e's deletion by hand, exactly as the handoff's Environment setup step 2
describes -- no third hunk ever appeared.

## `f3f85a80` -- docs(nyxloom): nyxloom-P103 -- close NL-6 with corrected mechanism/oracles, regenerate INDEX.md

Work item 5, order followed exactly (prose edit, then `set-status`, then
`backlog index`):

1. Replaced NL-6's "Observed mechanism and reproduction" and "Oracles"
   sections with corrected text (freeform prose per the handoff's own
   "degrees of freedom" note -- not a pinned block), covering all three false
   claims tabulated in the handoff plus the author-always-wins finding, and
   pointing at this package's own O1-O5 as the real replacement oracles.
2. `python3 exec-nyxloom.py backlog set-status NL-6 fixed --reason "..."` ->
   exit 0. Confirmed frontmatter now reads `status: fixed`,
   `closed_date: "2026-09-08"`, and the given `closed_reason` verbatim.
3. `python3 exec-nyxloom.py backlog index` -> exit 0. `git diff` on
   `INDEX.md` showed exactly one row moved from the `open` group to the
   `fixed` group (NL-6), nothing else changed.
4. `python3 exec-nyxloom.py lint` -> `EXIT=1`, 819 output lines; `grep -c
   BLG` on the captured output -> **0** (INDEX.md genuinely fresh,
   BLG3-clean). Separately, `python3 exec-nyxloom.py lint
   nyxloom-trove/handoffs/nyxloom-P103-ciu-governance-standalone-roots.md` ->
   `EXIT=0`, 0 errors, warnings only: 1 L10 warning, 18 L13 warnings, 4 L7
   warnings -- matching the handoff's own documented "Expected lint output"
   section (L10 count of 1, L13 all-false-positive, L7 all-expected) exactly.

## `955291c7` -- docs(nyxloom): nyxloom-P103 -- file NL-8..NL-11 (Work items 6-9), regenerate INDEX.md

**Discrepancy noted before filing**: the handoff's numbered Work items list
FOUR filing tasks -- items 6, 7, 8, and item 9 ("File a fourth backlog
entry"), the last covering nyxloomd's unmeasured memory ceiling -- but the
`scope.touch` frontmatter comment for `nyxloom-trove/backlog/` undercounts
this as "the THREE new entries Work items 6, 7 and 8 create" and lists only
those three topics. Followed the normative, numbered "Implementation packet"
Work items list (all four), since (a) it is more specific and detailed than
the summary comment, (b) the directory-level `scope.touch` permission ("Only
ADDING entries here is authorised") covers any number of new entries
underneath it regardless of the comment's count, and (c) Work item 9's own
body text is fully realised (a specific, well-motivated finding tied to two
named "OPERATOR MUST SET"/"TO REFINE" notes elsewhere in the repo) rather
than looking like leftover scaffolding. Recorded as a deviation for the
reviewer in the REPORT.

Filed via `python3 exec-nyxloom.py backlog new <title> --type ... --severity
... --component ... --provenance ... --body-from <file>` (checked `backlog
new --help` first; the handoff's own example command used `--title`, which
does not exist -- `title` is a positional argument, corrected here):

- `NL-8` (Work item 6, component `ciu-config`, severity low): pwmcp's two
  tracked `ciu.global.toml*.j2` layers.
- `NL-9` (Work item 7, component `nyxloom-lint`, severity medium): L7's
  sibling-project false-negative/false-positive pair.
- `NL-10` (Work item 8, component `nyxloom-doctor`, severity medium, type
  feature): extending `doctor`'s `cgroup-slice-missing` check to
  config/compose-declared placement, folding in the `test_render.py`
  template/sibling-agreement companion recommendation from the handoff's
  "Gate argv" section into the same entry.
- `NL-11` (Work item 9, component `nyxloomd`, severity medium): the
  unmeasured `mem_limit = "6g"` interim value.

`python3 exec-nyxloom.py backlog index` -> exit 0, `git diff` on `INDEX.md`
showed exactly four new `open` rows (NL-8..NL-11), nothing else changed.
`python3 exec-nyxloom.py lint` re-run -> `EXIT=1`, 0 BLG findings, and the
per-rule histogram (`grep -oE 'L[0-9]+ (error|warning)' | sort | uniq -c`)
was BYTE-IDENTICAL to the pre-filing baseline (2 L10 error, 32 L10 warning,
23 L11 error, 5 L12 error, 406 L13 warning, 91 L14 error, 59 L1 error, 18 L4
warning, 168 L7 error, 7 L7 warning) -- the four new entries introduce no new
lint findings of any kind.

## Final oracle re-verification (fresh, against HEAD `955291c7`)

Re-ran every static grep and every `ciu up --dry-run` oracle fresh against
the fully-committed state (all three commits above), to produce the
REPORT's verbatim evidence tied to the real final commit rather than
mid-work snapshots. `docker ps`/`uptime` immediately before: host load spiked
to ~19/14/10 from unrelated concurrent sessions (a second `tester-unified`
container, a full second dstdns stack, a buildkitd, etc.) -- irrelevant to
this package since every oracle here is `--dry-run` and starts no container.
All O1-O4 assertions reproduced identically to the mid-work runs (see
REPORT). `pwmcp`'s O3 run again created `pwmcp-016b19-network` and connected
`dstdns-devcontainer-vb`; torn down immediately after
(`docker network disconnect` + `docker network rm`, confirmed absent via
`docker network ls`). All generated artifacts (`.ciu/` dirs, rendered
`ciu.compose.yml`/`ciu.toml`s) cleaned; `nyxloomd/ciu.toml`'s two expected
render hunks appeared again and were discarded the same way. Final
`git status --short` clean.

## Gate run (regression check only -- see REPORT for why it cannot prove any oracle)

Waited for a DIFFERENT package's `tester-unified:local` container
(`run-gate-vbpub-coverage-1515189-1788843729`, a `cmru` coverage run,
confirmed genuinely active via `docker exec ... ps aux` -- real pytest with
`--cov-fail-under=100`, not hung) to clear, per the standing
one-gate-container rule. See the REPORT's "Gate run" section for the wait
and the verdict, read as a separate step from the run per LESSONS L4.

## Conclusion

All 9 numbered Work items complete (including the 4th backlog filing, see
the discrepancy note above). O1-O5 all have direct, hand-run evidence with
verbatim command output (REPORT). No `escalate_if` trigger fired. Not merged
and not claimed ready-to-merge -- a fresh adversarial reviewer's
determination, per doctrine.
