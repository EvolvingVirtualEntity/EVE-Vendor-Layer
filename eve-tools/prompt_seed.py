#!/usr/bin/env python3
"""Seed Eve-originated reflective prompts into the knowledge DB as 'items'.

Unlike RSS items (pulled from external feeds), these are conversation seeds
Eve carries — short prompts she can adapt at send-time to ask team members
something personal or reflective. They live in the same `items` table with a
distinguishing `source = 'eve_prompts'` marker, so the relevance scorer +
cadence recommender treat them like any other item but the Phase 3 outreach
step knows to *adapt + speak in Eve's voice* rather than just forward a link.

Idempotent — uses synthetic URLs as unique keys; reseeding doesn't duplicate.

Prompt content is *instance-layer*. This script loads prompts from
`~/.config/eve/prompt_seed.yaml`. See `prompt_seed.example.yaml` (in the
vendor repo alongside this script) for the schema and a minimal template.

Usage:
    prompt_seed.py             # seed (or refresh) the prompt set
    prompt_seed.py --reset     # delete all eve_prompts items first
"""

import argparse
import datetime as dt
import json
import pathlib
import sqlite3
import sys

import yaml

DB_PATH = pathlib.Path.home() / ".local" / "eve-tools" / "eve-knowledge.db"
PROMPTS_FILE = pathlib.Path.home() / ".config" / "eve" / "prompt_seed.yaml"


def load_prompts() -> list[tuple[str, str, str, list[str]]]:
    """Load and validate prompts from the customer-supplied YAML.

    Returns a list of (slug, title, summary, tags) tuples.
    Raises if the file is missing or malformed — fail loud.
    """
    if not PROMPTS_FILE.exists():
        raise FileNotFoundError(
            f"Customer prompts file not found at {PROMPTS_FILE}. "
            f"Copy eve-tools/prompt_seed.example.yaml to that path and fill in "
            f"instance-specific prompts before running this script."
        )

    data = yaml.safe_load(PROMPTS_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "prompts" not in data:
        raise ValueError(
            f"{PROMPTS_FILE}: expected top-level 'prompts:' key listing prompt objects."
        )

    out: list[tuple[str, str, str, list[str]]] = []
    seen_slugs: set[str] = set()
    for i, p in enumerate(data["prompts"]):
        if not isinstance(p, dict):
            raise ValueError(f"{PROMPTS_FILE}: prompt #{i} is not a mapping.")
        slug = p.get("slug")
        title = p.get("title")
        summary = p.get("summary")
        tags = p.get("tags") or []
        if not slug or not isinstance(slug, str):
            raise ValueError(f"{PROMPTS_FILE}: prompt #{i} missing or invalid 'slug'.")
        if slug in seen_slugs:
            raise ValueError(f"{PROMPTS_FILE}: duplicate slug '{slug}'.")
        seen_slugs.add(slug)
        if not title or not isinstance(title, str):
            raise ValueError(f"{PROMPTS_FILE}: prompt '{slug}' missing or invalid 'title'.")
        if not summary or not isinstance(summary, str):
            raise ValueError(f"{PROMPTS_FILE}: prompt '{slug}' missing or invalid 'summary'.")
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise ValueError(f"{PROMPTS_FILE}: prompt '{slug}' tags must be a list of strings.")
        out.append((slug, title, summary, list(tags)))
    return out


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed Eve-originated reflective prompts.")
    ap.add_argument("--reset", action="store_true", help="Delete all eve_prompts items before seeding.")
    args = ap.parse_args()

    prompts = load_prompts()
    print(f"# loaded {len(prompts)} prompts from {PROMPTS_FILE}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    if args.reset:
        n = cur.execute("DELETE FROM items WHERE source = 'eve_prompts'").rowcount
        print(f"# reset: deleted {n} existing eve_prompts items")

    now = now_iso()
    added = refreshed = 0
    for slug, title, summary, tags in prompts:
        url = f"eve-prompt://reflection/{slug}"
        existing = cur.execute("SELECT id FROM items WHERE url = ?", (url,)).fetchone()
        if existing:
            cur.execute(
                "UPDATE items SET title=?, summary=?, raw_excerpt=?, tags=?, status='new', "
                "fetched_at=?, published_at=?, relevance_score=NULL "
                "WHERE id=?",
                (title, summary, summary, json.dumps(sorted(tags)),
                 now, now, existing["id"]),
            )
            refreshed += 1
        else:
            cur.execute(
                "INSERT INTO items (source, url, title, summary, published_at, fetched_at, "
                "tags, raw_excerpt, status) VALUES "
                "('eve_prompts', ?, ?, ?, ?, ?, ?, ?, 'new')",
                (url, title, summary, now, now, json.dumps(sorted(tags)), summary),
            )
            added += 1
    conn.commit()

    print(f"# eve_prompts: +{added} new, {refreshed} refreshed (total: {len(prompts)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
