"""
Cross-cutting invariant + regression tests.

Bu dosya 3 grupla doludur:
  1. Command state machine invariants (model-level, no DB)
  2. Regression tests for previously-fixed live bugs
  3. Storage / config sanity
"""

from __future__ import annotations

import pytest

from src.commands.models import (
    Command,
    CommandStatus,
    MAX_RETRIES,
    UNSAFE_RETRY_TYPES,
)


# ─────────────────────────────────────────────────────────────────────
# 1. Command state machine — invariants
# ─────────────────────────────────────────────────────────────────────

def _cmd(**kw) -> Command:
    base = {"satellite_id": "SAT1", "command_type": "ping"}
    base.update(kw)
    return Command(**base)


def test_command_id_is_uuid_format():
    """Default factory must produce a UUID-shaped string."""
    import uuid as _uuid
    cmd = _cmd()
    _uuid.UUID(cmd.id)  # raises if not a valid UUID


def test_command_acked_is_terminal_no_further_transitions():
    cmd = _cmd().transition(CommandStatus.SCHEDULED)
    cmd = cmd.transition(CommandStatus.TRANSMITTING)
    cmd = cmd.transition(CommandStatus.SENT)
    cmd = cmd.transition(CommandStatus.ACKED)
    with pytest.raises(ValueError):
        cmd.transition(CommandStatus.SCHEDULED)


def test_command_dead_is_terminal_no_resurrection():
    cmd = _cmd().transition(CommandStatus.DEAD)
    for target in CommandStatus:
        if target == CommandStatus.DEAD:
            continue
        with pytest.raises(ValueError):
            cmd.transition(target)


def test_pending_to_pending_is_invalid():
    """Self-transitions don't make sense."""
    with pytest.raises(ValueError):
        _cmd().transition(CommandStatus.PENDING)


def test_unsafe_retry_types_never_safe_to_retry():
    """can_retry must be False for engine_fire, deploy_*, etc.
    Even with safe_retry=True and retry_count=0."""
    for unsafe in UNSAFE_RETRY_TYPES:
        cmd = _cmd(command_type=unsafe, safe_retry=True, retry_count=0)
        assert cmd.can_retry is False, f"{unsafe} unexpectedly retryable"


def test_safe_retry_false_blocks_can_retry():
    cmd = _cmd(safe_retry=False, retry_count=0)
    assert cmd.can_retry is False


def test_can_retry_at_max_retries_minus_one():
    cmd = _cmd(safe_retry=True, retry_count=MAX_RETRIES - 1)
    assert cmd.can_retry is True


def test_cannot_retry_at_max_retries():
    cmd = _cmd(safe_retry=True, retry_count=MAX_RETRIES)
    assert cmd.can_retry is False


def test_priority_must_be_in_range():
    """priority field is constrained 1-10. Pydantic should reject 0 and 11."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        _cmd(priority=0)
    with pytest.raises(ValidationError):
        _cmd(priority=11)


# ─────────────────────────────────────────────────────────────────────
# 2. Regression — bugs we already fixed must stay fixed
# ─────────────────────────────────────────────────────────────────────

def test_regression_anomaly_event_appevent_shape():
    """fdir/monitor.py and writer.py both must publish events that match
    the frontend AppEvent contract: id, type, satellite_id, message,
    timestamp, severity. This regression test inspects the writer's
    publish call shape.

    Bug fixed in commit 61a3fde — without these fields, Dashboard.tsx
    crashed on event.type.toUpperCase()."""
    import json
    import asyncio
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock, MagicMock

    from src.anomaly.detector import AnomalyEvent
    from src.ingestion.writer import TelemetryWriter

    js = MagicMock()
    js.publish = AsyncMock()
    pool = MagicMock()
    detector = MagicMock()
    w = TelemetryWriter(js, pool, detector=detector)

    ev = AnomalyEvent(
        satellite_id="X", parameter="battery_voltage_v",
        value=2.5, z_score=4.2, severity="critical",
        detected_at=datetime.now(timezone.utc),
    )
    detector.feed.return_value = [ev]

    # Mock the DB acquire context
    class _DbCtx:
        def __init__(self):
            self.execute = AsyncMock()
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None
    pool.acquire = lambda: _DbCtx()

    from src.ingestion.models import CanonicalTelemetry, TelemetryParams, SatelliteMode
    telem = CanonicalTelemetry(
        timestamp=datetime.now(timezone.utc), satellite_id="X",
        source="ax25", sequence=1,
        params=TelemetryParams(
            battery_voltage_v=2.5, temperature_obcs_c=25.0,
            temperature_eps_c=22.0, solar_power_w=2.5,
            rssi_dbm=-90, uptime_s=1, mode=SatelliteMode.NOMINAL,
        ),
    )

    asyncio.get_event_loop().run_until_complete(w._run_anomaly_detection(telem))
    body = json.loads(js.publish.await_args.args[1].decode())
    assert "id" in body
    assert "type" in body
    assert "message" in body
    assert "timestamp" in body
    assert "severity" in body


def test_regression_nats_subjects_use_recursive_wildcard():
    """commands.* would fail to match commands.ack.{sat} (3 tokens).
    events.* would fail to match events.anomaly.{sat}. Both must use '>'."""
    from src.ingestion.service import _STREAM_SUBJECTS
    assert "commands.>" in _STREAM_SUBJECTS
    assert "events.>" in _STREAM_SUBJECTS
    # The single-token wildcards must NOT be present (they were the bug)
    assert "commands.*" not in _STREAM_SUBJECTS
    assert "events.*" not in _STREAM_SUBJECTS


def test_regression_format_clock_time_uses_24h():
    """formatClockTime in Windows defaults to 12h with AM/PM. We forced
    hour12:false. This file is not a JS test, so we just assert the
    source contains the fix."""
    from pathlib import Path
    fmt_path = Path(__file__).parents[2] / "frontend" / "src" / "lib" / "format.ts"
    if not fmt_path.exists():
        pytest.skip("frontend not present in this checkout")
    src = fmt_path.read_text(encoding="utf-8")
    assert "hour12: false" in src, "regression: 12h default would return AM/PM"


def test_regression_app_event_dedupe_in_store():
    """Frontend store dedupes events by id to prevent the WS-replay bug."""
    from pathlib import Path
    store_path = Path(__file__).parents[2] / "frontend" / "src" / "store" / "index.ts"
    if not store_path.exists():
        pytest.skip("frontend not present")
    src = store_path.read_text(encoding="utf-8")
    assert "event.id" in src and "some(" in src, "dedupe-by-id missing in store"


def test_regression_satnogs_fetcher_uses_real_datetime():
    """asyncpg TIMESTAMPTZ rejects ISO strings. Fetcher must parse to
    datetime before INSERT — we keep _parse_iso as proof."""
    from src.ingestion.satnogs_fetcher import _parse_iso
    out = _parse_iso("2026-04-27T10:00:00Z")
    from datetime import datetime as _dt
    assert isinstance(out, _dt)


def test_regression_stream_update_on_subject_drift():
    """ensure_stream must call update_stream when wanted subjects differ
    from existing ones. Source-level inspection — the function must
    contain 'update_stream'."""
    import inspect
    from src.ingestion import service
    src = inspect.getsource(service.ensure_stream)
    assert "update_stream" in src


def test_regression_admin_bootstrap_writes_password_to_file_not_logs():
    """Logs must not contain the cleartext password. Source-level check."""
    import inspect
    from src.api import bootstrap
    src = inspect.getsource(bootstrap.ensure_admin_user)
    # Password must not be passed as a logger argument anywhere on the
    # happy path. We at least assert the WARNING line says 'Password
    # location' (file path), not 'Password:' verbatim.
    assert "Password location" in src


# ─────────────────────────────────────────────────────────────────────
# 3. Config / settings sanity
# ─────────────────────────────────────────────────────────────────────

def test_settings_has_jwt_algorithm_default():
    from src.config import settings
    assert settings.jwt_algorithm in ("HS256", "HS384", "HS512", "RS256")


def test_settings_jwt_secret_length_at_least_32():
    """A short JWT secret is brute-forceable. At app startup the secret
    must already be at least 32 chars (conftest puts a known good one)."""
    from src.config import settings
    assert len(settings.jwt_secret_key) >= 32


def test_settings_postgres_port_is_int():
    from src.config import settings
    assert isinstance(settings.postgres_port, int)
    assert 1 <= settings.postgres_port <= 65535
