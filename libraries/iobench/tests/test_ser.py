"""IOB-SER: time-series reduction."""
import math
import pathlib

import pytest

from iobench import series
from iobench.series import SeriesError, reduce_series


def pts(values, dt=5.0, t0=0.0):
    return [(t0 + i * dt, v) for i, v in enumerate(values)]


def noisy_tail(lo, hi):
    return [100000.0] * 10 + [lo if i % 2 == 0 else hi for i in range(30)]


def test_ser_01_refusals():
    base = [20000.0] * 20
    with pytest.raises(SeriesError, match="non-finite"):
        reduce_series(pts([20000.0, 20000.0, 20000.0, math.nan] + base))
    with pytest.raises(SeriesError, match="non-finite"):
        reduce_series(pts([20000.0, math.inf] + base))
    with pytest.raises(SeriesError, match="strictly increasing"):
        reduce_series([(0.0, v) for v in base])
    dup = pts(base)
    dup[5] = dup[4]
    with pytest.raises(SeriesError, match="strictly increasing"):
        reduce_series(dup)
    with pytest.raises(SeriesError, match="negative"):
        reduce_series(pts([20000.0, -1.0] + base))
    with pytest.raises(SeriesError, match="at least 8"):
        reduce_series(pts([1.0] * 7))
    with pytest.raises(SeriesError, match="zero steady"):
        reduce_series(pts([0.0] * 20))


def test_ser_02_clean_step():
    r = reduce_series(pts([100000.0] * 10 + [20000.0] * 30))
    assert r.state == "steady" and r.steady == 20000 and r.knee_s == 50
    assert r.steady_certified
    assert r.burst_units == pytest.approx(4.0e6)


def test_ser_02_noisy_tail_knee():
    r = reduce_series(pts(noisy_tail(18000.0, 22000.0)))
    assert r.state == "steady"
    assert r.steady == 20000
    assert r.knee_s == 50
    assert 3.6e6 <= r.burst_units <= 4.4e6
    assert r.burst_units <= 1.1 * 4.0e6
    assert r.tail_cv == pytest.approx(0.10)


def test_ser_02_noise_at_threshold():
    r = reduce_series(pts(noisy_tail(15000.0, 25000.0)))
    assert r.tail_cv == pytest.approx(0.25)
    assert r.state == "unsteady"
    assert not r.steady_certified


def test_ser_02_knee_none_when_settling_late():
    # settles only in the last 10 %: no knee in the first 90 %, so unsteady
    values = [100000.0] * 37 + [20000.0] * 3
    r = reduce_series(pts(values))
    assert r.state == "unsteady" and r.knee_s is None and r.burst_units == 0


def test_ser_03_states():
    flat = reduce_series(pts([5000.0] * 20))
    assert flat.state == "flat" and flat.knee_s == 0 and flat.burst_units == 0
    assert flat.steady_certified
    ramp = [10000.0] * 15 + [8000.0, 9000.0, 10000.0, 11000.0, 12000.0]
    trend = reduce_series(pts(ramp))
    assert trend.tail_cv < 0.2 and trend.state == "unsteady"
    assert reduce_series(pts([100.0] * 10 + [50.0] * 30)).state == "steady"
    d = flat.as_dict()
    assert d["state"] == "flat" and d["steady_certified"] is True and d["observation_s"] == 95.0


def test_ser_03_no_passed_knee_name():
    root = pathlib.Path(series.__file__).parent
    docs = pathlib.Path(__file__).parent.parent
    needle = "passed" + "_knee"
    for path in list(root.glob("*.py")) + list(docs.glob("**/*.md")):
        if path.name == "SPEC.md":
            continue
        assert needle not in path.read_text(), path
    assert not hasattr(series.Reading, needle)


def test_ser_03_flat_reason_text():
    # a flat reading says only "no burst observed in observation_s"
    r = reduce_series(pts([5000.0] * 20, dt=2.0))
    assert r.state == "flat" and r.observation_s == 38.0 and r.burst_units == 0


def test_ser_04_flat_line():
    mib = 4096 * 1024 * 1024.0
    flat = [(float(i), mib + (1 if i % 2 else -1)) for i in range(1, 601)]
    assert reduce_series(flat).rate_limited is True
    noisy = [(float(i), 21600.0 + (3000 if i % 3 else -3000) * ((i * 7) % 5) / 5) for i in range(1, 601)]
    assert reduce_series(noisy).rate_limited is False
    short = [(float(i), mib) for i in range(1, 41)]
    assert reduce_series(short).rate_limited == "not_assessed"
    # a leading run of zeros makes early windows zero-mean (never flat)
    zeros = [(float(i), 0.0 if i < 70 else mib) for i in range(1, 200)]
    assert reduce_series(zeros).rate_limited is True


def test_ser_05_burst_units():
    r = reduce_series(pts([100000.0] * 10 + [20000.0] * 30))
    assert r.burst_units == pytest.approx(4.0e6)


def test_ser_05_ratio_required():
    r = reduce_series(pts([100000.0] * 10 + [20000.0] * 30))
    with pytest.raises(TypeError):
        series.mean_overstates(r)  # type: ignore[call-arg]
    assert series.mean_overstates(r, 1.1)
    assert not series.mean_overstates(r, 10)


def test_ser_discard_warmup_and_downsample():
    raw = [(float(t), 1.0) for t in range(2, 32, 2)]
    assert [p[0] for p in series.discard_warmup(raw, 5.0)] == [6.0 + 2 * i for i in range(13)]
    small = series.downsample(raw)
    assert len(small) == len(raw)
    big = series.downsample([(float(i), 1.0) for i in range(1, 601)])
    assert len(big) == 120 and big[0] == [1.0, 1.0]
