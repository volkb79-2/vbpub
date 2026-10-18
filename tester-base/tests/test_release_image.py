"""The release pushes exactly the image the contract tested (tester-base R1). No docker needed:
the script's docker/crane calls go through an injected runner."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import release_image as ri  # noqa: E402

A = "sha256:" + "a" * 64
B = "sha256:" + "b" * 64
D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64
TAGS = ["ghcr.io/volkb79-2/tester-base:0.1.0", "ghcr.io/volkb79-2/tester-base:latest"]


class FakeDocker:
    """Records calls; ids/digests are configurable per tag."""

    def __init__(self, ids: dict[str, str], served: dict[str, str] | None = None, repo: dict[str, str] | None = None) -> None:
        self.ids, self.served, self.repo, self.calls = ids, served or {}, repo or {}, []

    def __call__(self, argv: list[str]) -> str:
        self.calls.append(argv)
        if argv[:3] == ["docker", "image", "inspect"] and "{{.Id}}" in argv:
            return self.ids[argv[-1]]
        if argv[:3] == ["docker", "image", "inspect"]:
            tag = argv[-1]
            return f"{tag.rsplit(':', 1)[0]}@{self.repo[tag]}"
        if argv[:2] == ["docker", "push"]:
            return ""
        if argv[:2] == ["crane", "digest"]:
            return self.served[argv[2]]
        raise AssertionError(argv)


class ReleaseImage(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.record = Path(self.tmp.name) / "build" / "tested-image.json"
        self.addCleanup(self.tmp.cleanup)

    def same_ids(self) -> dict[str, str]:
        return {ri.LOCAL_TAG: A, **{t: A for t in TAGS}}

    def test_the_release_tags_come_from_the_bake_target_and_must_include_the_local_tag(self) -> None:
        bake = json.dumps({"target": {"tester-base-release": {"tags": [ri.LOCAL_TAG, *TAGS]}}})
        self.assertEqual(ri.release_tags(bake), TAGS)
        for bad in ([*TAGS], [ri.LOCAL_TAG]):
            with self.assertRaises(SystemExit):
                ri.release_tags(json.dumps({"target": {"tester-base-release": {"tags": bad}}}))

    def test_the_real_bake_file_has_one_target_carrying_local_and_registry_tags(self) -> None:
        text = (ROOT / "docker-bake.hcl").read_text(encoding="utf-8")
        release = text[text.index('target "tester-base-release"'):]
        self.assertIn('"tester-base:local"', release)
        self.assertIn("${REGISTRY}/${NAMESPACE}/tester-base:${TESTER_BASE_VERSION}", release)

    def test_record_then_verify_passes_for_one_image(self) -> None:
        docker = FakeDocker(self.same_ids())
        self.assertEqual(ri.record(docker, self.record), A)
        self.assertEqual(ri.verify(TAGS, docker, self.record), A)

    def test_verify_refuses_a_release_tag_that_is_not_the_tested_image(self) -> None:
        ri.record(FakeDocker(self.same_ids()), self.record)
        rebuilt = {**self.same_ids(), TAGS[0]: B}  # what a second, floating build would produce
        with self.assertRaises(SystemExit) as ctx:
            ri.verify(TAGS, FakeDocker(rebuilt), self.record)
        self.assertIn("refusing to publish a different image", str(ctx.exception))

    def test_verify_refuses_when_nothing_was_recorded(self) -> None:
        with self.assertRaises(SystemExit):
            ri.verify(TAGS, FakeDocker(self.same_ids()), self.record)

    def test_push_pushes_the_local_tags_and_asserts_the_served_digest(self) -> None:
        ri.record(FakeDocker(self.same_ids()), self.record)
        docker = FakeDocker(self.same_ids(), served={t: D1 for t in TAGS}, repo={t: D1 for t in TAGS})
        self.assertEqual(ri.push(TAGS, docker, self.record), D1)
        self.assertEqual([c for c in docker.calls if c[:2] == ["docker", "push"]], [["docker", "push", t] for t in TAGS])
        self.assertFalse([c for c in docker.calls if "build" in c or "bake" in c], "a push must never rebuild")

    def test_push_fails_when_the_registry_serves_a_different_digest(self) -> None:
        ri.record(FakeDocker(self.same_ids()), self.record)
        docker = FakeDocker(self.same_ids(), served={TAGS[0]: D2, TAGS[1]: D1}, repo={t: D1 for t in TAGS})
        with self.assertRaises(SystemExit) as ctx:
            ri.push(TAGS, docker, self.record)
        self.assertIn("registry serves", str(ctx.exception))

    def test_push_fails_when_the_tags_end_up_at_different_digests(self) -> None:
        ri.record(FakeDocker(self.same_ids()), self.record)
        docker = FakeDocker(self.same_ids(), served={TAGS[0]: D1, TAGS[1]: D2}, repo={TAGS[0]: D1, TAGS[1]: D2})
        with self.assertRaises(SystemExit) as ctx:
            ri.push(TAGS, docker, self.record)
        self.assertIn("different digests", str(ctx.exception))

    def test_push_refuses_before_pushing_when_the_image_changed_after_the_test(self) -> None:
        ri.record(FakeDocker(self.same_ids()), self.record)
        docker = FakeDocker({**self.same_ids(), ri.LOCAL_TAG: B}, served={}, repo={})
        with self.assertRaises(SystemExit):
            ri.push(TAGS, docker, self.record)
        self.assertFalse([c for c in docker.calls if c[:2] == ["docker", "push"]])


class CmruSteps(unittest.TestCase):
    def test_there_is_exactly_one_build_command_and_it_loads(self) -> None:
        import tomllib

        steps = tomllib.loads((ROOT / "cmru.toml").read_text(encoding="utf-8"))["steps"]
        builds = [c["argv"] for s in steps.values() for c in s["commands"] if "bake" in c["argv"] or "build" in c["argv"][1:2]]
        self.assertEqual(len(builds), 1, builds)
        self.assertIn("--load", builds[0])
        self.assertNotIn("--push", builds[0])
        push_argvs = [c["argv"] for c in steps["push"]["commands"]]
        self.assertEqual(push_argvs, [["python3", "scripts/release_image.py", "push"]])
        self.assertEqual([c["argv"] for c in steps["build"]["commands"]], [["python3", "scripts/release_image.py", "verify"]])


if __name__ == "__main__":
    unittest.main()
