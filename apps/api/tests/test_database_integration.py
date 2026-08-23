import pytest
from familycare_api.health import database_is_ready


@pytest.mark.integration
def test_api_database_probe_reaches_postgresql() -> None:
    assert database_is_ready()
