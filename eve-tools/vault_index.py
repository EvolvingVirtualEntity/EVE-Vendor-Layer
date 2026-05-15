#!/usr/bin/env python3
"""Index the Obsidian vault into a local ChromaDB for semantic search.

Walks ~/EveBrain/**/*.md, splits each file into heading-based chunks (with size
safety for very long sections), and upserts embeddings into a persistent
Chroma collection at ~/.local/eve-tools/vault-chroma/.

Uses Chroma's built-in embedding function (onnxruntime + all-MiniLM-L6-v2 under
the hood — no torch required). First run downloads the ONNX model.

Usage:
    vault_index.py                  # incremental: only re-indexes changed files
    vault_index.py --rebuild        # drop the collection and re-index everything
    vault_index.py --status         # print the current collection stats
"""

import argparse
import hashlib
import pathlib
import re
import sys

VAULT = pathlib.Path.home() / "EveBrain"
CHROMA_DIR = pathlib.Path.home() / ".local" / "eve-tools" / "vault-chroma"
COLLECTION = "vault"

MAX_CHUNK_CHARS = 1500
HEADING_RE = re.compile(r"(?m)^#{1,6}\s+.+$")

# Dirs under the vault we never want to index
SKIP_DIRS = {".obsidian", ".git", "05-Archive"}


def file_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def chunk_markdown(path: pathlib.Path, text: str) -> list[dict]:
    """Split on top-level headings. If a section is too long, break on blank lines then hard-cap."""
    # Find heading positions.
    positions: list[int] = [m.start() for m in HEADING_RE.finditer(text)]
    if not positions or positions[0] > 0:
        positions = [0] + positions
    positions.append(len(text))

    chunks: list[dict] = []
    for i in range(len(positions) - 1):
        section = text[positions[i]:positions[i + 1]].strip()
        if not section:
            continue
        # Extract heading line if present
        heading = None
        first_line, _, rest = section.partition("\n")
        if HEADING_RE.match(first_line):
            heading = first_line.lstrip("# ").strip()
        # Hard-cap very long sections
        if len(section) <= MAX_CHUNK_CHARS:
            chunks.append({"text": section, "heading": heading})
        else:
            # Split at paragraph boundaries, greedily pack into MAX_CHUNK_CHARS windows
            parts = re.split(r"\n{2,}", section)
            buf = ""
            for part in parts:
                if len(buf) + len(part) + 2 > MAX_CHUNK_CHARS and buf:
                    chunks.append({"text": buf.strip(), "heading": heading})
                    buf = ""
                buf = (buf + "\n\n" + part).strip() if buf else part
            if buf:
                chunks.append({"text": buf.strip(), "heading": heading})
    return chunks


def iter_vault_files() -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for p in VAULT.rglob("*.md"):
        if any(part in SKIP_DIRS for part in p.relative_to(VAULT).parts):
            continue
        out.append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Index the vault into ChromaDB.")
    ap.add_argument("--rebuild", action="store_true", help="Drop and re-create the collection.")
    ap.add_argument("--status", action="store_true", help="Print stats and exit.")
    args = ap.parse_args()

    import chromadb
    from chromadb.utils import embedding_functions

    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    embedder = embedding_functions.DefaultEmbeddingFunction()

    if args.rebuild:
        try:
            client.delete_collection(COLLECTION)
            print(f"# dropped existing '{COLLECTION}' collection", file=sys.stderr)
        except Exception:
            pass

    coll = client.get_or_create_collection(name=COLLECTION, embedding_function=embedder)

    if args.status:
        print(f"# collection: {COLLECTION}")
        print(f"# chunks indexed: {coll.count()}")
        return 0

    files = iter_vault_files()
    print(f"# scanning {len(files)} markdown files…", file=sys.stderr)

    # Build doc-hash index of what's already in the collection so we can skip unchanged files
    existing_hashes: dict[str, str] = {}
    try:
        existing = coll.get(include=["metadatas"])
        for md in existing.get("metadatas") or []:
            if md and "path" in md and "file_hash" in md:
                existing_hashes[md["path"]] = md["file_hash"]
    except Exception:
        pass

    added = 0
    skipped = 0
    refreshed = 0

    for fp in files:
        text = fp.read_text(encoding="utf-8", errors="replace")
        h = file_hash(text)
        rel = str(fp.relative_to(VAULT))
        if existing_hashes.get(rel) == h and not args.rebuild:
            skipped += 1
            continue

        # Remove stale chunks from this file before re-adding
        try:
            coll.delete(where={"path": rel})
        except Exception:
            pass

        chunks = chunk_markdown(fp, text)
        if not chunks:
            continue

        ids = [f"{rel}::{i}" for i in range(len(chunks))]
        docs = [c["text"] for c in chunks]
        metas = [
            {"path": rel, "file_hash": h, "heading": c["heading"] or ""}
            for c in chunks
        ]
        coll.upsert(ids=ids, documents=docs, metadatas=metas)
        if existing_hashes.get(rel):
            refreshed += 1
        else:
            added += 1
        print(f"#   +{rel} ({len(chunks)} chunks)", file=sys.stderr)

    print(f"# done. added={added} refreshed={refreshed} skipped_unchanged={skipped}", file=sys.stderr)
    print(f"# total chunks in collection: {coll.count()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
