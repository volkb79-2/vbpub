"""The release contract in cmru.toml publishes the wheel that the build actually produces.

test-policy-v0.1.0 failed at its push step: the tag prefix is `test-policy` but the
distribution is `estate-test-policy`, and `cmru handler wheel-publish` without `--glob`
defaults to `<prefix with - as _>-*.whl`, which matched nothing in dist/. The dry run
did not catch it (cmru KI-201).
"""

import fnmatch
import re
import tomllib
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def wheel_filename() -> str:
    """The file name the build produces, per the wheel spec's distribution-name escaping."""
    name = tomllib.loads((PROJECT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["name"]
    return re.sub(r"[-_.]+", "_", name).lower() + "-0.1.0-py3-none-any.whl"


def publish_glob() -> str:
    """The glob of the one wheel-publish command, or cmru's documented default for its prefix."""
    config = tomllib.loads((PROJECT / "cmru.toml").read_text(encoding="utf-8"))
    argvs = [
        command["argv"]
        for step in config["steps"].values()
        for command in step.get("commands", [])
        if command["argv"][:3] == ["cmru", "handler", "wheel-publish"]
    ]
    assert len(argvs) == 1, argvs
    argv = argvs[0]
    flags = dict(zip(argv[3::2], argv[4::2]))
    return flags.get("--glob", flags["--prefix"].replace("-", "_") + "-*.whl")


def test_publish_glob_matches_the_built_wheel():
    assert fnmatch.fnmatch(wheel_filename(), publish_glob()), (wheel_filename(), publish_glob())


def test_publish_glob_does_not_match_another_distribution():
    assert not fnmatch.fnmatch("test_policy-0.1.0-py3-none-any.whl", publish_glob())
