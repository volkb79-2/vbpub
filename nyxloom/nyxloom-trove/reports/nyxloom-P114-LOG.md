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
that the declared tester-unified gate and final report are still pending; they
will be completed after the active session-extract R2 campaign releases gate
resources.

Affected tests passed in the devcontainer:

```text
/home/vscode/.venv/bin/python -m pytest -q tests/test_daemon_entrypoint.py tests/test_cli_adoption.py
exit: 0
```

`git diff --check` passed. This is diagnostic evidence, not the declared
tester-unified gate. Run that gate after the active session-extract R2 campaign
completes and add `nyxloom-P114-REPORT.md` with its exact receipt before
calling this fix complete.
