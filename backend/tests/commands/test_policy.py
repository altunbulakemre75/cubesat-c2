import pytest
from src.commands.policy import evaluate
from src.ingestion.models import SatelliteMode


def test_nominal_allows_all():
    assert evaluate("camera_on", SatelliteMode.NOMINAL)
    assert evaluate("ping", SatelliteMode.NOMINAL)
    assert evaluate("mode_change", SatelliteMode.NOMINAL)


def test_beacon_only_allows_mode_change():
    assert evaluate("mode_change", SatelliteMode.BEACON)
    assert not evaluate("camera_on", SatelliteMode.BEACON)
    assert not evaluate("ping", SatelliteMode.BEACON)


def test_safe_mode_allows_recovery_commands():
    assert evaluate("recovery", SatelliteMode.SAFE)
    assert evaluate("mode_change", SatelliteMode.SAFE)
    assert evaluate("diagnostic", SatelliteMode.SAFE)
    assert evaluate("reset", SatelliteMode.SAFE)


def test_safe_mode_blocks_nominal_commands():
    assert not evaluate("camera_on", SatelliteMode.SAFE)
    assert not evaluate("download_data", SatelliteMode.SAFE)


def test_science_mode_allows_abort():
    assert evaluate("abort", SatelliteMode.SCIENCE)
    assert evaluate("mode_change", SatelliteMode.SCIENCE)
    assert not evaluate("camera_on", SatelliteMode.SCIENCE)


def test_deployment_allows_deployment_commands():
    assert evaluate("deploy_antenna", SatelliteMode.DEPLOYMENT)
    assert evaluate("deploy_solar_panel", SatelliteMode.DEPLOYMENT)
    assert evaluate("mode_change", SatelliteMode.DEPLOYMENT)
    assert not evaluate("camera_on", SatelliteMode.DEPLOYMENT)


def test_denied_decision_has_reason():
    decision = evaluate("camera_on", SatelliteMode.SAFE)
    assert not decision
    assert "safe" in decision.reason.lower()
    assert "camera_on" in decision.reason


def test_allowed_decision_is_truthy():
    decision = evaluate("recovery", SatelliteMode.SAFE)
    assert decision
    assert bool(decision) is True
