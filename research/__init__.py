"""Research execution layer: provider payload in, persisted research out.

Entry point is :func:`research.executor.execute_research`. Nothing here opens a database
connection: the caller hands in an RLS-bound client.
"""

from __future__ import annotations

from .executor import (
    CATEGORIES,
    CONFIDENCES,
    FINDING_TYPES,
    CompanyNotVisibleError,
    CompanySuppressedError,
    DuplicateReportVersion,
    Finding,
    FixtureResearchProvider,
    IntegrationResearchProvider,
    ResearchError,
    ResearchIdentityError,
    ResearchPayload,
    ResearchPersistenceError,
    ResearchProvider,
    ResearchProviderError,
    ResearchResult,
    ResearchValidationError,
    ResearchWriteDenied,
    Source,
    TaskNotVisibleError,
    TaskOwnershipError,
    TaskScopeError,
    TaskStateError,
    execute_research,
    provider_from_integration,
    validate_payload,
)

__all__ = [
    "CATEGORIES",
    "CONFIDENCES",
    "FINDING_TYPES",
    "CompanyNotVisibleError",
    "CompanySuppressedError",
    "DuplicateReportVersion",
    "Finding",
    "FixtureResearchProvider",
    "IntegrationResearchProvider",
    "ResearchError",
    "ResearchIdentityError",
    "ResearchPayload",
    "ResearchPersistenceError",
    "ResearchProvider",
    "ResearchProviderError",
    "ResearchResult",
    "ResearchValidationError",
    "ResearchWriteDenied",
    "Source",
    "TaskNotVisibleError",
    "TaskOwnershipError",
    "TaskScopeError",
    "TaskStateError",
    "execute_research",
    "provider_from_integration",
    "validate_payload",
]
