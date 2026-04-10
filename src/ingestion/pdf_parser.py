"""
PDF Legal Document Parser.

Extracts text from PDF files and uses the configured LLM to structure
the content into the domain JSON schema, which is then ingested via
the existing GraphIngestionService pipeline.

Pipeline:
  PDF → pdfplumber (text) → section chunker → LLM (JSON extraction) → ParsedDocument

This parser implements the DocumentParser interface and plugs into the
existing ParserRegistry — no changes to the ingestion orchestrator needed.
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from src.ingestion.parsers import DocumentParser, ParsedDocument, JSONLegalParser
from src.models.domain import Act, Section, Amendment, AmendmentAction, Rule, CrossReference, AmendmentType
from src.core.exceptions import ParseError

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

# Max characters per chunk sent to the LLM.
# ~4000 chars ≈ ~1000 tokens, well within any model's context window.
CHUNK_SIZE = 4000
CHUNK_OVERLAP = 400  # characters of overlap between chunks to avoid cutting a section

# Regex patterns for detecting section / rule headings in Indian legal text
SECTION_PATTERNS = [
    re.compile(r"^\s*(\d+[A-Z]?(?:\.\d+)?)\.\s+([A-Z][^\n]{0,120})", re.MULTILINE),  # 12A. Title
    re.compile(r"^\s*Section\s+(\d+[A-Z]?)[\.\s—–-]+([A-Z][^\n]{0,120})", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^\s*Rule\s+(\d+[A-Z]?)[\.\s—–-]+([A-Z][^\n]{0,120})", re.MULTILINE | re.IGNORECASE),
]

LLM_EXTRACTION_SYSTEM_PROMPT = """\
You are a legal document parser. Given a chunk of text from an Indian legal Act or Rules,
extract ALL sections/rules you find and return ONLY valid JSON — no explanation, no markdown.

Return this exact JSON structure:
{
  "sections": [
    {
      "number": "string (e.g. '2', '12A', 'Rule 4')",
      "title": "string or null",
      "content": "string (full section text, preserve exact legal language)"
    }
  ],
  "cross_references": [
    {
      "source": "section number string",
      "target": "section number string",
      "context": "brief description of the reference"
    }
  ]
}

Rules:
- Output ONLY JSON. No preamble, no explanation.
- If no sections found in this chunk, return: {"sections": [], "cross_references": []}
- Keep content verbatim — do not paraphrase or summarise.
- Section numbers: '1', '2A', '12(1)(a)', 'Rule 3', etc.
- Extract cross-references only when one section explicitly cites another (e.g. "as defined in section 2(h)").
- Do not invent sections; only extract what is clearly present in the text.
"""


# ─────────────────────────────────────────────────────────────────────────────
# PDF Parser
# ─────────────────────────────────────────────────────────────────────────────

class PDFLegalParser(DocumentParser):
    """
    Parses a PDF legal document into a ParsedDocument via LLM-assisted extraction.

    The parser is constructed with act metadata (id, title, year, etc.) because
    this information is typically on a cover page that doesn't parse well,
    and it's more reliable to pass it explicitly from the CLI.

    Usage:
        parser = PDFLegalParser(llm_provider, act_meta={
            "id": "CA_2013",
            "title": "Companies Act, 2013",
            "year": 2013,
            "number": "18",
        })
        doc = parser.parse(Path("Companies Act, 2013.pdf"))
    """

    def __init__(self, llm_provider, act_meta: dict) -> None:
        self._llm = llm_provider
        self._act_meta = act_meta
        self._json_parser = JSONLegalParser()

    def can_parse(self, source) -> bool:
        if isinstance(source, (str, Path)):
            return Path(source).suffix.lower() == ".pdf"
        return False

    def parse(self, source) -> ParsedDocument:
        path = Path(source)
        if not path.exists():
            raise ParseError(f"PDF file not found: {path}")

        logger.info("Extracting text from PDF: %s", path.name)
        raw_text = self._extract_text(path)

        if not raw_text or len(raw_text.strip()) < 100:
            raise ParseError(
                f"Could not extract readable text from {path.name}. "
                "The PDF may be scanned/image-based and requires OCR."
            )

        logger.info("Extracted %d characters from PDF", len(raw_text))

        chunks = self._chunk_text(raw_text)
        logger.info("Split into %d chunks for LLM extraction", len(chunks))

        all_sections: list[dict] = []
        all_xrefs: list[dict] = []

        for i, chunk in enumerate(chunks):
            logger.info("Processing chunk %d/%d …", i + 1, len(chunks))
            try:
                result = self._llm_extract(chunk)
                all_sections.extend(result.get("sections", []))
                all_xrefs.extend(result.get("cross_references", []))
            except Exception as e:
                logger.warning("Chunk %d extraction failed: %s", i + 1, e)

        sections = self._deduplicate_sections(all_sections)
        logger.info("Extracted %d unique sections", len(sections))

        # Build the JSON structure that JSONLegalParser understands
        act_id = self._act_meta["id"]
        json_doc = self._build_json_doc(act_id, sections, all_xrefs)

        # Delegate to the existing JSON parser for graph model construction
        return self._json_parser.parse(json_doc)

    # ── Text Extraction ───────────────────────────────────────────────────────

    def _extract_text(self, path: Path) -> str:
        try:
            import pdfplumber
        except ImportError:
            raise ParseError(
                "pdfplumber not installed. Run: pip install pdfplumber"
            )

        pages_text = []
        with pdfplumber.open(path) as pdf:
            total = len(pdf.pages)
            logger.info("PDF has %d pages", total)
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages_text.append(text)

        return "\n\n".join(pages_text)

    # ── Chunking ──────────────────────────────────────────────────────────────

    def _chunk_text(self, text: str) -> list[str]:
        """
        Split text into overlapping chunks at section boundaries where possible.
        Tries to start each chunk at a section heading for cleaner LLM parsing.
        """
        # Find all section boundary positions
        boundary_positions = [0]
        for pattern in SECTION_PATTERNS:
            for m in pattern.finditer(text):
                boundary_positions.append(m.start())
        boundary_positions = sorted(set(boundary_positions))

        chunks: list[str] = []
        start = 0

        while start < len(text):
            end = start + CHUNK_SIZE

            if end >= len(text):
                chunks.append(text[start:])
                break

            # Try to break at the nearest section boundary before `end`
            good_break = end
            for pos in reversed(boundary_positions):
                if start < pos < end:
                    good_break = pos
                    break

            chunks.append(text[start:good_break])
            if good_break == end:
                # We reached chunk size without finding a natural boundary; overlap slightly
                start = end - CHUNK_OVERLAP
            else:
                # Neatly split at a section boundary; start the next chunk exactly there
                start = good_break

        return [c for c in chunks if c.strip()]

    # ── LLM Extraction ────────────────────────────────────────────────────────

    def _llm_extract(self, chunk: str, max_retries: int = 3) -> dict:
        """Send one text chunk to the LLM and parse the returned JSON. Includes retries."""
        for attempt in range(max_retries):
            try:
                # Add a 10-second delay between chunks if using Gemini to avoid free-tier 15RPM limit
                if getattr(self._llm, "_model_name", None) and "gemini" in self._llm._model_name:
                    logger.info("Sleeping 12s to respect Gemini API free-tier rate limits...")
                    time.sleep(12)

                raw = self._llm.complete(LLM_EXTRACTION_SYSTEM_PROMPT, chunk)
                raw = raw.strip()

                # Clean markdown fences
                for fence in ["```json", "```"]:
                    raw = raw.replace(fence, "")
                raw = raw.strip()

                # Handle model occasionally forgetting trailing quotes or brackets using regex fallback below
                try:
                    return json.loads(raw)
                except json.JSONDecodeError as e:
                    # Try to regex extract just the JSON object
                    match = re.search(r'\{[\s\S]*\}', raw)
                    if match:
                        try:
                            return json.loads(match.group())
                        except json.JSONDecodeError:
                            pass
                    logger.warning("Attempt %d: LLM returned invalid JSON: %s…", attempt + 1, raw[:200])
                    if attempt == max_retries - 1:
                        return {"sections": [], "cross_references": []}

            except Exception as e:
                error_str = str(e).lower()
                if "429" in error_str or "quota" in error_str or "rate limit" in error_str:
                    wait_time = 35 * (attempt + 1)
                    logger.warning("Attempt %d: Hit rate limit (429). Retrying in %ds...", attempt + 1, wait_time)
                    time.sleep(wait_time)
                else:
                    logger.error("Attempt %d: LLM call failed: %s", attempt + 1, e)
                    if attempt == max_retries - 1:
                        return {"sections": [], "cross_references": []}
                    time.sleep(5)
        
        return {"sections": [], "cross_references": []}

    # ── Post-processing ───────────────────────────────────────────────────────

    def _deduplicate_sections(self, sections: list[dict]) -> list[dict]:
        """Remove duplicate sections (same number). Keep the longest content."""
        seen: dict[str, dict] = {}
        for s in sections:
            num = str(s.get("number", "")).strip()
            if not num:
                continue
            existing = seen.get(num)
            if existing is None or len(s.get("content", "")) > len(existing.get("content", "")):
                seen[num] = s
        return list(seen.values())

    def _build_json_doc(
        self,
        act_id: str,
        sections: list[dict],
        xrefs: list[dict],
    ) -> dict:
        """Assemble a JSON dict matching the JSONLegalParser schema."""
        cross_references = []
        section_ids = {
            str(s.get("number", "")).strip(): f"{act_id}_S{str(s.get('number', '')).replace(' ', '_')}"
            for s in sections
        }

        for xr in xrefs:
            src_num = str(xr.get("source", "")).strip()
            tgt_num = str(xr.get("target", "")).strip()
            src_id = section_ids.get(src_num)
            tgt_id = section_ids.get(tgt_num)
            if src_id and tgt_id and src_id != tgt_id:
                cross_references.append({
                    "source_section_id": src_id,
                    "target_section_id": tgt_id,
                    "context": xr.get("context", "refers to"),
                })

        sections_json = []
        for i, s in enumerate(sections):
            num = str(s.get("number", "")).strip()
            sec_id = section_ids.get(num, f"{act_id}_S{uuid.uuid4().hex[:8]}")
            sections_json.append({
                "id": sec_id,
                "number": num,
                "title": s.get("title"),
                "content": s.get("content", ""),
                "order": i,
            })

        return {
            "act": self._act_meta,
            "sections": sections_json,
            "amendments": [],          # Amendments are in a separate PDF — handled separately
            "rules": [],               # Rules come from the Rules PDF
            "cross_references": cross_references,
        }
