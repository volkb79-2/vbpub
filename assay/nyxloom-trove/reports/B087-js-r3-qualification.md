# B087 — JavaScript R3 real-consumer qualification

**Date:** 2026-10-07  
**Assay implementation commit:** `cac4a92f86537a22bf86ae1c39a0f3523ffb4b7d`  
**Qualification:** PASS for both registered JavaScript R3 mechanisms.

## Source and judge identity

- Assay zipapp: `8.0.1.dev32+gcac4a92f8`
- Zipapp SHA-256: `2498cf249f3c598216414da844f4b80faf6a5cc7f17f864a84ad47426a3e4abb`
- dstdns `main` before and after qualification: `305d85198bc121d3142ff4091cb1f2be821c6232`
- Real comparison base: `fc75306035e8ffe6493417839649f09fec174991`
- Clean judged commit in the disposable dstdns worktree: `e84c936b155f153e9e8d3183f92d7fca92dd7ba1`
- Assay resolved the declared base to `fc75306035e8ffe6493417839649f09fec174991` (`merge-base`). The judged commit contains only temporary qualification lane declarations on top of dstdns `main`; the production source is byte-identical to dstdns `main`.
- Target: `applications/webapp-ui-react/src/api/queries/domains.ts`. The real test files `src/api/queries/__tests__/domains.test.ts` and `domainIdentity.test.ts` import it as `../domains`.

The real range changes the target and its imported callers/tests. With the dstdns `ui_unit` source roots and Istanbul coverage policy, Assay judged 29 executable changed lines: all 29 were covered, and all 16 reported branches were covered.

## Execution

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

Each temporary command lane invoked the built zipapp directly with its
`assay.toml` lane, `--request-base`, `--require-judge-provenance`, `--resume`,
and a gitignored `.assay/progress-<lane>.jsonl` stream and verdict destination.
The verdict's provenance matched the zipapp version and SHA-256 above.

## Observed results

| Lane | R0 | R1 | R3 control | Transformed run | R3 claim | Assay `verify` |
|---|---|---|---|---|---|---|
| `b087_import_break` | PASS | PASS, 29/29 changed executable lines and 16/16 branches | PASS | FAIL, expected and observed `COMMAND_FAILED` | PASS | exit 0 |
| `b087_uncovered_line` | PASS | PASS, 29/29 changed executable lines and 16/16 branches | PASS | FAIL, expected and observed `UNCOVERED_LINES` | PASS | exit 0 |

Both schema-v14 verdicts recorded the same judged commit and judge provenance,
and both run-gate command lanes returned PASS (exit 0). The Assay verdicts
record one attempted canary each, with `control_outcome = PASS`, the expected
transformed failure, and an R3 PASS claim.

## Cleanup and limits

`ciu worktree rm assay-b087-js-r3-qual -y` removed the disposable checkout,
its 14 containers, project volumes, and network. Exact branch/instance
container, volume, and network prefixes were then confirmed absent. dstdns
`main` remained clean at `305d85198bc121d3142ff4091cb1f2be821c6232`.

The dstdns run-gate profiler printed a cleanup warning on both runs
(`NoneType` during profiler cleanup; no profile recorded). This did not change
either Assay verdict or run-gate exit status. The warning belongs to the
qualification runner, not the Assay result.

This closes B087's real-consumer qualification acceptance. The requested
independent Sol xhigh review and Assay's registered gate remain pending. The
branch is not merged or released while B114 and B131 integrate first.
