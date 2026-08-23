import pytest
from familycare_worker.health import database_is_ready


@pytest.mark.integration
def test_worker_database_probe_reaches_postgresql() -> None:
    assert database_is_ready()
