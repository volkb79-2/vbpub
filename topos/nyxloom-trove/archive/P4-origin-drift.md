# P4 — Origin / drift detection

**Cut:** v1. **Depends:** P1 merged. Branch: `feat/topos-p4-drift`.
Follow `topos/README.md` workflow protocol.

## Goal

For each governed knob: WHO owns the live value (systemd property, raw write,
docker default) and does the live kernel value match the owner's record?
Finding-D-class drift (daemon-reload wiping raw writes) is a real production
failure mode this host has hit — this feature is why topos exists.

## Spec references

§3.2 origin/drift columns, §3.4 drill-down governance section, §6.5 (drift
severity policy: ANY drift = warning; RED only when it changes the effective
protection of a protected workload), spec §1 motivation (Findings A/D).

## Scope — in

1. `drift/origin.py`: for an entity, gather the systemd view via
   `systemctl show <unit> -p MemoryMin -p MemoryLow -p MemoryHigh -p MemoryMax
   -p CPUWeight -p IOWeight -p FragmentPath -p Transient` (subprocess,
   injectable for tests; batch where possible) and compare against the live
   cgroup files from the P1 frame.
2. Classification per knob: `origin ∈ {systemd_unit, systemd_runtime_dropin,
   raw_write, docker_default, unset}` + `drift: bool` + severity:
   - `warn`: live ≠ systemd-recorded (someone echo'd into the cgroup, or
     systemd hasn't applied yet);
   - `red`: drift on a protected entity (config `[tiers]`/protected list) that
     WEAKENS effective protection — includes the ancestor-chain check: an
     entity's memory.min is effectively capped by min(ancestors); compute the
     EFFECTIVE floor along the path and flag when recorded protection ≠
     effective protection (Finding A).
3. Effective-protection calculator: given the tree frame, annotate each
   protected entity with `effective_memory_min` (the ancestor-clamped value) —
   this is a derived registry metric.
4. Frame integration: origin/drift results are MetricValues + a structured
   per-entity `governance` block consumed by drill-down and P6 rules.
5. Tests: canned `systemctl show` outputs; cases: clean systemd-owned band;
   raw-write drift (live high ≠ recorded); ancestor-capped floor (parent
   min=0 → red for protected entity); transient-slice-without-unit
   (FragmentPath empty → the missing-slice footgun).

## Scope — out

Any set-property/fix actions (v2; show-only), paging/alerting transport.

## Acceptance

- On the gstammtisch fixture: game scope shows origin=systemd_runtime_dropin,
  no drift; a fixture with parent min=0 turns the protected floor red with a
  human-readable reason string.
- Registry entries for origin/drift/effective_min metrics with glossary text
  that explains Finding A and Finding D in two sentences each.
- pytest green; report per README protocol.
