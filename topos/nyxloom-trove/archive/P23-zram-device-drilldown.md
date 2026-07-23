# P23 - ZRAM per-device drill-down

**Cut:** v1.5 polish. **Depends:** P19. Branch:
`feat/topos-p23-zram-device-drilldown`. Worktree:
`.worktrees/-topos-p23-zram-device-drilldown`.

## Goal

Close the P19 drill-down gap by preserving and rendering per-device ZRAM state
without pretending that ZRAM can be attributed per cgroup. Operators should see
the active swap backend in the banner and be able to inspect each zram device
from the host-memory surface.

## Required context

- Read `topos/README.md`, especially "Workflow protocol".
- Read `topos/CONTRACTS.md`, especially model/registry serialization rules.
- Read `topos/docs/COMPRESSED-SWAP.md`.
- Read `topos/docs/STATUS.md` and `topos/docs/ROADMAP.md` P19/P23 context.
- Read existing implementation/tests around:
  - `src/topos/collect/host.py`
  - `src/topos/model.py`
  - `src/topos/ui/hostmem.py`
  - `tests/test_host_swap.py`
  - UI tests that render host-memory text.

## Scope

1. Add structured frame metadata for host-level non-metric details.
   - Prefer an additive field such as `Frame.host_meta: dict[str, object]`.
   - Keep `Frame.host` strictly registry-backed `MetricValue`s.
   - Serialization must round-trip through `frame_to_jsonable()` and
     `frame_from_jsonable()`.
   - Old frames without the field must still read cleanly.
2. Collect ZRAM device details under host metadata.
   - Suggested key: `host_meta["zram_devices"]`.
   - Include only read-only fields from `/sys/block/zram<N>/`: name, orig,
     compr, mem_used, mem_limit, mem_used_max, same_pages, huge_pages,
     failed_reads, failed_writes, writeback_bytes, ratio, efficiency.
   - Preserve graceful degradation: malformed stat fields should not crash and
     missing files should produce absent/zero/`None` values consistently with
     the aggregate P19 behavior.
3. Render the details in the host-memory screen.
   - Keep the banner compact; do not add a new global banner line.
   - Add a `ZRAM DEVICES` section to `render_host_memory_text()`.
   - Show a clear no-device line when none are present.
   - Keep wording explicit that per-cgroup zram compression/cost attribution is
     unavailable.
4. Update docs.
   - `docs/COMPRESSED-SWAP.md`: mark per-device drill-down semantics as
     implemented.
   - `docs/STATUS.md` and `docs/ROADMAP.md`: update P23 state and remaining
     gap language.
   - `README.md`: add/update P23 in the work package table.

## Out of scope

- No zram configuration writes, reset, recompression, writeback control, or
  tuning.
- No per-cgroup zram compression ratios or physical-memory attribution.
- No daemon protocol version bump unless the normal frame serializer naturally
  carries the additive field.
- No broad TUI redesign; this is a drill-down text surface, not a new panel.

## Acceptance criteria

- Host collection exposes aggregate P19 metrics exactly as before.
- A fixture with two zram devices round-trips per-device metadata through
  `frame_to_jsonable()` and `frame_from_jsonable()`.
- Host-memory text renders per-device names, logical/original bytes, memory
  used, ratio, failed IO counts, and writeback bytes.
- Host-memory text renders a no-device state and the per-cgroup attribution
  caveat.
- Existing old-frame JSON without host metadata still loads.
- Focused tests cover malformed zram stats and multiple devices.
- Full `topos/tests` suite passes, plus `py_compile` over changed files.

## Handoff artifacts

- Keep `topos/handoff/reports/P23-LOG.md` current using
  `handoff/AGENT-LOG-TEMPLATE.md`.
- Write `topos/handoff/reports/P23-REPORT.md` with implementation summary,
  deviations, test evidence, known gaps, and proposed contract changes.
- Commit the feature branch before handoff.
