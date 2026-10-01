# P97-REPORT — Close small deterministic coverage gaps

## Outcome

All 16 declared targets are at exact **100% statements and 100% branches**
in the complete xdist gate. The final controller runs each collected 1,825
tests, passed the changed-line evaluator, and passed a mechanical per-target
JSON assertion requiring empty `missing_lines` and `missing_branches`.

## Target closure

| Target | Final statements | Final branches |
|---|---:|---:|
| collect/zswapmath.py | 13/13 | 6/6 |
| collect/dockerjoin.py | 107/107 | 44/44 |
| collect/collector.py | 245/245 | 98/98 |
| model.py | 136/136 | 32/32 |
| registry.py | 49/49 | 18/18 |
| procs/identity.py | 18/18 | 0/0 |
| procs/sensitivity.py | 17/17 | 8/8 |
| procs/owners.py | 41/41 | 8/8 |
| ui/keys.py | 5/5 | 0/0 |
| ui/damon_control.py | 33/33 | 0/0 |
| ui/sparkline.py | 44/44 | 20/20 |
| record/ring.py | 98/98 | 26/26 |
| inspect_files/plan.py | 49/49 | 8/8 |
| damon/paddr.py | 50/50 | 12/12 |
| actions/preview.py | 38/38 | 10/10 |
| daemon/component_health.py | 146/146 | 26/26 |

The registry and sparkline denominators decreased because independent review
proved that one registry guard duplicated earlier validation and that the
sparkline truncation branch was unreachable by construction. Both dead paths
were removed without changing observable behavior. No exclusions or coverage
pragmas were added.

## Tests and behavioral repairs

- `test_p97_quickwins.py` collects 66 focused tests with exact results,
  exceptions, state transitions, and external-effect assertions.
- A real passive-DAMON fixture now proves that `--metrics damon` preserves the
  structured DAMON block while dropping unselected structured blocks.
- The Textual cancel action is tested at its dismissal boundary.
- Paddr tests prove wrong-confirmation and duplicate-owner refusal before
  sysfs writes.
- Ring saturation and UTF-8 zero-limit behavior cover the two paths previously
  mislabeled as aggregation artifacts.

## Gate and parity evidence

Two final clean controller runs:

| Run | Full suite | Changed-line gate | Target JSON assertion |
|---|---|---|---|
| 1 | 1825 passed in 48.94s | OK, 0/0 changed executable lines | 16/16 exact |
| 2 | 1825 passed in 51.84s | OK, 0/0 changed executable lines | 16/16 exact |

Both target sets have no missing executable lines or branch pairs, so their
executed/missing target sets are identical.

## Canary

After P96 merged at `025fb843`, the controller ran
`./nyxloom/exec-nyxloom.py gate verify topos`. Known-good `topos-suite`
exited 0; a planted import break in `topos/src/topos/cli.py` exited 1; verdict:
`TRUSTWORTHY`. The authoritative gate therefore declares
`tests-pass`, `changed-line-coverage`, and `canary-verified`.
