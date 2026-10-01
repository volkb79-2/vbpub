# nyxloom-P114 execution log

## Scope — 2026-09-28

The daemon entrypoint help/version path was found to start the daemon when the
provisional wheel was inspected. The isolated branch
`nyxloom-daemon-entrypoint-help` adds argument handling before daemon or
registry imports, so `nyxloomd --help`, `nyxloomd --version`, and invalid
arguments cannot start service work. The normal no-argument entrypoint still
loads the registry and runs the foreground daemon.

## Review correction

Codex review of implementation commit
`5d6c2e62611d3bc045c42607f93bb168385d934d` found a P1 in the existing runpy
test: it inherited pytest's arguments after the new parser was introduced.
The test now sets `sys.argv` to only `nyxloomd` before running the module.

Codex's follow-up review found no additional code issue. It correctly noted
that the declared tester-unified gate and final report were still pending at
that review point. The gate has since passed; the session-extract R2 remains
pending because the shared mutation slot is occupied.

Affected tests passed in the devcontainer:

```text
/home/vscode/.venv/bin/python -m pytest -q tests/test_daemon_entrypoint.py tests/test_cli_adoption.py
exit: 0
```

`git diff --check` passed. This was diagnostic evidence, not the declared
tester-unified gate.

## Final tester-unified result — 2026-09-29

The combined Nyxloom revision passed its declared gate on exact commit
`29d5cf6ee229f90c637729e8ade3169ccdf01fc3`:

```text
command: ./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-session-cli-acceptance tester-unified
start: 2026-09-29T02:27:50Z
end: 2026-09-29T02:31:01Z
container: run-gate-vbpub-tester-unified-3918834-1790648862
tester-unified: PASS (exit 0)
R0: PASS
R1: PASS — 127/127 changed executable lines and 62/62 branches; no missing,
excluded, or unclassified lines
```

The run used Assay 7.1.1.dev189+g29d5cf6e. Help/version and invalid-argument
tests are included in this evidence; the gate's changed-line coverage is
recorded in the machine verdict. The session-extract R2 and final P114 report
remain pending.
