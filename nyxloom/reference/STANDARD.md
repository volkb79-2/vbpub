# nyxloom project trove — SPEC & conventions

> **Canonical doctrine — ships with the nyxloom product** (`reference/STANDARD.md`).
> This file is **not** copied into project troves. **Project-specific additions or
> overrides live in the same-named sibling `nyxloom-trove/STANDARD.md`** — when that
> sibling exists, read it *after* this file; it refines (never replaces) the rules
> here. One canonical source, one optional project delta.


The **spec** for how a project exposes itself to nyxloom. Every project nyxloom
manages has one visible, tracked, tool-named folder — the **trove** — holding
all durable nyxloom-managed documents. Scaffolded by
`exec-nyxloom init <project_folder>`. This spec is **not** copied into the trove
it describes: it ships with the product and is read from there.

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
    backlog/         # OPTIONAL managed per-entry issue records + generated INDEX.md
    GUIDE.md         # OPTIONAL: project-specific agent operating guide (see below)
    archive/         # merged handoffs + reports land here
  docs/              # the project's OWN docs — nyxloom READS these (see [refs])
```

### `GUIDE.md` — project-specific operating instructions (optional, recommended)

A project MAY keep a `nyxloom-trove/GUIDE.md`: the nyxloom-specific
information and usage instructions an agent needs to *operate the
project's environment* — gate invocation, worktree/stack setup recipes,
environment modes and teardown rules, cockpit-vs-runner distinctions.
Rationale: repo-root `AGENTS.md` is the cross-tool surface every agent
CLI loads, but it should stay lean and tool-agnostic — so instead of
inlining nyxloom-specific operating detail there, `AGENTS.md` carries a
**one-line pointer** ("nyxloom-specific information and usage
instructions: see `nyxloom-trove/GUIDE.md`") and the detail lives in the
trove, versioned next to the handoffs that depend on it. Carvers should
reference GUIDE.md sections in a handoff's "Context to read first"
instead of restating environment recipes per-handoff (single source;
recipes rot fast). First adopter: dstdns (multi-stack environment rules,
2026-07-16).

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

## Direction spine (north-star / product-definition / roadmap / backlog)

A project MAY additionally adopt the managed "direction spine" -- four
numeric-prefixed trove docs (`1-north-star.md`, `2-product-definition.md`,
`3-roadmap.md`, `4-backlog.md`) with schema-validated YAML frontmatter,
non-AI-checked by `nyxloom lint`'s S1-S4 rules the same way handoffs get
L1-L12. Full contract (frontmatter schemas, `nyxloom.toml` config keys,
validator rules): see `docs/spine-documents-spec.md`. Adopting the spine is
**optional per project** -- the plain `roadmap.md`/`backlog.md` above remain
valid and are still what `exec-nyxloom init` scaffolds. nyxloom's own trove
has adopted it (see `nyxloom-trove/nyxloom.toml`'s `north_star`/
`product_definition`/`roadmap`/`backlog` keys and the four docs they point
at) as the worked example. (The spine's inbox doc is named
`4-backlog-inbox.md` since 2026-08-21 — renamed from `4-backlog.md` when the
managed per-entry backlog landed; existing projects migrate with a `git mv`
plus the one-line `backlog` config repoint.)

## Managed backlog entries (optional)

A project MAY track filed issues/features as **one file per entry** under a
managed `backlog/` dir, declared in `nyxloom.toml`:

```toml
[backlog_entries]
dir       = "nyxloom-trove/backlog"   # default shown; trove-relative
id_prefix = "CIU"                     # required; the project's id sequence
```

Entry files carry schema-validated YAML frontmatter (closed status vocabulary,
lint rules BLG2) and a human-narrative body; `backlog/INDEX.md` is GENERATED
and its staleness is itself a lint error (BLG3). The CLI surface is
`nyxloom backlog new|promote|note|set-status|list|show|index`, and the merge
flow auto-ticks an entry linked by `carved_handoff` to `merged`. Omitting the
section keeps every entry rule silent. Full contract:
`docs/backlog-entries-spec.md`; adoption recipes: `docs/CONSUMERS.md`.

## Document conventions ("managed" = enforced, not aspirational)

- **Naming:** the filename stem MUST equal the frontmatter `id` (enforced by
  lint L1) — i.e. `<id>.md`, where `id` is `<project>-P<NN>-<kebab-slug>` and
  `<NN>` is a zero-padded ordinal unique per project. (A short `P<NN>-<slug>.md`
  filename with a project-prefixed id fails L1 — see nyxloom-P23's own fix.)
  - **Component / category convention:** the id regex
    (`^[a-z][a-z0-9]*-P[0-9]{2,4}(-[a-z0-9-]+)?$`) allows only ONE hyphen-free
    token before `-P<NN>` — that token is the **real project id**, NOT a
    component. A project with components/categories encodes the component as the
    **first slug segment**: `<project>-P<NN>-<component>-<slug>` (e.g.
    `dstdns-P32-lifecycle-cancel-semantics` → project `dstdns`, component
    `lifecycle`). Do NOT make the component the pre-`P<NN>` token
    (`ui-P10`, `infra-P11`): that makes each component look like a *separate
    project* to the daemon (its own statefile namespace, registry entry, event
    log). For grouping/filtering by component, an optional first-class
    `component:` frontmatter field is preferred over parsing the slug
    (added by nyxloom-P42; until then the slug convention is the only signal).
- **Frontmatter mandatory + schema-validated** against
  `src/nyxloom/schemas/handoff-frontmatter.schema.json`. `nyxloom lint` rejects a handoff
  with missing/invalid frontmatter — that lint IS the managed-folder guard.
- **Reports pair with handoffs** and are verified against real git state, never
  trusted at face value.
- **Lifecycle:** carve → dispatch → gate (the project's declared gate, never the
  cockpit) → frontier review → merge (`--no-ff`, manual) → **archive**.
- **Archive UX:** on merge the handoff + its reports move to `archive/`. The
  dashboard keeps the **last `archive_keep_visible` (default 10) completed**
  packages visible; older ones sit behind an **Archive** button.

## What nyxloom requires of a project (the gate contract)

nyxloom orchestrates *around* a project's gate; it never supplies one. The gate is
the single load-bearing thing nyxloom trusts to answer "is this commit shippable",
so the contract is deliberately thin and **behavioural, not technological**:

**REQUIRED — the interface.** A project MUST declare at least one `[gates.*]` whose
`argv` (with the `{worktree}` placeholder) nyxloom can run at a commit in an
isolated worktree, and which **exits non-zero on any failure with nothing masking
the exit**. That is the whole hard requirement — `gate_runner.py` + the daemon's
run/verify/revert path do the rest, identically for any language or toolchain. It is
what lets dstdns (a `test-runner` container running pytest) and nyxloom (a
`tester-unified` container running pytest + a coverage floor) run under ONE daemon
with wholly unrelated gate commands.

**A gate is only as trustworthy as it is discriminating.** A declared gate that
cannot FAIL is worse than none — it launders every merge as "verified". dstdns
literally ships a `[gates.gate-probe]` whose `argv` is `true` (a reachability probe,
not a verdict); `argv=["true"]` as a project's ONLY gate would pass everything. So
beyond "a gate exists", a *meaningful* gate should:
- run in a **runtime-faithful, isolated environment** — a separate test
  container/venv, NEVER the interactive cockpit (whose pins are not a ship signal;
  see each trove's `GUIDE.md` cockpit-vs-runner note);
- **fail closed** — a wrapper's trailing `echo`/pipe must not mask the real exit
  (read the verdict in a step *separate* from the run; canonical `LESSONS.md` L4).
  "Nothing masking the exit" includes the **transport** between the gate and
  whoever reads it: a container gate reached over a truncating relay (e.g. a
  `socat` docker-socket proxy started without `-t`) can drop output mid-run and
  hand back a *forged* exit code, so a failing gate reads as passing — the same
  L4/L18 aliasing, one layer down in the plumbing, and invisible to everything
  *inside* the container. Two defenses: run container gates **detached**
  (`docker run -d` → `docker wait` for the code → `docker logs` for the output,
  never the attached/hijacked stream — this is what `gate_scaffold` emits); and
  probe the transport before trusting a verdict — `nyxloom doctor` fails closed
  when `transport_check.probe_default()` detects a truncating transport;
- ideally enforce a **completeness floor** (e.g. changed-line coverage) and run in
  **parallel** so the floor stays affordable.

For a broader, risk-based catalogue of deterministic and asynchronous evidence
(mutation, property testing, fuzzing, remote workers, and their limits), see
[`TESTING-METHODOLOGY.md`](TESTING-METHODOLOGY.md).

**Gate rigor is a first-class, per-project fact.** A weak gate shifts the
correctness burden onto the reviewer, so a project SHOULD declare what its gate
actually asserts (the `asserts=[tests-pass|changed-line-coverage|mutation|
canary-verified|assay-verdict]` key on `[gates.*]`) — nyxloom surfaces it and
routes review depth accordingly. Declaring a coverage floor is offered, not
mandated — advisable wherever the ecosystem supports it (`cargo llvm-cov`/`nyc`,
or your project's own declared assay/run-gate lane), but a project that runs tests
without one is still a valid consumer; it simply leans harder on the reviewer. Full
rationale + the layered model (gate ⊕ reviewer ⊕ controller): `nyxloom-trove/
LESSONS.md` PL2. The onboarding/verification workflow (offer to build a missing gate;
carver re-verifies the gate still rejects a known-bad canary): `docs/plan-gate-adoption.md`.

## Validation methodology — building gates and tests that actually catch bugs

The gate contract says a gate must *discriminate*; these are the hard-won practices
that make one that genuinely does (learned building nyxloom's own gate + `gate
verify`). They apply to any consumer project's suite, not just nyxloom's.

1. **A green gate is not a correct one.** A coverage floor proves every changed line
   *ran*; it cannot prove the logic is *right* or that a test *asserts* the right
   thing. Pair the deterministic gate (coverage/mutation) with an adversarial review
   that attacks the logic. Worked example: `gate verify` v1 passed its own gate at
   100% coverage while returning the *wrong verdict* (it probed the wrong directory)
   — only the review caught it. Gate ⊕ reviewer ⊕ controller each catch a class the
   others structurally can't (canonical `LESSONS.md` L2).
2. **Never mock the component under test.** At least one test must exercise the REAL
   thing end-to-end. When every test of a verb mocks the gate it drives, no test
   proves the verb works — the bug ships green. Add a real integration test (real
   command, real inputs, a decoy that would trip the bug if present).
3. **A regression test must fail *before* the fix and pass *after*.** Prove the test
   catches the bug: run it against the pre-fix code (must fail) and post-fix (must
   pass). A test never seen to fail is not known to test anything.
4. **A probe/canary must land where the gate actually looks.** If you inject a
   known-bad change to check a gate rejects it, put it in code the gate *runs* (the
   project's own tested subtree), never just anywhere reachable in the repo. Prefer a
   robust minimal signal (an import-break: one added `raise` at a module's top →
   every test importing it fails) over a fragile one (a covered-line mutation that
   needs coverage knowledge and can reformat the whole file).
5. **Parallel coverage must be parity-checked against serial.** Before trusting a
   parallelized coverage gate, confirm per-file executed-line PARITY (serial vs
   parallel). The dangerous direction is serial-covered-but-parallel-missed (future
   false-FAILs). Separate intrinsic flakiness (two *serial* runs already disagree)
   from a real parallel gap (serial-stable but parallel-missed).
6. **Coverage that only fires incidentally is hollow.** A line "covered" only because
   an integration test happened to fork a child that ran it has no deterministic test
   behind it. When a parallel runner stops crediting it, the gate is *exposing a
   hollow test*, not miscounting — fix it with a deterministic in-process test, not by
   recapturing the incidental coverage (`LESSONS.md` PL3).
7. **Keep the run and the verdict separate; fail closed.** Read a gate's pass/fail in
   a step SEPARATE from running it — a wrapper's trailing `echo` or a pipe can mask
   the real exit code (`LESSONS.md` L4). A gate that cannot fail is worse than none;
   prove it rejects a known-bad canary.

## `exec-nyxloom init <project_folder>`

Scaffolds a trove into a target project from nyxloom's bundled templates.
Because it runs through the **running nyxloom instance** (`exec-nyxloom` →
`docker exec` into the container, host fallback), it also *proves the instance
can reach the project folder* — a built-in access check. It writes
`nyxloom-trove/{nyxloom.toml, STANDARD.md, handoffs/, reports/, decisions.md,
roadmap.md, backlog.md, archive/}` and leaves `[refs]` for the operator to fill.

**Onboarding best practice — the gate is the highest-value setup step.** `init`
scaffolds the trove but does NOT manufacture a trustworthy gate; that is
project-specific and must be worked deliberately (the **gate-adoption checklist**,
`docs/plan-gate-adoption.md`): (1) confirm a **separate** runtime-faithful test
environment exists — not the cockpit; (2) wire a `[gates.*]` that **fails closed**;
(3) opt into the **coverage floor + parallelism** where the ecosystem supports it;
(4) **verify the gate actually REJECTS a deliberately-broken commit** before
trusting it. If a project has no gate, or an untrustworthy one, onboarding should
*offer to create* the separate test env + gate rather than register a project whose
merges nyxloom cannot actually verify — a project without a real gate is not
factory-ready.

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
