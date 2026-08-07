"""O1/O2/O3 — the Cobertura XML parser, driven through
:func:`assay.coverage.load_coverage_profile`.

Cobertura has no prior art in this estate (handoff item 6). Its shape is
cross-checked against a real sample this estate already has
(``/workspaces/netcup-api-filter/coverage.xml``, produced by coverage.py
7.15.2) for reference only; every fixture here is independently hand-written
(A-080), not copied from that file.

Negative (O1): removing this format from the registry, or binding it to
Python (rejecting a ``.java``/``.ts`` filename, say), changes a literal
normalized fixture. Negative (O2): ignoring a malformed ``<line>``/``<class>``
element, or selecting a parser from sniffed content rather than the declared
one, makes a reject fixture load. Negative (O3): reporting
``excluded=frozenset()`` instead of ``None`` falsely claims this format can
say "zero exclusions" — the Cobertura DTD's ``<line>`` element has no such
attribute at all.
"""

from __future__ import annotations

import pytest

from assay.coverage import FileCoverage, load_coverage_profile
from assay.coverage_parsers import cobertura
from assay.errors import AssayError, Outcome, ReasonCode

#: A `.java` filename (Cobertura's own origin, not Python) proves no language
#: binding: nothing in this parser inspects the filename's extension.
BASIC_ARTIFACT = """\
<?xml version="1.0" ?>
<coverage version="1.0" line-rate="0.75" branch-rate="0">
  <packages>
    <package name="pkg" line-rate="0.75">
      <classes>
        <class name="Mod" filename="pkg/Mod.java" line-rate="0.75">
          <methods/>
          <lines>
            <line number="1" hits="1"/>
            <line number="2" hits="1"/>
            <line number="3" hits="0"/>
            <line number="6" hits="1"/>
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
"""

#: Two <class> elements sharing one file -- the DTD permits it, and this
#: parser merges their line hits (executed wins on conflict) rather than
#: assuming one <class> per file.
MULTI_CLASS_SAME_FILE = """\
<coverage>
  <packages>
    <package name="pkg">
      <classes>
        <class name="a" filename="pkg/shared.py">
          <lines><line number="1" hits="0"/><line number="2" hits="1"/></lines>
        </class>
        <class name="b" filename="pkg/shared.py">
          <lines><line number="1" hits="5"/><line number="3" hits="0"/></lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
"""


def test_basic_artifact_normalizes_to_the_expected_file_coverage():
    profile = load_coverage_profile(BASIC_ARTIFACT, declared_format="cobertura")
    assert profile.files["pkg/Mod.java"] == FileCoverage(
        executed=frozenset({1, 2, 6}), missing=frozenset({3}), excluded=None
    )


def test_class_with_no_lines_element_at_all_reports_an_empty_file():
    # The DTD makes <lines> optional on <class> (e.g. an empty module) --
    # this is not malformed, just a file with nothing to report.
    artifact = (
        "<coverage><packages><package name='p'><classes>"
        "<class name='c' filename='empty.py'><methods/></class>"
        "</classes></package></packages></coverage>"
    )
    profile = load_coverage_profile(artifact, declared_format="cobertura")
    assert profile.files["empty.py"] == FileCoverage(
        executed=frozenset(), missing=frozenset(), excluded=None
    )


def test_excluded_is_always_none_for_this_format():
    profile = load_coverage_profile(BASIC_ARTIFACT, declared_format="cobertura")
    assert profile.files["pkg/Mod.java"].excluded is None


def test_two_classes_sharing_a_file_merge_with_executed_winning_on_conflict():
    profile = load_coverage_profile(MULTI_CLASS_SAME_FILE, declared_format="cobertura")
    # line 1: class a says 0 hits, class b says 5 -> executed wins.
    assert profile.files["pkg/shared.py"] == FileCoverage(
        executed=frozenset({1, 2}), missing=frozenset({3}), excluded=None
    )


@pytest.mark.parametrize(
    "artifact",
    [
        pytest.param(
            "<coverage><packages><package name='p'><classes>"
            "<class name='c' filename='bad.py'><lines>"
            "<line number='1' hits='1'>"  # unclosed <line>, unclosed ancestors
            "</lines></class></classes></package></packages>",
            id="not-well-formed-xml",
        ),
        pytest.param(
            "<coverage><packages><package name='p'><classes>"
            "<class name='c'><lines><line number='1' hits='1'/></lines></class>"
            "</classes></package></packages></coverage>",
            id="class-element-has-no-filename",
        ),
        pytest.param(
            "<coverage><packages><package name='p'><classes>"
            "<class name='c' filename='bad.py'><lines>"
            "<line number='x' hits='1'/></lines></class>"
            "</classes></package></packages></coverage>",
            id="line-number-not-an-integer",
        ),
        pytest.param(
            "<coverage><packages><package name='p'><classes>"
            "<class name='c' filename='bad.py'><lines>"
            "<line number='1'/></lines></class>"
            "</classes></package></packages></coverage>",
            id="line-element-has-no-hits-attribute",
        ),
        pytest.param(
            "<coverage><packages><package name='p'><classes>"
            "<class name='c' filename='bad.py'><lines>"
            "<line number='0' hits='1'/></lines></class>"
            "</classes></package></packages></coverage>",
            id="line-number-not-positive",
        ),
        pytest.param(
            "<coverage><packages><package name='p'><classes>"
            "<class name='c' filename='bad.py'><lines>"
            "<line number='1' hits='-1'/></lines></class>"
            "</classes></package></packages></coverage>",
            id="hits-negative",
        ),
        pytest.param(
            # sniffs true ("<coverage" appears, inside a comment) but the
            # ROOT element is not <coverage> -- exercises the sniff/parse
            # boundary directly: sniffing is a cheap substring check, never
            # a stand-in for real structural validation.
            "<report><!-- <coverage --><data/></report>",
            id="root-element-is-not-coverage",
        ),
    ],
)
def test_malformed_xml_raises_unreadable_artifact(artifact: str):
    with pytest.raises(AssayError) as excinfo:
        load_coverage_profile(artifact, declared_format="cobertura")
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_declared_format_mismatch_is_refused_before_any_record_is_read():
    lcov_text = "SF:a.c\nDA:1,1\nend_of_record\n"
    with pytest.raises(AssayError) as excinfo:
        load_coverage_profile(lcov_text, declared_format="cobertura")
    assert excinfo.value.reason_code is ReasonCode.FORMAT_MISMATCH


def test_sniff_is_a_cheap_signature_check_not_a_parse():
    assert cobertura.sniff(BASIC_ARTIFACT) is True
    assert cobertura.sniff("SF:a.c\nend_of_record\n") is False
    assert cobertura.sniff("mode: set\n") is False
