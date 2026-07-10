# P39 Release Readiness Ledger Report

## Outcome

P39 adds a canonical, operator-facing release checklist at
`groop/docs/RELEASE-READINESS.md`. It distinguishes implemented fixture-tested
surfaces from strict production acceptance and points all dated raw evidence to
`groop/MEASUREMENTS.md`.

## Delivered

- Exact v0/v1/v1.5 candidate claim with qualifications.
- Explicit non-claims for BPF lifecycle, executable actions, automated daemon
  installation, persistent paddr ownership, inspect-files content mode, web,
  GPU, and ZFS.
- A 14-item spec section 9 evidence map using Pass/Partial/Conditional states.
- Rootless full-suite, full-source py_compile, P33, P35, P38, direct TUI, and
  required pipx packaging commands.
- Paste-in templates for five-minute Textual CPU/RSS, controlled DAMON,
  deployed non-root daemon, and exact live non-root acceptance.
- A short list of unconditional and capability-conditional release blockers.
- README, OPERATIONS, STATUS, ROADMAP, and MEASUREMENTS alignment.

## Review Findings

Controller review removed several unsafe claims from the initial draft:

- P12 fresh-venv packaging does not satisfy the spec-required pipx test.
- Rootless fixture harnesses do not satisfy the exact live docker-group smoke.
- Model replay equality does not prove byte-identical formatted cells.
- Drift fixtures do not replace controlled live raw-write/reversion evidence.
- Preview-only v2 actions cannot be called full executable-action acceptance.
- Vaddr DAMON control has no CLI start command; it is a TUI typed-confirmation
  flow. Paddr retains a real CLI start command.

## Evidence-Exposed Follow-Up

The managed Python 3.14.6 / Textual 8.2.8 environment produced:

```text
367 passed, 15 failed in 48.27s
```

All failures use the removed `Static.renderable` test API. Production P38
`tui-smoke` still passes, but a red full suite blocks release. P40 is carved at
`groop/handoff/P40-textual-8-test-compatibility.md`; README and ROADMAP mark it
planned.

Controller validation also recorded 40 passing acceptance tests, passing P33,
P35, and P38 fixture commands, a passing direct checkout CLI UI smoke, clean
full-source `py_compile`, and clean diff whitespace. These checks do not
override the red full-suite blocker.

## Scope

P39 itself changes documentation only. It introduces no runtime, schema,
contract, default, or privilege behavior changes. No live-root actions or
package publication were performed.

P39 merged as `bfdf3db`. Its evidence-exposed P40 follow-up merged as
`970953a` and restored the full suite. Post-merge controller validation recorded
382 passed in 47.73s, 40 focused acceptance tests in 7.54s, passing TUI smoke,
and clean full-source compilation.

## Remaining Gates

Before a production-certified v1/v1.5 tag:

- Record five-minute live TUI CPU/RSS.
- Record controlled live drift/reversion and formatted replay fidelity.
- Record the exact live docker-group non-root smoke.
- Record DAMON and daemon live evidence only when those capabilities are in
  the release claim.
