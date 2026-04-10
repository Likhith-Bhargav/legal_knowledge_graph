"""
LegalService -- business logic layer.

Provides high-level operations that combine multiple repository calls.
This is the layer that API handlers and scripts call.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass

from src.graph.driver import Neo4jDriver
from src.graph.repositories import (
    ActRepository, SectionRepository,
    AmendmentRepository, RuleRepository, CrossReferenceRepository,
)
from src.core.exceptions import NodeNotFoundError

logger = logging.getLogger(__name__)


@dataclass
class SectionDetail:
    section: dict
    amendments: list[dict]
    rules: list[dict]
    cross_references: list[dict]


class LegalService:
    """
    High-level operations for the legal knowledge graph.
    All public methods return plain dicts/dataclasses for easy serialization.
    """

    def __init__(self, driver: Neo4jDriver) -> None:
        self._act_repo = ActRepository(driver)
        self._section_repo = SectionRepository(driver)
        self._amendment_repo = AmendmentRepository(driver)
        self._rule_repo = RuleRepository(driver)
        self._xref_repo = CrossReferenceRepository(driver)

    # -- Acts ----------------------------------------------------------

    def list_acts(self) -> list[dict]:
        return self._act_repo.find_all()

    def get_act(self, act_id: str) -> dict:
        return self._act_repo.find_by_id(act_id)

    # -- Sections ------------------------------------------------------

    def get_current_section(self, act_id: str, section_number: str) -> dict:
        """Return the current (post-amendment) text of a section."""
        return self._section_repo.find_by_number(act_id, section_number)

    def list_sections(self, act_id: str) -> list[dict]:
        return self._section_repo.find_all_in_act(act_id)

    def get_section_detail(self, act_id: str, section_number: str) -> SectionDetail:
        """
        Full context for a section: current text + amendments + rules + cross-refs.
        This answers the assignment's "structured explanation" requirement.
        """
        section = self._section_repo.find_by_number(act_id, section_number)
        section_id = section["id"]

        amendments = self._amendment_repo.find_by_section(section_id)
        rules = self._rule_repo.find_by_section(section_id)
        cross_refs = self._xref_repo.find_references_from(section_id)

        return SectionDetail(
            section=section,
            amendments=amendments,
            rules=rules,
            cross_references=cross_refs,
        )

    # -- Amendments ----------------------------------------------------

    def get_amendments_for_section(self, act_id: str, section_number: str) -> list[dict]:
        section = self._section_repo.find_by_number(act_id, section_number)
        return self._amendment_repo.find_by_section(section["id"])

    def list_amendments(self, act_id: str) -> list[dict]:
        return self._amendment_repo.find_all(act_id)

    # -- Rules ---------------------------------------------------------

    def get_rules_for_section(self, act_id: str, section_number: str) -> list[dict]:
        section = self._section_repo.find_by_number(act_id, section_number)
        return self._rule_repo.find_by_section(section["id"])

    # -- Analytics (extensible) ----------------------------------------

    def get_section_impact_summary(self, act_id: str) -> list[dict]:
        """Return sections ranked by number of amendments (most amended first)."""
        cypher = """
        MATCH (a:Act {id: $act_id})-[:HAS_SECTION]->(s:Section)
        OPTIONAL MATCH (s)-[:AMENDED_BY]->(amend:Amendment)
        RETURN s.number AS section, s.title AS title,
               count(amend) AS amendment_count
        ORDER BY amendment_count DESC
        """
        return self._act_repo._run(cypher, {"act_id": act_id})
