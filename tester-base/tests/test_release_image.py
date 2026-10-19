"""The release pushes exactly the image the contract tested, under the version cmru wrote
(tester-base R1, V1, V2). No docker needed: every docker/crane call goes through an injected runner."""
from __future__ import annotations

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
ENV = {"GITHUB_USERNAME": "volkb79-2", "GITHUB_PUSH_PAT": "ghp_SECRET_TOKEN_VALUE"}


def tags(version: str = "0.2.0") -> list[str]:
    return ri.release_tags(version)


class FakeDocker:
    """Records every call (argv + stdin). Ids are per-tag; registry state is configurable."""

    def __init__(self, ids: dict[str, str], served: dict[str, str] | None = None, repo: dict[str, str] | None = None,
                 existing: set[str] | None = None, fail_push: str | None = None, auth_failure: bool = False,
                 retag_to: str | None = None) -> None:
        self.retag_to = retag_to
        self.ids, self.served, self.repo = ids, served or {}, repo or {}
        self.existing, self.fail_push, self.auth_failure = existing or set(), fail_push, auth_failure
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, argv: list[str], *, stdin: str | None = None, env: dict[str, str] | None = None) -> str:
        self.calls.append((argv, stdin))
        if "login" in argv:
            return "Login Succeeded"
        if "{{.Id}}" in argv:
            return self.ids[argv[-1]]
        if "{{range .RepoDigests}}{{println .}}{{end}}" in argv:
            name = argv[-1]
            return f"{name.rsplit(':', 1)[0]}@{self.repo[name]}"
        if "tag" in argv[:3] and argv[0] == "docker" and argv[1] == "tag":
            self.ids[argv[3]] = self.retag_to or self.ids[argv[2]]
            return ""
        if "push" in argv:
            if argv[-1] == self.fail_push:
                raise SystemExit("release_image: docker push failed (1): connection reset")
            return ""
        if "imagetools" in argv:
            name = argv[argv.index("inspect") + 1]
            if self.auth_failure:
                raise SystemExit("release_image: ... failed (1): unauthorized: authentication required")
            if name in self.existing:
                return self.served.get(name, D1)
            if name in self.served and name not in self.existing and name in self.pushed():
                return self.served[name]
            raise SystemExit("release_image: ... failed (1): ghcr.io/x: not found")
        raise AssertionError(argv)

    def pushed(self) -> set[str]:
        return {c[0][-1] for c in self.calls if "push" in c[0]}

    def argvs(self, word: str) -> list[list[str]]:
        return [a for a, _ in self.calls if word in a]


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.record = Path(self.tmp.name) / "build" / "tested-image.json"
        self.version_file = Path(self.tmp.name) / "VERSION"

    def write_version(self, text: str = "0.2.0\n") -> None:
        self.version_file.write_text(text, encoding="utf-8")

    def build_tested(self) -> FakeDocker:
        docker = FakeDocker({ri.LOCAL_TAG: A})
        ri.record(docker, self.record)
        return docker


class VersionComesFromCmru(Base):
    """V1: the registry version is the VERSION file cmru wrote - no default, ever."""

    def test_a_missing_version_file_fails(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            ri.read_version(self.version_file)
        self.assertIn("missing", str(ctx.exception))

    def test_a_malformed_version_fails_instead_of_defaulting(self) -> None:
        for bad in ("", "latest", "1.2", "v1.2.3", "1.2.3-rc1", "0.1.0 extra"):
            self.write_version(bad)
            with self.assertRaises(SystemExit, msg=repr(bad)):
                ri.read_version(self.version_file)

    def test_the_version_tag_follows_the_file_and_is_pushed_last(self) -> None:
        self.write_version("3.4.5\n")
        self.assertEqual(ri.release_tags(ri.read_version(self.version_file)),
                         [f"{ri.REPOSITORY}:latest", f"{ri.REPOSITORY}:3.4.5"])

    def test_no_version_literal_exists_in_the_script_or_the_bake_file(self) -> None:
        for name in ("scripts/release_image.py", "docker-bake.hcl"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotRegex(text, r"\b0\.1\.0\b", f"{name} carries a default version")
            self.assertNotIn("TESTER_BASE_VERSION", text)

    def test_the_bake_file_has_one_unversioned_target(self) -> None:
        text = (ROOT / "docker-bake.hcl").read_text(encoding="utf-8")
        self.assertEqual(text.count('target "'), 1)
        self.assertNotIn("ghcr.io", text)

    def test_tag_gives_the_tested_image_the_version_from_the_file_without_rebuilding(self) -> None:
        docker = self.build_tested()
        self.write_version("0.2.0\n")
        names = ri.tag(self.version_file, docker, self.record)
        self.assertEqual(names, tags("0.2.0"))
        self.assertEqual([a[:4] for a in docker.argvs("tag") if a[:2] == ["docker", "tag"]], [["docker", "tag", ri.LOCAL_TAG, n] for n in names])
        self.assertFalse(docker.argvs("build") or docker.argvs("bake"))

    def test_tag_fails_without_a_version_file(self) -> None:
        with self.assertRaises(SystemExit):
            ri.tag(self.version_file, self.build_tested(), self.record)

    def test_tag_refuses_when_local_is_not_the_tested_image(self) -> None:
        self.build_tested()
        self.write_version()
        with self.assertRaises(SystemExit) as ctx:
            ri.tag(self.version_file, FakeDocker({ri.LOCAL_TAG: B}), self.record)
        self.assertIn("not the image the contract tested", str(ctx.exception))

    def test_the_id_is_reverified_after_tagging_in_the_step_that_knows_the_version(self) -> None:
        self.build_tested()
        self.write_version()
        docker = FakeDocker({ri.LOCAL_TAG: A}, retag_to=B)  # a name that ends up on a different image
        with self.assertRaises(SystemExit) as ctx:
            ri.tag(self.version_file, docker, self.record)
        self.assertIn("refusing to publish a different image", str(ctx.exception))

    def test_a_second_release_gets_a_second_tag(self) -> None:
        for version in ("0.2.0", "0.3.0"):
            docker = self.build_tested()
            self.write_version(version)
            self.assertIn(f"{ri.REPOSITORY}:{version}", ri.tag(self.version_file, docker, self.record))


class PushIsAuthenticatedAndOrdered(Base):
    """V2: explicit login, PAT on stdin only, version tag last, documented partial-publish."""

    def prepared(self, **kwargs) -> FakeDocker:
        docker = FakeDocker({ri.LOCAL_TAG: A}, served={t: D1 for t in tags()}, repo={t: D1 for t in tags()}, **kwargs)
        ri.record(docker, self.record)
        self.write_version("0.2.0\n")
        ri.tag(self.version_file, docker, self.record)
        return docker

    def test_push_logs_in_with_the_pat_on_stdin_and_never_on_argv(self) -> None:
        docker = self.prepared()
        ri.push(self.version_file, docker, self.record, ENV)
        logins = [(a, s) for a, s in docker.calls if "login" in a]
        self.assertEqual(len(logins), 1)
        argv, stdin = logins[0]
        self.assertIn("--password-stdin", argv)
        self.assertEqual(stdin, ENV["GITHUB_PUSH_PAT"] + "\n")
        for call_argv, _ in docker.calls:
            self.assertNotIn(ENV["GITHUB_PUSH_PAT"], " ".join(call_argv))
        self.assertIn("--config", argv, "login must target an isolated docker config, not the ambient one")

    def test_every_registry_call_uses_the_isolated_config(self) -> None:
        docker = self.prepared()
        ri.push(self.version_file, docker, self.record, ENV)
        for argv, _ in docker.calls:
            if "push" in argv or "imagetools" in argv or "login" in argv:
                self.assertEqual(argv[1], "--config", argv)

    def test_push_without_credentials_fails_before_any_registry_call(self) -> None:
        docker = self.prepared()
        before = len(docker.calls)
        for env in ({}, {"GITHUB_USERNAME": "u"}, {"GITHUB_PUSH_PAT": "t"}, {"GITHUB_USERNAME": " ", "GITHUB_PUSH_PAT": "t"}):
            with self.assertRaises(SystemExit, msg=str(env)):
                ri.push(self.version_file, docker, self.record, env)
        self.assertEqual(len(docker.calls), before)

    def test_the_version_tag_is_pushed_last_and_latest_before_it(self) -> None:
        docker = self.prepared()
        ri.push(self.version_file, docker, self.record, ENV)
        order = [a[-1] for a in docker.argvs("push")]
        self.assertEqual(order, [f"{ri.REPOSITORY}:latest", f"{ri.REPOSITORY}:0.2.0"])

    def test_a_failed_version_push_leaves_latest_moved_and_the_version_unpublished(self) -> None:
        docker = self.prepared(fail_push=f"{ri.REPOSITORY}:0.2.0")
        with self.assertRaises(SystemExit):
            ri.push(self.version_file, docker, self.record, ENV)
        self.assertEqual([a[-1] for a in docker.argvs("push")], [f"{ri.REPOSITORY}:latest", f"{ri.REPOSITORY}:0.2.0"])
        self.assertIn("Partial publish", Path(ri.__file__).read_text(encoding="utf-8"))

    def test_an_existing_version_tag_is_never_overwritten(self) -> None:
        docker = self.prepared(existing={f"{ri.REPOSITORY}:0.2.0"})
        with self.assertRaises(SystemExit) as ctx:
            ri.push(self.version_file, docker, self.record, ENV)
        self.assertIn("never overwrites", str(ctx.exception))
        self.assertEqual(docker.argvs("push"), [])

    def test_an_unauthorised_lookup_is_not_read_as_a_free_tag(self) -> None:
        docker = self.prepared(auth_failure=True)
        with self.assertRaises(SystemExit) as ctx:
            ri.push(self.version_file, docker, self.record, ENV)
        self.assertIn("unauthorized", str(ctx.exception))
        self.assertEqual(docker.argvs("push"), [])

    def test_push_refuses_before_pushing_when_the_image_changed_after_the_test(self) -> None:
        docker = self.prepared()
        docker.ids[ri.LOCAL_TAG] = B
        with self.assertRaises(SystemExit):
            ri.push(self.version_file, docker, self.record, ENV)
        self.assertEqual(docker.argvs("push"), [])

    def test_push_fails_when_the_registry_serves_a_different_digest(self) -> None:
        docker = self.prepared()
        docker.served[f"{ri.REPOSITORY}:latest"] = D2
        with self.assertRaises(SystemExit) as ctx:
            ri.push(self.version_file, docker, self.record, ENV)
        self.assertIn("registry serves", str(ctx.exception))

    def test_push_returns_the_one_digest_all_tags_share(self) -> None:
        docker = self.prepared()
        self.assertEqual(ri.push(self.version_file, docker, self.record, ENV), D1)

    def test_the_isolated_config_is_removed_afterwards(self) -> None:
        import glob
        import tempfile as tf

        before = set(glob.glob(f"{tf.gettempdir()}/tester-base-docker-config-*"))
        docker = self.prepared(fail_push=f"{ri.REPOSITORY}:0.2.0")
        with self.assertRaises(SystemExit):
            ri.push(self.version_file, docker, self.record, ENV)
        self.assertEqual(set(glob.glob(f"{tf.gettempdir()}/tester-base-docker-config-*")), before)


class CmruSteps(unittest.TestCase):
    def test_the_step_order_versions_late_and_builds_once(self) -> None:
        import tomllib

        steps = tomllib.loads((ROOT / "cmru.toml").read_text(encoding="utf-8"))["steps"]
        builds = [c["argv"] for s in steps.values() for c in s["commands"] if "bake" in c["argv"]]
        self.assertEqual(len(builds), 1, builds)
        self.assertIn("--load", builds[0])
        self.assertIn("tester-base-local", builds[0])
        self.assertEqual([c["argv"][-1] for c in steps["prepare"]["commands"]][-1], "record")
        self.assertEqual([c["argv"] for c in steps["build"]["commands"]], [["python3", "scripts/release_image.py", "tag"]])
        self.assertEqual([c["argv"] for c in steps["push"]["commands"]], [["python3", "scripts/release_image.py", "push"]])
        self.assertEqual(steps["push"]["required_env"], ["GITHUB_USERNAME", "GITHUB_PUSH_PAT"])

    def test_prepare_never_tags_a_version(self) -> None:
        import tomllib

        prepare = tomllib.loads((ROOT / "cmru.toml").read_text(encoding="utf-8"))["steps"]["prepare"]["commands"]
        for command in prepare:
            self.assertNotIn("tag", command["argv"][2:3])


if __name__ == "__main__":
    unittest.main()
