"""The Rust adapter's missing-LCOV classification fails closed on ambiguity."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from assay.adapters.rust import RustAdapter


ADAPTER = RustAdapter()


HAND_MUTANT_CASES = [
    ("body-present", "pub fn work() {}\n", True),
    ("async-body-present", "async fn work() -> u8 { 1 }\n", True),
    ("generic-body-present", "pub fn map<T: Copy>(value: T) -> T { value }\n", True),
    ("lifetime-is-not-char", "fn work<'a>(value: &'a str) -> &'a str { value }\n", True),
    ("trait-methods-have-no-body", "pub trait Work { fn call(&self); fn value(&self) -> u8; }\n", False),
    ("function-pointer-has-no-body", "type Callback = fn() -> u8;\n", False),
    ("declarations-have-no-body", "pub struct Data;\nconst LIMIT: usize = 4;\nuse core::fmt;\n", False),
    ("nested-comments-hide-fn", "// fn commented() {}\n/* fn outer() { /* comment */ fn inner() {} } */\n", False),
    ("normal-string-hides-fn", 'const TEXT: &str = "fn hidden() {}";\n', False),
    ("raw-string-hides-fn", 'const RAW: &str = r###"start "## fn hidden() {}"###;\n', False),
    ("byte-raw-string-hides-fn", 'const BYTES: &[u8] = br#"fn hidden() {}"#;\n', False),
    ("c-string-hides-fn", 'const C_TEXT: &CStr = c"fn hidden() {}";\n', False),
    ("char-and-byte-char-are-masked", "const CHAR: char = 'f';\nconst BYTE: u8 = b'f';\n", False),
    ("c-prefixed-char-is-ambiguous", "const BYTE: u8 = c'fn';\n", True),
    ("raw-identifier-fn-is-not-a-function", "let r#fn = if true { 1 } else { 2 };\n", False),
    ("bare-fn-is-ambiguous", "fn", True),
    ("unterminated-string-fails-closed", 'const TEXT: &str = "unterminated', True),
    ("unterminated-comment-fails-closed", "/* unterminated comment", True),
    ("ambiguous-apostrophe-fails-closed", "' maybe", True),
]


# Each case mutates one production guard in rust.py. The source input is the
# ordinary oracle: the real adapter must classify it as expected, while the
# hand-mutated implementation must disagree. This keeps guard-strength
# evidence in the same test lane as the adapter contract.
HAND_MUTANTS = [
    (
        "raw-close-requires-all-hashes",
        'closing = \'"\' + hashes',
        'closing = \'"\'',
        'const RAW: &str = r###"start "## fn hidden() {}"###;\n',
        False,
    ),
    (
        "escaped-quote-does-not-end-string",
        'if char == "\\\\":',
        'if char == "\\0":',
        'const TEXT: &str = "escaped \\\" fn hidden() {}";\n',
        False,
    ),
    (
        "nested-comment-depth-increments",
        "depth += 1",
        "depth -= 1",
        "/* outer /* fn hidden() {} */ still outer */\n",
        False,
    ),
    (
        "unterminated-block-comment-fails-closed",
        "if depth:\n                return None\n            _blank(output, text, start, cursor)",
        "if False:\n                return None\n            _blank(output, text, start, cursor)",
        "/* unterminated comment",
        True,
    ),
    (
        "unterminated-raw-string-fails-closed",
        "return None if end < 0 else end + len(closing)",
        "return len(text) if end < 0 else end + len(closing)",
        'const RAW: &str = r###"unterminated',
        True,
    ),
    (
        "unterminated-normal-string-fails-closed",
        "    return None\n\n\ndef _char_literal_end",
        "    return len(text)\n\n\ndef _char_literal_end",
        'const TEXT: &str = "unterminated',
        True,
    ),
    (
        "ambiguous-apostrophe-fails-closed",
        "if lifetime_end is None:\n                return None",
        "if lifetime_end is None:\n                cursor += 1\n                continue",
        "' maybe",
        True,
    ),
    (
        "invalid-byte-char-escape-fails-closed",
        '        else:\n            return None\n    else:\n        cursor += 1',
        '        else:\n            cursor += 1\n    else:\n        cursor += 1',
        "const BYTE: u8 = b'\\q';\n",
        True,
    ),
    (
        "balanced-parens-do-not-trigger-underflow",
        "if parens < 0:",
        "if parens <= 0:",
        "trait Work { fn call(&self); }\n",
        False,
    ),
    (
        "balanced-brackets-do-not-trigger-underflow",
        "if brackets < 0:",
        "if brackets <= 0:",
        "trait Work { fn call(&self, values: [u8; 3]); }\n",
        False,
    ),
    (
        "declaration-semicolon-means-no-body",
        'if char == ";":\n                return False',
        'if char == ";":\n                return None',
        "pub trait Work { fn call(&self); }\n",
        False,
    ),
    (
        "opening-brace-means-function-body",
        'if char == "{":\n                return True',
        'if char == "{":\n                return False',
        "fn work() {}\n",
        True,
    ),
    (
        "incomplete-function-signature-is-ambiguous",
        "if _function_body_state(masked, start) is not False:",
        "if _function_body_state(masked, start) is True:",
        "fn",
        True,
    ),
    (
        "raw-identifier-is-not-the-fn-keyword",
        'if start >= 2 and masked[start - 2 : start] == "r#" and _has_identifier_boundary(masked, start - 2):\n            continue',
        'if False:\n            continue',
        "let r#fn = if true { 1 } else { 2 };\n",
        False,
    ),
]


def _adapter_with_hand_mutant(monkeypatch, case):
    name, before, after, _source, _expected = case
    adapter_path = Path(__file__).resolve().parents[3] / "src/assay/adapters/rust.py"
    original = adapter_path.read_text(encoding="utf-8")
    assert original.count(before) == 1, f"{name}: mutation anchor must be unique"
    module_name = f"assay.adapters._rust_hand_mutant_{name.replace('-', '_')}"
    module = types.ModuleType(module_name)
    module.__package__ = "assay.adapters"
    monkeypatch.setitem(sys.modules, module_name, module)
    mutated = original.replace(before, after, 1)
    exec(compile(mutated, f"<{name}>", "exec"), module.__dict__)
    return module.RustAdapter()


@pytest.mark.parametrize(("case_name", "source", "expected"), HAND_MUTANT_CASES)
def test_missing_profile_code_classification_matches_the_fail_closed_cases(
    case_name: str, source: str, expected: bool
):
    actual = ADAPTER.has_executable_code("src/lib.rs", source)
    assert actual is expected, f"input case {case_name!r} classified incorrectly"


@pytest.mark.parametrize("case", HAND_MUTANTS, ids=[case[0] for case in HAND_MUTANTS])
def test_hand_mutation_of_each_rust_classification_guard_is_killed(case, monkeypatch):
    name, _before, _after, source, expected = case
    assert ADAPTER.has_executable_code("src/lib.rs", source) is expected

    mutant = _adapter_with_hand_mutant(monkeypatch, case)
    actual = mutant.has_executable_code("src/lib.rs", source)
    assert actual is not expected, f"hand mutant {name!r} survived the classification oracle"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/tests.rs", True),
        ("src/math_tests.rs", True),
        ("src/math-tests.rs", True),
        ("crates/demo/tests/integration.rs", True),
        ("crates/demo/examples/show.rs", True),
        ("crates/demo/benches/measure.rs", True),
        ("crates/demo/src/tests/mod.rs", True),
        ("crates/demo/src/test_support.rs", False),
        ("crates/demo/src/test.rs", False),
    ],
)
def test_test_path_conventions_match_cargo_llvm_cov_defaults(path: str, expected: bool):
    assert ADAPTER.is_test_path(path) is expected


def test_test_path_matching_normalizes_windows_separators():
    assert ADAPTER.is_test_path(r"crates\demo\tests\integration.rs")
