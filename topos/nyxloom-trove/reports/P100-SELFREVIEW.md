# P100-SELFREVIEW — Adversarial self-review

## Review checks

- **No hollow tests**: Every test asserts on exact values, finding presence,
  or exact contribution breakdowns.
- **No over-mocking**: monkeypatch replaces `_INPUTS` data (module-level config)
  then calls unmocked `score_entity`. Not mocking the function under test.
- **No pragmas**, **no product edits**, **no sleeps**, **no host proc**.
- **git diff --check**: Clean.
- **Parity**: Two gate runs identical.
- **All 3 targets at exact 100%**: empty missing_lines and missing_branches.

## Negative evidence

No universal per-test mutation campaign was run, so this report does not claim
one. The repair loop supplied direct negative evidence for its three critical
cases:

- the prior exact-confidence test left rules.py line 207 uncovered; the new
  host-network-confidence input closes that exact arc and asserts its value;
- the two prior hollow default-band tests left score.py lines 136–137
  uncovered; the replacement closes that exact branch and asserts every
  resulting contribution field;
- the prior scaling tests asserted only a range; the consolidated test asserts
  the exact score and both exact contributions.

The independent reviewer also inspected every retained assertion for behavioral
relevance. Full mutation testing remains a separate future package rather than
an unsupported claim in this receipt.
