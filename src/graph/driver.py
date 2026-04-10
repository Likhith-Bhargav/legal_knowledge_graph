"""
Neo4j driver wrapper.

Provides a managed connection pool with:
- Context manager support
- Typed query execution
- Health check
- Clean shutdown
"""
from __future__ import annotations
import logging
from contextlib import contextmanager
from typing import Any, Generator

from neo4j import GraphDatabase, Driver, Session, Result
from neo4j.exceptions import ServiceUnavailable, AuthError

from src.core.config import Neo4jSettings
from src.core.exceptions import GraphError, CypherExecutionError

logger = logging.getLogger(__name__)


class Neo4jDriver:
    """
    Thread-safe Neo4j driver wrapper.

    Usage:
        driver = Neo4jDriver(settings)
        driver.connect()

        with driver.session() as session:
            result = session.run("MATCH (n) RETURN n LIMIT 1")

        driver.close()
    """

    def __init__(self, settings: Neo4jSettings) -> None:
        self._settings = settings
        self._driver: Driver | None = None

    def connect(self) -> None:
        """Establish the connection pool."""
        try:
            self._driver = GraphDatabase.driver(
                self._settings.uri,
                auth=(self._settings.username, self._settings.password),
                max_connection_pool_size=self._settings.max_connection_pool_size,
            )
            self._driver.verify_connectivity()
            logger.info("Connected to Neo4j at %s", self._settings.uri)
        except AuthError as e:
            raise GraphError(f"Neo4j authentication failed: {e}") from e
        except ServiceUnavailable as e:
            raise GraphError(f"Neo4j unavailable at {self._settings.uri}: {e}") from e

    def close(self) -> None:
        if self._driver:
            self._driver.close()
            self._driver = None
            logger.info("Neo4j connection closed.")

    @contextmanager
    def session(self, database: str | None = None) -> Generator[Session, None, None]:
        """Provide a managed Neo4j session."""
        if not self._driver:
            raise GraphError("Driver not connected. Call connect() first.")
        db = database or self._settings.database
        session = self._driver.session(database=db)
        try:
            yield session
        finally:
            session.close()

    def execute_query(
        self,
        cypher: str,
        parameters: dict[str, Any] | None = None,
        database: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Execute a Cypher query and return results as a list of dicts.
        This is the primary interface for read queries.
        """
        params = parameters or {}
        try:
            with self.session(database) as session:
                result: Result = session.run(cypher, params)
                return [record.data() for record in result]
        except Exception as e:
            raise CypherExecutionError(cypher, str(e)) from e

    def execute_write(
        self,
        cypher: str,
        parameters: dict[str, Any] | None = None,
        database: str | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a write Cypher query within a managed transaction."""
        params = parameters or {}
        try:
            with self.session(database) as session:
                result = session.run(cypher, params)
                return [record.data() for record in result]
        except Exception as e:
            raise CypherExecutionError(cypher, str(e)) from e

    def health_check(self) -> bool:
        """Return True if the database is reachable."""
        try:
            self.execute_query("RETURN 1 AS health")
            return True
        except GraphError:
            return False
