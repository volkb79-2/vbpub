# Line-stream renderer design

**Status:** design proposal; no implementation is included in this package.

## Problem

`cli-extended` already owns `CliOutput` and the shared colour decision, while
CMRU's `cmru.output.SeverityStream` maintains a separate line-start parser and
colour table. That split makes terminal presentation differ between consumers
and leaves each one to solve line boundaries, inline decoration, and safe
terminal escapes on its own. The operator's direction is one line-stream
renderer in `cli-extended`, exposed through `CliOutput` and adopted by CMRU.

## Decided direction

- The renderer consumes text as lines and decorates each line. A severity token
  at the start of a line selects that line's severity prefix and colour
  treatment. Each line in a multi-line write is processed separately, with
  chunking behaviour covered by the same equivalence contract as P14. P14
  separately fixes the current CMRU `SeverityStream` multi-line defect; this
  design does not include that fix.
- Within a line, the renderer can highlight file paths, URLs, commit-like hex
  hashes, and branch names. On a colour-capable TTY, URLs can be emitted as
  OSC 8 hyperlinks.
- On a TTY with colour enabled, a small Markdown subset is rendered:
  `` `code` ``, `**bold**`, and `[text](url)`. When the stream is not a TTY,
  colour is off, or JSON mode is active, Markdown remains its original plain
  source.
- `CliOutput.color_enabled(stream)` remains the sole colour policy. The
  renderer does not inspect `NO_COLOR`, arguments, or TTY state to invent a
  second colour decision. JSON output stays plain even when colour is forced.
- `CliOutput` and CMRU share this engine. CMRU's output layer becomes a thin
  wrapper, and its many call sites move in small increments: new code and
  call sites already being changed use the renderer; there is no big-bang
  rewrite.

## Public surface proposal

The following names and placement are proposals for the implementation work;
they are not a committed API:

- Add `cli_extended.line_stream.LineStreamRenderer` in a new
  `cli_extended.line_stream` module. It is a stdlib-only text-stream adapter
  with `write(text)` and `flush()` methods. It owns line segmentation and
  rendering, but accepts presentation decisions already resolved by its
  caller. It does not inspect the environment or call `isatty()` itself.
- Add `CliOutput.line_stream(stream)` as the supported factory. It uses the
  explicit destination stream with `self.color_enabled(stream)`, carries the
  invocation's JSON mode, and supplies the TTY fact needed for TTY-only
  features. The factory keeps colour resolution at `CliOutput`; callers do not
  pass command-line switches to the renderer.
- Export the renderer from `cli_extended` only if the implementation confirms
  that direct construction is a supported use case. Ordinary consumers should
  obtain it from `CliOutput`, so the shared policy and JSON mode cannot be
  accidentally bypassed.

The resolved colour flag controls SGR styling and inline highlights. OSC 8 is
additionally limited to a TTY. Markdown transformation follows the stricter
decided rule: it is enabled only for a TTY when the shared colour policy is on
and JSON mode is off. Thus `--color` can continue to force SGR presentation on
a redirected human stream, but does not rewrite Markdown there or add OSC 8.
JSON mode suppresses all styling and Markdown transformation regardless of a
forced colour setting. Existing plain primary-result and JSON methods remain
plain; the line renderer is for text streams, not a JSON serializer.

## Rendering and escape safety

The renderer should sanitize input-origin terminal control sequences before
recognition and decoration. Untrusted ANSI CSI and OSC sequences must be
stripped or visibly neutralized; the renderer may then add only its own known
SGR sequences and OSC 8 links. This applies to rendered terminal text and its
plain fallback so an input escape cannot survive merely because a stream is
redirected. OSC 8 is generated only for a colour-enabled TTY, and its target
must be constructed from validated text rather than copied as an escape
sequence.

Highlighting and Markdown are limited to text within each line. The engine
must keep generated opening and closing sequences balanced when a line ends,
including when a single `write()` contains several lines. It must preserve
line content and line endings apart from the explicitly requested formatting
and control-sequence sanitization.

## Testing approach for implementation packages

Use golden strings for the same input in these modes: colour-enabled TTY,
colour-disabled TTY, redirected stream, and JSON mode with colour forced. Pin
severity handling, every supported Markdown construct, each highlighter,
OSC 8 placement, and hostile ANSI/OSC input. Include a redirected forced-colour
case to distinguish SGR policy from TTY-only Markdown and hyperlinks. The
plain-mode goldens must show Markdown source unchanged and no generated ANSI or
OSC; JSON goldens must remain plain even when colour is forced.

Add a chunking-equivalence property: writing a complete input in one call and
writing the same characters across arbitrary chunk boundaries, followed by
the same flush, produces identical output. Include splits inside a severity
token, Markdown delimiter, URL, hash, branch name, and newline, plus multi-line
writes and a final unterminated line. Keep these tests deterministic; do not
use sleeps or timing thresholds. Use small, explicit fake TTY streams and
assert exact bytes/text rather than terminal screenshots.

## CMRU migration steps

Each item is a separate implementation package and has its own close condition.

1. **Engine core (CLI-EXT-35).** Implement line segmentation, per-line
   severity handling, and incremental writes in the shared engine. **Done
   when:** plain and colour golden strings cover single- and multi-line writes,
   and chunking-equivalence passes for complete inputs.
2. **Inline highlighters (CLI-EXT-36).** Add paths, URLs, commit-like hashes,
   and branch names, with OSC 8 for URLs only under the decided TTY policy.
   **Done when:** token-level golden cases cover each kind, adjacent
   punctuation, non-matches, and escape injection in candidate text.
3. **Markdown subset (CLI-EXT-37).** Add inline code, bold, and links without
   introducing a general Markdown dependency. **Done when:** exact TTY output
   is pinned for each construct and non-TTY, colour-off, and JSON output retain
   the original Markdown source.
4. **`CliOutput` exposure (CLI-EXT-38).** Add the factory that applies the
   existing colour policy and JSON/plain rules. **Done when:** tests prove the
   destination stream is used for the colour decision and forced colour never
   styles JSON.
5. **CMRU engine adoption (CLI-EXT-39).** Replace the local rendering engine
   in `cmru.output` with a thin adapter over the released shared API. Keep
   CMRU's existing output contract covered; decide the existing optional short
   timestamp behavior before removing its implementation. **Done when:**
   CMRU output tests prove the adapter delegates to the shared engine and no
   second severity/line parser remains in CMRU.
6. **Incremental CMRU call-site migration (CLI-EXT-40).** New CMRU output and
   call sites already being changed use the shared renderer. Leave untouched
   call sites alone until their own small package. **Done when:** every call
   site changed in the package uses the shared API, its behavior is covered,
   and the diff contains no unrelated call-site rewrite.

## Risks

- A line-aware renderer may need to retain a partial token or line between
  writes. Buffering too much can delay progress output; buffering too little
  can make decoration depend on write chunk boundaries.
- File paths, branch names, hashes, URLs, and Markdown can overlap. False
  positives or an unclear precedence rule can corrupt otherwise useful text.
- OSC 8 and ANSI sequences create an injection boundary. Sanitizing input and
  emitting only balanced renderer-owned sequences are required for safety.
- `CliOutput` redacts diagnostic messages and primary results. A stream adapter
  must not create a path around that redaction boundary.
- CMRU currently supports an opt-in short timestamp prefix in its wrapper.
  Shared-engine adoption must preserve or explicitly retire it as a separate
  decision, rather than lose it as an incidental side effect.
- The renderer must remain stdlib-only so it does not add runtime dependencies
  to CMRU or other consumers.

## Open questions

- Which severity spellings are recognized, and how are `ERROR:`/`WARNING:`
  tokens related to existing `[ERROR]`/`[WARN]`/`[INFO]`/`[DEBUG]` tags?
- What exact path, URL, hash, and branch grammars and token boundaries should
  be used, and what happens when a candidate also contains Markdown syntax?
- Which URL schemes and characters are allowed in generated OSC 8 targets?
  Do Markdown links use the same validation and OSC 8 policy as bare URLs?
- What escaping, nesting, malformed-delimiter, and link-label rules define the
  small Markdown subset?
- What should `flush()` do with a partial severity token or unterminated line,
  and what latency guarantee should partial progress writes retain?
- Should lone carriage returns be treated as line boundaries, retained as
  content, or handled as progress redraws?
- How should malformed or incomplete ANSI/OSC sequences be neutralized, and
  should the safe fallback visibly spell them or remove them?
- Does CMRU keep its opt-in short timestamp prefix, and if so, does the thin
  adapter apply it or does it become an explicit renderer option?
- What exact SGR styles and colors identify paths, hashes, branches, Markdown
  code, and bold text? The shared policy is fixed; these visual choices are
  not specified here.
