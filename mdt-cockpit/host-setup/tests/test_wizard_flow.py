"""The wizard's dialogue: step order, numbering, grouping of related questions, review summary.

Runs the whole wizard once with every default accepted (blank answers) and a deterministic host
(no real benchmark file), records the prompts and the transcript, and checks the contract an
operator relies on.  Deliberately independent of test_buildkit_governance's smoke test, which reads
the host's real benchmark file.
"""
from __future__ import annotations

import importlib.util
import io
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_spec = importlib.util.spec_from_file_location("mdt_host_setup_wizard_flow", ROOT / "mdt-host-setup-wizard.py")
WIZARD = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = WIZARD  # @dataclass resolves its module through sys.modules
_spec.loader.exec_module(WIZARD)
EXAMPLE = ROOT / "host-setup.env.example"

# a prompt names its key either as "label (KEY)" or, for the scheduler/io.cost step, as "KEY [source: default]"
KEY_IN_LABEL = re.compile(r"\(([A-Z][A-Z0-9_]+)\)|^([A-Z][A-Z0-9_]{5,}) \[")


def run_wizard(stdin_lines: str = "") -> tuple[str, list[str], str]:
    """Return (transcript, prompt labels in order, written file) for an all-defaults run."""
    prompts: list[str] = []
    transcript = io.StringIO()

    def fake_prompt(text: str) -> str:
        prompts.append(text)
        return stdin_lines

    with tempfile.TemporaryDirectory() as directory:
        meminfo = Path(directory) / "meminfo"
        meminfo.write_text("MemTotal: 16777216 kB\nMemAvailable: 16777216 kB\nSwapTotal: 8388608 kB\n")
        output = Path(directory) / "host-setup.env"
        with mock.patch.object(WIZARD, "host_context_error", return_value=None), \
             mock.patch.object(WIZARD, "check_benchmark_results", return_value="current"), \
             mock.patch.object(WIZARD, "read_baseline_results", return_value={}), \
             mock.patch.object(WIZARD, "discover_io_dev_path_from_findmnt", return_value="/dev/vda"), \
             mock.patch.object(WIZARD, "discover_nproc", return_value=8), \
             mock.patch.object(WIZARD, "derive_disk_facts", return_value={"identity": {}}), \
             mock.patch.object(WIZARD, "read_scheduler_list", return_value=["none", "mq-deadline"]), \
             mock.patch.object(WIZARD, "_prompt", side_effect=fake_prompt), \
             mock.patch("sys.stdout", transcript):
            rc = WIZARD.main([
                "--example", str(EXAMPLE), "--output", str(output),
                "--meminfo-path", str(meminfo), "--skip-run-offer",
            ])
        written = output.read_text() if output.exists() else ""
    assert rc == 0, transcript.getvalue()[-2000:]
    return transcript.getvalue(), prompts, written


class WizardFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.transcript, cls.prompts, cls.written = run_wizard()
        cls.keys = [m.group(1) or m.group(2) for p in cls.prompts for m in [KEY_IN_LABEL.search(p.lstrip())] if m]
        cls.everything = cls.transcript + "\n" + "\n".join(cls.prompts)  # prompts are not part of stdout when _prompt is mocked

    def test_steps_are_listed_in_the_order_main_runs_them(self) -> None:
        main = (ROOT / "mdt-host-setup-wizard.py").read_text()
        main = main[main.index("def main("):]
        order = [
            "step_io_device(", "step_damon_stat_policy(", "step_io_baseline_start(",
            "step_slice_first_resources(", "step_build_policies(", "step_devcontainer_bootstrap(",
            "step_watcher(", "step_docker_daemon(", "finish_io_baseline(", "step_io_settings(",
            "step_io_writeback(", "step_io_scheduler_and_cost(",
        ]
        positions = [main.index(call) for call in order]
        self.assertEqual(positions, sorted(positions), "main() no longer calls the steps in the documented order")

    def test_every_heading_is_numbered_once_and_in_order(self) -> None:
        headings = re.findall(r"(?m)^-- \[(\d+)/(\d+)\] (.+) --$", self.transcript)
        total = len(WIZARD.STEP_TITLES)
        self.assertEqual([int(n) for n, _, _ in headings], list(range(1, total + 1)))
        self.assertEqual({int(t) for _, t, _ in headings}, {total})
        self.assertEqual([title for _, _, title in headings], list(WIZARD.STEP_TITLES))

    def test_no_stale_lettered_headings_remain(self) -> None:
        self.assertIsNone(re.search(r"(?m)^-- [a-j]\. ", self.transcript))
        self.assertNotIn("section b", self.transcript)

    def test_related_questions_are_asked_together_and_in_the_documented_order(self) -> None:
        keys = self.keys
        wanted = [
            "IO_DEV_PATH", "IO_BASELINE_ENV",
            "DEV_MEMORY_MIN_GUARANTEED_CEILING", "DEV_BUILDKITD_MAX_PARALLELISM",
            "BUILDX_ACCIDENTAL_CONTAINER_POLICY", "WATCHER_BUILDKIT_NAME_PATTERNS",
            "DEVCONTAINER_MISSING_BIND_SOURCE_POLICY",
            "WATCHER_INTERVAL", "CGROUP2_FLAGS", "WATCHER_MEMORY_POLICY",
            "DOCKER_DAEMON_CGROUP_PARENT",
            "DEV_STATIC_RIOPS", "DEV_IO_CAP_PCT", "DEV_SUBSLICE_IOPS_PCT",
            "IO_WRITEBACK_DIRTY_BYTES", "IO_ROOT_SCHEDULER",
        ]
        positions = []
        for key in wanted:
            self.assertIn(key, keys, f"{key} was never asked")
            positions.append(keys.index(key))
        self.assertEqual(positions, sorted(positions), dict(zip(wanted, positions)))

    def test_the_buildx_policy_is_not_asked_inside_the_buildkit_tier_any_more(self) -> None:
        keys = self.keys
        self.assertGreater(keys.index("BUILDX_ACCIDENTAL_CONTAINER_POLICY"), keys.index("DEV_BUILDKITD_MAX_PARALLELISM"))
        step3 = self.transcript[self.transcript.index("[3/9]"):self.transcript.index("[4/9]")]
        self.assertNotIn("Buildx policy", step3)
        self.assertNotIn("cgroup2 mount policy", step3)

    def test_every_key_is_asked_at_most_once_except_the_deliberate_confirmation(self) -> None:
        repeated = {k for k in self.keys if self.keys.count(k) > 1}
        self.assertEqual(repeated, set())

    def test_the_tier_rules_are_printed_once_not_once_per_tier(self) -> None:
        self.assertEqual(self.transcript.count("Rules for every slice below"), 1)
        self.assertNotIn("Child controls:", self.transcript)
        for slice_name in ("dev-interactive.slice", "dev-background.slice", "dev-gates.slice", "dev-buildkitd.slice"):
            self.assertRegex(self.transcript, rf"(?m)^{re.escape(slice_name)}$")  # out() strips leading blanks

    def test_duplicate_memory_warnings_are_gone(self) -> None:
        warnings = re.findall(r"(?m)^\s*review warning \(does not block\): .+$", self.transcript)
        self.assertEqual(len(warnings), len(set(warnings)), "the same memory warning is printed twice")

    def test_the_run_ends_with_a_summary_of_what_differs_from_the_starting_values(self) -> None:
        self.assertRegex(self.transcript, r"(?m)^Summary: \d+ value\(s\) differ from the starting values")
        self.assertLess(self.transcript.index("Summary:"), self.transcript.index("wrote `"))
        self.assertIn("`IO_DEV_PATH`: <empty> -> /dev/vda", self.transcript[self.transcript.index("Summary:"):])  # a derived value accepted with Enter

    def test_pinned_phrases_survive_the_rewrite(self) -> None:
        for phrase in (
            "mdt-container-io-events-watcher.service", "mdt-container-memory-inotify-watcher.service",
            "DEV_IO_CAP_PCT", "WATCHER_IO_CAP_PCT", "Starting proposals (review before accepting)",
            "+-- dev-gates.slice", "Static fallback values (used before a valid baseline)",
            "unavailable until current benchmark results exist", "no host mutation occurs",
            "Docker is not restarted automatically", "Sibling Min/Low/High controls are independent",
            "does not block", "Matching and cadence", "Cgroup mount safety",
            "The shipped default is '-' for both values", "install.sh leaves the memory watcher service",
            "Per-container memory watcher service (enabled or disabled)",
            "CIU may separately set mem_limit or MemoryMin", "it does not apply per-container memory limits",
        ):
            self.assertIn(phrase, self.everything)
        self.assertNotIn("aggregate violations", self.everything)

    def test_the_written_file_is_still_complete_and_comment_preserving(self) -> None:
        example = EXAMPLE.read_text()
        self.assertEqual(
            set(re.findall(r"(?m)^([A-Z][A-Z0-9_]*)=", example)),
            set(re.findall(r"(?m)^([A-Z][A-Z0-9_]*)=", self.written)),
        )
        strip = lambda text: [line for line in text.splitlines() if not re.match(r"[A-Z][A-Z0-9_]*=", line)]
        self.assertEqual(strip(example), strip(self.written), "a comment, blank line or heading was altered")


if __name__ == "__main__":
    unittest.main()
