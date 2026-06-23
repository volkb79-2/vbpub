# Container IO / Swap Diagnosis

This devcontainer should include a small diagnostics bundle so disk contention, swap storms,
and per-container pressure can be inspected from inside the cockpit.

## Add To The Image

Install these Debian packages in the devcontainer image:

- `procps` for `top`, `ps`, `vmstat`, `free`
- `sysstat` for `pidstat`, `iostat`, `sar`, `mpstat`
- `iotop` for per-process I/O and swap-wait visibility
- `iproute2` for `ss`, `ip`, `tc`
- `util-linux` for `ionice`, `lsblk`, `findmnt`

## Useful CLI Checks

- `docker stats` for live CPU, memory, network I/O, block I/O, and PIDs per container
- `docker compose stats` for the same view at the compose-service level
- `docker container top <name>` for process visibility inside a running container
- `docker buildx du` for BuildKit cache usage
- `vmstat 1` for `si` / `so` swap activity and `wa` I/O wait
- `sar -W 1` for swap-in / swap-out rates
- `pidstat -d 1` for per-process read/write rates
- `iostat -xz 1` for device saturation and await/queue depth
- `iotop -oPa` for top I/O consumers and swap-heavy processes
- `ss -tpn` for socket/process mapping

## What To Limit During Builds

- BuildKit concurrency first. Use a smaller solver parallelism setting before touching the
  whole machine.
- Memory next. Set build-step memory limits and keep `memory-swap` equal to `memory` if you want
  to suppress swap in build containers.
- If the host disk is saturating during layer extraction, the hard cap is host-side I/O control:
  cgroup v2 `io.max` or a lower I/O priority via `ionice`.
- Reduce work before throttling: keep `.dockerignore` tight, use cache mounts for package managers,
  and reuse external build caches where possible.

## Practical Notes

- `free -h` is useful for a quick host snapshot, but it does not tell you container-specific swap
  availability.
- Docker can report per-container block I/O, but it does not provide a first-class per-build I/O
  throttle for extraction/unpack.
- If the slowdown is really swap, `vmstat` and `sar -W` will show it quickly; if it is disk
  contention, `iostat` and `iotop` will show the saturation and the process causing it.
