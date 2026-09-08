"""P91 O10 — 24h synthetic workload measurement at D-005's stated production
scale (~447 KiB/frame), extrapolated by replicating the fixture's 8 entities
to approximate a ~450 KiB frame. One-shot, not a regression gate.
"""
import dataclasses
import json
import random
import resource
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "tests")

from topos.config import PersistConfig
from topos.daemon.persist import PersistentHistoryStore
from topos.model import MetricValue, frame_to_jsonable
from conftest import fixture_frame

INTERVAL_S = 5.0
DURATION_S = 24 * 3600
FRAME_COUNT = int(DURATION_S / INTERVAL_S)
TARGET_BYTES = 447 * 1024

cfg = PersistConfig(enabled=True, dir=Path("/tmp/p91-measure-store-scaled"))
shutil.rmtree(cfg.dir, ignore_errors=True)

base_frame = fixture_frame()
start_ts = 1_800_000_000.0
rng = random.Random(5678)

# Replicate entities under renamed keys until the frame reaches ~447 KiB raw,
# approximating D-005's measured gstammtisch-scale production frame.
entities = dict(base_frame.entities)
replica = 0
while len(json.dumps(frame_to_jsonable(dataclasses.replace(base_frame, entities=entities)))) < TARGET_BYTES:
    replica += 1
    for key, ef in base_frame.entities.items():
        new_key = f"{key}-r{replica}"
        entities[new_key] = dataclasses.replace(ef, entity=dataclasses.replace(ef.entity, key=new_key))
scaled_frame = dataclasses.replace(base_frame, entities=entities)
scaled_bytes = len(json.dumps(frame_to_jsonable(scaled_frame)))


def _jitter_metric(mv):
    if not isinstance(mv.v, (int, float)):
        return mv
    noise = mv.v * rng.uniform(-0.03, 0.03) if mv.v else rng.uniform(-1.0, 1.0)
    return dataclasses.replace(mv, v=round(mv.v + noise, 3))


def _jitter_frame(frame, ts: float):
    host = {k: _jitter_metric(v) for k, v in frame.host.items()}
    ents = {
        key: dataclasses.replace(ef, metrics={mk: _jitter_metric(mv) for mk, mv in ef.metrics.items()})
        for key, ef in frame.entities.items()
    }
    return dataclasses.replace(frame, ts=ts, host=host, entities=ents)


clock = {"i": 0}
ru0 = resource.getrusage(resource.RUSAGE_SELF)
wall0 = time.time()

store = PersistentHistoryStore(cfg, now=lambda: start_ts + clock["i"] * INTERVAL_S)
for i in range(FRAME_COUNT):
    clock["i"] = i
    ts = start_ts + i * INTERVAL_S
    store.append(_jitter_frame(scaled_frame, ts))
store.flush()

wall1 = time.time()
ru1 = resource.getrusage(resource.RUSAGE_SELF)
stats = store.stats()

result = {
    "scaled_frame_entities": len(entities),
    "scaled_frame_raw_bytes": scaled_bytes,
    "frame_count_simulated": FRAME_COUNT,
    "wall_s": round(wall1 - wall0, 3),
    "user_cpu_s": round(ru1.ru_utime - ru0.ru_utime, 3),
    "sys_cpu_s": round(ru1.ru_stime - ru0.ru_stime, 3),
    "cpu_pct_of_realtime_day": round(100 * ((ru1.ru_utime - ru0.ru_utime) + (ru1.ru_stime - ru0.ru_stime)) / DURATION_S, 4),
    "max_rss_kb": ru1.ru_maxrss,
    "final_segment_count": stats.segment_count,
    "final_byte_size": stats.byte_size,
    "final_raw_bytes": stats.raw_bytes,
    "final_compression_ratio": stats.compression_ratio,
    "evicted_segments": stats.evicted_segments,
    "evicted_frames": stats.evicted_frames,
    "lifetime_bytes_written": stats.lifetime_bytes_written,
    "lifetime_raw_bytes_written": stats.lifetime_raw_bytes_written,
    "lifetime_index_bytes_written": stats.lifetime_index_bytes_written,
    "byte_cap_bytes": stats.byte_cap_bytes,
    "within_byte_cap": stats.byte_size <= stats.byte_cap_bytes,
}
print(json.dumps(result, indent=2, sort_keys=True))
shutil.rmtree(cfg.dir, ignore_errors=True)
