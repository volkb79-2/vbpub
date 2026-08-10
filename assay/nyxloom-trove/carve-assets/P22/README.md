# P22 carver-owned acceptance packet

This packet freezes the committed-object snapshot interface and the hostile
repository distribution P22 must survive. It is owned by the carver and the
independent reviewer; the implementer must not edit it.

From the isolated P22 worktree, before production edits:

```sh
git apply assay/nyxloom-trove/carve-assets/P22/skeleton.patch
PYTHONPATH=assay/src python -m pytest --override-ini=pythonpath= \
  assay/nyxloom-trove/carve-assets/P22/test_acceptance.py -q
```

The patch creates the exact public API and compiles. It already implements the
mechanical constructor/timeout grammar so the implementer can check rather than
reinterpret it; every object-transfer/materialization body deliberately raises
`NotImplementedError`. The locked suite must be a controlled red at that point
and pass unchanged after implementation.

`fixture-manifest.json` is hand-authored from literal fixture bytes. It fixes
the base commit, modes, sizes, SHA-256 values, nested project prefix, contained
symlink target, and deliberately awkward UTF-8/newline/backslash path. The
tests create the Git repository independently; they never ask Assay to produce
its own expected manifest.

`expected/r0-snapshot-limit-v4.json` is the complete hand-authored artifact
which closes P21 reviewer disposition `SB-P21-R2`. P22 copies it unchanged to
`tests/fixtures/verdicts/r0_budget_exceeded_snapshot_limit_exceeded.json` and
removes that pair from `test_verdict_conformance.EXCLUDED_ENTIRELY`.

`probe_snapshot_plumbing.py` is the carver's tracer bullet. It proves, with a
real Git binary, that the specified full-closure pack transfer and fixed-
identity child commit work without checkout, filters, hooks, replacement refs,
or source-object writes. It is evidence, not production code.

The reviewer still adds at least one new combined-axis attack not named here.
