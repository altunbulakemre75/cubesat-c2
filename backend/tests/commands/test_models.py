import pytest
from src.commands.models import Command, CommandStatus, MAX_RETRIES


def _cmd(command_type: str = "ping", **kwargs) -> Command:
    return Command(satellite_id="SAT1", command_type=command_type, **kwargs)


def test_initial_status_is_pending():
    assert _cmd().status == CommandStatus.PENDING


def test_valid_transition_pending_to_scheduled():
    cmd = _cmd().transition(CommandStatus.SCHEDULED)
    assert cmd.status == CommandStatus.SCHEDULED


def test_invalid_transition_raises():
    with pytest.raises(ValueError, match="Invalid transition"):
        _cmd().transition(CommandStatus.ACKED)  # PENDING → ACKED not allowed


def test_sent_timestamp_set_on_sent():
    cmd = (
        _cmd()
        .transition(CommandStatus.SCHEDULED)
        .transition(CommandStatus.TRANSMITTING)
        .transition(CommandStatus.SENT)
    )
    assert cmd.sent_at is not None


def test_acked_timestamp_set_on_acked():
    cmd = (
        _cmd()
        .transition(CommandStatus.SCHEDULED)
        .transition(CommandStatus.TRANSMITTING)
        .transition(CommandStatus.SENT)
        .transition(CommandStatus.ACKED)
    )
    assert cmd.acked_at is not None
    assert cmd.is_terminal


def test_retry_increments_count():
    cmd = (
        _cmd(safe_retry=True)
        .transition(CommandStatus.SCHEDULED)
        .transition(CommandStatus.TRANSMITTING)
        .transition(CommandStatus.SENT)
        .transition(CommandStatus.TIMEOUT)
        .transition(CommandStatus.RETRY)
    )
    assert cmd.retry_count == 1


def test_dead_is_terminal():
    cmd = _cmd().transition(CommandStatus.DEAD)
    assert cmd.is_terminal
    with pytest.raises(ValueError):
        cmd.transition(CommandStatus.PENDING)


def test_can_retry_false_when_safe_retry_false():
    cmd = _cmd(safe_retry=False)
    assert not cmd.can_retry


def test_can_retry_true_when_conditions_met():
    cmd = _cmd(safe_retry=True, retry_count=0)
    assert cmd.can_retry


def test_can_retry_false_when_max_retries_reached():
    cmd = _cmd(safe_retry=True, retry_count=MAX_RETRIES)
    assert not cmd.can_retry


def test_unsafe_type_blocks_retry():
    cmd = _cmd(command_type="engine_fire", safe_retry=True)
    assert not cmd.can_retry


def test_idempotency_key_preserved_through_transitions():
    cmd = _cmd(idempotency_key="abc-123").transition(CommandStatus.SCHEDULED)
    assert cmd.idempotency_key == "abc-123"
