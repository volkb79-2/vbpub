"""Operator CLI. PACKAGE P10.

Thin argparse wiring over the modules; every state-changing verb appends an
audited event (actor OPERATOR with $USER). Output is plain aligned text
tables (no rich/click deps). Exit codes: 0 ok, 1 findings/failures, 2 usage.

INTERFACE CONTRACT (frozen) — subcommands:

  project add <id> <root>     config.register_project + paths.ensure_layout
                              + PROJECT_REGISTERED event.
  project list                registry table.
  lint [path ...]             no args -> lint_project for every registered
                              project; exit 1 if any has_blocking. Prints
                              'PATH:LINE RULE SEVERITY MESSAGE' lines.
  doctor [--project X] [--rebuild [--write]]
                              findings table; exit 1 on any severity in
                              {critical, error}. --rebuild prints diffs.
  status [--project X]        per task: id, state, since, attempt route,
                              cost, notes. Reads statefiles only.
  render                      render.render_all(registry); prints www path.
  daemon [--foreground]       Daemon(registry).run() (foreground only in
                              the pilot; systemd/tmux owns daemonization).
  tick [--project X]          daemon.run_once — one pass, prints action
                              count. THE debug/fallback mode.
  decide <project> <D-id> --choose TEXT [--note TEXT]
                              decisions.decide(authority=$USER) +
                              DECISION_RESOLVED event (decision_id set).
  discuss <project> <D-id>    prints decisions.discuss command string.
  pause <project> [task]      touch pause flag + PAUSE_SET event;
  unpause <project> [task]    remove + PAUSE_CLEARED. (Project-level pause
                              writes the flag file; task-level also flows
                              into the statefile via the event projection.)
  leases                      leases.holder_info for every mutex declared
                              by any registered project (project + host).
  digest <project> [--since SEQ]   prints notify.digest.
  events <project> [--since SEQ] [--type T]   raw event lines (debug).
  version                     handoffctl.__version__.

main(argv=None) -> int. Import module functions lazily inside handlers so
`handoffctl version` works even if an optional module is broken; handlers
catch HandoffctlError-family exceptions and print 'error: ...' to stderr
(exit 1), never tracebacks (tracebacks only with --debug global flag).
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    raise NotImplementedError


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
