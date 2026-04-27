"""Tests for the CCSDS Space Packet adapter."""

import json

import pytest

from src.ingestion.adapters.ccsds import CCSDSAdapter, build_ccsds_packet
from src.ingestion.models import SatelliteMode

adapter = CCSDSAdapter()


def _valid_payload(**overrides) -> dict:
    base = {
        "satellite_id": "CUBESAT1",
        "mode": "nominal",
        "battery_voltage_v": 3.9,
        "temperature_obcs_c": 25.0,
        "temperature_eps_c": 20.0,
        "solar_power_w": 2.5,
        "rssi_dbm": -95.0,
        "uptime_s": 3600,
        "sequence": 42,
    }
    base.update(overrides)
    return base


class TestDecode:
    def test_decode_valid_packet(self):
        payload = json.dumps(_valid_payload()).encode()
        pkt = build_ccsds_packet(apid=100, seq_count=42, payload_json=payload)
        ct = adapter.decode(pkt)
        assert ct.satellite_id == "CUBESAT1"
        assert ct.source == "ccsds"
        assert ct.params.mode == SatelliteMode.NOMINAL

    def test_decode_with_secondary_header(self):
        payload = json.dumps(_valid_payload()).encode()
        pkt = build_ccsds_packet(
            apid=100, seq_count=42, payload_json=payload, secondary_header=True,
        )
        ct = adapter.decode(pkt)
        assert ct.satellite_id == "CUBESAT1"

    def test_uses_apid_when_satellite_id_missing(self):
        payload = json.dumps(_valid_payload(satellite_id=None)).encode()
        # Remove the key so the fallback kicks in
        d = _valid_payload()
        del d["satellite_id"]
        pkt = build_ccsds_packet(apid=255, seq_count=1, payload_json=json.dumps(d).encode())
        ct = adapter.decode(pkt)
        assert ct.satellite_id == "APID-255"

    def test_falls_back_to_seq_count_when_no_sequence(self):
        d = _valid_payload()
        del d["sequence"]
        pkt = build_ccsds_packet(apid=10, seq_count=999, payload_json=json.dumps(d).encode())
        ct = adapter.decode(pkt)
        assert ct.sequence == 999


class TestRejection:
    def test_rejects_too_short(self):
        with pytest.raises(ValueError, match="too short"):
            adapter.decode(b"\x00\x00")

    def test_rejects_telecommand(self):
        # Set type bit (bit 12 of word0)
        pkt = bytearray(build_ccsds_packet(
            apid=1, seq_count=0,
            payload_json=json.dumps(_valid_payload()).encode(),
        ))
        pkt[0] |= 0x10  # set type=1
        with pytest.raises(ValueError, match="telecommand"):
            adapter.decode(bytes(pkt))

    def test_rejects_wrong_version(self):
        pkt = bytearray(build_ccsds_packet(
            apid=1, seq_count=0,
            payload_json=json.dumps(_valid_payload()).encode(),
        ))
        pkt[0] |= 0xE0  # set version=7
        with pytest.raises(ValueError, match="version"):
            adapter.decode(bytes(pkt))

    def test_rejects_length_mismatch(self):
        pkt = build_ccsds_packet(
            apid=1, seq_count=0,
            payload_json=json.dumps(_valid_payload()).encode(),
        )
        # Truncate user data
        with pytest.raises(ValueError, match="length mismatch"):
            adapter.decode(pkt[:-5])

    def test_rejects_invalid_json(self):
        pkt = build_ccsds_packet(apid=1, seq_count=0, payload_json=b"not-json")
        with pytest.raises(ValueError, match="JSON"):
            adapter.decode(pkt)

    def test_rejects_missing_field(self):
        d = _valid_payload()
        del d["battery_voltage_v"]
        pkt = build_ccsds_packet(apid=1, seq_count=0, payload_json=json.dumps(d).encode())
        with pytest.raises(ValueError, match="validation"):
            adapter.decode(pkt)
