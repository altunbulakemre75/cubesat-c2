"""
Protocol adapter registry.

Usage:
    from src.ingestion.adapters import get_adapter, register_adapter

    adapter = get_adapter("ax25")
    canonical = adapter.decode(raw_bytes)

Adding a new protocol at runtime:
    register_adapter("myproto", MyProtoAdapter())
"""

from src.ingestion.adapters.ax25 import AX25Adapter
from src.ingestion.adapters.base import ProtocolAdapter
from src.ingestion.adapters.ccsds import CCSDSAdapter
from src.ingestion.adapters.kiss import KISSAdapter

_registry: dict[str, ProtocolAdapter] = {
    "ax25": AX25Adapter(),
    "kiss": KISSAdapter(),
    "ccsds": CCSDSAdapter(),
}


def get_adapter(protocol: str) -> ProtocolAdapter:
    """Return the registered adapter for the given protocol name."""
    try:
        return _registry[protocol]
    except KeyError:
        available = sorted(_registry)
        raise KeyError(
            f"No adapter registered for protocol '{protocol}'. "
            f"Available: {available}"
        ) from None


def register_adapter(protocol: str, adapter: ProtocolAdapter) -> None:
    """Register a custom adapter, overwriting any existing entry."""
    _registry[protocol] = adapter


def registered_protocols() -> list[str]:
    return sorted(_registry)
