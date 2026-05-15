#!/usr/bin/env python3
"""Score every `status='new'` item in Eve's knowledge DB on a 0-1 conversational-fit scale.

Formula (v1, intentionally simple):

    score = 0.7 * interest_intensity
          + 0.2 * recency_decay
          + 0.1 * tag_richness

Where:
  interest_intensity = max intensity of any interest the item is tagged with
  recency_decay      = exp(-hours_old / 72)   -- ~50% at 50h, ~10% at 165h
  tag_richness       = 1 - exp(-num_distinct_tags / 3)  -- diminishing returns

After scoring, status flips from 'new' → 'scored'. The item is then
eligible for cadence-driven outreach (Phase 3).

Usage:
  relevance_score.py                     # score all unscored items
  relevance_score.py --rescore           # also re-score items already 'scored'
  relevance_score.py --top 10            # also print top-10 currently-scored items
"""

import argparse
import datetime as dt
import json
import math
import pathlib
import sqlite3
import sys

DB_PATH = pathlib.Path.home() / ".local" / "eve-tools" / "eve-knowledge.db"


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_iso(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def score_item(item_tags: set[str], item_published_at: str | None,
               item_fetched_at: str, interests_by_tag: dict[str, list[float]],
               item_source: str | None = None) -> tuple[float, dict]:
    """Return (score, components) where components is a small dict for explainability.

    If item_source == 'eve_prompts', treat the item as evergreen (recency=1.0) —
    seeds don't decay the way news does. A two-month-old reflective seed is not
    less valuable than a two-hour-old one.
    """
    # interest intensity: max intensity of any interest matching any tag
    intensities = []
    for t in item_tags:
        intensities.extend(interests_by_tag.get(t, []))
    interest_intensity = max(intensities) if intensities else 0.0

    # recency: prefer published_at, fall back to fetched_at. eve_prompts are evergreen.
    if item_source == "eve_prompts":
        recency = 1.0
        hours_old = 0.0
    else:
        when = parse_iso(item_published_at) or parse_iso(item_fetched_at) or now_utc()
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        hours_old = max(0.0, (now_utc() - when).total_seconds() / 3600.0)
        recency = math.exp(-hours_old / 72.0)

    # tag richness: more matching interests = slightly more salient
    matched_tags = sum(1 for t in item_tags if t in interests_by_tag)
    tag_richness = 1.0 - math.exp(-matched_tags / 3.0)

    score = 0.7 * interest_intensity + 0.2 * recency + 0.1 * tag_richness
    return round(score, 4), {
        "interest_intensity": round(interest_intensity, 3),
        "recency": round(recency, 3),
        "tag_richness": round(tag_richness, 3),
        "hours_old": round(hours_old, 1),
        "matched_tags": matched_tags,
    }


def build_interest_index(conn: sqlite3.Connection) -> dict[str, list[float]]:
    """Map every interest-tag → list of intensities of interests bearing that tag."""
    cur = conn.cursor()
    out: dict[str, list[float]] = {}
    for r in cur.execute("SELECT name, tags, intensity FROM interests"):
        tags = set(json.loads(r["tags"] or "[]"))
        tags.add(r["name"])  # name itself counts as an implicit tag
        for t in tags:
            out.setdefault(t, []).append(r["intensity"])
    return out


def score_all(conn: sqlite3.Connection, rescore: bool) -> dict:
    cur = conn.cursor()
    interests_by_tag = build_interest_index(conn)

    statuses = ("'new'",) if not rescore else ("'new'", "'scored'")
    rows = cur.execute(
        f"SELECT id, source, tags, published_at, fetched_at FROM items "
        f"WHERE status IN ({','.join(statuses)})"
    ).fetchall()

    stats = {"scored": 0, "by_bucket": {"hi": 0, "med": 0, "lo": 0}}
    for r in rows:
        tags = set(json.loads(r["tags"] or "[]"))
        score, _components = score_item(tags, r["published_at"], r["fetched_at"],
                                        interests_by_tag, item_source=r["source"])
        cur.execute(
            "UPDATE items SET relevance_score = ?, status = 'scored' WHERE id = ?",
            (score, r["id"]),
        )
        stats["scored"] += 1
        if score >= 0.70:
            stats["by_bucket"]["hi"] += 1
        elif score >= 0.55:
            stats["by_bucket"]["med"] += 1
        else:
            stats["by_bucket"]["lo"] += 1
    conn.commit()
    return stats


def print_top(conn: sqlite3.Connection, n: int) -> None:
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT relevance_score, source, substr(title,1,80) as title, "
        "substr(published_at,1,16) as pub, tags "
        "FROM items WHERE status='scored' ORDER BY relevance_score DESC LIMIT ?",
        (n,),
    ).fetchall()
    print(f"\n# top {len(rows)} by relevance:")
    for r in rows:
        tags = ", ".join(json.loads(r["tags"] or "[]")[:5])
        print(f"  [{r['relevance_score']:.3f}] [{r['pub'] or '?              '}] {r['source']:<24} {r['title']}")
        print(f"           tags: {tags}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Score knowledge DB items.")
    ap.add_argument("--rescore", action="store_true", help="Also re-score items already 'scored'.")
    ap.add_argument("--top", type=int, default=0, help="Also print the top-N items after scoring.")
    args = ap.parse_args()

    if not DB_PATH.exists():
        sys.exit(f"error: DB missing at {DB_PATH} — run interest_init.py first")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    stats = score_all(conn, args.rescore)
    print(f"# scored {stats['scored']} item(s)  "
          f"hi(≥0.70): {stats['by_bucket']['hi']}  "
          f"med(0.55-0.70): {stats['by_bucket']['med']}  "
          f"lo(<0.55): {stats['by_bucket']['lo']}")
    if args.top:
        print_top(conn, args.top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
