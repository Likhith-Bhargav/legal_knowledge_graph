#!/usr/bin/env python3
"""
PDF → Knowledge Graph Ingestion CLI.

Extracts sections from a legal PDF and loads them into Neo4j using
the configured LLM provider (Gemini / OpenAI / Anthropic).

Usage
-----
  # Companies Act
  python scripts/ingest_pdf.py \\
      --file "Companies Act, 2013.pdf" \\
      --act-id CA_2013 \\
      --title "Companies Act, 2013" \\
      --year 2013 \\
      --number 18

  # Companies Rules
  python scripts/ingest_pdf.py \\
      --file "Companies Rules, 2014.pdf" \\
      --act-id CR_2014 \\
      --title "Companies Rules, 2014" \\
      --year 2014 \\
      --number 1

  # Corporate Laws Amendment
  python scripts/ingest_pdf.py \\
      --file "Corporate Laws (Amendment) Act, 2026.pdf" \\
      --act-id CLAA_2026 \\
      --title "Corporate Laws (Amendment) Act, 2026" \\
      --year 2026 \\
      --number 1

Options
-------
  --file        Path to the PDF (relative to legal-kg/ or absolute)
  --act-id      Unique ID for this Act in the graph (e.g. CA_2013)
  --title       Full title of the Act
  --year        Year of enactment
  --number      Act number (optional, defaults to '1')
  --short-title Short title / abbreviation (optional)
  --dry-run     Extract and print JSON without writing to Neo4j
  --max-pages   Only process this many pages (useful for testing)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Make sure the src package is importable when running from the legal-kg dir
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Load .env before importing settings
from dotenv import load_dotenv
load_dotenv(ROOT / "config" / ".env")

from src.core.config import get_settings
from src.core.exceptions import LLMProviderError
from src.graph.driver import Neo4jDriver
from src.ingestion.graph_ingestion import GraphIngestionService
from src.ingestion.pdf_parser import PDFLegalParser
from src.intelligence.query_engine import build_llm_provider

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("ingest_pdf")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Ingest a legal PDF into the Knowledge Graph.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--file",        required=True,  help="Path to the PDF file")
    p.add_argument("--act-id",      required=True,  help="Unique Act ID, e.g. CA_2013")
    p.add_argument("--title",       required=True,  help="Full Act title")
    p.add_argument("--year",        required=True,  type=int, help="Year of the Act")
    p.add_argument("--number",      default="1",    help="Act number (default: 1)")
    p.add_argument("--short-title", default=None,   help="Short title / abbreviation")
    p.add_argument("--dry-run",     action="store_true",
                   help="Extract JSON but do NOT write to Neo4j")
    p.add_argument("--max-pages",   type=int, default=None,
                   help="Only process first N pages (for quick testing)")
    return p


def resolve_pdf_path(file_arg: str) -> Path:
    """Resolve PDF path — checks relative to CWD and to legal-kg root."""
    candidates = [
        Path(file_arg),
        ROOT / file_arg,
        ROOT / "data" / file_arg,
    ]
    for c in candidates:
        if c.exists():
            return c.resolve()
    raise FileNotFoundError(
        f"PDF not found: '{file_arg}'\n"
        f"Searched: {', '.join(str(c) for c in candidates)}"
    )


def main() -> None:
    args = build_parser().parse_args()

    # ── Resolve PDF path ──────────────────────────────────────────────────────
    try:
        pdf_path = resolve_pdf_path(args.file)
    except FileNotFoundError as e:
        logger.error(str(e))
        sys.exit(1)

    logger.info("PDF: %s", pdf_path)

    # ── Load settings & build LLM ─────────────────────────────────────────────
    settings = get_settings()
    logger.info("LLM provider: %s / %s", settings.llm.provider, settings.llm.model)

    try:
        llm = build_llm_provider(settings.llm)
    except LLMProviderError as e:
        logger.error("Failed to initialise LLM provider: %s", e)
        logger.error("Check LLM_PROVIDER and API key settings in config/.env")
        sys.exit(1)

    # ── Act metadata ──────────────────────────────────────────────────────────
    act_meta = {
        "id":          args.act_id,
        "title":       args.title,
        "year":        args.year,
        "number":      args.number,
        "short_title": args.short_title,
        "description": f"{args.title} — ingested from PDF",
    }

    # ── Optional: patch max pages ─────────────────────────────────────────────
    if args.max_pages:
        _patch_max_pages(args.max_pages)

    # ── Extract ───────────────────────────────────────────────────────────────
    logger.info("Starting PDF extraction for Act ID: %s", args.act_id)
    parser = PDFLegalParser(llm_provider=llm, act_meta=act_meta)

    try:
        doc = parser.parse(pdf_path)
    except Exception as e:
        logger.error("Extraction failed: %s", e)
        sys.exit(1)

    logger.info(
        "Extraction complete: %d sections, %d cross-refs",
        len(doc.sections),
        len(doc.cross_references),
    )

    # ── Dry-run: just print ───────────────────────────────────────────────────
    if args.dry_run:
        print("\n" + "─" * 60)
        print(f"DRY RUN — would ingest {len(doc.sections)} sections")
        print("─" * 60)
        for s in doc.sections[:10]:
            print(f"  §{s.number}  {s.title or '(no title)'}")
            print(f"     {(s.original_content or '')[:120]}…")
        if len(doc.sections) > 10:
            print(f"  … and {len(doc.sections) - 10} more sections")
        print("─" * 60)
        print("Cross-references:", len(doc.cross_references))
        print("(No data was written to Neo4j)")
        return

    # ── Ingest into Neo4j ─────────────────────────────────────────────────────
    logger.info("Connecting to Neo4j at %s …", settings.neo4j.uri)
    driver = Neo4jDriver(settings.neo4j)
    driver.connect()

    service = GraphIngestionService(driver)
    service.initialize_schema()

    try:
        result = service._write_to_graph(doc)
    finally:
        driver.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print(f"  ✅  Ingestion Complete: {result.act_id}")
    print("═" * 60)
    print(f"  Sections ingested:     {result.sections_ingested}")
    print(f"  Amendments ingested:   {result.amendments_ingested}")
    print(f"  Rules ingested:        {result.rules_ingested}")
    print(f"  Cross-refs ingested:   {result.cross_references_ingested}")
    if result.warnings:
        print(f"\n  ⚠  Warnings ({len(result.warnings)}):")
        for w in result.warnings[:5]:
            print(f"     • {w}")
    print("\n  Neo4j Browser: http://localhost:7474")
    print( "  API docs:      http://localhost:8000/docs")
    print("═" * 60 + "\n")


def _patch_max_pages(max_pages: int) -> None:
    """Monkey-patch PDFLegalParser._extract_text to cap page count."""
    from src.ingestion import pdf_parser as pm
    _orig = pm.PDFLegalParser._extract_text

    def _capped(self, path):
        try:
            import pdfplumber
        except ImportError:
            raise pm.ParseError("pdfplumber not installed.")
        pages_text = []
        with pdfplumber.open(path) as pdf:
            pages = pdf.pages[:max_pages]
            logger.info("(max-pages=%d) Processing %d of %d pages", max_pages, len(pages), len(pdf.pages))
            for page in pages:
                text = page.extract_text()
                if text:
                    pages_text.append(text)
        return "\n\n".join(pages_text)

    pm.PDFLegalParser._extract_text = _capped


if __name__ == "__main__":
    main()
