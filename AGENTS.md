# AGENTS.md — vbpub agent instructions (cross-tool)

Read by every agent CLI (codex, opencode, Claude Code, and — via its prompt —
Reasonix). Repo-wide rules; each project adds its own specifics under its
`nyxloom-trove/` (see below).

## Canonical doctrine ships with nyxloom (`nyxloom/reference/`)
nyxloom's cross-project doctrine lives **with the product**, never copied into a
trove: `reference/AUTHORING.md` (handoff contract), `reference/STANDARD.md`
(trove spec), `reference/DOCTRINE.md` (operational lessons — gates, evidence,
review, merge discipline). A project adds to or overrides any of them with the
**same-named sibling** in its own `nyxloom-trove/` — read canonical first, then
the sibling, which refines (never replaces) it. Everything below is *this repo's*
delta on top of that doctrine.

**Working on nyxloom itself?** Also read **`nyxloom/nyxloom-trove/DOCTRINE.md`**
(its project delta: structlog reserved-key traps, and `build_dispatch`'s hard
argv budget) and `nyxloom/nyxloom-trove/STANDING.md` (the current wave's frozen
files). A hand-started CLI agent gets only this file — a nyxloomd-dispatched one
gets the set injected — so check `ls nyxloom/nyxloom-trove/*.md` yourself.

## Writing a handoff / dispatch prompt — honor AUTHORING.md
When you are asked to **start an agent for a task**, or to **write a prompt or a
handoff package**, first read and follow
**`nyxloom/reference/AUTHORING.md`** (the handoff-authoring guide). A handoff
is only as good as its contract: a strong detailed contract, an explicit
"Context to read first" (name the exact files/sections — the token lever),
oracles that assert the *behavioral* contract (not hollow tests), a real gate,
and a **mechanical BLOCKED escape hatch** (escalation is trigger-based, not
"reflect on your expertise"). Product calls become `D-<NNN>` decisions, not
BLOCKED. The guide's frontmatter section makes the handoff nyxloom-compatible
(schema-validated by `nyxloom lint`).

## Defaults and fallbacks are hazards (MANDATORY, estate-wide)

> **A default is legitimate only when it is a policy choice that is correct in
> the absence of information. It is a hazard the moment it substitutes for a
> fact that exists somewhere else.**

The test: *if this default is wrong, does anything fail loudly?* If not, it is
not a safety net — it is a silent wrong answer with a fallback's reputation.
Prefer, in order: **DERIVE** what has a derivation, **READ** what has a source,
**FAIL** otherwise. Never invent.

| # | Anti-pattern | Shape |
|---|---|---|
| 1 | **Shadowing default** | a literal standing in for a value that has an authoritative source |
| 2 | **Silent-invention default** | the *consumer* invents on absence instead of refusing (Docker creating a missing bind source as an empty dir) |
| 3 | **Masked default** | a wrong default rendered harmless by later code — **invisible to testing**, because every context you would observe it in runs the masking step; it surfaces only where that step is skipped |

Corollaries: a required host path gets Compose's `${VAR:?msg}`, never
`${VAR:-fallback}`; an error message that prescribes a fix must prescribe a
*correct* one (read the value rather than demanding the operator type it); and
never validate a namespace-translated path with a local filesystem call — an
`is_file()` on a container→host translation asks the wrong kernel.

Incidents behind this: ciu CIU-14 (missing bind source phantom-mounted an empty
dir), CIU-15 (its own fix stat'd the daemon's path, which no devcontainer can
resolve, turning a fail-open into an unconditional fail-closed), and dstdns
`b9257cea` (a guard whose printed remedy set the container path where the host
path was required — the test-runner then mounted an empty directory over the
repo for ~16h without erroring). Long-form: `nyxloom/reference/LESSONS.md`.

## The gate is never the devcontainer (cockpit doctrine)
The devcontainer is a **cockpit** (inspect + drive). The gating suite runs in a
dedicated container, never here. For the vbpub family that is
**`tester-unified`** (see `tester-unified/`); it must give the run-uid a full
identity (passwd+group+HOME+XDG). "Green in the devcontainer venv" is not a ship
signal.

## Host cgroup placement for spawned containers
This host runs real production workloads (game servers, edge/site infra)
alongside dev/test/build work, so any container you or a tool starts must be
placed on the host, never left at Docker's unconfined default. The
devcontainer names both tiers as environment variables (injected by
`devcontainer.json`'s `containerEnv`) — read one of these, never hardcode a
slice name:

- `$CGROUP_PARENT_DEV_INTERACTIVE` — this devcontainer's own tier (already
  applied via `runArgs`; you don't need to pass this yourself).
- `$CGROUP_PARENT_DEV_BACKGROUND` — the shared tier for a test/gate/build
  container you spawn (`docker run --cgroup-parent=$CGROUP_PARENT_DEV_BACKGROUND
  ...`). `cmru`'s `tester-gate` (`cmru/src/cmru/tester_gate.py`) and `ciu`'s
  governance mechanism (`ciu/src/ciu/governance.py`) both already resolve this
  automatically — an explicit `CMRU_TESTER_CGROUP_PARENT` (cmru) or
  `[<root>.governance].cgroup_parent` (ciu) still overrides it when a project
  genuinely needs something else.

**No hardcoded fallbacks.** If neither variable nor an explicit override is
set, that is a configuration error — refuse to launch (or let the tool's own
preflight refuse), never fall through to Docker's unconfined default next to
production. A typo'd or nonexistent slice name fails **open** (systemd
silently auto-creates an unlimited transient slice), so any code that accepts
a slice name should verify it's actually a loaded unit first
(`systemctl show <slice> --property=LoadState`) rather than trust it blindly.

## Worktree protocol
Parallel implementation runs in `.worktrees/<branch>` (branch from `main`).
Merge serially onto `main` with `--no-ff`; expect minor overlap reconciliation.
Keep packages small + non-overlapping to parallelize. Each worktree has its own
index, so `git add`/`commit` there is private and safe.

## Committing from the shared `main` checkout
The main checkout (`/workspaces/vbpub`) is shared: another agent's serial merge
may `git add`/commit at any moment, so its index is not yours to trust. A plain
`git add <paths> && git commit` can capture whatever a concurrent `git add`
staged — observed live: a wings commit that swept in another agent's `cmru/`
files under the wrong message. When committing from here (not from an isolated
worktree), **scope to explicit paths and bypass the shared index**:

    git commit --only -F msg.txt -- <your paths>     # commits only these paths

then verify: `git show --stat HEAD --name-only | sed 's#/.*##' | sort -u` lists
only your dirs. Do **not** `reset`/`rebase`/`--amend` to repair a contaminated
commit — HEAD may have already moved under concurrent commits; leave the bad one
buried and land a correct new commit instead. (Inside a private worktree none of
this applies — commit normally.)

## User-facing docs are part of the change, not a follow-up (MANDATORY, estate-wide)

> **A capability is not shipped when its code is green. It is shipped when a
> human who does not know it exists can find it, understand why it works that
> way, and adopt it — before the merge, not after.**

Three documents, one job each. Do not blur them; they were already drifting
when this rule was written.

| document | its one job | failure if skipped |
|---|---|---|
| **README** | **WHAT** the product does — the user-facing feature surface | a reader is told the opposite of what the tool does |
| **DESIGN-GUIDE** (`docs/`) | **WHY** it does it that way — choices, rejected alternatives, reasoning | the next agent re-argues a settled question, or reopens a rejected option |
| **CONSUMERS.md** (`docs/`) | **HOW** to adopt it — worked examples and real use cases | an adopter follows the guide and gets a refusal with no hint why |

A README feature **links** to its DESIGN-GUIDE section rather than re-arguing
the rationale there; CONSUMERS.md shows something an adopter could **paste**,
not a description of one.

**The obligation:** any work item that adds, removes or changes a user-facing
capability, a public config key, a closed vocabulary value, or a compatibility
fact is **INCOMPLETE until all three are in sync**, and that sync lands in the
work, not in a later tidy-up commit. State it in the work item's own file list
so it cannot be forgotten silently.

**Make it a test, not an intention** — "we will remember to update the docs" is
precisely the check that cannot fail, which is this estate's most expensive
recurring defect. Each of these must be able to go red:

1. **every config example in all three documents parses with the SHIPPED
   loader**, and declares the current schema version;
2. **every value of every closed public vocabulary a consumer must type**
   appears in at least one of the three, so a capability cannot ship
   undocumented;
3. **every cross-document anchor resolves.**

Incident behind this: assay wave 1. The plan survived a carve, three failed
review rounds, a full recarve and an independent adversarial review — and all
of them missed that the README's headline bullet said "changed-line coverage,
**not** whole-project coverage" while the wave was shipping exactly the
whole-target mode that denies, and that `docs/CONSUMERS.md` was named in **no
work item at all** while its adoption steps had gone stale against a now-
mandatory config table. Both had the same cause: trove documents get touched
every day by the process, and the documents facing a human adopter get touched
only when somebody remembers. Recorded as assay `decisions.md` **A-270**.

## Carving for a project — where the specifics live
Project-specific constraints a carve/review agent must honor (schema policy,
gate command, stack/mutex rules, product invariants) live in that project's
`nyxloom-trove/nyxloom.toml` (`[gates.*]`, `[refs]`) and, when distilled, that
project's own `AGENTS.md`. Read `nyxloom/reference/STANDARD.md` (the canonical
layout spec), any `nyxloom-trove/STANDARD.md` sibling the project adds, and its
`[refs]` docs before carving for it. Do NOT rely on the
historical `legacy-workflow-origin/` docs — their live rules are already in
nyxloom (schema/lint/review) and this file.

## A check is only as strong as what it actually compares (MANDATORY, estate-wide)

> **A check's message states a conclusion. The code states a comparison. When
> the comparison is narrower than the message, the tool does not merely fail to
> catch something — it issues a false certification, which is worse than having
> no check at all.**

The habit that finds these, and it is cheap: **for every status your code can
report, list the distinct real-world conditions that collapse into it.** Then
check both directions — that the benign condition is not reported as the
alarming one, and that the alarming one is not reported as benign.

| # | Anti-pattern | Shape |
|---|---|---|
| 1 | **Name for object** | verifying that an identifier *exists* remotely while claiming the *thing* is verified |
| 2 | **Absence for emptiness** | folding "could not reach it" into "there is nothing there" |
| 3 | **Type for behaviour** | asserting an exception's *type* where two different causes raise the same type |
| 4 | **Superset refusal** | a refusal whose condition also matches an ordinary, legitimate state |

Corollaries. A refusal that blocks work must have its **legitimate state
constructed before it ships** — a gate that cries wolf on a healthy estate gets
switched off, and then it protects nothing. Any status meaning "I could not
determine this" must be reachable *only* from genuine indeterminacy, never as a
fallback for a failed comparison. And when you fix such a check, re-read its
message: it was usually already claiming the stronger thing.

Incidents behind this, all four found in one cmru change set and all four the
same shape: a tag-**name** presence check whose message asserted a verified
release baseline (a local tag reusing a published name at a different commit
certified a false baseline and silently dropped real work); `prefix + "-v"`
doubling reported as *"no release exists yet"*; **HTTP 404 on a release list
folded into "nothing published yet"**, so an inaccessible or renamed repository
would have stopped being checked forever, fail-open; and an abort on "tag at *or
ahead of* the snapshot" that fired on the ordinary just-released state while
advising `git tag -d` on a real release tag. Detail: `cmru/docs/CONTRIBUTING.md`
§5 and `cmru/KNOWN_ISSUES_TODO_BACKLOG.md` KI-12.

## Read the exit status from the job, never from the wrapper (MANDATORY, estate-wide)

> **A compound, piped, or backgrounded invocation reports *its own* status. If
> you did not capture the status of the thing you care about, you do not know
> it — and the number you are looking at will usually be `0`.**

* Never `cmd | tail` (or `| head`, `| tee`) and then read `$?` — that is the
  pager's status. `set -o pipefail` or `${PIPESTATUS[@]}` if you must pipe.
* A backgrounded job's completion notice reports the *wrapper*. Append your own
  marker and read that:

  ```bash
  <long running thing> > /tmp/x.log 2>&1
  echo "EXIT=$?" >> /tmp/x.log
  grep EXIT /tmp/x.log
  ```

Incidents behind this: a gate run that **failed** was reported as "exit code 0"
because the reported status belonged to the compound command; `ciu provenance`'s
real exit status (2) was read as 0 through a `| head`, which put a wrong
conclusion into a design document; and two cmru mutation-campaign runs that
executed **zero mutants** were both announced as "exit code 0" while their logs
said `MUTATION_EXIT=1`.
