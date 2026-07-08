from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from groop.collect.cgroup import parse_int_text, read_text
from groop.config import DamonConfig
from groop.model import EntityFrame, EntityKey, Frame, MetricSource, MetricValue

DEFAULT_DAMON_ROOT = Path("/sys/kernel/mm/damon/admin/kdamonds")
_MODE_CODES = {"vaddr": 1, "paddr": 2}
_DAMON_METRIC_NAMES = (
    "damon_hot_bytes",
    "damon_warm_bytes",
    "damon_cold_bytes",
    "damon_idle_bytes",
    "damon_hot_pct",
    "damon_warm_pct",
    "damon_cold_pct",
    "damon_idle_pct",
    "damon_sample_age_s",
    "damon_mode",
)
_CLASS_NAMES = ("hot", "warm", "cold", "idle")


@dataclass(frozen=True)
class _Region:
    start: int
    end: int
    nr_accesses: int
    age_aggr_intervals: int

    @property
    def size_bytes(self) -> int:
        return max(0, self.end - self.start)


@dataclass
class _AttributedSession:
    entity_key: EntityKey
    session: dict[str, object]


class _Classifier:
    def __init__(self, config: DamonConfig) -> None:
        self.hot_rate = config.hot_rate
        self.warm_rate = config.warm_rate
        self.cold_age_us = int(config.cold_age * 1_000_000)
        self.idle_age_us = int(config.idle_age * 1_000_000)

    @staticmethod
    def access_rate_pct(nr_accesses: int, max_nr_accesses: int) -> float:
        if max_nr_accesses <= 0:
            return 0.0
        return (nr_accesses / max_nr_accesses) * 100.0

    def classify(self, nr_accesses: int, age_us: int, max_nr_accesses: int) -> str:
        rate = self.access_rate_pct(nr_accesses, max_nr_accesses)
        if rate >= self.hot_rate:
            return "hot"
        if rate >= self.warm_rate:
            return "warm"
        if rate == 0.0 and age_us >= self.idle_age_us:
            return "idle"
        if age_us >= self.cold_age_us:
            return "cold"
        return "warm"


def annotate_frame_damon(
    frame: Frame,
    *,
    damon_root: Path = DEFAULT_DAMON_ROOT,
    proc_root: Path = Path("/proc"),
    cgroup_root: Path,
    config: DamonConfig,
    now: float,
) -> Frame:
    unavailable_src = _root_unavailable_src(damon_root)
    for entity_frame in frame.entities.values():
        _seed_unavailable_metrics(entity_frame, unavailable_src)

    attributed: dict[EntityKey, list[dict[str, object]]] = {}
    root_host_sessions: list[dict[str, object]] = []
    classifier = _Classifier(config)
    for kdamond_dir in _numeric_dirs(damon_root):
        for context_dir in _numeric_dirs(kdamond_dir / "contexts"):
            session = _read_context_session(
                kdamond_dir,
                context_dir,
                proc_root=proc_root,
                cgroup_root=cgroup_root,
                classifier=classifier,
                now=now,
            )
            if session is None:
                continue
            mode = str(session["mode"])
            if mode == "paddr":
                root_host_sessions.append(session)
                continue
            entity_key = session.get("entity_key")
            if not isinstance(entity_key, str):
                continue
            attributed.setdefault(entity_key, []).append(session)

    for entity_key, sessions in attributed.items():
        entity_frame = frame.entities.get(entity_key)
        if entity_frame is None:
            continue
        _apply_entity_sessions(entity_frame, sessions)

    if root_host_sessions and "" in frame.entities:
        root_frame = frame.entities[""]
        root_frame.damon = {
            **(root_frame.damon or {}),
            "host_sessions": root_host_sessions,
        }

    return frame


def _seed_unavailable_metrics(entity_frame: EntityFrame, src: MetricSource) -> None:
    for name in _DAMON_METRIC_NAMES:
        entity_frame.metrics[name] = MetricValue(None, src)


def _apply_entity_sessions(entity_frame: EntityFrame, sessions: list[dict[str, object]]) -> None:
    totals = {name: 0 for name in _CLASS_NAMES}
    total_bytes = 0
    sample_ages: list[float] = []
    for session in sessions:
        class_bytes = session.get("class_bytes")
        if isinstance(class_bytes, dict):
            for name in _CLASS_NAMES:
                value = int(class_bytes.get(name, 0) or 0)
                totals[name] += value
                total_bytes += value
        sample_age = session.get("sample_age_s")
        if isinstance(sample_age, (int, float)):
            sample_ages.append(float(sample_age))

    for name in _CLASS_NAMES:
        entity_frame.metrics[f"damon_{name}_bytes"] = MetricValue(totals[name], "exact")
        pct = (totals[name] / total_bytes * 100.0) if total_bytes > 0 else 0.0
        entity_frame.metrics[f"damon_{name}_pct"] = MetricValue(pct, "exact")
    entity_frame.metrics["damon_sample_age_s"] = MetricValue(max(sample_ages) if sample_ages else None, "exact" if sample_ages else "unavail_kernel")
    entity_frame.metrics["damon_mode"] = MetricValue(_MODE_CODES["vaddr"], "exact")
    entity_frame.damon = {
        "sessions": sessions,
        "summary": {
            "total_bytes": total_bytes,
            "class_bytes": totals,
        },
    }


def _read_context_session(
    kdamond_dir: Path,
    context_dir: Path,
    *,
    proc_root: Path,
    cgroup_root: Path,
    classifier: _Classifier,
    now: float,
) -> dict[str, object] | None:
    mode_result = read_text(context_dir / "operations")
    mode = str(mode_result.value).strip() if mode_result.value is not None else None
    if mode not in {"vaddr", "paddr"}:
        return None

    scheme_count = len(_numeric_dirs(context_dir / "schemes"))
    target_pids = _target_pids(context_dir / "targets")
    scheme = _preferred_scheme(context_dir / "schemes")
    if scheme is None:
        return {
            "mode": mode,
            "kdamond_idx": _numeric_name(kdamond_dir),
            "context_idx": _numeric_name(context_dir),
            "kdamond_pid": _read_int(kdamond_dir / "pid")[0],
            "state": _read_text_value(kdamond_dir / "state"),
            "sample_us": _read_int(context_dir / "monitoring_attrs" / "intervals" / "sample_us")[0],
            "aggr_us": _read_int(context_dir / "monitoring_attrs" / "intervals" / "aggr_us")[0],
            "update_us": _read_int(context_dir / "monitoring_attrs" / "intervals" / "update_us")[0],
            "target_pids": target_pids,
            "scheme_count": scheme_count,
            "regions": [],
            "class_bytes": {name: 0 for name in _CLASS_NAMES},
            "class_pct": {name: 0.0 for name in _CLASS_NAMES},
            "sample_age_s": None,
        } if mode == "paddr" else None

    sample_us, _sample_src = _read_int(context_dir / "monitoring_attrs" / "intervals" / "sample_us")
    aggr_us, _aggr_src = _read_int(context_dir / "monitoring_attrs" / "intervals" / "aggr_us")
    update_us, _update_src = _read_int(context_dir / "monitoring_attrs" / "intervals" / "update_us")
    max_nr_accesses = aggr_us // sample_us if isinstance(sample_us, int) and isinstance(aggr_us, int) and sample_us > 0 else 0
    class_bytes = {name: 0 for name in _CLASS_NAMES}
    regions_meta: list[dict[str, object]] = []
    total_bytes = 0
    for region in scheme["regions"]:
        age_us = region.age_aggr_intervals * aggr_us if isinstance(aggr_us, int) else 0
        access_rate_pct = classifier.access_rate_pct(region.nr_accesses, max_nr_accesses)
        region_class = classifier.classify(region.nr_accesses, age_us, max_nr_accesses)
        class_bytes[region_class] += region.size_bytes
        total_bytes += region.size_bytes
        regions_meta.append(
            {
                "start": region.start,
                "end": region.end,
                "size_bytes": region.size_bytes,
                "nr_accesses": region.nr_accesses,
                "access_rate_pct": access_rate_pct,
                "age_s": age_us / 1_000_000.0,
                "class": region_class,
            }
        )
    if total_bytes <= 0 and isinstance(scheme["total_bytes"], int):
        total_bytes = int(scheme["total_bytes"])
    class_pct = {name: (class_bytes[name] / total_bytes * 100.0) if total_bytes > 0 else 0.0 for name in _CLASS_NAMES}
    session: dict[str, object] = {
        "mode": mode,
        "kdamond_idx": _numeric_name(kdamond_dir),
        "context_idx": _numeric_name(context_dir),
        "scheme_idx": scheme["scheme_idx"],
        "scheme_action": scheme["action"],
        "kdamond_pid": _read_int(kdamond_dir / "pid")[0],
        "state": _read_text_value(kdamond_dir / "state"),
        "sample_us": sample_us,
        "aggr_us": aggr_us,
        "update_us": update_us,
        "target_pids": target_pids,
        "scheme_count": scheme_count,
        "total_bytes": total_bytes,
        "class_bytes": class_bytes,
        "class_pct": class_pct,
        "sample_age_s": _sample_age_seconds(scheme["sample_paths"], now),
        "regions": regions_meta,
    }
    if mode == "paddr":
        return session

    entity_key = _single_target_entity(target_pids, proc_root)
    if entity_key is None:
        return None
    entity_pids = _cgroup_pids(cgroup_root, entity_key)
    covered_pids = [pid for pid in target_pids if pid in entity_pids] if entity_pids is not None else list(target_pids)
    session["entity_key"] = entity_key
    session["entity_pid_count"] = len(entity_pids) if entity_pids is not None else None
    session["covered_pid_count"] = len(covered_pids)
    session["covered_pids"] = covered_pids
    return session


def _preferred_scheme(schemes_dir: Path) -> dict[str, object] | None:
    candidates: list[dict[str, object]] = []
    for scheme_dir in _numeric_dirs(schemes_dir):
        tried_regions_dir = scheme_dir / "tried_regions"
        regions = _read_regions(tried_regions_dir)
        total_bytes, _src = _read_int(tried_regions_dir / "total_bytes")
        action = _read_text_value(scheme_dir / "action")
        sample_paths = [tried_regions_dir / "total_bytes"]
        sample_paths.extend(_region_sample_paths(tried_regions_dir))
        if not regions and total_bytes is None:
            continue
        candidates.append(
            {
                "scheme_idx": _numeric_name(scheme_dir),
                "action": action,
                "regions": regions,
                "total_bytes": total_bytes,
                "sample_paths": sample_paths,
            }
        )
    if not candidates:
        return None
    candidates.sort(key=lambda item: (0 if item.get("action") == "stat" else 1, -len(item["regions"])))
    return candidates[0]


def _read_regions(tried_regions_dir: Path) -> list[_Region]:
    regions: list[_Region] = []
    for region_dir in _numeric_dirs(tried_regions_dir):
        start, start_src = _read_int(region_dir / "start")
        end, end_src = _read_int(region_dir / "end")
        nr_accesses, accesses_src = _read_int(region_dir / "nr_accesses")
        age, age_src = _read_int(region_dir / "age")
        if not all(src == "exact" for src in (start_src, end_src, accesses_src, age_src)):
            continue
        if not all(isinstance(value, int) for value in (start, end, nr_accesses, age)):
            continue
        regions.append(_Region(start=start, end=end, nr_accesses=nr_accesses, age_aggr_intervals=age))
    return regions


def _region_sample_paths(tried_regions_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for region_dir in _numeric_dirs(tried_regions_dir):
        for name in ("start", "end", "nr_accesses", "age", "sz_filter_passed"):
            paths.append(region_dir / name)
    return paths


def _target_pids(targets_dir: Path) -> list[int]:
    out: list[int] = []
    for target_dir in _numeric_dirs(targets_dir):
        pid, src = _read_int(target_dir / "pid_target")
        if src == "exact" and isinstance(pid, int):
            out.append(pid)
    return out


def _single_target_entity(target_pids: list[int], proc_root: Path) -> EntityKey | None:
    entities = {entity_key for pid in target_pids if (entity_key := _pid_entity_key(proc_root, pid)) is not None}
    if len(entities) != 1:
        return None
    return next(iter(entities))


def _pid_entity_key(proc_root: Path, pid: int) -> EntityKey | None:
    result = read_text(proc_root / str(pid) / "cgroup")
    if result.value is None:
        return None
    for line in str(result.value).splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        if parts[0] == "0" or parts[1] == "":
            path = parts[2].strip()
            if path == "/":
                return ""
            return path.lstrip("/")
    return None


def _cgroup_pids(cgroup_root: Path, entity_key: EntityKey) -> set[int] | None:
    cgroup_path = cgroup_root if entity_key == "" else cgroup_root / entity_key
    procs = read_text(cgroup_path / "cgroup.procs")
    if procs.value is None:
        return None
    out: set[int] = set()
    for line in str(procs.value).splitlines():
        value = parse_int_text(line)
        if value is not None:
            out.add(value)
    return out


def _sample_age_seconds(paths: list[Path], now: float) -> float | None:
    newest_mtime: float | None = None
    for path in paths:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        newest_mtime = mtime if newest_mtime is None else max(newest_mtime, mtime)
    if newest_mtime is None:
        return None
    return max(0.0, now - newest_mtime)


def _numeric_dirs(path: Path) -> list[Path]:
    try:
        children = [child for child in path.iterdir() if child.is_dir() and child.name.isdigit()]
    except OSError:
        return []
    return sorted(children, key=lambda child: int(child.name))


def _numeric_name(path: Path) -> int | None:
    return int(path.name) if path.name.isdigit() else None


def _read_text_value(path: Path) -> str | None:
    result = read_text(path)
    return None if result.value is None else str(result.value).strip()


def _read_int(path: Path) -> tuple[int | None, MetricSource]:
    result = read_text(path)
    if result.value is None:
        return None, result.src
    value = parse_int_text(str(result.value))
    return (value, "exact") if value is not None else (None, "unavail_kernel")


def _root_unavailable_src(damon_root: Path) -> MetricSource:
    if damon_root.exists():
        nr_kdamonds = read_text(damon_root / "nr_kdamonds")
        if nr_kdamonds.value is None and nr_kdamonds.src == "unavail_perm":
            return "unavail_perm"
    return "unavail_kernel"
