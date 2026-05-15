#!/usr/bin/env python3
"""Eve's image generator.

Backends:
  * pollinations (default) — truly free HTTP API, no key, good quality.
  * gemini — Google AI Studio's Gemini 2.5 Flash Image (Nano Banana). Requires
    an API key AND a paid AI Studio plan; free-tier quota for the image model
    is 0/day as of 2026-04.

Usage:
    imagegen.py "a cat wearing a monocle"                 # pollinations
    imagegen.py "..." --backend gemini                    # gemini (paid)
    imagegen.py "..." --out /path/to/image.png
    imagegen.py "..." --edit /path/to/input.jpg           # gemini-only

Key for gemini backend: ~/.config/eve/api-keys.env (GOOGLE_AI_STUDIO_API_KEY).
Default output dir: ~/eve-images/YYYY-MM-DD_HHMMSS_slug.png
"""

import argparse
import datetime as dt
import io
import pathlib
import re
import sys
import urllib.parse
import urllib.request


KEY_FILE = pathlib.Path.home() / ".config" / "eve" / "api-keys.env"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash-image"
DEFAULT_OUT_DIR = pathlib.Path.home() / "eve-images"
POLLINATIONS_ENDPOINT = "https://image.pollinations.ai/prompt/"


def load_api_key() -> str:
    if not KEY_FILE.exists():
        sys.exit(f"error: {KEY_FILE} not found")
    for line in KEY_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("GOOGLE_AI_STUDIO_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("error: GOOGLE_AI_STUDIO_API_KEY not found in key file")


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return slug[:max_len] or "image"


def build_output_path(prompt: str, explicit: str | None) -> pathlib.Path:
    if explicit:
        return pathlib.Path(explicit).expanduser().resolve()
    DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return DEFAULT_OUT_DIR / f"{stamp}_{slugify(prompt)}.png"


def run_pollinations(prompt: str, out_path: pathlib.Path, width: int, height: int, seed: int | None) -> None:
    params = {"width": width, "height": height, "nologo": "true", "safe": "false"}
    if seed is not None:
        params["seed"] = seed
    url = POLLINATIONS_ENDPOINT + urllib.parse.quote(prompt, safe="") + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "eve-imagegen/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    if not data or len(data) < 512:
        sys.exit(f"error: pollinations returned {len(data)} bytes (likely an error page)")
    out_path.write_bytes(data)


def run_gemini(prompt: str, out_path: pathlib.Path, model: str, edit_path: pathlib.Path | None) -> list[str]:
    from google import genai  # imported lazily so pollinations path has no hard dep
    from PIL import Image

    client = genai.Client(api_key=load_api_key())
    contents: list = [prompt]
    if edit_path is not None:
        contents = [prompt, Image.open(edit_path)]

    response = client.models.generate_content(model=model, contents=contents)
    saved = False
    text_parts: list[str] = []
    for candidate in response.candidates or []:
        for part in (candidate.content.parts if candidate.content else []):
            if getattr(part, "inline_data", None) and part.inline_data.data:
                Image.open(io.BytesIO(part.inline_data.data)).save(out_path)
                saved = True
            elif getattr(part, "text", None):
                text_parts.append(part.text)
    if not saved:
        msg = "error: gemini returned no image"
        if text_parts:
            msg += f"\nmodel text: {' '.join(text_parts)}"
        sys.exit(msg)
    return text_parts


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate an image (pollinations or gemini).")
    ap.add_argument("prompt", help="Text prompt (wrap in quotes).")
    ap.add_argument("--backend", choices=["pollinations", "gemini"], default="pollinations",
                    help="Image backend. Default: pollinations (free, no key).")
    ap.add_argument("--out", help="Output path (default: ~/eve-images/<stamp>_<slug>.png).")
    ap.add_argument("--model", default=DEFAULT_GEMINI_MODEL,
                    help=f"Gemini model name (default: {DEFAULT_GEMINI_MODEL}).")
    ap.add_argument("--edit", help="Path to input image for edit mode (gemini only).")
    ap.add_argument("--width", type=int, default=1024, help="Pollinations width (default 1024).")
    ap.add_argument("--height", type=int, default=1024, help="Pollinations height (default 1024).")
    ap.add_argument("--seed", type=int, default=None, help="Pollinations seed for reproducibility.")
    args = ap.parse_args()

    out_path = build_output_path(args.prompt, args.out)
    text_parts: list[str] = []

    if args.backend == "pollinations":
        if args.edit:
            sys.exit("error: --edit requires --backend gemini")
        run_pollinations(args.prompt, out_path, args.width, args.height, args.seed)
    else:
        edit_path: pathlib.Path | None = None
        if args.edit:
            edit_path = pathlib.Path(args.edit).expanduser().resolve()
            if not edit_path.exists():
                sys.exit(f"error: edit source {edit_path} not found")
        text_parts = run_gemini(args.prompt, out_path, args.model, edit_path)

    print(out_path)
    if text_parts:
        print(f"# model text: {' '.join(text_parts)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
