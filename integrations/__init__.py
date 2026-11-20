"""Integration adapters (layer D).

Import rules (ADR-0001): adapters may import ``contracts`` only. They may not
import ``agents``, and they never write to the CRM.
"""

from .base import BaseProvider, HttpTransport, ProviderConfig, RequestsTransport, TransportResponse

__all__ = [
    "BaseProvider",
    "HttpTransport",
    "ProviderConfig",
    "RequestsTransport",
    "TransportResponse",
]
