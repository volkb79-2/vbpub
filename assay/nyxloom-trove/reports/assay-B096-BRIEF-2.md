# Assay B096 gate repair — P25 declared-base tag failure

**Controller:** RG-55 controller. **Worker:** fresh Luna xhigh.
**Worktree:** `/workspaces/vbpub/.worktrees/assay-b096`.

## Context to read first

Read `/workspaces/vbpub/AGENTS.md` and
`/workspaces/vbpub/nyxloom/reference/AUTHORING.md` first. Then read the B096
implementation/review files in `assay/nyxloom-trove/reports/`, especially
`assay-B096-REVIEW-round2-2026-09-14.md`, the existing B096 brief/report, and
the full terminal evidence in `/tmp/rg55-assay-b096-final-gates.log` if still
available. Read `assay/gate/python/qualify_topos.py` around
`_check_resolved_base_is_the_resolution_not_the_declaration`,
`_materialize_negative`, `_invoke`, and `assay/src/assay/isolation.py`'s
snapshot materialization. Read the corresponding runner/git tests and the
P25 gate script.

## Failure to resolve

The authoritative `assay/run-gate.py tester-unified` gate was run on commit
`51d9701e0c640f6bfdd1db102e2519fe0398d337` after the B092+B098+B096 work and
fresh Luna review. Its earlier phases passed (wheel build/install,
attestation, schema hard cuts, 110 verdict tests, the self-hosted tester lane),
but P25 failed:

`QualificationError: the declared-base-as-tag scenario expected PASS, got
FAIL/COMMAND_FAILED`.

The scenario creates a disposable repository, tags its baseline commit as
`p33-declared-base`, declares that symbolic tag in `assay.toml`, and runs a
changed-line lane. The snapshot implementation appears to materialize commit
objects/files without refs, while the snapshot-side base check may resolve the
declared tag again. Establish the exact cause from code and a focused
reproduction before editing. Do not paper over the scenario or weaken the
base-resolution contract: the recorded value must be the resolved commit,
not the declared spelling, and `BASE_IS_HEAD`/R-36-style fail-closed behavior
must remain correct.

## Required repair

- Make the smallest principled product or gate-harness repair, whichever owns
  the violated contract. If snapshot refs are intentionally absent, carry the
  already-resolved base in the snapshot path in a way that preserves the
  declared-vs-resolved distinction and does not trust a mutable symbolic ref;
  if the P25 harness is wrong, repair its construction so it tests the actual
  shipped contract. Do not add an untracked or hidden fallback.
- Add regression coverage that fails for the observed failure and protects the
  negative sibling (`base-is-head`) plus ordinary full-pass base behavior.
- Keep B092 identity filtering and B098 mutation-bucket vocabulary untouched
  unless the evidence proves a direct dependency.
- Update the B096 report/CHANGES/backlog evidence only after the behavior is
  green; keep the existing round-2 review report unchanged and record the
  reason for this new gate repair.
- Run focused tests and the complete authoritative `./run-gate.py
  tester-unified` gate on a quiet, committed tip. Use PSI/CPU rules and
  separate exit markers. Do not launch a mutation lane. Commit with the
  required `Co-Authored-By: GPT-5 Codex <noreply@openai.com>` trailer.

## Stop/checkpoint rule

Do not touch `/workspaces/dstdns` or operator-owned dirty files. Do not use
Sol. If the failure requires a product decision not settled by the existing
contract, write the exact blocker and stop; otherwise implement the repair.
If approaching the dispatch context/call ceiling, checkpoint at a coherent
boundary with a successor BRIEF and retention prompt, commit it, and return.
