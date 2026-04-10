"""
FastAPI Application -- REST API layer.

Route structure:
  POST /api/v1/query                                   -- NL query (AI-powered)
  GET  /api/v1/acts                                    -- List all acts
  GET  /api/v1/acts/{id}                               -- Act detail
  GET  /api/v1/acts/{id}/sections                      -- All sections
  GET  /api/v1/acts/{id}/sections/{number}             -- Section detail
  GET  /api/v1/acts/{id}/sections/{number}/amendments  -- Section amendments
  GET  /api/v1/acts/{id}/sections/{number}/rules       -- Section rules
  GET  /api/v1/acts/{id}/amendments                    -- All amendments
  GET  /api/v1/acts/{id}/analytics/impact              -- Amendment impact
  POST /api/v1/ingest                                  -- Ingest a document
  GET  /health                                         -- Health check
"""
from __future__ import annotations
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.core.config import get_settings
from src.core.exceptions import NodeNotFoundError, LegalKGError, LLMProviderError
from src.graph.driver import Neo4jDriver
from src.services.legal_service import LegalService
from src.ingestion.graph_ingestion import GraphIngestionService
from src.intelligence.query_engine import LegalQueryIntelligence, build_llm_provider

logger = logging.getLogger(__name__)


# -----------------------------------------
# Application State
# -----------------------------------------

class AppState:
    driver: Neo4jDriver = None
    legal_service: LegalService = None
    ingestion_service: GraphIngestionService = None
    intelligence: LegalQueryIntelligence | None = None


app_state = AppState()


# -----------------------------------------
# Lifespan (startup / shutdown)
# -----------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info("Starting Legal Knowledge Graph API...")

    app_state.driver = Neo4jDriver(settings.neo4j)
    app_state.driver.connect()

    app_state.ingestion_service = GraphIngestionService(app_state.driver)
    app_state.ingestion_service.initialize_schema()

    app_state.legal_service = LegalService(app_state.driver)

    try:
        llm = build_llm_provider(settings.llm)
        app_state.intelligence = LegalQueryIntelligence(app_state.driver, llm)
        logger.info("LLM provider initialized: %s / %s", settings.llm.provider, settings.llm.model)
    except LLMProviderError as e:
        logger.warning("LLM not available: %s. AI queries will be disabled.", e)

    yield

    app_state.driver.close()
    logger.info("API shut down cleanly.")


# -----------------------------------------
# Request / Response Schemas
# -----------------------------------------

class NLQueryRequest(BaseModel):
    question: str
    act_id: str | None = None


class NLQueryResponse(BaseModel):
    question: str
    answer: str
    cypher: str
    result_count: int
    raw_results: list[dict]


class IngestRequest(BaseModel):
    data: dict


class IngestResponse(BaseModel):
    act_id: str
    sections_ingested: int
    amendments_ingested: int
    rules_ingested: int
    cross_references_ingested: int
    warnings: list[str]


# -----------------------------------------
# App Factory
# -----------------------------------------

def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app.app_name,
        version=settings.app.version,
        lifespan=lifespan,
        docs_url="/docs",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    _register_routes(app)
    return app


# -----------------------------------------
# Route Registration
# -----------------------------------------

def _register_routes(app: FastAPI) -> None:

    # -- Health --------------------------------------------------

    @app.get("/health", tags=["System"])
    def health():
        db_ok = app_state.driver.health_check() if app_state.driver else False
        return {
            "status": "ok" if db_ok else "degraded",
            "database": "connected" if db_ok else "disconnected",
            "ai": "enabled" if app_state.intelligence else "disabled",
        }

    # -- AI Query ------------------------------------------------

    @app.post("/api/v1/query", response_model=NLQueryResponse, tags=["Intelligence"])
    def nl_query(req: NLQueryRequest):
        if not app_state.intelligence:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AI query engine not available. Check LLM configuration.",
            )
        try:
            result = app_state.intelligence.query(req.question)
            return NLQueryResponse(
                question=result.question,
                answer=result.answer,
                cypher=result.cypher,
                result_count=result.result_count,
                raw_results=result.raw_results,
            )
        except LegalKGError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # -- Ingestion -----------------------------------------------

    @app.post("/api/v1/ingest", response_model=IngestResponse, tags=["Ingestion"])
    def ingest_document(req: IngestRequest):
        try:
            result = app_state.ingestion_service.ingest(req.data)
            return IngestResponse(
                act_id=result.act_id,
                sections_ingested=result.sections_ingested,
                amendments_ingested=result.amendments_ingested,
                rules_ingested=result.rules_ingested,
                cross_references_ingested=result.cross_references_ingested,
                warnings=result.warnings,
            )
        except LegalKGError as e:
            raise HTTPException(status_code=422, detail=str(e))

    # -- Acts ----------------------------------------------------

    @app.get("/api/v1/acts", tags=["Acts"])
    def list_acts():
        return app_state.legal_service.list_acts()

    @app.get("/api/v1/acts/{act_id}", tags=["Acts"])
    def get_act(act_id: str):
        try:
            return app_state.legal_service.get_act(act_id)
        except NodeNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # -- Sections ------------------------------------------------

    @app.get("/api/v1/acts/{act_id}/sections", tags=["Sections"])
    def list_sections(act_id: str):
        try:
            return app_state.legal_service.list_sections(act_id)
        except NodeNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.get("/api/v1/acts/{act_id}/sections/{section_number}", tags=["Sections"])
    def get_section(act_id: str, section_number: str):
        """Returns current text + amendment history + rules + cross-references."""
        try:
            detail = app_state.legal_service.get_section_detail(act_id, section_number)
            return {
                "section": detail.section,
                "amendments": detail.amendments,
                "rules": detail.rules,
                "cross_references": detail.cross_references,
            }
        except NodeNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.get("/api/v1/acts/{act_id}/sections/{section_number}/amendments", tags=["Sections"])
    def get_section_amendments(act_id: str, section_number: str):
        try:
            return app_state.legal_service.get_amendments_for_section(act_id, section_number)
        except NodeNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.get("/api/v1/acts/{act_id}/sections/{section_number}/rules", tags=["Sections"])
    def get_section_rules(act_id: str, section_number: str):
        try:
            return app_state.legal_service.get_rules_for_section(act_id, section_number)
        except NodeNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # -- Amendments ----------------------------------------------

    @app.get("/api/v1/acts/{act_id}/amendments", tags=["Amendments"])
    def list_amendments(act_id: str):
        return app_state.legal_service.list_amendments(act_id)

    # -- Analytics -----------------------------------------------

    @app.get("/api/v1/acts/{act_id}/analytics/impact", tags=["Analytics"])
    def section_impact(act_id: str):
        """Which sections have been most amended?"""
        return app_state.legal_service.get_section_impact_summary(act_id)


app = create_app()
