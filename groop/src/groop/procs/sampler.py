"""ProcessSampler — the P90 bounded CPU/I/O-hot process orchestrator.

Each tick this reads cheap ``/proc/PID/stat`` + ``/proc/PID/io`` baselines for
every visible PID (Required contract 2), selects the bounded D-019 union
(``groop.procs.candidates``), enriches only that retained set with the
expensive fields (Required contract 4), and emits one ``Frame`` — a SEPARATE
stream from the main cgroup collector's ``Frame`` — plus a
:class:`ProcessCoverage` telemetry record (Required contract 3). The retained
history is exposed through :class:`ProcessFrameSource`, a ``groop.query``
``FrameSource``, so current/raw/summary projections run over process history
through the existing P88 engine (Required contract 5) with no second
aggregation engine.
"""

from __future__ import annotations

import pwd
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from groop.config import ProcessConfig
from groop.model import Entity, EntityFrame, EntityKey, Frame, MetricValue
from groop.procs import procfs
from groop.procs.candidates import CandidateReasons, select_candidates
from groop.procs.identity import ProcessKey, read_boot_id
from groop.procs.owners import join_owner
from groop.query.source import FrameSource, SourceFrame, SourceProvenance

_NUMERIC_METRIC_NAMES: tuple[str, ...] = (
    "proc_cpu_pct",
    "proc_cpu_host_pct",
    "proc_rss",
    "proc_vsz",
    "proc_swap",
    "proc_io_r_bps",
    "proc_io_w_bps",
    "proc_io_cancelled_w_bps",
    "proc_io_delay_per_s",
    "proc_minflt_per_s",
    "proc_majflt_per_s",
    "proc_ctxt_vol_per_s",
    "proc_ctxt_invol_per_s",
    "proc_threads",
    "proc_elapsed_s",
)


@dataclass(frozen=True)
class ProcessCoverage:
    eligible_count: int
    candidate_count: int
    sampled_count: int
    omitted_count: int
    omitted_reasons: dict[str, int]
    scan_duration_s: float
    warm_up_coverage: float

    def to_jsonable(self) -> dict[str, object]:
        return {
            "eligible_count": self.eligible_count,
            "candidate_count": self.candidate_count,
            "sampled_count": self.sampled_count,
            "omitted_count": self.omitted_count,
            "omitted_reasons": dict(sorted(self.omitted_reasons.items())),
            "scan_duration_s": self.scan_duration_s,
            "warm_up_coverage": self.warm_up_coverage,
        }


@dataclass
class ProcessTick:
    frame: Frame
    coverage: ProcessCoverage


@dataclass
class _Baseline:
    key: ProcessKey
    ppid: int | None
    comm: str | None
    state: str | None
    utime: int | None
    stime: int | None
    minflt: int | None
    majflt: int | None
    blkio_ticks: int | None
    read_bytes: int | None
    write_bytes: int | None
    cancelled_write_bytes: int | None
    stat_src: str
    io_src: str


@dataclass
class _Rates:
    cpu_pct: float | None = None
    io_combined_bps: float | None = None
    read_bps: float | None = None
    write_bps: float | None = None
    cancelled_w_bps: float | None = None
    minflt_per_s: float | None = None
    majflt_per_s: float | None = None
    blkio_per_s: float | None = None


def _delta(cur: int | None, prev: int | None) -> int | None:
    if cur is None or prev is None or cur < prev:
        return None
    return cur - prev


class ProcessSampler:
    """Bounded CPU-hot/I/O-hot process candidate sampler (D-013/D-019)."""

    def __init__(
        self,
        *,
        config: ProcessConfig | None = None,
        proc_root: Path = Path("/proc"),
        now: Callable[[], float] | None = None,
        pinned_pids: frozenset[int] | None = None,
        history_capacity: int = 120,
        default_interval_s: float = 5.0,
    ) -> None:
        self.config = config or ProcessConfig()
        self.proc_root = proc_root
        self.now = now or time.time
        self.pinned_pids = pinned_pids or frozenset()
        self._default_interval_s = default_interval_s
        self._boot_id = read_boot_id(proc_root)
        self._prev: dict[ProcessKey, _Baseline] = {}
        self._prev_status: dict[ProcessKey, procfs.StatusFields] = {}
        self._prev_ts: float | None = None
        self._hot_since: dict[ProcessKey, float] = {}
        self._last_retained: set[ProcessKey] = set()
        self._history: list[tuple[int, Frame]] = []
        self._history_capacity = max(1, history_capacity)
        self._seq = 0
        self._evicted = False

    def set_pinned(self, pinned_pids: frozenset[int]) -> None:
        self.pinned_pids = pinned_pids

    def frame_source(self) -> ProcessFrameSource:
        source = ProcessFrameSource(list(self._history))
        source.evicted = self._evicted
        return source

    # -- one tick -----------------------------------------------------

    def sample(self, entities: dict[EntityKey, Entity] | None = None) -> ProcessTick:
        ts = self.now()
        interval_s = self._default_interval_s if self._prev_ts is None else max(0.0, ts - self._prev_ts)
        entities = entities or {}
        scan_start = time.monotonic()

        pids = procfs.discover_pids(self.proc_root)
        eligible_count = len(pids)
        baselines: dict[ProcessKey, _Baseline] = {}
        for pid in pids:
            pid_dir = self.proc_root / str(pid)
            stat, stat_src = procfs.read_stat(pid_dir)
            if stat is None:
                continue  # vanished/raced mid-scan this tick
            key = ProcessKey(pid=pid, start_ticks=stat.starttime, boot_id=self._boot_id)
            io, io_src = procfs.read_io(pid_dir)
            baselines[key] = _Baseline(
                key=key,
                ppid=stat.ppid,
                comm=stat.comm,
                state=stat.state,
                utime=stat.utime,
                stime=stat.stime,
                minflt=stat.minflt,
                majflt=stat.majflt,
                blkio_ticks=stat.blkio_ticks,
                read_bytes=io.read_bytes if io else None,
                write_bytes=io.write_bytes if io else None,
                cancelled_write_bytes=io.cancelled_write_bytes if io else None,
                stat_src=stat_src,
                io_src=io_src,
            )

        rates = self._compute_rates(baselines, interval_s)
        cpu_rate = {k: r.cpu_pct for k, r in rates.items() if r.cpu_pct is not None}
        io_rate = {k: r.io_combined_bps for k, r in rates.items() if r.io_combined_bps is not None}

        # Give any process that vanished but was retained (pinned or hot) last
        # tick one more chance to resolve/appear as an explicit vanished row
        # instead of silently disappearing (oracle O6), and to keep a pinned
        # pid's key resolvable even after it exits.
        vanished_relevant = (self._last_retained | set(self._hot_since)) - set(baselines)
        all_keys = list(baselines) + list(vanished_relevant)

        selection = select_candidates(
            keys=all_keys,
            cpu_rate=cpu_rate,
            io_rate=io_rate,
            pinned_pids=self.pinned_pids,
            hot_since=self._hot_since,
            now=ts,
            config=self.config,
            eligible_count=eligible_count,
        )
        self._hot_since = selection.hot_since

        ncpus = procfs.count_logical_cpus(self.proc_root)
        boot_time = procfs.read_boot_time(self.proc_root)

        entity_frames: dict[EntityKey, EntityFrame] = {}
        warm = 0
        next_prev_status: dict[ProcessKey, procfs.StatusFields] = {}
        for key in selection.retained:
            baseline = baselines.get(key)
            present = baseline is not None
            eframe = self._build_entity_frame(
                key=key,
                baseline=baseline,
                rate=rates.get(key, _Rates()),
                reasons=selection.reasons[key],
                entities=entities,
                ts=ts,
                interval_s=interval_s,
                ncpus=ncpus,
                boot_time=boot_time,
                next_prev_status=next_prev_status,
            )
            entity_frames[key.entity_key()] = eframe
            if key in self._prev:
                warm += 1

        scan_duration = time.monotonic() - scan_start
        warm_up_coverage = (warm / len(selection.retained)) if selection.retained else 1.0
        omitted_reasons: dict[str, int] = {}
        for reason in selection.omitted_reason.values():
            omitted_reasons[reason] = omitted_reasons.get(reason, 0) + 1
        coverage = ProcessCoverage(
            eligible_count=eligible_count,
            candidate_count=selection.candidate_count,
            sampled_count=len(selection.retained),
            omitted_count=len(selection.omitted),
            omitted_reasons=omitted_reasons,
            scan_duration_s=scan_duration,
            warm_up_coverage=warm_up_coverage,
        )

        frame = Frame(
            schema_version=1,
            ts=ts,
            interval_s=interval_s,
            host={},
            entities=entity_frames,
            host_meta={"process_coverage": coverage.to_jsonable()},
        )

        self._prev = baselines
        self._prev_status = next_prev_status
        self._prev_ts = ts
        self._last_retained = set(selection.retained)
        self._history.append((self._seq, frame))
        self._seq += 1
        if len(self._history) > self._history_capacity:
            self._history.pop(0)
            self._evicted = True

        return ProcessTick(frame=frame, coverage=coverage)

    # -- helpers --------------------------------------------------------

    def _compute_rates(
        self, baselines: dict[ProcessKey, _Baseline], interval_s: float
    ) -> dict[ProcessKey, _Rates]:
        out: dict[ProcessKey, _Rates] = {}
        if interval_s <= 0:
            return out
        for key, cur in baselines.items():
            prev = self._prev.get(key)
            if prev is None:
                continue
            rates = _Rates()
            cpu_ticks = _delta(
                None if cur.utime is None or cur.stime is None else cur.utime + cur.stime,
                None if prev.utime is None or prev.stime is None else prev.utime + prev.stime,
            )
            if cpu_ticks is not None:
                rates.cpu_pct = (cpu_ticks / procfs.CLK_TCK) / interval_s * 100.0
            read_delta = _delta(cur.read_bytes, prev.read_bytes)
            write_delta = _delta(cur.write_bytes, prev.write_bytes)
            if read_delta is not None:
                rates.read_bps = read_delta / interval_s
            if write_delta is not None:
                rates.write_bps = write_delta / interval_s
            if read_delta is not None and write_delta is not None:
                rates.io_combined_bps = (read_delta + write_delta) / interval_s
            cancelled_delta = _delta(cur.cancelled_write_bytes, prev.cancelled_write_bytes)
            if cancelled_delta is not None:
                rates.cancelled_w_bps = cancelled_delta / interval_s
            minflt_delta = _delta(cur.minflt, prev.minflt)
            if minflt_delta is not None:
                rates.minflt_per_s = minflt_delta / interval_s
            majflt_delta = _delta(cur.majflt, prev.majflt)
            if majflt_delta is not None:
                rates.majflt_per_s = majflt_delta / interval_s
            blkio_delta = _delta(cur.blkio_ticks, prev.blkio_ticks)
            if blkio_delta is not None:
                rates.blkio_per_s = (blkio_delta / procfs.CLK_TCK) / interval_s
            out[key] = rates
        return out

    def _resolve_user(self, uid: int | None) -> str | None:
        if uid is None:
            return None
        try:
            return pwd.getpwuid(uid).pw_name
        except KeyError:
            return str(uid)

    def _build_entity_frame(
        self,
        *,
        key: ProcessKey,
        baseline: _Baseline | None,
        rate: _Rates,
        reasons: CandidateReasons,
        entities: dict[EntityKey, Entity],
        ts: float,
        interval_s: float,
        ncpus: int,
        boot_time: float | None,
        next_prev_status: dict[ProcessKey, procfs.StatusFields],
    ) -> EntityFrame:
        present = baseline is not None
        metrics: dict[str, MetricValue] = {}
        cgroup_key: str | None = None
        cmdline: str | None = None
        cmdline_src = "unavail_kernel"
        user: str | None = None
        status_src = "unavail_kernel"

        if not present:
            metrics["proc_present"] = MetricValue(0, "exact")
            for name in _NUMERIC_METRIC_NAMES:
                metrics[name] = MetricValue(None, "unavail_kernel")
            stale = self._prev.get(key)
            comm = stale.comm if stale else None
            ppid = stale.ppid if stale else None
            state = stale.state if stale else None
        else:
            metrics["proc_present"] = MetricValue(1, "exact")
            comm = baseline.comm
            ppid = baseline.ppid
            state = baseline.state
            pid_dir = self.proc_root / str(key.pid)

            cpu_src = "derived"
            metrics["proc_cpu_pct"] = MetricValue(rate.cpu_pct, cpu_src)
            host_pct = None if rate.cpu_pct is None else rate.cpu_pct / max(1, ncpus)
            metrics["proc_cpu_host_pct"] = MetricValue(host_pct, cpu_src)
            metrics["proc_io_r_bps"] = MetricValue(rate.read_bps, "derived" if baseline.io_src == "exact" else baseline.io_src)
            metrics["proc_io_w_bps"] = MetricValue(rate.write_bps, "derived" if baseline.io_src == "exact" else baseline.io_src)
            metrics["proc_io_cancelled_w_bps"] = MetricValue(rate.cancelled_w_bps, "derived" if baseline.io_src == "exact" else baseline.io_src)
            metrics["proc_io_delay_per_s"] = MetricValue(
                rate.blkio_per_s, "derived" if baseline.blkio_ticks is not None else "unavail_kernel"
            )
            metrics["proc_minflt_per_s"] = MetricValue(rate.minflt_per_s, "derived")
            metrics["proc_majflt_per_s"] = MetricValue(rate.majflt_per_s, "derived")

            if boot_time is not None and key.start_ticks is not None:
                elapsed = ts - (boot_time + key.start_ticks / procfs.CLK_TCK)
                metrics["proc_elapsed_s"] = MetricValue(max(0.0, elapsed), "exact")
            else:
                metrics["proc_elapsed_s"] = MetricValue(None, "unavail_kernel")

            status, status_src = procfs.read_status(pid_dir)
            if status is not None:
                metrics["proc_rss"] = MetricValue(status.vm_rss, "exact" if status.vm_rss is not None else "unavail_kernel")
                metrics["proc_vsz"] = MetricValue(status.vm_size, "exact" if status.vm_size is not None else "unavail_kernel")
                metrics["proc_swap"] = MetricValue(status.vm_swap, "exact" if status.vm_swap is not None else "unavail_kernel")
                metrics["proc_threads"] = MetricValue(status.threads, "exact" if status.threads is not None else "unavail_kernel")
                prev_status = self._prev_status.get(key)
                if prev_status is not None:
                    vol_delta = _delta(status.voluntary_ctxt_switches, prev_status.voluntary_ctxt_switches)
                    invol_delta = _delta(status.nonvoluntary_ctxt_switches, prev_status.nonvoluntary_ctxt_switches)
                    metrics["proc_ctxt_vol_per_s"] = MetricValue(
                        None if vol_delta is None or interval_s <= 0 else vol_delta / interval_s, "derived"
                    )
                    metrics["proc_ctxt_invol_per_s"] = MetricValue(
                        None if invol_delta is None or interval_s <= 0 else invol_delta / interval_s, "derived"
                    )
                else:
                    metrics["proc_ctxt_vol_per_s"] = MetricValue(None, "derived")
                    metrics["proc_ctxt_invol_per_s"] = MetricValue(None, "derived")
                next_prev_status[key] = status
                user = self._resolve_user(status.uid)
            else:
                for name in ("proc_rss", "proc_vsz", "proc_swap", "proc_threads", "proc_ctxt_vol_per_s", "proc_ctxt_invol_per_s"):
                    metrics[name] = MetricValue(None, status_src)

            cmdline, cmdline_src = procfs.read_cmdline(pid_dir)
            cgroup_key, cgroup_src = procfs.read_cgroup_path(pid_dir)
            if cgroup_key is None:
                cgroup_key = None

        owner = join_owner(cgroup_key, entities)
        process_block: dict[str, object] = {
            "ppid": ppid,
            "comm": comm,
            "cmdline": cmdline,
            "cmdline_available": cmdline_src == "exact",
            "user": user,
            "user_available": status_src == "exact",
            "state": state,
            "present": present,
            "reasons": list(reasons.as_tuple()),
            "cgroup_key": owner.cgroup_key,
            "unit": owner.unit,
            "slice": owner.slice_name,
            "docker": (
                {"cid": owner.docker_cid, "name": owner.docker_name, "image": owner.docker_image}
                if owner.docker_cid or owner.docker_name
                else None
            ),
            "ciu": (
                {"stack": owner.ciu_stack, "phase": owner.ciu_phase}
                if owner.ciu_stack is not None
                else None
            ),
        }

        entity = Entity(key=key.entity_key(), kind="process", parent=owner.cgroup_key)
        return EntityFrame(entity=entity, metrics=metrics, process=process_block)


class ProcessFrameSource(FrameSource):
    """``groop.query`` ``FrameSource`` over a :class:`ProcessSampler`'s in-memory
    retained-frame history (Required contract 5). No disk persistence — that is
    P91's scope; this is the bounded live/replay-in-memory boundary the cheap
    candidate loop keeps regardless of page visibility."""

    def __init__(self, entries: list[tuple[int, Frame]]) -> None:
        self._entries = list(entries)
        self.provenance = SourceProvenance(kind="process-sampler", detail={})
        self.evicted = False

    def iter_source_frames(self) -> Iterator[SourceFrame]:
        prev_seq: int | None = None
        for i, (seq, frame) in enumerate(self._entries):
            if i == 0:
                gap_before = self.evicted
            else:
                gap_before = prev_seq is not None and seq > prev_seq + 1
            yield SourceFrame(seq=seq, frame=frame, gap_before=gap_before)
            prev_seq = seq
