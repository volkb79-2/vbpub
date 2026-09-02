# ciu-P49 — LOG

Package: `nyxloom-trove/handoffs/ciu-P49-ciu89-probe-container-override-ciu90-governance-cpu-quota.md`
— CIU-89 (Part A, multi-service probe-container resolution) + CIU-90 (Part B,
governance CPU-quota key). Worktree
`/workspaces/vbpub/.worktrees/feat/ciu-P49-probe-container-and-governance-cpu`,
branch `feat/ciu-P49-probe-container-and-governance-cpu`, based on vbpub
`main` at `af98e1f0` (the handoff's own carve commit — no ciu commits land
between it and this package's own).

Fresh implementer, zero prior context beyond the handoff, the two backlog
rows, and the live repo. Both parts implemented, both mutation-verified,
CIU-90's live `docker inspect` verification run, full suite green. Real
gate run TWICE: first run FAILED (one self-caused R1 coverage gap + one
pre-existing, unrelated R0 flake, both diagnosed and addressed below and
in the REPORT); second run PASSED clean.

---

## Part A — CIU-89, exactly as designed, one deviation caught by a real test

Built route (a) — the sibling `provides_container` override table — as the
handoff specified, not the compose-service-list walk. `_resolve_probe_container`
(`src/ciu/provisioning.py:401-465`) checks
`stacks[stack_path]["provides_container"].get(ref)` before falling back to
`_stack_container_name`'s basename guess; an override is passed to
`container_name()` directly (with the same unresolvable-project/env-tag
fallback `_stack_container_name` itself uses). `config_model.validate_stack_provisioning`
(`src/ciu/config_model.py:1267-1299`) gained the `[S13.2]` checks: a
`provides_container` key must already be one of that stack's own `provides`
entries, and every value must be a non-empty string — both violations
collected alongside requires/provides violations in the existing "list ALL
violations" pass, not raised separately.

**Threading the new key through the `stacks` dict — more builders than the
handoff's literal grep pointer named, verified by tracing actual call
sites, not by pattern-matching the dict-literal text.** The handoff pointed
at "provisioning_graph() and the other builder near deploy.py:760". Tracing
which `stacks`/`graph` dicts are actually passed as `stacks=` into
`provisioning.probe_ref` (the only thing that matters for correctness) found
**two** real feeders, not the one the line-760 pointer suggested:
`deploy.provisioning_graph()` (`:632-649`, `ciu up`'s per-phase probe graph)
and `action_check`'s own separate builder (`:3141-3167`, `ciu check --live`'s
distinct feed path — confirmed via `grep -rn "probe_ref(" src/ciu/` showing
exactly three call sites, two fed by `provisioning_graph`'s output and one by
`action_check`'s own `stacks`). `provisioning_preflight`'s builder near line
767 (the literal text match closest to "760") is real but feeds only
`lint_graph`, never a probe — added `provides_container` there too anyway
since it's free and matches the dict shape everywhere.

**Caught by a real test, not by design review**: I initially threaded
`provides_container` into `action_graph`'s builder too (deploy.py, the
`--graph` command), reasoning it kept all `stacks[rel] = {...}` builders
consistent. `test_ciu_deploy_deeper2.py::test_graph_emits_machine_readable_edges_for_valid_rendered_topology`
failed — `render_graph`'s `fmt="json"` path (`provisioning.py:291-302`) echoes
the `stacks` dict **verbatim** into `--graph --fmt json`'s public `"stacks"`
key, so the extra key would have been a real, unasked-for shape change to an
external tooling contract that `--graph` (which never resolves a probe
container) has no use for. Reverted that one builder; the other three are
correct and green.

## Part B — CIU-90, exactly as designed

`GOVERNANCE_DEFAULTS["cpus"] = ""` (`src/ciu/governance.py:80-88`),
`INJECTED_KEYS` gains `"cpus"` (`:260-278`, no translation needed unlike
`mem_swap_limit`). `resolve_config()` validates `""` or `float(cpus) > 0`,
raising `[S15.21]` otherwise (`:422-438`, mirrors `io_weight`'s S15.14
shape). `build_injections()` injects `frag["cpus"]` only when configured AND
not already author-set (`:1033-1038`), and the `cpus=` notes-list entry is
conditional — only appended when set, via `*([f"cpus={cpus}"] if cpus else [])`
spliced in at the right position rather than a fixed-position placeholder
string (`:1082`), matching the handoff's explicit "not unconditionally"
instruction and `blkio_config`'s own conditional-note precedent rather than
`mem_limit`'s unconditional one.

Type chosen for the injected `cpus` value: a **string** (`"2"`, `"1.5"`),
matching `mem_limit`'s own string convention and Compose's own accepted
`cpus` value shape — not coerced to a Python float/int, so a config author's
exact decimal representation reaches the compose overlay unchanged.

## Doc updates

`docs/SPEC.md` S13.2 gained a `provides_container` subsection (worked
example + validation rules) right after the existing resolution-rules list,
and the "may be keyed anything" sentence now cross-references it as the
escape hatch. S15.1's declaration block, S15.3's injection table (`cpus` row
added, "five keys" language corrected to "six" throughout that section), and
a new S15.21 section (mirrors S15.14's shape/length, includes the
`docker inspect --format '{{.HostConfig.NanoCpus}}'` verification method).
`docs/CONFIG.md` gained a `provides_container` worked example under
requires/provides and a `governance.cpus` paragraph + worked example under
`[<root>.governance]`, both linking to their SPEC sections (anchors
hand-verified against GitHub's slug algorithm, not guessed).

## Mutation verification (both parts) — actually run, not asserted

Every mutation below was applied by editing the real source file, running
the targeted tests, observing the predicted failures, then restoring the
fix and re-confirming green. Full detail + real command output in the
REPORT.

- CIU-89-M1: removed the `provides_container` override check from
  `_resolve_probe_container` → exactly the 3 override-dependent tests failed
  (the 2 basename-guess-unchanged regression guards stayed green).
- CIU-89-M2: disabled `validate_stack_provisioning`'s new `provides_container`
  block → exactly the 5 validation tests failed (in this file) + 1 in the
  dedicated CIU-89 test file.
- CIU-90-M1: disabled the `build_injections` cpus conditional → exactly the
  "configured injects the key" test failed.
- CIU-90-M2: disabled `resolve_config`'s cpus validation → exactly the
  `"0"`/`"-1"`/non-numeric raise tests failed (3 tests).

## Live verification (CIU-90) — real docker, real inspect

Ran `governance.build_injections()` for real against a synthetic service
with `governance.cpus = "1.5"` configured, fed the exact computed fragment
into a real `docker compose up`, and read back
`docker inspect --format '{{.HostConfig.NanoCpus}}'`: **1500000000**
(`1.5 * 1e9`). Same harness with no `cpus` key at all (the unset-default
case): **0**. Both containers torn down immediately after
(`docker compose down`), confirmed via a post-hoc `docker ps -a`/
`docker network ls` filter showing no residue. Full transcript in the
REPORT.

## Suite / gate

Full `ciu` suite green throughout (`nice ionice -c3 python3 -m pytest
tests/ -q`): 3582 passed at commit `66c990ba`, 3583 at `69c573a0` (one
coverage test added), both before AND after restoring every mutation.

**Real gate, run twice** (`./run-gate.py ciu --worktree <this worktree>`),
verdict read in a separate step each time, never off a piped tail:

- Run 1, at `66c990ba`: **FAIL**. Two independent findings: (a) R1
  (coverage) — a real, self-caused gap: the `except (ValueError,
  KeyError)` fallback in the new `provides_container` override path
  (`provisioning.py:462-463`) had no test exercising it; fixed by
  `69c573a0` (a new test, `test_resolve_probe_container_override_falls_back_to_literal_on_unresolvable_config`).
  (b) R0 — `test_ciu_render_selection_context.py::test_engine_threads_selection_into_configfiles_and_hooks`
  failed with a `shutil.Error` copying `test-repo/applications/app-config/ciu.toml`.
  Investigated (this package touches none of that test's files): a
  pre-existing cross-xdist-worker TOCTOU race between two OTHER test files
  that share `test-repo/`'s on-disk state — `test_ciu_test_repo.py` renders
  `ciu.toml` directly into the committed tree (and one of its own tests
  unlinks it as prep for a fresh render), while `test_ciu_render_selection_context.py`
  copies that same directory as a template, with no ordering guarantee
  between the two files under `--dist loadfile`. Did not reproduce on the
  very next gate run (code-identical aside from the one coverage-only test)
  — filed as **CIU-91** (backlog), not fixed here (out of this package's
  own disjoint-files scope; neither CIU-89 nor CIU-90's own sections named
  those test files).
- Run 2, at `69c573a0`: **PASS**. R0 PASS, R1 PASS, 100% coverage (80/80
  executable lines, 18/18 branches, both sides of every branch). Full
  verdict JSON in the REPORT.

---

## Commits

1. `66c990ba` — `feat(ciu): CIU-89 -- provides_container probe-container
   override + CIU-90 -- governance.cpus quota key (ciu-P49)` — all source,
   test, and doc changes for both parts, plus the two backlog rows and the
   CHANGES.md draft section.
2. `69c573a0` — `test(ciu): ciu-P49 -- cover _resolve_probe_container's
   unresolvable-config fallback for a provides_container override` — the
   R1 coverage-gap fix the first gate run caught.
3. `<this commit>` — `backlog(ciu): file CIU-91 -- test-suite flake found
   during ciu-P49's own gate run`.
4. `<this commit>` — `docs(ciu): ciu-P49 -- LOG/REPORT for CIU-89 +
   CIU-90`.
