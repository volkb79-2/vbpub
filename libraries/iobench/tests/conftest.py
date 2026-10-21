import pytest

from iobench import testing


@pytest.fixture
def host():
    return testing.standard_host()
