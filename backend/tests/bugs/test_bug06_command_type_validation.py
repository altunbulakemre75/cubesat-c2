"""
Bug #6: CommandCreate.command_type is a free-form str.
Accepts empty string, whitespace-only, absurdly long strings, etc.
Downstream code trusts this value and persists it to the DB.

Fix: add Pydantic validation — non-empty, reasonable max length, no
whitespace-only values, optional pattern match.
"""

import pytest
from pydantic import ValidationError

from src.api.schemas import CommandCreate


def test_empty_command_type_rejected():
    with pytest.raises(ValidationError):
        CommandCreate(satellite_id="SAT1", command_type="")


def test_whitespace_only_command_type_rejected():
    with pytest.raises(ValidationError):
        CommandCreate(satellite_id="SAT1", command_type="   ")


def test_absurdly_long_command_type_rejected():
    with pytest.raises(ValidationError):
        CommandCreate(satellite_id="SAT1", command_type="x" * 200)


def test_empty_satellite_id_rejected():
    with pytest.raises(ValidationError):
        CommandCreate(satellite_id="", command_type="ping")


def test_valid_command_passes():
    cmd = CommandCreate(satellite_id="SAT1", command_type="ping")
    assert cmd.command_type == "ping"
