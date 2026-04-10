"""
Unit tests for the core domain logic.
These tests use a mock driver -- no Neo4j required.
"""
import pytest
from unittest.mock import MagicMock

from src.models.domain import (
    Act, Section, Amendment, AmendmentAction, Rule, AmendmentType
)
from src.core.exceptions import NodeNotFoundError, ParseError
from src.ingestion.parsers import JSONLegalParser


# -----------------------------------------
# Domain Model Tests
# -----------------------------------------

class TestDomainModels:
    def test_act_is_immutable(self):
        act = Act(id="TEST_1", title="Test Act", year=2020, number="1")
        with pytest.raises(Exception):
            act.id = "CHANGED"

    def test_amendment_type_enum(self):
        assert AmendmentType("substitution") == AmendmentType.SUBSTITUTION
        assert AmendmentType("insertion") == AmendmentType.INSERTION
        with pytest.raises(ValueError):
            AmendmentType("unknown_type")

    def test_section_effective_content_defaults_to_original(self):
        section = Section(
            id="S1", number="1",
            original_content="Original text.",
            effective_content="Original text.",
            act_id="ACT1",
        )
        assert section.effective_content == section.original_content

    def test_rule_section_id_optional(self):
        rule = Rule(id="R1", number="1", title="Rule", content="Content", act_id="ACT1")
        assert rule.section_id is None


# -----------------------------------------
# JSON Parser Tests
# -----------------------------------------

SAMPLE = {
    "act": {
        "id": "TEST_ACT", "title": "Test Act",
        "year": 2000, "number": "1"
    },
    "sections": [
        {
            "id": "TEST_ACT_S1", "number": "1",
            "title": "Title", "content": "Content of section 1.",
            "subsections": []
        },
        {
            "id": "TEST_ACT_S2", "number": "2",
            "title": "Second section", "content": "Content of section 2.",
            "subsections": [
                {
                    "id": "TEST_ACT_S2_sub1", "number": "1",
                    "content": "Subsection content.", "clauses": []
                }
            ]
        }
    ],
    "amendments": [
        {
            "id": "AMEND_1", "number": "1", "year": 2005,
            "title": "Amendment 1",
            "actions": [
                {
                    "id": "ACTION_1",
                    "type": "substitution",
                    "section_id": "TEST_ACT_S1",
                    "old_content": "Content of section 1.",
                    "new_content": "Updated content of section 1."
                }
            ]
        }
    ],
    "rules": [
        {
            "id": "RULE_1", "number": "1", "title": "Rule 1",
            "content": "Rule content.", "section_id": "TEST_ACT_S1"
        }
    ],
    "cross_references": [
        {
            "source_section_id": "TEST_ACT_S2",
            "target_section_id": "TEST_ACT_S1",
            "context": "as defined in"
        }
    ]
}


class TestJSONLegalParser:
    def test_can_parse_dict_with_act_key(self):
        parser = JSONLegalParser()
        assert parser.can_parse({"act": {}}) is True
        assert parser.can_parse({"no_act": {}}) is False

    def test_parses_act(self):
        parser = JSONLegalParser()
        doc = parser.parse(SAMPLE)
        assert doc.act is not None
        assert doc.act.id == "TEST_ACT"
        assert doc.act.year == 2000

    def test_parses_sections(self):
        parser = JSONLegalParser()
        doc = parser.parse(SAMPLE)
        assert len(doc.sections) == 2
        assert doc.sections[0].number == "1"
        assert len(doc.sections[1].subsections) == 1

    def test_parses_amendments_and_actions(self):
        parser = JSONLegalParser()
        doc = parser.parse(SAMPLE)
        assert len(doc.amendments) == 1
        assert len(doc.amendment_actions) == 1
        assert doc.amendment_actions[0].amendment_type == AmendmentType.SUBSTITUTION

    def test_parses_rules(self):
        parser = JSONLegalParser()
        doc = parser.parse(SAMPLE)
        assert len(doc.rules) == 1
        assert doc.rules[0].section_id == "TEST_ACT_S1"

    def test_parses_cross_references(self):
        parser = JSONLegalParser()
        doc = parser.parse(SAMPLE)
        assert len(doc.cross_references) == 1
        assert doc.cross_references[0].context == "as defined in"

    def test_raises_parse_error_on_invalid_json_path(self):
        parser = JSONLegalParser()
        with pytest.raises(ParseError):
            parser.parse("/non/existent/file.json")

    def test_section_effective_content_equals_original_on_parse(self):
        parser = JSONLegalParser()
        doc = parser.parse(SAMPLE)
        for section in doc.sections:
            assert section.effective_content == section.original_content


# -----------------------------------------
# Exception Tests
# -----------------------------------------

class TestExceptions:
    def test_node_not_found_has_details(self):
        exc = NodeNotFoundError("Section", "5")
        assert exc.details["node_type"] == "Section"
        assert exc.details["identifier"] == "5"
        assert "Section" in str(exc)

    def test_parse_error_is_ingestion_error(self):
        from src.core.exceptions import IngestionError
        exc = ParseError("bad format")
        assert isinstance(exc, IngestionError)

    def test_cypher_execution_error_has_query(self):
        from src.core.exceptions import CypherExecutionError
        exc = CypherExecutionError("MATCH (n) RETURN n", "syntax error")
        assert exc.details["query"] == "MATCH (n) RETURN n"


# -----------------------------------------
# Repository Tests (mocked driver)
# -----------------------------------------

class TestSectionRepository:
    def _make_repo(self, query_results=None):
        from src.graph.repositories import SectionRepository
        driver = MagicMock()
        driver.execute_query.return_value = query_results or []
        driver.execute_write.return_value = []
        return SectionRepository(driver)

    def test_find_by_id_raises_when_not_found(self):
        repo = self._make_repo(query_results=[])
        with pytest.raises(NodeNotFoundError):
            repo.find_by_id("nonexistent")

    def test_find_by_id_returns_node_data(self):
        repo = self._make_repo(query_results=[{"s": {"id": "S1", "number": "1"}}])
        result = repo.find_by_id("S1")
        assert result["id"] == "S1"

    def test_upsert_calls_write(self):
        repo = self._make_repo()
        section = Section(
            id="S1", number="1",
            original_content="text",
            effective_content="text",
            act_id="ACT1"
        )
        repo.upsert(section)
        repo._driver.execute_write.assert_called_once()

    def test_update_effective_content_calls_write(self):
        repo = self._make_repo()
        repo.update_effective_content("S1", "New text")
        repo._driver.execute_write.assert_called_once()


class TestActRepository:
    def _make_repo(self, query_results=None):
        from src.graph.repositories import ActRepository
        driver = MagicMock()
        driver.execute_query.return_value = query_results or []
        driver.execute_write.return_value = []
        return ActRepository(driver)

    def test_find_by_id_raises_when_not_found(self):
        repo = self._make_repo(query_results=[])
        with pytest.raises(NodeNotFoundError):
            repo.find_by_id("MISSING")

    def test_find_all_returns_list(self):
        repo = self._make_repo(query_results=[{"a": {"id": "A1"}}, {"a": {"id": "A2"}}])
        results = repo.find_all()
        assert len(results) == 2


# -----------------------------------------
# Parser Registry Tests
# -----------------------------------------

class TestParserRegistry:
    def test_registry_finds_json_parser_for_dict(self):
        from src.ingestion.parsers import build_default_registry
        registry = build_default_registry()
        parser = registry.get_parser({"act": {}})
        assert isinstance(parser, JSONLegalParser)

    def test_registry_raises_for_unknown_source(self):
        from src.ingestion.parsers import build_default_registry
        registry = build_default_registry()
        with pytest.raises(ParseError):
            registry.get_parser(12345)
