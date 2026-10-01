"""P91 O10 — 24h synthetic workload measurement, one-shot (not a regression gate).

Simulates 24h of daemon collection at the default 5s interval (17280 frames)
through PersistentHistoryStore at its default PersistConfig, measuring wall
time, CPU, RSS, compression ratio and write amplification against the
Required Contract 6 resource budget.
"""
import dataclasses
import json
import random
import resource
import sys
import time

sys.path.insert(0, "src")
sys.path.insert(0, "tests")

from topos.config import PersistConfig
from topos.daemon.persist import PersistentHistoryStore
from topos.model import MetricValue
from conftest import fixture_frame

INTERVAL_S = 5.0
DURATION_S = 24 * 3600
FRAME_COUNT = int(DURATION_S / INTERVAL_S)

cfg = PersistConfig(enabled=True, dir=__import__("pathlib").Path("/tmp/p91-measure-store"))
import shutil
shutil.rmtree(cfg.dir, ignore_errors=True)

base_frame = fixture_frame()
start_ts = 1_800_000_000.0
rng = random.Random(1234)


def _jitter_metric(mv: MetricValue) -> MetricValue:
    if not isinstance(mv.v, (int, float)):
        return mv
    noise = mv.v * rng.uniform(-0.03, 0.03) if mv.v else rng.uniform(-1.0, 1.0)
    return dataclasses.replace(mv, v=round(mv.v + noise, 3))


def _jitter_frame(frame, ts: float):
    host = {k: _jitter_metric(v) for k, v in frame.host.items()}
    entities = {
        key: dataclasses.replace(ef, metrics={mk: _jitter_metric(mv) for mk, mv in ef.metrics.items()})
        for key, ef in frame.entities.items()
    }
    return dataclasses.replace(frame, ts=ts, host=host, entities=entities)


clock = {"i": 0}

ru0 = resource.getrusage(resource.RUSAGE_SELF)
wall0 = time.time()

store = PersistentHistoryStore(cfg, now=lambda: start_ts + clock["i"] * INTERVAL_S)

for i in range(FRAME_COUNT):
    clock["i"] = i
    ts = start_ts + i * INTERVAL_S
    f = _jitter_frame(base_frame, ts)
    store.append(f)
store.flush()

wall1 = time.time()
ru1 = resource.getrusage(resource.RUSAGE_SELF)

stats = store.stats()
wall_s = wall1 - wall0
user_cpu = ru1.ru_utime - ru0.ru_utime
sys_cpu = ru1.ru_stime - ru0.ru_stime
rss_kb = ru1.ru_maxrss

write_amplification = (
    (stats.lifetime_bytes_written + stats.lifetime_index_bytes_written) / stats.lifetime_raw_bytes_written
    if stats.lifetime_raw_bytes_written
    else None
)

result = {
    "frame_count_simulated": FRAME_COUNT,
    "duration_hours_simulated": DURATION_S / 3600,
    "interval_s": INTERVAL_S,
    "wall_s": round(wall_s, 3),
    "user_cpu_s": round(user_cpu, 3),
    "sys_cpu_s": round(sys_cpu, 3),
    "cpu_pct_of_realtime_day": round(100 * (user_cpu + sys_cpu) / DURATION_S, 4),
    "max_rss_kb": rss_kb,
    "final_segment_count": stats.segment_count,
    "final_byte_size": stats.byte_size,
    "final_raw_bytes": stats.raw_bytes,
    "final_compression_ratio": stats.compression_ratio,
    "evicted_segments": stats.evicted_segments,
    "evicted_frames": stats.evicted_frames,
    "quarantined_segments": stats.quarantined_segments,
    "lifetime_bytes_written": stats.lifetime_bytes_written,
    "lifetime_raw_bytes_written": stats.lifetime_raw_bytes_written,
    "lifetime_index_bytes_written": stats.lifetime_index_bytes_written,
    "lifetime_frames_written": stats.lifetime_frames_written,
    "write_amplification": write_amplification,
    "byte_cap_bytes": stats.byte_cap_bytes,
    "age_cap_seconds": stats.age_cap_seconds,
    "within_byte_cap": stats.byte_size <= stats.byte_cap_bytes,
}
print(json.dumps(result, indent=2, sort_keys=True))
shutil.rmtree(cfg.dir, ignore_errors=True)
