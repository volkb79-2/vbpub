"""B048 -- the `vite-plugin-istanbul` artifact, read through the REAL,
unmodified `coverage-istanbul-json` parser.

`tests/fixtures/coverage/coverage-istanbul-json.vite-plugin-istanbul.json` is
real `window.__coverage__` output captured from a REAL `vite build` (plugin
`vite-plugin-istanbul`, `forceBuildInstrument: true`) executed in a real
jsdom environment -- not a Vitest artifact, not a heredoc. See
`tests/fixtures/coverage/PROVENANCE.md`'s own "vite-plugin-istanbul artifact"
section for exactly how it was produced and what it proves.

The one fact this module exists to pin: the plugin instruments PRE-transform
source, so the artifact's keys are the ORIGINAL `src/*.ts` paths, never a
built `dist/assets/*.js` bundle path -- the load-bearing claim for B048's
"Browser coverage of a UI as an R1 lane" recipe (a browser-driven R1 lane
declares `source_roots` against real source, and this is the proof the
producer's own keys actually land there).
"""

from __future__ import annotations

from pathlib import Path

from assay.coverage import load_coverage_profile

FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "coverage"
    / "coverage-istanbul-json.vite-plugin-istanbul.json"
)


def _load():
    return load_coverage_profile(
        FIXTURE.read_text(encoding="utf-8"), declared_format="coverage-istanbul-json"
    )


def test_every_key_is_an_original_src_path_never_a_dist_bundle_path():
    profile = _load()

    assert profile.files, "the fixture itself measured nothing -- regenerate it"
    for key in profile.files:
        assert key.endswith("src/math.ts") or key.endswith("src/main.ts"), key
        assert "/dist/" not in key, key
        assert "assets/" not in key, key


def test_the_real_instrumented_bundle_reports_genuine_partial_coverage():
    """Not a trivially-all-green fixture: `subtract`'s defensive branch
    (`if (a < b)`, its body at line 6) is never taken by the one real call
    (`subtract(10, 4)`) in `main.ts`, so it is the only missing line in
    either file -- a real, measured fact about the executed bundle, not an
    invented one."""
    profile = _load()
    (math_key,) = [key for key in profile.files if key.endswith("src/math.ts")]
    (main_key,) = [key for key in profile.files if key.endswith("src/main.ts")]

    math = profile.files[math_key]
    assert math.missing == frozenset({6})
    assert 6 not in math.executed

    main = profile.files[main_key]
    assert main.missing == frozenset()


def test_the_parser_needed_no_change_for_this_producer():
    """The claim B048 makes explicit: this is the SAME `coverage-istanbul-
    json` format every Vitest artifact in this directory already uses, read
    by the identical, unmodified parser -- `vite-plugin-istanbul` is a third
    producer of one format, not a new format."""
    profile = _load()
    assert profile.files  # parsed without any producer-specific handling
