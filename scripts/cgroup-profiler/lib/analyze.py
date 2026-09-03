"""Turn raw samples into an :class:`Analysis`: reshape, resample, correlate,
find changepoints, and propose changes — all through pandas/ruptures, never
by hand.

``Series``, ``Phase``, ``Event``, ``Proposal`` and ``Analysis`` live in
``lib.model``; this module only produces them.

One frame carries the whole pipeline. :func:`to_frame` flattens
``samples.jsonl`` into a wide :class:`pandas.DataFrame` — a
``TimedeltaIndex`` of ``mono`` seconds, and one column per ``(target,
metric)`` pair, where ``target`` is the cgroup path as it appears in the
sample and ``metric`` is a dotted field name. Counters (``cpu.usage_usec``,
``io.*bytes``, the workingset-refault fields) are turned into per-second
rates in the same pass, because that is still a reshape — a single
vectorised ``diff()/dt`` — not a second stage a caller has to remember to
run. Everything downstream (:func:`build_series`, :func:`detect_changepoints`,
:func:`correlate`, the phase/target summaries in :func:`build`) reads that
one frame; nothing here re-derives a value pandas already computed.

A cgroup path only becomes a target *key* (``"gate"``, ``"soulmask"``) inside
:func:`build`, once the manifest is available to say which key a path belongs
to. ``to_frame``/``build_series`` know nothing about the manifest and use the
raw path as the entity identity, which keeps them testable in isolation.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd
import ruptures as rpt

from . import util
from .model import Analysis, Event, Mark, Phase, Proposal, Series
from .phases import phases_from_marks
from .targets import path_to_slice


# ── metric registry ─────────────────────────────────────────────────────────
# Raw flattened key -> (unit, panel group, human label). Only metrics named
# here become a chartable Series; everything else stays in the frame (usable
# by correlate/detect_changepoints) but is not surfaced — a panel with every
# raw counter a kernel exposes helps nobody.
_GAUGES: Dict[str, Tuple[str, str, str]] = {
    "mem.current": ("bytes", "memory", "memory.current"),
    "mem.swap_current": ("bytes", "swap", "memory.swap.current"),
    "mem.zswap_current": ("bytes", "swap", "memory.zswap.current"),
    "memstat.anon": ("bytes", "memory", "anon"),
    "memstat.file": ("bytes", "memory", "file"),
    "psi_mem.some_avg10": ("pct", "pressure", "memory PSI (some, 10s)"),
    "psi_cpu.some_avg10": ("pct", "pressure", "cpu PSI (some, 10s)"),
    "psi_io.some_avg10": ("pct", "pressure", "io PSI (some, 10s)"),
    "pids.current": ("count", "pids", "pids.current"),
}

_RATES: Dict[str, Tuple[str, str, str]] = {
    "cpu.cores": ("cores", "cpu", "CPU cores used"),
    "io.rbytes_per_s": ("bytes/s", "io", "IO read rate"),
    "io.wbytes_per_s": ("bytes/s", "io", "IO write rate"),
    "faults.rfz_per_s": ("count/s", "faults", "zswap refault-in rate (rfz/s)"),
    "faults.rff_per_s": ("count/s", "faults", "file refault rate (rff/s)"),
    "faults.rfd_per_s": ("count/s", "faults", "disk refault rate (rfd/s)"),
}

_CHART_META: Dict[str, Tuple[str, str, str]] = {**_GAUGES, **_RATES}

# DESIGN.md §4.9a: additive units may be summed when folding a descendant
# fan-out; non-additive ones must not be (a PSI percentage or a ratio is not
# meaningful added to another one — the max is the only honest fold).
_ADDITIVE_UNITS = {"bytes", "bytes/s", "count", "count/s", "cores"}
_NON_ADDITIVE_UNITS = {"pct", "ratio"}

_MIN_GAP_DEFAULT = 5.0
_MIN_CORR_N = 30  # below this, correlate() still reports r but callers must not call it a finding.

# The adaptive sampler (DESIGN.md §4.4) swings cadence from hot_interval
# (0.25s default) to idle_interval (2s+) and back, so the raw frame's row
# spacing is irregular by construction. changepoint detection, correlation,
# and phase/target statistics all implicitly assume comparable time-per-row —
# a hot burst would otherwise dominate a correlation or bias a changepoint's
# apparent location purely by contributing more rows for the same duration.
# build() resamples onto this fixed grid before any of those three; charting
# (build_series) deliberately stays on the raw, native-resolution frame — that
# is a different consumer with its own separate resampling for report_html's
# resolution selector (DESIGN.md §4.11). 1s sits well below _MIN_GAP_DEFAULT
# (5s) so it cannot itself blur two changepoints the min-gap dedup would
# otherwise have kept distinct, and matches report_html's own finest
# post-raw resolution step.
_ANALYSIS_RESAMPLE_STEP = "1s"


# ── to_frame: reshape + rate derivation, entirely pandas ───────────────────

def _flatten_group(entry: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """One cgroup's sample entry -> ``{"group.field": value_or_None}``.

    ``io`` is keyed by device major:minor rather than field name, and is
    summed across devices: a target's total throughput is what the charts and
    the changepoint detector need, not any one disk. Non-numeric fields
    (there are none today, but a future kernel could add one) are dropped
    rather than raising, matching ``util.read_kv``'s tolerance.
    """
    out: Dict[str, Optional[float]] = {}
    for group, payload in (entry or {}).items():
        if not isinstance(payload, dict):
            continue
        if group == "io":
            devices = [fields for fields in payload.values() if isinstance(fields, dict)]
            if devices:
                all_keys: Set[str] = set()
                for fields in devices:
                    all_keys.update(fields.keys())
                for key in all_keys:
                    # A field missing from even one device that reported
                    # this tick makes the TOTAL unmeasurable, not "as if
                    # that device contributed 0" — summing only the devices
                    # that happened to have the field would silently invent
                    # a total lower than the real one. dict.get(key) folds
                    # "key absent" and "explicit None" into the same check.
                    values = [fields.get(key) for fields in devices]
                    if any(v is None for v in values):
                        continue
                    out[f"io.{key}"] = sum(values)
            continue
        for key, value in payload.items():
            if value is None or (isinstance(value, (int, float)) and not isinstance(value, bool)):
                out[f"{group}.{key}"] = value
    return out


def to_frame(samples: Iterable[Dict[str, Any]]) -> pd.DataFrame:
    """Flatten sample records into one wide frame on a TimedeltaIndex of ``mono``.

    Columns are a ``(target, metric)`` MultiIndex. A metric absent from a given
    tick — because the group was not sampled for that target, or because the
    counter behind it reset — is simply missing from that row, and pandas
    fills the gap with ``NaN`` on its own; nothing here ever substitutes a
    number for "not measurable".
    """
    samples = list(samples)
    # Every sample tick belongs on the index, even one where no cgroup at all
    # was readable that instant (every target mid-restart, say) — otherwise
    # that tick simply vanishes from the frame instead of becoming a row of
    # NaN, and a line chart would draw straight through the gap instead of
    # breaking at it.
    all_monos = sorted({float(record.get("mono", 0.0)) for record in samples})
    full_index = pd.to_timedelta(all_monos, unit="s")

    rows: List[Dict[str, Any]] = []
    for record in samples:
        mono = float(record.get("mono", 0.0))
        for target, entry in (record.get("cg") or {}).items():
            flat = _flatten_group(entry)
            flat["mono"] = mono
            flat["target"] = target
            rows.append(flat)

    empty_cols = pd.MultiIndex.from_tuples([], names=["target", "metric"])
    if not rows:
        return pd.DataFrame(index=full_index.rename("mono"), columns=empty_cols)

    long = pd.DataFrame(rows).set_index(["mono", "target"])

    wide = long.unstack("target")
    wide = wide.reorder_levels([1, 0], axis=1)
    wide.columns = wide.columns.set_names(["target", "metric"])
    wide = wide.sort_index(axis=1)
    wide.index = pd.to_timedelta(wide.index, unit="s")
    wide = wide.reindex(full_index)
    wide.index.name = "mono"

    return _add_rates(wide)


def _dt_seconds(index: pd.TimedeltaIndex) -> pd.Series:
    return index.to_series().diff().dt.total_seconds()


def _rate(counter: pd.Series, dt: pd.Series) -> pd.Series:
    """Per-second delta of a monotonic counter, vectorised.

    Mirrors ``util.rate``'s contract (a counter reset or a non-positive
    interval must surface as unknown, never as a spike) but as one pandas
    expression over the whole column instead of a Python loop — looping here
    would be exactly the hand-rolling this module exists to avoid.
    """
    delta = counter.diff()
    rate = delta / dt
    return rate.mask((delta < 0) | (dt <= 0))


def _rfd_per_s(refault_anon: pd.Series, zswpin: pd.Series, dt: pd.Series) -> pd.Series:
    """``max(0, Δworkingset_refault_anon − Δzswpin) / Δt`` — anon refaults
    actually served from disk swap, not zswap. Reproduces the host's
    ``soulmask-zswap-monitor.py`` formula exactly (DESIGN.md §4.1) so the two
    tools read side by side. A reset in *either* counter invalidates the tick
    entirely rather than only clamping to zero — a reset means "unknown", not
    "zero refaults".
    """
    d_anon = refault_anon.diff()
    d_zswpin = zswpin.diff()
    invalid = (d_anon < 0) | (d_zswpin < 0) | (dt <= 0)
    raw = ((d_anon - d_zswpin) / dt).clip(lower=0)
    return raw.mask(invalid)


def _add_rates(wide: pd.DataFrame) -> pd.DataFrame:
    dt = _dt_seconds(wide.index)
    targets = sorted({target for target, _ in wide.columns})
    new_cols: Dict[Tuple[str, str], pd.Series] = {}

    for target in targets:
        def column(metric: str) -> Optional[pd.Series]:
            key = (target, metric)
            return wide[key] if key in wide.columns else None

        usage = column("cpu.usage_usec")
        if usage is not None:
            new_cols[(target, "cpu.cores")] = _rate(usage, dt) / 1_000_000.0

        rbytes = column("io.rbytes")
        if rbytes is not None:
            new_cols[(target, "io.rbytes_per_s")] = _rate(rbytes, dt)

        wbytes = column("io.wbytes")
        if wbytes is not None:
            new_cols[(target, "io.wbytes_per_s")] = _rate(wbytes, dt)

        zswpin = column("memstat.zswpin")
        if zswpin is not None:
            new_cols[(target, "faults.rfz_per_s")] = _rate(zswpin, dt)

        refault_file = column("memstat.workingset_refault_file")
        if refault_file is not None:
            new_cols[(target, "faults.rff_per_s")] = _rate(refault_file, dt)

        refault_anon = column("memstat.workingset_refault_anon")
        if refault_anon is not None and zswpin is not None:
            new_cols[(target, "faults.rfd_per_s")] = _rfd_per_s(refault_anon, zswpin, dt)

    if not new_cols:
        return wide
    extra = pd.DataFrame(new_cols, index=wide.index)
    out = pd.concat([wide, extra], axis=1)
    out.columns = out.columns.set_names(["target", "metric"])
    return out.sort_index(axis=1)


# ── build_series ─────────────────────────────────────────────────────────

def build_series(df: pd.DataFrame) -> List[Series]:
    """One :class:`Series` per ``(target, metric)`` column this package knows
    how to chart (see ``_CHART_META``). ``None`` in ``v`` is the frame's
    ``NaN`` translated back — JSON has no NaN, and a renderer must see an
    explicit "not measurable" to know to break the line rather than draw
    through it.
    """
    out: List[Series] = []
    if df.empty or len(df.columns) == 0:
        return out
    t = df.index.total_seconds().tolist()
    for target, metric in df.columns:
        meta = _CHART_META.get(metric)
        if meta is None:
            continue
        unit, group, label_suffix = meta
        v = [None if (x is None or (isinstance(x, float) and math.isnan(x))) else float(x)
             for x in df[(target, metric)]]
        out.append(Series(
            key=metric,
            target=target,
            label=f"{util.short_label(target)} {label_suffix}",
            unit=unit,
            group=group,
            t=t,
            v=v,
            meta={"metric_label": label_suffix, "cgroup": target},
        ))
    return out


# ── descendant summarisation (DESIGN.md §4.9a) ──────────────────────────

def summarise_descendants(series: List[Series], named: Set[str], top_n: int = 6) -> List[Series]:
    """Fold a ``@follow``-discovered fan-out down to a report-sized panel.

    Measured on a real run: ``dev-background.slice@follow`` resolved 29
    cgroups into 435 series — every one of them plotted, legended, and
    tabled — because that tier also holds an unrelated 26-container stack
    nobody asked to look at. A named target or observer (``named``) is
    always plotted in full; everything else is grouped by ``(group, key)`` —
    same panel, same metric, i.e. exactly the lines that would otherwise
    share one chart — and past ``top_n`` by peak absolute value, the tail is
    folded into a single series so the chart stays readable and the report
    stays a report.
    """
    kept: List[Series] = [s for s in series if s.target in named]
    descendants = [s for s in series if s.target not in named]
    if not descendants:
        return series

    groups: Dict[Tuple[str, str], List[Series]] = {}
    for s in descendants:
        groups.setdefault((s.group, s.key), []).append(s)

    for (group, key), members in groups.items():
        if len(members) <= top_n:
            kept.extend(members)
            continue
        ranked = sorted(members, key=_peak_abs, reverse=True)
        kept.extend(ranked[:top_n])
        kept.append(_fold_descendants(ranked[top_n:], group, key))

    return kept


def _peak_abs(s: Series) -> float:
    finite = s.finite()
    return max((abs(v) for v in finite), default=0.0)


def _fold_descendants(members: List[Series], group: str, key: str) -> Series:
    """One series standing in for ``members``.

    Aligned by timestamp *value*, not list position — nothing upstream
    guarantees every folded member was sampled on an identical grid, and
    indexing by position would silently mismatch two members whose ``t``
    lists differ in length. At each timestamp, the fold uses whichever
    members are actually present there (DESIGN.md §4.9a: "uses the members
    that are present, and is None only when all are") — additive units sum
    them, non-additive ones (a PSI percentage, a ratio) take the max instead,
    because summing a saturation percentage across cgroups would invent a
    number nothing in the kernel ever produced.
    """
    unit = members[0].unit
    additive = unit in _ADDITIVE_UNITS
    all_t = sorted({tt for m in members for tt in m.t})
    lookups = [dict(zip(m.t, m.v)) for m in members]

    values: List[Optional[float]] = []
    for tt in all_t:
        present = [lu[tt] for lu in lookups if lu.get(tt) is not None]
        if not present:
            values.append(None)
        elif additive:
            values.append(sum(present))
        else:
            values.append(max(present))

    label = f"other ({len(members)} cgroups)" if additive else f"other (max of {len(members)})"
    return Series(
        key=key,
        target="other",
        label=label,
        unit=unit,
        group=group,
        t=all_t,
        v=values,
        role="subject",
        meta={"folded_count": len(members), "folded_targets": sorted(m.target for m in members)},
    )


# ── resampling ───────────────────────────────────────────────────────────

def resample_frame(df: pd.DataFrame, step: str) -> pd.DataFrame:
    """Onto a fixed grid, via ``resample().mean()`` — an empty bucket is
    ``NaN``, never filled, so a gap survives resampling instead of being
    smoothed over.
    """
    if df.empty:
        return df
    return df.resample(step).mean()


# ── changepoint detection ───────────────────────────────────────────────

def _auto_penalty(n: int) -> float:
    """A BIC-style ``pen`` from the sample count actually being fit, since
    ruptures' own ``pen`` does not scale itself.

    Measured directly against this package's fixtures: a fixed ``pen=8.0``
    (this function's previous default) finds **zero** breakpoints on the
    40-sample ``sample_records`` fixture — Pelt/rbf reports nothing at all
    until the penalty drops below ~6 for a signal that short — and would
    still be too small once a real run reaches thousands of samples, where a
    constant low enough to fire on 40 points fires on every noise wiggle.
    ``max(2.0, log(n))`` tracks BIC's own ``log(n)`` growth (n=40 → ~3.7,
    n=1000 → ~6.9, both verified empirically not to over- or under-fire
    against synthetic signals of those sizes) with a floor so a very short
    run does not get a near-zero penalty and a boundary at every tick.
    """
    return max(2.0, math.log(n)) if n > 0 else 2.0


def detect_changepoints(
    df: pd.DataFrame,
    columns: Sequence[Tuple[str, str]],
    penalty: Optional[float] = None,
    min_gap: float = _MIN_GAP_DEFAULT,
) -> List[float]:
    """Boundaries where the named columns' joint behaviour shifts, via
    ``ruptures.Pelt(model="rbf")`` on the z-scored matrix.

    Z-scoring is this function's own job, not ruptures': the rbf cost is
    scale-sensitive, and a memory series in bytes would otherwise swamp a PSI
    series in single-digit percent. Collapsing boundaries within ``min_gap``
    seconds of each other is a labelling decision, not a detection one, and is
    done after ruptures has already run.

    ``penalty=None`` (the default) computes :func:`_auto_penalty` from the
    number of rows actually being fit rather than using one fixed constant —
    see that function's docstring for the measurements behind why a fixed
    default cannot be right across both a short attach and an hours-long run.
    An explicit ``penalty`` always overrides the computed one.
    """
    cols = [c for c in columns if c in df.columns]
    if not cols or df.empty:
        return []
    sub = df.loc[:, cols]
    sub = sub.loc[:, sub.notna().any(axis=0)]  # an all-missing column would drop every row below
    sub = sub.dropna()
    if len(sub) < 4:
        return []

    std = sub.std(ddof=0)
    scaled = (sub - sub.mean()).div(std.replace(0, np.nan), axis=1)
    scaled = scaled.fillna(0.0)  # a genuinely zero-variance column carries no signal, not a gap

    effective_penalty = penalty if penalty is not None else _auto_penalty(len(sub))
    algo = rpt.Pelt(model="rbf").fit(scaled.to_numpy())
    breakpoints = algo.predict(pen=effective_penalty)
    times = sorted(sub.index[i].total_seconds() for i in breakpoints if i < len(sub))

    kept: List[float] = []
    for candidate in times:
        if not kept or candidate - kept[-1] >= min_gap:
            kept.append(candidate)
    return kept


def auto_phases(df: pd.DataFrame, known: Sequence[Phase], t_start: float, t_end: float) -> List[Phase]:
    """Fill in unlabelled structure around the marks a human actually placed.

    An explicit label always wins: every mark-derived boundary is kept
    exactly as given, and a ruptures boundary within ``min_gap`` of one is
    treated as the same event, not a second one. What survives becomes
    ``auto-1..auto-n``. When a surviving changepoint falls strictly inside a
    known phase, that phase is split — the leading piece keeps the original
    name (a naming scheme may add detail, it must never rename what someone
    already named), the trailing piece(s) become ``auto-N``.
    """
    known_sorted = sorted(known, key=lambda p: p.t0)
    candidate_cols = [c for c in df.columns if c[1] in _GAUGES] if not df.empty else []
    raw = detect_changepoints(df, candidate_cols) if candidate_cols else []

    bounds = sorted({t_start, t_end} | {p.t0 for p in known_sorted} | {p.t1 for p in known_sorted})
    survivors = [
        cp for cp in raw
        if t_start < cp < t_end and all(abs(cp - b) >= _MIN_GAP_DEFAULT for b in bounds)
    ]

    if not known_sorted:
        edges = sorted({t_start, t_end} | set(survivors))
        return [
            Phase(name=f"auto-{i + 1}", t0=edges[i], t1=edges[i + 1], source="auto")
            for i in range(len(edges) - 1)
        ]

    out: List[Phase] = []
    counter = 0
    for phase in known_sorted:
        inner = sorted(cp for cp in survivors if phase.t0 < cp < phase.t1)
        if not inner:
            out.append(phase)
            continue
        edges = [phase.t0, *inner, phase.t1]
        out.append(Phase(name=phase.name, t0=edges[0], t1=edges[1],
                          source=phase.source, meta=dict(phase.meta)))
        for i in range(1, len(edges) - 1):
            counter += 1
            out.append(Phase(name=f"auto-{counter}", t0=edges[i], t1=edges[i + 1], source="auto"))
    return out


# ── correlation ──────────────────────────────────────────────────────────

def correlate(df: pd.DataFrame, observers: Sequence[str], subjects: Sequence[str]) -> List[Dict[str, Any]]:
    """Pearson correlation (``Series.corr``, pandas' own) between each
    observer's pressure signals and each subject's activity rates, on the
    shared grid ``df`` already sits on.

    Always reports ``n`` next to ``r``: a handful of overlapping samples is
    not a trend, and the caller (the report) needs the count to say so — this
    function's job is only to not hide it.

    A perfectly constant series (observed on a real run: an idle target with
    zero variance the whole window) has an undefined Pearson coefficient —
    0/0 — which numpy computes as ``NaN`` while also raising a
    ``RuntimeWarning: invalid value encountered in divide``. The ``NaN`` is
    already handled below (``r`` comes back ``None``, never a fabricated 0.0
    or 1.0); ``np.errstate`` only silences the warning so a real run's output
    is not spammed by an expected, already-handled condition.
    """
    obs_metrics = ("faults.rfd_per_s", "psi_mem.some_avg10")
    subj_metrics = ("mem.current", "io.rbytes_per_s", "io.wbytes_per_s")
    out: List[Dict[str, Any]] = []
    if df.empty:
        return out
    for observer in observers:
        for obs_metric in obs_metrics:
            obs_col = (observer, obs_metric)
            if obs_col not in df.columns:
                continue
            for subject in subjects:
                if subject == observer:
                    continue
                for subj_metric in subj_metrics:
                    subj_col = (subject, subj_metric)
                    if subj_col not in df.columns:
                        continue
                    pair = df.loc[:, [obs_col, subj_col]].dropna()
                    n = len(pair)
                    r: Optional[float] = None
                    if n >= 2:
                        with np.errstate(invalid="ignore", divide="ignore"):
                            value = pair[obs_col].corr(pair[subj_col])
                        if value is not None and not (isinstance(value, float) and math.isnan(value)):
                            r = float(value)
                    out.append({
                        "observer": observer,
                        "subject": subject,
                        "metric": f"{obs_metric}~{subj_metric}",
                        "r": r,
                        "n": n,
                    })
    return out


# ── per-phase / per-target summaries ────────────────────────────────────

def _none_if_nan(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        if math.isnan(value):
            return None
    except TypeError:
        return None
    return float(value)


def _phase_stats(df: pd.DataFrame, phases: Sequence[Phase]) -> List[Dict[str, Any]]:
    """Per-phase mean/max/n for every chartable metric, via ``groupby`` over
    a phase column built with ``pd.cut`` — the exact idiom DESIGN.md asks for,
    rather than slicing the frame once per phase in a loop.
    """
    if df.empty or not phases:
        return []
    ordered = sorted(phases, key=lambda p: p.t0)
    edges = [ordered[0].t0] + [p.t1 for p in ordered]
    labels = [p.name for p in ordered]
    seconds = pd.Series(df.index.total_seconds(), index=df.index)
    phase_col = pd.cut(seconds, bins=edges, labels=labels, right=False, include_lowest=True, duplicates="drop")

    grouped = df.groupby(phase_col, observed=True)
    means = grouped.mean()
    maxes = grouped.max()
    counts = grouped.count()

    out: List[Dict[str, Any]] = []
    for phase_name in means.index:
        for target, metric in df.columns:
            if metric not in _CHART_META:
                continue
            n = int(counts.loc[phase_name, (target, metric)])
            if n == 0:
                continue
            out.append({
                "phase": str(phase_name),
                "target": target,
                "metric": metric,
                "mean": _none_if_nan(means.loc[phase_name, (target, metric)]),
                "max": _none_if_nan(maxes.loc[phase_name, (target, metric)]),
                "n": n,
            })
    return out


def _target_stats(df: pd.DataFrame) -> List[Dict[str, Any]]:
    if df.empty:
        return []
    out: List[Dict[str, Any]] = []
    for target, metric in df.columns:
        if metric not in _CHART_META:
            continue
        col = df[(target, metric)].dropna()
        if col.empty:
            continue
        out.append({
            "target": target,
            "metric": metric,
            "mean": float(col.mean()),
            "max": float(col.max()),
            "min": float(col.min()),
            "n": int(col.count()),
        })
    return out


def _resolve_mark_times(marks: List[Mark], started: float, t_start: float, t_end: float) -> List[Mark]:
    """Backfill ``mono`` on marks that only carry wall-clock ``t``.

    ``phases.emit_mark`` deliberately never sets ``mono`` — its own docstring
    says a mark from a foreign container has no idea when *this* run's
    monotonic clock started, and that a caller holding the manifest can
    resolve it later. ``build()`` is that caller. Skipping this step is what
    let epoch seconds (``t``, ~1.7e9) and monotonic run seconds (``mono``, a
    handful to a few thousand) land in the same boundary list, which is what
    made ``pd.cut`` see non-monotonic bin edges in ``_phase_stats`` on every
    real wrapper-mode run — confirmed against an actual crash.

    A resolved time outside ``[t_start, t_end]`` — a mark written before the
    collector attached, or after it stopped, both observed live — is clamped
    into the window rather than left to produce a phase that starts after
    the run supposedly ended.
    """
    out: List[Mark] = []
    for m in marks:
        mono = m.mono if m.mono is not None else (m.t - started)
        mono = util.clamp(mono, t_start, t_end)
        out.append(replace(m, mono=mono))
    return out


def _dedupe_simultaneous_phase_marks(marks: List[Mark]) -> List[Mark]:
    """Collapse ``phase`` marks that resolve to the exact same instant.

    Observed live: ``cgprofile run`` emits ``"<cmd>:done"`` immediately
    followed by ``"settle"``, and ``time.time()`` genuinely returns the same
    value for both on a fast host. Folding both into phase boundaries would
    produce a zero-width phase — no sample can ever land inside a span with
    no duration, and a zero-width span also breaks ``pd.cut``'s bins-vs-
    labels bookkeeping, not just its monotonicity check.

    Only the *last* mark at a shared instant survives: it is what actually
    describes the run from that instant on. Marks are sorted by resolved
    position first, so "last" means "the one emitted later", not "whichever
    happened to be later in an unordered read".
    """
    ordered = sorted(marks, key=lambda m: m.mono if m.mono is not None else m.t)
    out: List[Mark] = []
    for m in ordered:
        if (m.kind == "phase" and out and out[-1].kind == "phase"
                and out[-1].mono == m.mono):
            out[-1] = m
        else:
            out.append(m)
    return out


# ── build ────────────────────────────────────────────────────────────────

def _read_safe(run: Any, name: str) -> List[Dict[str, Any]]:
    try:
        return list(run.read(name))
    except (FileNotFoundError, OSError):
        return []


def _remap_targets(df: pd.DataFrame, by_cgroup: Dict[str, Dict[str, Any]]) -> pd.DataFrame:
    """Cgroup path -> target key on the column index, once the manifest is
    available to say what the key is. Everything before this point in the
    pipeline (``to_frame``, ``build_series`` when called standalone) is
    manifest-free and only ever sees the raw path.
    """
    if df.empty:
        return df
    out = df.copy()
    out.columns = pd.MultiIndex.from_tuples(
        [(by_cgroup[target]["key"] if target in by_cgroup else target, metric)
         for target, metric in df.columns],
        names=["target", "metric"],
    )
    return out.sort_index(axis=1)


def build(run: Any) -> Analysis:
    """``run`` needs only ``.path``, ``.read(name)`` and ``.read_manifest()``
    (the shape ``store.RunDir`` promises in DESIGN.md §4.3) — analyze.py does
    not construct or import a ``RunDir`` itself, so it works against the real
    one once ``store.py`` lands and against any stand-in with the same shape
    today.
    """
    manifest = run.read_manifest()
    samples = _read_safe(run, "samples.jsonl")
    marks = [Mark.from_dict(m) for m in _read_safe(run, "marks.jsonl")]
    events = [Event.from_dict(e) for e in _read_safe(run, "events.jsonl")]

    df = to_frame(samples)
    # Everything below except build_series() (native-resolution charting,
    # kept on the raw frame on purpose) needs comparable time-per-row — see
    # _ANALYSIS_RESAMPLE_STEP's docstring.
    resampled = resample_frame(df, _ANALYSIS_RESAMPLE_STEP)

    monos = [float(s.get("mono", 0.0)) for s in samples]
    t_start = min(monos) if monos else float(manifest.get("started", 0.0) or 0.0)
    t_end = max(monos) if monos else t_start

    started = float(manifest.get("started", 0.0) or 0.0)
    marks = _dedupe_simultaneous_phase_marks(_resolve_mark_times(marks, started, t_start, t_end))

    known_phases = phases_from_marks(marks, t_start, t_end)
    phases = auto_phases(resampled, known_phases, t_start, t_end) if not resampled.empty else known_phases

    targets_meta = manifest.get("targets") or []
    by_cgroup = {t["cgroup"]: t for t in targets_meta if t.get("cgroup")}

    series = build_series(df)
    for s in series:
        meta = by_cgroup.get(s.target)
        if not meta:
            continue
        s.role = meta.get("role", "subject")
        label = meta.get("label") or meta.get("key") or s.target
        s.label = f"{label} {s.meta.get('metric_label', s.key)}"
        s.target = meta.get("key", s.target)

    named_keys = {t["key"] for t in targets_meta if t.get("key")}
    series = summarise_descendants(series, named_keys)

    df = _remap_targets(df, by_cgroup)
    resampled = _remap_targets(resampled, by_cgroup)

    observers = [t["key"] for t in targets_meta if t.get("role") == "observer" and t.get("key")]
    subjects = [t["key"] for t in targets_meta if t.get("role") != "observer" and t.get("key")]
    correlations = correlate(resampled, observers, subjects)

    analysis = Analysis(
        manifest=manifest,
        targets=targets_meta,
        phases=phases,
        events=events,
        series=series,
        per_phase=_phase_stats(resampled, phases),
        per_target=_target_stats(resampled),
        correlations=correlations,
        host=manifest.get("host") or {},
        limits=manifest.get("limits") or {},
    )
    analysis.proposals = make_proposals(analysis)
    return analysis


# ── proposals ────────────────────────────────────────────────────────────
# Every check below reads only Analysis.limits/host/targets — declared
# configuration already resolved by lib.limits, not the observed series — so
# every proposal they produce is "inferred": a logical consequence of what is
# configured, not something we watched go wrong. A check with nothing to
# point at returns nothing; it does not guess.
#
# manifest["limits"] is `{cgroup_path: {"resolved": {...}, "described": [...],
# "fingerprint": "..."}}` (schema decided by the coordinator, cgprofile.py's
# writer, over this module's earlier draft of it). "resolved" is a flat dict
# of `limits.Effective`'s own scalar fields (memory_max, memory_max_by,
# memory_high, memory_high_by, memory_swap_max, memory_swap_max_by,
# strict_min, recursive_min, strict_low, recursive_low, protection_mode,
# cpu_cores, cpu_cores_by, io_max, io_max_by, pids_max, warnings) — the
# tightest-wins caps and both protection readings already resolved across
# the whole ancestor chain by `lib.limits.effective()`. Re-deriving any of
# that here (walking ancestors, comparing declared values by hand) would be
# exactly the hand-rolling this package's design forbids; every check below
# only ever reads fields `effective()` already computed.
#
# There is no "own, undressed" LimitSet in this schema (only the resolved,
# chain-wide view) — `_check_pinned` reads the *governing* protection value
# (`strict_min`/`strict_low` under strict mode, `recursive_min`/
# `recursive_low` under recursive) rather than "what the leaf itself
# declares", which DESIGN.md's wording ("its own memory.min") suggests but
# this schema cannot supply; see that check's docstring.
#
# An older run's manifest may instead carry the bare `limits.describe()`
# list directly (`{cgroup_path: [str, ...]}`, no "resolved" key at all) —
# `_effective_limits` treats that exactly like no limit snapshot: every
# check returns nothing rather than parsing a human sentence for a number.

def _effective_limits(analysis: Analysis) -> Dict[str, Dict[str, Any]]:
    """``{cgroup: resolved-limits-dict}`` — only entries that actually carry
    a structured ``"resolved"`` dict; everything else (a bare
    ``describe()`` list, a dict missing "resolved", no limit snapshot at
    all) contributes nothing rather than being guessed at.
    """
    limits = analysis.limits or {}
    out: Dict[str, Dict[str, Any]] = {}
    for path, entry in limits.items():
        if not isinstance(entry, dict):
            continue
        resolved = entry.get("resolved")
        if isinstance(resolved, dict):
            out[path] = resolved
    return out


def _governing_floor(eff: Dict[str, Any]) -> Optional[int]:
    """The protection value that actually holds for this cgroup right now:
    the strict reading under strict mode (a zero anywhere in the chain
    wins), the recursive reading under recursive mode. Falls back from
    ``*_min`` to ``*_low`` when the min-side reading is zero/absent, since
    either can be the thing holding a target's floor up.
    """
    mode = eff.get("protection_mode")
    min_key, low_key = ("strict_min", "strict_low") if mode == "strict" else ("recursive_min", "recursive_low")
    return eff.get(min_key) or eff.get(low_key)


def _check_oversubscription(analysis: Analysis) -> List[Proposal]:
    """A slice whose children's *effective* ``memory.max`` sums past host
    RAM: nothing stops them all filling up at once. Uses the resolved cap
    (tightest across each child's own chain), not just what a child declares
    for itself, since an ancestor can bind a child tighter than its own file
    says."""
    effective = _effective_limits(analysis)
    total_ram = (analysis.host or {}).get("MemTotal")
    if not effective or not total_ram:
        return []
    children_by_parent: Dict[str, List[str]] = {}
    for path in effective:
        if path == "/":
            continue
        parent = util.ancestors(path)[1]
        children_by_parent.setdefault(parent, []).append(path)

    out: List[Proposal] = []
    for parent, children in sorted(children_by_parent.items()):
        if len(children) < 2:
            continue
        maxima = [(child, effective[child].get("memory_max")) for child in sorted(children)]
        if any(value is None for _, value in maxima):
            continue  # an uncapped child makes "the sum" meaningless as a hard bound
        total = sum(value for _, value in maxima)
        if total <= total_ram:
            continue
        slice_name = path_to_slice(parent) or parent
        out.append(Proposal(
            id=f"oversubscribed:{parent}",
            severity="serious",
            title=f"{util.short_label(parent)} children's memory.max sums past host RAM",
            rationale=(
                f"{util.fmt_bytes(total)} of effective memory.max across {len(children)} "
                f"children under {parent}, against {util.fmt_bytes(total_ram)} of host RAM."
            ),
            evidence=[f"{child}: memory.max={util.fmt_bytes(value)}" for child, value in maxima],
            change=[
                f"systemctl set-property {slice_name} MemoryMax={util.fmt_bytes(total_ram)}",
                f"or reduce individual children so their memory.max total <= {util.fmt_bytes(total_ram)}",
            ],
            confidence="inferred",
        ))
    return out


def _check_recursiveprot_gap(analysis: Analysis) -> List[Proposal]:
    """Some ancestor's protection is being discarded for a cgroup under
    strict mode: ``lib.limits.effective`` already resolves both readings
    *and* the mode itself (from the real mount flags it read live — this
    schema carries no separate mount-flags field, nor needs one), so the
    gap is just ``recursive_{min,low} > strict_{min,low}`` while
    ``protection_mode`` is ``"strict"`` for that cgroup. No ancestor walk
    happens here; ``effective()`` already did it.
    """
    effective = _effective_limits(analysis)
    out: List[Proposal] = []
    for path in sorted(effective):
        eff = effective[path]
        if eff.get("protection_mode") != "strict":
            continue  # recursive mode (or unknown): nothing being discarded here
        strict_min = eff.get("strict_min") or 0
        recursive_min = eff.get("recursive_min") or 0
        strict_low = eff.get("strict_low") or 0
        recursive_low = eff.get("recursive_low") or 0
        if recursive_min <= strict_min and recursive_low <= strict_low:
            continue  # nothing upstream is being discarded for this cgroup
        evidence = [
            f"{path}: strict_min={util.fmt_bytes(strict_min)}, recursive_min={util.fmt_bytes(recursive_min)}, "
            f"strict_low={util.fmt_bytes(strict_low)}, recursive_low={util.fmt_bytes(recursive_low)}",
        ]
        evidence.extend(eff.get("warnings") or [])
        out.append(Proposal(
            id=f"recursiveprot-gap:{path}",
            severity="warn",
            title=f"{util.short_label(path)} loses ancestor protection under strict mode",
            rationale=(
                "cgroup2 is mounted without memory_recursiveprot, so protection is strict "
                f"(a zero anywhere in the chain wins): {path} would effectively have "
                f"memory.min={util.fmt_bytes(recursive_min)} / memory.low={util.fmt_bytes(recursive_low)} "
                "under recursive protection, but only "
                f"memory.min={util.fmt_bytes(strict_min)} / memory.low={util.fmt_bytes(strict_low)} under strict."
            ),
            evidence=evidence,
            change=[
                f"declare memory.min/memory.low on {path} itself, matching what the ancestor intends",
                "or mount cgroup2 with memory_recursiveprot so the ancestor value applies",
            ],
            confidence="inferred",
        ))
    return out


def _check_pinned(analysis: Analysis) -> List[Proposal]:
    """A target pinned between the protection that actually governs it and
    its *effective* ``memory.high``: little to no room to actually respond
    to pressure before the high-throttle boundary. The floor is
    :func:`_governing_floor` (this schema has no separate "what the leaf
    itself declares" field — only the resolved, chain-wide reading — so the
    check uses whichever reading is the one really in force); the ceiling
    is the resolved value, since an ancestor's tighter high is what actually
    throttles it regardless of what the leaf's own file says.
    """
    effective = _effective_limits(analysis)
    out: List[Proposal] = []
    for path in sorted(effective):
        eff = effective[path]
        floor = _governing_floor(eff)
        ceiling = eff.get("memory_high")
        if not floor or not ceiling or floor >= ceiling:
            continue
        headroom = (ceiling - floor) / ceiling
        if headroom > 0.15:
            continue  # enough room between the floor and the ceiling to breathe
        out.append(Proposal(
            id=f"pinned:{path}",
            severity="warn",
            title=f"{util.short_label(path)} is pinned between its protection and its ceiling",
            rationale=(
                f"governing memory.min/low={util.fmt_bytes(floor)} and effective "
                f"memory.high={util.fmt_bytes(ceiling)} leave only "
                f"{util.fmt_bytes(ceiling - floor)} ({headroom:.0%}) of headroom "
                "before memory.high starts throttling reclaim."
            ),
            evidence=[f"{path}: governing floor={util.fmt_bytes(floor)}, effective ceiling={util.fmt_bytes(ceiling)}"],
            change=[f"widen memory.high for {path}, or lower its protection, so real headroom exists between the two"],
            confidence="inferred",
        ))
    return out


def _check_shared_tier(analysis: Analysis) -> List[Proposal]:
    """A subject that is itself one shared tier (a slice with its own
    memory/IO budget) whose children include a cgroup that is not one of
    this run's declared targets: that co-tenant's memory/IO load counts
    toward the same budget and the same PSI numbers as the subject's own.
    """
    effective = _effective_limits(analysis)
    if not effective:
        return []
    target_paths = {t.get("cgroup") for t in analysis.targets if t.get("cgroup")}
    subject_paths = sorted(
        t.get("cgroup") for t in analysis.targets
        if t.get("cgroup") and t.get("role", "subject") != "observer"
    )
    children_by_parent: Dict[str, List[str]] = {}
    for path in effective:
        if path == "/":
            continue
        children_by_parent.setdefault(util.ancestors(path)[1], []).append(path)

    out: List[Proposal] = []
    for subject in subject_paths:
        co_tenants = sorted(
            p for p in children_by_parent.get(subject, []) if p not in target_paths
        )
        if not co_tenants:
            continue
        out.append(Proposal(
            id=f"shared-tier:{subject}",
            severity="info",
            title=f"{util.short_label(subject)} shares its tier with an unrelated stack",
            rationale=(
                f"{', '.join(util.short_label(s) for s in co_tenants)} lives under {subject} "
                "but is not one of this run's targets — its memory/IO load counts toward the "
                "same cgroup budget and the same PSI numbers as the subject's."
            ),
            evidence=[f"{subject} children in the limit snapshot: {sorted(children_by_parent[subject])}"],
            change=[
                f"move {', '.join(util.short_label(s) for s in co_tenants)} to its own slice, "
                f"or account for it when sizing {subject}",
            ],
            confidence="inferred",
        ))
    return out


def make_proposals(analysis: Analysis) -> List[Proposal]:
    """Every check names its own evidence and confidence (see each helper's
    docstring); this just runs all of them and returns what the data
    supports. A run with no limit snapshot (``analysis.limits`` empty)
    produces no proposals — silence, not a guess.
    """
    proposals: List[Proposal] = []
    proposals.extend(_check_oversubscription(analysis))
    proposals.extend(_check_recursiveprot_gap(analysis))
    proposals.extend(_check_pinned(analysis))
    proposals.extend(_check_shared_tier(analysis))
    return proposals
