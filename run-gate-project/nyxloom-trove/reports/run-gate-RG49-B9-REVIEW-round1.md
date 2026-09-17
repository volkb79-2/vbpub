# RG-49 B9 provider verification — initial rejection

REJECT

Fresh review target: P5, limited to the B9 Assay provider fix. Initial HEAD
`12e061b1856e919e1dc3bedb36bf808cecfabec3`, tree
`44eda1e052d2d4eaa87f7f0d7fab296cd8505294`, parent/base
`3693a9aea02aa746293ba28ed3b52f4bf6e6a014`; initial status clean. The initial
binary parent diff, status and identity were saved before any tracked edit in
`.run-gate/rg49-b9-review-evidence/initial.*`.

Actual client runtime metadata: model `gpt-5.6-sol`, effort `xhigh`, provider
`openai`, CLI `0.154.0`, fresh thread
`01a0b128-2063-7760-ab6e-3959544c5034`, session start
`2026-09-17T20:56:32.145Z`. The first session `turn_context` supplies model
and effort; a whitelisted copy and source path are in the ignored `route.json`.
This is exposed runtime configuration, without an independent backend attestation.

## B11 — bounded JSON numeric root escapes the structured refusal boundary

Severity: major, still violates B9. Location: `assay/src/assay/mutation.py:1274`.
Only `UnicodeDecodeError` and `JSONDecodeError` are translated. The decoder
also raises `ValueError` when a decimal integer exceeds Python's configured
integer conversion limit. The object-shape guard is never reached.

Reproduction: dedicated `tester-unified:local` container
`rg49-b9-decoder-01a0b128`, actual source CLI `python -m assay.cli`, ordinary
native R2 fixture with `--resume --progress`, and a fresh verdict destination.
Replace its single completed candidate record with the valid JSON number
`1` repeated `sys.get_int_max_str_digits() + 1` times. Here that is 4,301 bytes,
below the shipped 1,048,576-byte record limit. The lane exits **1**, emits a
`ValueError` traceback, and writes **no verdict**. Raw summary and streams are
in `.run-gate/rg49-b9-review-evidence/decoder.*` and
`decoder-large-number.stderr.log`. The script restores the owned record.
The assertion-based checking driver exits 1; Docker wait/log CLI statuses
are separately 0. Prelaunch memory `full avg10=0.11`; declared slice verified
loaded with a real FragmentPath; immediate CPU cap readback is 3,000,000,000.

Prescription: translate the decoder's bounded-input resource refusals
(`ValueError`, which includes `JSONDecodeError`, and `RecursionError`) into
the existing `MutationStateError`. Keep the object guard, byte limit,
`ERROR`/`UNREADABLE_ARTIFACT`, valid replay and cache-miss semantics unchanged.
Make the diagnostic describe inability to decode, including configured
resource limits, rather than falsely claiming every such input is invalid
JSON. Add real numeric-root loader and CLI regressions with a controlled,
restored interpreter conversion limit, plus a decoder recursion refusal
oracle. Synchronize README, DESIGN-GUIDE and CONSUMERS.

Earlier checks do not overrule this finding: all 57 identity tests pass;
2/2 changed lines and 2/2 guard branches execute; 19 independent CLI cases
pass, including 15 malformed-file ERROR verdicts and healthy replay/stale/
absent cases. The five shipped shape tests fail against the actual parent
source, pytest exit 1. A 2,201-byte nested array was correctly refused; it
did not trigger a decoder resource error on this interpreter.

The incomplete wheel-based provider gate was stopped before production/test
edits: exact container `rg49-b9-provider-01a0b128`, Docker job **137**, wait/log
CLI statuses **0**, OOMKilled=false. It is explicitly cancelled/inconclusive,
not a product PASS or FAIL. Its locked acceptance phases and wheel build had
completed; no terminal self-hosted suite or final gate verdict is claimed.
Every initial-tree result must remain separately identified after repair.

This record was written before repairing B11. No merge, release, global tool
install, forbidden namespace option, operator-path edit or dstdns access occurred.
