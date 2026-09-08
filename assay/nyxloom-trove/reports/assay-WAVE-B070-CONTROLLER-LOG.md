# Wave controller log — B070 v11 schema cut (2026-09-08)

Wave prompt: `assay/nyxloom-trove/WAVE-PROMPT-2026-09-08-b070-discarded-mutants.md`.
This log opens with the tail end of Wave 2's post-release checklist (found
mid-flight on resume, completed here), then records Wave 3 dispatch.

## Wave 2 (progress/resume) post-release checklist — completed on resume

2026-09-08. Resumed a paused controller session. `cmru release --project
assay` (launched pre-pause, pid 2521924) had actually already completed
successfully — log confirmed gate GREEN
(`assay: registered self-hosted gate: succeeded in 876.3s`), build+publish
succeeded, tagged `assay-v5.2.0`. A separate prior session
(`session_01JC8rMkA4eNyF9xaLDaHCbA`, visible in `git log`) had already run
the immediate post-release steps beyond what this controller's own context
had recorded: `cmru tool-deps --refresh assay` plus the manual
`cmru/run-gate.toml` pin fix (`583faad7`, which also opportunistically
re-pinned two OTHER consumers' untracked vendored zipapps — ciu at 3.2.0
and nyxloom at 4.0.0, three and one majors stale respectively, with no
`cmru.toml` declaration tracking either — and filed **KI-27** for
`--refresh` not touching a project's own `run-gate.toml`), plus an
unrelated backlog sweep (`8e3d1ae7`). Verified via `pip show assay` (still
5.1.0) and `/workspaces/dstdns/.assay-inbox/release.json` (still the
Wave 1 5.1.0 notify) that deploy and dstdns-notify had NOT yet happened —
completed both here:

- **Deploy**: `pip install --upgrade` the GitHub release wheel
  (`assay-5.2.0-py3-none-any.whl`) — `pip show assay` confirms 5.2.0.
- **dstdns notify**: rewrote `/workspaces/dstdns/.assay-inbox/release.json`
  for 5.2.0 (sha256 `09e74237d120c2504ddc88f7852f2baf264a7116761b18a618b0398911f87ead`,
  recomputed independently against the vendored `cmru/tools/assay/assay-5.2.0.pyz`
  and matched), mentioning `--progress-heartbeat`, `--state-dir`, the
  `budget = "unbounded"` admissibility rule, and the BREAKING
  `candidate_total` header change. File is gitignored in dstdns
  (`.assay-inbox/*`) — a drop file, not committed.
- **tool-deps/run-gate.toml pin**: already done by the prior session
  (`583faad7`) — verified, not re-done.
- **Cleanup**: confirmed both `feat/assay-progress-resume-2026-09-08`
  (`6ea26609`) and its review worktree's detached head (`078e3703`) were
  fully merged ancestors of `main` (`git merge-base --is-ancestor`, both
  true) before removing `.worktrees/assay-progress-resume`,
  `.worktrees/assay-progress-resume-review`, and deleting the branch
  locally and on `origin`.

Wave 2 is now fully closed: merged, released (`assay-v5.2.0`), deployed,
notified, tool-deps swept, worktrees/branch gone.

## PR-R1 — Wave 3 dispatched

2026-09-08. Fresh Opus implementer dispatched on
`feat/assay-b070-discarded-mutants-2026-09-08` from `main` @ `361cf628`
(assay 5.2.0, Wave 2 fully shipped), worktree
`.worktrees/assay-b070-discarded-mutants`. Scope: B070 alone — the last
discussed wave under the operator's `/goal proceed through impl/review/
test/release all discussed next waves for assay`. Shape already ruled by
the operator (Shape 1, list the discarded mutants, over the smaller
ingested-only-count Shape 2) — the wave prompt states this explicitly as
non-negotiable. This is a v11 MAJOR/BREAKING cut
(`VERDICT_SCHEMA_VERSION` 10 → 11), the last wave in this program; once it
ships the standing `/goal` condition is satisfied.

Host checked before dispatch: `uptime` load average 2.70 on 8 cores, no
gate container or `tester-unified-gate.sh` process running — clear to
proceed.

Next: await the implementer's LOG/REPORT + green gate, independently
verify the gate from its own log markers (never the self-report alone),
then dispatch a fresh adversarial reviewer (never fork).
