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
3. `880a6efb` — `backlog(ciu): file CIU-91 -- test-suite flake found
   during ciu-P49's own gate run`.
4. `efcd7260` — `docs(ciu): ciu-P49 -- LOG/REPORT for CIU-89 + CIU-90`.
5. `13046641` — `docs(ciu): ciu-P49 -- fill in the real commit hashes in
   LOG/REPORT's commit lists`.

---

# ciu-P49 — LOG addendum: review-fix pass

Fresh adversarial reviewer returned **ACCEPT-conditional** against commit
`13046641`: real gate re-run clean in its own control worktree (R0/R1
PASS, 100% coverage, matched my own numbers), all 4 mutation tests
independently confirmed real (reviewer wrote its own mutations, watched
the right test fail each time), the live `docker inspect` numbers
reproduced exactly, the `action_graph` exclusion verified true and
correctly pinned, scope clean (13 files, all authorized), CIU-91's root
cause independently confirmed correct. One real blocker plus several
strongly-recommended items came back. All addressed here, same branch,
one commit: `57348c00`.

## Blocker — `TypeError` regression from `main`

Real bug, and a nasty one: `config_model.py`'s new `provides_container`
validation built `declared_provides = set(root_section.get("provides"))`
without checking that every ELEMENT of `provides` is itself hashable.
`provides = [["pg:db/x"]]` — a `provides` entry that is ITSELF a list —
passes `isinstance(val, list)` (the check that gates the whole
`requires`/`provides` field), but the individual entry `["pg:db/x"]` is
unhashable, so `set(...)` on it raised an uncaught `TypeError`. That
`TypeError` propagates all the way up through every `except ValueError`
handler in the call chain, including `engine._exit_code_for` (which maps
`ValueError` -> exit 2, S10.3, but has no `TypeError` case) — so a config
that `main` already reports cleanly, with a well-worded finding
(`'provides[0]' must be a string, got list`) and exit code 2, instead
crashes the CLI with an uncaught traceback and a DIFFERENT, silently wrong
exit code on this branch.

**Why I missed it.** The pre-existing requires/provides loop a few lines
above already validates each `provides` entry is a string one at a time,
and I read that loop before writing my own code — but I built
`declared_provides` by re-deriving straight from `root_section.get(
"provides")` (deliberately, to avoid depending on loop-local state), and
in doing that re-derivation I dropped the type check the loop already had,
reasoning "provides is a list, `set()` of a list of strings is fine" and
not testing the case where an ELEMENT of that list is itself unhashable.

**Fix.** Filter to string elements before building the set:
`{p for p in provides_list if isinstance(p, str)}`. Safe because a
non-string `provides` entry is already reported, once, by the pre-existing
loop, and a `provides_container` key can never legitimately equal a
non-string entry anyway (TOML table keys are always strings).

**Pinned by 2 new tests**:
`test_validate_stack_provisioning_provides_containing_a_nested_list_raises_valueerror_not_typeerror`
(the raise-type + message-survives assertion) and
`test_validate_stack_provisioning_provides_containing_a_nested_list_maps_to_exit_2_via_engine`
(drives the SAME config through the real `engine._exit_code_for`, not just
the raise type, closing the loop to the actual CLI-facing consequence).
Reverting the fix to the buggy `set(declared_provides)` form reproduces
the EXACT reported `TypeError` and fails both new tests; restored, both
pass.

## Strongly recommended #1 — `provides_container` kind gate

`_resolve_probe_container` (the only consumer of `provides_container`) is
reached exclusively from `_probe_pg`/`_probe_minio`. A `provides_container`
entry keyed to a `vault:`/`consul:`/`stack:` ref was accepted by
validation and then silently never consulted by anything — precisely the
"looking live while never being consulted" failure the undeclared-ref
check already exists to prevent, just for a different way of getting
there.

**Fix.** After the existing "key is in `provides`" and "value is a
non-empty string" checks, `validate_stack_provisioning` now also parses
the key with `provisioning.parse_ref` (function-local import — confirmed
`provisioning.py` never imports `config_model.py`, so no circular-import
risk, but kept local to match this file's own established convention for
a cross-module reach used by one function) and requires
`.kind in ("pg", "minio")`, raising a `[S13.2]`-tagged error naming the
offending kind otherwise. A key equal to an already-malformed `provides`
entry (fails `parse_ref` itself) is left to the pre-existing
malformed-ref violation rather than double-reported — pinned by
`test_validate_stack_provisioning_provides_container_key_that_is_itself_a_malformed_ref_does_not_double_report_or_crash`.

**Pinned by 5 new tests** (`accepts_minio_kind_override` plus
`rejects_{vault,consul,stack}_kind_override` plus the malformed-key
no-double-report/no-crash case above). Disabling the new kind check (`if
False and ref_kind not in (...)`) fails exactly the 3 rejection tests;
`accepts_minio_kind_override` stays green, confirming the mutation is
precise.

`docs/SPEC.md` S13.2's `provides_container` paragraph and
`docs/CONFIG.md`'s worked example both now state the restriction.

## Strongly recommended #2 — `docs/FEATURES.md` stale parallel copy

`docs/FEATURES.md`'s own "Your Postgres/MinIO service can be keyed
anything" bullet is a SEPARATE copy of the claim SPEC.md's S13.2 makes —
missed when SPEC.md was updated in the original package because I grepped
for the exact SPEC.md section, not for every doc file carrying the same
sentence. Added the identical qualifying clause + a pointer at
`provides_container` as the escape hatch.

## CIU-91 correction

The reviewer caught a real factual error in the CIU-91 row I filed: I'd
written "or simply run before that file has been generated at all in a
fresh run" as an alternative failure mechanism alongside the TOCTOU race.
That's wrong — `shutil.copytree`'s own `entries` list, captured once by
`os.scandir` at the START of the call, is what a later per-entry
`copy_function` iterates; a file that was never generated would simply be
ABSENT from that list, and the copy would silently proceed without it —
`shutil.copytree` cannot raise `[Errno 2] No such file or directory` for
an entry that was never in its own listing. My OWN captured traceback (in
the REPORT, from the first gate run) already contained the proof I hadn't
looked at closely enough: `entries = [..., <DirEntry 'ciu.toml'>, ...]` —
the file WAS in the scandir snapshot. The failure is strictly "present at
scandir time, gone by the time `copy_function` reaches it a few lines
later" — a vanish-mid-copy race, not an ordering/absence gap.

Consequence for the proposed fix directions: **(c) ("`_add_stack` renders
`ciu.toml` itself when absent") does not actually address the confirmed
mechanism** — the file is never "absent" from `_add_stack`'s own
perspective at the moment it would check; it disappears strictly during
the window `_add_stack` doesn't control (between its own `scandir` and its
own read). Only **(b)** (isolate/serialize the in-place-render tests so
`_clean_stack_artifacts`'s unlink can't race a concurrent read) addresses
the mechanism as actually confirmed. Corrected the CIU-91 row in
`KNOWN_ISSUES_TODO_BACKLOG.md`: struck the wrong alternative-mechanism
claim with an explanation grounded in the traceback's own `entries` list,
and annotated (b)/(c) accordingly, so whoever picks CIU-91 up next isn't
misled into implementing a fix that wouldn't fix anything.

## Should-fix — permanent `tomllib` round-trip regression test

Both CIU-89 oracles used hand-built in-memory dict fixtures rather than
the `test-repo/` fixture the handoff literally described — the reviewer
called this the right call (CIU-91 IS a `test-repo/`-sharing race;
widening this package's own footprint into that shared directory would
have made things worse, not better) but had to hand-verify, by reading
code, that `provides_container` actually survives a REAL
`tomllib`-parsed `ciu.toml` through the full production chain.

Added ONE permanent test,
`test_ciu89_real_toml_round_trip_through_validation_graph_and_resolution`
(`test_ciu_provisioning_ciu70_probe_container.py`, the existing dedicated
CIU-89 section — no new test infrastructure invented): parses an actual
TOML string with `tomllib.loads` (not `test-repo/`, not a
`tmp_path`-scoped file either — an in-memory string is sufficient here and
keeps the test dependency-free), then drives it through the REAL
`validate_stack_provisioning` -> `deploy.provisioning_graph` ->
`provisioning._resolve_probe_container` chain, unmocked. Confirms
`p-t-postgres` for the overridden ref and `p-t-db-core` for the
un-overridden sibling in the same `provides` list — the exact numbers the
reviewer reported hand-verifying.

## Explicitly not done (controller ruling)

Left alone, per the controller's explicit instruction: `cpus` string
validation looser than Docker Compose's own parser; `provides_container`
not gated by `ciu check`'s `if requires or provides:` guard when
`provides` itself is empty/absent; stale `INJECTED_KEYS`-adjacent
enumerations in `composefile.py`/`SPEC.md` S15.7/S15.8 that already
omitted `memswap_limit` before this package; `provides_container` staying
excluded from `ciu graph --fmt json` (kept as shipped, the controller's
own call). None filed as new backlog entries — noted here for the
controller's own tracking decision, per instruction.

## Re-verification

`pytest tests/ --cov=ciu --cov-branch`: **3591 passed**, 100% coverage
(all local, before the gate run). Real gate
(`./run-gate.py ciu --worktree <this worktree>`) at `57348c00`: **PASS**
— R0 PASS, R1 PASS, 100% changed-line+branch coverage (94/94 executable
lines, 20/20 branches). Verdict read in a separate step from the run, off
`.assay/verdict-ciu.json`, never a piped tail. No stray Docker state
(no live containers were started in this fix pass — the blocker/kind-gate
fixes are pure validation logic, no live verification needed beyond the
existing CIU-90 one from the original package).
