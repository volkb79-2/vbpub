# P23 carver-owned acceptance packet

This byte-locked packet freezes exact committed-snapshot reexecution at P22
merge `9d30b25b96b8ffd8f952c02e8958b923bb8e1d13`. It is owned by the Sol
carver and independent reviewer; implementer edits are forbidden.

From a detached P23 implementation worktree at that commit, before production
work:

```sh
git apply assay/nyxloom-trove/carve-assets/P23/skeleton.patch
PYTHONPATH=assay/src python -m pytest --override-ini=pythonpath= \
  assay/nyxloom-trove/carve-assets/P23/test_acceptance.py -q
```

The skeleton implements only the mechanical contract that an implementer
should check rather than reinterpret: R0-led canonical rigor grammar,
uncovered-line's R1 prerequisite, the extended immutable `CommandPlan`, the
required-plan `execute_plan` seam, injected `LaneDeadline`, and the singular
scratch-root context seam. It then raises one explicit `NotImplementedError`
at the higher-rigor `run_lane` branch. The locked suite must be a controlled
red there and pass unchanged after P23.

The acceptance fixture constructs a real two-commit repository at runtime. Its
project is `apps/p`; its command reads tracked `shared/input.txt`; the consumer
contains ignored stale coverage/cache state. The byte ledger fixes appended
argv, captured/missing passthrough values, effective closed environment, nested
cwd, baseline/replacement identity, and artifact absence. Other locked cases
combine max+1 with exploding executor/snapshot/process boundaries; injected
expiry with an unstarted second mutant; a snapshot-local tracked support write;
absolute committed symlinks with the explicit R0-only policy; and control-
writes/transform-omits coverage. Failed R0/R1 controls and injected scratch
entry/exit failures fix the complete v4 payload and judgment shapes without
filesystem-permission tricks.

`expected/r0-snapshot-limit-v4.json` is byte-identical to P22's carver-owned
artifact. P23 copies it byte-for-byte into ordinary fixtures, removes only the
now-reachable conformance exclusion, and proves JSON Schema, the direct raw
checker layer, and merged verification. It does not generate another artifact.

`probe_reexecution_contract.py` is a premise tracer. It uses the real landed
P22 public API to prepare once and materialize simultaneous base/replacement
contexts, while an independent project-only copy demonstrates the missing-
sibling/stale-coverage failure shape. It is evidence, not production code.

The reviewer must preserve every hash in `fixture-manifest.json`, add at least
one new combined-axis attack outside this directory, and record both the
controlled-red count and the controlled-break count in the P23 LOG.
