from abc import ABC, abstractmethod

from src.ingestion.models import CanonicalTelemetry


class ProtocolAdapter(ABC):
    """
    Convert raw protocol bytes into a CanonicalTelemetry object.

    Implementations must be stateless — decode() may be called concurrently.
    Raise ValueError on malformed input; let the caller decide what to do.
    """

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Protocol identifier written into CanonicalTelemetry.source."""

    @abstractmethod
    def decode(self, raw: bytes) -> CanonicalTelemetry:
        """Decode raw frame bytes and return a validated CanonicalTelemetry."""
