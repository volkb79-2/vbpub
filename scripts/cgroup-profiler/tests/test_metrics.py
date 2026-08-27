"""Tests for lib.metrics: per-tick readers and the *_rates helpers.

Every test here works against the synthetic trees in conftest.py or against
tmp_path-built fakes — never the real /sys/fs/cgroup or /proc, per DESIGN.md
§7. sample_proc is the one function with no root parameter (a pid is process-
namespace bound, not something a bind mount can redirect); it grew a test-only
proc_root keyword for exactly this reason, so it can be pointed at the fake
proc_root fixture like everything else.
"""

from __future__ import annotations

import os

from lib import metrics


# ── sample_cgroup ────────────────────────────────────────────────────────────


def test_groups_constant_matches_contract():
    assert metrics.GROUPS == (
        "mem", "memstat", "memev", "psi", "cpu", "io", "pids", "cgstat",
    )


def test_sample_cgroup_default_groups_produce_every_key(cgroup_root):
    sample = metrics.sample_cgroup(str(cgroup_root / "dev.slice" / "dev-background.slice"))
    # "psi" is one requested group but three output keys.
    assert set(sample) == {
        "mem", "memstat", "memev", "psi_mem", "psi_cpu", "psi_io",
        "cpu", "io", "pids", "cgstat",
    }


def test_sample_cgroup_honours_requested_groups(cgroup_root):
    sample = metrics.sample_cgroup(
        str(cgroup_root / "dev.slice" / "dev-background.slice"), groups={"mem", "io"}
    )
    assert set(sample) == {"mem", "io"}


def test_sample_cgroup_mem_values(cgroup_root):
    sample = metrics.sample_cgroup(
        str(cgroup_root / "dev.slice" / "dev-background.slice"), groups={"mem"}
    )
    assert sample["mem"]["current"] == 4 * 1024**3
    assert sample["mem"]["swap_current"] == 0
    assert sample["mem"]["zswap_current"] == 0


def test_sample_cgroup_io_is_per_device(cgroup_root):
    sample = metrics.sample_cgroup(
        str(cgroup_root / "dev.slice" / "dev-background.slice"), groups={"io"}
    )
    assert sample["io"]["254:0"]["rbytes"] == 1048576
    assert sample["io"]["254:0"]["wbytes"] == 2097152


def test_sample_cgroup_io_reports_device_with_no_io_max_cap(tmp_path):
    # io.stat (activity) and io.max (a cap) are independent files; sampling
    # the "io" group must report every device io.stat names, whether or not
    # that device has any cap declared — this reader has no business
    # cross-referencing io.max at all.
    leaf = tmp_path / "leaf"
    leaf.mkdir()
    (leaf / "io.stat").write_text("254:0 rbytes=1024 wbytes=0 rios=1 wios=0\n")
    (leaf / "io.max").write_text("9:0 rbps=5000000\n")  # a different, unrelated device
    sample = metrics.sample_cgroup(str(leaf), groups={"io"})
    assert sample["io"] == {"254:0": {"rbytes": 1024, "wbytes": 0, "rios": 1, "wios": 0}}


def test_sample_cgroup_psi_produces_three_named_panels(cgroup_root):
    sample = metrics.sample_cgroup(
        str(cgroup_root / "wings.slice"), groups={"psi"}
    )
    for key in ("psi_mem", "psi_cpu", "psi_io"):
        assert "some_avg10" in sample[key]
        assert "full_total" in sample[key]


def test_sample_cgroup_vanished_directory_is_fully_absent(cgroup_root):
    missing = str(cgroup_root / "no" / "such" / "cgroup")
    assert metrics.sample_cgroup(missing) == {}
    # Even a narrow request comes back empty — there is nothing to report,
    # not a dict of groups full of None.
    assert metrics.sample_cgroup(missing, groups={"mem"}) == {}


def test_sample_cgroup_memev_uses_local_not_hierarchical(cgroup_root):
    # cgroup_files() writes identical content into both memory.events and
    # memory.events.local by default, so a version of this test using the
    # fixture as-is could not actually tell which file sample_cgroup reads —
    # only that the expected key exists. Give the two files genuinely
    # different values here so the assertion actually distinguishes them: if
    # sample_cgroup ever regressed to reading the hierarchical
    # memory.events (which double-counts at every ancestor level) instead of
    # the per-cgroup memory.events.local, this would read 999, not 5.
    target = cgroup_root / "wings.slice"
    (target / "memory.events").write_text(
        "low 0\nhigh 999\nmax 0\noom 0\noom_kill 0\noom_group_kill 0\n"
    )
    (target / "memory.events.local").write_text(
        "low 0\nhigh 5\nmax 0\noom 0\noom_kill 0\noom_group_kill 0\n"
    )

    sample = metrics.sample_cgroup(str(target), groups={"memev"})
    assert sample["memev"]["high"] == 5
    assert "oom_kill" in sample["memev"]


# ── sample_proc ──────────────────────────────────────────────────────────────


def test_sample_proc_reads_expected_fields(proc_root):
    sample = metrics.sample_proc(4242, proc_root=str(proc_root))
    assert sample["rss_anon"] == 900000 * 1024
    assert sample["rss_file"] == 148576 * 1024
    assert sample["swap"] == 200000 * 1024
    assert sample["read_bytes"] == 1048576
    assert sample["write_bytes"] == 2097152

    hz = os.sysconf("SC_CLK_TCK")
    assert sample["utime"] == int(350 * 1_000_000 / hz)
    assert sample["stime"] == int(120 * 1_000_000 / hz)


def test_sample_proc_gone_pid_is_empty_dict(proc_root):
    assert metrics.sample_proc(999999999, proc_root=str(proc_root)) == {}


# ── sample_host ──────────────────────────────────────────────────────────────


def _build_sys_root(tmp_path, *, with_ksm=True, with_zswap=True):
    sys_root = tmp_path / "sys"
    if with_ksm:
        ksm = sys_root / "kernel" / "mm" / "ksm"
        ksm.mkdir(parents=True)
        (ksm / "pages_sharing").write_text("12345\n")
        (ksm / "general_profit").write_text("998877\n")
    if with_zswap:
        zswap = sys_root / "module" / "zswap" / "parameters"
        zswap.mkdir(parents=True)
        (zswap / "max_pool_percent").write_text("25\n")
        (zswap / "compressor").write_text("zstd\n")
    return sys_root


def test_sample_host_full_tree(proc_root, tmp_path):
    sys_root = _build_sys_root(tmp_path)
    host = metrics.sample_host(proc_root=str(proc_root), sys_root=str(sys_root))

    assert host["meminfo"]["MemTotal"] == 16374620 * 1024
    assert host["meminfo"]["Zswap"] == 447880 * 1024

    assert host["vmstat"]["zswpin"] == 400000
    assert host["vmstat"]["workingset_refault_anon"] == 1000

    assert host["psi"]["memory"]["some_avg10"] == 1.5
    assert host["psi"]["memory"]["full_avg10"] == 0.5
    assert host["psi"]["memory"]["some_total"] == 12345
    assert host["psi"]["memory"]["full_total"] == 6172  # total // 2, from the psi() helper

    assert host["ksm"]["pages_sharing"] == 12345
    assert host["ksm"]["general_profit"] == 998877

    assert host["zswap"]["max_pool_percent"] == 25
    assert host["zswap"]["compressor"] == "zstd"

    assert host["loadavg"] == [1.50, 1.20, 0.90]

    # "cpu  1000 20 3000 90000 500 0 100 0 0 0" -> idle = idle(90000) + iowait(500)
    assert host["cpu"]["total_jiffies"] == 1000 + 20 + 3000 + 90000 + 500 + 0 + 100 + 0 + 0 + 0
    assert host["cpu"]["idle_jiffies"] == 90000 + 500


def test_sample_host_missing_sys_facts_are_none_not_zero(proc_root, tmp_path):
    empty_sys = tmp_path / "empty-sys"
    empty_sys.mkdir()
    host = metrics.sample_host(proc_root=str(proc_root), sys_root=str(empty_sys))
    assert host["ksm"]["pages_sharing"] is None
    assert host["ksm"]["general_profit"] is None
    assert host["zswap"]["max_pool_percent"] is None
    assert host["zswap"]["compressor"] is None


# ── cpu_cores_used ───────────────────────────────────────────────────────────


def test_cpu_cores_used_one_core_for_two_seconds():
    prev = {"usage_usec": 1_000_000}
    cur = {"usage_usec": 3_000_000}
    assert metrics.cpu_cores_used(prev, cur, 2.0) == 1.0


def test_cpu_cores_used_none_on_counter_reset():
    prev = {"usage_usec": 5_000_000}
    cur = {"usage_usec": 1_000_000}
    assert metrics.cpu_cores_used(prev, cur, 2.0) is None


def test_cpu_cores_used_none_on_missing_field():
    assert metrics.cpu_cores_used({"usage_usec": 1000}, {}, 1.0) is None


# ── io_rates ─────────────────────────────────────────────────────────────────


def test_io_rates_per_device_per_field():
    prev = {"254:0": {"rbytes": 1000, "wbytes": 500, "rios": 10, "wios": 5}}
    cur = {"254:0": {"rbytes": 3000, "wbytes": 1500, "rios": 30, "wios": 15}}
    rates = metrics.io_rates(prev, cur, 2.0)
    assert rates["254:0"]["rbytes_per_s"] == 1000.0
    assert rates["254:0"]["wbytes_per_s"] == 500.0
    assert rates["254:0"]["rios_per_s"] == 10.0
    assert rates["254:0"]["wios_per_s"] == 5.0


def test_io_rates_new_device_has_no_prior_baseline():
    prev = {}
    cur = {"8:16": {"rbytes": 4096}}
    rates = metrics.io_rates(prev, cur, 1.0)
    assert rates["8:16"]["rbytes_per_s"] is None


def test_io_rates_vanished_device_is_dropped_not_zeroed():
    prev = {"254:0": {"rbytes": 1000}, "8:16": {"rbytes": 500}}
    cur = {"254:0": {"rbytes": 2000}}
    rates = metrics.io_rates(prev, cur, 1.0)
    assert "8:16" not in rates


# ── memstat_rates ────────────────────────────────────────────────────────────


def test_memstat_rates_basic_counters():
    prev = {"pgfault": 100, "pgmajfault": 10}
    cur = {"pgfault": 300, "pgmajfault": 30}
    rates = metrics.memstat_rates(prev, cur, 2.0)
    assert rates["pgfault_per_s"] == 100.0
    assert rates["pgmajfault_per_s"] == 10.0


def test_memstat_rates_refault_split_matches_zswap_monitor_formula():
    prev = {"zswpin": 1000, "workingset_refault_anon": 5000, "workingset_refault_file": 2000}
    cur = {"zswpin": 1400, "workingset_refault_anon": 6000, "workingset_refault_file": 2500}
    rates = metrics.memstat_rates(prev, cur, 2.0)
    # rfz/s = Δzswpin/Δt
    assert rates["rfz_per_s"] == 200.0
    # rfd/s = max(0, Δwra - Δzswpin)/Δt = max(0, 1000-400)/2
    assert rates["rfd_per_s"] == 300.0
    # rff/s = Δworkingset_refault_file/Δt
    assert rates["rff_per_s"] == 250.0


def test_memstat_rates_rfd_clamped_to_zero_when_zswap_outpaces_anon_refault():
    # Δzswpin (400) exceeds Δworkingset_refault_anon (100): every anon
    # refault this tick could plausibly have come from zswap, so disk
    # refaults must read as 0, never negative.
    prev = {"zswpin": 1000, "workingset_refault_anon": 5000}
    cur = {"zswpin": 1400, "workingset_refault_anon": 5100}
    rates = metrics.memstat_rates(prev, cur, 2.0)
    assert rates["rfd_per_s"] == 0.0


def test_memstat_rates_rfd_none_when_either_component_unmeasurable():
    # zswpin is measurable but workingset_refault_anon is absent this tick.
    prev = {"zswpin": 1000, "workingset_refault_anon": 5000}
    cur = {"zswpin": 1400}
    rates = metrics.memstat_rates(prev, cur, 2.0)
    assert rates["rfz_per_s"] == 200.0
    assert rates["rfd_per_s"] is None


def test_memstat_rates_rfd_none_on_zswpin_counter_reset():
    # workingset_refault_anon is fine, but zswpin went backwards (cgroup
    # recreated) — rfd must not silently compute off a stale zswpin.
    prev = {"zswpin": 5000, "workingset_refault_anon": 1000}
    cur = {"zswpin": 100, "workingset_refault_anon": 1500}
    rates = metrics.memstat_rates(prev, cur, 2.0)
    assert rates["rfz_per_s"] is None
    assert rates["rfd_per_s"] is None


def test_memstat_rates_all_none_safe_with_empty_inputs():
    rates = metrics.memstat_rates({}, {}, 1.0)
    for name in (
        "pgfault_per_s", "pgmajfault_per_s", "pgscan_per_s", "pgsteal_per_s",
        "pswpin_per_s", "pswpout_per_s", "zswpin_per_s", "zswpout_per_s",
        "zswpwb_per_s", "workingset_refault_anon_per_s",
        "workingset_refault_file_per_s", "rfz_per_s", "rfd_per_s", "rff_per_s",
    ):
        assert rates[name] is None


# ── _proc_cpu_usec: /proc/<pid>/stat is a kernel-owned, mid-write-race file ──
#
# sample_proc's own "pid gone" test never reaches _proc_cpu_usec at all (the
# isdir() guard fires first); these target the parser's handling of a pid
# directory that exists but whose stat file is truncated, corrupt, or from a
# kernel too old/new to have the fields this code assumes.


def _proc_dir(tmp_path, pid=1234, *, stat=None, with_status=True, with_io=True):
    base = tmp_path / "proc" / str(pid)
    base.mkdir(parents=True)
    if with_status:
        (base / "status").write_text("Name:\tx\nRssAnon:\t 1000 kB\n")
    if with_io:
        (base / "io").write_text("read_bytes: 10\nwrite_bytes: 20\n")
    if stat is not None:
        (base / "stat").write_text(stat)
    return str(tmp_path / "proc"), pid


def test_sample_proc_missing_stat_file_gives_none_cpu_times(tmp_path):
    proc_root, pid = _proc_dir(tmp_path, stat=None)
    sample = metrics.sample_proc(pid, proc_root=proc_root)
    assert sample["utime"] is None
    assert sample["stime"] is None
    # the rest of the sample is unaffected by the missing stat file
    assert sample["rss_anon"] == 1000 * 1024


def test_sample_proc_empty_stat_file_gives_none_cpu_times(tmp_path):
    proc_root, pid = _proc_dir(tmp_path, stat="")
    sample = metrics.sample_proc(pid, proc_root=proc_root)
    assert sample["utime"] is None
    assert sample["stime"] is None


def test_proc_cpu_usec_no_closing_paren_is_unparseable(tmp_path):
    # A stat line missing its comm-field parens entirely (impossible on a
    # healthy kernel, but exactly what a corrupt/truncated read looks like).
    proc_root, pid = _proc_dir(tmp_path, stat="1234 nocomm S 1 1 1\n")
    utime, stime = metrics._proc_cpu_usec(f"{proc_root}/{pid}/stat")
    assert (utime, stime) == (None, None)


def test_proc_cpu_usec_truncated_after_comm_is_unparseable(tmp_path):
    # Fewer than 13 fields after ')' — a read that landed mid-write.
    proc_root, pid = _proc_dir(tmp_path, stat="1234 (x) S 1 1 1 1 1\n")
    utime, stime = metrics._proc_cpu_usec(f"{proc_root}/{pid}/stat")
    assert (utime, stime) == (None, None)


def _stat_fields(utime="1", stime="1"):
    """The fields substring after the last ')' with utime/stime placed at
    the exact indices _proc_cpu_usec reads (11 and 12) — verified against
    conftest's real pid-4242 fixture: index 0 is state, 1-10 are ten filler
    fields, 11 is utime, 12 is stime, per proc(5)'s field ordering."""
    return " ".join(["S"] + ["1"] * 10 + [utime, stime] + ["1"] * 6)


def test_proc_cpu_usec_non_numeric_utime_stime_is_unparseable(tmp_path):
    # 13+ fields present (so the length check passes) but the utime/stime
    # slots themselves are garbage — a corrupt read, not a short one.
    stat = f"1234 (x) {_stat_fields(utime='?', stime='?')}\n"
    proc_root, pid = _proc_dir(tmp_path, stat=stat)
    utime, stime = metrics._proc_cpu_usec(f"{proc_root}/{pid}/stat")
    assert (utime, stime) == (None, None)


def test_proc_cpu_usec_comm_containing_parens_and_spaces(tmp_path):
    # comm is process-controlled and can contain ')' and spaces (e.g. a
    # process renamed itself to "weird (name) here"); parsing must resume
    # after the *last* ')', not the first.
    stat = f"1234 (weird (name) here) {_stat_fields(utime='350', stime='120')}\n"
    proc_root, pid = _proc_dir(tmp_path, stat=stat)
    utime, stime = metrics._proc_cpu_usec(f"{proc_root}/{pid}/stat")
    hz = os.sysconf("SC_CLK_TCK")
    assert utime == int(350 * 1_000_000 / hz)
    assert stime == int(120 * 1_000_000 / hz)


def test_proc_cpu_usec_falls_back_when_sysconf_raises(tmp_path, monkeypatch):
    # A platform where SC_CLK_TCK genuinely can't be queried must not crash
    # the sampler — it degrades to the near-universal 100 Hz assumption
    # instead of losing the whole sample.
    stat = f"1234 (x) {_stat_fields(utime='350', stime='120')}\n"
    proc_root, pid = _proc_dir(tmp_path, stat=stat)

    def _raise(_name):
        raise OSError("no such configuration parameter")

    monkeypatch.setattr(metrics.os, "sysconf", _raise)
    utime, stime = metrics._proc_cpu_usec(f"{proc_root}/{pid}/stat")
    assert utime == 350 * 10_000  # 1_000_000 / 100
    assert stime == 120 * 10_000


# ── _loadavg: /proc/loadavg is small but not immune to a bad read ───────────


def test_loadavg_missing_file(tmp_path):
    assert metrics._loadavg(str(tmp_path)) is None


def test_loadavg_too_few_fields(tmp_path):
    (tmp_path / "loadavg").write_text("1.50 1.20\n")
    assert metrics._loadavg(str(tmp_path)) is None


def test_loadavg_non_numeric_fields(tmp_path):
    (tmp_path / "loadavg").write_text("abc def ghi 3/1200 999\n")
    assert metrics._loadavg(str(tmp_path)) is None


# ── _cpu_jiffies: /proc/stat parsing under a missing or reordered "cpu " line ─


def test_cpu_jiffies_missing_stat_file(tmp_path):
    jiffies = metrics._cpu_jiffies(str(tmp_path))
    assert jiffies == {"total_jiffies": None, "idle_jiffies": None}


def test_cpu_jiffies_skips_non_aggregate_lines_before_finding_cpu(tmp_path):
    # A line that does not start with "cpu " (here, a made-up leading line)
    # must be skipped rather than mistaken for the aggregate line.
    (tmp_path / "stat").write_text("intr 12345 0 0\ncpu  1000 20 3000 90000 500 0 100 0 0 0\n")
    jiffies = metrics._cpu_jiffies(str(tmp_path))
    assert jiffies["total_jiffies"] == 1000 + 20 + 3000 + 90000 + 500 + 100
    assert jiffies["idle_jiffies"] == 90000 + 500


def test_cpu_jiffies_non_numeric_fields(tmp_path):
    (tmp_path / "stat").write_text("cpu  1000 abc 3000 90000 500 0 100 0 0 0\n")
    jiffies = metrics._cpu_jiffies(str(tmp_path))
    assert jiffies == {"total_jiffies": None, "idle_jiffies": None}


def test_cpu_jiffies_aggregate_line_with_no_fields(tmp_path):
    # "cpu " with nothing after it — degenerate but must not IndexError.
    (tmp_path / "stat").write_text("cpu \n")
    jiffies = metrics._cpu_jiffies(str(tmp_path))
    assert jiffies == {"total_jiffies": None, "idle_jiffies": None}


def test_cpu_jiffies_no_aggregate_line_at_all(tmp_path):
    # Only per-core lines present ("cpu0", not "cpu ") — the aggregate line
    # this function needs was never found.
    (tmp_path / "stat").write_text("cpu0 500 10 1500 45000 250 0 50 0 0 0\n")
    jiffies = metrics._cpu_jiffies(str(tmp_path))
    assert jiffies == {"total_jiffies": None, "idle_jiffies": None}
