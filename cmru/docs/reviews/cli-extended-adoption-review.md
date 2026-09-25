# CMRU `cli-extended` adoption and CLI semantics review

> **Historical review snapshot.** This document preserves the findings and gate
> result from its original review baseline. Product decisions and current
> implementation outcomes are recorded in the canonical [CLI spec, S-CLI.9](../SPEC.md#s-cli9-canonical-cli-grammar-and-semantic-audit);
> use that table for current semantics and acceptance state. This snapshot is
> not a report of the current worktree.

**Review state: NOT READY for final acceptance.** Registered grammar adoption is
complete and the canonical inventory is now in the CMRU spec, but the review
found safety defects and unresolved behavior choices. The pinned project gate
also failed its Assay lane.

## Review scope and evidence

- Reviewed worktree branch `cmru-cli-extended`, implementation HEAD
  `4de03ca51009b29717ed7c485c00b20c3f4ba9e5`, against base
  `24fad0c2dc4cf87f2bf2baf63be8dae4804f090b`.
- Covered the installed `cmru`, `cmru-agent`, and `cmru-controller` entrypoints,
  nested commands, and executable `cmru.bundle`, `cmru.runner`, and
  `cmru.handlers` module adapters.
- Read the CMRU backlog and compared registered flags, generated help, dispatch,
  behavior-specific code, and current S-CLI clauses.
- Searched `cmru/src/cmru` for hand-authored argument parsing. The installed
  CLIs and module adapters declare grammar through `cli-extended`. Remaining
  `argparse` use is the deliberately standalone generated `get.py` template;
  `cmru.cli._orchestrate(args=None)` is a compatibility helper that parses the
  library-generated `run` parser, not a second grammar.
- Added an exact registry-to-SPEC check and semantic-audit coverage check.
  Focused result: `4 passed` with
  `PYTHONPATH=src:../libraries/cli-extended/src:../libraries/worktree/src
  python -m pytest tests/test_cli_spec_inventory.py -q`.
- Probed generated help for `run`, `get`, and `get-py`, and compared the shared
  color switches. `cmru get --help` currently prints `usage: cmru get-py ...`.

The canonical inventory and the required review prompt/results are in
[`SPEC.md` S-CLI.9](../SPEC.md#s-cli9-canonical-cli-grammar-and-semantic-audit). That clause includes every
registered leaf, common flags, hidden options, aliases, module adapters, and a
check that future grammar edits update the spec in the same product change.

## Findings requiring attention

### CLI-R1 — Hidden child flag bypasses isolated build execution

**Severity: blocking safety issue.** `--_transaction-child` is hidden, but the
registered parser accepts it. Normal `cmru build` enters the isolated-worktree
launcher only when that flag is false; when true, dispatch falls through to
`_run_isolated_build_projects()` in the caller checkout. That function executes
the project's prepare, gate, and artifact steps from the current tree. A user
can therefore bypass the build isolation boundary with
`cmru build --_transaction-child`. The same flag skips the release launcher; in
a dry-run it can execute an external-version prepare step and commit generated
inputs in the invoking checkout. On `publish`, the shared option is accepted
but unused.

Evidence: [`cli.py`](../src/cmru/cli.py#L2229) skips the launcher when the flag
is true; [`cli.py`](../src/cmru/cli.py#L2326) takes the direct build path;
[`cli.py`](../src/cmru/cli.py#L2425) skips the release launcher;
[`cli.py`](../src/cmru/cli.py#L2575) prepares external versions during dry-run
and [`cli.py`](../src/cmru/cli.py#L2120) commits generated inputs;
[`cli.py`](../src/cmru/cli.py#L3050) registers the hidden user-supplied flag
for both build and publish. Hide text is not an authorization boundary.

**Recommendation:** prove trusted transaction-child context before honoring
internal dispatch state, or remove the switch from the user parser. Add a
behavioral oracle that proves an ordinary invocation cannot enter the direct
child path. Runtime code was not changed during this review.

### CLI-R2 — `cmru run` silently selects configured steps; its dry-run is local

**Severity: high.** The help describes “explicit project steps,” but with no
step flags CMRU loads `default_steps` and runs them. If the configured list
includes `push`, a bare `cmru run` can publish. The same verb exposes
`--remove-assets`; when step flags and cleanup are combined, `--dry-run` is
passed only to asset deletion, while selected/default project steps still run.
The help text says “show cleanup actions without deleting,” which is narrower
than a whole-command dry-run but does not make the mixed invocation easy to
reason about.

Evidence: [`cli.py`](../src/cmru/cli.py#L1272) selects flags and configured
defaults; [`cli.py`](../src/cmru/cli.py#L1290) executes steps;
[`cli.py`](../src/cmru/cli.py#L1310) applies dry-run only to asset removal;
[`cli.py`](../src/cmru/cli.py#L3044) calls the command explicit.

**Owner decision:** choose whether to require explicit steps, preserve
`default_steps` with clearer help, and whether cleanup should move exclusively
to `cmru cleanup`. Define `--dry-run` scope for mixed invocations. The SPEC
currently records the implemented behavior and marks this decision open.

### CLI-R3 — Bare `cmru cleanup` performs configured deletions

**Severity: high; intentionality needs confirmation.** When no cleanup mode is
selected, dispatch calls the configured project-aware cleanup policy. That
path can delete remote release/tag/package objects. It does not require
`--yes`; only exact unmanaged-release, build-record, and build-worktree modes
require `--yes` or `--dry-run`. `--dry-run` defaults false.

Evidence: [`cli.py`](../src/cmru/cli.py#L2694) checks explicit modes;
[`cli.py`](../src/cmru/cli.py#L2707) gates exact tag deletion on confirmation;
[`cli.py`](../src/cmru/cli.py#L2761) falls through to configured cleanup.

**Owner decision:** confirm that invoking the maintenance verb without a mode
is meant to execute the configured deletion policy. If yes, make that default
and its confirmation boundary explicit in root help and examples; otherwise
require an explicit mode or confirmation.

### CLI-R4 — Version flags accept ambiguous or inapplicable combinations

**Severity: medium.** `--minor`, `--major`, and `--set-version` can be combined
on both `status` and `release`. For tagged, non-external strategies, explicit
version wins over major, and major wins over minor. External-version and no-tag
projects ignore those switches. Parser grammar does not encode these
constraints, so an accepted spelling can be ignored for some selected
projects.

Evidence: [`cli.py`](../src/cmru/cli.py#L3054) and
[`cli.py`](../src/cmru/cli.py#L3072) register them independently;
[`version.py`](../src/cmru/version.py#L611) sets major-over-minor precedence;
[`version.py`](../src/cmru/version.py#L619) and
[`version.py`](../src/cmru/version.py#L631) show project-strategy-specific
behavior; [`version.py`](../src/cmru/version.py#L705) applies explicit version
only after the external strategy branch.

**Owner decision:** keep and document precedence, or make the switches mutually
exclusive; also decide whether CMRU should reject overrides when the selected
project strategy cannot use them.

### CLI-R5 — `tool-deps --refresh` accepts options it ignores

**Severity: medium.** `--refresh` returns before verification/rendering, so
`--json` and `--allow-stale-tool-deps` have no effect when combined with it.
This violates the CLI contract that accepted options affect their described
behavior.

Evidence: [`tool_deps.py`](../src/cmru/tool_deps.py#L669) returns from refresh;
JSON and staleness handling occur later at
[`tool_deps.py`](../src/cmru/tool_deps.py#L692) and
[`tool_deps.py`](../src/cmru/tool_deps.py#L698).

**Recommendation:** refuse these combinations or define refresh output and
staleness semantics explicitly.

### CLI-R6 — Disabled OCI repack leaves visible no-op options

**Severity: medium.** `--repack` fails closed under KI-02, as intended, but
`--repack-target-size` and `--repack-compression` remain accepted on build
without `--repack`, where the handler does not read them. Their help promises
effects they cannot currently have.

Evidence: [`handlers.py`](../src/cmru/handlers.py#L343) refuses only when
`repack` is true; the build handler does not consume target size/compression
([`handlers.py`](../src/cmru/handlers.py#L385)); all three flags remain
registered at [`handlers.py`](../src/cmru/handlers.py#L471). KI-02 is an
intentional fail-closed product decision; this finding concerns the grammar
left visible around it.

**Recommendation:** hide/remove the inactive value switches until repack is
available, or reject them when `--repack` is absent.

### CLI-R7 — Alias and module surfaces need an explicit support policy

**Severity: medium.** `dependencies`, `dependency-graph`, and `graph` share
identical grammar/handler. `get` and `get-py` share one grammar/handler, but
`cmru get --help` renders the canonical parser name `cmru get-py`. Numeric
`cmru init --layout 1|2` spellings also remain accepted. In addition,
`python -m cmru.bundle` is executable but is not an installed console command
or a prominent README workflow.

These may all be intentional compatibility surfaces. Record which spelling is
canonical, what migration period aliases receive, and whether the module bundle
command is a supported user interface. Do not remove a spelling without an
explicit compatibility decision.

### CLI-R8 — Controller `status --dry-run` is a no-op

**Severity: low.** `--dry-run` is a controller-wide option, so the read-only
`status` command accepts it without changing behavior. Decide whether that is a
useful consistent invocation surface or an option that should appear only on
write verbs.

Evidence: [`controller/cli.py`](../src/cmru/controller/cli.py#L204) registers
the global option and [`controller/cli.py`](../src/cmru/controller/cli.py#L225)
registers read-only status.

## Verb groups and use-case coverage

The command families cover release transactions, local build and low-level
publish, project-step execution, state inspection, version/dependency
maintenance, consumer artifact resolution, cleanup/recovery, and the separate
agent/controller deployment plane. No new verb is justified by this review
alone. Candidate use cases to revisit are stable JSON for release/status plans
and clearer separation of execution from cleanup in `cmru run`.

The main bloat candidates are the three dependency names and the overlapping
`get`/`get-py` installer names. `--allow-tag-at-head` is already deprecated
but its name is misleading relative to the strict-ahead behavior; the CMRU spec
should retain its removal window. The CLI groups otherwise roughly match
behavior: maintenance has cleanup/abandon, exploration has previews/resolution,
and mutation contains execution/build/publish. Keep checking group labels when
adding commands, since nested verbs and the service CLIs do not share the root
verb-group catalog.

## Backlog relationships

| Backlog item | Current disposition | Review relevance |
|---|---|---|
| KI-26 — installed-wheel `get-py` | SHIPPED in this branch | The `cmru get-py` module-resource fix is in scope; the standalone generated installer intentionally remains argparse. |
| KI-29 — `cmru abandon` | SHIPPED in this branch | Exact branch selection and dry-run-safe separation from remote cleanup are reflected in S-CLI.8. |
| KI-06 — durable post-tag publication resume | Open, deliberately scoped | `--resume` is pre-tag recovery/debugging only; do not imply it retries remote publication. |
| KI-10 — build output to publish | Open, decision required | `cmru build` retained local outputs cannot safely feed caller-worktree `cmru publish`; keep the limitation visible. |
| KI-11 — bind project commands to the transaction's CMRU | Open, strict runtime binding required | Project commands can resolve a different CMRU through ambient PATH, including gate commands. This is a release/runtime consistency gap outside parser adoption. |

## Gate result

The pinned `./run-gate.py gate` invocation on implementation HEAD
`4de03ca51009b29717ed7c485c00b20c3f4ba9e5` exited 1 in the Assay lane. The
verdict is `cmru/.assay/verdict-cmru.json`, ended 2026-09-24 12:47:59 UTC:

- R0 passed.
- R1 failed `UNCOVERED_LINES`: 88.37% statement coverage (1013/1108); branch
  coverage was 81/130.
- R2 failed `MUTANTS_SURVIVED`: 185 of 279 candidates survived; 94 were killed.
- R3 was inconclusive (`CANARY_INCONCLUSIVE`): both the inserted import-break
  control and transformed run failed, so there was no differential proof.

The gate conjunction stopped at Assay; later lanes were not verified in that
run. This review added only documentation and a registry/spec test after that
implementation HEAD. The focused test passes, but it does not clear the failed
Assay result or make the project gate green.
