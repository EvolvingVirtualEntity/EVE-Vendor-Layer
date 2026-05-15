#!/usr/bin/env python3
"""RAG chat over the vault — semantic search + local LLM.

Combines vault_ask.py (Chroma retrieval) with ask_local.py (Ollama) to answer
natural-language questions grounded in the vault content.

Usage:
    vault_chat.py "what did we agree on for the Wheaton rent schedule?"
    vault_chat.py "summarize the Van Emmon deal in 5 bullets"
    vault_chat.py "what's outstanding on the Germany trip?" --top 8 --show-sources
"""

import argparse
import pathlib
import sys
import urllib.error
import urllib.request
import json

CHROMA_DIR = pathlib.Path.home() / ".local" / "eve-tools" / "vault-chroma"
COLLECTION = "vault"
OLLAMA = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen2.5:7b-instruct-q4_K_M"

SYSTEM_PROMPT = (
    "You are Eve, L&R's assistant. Answer the user's question using only the "
    "context chunks provided. If the context does not contain the answer, say "
    "so directly — do not invent facts. Cite the relevant file paths inline "
    "like [path/to/note.md]."
)


def retrieve(query: str, top_k: int) -> list[dict]:
    import chromadb
    from chromadb.utils import embedding_functions

    if not CHROMA_DIR.exists():
        sys.exit("error: no vault index. Run vault_index.py first.")
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    embedder = embedding_functions.DefaultEmbeddingFunction()
    coll = client.get_or_create_collection(name=COLLECTION, embedding_function=embedder)
    if coll.count() == 0:
        sys.exit("error: vault index empty. Run vault_index.py to populate.")
    res = coll.query(query_texts=[query], n_results=top_k)
    out = []
    for doc, meta, dist in zip(
        (res.get("documents") or [[]])[0],
        (res.get("metadatas") or [[]])[0],
        (res.get("distances") or [[]])[0],
    ):
        out.append({"path": meta.get("path", "?"),
                    "heading": meta.get("heading") or "",
                    "distance": dist, "text": doc})
    return out


def ask_llm(query: str, chunks: list[dict], model: str) -> int:
    context_blocks = []
    for i, c in enumerate(chunks, 1):
        context_blocks.append(f"### [{i}] {c['path']}  —  {c['heading']}\n{c['text']}")
    context = "\n\n".join(context_blocks)

    user_msg = (
        f"Context (vault chunks):\n\n{context}\n\n"
        f"Question: {query}\n\n"
        "Answer using only the context above. Cite files inline."
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "stream": True,
    }
    req = urllib.request.Request(
        f"{OLLAMA}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=3600)
    except urllib.error.URLError as e:
        sys.exit(f"error: cannot reach Ollama at {OLLAMA}: {e}")

    for raw in resp:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("message", {}).get("content"):
            sys.stdout.write(obj["message"]["content"])
            sys.stdout.flush()
        if obj.get("done"):
            break
    sys.stdout.write("\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="RAG chat over the vault.")
    ap.add_argument("query")
    ap.add_argument("--top", type=int, default=5, help="Chunks to retrieve (default 5).")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--show-sources", action="store_true", help="Print sources before the answer.")
    args = ap.parse_args()

    chunks = retrieve(args.query, args.top)

    if args.show_sources:
        print(f"# sources for: {args.query}", file=sys.stderr)
        for i, c in enumerate(chunks, 1):
            print(f"#  [{i}] {c['path']}  —  {c['heading']}  (dist {c['distance']:.3f})",
                  file=sys.stderr)
        print("", file=sys.stderr)

    return ask_llm(args.query, chunks, args.model)


if __name__ == "__main__":
    sys.exit(main())
