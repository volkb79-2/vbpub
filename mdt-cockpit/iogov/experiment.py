"""`quiet-experiment`: a DEFINITION and a plan printer for an operator-approved nr_requests / io.max test. It executes nothing."""

from __future__ import annotations

KNOBS = {
    "nr_requests": "echo {value} > /sys/block/<disk>/queue/nr_requests",
    "max_sectors_kb": "echo {value} > /sys/block/<disk>/queue/max_sectors_kb",
    "io.max": "echo '<MAJ:MIN> wbps={value}' > <governed cgroup>/io.max",
}


def plan(cfg, knob: str, value: str, window_min: int = 30) -> str:
    if knob not in KNOBS:
        raise ValueError(f"unknown knob {knob!r}; known: {', '.join(sorted(KNOBS))}")
    change = KNOBS[knob].format(value=value)
    return "\n".join([
        f"iogov quiet-experiment PLAN for {knob}={value}  (printed only: this command changes nothing and never will)",
        "",
        "Purpose: measure the actuator's stopping distance (how long protected distress persists after the change) and whether the",
        "probe series move with the setting, so that a later model/profile change rests on a measurement.",
        "",
        "Preconditions (all must hold; otherwise do not start):",
        "  - the operator approved this window in writing and is watching;",
        f"  - `iogov status` shows no active kill switch, the last bench is ok, and the protected cgroups show no distress for {window_min} min;",
        "  - `iogov watch` is running (episode recording) and the probes are enabled, so both sides of the change are in the journal;",
        f"  - the previous value is known and written down: read it first (`cat` of the same file).",
        "",
        "Procedure (the operator runs each step by hand):",
        f"  1. iogov annotate --action \"quiet-experiment start: {knob}={value} (previous value: <PREV>)\"",
        f"  2. {change}          # the only host change",
        f"  3. observe {window_min} min: `iogov status`, `iogov analyze --lane r_`; run `iogov bench --force` once if the host is calm",
        "  4. revert to <PREV> with the same command",
        f"  5. iogov annotate --action \"quiet-experiment end: {knob} reverted to <PREV>\"",
        "",
        "Abort at once (revert and annotate) if protected pressure `some` stays above the episode threshold or any probe is slow.",
        "Reading the result: `iogov analyze` section 'around annotation' shows distress episodes (duration and recovery) before and after.",
        "settings_epoch changes in the journal, so the recognizer labels windows after the change as confounded.",
    ])
