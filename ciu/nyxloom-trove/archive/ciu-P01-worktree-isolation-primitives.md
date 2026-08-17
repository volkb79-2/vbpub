---
schema_version: 1
id: ciu-P01-worktree-isolation-primitives
project: ciu
title: "Worktree instances become usable for real (non-mock) test lanes"
tier: implement-2
input_revision: "1a891facc6936419b67f2876c1eafb6eeb0862d4"
source: {kind: user, ref: "KNOWN_ISSUES_TODO_BACKLOG.md CIU-20, CIU-21, CIU-23"}
stack: none
depends_on: []
session: fresh
scope:
  touch:
    - "src/ciu/deploy.py"
    - "src/ciu/cli.py"
    - "src/ciu/engine.py"
    - "src/ciu/composefile.py"
    - "src/ciu/worktree.py"
    - "docs/SPEC.md"
    - "docs/DESIGN-NOTES.md"
    - "KNOWN_ISSUES_TODO_BACKLOG.md"
    - "tests/tests/test_ciu_provenance_json.py"
    - "tests/tests/test_ciu_provenance_env_injection.py"
    - "tests/tests/test_ciu_worktree.py"
    - "tests/tests/test_ciu_cli_worktree.py"
    - "nyxloom-trove/reports/ciu-P01-worktree-isolation-primitives-LOG.md"
  forbid:
    - "nyxloom-trove/carve-assets/ciu-P01-worktree-isolation-primitives/provenance-verified-match.json"
    - "nyxloom-trove/carve-assets/ciu-P01-worktree-isolation-primitives/provenance-mismatch.json"
    - "nyxloom-trove/carve-assets/ciu-P01-worktree-isolation-primitives/provenance-not-verified-dirty.json"
    - "nyxloom-trove/carve-assets/ciu-P01-worktree-isolation-primitives/provenance-not-verified-unknown.json"
    - "nyxloom-trove/carve-assets/ciu-P01-worktree-isolation-primitives/provenance-not-verified-no-evidence.json"
    - "nyxloom-trove/carve-assets/ciu-P01-worktree-isolation-primitives/provenance-not-verified-no-evidence-unlabelled.json"
    - "nyxloom-trove/carve-assets/ciu-P01-worktree-isolation-primitives/provenance-refused-no-identity.json"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/roadmap.md"
    - "nyxloom-trove/backlog.md"
    - "../assay"
    - "../srdm"
    - "../dstdns"
    - "../nyxloom"
oracles:
  - id: O1
    observable: "CIU-20. `verify_running_provenance` (deploy.py) is refactored to ALWAYS build and return a result object (never bare `None`, never raising internally) with fields, IN THIS ORDER: `schema_version` (constant `1`), `instance`, `commit_under_test`, `tree_state`, `containers`, `overall` — a real, intentional signature change, not 'unchanged'. `containers` is `list[{name, image, labelled_revision, status}]` when — and ONLY when — enumeration ran (`docker ps` succeeded), and JSON `null` in every case where no container-level verdict was formed (identity refused, dirty tree, non-checkout, or enumeration could not run). `_provenance` (cli.py:376) is the ONLY place that decides prose/raise/warn behaviour from that result and is BYTE-IDENTICAL in that behaviour when `--json` is absent. `ciu provenance --json` (store_true, matching `ciu diagnose --json`'s shape at cli.py:726 EXACTLY — NOT `[PATH|-]`) prints ONLY the JSON document to stdout, no prose mixed in. Grammar, fixed: `labelled_revision` is JSON `null` when unknown, NEVER `\"\"`; `commit_under_test` is `get_git_hash()`'s return value VERBATIM (the `-dirty` suffix, if any, lives ONLY here); `tree_state` is DERIVED from that same string (`.endswith(\"-dirty\")`->`dirty`, `==\"dev\"`->`not-a-checkout`, else `clean`) — never set independently, so the two fields cannot contradict; `containers[]` sorted by `name` ascending; each `status` is one of `match`/`mismatch`/`unlabelled`. `overall` is one of SIX closed values, decided IN THIS ORDER: (1) identity (`project`/`env_tag`) unresolved -> `refused-no-identity` (instance/commit_under_test/tree_state/containers ALL null), emitted by `_provenance` BEFORE `verify_running_provenance` is called at all; (2) `commit_under_test==\"dev\"` -> `not-verified-unknown` (containers null); (3) `commit_under_test.endswith(\"-dirty\")` -> `not-verified-dirty` (containers null); (4) enumeration could NOT run (`docker ps` raised FileNotFoundError/OSError or returned non-zero — the case `_running_containers` today silently swallows to `[]`) -> `not-verified-no-evidence` (containers null); (5) enumeration ran, >=1 container `status==\"mismatch\"` -> `mismatch` (containers is the sorted list); (6) enumeration ran, >=1 `status==\"match\"` and ZERO `mismatch` -> `verified-match` (containers is the list); (7) enumeration ran but produced NEITHER a match NOR a mismatch (empty, or all `unlabelled`) -> `not-verified-no-evidence` (containers the possibly-empty list). `verified-match` REQUIRES >=1 `match` — a green verdict is NEVER emitted from zero checked containers. LOAD-BEARING CODE CHANGE: `_running_containers` (deploy.py:638) today returns `[]` on BOTH FileNotFoundError/OSError/non-zero-rc AND a genuine empty enumeration, which is exactly what lets a docker-less host emit `verified-match`+`containers:[]`; it MUST be refactored to signal enumeration-failure distinctly from empty (e.g. return `None`/raise vs `[]`), so rule 4 (`containers:null`) and rule 7 (`containers:[]`) stay distinguishable. The SEVEN committed fixtures in `nyxloom-trove/carve-assets/ciu-P01-worktree-isolation-primitives/` (one per `overall` value PLUS the all-`unlabelled` rule-7 shape; carver-frozen, in `forbid`, SHA-256 recorded in this file's fixture manifest) are the grammar. A test asserts each by loading BOTH the fixture and the tool's emitted document with `json.load` and comparing the PARSED objects for equality (after normalising `instance`/`commit_under_test`/image names to the fixture's own values) — NOT byte-for-byte, so serialiser whitespace is never part of the contract. REQUIRED rule-7 discriminator: a successful enumeration containing only `unlabelled` containers (for example `postgres:16`) emits `not-verified-no-evidence` with the non-null list from `provenance-not-verified-no-evidence-unlabelled.json`; it must be tested explicitly, separately from the docker-unavailable rule-4 fixture (fake raises FileNotFoundError -> `containers:null`). DOCKER SEAM (the gate has NO docker socket — verified: `grep -c docker.sock nyxloom-trove/nyxloom.toml` -> 0): the enumeration path goes through `procutil.docker`; tests monkeypatch it exactly as the existing suite does — `monkeypatch.setattr(deploy.procutil, \"docker\", fake)` (precedent: tests/tests/test_ciu_deploy_actions.py:1368) — to drive all seven grammar shapes, including both `not-verified-no-evidence` discriminators."
    negative: "Emitting a document ONLY on the clean-success path (CIU-20's own defect re-created inverted), mixing prose onto `--json`'s stream, `labelled_revision:\"\"` instead of `null`, `tree_state` set independently of `commit_under_test` (able to disagree), `containers[]` in non-deterministic (docker-returned) order, MAPPING THE DOCKER-UNAVAILABLE / NON-ENUMERATED PATH TO `verified-match` WITH `containers:[]` (the round-2 false green — a green provenance document on a host with no docker), or emitting `verified-match` from zero `match`-status containers, each fail this oracle. The docker-unavailable fixture is the discriminator: a test that never drives `procutil.docker` raising cannot pass it."
    gate: tester-unified
  - id: O2
    observable: "CIU-21. A `Mapping[str, str]` of service-name -> image-revision (omitting a service with no baked label, or with `build:` and no baked `image:` yet) is built ONCE per render/up pass in `engine.py` (already docker-aware) immediately before the existing Step-15 `generate_overlay` call, by calling `deploy._image_revision_label` (deploy.py:666) per service — NEVER reimplementing its label-reading logic, and NEVER `engine.get_git_hash()` (a different claim: the host tree's current view, not the running image's baked truth). The map is passed as DATA through a NEW keyword parameter `image_revisions` on `generate_overlay` (composefile.py:707) to the injection site. `composefile.py` IS in scope for this — the round-2 `forbid` was WRONG and made this oracle unimplementable (see Round 3): the map must ENTER through `generate_overlay`'s signature, and the injection must happen OUTSIDE `composefile.py:777`'s `if gov_cfg[\"enabled\"]:` gate; BOTH edits live in composefile.py. The single binding constraint on composefile.py is that it stays DOCKER-FREE: NO `docker`, `procutil`, or `subprocess` import may be ADDED to it (verified today: `grep -c` for all three -> 0). The docker lookup that BUILDS the map lives in engine.py; composefile.py only receives the finished map as data and appends from it. Its early-return guard at composefile.py:858 MUST be exactly `if not materialized and not configfile_mounts and not governance_injections and not image_revisions: return None`, so a non-empty real provenance map writes an overlay even when every prior reason to write one is absent. This has a real, intentional blast radius: such stacks previously had no overlay, but will now have one containing the per-service revision. `engine.py:745`'s `reset_service` observes `overlay_path.exists()` and then adds `-f .ciu/ciu.compose.overlay.yml` to `down_cmd`; therefore this branch WILL newly run for those stacks, making `docker compose down` use the same rendered overlay that `up` used. `CIU_IMAGE_REVISION=<value>` is APPENDED (never assigned — `svc[\"environment\"] = [...]` at the composefile.py:930-939 service-fragment merge would silently drop the KSM `LD_PRELOAD` entry, S15.11 / CIU-14's exact failure class one call site over; composefile.py:936 already treats `environment` as an append-never-clobber MERGE key) to each service's environment fragment, using THAT SERVICE'S OWN revision from the map. Injection is UNCONDITIONAL: it happens regardless of `governance.enabled` (`composefile.py:777`'s gate governs RESOURCE governance, not provenance, and injection must run even when that gate is false), and `governance.exempt_services` does NOT exempt a service from it. The value must be independently confirmed READABLE for the owning service; the gate-verifiable proxy is reading `CIU_IMAGE_REVISION` back out of the rendered compose overlay's `environment` list for the SPECIFIC service whose image it names. DOCKER SEAM: the overlay-injection half is tested by passing a hand-built map DIRECTLY to `generate_overlay` (no docker — the map is data); the map-BUILDING half (engine.py) is tested by monkeypatching the label lookup (`deploy._image_revision_label`, or `procutil.docker` beneath it — the same seam as O1), since the gate has no docker."
    negative: "Injection suppressed when governance is disabled or a service is in `exempt_services`, a value sourced from `get_git_hash()`, an assignment to `svc[\"environment\"]` at the composefile.py:930-939 service-fragment merge that drops the KSM entry, an injected placeholder for a service with no baked label (must be OMITTED, not empty), a test that only inspects the overlay fragment in the abstract without confirming it belongs to the SPECIFIC service whose image it names, OR routing the map into the existing `governance` mapping so it lands inside the `if gov_cfg[\"enabled\"]` gate (the round-2 smuggle attack — passes every governance-ENABLED fixture while silently violating the unconditional ruling), each fail this oracle. REQUIRED discriminator: a fixture with `governance.enabled = false` AND two services with DIFFERENT baked labels, asserting two injected variables with distinct per-service values — a governance-enabled-only test cannot catch the smuggle."
    gate: tester-unified
  - id: O4
    observable: "CIU-23. `worktree add --data-isolation <db-profile>` provisions a database/schema namespaced by the new instance's own `INSTANCE_ID` via an INJECTABLE provisioner interface you define (a `Protocol`/callable your implementation calls, with the real Postgres-backed implementation as the shipped default) — this makes the naming/ordering/force-semantics mechanism independently testable in-gate WITHOUT a live Postgres server, which `tester-unified:local` cannot supply. The injectable provisioner IS the test seam (name it explicitly in the LOG); tests exercise naming/ordering/force against a FAKE provisioner. A real-server proof is deliberately OUT OF SCOPE for this package's gate and is filed as CIU-26 (a follow-up integration verification) so the deferral has an owner rather than being remembered. `remove()` (worktree.py:242) gains a new step BEFORE `_clean_in` that drops the namespaced entity, and that drop MUST be IDEMPOTENT: dropping an already-absent entity is a no-op success. A FAILED drop aborts removal unless `force=True` — EXTENDING `_clean_in`'s abort-unless-force contract to a second precondition. ROUND-3 CORRECTION: `remove()`'s existing `force` path proceeds SILENTLY (no warning — verified); O4 does NOT 'mirror it exactly', it IMPROVES on it — when `force=True` masks a FAILED drop, O4 MUST emit a warning that explicitly names the entity that was NOT dropped and states it is now the operator's problem (`_clean_in`'s own silent force path may be left as-is; that is not this oracle's subject). TERMINAL-STATE / RETRY CONTRACT: after (drop SUCCEEDS -> `_clean_in` FAILS -> removal aborts), the database is gone while the checkout, its `ciu.env`, and its now-dead DSN remain; `worktree rm <name>` retried in that state MUST succeed idempotently — the drop re-runs as a no-op (entity already absent), then `_clean_in` is retried. The connection identity is written to the new worktree's own `ciu.env`; `docs/SPEC.md` and the LOG must BOTH state plainly that this value MAY be credential-bearing and must never be recommended as an `env_passthrough` candidate for a consumer's own assay lane (a passthrough value lands in every verdict artifact in cleartext, established last session)."
    negative: "A collision test using two worktrees WITHIN one repo (untestable — `add` already refuses a duplicate name); the real attack is two CLONES of the repo choosing the same worktree name, which the test must reproduce (different physical paths -> different `INSTANCE_ID` -> a correct implementation has no collision, a name-based one does). A DB drop happening AFTER `_clean_in` rather than before, `--force` silently succeeding with NO warning when the drop failed, a drop step that is NOT idempotent (retry re-fails on the already-dropped entity), or the 'real Postgres-backed default' shipped as a thin UNTESTED class while all naming/ordering/force behaviour is exercised only against the fake, each fail this oracle. REQUIRED case: drop SUCCEEDS x `_clean_in` FAILS x `force=False` -> asserts the abort AND pins the retry contract above."
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "A correct O2 implementation cannot source the per-service revision without ADDING a `docker`/`procutil`/`subprocess` import INSIDE `composefile.py` — the map is meant to be built in `engine.py` and passed through `generate_overlay`'s signature as data; if that data path genuinely cannot carry it, say so rather than breaking composefile.py's docker-free invariant."
  - "O4's injectable-provisioner seam cannot discriminate a correct implementation from a name-collision-prone one WITHOUT also faking away the exact behaviour (naming collisions, drop failures) the oracle exists to prove — the real-server proof is deferred to CIU-26, but if even the in-gate SEAM cannot tell the two apart, escalate rather than shipping a hollow test."
  - "O1's `not-verified-no-evidence` / `containers:null` grammar cannot be produced because `_running_containers` cannot be made to signal enumeration-failure distinctly from an empty enumeration without a change that ripples beyond `deploy.py`'s own scope — surface the real blast radius rather than collapsing the two back into `[]`."
mutexes: [merge-lane]
---

# ciu-P01-worktree-isolation-primitives — worktree instances become usable for real test lanes

## Round 3 — what changed from the second carve, and why (the split)

The round-2 handoff was reviewed a SECOND time by C-sol-1 and returned **NOT
READY** again — this time on deeper problems, some of them *caused by* how
round 2 fixed round 1. Every one of its ten blocking findings was independently
re-verified against the real `src/` tree this session and **held**. Rather than
patch a five-item package that does not converge, this round **splits it**:

**Shipped now (this handoff): O1 (CIU-20), O2 (CIU-21), O4 (CIU-23).** These
three are naming/fixture/prose fixes away from ready, and the review's own
summary agrees. What round 3 changed in them:

- **`composefile.py` is back in `touch`** (round 2 wrongly moved it to
  `forbid`). Verified: `generate_overlay` (composefile.py:707) has no seam for
  O2's map, and the injection is gated by `if gov_cfg["enabled"]:`
  (composefile.py:777). BOTH the new parameter and the unconditional-injection
  path have to land in that file, so forbidding it made O2 literally
  unimplementable. The constraint is now stated correctly: composefile.py stays
  **docker-free** (no `docker`/`procutil`/`subprocess` import may be added), but
  it is editable; the docker *lookup* still lives in `engine.py`.
- **The `escalate_if` entry that misquoted the round-1 finding is deleted.**
  Round 1 said "add `engine.py` to scope", not "forbid `composefile.py`". The
  replacement trigger is the correct mechanical one (escalate only if the map
  genuinely cannot reach `generate_overlay` as data without a docker import in
  composefile.py).
- **O1 gained a sixth `overall` value, `not-verified-no-evidence`, and a
  `containers: null` rule.** Verified false green: `_running_containers`
  (deploy.py:638) returns `[]` on `FileNotFoundError`/`OSError`/non-zero
  `docker ps` **and** on a genuinely empty enumeration, so a host with no docker
  produced `overall: verified-match, containers: []` — a green provenance
  document attesting nothing. `null` now means "did not / could not enumerate";
  `[]` means "enumerated, found none"; and `verified-match` requires ≥1 checked
  `match`. The three previously-`[]` fixtures (dirty/unknown/refused) are now
  `containers: null` for a uniform grammar (they never enumerate).
- **The fixtures are regenerated canonically and the comparison is now
  parse-based.** Round 2's `provenance-mismatch.json` and
  `provenance-verified-match.json` were hand-wrapped two-keys-per-line and no
  serialiser reproduced them, so "byte-comparable" was impossible for two of
  five (measured). All seven are exact `json.dumps(indent=2)` output, and O1's
  instruction is **"compare PARSED documents"**, not byte-for-byte — whitespace
  is out of the contract. The sixth fixture covers the docker-unavailable case;
  the seventh pins successful all-unlabelled enumeration distinctly from it.
- **The frozen fixtures moved from a writable `touch` glob to `forbid`, with
  SHA-256 recorded** (see the manifest below). "Frozen grammar" plus a writable
  path was a contradiction — an implementer whose output disagreed could edit
  the fixture instead of the bug. Tests still READ them (reading a forbidden
  file is allowed); the implementer cannot MODIFY them.
- **O2 now names the docker seam** (the overlay-injection half needs none; the
  map-building half monkeypatches `deploy._image_revision_label` /
  `procutil.docker`) and **requires a governance-DISABLED, two-distinct-label
  fixture** — the only shape that catches the "smuggle the map through the
  `governance` dict" false-PASS, which every governance-enabled test misses.
- **O4's `--force` claim is corrected** (round 2 said it "mirrors `_clean_in`
  exactly"; verified that `remove()`'s force path is **silent** — O4 *improves*
  on it by warning) and **gains a retry/idempotency contract** for the
  drop-succeeds-then-clean-fails terminal state round 2 left undefined.

**Deferred (split out — real design work first): O3 (CIU-22, shared-infra
join) and O5 (CIU-24, concurrency budget).** These are NOT naming fixes; they
carry unresolved architecture and are refutable-in-advance as written:

- **O3** named a mechanism that is **inert**: ciu writes **no `networks:` key
  anywhere** in `src/ciu/` (verified — zero matches), so a "list of extra
  networks in `ciu.env`" has no consumer, and `add()` "cannot produce
  connectivity because it does not deploy" (its own docstring: *"Deploy is
  deliberately NOT performed"*). The real mechanism the review names is
  imperative `docker network connect` at **`ciu up`** time (precedent:
  `_connect_devcontainer_to_network`, workspace_env.py:608) — connectivity
  spans **two verbs**, and the design (which tier joins, idempotency, failure
  handling) is genuinely open.
- **O5**'s config key `governance.max_concurrent_worktrees` is a **per-stack**
  `[<root>.governance]` value, but `add()` (worktree.py:172) **loads no stack
  config at all**, and a multi-stack repo has several such tables with no single
  one to read — the key's location (repo-level vs. stack-resolved) and its
  interaction with CIU-13's global/per-stack merge are undecided.

Their review-established findings are captured in `nyxloom-trove/backlog.md`
(un-carved ideas) so nothing is lost; CIU-22 and CIU-24 stay OPEN. The operator
directive for this pipeline was explicit — *don't let it escalate to six rounds
again* — and a tighter package that converges beats a five-item one that does
not. The three shipped oracles keep their round-2 ids (**O1/O2/O4**, with O3/O5
absent) so the round-2 review's references stay valid; the gap is deliberate.

## Round 2 — what it fixed (history, for continuity)

Round 2 answered C-sol-1's first NOT-READY: corrected an id that failed
`nyxloom`'s frontmatter schema, fixed guessed test paths to real
`tests/tests/…` files, added `engine.py` to scope, introduced the frozen JSON
fixtures, named O1's signature seam, ruled O2's governance gating unconditional,
rewrote O3 around connectivity, added O4's injectable-provisioner seam, and
named O5's config key. Round 3 keeps every one of those that survived for the
three shipped oracles; it undoes only the two changes that regressed (the
`composefile.py` forbid and the misquoting `escalate_if`) and finishes the
grammar/fixture work round 2 started.

## Why this package exists, and why it's three items in one

`ciu worktree` (S16) already gives every worktree its own container/network/
volume isolation, automatically, from the physical checkout path — nothing about
that is broken. What's missing is what makes it *practical* for real (non-mock)
integration/schema/E2E test lanes run concurrently across packages, where
per-package schema genuinely diverges and a shared stack is architecturally
wrong. Three primitives close that for the common case:

- **CIU-20 (O1)** — a machine-readable provenance verdict, so a downstream
  evidence consumer records *what was checked and what it found*, not merely
  "no refusal happened".
- **CIU-21 (O2)** — the image's baked revision made readable from *inside* the
  container, so an in-container test runner can verify its own provenance
  without an outside co-process.
- **CIU-23 (O4)** — lightweight namespaced-database isolation for the common
  "schema diverges, nothing else does" lane, instead of N full Postgres
  containers.

Two consumers hit these from different directions the same day (assay's
cross-project review and dstdns's own real-lane reconciliation program).
CIU-22, CIU-24 (deferred, above) and CIU-25 (leak detector, always excluded)
are the remaining backlog items.

## What's already real (read before touching anything)

- `src/ciu/deploy.py` — `verify_running_provenance` (S17.2, deploy.py:556, the
  function O1 refactors), `_running_containers` (deploy.py:638, the swallow-to-
  `[]` that O1 must make failure-distinguishable), `_image_revision_label`
  (deploy.py:666 — O2 must CALL this exact function per-service, never
  reimplement its label-reading).
- `src/ciu/cli.py` — `_provenance` (cli.py:376, O1's CLI wiring — the ONLY place
  behaviour forks on `--json`; the `refused-no-identity` bail at cli.py:406-413
  is where O1 emits that verdict, before `verify_running_provenance` is called),
  `_worktree` (cli.py:434, O4's CLI wiring site), and `ciu diagnose --json`
  (`p.add_argument("--json", …, action="store_true")`, cli.py:726) as O1's
  PRECEDENT TO MATCH EXACTLY (`store_true`, not `[PATH|-]`).
- `src/ciu/engine.py` — Step 15 (~line 1368), immediately before the existing
  `generate_overlay` call. `materialized`, `configfile_mounts`, `governance` are
  already resolved here and passed in as data; O2's service->revision map is the
  same shape and belongs here (engine is docker-aware).
- `src/ciu/composefile.py` — `generate_overlay` (composefile.py:707), whose
  signature O2 EXTENDS with the map parameter, and the governance gate at
  composefile.py:777 that O2's injection must live OUTSIDE of. `environment` is
  an append-never-clobber merge key (composefile.py:936). This file has ZERO
  `docker`/`procutil`/`subprocess` imports today and MUST stay that way.
- `src/ciu/worktree.py` — the S16 module. Read the docstring: the `rm`-order
  invariant (clean before remove) is normative; O4 extends it to
  drop-before-clean-before-remove. **This file has ZERO test coverage today**
  (no `tests/tests/test_ciu_worktree*.py` exists); it is this package's
  first-ever test module for it. The coverage gate is changed-line-scoped, so
  pre-existing untested lines you don't touch are not your problem, but any line
  you DO touch in `add`/`remove` needs real coverage.
- `src/ciu/governance.py` — READ-ONLY PRECEDENT for O2 (NOT in scope): the KSM
  env-injection at `frag["environment"] = [f"LD_PRELOAD=…"]` is the
  append-vs-assign collision O2 must avoid; it is reached only inside the
  governance gate, which is exactly why O2's UNCONDITIONAL injection cannot be
  nested there.
- `docs/DESIGN-NOTES.md` D7 and `docs/SPEC.md` S16/S17 — read both before
  writing the new S17.3 (machine-readable verdict), S17.4 (in-container
  revision) and S16.2 (namespaced data isolation) sections this package adds.
- `dstdns/scripts/schema-gate.sh` (a SIBLING repo, read-only reference, NOT in
  scope) — the hand-rolled throwaway-database pattern O4 makes first-class.

## Fixture manifest (O1's frozen grammar — carver-owned, in `forbid`)

Seven documents across the six `overall` values: one per value, plus the
all-unlabelled successful-enumeration form of `not-verified-no-evidence`. They
are exact `json.dumps(indent=2)` output plus a trailing newline. O1's test
compares **parsed** documents, never bytes; these SHA-256 values exist to detect
an implementer editing a fixture in place instead of fixing the tool (the
round-2 "frozen but writable" contradiction):

| fixture | overall | containers | sha256 |
|---|---|---|---|
| provenance-verified-match.json | verified-match | list (≥1 match) | `b3243683e2942301151923da5680152318c88a04c17873f10b74935f85d98761` |
| provenance-mismatch.json | mismatch | list (has mismatch) | `39e7383d161ed9a50864ea984d478b8fa409960d8e83b125d7c58e54d54a1a3e` |
| provenance-not-verified-dirty.json | not-verified-dirty | null | `ef98a6109399de3ba820a2c5bb44b9a99a2152ad3d34c5e1477ed1928d5e4320` |
| provenance-not-verified-unknown.json | not-verified-unknown | null | `d41bb52027d05a84b4b2ea9b8ee6c51740ad83e1831c877e3e9303d7dae9210c` |
| provenance-not-verified-no-evidence.json | not-verified-no-evidence | null | `180351a7128113e7a5e6f952d73d6961f313937eb390f91460ae302638e5835f` |
| provenance-not-verified-no-evidence-unlabelled.json | not-verified-no-evidence | list (all unlabelled) | `699f414e0c2fe1b40e632186063f95510ab925baa82d7527d85e80c460c52b40` |
| provenance-refused-no-identity.json | refused-no-identity | null | `73407c5d34eff7a39831c18ebe24e3f853bd526a350e6b2bd6ff89b855329f1d` |

## Dispatch contract

- Contract class: **2b/2d mixed** — O1/O2 are constrained implementation against
  a fully-specified target (2b: all data grammar and semantics ruled); O4
  retains real, named design freedom within a falsifiable observable (2d — the
  provisioner-interface shape and the data-isolation config-table shape are
  yours). Say in the LOG which class each oracle actually turned out to be.
- Required roles: **Sonnet xhigh implementer -> fresh Opus xhigh independent
  reviewer.**
- Readiness: READY only after this handoff passes adversarial carve review.
- Baseline: measure the real gate at `input_revision` and paste the actual
  output as the LOG's first entry, before any code change — a stated pass/fail
  count with no pasted artifact is not evidence.

## Worktree and branch

`.worktrees/ciu-P01-worktree-isolation-primitives`, branch
`ciu-P01-worktree-isolation-primitives`, created from `ciu`'s current `main` at
the pinned `input_revision`. `nyxloom.toml`'s `worktree_root = "../.worktrees"`
already points at the shared `.worktrees/` directory at the monorepo root.

## Gate

`[gates.tester-unified]` in `nyxloom-trove/nyxloom.toml` — real, already wired,
runs `run-ciu-tests.py` inside `tester-unified:local` plus
`nyxloom.coverage_gate` against `src/ciu`. **It mounts no docker socket**
(verified: `grep -c docker.sock nyxloom-trove/nyxloom.toml` -> 0), so every
oracle that depends on a docker fact (O1's enumeration, O2's label lookup) MUST
be driven through the `procutil.docker` monkeypatch seam named in its observable
— there is no real docker inside the gate.

## What must stay out, and why

The strategic docs (`decisions.md`, `roadmap.md`, `backlog.md`) are out of scope
for the IMPLEMENTER — this package documents new capability (new S-numbered
subsections in `docs/SPEC.md`), it does not re-plan the roadmap. The seven
provenance fixtures are `forbid` (carver-frozen; read them, don't edit them).
The sibling repos (`assay`, `srdm`, `dstdns`, `nyxloom`) are out of scope
entirely: this package ships only the ciu-side primitive; a consumer adopting it
is separate, unstarted work in that consumer's own repo.

## On landing

Mark **CIU-20, CIU-21, CIU-23** `FIXED` in `KNOWN_ISSUES_TODO_BACKLOG.md`'s
status board, each row pointing at the real SPEC id and evidence (code + tests +
spec + docs land together, per that file's house rule). **File CIU-26** (O4's
deferred real-Postgres integration proof) as a new `OPEN` entry so the deferral
has an owner. Leave **CIU-22 and CIU-24** OPEN — split out of this package
pending design (findings in `nyxloom-trove/backlog.md`). Leave CIU-25 alone.
