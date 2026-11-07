# TUI UX testing

The UX lane checks two complementary things:

- **Pilot tests** inspect Textual's composed screen and drive widgets directly. They cover screen content, navigation,
  workflows, and representative layouts without starting a terminal emulator.
- **tmux tests** start the real `TntApp` on a pseudo-terminal backed by the existing loopback `FakeServer`. They use
  `send-keys`, `capture-pane`, and `resize-window` at 60×40, 80×24, 120×40, and 200×60 to check terminal rendering,
  focus styling, typed confirmations, resizing, and clean quit behavior. `tester-unified` installs tmux and the
  registered R0/R1, UX, and R2 lanes fail if the binary is missing. A local workstation can skip these cases only with
  `TNT_TEST_ALLOW_MISSING_TMUX_FOR_LOCAL_DEV=1`; see [TNT-54](../BACKLOG.md#tnt-54-tmux-real-terminal-ux-checks).

Run the focused UX lane with `run-gate ux-focused`; run the full suite and coverage gate with `run-gate r0-r1`.
Both use the registered `tester-unified` environment.

These checks do not evaluate screen-reader behavior or make aesthetic judgments about the interface.
