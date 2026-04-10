#!/usr/bin/env python3
"""
CLI script to ingest a legal document JSON into Neo4j.

Usage:
    python scripts/ingest.py --file data/sample_act.json
    python scripts/ingest.py --file my_act.json
"""
import argparse
import sys
from pathlib import Path

# Make src importable from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.config import get_settings
from src.graph.driver import Neo4jDriver
from src.ingestion.graph_ingestion import GraphIngestionService
from src.utils.logging import configure_logging


def main():
    parser = argparse.ArgumentParser(
        description="Ingest a legal document into the Knowledge Graph"
    )
    parser.add_argument("--file", required=True, help="Path to JSON document")
    args = parser.parse_args()

    configure_logging("INFO")

    settings = get_settings()
    driver = Neo4jDriver(settings.neo4j)

    print(f"Connecting to Neo4j at {settings.neo4j.uri}...")
    driver.connect()

    service = GraphIngestionService(driver)

    print("Initializing schema...")
    service.initialize_schema()

    print(f"Ingesting: {args.file}")
    result = service.ingest(args.file)

    print("\n✅ Ingestion Complete!")
    print(f"   Act ID:              {result.act_id}")
    print(f"   Sections ingested:   {result.sections_ingested}")
    print(f"   Amendments ingested: {result.amendments_ingested}")
    print(f"   Rules ingested:      {result.rules_ingested}")
    print(f"   Cross-references:    {result.cross_references_ingested}")

    if result.warnings:
        print(f"\n⚠️  Warnings ({len(result.warnings)}):")
        for w in result.warnings:
            print(f"   - {w}")

    driver.close()


if __name__ == "__main__":
    main()
