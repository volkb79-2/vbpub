# CIU v2 — Work Packet Board

Execution state for Stages 1–3 (see ../SPEC.md). Rules: one packet = one
commit; full suite green before commit; Sonnet for mechanical packets,
strong model for spec-sensitive (*) packets and reviews; engine.py/deploy.py
untouched until the cutover packets. Update Status as work lands.

Baseline: 53 tests green @ branch ciu-v2 (af0987f).

## Wave 1 — foundations (parallel, disjoint)

| ID | Packet | Files | Status |
|---|---|---|---|
| P1 | paths + procutil (S1.4, S1.9) | src/ciu/paths.py, src/ciu/procutil.py, tests/tests/test_ciu_paths_procutil.py | DONE |
| P2 | secrets directive parser v2 (S4.1–S4.7) | src/ciu/secrets/{__init__,directives}.py, rewrite tests/tests/test_ciu_secret_directives.py, delete src/ciu/secret_directives.py | DONE |
| P3 | config model (S3) | src/ciu/config_model.py, tests/tests/test_ciu_config_model.py | DONE |

## Wave 2 — spec-sensitive core (* strong model)

| ID | Packet | Files | Status |
|---|---|---|---|
| P4* | secret providers + materializer (S4.8–S4.16, S4.24–S4.26) | src/ciu/secrets/{providers,materialize}.py + tests | DONE |
| P5* | composefile: render, overlay gen, leak scan (S4.17–S4.23, S8.1) | src/ciu/composefile.py + tests | DONE |
| P6 | hooks v2 (S9) | src/ciu/hooks_runner.py + tests | DONE |
| P7 | deploy submodules: http_util, health, phases, profiles (S7) | src/ciu/deploy_pkg/*.py + tests | DONE |

## Wave 3 — integration & cutover (* strong model)

| ID | Packet | Files | Status |
|---|---|---|---|
| P8 | workspace_env v2 (S2.7 table, S2.8 duties, S1.9 native parity, ENV_TYPE=native) | src/ciu/workspace_env.py + tests | DONE |
| P9* | engine pipeline rewrite to S8.3 + CLI (S10.1, ciu secrets) | src/ciu/engine.py, src/ciu/cli.py | DONE |
| P10* | deploy rewire: --profile, numeric phases, honest health gate, failure semantics, registry auth (S7, S10.2) | src/ciu/deploy.py | DONE |

## Wave 4 — Stage 3 demo + contract tests

| ID | Packet | Files | Status |
|---|---|---|---|
| P11 | rebuild test-repo as miniature dstdns | test-repo/** | DONE |
| P12 | contract tests keyed to spec IDs | tests/tests/test_spec_*.py | DONE |
| P13 | finalize MIGRATION-V2.md; ARCHITECTURE.md; rewrite CONFIG/CIU/CIU-DEPLOY as guides | docs/** | DONE |

## Review gates

- R1 after Wave 1: diff review (strong) → commit per packet — DONE (188 green, commits 87c18c6/1ab0748/2e846f4 + regex fix folded into P2)
- R2 after Wave 2: adversarial review of P4/P5 — DONE (verdict SOUND-WITH-FIXES; F1-F6 fixed, regression tests in test_ciu_r2_fixes.py; 407 green; commits bf0ebd1..8abe28f)
- R3 after Wave 3: full suite + dry-run smoke — DONE (458 green; render-toml + dry-run deploy exit 0; found+noted UX footgun: inherited REPO_ROOT from foreign workspace wins over resolved root — harden in Wave 4). LIVE container smoke deferred to post-P11 (needs public images).
- R4 after Wave 4: multi-angle strong review (engine/deploy/seams) — DONE: 2 high + 4 medium + lows all fixed (stale build/ in wheel, v1 example hooks, preflight exit codes, HookExecutionError, S3.8 withdrawal, overlay scan, atomic writes); 501 green; wheel verified; live smoke done in P11 (4/4 healthy incl. cross-stack vault chain)
