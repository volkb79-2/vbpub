# groop Measurements Ledger

This file records acceptance and overhead evidence required by `TUI-SPEC.md`.
Do not enable BPF by default, raise DAMON defaults, or make release performance
claims without updating this file.

## Current Evidence

Most recent release-hardening validation after P12:

```bash
# isolated venv, from feat/groop-p12-release-hardening
python -m pytest groop/tests -q
# 79 passed in 11.28s
```

Also passed: `py_compile`, `--once --json` over the gstammtisch fixture,
replay UI smoke (`ui smoke ok frames=1 view=tree profile=auto`), sdist/wheel
build, fresh wheel install, and `groop --version` (`groop 0.1.0`).

Bounded once/json CPU/RSS smoke:

- Wall time: `0.189s`
- Child user CPU: `0.134s`
- Child sys CPU: `0.028s`
- Max RSS: `29984 KB`

## v1 Acceptance Measurements

### CPU Steady State

Required by spec §9 item 1.

```bash
pidstat -p "$(pgrep -f 'groop')" 5 60
```

Record:

- Host:
- Kernel:
- CPU count:
- Entity count:
- Command:
- Result:
- Pass/fail against `<5%` of one CPU core:

### RSS

Required by spec §9 item 2.

```bash
ps -o pid,rss,cmd -p "$(pgrep -f 'groop')"
```

Record:

- Entity count:
- History settings:
- RSS:
- Pass/fail:

### Packaging

Required by spec §9 item 11.

```bash
python3 -m build groop/
pipx install ./groop/dist/groop-*.whl --force
groop --version
groop --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl --step --ui-smoke
```

Record:

- Build artifact:
- `groop-0.1.0.tar.gz`
- `groop-0.1.0-py3-none-any.whl`
- pipx version:
- Not measured; P12 used fresh venv wheel install instead.
- Result:
- Pass: wheel installed in a fresh venv and `groop --version` returned
  `groop 0.1.0`.

## DAMON Gate

Required before raising DAMON defaults or enabling persistent paddr.

Measurement plan:

1. Baseline game/server session without groop-controlled DAMON.
2. Passive read-only groop TUI.
3. Controlled vaddr session against one entity.
4. Manual paddr host session.
5. Stop all groop-owned sessions and verify foreign sessions remain untouched.

Record for each:

- Workload:
- DAMON config:
- CPU/RSS overhead:
- Collection interval:
- Observed latency/stutter:
- Result:

## BPF Gate

Required before enabling any BPF provider by default.

Measurement plan from spec §10 / Appendix B:

1. Baseline traffic without BPF.
2. Same traffic with BPF loaded.
3. Cgroup churn while BPF is attached.
4. High packet-rate traffic.
5. Many cgroups/containers.
6. Attach/detach failure recovery.
7. Reboot cleanup / pinned-object audit.

Record:

- BPF program version:
- Pin path:
- Map sizes:
- Traffic generator:
- Packet/byte rate:
- CPU overhead:
- Drop/error counters:
- Result:

## Release Signoff Template

- Release/tag:
- Commit:
- v1 CPU/RSS measured:
- Packaging measured:
- DAMON measured:
- BPF measured if applicable:
- Known exceptions:
