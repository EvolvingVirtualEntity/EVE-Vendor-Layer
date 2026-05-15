#!/usr/bin/env python3
"""Extract text and structure from a PDF.

Usage:
    pdf_extract.py <file.pdf>                     # plain text to stdout
    pdf_extract.py <file.pdf> --format json       # per-page JSON
    pdf_extract.py <file.pdf> --tables            # extract tables as TSV
    pdf_extract.py <file.pdf> --engine pymupdf    # swap engine (default: pdfplumber)

Uses pdfplumber by default (better for layout + tables). pymupdf is faster and handles
weird PDFs better; swap in with --engine pymupdf.

Writes everything to stdout so the caller can pipe it (e.g. into a summarizer).
"""

import argparse
import json
import pathlib
import sys


def extract_pdfplumber(path: pathlib.Path, want_tables: bool) -> dict:
    import pdfplumber
    pages: list[dict] = []
    with pdfplumber.open(path) as pdf:
        meta = dict(pdf.metadata or {})
        for i, page in enumerate(pdf.pages, 1):
            entry = {
                "page": i,
                "width": float(page.width),
                "height": float(page.height),
                "text": page.extract_text() or "",
            }
            if want_tables:
                tables = page.extract_tables() or []
                entry["tables"] = tables
            pages.append(entry)
    return {"meta": meta, "page_count": len(pages), "pages": pages}


def extract_pymupdf(path: pathlib.Path, want_tables: bool) -> dict:
    import fitz  # pymupdf
    doc = fitz.open(path)
    meta = dict(doc.metadata or {})
    pages: list[dict] = []
    for i, page in enumerate(doc, 1):
        entry = {
            "page": i,
            "width": float(page.rect.width),
            "height": float(page.rect.height),
            "text": page.get_text("text"),
        }
        if want_tables:
            try:
                tables = page.find_tables()
                entry["tables"] = [t.extract() for t in tables]
            except Exception as e:
                entry["tables_error"] = str(e)
        pages.append(entry)
    return {"meta": meta, "page_count": len(pages), "pages": pages}


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract text + structure from a PDF.")
    ap.add_argument("pdf", type=pathlib.Path)
    ap.add_argument("--format", choices=["text", "json"], default="text")
    ap.add_argument("--engine", choices=["pdfplumber", "pymupdf"], default="pdfplumber")
    ap.add_argument("--tables", action="store_true", help="Also extract tables.")
    args = ap.parse_args()

    if not args.pdf.exists():
        sys.exit(f"error: {args.pdf} not found")

    extractor = extract_pdfplumber if args.engine == "pdfplumber" else extract_pymupdf
    result = extractor(args.pdf, args.tables)

    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"# file: {args.pdf.name}")
    print(f"# pages: {result['page_count']}")
    if result["meta"]:
        for k, v in result["meta"].items():
            if v:
                print(f"# meta.{k}: {v}")
    print()
    for p in result["pages"]:
        print(f"--- page {p['page']} ---")
        print(p["text"].rstrip())
        print()
        for ti, tbl in enumerate(p.get("tables") or []):
            print(f"--- page {p['page']} table {ti + 1} ---")
            for row in tbl:
                print("\t".join("" if c is None else str(c) for c in row))
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
