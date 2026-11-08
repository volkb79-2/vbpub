# CGProfile release readiness — Opus follow-up

- Reviewer: Claude Opus 5.5, high effort
- Persistent session: `810a8086-bbea-40e9-936f-9450666b214b`
- Follow-up run id: `c82d289a-21e1-4287-a76f-b68b9acdb1e6`
- Reviewed code revision: `8f5e5eae24c47f4eb58aa39447726b8b2d67760b`
- Scope: read-only adjudication of the six findings in the independent GPT-6-Sol xhigh review
- Verdict: **REJECT**
- Full machine result: `/tmp/vbpub-cgprofile-opus-followup-20261015.json`
- Prompt/binding record: `/tmp/vbpub-cgprofile-opus-followup-prompt-20261015.txt`

This reverses Opus's earlier READY verdict. Two findings block release. No Sol finding was refuted.

## Confirmed blockers

### 1. Stop can report success while the sampler is still acting

`_finalize_session_locked` sets the stop event, joins the sampler for at most ten seconds, then finalizes regardless of whether the thread exited. A sample tick can take longer than that while doing systemd calls or DAMON collection. The sampler can then move/kill processes, change the placement journal, append samples after the summary was written, or act on a DAMON session that finalization has closed. `migrate` is not serialized with placement release. The stop response may say `ok:true` while those side effects remain possible.

Required contract: a session is terminal only after the sampler has exited or is provably unable to cause further side effects. A blocked sampler must not permit a successful final response or a completed journal while it can still mutate state.

Discriminating oracle: gate a fake attach or DAMON collection on an event, request stop while blocked, then release it. Prove that no attach, kill, DAMON action, journal write, or sample append occurs after the terminal response; the journal remains complete; and the scope is not stopped while a lane PID remains inside it. The response must either wait for exit or explicitly report a non-final stopping state.

The product contract still needs the controller to choose the client-visible behavior for a sampler that exceeds the bounded join: wait for it to exit, or return a non-final `stop-pending` state. Do not silently finalize after timeout.

### 2. Contract-2 observer lacks live acceptance evidence

The code and unit tests cover local and hierarchical event counters and refusal of contract 1. Existing live records prove contract-1 peer binding/path mapping only. No live query proves contract-2 event-file reads, mount event-mode interpretation, or local-versus-hierarchical attribution. CP-20 still records live acceptance as pending, while contract 2 is the 1.0.0 headline capability.

Required evidence: build/deploy the candidate image through CIU and bind the image revision to the candidate commit; from a disposable placed peer, query contract 2 and assert ordered ancestry, event scopes, and regular/local maps; confirm contract 1 refuses with `bad-argument`; cause a leaf PID-limit event and a sibling-container PID-limit event, then prove the leaf and shared ancestor counters are attributed to the correct scopes. Do not trigger an enforcing event at the shared host slice. Full-source R2 is not required.

## Valid, not release-blocking

- **Placement origin race:** `_origin_scope_retains_unmoved_process` accepts a still-live selected PID when it is absent from the recorded origin's current census. Fix before closing CP-19: drop an exited candidate; require a live candidate to remain in its recorded origin; otherwise refuse with `identity-unavailable`. Assert scope creation never occurs on the inconsistent case. The race requires a concurrent privileged cgroup mover and is not considered a release blocker.
- **DAMON cleanup visibility:** cleanup failure is logged but not exposed in the summary, manifest, or stop response. DAMON is optional evidence, so this must not alter the workload verdict. An additive `damon.cleanup: confirmed | unconfirmed` disposition is recommended.
- **Implicit `cgprofile.slice`:** the concern is real, but the daemon still has Compose memory limits; a missing slice loses the authored memory floor and I/O weight. The existing documentation also claims a doctor message that the code does not produce. Reconcile the docs and revisit the deployment refusal separately; it is not a release blocker under the current design decision.
- **Exact-tree qualification:** saved R0/R1 and R3 receipts are dirty and ineligible. Rerun those gates on a clean exact tree after the blocker fix. CMRU release will rerun them on its candidate. Image digest, GitHub release body, and deployed revision identity are post-tag evidence for CP-21, not prerequisites for source review.
- **Documentation:** correct stale CP-13, contradictory consumer-guide live-probe wording, and add the documented `DAMON_STAT` limitation to CHANGES.
- Existing R3 canaries do not cover the new 1.0.0 behavior; add a canary for the sampler terminal-state invariant.

## Release decision

Do not release until the sampler terminal-state behavior is fixed and gated, and contract-2 live acceptance is recorded against the final candidate image. Keep long R2 deferred as authorized; it is not a substitute for the bounded live observer acceptance above.
