# RG-49 B9 provider verification — intermediate gate rejection

REJECT

Exact intermediate HEAD `a84581fcd915e36e4daf72774a3f1822ba61dc51`, tree
`398f8484e32468999e54f29acfb9c59c456adc27`, clean pre/post status.
Actual route remains fresh `gpt-5.6-sol` / `xhigh`, session
`01a0b128-2063-7760-ab6e-3959544c5034`.

## B12 — guarded decoder retains a stale trusted-site exemption

Severity: major acceptance blocker; no new decoder behavioral failure.
The B11 repair adds the correct RecursionError handler, but the mutation
record loader remains in `TRUSTED_SITES` in
`assay/tests/test_untrusted_json_parse_sweep.py:96`. Its existing
`test_no_trusted_entry_is_stale` correctly refuses that contradiction.
Remove the obsolete exemption and pin the newly guarded loader alongside
the other named parse sites so its guard cannot silently disappear.
Do not weaken or suppress the audit assertion.

The exact installed-wheel R0 verdict is **FAIL / COMMAND_FAILED**, exit 1.
Container `rg49-b9-provider-01a0b128`, ID
`eee84f967311ca3a3e1f56ab2d7a2c9a1662253fa3fd271a702c1159469bdc53`,
ran 2026-09-17T21:07:54.151103997Z to 21:24:58.496761917Z.
Docker job 1; wait/log/inspect CLI statuses 0; terminal inner marker 1;
OOMKilled=false; NanoCpus=3000000000. The separate 60-minute watcher
finished normally and observed job 1, without a budget exhaustion.
The diagnostic rerun reports **1 failed, 4753 passed, 21 skipped** in
484.08 seconds. That rerun is diagnostic, not a second successful gate.
Only six acceptance phase markers completed; no qualification or final
success is claimed.

The 109 focused identity/docs tests and independent CLI/Mode-B/resource
probes pass on this intermediate HEAD, but do not override its red gate.
Full raw receipts, failed schema-11 verdict and exact built wheel were
preserved before audit repair under
`.run-gate/rg49-b9-review-evidence/archives/a84581fc-intermediate/`.
After repair, commit and rerun the focused audit, behavioral probes and
complete shipped inner gate on a quiet clean new HEAD.

No merge, release, global install, forbidden namespace or operator-path
modification occurred. This intermediate rejection remains historical
and must be distinguished from the final exact-tree verdict.
