#!/usr/bin/env python3
"""Semantic search over the indexed vault.

Usage:
    vault_ask.py "what cap rate did we assume for wheaton"
    vault_ask.py "who owns what" --top 8
    vault_ask.py "lap of luxury lease terms" --format json

Requires vault_index.py to have run first.
"""

import argparse
import json
import pathlib
import sys

CHROMA_DIR = pathlib.Path.home() / ".local" / "eve-tools" / "vault-chroma"
COLLECTION = "vault"


def main() -> int:
    ap = argparse.ArgumentParser(description="Semantic search over the vault.")
    ap.add_argument("query", help="Natural language question.")
    ap.add_argument("--top", type=int, default=5, help="Number of chunks to return (default 5).")
    ap.add_argument("--format", choices=["pretty", "json"], default="pretty")
    args = ap.parse_args()

    import chromadb
    from chromadb.utils import embedding_functions

    if not CHROMA_DIR.exists():
        sys.exit("error: no vault index found. Run vault_index.py first.")

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    embedder = embedding_functions.DefaultEmbeddingFunction()
    coll = client.get_or_create_collection(name=COLLECTION, embedding_function=embedder)

    if coll.count() == 0:
        sys.exit("error: vault index is empty. Run vault_index.py to populate.")

    res = coll.query(query_texts=[args.query], n_results=args.top)

    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]

    if args.format == "json":
        payload = [
            {"path": m.get("path"), "heading": m.get("heading"),
             "distance": d, "text": t}
            for m, d, t in zip(metas, dists, docs)
        ]
        print(json.dumps({"query": args.query, "results": payload},
                         ensure_ascii=False, indent=2))
        return 0

    print(f"# query: {args.query}")
    print(f"# hits: {len(docs)}")
    print()
    for i, (m, d, t) in enumerate(zip(metas, dists, docs), 1):
        path = m.get("path", "?")
        heading = m.get("heading") or "(no heading)"
        print(f"--- [{i}] {path}  —  {heading}  (dist {d:.3f}) ---")
        snippet = t if len(t) < 800 else t[:800] + "…"
        print(snippet)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
