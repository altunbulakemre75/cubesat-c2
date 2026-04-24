"""
Bug #9: Command.transition() does dict lookup on _TRANSITIONS but doesn't
handle the case where someone constructs a Command with a status that's
a valid enum value but whose key was forgotten from _TRANSITIONS.

All 8 states are currently mapped, but if a future maintainer adds a new
state to CommandStatus and forgets _TRANSITIONS, the error is cryptic:
KeyError instead of a clear "status is missing from the transition table".

Also test: transition called on terminal state returns a clear error, not
a silent no-op.
"""

import pytest

from src.commands.models import Command, CommandStatus, _TRANSITIONS


def test_all_command_statuses_have_transitions_defined():
    """Every CommandStatus enum value must be a key in _TRANSITIONS.
    Otherwise adding a new state breaks transition() with a cryptic KeyError."""
    missing = [s for s in CommandStatus if s not in _TRANSITIONS]
    assert missing == [], (
        f"CommandStatus values missing from _TRANSITIONS: {missing}. "
        "Add them (even empty sets for terminal states) or transition() crashes."
    )


def test_transition_from_terminal_acked_raises_clear_error():
    """Transitioning from ACKED (terminal) should raise ValueError with
    a clear message — not something cryptic."""
    cmd = Command(
        satellite_id="SAT1",
        command_type="ping",
        status=CommandStatus.ACKED,
    )
    with pytest.raises(ValueError) as exc:
        cmd.transition(CommandStatus.PENDING)
    # Must mention the current status in the error
    assert "acked" in str(exc.value).lower()


def test_transition_from_dead_terminal_raises_clear_error():
    cmd = Command(
        satellite_id="SAT1",
        command_type="ping",
        status=CommandStatus.DEAD,
    )
    with pytest.raises(ValueError):
        cmd.transition(CommandStatus.RETRY)
