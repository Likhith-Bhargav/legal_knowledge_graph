"""
GraphIngestionService -- orchestrates writing a ParsedDocument into Neo4j.

This service:
1. Writes all nodes in dependency order
2. Applies amendment actions to update effective_content
3. Creates all relationships
4. Is idempotent (MERGE-based -- safe to run multiple times)
"""
from __future__ import annotations
import logging
from dataclasses import dataclass

from src.ingestion.parsers import ParsedDocument, ParserRegistry, build_default_registry
from src.graph.repositories import (
    ActRepository, SectionRepository, SubsectionRepository, ClauseRepository,
    AmendmentRepository, RuleRepository, CrossReferenceRepository, SchemaRepository
)
from src.graph.driver import Neo4jDriver
from src.models.domain import AmendmentType

logger = logging.getLogger(__name__)


@dataclass
class IngestionResult:
    act_id: str
    sections_ingested: int
    amendments_ingested: int
    rules_ingested: int
    cross_references_ingested: int
    warnings: list[str]


class GraphIngestionService:
    """
    Orchestrates the full ingestion pipeline:
    Parse -> Validate -> Write to Graph -> Apply Amendments
    """

    def __init__(self, driver: Neo4jDriver) -> None:
        self._driver = driver
        self._schema_repo = SchemaRepository(driver)
        self._act_repo = ActRepository(driver)
        self._section_repo = SectionRepository(driver)
        self._subsection_repo = SubsectionRepository(driver)
        self._clause_repo = ClauseRepository(driver)
        self._amendment_repo = AmendmentRepository(driver)
        self._rule_repo = RuleRepository(driver)
        self._xref_repo = CrossReferenceRepository(driver)
        self._parser_registry: ParserRegistry = build_default_registry()

    def initialize_schema(self) -> None:
        self._schema_repo.initialize()

    def ingest(self, source) -> IngestionResult:
        """
        Main entry point. Accepts a file path, dict, or any parseable source.
        Returns an IngestionResult with counts and any warnings.
        """
        logger.info("Starting ingestion from source: %s", type(source).__name__)

        parser = self._parser_registry.get_parser(source)
        doc = parser.parse(source)
        return self._write_to_graph(doc)

    def _write_to_graph(self, doc: ParsedDocument) -> IngestionResult:
        warnings: list[str] = []

        # 1. Act
        self._act_repo.upsert(doc.act)
        logger.info("Ingested Act: %s", doc.act.title)

        # 2. Sections + Hierarchy
        for section in doc.sections:
            self._section_repo.upsert(section)
            
            # Deep persist Subsections and Clauses
            for i, sub in enumerate(section.subsections):
                self._subsection_repo.upsert(sub)
                self._section_repo.link_subsection(section.id, sub.id, i)
                
                for j, cl in enumerate(sub.clauses):
                    self._clause_repo.upsert(cl)
                    self._subsection_repo.link_clause(sub.id, cl.id, j)
                    
        logger.info("Ingested %d sections with full hierarchy", len(doc.sections))

        # 3. Amendments
        for amendment in doc.amendments:
            self._amendment_repo.upsert(amendment)
        logger.info("Ingested %d amendments", len(doc.amendments))


        # 4. Amendment Actions -- link + apply to effective_content
        section_map = {s.id: s for s in doc.sections}
        for action in doc.amendment_actions:
            try:
                self._amendment_repo.link_action(action)
                self._apply_amendment_action(action, section_map, warnings)
            except Exception as e:
                warnings.append(f"Amendment action {action.id} failed: {e}")
                logger.warning("Amendment action %s failed: %s", action.id, e)

        # 5. Rules
        for rule in doc.rules:
            self._rule_repo.upsert(rule)
            if rule.section_id:
                try:
                    self._rule_repo.link_to_section(rule.id, rule.section_id)
                except Exception as e:
                    warnings.append(f"Rule-section link failed for {rule.id}: {e}")
        logger.info("Ingested %d rules", len(doc.rules))

        # 6. Cross References
        for xref in doc.cross_references:
            try:
                self._xref_repo.create(xref)
            except Exception as e:
                warnings.append(f"Cross-reference failed: {e}")
        logger.info("Ingested %d cross-references", len(doc.cross_references))

        return IngestionResult(
            act_id=doc.act.id,
            sections_ingested=len(doc.sections),
            amendments_ingested=len(doc.amendments),
            rules_ingested=len(doc.rules),
            cross_references_ingested=len(doc.cross_references),
            warnings=warnings,
        )

    def _apply_amendment_action(self, action, section_map: dict, warnings: list[str]) -> None:
        """
        Apply amendment to update the graph node's effective_content.
        """
        section = section_map.get(action.target_section_id)

        if not section:
            warnings.append(
                f"Section {action.target_section_id} not found for amendment action {action.id}"
            )
            return

        if action.amendment_type == AmendmentType.SUBSTITUTION and action.new_content:
            if action.old_content:
                new_text = section.effective_content.replace(
                    action.old_content, action.new_content
                )
            else:
                new_text = action.new_content
            self._section_repo.update_effective_content(section.id, new_text)

        elif action.amendment_type == AmendmentType.DELETION:
            if action.old_content:
                new_text = section.effective_content.replace(action.old_content, "").strip()
                self._section_repo.update_effective_content(section.id, new_text)

        elif action.amendment_type == AmendmentType.INSERTION and action.new_content:
            new_text = section.effective_content + "\n" + action.new_content
            self._section_repo.update_effective_content(section.id, new_text)
