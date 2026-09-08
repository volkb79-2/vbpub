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

## PR-R2 — implementation returned, gate independently re-verified GREEN, reviewer dispatched

2026-09-08. Implementer landed all of B070: `4fc13ca2` (the one
`feat(assay)!:` schema-bump commit), plus ingest/verify/fixture/W7/
migration-notes commits, LOG+REPORT at `efd3920a`. Controller independently
re-verified the gate from `gate1.log`'s own markers before trusting the
report — confirmed `tester-unified: PASS (exit 0)` /
`ASSAY_REGISTERED_GATE_COMPLETE=1` on `6797d4fa`, matching. Fresh
adversarial reviewer dispatched (never fork) against the full
`361cf628..efd3920a` diff with 8 specific things to independently verify
rather than trust the LOG's own framing.

## PR-R3 — review returned: ACCEPT-conditional, 3 blockers, ruling made, fix dispatched

2026-09-08. Round-1 review committed at `b46d0467` (amended `d7e53e53`
with a confirmatory full-suite mutant run). Design and execution judged
sound overall (all six of the backlog's own acceptance boxes genuinely
satisfied, the fixture independently confirmed as real captured StrykerJS
output, W1-W6 byte-untouched, forbid-list clean) — but 3 real blockers,
all inside B070's own scope:

1. **`candidate_count = attempted + len(discarded)` silently reintroduces
   the exact DA-R26-rejected failure mode** — an honest high-discard
   ingested report can now be refused at `MAX_CANDIDATE_CEILING` (10,001)
   *because it discarded too much*, misattributed as `UNREADABLE_ARTIFACT`
   naming `max_mutants` (a field ingested lanes never declare). Reviewer
   named 3 routes, declined to pick one. **Controller ruling: route
   (a) — make the ceiling producer-aware** (native-only; an ingested
   payload is already bounded by `MAX_INGESTED_MUTANTS`), on the
   reasoning that this matches the wave's own existing producer-fork
   pattern (`_INGESTED_ONLY_FIELDS`) and is the only route consistent
   with B070's actual purpose (never refuse an honest high-discard
   report) — route (c) would have been self-defeating, route (b) just
   moves the same arbitrary line.
2. **The "latent lie" fix (the LOG's own headline discovery) has ZERO
   tests** — reviewer reverted it, entire 4,300-test suite stayed green.
   Two specific tests prescribed, no design decision needed.
3. **`Verdict._check_discarded_disposition` was never re-narrowed** after
   `Claim`'s sentinel rule had to widen for B070 — model layer currently
   ACCEPTS both a relabeled native limit sentinel and the exact ingested
   latent-lie shape blocker 2 is about; only `verify.py` catches either.
   Confirmed by the amendment's full-suite mutant run: stubbing the check
   entirely still leaves the full suite green (4257 passed / 77 skipped).
   Re-derivation prescribed precisely, shares fixtures with blocker 2.

Also actioned in the same fix dispatch: OBS1 (stale doc-comment describing
v10 behavior), OBS2 (CONSUMERS.md/CHANGES.md overstate "refused by name" —
an inflated `discarded` + matching `candidate_count` bump together still
passes; add the qualifying clause), OBS3 (file a NEW backlog entry
recording the CompileError-vs-RuntimeError distinction `discarded`'s own
justifying sentence leans on but the record doesn't carry — REPORT's
decision to leave this OUT of B070 itself was confirmed correct, just
capture it now while fresh).

**Process finding, not the implementer's to fix**: implementer capped
`nyxloom-p106`'s gate container by mistake during host contention (a cap,
not a kill), never reverted it, didn't tell that session. Checked after
the fact — that session's gate later passed clean, no observable harm.
Standing lesson for future dispatch prompts: the `--cpus=3` instruction
needs to say "only containers whose worktree path you own" — it currently
doesn't distinguish yours from a peer's.

Same implementer resumed via SendMessage (not a fresh agent — this is a
fix round, not a checkpoint hand-off) with the ruling and all three
blockers' prescriptions. Next: await repair commit + still-green gate,
then resume the SAME reviewer (never a different one) for fix-verification.
