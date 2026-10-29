def test_live_lane_is_registered_and_excluded_by_default(pytestconfig):
    assert any(marker.startswith("live:") for marker in pytestconfig.getini("markers"))
    assert "not live" in pytestconfig.getini("addopts")
