---
kind: backlog
schema_version: 1
items:
- id: B1
  title: route doctor verb (validate routes.toml + live-test each route)
  type: feature
  component: routing
  context_estimate: small
  folds_into: F009
- id: B2
  title: 'availability layer: disable a CLI/provider/model without removing config'
  type: feature
  component: routing
  context_estimate: medium
  folds_into: F009
- id: B3
  title: per-project route policy (no-china / no-openrouter / no-model-X)
  type: feature
  component: routing
  context_estimate: small
  folds_into: F009
- id: B4
  title: reviewer on-the-fly fixes (configurable, serial-favored)
  type: feature
  component: review
  context_estimate: medium
  folds_into: F005
- id: B5
  title: component-in-slug ID scheme + STANDARD update
  type: feature
  component: spine
  context_estimate: small
  folds_into: F001
- id: B6
  title: implementer self-review text (gated to IMPLEMENTER role)
  type: feature
  component: dispatch
  context_estimate: small
  folds_into: F005
- id: B8
  title: smart reject-triage — needs-human branch (tech-fixable half shipped via
    P45's exhausted-budget->READY_TO_CARVE re-carve route; the needs-human->D-NNN
    escalation branch is still unbuilt, reconcile.py item 10 has no such path)
  type: feature
  component: review
  context_estimate: medium
  folds_into: F005
- id: B9
  title: intake-over-ntfy chatbot (human-initiated new direction)
  type: feature
  component: control
  context_estimate: large
  folds_into: F012
- id: B10
  title: session-limit monitoring + per-job token estimation
  type: feature
  component: routing
  context_estimate: large
  folds_into: F009
- id: B11
  title: sweep stale daemon worktrees/branches (merge-status-checked)
  type: bugfix
  component: ops
  context_estimate: small
- id: B13
  title: runaway watchdog conflates a persistent-but-acknowledged condition with
    actively-worsening thrash -- review_rejections_by_area>=2 stays true for the
    FULL 7-day HISTORY_REJECTION_WINDOW_SECONDS regardless of an operator having
    already resumed once, so the SAME reconcile-thrash streak re-trips an
    auto-pause every ~13 reconcile passes (minutes, once unblocked) until the
    triggering rejections finally age out; needs the thrash-streak to reset (or
    not count) once an operator has resumed for this specific condition, not
    just dedupe the notification
  type: bugfix
  component: watchdog
  context_estimate: medium
  folds_into: F006
- id: B12
  title: carve-ahead drift/staleness guard -- input_revision is stamped by the
    carver but never re-validated against current main before an implementer
    attempt starts; raising carve_ahead_target increases exposure with no
    safety net (a CARVED task's premises can go stale while it waits)
  type: feature
  component: dispatch
  context_estimate: medium
  folds_into: F008
- id: B-self-review-leg
  title: wire the independent SELF_REVIEW dispatched leg (beyond the prompt-level
    implementer self-review)
  type: feature
  component: dispatch
  context_estimate: medium
  folds_into: F005
- id: B14
  title: 'onboarding must be interview-driven + content-preserving at ANY project
    maturity: never one-shot derive-from-code a canonical spine. Required design:
    an extensive user-in-the-loop interview PLUS a migration that absorbs existing
    curated docs (roadmap/backlog/product-definition) into the spine schema and
    retires the source docs afterward. Supersedes the F4b --questionnaire code-regen
    as the default (operator directive 2026-07-23; proven need by the dstdns/topos
    content-preserving migrations, which the code-regen path would have thinned).'
  type: feature
  component: onboarding
  context_estimate: large
  folds_into: F002
- id: B15
  title: 'free-models refresh follow-ups: (a) validate Tier-2 provider route
    addressing (groq/<model>, cerebras/<model>, ...) with a live probe before real
    traffic (folds into B1 route doctor); (b) honor an operator exclude-list so a
    refresh does not re-include manually vetted-out free models.'
  type: feature
  component: routing
  context_estimate: small
  folds_into: F009
- id: B20
  title: 'scheduled-jobs subsystem: daemon-owned cron with capability-catalog refresh as
    first consumer. Config-driven jobs are read-only in the UI (config is source of truth);
    user-driven jobs added via UI are editable there; neither origin overwrites the other.
    Every job stays invocable ad-hoc. Generalizes B10s session-limit monitoring cadence.'
  type: feature
  component: control
  context_estimate: medium
  folds_into: F015
- id: B23
  title: 'per-task permission fields: make the OS sandbox mode (read-only / workspace-write
    / full-access) AND the scope breadth declared handoff fields, capability-matched to the
    task, rather than a global route/CLI default. Narrow + read-mostly for mechanical edits,
    broad + full-access for cross-cutting refactors (D-R16 Axis A/B unification).'
  type: feature
  component: runtime
  context_estimate: medium
  folds_into: F010
- id: B25
  title: 'de-flake + re-land test_transient_throttle_resumes_same_attempt_end_to_end
    (B24/D-R17 O4), currently xfail(strict=False). It is the only behavioral test that
    drives a REAL wrapper double-fork (wrapper.launch_detached uses os.fork()) through a
    transient-classified leg, and os.fork() under load on Python 3.14 is fragile in the
    tester-unified image: it passed its solo branch gate then failed the certify on the
    same tree (~50% under full-suite load), while passing ~12/12 in the devcontainer.
    Re-land it driving the transient leg deterministically (without a real fork), then
    remove the xfail. NOT a feature defect -- the D-R17 contract is covered by
    deterministic oracles in test_wrapper/test_reconcile/test_daemon. Also consider
    skipping wrapper.SESSION_CAPTURE_DELAY entirely when a route declares no
    session_capture/session_discover (capture can never succeed, so the 5s block is
    always wasted) -- a real product speedup, not just a test fix.'
  type: bugfix
  component: testing
  context_estimate: small
- id: B26
  title: 'per-handoff processing-trace artifact: capture each dispatched agent
    summary + insights across legs (implementation, review, gate, merge) as a
    structured, drill-down-able record surfaced in the dashboard, so a human can
    trace how a handoff was processed leg-by-leg (agent A implemented with these
    insights; agent B reviewed and found these; gate result; merge). Generalizes
    the ad-hoc controller session log into a first-class per-task trace view
    (operator ask 2026-07-24). Relates to the Logging-P04 log-stream UI and the
    LOG/REPORT handoff artifacts.'
  type: feature
  component: control
  context_estimate: medium
- id: B27
  title: 'Scale SEAL (labs.scale.com) benchmark source: ingest SWE-Bench Pro
    (public+private) resolve-rate and MCP Atlas tool-use pass-rate -- both live
    (2026-07 rows, frontier models) and on-domain (coding / agentic-ops), covering
    a non-saturated SWE benchmark plus a new MCP tool-discovery axis that AA-direct
    and DeepSWE do not. Data path: plain curl GET + regex-extract the embedded
    self.__next_f.push RSC JSON (no Playwright), then regex-split the free-text
    effort suffix out of the model name, with a manual company/effort normalization
    table. Defensive: schema-validate every field, fail loud on shape drift
    (undocumented internal Next.js serialization, may change on their redeploy).
    Explicitly SKIP the stale Legacy bucket (Coding / Agentic-Tool-Use frozen at
    o1/o3-mini era) and the off-domain Frontier/Safety buckets. Scout report 2026-07-24.'
  type: feature
  component: routing
  context_estimate: medium
- id: B28
  title: 'Gate-adoption & verification workstream (GA1-GA4): `nyxloom gate verify` canary verb (run gate + inject known-bad mutation, assert it FAILS), `asserts=[]` gate-rigor + review-depth routing (folds with factory-hardening D), onboarding offer-to-build-a-gate, carver periodic gate re-verify. See docs/plan-gate-adoption.md'
  type: feature
  component: gate
  context_estimate: large
- id: B29
  title: 'review-leg progress watchdog: persist/measure transcript event growth, worktree writes, gate activity, and concrete findings, detect repetitive work/loops, detect orchestration turns without a finding or patch: escalate to AI to investigate logs and determine the next suitable action (restart/adapt handoff prompt/switch model or tier/...), consider session resume with giving hints how to proceed based on analyzed history.  
  Suppress delivery-profile subagents/capability bookkeeping for bounded review legs unless the handoff explicitly requests them. Emit a typed stalled-review reason and dashboard trace. see also PL11 for originating case, this is generalized towards nyxloom operation.'
  type: feature
  component: review
  context_estimate: medium
  folds_into: F005
- id: B30
  title: 'coverage_gate: make the changed-line floor language-agnostic (Go, Rust, JS).
    The PURE CORE ALREADY IS -- parse_added_lines, evaluate, _resolve_base and
    _check_measurable are plain-data functions with nothing Python-specific in them,
    and none of them needs to change. Exactly two seams bind the module to
    coverage.py: (1) _load_coverage reads its JSON report; (2) evaluate hard-filters
    `if not npath.endswith(".py"): continue` (coverage_gate.py:222). SEAM 2 IS A
    SILENT-PASS BUG for any non-Python consumer, not merely a limitation: it skips
    every source file, evaluate returns 0 changed executable lines, the verdict
    renders "0/0 changed executable lines covered (100.0%)" and the gate PASSES --
    while _check_measurable stays quiet, because the tree is clean and the base is a
    real ancestor. A declared changed-line-coverage assert would then launder every
    merge. PROPOSED: a --source-ext flag (default .py, so Python behaviour is
    bit-identical) plus a pluggable report loader; a Go cover profile and lcov between
    them reach Go, Rust and JS, and lcov alone covers Rust (cargo llvm-cov --lcov) and
    JS (nyc --reporter=lcovonly). THIRD REQUIREMENT, and it falls out of NEITHER seam
    -- a per-language "could this file ever be measured" guard. Go instruments
    function BODIES only, so a .go file that declares no functions produces no cover
    blocks and is absent from the profile for exactly the reason a JSON schema is;
    the existing cov-is-None branch then counts every one of its changed lines as
    uncovered and appends it to files_missing_coverage. MEASURED, not hypothetical:
    94 phantom uncovered lines across four comment-only doc.go package stubs on the
    first run of the reference implementation -- a verdict no test could ever clear.
    This is the B63 "unmeasurABLE is not unmeasurED" lesson one layer in, and it has
    analogues elsewhere (Rust: a mod.rs of only pub use; TS: a .d.ts). The Go form of
    the guard is a go/parser walk for a FuncDecl carrying a Body. WORTH PORTING BACK
    from the reference implementation as its own small fix, independent of the rest:
    a legitimate test-only or comment-only change renders "0/0 changed executable
    lines covered (100.0%)", textually IDENTICAL to what a measurement that never
    happened prints -- the precise ambiguity exit 3 and the NO MEASUREMENT docstring
    section exist to eliminate, reintroduced at the point where the verdict is
    formatted. Fix is to state why the denominator is empty (how many changed files
    reached the intersection, under which prefix); the verdict itself is unchanged.
    Note also that pragma-exclusion (GA5) has no Go analogue, so an adapter should
    report an empty excluded set rather than the guard being made conditional.
    REFERENCE IMPLEMENTATION, with unit tests over crafted inputs, a canary proving
    the floor can fail (95% floor against a 78.0% delta exits 1), and a real
    measurement of 1409/1807 changed executable lines:
    shared-ramdisk-depot-manager/tools/covergate (vbpub commits 855ea4b8, 9d837c27),
    written up in that project nyxloom-trove/decisions.md D-007. CONSUMER WAITING ON
    THIS: srdm, a Go project under one daemon with the Python ones, currently running
    its own reimplementation rather than declaring a floor nyxloom cannot enforce.
    Landing this lets srdm delete tools/covergate and declare [gates.coverage]
    against the shared evaluator, which is also the honest test of whether the
    generalization is real.'
  type: feature
  component: gate
  context_estimate: medium
- id: B31
  title: 'frozen-orientation fork policy: add role/model/effort/epoch base records,
    full orientation anchors and manifests, supported CLI child-fork templates,
    scoped anchor-to-HEAD reconciliation that deliberately exposes progression
    from the frozen OID, typed stale-base refusal, and epoch rotation. Distinguish
    immutable implementer/reviewer parents from an evolving carver continuation;
    disk changes do not mutate the serialized base prefix, so land truth now and
    batch base rebuilds rather than decisions. Pilot manually before changing
    today''s growing resume behavior.
    Design: docs/frozen-orientation-fork-workflow.md.'
  type: feature
  component: dispatch
  context_estimate: large
  folds_into: F010
- id: B32
  title: 'structured successor-brief lifecycle: implementer candidates, blind-first
    reviewer adjudication, carver promotion to durable contract/epoch/decision,
    and controller-only target routing plus one-hop expiry. Surface every
    disposition in the per-handoff processing trace; route reviewed candidates
    through the immediate successor JIT carve so lasting facts are promoted and
    the one-hop remainder is compressed by design authority. Never let the
    controller invent semantic truth. Design:
    docs/frozen-orientation-fork-workflow.md.'
  type: feature
  component: review
  context_estimate: medium
  folds_into: F005
- id: B33
  title: 'five-band implementation-contract routing: codify AUTHORING 2a-2e as
    implement-5 (design-bearing) down through implement-1 (mechanical), add a
    schema/frontmatter field and lintable per-class required assets, then deploy
    implement-3/4/5 routes without changing the established numeric direction.'
  type: feature
  component: routing
  context_estimate: medium
  folds_into: F009
- id: B34
  title: 'prompt-cache experiment and observability: compare fresh, warm compact
    fork, cold fork, and historical broad orientation; persist per-request
    cache-read/write/TTL-class tokens, request-start time, latency,
    time-to-first-edit, reviewer defects, and rework in an append-only trace.
    Fingerprint model/effort/CLI/system/tools because the cache key begins before
    user messages. Trial opt-in disposable-child keepalives from the last
    request start around the measured TTL; never report a warm cache without
    provider telemetry. Design:
    docs/frozen-orientation-fork-workflow.md.'
  type: feature
  component: routing
  context_estimate: medium
  folds_into: F009
- id: B35
  title: 'JIT-carver continuation and pilot state schemas: model the immediate
    post-merge/pre-dispatch carve as a typed leg; persist route-to-carver and
    external compaction checkpoints carrying acknowledged OID, predecessor
    range, reviewed brief dispositions, unresolved decisions, and open probes.
    Add versioned/validated atomic writers for bases.yaml and brief YAML plus an
    append-only invocations.jsonl writer. The evolving Sol carver validates every
    checkpoint against Git; Luna may prepare/route it but never promote product
    truth. Design: docs/frozen-orientation-fork-workflow.md.'
  type: feature
  component: control
  context_estimate: medium
  folds_into: F010
- id: B36
  title: 'controller-owned gate receipts: execute the authoritative gate only
    after the reviewed commit is final; persist exact argv/commit/start/end,
    outer exit, raw combined log and digest; support required phase markers and
    a host-side final marker so a disposable --rm container is fully evidenced.
    Exit zero without the final marker is typed incomplete evidence, not PASS.
    Remove duplicate implementer/reviewer ship-signal runs; their focused checks
    remain diagnostics. Design: docs/frozen-orientation-fork-workflow.md.'
  type: feature
  component: gate
  context_estimate: medium
  folds_into: F010
- id: B37
  title: 'bounded adversarial review harness: predeclare controlled mutation,
    narrow owning test, expected red, process-group failsafe, output cap and
    restoration oracle; kill the whole group on expiry and record
    PROBE_INCONCLUSIVE_HUNG rather than PASS/failure. Add per-package probe and
    total-wall budgets plus trace telemetry so a dark review cannot wait
    indefinitely on a mutated suite. Design:
    docs/frozen-orientation-fork-workflow.md.'
  type: feature
  component: review
  context_estimate: medium
  folds_into: F005
- id: B38
  title: 'contract ladder v2 — replace the prose 2a-2e class with four declared
    axes (A contract fixedness / B integration breadth / C proof cost / D blast
    radius) carried in frontmatter. A drives implementer tier and packet depth,
    C drives the gate lane, D drives review tier, B drives parallel-vs-serial.
    Adds A1 = contracts unfixed = NOT dispatchable, the rung the current ladder
    lacks. Design: docs/design-contract-ladder-v2.md.'
  type: feature
  component: spine
  context_estimate: medium
  folds_into: F008
- id: B39
  title: 'handoff frontmatter v2 + lint cross-checks: add axes/wave/packet/
    review_tier objects to handoff-frontmatter.schema.json, and the axis->tier
    mapping as declared data in routes.toml ([contract_axis]/[blast_axis])
    rather than doctrine prose that names tiers a host may not have. New rules
    L-A1..L-A8; ship L-A5 (a gate whose argv cannot COLLECT an oracle''s
    evidence is an error) and L-A8 (wave-level scope closure: no slice forbids a
    path another slice or the contract needs) first — both have observed defects
    behind them (dstdns P85, assay P21). Design: docs/design-contract-ladder-v2.md.'
  type: feature
  component: spine
  context_estimate: medium
  folds_into: F008
- id: B40
  title: 'wave carving: a contract-freeze package whose deliverable is EXECUTABLE
    (real DDL, importable signatures, declared config keys, error constants, and
    a conformance suite that fails red for every unimplemented slice), then
    parallel A4/A5 slices gated on their own slice of that suite, then one
    terminal qualification package owning all C3/C4 proof. Amortises the
    per-package JIT carve. Defer the live/scale proof, NEVER contract
    conformance — the pre-ladder assay regime is the recorded natural experiment
    for deferral (5 individually-green packages, terminal NOT READY on four
    cross-package integrity defects, 13-package recarve). Design:
    docs/design-contract-ladder-v2.md §6.'
  type: feature
  component: carve
  context_estimate: large
  folds_into: F008
- id: B41
  title: 'authoring corrections folded from the v2 design: state the carve-down
    inequality explicitly (carve down only when the carve delta beats the tier
    delta — routing UP is a correct outcome, not a discipline failure); emit a
    standing escalate_if "the normative packet is internally inconsistent or
    contradicted by the code it names" with every packet, since a prescriptive
    WRONG packet is worse than a vague one; split the pre-flight packet bullet
    into individually falsifiable checkboxes (tracer bullet run, hostile case
    run, skeleton compiles, each negative WITNESSED failing, one carver-authored
    artifact); add two worked examples per axis value from merged packages.
    Design: docs/design-contract-ladder-v2.md §4.5-§4.7.'
  type: feature
  component: spine
  context_estimate: small
  folds_into: F005
- id: B42
  title: 'L13 path extraction is noise-dominated on prose oracles: measured 10/10
    FALSE POSITIVES across 11 dstdns handoffs. Four distinct causes, all in the
    extractor rather than the carve: (a) SHORTHAND paths in oracle prose --
    `mock_targets/mock_dns.py` warned while scope.touch correctly held
    `tests/_harness/mock_targets/mock_dns.py`; (b) DIRECTORY mentions -- a warning
    on `tests/config` while touch held `tests/config/test_x.py`, i.e. the inverse
    of a real gap; (c) GATE-COMMAND targets -- `tests/unit`, `tests/contract`,
    `tests/schema` extracted from the pytest argv, which are RUN targets and must
    never be edit targets; (d) `./` PREFIX defeating fnmatch --
    `./scripts/testing-exec.sh` warned while touch held `scripts/testing-exec.sh`.
    Why it matters more than the noise: L13 exists to catch an oracle
    unsatisfiable within scope.touch, which is the single failure mode that
    killed dstdns packages P26 and P31 and was caught again by hand on P97. A
    warning that is wrong every time trains the operator to filter the channel --
    and dstdns did exactly that, grepping lint output for `error` for a whole
    session and never seeing L13 at all. Suggested: normalise a leading `./`;
    treat a directory-shaped token as covered when any touch entry lies under it;
    do not extract from gate-command text; and require an extracted token to
    resolve in-repo before warning.'
  type: bugfix
  component: carve
  context_estimate: medium
  folds_into: F008
- id: B43
  title: 'a scope.touch entry that is an existing BARE DIRECTORY grants zero
    coverage yet passes every rule. L7 checks existence, and a directory exists,
    so it passes; L13 matches per-file with fnmatch (lint.py:1175), under which
    fnmatch("docs/X.md", "docs") is False. The entry therefore looks generous and
    covers nothing, and there is no rule that reports the contradiction between
    the two. Detection today is INCIDENTAL: it surfaces only if an oracle happens
    to reference a path beneath that directory. Canaried both ways on dstdns --
    a handoff with `tests/fixtures/mock_data/dns_queries` and the same handoff
    with `.../dns_queries/**` produce BYTE-IDENTICAL lint output and both exit 0.
    dstdns shipped this in P98 (`- "docs"`) and carried a second live instance in
    P94; both were caught by hand, then mechanically by a project-local checker.
    Suggested: error on a scope.touch entry resolving to an existing directory
    with no glob metacharacter, prescribing `<dir>/**`. scope.forbid MUST be
    exempt -- bare directories are the established convention there (all 11
    dstdns packages use them) and lint accepts them today.'
  type: bugfix
  component: carve
  context_estimate: small
  folds_into: F008
- id: B44
  title: 'gate identity: a project gate must not silently run MAIN''s tests against
    MAIN''s stack. dstdns spent a whole package (dstdns-P96, two BLOCK verdicts,
    §4.4 tripped) trying to solve this project-locally and it is not solvable
    there. Two coupled facts: (a) the gate argv runs the MAIN checkout''s shim by
    design, so a guard keyed on the shim''s checkout demands --force for EVERY
    dispatched package and becomes decoration; (b) the obvious alternative
    discriminator -- does the command carry a worktree cd prefix -- cannot be
    written against the argv nyxloom actually emits, because gate_runner.py:80,91
    substitutes an ABSOLUTE path and BOTH fallbacks (gate_runner.py:89,91 when
    scratch is None; effects_gates.py:294-297 post-merge) substitute main''s repo
    root with no .worktrees segment at all. A string match fires on every
    dispatch; a path match turns a degraded run into a hard refusal with no
    operator in the loop. This belongs upstream because worktree identity is
    nyxloom''s to declare, not each project''s to infer. Note ciu is expected to
    grow a `worktree` verb owning the directory structure, which likely dissolves
    the inference problem entirely -- so sequence this behind that rather than
    building an inference now. Consumer evidence: 21 rows written to dstdns''s
    PRODUCTION classification_parameter_sets table, one per gate run, before
    anyone noticed the adjacency.'
  type: bugfix
  component: gate
  context_estimate: medium
  folds_into: F008

---

# nyxloom — backlog

Un-scheduled items and sub-packages, each folding into a product-definition
feature (or standalone for ops). `context_estimate` is the carver's read-
context estimate (a scheduler input); `component` is the wave-grouping proxy.


# non-formatted manual backlog reminders

items that must be folded in somewhere, should not be forgotten

- Legacy Data API endpoints retire November 4, 2026, /api/v2/data/llms/models => /api/v2/language/models/free, see https://artificialanalysis.ai/data-api/migrate-v2-data
- "One optional refinement, only if you want it: the Opus reviewer could sanity-check a freshly-carved handoff's oracle satisfiability as a cheap add-on before I dispatch it (same "allowed small in-scope enhancements" spirit as its code review). I'd only add that if carving quality becomes a problem — no evidence of that yet, so I'd default to skipping it, same reasoning as escalate-only Opus: don't add a step until it's earning its keep."
- consider: "the implementer runs its own per-package gate (to our standards, canary, mutation,...), foreground, waits for real output, and only commits/hands back on genuine pass with the actual output pasted into its LOG. (the standing rule "an implementer's self-report is not evidence" still holds, it's just evidence)"- `select_verification_gate` conflates TWO roles: "the project's post-merge gate"
  and "the gate used to verify a merge". `gate_runner.select_verification_gate`
  unconditionally prefers `phase == "post-merge"` over `implementation`, and its
  docstring still says "no project registered today declares a dedicated
  post-merge gate" — dstdns became the first on 2026-08-06 and the effect was
  immediate and silent: merge verification switched from an ~85s unit lane to a
  25–45min live/cross-component lane, and `nyxloom gate verify dstdns` began
  reporting on the release gate instead of the implementation gate. Those are
  different jobs. A release/qualification lane is deliberately slower, broader,
  and allowed to be red while the implementation gate is green (see
  `reference/TESTING-METHODOLOGY.md` §"Scope, rigor, and lanes" — S4 vs S1/S2);
  making it the merge gate means a red release lane blocks every merge, and
  means no project can declare a release lane at all without paying that price.
  Likely shape: keep selection on the implementation gate, and either add an
  explicit `verification = true` marker or a distinct phase for "run this at
  release, not at merge". Until then a project must choose between declaring
  its release lane and keeping a fast merge gate, which is a false choice.

- **Testing-library extraction — SPECIFIED, queued behind the CORE REDESIGN.**
  Now carried as a real handoff:
  `nyxloom-trove/handoffs/nyxloom-P90-extract-testing-library.md` (lint-clean).
  Motivation is measured, not theoretical: `coverage_gate.py` exists FOUR times
  across the estate — nyxloom 455, dstdns 804, topos 299, plus srdm's Go
  `tools/covergate` — and the Python copies have diverged. srdm rewriting it in
  Go rather than adopting a tool it could not consume standalone is the sharpest
  signal that the capability needs to ship as a library, not as nyxloom
  internals. Consumers (dstdns, topos, netcup-api-filter, srdm) each carry a
  note saying migration happens once the library exists; none changes before it
  does. This supersedes the earlier "generalize covergate for srdm" entry above
  by giving it a concrete package.

- **Successor briefs as a first-class trove artifact, with UI drilldown.**
  Emerged from running the assay series (2026-08-06) and is not currently
  expressible in nyxloom. Today a finished package leaves a LOG written *for
  the controller*: what ran, what the gate said. Nothing carries package-to-
  package knowledge to the **next implementer**, so every agent re-derives
  house style, environment quirks and trap history from scratch — or, worse,
  diverges from them silently.

  Shape proven in assay: after self-review, the implementer writes
  `reports/<id>-BRIEF.md` — under 500 words, addressed to a successor that has
  the same docs but has NOT seen this work, explicitly *not* a diff summary.
  Five sections: conventions established that the spec did not dictate; traps
  that cost real time; **spec ambiguities it had to interpret**; environment
  facts established empirically; what was left for a successor. Briefs are
  CONCATENATED in order and handed to every later implementer.

  Three things make it worth productising rather than leaving as a convention:

  1. **The ambiguity section is a feedback channel the daemon lacks.** It is
     where an implementer says "the spec was silent, I chose X" — exactly the
     input `decisions_inbox` wants, currently arriving only as prose in a LOG
     nobody re-reads. On assay's first package it surfaced five rulings, one of
     which the controller overruled; without the channel that would have become
     silent precedent for nine more packages.
  2. **Append-only concatenation is prompt-cache-shaped.** `S0+B1+B2` is a
     literal prefix of `S0+B1+B2+B3`, so each successor re-uses the whole prior
     chain. This is why ratifications should be BATCHED into decisions.md at
     deliberate rebuild points rather than applied per package — editing the
     spec invalidates the shared prefix, appending a brief does not.
  3. **UI drilldown** (the specific ask): a brief should be reachable from its
     package in the UI, and the concatenated chain viewable as one document —
     it is the closest thing to a running "what this project learned" record,
     and it is currently invisible.

  Related and worth carving together: a **two-way readiness protocol**, which is
  the part with no equivalent in nyxloom today.

  Not merely a gate. The implementer is dispatched to orient, assess the
  handoff, and STOP — reporting readiness, ambiguities that admit two readings
  (stated as two readings), conflicts between handoff/spec/disk, and **for each
  oracle, whether it could be satisfied in a way that is technically green but
  hollow**. The controller then rules on each item IN THE GO-MESSAGE, and only
  then does implementation start. Today a nyxloom handoff goes straight to
  implementation and a defect in it surfaces as a failed or subtly wrong
  package.

  Measured on assay, two runs, and the pattern is consistent: **every phase that
  ran before code was written found defects in the SPECIFICATION, not in the
  implementation.**

  - P01's readiness pass: 3 blockers + 7 ambiguities, including an oracle that
    *could not fail* — its `grep -rn` prefixed every line with a path containing
    the package name, and the inverted alternation contained that same name, so
    it filtered everything and passed clean on a file with three third-party
    imports.
  - P01b's readiness pass: a **carving defect** (four later packages needed
    fields in a file their `scope.touch` forbade), a **wrong sentence in the
    project's own design guide** (a claimed superset artifact could never be
    deserialised, because the consumer rejects unknown keys), and an **oracle
    demanding something JSON Schema cannot express**.

  None of those is findable by a gate, and all were found for the cost of one
  orientation turn against an implementation plus review plus rework. The
  readiness report is therefore best understood as the cheapest available review
  of the CARVER's work, not as a check on the implementer.

  **But the readiness pass cannot carry this alone, and the assay evidence for
  it is contaminated.** Both runs cited above were Opus — `review-3`, the review
  and carve tier — while implementation routes cheap-first through
  `implement-1`/`implement-2` by design. Disproving a sentence in the design
  guide required reading `types.py` and noticing `_Serde.from_dict` rejects
  unknown keys; finding the carving defect required intersecting `scope.touch`
  across all ten handoffs with what four later packages needed. Neither is
  plausible band-1 work. A cheap implementer's readiness report is a capability
  self-assessment, not a review of the carver.

  **So: an independent HANDOFF REVIEW by the reviewer standing agent, before
  dispatch.** The argument is not theoretical — on assay the same agent carved
  P01, reviewed its own carving, and shipped an oracle that could not fail. It
  was caught by an independent pass, not by the carver.

  Division of labour, keeping both:

  | Check | Who | Why |
  |---|---|---|
  | Can each oracle FAIL? Name the change that turns it red; if none exists it is unfalsifiable | reviewer, pre-dispatch | independent of implementer tier |
  | Could the oracle be satisfied green but HOLLOW? Name the cheapest passing non-implementation | reviewer, pre-dispatch | the failure a gate cannot see |
  | Scope closure — **across the SERIES, not one handoff**: does any later package need a path its own `scope.touch` forbids? | reviewer, pre-dispatch | a per-handoff reviewer would have MISSED assay's carving defect; it needed the cross-handoff view |
  | Do cited files still exist and say what is claimed? | reviewer, pre-dispatch | specs rot faster than code |
  | Sizing against the carving limit | reviewer, pre-dispatch | cheap, mechanical |
  | "Can I do this, with what is on disk, in this environment?" | implementer, post-dispatch | only the implementer knows its own capability; a band-1 NOT READY is BLOCKED fired early and cheap |

  Two constraints that keep it honest: the reviewer **reports and never edits**
  the handoff — an editing reviewer is a co-carver and the independence is gone;
  and its brief must be adversarial (*attack this handoff*), because two agents
  at the same tier sharing priors will otherwise converge on approval.

  Cost data for sizing this: `assay/nyxloom-trove/MEASUREMENTS.md`.
