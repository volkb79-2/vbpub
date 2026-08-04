"""Route doctor: schema-validate + live-probe routes.toml. BACKLOG B1
(folds into F009, docs/routing-model-redesign.md), also closes B15(a)'s
"validate Tier-2 provider route addressing with a live probe before real
traffic" ask -- both are the same probe pass over the same route set.

Read-only diagnostic over the routing configuration -- mirrors doctor.py's
DoctorFinding/severity vocabulary (kind, severity in
{critical,error,warning,info}, message, refs) and its "critical/error ->
non-zero exit" convention (see cli.cmd_route_doctor). Every finding here
carries project=None: routes.toml is a HOST-scoped file (paths.routes_path(),
one per deployment), not owned by any single registered project -- the same
convention doctor.doctor_host() already uses for its own host-scoped checks.

INTERFACE CONTRACT:

- `check_schema(routes)` -- static validation over an already-loaded
  `config.Routes`, no I/O:
    route-unknown-cli   critical  route.cli is not one of the adapters.
                                  build_dispatch-recognized clis (adapters.py
                                  ~756-788: claude/codex/opencode/reasonix/
                                  fake -- KNOWN_CLIS below mirrors that
                                  dispatch table by hand; adapters.py exposes
                                  no such constant to import instead).
    route-missing-model critical  route.model is empty/falsy. There is no
                                  closed catalog of valid model strings (they
                                  are arbitrary per-provider/per-CLI values,
                                  e.g. "sonnet" vs "openrouter/vendor/x:free")
                                  so "references a known model" is enforced
                                  as "a model is actually declared" -- the
                                  one model-shaped requirement adapters.
                                  build_dispatch structurally needs (it
                                  renders route.model straight into CLI argv
                                  with no validation of its own).
    route-malformed-probe error    route.probe is set but is neither a named
                                  builtin (adapters.probe's own
                                  "one-token-ping"/"session-limit-check") nor
                                  a non-empty list[str] argv -- adapters.probe
                                  subprocess.run()s it verbatim, so anything
                                  else either can't run or silently probes
                                  the wrong thing.
    tier-dangling-route critical  a tier's routes=[...] entry names a route
                                  id absent from [routes.*]. Routes.for_tier/
                                  for_role drop the dangling id and return
                                  what does resolve (CR-08: never KeyError),
                                  so this is silent quantity loss -- fewer
                                  candidates than the operator declared --
                                  rather than a crash; still critical, since
                                  a tier that silently loses its only route
                                  degrades to tier-empty's dead-tier case with
                                  no warning at the point of loss.
    tier-empty          warning   a tier is declared with an empty routes
                                  list (dead tier -- for_tier/for_role always
                                  return [] for it).
- `check_live(routes)` -- reuses `adapters.probe(route)` UNCHANGED (the same
  function daemon.py's `_provider_ok` memoizes for real dispatch) for every
  route, regardless of any schema finding on it. `adapters.probe` never
  raises (every subprocess/lookup failure is already caught internally and
  turned into `(False, detail)`), so this never needs its own try/except --
  a failure becomes one `route-probe-failed` (warning) finding, never a
  crash. Skipped entirely when the caller does not want live traffic
  (`doctor_routes(live_probe=False)` / CLI `--no-probe`) -- schema
  validation alone has no network/subprocess dependency and must work
  offline (test suite, an operator with no CLI credentials handy).
- `doctor_routes(path=None, *, live_probe=True) -> (Routes | None, list[
  DoctorFinding])` -- the combined entry point `cli.cmd_route_doctor` calls.
  Loads `config.Routes.load(path)` itself so the CLI need not duplicate that
  try/except: a load failure (malformed TOML, an `inherit` cycle or a
  dangling `inherit` parent -- `Routes.load` raises ValueError/KeyError for
  both) degrades to a SINGLE critical `routes-load-failed` finding and a
  `None` Routes (nothing to enumerate) rather than propagating -- doctor is
  precisely the tool an operator reaches for when the config may already be
  broken (mirrors doctor.doctor_project's degrade-on-exception discipline
  for its own replay-divergence check).

READ-ONLY: this module never writes routes.toml (or anything else). It has
no writer counterpart, unlike free_models.py/capability_map.py's managed-
block writers.
"""

from __future__ import annotations

from pathlib import Path

from . import adapters, config, paths
from .types import DoctorFinding

# Mirrors adapters.build_dispatch's recognized `route.cli` branches
# (adapters.py ~756-788). Not imported from adapters.py -- it exposes no
# such constant, only an if/elif/else chain ending in
# `raise AdapterError(f"unknown cli: {route.cli}")` -- so keep this list
# hand-in-sync if a new CLI adapter is ever added there.
KNOWN_CLIS: frozenset[str] = frozenset({"claude", "codex", "opencode", "reasonix", "fake"})

# adapters.probe's own two named builtins (adapters.py ~894) -- any other
# non-None `route.probe` must be an argv list.
_PROBE_BUILTINS: frozenset[str] = frozenset({"one-token-ping", "session-limit-check"})


def _is_valid_probe_argv(probe: object) -> bool:
    return isinstance(probe, list) and len(probe) > 0 and all(isinstance(a, str) for a in probe)


def check_schema(routes: config.Routes) -> list[DoctorFinding]:
    """Static validation over an already-loaded Routes -- no I/O, no probe."""
    findings: list[DoctorFinding] = []

    for route_id, route in sorted(routes.routes.items()):
        if route.cli not in KNOWN_CLIS:
            findings.append(DoctorFinding(
                kind="route-unknown-cli",
                severity="critical",
                message=(f"route {route_id!r}: unknown cli {route.cli!r} "
                          f"(not one of {sorted(KNOWN_CLIS)})"),
                project=None,
                refs=[route_id],
            ))
        # Type AND emptiness. `not route.model` alone lets a truthy NON-STRING
        # through: TOML `model = 3.5` / `model = true` parse to float/bool,
        # `RouteDef` annotates `model: str` but a dataclass does not enforce it,
        # and `Routes.load` passes the parsed value straight to the constructor.
        # Such a route reads as clean here, then `build_dispatch` renders it
        # straight into argv (adapters.py:761/768/776) and `subprocess.run`
        # raises `TypeError: expected str, bytes or os.PathLike object, not
        # float` at DISPATCH time -- a false OK on a genuinely broken config,
        # which is the one failure class this verb exists to prevent. (`cli` is
        # already type-safe via set membership; `probe` via isinstance +
        # _is_valid_probe_argv. `model` was the lone gap.) Verified live.
        if not isinstance(route.model, str) or not route.model:
            findings.append(DoctorFinding(
                kind="route-missing-model",
                severity="critical",
                message=(f"route {route_id!r}: model must be a non-empty string, "
                         f"got {route.model!r}"),
                project=None,
                refs=[route_id],
            ))
        if route.probe is not None:
            is_builtin = isinstance(route.probe, str) and route.probe in _PROBE_BUILTINS
            if not is_builtin and not _is_valid_probe_argv(route.probe):
                findings.append(DoctorFinding(
                    kind="route-malformed-probe",
                    severity="error",
                    message=(f"route {route_id!r}: probe is neither a named "
                              f"builtin ({sorted(_PROBE_BUILTINS)}) nor a "
                              f"non-empty argv list: {route.probe!r}"),
                    project=None,
                    refs=[route_id],
                ))

    for tier, route_ids in sorted(routes.tiers.items()):
        if not route_ids:
            findings.append(DoctorFinding(
                kind="tier-empty",
                severity="warning",
                message=f"tier {tier!r}: no routes",
                project=None,
                refs=[tier],
            ))
            continue
        for rid in route_ids:
            if rid not in routes.routes:
                findings.append(DoctorFinding(
                    kind="tier-dangling-route",
                    severity="critical",
                    message=(f"tier {tier!r}: references undefined route "
                              f"{rid!r} (Routes.for_tier/for_role silently "
                              "drops it -- fewer candidates than declared)"),
                    project=None,
                    refs=[tier, rid],
                ))

    return findings


def check_live(routes: config.Routes) -> list[DoctorFinding]:
    """Live-probe every route via adapters.probe (unchanged). A probe==None
    route always reports (True, 'no-probe') -- no finding. A probe failure
    (non-zero exit, timeout, command not found, ...) is a warning finding,
    never a crash -- adapters.probe already never raises."""
    findings: list[DoctorFinding] = []
    for route_id, route in sorted(routes.routes.items()):
        ok, detail = adapters.probe(route)
        if not ok:
            findings.append(DoctorFinding(
                kind="route-probe-failed",
                severity="warning",
                message=f"route {route_id!r}: probe failed ({detail})",
                project=None,
                refs=[route_id],
            ))
    return findings


def doctor_routes(path: Path | None = None, *, live_probe: bool = True,
                   ) -> tuple[config.Routes | None, list[DoctorFinding]]:
    """Load routes.toml (paths.routes_path() unless `path` overrides it),
    schema-check it, and -- unless live_probe=False -- live-probe every
    route. Returns (routes, findings); `routes` is None only when the load
    itself failed (in which case `findings` is the single routes-load-failed
    finding and there is nothing else to check)."""
    p = path or paths.routes_path()
    try:
        routes = config.Routes.load(path=p)
    except Exception as exc:
        return None, [DoctorFinding(
            kind="routes-load-failed",
            severity="critical",
            message=f"routes.toml failed to load: {type(exc).__name__}: {exc}",
            project=None,
            refs=[str(p)],
        )]

    findings = check_schema(routes)
    if live_probe:
        findings.extend(check_live(routes))
    return routes, findings
