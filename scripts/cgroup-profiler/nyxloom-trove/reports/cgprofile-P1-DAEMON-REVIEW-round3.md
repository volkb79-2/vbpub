# RG-55 P1 daemon adversarial review — round 3

REJECT

## Blind finding preserved before repair

Initial clean candidate: `3f3777662966baeedeb8c903d10fcb6235143ba1` on
`rg55-p1-private-ns`; base and merge base:
`e5e9b95c5ac8be3452c93f1066f9436347f862fd`. The full initial diff was
captured before editing at `/tmp/rg55-p1-sol-blind.diff` (SHA-256
`cc2318916bb12e09a6842b2be1bfdd42ac32b5c1ed97ea2018cc1fb949c5ee5f`).
The code and docs diff was reviewed before the implementer LOG/REPORT/briefs.

### B1 — explicit empty build version silently becomes a development version

Severity: release identity / fail-closed contract. In `lib/version.py:73-81`,
`env.get(VERSION_ENV, "").strip()` collapses an absent version and an
explicitly supplied empty or whitespace-only version. On an untagged tree,
`resolve_build_version(..., require_release_tag=False,
environ={"CGPROFILE_VERSION":"   "})` returns `0.0.0-dev`. RW-311 requires an
explicit empty or malformed version to refuse, and the estate default rule
forbids replacing a supplied but invalid fact with a fallback. A local build
can therefore label an accidentally empty release input as a valid development
image without failing. Reproduced against the candidate with a direct Python
call; the push path refuses only because it lacks a tag, with the wrong
absence diagnosis. Prescription: distinguish key absence from key presence,
validate every present value, and add behavioral tests for both build and
publish modes while retaining absent-input development behavior.

## Fix verification and final verdict

Pending live probes and exact-tree short gates. This section will be completed
in the same reviewer session after the scoped repair.
