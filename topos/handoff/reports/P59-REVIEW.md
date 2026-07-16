# P59-REVIEW — Frontier Review Pass #2 (merge gate)

**Reviewer:** Frontier review + merge authority (Opus high), controller-workflow-v2 §6-§8
**Date:** 2026-07-13
**Verdict:** APPROVED (merged after review fixes)
**Route note:** implemented + self-reviewed via OpenRouter → DeepSeek (opencode harness) — quality data point below.

## Scope / gate re-run

- Focused: `PYTHONPATH=topos/src /tmp/p43-clean-venv/bin/python -m pytest topos/tests/test_p59_container_selector.py -q` → `9 passed in 0.16s`.
- Full suite (post-fix): `PYTHONPATH=topos/src timeout 400 /tmp/p43-clean-venv/bin/python -m pytest topos/tests -q -W error` → `923 passed, 2 skipped in 120.93s`.
- Environment: `/tmp/p43-clean-venv` (Python 3.14, textual 8.2.8, zstandard NOT installed).

## Findings

| # | Finding | Severity | flagged-by-pass-1 | Disposition |
|---|---|---|---|---|
| R1 | `docs/ROADMAP.md` mermaid graph: the three P59/P60 edges were re-indented inconsistently (`P57 --> P59` at column 0, `P55 -->` lines at 5 spaces) breaking the uniform 4-space block indentation. | Minor (hygiene) | no | Fixed — restored 4-space indent. |
| R2 | `docs/STATUS.md`: the P55 "31 focused tests…" continuation lines were dedented (2-space → 0/3-space), breaking the bullet's continuation nesting. | Minor (hygiene) | no | Fixed — restored 2-space continuation. |
| R3 | `CONTRACTS.md`: the reflowed filtered-recordings bullet gained a 3-space continuation indent (was 2). | Trivial (hygiene) | no | Fixed — normalized to 2-space. |

Everything else verified clean:
- **Core contract (requirement 2) correct:** container resolution runs INSIDE `Collector.collect_once()` against the freshly `enrich_entities()`-populated entity dict, not pre-resolved in `cli.py` against a throwaway sweep. The collector merge logic (`matched` set → `add_entity_ancestors`) is sound for all four selector combinations (none / predicate-only / container-only / both).
- **Resolution-ordering guard (test 8) is genuinely adversarial:** it proves `resolve_container_key` raises on the pre-`enrich_entities` walk and only succeeds through the collector's post-enrich path — it would fail if resolution had been placed in `cli.py` pre-sweep.
- Union semantics, exact/prefix match, ambiguous-prefix (lists both candidate names), nonexistent (exit 2, bounded P57 message on captured stderr), `--replay`/`--attach` rejection (exit 2), and `--metrics compact` composition (`ram` present, `net_rx_bps` absent, `eframe.network is None`) all asserted on observable frame contents / exit codes / stderr. No hollow tests.
- `--container` is additive to `parse_args` and distinct from the P57 `inspect-files`/`action` subcommand flags (untouched). The P57 `.. todo::` note is correctly downgraded to a `.. note::` pointer; the P56 TODO is left intact as instructed.
- Scope: all 10 files under `topos/**`.

## Known limitation (accepted, not blocking)

The REPORT documents that on the **live TUI / `--record`** paths a `ContainerResolveError` raised mid-stream may propagate through the frame-stream generator rather than being caught as cleanly as on `--once`. The `--once --json` path (the fixture-testable one, and the one all tests exercise) catches it and exits 2 with the bounded message. For a bounded, fixture-testable ergonomics slice this is an acceptable honest gap, disclosed in the REPORT rather than hidden.

## flagged-by-pass-1 tally (P59)

SELFREVIEW found and fixed a solid set of MECHANICAL items on its own: 7 wrong line-number references in the REPORT, stale test-evidence counts (922→923, timing), and a stale "1 failed" flake note (**yes ×3** categories — all independently catchable by pass #2). But it explicitly recorded "**Finding: none**" for hygiene/ASCII (§5) and missed all three markdown-indentation regressions it had itself introduced into ROADMAP/STATUS/CONTRACTS (R1-R3, **no ×3**). Consistent with the standing pattern: same-tier self-review scores well on mechanical self-consistency (line refs, counts, dates) and poorly on diff-hygiene it authored.

## OpenRouter/DeepSeek quality data point (for the controller)

Consistent with reasonix-direct DeepSeek packages, not degraded by the OpenRouter route:
- **Substantive quality on par:** the hard part of this package — moving resolution into the collector for post-enrich correctness, and writing an ordering guard that actually fails against the wrong design — was implemented correctly on the first attempt, with a genuinely adversarial test (test 8), not a hollow one. No correctness defect found at pass #2.
- **Defect profile identical to direct-DeepSeek:** the only pass-#2 findings were cosmetic markdown-indentation regressions from doc reflows — the same low-severity hygiene class seen on prior flash-tier packages, and the same class same-tier self-review reliably misses.
- **No route-specific artifacts:** no 504s, no truncated/incremental-write damage, no argv-wedge symptoms in the committed diff; the self-review even corrected its own stale numbers. One data point, but it reads as "DeepSeek quality, different pipe" — supports the 2026-07-13 deciding-log note treating OpenRouter→DeepSeek as a usable fallback route alongside reasonix-direct.
