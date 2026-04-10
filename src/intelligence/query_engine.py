"""
Intelligence Layer -- Natural Language -> Cypher -> Grounded Answer

Pipeline:
  1. CypherGenerator:    NL query -> Cypher query (LLM with few-shot examples)
  2. Graph Execution:    Run Cypher against Neo4j
  3. ResponseGrounder:   graph results + original query -> structured response

Design decisions:
- LLM generates Cypher, NOT the final answer.
- Final answer is ALWAYS grounded in actual graph results.
- This prevents hallucination and ensures traceability.
- LLMProvider is an abstract interface -> swap OpenAI / Anthropic / Gemini freely.

Supported providers (set LLM_PROVIDER in .env):
  openai     -> OpenAI GPT models (gpt-4o, gpt-4-turbo, ...)
  anthropic  -> Anthropic Claude models (claude-sonnet-4-6, ...)
  gemini     -> Google Gemini models (gemini-1.5-pro, gemini-2.0-flash, ...)
"""
from __future__ import annotations
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from src.core.config import LLMSettings
from src.core.exceptions import QueryTranslationError, LLMProviderError

logger = logging.getLogger(__name__)


# -----------------------------------------
# LLM Provider Abstraction (ISP / DIP)
# -----------------------------------------

class LLMProvider(ABC):
    @abstractmethod
    def complete(self, system_prompt: str, user_message: str) -> str:
        """Return the model's text response."""
        ...


# -----------------------------------------
# OpenAI Provider
# -----------------------------------------

class OpenAIProvider(LLMProvider):
    def __init__(self, settings: LLMSettings) -> None:
        try:
            from openai import OpenAI
            self._client = OpenAI(api_key=settings.api_key)
            self._model = settings.model
            self._temperature = settings.temperature
            self._max_tokens = settings.max_tokens
        except ImportError:
            raise LLMProviderError(
                "openai package not installed. Run: pip install openai"
            )

    def complete(self, system_prompt: str, user_message: str) -> str:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            )
            return response.choices[0].message.content
        except Exception as e:
            raise LLMProviderError(f"OpenAI API error: {e}") from e


# -----------------------------------------
# Anthropic Provider
# -----------------------------------------

class AnthropicProvider(LLMProvider):
    """Claude models via the Anthropic SDK."""

    def __init__(self, settings: LLMSettings) -> None:
        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=settings.api_key)
            self._model = settings.model
            self._max_tokens = settings.max_tokens
        except ImportError:
            raise LLMProviderError(
                "anthropic package not installed. Run: pip install anthropic"
            )

    def complete(self, system_prompt: str, user_message: str) -> str:
        try:
            message = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            return message.content[0].text
        except Exception as e:
            raise LLMProviderError(f"Anthropic API error: {e}") from e


# -----------------------------------------
# Gemini Provider
# -----------------------------------------

class GeminiProvider(LLMProvider):
    """
    Google Gemini models via the google-generativeai SDK.

    Gemini does not have a separate "system" role in the same way as
    OpenAI/Anthropic. We prepend the system prompt as the first user
    turn followed by a model acknowledgement — this is the recommended
    few-shot pattern for Gemini.

    Recommended models:
      gemini-2.0-flash          (fast, cheap, great for Cypher generation)
      gemini-1.5-pro            (more capable, higher context window)
      gemini-1.5-flash          (balanced)
    """

    def __init__(self, settings: LLMSettings) -> None:
        try:
            import google.generativeai as genai
            genai.configure(api_key=settings.gemini_api_key)
            self._genai = genai
            self._model_name = settings.model
            self._temperature = settings.temperature
            self._max_tokens = settings.max_tokens
        except ImportError:
            raise LLMProviderError(
                "google-generativeai package not installed. "
                "Run: pip install google-generativeai"
            )

    def complete(self, system_prompt: str, user_message: str) -> str:
        try:
            generation_config = self._genai.GenerationConfig(
                temperature=self._temperature,
                max_output_tokens=self._max_tokens,
            )
            model = self._genai.GenerativeModel(
                model_name=self._model_name,
                generation_config=generation_config,
                # Gemini supports system_instruction natively in newer SDK versions
                system_instruction=system_prompt,
            )
            response = model.generate_content(user_message)
            return response.text
        except Exception as e:
            raise LLMProviderError(f"Gemini API error: {e}") from e


# -----------------------------------------
# Ollama Provider (Local LLMs)
# -----------------------------------------

class OllamaProvider(LLMProvider):
    """
    Local models via Ollama API (e.g., gemma, llama3, mistral).
    """

    def __init__(self, settings: LLMSettings) -> None:
        try:
            import httpx
            self._client = httpx.Client(
                base_url=settings.ollama_base_url,
                timeout=120.0
            )
            self._model = settings.model
            self._temperature = settings.temperature
        except ImportError:
            raise LLMProviderError(
                "httpx package not installed. Run: pip install httpx"
            )

    def complete(self, system_prompt: str, user_message: str) -> str:
        try:
            payload = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "stream": False,
                "options": {
                    "temperature": self._temperature,
                }
            }
            res = self._client.post("/api/chat", json=payload)
            res.raise_for_status()
            return res.json()["message"]["content"]
        except Exception as e:
            raise LLMProviderError(f"Ollama API error: {e}") from e


# -----------------------------------------
# Provider Registry / Factory
# -----------------------------------------

_PROVIDER_REGISTRY: dict[str, type[LLMProvider]] = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "ollama": OllamaProvider,
}


def register_provider(name: str, cls: type[LLMProvider]) -> None:
    """
    Register a custom LLM provider at runtime.
    Allows third-party extensions without modifying this file.

    Example:
        from src.intelligence.query_engine import register_provider
        register_provider("my_llm", MyCustomProvider)
    """
    _PROVIDER_REGISTRY[name] = cls


def build_llm_provider(settings: LLMSettings) -> LLMProvider:
    """
    Instantiate the correct LLMProvider based on LLM_PROVIDER env var.
    Raises LLMProviderError if the provider name is unknown.
    """
    cls = _PROVIDER_REGISTRY.get(settings.provider)
    if not cls:
        available = ", ".join(_PROVIDER_REGISTRY.keys())
        raise LLMProviderError(
            f"Unknown LLM provider: '{settings.provider}'. "
            f"Available providers: {available}"
        )
    logger.info("Building LLM provider: %s (%s)", settings.provider, settings.model)
    return cls(settings)


# -----------------------------------------
# Few-Shot Prompts
# -----------------------------------------

CYPHER_SYSTEM_PROMPT = """
You are an expert Neo4j Cypher query generator for a Legal Knowledge Graph.

Graph Schema:
- Nodes: Act, Section, Subsection, Clause, Amendment, Rule
- Key Properties:
  - Section: id, number, title, original_content, effective_content, act_id
  - Subsection: id, number, content, section_id
  - Clause: id, identifier, content, subsection_id, section_id
  - Amendment: id, number, year, title, effective_date
  - Rule: id, number, title, content
- Relationships:
  - (Act)-[:HAS_SECTION]->(Section)
  - (Section)-[:HAS_SUBSECTION]->(Subsection)
  - (Subsection)-[:HAS_CLAUSE]->(Clause)
  - (Section)-[:AMENDED_BY]->(Amendment)
  - (Amendment)-[:SUBSTITUTES|INSERTS|DELETES]->(Section)
  - (Section)-[:DERIVED_RULE]->(Rule)
  - (Section)-[:REFERS_TO]->(Section)
  - (Rule)-[:UNDER_ACT]->(Act)

Rules:
1. Always return ONLY valid Cypher -- no explanation, no markdown, no backticks.
2. Use parameterized queries when entity identifiers are involved.
3. Return meaningful aliases (e.g., RETURN s.number AS section_number, s.effective_content AS current_text).
4. For "current version" queries, use s.effective_content (not s.original_content).
5. For hierarchy, traverse HAS_SUBSECTION and HAS_CLAUSE.
6. For amendment history, traverse AMENDED_BY relationships.

Few-shot examples:
Q: What is the current version of Section 5?
A: MATCH (s:Section {number: '5'}) RETURN s.number AS section_number, s.title AS title, s.effective_content AS current_text

Q: Show me subsection (2) of Section 12
A: MATCH (s:Section {number: '12'})-[:HAS_SUBSECTION]->(sub:Subsection {number: '2'}) RETURN sub.content AS content

Q: What are the clauses in Section 7, subsection (1)?
A: MATCH (s:Section {number: '7'})-[:HAS_SUBSECTION]->(sub:Subsection {number: '1'})-[:HAS_CLAUSE]->(c:Clause) RETURN c.identifier AS clause_id, c.content AS content

Q: What amendments have affected Section 3?
A: MATCH (s:Section {number: '3'})-[r:AMENDED_BY]->(a:Amendment) RETURN a.number AS amendment_number, a.year AS year, a.title AS title, r.type AS amendment_type ORDER BY a.year

Q: Which rules apply under Section 12?
A: MATCH (s:Section {number: '12'})-[:DERIVED_RULE]->(r:Rule) RETURN r.number AS rule_number, r.title AS title, r.content AS content

Q: What sections does Section 4 refer to?
A: MATCH (s:Section {number: '4'})-[r:REFERS_TO]->(t:Section) RETURN t.number AS referred_section, t.title AS title, r.context AS context

Q: Show me all sections in the Act
A: MATCH (a:Act)-[:HAS_SECTION]->(s:Section) RETURN s.number AS section_number, s.title AS title ORDER BY s.order

Q: What is the full history of amendments to the Act?
A: MATCH (s:Section)-[:AMENDED_BY]->(a:Amendment) RETURN a.number AS amendment, a.year AS year, a.title AS title, collect(s.number) AS affected_sections ORDER BY a.year
"""

GROUNDING_SYSTEM_PROMPT = """
You are a legal assistant that explains graph query results in clear, structured language.

Rules:
1. Base your answer ONLY on the provided graph results -- do not add information not in the results.
2. If the results are empty, say so clearly.
3. Structure your response with:
   - A direct answer to the question
   - The supporting evidence (from graph results)
   - A traceability note (what was queried)
4. Be concise and precise -- this is a legal context.
5. Never speculate or add information not present in the graph results.
"""


# -----------------------------------------
# Result Type
# -----------------------------------------

@dataclass
class QueryResult:
    question: str
    cypher: str
    raw_results: list[dict]
    answer: str
    result_count: int


# -----------------------------------------
# Main Intelligence Service
# -----------------------------------------

class LegalQueryIntelligence:
    """
    Orchestrates the full NL -> Cypher -> Answer pipeline.

    Usage:
        intel = LegalQueryIntelligence(driver, llm_provider)
        result = intel.query("What amendments affected Section 5?")
    """

    def __init__(self, driver, llm_provider: LLMProvider) -> None:
        self._driver = driver
        self._llm = llm_provider

    def query(self, natural_language_question: str) -> QueryResult:
        """Full pipeline: NL -> Cypher -> Execute -> Ground -> Answer"""
        logger.info("Processing query: %s", natural_language_question)

        # Step 1: Generate Cypher via LLM
        cypher = self._generate_cypher(natural_language_question)
        logger.debug("Generated Cypher: %s", cypher)

        # Step 2: Execute against the graph
        raw_results = self._execute_cypher(cypher)
        logger.debug("Graph returned %d results", len(raw_results))

        # Step 3: Ground the answer in real graph results
        answer = self._ground_answer(natural_language_question, cypher, raw_results)

        return QueryResult(
            question=natural_language_question,
            cypher=cypher,
            raw_results=raw_results,
            answer=answer,
            result_count=len(raw_results),
        )

    def _generate_cypher(self, question: str) -> str:
        try:
            cypher = self._llm.complete(CYPHER_SYSTEM_PROMPT, question)
            cypher = cypher.strip()
            # Strip markdown fences some models add
            for fence in ["```cypher", "```", "`"]:
                cypher = cypher.replace(fence, "")
            return cypher.strip()
        except Exception as e:
            raise QueryTranslationError(question, str(e)) from e

    def _execute_cypher(self, cypher: str) -> list[dict]:
        from src.core.exceptions import CypherExecutionError
        try:
            return self._driver.execute_query(cypher)
        except CypherExecutionError as e:
            logger.error("Cypher execution failed: %s", e)
            return []

    def _ground_answer(self, question: str, cypher: str, results: list[dict]) -> str:
        results_text = json.dumps(results, indent=2, default=str)
        user_message = f"""
Question: {question}

Cypher Query Used:
{cypher}

Graph Results:
{results_text}

Please provide a structured answer based ONLY on the above results.
"""
        return self._llm.complete(GROUNDING_SYSTEM_PROMPT, user_message)
