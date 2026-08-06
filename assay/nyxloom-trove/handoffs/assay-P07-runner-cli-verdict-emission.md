---
schema_version: 1
id: assay-P07-runner-cli-verdict-emission
project: assay
title: "The commit-isolated runner, declared-only environment, and verdict emission on every outcome"
tier: implement-2
input_revision: "d87f028b"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P06-go-adapter-and-fixture-projects]
session: fresh
scope:
  touch:
    - "src/assay/runner.py"
    - "src/assay/cli.py"
    - "src/assay/emit.py"
    - "tests/**"
  forbid:
    - "src/assay/evaluate.py"
    - "src/assay/adapters/**"
oracles:
  - id: O1
    observable: "a lane whose argv exceeds its declared budget yields exit 4 / BUDGET_EXCEEDED / LANE_TIMEOUT, and a verdict artifact IS written; a lane whose argv[0] does not exist yields exit 2 / ERROR / EXEC_FAILED, and an artifact is written for that too"
    negative: "a timeout is reported as a test failure (or as a tool error), so a slow lane and a broken lane get the same code and the same retry policy"
    gate: tester-unified
  - id: O2
    observable: "with `--verdict-json PATH`, an artifact is emitted for ALL SIX outcomes including ERROR; with no such flag NO file is written anywhere and the exit code alone still gates correctly"
    negative: "a consumer cannot distinguish 'assay errored' from 'assay never ran' -- a transport failure that must fail closed"
    gate: tester-unified
  - id: O3
    observable: "a variable present in the parent environment but absent from both `env` and `env_passthrough` is NOT visible to the child process; a variable named in `env_passthrough` is; `env_declared` and `env_effective` are both recorded in the artifact"
    negative: "the lane inherits the ambient environment, so the same lane file means different things at S1 and S3 -- AGENTS.md 4.2a anti-pattern #3, which is invisible to testing because every shell you would check it in has the variable"
    gate: tester-unified
  - id: O4
    observable: "`assay run --lane X -- --extra-flag` records argv_declared / argv_appended / argv_effective and sets argv_modified=true; the run is REFUSED with BAD_LANE_CONFIG unless the lane declares `allow_argv_append = true`; assay never synthesises a flag from the diff"
    negative: "appended flags are invisible in the verdict, so a gate PASS can describe a run that skipped most of the suite"
    gate: tester-unified
  - id: O5
    observable: "the gate argv runs in a disposable detached worktree at the named commit and the scratch worktree is removed even when the argv times out or raises; `git worktree list` is clean afterwards"
    negative: "the gate validates the operator's live checkout, which may sit on another branch or carry uncommitted edits, and scratch worktrees accumulate"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "removing the scratch worktree reliably requires serialising git admin calls in a way that changes the runner's public shape"
mutexes: []
---

# P07 — runner, CLI, emission

The claim to attack: **does the real exit propagate, and is every outcome
recorded?**

## Context to read first

1. `/workspaces/vbpub/nyxloom/src/nyxloom/gate_runner.py` — the scratch-worktree
   discipline, the `{worktree}` substitution, the 124/127 sentinels, and the
   `finally` removal. Note it falls back to the live root when the worktree
   cannot be created; decide explicitly whether assay keeps that fallback or
   fails, and record the choice.
2. `docs/DESIGN-GUIDE.md` §5 (env), §6 (three channels, transparency).
3. `nyxloom-trove/decisions.md` A-019, A-020, A-027, A-028, A-036.

## Work

1. `runner.py` — `run(argv, env, timeout, at_commit, repo) -> GateResult`.
   Inner 124 maps to BUDGET_EXCEEDED (exit 4); inner 127 maps to ERROR /
   EXEC_FAILED (exit 2). Commit isolation only; **no container, network or image
   knowledge, ever** (DESIGN-GUIDE §7).
2. `emit.py` — artifact writing, validated against the shipped JSON Schema before
   it is written, so a malformed artifact is a test failure and not a consumer's
   surprise.
3. `cli.py` — `assay run` joins `assay lanes`. `assay verify` and `assay mutate`
   arrive in P08 and P10.

## Boundary check

If this package finds itself needing to know a service name, a network, or an
image tag, stop: that is `[…where]`, which assay parses and never interprets.
