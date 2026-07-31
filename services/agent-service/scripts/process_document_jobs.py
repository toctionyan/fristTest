#!/usr/bin/env python3
from __future__ import annotations
import argparse
from app.services.document_service import DocumentService

parser = argparse.ArgumentParser(description="Process durable RAG document index jobs")
parser.add_argument("--limit", type=int, default=10)
args = parser.parse_args()
for row in DocumentService().process_pending_jobs(limit=args.limit):
    print(f"{row.get('job_id')} {row.get('state')} {row.get('doc_id') or ''}")
