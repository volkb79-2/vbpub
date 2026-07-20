# Test ladder

Four rungs, cheapest first. The table states what each rung **covers** — run
them yourself for a result. The full ladder was last exercised green against
the complete six-commit series (0001–0006); both patched trees are
build + vet + unit green as of this review.

| Rung | What it proves | How |
|---|---|---|
| **Unit** | Validation + resolver logic (namespace guard, allowlist, fail-closed) in both patched trees; the slice config schema and `budget_policy` validation from 0004 (`config/config_docker_slices_test.go`); the BFQ scale conversion from 0005 (`internal/cgroups/bfq_test.go` — the `IOWeight`→`io.bfq.weight` formula and the `io_bfq_weight` passthrough); the config discarded-key diagnostics from 0006 (`config/config_discarded_test.go`); slice-manager spec/budget/GC logic | `../patchstack/scripts/test.sh pterodactyl` / `pelican`; `make test` in `../t3a-slice-manager/` |
| **Wings integration** | The *patched Wings code itself* creates containers with the right `HostConfig.CgroupParent` (node-wide, accepted override, fail-closed rejection) against a real Docker daemon | `INTEGRATION=1 ../patchstack/scripts/test.sh pterodactyl` (build-tagged `dockerintegration` tests inside the tree) |
| **Placement smoke** | This machine's daemon really places scopes under named slices (systemd driver, cgroup v2), plus the transient-slice footgun check | `./smoke-placement.sh [slice]` |
| **systemd e2e** | *Effective guarantees*, not just paths: real slice units with `MemoryMin/High`, values verified in cgroupfs, **daemon-reload survival** (Finding D regression), and the black-box slice-manager scenario (create→properties, delete→GC) | `./e2e-systemd/run-e2e.sh` (privileged container; skips politely if privileged is unavailable) |

## Why "docker in docker"?

The devcontainer already talks to a real systemd/cgroup-v2 daemon, so the first
three rungs need no DinD at all. The e2e rung runs its **own** systemd + dockerd
inside a privileged container because it must install slice *units with
properties* and trigger `systemctl daemon-reload` — host-mutating actions we
don't want to perform on a shared host.
