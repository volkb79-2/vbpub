# run-gate resumable-gate wave (E-1 → 23.4.0) — controller log

Rulings RW-1..RW-8 are in the wave prompt
(`WAVE-PROMPT-2026-09-02-resumable-gate.md`). This file carries the
controller's entries from the implementer's return onward; wave records
(`-LOG`, `-REPORT`, `-REVIEW-round<n>`) live on the branch
`feature/run-gate-wave-resumable`.

- **2026-09-02 (implementer returned — all four items landed, gate GREEN;
  RW-9..RW-12; follow-up package before the reviewer)** — Verified in a
  separate step from `selftest-close.log`: `488 passed, 2 skipped`,
  `diff-coverage OK: 269/269 changed executable lines covered (100.0% ≥
  100.0% floor)`, `lane 'selftest' exit 0`; `git log main..HEAD` = five
  commits (`6fe633f5` RG-35/R-39, `8db781e6` RG-32/R-08a BREAKING,
  `1e41069f` RG-34/R-30b, `10aa59e2` RG-36/R-40, `d4e8e137` records +
  RG-39 filed), tree clean, `__revision__ = 34`. Red-first for RG-35 was
  real: rev 33 on a detached worktree starts two `docker run -d` for one
  lane/commit (`assert 2 == 1`). The live acceptance probe ran against a
  real `tester-unified:local` at 21:54 UTC (waited for Wave D's container,
  `--cpus=3`, trap-removed): client SIGKILLed 8 s in, container survived,
  invocation 2 printed `re-attached to … (started 2026-09-02T21:54:24Z,
  running for 0m 08s)`, started no container, replayed from `tick 1`
  (`--since` proven), exit 0, record cleared, ONE history entry with
  `duration_seconds: 70.858` from the container's clock (RW-3 proven live).
  The implementer deliberately continued past the E-008 arm point (~60
  calls after RG-34's green gate) and finished RG-36 without a BRIEF; the
  deviation is recorded in its LOG.
  - **RW-9 (ask 1 — `stall_timeout` on a `kind = "command"` lane): the
    refusal stands.** RW-5's signal is the progress file; a key that can
    never act on a command lane is exactly RG-32's defect class, and
    run-gate does not guess a second signal on its own. The gap is real
    (container command lanes are the shape most likely to hang) and gets
    its own item: **RG-40 — liveness for a container command lane judged
    from LOG-STREAM silence.** run-gate already streams `docker logs -f`;
    the arrival time of the last line on run-gate's own clock carries the
    same "silence, never elapsed" semantics R-40 gives the progress file,
    so `stall_timeout` becomes legal on command lanes with the SOURCE of
    the signal disclosed at start (`progress file` vs `log stream`).
    E-3 candidate (23.5.0). Filed on the branch by the implementer in the
    follow-up package so the reviewer's tip carries it and the merge has
    no backlog conflict.
  - **RW-10 (ask 2 — `--fresh` through a conjunction): no propagation, no
    token; document the shape.** `--fresh` is per-invocation and
    deliberately does NOT fan out: fanning it out would remove EVERY
    sub-lane's container including ones legitimately running, the blunt
    tool RG-35 exists to avoid. A sub-lane's commit-mismatch refusal
    already exits 2 through the conjunction's `&&` chain naming THAT
    sub-lane and `--fresh`, and the operator applies it to that sub-lane
    directly. A consumer who wants a sub-lane always fresh writes
    `--fresh` into that sub-invocation's static argv, forfeiting re-attach
    for it, and the docs say so. One paragraph in CONSUMERS.md's
    "Gate-conjunction lanes" section beside the RG-1/`{base}` sentences,
    one line under SPEC R-39.
  - **RW-11 (ask 3 — RG-39, `coverage_gate.py` dirty-tree offsets): filed
    stands, not fixed in this wave.** Every measurement in the wave was
    taken on a committed tip at 100.0%, so the evidence stands. The fix
    (changed lines diffed against the WORKING TREE when `--allow-dirty`,
    plus its test) is the first item of E-3's package. The reviewer is
    told to gate on a committed tip only.
  - **RW-12 (the E-008 deviation): accepted as recorded, no rule change.**
    196 calls, ~330k tokens, finished green with one well-scoped item whose
    design was fixed by RW-4..RW-6. One data point for the E-008 record
    (nyxloom `design-context-lifecycle-experiments.md`), to be added when
    that record is next touched, not a new doctrine.
  - **Follow-up package sent to the implementer (same session, context
    intact):** RG-40 section + index row; the RW-10 paragraph and SPEC
    line; CHANGES `[Unreleased]` unchanged except a one-line pointer to
    RG-40 under RG-36's entry; one selftest run; one commit; return. The
    reviewer (fresh, never a fork, 3-round cap) is dispatched on that tip.
