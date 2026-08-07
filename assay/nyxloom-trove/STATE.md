# assay — state of play

> Written 2026-08-07 at the end of the scoping-and-repair session, so a fresh
> controller can resume without re-deriving any of it. Update this file at the
> end of every session; it is the first thing the next controller should read
> after `handoffs/README.md`.

## Where things stand

**Merged on `main`:** P00 (skeleton + lane config loader), P01 (verdict model +
JSON Schema), P01c (contract repair + the reissued series). Gate green at
**733 passed, exit 0, 100% statement AND branch** (690 stmts / 298 branches),
verified in the foreground on a slice checked by `assay/tools/cgroup-parent.sh`.

**Outstanding:** P02–P14, thirteen packages, all validating against nyxloom's
real `handoff-frontmatter.schema.json` with a closed dependency graph.
**P02 is next** — *"does assay refuse to judge a diff it cannot see?"*

Key commits: `27fb88d7` (P01c + reissue), `c1bb518d` (P00/P01 rename),
`caf4fc78` (P01), `b2684da9` (P00), `a0c9e515` (original scoping).

## How the work is run

`WORKFLOW.md` is the loop. `MEASUREMENTS.md` is what it costs and what it
catches — read both; several of their claims are corrections of earlier claims
that were wrong, and the corrections are the useful part.

Per package: **readiness dispatch** (orient, report, implement nothing) →
controller rules on every raised item **in the go-message** → implement →
self-review → successor brief → controller reviews, repairs, merges → controller
asks what the result changes about *remaining* handoffs.

Three rules with scar tissue behind them:

1. **A ruling delivered only in an agent message is not applied** (A-072). It
   reaches one agent and nowhere else. Land it in `decisions.md` and in the
   handoff files before the next dispatch.
2. **Ratifications batch** — `decisions.md` is read inside the orientation
   snapshot, so editing it invalidates the snapshot. Accumulate rulings, apply
   them at a deliberate rebuild point.
3. **Run nyxloom's linter as part of review** (A-089). A defect lived through
   two implementations and two reviews because nobody ran it.

## Roles, and why they are split this way

The controller carved the original series and **reviewed its own carving**. An
external adversarial review (gpt-5.6-sol, high effort, series-level remit) then
found **23 confirmed defects**, five critical, two in already-merged code —
including an oracle that could not fail, a carving defect spanning four
packages, and a wrong sentence in the design guide.

So the roles were **swapped, not collapsed**: sol carved the repair and the
reissued series; the controller reviewed and merged. The independence is the
asset; the seating is not. If the controller carves again, something else must
review it.

**Codex sessions (resumable, keep for continuity):**
- `019fd977-f091-7dd1-8af5-38c41db89507` — the review → guidance → repair thread.
- Invoke: `codex exec --sandbox read-only -m gpt-5.6-sol -c model_reasoning_effort=high --cd <dir> resume <id> "<prompt>"`, and **run it via `run_in_background` with NO `nohup`/`&`** or the completion notification is spurious.

## Not done, and worth doing before or during P02

- **The snapshot lineage is designed and verified but NOT built.** Orientation
  measures ~142k tokens per package — more than the implementation it precedes —
  and a restored snapshot costs ~14k. That is ~128k per package across thirteen
  packages, unclaimed. `MEASUREMENTS.md` has the verified procedure.
- **S0 must be package-neutral.** The one taken for P01 was not; it carried that
  package's own handoff and plan.
- **`assay verify` / R3** is recorded as `assay verify --lane X` producing R3
  *about* lane X — deliberately not something `assay run` performs recursively.
  P14 owns it.
- Sol's own stated lowest confidence: **P08's conservative Go lexer**. It is
  fail-closed and falsifiable, but a thin fixture corpus would make it "correct
  only on toy Go". Weight the P08 review accordingly.
- Sol also flagged that **P10's ancestor/path staleness is contractual only**
  until that package lands — P01c froze the shape, not the git behaviour.

## Things that will bite a newcomer

- `/workspaces/vbpub` is shared with a concurrent committer. Commit scoped:
  `git add -- <paths> && git commit --only -F msg.txt -- <paths>`. Never
  `reset`/`rebase`/`--amend`.
- The gate runs in Docker and its bind mount uses the **host** path
  (`/home/vb/volkb79-2/vbpub`), not the container path.
- `/opt/tester-venv` exists only inside the container; there is no
  `setuptools_scm` in it, so built wheels version as `0.0.0` (A-069).
- There is **no Go toolchain** and none is needed — Go fixtures ship
  pre-generated (A-042).
- A hook blocks some scripted file edits. Use the editor, not `sed -i` or
  script-driven writes; a silent no-op reads as success.
