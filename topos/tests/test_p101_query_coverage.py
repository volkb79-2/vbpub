"""P101 — Close query/semantics.py coverage gaps (12 lines, 12 arcs).

Each test targets one specific nl -ba line and branch via a real call to
the semantic helper with a concrete _Point/point setup. Exact values asserted.
Uses fixture patterns from test_query.py (_g, _rr, _frame, collect_points).
"""

from __future__ import annotations

import pytest

import topos.query.semantics as sem


# ---------------------------------------------------------------------------
# nl -ba 112 — _round returns None when v is None         arc [111,112]
# ---------------------------------------------------------------------------
def test_round_none():
    """_round(None) returns None (line 112)."""
    assert sem._round(None) is None


# ---------------------------------------------------------------------------
# nl -ba 121 — _finite_number returns None for non-numeric  arc [120,121]
# ---------------------------------------------------------------------------
def test_finite_number_rejects_non_numeric():
    """_finite_number returns None for a string (line 121)."""
    assert sem._finite_number("not_a_number") is None


# ---------------------------------------------------------------------------
# nl -ba 223 — _rate_pairs continue when prior.raw is None   arc [222,223]
# ---------------------------------------------------------------------------
def test_rate_pairs_skip_prior_without_raw():
    """A prior point without raw is skipped (continue), search continues (line 223)."""
    pts = [
        sem._Point(ts=0, v=None, raw=None, src="derived", state=None),       # idx 0: v=None, src=derived but raw=None → inner continue
        sem._Point(ts=1, v=None, raw=None, src="derived", state=None),       # idx 1: v=None, src=derived but raw=None → inner continue
        sem._Point(ts=2, v=None, raw=200, src="derived", state=None),        # idx 2: v=None, src=derived, raw=200 → needs prior, searches back
        sem._Point(ts=3, v=None, raw=400, src="derived", state=None),        # idx 3: v=None, src=derived, raw=400 → needs prior
    ]
    pairs, resets = sem._rate_pairs(pts)
    # idx 2: prior search finds nothing → no pair added
    # idx 3: prior = idx 2 (has raw=200) → raw_delta=200, ts_delta=1 → pair (3, 200)
    assert pairs == [(3, 200.0)]
    assert resets == 0


# ---------------------------------------------------------------------------
# nl -ba 230 — _rate_pairs break when ts_delta <= 0        arc [229,230]
# ---------------------------------------------------------------------------
def test_rate_pairs_break_on_non_positive_ts_delta():
    """_rate_pairs: ts_delta <= 0 breaks the prior search (line 230)."""
    pts = [
        sem._Point(ts=5, v=None, raw=100, src="derived", state=None),  # idx 0: prior candidate
        sem._Point(ts=5, v=None, raw=200, src="derived", state=None),  # idx 1: same ts → ts_delta=0
    ]
    pairs, resets = sem._rate_pairs(pts)
    # idx 1: prior = idx 0, raw_delta=100, ts_delta=0 → break, no pair
    assert pairs == []
    assert resets == 0


# ---------------------------------------------------------------------------
# nl -ba 254 — _counter_total continue when point.raw is None  arc [253,254]
# ---------------------------------------------------------------------------
def test_counter_total_skip_no_raw():
    """_counter_total skips points without raw counters (line 254)."""
    pts = [
        sem._Point(ts=0, v=1.0, raw=None, src="exact", state=None),  # raw is None → continue
        sem._Point(ts=1, v=None, raw=100, src="derived", state=None),  # raw=100
    ]
    total, intervals, resets = sem._counter_total(pts)
    assert total == 0  # only one raw point, no interval
    assert intervals == 0
    assert resets == 0


# ---------------------------------------------------------------------------
# nl -ba 269 — _integral_of_series returns (0,0) when len<2   arc [268,269]
# ---------------------------------------------------------------------------
def test_integral_empty_series():
    """_integral_of_series returns (0,0) for fewer than 2 pairs (line 269)."""
    i, s = sem._integral_of_series([])
    assert (i, s) == (0.0, 0.0)
    i2, s2 = sem._integral_of_series([(0, 1.0)])
    assert (i2, s2) == (0.0, 0.0)


# ---------------------------------------------------------------------------
# nl -ba 274 — _integral_of_series continue when dt <= 0    arc [273,274]
# ---------------------------------------------------------------------------
def test_integral_skip_non_positive_dt():
    """_integral_of_series skips intervals with dt <= 0 (line 274)."""
    pairs = [(0, 1.0), (0, 2.0), (2, 3.0)]
    i, s = sem._integral_of_series(pairs)
    # (0,1)->(0,2): dt=0 → skip
    # (0,2)->(2,3): dt=2 → (2+3)/2*2 = 5.0
    assert i == 5.0
    assert s == 2.0


# ---------------------------------------------------------------------------
# nl -ba 329 — summarize state_duration continue when state is None  arc [328,329]
# ---------------------------------------------------------------------------
def test_state_duration_skip_none_state():
    """state_duration skips points with state=None (line 329)."""
    pts = [
        sem._Point(ts=0, v="idle", raw=None, src="exact", state="idle"),
        sem._Point(ts=1, v=None, raw=None, src="exact", state=None),  # state=None → continue
        sem._Point(ts=3, v="busy", raw=None, src="exact", state="busy"),
    ]
    s = sem.summarize(pts, sem.ValueSemantic.STATE_DURATION)
    # (idle, None): dt=1, idle counted → durations={"idle":1}
    # (None, busy): cur.state=None → continue
    assert s.stats["states"] == {"idle": 1.0}
    assert s.sample_count == 1


# ---------------------------------------------------------------------------
# nl -ba 333 — summarize state_duration continue when dt <= 0  arc [332,333]
# ---------------------------------------------------------------------------
def test_state_duration_skip_non_positive_dt():
    """state_duration skips interval with dt <= 0 (line 333)."""
    pts = [
        sem._Point(ts=0, v="a", raw=None, src="exact", state="a"),
        sem._Point(ts=0, v="b", raw=None, src="exact", state="b"),  # dt=0 → continue
        sem._Point(ts=4, v="c", raw=None, src="exact", state="c"),
    ]
    s = sem.summarize(pts, sem.ValueSemantic.STATE_DURATION)
    # (a,b): dt=0 → continue → a not counted
    # (b,c): dt=4 → b counted
    assert s.stats["states"] == {"b": 4.0}
    assert s.sample_count == 1


# ---------------------------------------------------------------------------
# nl -ba 337 — state_duration raises on > MAX_STATES states  arc [336,337]
# ---------------------------------------------------------------------------
def test_state_duration_exceeds_max_states():
    """More than MAX_STATES distinct states raises IncompatibleQueryError (line 337)."""
    pts = []
    # 66 points → 65 pairs → 65 distinct cur states → > 64 MAX_STATES
    for i in range(66):
        pts.append(sem._Point(ts=i, v=str(i), raw=None, src="exact", state=str(i)))
    import topos.query.errors as qe
    with pytest.raises(qe.IncompatibleQueryError, match="64 distinct states"):
        sem.summarize(pts, sem.ValueSemantic.STATE_DURATION)


# ---------------------------------------------------------------------------
# nl -ba 349 — _state_key returns "true"/"false" for bool     arc [348,349]
# ---------------------------------------------------------------------------
def test_state_key_boolean():
    """_state_key returns 'true'/'false' for bool (line 349)."""
    assert sem._state_key(True) == "true"
    assert sem._state_key(False) == "false"


# ---------------------------------------------------------------------------
# nl -ba 351 — _state_key returns repr(round(v,6)) for float  arc [350,351]
# ---------------------------------------------------------------------------
def test_state_key_float():
    """_state_key returns repr(round(v,6)) for float (line 351)."""
    result = sem._state_key(3.14159265)
    assert result == "3.141593"  # rounded to 6 digits
    result2 = sem._state_key(0.0)
    assert result2 == "0.0"
