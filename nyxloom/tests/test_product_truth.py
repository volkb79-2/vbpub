"""Standing product-truth check (CR-01, DR-04; amendment §3.2).

Ordinary pytest, run by the existing `tester-unified` gate command
unchanged (no gate argv growth) -- this is the "contradiction check that
runs on every package" the amendment mandates, since a one-time cleanup
would already be stale by the time CR-04 deletes the file backend.

Two kinds of test here:

- `Test*RealRepo` classes assert every registered fact against the ACTUAL
  nyxloom project root (this repo, self-hosted) -- both sides read from real,
  already-authoritative sources (nyxloom-trove/nyxloom.toml,
  nyxloomd/docker-compose.yml, sys.version_info, a spine doc's own
  frontmatter), never a second hardcoded literal (F: no
  hardcoded-vs-hardcoded anti-pattern).
- `Test*Discriminates` classes prove each check actually catches drift, by
  pointing the SAME pure functions at a synthetic tmp_path tree with one
  side deliberately mutated. This is the negative case the anti-pattern
  contract requires (C): a check nobody has seen fail is not known to check
  anything.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from nyxloom import product_truth

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# real-repo assertions

class TestRealRepoFacts:
    """Every registered fact, checked against THIS repo (self-hosting)."""

    @pytest.mark.parametrize("fact", product_truth.FACT_REGISTRY, ids=lambda f: f.key)
    def test_fact_agrees_with_machine_truth(self, fact):
        declared, actual = product_truth.check_fact(REPO_ROOT, fact)
        assert declared is not None, (
            f"{fact.key}: no <!-- product-truth:{fact.key}=... --> marker found "
            f"in {fact.doc_relpath}"
        )
        assert actual is not None, (
            f"{fact.key}: machine source unavailable ({fact.description})"
        )
        assert declared == actual, (
            f"{fact.key}: {fact.doc_relpath} declares '{declared}' but the "
            f"machine ({fact.description}) shows '{actual}' -- "
            f"update {fact.doc_relpath} or the underlying config, whichever is stale"
        )

    def test_authoritative_gate_is_a_real_declared_gate(self):
        declared, is_real = product_truth.check_authoritative_gate(REPO_ROOT)
        assert declared is not None, "no product-truth:authoritative_gate marker in STANDING.md"
        assert is_real, (
            f"STANDING.md declares gate id '{declared}' but nyxloom-trove/nyxloom.toml "
            f"has no [gates.{declared}] section"
        )

    def test_active_milestone_marker_matches_roadmap_frontmatter(self):
        declared, actual = product_truth.check_active_milestone(REPO_ROOT)
        assert declared is not None, "no product-truth:active_milestone marker in 3-roadmap.md"
        assert actual is not None, "3-roadmap.md frontmatter unreadable"
        assert {declared} == actual, (
            f"3-roadmap.md's prose marker claims active milestone '{declared}' but its "
            f"OWN frontmatter marks {sorted(actual)} as status=active"
        )


# ---------------------------------------------------------------------------
# discrimination: prove each check catches a real mismatch

def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def _minimal_project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    _write(root / "nyxloom-trove" / "nyxloom.toml", """\
        [project]
        id = "demo"
        default_branch = "main"
        handoff_globs = ["nyxloom-trove/handoffs/*.md"]

        [gates.tester-unified]
        argv = ["true"]
        phase = "implementation"
        timeout_seconds = 60

        [policy]
        merge_mode = "guarded-automatic"
        """)
    _write(root / "nyxloomd" / "docker-compose.yml", """\
        services:
          nyxloomd:
            restart: unless-stopped
            environment:
              NYXLOOM_STATE_BACKEND: "sqlite"
        """)
    return root


class TestDiscriminatesRealMismatch:
    """Each check must flip from pass to fail when ONE side changes --
    otherwise it isn't discriminating, just tautological (anti-pattern F)."""

    def test_state_backend_mismatch_detected(self, tmp_path):
        root = _minimal_project(tmp_path)
        _write(root / "README.md", "<!-- product-truth:state_backend=sqlite -->\n")
        declared, actual = product_truth.check_fact(
            root, product_truth.ProductFact("state_backend", "README.md", "d", product_truth.actual_state_backend)
        )
        assert declared == actual == "sqlite"

        # Flip the MACHINE side only (docs unchanged) -> must now disagree.
        _write(root / "nyxloomd" / "docker-compose.yml", """\
            services:
              nyxloomd:
                restart: unless-stopped
                environment:
                  NYXLOOM_STATE_BACKEND: "files"
            """)
        declared2, actual2 = product_truth.check_fact(
            root, product_truth.ProductFact("state_backend", "README.md", "d", product_truth.actual_state_backend)
        )
        assert declared2 == "sqlite"
        assert actual2 == "files"
        assert declared2 != actual2

    def test_daemon_mode_mismatch_detected(self, tmp_path):
        root = _minimal_project(tmp_path)
        _write(root / "README.md", "<!-- product-truth:daemon_mode=resident -->\n")
        fact = product_truth.ProductFact("daemon_mode", "README.md", "d", product_truth.actual_daemon_mode)
        declared, actual = product_truth.check_fact(root, fact)
        assert declared == actual == "resident"

        # Flip the DOC side only -> must now disagree.
        _write(root / "README.md", "<!-- product-truth:daemon_mode=non-resident -->\n")
        declared2, actual2 = product_truth.check_fact(root, fact)
        assert declared2 == "non-resident"
        assert actual2 == "resident"
        assert declared2 != actual2

    def test_merge_mode_mismatch_detected(self, tmp_path):
        root = _minimal_project(tmp_path)
        _write(root / "README.md", "<!-- product-truth:merge_mode=guarded-automatic -->\n")
        fact = product_truth.ProductFact("merge_mode", "README.md", "d", product_truth.actual_merge_mode)
        declared, actual = product_truth.check_fact(root, fact)
        assert declared == actual == "guarded-automatic"

        toml_path = root / "nyxloom-trove" / "nyxloom.toml"
        toml_path.write_text(
            toml_path.read_text(encoding="utf-8").replace(
                'merge_mode = "guarded-automatic"', 'merge_mode = "manual"'
            ),
            encoding="utf-8",
        )
        declared2, actual2 = product_truth.check_fact(root, fact)
        assert declared2 == "guarded-automatic"
        assert actual2 == "manual"
        assert declared2 != actual2

    def test_trove_path_mismatch_detected(self, tmp_path):
        root = _minimal_project(tmp_path)
        _write(root / "nyxloom-trove" / "README.md", "<!-- product-truth:trove_path=nyxloom-trove -->\n")
        fact = product_truth.ProductFact("trove_path", "nyxloom-trove/README.md", "d", product_truth.actual_trove_dirname)
        declared, actual = product_truth.check_fact(root, fact)
        assert declared == actual == "nyxloom-trove"

        # Simulate a legacy-layout project: no nyxloom-trove/nyxloom.toml,
        # only the old .nyxloom/project.toml -- the machine value flips, the
        # (unmoved) doc claim does not.
        legacy_root = tmp_path / "legacy"
        _write(legacy_root / ".nyxloom" / "project.toml", (root / "nyxloom-trove" / "nyxloom.toml").read_text())
        _write(legacy_root / "nyxloom-trove" / "README.md", "<!-- product-truth:trove_path=nyxloom-trove -->\n")
        declared2, actual2 = product_truth.check_fact(legacy_root, fact)
        assert declared2 == "nyxloom-trove"
        assert actual2 == ".nyxloom"
        assert declared2 != actual2

    def test_interpreter_mismatch_detected(self, tmp_path):
        """The interpreter's `actual` side reads sys.version_info, so the DOC
        side is the one this drives -- through the same check_fact() the
        real-repo test uses, not a bare literal comparison.

        The original of this test asserted `"9.9" != actual_interpreter(...)`,
        which is true for every interpreter that will ever run it: it could
        not fail, so it certified nothing. Anti-pattern C in its own file
        docstring -- a check nobody has seen fail is not known to check
        anything -- and the one fact whose real-repo assertion is therefore
        the only thing standing between a stale STANDING.md interpreter claim
        and the factory. Drive both verdicts."""
        root = _minimal_project(tmp_path)
        fact = product_truth.ProductFact(
            "interpreter", "nyxloom-trove/STANDING.md", "d", product_truth.actual_interpreter)
        running = product_truth.actual_interpreter(root)

        # Agreeing declaration -> the check passes...
        _write(root / "nyxloom-trove" / "STANDING.md",
               f"<!-- product-truth:interpreter={running} -->\n")
        declared, actual = product_truth.check_fact(root, fact)
        assert declared == actual == running

        # ...and a stale one -- exactly STANDING.md's real 2026-08-02 defect,
        # where it named an interpreter version that was not the one running
        # -- must flip it to a mismatch.
        stale = "3.13" if running != "3.13" else "3.12"
        _write(root / "nyxloom-trove" / "STANDING.md",
               f"<!-- product-truth:interpreter={stale} -->\n")
        declared2, actual2 = product_truth.check_fact(root, fact)
        assert declared2 == stale
        assert actual2 == running
        assert declared2 != actual2

        # A doc with no marker at all is a mismatch too, not a silent pass:
        # the real-repo assertion fails closed on `declared is None`.
        _write(root / "nyxloom-trove" / "STANDING.md", "no marker here\n")
        assert product_truth.check_fact(root, fact)[0] is None

    def test_authoritative_gate_rejects_unknown_gate_id(self, tmp_path):
        root = _minimal_project(tmp_path)
        _write(root / "nyxloom-trove" / "STANDING.md", "<!-- product-truth:authoritative_gate=tester-unified -->\n")
        declared, is_real = product_truth.check_authoritative_gate(root)
        assert declared == "tester-unified"
        assert is_real

        _write(root / "nyxloom-trove" / "STANDING.md", "<!-- product-truth:authoritative_gate=nonexistent-gate -->\n")
        declared2, is_real2 = product_truth.check_authoritative_gate(root)
        assert declared2 == "nonexistent-gate"
        assert not is_real2

    def test_active_milestone_rejects_stale_marker(self, tmp_path):
        root = _minimal_project(tmp_path)
        _write(root / "nyxloom-trove" / "3-roadmap.md", """\
            ---
            kind: roadmap
            schema_version: 1
            milestones:
            - id: M1
              title: One
              target_product_version: 1
              features: []
              status: done
            - id: M2
              title: Two
              target_product_version: 1
              features: []
              status: active
            ---

            <!-- product-truth:active_milestone=M1 -->
            """)
        declared, actual = product_truth.check_active_milestone(root)
        assert declared == "M1"
        assert actual == {"M2"}
        assert {declared} != actual


class TestExtractMarker:
    """Pure regex extraction: absent marker -> None; present -> its value;
    a marker elsewhere in the text does not bleed into another key's value."""

    def test_missing_marker_is_none(self):
        assert product_truth.extract_marker("no markers here", "state_backend") is None

    def test_present_marker_extracted(self):
        text = "prose\n<!-- product-truth:state_backend=sqlite -->\nmore prose"
        assert product_truth.extract_marker(text, "state_backend") == "sqlite"

    def test_distinct_keys_do_not_collide(self):
        text = (
            "<!-- product-truth:state_backend=sqlite -->\n"
            "<!-- product-truth:daemon_mode=resident -->\n"
        )
        assert product_truth.extract_marker(text, "state_backend") == "sqlite"
        assert product_truth.extract_marker(text, "daemon_mode") == "resident"
        assert product_truth.extract_marker(text, "merge_mode") is None
