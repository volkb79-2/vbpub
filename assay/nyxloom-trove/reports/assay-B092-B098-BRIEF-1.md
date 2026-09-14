# Assay B092+B098 implementation brief

## Assignment

Implement and verify only assay backlog items B092 and B098 on branch
`assay-b092-b098`. Work in this worktree only. Do not modify the active
`/workspaces/vbpub/.worktrees/rg55-followups-cgprofile` judged tree, run-gate,
cgroup-profiler, ciu, dstdns, or operator-owned dirty files.

Use the existing assay configuration and mutation-identity architecture; read
the exact implementations before editing. The lane schema remains 2. Do not
reopen settled RG-55 decisions.

## B092 contract (settled for this implementation)

Add an explicit optional native-R2 lane declaration:

```toml
schema_version = 2

[lanes.worker]
scope = "S0"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["python", "-m", "pytest"]
env = {}
env_passthrough = ["PATH"]
budget = "10m"
allow_argv_append = false

[lanes.worker.isolation]
snapshot_selection = "repository"

[lanes.worker.judge]
language = "python"
base = "main"
source_roots = ["src"]

[lanes.worker.judge.mutation]
jobs = 1
max_mutants = 100
operators = ["python:compare-swap"]
identity_exclude = ["nyxloom-trove/**"]
```

`judge.mutation.identity_exclude` is a list of POSIX path globs relative to
the judged repository root. It is explicit opt-in, has no default, and is
legal only on native R2. Normalize and validate entries at load time: strings,
non-empty, relative, no backslash, no NUL, and no `.`/`..` path components.
Refuse invalid declarations loudly with the existing config error type. Use
one documented, deterministic matching rule (case-sensitive matching against
normalized Git tree paths); do not use a local filesystem test for a
namespace-translated path.

An omitted key must preserve the current whole-tree `judge_sha256` behavior
byte-for-byte so old records continue to match when nothing was declared. A
present declaration filters only the tree-content portion: paths matching the
patterns are omitted from the tree-content digest, while argv, declared and
ambient environment names, cwd, project prefix, link paths, and assay version
remain identity inputs. A declaration with an empty list excludes nothing but
is still a distinct explicit identity domain from the legacy omitted form, so
pre-key records cannot silently masquerade as records produced under a
declared policy. Include a versioned/tagged identity encoding for the new
filtered form; preserve the legacy encoding only for omitted `identity_exclude`.
Do not exclude candidates, argv/env/cwd/link paths, or anything outside the
tree-content half.

The same resolved identity must be used by both resume reader and record
writer. Excluding only `nyxloom-trove/**` must keep the digest stable across a
docs/report-only commit; changing a non-excluded file must change it, even in
the same commit as an excluded change. A no-key digest must remain exactly the
pre-B092 digest. Add focused tests for all three directions, mixed excluded
and included paths, explicit-empty versus omitted, and malformed patterns.

Prefer deriving the filtered tree digest from the already frozen Git manifest
used by `PreparedSnapshot`; do not add a second filesystem walk or weaken the
whole-tree model. Keep the existing injective/netstring guarantees.

## B098 contract

`mutation_pct` already scores only `killed / (killed + survived)`. Keep that
arithmetic and all verdict behavior unchanged. Correct its public docstring
and user-facing documentation so every excluded mutation bucket is named,
including `crashed` (alongside `budget_exceeded`, `hung`, and `equivalent`; do
not claim a bucket is absent from the payload when it is present). Add a
regression/oracle test tied to the canonical bucket vocabulary where practical
so a future bucket addition cannot silently leave the enumeration stale.

## Documentation and backlog obligation

Because B092 adds a public config key, update all three human-facing docs in
the same work: `README.md` says what, `docs/DESIGN-GUIDE.md` says why, and
`docs/CONSUMERS.md` gives a pasteable schema-version-2 example and adoption
semantics. Keep anchors and examples valid under the shipped loader. Update
`CHANGES.md` and the B092/B098 rows in `nyxloom-trove/4-backlog.md` with
evidence. Do not bump the schema version: document the compatibility fact and
the identity-domain distinction.

## Verification and handoff

Run targeted tests serially while iterating, then the relevant full assay
suite/gates. Before any gate run that records assay evidence, keep HEAD quiet;
if a commit is needed, rerun from a detached judged tree and obey assay's
per-tree resume identity. Read job exit status and assay verdict separately;
never infer success from a wrapper or a pipe. Mutation campaigns must use the
repository's normal `--resume --progress` mechanics and be PSI-gated. No broad
Docker cleanup.

When implementation reaches a coherent boundary, write/update a checkpoint
brief and retention prompt, commit the implementation and evidence with the
required trailer, and report exact commit, tests, gate verdicts, and any
blocker. Do not merge or release; the controller will run the final Luna/Sol
policy review and gates before merge.
