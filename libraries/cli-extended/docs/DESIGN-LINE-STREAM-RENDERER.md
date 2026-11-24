# Line-stream renderer design

**Status:** implemented by CLI-EXT-35 and CLI-EXT-38. Scope is severity
rendering and input-escape sanitisation only.

## Problem

`CliOutput.color_enabled(stream)` owns the shared colour policy, while CMRU
and CIU each carry a separate `SeverityStream` with its own line parsing and
ANSI table. CMRU has fixed its multi-line write defect; CIU has not. Both
copies also pass input-origin terminal escapes through unchanged.

The shared engine must preserve their machine-readable textual prefix
contract, process every line independently, remain equivalent under arbitrary
write chunking, and remove hostile input controls at the terminal boundary.

## Decided direction

- The renderer recognises exactly `[INFO]`, `[WARN]`, and `[ERROR]` at the
  start of a line. Other labels, including `[DEBUG]`, and the same tokens
  later in a line remain ordinary text.
- LF is the only line boundary. Each line in a multi-line write is processed
  separately. CR and HT are content bytes, matching the current CMRU
  `SeverityStream`; CR is not treated as a redraw boundary.
- Plain mode leaves the sanitised text and its textual severity prefix
  unchanged. For benign input, the final output is byte-identical to CMRU's
  `SeverityStream` plain output for the same complete input and final flush.
- Colour mode wraps only the severity prefix with the existing CMRU palette:
  green `INFO`, yellow `WARN`, and red `ERROR`. It never changes the prefix
  text. JSON mode suppresses all generated ANSI even when colour was forced.
- The engine receives already-resolved `color` and `json_mode` flags. It
  never reads the environment, calls `isatty()`, or infers a second colour
  policy.
- A partial line is buffered until LF or `flush()`. This deliberately trades
  live partial-line progress for deterministic line recognition and
  chunking equivalence.

Path, URL, hash, and branch highlighting (CLI-EXT-36), the inline Markdown
subset (CLI-EXT-37), and OSC 8 hyperlinks are deferred. The engine emits no
OSC sequences.

## Public surface

`cli_extended.line_stream.LineStreamRenderer` is a stdlib-only text adapter:

```python
LineStreamRenderer(stream, *, color: bool, json_mode: bool)
renderer.write(text) -> int
renderer.flush() -> None
```

`write()` returns the input character count, including characters held
between the write and a later LF or flush. `flush()` renders the current
unterminated line, discards any incomplete input escape tail, and flushes
the destination stream.

Ordinary consumers obtain the renderer through:

```python
renderer = runtime.output.line_stream(destination)
```

`CliOutput.line_stream(stream)` resolves `color_enabled(stream)` for that exact
destination and carries the invocation's JSON mode. The class remains
importable from its module for focused tests and adapters, but is not re-
exported from the package root; the factory is the supported construction
path so a consumer does not accidentally bypass the shared policy.

## Rendering contract

Recognition happens after sanitisation. Thus a discarded CSI prefix cannot
hide a severity token, and an escape inserted inside a token cannot split
recognition across an accidental boundary. A severity token produced by
joining separate writes is still recognised if no newline or flush has
finalised the current line.

The colour output for a recognized prefix is exactly:

```text
\x1b[32m[INFO]\x1b[0m rest-of-line
\x1b[33m[WARN]\x1b[0m rest-of-line
\x1b[31m[ERROR]\x1b[0m rest-of-line
```

Only those renderer-owned SGR bytes are added. No input-origin SGR is
preserved, including bytes directly around a severity token.

## Escape safety

The input sanitiser runs before line segmentation and severity recognition,
in plain, colour, and JSON modes. It uses a streaming state machine so splits
inside an escape sequence remain equivalent to a single write.

The following input bytes are stripped rather than rendered visibly:

- Complete ESC-based CSI sequences: `ESC [` through a final byte `0x40..0x7E`.
- Complete single-byte C1 CSI sequences: U+009B through the same final byte.
- Complete ESC-based OSC sequences: `ESC ]` through BEL or ST (`ESC \`).
  All payload bytes, including LF, are part of the stripped sequence.
- Complete single-byte C1 OSC sequences: U+009D through BEL or U+009C.
- A lone ESC, and any ESC sequence that is neither CSI nor OSC: the ESC is
  removed and the following printable character is processed normally.
- Every C0 control (U+0000..U+001F) except LF, HT, and CR; DEL (U+007F);
  and every other C1 control in U+0080..U+009F.
- Incomplete CSI/OSC tails and their consumed parameters/payload. They are
  discarded when an invalid byte ends the sequence, when `flush()` occurs,
  or when a malformed CSI reaches LF; LF itself is then preserved.

There is no caret or symbolic replacement: "neutralised" here means the
unsafe bytes are absent from the output. The engine only emits the
renderer-owned SGR described above. It does not generate hyperlinks.

## Testing approach

Exact-string goldens cover:

- colour-enabled TTY and colour-disabled TTY decisions supplied by `CliOutput`;
- forced colour on a redirected destination;
- forced colour with JSON mode, which remains ANSI-free;
- hostile CSI, OSC, C0, DEL, and C1 input in plain, colour, and JSON modes;
- single-line and multi-line writes, including a final unterminated line and
  `flush()`;
- the shared plain cases that prove CMRU's textual `[INFO]/[WARN]/[ERROR]`
  contract without importing CMRU.

A deterministic chunking test writes the same sanitised message in one chunk,
at every single split position, and in several multi-split partitions. The
message splits severity tokens, CSI/OSC sequences, CRLF/LF boundaries, and
ends with an unterminated line. Tests do not sleep or depend on timing.

## Adoption sequence

CLI-EXT-35 and CLI-EXT-38 stop at the shared engine and factory. CLI-EXT-39
remains the CMRU thin-adapter adoption tracked by CMRU KI-185; it must decide
how CMRU's optional short timestamp prefix is applied. CLI-EXT-40 continues
the incremental call-site migration. No CMRU or CIU code changes are part of
this package.

## Risks

- Buffering every partial line delays progress that does not end in LF; callers
  needing live non-line progress should not route those writes through this
  renderer.
- Removing malformed or incomplete OSC tails can discard text until
  `flush()`; this is the safe failure mode for an unterminated terminal
  control string.
- CR remains content for CMRU byte compatibility and can still redraw a
  terminal line. A future policy change would need to be explicit and
  coverage-visible.
- CMRU adoption must preserve redaction ordering and settle the short
  timestamp behaviour; this shared engine does neither implicitly.
