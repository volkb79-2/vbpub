# Core redesign — session handoff, 2026-08-04

Supersedes [`CORE-REDESIGN-SESSION-HANDOFF-2026-08-03.md`](CORE-REDESIGN-SESSION-HANDOFF-2026-08-03.md),
which described the tree before CR-06. Its "working loop", "gate-base trap"
and "consequences of accepted packages" sections are still accurate and are
not repeated here except where this session changed them.

The authoritative documents remain
[`CORE-REDESIGN-IMPLEMENTATION-PLAN-2026-08-02-AMENDMENT.md`](../reports/CORE-REDESIGN-IMPLEMENTATION-PLAN-2026-08-02-AMENDMENT.md)
and [`DEEP-REVIEW-2026-08-02-AMENDMENT.md`](../reports/DEEP-REVIEW-2026-08-02-AMENDMENT.md).
**Read the plan's "Implementation progress" ledger first — it is the live
state.** This file is the operating manual beside it.

## Where the program is

Merged on `main` this session, each with an independent review-and-fix pass
and the authoritative `tester-unified` gate, denominator recounted out of band:

| Package | Gate | Merge |
| --- | --- | --- |
| CR-06a — planning kernel, rule table, arbiter | 428/428 | `fa7bd004` |
| CR-06b — dispatch + attempt ladder; permutation acceptance CLOSED | 99/99 | `4759de71` |
| CR-06c — carve authority; `LEGACY_RULE_BUDGET` 0 | 187/187 | `ff3ba7f7` |
| CR-16 — liveness, channel health, silent-failure detection | 129/129 | `1f67bd22` |
| CR-16b — the host-side invoker CR-16 lacked | 0/0 | `7f89e982` |
| CR-04c — sqlite first-open schema race | 4/4 | `6af5e3a1` |
| CR-05g — the second copy of the warm-session tie | 1/1 | `c215cf0c` |
| CR-07a — workflow compiler and IR | 823/823 | `6e50b666` |
| CR-13a — execution containment, fail-closed launch gate | 248/248 | `9c46da96` |
| CR-07 prerequisite — kernel/compiler transition inventory | docs | `088e1841` |

Earlier sessions: CR-00, CR-15, CR-01, CR-02a/b, CR-03, CR-04a/b, CR-05a–f.

**`reconcile.py` 2,319 → 1,238. `daemon.py` 4,135. Both legacy budgets are 0
(`effects.LEGACY_HANDLER_BUDGET`, `planning.LEGACY_RULE_BUDGET`).**

**CR-06 is complete.** **CR-07a is complete and the §5.4 stop-loss did NOT
fire** — see the ledger row for why the reasoning is stronger than the
implementer's.

## CR-13a — MERGED as `9c46da96` (this section is kept for its lessons)

**Superseding the "do not merge" below: the hang was root-caused and fixed,
the nine uncovered refusal branches were covered, and the package gated at
`248/248 (100.0%)` on the eighth attempt. Every one of the seven prior
failures was a real defect.**

The hang: `resume_attempt` did a filesystem scan (`next_resume_n`) and a
`config.Routes.load()` BEFORE either of its refusal checks, so a resume that
was going to be refused paid for a disk walk and a config parse first, every
pass. `next_resume_n` walks resume ordinals until the filesystem port says
stop — unbounded, gated on an external predicate — and the paused-ports test
double answers `exists -> True` for every path, so it never terminated. Both
refusals now come first; the hang went away as a CONSEQUENCE of refusing
before doing work, not as the goal. Found with
`in-gate.sh <wt> tests -n auto -q -o faulthandler_timeout=240`, which dumps
the stuck thread's traceback — that is the tool for any future gate hang.

**Three gaps remain open with owners** (see the ledger row "CR-13a open
gaps"): the four interactive surfaces are the most privileged agent execution
in the system and are a HOLE rather than an acceptable boundary; the routes
sync the package REQUIRES breaks four features, so **the daemon must not be
restarted until that is resolved**; and admission still sits after durable
side effects on `effects_carve`'s legacy fresh-carve path.

The verification history below is retained because it is the transferable
part.

**CR-13a as it stood mid-session — branch `cr/nyxloom-cr13a`.**

The security design is strong and its escape proofs are real (capabilities
dropped to zero, symlinks resolving in the container's own namespace, the
host's docker-socket-proxy proven unreachable, positive controls throughout).
**Its verification has been wrong every time it was asserted.** Five
authoritative gate attempts:

1. failed — `NameError` in the fail-closed gate itself (`effects_carve.py`
   carve-via-session-resume called `admissible(ctx, ...)` in a function with
   no `ctx`), killing the whole pass via `TICK_ERROR`;
2. failed — `TestWaveLeaseUnion`, a fixture missing `trust`, bisected as
   passing on `main` and failing at CR-13a's tip;
3. failed — `KeyError` at `effects_attempt.py:109`, a bare
   `routes[action.route_id]` lookup; the pattern was copied to **five** sites,
   two as `IndexError` from `for_role(...)[0]`;
4. and 5. **HANG at 97% under `-n auto` in the gate container**, twice, with
   defunct child processes and two stuck xdist workers. **This is unresolved.**

A faulthandler diagnostic (`-o faulthandler_timeout=240`) was running when
this handoff was written; its result is the next thing to look at. If the hang
is CR-13a's, it is the same family as the other two defects — *a containment
path that does not fail fast* — and a gate that hangs is a daemon that hangs.
If it is pre-existing and merely exposed, that is an operator decision, not a
controller one: it would mean merging a package whose suite cannot complete
under the authoritative gate.

The implementer claimed "all 90 test files green across four runs". Actual:
13 failures across 3 files, plus a 4th file it never ran. **Three of the four
were found by something other than that verification.**

**CR-07b (guard-facts derivation) — gate was running at handoff. Branch
`cr/nyxloom-cr07b`, tip `6b8f9e5f`, recount 71 changed executable production
lines.** Complete and reported; needs an independent review before merge.

## CR-13a's open gaps, carried forward

1. **The sync hazard.** The deployed `$XDG_STATE_HOME/nyxloom/routes.toml`
   carries no `trust` key, so with fail-closed containment **every route in it
   refuses to launch** until synced with the tracked `routes.host.toml`. The
   daemon must not be restarted before that sync. **And the sync itself breaks
   four features**: deployed carries pre-B16 tier names, tracked carries
   post-B16, and four modules hard-code `frontier-review`, which exists only
   in the deployed copy. Alias-vs-repoint is a product call, documented not
   decided.
3. **The four interactive surfaces**, ruled a HOLE rather than an acceptable
   boundary by the security review. `intake_chat`, `decision_chat`,
   `onboarding_scan`, `onboarding_questionnaire` shell out with the daemon's
   entire environment, uncontained. "Human-initiated" describes the *request*:
   two are reached from inside the daemon via `/api/intake/reply`,
   `/api/decision/reply` and the ntfy command listener. **They now hold
   strictly more than any dispatched agent** — every route key, `AA_API_KEY`,
   both ntfy tokens, the docker socket, the operator home, every repository —
   and are the most privileged agent execution in the system. Recorded as a
   known-open gap with an owner, not as settled scope.
4. **Free routes stay disabled.** Now enforced by mechanism rather than
   prompt: `requires_containment` is True for every free route and unwaivable,
   and `unavailable_reason` returns `no-image-configured` because no agent
   image exists. Lifting it is an operator decision and needs an image
   containing `opencode`, `NYXLOOM_CONTAINMENT_HOST_MAP` set, one verified
   live dispatch, and the deployed routes synced.

## The finding this program keeps making, in its strongest form

Five packages in a row shipped **green** while something a green could not see
was still true, and it was the **same defect every time**:

> **The oracle was sound and the population it ran over was not.**

- CR-06a: a permutation test passed over a corpus containing **zero**
  `SELF_REVIEWING` tasks — those projects never composed that stage.
- CR-06b: an anti-absorption control bound to a two-action case, so all four
  mutations died on the cheapest guard; **deleting the foreign-class guard
  left the control green** (proved by ablation).
- CR-06c: the reservation branch was unreachable from all 15,966 differential
  passes — 2,901 EMIT grants, **zero** reservations.
- CR-07a: the vocabulary oracle's own anti-curation guard was bypassable by
  writing a manifest as `dict(...)`, or as a dict whose only literal key was
  not a marker, or by naming the probe file outside its glob. **This was in
  the file written to close the first three.**
- CR-13a: the launch-path enumeration behind a scope boundary was 4 of 6.

**Coverage cannot detect this class — every one of those lines was covered.**

What worked, every time: **enumerate, per enum and per population, which
members no input reaches.** That found 4 missing `TaskState` members, then 4
missing `CarverStatus` members, then the manifest-population holes.

The rule to carry forward, from CR-07a's review:

> **A coverage oracle needs a population proof, and the population proof needs
> one too, until the argument bottoms out in something DERIVED rather than
> declared.**

CR-07b's derivation oracle is the first to answer this properly: its
canonicalizer walks `fields()` and runtime types rather than a field list, and
bottoms out in a residual check (`repr` searched for the original strings)
that knows nothing about dataclasses — two independent derivations of "where
the strings are", with an ablation showing the residual check catches a
no-op walk.

## Carving principle (new, and load-bearing)

CR-07a landed at **823 changed executable lines** — the largest this program
has accepted, against a ~713-line cut CR-05 rejected as too big. Its reviewer,
*having reviewed it*, said it should have been split and was candid that it
**sampled rather than exhausted** the 492-statement parser; the Tarjan SCC
logic and the 31 diagnostic messages got proportionally less attention.

> **Put the thing whose CLAIM needs attacking in its own package, and let the
> volume of mechanical consequence follow separately.**

Applied immediately: CR-07b was scoped to the `GuardFacts` derivation alone
(71 production lines) with an instruction to stop and report if it exceeded
~300. **Package size is a review-quality property, not a preference.**

## Traps learned this session (additions to the 08-03 list)

- **Never pipe `run-gate.sh` through `tail`.** The pipeline's exit status is
  the last command's, so a FAILING gate reports success and the missing
  `diff-coverage` line is the only tell. This happened once, on CR-04c, where
  an inventory-ratchet failure read as green. The script now carries a note.
- **The inner `cd` is lost in hand-written `docker run ... bash -c` commands.**
  I did this **three times**. It is nasty because the result is fast and
  green: pytest collects zero tests from the container's default cwd and exits
  0. `run-gate.sh` refuses it; ad-hoc diagnostics had no guard, so
  `$CLAUDE_JOB_DIR/tmp/in-gate.sh` now exists for everything that is not the
  authoritative gate.
- **Agents stall on wait-loops keyed on the box going quiet** (`pgrep pytest`
  emptying, load dropping). That condition is **permanently false** — several
  agents and two other projects run suites concurrently, load routinely 30+.
  It stalled three agents for hours. Put this in every implementation brief.
- **Agents leave work uncommitted.** Two agents finished building and stopped
  without committing; the gate diffs committed HEAD, so an uncommitted tree
  yields `NO MEASUREMENT`. Tell every agent to commit early and often.
- **Do not accept partition test evidence uncritically.** Require agents to
  **name the files they ran** rather than characterise the whole suite. On
  CR-13a this failure cost four gate cycles.
- **A gate run against a worktree an agent is still editing is void.** Kill
  and re-run once the tree is settled; the gate reads the live working tree
  while `coverage_gate` diffs committed HEAD.
- **`main` moves under you.** A concurrent committer (the srdm project) landed
  repeatedly this session. Rebase before every gate, and re-verify the
  denominator after — it is cheap and it is the CR-01 failure mode.

## Environment

- Work dir `/workspaces/vbpub/nyxloom`; package worktrees under
  `/workspaces/vbpub/.worktrees/nyxloom-cr<NN>`.
- **cgroups changed on the host.** `dev.slice` and `dev-background.slice`
  exist but carry **no limits** (`memory.max=max`); `dev-interactive.slice` is
  absent and this devcontainer predates the rollout, so it is unplaced
  (placement is create-time only — it needs a rebuild). The real envelope is
  the Docker daemon-wide default: **32 GiB memory / 64 GiB swap**. A missing
  child slice degrades to a transient unlimited one, so the gate argv's
  `--cgroup-parent=nyxloom-gates.slice` still works. **The argv was left
  byte-identical deliberately** — it is a contract file, and changing it
  mid-programme would make gate evidence non-comparable with the accepted
  packages. Moving nyxloom gates to `dev-background.slice` is a proposal for
  its own change.
- The gate container has a `docker` **binary but no socket**; CR-13a's
  `requires_docker` predicate correctly probes `docker image inspect` with a
  timeout rather than testing for the binary.
- `vbpub` has a concurrent committer. Use `git commit --only -- <paths>`;
  for a NEW file, `git add -N <path>` first. Never `git add -A`.
- The nyxloom daemon stays STOPPED for the duration.

## Suggested opening move

1. Read the plan's ledger.
2. Read this file.
3. `git log --oneline -20 main`.
4. **Review and merge CR-07b** (gate-green at 71/71, branch `cr/nyxloom-cr07b`) — it needs an independent review pass before merge.
   that is otherwise reviewed and fixed. The faulthandler diagnostic is the
   tool; `in-gate.sh` runs it safely.
5. Then review and merge CR-07b, and carve **CR-07c** — the lifecycle/node
   migration: the 17 compiler edges onto compiled nodes, the CR-04 upcaster,
   `DRAFT` read-compatibility via enum `_missing_` (not an executable state),
   and repairing `stages.py`'s declared `implement.done` exit, which has
   disagreed with `effects_exit.py` for **every shipped preset including the
   default** since B5 (2026-07-20). CR-07a pinned that disagreement rather
   than fixing it, because repairing the layer being replaced belongs to the
   package that replaces it.

Remaining after CR-07: CR-08, CR-09, CR-10, CR-11, CR-12, CR-13b, CR-14.

**Queued BEHIND the redesign, specified but NOT dispatched:**
[`nyxloom-P90-extract-testing-library.md`](nyxloom-P90-extract-testing-library.md)
— extract `gate_runner`/`coverage_gate`/`gate_canary`/`mutation_gate` (~1,600
lines) into a standalone library any project can consume WITHOUT adopting
nyxloom. Written up now because the evidence is fresh (`coverage_gate.py` exists
FOUR times across the estate — 299/455/804 lines plus a Go rewrite — and all of
them have diverged), and deferred because a 1,600-line package is precisely the
shape this programme's own carving principle rejects. Fold it into the CR order
as its own numbered package, or land it after CR-14. Its own frontmatter has the
same instruction in `escalate_if`.
Section 7 of the plan is the authoritative order. Open ledger rows carrying
named owners: carve authority is planner-scoped (not system-scoped); stale
route ids crash the planner and renderer (owner CR-08); the four interactive
surfaces; §4.3 condition 6 split across CR-07a and CR-12.
