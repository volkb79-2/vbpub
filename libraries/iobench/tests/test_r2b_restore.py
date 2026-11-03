"""IOB-R2-01 (group B): boundaries and lifecycle oracles for ``iobench.restore``."""
import json
import math

import pytest

from iobench import restore
from iobench.errors import Aborted, Refusal, VerificationFailure
from iobench.host import HostError
from iobench.testing import GIB, standard_host

BYTES = "/proc/sys/vm/dirty_bytes"
RATIO = "/proc/sys/vm/dirty_ratio"
PSI = "/sys/fs/cgroup/game-server.slice/io.pressure"


def psi_text(avg10):
    return f"some avg10={avg10:.2f} avg60=0.00 avg300=0.00 total=0\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"


def locked_safety(host=None):
    host = host or standard_host()
    safety = restore.Safety(host, "run-test", "2026-10-21T03:00:00Z")
    safety.acquire()
    return host, safety


# -- io.max snapshot ----------------------------------------------------------------------------------------------------
def test_a_cgroup_directory_that_vanishes_while_walking_is_skipped_not_an_error():
    host = standard_host()
    host.put("/sys/fs/cgroup/a/io.max", "8:0 rbps=max\n")
    host.files["/sys/fs/cgroup/ghost/io.max"] = "x"  # listed in its parent, but its own stat is gone
    assert restore.snapshot_io_max(host) == {"/sys/fs/cgroup/a": "8:0 rbps=max\n"}


# -- the abort watch ----------------------------------------------------------------------------------------------------
def test_pressure_exactly_at_the_threshold_is_not_a_strike():
    host = standard_host()
    host.dynamic[PSI] = lambda: psi_text(40.0)
    watch = restore.AbortWatch(host, "game-server.slice", 40.0)
    for _ in range(restore.PSI_SAMPLES + 2):
        watch.check()
    assert watch.strikes == 0
    host.dynamic[PSI] = lambda: psi_text(40.01)
    watch.check()
    watch.check()
    with pytest.raises(Aborted):
        watch.check()


# -- bounded waits -----------------------------------------------------------------------------------------------------
def test_waiting_for_inflight_io_gives_up_after_exactly_the_timeout():
    host = standard_host()
    host.put("/sys/class/block/vda/inflight", "1 0\n")
    start = host.now()
    assert restore.wait_inflight_zero(host, "vda", timeout_s=3.0) is False
    assert host.now() - start == 3.0


def test_waiting_for_a_unit_to_stop_gives_up_after_exactly_the_timeout():
    host = standard_host()
    unit = "iobench-probe-x"
    host.active_units.add(unit)
    host.unit_stop_sticks = False
    start = host.now()
    assert restore.stop_unit(host, unit, timeout_s=3.0) is False
    assert host.now() - start == 3.0 and host.stopped_units == [unit]


# -- the guard thresholds -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value,ok", [(100.0, True), (0.01, True), (100.1, False), (0.0, False), (-1.0, False), (math.nan, False), (math.inf, False)])
def test_an_abort_threshold_must_be_a_finite_percentage_above_zero_up_to_one_hundred(value, ok):
    host = standard_host()
    host.dynamic[PSI] = lambda: psi_text(0.0)
    if ok:
        restore._validate_guards(host, "game-server.slice", value)
    else:
        with pytest.raises(Refusal) as err:
            restore._validate_guards(host, "game-server.slice", value)
        assert err.value.condition == "abort_psi_invalid"


# -- Safety lifecycle ----------------------------------------------------------------------------------------------------
def test_a_new_safety_has_no_snapshot_and_verifying_io_max_is_then_a_no_op():
    host = standard_host()
    host.put("/sys/fs/cgroup/a/io.max", "8:0 rbps=max\n")
    safety = restore.Safety(host, "run-test", "t")
    assert safety.begun is False and safety.locked is False and safety.journal_written is False
    safety.verify_io_max()
    assert safety.report.warnings == [] and safety.report.io_max_after == {}


def test_releasing_the_lock_ends_the_right_to_write_the_journal():
    host, safety = locked_safety()
    safety.release()
    assert safety.locked is False and not host.locks
    with pytest.raises(HostError, match="only by the lock holder"):
        safety.register_file("/run/iobench/run-x/iobench-run-x-w")


def test_the_journal_is_canonical_json_with_sorted_keys():
    host, safety = locked_safety()
    safety.set_knob(BYTES, "1000")
    text = host.read_text(restore.JOURNAL)
    assert text == json.dumps(json.loads(text), sort_keys=True)
    assert list(json.loads(text)) == ["coupled", "entries", "files", "run", "started"]


def test_a_session_owns_no_journal_after_a_failed_restore_and_none_after_a_successful_one():
    host, safety = locked_safety()
    safety.set_knob(BYTES, "1000")
    assert safety.journal_written is True
    safety.restore()
    assert safety.journal_written is False and not host.exists(restore.JOURNAL)
    host2, safety2 = locked_safety()
    safety2.set_knob(BYTES, "1000")
    host2.sysfs_swallow.add(BYTES)  # the restore write has no effect: read-back mismatch
    with pytest.raises(VerificationFailure):
        safety2.restore()
    assert safety2.journal_written is False and host2.exists(restore.JOURNAL)  # the journal stays for `iobench restore`


def test_a_recovered_journal_leaves_the_session_without_a_journal_of_its_own():
    host, safety = locked_safety()
    safety.set_knob(BYTES, "1000")
    safety.journal_written = False  # the run died: only the file on disk is left
    other = restore.Safety(host, "run-next", "t")
    other.locked = True
    report = other.recover_previous()
    assert report.verified is True and host.read_text(BYTES) == "0\n" and not host.exists(restore.JOURNAL)
    assert other.journal_written is False


# -- the coupled ratio ------------------------------------------------------------------------------------------------
def test_a_zero_ratio_is_never_written_back_when_the_bytes_knob_was_zero_too():
    host, safety = locked_safety()
    host.put(RATIO, "0\n")
    safety.set_knob(BYTES, "1000")
    host.write_log.clear()
    safety.restore()
    assert RATIO not in [path for path, _ in host.write_log]
    assert host.read_text(BYTES) == "0\n" and host.read_text(RATIO) == "0\n"


def test_a_ratio_is_written_back_only_when_the_bytes_knob_was_zero_and_the_ratio_was_not():
    host, safety = locked_safety()  # standard estate: bytes 0, ratio 20
    safety.set_knob(BYTES, "1000")
    assert host.read_text(RATIO) == "0\n"
    safety.restore()
    assert host.read_text(BYTES) == "0\n" and host.read_text(RATIO) == "20\n"
