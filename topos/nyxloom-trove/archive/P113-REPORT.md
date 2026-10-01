# P113 report — exact execution validation and audit primitives

## Result

The declared P113 residual in `src/topos/actions/execute.py` is closed:
32 executable lines and 24 branch pairs have empty intersections in two clean,
branch-aware xdist coverage records. The exact same normalized `execute.py`
record hash was produced in both runs. The package adds 9 test functions / 13
collected cases, moving the verified suite from 2,156 to 2,169 cases.

This is a literal primitive tranche, not whole-file completion. Later
`execute.py` gaps starting at line 322 remain explicitly assigned to P114/P115.

## Behavioral evidence

- Output and audit text reject non-string/unbounded input; invalid UTF-8 is
  decoded with the documented replacement character.
- Production identity falls back to `unknown` on passwd lookup failure;
  malformed identity, timeout, and forged/unstable action plans refuse with
  exact errors.
- The safe-audit target itself—not a mocked target—runs against narrow OS seams
  that prove no-follow availability, traversal refusal, private/root-owned
  directory and leaf requirements, `0o700` creation, `0o600` leaf repair, and
  descriptor cleanup.
- The `BaseException` cleanup around `fdopen` is deliberately retained:
  injected `KeyboardInterrupt` closes the leaf and parent descriptors in order
  and then propagates. It is not an operator-interrupt swallowing boundary.
- Oversized JSONL audit records fail before writing an unbounded record.

No product defect was found in this tranche; no production source changed.

## Literal residual receipt

```text
before lines:
83 95 123 124 132 138 140 146 161 163 165 168 178 180 186 188
198 201 202 203 211 213 224 225 238 240 242 245 246 247 264 291

before pairs:
82->83 131->132 133->138 139->140 145->146 156->161 162->163
164->165 167->168 177->178 179->180 185->186 187->188 197->198
210->211 212->213 222->224 224->225 224->226 237->238 239->240
241->242 263->264 290->291

run 1 intersection: lines=[] pairs=[]
run 2 intersection: lines=[] pairs=[]
```

Both exact clean-commit declared gates passed at 2,169 cases, with the
changed-line evaluator returning `0/0` and exit zero. See `P113-LOG.md` for
commands, exits, hashes, discarded no-data diagnostic, and the independent
reviewer's corroborating run.
