# P26 locked attestation/deadline packet

This directory is carver-owned specification and independent evidence. P26's
implementer and reviewer may read it but must not edit it. The packet is pinned
to clean post-P25 main
`233926cedd26a6e34512806e267b7141377913b2`.

`interface-contract.json` freezes the public types, bounds, lower Git helpers,
and lifecycle. `skeleton.patch` transfers those signatures and the orchestration
shape into implementation form; `git_boundary_skeleton.py` is a separately
compiling construction for the hardest process-group/deadline loop. They are
contract material, not patches to apply blindly. Private decomposition remains
free where the handoff says it does.

`probe_current_failures.py` is the carver's premise probe. On the input revision
it witnesses both known false PASSes (a changed descendant beneath an attested
directory and a `../` evidence key reading outside its root), the distinction
between a missing attestation directory and an unsafe object, the generic Git
boundary's escaped descendant, and the exact real-Git behavior of the frozen
`ls-tree -z`/`diff --quiet` construction. Its compact expected record is
`probe-results.json`.

The four documents under `expected/` are hand-authored complete v4 artifacts.
Tests may substitute only the named runtime identity/timestamp placeholders;
every other byte of the decoded JSON object is an independent expected value.
They distinguish current, stale-directory, independently resolved
malformed/missing/current sibling evidence, and atomic attestation timeout.

Run quick locked acceptance from `assay/`:

```text
python -m pytest nyxloom-trove/carve-assets/P26/test_acceptance.py -q -p no:randomly
```

Before implementation this is intentionally red at **9 passed, 32 failed** on
the absent config/API/deadline/gate seams while its hashes and premise fixtures
are green. After P26 all 41 tests must pass. The suite does not replace the
registered gate: the controller runs `tools/tester-unified-gate.sh` foreground
and requires the new
`ASSAY_GATE_PHASE=attestation-hardened` marker, every earlier phase marker, the
outer completion receipt, and a zero exit.

The safe-input rule is exact: an absent component means no producer supplied
that declared attestation and becomes `MISSING_ATTESTATION`; a symlink,
non-directory parent, special final object, permission failure, malformed JSON,
or excess bound is present-but-untrustworthy and becomes
`UNREADABLE_ARTIFACT`. No pathname precheck may be followed by a second open.

The normal and refusal runner APIs derive the authoritative ordered identities
from `lane.judge.evidence` and bind both supplied tuples to that source before
doing work. Their empty defaults are valid only when the lane itself declares
none; omission against a nonempty lane fails loudly.

The deadline rule is also exact: CLI starts one `LaneDeadline` before the first
Git child, and the same bound method reaches HEAD resolution, every attestation
query, direct R0 or higher-rigor work, and all generic Git bootstrap/substantive
children. Lower non-lane Git callers may omit a deadline; a lane caller never
does. Expiry preserves the original `BUDGET_EXCEEDED/LANE_TIMEOUT`, kills the
owned process group, reaps it, and launches no successor command.
