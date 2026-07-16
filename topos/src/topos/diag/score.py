from __future__ import annotations

from dataclasses import dataclass

from topos.config import ToposConfig, ThresholdBand
from topos.model import EntityFrame, MetricValue


@dataclass(frozen=True)
class ScoreBreakdown:
    score: int
    contributions: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class ScoreInput:
    key: str
    label: str
    metrics: tuple[str, ...]
    weight_key: str
    threshold_key: str | None
    default_band: ThresholdBand | None
    detail: str


_INPUTS = (
    ScoreInput(
        key="psi_mem_full_avg10",
        label="Memory PSI full",
        metrics=("psi_mem_full_avg10",),
        weight_key="psi_mem_full_avg10",
        threshold_key="psi_full_avg10",
        default_band=ThresholdBand(1.0, 2.0),
        detail="avg10 stall time",
    ),
    ScoreInput(
        key="psi_mem_some_avg10",
        label="Memory PSI some",
        metrics=("psi_mem_some_avg10",),
        weight_key="psi_mem_some_avg10",
        threshold_key="psi_some_avg10",
        default_band=ThresholdBand(5.0, 15.0),
        detail="avg10 partial stall time",
    ),
    ScoreInput(
        key="psi_io_full_avg10",
        label="I/O PSI full",
        metrics=("psi_io_full_avg10",),
        weight_key="psi_io_full_avg10",
        threshold_key="psi_full_avg10",
        default_band=ThresholdBand(1.0, 2.0),
        detail="avg10 full I/O stall time",
    ),
    ScoreInput(
        key="psi_io_some_avg10",
        label="I/O PSI some",
        metrics=("psi_io_some_avg10",),
        weight_key="psi_io_some_avg10",
        threshold_key="psi_some_avg10",
        default_band=ThresholdBand(5.0, 15.0),
        detail="avg10 partial I/O stall time",
    ),
    ScoreInput(
        key="psi_cpu_some_avg10",
        label="CPU PSI some",
        metrics=("psi_cpu_some_avg10",),
        weight_key="psi_cpu_some_avg10",
        threshold_key="psi_some_avg10",
        default_band=ThresholdBand(5.0, 15.0),
        detail="avg10 runnable pressure",
    ),
    ScoreInput(
        key="rf_d_per_s",
        label="Device anon refaults",
        metrics=("rf_d_per_s",),
        weight_key="rf_d_per_s",
        threshold_key="rf_d_per_s",
        default_band=ThresholdBand(1.0, 20.0),
        detail="anonymous refaults that missed zswap; backend may be disk, zram, or mixed according to host classification",
    ),
    ScoreInput(
        key="rf_f_per_s",
        label="File refaults",
        metrics=("rf_f_per_s",),
        weight_key="rf_f_per_s",
        threshold_key="rf_f_per_s",
        default_band=ThresholdBand(1.0, 10.0),
        detail="file-cache refault rate",
    ),
    ScoreInput(
        key="mem_events_high_per_s",
        label="memory.high events",
        metrics=("mem_events_high_per_s",),
        weight_key="mem_events_high_per_s",
        threshold_key="mem_events_high_per_s",
        default_band=ThresholdBand(0.1, 1.0),
        detail="throttle events per second",
    ),
    ScoreInput(
        key="mem_events_oom_kill_per_s",
        label="OOM kills",
        metrics=("mem_events_oom_kill_per_s",),
        weight_key="mem_events_oom_kill_per_s",
        threshold_key="mem_events_oom_kill",
        default_band=ThresholdBand(1.0, 1.0),
        detail="oom_kill events per second",
    ),
    ScoreInput(
        key="io_cap_saturation_pct",
        label="I/O cap saturation",
        metrics=("io_cap_saturation_pct",),
        weight_key="io_cap_saturation_pct",
        threshold_key="io_cap_saturation_pct",
        default_band=ThresholdBand(75.0, 95.0),
        detail="share of the configured io.max budget in use",
    ),
    ScoreInput(
        key="network_loss_pct",
        label="Network loss / retrans",
        metrics=("network_loss_pct",),
        weight_key="network_loss_pct",
        threshold_key="network_loss_pct",
        default_band=ThresholdBand(1.0, 5.0),
        detail="drops or retransmits attributable to this entity",
    ),
)


def score_entity(entity_frame: EntityFrame, config: ToposConfig) -> ScoreBreakdown:
    tier = entity_frame.entity.tier
    raw_items: list[dict[str, object]] = []
    for input_spec in _INPUTS:
        metric = entity_frame.metrics.get(input_spec.key)
        weight = float(config.diagnostics.score_weights.get(input_spec.weight_key, 0.0))
        if input_spec.default_band is None:
            band = None
            normalized = 0.0
        else:
            band = config.threshold_band(
                input_spec.threshold_key or input_spec.key,
                tier=tier,
                warn=input_spec.default_band.warn,
                crit=input_spec.default_band.crit,
            )
            normalized = band.normalize(metric.v if metric is not None else None)
        raw_items.append(
            {
                "key": input_spec.key,
                "label": input_spec.label,
                "metrics": input_spec.metrics,
                "weight": weight,
                "value": None if metric is None else metric.v,
                "normalized": normalized,
                "src": "missing" if metric is None else metric.src,
                "confidence": _metric_confidence(entity_frame, input_spec.metrics),
                "detail": input_spec.detail,
                "contribution_raw": weight * normalized,
                "thresholds": None if band is None else {"warn": band.warn, "crit": band.crit},
            }
        )
    raw_sum = max(0.0, sum(float(item["contribution_raw"]) for item in raw_items))
    if raw_sum > 100.0:
        scale = 100.0 / raw_sum
        for item in raw_items:
            item["contribution_raw"] = float(item["contribution_raw"]) * scale
    total_raw = min(100.0, max(0.0, sum(float(item["contribution_raw"]) for item in raw_items)))
    score = int(round(total_raw))
    contributions = _rounded_contributions(raw_items, score)
    items: list[dict[str, object]] = []
    for item, contribution in zip(raw_items, contributions, strict=True):
        items.append(
            {
                "key": item["key"],
                "label": item["label"],
                "metrics": item["metrics"],
                "weight": item["weight"],
                "value": item["value"],
                "normalized": item["normalized"],
                "contribution": contribution,
                "src": item["src"],
                "confidence": item["confidence"],
                "detail": item["detail"],
                "thresholds": item["thresholds"],
            }
        )
    return ScoreBreakdown(score=score, contributions=tuple(items))


def pressure_breakdown(entity_frame: EntityFrame, config: ToposConfig) -> tuple[dict[str, object], ...]:
    return score_entity(entity_frame, config).contributions


def _rounded_contributions(items: list[dict[str, object]], score: int) -> list[int]:
    floors = [int(float(item["contribution_raw"])) for item in items]
    remainder = max(0, score - sum(floors))
    ranked = sorted(
        range(len(items)),
        key=lambda index: (float(items[index]["contribution_raw"]) - floors[index], float(items[index]["weight"])),
        reverse=True,
    )
    out = list(floors)
    for index in ranked[:remainder]:
        out[index] += 1
    return out


def _metric_confidence(entity_frame: EntityFrame, metric_names: tuple[str, ...]) -> str:
    confidences: list[str] = []
    network_confidence = str((entity_frame.network or {}).get("confidence") or "n/a")
    for name in metric_names:
        metric = entity_frame.metrics.get(name)
        if metric is None or metric.v is None:
            continue
        if metric.src == "netns":
            confidences.append("estimated")
        elif metric.src in {"unavail_kernel", "unavail_perm"}:
            confidences.append("n/a")
        elif metric.src == "host" and name.startswith("net_"):
            confidences.append(network_confidence)
        else:
            confidences.append("exact")
    if not confidences:
        return "n/a"
    if "estimated" in confidences:
        return "estimated"
    if "n/a" in confidences and all(value == "n/a" for value in confidences):
        return "n/a"
    return "exact"
