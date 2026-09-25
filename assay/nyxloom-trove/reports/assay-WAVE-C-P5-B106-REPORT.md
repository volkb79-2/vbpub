# Wave C P5 — B106 selective mutation reuse report

**Status:** IMPLEMENTED; registered `tester-unified` gate PASS on source tip
`a0e2fe23`. This report is bound to CIU worktree
`/workspaces/vbpub/.worktrees/assay-wave-c-p5-b106-selective-reuse`, branch
`assay-wave-c-p5-b106-selective-reuse`, originally based on main tip
`3f117b27`; current main `23214ef5` was merged in `34580468` before the final
gate.

## Decision and review

B106 is limited to a verdict schema v13 change. The xhigh design recommendation
and the operator's ruling are recorded as A-461 in `decisions.md`: the existing
candidate inventory field is used, current kills require current pytest
witnesses, incomplete/v12 inputs cold-start, and no lane schema or reason-code
change is needed.

The first GPT-6-Luna xhigh code review found five actionable defects. The
implementation now rejects third-party pytest lifecycle hooks for replay,
prevents `--rejudge-outcome` candidates from consuming old witnesses, persists
and restores execution provenance in resume records, includes effective
pytest configuration in plan eligibility, and describes JavaScript R2
ingestion as an existing supported path rather than as a new registration.

The final review found one remaining P2: a plugin with module name
`assay_mutation_witness_plugin_extra` could pass a prefix-based allowlist. The
allowlist now checks exact Assay plugin names and resolved generated plugin
paths; pytest built-ins must originate under pytest's `_pytest` package path.
The regression loads a lookalike plugin that changes a passing report and the
session exit status. Its campaign must fall back and record the mutant as a
full-run survivor. A fresh GPT-6-Luna xhigh follow-up returned **no actionable
P1/P2 findings**. The review did not edit files or run tests.

## Verification before the registered gate

- The combined focused B106, verifier, standalone and distribution checks
  reached **315 passed**. ShellCheck initially caught the new command's
  intentional empty `PYTHONPATH`; a local `SC1007` suppression fixed it.
- After that fix, `shellcheck tools/tester-unified-gate.sh` exited 0 and the
  gate marker/order test passed.
- After the final hook-allowlist repair, the lookalike-hook and genuine
  liveness-plugin integration tests passed (**2 passed**), and
  `tests/test_b106_reuse_and_witness.py` passed (**25 passed in 19.35s**).
- `git diff --check` passed on the final merged source tree before its
  registered gate. Final gate evidence follows.

## Registered gate

The first committed-code attempt ran `./run-gate.py tester-unified` at
`d43167c6cfc0280d27a9173fff301a52df1ae714` and exited 1 during the historical
v6-v10 hard-cut check. The installed v13 verifier correctly rejected the v6
template, but the inline gate assertion still expected the diagnostic to say
the current verifier was v12. The gate stopped before the P5 suite or
self-hosted lane. Its raw 3,995-byte log is preserved at
[`assay-WAVE-C-P5-gate-failed-2026-09-25-d43167c6.log`](assay-WAVE-C-P5-gate-failed-2026-09-25-d43167c6.log),
SHA-256 `deb8164774864c80134b7f36244128515490ea2073aff1a4d0a15900f186fb18`.

The gate now imports `VERDICT_SCHEMA_VERSION`, asserts the intended Wave C v13
cut, and derives the expected historical refusal from that constant. A static
gate-source test pins this invariant. That fix exposed a second stale gate
assumption on commit `8aa1d61d`: the gate still ran W8's frozen v12 positive
acceptance suite against the v13 wheel. The verifier correctly refuses v12;
90 v12 tests therefore failed and 21 passed before the self-hosted lane. This
was a gate-selection error, not a B106 behavior failure. The 178,237-byte raw
log is preserved byte-for-byte, gzip-compressed, at
[`assay-WAVE-C-P5-gate-failed-2026-09-25-8aa1d61d.log.gz`](assay-WAVE-C-P5-gate-failed-2026-09-25-8aa1d61d.log.gz).
Its decompressed SHA-256 is
`eb8c000888e72d94d7b6cf9fd79c006e8d6f918dabf3cfcb6dc66a86b7087b26`; the
compressed artifact SHA-256 is
`683da5550f6368872f293f7ec32462bb48681d4e1597e7f226d43ff4237de2d6`.

The gate now collects W7/W8 as historical suites and verifies their v11/v12
templates are rejected by v13 alongside W1/W2/W4/W5/W6. It no longer runs
W8's v12-positive assertions against the v13 wheel. The historical-cut marker
is now `verdict-v6-v12-hard-cut-verified`, followed by the B106
`verdict-v13-successors-verified` suite. ShellCheck, `bash -n`,
`git diff --check`, and the focused gate-source assertion pass. A fresh
independent review found a P2 coverage gap: that demotion would remove W8's
only positive R4 red-first control, which the B106 suite does not exercise.
Added `tests/test_verdict_v13_successors.py` to preserve the v13-valid R3
multi-target and R4 red-first controls with their refusal cases. These W8
documents have no native mutation outcomes, so the test updates only the
schema version; the remaining W8 documents stay historical and are hard-cut.
The first follow-up found two more P2 gaps: the valid R4 `FAIL/RED_FIRST_UNPROVEN`
case with no after-run, and R3 refusal cases for a shortened attempt list and
a `not_attempted` entry carrying a run. All three controls are now present.
The resulting eleven behavioral checks, the focused gate-source assertion,
ShellCheck, `bash -n`, and `git diff --check` pass. A final follow-up review
returned **no actionable defect**. Its focused run passed 37 checks across the
v13 successor and gate-source suites, and its independent probe confirmed all
W7/W8 templates produce the exact expected v13 refusal. The registered gate
history and final result follow.

The first container was
`run-gate-assay-selfhosted-397279-21079-1790344007`, confirmed at 3 CPUs under
`dev-gates.slice`; run-gate removed it after the early failure. At the required
90-second check, the log showed the mismatch and terminal exit. Since the run
stopped before the main lane, it does not provide a full runtime estimate; the
prior P4 gate took about 15 minutes.

The retry container was
`run-gate-assay-selfhosted-403856-7613-1790344303`. The 90-second check found
the W8/v13 mismatch and terminal exit. The attempted CPU-cap inspection found
that the wrapper had already removed this failed container, so its `NanoCpus`
value could not be confirmed. It stopped before the self-hosted lane; the
previous P4 gate's roughly 15-minute duration remains the runtime estimate for
the complete retry.

The third attempt started on `c28fbbb9`, but the shared checkout's `main` had
advanced to `23214ef5` since the package branch's `3f117b27` base. The 90-second
progress check showed every focused Wave C/P5 phase passing and the
self-hosted full suite starting. The exact container
`run-gate-assay-selfhosted-509310-25194-1790349041` was confirmed at 3 CPUs
under `dev-gates.slice`. I intentionally stopped that run with exit 143 before
using it as acceptance evidence, because the prompt requires the final package
tree to contain current `main`. The 4,199-byte raw log is preserved as
[`assay-WAVE-C-P5-gate-cancelled-2026-09-25-c28fbbb9.log.gz`](assay-WAVE-C-P5-gate-cancelled-2026-09-25-c28fbbb9.log.gz),
raw SHA-256 `e70016a4e5ed5d340047f86bd2cd35c975df1040fb8df4601224e4e66c9ecac2`,
compressed SHA-256
`35f30e8581f96d7a9d2d5637d364bdd0bf7f574521a65495f87feaba8ffa74b9`.
Fetched `main` was merged cleanly into the P5 branch as `34580468`. The final
registered gate ran on that merge plus the report-only commit `a0e2fe23`.

### Final registered gate — PASS

`./run-gate.py tester-unified` passed on exact source tip
`a0e2fe23d4a47ddd1c04564a901f5ef647acc670`, which contains current main
`23214ef58d6ba91bca6e01ee6cef0553929e440a` through merge `34580468`. The run
started at 2026-09-25 15:20:32 UTC and completed at 15:38:59 UTC (18m27s).
At the 90-second check, wheel installation and all focused v13/B106 phases had
passed and the self-hosted full suite had started. At the near-completion
check, the self-hosted suite, Topos and CMRU qualifications, and independent
self-hosting witness had passed; the gate then completed its pyflakes phase.

All 13 gate phase markers appeared, including
`verdict-v13-successors-verified`, `topos-qualified`,
`cmru-b006a-qualified`, `self-hosted-lane-passed`,
`independent-self-hosting-passed`, and `pyflakes-clean`. Exit evidence is
`ASSAY_GATE_CONTAINER_EXIT=0`, `ASSAY_REGISTERED_GATE_COMPLETE=1`,
`run-gate: lane 'tester-unified' exit 0`, `OUTER_GATE_EXIT=0`, and
`GATE_EXIT=0`. The exact container
`run-gate-assay-selfhosted-520812-20390-1790349632` ran under
`dev-gates.slice` with `NanoCpus=3000000000`; it was removed after completion.
The optional `cgprofile-host-daemon` was unavailable, so run-gate reported
coarse rusage sampling; this did not affect the gate result.

The raw 6,955-byte log is preserved at
[`assay-WAVE-C-P5-gate-2026-09-25-a0e2fe23.log.gz`](assay-WAVE-C-P5-gate-2026-09-25-a0e2fe23.log.gz),
SHA-256 `f930192e75eca593d82fce196eb5a53a0323ef80621fc95a0bf7ac250570a056`;
the compressed artifact SHA-256 is
`9d0f26b976bd0a21fcf59543815c79fb93d5d51890c21d3d7c4ba2e4f7b1d438`.
The archived log and this report update are report-only additions after the
passing source tip; the tested source tip remains `a0e2fe23`.

## Agreed work after P5

After P5 is merged and the single Wave C release is complete, B105 is the next
package, before M7. The pre-release Wave C lane remains R0-only. B105 will add
a separately invocable `tester-unified` self-qualification run: R1 measures
branch arcs over all declared production source with a 100% branch floor, R2
runs native mutation over the complete source target, and R3 exercises the
declared canary contract. The final B105 tree must produce and retain an actual
verifier-accepted report from that run; reviews are useful but cannot stand in
for the measurement. The detailed acceptance requirements are now in B105 in
`4-backlog.md`. Since B106 ships in Wave C, later runs may use `--reuse-from`
only when current evidence proves each reused result; uncertain candidates run
fully, and every run checks the current baseline first.
