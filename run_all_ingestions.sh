#!/bin/bash
# run_all_ingestions.sh
# Runs the full PDF ingestion pipeline for all three acts.
# Output is saved to ingestion.log

echo "Starting ingestion pipeline at $(date)" > ingestion.log

echo "1. Ingesting Companies Rules, 2014..." | tee -a ingestion.log
venv/bin/python scripts/ingest_pdf.py \
  --file "Companies Rules, 2014.pdf" \
  --act-id CR_2014 --title "Companies Rules, 2014" --year 2014 --number 1 \
  --short-title "CR" \
  >> ingestion.log 2>&1

echo "2. Ingesting Corporate Laws (Amendment) Act, 2026..." | tee -a ingestion.log
venv/bin/python scripts/ingest_pdf.py \
  --file "Corporate Laws (Amendment) Act, 2026.pdf" \
  --act-id CLAA_2026 --title "Corporate Laws (Amendment) Act, 2026" --year 2026 --number 1 \
  --short-title "CLAA" \
  >> ingestion.log 2>&1

echo "3. Ingesting Companies Act, 2013... (This one is huge and will take a while)" | tee -a ingestion.log
venv/bin/python scripts/ingest_pdf.py \
  --file "Companies Act, 2013.pdf" \
  --act-id CA_2013 --title "Companies Act, 2013" --year 2013 --number 18 \
  --short-title "CA" \
  >> ingestion.log 2>&1

echo "All ingestions finished at $(date)" >> ingestion.log
