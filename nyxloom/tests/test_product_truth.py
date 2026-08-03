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
        """CR-04b moved this fact's source from a compose env var (a SELECTOR,
        meaningful only while two backends existed) to whether `storage.py`
        still selects at all -- which is also CR-04's acceptance, so the
        standing document check and that acceptance became one mechanism."""
        root = _minimal_project(tmp_path)
        store = root / "src" / "nyxloom" / "storage.py"
        _write(store, '"""The store. One implementation, no selector."""\n')
        _write(root / "README.md", "<!-- product-truth:state_backend=sqlite -->\n")
        fact = product_truth.ProductFact(
            "state_backend", "README.md", "d", product_truth.actual_state_backend)
        declared, actual = product_truth.check_fact(root, fact)
        assert declared == actual == "sqlite"

        # Reintroduce a selector -- the machine side flips, the doc does not.
        _write(store, 'import os\n\n'
                       'def enabled():\n'
                       '    return os.environ.get("NYXLOOM_STATE_BACKEND") == "sqlite"\n')
        declared2, actual2 = product_truth.check_fact(root, fact)
        assert declared2 == "sqlite"
        assert actual2 == "selectable"
        assert declared2 != actual2

    def test_state_backend_is_unavailable_without_a_store_module(self, tmp_path):
        """A tree with no `storage.py` cannot establish the fact at all, and
        must say so rather than guessing -- `None` is what the real-repo
        assertion treats as a hard failure."""
        root = _minimal_project(tmp_path)
        assert product_truth.actual_state_backend(root) is None

    def test_containment_posture_mismatch_detected(self, tmp_path):
        """CR-13a. The amendment names "containment requirement" as one of
        the machine-known facts, and this package is exactly the one that
        falsified the old claim -- `secrets.env.example` said agent CLIs
        "inherit this env", which stopped being true when the wrapper stopped
        handing out the daemon's environment.

        Driven through all three postures, in the order they superseded each
        other, so the reader can see the check discriminate rather than
        merely agree with today's tree."""
        root = _minimal_project(tmp_path)
        wrapper = root / "src" / "nyxloom" / "wrapper.py"
        fact = product_truth.ProductFact(
            "containment", "README.md", "d", product_truth.actual_containment)
        _write(root / "README.md", "<!-- product-truth:containment=per-use-container -->\n")

        _write(wrapper, "argv, name = contained_launch(spec, route, env)\n")
        assert product_truth.check_fact(root, fact) == (
            "per-use-container", "per-use-container")

        # Regress to the denylist stopgap: the doc claim does not move.
        _write(wrapper, 'DAEMON_ONLY_ENV = ("AA_API_KEY",)\n')
        declared, actual = product_truth.check_fact(root, fact)
        assert (declared, actual) == ("per-use-container", "env-denylist")

        # ...and regress the whole way, to plain inheritance.
        _write(wrapper, "env = os.environ.copy()\n")
        assert product_truth.check_fact(root, fact)[1] == "inherited"

    def test_containment_is_unavailable_without_the_module_that_launches(self, tmp_path):
        """No wrapper.py means the fact cannot be established at all, which
        the real-repo assertion treats as a hard failure rather than
        guessing a posture."""
        root = _minimal_project(tmp_path)
        assert product_truth.actual_containment(root) is None

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


class TestUnavailableSourcesFailClosed:
    """The other half of every reader: what it returns when the machine
    source it needs is NOT THERE.

    None of these paths is reachable from the real repo -- it has a compose
    file, a trove config and a parsable roadmap -- so the RealRepo class
    above can only ever exercise the happy branch of each reader. That is
    why they went untested while the registry looked covered.

    They matter because `None` is the value the real-repo assertions treat
    as a hard failure (`assert actual is not None`). A reader that swallowed
    a missing source into a plausible-looking string instead would turn the
    whole standing check green on a tree where the fact cannot be
    established at all -- the same unavailable-collapses-to-benign defect
    the core-redesign plan is removing from the daemon's snapshot."""

    def test_a_missing_doc_declares_nothing_rather_than_raising(self, tmp_path):
        """`declared_value` on a path that does not exist. The registry names
        a doc per fact; if that doc is deleted or renamed, the check must
        report "not declared" (which fails) rather than raise, so ONE moved
        file cannot take the whole gate down with an unrelated OSError."""
        root = _minimal_project(tmp_path)
        assert product_truth.declared_value(root, "README.md", "state_backend") is None

    def test_no_compose_file_makes_the_deployment_facts_unavailable(self, tmp_path):
        """Both compose-derived facts read through one loader, so a tree with
        no nyxloomd/docker-compose.yml must report unavailable for both --
        never "files" or "non-resident", which are real values an operator
        would act on."""
        bare = tmp_path / "bare"
        (bare / "nyxloom-trove").mkdir(parents=True)
        assert product_truth.actual_state_backend(bare) is None
        assert product_truth.actual_daemon_mode(bare) is None

    def test_a_compose_file_with_no_nyxloomd_service_is_also_unavailable(self, tmp_path):
        """Present-but-irrelevant is not a reading either: a compose file
        that defines only other services says nothing about the daemon."""
        root = _minimal_project(tmp_path)
        _write(root / "nyxloomd" / "docker-compose.yml", """\
            services:
              ntfy:
                restart: always
            """)
        assert product_truth.actual_daemon_mode(root) is None
        assert product_truth.actual_state_backend(root) is None

    def test_no_config_file_at_all_makes_the_toml_facts_unavailable(self, tmp_path):
        """`_load_toml` on a missing path. merge_mode has no default here on
        purpose -- absent config must read as unavailable, not as whatever
        ProjectConfig.load would have defaulted it to, or the check would
        certify a default nobody wrote down."""
        bare = tmp_path / "bare"
        bare.mkdir()
        assert product_truth.actual_merge_mode(bare) is None
        assert product_truth.actual_gate_ids(bare) == set()
        assert product_truth.actual_trove_dirname(bare) is None

    def test_a_legacy_layout_project_is_read_from_its_own_config(self, tmp_path):
        """The `.nyxloom/project.toml` fallback in `_nyxloom_toml_path`.

        A legacy-layout project has no nyxloom-trove/nyxloom.toml, and the
        readers must follow it there rather than reporting every TOML-backed
        fact unavailable -- otherwise the standing check is silently
        inapplicable to exactly the projects most likely to have stale docs."""
        legacy = tmp_path / "legacy"
        _write(legacy / ".nyxloom" / "project.toml", """\
            [project]
            id = "demo"
            default_branch = "main"
            handoff_globs = ["handoff/*.md"]

            [gates.tester-unified]
            argv = ["true"]
            phase = "implementation"
            timeout_seconds = 60

            [policy]
            merge_mode = "manual"
            """)
        assert product_truth.actual_trove_dirname(legacy) == ".nyxloom"
        assert product_truth.actual_merge_mode(legacy) == "manual"
        assert product_truth.actual_gate_ids(legacy) == {"tester-unified"}

    def test_an_absent_gate_marker_is_not_a_real_gate(self, tmp_path):
        """`check_authoritative_gate` with no marker in STANDING.md. The
        (None, False) pair is what makes the real-repo assertion fail: a
        contract file that stopped declaring which gate is authoritative is
        the STANDING.md defect this fact exists to catch, and deleting the
        marker must not be a way to make the check pass."""
        root = _minimal_project(tmp_path)
        _write(root / "nyxloom-trove" / "STANDING.md", "no marker here\n")
        assert product_truth.check_authoritative_gate(root) == (None, False)

    def test_a_missing_roadmap_yields_no_milestone_set(self, tmp_path):
        root = _minimal_project(tmp_path)
        assert product_truth.actual_active_milestones(root, "nyxloom-trove/3-roadmap.md") is None

    def test_an_unparsable_roadmap_yields_no_milestone_set_rather_than_an_empty_one(
            self, tmp_path):
        """The distinction that carries the safety: an EMPTY active set and
        an UNREADABLE roadmap must not look alike.

        `check_active_milestone`'s caller asserts `{declared} == actual`. If
        a parse failure returned `set()`, a doc that declares M3 would just
        mismatch -- fine. But `assert actual is not None` is what tells the
        operator the roadmap is broken rather than the marker being stale,
        and those are opposite repairs."""
        root = _minimal_project(tmp_path)
        _write(root / "nyxloom-trove" / "3-roadmap.md",
               "---\nmilestones: [oh dear\n---\n\n<!-- product-truth:active_milestone=M3 -->\n")

        declared, actual = product_truth.check_active_milestone(root)

        assert declared == "M3"                  # the marker still reads
        assert actual is None                    # ...but the truth does not
        # And it is NOT the same as a roadmap that parses with nothing active.
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
            ---

            <!-- product-truth:active_milestone=M3 -->
            """)
        assert product_truth.check_active_milestone(root)[1] == set()


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
