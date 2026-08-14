"""Stable domain exception types shared across the engine."""


class DomainError(Exception):
    """Base class for all domain-level errors."""


class ValidationError(DomainError):
    """Raised when domain data violates a validation rule."""


class StaleStateError(DomainError):
    """Raised when a mutation targets an outdated state version."""


class NotFoundError(DomainError):
    """Raised when a requested entity does not exist."""


class ConfirmationRequiredError(DomainError):
    """Raised when an operation requires explicit confirmation."""


class AmbiguousEntityError(DomainError):
    """Raised when a name matches multiple entities."""


class BranchResolutionError(DomainError):
    """Raised when visible history cannot be mapped to exactly one parent turn."""
