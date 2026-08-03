# nyxloom — nyxloom trove

<!-- product-truth:trove_path=nyxloom-trove -->

This folder, `nyxloom-trove/`, is **nyxloom's own** nyxloom content: its
direction documents, handoffs, reports, decisions, and project-specific
instructions — the folder `nyxloom-trove/nyxloom.toml`'s `[project]` section
loads as its trove (`tests/test_product_truth.py` asserts this marker still
names the folder that is actually loaded, CR-01/DR-04).

## Where the rules live (upstream, not here)

nyxloom's canonical doctrine — how to author a handoff, the trove spec, the
operational lessons — **ships with the nyxloom product** and is read from there.
It is deliberately **not copied into this folder**, so this trove can never hold
a stale duplicate of a rule that has since changed upstream:

| Canonical doc | What it covers |
|---|---|
| `reference/AUTHORING.md` | how to write a handoff an agent can actually implement |
| `reference/STANDARD.md`  | the trove spec: what lives here, and the declaration model |
| `reference/DOCTRINE.md`  | operational lessons: gates, evidence, review, merge discipline |

## Adding or overriding a rule for this project

Create the **same-named sibling** here. Need project-specific authoring rules?
Add `nyxloom-trove/AUTHORING.md` — agents read the canonical file first, then
yours, which **refines (never replaces)** it. Keep it to the delta; never copy
the upstream text back in.

A project may also keep:

- `GUIDE.md` — how to operate this project's environment (gate invocation, stack
  and worktree setup, teardown rules). Point at it from repo-root `AGENTS.md`.
- `STANDING.md` — standing contracts inherited by every handoff in this trove.

## What nyxloom manages here

`handoffs/`, `reports/`, `archive/`, `agent-logs/`, `decisions.md`, and the
direction spine (`1-north-star.md` … `4-backlog.md`) if adopted. Configuration
lives in `nyxloom.toml`.
