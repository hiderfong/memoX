#!/usr/bin/env python3
"""Audit and repair MemoX Chroma/BM25/manifest index consistency."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import default_config_path  # noqa: E402
from src.ops.index_consistency import audit_indexes, build_runtime, repair_indexes  # noqa: E402


def _print_human(result: dict, *, repaired: bool) -> None:
    report = result["after"] if repaired else result
    print(f"Status: {report['status']}")
    print(
        "Summary: "
        f"Chroma docs={report['summary']['chroma_documents']}, "
        f"Chroma chunks={report['summary']['chroma_chunks']}, "
        f"BM25 chunks={report['summary']['bm25_chunks']}, "
        f"manifest entries={report['summary']['manifest_entries']}"
    )
    if repaired:
        actions = result.get("repair_actions", [])
        if actions:
            print("Repairs:")
            for action in actions:
                print(f"- {action}")
        else:
            print("Repairs: none")
    if report.get("issue_counts"):
        print("Issue counts:")
        for code, count in report["issue_counts"].items():
            print(f"- {code}: {count}")
    issues = report.get("issues", [])
    if not issues:
        print("Issues: none")
        return
    print("Issues:")
    for issue in issues:
        print(f"- [{issue['severity']}] {issue['code']}: {issue['message']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(default_config_path()), help="Path to config.yaml")
    parser.add_argument("--collection", default="documents", help="Chroma collection name")
    parser.add_argument("--repair", action="store_true", help="Rebuild BM25 and remove stale manifest entries")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    vector_store, bm25_indexer, manifest_path = build_runtime(Path(args.config))
    if args.repair:
        result = repair_indexes(
            vector_store=vector_store,
            bm25_indexer=bm25_indexer,
            manifest_path=manifest_path,
            collection_name=args.collection,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            _print_human(result, repaired=True)
        return 0 if result["ok"] else 1

    result = audit_indexes(
        vector_store=vector_store,
        bm25_indexer=bm25_indexer,
        manifest_path=manifest_path,
        collection_name=args.collection,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_human(result, repaired=False)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
