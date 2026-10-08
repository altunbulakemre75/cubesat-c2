"""The simulator plays the ground station: it must authenticate as the
least-privileged "groundstation" NATS user, with its own inbox prefix
(the server only lets that user read _INBOX_gs.> replies)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.config import SimulatorConfig
from src.publisher import connect_with_retry


def test_password_is_read_from_a_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = tmp_path / "nats_groundstation_password"
    secret.write_text("gs-secret\n", encoding="utf-8")
    monkeypatch.setenv("SIM_NATS_PASSWORD_FILE", str(secret))
    monkeypatch.delenv("SIM_NATS_PASSWORD", raising=False)

    assert SimulatorConfig().resolved_nats_password == "gs-secret"


def test_explicit_password_wins_over_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = tmp_path / "pw"
    secret.write_text("from-file", encoding="utf-8")
    monkeypatch.setenv("SIM_NATS_PASSWORD_FILE", str(secret))
    monkeypatch.setenv("SIM_NATS_PASSWORD", "from-env")

    assert SimulatorConfig().resolved_nats_password == "from-env"


async def test_connects_as_groundstation_with_its_inbox_prefix() -> None:
    config = SimulatorConfig(nats_user="groundstation", nats_password="gs-secret")
    with patch("src.publisher.nats.connect", new=AsyncMock()) as connect:
        await connect_with_retry(config)
    kwargs = connect.call_args.kwargs
    assert kwargs["user"] == "groundstation"
    assert kwargs["password"] == "gs-secret"
    assert kwargs["inbox_prefix"] == "_INBOX_gs"
