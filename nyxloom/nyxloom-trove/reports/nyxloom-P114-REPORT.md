# P114 report: side-effect-free daemon help and version

Date: 2026-10-01
Result: **DONE** — daemon help, version, and invalid arguments return before
service startup; the change is integrated into main.

## Summary

The nyxloomd entrypoint now handles --help and --version without loading the
service registry or starting the daemon. Invalid arguments also exit before
service work. The normal no-argument entrypoint still loads the registry and
runs the foreground daemon.

Review found that the runpy regression test inherited pytest's command-line
arguments after the parser change. The test now sets sys.argv to only
nyxloomd before invoking the module. Follow-up review found no further code
issue.

## Coverage and review

The integrated Nyxloom tester-unified gate passed at commit c1d0fbe2:

- R0: PASS.
- R1: PASS — 655/655 changed executable lines and 166/166 branches.

The complete Nyxloom product tree at c1d0fbe2 is identical to the tree merged
at 2ba90c10 for source, tests, and user documentation. The machine verdict is
evidence/tester-unified-c1d0fbe2.json.

Post-coverage review found no remaining P114 issue. Help/version and invalid
argument behavior have regression coverage in test_daemon_entrypoint.py.

## Files changed

- nyxloom/src/nyxloom/daemon_entrypoint.py
- nyxloom/tests/test_daemon_entrypoint.py
- nyxloom-P114-LOG.md

P114 is complete and integrated.
