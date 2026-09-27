# P111 report: primary Nyxloom CLI audit for cli-extended

Date: 2026-09-26; operator review updated 2026-09-27
Result: **IN PROGRESS** - the audit artifacts are complete. The original gate
attempt was refused on the dirty worktree; the operator has since authorized
feature-worktree commits and removed that blocker. O6 awaits a complete gate on
the final committed audit package revision.

## Summary

The installed primary CLI is declared and dispatched through
`src/nyxloom/cli.py`. The inventory contains 35 top-level choices, 55 parser
nodes, 45 dispatchable leaf actions, one empty reserved `gate`, 37 positional
declarations, 158 action-local option declarations (168 spellings), and two
root global options. The complete current-state syntax, options, effects,
environment inputs, documentation accuracy, and adoption recommendations are
in [`docs/CLI-REFERENCE.md`](../../docs/CLI-REFERENCE.md).

Safe probes established the parser-to-dispatch mapping, invalid-input
behavior, global-option placement, help/version output and side effects, a
write performed by `backlog list`, and documented command-example validity.
The source, tests, package boundaries, and docs were inspected without running
stateful operations against real project or remote state. Existing test
assertions support selected dry-run and safeguard claims. External
notifications/provider calls and sensitive mutations were not live-executed;
the reference does not certify those from an untested probe.

Two concrete behavior defects were recorded for follow-up: help/version
create a log file before displaying, and `backlog list` creates a missing
index. Several accepted flag combinations are ignored, including doctor
rebuild/write flags with `--liveness`; proposals are captured in D-002/D-003.
No runtime, tests, packaging, or library source changed in this audit.

## Counts and probe ledger

| Measure | Result |
|---|---:|
| Top-level choices / parser nodes | 35 / 55 |
| Dispatchable leaves / empty reserved path | 45 / 1 (`gate`) |
| Positional declaration occurrences | 37 |
| Action-local option declarations / spellings | 158 / 168 |
| Root global options | 2 (`--debug`, `--version`) |
| Valid option declaration probes | 158 / 158 parsed |
| Valid leaf dispatch probes | 45 / 45 reached the intended stub handler |
| Invalid grammar/combination cases | 94 expected refusals or handler validations |
| README/CONSUMERS command examples | 48 parsed; 0 failures |
| Local document links/anchors | 32 checked; 0 missing at final check |

The detailed ledger records argv, environment, fixture, streams/status, side
effects, and evidence in [`nyxloom-P111-LOG.md`](nyxloom-P111-LOG.md). One
intentional parser-to-handler validation is called out: tool-call intent
requires visible tool-call output.

## Oracle traceability

| Oracle | Result | Evidence |
|---|---|---|
| O1 - canonical grammar reference | PASS | Parser/dispatch introspection and declaration-level tables in `docs/CLI-REFERENCE.md`; every positional and option alias/path is represented. |
| O2 - behavioral audit | PASS WITH LIMITS | 94 invalid cases, 158 valid option probes, 45 leaf dispatch probes, placement/display/backlog-write probes, and inspected effect assertions. Remote/provider and live stateful effects are explicitly unverified, not certified. |
| O3 - design review | PASS | Current behavior, concrete user journeys, overlap, risk, scope, migration, and compatibility are recorded for command-design proposals and NL-10/NL-13..NL-22. The operator resolved D-001..D-016 on 2026-09-27. No runtime choice was silently shipped. |
| O4 - docs and package boundary | PASS | README links the canonical reference; Design Guide distinguishes current and target behavior; Consumers examples parse; 32 links/anchors resolve; the P111 handoff lint exits 0 `clean`; package inclusion gap and isolated-wheel oracle are documented. |
| O5 - no runtime migration | PASS WITH OPERATOR-APPROVED PROCESS DELTA | No parser, dispatch, runtime, test, packaging, or cli-extended source changed. The operator explicitly authorized changing `nyxloom-trove/STANDING.md` to allow commits from the assigned worktree and to permit exact-scope docs/packaging edits; this path was forbidden by the original P111 audit scope and is disclosed below. |
| O6 - complete exact-revision gate | PENDING | The original dirty-tree attempts exited 2 before any lane started. The operator then authorized worktree commits; run the exact gate on the final committed audit package and record each lane's result. |

### Gate result

History inspection returned exit 0 with no active `tester-unified` run. The
initial required command attempts were refused before a lane started because
the worktree was dirty. The final historical attempt was:

```text
./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-cli-extended tester-unified
```

Its output, verbatim:

```text
run-gate: refusing to judge a dirty tree: /workspaces/vbpub/.worktrees/nyxloom-cli-extended has 7 uncommitted change(s) (first: ' M nyxloom/README.md') — commit or pass --allow-dirty
```

Exit status: 2. No container or test lane started on those attempts; pass count
was zero and they were not green. On 2026-09-27, the operator authorized
commits from the assigned feature worktree and removal of the blanket
documentation/packaging freeze. `STANDING.md` now permits that scoped workflow.
O6 remains pending only until the exact final-revision gate completes; the
historical refusal is retained as evidence and is not reported as a product
failure.

## Decisions and backlog dispositions

At the audit snapshot, D-001 through D-006 were open in
[`nyxloom-trove/decisions.md`](../decisions.md). The operator resolved them on
2026-09-27; the recorded target behavior is:

| Decision | Approved behavior |
|---|---|
| D-001 | Remove `-h`; retain the library's help contract. |
| D-002 | Keep `backlog list` read-only; render missing index content in memory and leave file writes to `backlog index`. |
| D-003 | Reject ignored combinations, make doctor liveness exclusive with rebuild/write, and remove lossless redaction from that verb. |
| D-004 | Refuse unknown explicit project selectors for status/doctor in every mode. |
| D-005 | Remove the empty reserved `gate` command path. |
| D-006 | Adopt `--debug` as verbosity and preserve traceback under `--traceback`. |

The operator approved three user-facing console scripts in the same Nyxloom
wheel: `nyxloom` for local project authoring,
`nyxloom-harness` for AI-harness workflows, and `nyxloomctl` for daemon
interaction/admin/developer diagnostics. The first two must not depend on
daemon availability; the dashboard remains the current HTTP/SSE client, while
`nyxloomctl`'s transport and operations are deferred for a separate design.
The operator also approved moving all host-control command families to
`nyxloomctl` with no old `nyxloom` forwarding paths, a service-only `nyxloomd`
executable for the existing container, moving the local daemon verb to
`nyxloomctl daemon`, and the shared optional prompt API plus Nyxloom's
`[interactive]` extra.
The wheel boundary remains unchanged.

The reference assesses NL-10 and NL-13 through NL-22, including the specified
NL-16, NL-18, NL-19, NL-20, and NL-22 candidates. Nothing from the backlog was
implemented. Current approved packaging direction remains: bundle
`cli_extended` into the existing Nyxloom wheel, with no separate library
release/runtime dependency.

## Documentation and implementation map

README is the current user-facing overview and links the canonical grammar.
DESIGN-GUIDE records observed rationale and identifies unshipped cli-extended
requirements. CONSUMERS contains parser-checked examples. The reference notes
that `docs/SPEC.md` lacks a normative installed-CLI help/error/version/JSON/
confirmation contract; change that in the follow-up package, not this audit.
`docs/USAGE.md` and the introductory command list in `cli.py` also need
consolidation when the registry is adopted.

The follow-up implementation map is recorded in the LOG. Its key boundaries
are `src/nyxloom/cli.py`, `pyproject.toml`, Nyxloom CLI tests, user docs, and a
separately scoped cli-extended API prerequisite. Nyxloom declares no verb
aliases; existing option aliases fit `OptionSpec.flags`, so no alias API
change is needed. The library couples `mutating=True` to `--yes` and required
confirmation wording; Nyxloom's existing operation/state safeguards are the
approved consent model. The library prerequisite must let a command be
declared mutating without enabling generic confirmation, and add the approved
optional prompt API. Do not retain a second parser or misdeclare verbs.

The wheel proof must build Nyxloom, install into an isolated environment
outside this checkout, make the checkout unavailable on `sys.path`, run
`nyxloom --help`, `nyxloom version`, and representative nested help, then
prove `cli_extended.__file__` is inside the installed Nyxloom wheel and the
identity comes from Nyxloom distribution metadata. This audit did not build a
wheel or claim cli-extended is currently bundled.

## Files touched

- `README.md`
- `docs/CLI-REFERENCE.md`
- `docs/DESIGN-GUIDE.md`
- `docs/CONSUMERS.md`
- `nyxloom-trove/decisions.md`
- `nyxloom-trove/reports/nyxloom-P111-LOG.md`
- `nyxloom-trove/reports/nyxloom-P111-REPORT.md`
- `nyxloom-trove/STANDING.md` (operator-authorized process-rule change,
  outside the original P111 scope)

P111 produced an audit and decision packet. The
approved CLI split and direct service executable are recorded in D-007..D-009;
the shared wizard API and legacy daemon-verb compatibility remain open in
D-010/D-011.

## Operator review addendum - 2026-09-27

The operator approved D-001 through D-016 as summarized above. `backlog list`
is intended to be read-only; the current implementation still writes a
missing index until the approved adoption change lands. Investigation found
one console script (`nyxloom`), direct local CLI dispatch, and a separate
daemon HTTP/SSE surface consumed by the dashboard. It did not find a
project-scoped consumer API/client. The approved target is the three user-facing
scripts plus a service-only `nyxloomd`, all in the same wheel. The daemon
transport/operations for remote `nyxloomctl` remain explicitly separated for
later design. D-014 selects managed backlog entry create/edit as the first
consumer of the shared prompt API; D-016 fixes its exact command spelling.
The operator authorized changing `STANDING.md` so worktree commits
and exact-scope documentation/packaging changes are allowed; P111 discloses
that operator override because the original handoff forbade touching that
file.
The review update also documented these current boundaries in README and the
canonical reference; the follow-up link check found 33 local links/anchors and
no missing targets, and the P111 handoff lint remained `clean`.

The operator then resolved two remaining details. D-015 keeps project-local
lint on `nyxloom` and moves the existing all-registered-project no-argument
scan to `nyxloomctl lint`. D-016 fixes the interactive backlog grammar as
`nyxloom backlog new --interactive [TITLE]` and `nyxloom backlog edit
ENTRY_ID`; the old noninteractive `new TITLE` path remains. When interactive
creation omits TITLE, the prompt requests it. The editor preserves body text,
validates the complete candidate before writing, and does not directly edit
status or merge-owned fields. README, DESIGN-GUIDE, and the canonical CLI
reference now describe these approved target behaviors as unimplemented.
