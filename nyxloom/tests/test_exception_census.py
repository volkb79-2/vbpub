"""CR-02b (DR-03): the broad-exception census and its allow-list oracle.

Two kinds of test, for the same reason test_product_truth.py has two:

- the `TestThisRepo` class runs the oracle against the REAL source tree, so
  the rules bind the code that ships;
- the rest drive the scanner over synthetic modules, so every rule is seen to
  FAIL as well as pass. An allow-list that has never rejected anything is not
  known to reject anything.

Determinism: everything here is a pure AST read of text. No clock, no
subprocess, no wall-clock budget, nothing process-global mutated.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from nyxloom import exception_census as census

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "nyxloom"


def _module(tmp_path: Path, body: str, name: str = "probe.py") -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# the oracle, against this repo


class TestThisRepo:

    def test_no_unclassified_broad_handler_in_any_fan_in_path(self):
        """CR-02's acceptance, stated mechanically: "a source audit finds no
        unclassified broad exception in authority-bearing snapshot paths".

        The fan-in is derived structurally (a `builder` parameter or a
        snapshot type by name), so this covers acquisition sites nobody
        remembered to register -- which is the only kind that matters, since
        the registered ones are the ones already reviewed."""
        offenders = [h for h in census.scan_tree(SRC_ROOT) if h.in_fanin and not h.classified]
        assert not offenders, (
            "broad exception handlers on the snapshot fan-in with no census "
            "class:\n  " + "\n  ".join(str(h) for h in offenders) +
            "\nClassify each with a trailing `# census: <class>` comment; the "
            f"vocabulary is {sorted(census.CENSUS_CLASSES)}.")

    def test_the_legacy_budget_is_never_exceeded(self):
        """No new unclassified debt. A handler added to a module without a
        census tag pushes it over budget and fails here."""
        actual = census.unclassified_by_module(census.scan_tree(SRC_ROOT))
        over = {m: (n, census.LEGACY_BUDGET.get(m, 0))
                for m, n in actual.items() if n > census.LEGACY_BUDGET.get(m, 0)}
        assert not over, (
            "modules over their unclassified-handler budget "
            "(actual, budget): " + repr(over) +
            "\nEither classify the new handler with `# census: <class>` or, if "
            "it is genuinely legacy, say why in LEGACY_BUDGET -- do not raise "
            "the number.")

    def test_the_legacy_budget_is_never_stale(self):
        """The half that keeps this a census rather than a ceiling.

        Repaying debt without lowering the number leaves a budget that quietly
        re-authorizes the handler someone just removed. Under budget fails
        too, and the fix is a one-line edit in the same commit."""
        actual = census.unclassified_by_module(census.scan_tree(SRC_ROOT))
        stale = {m: (actual.get(m, 0), budget)
                 for m, budget in census.LEGACY_BUDGET.items()
                 if actual.get(m, 0) < budget}
        assert not stale, (
            "modules whose unclassified-handler budget is now too generous "
            "(actual, budget): " + repr(stale) +
            "\nLower the number in LEGACY_BUDGET to what the tree actually "
            "has; a budget above the real count re-authorizes a handler that "
            "was already retired.")

    def test_every_budgeted_module_still_exists(self):
        """A budget for a deleted module is dead weight that would also hide a
        module reappearing with unreviewed handlers."""
        missing = [m for m in census.LEGACY_BUDGET if not (SRC_ROOT / m).exists()]
        assert not missing, f"LEGACY_BUDGET names modules that no longer exist: {missing}"

    def test_every_declared_class_is_actually_used(self):
        """A vocabulary entry nobody uses is either dead or a sign the code it
        described was retired. Either way the entry should go, so the closed
        list stays a description of the tree rather than an aspiration."""
        used = {h.census_class for h in census.scan_tree(SRC_ROOT) if h.classified}
        unused = census.CENSUS_CLASSES - used
        assert not unused, (
            f"census classes declared but never used: {sorted(unused)}")

    def test_the_fan_in_is_not_empty(self):
        """Guards against the rule passing because the derivation broke.

        If a refactor renamed SnapshotBuilder and `is_fanin_function` stopped
        matching anything, every fan-in assertion above would pass vacuously
        while the boundary went unpoliced."""
        handlers = census.scan_tree(SRC_ROOT)
        assert [h for h in handlers if h.in_fanin], (
            "no fan-in handlers found at all -- is_fanin_function no longer "
            "recognises the snapshot boundary, so its rules are vacuous")
        assert [h for h in handlers if h.classified]


# ---------------------------------------------------------------------------
# the scanner: every rule seen to fail


class TestBroadDetection:

    @pytest.mark.parametrize("clause, broad, why", [
        ("except Exception:", True, "the plain form"),
        ("except BaseException:", True, "also swallows KeyboardInterrupt/SystemExit"),
        ("except:", True, "bare"),
        ("except (ValueError, Exception):", True,
         "a narrow type beside a broad one still catches everything"),
        ("except (ValueError, KeyError):", False, "genuinely narrow"),
        ("except OSError:", False, "genuinely narrow"),
    ])
    def test_breadth_is_decided_by_what_is_caught_not_how_it_reads(
            self, tmp_path, clause, broad, why):
        path = _module(tmp_path, f"""\
            def f():
                try:
                    g()
                {clause}
                    pass
            """)
        found = census.scan_module(path)
        assert bool(found) is broad, why

    def test_a_module_level_handler_is_never_counted_as_fan_in(self, tmp_path):
        """Import-time code cannot be part of a pass, so a module-scope
        handler must not inherit the fan-in rule from a marker elsewhere in
        the file -- otherwise a module that merely IMPORTS SnapshotInput would
        drag its import guard into the strictest rule in the package."""
        path = _module(tmp_path, """\
            try:
                from nyxloom.snapshot import SnapshotInput
            except Exception:
                SnapshotInput = None
            """)
        found = census.scan_module(path)
        assert len(found) == 1
        assert found[0].function == "<module>"
        assert found[0].in_fanin is False


class TestClassParsing:

    def test_a_recognised_class_is_read_off_the_except_line(self, tmp_path):
        path = _module(tmp_path, """\
            def f():
                try:
                    g()
                except Exception:  # census: cleanup/containment (CR-02b)
                    pass
            """)
        assert census.scan_module(path)[0].census_class == "cleanup/containment"

    @pytest.mark.parametrize("comment, why", [
        ("", "no tag at all"),
        ("  # census: whatever-i-felt-like", "a class outside the closed vocabulary"),
        ("  # this one is fine, honest", "prose is not a classification"),
        ("  # census:", "the marker with nothing after it"),
    ])
    def test_anything_but_a_declared_class_reads_as_unclassified(
            self, tmp_path, comment, why):
        """Inventing a fifth class is not a way past the oracle: an
        unrecognised tag is ABSENT, not accepted."""
        path = _module(tmp_path, f"""\
            def f():
                try:
                    g()
                except Exception:{comment}
                    pass
            """)
        found = census.scan_module(path)[0]
        assert found.census_class is None, why
        assert not found.classified

    def test_a_tag_on_a_neighbouring_line_does_not_classify_the_handler(
            self, tmp_path):
        """The tag has to be on the `except` line itself. A comment two lines
        up describes the try block, and reading it as the handler's class
        would let one tag silently cover several handlers."""
        path = _module(tmp_path, """\
            def f():
                # census: cleanup/containment (CR-02b)
                try:
                    g()
                except Exception:
                    pass
                try:
                    h()
                except Exception:
                    pass
            """)
        assert all(h.census_class is None for h in census.scan_module(path))


class TestOffenderRendering:
    """The failure message is the whole value of the oracle: it fires on a
    tree the author has not read, and has to say WHERE without them going
    looking. It is also the only code path that never runs while the repo is
    clean, which is exactly when a broken message would be discovered."""

    def test_an_offender_names_its_module_line_function_and_verdict(self, tmp_path):
        path = _module(tmp_path, """\
            def acquire_something(project, *, builder=None):
                try:
                    read(project)
                except Exception:
                    return None
            """, name="acq.py")
        offender = census.scan_module(path)[0]

        rendered = str(offender)

        assert "acq.py:4" in rendered, rendered
        assert "acquire_something()" in rendered, rendered
        assert "UNCLASSIFIED" in rendered, rendered

    def test_a_classified_handler_renders_its_class_instead(self, tmp_path):
        path = _module(tmp_path, """\
            def f():
                try:
                    g()
                except Exception:  # census: advisory-degradation (CR-02b)
                    pass
            """, name="ok.py")
        rendered = str(census.scan_module(path)[0])
        assert "advisory-degradation" in rendered
        assert "UNCLASSIFIED" not in rendered


class TestFanInDerivation:

    def test_a_builder_parameter_puts_a_function_in_the_fan_in(self, tmp_path):
        path = _module(tmp_path, """\
            def acquire_something(project, *, builder=None):
                try:
                    return read(project)
                except Exception:
                    return None
            """)
        assert census.scan_module(path)[0].in_fanin is True

    def test_naming_a_snapshot_type_puts_a_function_in_the_fan_in(self, tmp_path):
        path = _module(tmp_path, """\
            def acquire_something(project):
                b = SnapshotBuilder()
                try:
                    return b.authoritative("x", lambda: read(project))
                except Exception:
                    return None
            """)
        assert census.scan_module(path)[0].in_fanin is True

    def test_an_ordinary_function_is_not_in_the_fan_in(self, tmp_path):
        path = _module(tmp_path, """\
            def render_a_page(rows):
                try:
                    return format(rows)
                except Exception:
                    return "<error>"
            """)
        assert census.scan_module(path)[0].in_fanin is False

    def test_a_nested_handler_inherits_its_enclosing_function(self, tmp_path):
        """The rule attaches to the function, so a handler nested three blocks
        deep inside an acquisition is still on the fan-in."""
        path = _module(tmp_path, """\
            def acquire_something(project, *, builder=None):
                for item in project:
                    if item:
                        try:
                            read(item)
                        except Exception:
                            pass
            """)
        found = census.scan_module(path)[0]
        assert found.function == "acquire_something"
        assert found.in_fanin is True


class TestBudgetAccounting:

    def test_only_unclassified_handlers_count_against_a_budget(self, tmp_path):
        path = _module(tmp_path, """\
            def f():
                try:
                    g()
                except Exception:  # census: advisory-degradation (CR-02b)
                    pass
                try:
                    h()
                except Exception:
                    pass
            """, name="thing.py")
        counts = census.unclassified_by_module(census.scan_module(path))
        assert counts == {"thing.py": 1}

    def test_a_module_with_nothing_left_to_classify_reports_no_entry(self, tmp_path):
        """Zero must be ABSENT rather than 0, so `test_the_legacy_budget_is
        _never_stale` sees a fully-retired module as stale (budget > 0,
        actual 0) and demands the entry be removed."""
        path = _module(tmp_path, """\
            def f():
                try:
                    g()
                except Exception:  # census: cleanup/containment (CR-02b)
                    pass
            """, name="thing.py")
        assert census.unclassified_by_module(census.scan_module(path)) == {}

    def test_scanning_a_tree_is_deterministically_ordered(self, tmp_path):
        """The oracle's failure message lists offenders; an unstable order
        would make two runs of the same tree produce different diffs."""
        _module(tmp_path, "def a():\n    try:\n        g()\n    except Exception:\n        pass\n", "b_mod.py")
        _module(tmp_path, "def a():\n    try:\n        g()\n    except Exception:\n        pass\n", "a_mod.py")
        order = [h.module for h in census.scan_tree(tmp_path)]
        assert order == sorted(order)
        assert order == [h.module for h in census.scan_tree(tmp_path)]
