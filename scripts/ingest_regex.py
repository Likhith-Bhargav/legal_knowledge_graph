#!/usr/bin/env python3
import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / "config" / ".env")

from src.core.config import get_settings
from src.graph.driver import Neo4jDriver
from src.ingestion.regex_parser import RegexLegalParser
from src.ingestion.graph_ingestion import GraphIngestionService

logger = logging.getLogger(__name__)

def run_ingestion(
    pdf_path: Path,
    act_id: str,
    title: str,
    year: int,
    number: str,
    short_title: str | None,
    dry_run: bool
):
    logger.info(f"PDF: {pdf_path}")
    logger.info("Using REGEX Parser (No LLM)")

    parser = RegexLegalParser(
        act_id=act_id,
        title=title,
        year=year,
        number=number,
        short_title=short_title
    )
    
    # 1. Parse PDF using fast Regex rules
    doc = parser.parse(pdf_path)
    
    num_secs = len(doc.sections)
    if dry_run:
        print("\n" + "─"*60)
        print(f"DRY RUN — would ingest {num_secs} sections using Regex rules")
        print("─"*60)
        for s in doc.sections[:5]:
            title_trunc = (s.title[:45] + '...') if len(s.title) > 45 else s.title
            print(f"  Section {s.number}: {title_trunc}")
        if num_secs > 5:
            print("  ...")
        print("─"*60)
        print("(No data was written to Neo4j)")
        return

    logger.info(f"Ingesting into DB... ({num_secs} sections)")
    
    settings = get_settings()
    driver = Neo4jDriver(settings.neo4j)
    driver.connect()

    service = GraphIngestionService(driver)
    service.initialize_schema()

    try:
        service._write_to_graph(doc)
    finally:
        driver.close()
        
    logger.info("Ingestion complete!")

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    
    parser = argparse.ArgumentParser(description="Ingest PDF using pure Regex rules (No LLM)")
    parser.add_argument("--file", required=True, help="Path to the PDF file")
    parser.add_argument("--act-id", required=True, help="Unique Act ID (e.g. CA_2013)")
    parser.add_argument("--title", required=True, help="Full Act title")
    parser.add_argument("--year", type=int, required=True, help="Act year")
    parser.add_argument("--number", required=True, help="Act number")
    parser.add_argument("--short-title", help="Optional short code (e.g. CA)")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, do not write to DB")
    
    args = parser.parse_args()
    
    if not Path(args.file).exists():
        logger.error(f"File not found: {args.file}")
        exit(1)
        
    run_ingestion(
        pdf_path=Path(args.file),
        act_id=args.act_id,
        title=args.title,
        year=args.year,
        number=args.number,
        short_title=args.short_title,
        dry_run=args.dry_run
    )

if __name__ == "__main__":
    main()
