"""
Document Ingestion Pipeline.

Architecture:
  DocumentParser (abstract)
      +-- JSONLegalParser       (structured JSON input)
      +-- TextLegalParser       (semi-structured text -- future)

  GraphIngestionService        (orchestrates parsing -> graph writes)

Open/Closed Principle: add new formats by implementing DocumentParser only.
"""
from __future__ import annotations
import json
import logging
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from src.models.domain import (
    Act, Section, Subsection, Clause,
    Amendment, AmendmentAction, Rule, CrossReference, AmendmentType
)
from src.core.exceptions import ParseError, ValidationError

logger = logging.getLogger(__name__)


# -----------------------------------------
# Parsed Document Container
# -----------------------------------------

class ParsedDocument:
    """Holds all extracted entities from a legal document."""
    def __init__(self) -> None:
        self.act: Act | None = None
        self.sections: list[Section] = []
        self.amendments: list[Amendment] = []
        self.amendment_actions: list[AmendmentAction] = []
        self.rules: list[Rule] = []
        self.cross_references: list[CrossReference] = []


# -----------------------------------------
# Abstract Parser
# -----------------------------------------

class DocumentParser(ABC):
    @abstractmethod
    def parse(self, source: str | Path | dict) -> ParsedDocument:
        """Parse the source into a ParsedDocument."""
        ...

    @abstractmethod
    def can_parse(self, source: str | Path | dict) -> bool:
        """Return True if this parser can handle the given source."""
        ...


# -----------------------------------------
# JSON Parser (primary format)
# -----------------------------------------

class JSONLegalParser(DocumentParser):
    """
    Parses a structured JSON representation of a legal Act.

    Expected JSON shape:
    {
      "act": { "id", "title", "year", "number", ... },
      "sections": [
        {
          "id", "number", "title", "content",
          "subsections": [
            { "id", "number", "content", "clauses": [...] }
          ]
        }
      ],
      "amendments": [
        {
          "id", "number", "year", "title",
          "actions": [
            { "type": "substitution", "section_id", "new_content", "old_content" }
          ]
        }
      ],
      "rules": [{ "id", "number", "title", "content", "section_id" }],
      "cross_references": [{ "source_section_id", "target_section_id", "context" }]
    }
    """

    def can_parse(self, source) -> bool:
        if isinstance(source, dict):
            return "act" in source
        if isinstance(source, (str, Path)):
            p = Path(source)
            return p.suffix == ".json" and p.exists()
        return False

    def parse(self, source) -> ParsedDocument:
        if isinstance(source, (str, Path)):
            try:
                data = json.loads(Path(source).read_text())
            except (json.JSONDecodeError, IOError) as e:
                raise ParseError(f"Failed to load JSON: {e}") from e
        elif isinstance(source, dict):
            data = source
        else:
            raise ParseError("Unsupported source type for JSONLegalParser")

        doc = ParsedDocument()
        doc.act = self._parse_act(data["act"])
        doc.sections = self._parse_sections(data.get("sections", []), doc.act.id)
        doc.amendments, doc.amendment_actions = self._parse_amendments(
            data.get("amendments", []), doc.act.id
        )
        doc.rules = self._parse_rules(data.get("rules", []), doc.act.id)
        doc.cross_references = self._parse_cross_refs(data.get("cross_references", []))
        return doc

    def _parse_act(self, data: dict) -> Act:
        return Act(
            id=data["id"],
            title=data["title"],
            year=int(data["year"]),
            number=str(data["number"]),
            short_title=data.get("short_title"),
            description=data.get("description"),
        )

    def _parse_sections(self, sections_data: list[dict], act_id: str) -> list[Section]:
        sections = []
        for i, s in enumerate(sections_data):
            section = Section(
                id=s.get("id", f"{act_id}_sec_{s['number']}"),
                number=str(s["number"]),
                title=s.get("title"),
                original_content=s["content"],
                effective_content=s["content"],
                act_id=act_id,
                order=s.get("order", i),
                subsections=[
                    Subsection(
                        id=sub.get("id", f"{act_id}_sub_{s['number']}_{sub['number']}"),
                        number=str(sub["number"]),
                        content=sub["content"],
                        section_id=s.get("id", f"{act_id}_sec_{s['number']}"),
                        clauses=[
                            Clause(
                                id=cl.get("id", str(uuid.uuid4())),
                                identifier=cl["identifier"],
                                content=cl["content"],
                                section_id=s.get("id", f"{act_id}_sec_{s['number']}"),
                                subsection_id=sub.get("id"),
                            )
                            for cl in sub.get("clauses", [])
                        ]
                    )
                    for sub in s.get("subsections", [])
                ]
            )
            sections.append(section)
        return sections

    def _parse_amendments(
        self, amendments_data: list[dict], act_id: str
    ) -> tuple[list[Amendment], list[AmendmentAction]]:
        amendments, actions = [], []
        for a in amendments_data:
            amend = Amendment(
                id=a["id"],
                number=str(a["number"]),
                year=int(a["year"]),
                title=a["title"],
                description=a.get("description"),
                act_id=act_id,
            )
            amendments.append(amend)
            for i, act_data in enumerate(a.get("actions", [])):
                action = AmendmentAction(
                    id=act_data.get("id", f"{a['id']}_action_{i}"),
                    amendment_id=a["id"],
                    amendment_type=AmendmentType(act_data["type"]),
                    target_section_id=act_data["section_id"],
                    target_subsection_id=act_data.get("subsection_id"),
                    old_content=act_data.get("old_content"),
                    new_content=act_data.get("new_content"),
                    position=act_data.get("position"),
                )
                actions.append(action)
        return amendments, actions

    def _parse_rules(self, rules_data: list[dict], act_id: str) -> list[Rule]:
        return [
            Rule(
                id=r["id"],
                number=str(r["number"]),
                title=r["title"],
                content=r["content"],
                act_id=act_id,
                section_id=r.get("section_id"),
            )
            for r in rules_data
        ]

    def _parse_cross_refs(self, refs_data: list[dict]) -> list[CrossReference]:
        return [
            CrossReference(
                source_section_id=r["source_section_id"],
                target_section_id=r["target_section_id"],
                context=r.get("context", ""),
            )
            for r in refs_data
        ]


# -----------------------------------------
# Parser Registry
# -----------------------------------------

class ParserRegistry:
    """
    Finds the right parser for a given source.
    Extensible: register new parsers with .register().
    """
    def __init__(self) -> None:
        self._parsers: list[DocumentParser] = []

    def register(self, parser: DocumentParser) -> None:
        self._parsers.append(parser)

    def get_parser(self, source) -> DocumentParser:
        for parser in self._parsers:
            if parser.can_parse(source):
                return parser
        raise ParseError(f"No parser found for source: {type(source)}")


def build_default_registry() -> ParserRegistry:
    registry = ParserRegistry()
    registry.register(JSONLegalParser())
    # PDFLegalParser requires an LLM provider and act metadata at construction time.
    # It is instantiated and used directly by scripts/ingest_pdf.py.
    return registry

