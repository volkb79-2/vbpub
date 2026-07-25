# P104 self-review — final controller repair

## Adversarial checks

- All 22 test bodies contain an exact behavioral assertion or an exact
  exception assertion; there are no `pass` or assertion-free bodies.
- Enrichment tests compare complete returned tuples and status dictionaries.
- Filesystem tests use per-case `tmp_path`; the `/virtual` value is never
  accessed because `Path.exists` is patched for the exhaustion boundary.
- No test mocks the function under test. Patches are limited to environment,
  home-directory, optional-dependency, and filesystem-existence seams.
- The cgroup error case proves both the successful primary copy and the exact
  retained directory that causes and survives the swallowed ancestor write
  error.
- Tar handles are closed, tar membership is exact, and traversal rejection
  retains the production safety behavior.
- There are no weak non-`None`, range-only, membership-only, call-only, sleep,
  random, host-state, coverage pragma, omission, or mutation claims.
- Two complete xdist receipt runs have identical complete target records and
  empty whole-file missing sets.

## Scope

Only the P104 handoff, P104 reports, and P104 test file changed. Product source,
gate tooling, dependencies, and coverage configuration are unchanged.
