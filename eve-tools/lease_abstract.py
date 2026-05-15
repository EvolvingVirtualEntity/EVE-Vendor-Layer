#!/usr/bin/env python3
"""Build a first-pass lease abstract from a PDF.

MVP scope (2026-04-21):
- Extract text via pdfplumber, OCR fallback if the PDF is scanned/image-only
- Apply regex/heuristics to pull common fields (parties, premises, term, rent, NNN, renewals, etc.)
- Write a structured Markdown abstract into the vault Lease-Abstracts folder
- Leave [TODO] placeholders for fields the heuristics couldn't find — Eve fills those manually
  by reading the extracted text at the bottom of the same file

Once a local LLM is in place (#8 Ollama), replace the heuristics with an LLM extraction pass.

Usage:
    lease_abstract.py <lease.pdf>
    lease_abstract.py <lease.pdf> --out ~/EveBrain/.../custom.md
    lease_abstract.py <lease.pdf> --lang eng+deu    # OCR language if scanned
"""

import argparse
import datetime as dt
import pathlib
import re
import subprocess
import sys

EVE_TOOLS = pathlib.Path.home() / ".local" / "eve-tools"
DOCS_PY = EVE_TOOLS / "docs-venv" / "bin" / "python"
PDF_EXTRACT = EVE_TOOLS / "pdf_extract.py"
OCR = EVE_TOOLS / "ocr.py"
DEFAULT_OUT_DIR = pathlib.Path.home() / "EveBrain" / "04-Resources" / "Lease-Abstracts"

# Threshold below which we assume the PDF is scanned/image-only and OCR is needed.
OCR_FALLBACK_CHAR_THRESHOLD = 200


def run(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        sys.exit(f"error: command failed ({cmd[0]}): exit {r.returncode}")
    return r.stdout


def extract_text(pdf: pathlib.Path, ocr_lang: str) -> tuple[str, str]:
    """Return (text, source) where source is 'pdfplumber' or 'tesseract'."""
    text = run([str(DOCS_PY), str(PDF_EXTRACT), str(pdf)])
    if len(text.strip()) >= OCR_FALLBACK_CHAR_THRESHOLD:
        return text, "pdfplumber"
    # Sparse text → probably scanned. Fall back to OCR.
    ocr_text = run([str(DOCS_PY), str(OCR), str(pdf), "--lang", ocr_lang])
    return ocr_text, "tesseract"


def slugify(s: str, max_len: int = 60) -> str:
    return (re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower() or "abstract")[:max_len]


# --- Field heuristics -------------------------------------------------------

FIELD_PATTERNS: dict[str, list[str]] = {
    # Key: pretty label.   Value: ordered list of regex patterns (first match wins).
    "Lessor / Landlord": [
        r"(?im)^\s*(?:Lessor|Landlord|Party of the First Part)\s*[:\-]\s*(.+?)\s*$",
        r"(?i)\bbetween\s+(.+?)\s*,?\s*(?:a|an)?\s*(?:Illinois|Delaware|[A-Z][a-z]+)\s+(?:LLC|Limited Liability Company|corporation|partnership)",
    ],
    "Lessee / Tenant": [
        r"(?im)^\s*(?:Lessee|Tenant|Party of the Second Part)\s*[:\-]\s*(.+?)\s*$",
    ],
    "Premises / Address": [
        r"(?im)^\s*(?:Premises|Property|Demised Premises|Leased Premises|Property Address)\s*[:\-]\s*(.+?)\s*$",
        r"(?i)located at\s+([^\.\n]+)",
    ],
    "Term": [
        r"(?im)^\s*(?:Term|Lease Term|Initial Term)\s*[:\-]\s*(.+?)\s*$",
        r"(?i)(?:term of|for a term of)\s+((?:\d+\s+years?\s*(?:and\s+\d+\s+months?)?|\d+\s+months?))",
    ],
    "Commencement Date": [
        r"(?im)^\s*(?:Commencement Date|Lease Commencement|Start Date|Effective Date)\s*[:\-]\s*(.+?)\s*$",
    ],
    "Expiration Date": [
        r"(?im)^\s*(?:Expiration Date|Termination Date|End Date|Lease Expiration)\s*[:\-]\s*(.+?)\s*$",
    ],
    "Base Rent": [
        r"(?im)^\s*(?:Base Rent|Monthly Rent|Annual Rent|Minimum Rent|Rent)\s*[:\-]\s*(.+?)\s*$",
        r"(?i)\$([\d,]+(?:\.\d{2})?)\s*(?:per|/)\s*month",
    ],
    "Rent Escalations": [
        r"(?im)^\s*(?:Rent Escalation|Escalation|Rent Increase|Annual Increase)\s*[:\-]\s*(.+?)\s*$",
        r"(?i)escalat(?:ing|ion)\s+(?:at|of|by)\s+(\d+(?:\.\d+)?\s*%?)",
    ],
    "NNN / Lease Type": [
        # Word boundaries prevent "NN" matching inside "anniversary", "announce", etc.
        # Ordered longest-first so "NNN" wins over "NN" and "triple-net" wins over "net".
        r"(?i)\b(triple[\s\-]?net|absolute net|modified gross|gross lease|NNN|double[\s\-]?net|NN)\b",
    ],
    "CAM / Operating Expenses": [
        r"(?im)^\s*(?:CAM|Common Area Maintenance|Operating Expenses|Expenses)\s*[:\-]\s*(.+?)\s*$",
    ],
    "Renewal Options": [
        r"(?im)^\s*(?:Renewal Option|Renewal Options|Option to Renew|Option to Extend)\s*[:\-]\s*(.+?)\s*$",
        r"(?i)(\d+)\s+(?:option|options)\s+to\s+(?:renew|extend)",
    ],
    "Security Deposit": [
        r"(?im)^\s*(?:Security Deposit|Deposit)\s*[:\-]\s*(.+?)\s*$",
    ],
    "Guarantor": [
        r"(?im)^\s*(?:Guarantor|Guaranty)\s*[:\-]\s*(.+?)\s*$",
    ],
}


def extract_fields(text: str) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for label, patterns in FIELD_PATTERNS.items():
        hit = None
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                hit = (m.group(1) if m.groups() else m.group(0)).strip()
                # Trim excessively long captures (likely over-matched)
                if len(hit) > 200:
                    hit = hit[:200] + "…"
                break
        out[label] = hit
    return out


# --- Output writer ----------------------------------------------------------

def build_markdown(pdf: pathlib.Path, text: str, source: str, fields: dict[str, str | None]) -> str:
    today = dt.date.today().isoformat()
    lessee = fields.get("Lessee / Tenant") or "UNKNOWN-TENANT"
    address = fields.get("Premises / Address") or "UNKNOWN-ADDRESS"
    title = f"Lease Abstract — {lessee} @ {address}"

    front = [
        "---",
        "type: lease-abstract",
        f"date-abstracted: {today}",
        f"source-pdf: {pdf}",
        f"extraction-source: {source}",
        f"lessee: {fields.get('Lessee / Tenant') or ''!r}",
        f"lessor: {fields.get('Lessor / Landlord') or ''!r}",
        f"premises: {fields.get('Premises / Address') or ''!r}",
        "status: draft",
        "tags: [lease, abstract]",
        "---",
        "",
    ]

    summary = [f"# {title}", "", "## Key Terms (auto-extracted)"]
    for label in FIELD_PATTERNS:
        value = fields.get(label)
        if value:
            summary.append(f"- **{label}:** {value}")
        else:
            summary.append(f"- **{label}:** [TODO — not found by heuristics]")

    summary += [
        "",
        "## Unusual Clauses / Manual Review",
        "- [TODO] Review the full extracted text below for break clauses, co-tenancy, exclusive-use, "
        "radius restrictions, assignment/subletting limits, audit rights, and anything unusual.",
        "",
        f"## Full Extracted Text (source: {source})",
        "```",
        text.rstrip(),
        "```",
    ]
    return "\n".join(front + summary) + "\n"


def build_output_path(pdf: pathlib.Path, fields: dict[str, str | None], explicit: str | None) -> pathlib.Path:
    if explicit:
        return pathlib.Path(explicit).expanduser().resolve()
    DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    lessee = slugify(fields.get("Lessee / Tenant") or pdf.stem, max_len=30)
    return DEFAULT_OUT_DIR / f"{today}_{lessee}.md"


def main() -> int:
    ap = argparse.ArgumentParser(description="Build a first-pass lease abstract from a PDF.")
    ap.add_argument("pdf", type=pathlib.Path)
    ap.add_argument("--out", help="Explicit output Markdown path.")
    ap.add_argument("--lang", default="eng", help="OCR language if fallback is triggered. Default: eng.")
    args = ap.parse_args()

    if not args.pdf.exists():
        sys.exit(f"error: {args.pdf} not found")

    text, source = extract_text(args.pdf.resolve(), args.lang)
    fields = extract_fields(text)
    md = build_markdown(args.pdf.resolve(), text, source, fields)
    out = build_output_path(args.pdf.resolve(), fields, args.out)
    out.write_text(md, encoding="utf-8")

    print(out)
    filled = sum(1 for v in fields.values() if v)
    print(f"# {filled}/{len(fields)} fields auto-extracted from {source}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
