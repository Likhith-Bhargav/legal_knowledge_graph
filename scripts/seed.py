#!/usr/bin/env python3
"""
Seed the graph with sample data from data/sample_act.json.
Useful for a fresh demo environment.

Usage:
    python scripts/seed.py
    python scripts/seed.py --reset   # wipe existing data first
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.config import get_settings
from src.graph.driver import Neo4jDriver
from src.ingestion.graph_ingestion import GraphIngestionService
from src.utils.logging import configure_logging


SAMPLE_FILE = Path(__file__).parent.parent / "data" / "sample_act.json"


def wipe_database(driver: Neo4jDriver) -> None:
    print("⚠️  Wiping all data from the database...")
    driver.execute_write("MATCH (n) DETACH DELETE n")
    print("   Done.")


def main():
    parser = argparse.ArgumentParser(description="Seed the Legal KG with sample data")
    parser.add_argument("--reset", action="store_true", help="Delete all existing data first")
    args = parser.parse_args()

    configure_logging("INFO")

    settings = get_settings()
    driver = Neo4jDriver(settings.neo4j)

    print(f"Connecting to Neo4j at {settings.neo4j.uri}...")
    driver.connect()

    if args.reset:
        wipe_database(driver)

    service = GraphIngestionService(driver)
    service.initialize_schema()

    print(f"Ingesting sample data from: {SAMPLE_FILE}")
    result = service.ingest(str(SAMPLE_FILE))

    print("\n✅ Seed Complete!")
    print(f"   Act:          {result.act_id}")
    print(f"   Sections:     {result.sections_ingested}")
    print(f"   Amendments:   {result.amendments_ingested}")
    print(f"   Rules:        {result.rules_ingested}")
    print(f"   Cross-refs:   {result.cross_references_ingested}")

    if result.warnings:
        print(f"\n⚠️  Warnings: {len(result.warnings)}")
        for w in result.warnings:
            print(f"   - {w}")

    driver.close()
    print("\nNeo4j Browser: http://localhost:7474")
    print("API docs:       http://localhost:8000/docs")


if __name__ == "__main__":
    main()
