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
from .ports import (
    BATCH_CONSTRAINT,
    CANDIDATE_COLUMNS,
    EVIDENCE_COLUMNS,
    PENDING_DOMAIN_CONSTRAINT,
    AlreadyStaged,
    CandidatePersistenceError,
    CandidateSink,
    CandidateSource,
    EvidencePersistenceError,
    JsonFileSource,
    JsonlOutboxSink,
    PartialRecoveryError,
    StagingContractError,
    StagingResult,
    StagingUnavailable,
    SupabaseCandidateSink,
    candidate_evidence_rows,
    reject_unknown_columns,
)
from .service import (
    ACTOR_PATTERN,
    DiscoveryIdentityError,
    DiscoveryOutcome,
    DiscoveryRequest,
    DiscoveryRequestError,
    compute_batch_key,
    run_discovery,
)

__all__ = [
    "ACTOR_PATTERN",
    "BATCH_CONSTRAINT",
    "CANDIDATE_COLUMNS",
    "EVIDENCE_COLUMNS",
    "PENDING_DOMAIN_CONSTRAINT",
    "AlreadyStaged",
    "CandidateIdentity",
    "CandidatePersistenceError",
    "CandidateSink",
    "CandidateSource",
    "DiscoveryIdentityError",
    "DiscoveryOutcome",
    "DiscoveryRequest",
    "DiscoveryRequestError",
    "EvidencePersistenceError",
    "JsonFileSource",
    "JsonlOutboxSink",
    "PartialRecoveryError",
    "StagingContractError",
    "StagingResult",
    "StagingUnavailable",
    "SupabaseCandidateSink",
    "candidate_evidence_rows",
    "compute_batch_key",
    "normalize_domain",
    "normalize_name",
    "reject_unknown_columns",
    "resolve_identity",
    "run_discovery",
]
