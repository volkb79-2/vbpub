"""The release pushes exactly the image the contract tested, under the version cmru wrote
(tester-base R1, V1, V2, W1-W5). No docker needed: every docker/git call goes through an injected runner."""
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
COMMIT = "c0ffee" + "0" * 34
PRIVATE = "tester-base:release-c0ffee000000"
ENV = {"GITHUB_USERNAME": "volkb79-2", "GITHUB_PUSH_PAT": "ghp_SECRET_TOKEN_VALUE"}
LATEST = f"{ri.REPOSITORY}:latest"


def names(version: str = "0.2.0") -> list[str]:
    return ri.release_tags(version)


class FakeDocker:
    """Records every call (argv + stdin). Local ids are per-tag; the registry state is configurable."""

    def __init__(self, ids=None, existing: dict[str, str] | None = None, fail_push: str | None = None,
                 lookup_failure: str | None = None, retag_to: str | None = None) -> None:
        self.ids = dict(ids if ids is not None else {PRIVATE: A})
        self.existing = dict(existing or {})  # registry tag -> image id it serves
        self.fail_push, self.lookup_failure, self.retag_to = fail_push, lookup_failure, retag_to
        self.pushed_names: list[str] = []
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, argv: list[str], *, stdin: str | None = None, env: dict[str, str] | None = None) -> str:
        self.calls.append((argv, stdin))
        if argv[:2] == ["git", "rev-parse"]:
            return COMMIT
        if "bake" in argv:
            self.ids[argv[argv.index("--set") + 1].split("=", 1)[1]] = A
            return ""
        if "login" in argv:
            return "Login Succeeded"
        if "{{.Id}}" in argv:
            return self.ids[argv[-1]]
        if "{{range .RepoDigests}}{{println .}}{{end}}" in argv:
            name = argv[-1]
            return f"{name.rsplit(':', 1)[0]}@{D1}"
        if argv[:2] == ["docker", "tag"]:
            self.ids[argv[3]] = self.retag_to or self.ids[argv[2]]
            return ""
        if "manifest" in argv and "inspect" in argv:
            if self.lookup_failure:
                raise SystemExit(f"release_image: ... failed (1): {self.lookup_failure}")
            if argv[-1] in self.existing:
                return "{}"
            raise SystemExit("release_image: ... failed (1): manifest unknown")
        if "push" in argv:
            if argv[-1] == self.fail_push:
                raise SystemExit("release_image: docker push failed (1): connection reset")
            self.pushed_names.append(argv[-1])
            self.existing[argv[-1]] = self.ids[argv[-1]]
            return ""
        if "imagetools" in argv and "--raw" in argv:
            ref = argv[argv.index("inspect") + 1]
            if "@" in ref:
                return json.dumps({"config": {"digest": self.existing[ref.split('@')[0] + ":" + "by-digest"]}})
            return json.dumps({"schemaVersion": 2, "config": {"digest": self.existing[ref]}})
        if "imagetools" in argv:
            return D1
        raise AssertionError(argv)

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

    def built(self, **kwargs) -> FakeDocker:
        docker = FakeDocker(ids={}, **kwargs)
        ri.build(docker, self.record)
        return docker


class PrivateCandidateTag(Base):
    """W5: a release never tests or pushes the shared developer tag."""

    def test_the_tag_is_derived_from_the_source_commit(self) -> None:
        self.assertEqual(ri.private_tag(COMMIT), PRIVATE)
        for bad in ("", "HEAD", "abc", "g" * 40):
            with self.assertRaises(SystemExit):
                ri.private_tag(bad)

    def test_build_bakes_the_private_tag_and_never_the_shared_local_tag(self) -> None:
        docker = self.built()
        bake = docker.argvs("bake")[0]
        self.assertIn(f"tester-base-local.tags={PRIVATE}", bake)
        self.assertNotIn("tester-base:local", " ".join(bake))
        self.assertEqual(ri.recorded(self.record), (PRIVATE, A))

    def test_a_record_naming_the_shared_tag_is_refused(self) -> None:
        self.record.parent.mkdir(parents=True)
        self.record.write_text(json.dumps({"tag": "tester-base:local", "image_id": A}))
        with self.assertRaises(SystemExit) as ctx:
            ri.recorded(self.record)
        self.assertIn("never a release candidate", str(ctx.exception))

    def test_another_session_rebuilding_tester_base_local_cannot_change_what_is_released(self) -> None:
        docker = self.built()
        docker.ids["tester-base:local"] = B  # someone else's rebuild of the shared tag
        self.write_version()
        names_ = ri.tag(self.version_file, docker, self.record)
        self.assertEqual([a[2] for a in docker.argvs("tag") if a[:2] == ["docker", "tag"]], [PRIVATE, PRIVATE])
        self.assertEqual(docker.ids[names_[0]], A)

    def test_the_gate_runs_the_contract_lane_against_the_recorded_private_tag(self) -> None:
        self.built()
        seen = {}

        def fake_exec(argv, env):
            seen["argv"], seen["image"] = argv, env["TESTER_BASE_IMAGE"]
            return 0

        self.assertEqual(ri.gate(fake_exec, self.record), 0)
        self.assertEqual(seen, {"argv": ["./run-gate.py", "contract"], "image": PRIVATE})

    def test_the_gate_cannot_run_without_a_build(self) -> None:
        with self.assertRaises(SystemExit):
            ri.gate(lambda a, e: 0, self.record)

    def test_the_cmru_steps_build_and_gate_through_the_script(self) -> None:
        import tomllib

        cfg = tomllib.loads((ROOT / "cmru.toml").read_text(encoding="utf-8"))
        self.assertEqual([c["argv"] for c in cfg["steps"]["prepare"]["commands"]], [["python3", "scripts/release_image.py", "build"]])
        self.assertEqual([c["argv"] for c in cfg["steps"]["run-tests"]["commands"]], [["python3", "scripts/release_image.py", "gate"]])
        self.assertNotIn("TESTER_BASE_IMAGE", cfg.get("env", {}), "the lane's image comes from the record, not a static env")


class VersionComesFromCmru(Base):
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
        self.assertEqual(ri.release_tags(ri.read_version(self.version_file)), [LATEST, f"{ri.REPOSITORY}:3.4.5"])

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
        docker = self.built()
        self.write_version("0.2.0\n")
        self.assertEqual(ri.tag(self.version_file, docker, self.record), names("0.2.0"))
        self.assertEqual(len(docker.argvs("bake")), 1, "tag must not rebuild")

    def test_tag_fails_without_a_version_file(self) -> None:
        with self.assertRaises(SystemExit):
            ri.tag(self.version_file, self.built(), self.record)

    def test_tag_refuses_when_the_candidate_is_not_the_tested_image(self) -> None:
        docker = self.built()
        docker.ids[PRIVATE] = B
        self.write_version()
        with self.assertRaises(SystemExit) as ctx:
            ri.tag(self.version_file, docker, self.record)
        self.assertIn("not the image the contract tested", str(ctx.exception))

    def test_the_id_is_reverified_after_tagging_in_the_step_that_knows_the_version(self) -> None:
        docker = self.built(retag_to=B)
        self.write_version()
        with self.assertRaises(SystemExit) as ctx:
            ri.tag(self.version_file, docker, self.record)
        self.assertIn("refusing to publish a different image", str(ctx.exception))

    def test_a_second_release_gets_a_second_tag(self) -> None:
        for version in ("0.2.0", "0.3.0"):
            docker = self.built()
            self.write_version(version)
            self.assertIn(f"{ri.REPOSITORY}:{version}", ri.tag(self.version_file, docker, self.record))


class PushIsAuthenticatedAndOrdered(Base):
    def prepared(self, **kwargs) -> FakeDocker:
        docker = self.built(**kwargs)
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
        self.assertIn("--config", argv)

    def test_every_registry_call_uses_the_isolated_config(self) -> None:
        docker = self.prepared()
        ri.push(self.version_file, docker, self.record, ENV)
        for argv, _ in docker.calls:
            if "push" in argv or "imagetools" in argv or "login" in argv or "manifest" in argv:
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
        self.assertEqual(docker.pushed_names, [LATEST, f"{ri.REPOSITORY}:0.2.0"])

    def test_a_failed_version_push_leaves_latest_moved_and_the_version_unpublished(self) -> None:
        docker = self.prepared(fail_push=f"{ri.REPOSITORY}:0.2.0")
        with self.assertRaises(SystemExit):
            ri.push(self.version_file, docker, self.record, ENV)
        self.assertEqual(docker.pushed_names, [LATEST])
        self.assertIn("Partial publish", Path(ri.__file__).read_text(encoding="utf-8"))

    def test_push_refuses_before_pushing_when_the_image_changed_after_the_test(self) -> None:
        docker = self.prepared()
        docker.ids[PRIVATE] = B
        with self.assertRaises(SystemExit):
            ri.push(self.version_file, docker, self.record, ENV)
        self.assertEqual(docker.pushed_names, [])

    def test_the_isolated_config_is_removed_afterwards(self) -> None:
        import glob
        import tempfile as tf

        before = set(glob.glob(f"{tf.gettempdir()}/tester-base-docker-config-*"))
        docker = self.prepared(fail_push=f"{ri.REPOSITORY}:0.2.0")
        with self.assertRaises(SystemExit):
            ri.push(self.version_file, docker, self.record, ENV)
        self.assertEqual(set(glob.glob(f"{tf.gettempdir()}/tester-base-docker-config-*")), before)


class RegistryAnswersOnly(Base):
    """W1: only the registry's own 'does not exist' answer means the tag is free."""

    REAL_NOT_FOUND = ["manifest unknown", "MANIFEST_UNKNOWN: manifest unknown", "name unknown: repository name not known",
                      "NAME_UNKNOWN", "unexpected status: 404 Not Found"]
    NOT_EVIDENCE = [
        'docker: unknown command: docker buildx\nexec: "docker-buildx": executable file not found in $PATH',
        "docker: 'manifest' is not a docker command",
        "/bin/bash: line 1: docker: command not found",
        "unauthorized: authentication required",
        "toomanyrequests: You have reached your pull rate limit",
        "Get https://ghcr.io/v2/: dial tcp: lookup ghcr.io: no such host",
        "error: file not found in cache",
    ]

    def test_the_registrys_own_answers_mean_free(self) -> None:
        for text in self.REAL_NOT_FOUND:
            docker = FakeDocker(lookup_failure=text)
            self.assertFalse(ri.tag_exists("x:1", "/cfg", docker), text)

    def test_a_broken_tool_or_transport_is_not_evidence_of_absence(self) -> None:
        for text in self.NOT_EVIDENCE:
            docker = FakeDocker(lookup_failure=text)
            with self.assertRaises(SystemExit, msg=text):
                ri.tag_exists("x:1", "/cfg", docker)

    def test_a_broken_buildx_aborts_the_push_instead_of_reading_the_tag_as_free(self) -> None:
        docker = self.built()
        self.write_version()
        ri.tag(self.version_file, docker, self.record)
        docker.lookup_failure = self.NOT_EVIDENCE[0]
        with self.assertRaises(SystemExit):
            ri.push(self.version_file, docker, self.record, ENV)
        self.assertEqual(docker.pushed_names, [])


class PushIsIdempotent(Base):
    """W2: an existing version tag serving THIS image is 'already published'; another image refuses."""

    def prepared(self, served_by_version: str | None) -> FakeDocker:
        docker = self.built()
        self.write_version("0.2.0\n")
        ri.tag(self.version_file, docker, self.record)
        if served_by_version is not None:
            docker.existing[f"{ri.REPOSITORY}:0.2.0"] = served_by_version
        return docker

    def test_an_existing_version_tag_serving_the_tested_image_is_already_published(self) -> None:
        docker = self.prepared(A)
        digest = ri.push(self.version_file, docker, self.record, ENV)
        self.assertEqual(digest, D1)
        self.assertEqual(docker.pushed_names, [LATEST], "the version tag is not pushed again")

    def test_an_existing_version_tag_serving_another_image_still_refuses(self) -> None:
        docker = self.prepared(B)
        with self.assertRaises(SystemExit) as ctx:
            ri.push(self.version_file, docker, self.record, ENV)
        self.assertIn("never overwrites a version tag", str(ctx.exception))
        self.assertEqual(docker.pushed_names, [], "nothing moves when the version tag serves another image")

    def test_the_comparison_is_by_image_content_through_an_index_too(self) -> None:
        index = json.dumps({"manifests": [
            {"digest": "sha256:" + "9" * 64, "platform": {"os": "unknown", "architecture": "unknown"}},
            {"digest": "sha256:" + "8" * 64, "platform": {"os": "linux", "architecture": "amd64"}}]})
        seen = []

        def runner(argv, **kwargs):
            seen.append(argv)
            if "@" in argv[argv.index("inspect") + 1]:
                return json.dumps({"config": {"digest": A}})
            return index

        self.assertEqual(ri.served_image_id("ghcr.io/x/y:1", "/cfg", runner), A)
        self.assertTrue(any("ghcr.io/x/y@sha256:" + "8" * 64 in " ".join(a) for a in seen), "the amd64 image, not the attestation entry")
        with self.assertRaises(SystemExit):
            ri.served_image_id("ghcr.io/x/y:1", "/cfg", lambda argv, **k: json.dumps({"manifests": []}))


class CmruSteps(unittest.TestCase):
    def test_the_step_order_versions_late_and_builds_once(self) -> None:
        import tomllib

        steps = tomllib.loads((ROOT / "cmru.toml").read_text(encoding="utf-8"))["steps"]
        self.assertEqual([c["argv"] for c in steps["build"]["commands"]], [["python3", "scripts/release_image.py", "tag"]])
        self.assertEqual([c["argv"] for c in steps["push"]["commands"]], [["python3", "scripts/release_image.py", "push"]])
        self.assertEqual(steps["push"]["required_env"], ["GITHUB_USERNAME", "GITHUB_PUSH_PAT"])
        bakes = [c["argv"] for s in steps.values() for c in s["commands"] if "bake" in c["argv"]]
        self.assertEqual(bakes, [], "the only build is inside release_image.py build (one bake call)")
        self.assertEqual((ROOT / "scripts" / "release_image.py").read_text(encoding="utf-8").count('"bake"'), 1)


if __name__ == "__main__":
    unittest.main()
