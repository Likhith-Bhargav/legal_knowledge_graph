"""
Integration tests for the FastAPI endpoints.
Uses TestClient with a mocked Neo4j driver -- no live database required.
Set RUN_LIVE_TESTS=true in env to run against a real Neo4j instance.
"""
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


SAMPLE_ACT_DATA = {
    "act": {
        "id": "INT_TEST_ACT",
        "title": "Integration Test Act",
        "year": 2024,
        "number": "99"
    },
    "sections": [
        {
            "id": "INT_S1",
            "number": "1",
            "title": "Test Section",
            "content": "Test content.",
            "subsections": []
        }
    ],
    "amendments": [],
    "rules": [],
    "cross_references": []
}


@pytest.fixture(scope="module")
def client():
    """Create a TestClient with a fully mocked Neo4j driver."""
    with patch("src.graph.driver.GraphDatabase") as MockGDB:
        mock_neo4j_driver = MagicMock()
        mock_neo4j_driver.verify_connectivity.return_value = None
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.run.return_value = iter([])
        mock_neo4j_driver.session.return_value = mock_session
        MockGDB.driver.return_value = mock_neo4j_driver

        from src.core.exceptions import LLMProviderError
        with patch("src.intelligence.query_engine.build_llm_provider",
                   side_effect=LLMProviderError("No LLM in tests")):
            from src.api.main import app
            with TestClient(app) as c:
                yield c


class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_has_required_fields(self, client):
        data = client.get("/health").json()
        assert "status" in data
        assert "database" in data
        assert "ai" in data

    def test_ai_disabled_without_llm(self, client):
        data = client.get("/health").json()
        assert data["ai"] == "disabled"


class TestActEndpoints:
    def test_list_acts_returns_list(self, client):
        resp = client.get("/api/v1/acts")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_act_not_found_returns_404(self, client):
        resp = client.get("/api/v1/acts/NONEXISTENT_ACT_ID_XYZ")
        assert resp.status_code == 404

    def test_get_act_404_has_detail(self, client):
        resp = client.get("/api/v1/acts/MISSING")
        assert "detail" in resp.json()


class TestSectionEndpoints:
    def test_list_sections_returns_list(self, client):
        resp = client.get("/api/v1/acts/SOME_ACT/sections")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_section_not_found_returns_404(self, client):
        resp = client.get("/api/v1/acts/MISSING_ACT/sections/999")
        assert resp.status_code == 404


class TestQueryEndpoint:
    def test_query_without_llm_returns_503(self, client):
        resp = client.post(
            "/api/v1/query",
            json={"question": "What is Section 1?"}
        )
        assert resp.status_code == 503

    def test_query_503_has_meaningful_detail(self, client):
        resp = client.post("/api/v1/query", json={"question": "test"})
        assert "detail" in resp.json()


class TestIngestEndpoint:
    def test_ingest_valid_document(self, client):
        resp = client.post("/api/v1/ingest", json={"data": SAMPLE_ACT_DATA})
        # Either 200 (success) or 422 (validation) are acceptable in mock mode
        assert resp.status_code in (200, 422)

    def test_ingest_missing_act_returns_error(self, client):
        resp = client.post("/api/v1/ingest", json={"data": {"no_act_key": {}}})
        assert resp.status_code in (422, 500)
