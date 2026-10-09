## Findings

- **P1 — A fully accounted campaign can return after its deadline.** [mutation.py](/workspaces/vbpub/.worktrees/assay-b114-evidence-deadline-20261009/assay/src/assay/mutation.py:3795) treats any `budget_exceeded` bucket entry as evidence of a partial campaign. A candidate that exhausts only its *per-candidate* budget is a valid, recorded outcome; if the lane deadline expires during resume merging or terminal progress, the remaining checks are skipped and `run_mutation` returns. Carry the executor’s actual partial-campaign flag through aggregation instead of inferring it from the bucket, and test deadline expiry during finalization with a recorded per-candidate timeout.

- **P3 — The design guide states the checker order incorrectly.** [DESIGN-GUIDE.md](/workspaces/vbpub/.worktrees/assay-b114-evidence-deadline-20261009/assay/docs/DESIGN-GUIDE.md:2458) says B105 calls `verify_document` *then* checks source bindings. The code checks bindings first and calls the verifier before acceptance. Correct the sentence to reflect that intentional refusal order.

Reviewed against HEAD `cbd6054450610c2c57d51ea35d1e914a391c5177`. I made no edits and ran no tests, gates, or containers.