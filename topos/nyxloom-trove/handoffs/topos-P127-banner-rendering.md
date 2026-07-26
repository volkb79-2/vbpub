---
schema_version: 1
id: topos-P127-banner-rendering
project: topos
title: "Cover banner safety and high-signal rendering variants"
tier: claude-haiku-high
input_revision: "404e569b"
source: {kind: product-goal, ref: "global-coverage-healing"}
stack: none
depends_on: [topos-P126-damon-nonpositive-pids]
session: fresh
scope:
  touch: ["topos/tests/test_ui_banner.py", "topos/nyxloom-trove/handoffs/topos-P127-banner-rendering.md"]
  forbid: ["topos/src/topos/ui/banner.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py", "topos/pyproject.toml"]
oracles:
  - id: O1
    observable: "collapsed banners retain only host status; warning and critical pressure are distinct user-visible verdicts"
    negative: "collapse leaks detail or elevated host PSI still renders OK"
    gate: topos-suite
  - id: O2
    observable: "partial DAMON telemetry hides heat, complete telemetry renders it, and malformed host/GPU/network fields degrade visibly but safely"
    negative: "partial telemetry fabricates a heat line or malformed telemetry raises/crashes"
    gate: topos-suite
gates: [topos-suite]
review_focus: ["assert rendered text/snapshot behavior, not private helper returns", "each frame mutation remains local to an existing _make_base_frame result"]
escalate_if: ["a supplied rendering expectation contradicts the frozen source capsule", "scope requires an out-of-scope edit"]
advances: []
---

# P127 — banner rendering capability capsule

Read only this handoff and `topos/tests/test_ui_banner.py`; append exactly five
tests to that file. Do not alter existing tests or any other file. No shell,
network, gate, commit, source edit, search/listing, or new file is authorized.
The controller runs tests and gates.

Use existing `_make_base_frame`, `MetricValue`, `Entity`, `EntityFrame`,
`Frame`, and `render_banner`; add no import unless unavoidable.

1. Render `_make_base_frame()` with `collapsed=True`; assert `lines` has one
   element and equals `"HOST OK"`. Then make two independent base frames:
   set `host_psi_mem_full_avg10` to `MetricValue(1.0, "host")` and assert
   verdict `"WARN"`; set it to `MetricValue(2.0, "host")` and assert
   verdict `"CRIT"`.
2. On one base frame set only `host_damon_mode` and
   `host_damon_hot_bytes`; render and assert no line starts `"DRAM HEAT"`.
   On another base frame set mode plus all four byte metrics (`hot=40`,
   `warm=30`, `cold=20`, `idle=10`, src `"host"`) and all four pct metrics
   (`40.0`, `30.0`, `20.0`, `10.0`) plus sample age. Render and assert a line
   starts `"DRAM HEAT [HHHHHHHHWWWWWWCCCCII]"` and contains `"owner unknown"`.
3. On a base frame set `host_gpu_vram_total=MetricValue(2048,"host")` and
   `host_gpu_vram_used=MetricValue(1024,"host")`, leaving busy/count absent;
   render and assert the GPU line starts `"GPU 1.0KiB/2.0KiB"` and lacks
   `"busy"`. In the same or a separate base frame set
   `host_swap_backend=MetricValue("bad","host")`,
   `host_mem_total=MetricValue(1024**5,"host")`, and
   `host_mem_available=MetricValue(1024**5,"host")`; render and assert its
   swap line contains `"SWAP backend ?"` and its LOAD/MEM line contains
   `"1024.0TiB"`.
4. On a base frame set `host_meta` to one net device with name `"eth0"`,
   `rx_bps=1000.0`, `tx_bps=500.0`, `rx_pps=1_000_000.0`,
   `tx_pps=1.0`, `rx_drops_s=0.0`, `tx_drops_s=0.0`,
   `rx_errors_s=0.0`, and `tx_errors_s=100.0`. Render, find the NET line,
   and assert it contains `"rx1.0M/s"`, `"LOSS"`, and `"tx_err100/s"`.
5. Make a base frame with an entity at key `""` whose `damon` is a dict with
   `host_sessions` set to the string `"malformed"`; also provide the complete
   DAMON telemetry from test 2. Render and assert the DRAM HEAT line still
   contains `"owner unknown"`. This proves malformed ownership metadata is a
   safe fallback, not an exception or fabricated owner.

Every assertion must inspect `BannerSnapshot` output. Do not import or call
private banner functions. Stop after the edit and reply with test-name →
behavior and exact changed file. Do not claim a test/gate ran. If impossible,
reply `BLOCKED: <one sentence>` and make no edit.

## Frozen source capsule

```python
20 if notice_count: ...
22 if collapsed:
23     return BannerSnapshot(...)
71 if sample >= crit: return "CRIT"
73 if sample >= warn: verdict = "WARN"
108 if any(metric is None or metric.v is None for metric in bytes_by_class.values()):
109     return None
138 if remaining > 0:
139     parts.append("." * remaining)
145 if root is None or not isinstance(root.damon, dict): return ()
148 if not isinstance(sessions, list): return ()
187 if busy_pct is not None and busy_pct.v is not None: return ... busy ...
189 return f"GPU {used_text}/{total_text}{count_suffix}"
276 if tx_e is not None:
277     loss_parts.append(...)
315 if value >= 1_000_000: return ... "M"
326 if value >= 100: return ...
356 try: return labels[int(metric.v)]
358 except (TypeError, ValueError, KeyError): return "?"
400 for unit in units:
401     if abs(scaled) < 1024 or unit == units[-1]: break
404 if unit == "B": ...
```
