# Origin date: 2026-11-12
# Status: persisted test for fast/reference identity over the imagegen matrix
# Provenance: copied from the controller program directory 2026-11-12
"""Fast-vs-reference identity of glyphgrid over imagegen's transform matrix."""
import json
import os
import sys

import pytest

import _testdeps

_testdeps.optional_module("PIL", "Pillow renders the generated images", module_level=True)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools", "glyphgrid"))
import glyphgrid as gg  # noqa: E402
from imagegen import fonts as igfonts  # noqa: E402
from imagegen.render import generate  # noqa: E402
from imagegen.suite import SCREENS, TRANSFORMS  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "glyphgrid")
BITMAP_FONTS = tuple(igfonts.BITMAP_IDS)
# Every transform on the realistic screen for every bitmap font; the other two screens on a representative spread.
SPREAD = ("clean", "nn-upscale:3", "offset:1,1", "scale:1.5,lanczos", "blur:0.8", "jpeg:50", "noise:8", "subpixel:0.33,fringe")
CASES = [(f, "installer", name, chain) for f in BITMAP_FONTS for name, chain in TRANSFORMS]
CASES += [(f, s, name, chain) for f in BITMAP_FONTS for s in SCREENS if s != "installer"
          for name, chain in TRANSFORMS if name in SPREAD]


def canon(result):
    return json.dumps(result, sort_keys=True, default=str)


@pytest.fixture(scope="module")
def atlas():
    return gg.Atlas.load(os.path.join(FIX, "fonts.atlas.json"))


@pytest.mark.parametrize("font,screen,name,chain", CASES, ids=[f"{c[0]}-{c[1]}-{c[2]}" for c in CASES])
def test_fast_path_is_identical_to_reference_on_the_generator_suite(atlas, font, screen, name, chain):
    png, _truth = generate(font, screen, chain, seed=1)
    fast = canon(gg.read_image(png, atlas))
    with gg.reference_mode():
        reference = canon(gg.read_image(png, atlas))
    assert fast == reference, f"{font}/{screen}/{name}: fast path diverged from the reference path"


def test_suite_matrix_is_the_documented_size():
    # A changed matrix must be a conscious change of this test's coverage, not a silent shrink.
    assert len(TRANSFORMS) == 20 and set(SCREENS) == {"alphabet", "installer", "random"}
    assert len(BITMAP_FONTS) >= 3 and len(CASES) == len(BITMAP_FONTS) * (20 + 2 * len(SPREAD))
