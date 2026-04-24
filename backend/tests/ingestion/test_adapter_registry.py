import pytest

from src.ingestion.adapters import get_adapter, register_adapter, registered_protocols
from src.ingestion.adapters.ax25 import AX25Adapter
from src.ingestion.adapters.base import ProtocolAdapter
from src.ingestion.models import CanonicalTelemetry


def test_ax25_registered_by_default():
    adapter = get_adapter("ax25")
    assert isinstance(adapter, AX25Adapter)


def test_known_protocols_in_registry():
    protocols = registered_protocols()
    assert "ax25" in protocols
    assert "kiss" in protocols
    assert "ccsds" in protocols


def test_unknown_protocol_raises_key_error():
    with pytest.raises(KeyError, match="warp_drive"):
        get_adapter("warp_drive")


def test_error_message_lists_available_protocols():
    with pytest.raises(KeyError) as exc_info:
        get_adapter("unknown")
    assert "ax25" in str(exc_info.value)


def test_register_custom_adapter():
    class _Stub(ProtocolAdapter):
        @property
        def source_name(self) -> str:
            return "stub"

        def decode(self, raw: bytes) -> CanonicalTelemetry:
            raise NotImplementedError

    stub = _Stub()
    register_adapter("stub", stub)
    assert get_adapter("stub") is stub


def test_register_overwrites_existing():
    class _NewAX25(ProtocolAdapter):
        @property
        def source_name(self) -> str:
            return "ax25"

        def decode(self, raw: bytes) -> CanonicalTelemetry:
            raise NotImplementedError

    replacement = _NewAX25()
    register_adapter("ax25", replacement)
    assert get_adapter("ax25") is replacement

    # Restore the real adapter so other tests aren't affected
    register_adapter("ax25", AX25Adapter())
