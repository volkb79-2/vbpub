# assay Wave C (Go, the P27 re-carve) — controller log

Running audit trail of every autonomous controller decision for Wave C,
maintained the same way as `assay-WAVE-B-producer-CONTROLLER-LOG.md`.
Operating rule: same active `/goal` as Wave B — report-and-continue on
routine events (checkpoint/successor cycles, REJECT→fix-round,
ACCEPT→merge/release), log every one here plus a brief chat note; actually
pause only for the reviewer's 3-round cap hit without ACCEPT, or a genuine
product/design fork the wave plan / decisions.md / A-334/A-335-style
principles don't already resolve.

- **2026-08-31 (dispatch)** — Wave B's real release (assay-v4.0.0) is
  confirmed published, deployed, and dstdns-notified (see the Wave B
  controller log's final entries). Per the operator's standing ruling
  (`WAVE-PROMPT-2026-08-30-js-consumer-producer.md` line ~20: "Wave C's
  dispatch waits for Wave B's own real RELEASE... not merely its merge"),
  Wave C is now clear to start.

  Before writing the dispatch prompt, ran a research-only fork to settle
  whether the Go wave's own planning ground (`STATE.md`'s P27 history) was
  current or stale — see the Wave B log's last entry for why this was
  needed rather than assumed. Findings, independently re-verified by the
  controller directly against `decisions.md`/`2-product-definition.md`/
  `carve-assets/P27/`:
  - **A-217 (2026-08-11) already resolved A-O19** — ruled option 2 (a
    real source-side statement-position oracle) over blocks-as-truth or a
    v5 schema narrowing. `STATE.md`'s "P27 is still NOT dispatchable"
    language describes the pre-ruling state and is stale AS A BLOCKER, but
    was correct that P27 needed re-carving around the oracle rather than
    dispatch as originally scoped — this wave IS that re-carve, not a
    contradiction of the old note.
  - `3-roadmap.md` (current as of 2026-08-24) lists M6 (Go) as next,
    after M4 (ship) and M5 (SQL/P34), both done — clear to proceed.
  - **Current authoritative scope is narrower than the old STATE.md
    P27→P32 sequence.** `2-product-definition.md`'s F008 has 5 acceptance
    criteria; F008-A1/A2 already `proven`. M6 delivers only the three
    `absent` ones (F008-A3 statement-granular R1, F008-A4 real fixture
    regen, F008-A5 srdm covergate qualification) plus B047's smaller
    items (helper distribution, `external_tools` declaration, `go-cover`
    producer key, gate-envelope check). **No later roadmap or backlog
    entry re-authorizes the older STATE.md language about "P30 real Go
    R2" / "P31 real Go R3"** — Go R2/R3 stay explicitly out of scope for
    this wave; `generate_mutants` stays `UNSUPPORTED` for Go.
  - `carve-assets/P27/` (README, BLOCKED-grammar.md, probe-results.json,
    pinned-environment.json, fixture/, expected/, manifest/, witness/ — 14
    real witness files) is fully present and real: real `gofmt`-clean Go
    source, real `tester-unified-go:local`-produced coverage profiles,
    including the collision-pair impossibility proof itself
    (byte-identical profiles, provably different correct statement
    positions) and four other named discriminator/caveat fixtures. This
    is carver-owned, frozen evidence — cited in the dispatch prompt, not
    to be edited by the implementer.
  - Code is NOT starting from zero: `adapters/go.py` and
    `coverage_parsers/go_cover.py` already exist, already carry
    "STALE PREMISE, TRACKED (A-234/A-217)" banners naming exactly what
    this wave must fix, and were both touched as recently as Wave A's
    B039 work (the shared classified-line ceiling).
  - A genuine design fork exists (A-239's concrete `blocks` field shape
    and the new protocol hook's exact signature) but is correctly left
    undecided in the docs for the re-carve itself to design, per A-084 —
    not a controller-level pause point; this is ordinary implementer
    design work bounded by an already-accepted general shape, the same
    kind of call Wave B's implementer made for its own schema fields.

  **Controller decision**: proceed with dispatch, scoped to F008-A3/A4/A5
  + B047 items 1/2/3/5/6 (item 4, the shared bound, already landed in
  Wave A — nothing to do). Recorded the full delta as a new "## Wave C —
  dispatched 2026-08-31" section in
  `WAVE-PROMPT-2026-08-30-js-consumer-producer.md` (commit `25b1f7fb`),
  following the same shared-skeleton-plus-delta pattern the doc already
  used for Wave A→B, so the authoritative scope, NOT-IN-SCOPE list, rules,
  and reviewer emphasis live in one committed place before the implementer
  starts — not only in this log or the dispatch prompt text.

  Dispatched a fresh Opus session (never a fork, per the estate's
  implementer-role doctrine — this wave carries real design judgment:
  A-239's concrete carve) as the Wave C implementer, worktree
  `.worktrees/assay-wave-c-go`, branch `feature/assay-wave-c-go` off
  `main`. Expect a MINOR bump (no `!` commit) — this wave is designed not
  to touch the verdict wire schema (the new `blocks` field lives in the
  internal `coverage_parsers/model.py` representation, not `verdict.py`);
  told the implementer to stop and write a decision ask rather than cut a
  v10 if that assumption turns out wrong.

  Next: wait for the implementer's first checkpoint or completion signal,
  per the same external-compaction / fresh-successor pattern used
  throughout Wave B.
