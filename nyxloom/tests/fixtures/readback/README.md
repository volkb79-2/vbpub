# Readback fixtures

`agent-comms.jsonl` is a minimized, anonymized slice derived from the real
Claude Code controller-session record shapes observed on 2026-11-10. It
preserves source ordering and the relevant envelopes: plain operator text,
Agent tool input, a shell launcher after a leading `cd`, task notifications
with harness-only fields, and assistant prose.

`layout.jsonl` is the LAYOUT fixture: a session whose first operator prompt
carries an injected `<system-reminder>` block, whose first record is a
`<command-name>/loop</command-name>` wrapper (never the goal), then middle
prose, then the newest pre-cut prose plus a tool call, then records after the
cut. It exists so the three-part readback (goal anchor, recent prose tail,
delta) and the no-duplication property across two consecutive read-backs can be
asserted on one source.

All paths, identities, prose, and tool ids are repository-neutral test
content. The original private session path, thread/session id, task ids, and
controller paths are not part of the fixture. Tests create referenced run
files under `tmp_path`; the fixture itself contains no machine-local path.
