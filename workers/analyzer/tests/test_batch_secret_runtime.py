from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Thread
from uuid import uuid4

from familycare_api.documents.secret_channel import BatchSecretSocketClient, SecretHandoff
from familycare_worker.imports.secret_channel import (
    BatchPasswordRegistry,
    BatchSecretSocketServer,
)


def test_socket_ack_means_password_is_already_available(tmp_path: Path) -> None:
    batch_id = uuid4()
    item_id = uuid4()
    handoff_id = uuid4()
    expires_at = datetime.now(UTC) + timedelta(minutes=5)
    registry = BatchPasswordRegistry()
    server = BatchSecretSocketServer(
        tmp_path / "secret.sock",
        active_batches={batch_id},
        on_handoff=registry.replace,
    )
    server.start()
    receiver = Thread(target=server.receive_once)
    receiver.start()
    try:
        BatchSecretSocketClient(tmp_path / "secret.sock").send_once(
            SecretHandoff(
                batch_id=batch_id,
                handoff_id=handoff_id,
                password="synthetic-runtime-password",
                expires_at=expires_at,
            )
        )
        assert registry.password_for(batch_id, item_id) == "synthetic-runtime-password"
    finally:
        receiver.join(timeout=3)
        server.close()
        registry.dispose()


def test_registry_expiry_and_replacement_dispose_old_secret() -> None:
    batch_id = uuid4()
    item_id = uuid4()
    registry = BatchPasswordRegistry()
    registry.replace(
        batch_id,
        uuid4(),
        "synthetic-old-password",
        datetime.now(UTC) + timedelta(minutes=5),
    )
    registry.replace(
        batch_id,
        uuid4(),
        "synthetic-new-password",
        datetime.now(UTC) + timedelta(minutes=5),
    )

    assert registry.password_for(batch_id, item_id) == "synthetic-new-password"
    registry.dispose()
    assert registry.password_for(batch_id, item_id) is None
