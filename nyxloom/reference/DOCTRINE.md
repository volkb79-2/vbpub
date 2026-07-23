# nyxloom operational doctrine — the lessons, and what they cost

> **Canonical doctrine — ships with the nyxloom product** (`reference/DOCTRINE.md`).
> This file is **not** copied into project troves. **Project-specific additions or
> overrides live in the same-named sibling `nyxloom-trove/DOCTRINE.md`** — when that
> sibling exists, read it *after* this file; it refines (never replaces) the rules
> here. One canonical source, one optional project delta.

Every rule below was paid for by a real failure. They are stated with the failure
attached, because a rule without its failure gets "optimised away" by the next
reader who cannot see why it exists.

Audience: anyone — human or agent — dispatching, gating, reviewing, or merging
work through nyxloom.

---

## 1. Gates

**Run one gate at a time. Never concurrently.**
A gate that spawns real processes (daemons, containers, browsers) competes for
memory with any other gate running beside it. Two concurrent gate containers
OOM-kill each other, and the victim reports `exit 137` (SIGKILL) — which *looks
exactly like a test failure*. Hours get spent debugging a "flaky test" that was
never failing. **Serialize gate runs; kill strays before starting one.** Parallel
*implementation* is fine and encouraged; it is the gate step that must be serial.

**A pipe masks the exit code.**
`<gate-command> | tail -40` reports **`tail`'s** exit status, not the gate's — so a
red gate reads as green. Always capture the real status:
`<gate-command> 2>&1 | tail -40; echo "EXIT=${PIPESTATUS[0]}"`.
This has produced false "all green" reports more than once.

**A gate container must give its run-uid a full identity.**
A gate that runs as a specific uid:gid (to match host-owned bind mounts) must
provide that identity completely — a `/etc/passwd` entry, an `/etc/group` entry, a
WRITABLE `HOME`, and `XDG_*` set. Otherwise tests fail on `pwd.getpwuid` /
`grp.getgrgid` KeyErrors and `PermissionError` under `$HOME/.config`, which read as
product breakage but are pure environment. (One project saw 108 spurious failures
go to zero from this fix alone.) Suspect it whenever a suite fails *only* in the
gate and only with identity/permission errors.

**The gate is not the cockpit.**
The environment you *inspect* from (a devcontainer, an admin shell) generally does
not carry the application's real dependency closure. "Green in my shell" is not a
ship signal; only the project's declared gate environment is. Every project must
declare its real gate in `nyxloom.toml` — never its cockpit.

## 2. Agents and evidence

**Trust git state. Never trust a receipt.**
An agent's self-report is a *claim*, not evidence. Verify what actually happened:
`git log`, `git status`, `git diff --stat` against the base. Receipts have claimed
work that was never committed, tests that were never run, and merges that never
happened. Verification is cheap; a false-done is not.

**A backgrounded gate produces a false-done.**
An implementation agent told to "run the gate" will background it, end its turn
waiting for a completion signal that never arrives (only the top-level session is
notified), and exit looking finished — with an unverified, possibly red gate. This
recurs with *every* agent that is given a long-running command.
**Therefore: implementation agents implement, commit, and stop.** The controller
runs the authoritative gate itself, serially (§1). Give agents only fast,
foreground sanity checks.

**Report failure honestly, including your own.**
An agent that cannot run a check must say so rather than imply success. A report
that quietly omits a skipped step is worse than one that admits it, because the
omission is only discovered after merge.

## 3. Scope and the forbidden-needed-file failure

**Every oracle must be satisfiable within `scope.touch`.**
The most expensive authoring defect is a handoff that forbids the very file the
correct implementation needs. The agent then either fakes a hollow workaround or
hard-blocks — neither ships the work, and both waste a full dispatch cycle. Check
each oracle against the allowlist *before* dispatching. (`nyxloom lint` flags the
common cases, but lint is a net, not a substitute for the check.)

**Prefer a mid-flight amendment to a re-carve.**
When an agent genuinely needs a file outside its allowlist, the cheap correct move
is a bounded scope amendment, not a hard block plus a full re-carve.

**When scope is genuinely wrong, block mechanically — do not improvise.**
A blocked agent should state exactly which file it needs and why, write that to its
log, and stop. Partial credit improvisation produces code that must be thrown away.

## 3a. Carving from documents — verify premises, do not trust status lines

**A document's `Status:` line is authored once and never self-updates.** Neither do
backlog rows, plan headers, or "ready to implement" banners. Carving a package from
them without checking produces work that is already done, already obsolete, or
aimed at a defect that no longer exists.

The failure this encodes: 13 packages were carved from plan and report documents in
one day; **nine were withdrawn the same day** because the work had already merged.
One had already reached ACTIVE and had to be killed mid-attempt. Two independent
agent digests of those documents *also* missed it — because they summarised the
documents, not the repository. **An agent digest of stale docs inherits the
staleness and adds confidence to it.**

**Therefore, before carving from any plan / backlog / workstream row:** verify each
premise mechanically against the repo — `git log --grep` for the feature or phase
name, plus a direct read of the exact files the contract would touch (does the
defect still exist? does the target still look pre-change?). Carve only contracts
whose premises verify *today*, and record the verifying revision. Git is the only
self-updating record of what is done.

## 4. Review

Reviewing a diff is not "does this look reasonable". Check specifically for:

- **Overclaimed evidence** — a report citing a test run that never executed, or
  written *before* the event it claims to have verified.
- **Hollow tests** — tests that pass without asserting the behavioural contract in
  question. The classic false-green.
- **Missing handoff requirements** — anything the originating handoff specified that
  the diff silently skipped.
- **Wrong dates** — in logs, reports, and commit messages.
- **Environment-specific claims** — a pass/fail reported from the agent's own
  environment that does not reproduce under the real gate. Re-run it (§1).
- **Stale docs after a fix** — if the diff was patched post-review, confirm any
  comment or doc describing the old behaviour was updated too.
- **Edge cases** — the thing a fast read misses.

## 5. Dispatch and merge discipline

**One dispatcher per project.**
Never run a manual controller against a project whose daemon is live and unpaused.
Two dispatchers race: an automated writer can commit a stale tree over manual work.
Stop the daemon before manual intervention, and confirm it is stopped.

**Merging is serial, even when building is parallel.**
There is one target branch. Independent work streams may be implemented in parallel
worktrees, but they land one at a time — and each should be re-based and gated
against what it will actually become, not against a base that has since moved.

**Guard the merge against a moved base.**
Check the target branch is where you last saw it before merging. If it moved,
investigate before proceeding rather than merging blind.

**The merge step is the bottleneck, not the building step.**
Work that is implemented and reviewed but unmerged is not delivered. Check for
already-finished branches before dispatching new work.

## 6. Handoff artefacts

Each implementation package produces two companion documents:

- **LOG** — written *during* the work: actions taken, commands run, files changed,
  decisions made, blockers hit. This is the resumability artefact if the agent or
  session dies mid-package.
- **REPORT** — written *after*: evidence, what was verified and how, the commit.
  This is what review checks against.

Neither is trusted at face value (§2, §4). After a controller-side correction,
*append* the outcome rather than editing the agent's original entries — the record
of what actually happened is more valuable than a tidy one.
