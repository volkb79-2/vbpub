# P4 final adversarial review — round 4

BLOCKED

## Identity and scope

- Target: `P4`.
- Required runtime: `gpt-5.6-sol`, effort `xhigh` (or an exact platform equivalent).
- Actual exposed runtime identity: Codex based on GPT-5. No `gpt-5.6-sol` route or `xhigh` effort metadata is exposed to this session.
- Worktree: `/workspaces/vbpub/.worktrees/rg55-followups-run-gate`.
- Branch: `rg55-followups-run-gate`.
- Target/final product HEAD at the precondition check: `c8f1654cc371c09e78faa6bf66feaf2aaf2e1a22`.
- Merge base with local `main`: `3df192a3b4c994cec41d1dca585139666ef812ef`.
- Initial worktree status: clean.
- Initial diff captured before edits: no. The invocation contract requires a mechanical `BLOCKED` verdict before reading the P4 diff when the Sol xhigh identity cannot be established.

## Blocker

### B1 — critical — required independent reviewer identity unavailable

- Location: `run-gate-P55-sol-final-review.md`, "Role and authority" and "Review and repair procedure" step 1.
- Observable failure: this session cannot attest that the review was performed by the mandated fresh Sol xhigh route. Treating an unverified Codex/GPT-5 session as that reviewer would create false release evidence.
- Reproduction: inspect the runtime metadata exposed to this session. It identifies Codex/GPT-5 but provides neither model id `gpt-5.6-sol` nor effort `xhigh`.
- Exact prescription: start a genuine fresh session with model route `gpt-5.6-sol` and effort `xhigh` (or a platform route whose metadata explicitly establishes the exact equivalent), provide `REVIEW_TARGET=P4` and the final-review packet path, and repeat the review from the clean target tree.

No product blocker or non-blocking item was classified because P4 was not reviewed. There are no `S<n>` items.

## Requirement-to-oracle disposition

| Requirement | Code path | Observable and negative case | Gate | Disposition |
|---|---|---|---|---|
| O1 blind review and complete classification | Not inspected | A non-Sol review must not be presented as independent Sol evidence | `selftest` | Blocked before review |
| O2 100% changed line/branch coverage | Not inspected | Prior-tree coverage must not be relabeled as this review's evidence | `assay-r1` | Not run |
| O3 complete mutation accounting | Not inspected | Prior 59/59 evidence was not independently reconciled here | `assay-r2` | Not run |
| O4 exact-tree canaries and live acceptance | Not inspected | Fake/cockpit-only evidence must not substitute for live probes | `assay-r3` | Not run |
| O5 R-36h, D-15, provenance, and verdict neutrality | Not inspected | Infrastructure conditions must not become functional verdicts | `gate-full` | Not run |

Pairwise and combined-axis attack fixtures were not designed or executed: doing so would begin the substantive review after the packet's identity precondition had failed.

## Commands and evidence

Administrative precondition checks only:

```text
git rev-parse --show-toplevel
git branch --show-current
git rev-parse HEAD
git status --short
git merge-base HEAD main
rg --files run-gate-project/nyxloom-trove/reports | rg 'P4|LOG'
```

The combined read-only command exited `0`; its outputs establish the worktree, branch, target HEAD, clean initial status, merge base, and the next review artifact name. No test, coverage, mutation, package gate, Docker container, daemon/carrier probe, or release command was started.

- Coverage line/branch totals: not produced.
- Mutation accounting: not produced; no candidates classified.
- PSI at launch: not applicable; nothing was launched.
- Container CPU caps: not applicable; no container was launched.
- Live probes: not run. Daemon/carrier availability and `place-refused:no-gates-slice` are therefore unknown, not inferred.
- Commits containing product repairs: none.
- Prior evidence invalidated: none; no product code was edited or committed.
- Targeted tests after repair: not applicable.

## Documentation, backlog, and open questions

No production code, tests, user-facing documentation, footprint, changelog, or backlog entry was changed. The packet's P4 product questions—including the real footprint provenance, bare-host rusage and lock isolation behavior, profiling-failure verdict neutrality, exact-tree mutation result, live carriers, and release ordering—remain unresolved by this session. No product decision is invented.

The only required next action is the mechanical prescription in B1. This verdict does not authorize merge, release, installation, publication, daemon startup, or work in any forbidden path.
