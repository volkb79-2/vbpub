# RG-55 P5 final adversarial review — round 2

REJECT

## Initial blind checkpoint before repair

- Target `P5`; candidate branch `rg55-p5-transport-port-20261010`, HEAD `4bb006a9f06108dd9b1513f8fe900387be9d4297`, clean at inspection. Base and merge base: `efaf112945233e3a8f6d53f2fef136439d18d089`. The full 76-path integrated diff was captured before edits as `/tmp/rg55-p5-initial-integrated.diff` (SHA-256 `0360cc39eeafff13f099d985041d26a7ca184313efb95a14b4e773b3e8c60b0e`, 18,892 lines). It includes Run-Gate, Assay, and cgroup-profiler.
- Caller route evidence reference: the controller's programmatic P5 invocation/session record for this exact target, supplied outside the reviewed tree. Route selection and verification belong to the caller; this review does not self-attest either.
- Initial memory PSI `full avg10=0.00`; no P5 gate or mutation process was active. The CIU-managed main daemon was up. The two contract mirrors have identical SHA-256 `409bafc7e5e572443d2d1075bebc8b491927ffc4f30fdb396d998e129583ecf4`; `run-gate.footprint.json` is tracked.

## Initial blockers

| ID | Severity and location | Observable failure, reproduction, exact prescription |
|---|---|---|
| B1 | High — `run-gate-project/run-gate.py:2119-2158`, `_parse_response` | Contract §1.3 says any nonzero exec status is profiling unavailable, and §1.4 requires integer contract 1. The parser accepts `{"ok":true,"contract":1}` with return code 3, `{"ok":true,"contract":true}` with return code 0, and a truthy string `ok` value as success. Direct Python reproduction on the initial tree printed `accepted=True` for all three. Reject a nonzero status before accepting an `ok` response; require exact boolean `ok` and exact integer contract, while retaining the existing useful refusal diagnostics. Test both valid success and each malformed/failed status. |
| B2 | High — `run-gate-project/run-gate.py:2383-2395`, `ProfilerWatch._accept` | A watch for `s-expected` queues a `verdict=killed` line tagged `session=s-other` as an ordinary event. The consumer can then take the R-40 stall path for the wrong lane, violating §8.2 and R-36h. A direct reproduction queued the wrong-session event unchanged. Validate the contract, exact session, event vocabulary, and verdict shape before forwarding an event; turn malformed lines into a watch error/fallback, never a daemon kill. Add a positive valid-event fixture and negative wrong-session/wrong-contract/malformed-verdict fixtures. |

No code or tests were edited before this record. The final disposition and exact-tree gate evidence will be appended after the repairs are judged.

## Repair checkpoint (before registered gates)

- The Run-Gate parser now requires an exact integer contract, an exact boolean `ok`, and exit 0 before accepting a successful control response. Refusal JSON still carries its stated error. The watch reader requires the correct contract and session; an invalid verdict becomes a watch error and cannot be followed by a queued kill event from that stream. A valid reading/verdict stream remains accepted. The copy-drift marker advances to rev 64.
- Changed paths: `run-gate-project/run-gate.py`, `tests/test_run_gate.py`, the placement-live fixture README, and `CHANGES.md`. README, DESIGN-GUIDE, CONSUMERS, SPEC and LANE-AUTHORING already describe the strict contract/fallback behavior; no public key or vocabulary changed. RG-56 and the integrated Assay/cgroup-profiler package holds remain separate.
- The first focused run had 187 passes and one failure: an older missing-`ok` oracle expected the word `refused` in its diagnostic. The code was adjusted to retain that message while rejecting non-boolean values. The subsequent focused command `nice -n 19 ionice -c 3 /home/vscode/.venv/bin/python -m pytest -q tests/test_run_gate.py -k 'TestProfilerClient or TestProfilerClientDegradesOnMalformedResponses or TestPlacementBarrier or TestExecLaneProfilingWiring or TestBareHostProfilingWiring'` exited 0 with 188 passed and 1,569 deselected. `python -m py_compile run-gate-project/run-gate.py` and `git diff --check` exited 0. These are cockpit iteration checks, not final gate receipts.
