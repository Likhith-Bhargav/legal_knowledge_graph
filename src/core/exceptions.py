"""
Domain-specific exception hierarchy.
All exceptions are typed and traceable.
"""


class LegalKGError(Exception):
    """Base exception for all Legal KG errors."""
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


# --- Graph Errors ---

class GraphError(LegalKGError):
    """Base for Neo4j / graph errors."""


class NodeNotFoundError(GraphError):
    """Raised when a requested node doesn't exist."""
    def __init__(self, node_type: str, identifier: str):
        super().__init__(
            f"{node_type} '{identifier}' not found in the graph.",
            {"node_type": node_type, "identifier": identifier},
        )


class RelationshipError(GraphError):
    """Raised when a relationship operation fails."""


class CypherExecutionError(GraphError):
    """Raised when a Cypher query fails to execute."""
    def __init__(self, query: str, error: str):
        super().__init__(
            f"Cypher execution failed: {error}",
            {"query": query, "error": error},
        )


# --- Ingestion Errors ---

class IngestionError(LegalKGError):
    """Base for document ingestion errors."""


class ParseError(IngestionError):
    """Raised when document parsing fails."""


class ValidationError(IngestionError):
    """Raised when ingested data fails validation."""


# --- Intelligence / LLM Errors ---

class IntelligenceError(LegalKGError):
    """Base for AI/LLM layer errors."""


class QueryTranslationError(IntelligenceError):
    """Raised when NL->Cypher translation fails."""
    def __init__(self, nl_query: str, reason: str):
        super().__init__(
            f"Could not translate query to Cypher: {reason}",
            {"nl_query": nl_query, "reason": reason},
        )


class LLMProviderError(IntelligenceError):
    """Raised when the LLM provider returns an error."""
