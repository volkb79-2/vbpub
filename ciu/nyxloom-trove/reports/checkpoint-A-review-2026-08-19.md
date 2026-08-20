# Checkpoint A review — ciu-P08 + ciu-P09 — 2026-08-19

**Reviewer:** dstdns Fable controller session. **Verdict: APPROVED ON-BRANCH** (both packages).
**Merge: DEFERRED to checkpoint B** — see "Plan correction" below. Implementer: opencode
(DeepSeek V4 Flash), commits `f19e5451` (P08), `f1b8e727` (P09).

## Evidence (hermetic, tester-unified:local — the trove gate, run by the reviewer)

- **Tests+coverage:** `run-ciu-tests.py` → **2092 passed / 0 failed, TOTAL 6873 stmts / 2698
  branches, 100.00%**, exit 0.
- **Differential proof of no regression:** the same container invocation on the pre-P08
  baseline (`cd672648`) → 2072 passed + the SAME 4 env-sensitive failures as the first
  (mis-invoked) checkpoint run; delta = **+16 passed (= P08's 6 + P09's 10), zero new
  failures**. The 4 failures were the reviewer's invocation missing
  `$CGROUP_PARENT_DEV_BACKGROUND` — with it passed, all 2092 green.
- **Scope:** both diffs exactly within scope.touch; zero forbid-list files (verified via
  `git diff --stat`; no `cli.py`/`engine.py`/`deploy.py`/`worktree.py`/`directives.py`/
  `providers.py`).
- **Code review:** P08 exactly per contract (final-merge validation incl. the branch's worktree
  overlay, opt-in, tagged `[S3.11]`). P09 per contract (declaration checks pre-render, content
  validation post-`os.replace` pre-mount, invalid file unlinked, key path in the error,
  fail-loud optional dep). One letter-level deviation, accepted: non-TOML-target detection
  happens at content-parse time (tagged `[S5.7]`), not declaration time — fail-loud either way.
- **Evidence-hygiene blemish (cosmetic, non-blocking):** the per-file coverage excerpts pasted
  in the two LOGs are mutually inconsistent (`config_model.py` 368 stmts in P08's excerpt vs
  257 in P09's; the inter-run TOTAL deltas don't reconcile with the claimed per-file deltas).
  The FINAL state (6873/2698/100%, 2092 tests) is hermetically verified above and supersedes
  the excerpts. Implementers: paste whole summary blocks from one run, never stitch.

## The reviewer's gate-invocation recipe (so nobody rediscovers this)

The daemon normally runs this gate; a manual run needs ALL of:
1. `-e CGROUP_PARENT_DEV_BACKGROUND=<value from the devcontainer env>` — 4 governance tests
   read it ambiently and fail without it (S15.2 by design).
2. Dual mount: `-v /home/vb/volkb79-2/vbpub:/home/vb/volkb79-2/vbpub` **and**
   `-v /home/vb/volkb79-2/vbpub:/workspaces/vbpub` — the worktree's `.git` gitfile records the
   DEVCONTAINER path, so both must resolve inside the gate container.
3. `git config --global safe.directory '*'` inside the container (uid mismatch).
4. Detached form (`docker run -d` → `docker wait` → `docker logs`), `--cgroup-parent=nyxloom-gates.slice`.

## Open finding → checkpoint B entry ticket (NOT P08/P09's)

`nyxloom.coverage_gate --base main` (the trove gate's second half) FAILS on the branch:
**6 changed lines excluded by `pragma: no cover` in `src/ciu/worktree.py`**
(`:225-226`, `:414-415`, `:668-669` — two best-effort `tmp.unlink()` OSError arcs and one
defensive `len(matches) > 1` arc), all introduced by the branch's own checkpoint `71f5ec79`,
before this lane's rules were written down. The gate's own remedy applies: **test the arcs**
(monkeypatched `unlink`; a constructed duplicate-record list) — they are reachable — or make
`--allow-excluded` an explicit reviewed argv change. **Disposition: fix inside checkpoint B**
(worktree.py is B's scope; P08/P09 are forbidden from touching it). The B merge cannot pass the
trove gate until this clears.

## Plan correction (brief rev 4)

Checkpoint A originally planned a merge-to-main + release. That would have carried the branch's
**not-yet-P07-qualified** worktree-identity implementation to main, contradicting the branch's
own plan ("once P04–P07 are complete and the serial branch is finally merged"). Corrected:
**A = review-only (this record). First merge + release happens after checkpoint B** (P07
qualification + the pragma finding cleared); second after C. No dstdns cost: config-cutover is
not blocked on a ciu release.

---

# Checkpoint B review — ciu-P04..P06 — 2026-08-19 (same reviewer)

**Verdict: APPROVED — MERGED + RELEASED as P04–P06 (operator decision: release now;
`ciu-P07-assay-qualification` is DEFERRED to the next checkpoint, not abandoned).**
Implementer: opencode (DeepSeek V4 Flash); its session ended mid-P06 — the controller
committed P06 from its completed working tree with a controller-authored LOG.

## Evidence (hermetic, tester-unified, full recipe from checkpoint A)

- `run-ciu-tests.py`: **2173 passed / 0 failed, 100.00% line+branch (7159 stmts / 2808 br)**, exit 0.
- `nyxloom.coverage_gate --base main`: **819/819 changed executable lines covered — exit 0.**
  The checkpoint-A finding (6 pragma-excluded changed lines in `worktree.py`) was fixed in P04
  (pragmas removed, arcs tested); the 3 remaining pragmas in that file predate the branch and
  sit on unchanged lines.
- Scope: P04/P05/P06 commits each within their handoff's scope.touch; forbid lists untouched.
  The operator's own local `cmru/build-initial-standalone.sh` note-edit and the untracked
  `_last-summary.txt` were excluded from all commits.

## Review finding FIXED at review (would have been a total functional failure)

P06 passed `--` to `docker exec` after the CONTAINER positional; docker executes it AS the
in-container command. **Measured live: exit 127** (`exec: "--": executable file not found`).
Every `exec --target` call would have failed despite a green suite — the argv was pinned
against a fake docker, which proves construction, not acceptance. Fixed in the P06 commit
(`666ccd9d`); test updated. **Standing lesson: any NEW docker argv shape gets one live
acceptance probe at review.**

## Deviations record

1. P07 deferred (operator: release now). CIU-29 row stays OPEN with "qualification P07
   pending"; the branch continues with P07 as its next package, followed by checkpoint C
   (P10, P11).
2. P06's LOG is controller-authored (implementer session ended pre-LOG); its content was
   verified against the diff, not taken from the implementer's summary on trust.

---

# Checkpoint P07 review — ciu-P07-assay-qualification — 2026-08-20 (same reviewer)

**Verdict: APPROVED — MERGED (`ac964b60`) + RELEASED (`ciu-v6.2.0`).**
Implementer: opencode; unblocked from its own BLOCKED (3271681f) via the
controller-approved cmru vendoring precedent.

## Evidence

- **Vendored artifact integrity:** sha256 `f2f13021…` identical across the
  vendored pyz, its pin file, and cmru's qualified copy (measured).
- **Hermetic gate (the NEW Assay-backed argv, run by the reviewer):** Assay
  verdict `ciu: PASS` at tip `db861ac2`, R0 PASS (computed, verified_by_assay)
  + R1 PASS (changed-line floor vs base=main `98549075`, allow_excluded=false,
  require_branch=true), container exit 0. Verdict JSON read directly
  (`.assay/verdict-ciu.json`), not inferred from the wrapper.
- **Release:** `cmru release --project ciu` from the vbpub root, exit 0,
  GitHub release `ciu-v6.2.0` (wheel + sha256 present, verified via gh).
  *Evidence blemish (mine):* the transaction console was piped through
  `tail`, so the release-internal gate lines were not captured and the
  isolated worktree's logs are cleaned on success. Non-blocking: the
  authoritative gate evidence is the reviewer's own hermetic run above, on
  byte-identical content. Lesson: never pipe `cmru release` through tail —
  capture the full stream.

## Three argv defects found + fixed AT REVIEW (commits 1a29b9f4, db861ac2)

The committed gate argv had never executed end-to-end (the lane was validated
with a substitute interpreter — correctly recorded as such in the LOG). Live
probes at review found: (1) missing `-e CGROUP_PARENT_DEV_BACKGROUND` — the
image does not bake it, `env_passthrough` cannot pass what does not exist, the
four governance tests red-by-construction; (2) unconditional LoadState check —
the devcontainer's `systemctl` is a shim exiting 0 with advisory stdout, so
the check could never pass in any containerized gate context (now guarded by
`[ -d /run/systemd/system ]`); (3) `sha256sum -c` resolved the pin's bare
filename against the wrong CWD (cmru's `cd tools/assay &&` shape restored).
Full detail in the P07 LOG's controller-review addenda. **Fourth consecutive
checkpoint where the only defects were in never-executed invocations against
a 100%-green suite.**
