"""
Repository Pattern for Neo4j.

Each repository:
- Has a single responsibility (one domain entity)
- Depends only on the driver abstraction
- Is fully replaceable / mockable in tests
- Exposes typed methods, not raw Cypher
"""
from __future__ import annotations
import logging
from abc import ABC, abstractmethod
from typing import Any

from src.graph.driver import Neo4jDriver
from src.models.domain import (
    Act, Section, Subsection, Clause,
    Amendment, AmendmentAction, Rule, CrossReference, AmendmentType
)
from src.core.exceptions import NodeNotFoundError

logger = logging.getLogger(__name__)


# -----------------------------------------
# Base Repository
# -----------------------------------------

class BaseRepository(ABC):
    def __init__(self, driver: Neo4jDriver) -> None:
        self._driver = driver

    def _run(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict]:
        return self._driver.execute_query(cypher, params)

    def _write(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict]:
        return self._driver.execute_write(cypher, params)


# -----------------------------------------
# Schema Initializer
# -----------------------------------------

class SchemaRepository(BaseRepository):
    """Creates constraints and indexes on first run."""

    CONSTRAINTS = [
        "CREATE CONSTRAINT act_id IF NOT EXISTS FOR (a:Act) REQUIRE a.id IS UNIQUE",
        "CREATE CONSTRAINT section_id IF NOT EXISTS FOR (s:Section) REQUIRE s.id IS UNIQUE",
        "CREATE CONSTRAINT subsection_id IF NOT EXISTS FOR (s:Subsection) REQUIRE s.id IS UNIQUE",
        "CREATE CONSTRAINT clause_id IF NOT EXISTS FOR (c:Clause) REQUIRE c.id IS UNIQUE",
        "CREATE CONSTRAINT amendment_id IF NOT EXISTS FOR (a:Amendment) REQUIRE a.id IS UNIQUE",
        "CREATE CONSTRAINT rule_id IF NOT EXISTS FOR (r:Rule) REQUIRE r.id IS UNIQUE",
    ]

    INDEXES = [
        "CREATE INDEX section_number IF NOT EXISTS FOR (s:Section) ON (s.number)",
        "CREATE INDEX amendment_year IF NOT EXISTS FOR (a:Amendment) ON (a.year)",
        "CREATE INDEX rule_number IF NOT EXISTS FOR (r:Rule) ON (r.number)",
    ]

    def initialize(self) -> None:
        for stmt in self.CONSTRAINTS + self.INDEXES:
            try:
                self._write(stmt)
            except Exception as e:
                logger.warning("Schema statement failed (may already exist): %s", e)
        logger.info("Neo4j schema initialized.")


# -----------------------------------------
# Act Repository
# -----------------------------------------

class ActRepository(BaseRepository):

    def upsert(self, act: Act) -> None:
        cypher = """
        MERGE (a:Act {id: $id})
        SET a.title = $title,
            a.year = $year,
            a.number = $number,
            a.short_title = $short_title,
            a.effective_date = $effective_date,
            a.description = $description
        """
        self._write(cypher, {
            "id": act.id,
            "title": act.title,
            "year": act.year,
            "number": act.number,
            "short_title": act.short_title,
            "effective_date": str(act.effective_date) if act.effective_date else None,
            "description": act.description,
        })

    def find_by_id(self, act_id: str) -> dict:
        results = self._run("MATCH (a:Act {id: $id}) RETURN a", {"id": act_id})
        if not results:
            raise NodeNotFoundError("Act", act_id)
        return results[0]["a"]

    def find_all(self) -> list[dict]:
        return [r["a"] for r in self._run("MATCH (a:Act) RETURN a ORDER BY a.year")]


# -----------------------------------------
# Section Repository
# -----------------------------------------

class SectionRepository(BaseRepository):

    def upsert(self, section: Section) -> None:
        cypher = """
        MERGE (s:Section {id: $id})
        SET s.number = $number,
            s.title = $title,
            s.original_content = $original_content,
            s.effective_content = $effective_content,
            s.act_id = $act_id,
            s.order = $order
        WITH s
        MATCH (a:Act {id: $act_id})
        MERGE (a)-[:HAS_SECTION {order: $order}]->(s)
        """
        self._write(cypher, {
            "id": section.id,
            "number": section.number,
            "title": section.title,
            "original_content": section.original_content,
            "effective_content": section.effective_content,
            "act_id": section.act_id,
            "order": section.order,
        })

    def find_by_number(self, act_id: str, number: str) -> dict:
        cypher = """
        MATCH (a:Act {id: $act_id})-[:HAS_SECTION]->(s:Section {number: $number})
        RETURN s
        """
        results = self._run(cypher, {"act_id": act_id, "number": number})
        if not results:
            raise NodeNotFoundError("Section", f"{act_id}:{number}")
        return results[0]["s"]

    def find_by_id(self, section_id: str) -> dict:
        results = self._run("MATCH (s:Section {id: $id}) RETURN s", {"id": section_id})
        if not results:
            raise NodeNotFoundError("Section", section_id)
        return results[0]["s"]

    def find_all_in_act(self, act_id: str) -> list[dict]:
        cypher = """
        MATCH (a:Act {id: $act_id})-[:HAS_SECTION]->(s:Section)
        RETURN s ORDER BY s.order
        """
        return [r["s"] for r in self._run(cypher, {"act_id": act_id})]

    def update_effective_content(self, section_id: str, new_content: str) -> None:
        cypher = """
        MATCH (s:Section {id: $id})
        SET s.effective_content = $content
        """
        self._write(cypher, {"id": section_id, "content": new_content})

    def find_with_amendments(self, section_id: str) -> list[dict]:
        cypher = """
        MATCH (s:Section {id: $id})-[r:AMENDED_BY]->(a:Amendment)
        RETURN s, r, a ORDER BY a.year, a.effective_date
        """
        return self._run(cypher, {"id": section_id})

    def find_with_rules(self, section_id: str) -> list[dict]:
        cypher = """
        MATCH (s:Section {id: $id})-[:DERIVED_RULE]->(r:Rule)
        RETURN s, r
        """
        return self._run(cypher, {"id": section_id})

    def link_subsection(self, section_id: str, subsection_id: str, order: int) -> None:
        cypher = """
        MATCH (s:Section {id: $s_id})
        MATCH (sub:Subsection {id: $sub_id})
        MERGE (s)-[r:HAS_SUBSECTION {order: $order}]->(sub)
        """
        self._write(cypher, {"s_id": section_id, "sub_id": subsection_id, "order": order})


# -----------------------------------------
# Subsection Repository
# -----------------------------------------

class SubsectionRepository(BaseRepository):

    def upsert(self, sub: Subsection) -> None:
        cypher = """
        MERGE (s:Subsection {id: $id})
        SET s.number = $number,
            s.content = $content,
            s.section_id = $section_id
        """
        self._write(cypher, {
            "id": sub.id,
            "number": sub.number,
            "content": sub.content,
            "section_id": sub.section_id,
        })

    def link_clause(self, subsection_id: str, clause_id: str, order: int) -> None:
        cypher = """
        MATCH (sub:Subsection {id: $sub_id})
        MATCH (c:Clause {id: $c_id})
        MERGE (sub)-[r:HAS_CLAUSE {order: $order}]->(c)
        """
        self._write(cypher, {"sub_id": subsection_id, "c_id": clause_id, "order": order})


# -----------------------------------------
# Clause Repository
# -----------------------------------------

class ClauseRepository(BaseRepository):

    def upsert(self, cl: Clause) -> None:
        cypher = """
        MERGE (c:Clause {id: $id})
        SET c.identifier = $identifier,
            c.content = $content,
            c.section_id = $section_id,
            c.subsection_id = $subsection_id
        """
        self._write(cypher, {
            "id": cl.id,
            "identifier": cl.identifier,
            "content": cl.content,
            "section_id": cl.section_id,
            "subsection_id": cl.subsection_id,
        })


# -----------------------------------------
# Amendment Repository
# -----------------------------------------

class AmendmentRepository(BaseRepository):

    def upsert(self, amendment: Amendment) -> None:
        cypher = """
        MERGE (a:Amendment {id: $id})
        SET a.number = $number,
            a.year = $year,
            a.title = $title,
            a.effective_date = $effective_date,
            a.description = $description,
            a.act_id = $act_id
        """
        self._write(cypher, {
            "id": amendment.id,
            "number": amendment.number,
            "year": amendment.year,
            "title": amendment.title,
            "effective_date": str(amendment.effective_date) if amendment.effective_date else None,
            "description": amendment.description,
            "act_id": amendment.act_id,
        })

    def link_action(self, action: AmendmentAction) -> None:
        """Create the typed relationship for an amendment action."""
        rel_map = {
            AmendmentType.SUBSTITUTION: "SUBSTITUTES",
            AmendmentType.INSERTION: "INSERTS",
            AmendmentType.DELETION: "DELETES",
            AmendmentType.RENUMBERING: "RENUMBERS",
        }
        rel_type = rel_map[action.amendment_type]
        cypher = f"""
        MATCH (s:Section {{id: $section_id}})
        MATCH (amend:Amendment {{id: $amendment_id}})
        MERGE (s)-[r:AMENDED_BY {{amendment_id: $amendment_id, type: $type}}]->(amend)
        SET r.effective_date = $effective_date
        WITH s, amend
        MERGE (amend)-[ar:{rel_type} {{action_id: $action_id}}]->(s)
        SET ar.old_content = $old_content,
            ar.new_content = $new_content,
            ar.position = $position,
            ar.effective_date = $effective_date
        """
        self._write(cypher, {
            "section_id": action.target_section_id,
            "amendment_id": action.amendment_id,
            "action_id": action.id,
            "type": action.amendment_type.value,
            "old_content": action.old_content,
            "new_content": action.new_content,
            "position": action.position,
            "effective_date": str(action.effective_date) if action.effective_date else None,
        })

    def find_by_section(self, section_id: str) -> list[dict]:
        cypher = """
        MATCH (s:Section {id: $id})-[r:AMENDED_BY]->(a:Amendment)
        RETURN a, r.type AS amendment_type, r.effective_date AS effective_date
        ORDER BY a.year
        """
        return self._run(cypher, {"id": section_id})

    def find_all(self, act_id: str) -> list[dict]:
        cypher = """
        MATCH (a:Amendment {act_id: $act_id})
        RETURN a ORDER BY a.year
        """
        return [r["a"] for r in self._run(cypher, {"act_id": act_id})]


# -----------------------------------------
# Rule Repository
# -----------------------------------------

class RuleRepository(BaseRepository):

    def upsert(self, rule: Rule) -> None:
        cypher = """
        MERGE (r:Rule {id: $id})
        SET r.number = $number,
            r.title = $title,
            r.content = $content,
            r.act_id = $act_id,
            r.effective_date = $effective_date
        WITH r
        MATCH (a:Act {id: $act_id})
        MERGE (r)-[:UNDER_ACT]->(a)
        """
        self._write(cypher, {
            "id": rule.id,
            "number": rule.number,
            "title": rule.title,
            "content": rule.content,
            "act_id": rule.act_id,
            "effective_date": str(rule.effective_date) if rule.effective_date else None,
        })

    def link_to_section(self, rule_id: str, section_id: str) -> None:
        cypher = """
        MATCH (s:Section {id: $section_id})
        MATCH (r:Rule {id: $rule_id})
        MERGE (s)-[:DERIVED_RULE]->(r)
        """
        self._write(cypher, {"section_id": section_id, "rule_id": rule_id})

    def find_by_section(self, section_id: str) -> list[dict]:
        cypher = """
        MATCH (s:Section {id: $section_id})-[:DERIVED_RULE]->(r:Rule)
        RETURN r ORDER BY r.number
        """
        return [r["r"] for r in self._run(cypher, {"section_id": section_id})]


# -----------------------------------------
# Cross-Reference Repository
# -----------------------------------------

class CrossReferenceRepository(BaseRepository):

    def create(self, ref: CrossReference) -> None:
        cypher = """
        MATCH (s1:Section {id: $source})
        MATCH (s2:Section {id: $target})
        MERGE (s1)-[r:REFERS_TO]->(s2)
        SET r.context = $context
        """
        self._write(cypher, {
            "source": ref.source_section_id,
            "target": ref.target_section_id,
            "context": ref.context,
        })

    def find_references_from(self, section_id: str) -> list[dict]:
        cypher = """
        MATCH (s:Section {id: $id})-[r:REFERS_TO]->(t:Section)
        RETURN t, r.context AS context
        """
        return self._run(cypher, {"id": section_id})
