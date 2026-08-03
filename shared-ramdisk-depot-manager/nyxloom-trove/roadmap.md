# srdm roadmap

Phases as the master plan orders them (§Kickoff plan, reordered by decision
10 so the vanilla-Wings product ships first).

| Phase | Package | State |
|---|---|---|
| 1 | **P01** — project bootstrap + release store + journal + offline doctor | **done** |
| 1 | P02 — publication topology: op tmpfs, per-class hold services, charging, teardown | next |
| 1 | P03 — `host-bind` exposure driver (`ro`/`rw`), consumer registry, teardown safety | |
| 1 | P04 — `harvest`: adopt an in-place-updated generation as a release | |
| 1 | P05 — retention/GC, daemon, admin socket, the online half of doctor | |
| 2 | F1 — the Wings chown-skip patch (`../../wings-patchstack/`) | parallel, not srdm |
| 3 | Soulmask migration onto `host-bind` — retires `soulmask_tmpfs` | |
| 4–7 | provider protocol freeze, L1/L1b, the `provider` exposure driver, cutover | v2 |

The MVP gate is master-plan oracles 19–24. Phase 1 does not ship without
them.
