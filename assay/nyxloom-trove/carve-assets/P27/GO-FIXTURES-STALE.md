# The committed Go coverage fixtures contradict A-172's own finding

Recorded by C-sol-1, 2026-08-11, from the Fable full-codebase review at
`e41ea99f`. **This is a tracked pre-P27-re-carve blocker (A-234), not a defect
in anything that runs today.**

## What is wrong

`tests/fixtures/go/hello/hello.out` was hand-shaped to the assumption that a
cover block's extent equals its statement lines — the exact premise A-172's
probe disproved and A-217 ruled on. Its own header says to regenerate it from
real output; nobody has, because the devcontainer has no Go toolchain (A-042).

The P27 carve pinned `tester-unified-go:local`, so a real answer is now
available. Regenerated with `go1.25.12` (`--network=none`, `GOTOOLCHAIN=local`)
using the recipe in `hello.go`'s own header:

```text
COMMITTED   hello/hello.go:29.34,30.42 1 1     hello/hello.go:36.37,37.44 1 0
REAL        hello/hello.go:29.32,31.2  1 1     hello/hello.go:36.35,38.2  1 0
```

The real output is `witness/coverage-hello-fixture-REAL.out`. The committed
fixture is wrong in **both** coordinates, which is more than the review claimed:

* **End position.** Real blocks end at `Rbrace + 1` — line 31 column 2, where
  `}` sits at column 1. A-218 states that arithmetic ("`Rbrace + 1` is always
  ≥ 2") and P27's own probe shows it directly (`calc.go:4.24,6.2` for a
  one-statement function spanning lines 4–6). The committed fixture ends at
  `30.42`, the end of the `return` statement, which `cmd/cover` never emits.
* **Start column.** Real is `29.32`; committed is `29.34`. Guessed, and wrong
  by two.

The same shape applies to `tests/fixtures/canary/go/greet/greet_control.out`.

`hello.go`'s docstring also claims the closing braces are "untracked by any
block". That is false of real profiles: under the shipped
`range(start, end + 1)` expansion, real block `29..31` puts line 31 — the
closing brace — into the executed set.

## Why it is not fixed here

Regenerating alone would not be an improvement. `test_adapters_go_union_fidelity.py`
encodes the current expected mapping, so a real profile changes what that test
asserts — and the *correct* new expectation depends on the statement-position
oracle A-217 ruled for and P27's re-carve owns. Swapping a hand-guessed fixture
for a real one while the parser still over-approximates would replace a wrong
profile with a real profile whose block extents are then read as statement
truth, which is precisely the conflation A-O19 exists to remove.

So: the real bytes are captured as evidence, the fixtures and their consumers
carry stale banners, and the correction is sequenced into the re-carve where the
oracle exists to make it meaningful.

## What the P27 re-carve owes

1. Regenerate both fixtures from the pinned toolchain (recipe above; the real
   `hello.out` bytes are already here to check against).
2. Update `test_adapters_go_union_fidelity.py`'s expected mapping to whatever
   the option-2 oracle yields — **not** to the raw block expansion.
3. Delete `hello.go`'s "untracked by any block" claim.
4. Fix `adapters/go.py:470`'s `_inject_uncovered_line` description, which
   hardcodes "(2 uncovered lines)": statement truth is 2, but real-profile line
   truth under the current expansion is 4. Same family.

Until then, nobody should cite these fixtures as evidence that Go union
fidelity is proven (A-009/A-042's premise). They are evidence of what P08
believed before A-172 measured it.
