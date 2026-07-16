# nyxloom project trove — SPEC & conventions

The **spec** for how a project exposes itself to nyxloom. Every project nyxloom
manages has one visible, tracked, tool-named folder — the **trove** — holding
all durable nyxloom-managed documents. Copied per project (it *is* the template)
and scaffolded by `exec-nyxloom init <project_folder>`.

## Why a visible `nyxloom-trove/` (not a hidden `.nyxloom/`)

A dotted `.nyxloom/` reads as config-only and hides from a casual `ls`. A
visible, tool-named folder announces "these are nyxloom-managed resources — they
follow the standard below." It also can't be confused with `nyxloom/` the
project home (the tool's own source tree).

## Directory structure

```
<project>/
  nyxloom-trove/
    nyxloom.toml     # config — schema-validated by `nyxloom lint`
    STANDARD.md      # this spec, copied per project
    handoffs/        # work packages: <id>.md (stem == frontmatter id, lint L1), YAML frontmatter, lint-gated
    reports/         # P<NN>-LOG.md (during) / P<NN>-REPORT.md (after)
    decisions.md     # decisions inbox — product calls (D-<NNN>)
    roadmap.md       # self-dev milestones
    backlog.md       # un-carved ideas
    archive/         # merged handoffs + reports land here
  docs/              # the project's OWN docs — nyxloom READS these (see [refs])
```

## Where nyxloom's data lives — the trove vs. the state volume

Two homes, chosen by what the data *is*:

- **The trove (in the repo).** Durable managed docs — handoffs, reports,
  decisions, roadmap, backlog, archive — **and per-attempt agent logs**
  (`nyxloom-trove/agent-logs/<att-id>/`: spec.json, receipt.json, attempt.log).
  Agent logs are **gitignored by default** (churny, regenerable) but MAY be
  committed for full traceability (edit the trove `.gitignore`, or force-add a
  run). Locality: a project's entire nyxloom footprint — what to do *and* what
  happened — sits in its trove.
- **The `nyxloom-state` volume (the daemon's DB).** The source of truth the
  daemon reconciles from: the append-only **event log**, statefiles, the
  **registry** (which projects exist), **routes** (model routing), **leases**
  (cross-project mutexes), the pidfile. This is a dedicated **persistent docker
  volume** mounted into the nyxloomd container — NOT the host home
  (`~/.local/state/nyxloom` was a transitional artifact of binding the whole
  home for CLI auth). Container-native, survives restart, safe from
  `git clean`, and — unlike the trove — never entangled with a checkout's git
  state. Inspect it via `exec-nyxloom` (which execs into the container).

Rule of thumb: **the trove is what a human reads and versions; the state volume
is what the daemon trusts.** Agent logs live in the trove (a human wants them
next to the work); the event log lives in the volume (the daemon's ledger must
not be wiped by a `git clean` or a branch switch).

## Declaration model — nothing nyxloom touches is implicit

Every document nyxloom **manages or reads** is either:
1. **under the trove** (managed — nyxloom may write it), or
2. **declared in `nyxloom.toml [refs]`** (read-only — lives in the project's own
   `docs/`, nyxloom depends on it but never edits it).

If it's neither, nyxloom doesn't know about it. `nyxloom lint` (config
schema-validation) flags a `[refs]` path that doesn't resolve.

## Document conventions ("managed" = enforced, not aspirational)

- **Naming:** the filename stem MUST equal the frontmatter `id` (enforced by
  lint L1) — i.e. `<id>.md`, where `id` is `<project>-P<NN>-<kebab-slug>` and
  `<NN>` is a zero-padded ordinal unique per project. (A short `P<NN>-<slug>.md`
  filename with a project-prefixed id fails L1 — see nyxloom-P23's own fix.)
- **Frontmatter mandatory + schema-validated** against
  `schemas/handoff-frontmatter.schema.json`. `nyxloom lint` rejects a handoff
  with missing/invalid frontmatter — that lint IS the managed-folder guard.
- **Reports pair with handoffs** and are verified against real git state, never
  trusted at face value.
- **Lifecycle:** carve → dispatch → gate (the project's declared gate, never the
  cockpit) → frontier review → merge (`--no-ff`, manual) → **archive**.
- **Archive UX:** on merge the handoff + its reports move to `archive/`. The
  dashboard keeps the **last `archive_keep_visible` (default 10) completed**
  packages visible; older ones sit behind an **Archive** button.

## `exec-nyxloom init <project_folder>`

Scaffolds a trove into a target project from nyxloom's bundled templates.
Because it runs through the **running nyxloom instance** (`exec-nyxloom` →
`docker exec` into the container, host fallback), it also *proves the instance
can reach the project folder* — a built-in access check. It writes
`nyxloom-trove/{nyxloom.toml, STANDARD.md, handoffs/, reports/, decisions.md,
roadmap.md, backlog.md, archive/}` and leaves `[refs]` for the operator to fill.

## Config is schema-validated

`nyxloom.toml` has its own JSON schema (like the handoff frontmatter schema), so
`nyxloom lint` catches config typos — a bad gate `argv`, a missing
`worktree_root`, an unresolved `[refs]` path — before dispatch, not at runtime.
The dashboard reads `nyxloom.toml` to show each project's gate, channels, and
folders without opening files.

## Migration (existing projects on a root `handoff/` or `.nyxloom/`)

`git mv handoff nyxloom-trove/handoffs && git mv nyxloom-trove/handoffs/reports
nyxloom-trove/reports`, seed `decisions.md`/`roadmap.md`/`backlog.md`/`archive/`,
then repoint `nyxloom.toml`. One deliberate pass per project (handoff prose
cross-references paths). nyxloom did this to itself first (dogfooding).
