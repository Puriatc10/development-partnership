"""Eryndex-owned discovery capability (layer A/C, not an OpenGTM dependency).

Discovery is a business workflow: take a directive, find candidate agencies,
normalize their identity, refuse duplicates and domain conflicts, stage them for
Human Gate 1, and produce a research brief aimed at the IPP anchors.

Providers (OpenGTM, Crawl4AI, SearXNG) are *sources* behind a port, never a
dependency of the workflow itself.
"""

from .identity import (
    CandidateIdentity,
    normalize_domain,
    normalize_name,
    resolve_identity,
)
from .ports import CandidateSink, CandidateSource, JsonFileSource, JsonlOutboxSink, StagingUnavailable
from .service import DiscoveryOutcome, DiscoveryRequest, run_discovery

__all__ = [
    "CandidateIdentity",
    "CandidateSink",
    "CandidateSource",
    "DiscoveryOutcome",
    "DiscoveryRequest",
    "JsonFileSource",
    "JsonlOutboxSink",
    "StagingUnavailable",
    "normalize_domain",
    "normalize_name",
    "resolve_identity",
    "run_discovery",
]
