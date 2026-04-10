#!/usr/bin/env python3
"""
CLI script to query the Legal Knowledge Graph.

Usage:
    # Natural language (AI-powered)
    python scripts/query.py --question "What is the current version of Section 375?"
    python scripts/query.py --question "Which amendments changed Section 354?"
    python scripts/query.py --question "Which rules apply under Section 300?"

    # Direct Cypher
    python scripts/query.py --cypher "MATCH (s:Section) RETURN s.number, s.title LIMIT 5"
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.config import get_settings
from src.graph.driver import Neo4jDriver
from src.intelligence.query_engine import LegalQueryIntelligence, build_llm_provider
from src.utils.logging import configure_logging


def main():
    parser = argparse.ArgumentParser(description="Query the Legal Knowledge Graph")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--question", help="Natural language question (uses AI)")
    group.add_argument("--cypher", help="Direct Cypher query")
    args = parser.parse_args()

    configure_logging("WARNING")

    settings = get_settings()
    driver = Neo4jDriver(settings.neo4j)
    driver.connect()

    try:
        if args.cypher:
            results = driver.execute_query(args.cypher)
            print(json.dumps(results, indent=2, default=str))
        else:
            llm = build_llm_provider(settings.llm)
            intel = LegalQueryIntelligence(driver, llm)
            result = intel.query(args.question)

            print("\n" + "=" * 60)
            print(f"Question: {result.question}")
            print("=" * 60)
            print(f"\nCypher Generated:\n{result.cypher}")
            print(f"\nResults ({result.result_count} records):")
            print(json.dumps(result.raw_results, indent=2, default=str))
            print(f"\nAnswer:\n{result.answer}")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
