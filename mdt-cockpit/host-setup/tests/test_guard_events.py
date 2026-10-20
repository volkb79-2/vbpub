"""MDT-HS-03: the guard must read Docker 29's event shape and survive one bad event.

On the host (Docker 29.9.1, API 1.56) the daemon no longer emits the deprecated top-level
`id`/`status`/`from` fields, so the old `event.get("id") or event.get("ID")` raised on EVERY
container create and the guard crash-looped (Restart=always, RestartSec=5) from 2026-10-02.
"""
from __future__ import annotations

import collections
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


GUARD = _load(ROOT / "scripts" / "mdt-buildkit-guard.py", "mdt_guard_events")

CID = "353b0541bc03105fb849f1ea4795c408b1a4dcf639c27389a931b86a2dc3698e"
OTHER = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
# a real event line from the host journal (Docker 29.9.1)
DOCKER_29 = (
    '{"Type":"container","Action":"create","Actor":{"ID":"' + CID + '","Attributes":{"image":"debian:bookworm-slim",'
    '"name":"fervent_lichterman"}},"scope":"local","time":1792469532,"timeNano":1792469532580725060}'
)
LEGACY_BOTH = json.dumps({"status": "create", "id": CID, "from": "debian:bookworm-slim", "Type": "container",
                          "Action": "create", "Actor": {"ID": CID, "Attributes": {}}})
LEGACY_ONLY = json.dumps({"status": "create", "id": CID, "from": "debian:bookworm-slim", "time": 1})
DISAGREE = json.dumps({"Type": "container", "Action": "create", "id": OTHER, "Actor": {"ID": CID}})
NOT_CONTAINER = json.dumps({"Type": "network", "Action": "create", "Actor": {"ID": CID, "Attributes": {}}})
NO_ID = json.dumps({"Type": "container", "Action": "create", "Actor": {"Attributes": {}}})
NOT_JSON = "Error response from daemon: boom"


def run_watch(lines: list[str], step: float = 0.0):
    """Feed `lines` to watch() through fakes; return (reconciled ids, reconcile_all calls, raised error)."""
    queue = collections.deque(lines)
    reconciled: list[str] = []
    full_scans = [0]

    class Process:
        returncode = None
        stderr = None

        def __init__(self) -> None:
            self.stdout = Stream(self)

        def poll(self):
            return self.returncode

        def wait(self):
            return self.returncode

        def terminate(self):
            self.returncode = 1

    class Stream:
        def __init__(self, process) -> None:
            self.process = process

        def readline(self):
            if queue:
                return queue.popleft() + "\n"
            self.process.returncode = 0
            return ""

    class Selector:
        def register(self, stream, event):
            self.stream = stream

        def select(self, timeout):
            return [(self.stream, 1)]

        def unregister(self, stream):
            pass

        def close(self):
            pass

    process = Process()
    now = [0.0]

    def clock():
        now[0] += step
        return now[0]

    config = GUARD.GuardConfig(policy="report-only", buildkit_image="moby/buildkit:x")
    error = None
    with mock.patch.object(GUARD, "reconcile_all", side_effect=lambda d, c: full_scans.__setitem__(0, full_scans[0] + 1)), \
         mock.patch.object(GUARD, "reconcile", side_effect=lambda d, cid, c: reconciled.append(cid)), \
         mock.patch("sys.stderr"):
        try:
            GUARD.watch("docker", config, rescan_interval=3600.0, popen=lambda *a, **k: process,
                        clock=clock, selector_factory=Selector)
        except GUARD.GuardError as exc:
            error = str(exc)
    return reconciled, full_scans[0], error


ENDED = "docker events ended unexpectedly"


class DockerEventShapeTests(unittest.TestCase):
    def test_the_real_docker_29_line_is_reconciled_by_its_actor_id(self) -> None:
        reconciled, _, error = run_watch([DOCKER_29])
        self.assertEqual(reconciled, [CID])
        self.assertTrue(error.startswith(ENDED), error)  # the stream ending is the only exit

    def test_a_legacy_line_with_both_fields_agreeing_is_accepted(self) -> None:
        reconciled, _, error = run_watch([LEGACY_BOTH])
        self.assertEqual(reconciled, [CID])
        self.assertTrue(error.startswith(ENDED), error)

    def test_a_legacy_line_without_actor_is_accepted_for_older_daemons(self) -> None:
        reconciled, _, error = run_watch([LEGACY_ONLY])
        self.assertEqual(reconciled, [CID])
        self.assertTrue(error.startswith(ENDED), error)

    def test_disagreeing_ids_are_refused_loudly_and_never_acted_on(self) -> None:
        reconciled, full_scans, error = run_watch([DISAGREE])
        self.assertEqual(reconciled, [], "neither id may be reconciled when they disagree")
        self.assertEqual(full_scans, 2, "one initial scan plus one immediate full reconcile")
        self.assertTrue(error.startswith(ENDED), error)

    def test_the_parser_names_the_reason(self) -> None:
        with self.assertRaisesRegex(GUARD.MalformedEvent, "disagree"):
            GUARD.container_id_from_event(json.loads(DISAGREE))
        with self.assertRaisesRegex(GUARD.MalformedEvent, "no container id"):
            GUARD.container_id_from_event(json.loads(NO_ID))
        with self.assertRaises(GUARD.MalformedEvent):
            GUARD.container_id_from_event({"Actor": {"ID": "--force"}})  # never an option for docker
        self.assertEqual(GUARD.container_id_from_event(json.loads(DOCKER_29)), CID)

    def test_a_non_container_event_is_ignored_not_a_crash(self) -> None:
        """The command filters on type=container, so this cannot happen on a healthy daemon; it carries
        no container to police, so it is logged and ignored (the periodic rescan still covers us)."""
        reconciled, full_scans, error = run_watch([NOT_CONTAINER, DOCKER_29])
        self.assertEqual(reconciled, [CID])
        self.assertEqual(full_scans, 1, "an ignored event does not trigger a full reconcile")
        self.assertTrue(error.startswith(ENDED), error)

    def test_the_events_command_still_filters_on_containers_and_the_parser_checks_the_type(self) -> None:
        source = (ROOT / "scripts" / "mdt-buildkit-guard.py").read_text()
        self.assertIn('"type=container"', source)
        self.assertIn('"container"', source[source.index("def container_id_from_event"):])


class ResilienceTests(unittest.TestCase):
    def test_one_malformed_event_does_not_kill_the_guard_and_triggers_a_full_reconcile(self) -> None:
        for bad in (NO_ID, DISAGREE, NOT_JSON):
            with self.subTest(bad=bad[:30]):
                reconciled, full_scans, error = run_watch([bad, DOCKER_29])
                self.assertEqual(reconciled, [CID], "the good event after the bad one is still handled")
                self.assertEqual(full_scans, 2)
                self.assertTrue(error.startswith(ENDED), error)

    def test_persistent_malformed_events_do_exit_so_systemd_shows_the_failure(self) -> None:
        reconciled, full_scans, error = run_watch([NO_ID] * 20)
        self.assertIn("malformed", error)
        self.assertIn(str(GUARD.MAX_CONSECUTIVE_MALFORMED_EVENTS), error)
        self.assertEqual(full_scans, 2, "sweeps are throttled to one per rescan interval (H-28)")

    def test_interleaved_malformed_events_trip_the_rolling_window(self) -> None:
        """H-26: bad,good,bad,good never reaches the consecutive limit but must not hide a broken parser."""
        reconciled, _, error = run_watch([NO_ID, DOCKER_29] * 50)
        self.assertIn("within", error)
        self.assertIn(str(GUARD.MAX_MALFORMED_IN_WINDOW), error)

    def test_a_slow_trickle_outside_the_window_does_not_exit(self) -> None:
        reconciled, _, error = run_watch([NO_ID, DOCKER_29] * 50, step=4.0)
        self.assertTrue(error.startswith(ENDED), error)
        self.assertEqual(len(reconciled), 50)

    def test_interleaved_flood_does_not_storm_reconcile_all(self) -> None:
        """H-28: 19 interleaved bad lines (under the window limit) cost one sweep, not nineteen."""
        lines = [NO_ID, DOCKER_29] * (GUARD.MAX_MALFORMED_IN_WINDOW - 1)
        _, full_scans, error = run_watch(lines)
        self.assertTrue(error.startswith(ENDED), error)
        self.assertEqual(full_scans, 2)

    def test_a_good_event_resets_the_malformed_streak(self) -> None:
        n = GUARD.MAX_CONSECUTIVE_MALFORMED_EVENTS
        lines = ([NO_ID] * (n - 1) + [DOCKER_29]) * 3
        reconciled, _, error = run_watch(lines)
        self.assertEqual(reconciled, [CID] * 3)
        self.assertTrue(error.startswith(ENDED), error)


class InspectRaceTests(unittest.TestCase):
    """H-25: an ephemeral container may vanish between listing/event and inspect."""

    @staticmethod
    def _docker(stderr: str, rc: int = 1):
        import subprocess
        return lambda docker, args: subprocess.CompletedProcess([docker, *args], rc, "", stderr)

    def test_vanished_container_is_skipped_not_fatal(self) -> None:
        config = GUARD.GuardConfig(policy="terminate", buildkit_image="moby/buildkit:x")
        for msg in ("Error response from daemon: No such container: " + CID,
                    "Error: No such object: " + CID):
            with mock.patch.object(GUARD, "_docker", self._docker(msg)):
                self.assertIsNone(GUARD.reconcile("docker", CID, config))

    def test_daemon_error_still_fails_loudly(self) -> None:
        config = GUARD.GuardConfig(policy="terminate", buildkit_image="moby/buildkit:x")
        for msg in ("Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?",
                    "permission denied while trying to connect to the Docker daemon socket", ""):
            with mock.patch.object(GUARD, "_docker", self._docker(msg)):
                with self.assertRaises(GUARD.GuardError) as ctx:
                    GUARD.reconcile("docker", CID, config)
                self.assertNotIsInstance(ctx.exception, GUARD.ContainerGone)

    def test_sweep_survives_a_container_vanishing_midway(self) -> None:
        import subprocess
        config = GUARD.GuardConfig(policy="report-only", buildkit_image="moby/buildkit:x")
        seen = []

        def fake(docker, args):
            if args[0] == "ps":
                return subprocess.CompletedProcess(args, 0, CID + "\n" + OTHER + "\n", "")
            seen.append(args[-1])
            if args[-1] == CID:
                return subprocess.CompletedProcess(args, 1, "", "Error: No such object: " + CID)
            return subprocess.CompletedProcess(args, 1, "", "daemon exploded")

        with mock.patch.object(GUARD, "_docker", fake):
            with self.assertRaises(GUARD.GuardError):
                GUARD.reconcile_all("docker", config)
        self.assertEqual(seen, [CID, OTHER], "the vanished one is skipped, the real failure then raises")

    def test_sweep_lists_full_ids(self) -> None:
        import subprocess
        calls = []

        def fake(docker, args):
            calls.append(list(args))
            return subprocess.CompletedProcess(args, 0, "", "")

        with mock.patch.object(GUARD, "_docker", fake):
            GUARD._container_ids("docker")
        self.assertIn("--no-trunc", calls[0])

    def test_short_event_ids_are_refused(self) -> None:
        with self.assertRaises(GUARD.MalformedEvent):
            GUARD.container_id_from_event({"Actor": {"ID": CID[:12]}})
        with self.assertRaises(GUARD.MalformedEvent):
            GUARD.container_id_from_event({"Actor": {"ID": 123456789012}})


class DocsTests(unittest.TestCase):
    def test_the_guard_docs_describe_the_event_contract(self) -> None:
        readme = " ".join((ROOT / "README.md").read_text().split())
        design = " ".join((ROOT.parent / "docs" / "DESIGN-GUIDE.md").read_text().split())
        for text in (readme, design):
            self.assertIn("Actor.ID", text)
        self.assertIn("MDT-HS-03", design)
        changes = (ROOT.parent / "CHANGES.md").read_text()
        self.assertIn("Actor.ID", changes)


if __name__ == "__main__":
    unittest.main()
