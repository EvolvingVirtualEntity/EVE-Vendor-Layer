#!/usr/bin/env python3
"""Run Tesseract OCR over an image or a scanned PDF.

Usage:
    ocr.py page.png                         # default lang: eng
    ocr.py scan.pdf                         # auto-rasterises each page via poppler
    ocr.py doc.jpg --lang deu               # German only
    ocr.py doc.pdf --lang eng+deu           # both (slower, better for mixed docs)
    ocr.py doc.pdf --dpi 300                # override raster DPI (default 200)
    ocr.py doc.pdf --format json            # per-page JSON instead of flat text

Requires `tesseract` + `poppler-utils` on PATH (already installed system-wide).
"""

import argparse
import json
import pathlib
import sys


def ocr_image_file(path: pathlib.Path, lang: str) -> str:
    import pytesseract
    from PIL import Image
    return pytesseract.image_to_string(Image.open(path), lang=lang)


def ocr_pdf_file(path: pathlib.Path, lang: str, dpi: int) -> list[dict]:
    import pytesseract
    from pdf2image import convert_from_path
    images = convert_from_path(str(path), dpi=dpi)
    out: list[dict] = []
    for i, img in enumerate(images, 1):
        text = pytesseract.image_to_string(img, lang=lang)
        out.append({"page": i, "text": text})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Tesseract OCR for images and scanned PDFs.")
    ap.add_argument("src", type=pathlib.Path, help="Input image or PDF.")
    ap.add_argument("--lang", default="eng", help="Tesseract lang code. 'eng', 'deu', or 'eng+deu'. Default: eng.")
    ap.add_argument("--dpi", type=int, default=200, help="Raster DPI for PDF pages. Default: 200.")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    args = ap.parse_args()

    if not args.src.exists():
        sys.exit(f"error: {args.src} not found")

    is_pdf = args.src.suffix.lower() == ".pdf"

    if is_pdf:
        pages = ocr_pdf_file(args.src, args.lang, args.dpi)
        if args.format == "json":
            print(json.dumps({"file": args.src.name, "pages": pages}, ensure_ascii=False, indent=2))
        else:
            for p in pages:
                print(f"--- page {p['page']} ---")
                print(p["text"].rstrip())
                print()
    else:
        text = ocr_image_file(args.src, args.lang)
        if args.format == "json":
            print(json.dumps({"file": args.src.name, "text": text}, ensure_ascii=False, indent=2))
        else:
            print(text.rstrip())

    return 0


if __name__ == "__main__":
    sys.exit(main())
