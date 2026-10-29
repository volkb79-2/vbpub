# TUI UX testing

The UX lane checks two complementary things:

- **Pilot tests** inspect Textual's composed screen and drive widgets directly. They cover screen content, navigation,
  workflows, and representative layouts without starting a terminal emulator.
- **tmux tests** start the real `TntApp` on a pseudo-terminal backed by the existing loopback `FakeServer`. They use
  `send-keys`, `capture-pane`, and `resize-window` at 60×40, 80×24, 120×40, and 200×60 to check terminal rendering,
  focus styling, typed confirmations, resizing, and clean quit behavior. They skip with a packaging reason when tmux is
  absent from `tester-unified`; see [TNT-54](../BACKLOG.md#tnt-54-tmux-real-terminal-ux-checks).

Run the focused UX lane with `run-gate ux-focused`; run the full suite and coverage gate with `run-gate r0-r1`.
Both use the registered `tester-unified` environment.

These checks do not evaluate screen-reader behavior or make aesthetic judgments about the interface.
