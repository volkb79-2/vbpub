# ciu-P46 — adversarial review verdict (ACCEPT-conditional)

Fresh reviewer, independent control worktree, independent gate run (PASS,
`d58b9dc5`, R0/R1 both PASS, 100% line+branch, `excluded_lines={}`). Full
detail lives in the reviewer's own transcript; this file is the durable,
actionable summary for the fix pass.

**Verdict on the mechanism (A1-A5): ACCEPT.** Every guard was planted-and-
fired (state-secrets, vault-presence, A1 collision refusal, A1
apply_to_config+secret refusal, migration-check both entry points). 8
hollow-test mutations, all caught. The `token_file` deviation (judgment
call 1) is correct and internally consistent — do not touch it. Judgment
calls 3, 4, 5 (migration-check rule-1 direction, rule-3 absent-`.gitignore`
behavior, the `.hook-persisted.toml` sidecar) were all independently
re-verified as correct — do not touch them either.

**All 8 blockers are documentation/consumer-dimension gaps.** Fix exactly
these, nothing else in A1-A5's mechanism needs rework.

## Blockers to fix

**B1 — `src/ciu/hooks/examples/post_compose_example.py:24-29` returns a
`root_token` key with `persist:"state"`, which the NEW state-secrets stage
now refuses** (`is_secret_shaped("root_token", "placeholder-...")` is
`True`). `tests/tests/test_ciu_hook_examples_deeper13.py:44-52` pins that
exact return shape, actively defending the anti-pattern. Fix: switch the
example to `"persist": "secret"` (preferred — it demonstrates the NEW
sanctioned channel, which is the more useful example for this package to
ship) and update the pinning test to match. Update the return-contract
prose in the same file's module docstring if it describes the old shape.

**B2 — `src/ciu/hooks/examples/README.md:56-63,73,77`.** Line 61
(`"only valid destination"`) is false as of S9.4a. Line 59's illustrative
value (`root_token`/`"s.secret-token"`) is the exact anti-pattern the
package just closed. Line 77 says the test-repo vault hook "persists
Vault's root token" — it no longer does (per A2, the hook's `root_token`
return was deleted entirely). Rewrite all three to reflect
`persist:"secret"` and the current hook shape.

**B3 — `src/ciu/deploy.py:479-484` (S7.6 vault preflight message) still
names the deleted path**: `"...the vault stack's [state].root_token)."`.
`engine.py:1666-1669` was correctly updated to name the new store file —
mirror that exact wording here.

**B4 — `test-repo/README.md:15`** still describes the OLD fixture shape
("persists `root_token`+`initialized` to `[state]` ... feeds S4.16 token
source #3"). Rewrite to match: the hook now persists only `initialized`;
`root_token` is `GEN_LOCAL`-declared and reached via `[vault].token_file`
(source #2), not source #3.

**B5 — `docs/CIU.md:479-497`.** The "Structured return [S9.4]" block is
explicitly labelled as the `post_compose_vault.py` example and shows the
DELETED code (`"persist": "state", # S4.16 token source #3`), followed by
a now-false claim that `persist:"state"` is the "only" destination. Update
both the code block and the surrounding prose.

**B6 — `docs/CIU-DEPLOY.md:220`** still documents token source #3 as
`... vault stack's ciu.toml [state].root_token`. Update to the new store-
file description.

**B7 — `README.md:85`** (project front door) still says hooks "persist
[Vault's] root token to `[state]`" as the illustrative example of what
hooks do. Rewrite the illustrative phrase to the current pattern.

**B8 — `CHANGES.md` `[7.9.0]` Adoption Note 1 overclaims.** It says a
`persist:"secret"` value is "0440, atomic, masked, leak-scanned". It is
NOT masked or leak-scanned — `engine.py`'s leak-scan call only covers
directive-materialized secrets (`materialize()`'s return), and Step 14's
scan runs BEFORE Step 17's `post_compose` hooks anyway. It doesn't need to
be: the value never enters the in-memory config or any log path in the
first place, which is sufficient — but the CLAIM is false and must be
corrected. Fix: strike "masked, leak-scanned"; state instead "0440 file in
a 0700 store dir, atomic `mkstemp`+`os.replace`, under the stack's S4.26
lock, and never entering the in-memory config or any log path." (Note:
`docs/SPEC.md` S9.4a itself is already correct — it only claims "never
logged" — this fix is CHANGES.md-only.)

## Also fix (not a blocker, but unambiguous — no decision needed)

**N1 — gitignore-gaps migration-check rule false-positives on ciu's own
checkout.** The comparison is exact-string against the canonical entry
list, so a BROADER glob already covering an entry (ciu's own `.gitignore`
uses `**/ciu.env`, `**/ciu.global.worktree.toml.j2`, which strictly
subsume the canonical un-globbed entries) still WARNs as missing. Fix:
normalize a leading `**/` before comparing (or equivalent — treat an
existing entry that is a superset-glob of a canonical entry as satisfying
it), so `ciu migration-check` is clean against ciu's own repo. Add a test
fixture with a `**/`-prefixed entry proving it's now recognized.

## Operator decision — resolved, do NOT build anything for this

**The S2.4.1 secret-shape heuristic (A4) ships AS-IS, with no escape
hatch.** The reviewer found real false-positive shapes by construction
(`sort_key`, `primary_key`, `public_key`, `idempotency_key`, etc. — see
its transcript for the full list and the counter-examples it ran) but
ZERO live occurrences across ciu, test-repo, and dstdns today. Operator
decision: ship the hard ERROR with no suppression mechanism now; revisit
only if a real false positive is actually observed. **Do not add a value-
shape exclusion, an allowlist, or any other escape hatch for this** — that
was considered and explicitly declined.

## Process for this fix pass

- Same worktree/branch you already have (`worktree-agent-a3aef243215d54b0d`).
- Fix B1-B8 + N1 exactly as prescribed above. Nothing else.
- B1 requires updating a pinned test — the gate MUST go green again after
  this change; re-run `./run-gate.py ciu`, read the verdict in a separate
  step, do not infer it from `pytest` alone.
- Commit with the same trailer convention as your prior commits.
- Append to `nyxloom-trove/reports/ciu-P46-LOG.md`/`REPORT.md` (don't
  rewrite prior entries) describing this fix pass and its gate verdict.
- Do not merge. Report back the new commit hash(es) and gate verdict.
