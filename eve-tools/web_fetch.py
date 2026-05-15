#!/usr/bin/env python3
"""Fetch a web page with a real headless browser.

Unlike `curl` or `requests`, this runs JavaScript — so it can handle sites that
render content client-side (listing aggregators, county assessor portals,
market-data dashboards).

Usage:
    web_fetch.py "https://example.com"
    web_fetch.py "https://..." --text                      # plaintext content
    web_fetch.py "https://..." --screenshot out.png        # full-page screenshot
    web_fetch.py "https://..." --wait-selector "#results"  # wait for a CSS selector
    web_fetch.py "https://..." --wait-ms 3000              # extra fixed wait

Requires `playwright` + chromium installed (`~/.local/eve-tools/docs-venv/`).
"""

import argparse
import pathlib
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch a URL via headless Chromium.")
    ap.add_argument("url")
    ap.add_argument("--text", action="store_true", help="Output plaintext instead of HTML.")
    ap.add_argument("--screenshot", help="Save a full-page PNG screenshot to this path.")
    ap.add_argument("--wait-selector", help="CSS selector to wait for before capture.")
    ap.add_argument("--wait-ms", type=int, default=0, help="Extra fixed wait (ms).")
    ap.add_argument("--timeout", type=int, default=30000, help="Navigation timeout in ms (default 30000).")
    ap.add_argument("--viewport", default="1440x900", help="Viewport WIDTHxHEIGHT (default 1440x900).")
    ap.add_argument("--user-agent",
                    default=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
                    help="User-Agent override.")
    args = ap.parse_args()

    try:
        w, h = [int(x) for x in args.viewport.lower().split("x")]
    except Exception:
        sys.exit(f"error: bad --viewport {args.viewport!r}; expected WIDTHxHEIGHT")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": w, "height": h}, user_agent=args.user_agent)
        page = context.new_page()
        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout)
            if args.wait_selector:
                page.wait_for_selector(args.wait_selector, timeout=args.timeout)
            if args.wait_ms:
                page.wait_for_timeout(args.wait_ms)

            if args.screenshot:
                out = pathlib.Path(args.screenshot).expanduser().resolve()
                out.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(out), full_page=True)
                print(f"# screenshot: {out}", file=sys.stderr)

            if args.text:
                content = page.inner_text("body")
            else:
                content = page.content()
            print(content)
        finally:
            context.close()
            browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
