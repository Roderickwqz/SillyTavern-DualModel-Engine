"""Operation contracts: transaction-local mutations and their contexts."""

from dataclasses import dataclass
from typing import Any, Protocol
import sqlite3

from .models import Campaign


@dataclass(frozen=True)
class MutationContext:
    campaign: Campaign
    branch_id: str
    next_state_version: int
    now: str


class MutationOperation(Protocol):
    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict[str, Any]:
        """Apply transaction-local writes and return an audit-safe payload."""


@dataclass(frozen=True)
class CompositeOperation:
    operations: tuple[MutationOperation, ...]

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict[str, Any]:
        return {
            "operations": [operation.apply(connection, context) for operation in self.operations]
        }
