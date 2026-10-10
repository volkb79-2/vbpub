# B087 — JavaScript R3 real-consumer qualification

**Date:** 2026-10-08 (updates the 2026-10-07 archival record)

**Original Assay implementation commit:** `cac4a92f86537a22bf86ae1c39a0f3523ffb4b7d`

**Historical qualification:** operator-reported PASS for both JavaScript R3 mechanisms; the detailed verdicts and verifier results were not retained.

**Current status (2026-10-09):** both current JavaScript R3 canaries passed
against dstdns using the pinned Assay candidate below. Full schema-v15
verdicts, separate `assay verify` transcripts, progress streams, and run-gate
logs are retained. The JavaScript adapter and registry code are unchanged
between that candidate and B087 tip `47fbaac6`. B087 is provisionally merged
into integration branch `assay-b131-r2` at `1b5f61a9`; the integrated
`tester-unified` gate and final exact-tip review are pending. No release has
been made.

## Current requalification (2026-10-08)

Both lanes ran serially through dstdns's main `scripts/gate-slot.sh` wrapper
and run-gate rev 55 against the same clean disposable CIU worktree. The lane
declarations remain `kind = "assay"`; run-gate verified the candidate zipapp's
version and SHA-256 pin before invoking Assay.

| Fact | Value |
|---|---|
| Assay branch / source tip | `assay-b087-js-canary` / `ad17ab38` |
| Assay artifact | `8.0.1.dev126+gad17ab38a`, SHA-256 `cbef763b036f3fcf3c91e46028ac482621372d154306c8790bdad16c69b86d18` |
| dstdns main / comparison base | `305d85198bc121d3142ff4091cb1f2be821c6232` / `fc75306035e8ffe6493417839649f09fec174991` |
| Judged disposable tree | `00b6f56d1dfa9f942c1ae261ef1b6306448a7cfe` |
| run-gate container | `assay-b087-js-r3-qual-20261008-n1gbah-test-runner` |

| Lane | Gate | R0 | R1 | R3 control | Transformed result | R3 | `assay verify` |
|---|---|---|---|---|---|---|---|
| `b087-import-break` | PASS, exit 0 | PASS | PASS, 29/29 changed executable lines and 16/16 branches | PASS | FAIL, `COMMAND_FAILED` (expected and observed) | PASS | exit 0 |
| `b087-uncovered-line` | PASS, exit 0 | PASS | PASS, 29/29 changed executable lines and 16/16 branches | PASS | FAIL, `UNCOVERED_LINES` (expected and observed) | PASS | exit 0 |

Both verdicts are schema v15, report Assay `8.0.1.dev126+gad17ab38a`, and
bind to the same zipapp digest and judged tree above. The second lane ran from
17:22:29.730Z to 17:23:54.283Z (84.565 seconds in its progress stream); the
gate-slot wrapper and run-gate both exited 0. The run-gate profiler emitted a
cleanup warning (`NoneType` access) and recorded no profile; the lane verdict
remained PASS.

Evidence is retained under `reports/B087-js-r3-evidence/`:

- `dstdns-import-break.verdict.json`, `.assay-verify.txt`, `.progress.jsonl`,
  `.run-gate-inner.log`, and `.run-gate-outer.log`;
- `dstdns-uncovered-line.verdict.json`, `.assay-verify.txt`, `.progress.jsonl`,
  `.run-gate-inner.log`, `.run-gate-outer.log`, and `.outer-exit.txt`.

The disposable dstdns worktree and its support stack remain in place. No
registered Assay gate or merge was started.

## Original run identity (operator-reported)

The following identity and coverage details are transcribed from the original
operator record. The retained lane logs show only the outer PASS, judged
commit, and argv; the detailed verdicts were removed with the disposable
checkout, so the original judge-provenance binding and coverage counts cannot
be independently confirmed from the surviving artifacts.

- Reported Assay zipapp: `8.0.1.dev32+gcac4a92f8`
- Reported zipapp SHA-256: `2498cf249f3c598216414da844f4b80faf6a5cc7f17f864a84ad47426a3e4abb`
- Reported dstdns `main` before and after qualification: `305d85198bc121d3142ff4091cb1f2be821c6232`
- Reported real comparison base: `fc75306035e8ffe6493417839649f09fec174991`
- Reported clean judged commit in the disposable dstdns worktree: `e84c936b155f153e9e8d3183f92d7fca92dd7ba1`
- The original operator report says Assay resolved the declared base to `fc75306035e8ffe6493417839649f09fec174991` (`merge-base`) and the judged commit contained only temporary qualification lane declarations on top of dstdns `main`.
- Reported target: `applications/webapp-ui-react/src/api/queries/domains.ts`. The real test files `src/api/queries/__tests__/domains.test.ts` and `domainIdentity.test.ts` imported it as `../domains`.

The original operator report says the real range changed the target and its imported callers/tests. With dstdns `ui_unit` source roots and Istanbul coverage policy, it records 29 executable changed lines all covered and 16 reported branches all covered.

## Original execution (archival)

The temporary CIU-managed dstdns worktree used its own `dstdns/test-runner:latest` container, `assay-b087-js-r3-qual-bsokq1-test-runner`; both run-gate invocations resolved to that container and the loaded `dev-gates.slice`. `run-gate` was rev 55. The existing Vitest command used `npx --no-install`, the committed lockfile, and the worktree's `node_modules` link path.

Outer commands, run serially through dstdns's main `gate-slot.sh` wrapper:

```bash
/workspaces/dstdns/scripts/gate-slot.sh run-gate b087-import-break \
  --worktree /workspaces/dstdns/.worktrees/assay-b087-js-r3-qual \
  --base fc75306035e8ffe6493417839649f09fec174991

/workspaces/dstdns/scripts/gate-slot.sh run-gate b087-uncovered-line \
  --worktree /workspaces/dstdns/.worktrees/assay-b087-js-r3-qual \
  --base fc75306035e8ffe6493417839649f09fec174991
```

The original operator notes say each temporary command lane invoked the built
zipapp directly with its `assay.toml` lane, `--request-base`,
`--require-judge-provenance`, `--resume`, and a gitignored
`.assay/progress-<lane>.jsonl` stream and verdict destination. They report that
the verdict provenance matched the zipapp version and SHA-256 above, but the
verdict files needed to verify that match were not retained.

## Original run observations (not independently retained)

An independent Sol xhigh review on 2026-10-07 found that the records retained
after cleanup do not substantiate the detailed verdict fields or the separate
`assay verify` results below. The surviving run-gate logs
(`/tmp/run-gate/lanes/b087-import-break/4f817878e455c660ad721e6677021c7a.log`
and
`/tmp/run-gate/lanes/b087-uncovered-line/8a3b11e4477d0a2f3c7128e2825b0358.log`)
record only the command-lane PASS, judged commit, and argv. The disposable
checkout's verdict JSON files were removed, and no verifier invocation or
transcript survives. The rows below preserve the original operator's run
observations; they are not independently auditable evidence. Requalification
with retained verdicts and verifier transcripts is required before using
these details as B087 acceptance evidence.

| Lane | R0 | R1 | R3 control | Transformed run | R3 claim | Assay `verify` |
|---|---|---|---|---|---|---|
| `b087_import_break` | operator-reported PASS | operator-reported PASS, 29/29 changed executable lines and 16/16 branches | operator-reported PASS | operator-reported FAIL, expected and observed `COMMAND_FAILED` | operator-reported PASS | operator-reported exit 0; transcript not retained |
| `b087_uncovered_line` | operator-reported PASS | operator-reported PASS, 29/29 changed executable lines and 16/16 branches | operator-reported PASS | operator-reported FAIL, expected and observed `UNCOVERED_LINES` | operator-reported PASS | operator-reported exit 0; transcript not retained |

Both run-gate command lanes returned PASS (exit 0), as the retained outer logs
show. The schema-v14 verdict contents, canary attempt counts, and provenance
fields in the table are operator-reported and were not preserved for review.

## Original cleanup and limits (archival)

The original operator reports that `ciu worktree rm assay-b087-js-r3-qual -y`
removed the disposable checkout, its 14 containers, project volumes, and
network; exact branch/instance resource prefixes were then confirmed absent.
dstdns `main` was reported clean at
`305d85198bc121d3142ff4091cb1f2be821c6232`.

The original operator also reports that the dstdns run-gate profiler printed a
cleanup warning on both runs (`NoneType` during profiler cleanup; no profile
recorded). They observed no change to either Assay verdict or run-gate exit
status; that observation is not independently verifiable from the retained
logs.

This section records only the original attempt and its evidence gaps. The
current qualification above supersedes its acceptance status. B087 remains
unreleased pending the integrated registered Assay gate and exact-tip review.
