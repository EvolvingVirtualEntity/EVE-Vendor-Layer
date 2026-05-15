#!/usr/bin/env python3
"""Phase 6 — Daily BTS (build-to-suit) deal-sourcing sweep.

Runs on a cron each morning. For every scored item fetched in the last 24
hours that carries a BTS-family tag (bts / franchise / tenant_search / rfp /
economic_development / relocation / expansion), applies a secondary
"deal-fit" score on top of the base relevance score:

    deal_fit = base_relevance
             + 0.15 if the item mentions L&R's operating geography (Illinois /
                     Chicago / Chicagoland / DuPage / Kendall / Cook / etc.)
             + 0.10 if the item contains a tenant-action keyword
                     ("seeking sites", "looking for", "expanding", "new
                     locations", "build-to-suit", "site selection", etc.)
             + 0.10 if the item contains a size signal in L&R's wheelhouse
                     (a SF/square-feet number within 1,000 - 30,000 SF)

Items with `deal_fit >= PROMOTE_THRESHOLD` (0.90):
  · Get tagged with `bts_flagged` in the items table, which makes the
    cadence recommender pick them as high-priority for either partner's
    morning_work window.
  · Get their relevance_score bumped to `deal_fit` so the recommender
    ranks them correctly.

A daily digest is written to the vault at:
    02-Projects/BTS-Pipeline/YYYY-MM-DD.md

The digest is append-only per the vault rule — if today's digest file
already exists, a new timestamped section is appended; the earlier content
is preserved.

Usage:
    bts_sweep.py                   # live run (writes digest, bumps items)
    bts_sweep.py --dry-run         # print what would happen, no writes
    bts_sweep.py --window-hours N  # look back N hours instead of 24
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sqlite3
import sys

EVE_TOOLS = pathlib.Path.home() / ".local" / "eve-tools"
DB_PATH = EVE_TOOLS / "eve-knowledge.db"
VAULT = pathlib.Path.home() / "EveBrain"
DIGEST_DIR = VAULT / "02-Projects" / "BTS-Pipeline"

BTS_TAGS = {
    "bts", "franchise", "tenant_search", "rfp",
    "economic_development", "relocation", "expansion",
}

GEO_PATTERNS = [
    r"\billinois\b", r"\bchicago\b", r"\bchicagoland\b",
    r"\bdupage\b", r"\bkendall\b", r"\bcook county\b",
    r"\bwheaton\b", r"\byorkville\b", r"\bnaperville\b",
    r"\baurora\b", r"\bjoliet\b", r"\bschaumburg\b",
    r"\boak brook\b", r"\bfox valley\b", r"\brockford\b",
    r"\bchicago suburbs?\b",
]

ACTION_PATTERNS = [
    r"\bseeking\b", r"\blooking for\b", r"\bexpand(?:ing|ed)?\b",
    r"\bnew (?:location|store|site)s?\b", r"\bbuild[- ]to[- ]suit\b",
    r"\bsite selection\b", r"\brelocat(?:e|ing|ion)\b",
    r"\bopen(?:ing)? (?:a )?new\b", r"\bexpansion\b",
    r"\btenant search\b", r"\brfp\b", r"\b(?:ground )?break(?:s|ing)\b",
    r"\blaunch(?:ing)?\b",
]

# Match a number between 1000 and 29999 followed by SF/sq ft indicator,
# OR with a K suffix like "5K SF" / "10K square feet".
SIZE_PATTERN = re.compile(
    r"\b(?:"
    r"(?:[1-9](?:,?\d{3})(?:-[1-9](?:,?\d{3}))?)"  # 1,000–29,999 optionally 5,000-10,000
    r"|(?:[1-9]\d?)\s?K"                             # 5K, 10K
    r")\s?(?:sf|sq\.?\s?ft\.?|square feet|square-feet)\b",
    re.IGNORECASE,
)

PROMOTE_THRESHOLD = 0.90
GEO_BONUS = 0.15
ACTION_BONUS = 0.10
SIZE_BONUS = 0.10
DIGEST_TOP_N = 15


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def today_local_date() -> str:
    """Use the server's local date for the digest filename (cron runs on local time)."""
    return dt.date.today().isoformat()


def bts_tagged(tags: set[str]) -> bool:
    return bool(tags & BTS_TAGS)


def has_any(patterns: list[str], text: str) -> bool:
    for p in patterns:
        if re.search(p, text, flags=re.IGNORECASE):
            return True
    return False


def has_size_signal(text: str) -> bool:
    return SIZE_PATTERN.search(text) is not None


def compute_deal_fit(base: float, haystack: str) -> tuple[float, dict]:
    bonuses: dict = {"geo": False, "action": False, "size": False}
    total = base
    if has_any(GEO_PATTERNS, haystack):
        total += GEO_BONUS
        bonuses["geo"] = True
    if has_any(ACTION_PATTERNS, haystack):
        total += ACTION_BONUS
        bonuses["action"] = True
    if has_size_signal(haystack):
        total += SIZE_BONUS
        bonuses["size"] = True
    return round(min(1.0, total), 4), bonuses


def fetch_candidates(conn: sqlite3.Connection, window_hours: int) -> list[dict]:
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=window_hours)).isoformat()
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT id, source, url, title, summary, published_at, fetched_at, "
        "tags, relevance_score FROM items "
        "WHERE status = 'scored' AND fetched_at >= ? ",
        (cutoff,),
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        tags = set(json.loads(r["tags"] or "[]"))
        if not bts_tagged(tags):
            continue
        haystack = f"{r['title']}\n{r['summary'] or ''}"
        deal_fit, bonuses = compute_deal_fit(r["relevance_score"] or 0.0, haystack)
        out.append({
            "id": r["id"],
            "source": r["source"],
            "url": r["url"],
            "title": r["title"],
            "summary": r["summary"] or "",
            "published_at": r["published_at"],
            "fetched_at": r["fetched_at"],
            "tags": sorted(tags),
            "base_score": r["relevance_score"],
            "deal_fit": deal_fit,
            "bonuses": bonuses,
        })
    out.sort(key=lambda c: c["deal_fit"], reverse=True)
    return out


def promote_high_score_items(conn: sqlite3.Connection, items: list[dict],
                             dry_run: bool) -> list[int]:
    """Tag high-deal-fit items with 'bts_flagged' and bump their relevance_score."""
    promoted: list[int] = []
    cur = conn.cursor()
    for c in items:
        if c["deal_fit"] < PROMOTE_THRESHOLD:
            continue
        promoted.append(c["id"])
        if dry_run:
            continue
        tags = set(c["tags"]) | {"bts_flagged"}
        cur.execute(
            "UPDATE items SET relevance_score = ?, tags = ? WHERE id = ?",
            (c["deal_fit"], json.dumps(sorted(tags)), c["id"]),
        )
    if promoted and not dry_run:
        conn.commit()
    return promoted


# ---------------------------------------------------------------------------
# Digest rendering
# ---------------------------------------------------------------------------

FAMILY_BUCKETS = [
    ("Illinois / regional matches", {"illinois", "chicago", "dupage", "kendall", "cook"},
     lambda c: c["bonuses"]["geo"]),
    ("Franchise + retail expansion", {"franchise", "retail", "expansion"},
     lambda c: bool({"franchise", "retail"} & set(c["tags"]))),
    ("Tenant searches + BTS trade press", {"tenant_search", "rfp", "bts"},
     lambda c: bool({"tenant_search", "rfp"} & set(c["tags"]))),
    ("Economic development / relocations", {"economic_development", "relocation"},
     lambda c: bool({"economic_development", "relocation"} & set(c["tags"]))),
]


def fmt_item(c: dict) -> str:
    flags = []
    if c["bonuses"]["geo"]:
        flags.append("🗺️ IL")
    if c["bonuses"]["action"]:
        flags.append("🎯 action")
    if c["bonuses"]["size"]:
        flags.append("📐 size")
    flag_str = " · ".join(flags) if flags else "—"
    pub = (c["published_at"] or "")[:16]
    summary_snippet = (c["summary"] or "").replace("\n", " ")[:240]
    return (
        f"- **[{c['deal_fit']:.3f}]** [{c['title']}]({c['url']})\n"
        f"  · *source*: `{c['source']}` · *published*: {pub or '?'} · *signals*: {flag_str}\n"
        f"  · *tags*: {', '.join(c['tags'][:6])}\n"
        f"  · {summary_snippet}…"
    )


def render_digest(items: list[dict], promoted: list[int], window_hours: int) -> str:
    now_local = dt.datetime.now().astimezone()
    lines: list[str] = []
    lines.append(f"## Sweep at {now_local.isoformat(timespec='minutes')}")
    lines.append("")
    lines.append(f"- **Items in BTS window** ({window_hours}h look-back): {len(items)}")
    lines.append(f"- **Promoted to `bts_flagged`** (deal_fit ≥ {PROMOTE_THRESHOLD}): "
                 f"{len(promoted)}")
    lines.append("")

    if items:
        top = items[:DIGEST_TOP_N]
        lines.append(f"### 🏆 Top picks (deal_fit ranked, top {len(top)})")
        lines.append("")
        for c in top:
            lines.append(fmt_item(c))
            lines.append("")

    for label, _tag_set, pred in FAMILY_BUCKETS:
        bucket = [c for c in items if pred(c)][:8]
        if not bucket:
            continue
        lines.append(f"### {label}")
        lines.append("")
        for c in bucket:
            lines.append(fmt_item(c))
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_digest(body: str, dry_run: bool) -> pathlib.Path:
    DIGEST_DIR.mkdir(parents=True, exist_ok=True)
    path = DIGEST_DIR / f"{today_local_date()}.md"
    header_if_new = (
        f"# BTS Pipeline — {today_local_date()}\n\n"
        "*Auto-generated by `bts_sweep.py`. Items grouped by secondary "
        "deal-fit scoring on top of the base relevance score. Promoted "
        "items (`bts_flagged`) also surface through Phase 3 outreach.*\n\n"
        "---\n\n"
    )
    if dry_run:
        return path
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        new_content = existing.rstrip() + "\n\n---\n\n" + body
    else:
        new_content = header_if_new + body
    path.write_text(new_content, encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="Daily BTS deal-sourcing sweep (Phase 6).")
    ap.add_argument("--dry-run", action="store_true", help="No DB writes, no file writes.")
    ap.add_argument("--window-hours", type=int, default=24,
                    help="Look-back window in hours (default 24).")
    ap.add_argument("--json", action="store_true", help="JSON output instead of pretty.")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    items = fetch_candidates(conn, args.window_hours)
    promoted = promote_high_score_items(conn, items, args.dry_run)
    body = render_digest(items, promoted, args.window_hours)
    path = write_digest(body, args.dry_run)

    if args.json:
        print(json.dumps({
            "window_hours": args.window_hours,
            "candidate_count": len(items),
            "promoted_count": len(promoted),
            "digest_path": str(path),
            "dry_run": args.dry_run,
            "top_titles": [i["title"] for i in items[:10]],
        }, indent=2))
    else:
        print(f"# BTS sweep — window={args.window_hours}h  "
              f"candidates={len(items)}  promoted={len(promoted)}  "
              f"digest={path}  dry_run={args.dry_run}")
        print()
        print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
