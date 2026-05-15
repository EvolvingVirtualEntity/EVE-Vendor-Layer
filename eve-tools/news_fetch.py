#!/usr/bin/env python3
"""Fetch news/RSS feeds for every interest in Eve's knowledge DB.

Walks the `interests` table, polls every URL in each interest's `rss_queries`,
parses with feedparser, dedupes by URL, and inserts new items into the `items`
table tagged with the matching interest name(s).

Designed to be run twice a day via cron (e.g. 7 AM + 1 PM Pacific). Idempotent:
already-known URLs are skipped silently.

Usage:
    news_fetch.py                 # fetch all interests
    news_fetch.py --interest cristiano_ronaldo   # just one interest
    news_fetch.py --dry-run       # don't write to DB, just report
    news_fetch.py --max-per-feed 20   # cap items pulled per feed (default 25)
"""

import argparse
import datetime as dt
import html
import json
import pathlib
import re
import sqlite3
import sys

import feedparser

EVE_TOOLS = pathlib.Path.home() / ".local" / "eve-tools"
DB_PATH = EVE_TOOLS / "eve-knowledge.db"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def parse_published(entry) -> str | None:
    """Try the standard feedparser fields; return ISO8601 UTC or None."""
    for key in ("published_parsed", "updated_parsed"):
        t = getattr(entry, key, None) or entry.get(key)
        if t:
            try:
                return dt.datetime(*t[:6], tzinfo=dt.timezone.utc).isoformat(timespec="seconds")
            except Exception:
                continue
    return None


def clean_excerpt(text: str, max_chars: int = 600) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)        # strip HTML tags
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def fetch_feed(url: str, max_items: int) -> list[dict]:
    parsed = feedparser.parse(url)
    out: list[dict] = []
    for entry in parsed.entries[:max_items]:
        link = getattr(entry, "link", None) or entry.get("link")
        title = getattr(entry, "title", None) or entry.get("title") or "(no title)"
        if not link:
            continue
        summary = (
            getattr(entry, "summary", None)
            or entry.get("summary")
            or entry.get("description")
            or ""
        )
        out.append({
            "url": link,
            "title": title.strip(),
            "summary": clean_excerpt(summary),
            "published_at": parse_published(entry),
        })
    return out


def fetch_for_interests(conn: sqlite3.Connection, only: str | None,
                        max_per_feed: int, dry_run: bool) -> dict:
    cur = conn.cursor()
    rows = cur.execute("SELECT id, name, rss_queries, tags FROM interests").fetchall()
    stats = {"interests_polled": 0, "feeds_polled": 0, "items_seen": 0,
             "items_new": 0, "items_dup": 0, "errors": 0, "by_interest": {}}

    for r in rows:
        if only and r["name"] != only:
            continue
        feeds = json.loads(r["rss_queries"] or "[]")
        if not feeds:
            continue
        interest_tags = set(json.loads(r["tags"] or "[]"))
        added_for_this = 0
        stats["interests_polled"] += 1

        for f in feeds:
            stats["feeds_polled"] += 1
            try:
                items = fetch_feed(f["url"], max_per_feed)
            except Exception as e:
                stats["errors"] += 1
                print(f"#   ! fetch failed for {f['name']}: {e}", file=sys.stderr)
                continue

            for it in items:
                stats["items_seen"] += 1
                existing = cur.execute("SELECT id, tags FROM items WHERE url = ?",
                                       (it["url"],)).fetchone()
                if existing:
                    stats["items_dup"] += 1
                    # Merge tags — an item can match multiple interests
                    existing_tags = set(json.loads(existing["tags"] or "[]"))
                    new_tags = sorted(existing_tags | interest_tags | {r["name"]})
                    if new_tags != sorted(existing_tags):
                        cur.execute("UPDATE items SET tags=? WHERE id=?",
                                    (json.dumps(new_tags), existing["id"]))
                    continue

                merged_tags = sorted(interest_tags | {r["name"]})
                if not dry_run:
                    cur.execute("""
                        INSERT INTO items
                          (source, url, title, summary, published_at, fetched_at, tags, raw_excerpt, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new')
                    """, (f["name"], it["url"], it["title"], it["summary"],
                          it["published_at"], now_iso(),
                          json.dumps(merged_tags), it["summary"]))
                stats["items_new"] += 1
                added_for_this += 1

        stats["by_interest"][r["name"]] = added_for_this

    if not dry_run:
        conn.commit()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch news for Eve's interests.")
    ap.add_argument("--interest", help="Only fetch one interest by name.")
    ap.add_argument("--max-per-feed", type=int, default=25, help="Cap items per feed (default 25).")
    ap.add_argument("--dry-run", action="store_true", help="Don't write to DB.")
    args = ap.parse_args()

    if not DB_PATH.exists():
        sys.exit(f"error: DB not found — run interest_init.py first ({DB_PATH})")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    stats = fetch_for_interests(conn, args.interest, args.max_per_feed, args.dry_run)

    print(f"# interests polled: {stats['interests_polled']}")
    print(f"# feeds polled:     {stats['feeds_polled']}")
    print(f"# items seen:       {stats['items_seen']}")
    print(f"# items new:        {stats['items_new']}{' (dry-run)' if args.dry_run else ''}")
    print(f"# items duplicate:  {stats['items_dup']}")
    print(f"# errors:           {stats['errors']}")
    print()
    print("# new items per interest:")
    for name, n in sorted(stats["by_interest"].items(), key=lambda kv: -kv[1]):
        if n > 0:
            print(f"#   {name:<28} +{n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
