#!/usr/bin/env python3
"""(Re)initialize Eve's knowledge DB from `interest_profile.yaml`.

Idempotent: existing interests are upserted (intensity preserved if already set;
new metadata fields refreshed). Facts are upserted by (subject, date_anchor) key.

Usage:
    interest_init.py                 # apply schema + load profile
    interest_init.py --reset         # WIPE the DB and start fresh
    interest_init.py --status        # print current interest list + counts
"""

import argparse
import datetime as dt
import json
import pathlib
import sqlite3
import sys

import yaml

EVE_TOOLS = pathlib.Path.home() / ".local" / "eve-tools"
DB_PATH = EVE_TOOLS / "eve-knowledge.db"
SCHEMA = EVE_TOOLS / "eve-knowledge-schema.sql"
PROFILE = EVE_TOOLS / "interest_profile.yaml"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA.read_text())
    return conn


def upsert_interests(conn: sqlite3.Connection, profile: dict) -> tuple[int, int]:
    cur = conn.cursor()
    added = 0
    refreshed = 0
    now = now_iso()
    for entry in profile.get("interests", []):
        existing = cur.execute(
            "SELECT id, intensity FROM interests WHERE name = ?",
            (entry["name"],),
        ).fetchone()
        rss_json = json.dumps(entry.get("rss_queries", []))
        tags_json = json.dumps(entry.get("tags", []))
        if existing:
            # Refresh metadata AND intensity from the YAML — the profile is the
            # source of truth for stable interests. The Phase-4 curator owns
            # transient-interest intensity drift via separate machinery.
            cur.execute("""
                UPDATE interests SET label=?, tags=?, type=?, rss_queries=?, notes=?, intensity=?
                WHERE id=?
            """, (entry["label"], tags_json, entry["type"], rss_json,
                  entry.get("notes", ""), entry.get("intensity", 0.5), existing["id"]))
            refreshed += 1
        else:
            cur.execute("""
                INSERT INTO interests
                  (name, label, tags, intensity, type, origin, rss_queries, notes,
                   created_at, last_reinforced, last_decayed)
                VALUES (?, ?, ?, ?, ?, 'profile', ?, ?, ?, NULL, NULL)
            """, (entry["name"], entry["label"], tags_json,
                  entry.get("intensity", 0.5), entry["type"], rss_json,
                  entry.get("notes", ""), now))
            added += 1
    conn.commit()
    return added, refreshed


def upsert_facts(conn: sqlite3.Connection, profile: dict) -> tuple[int, int]:
    cur = conn.cursor()
    added = 0
    refreshed = 0
    now = now_iso()
    for fact in profile.get("facts", []):
        tags_json = json.dumps(fact.get("tags", []))
        existing = cur.execute(
            "SELECT id FROM facts WHERE subject = ? AND date_anchor IS ?",
            (fact["subject"], fact.get("date_anchor")),
        ).fetchone()
        if existing:
            cur.execute("""
                UPDATE facts SET fact=?, recurrence=?, tags=?, notes=? WHERE id=?
            """, (fact["fact"], fact.get("recurrence", "once"), tags_json,
                  fact.get("notes", ""), existing["id"]))
            refreshed += 1
        else:
            cur.execute("""
                INSERT INTO facts
                  (subject, fact, date_anchor, recurrence, tags, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (fact["subject"], fact["fact"], fact.get("date_anchor"),
                  fact.get("recurrence", "once"), tags_json,
                  fact.get("notes", ""), now))
            added += 1
    conn.commit()
    return added, refreshed


def status(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    print("=== interests ===")
    rows = cur.execute("""
        SELECT name, label, type, intensity, last_reinforced
        FROM interests ORDER BY type, intensity DESC
    """).fetchall()
    for r in rows:
        lr = r["last_reinforced"] or "never"
        print(f"  [{r['type'][:3]}] {r['intensity']:.2f}  {r['name']:<28} {r['label']!r}  (last_reinforced: {lr})")
    print()

    n_items = cur.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    n_new   = cur.execute("SELECT COUNT(*) FROM items WHERE status='new'").fetchone()[0]
    n_facts = cur.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    n_log   = cur.execute("SELECT COUNT(*) FROM outreach_log").fetchone()[0]
    print(f"items: {n_items} ({n_new} new) · facts: {n_facts} · outreach log: {n_log}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Initialize Eve's knowledge DB.")
    ap.add_argument("--reset", action="store_true", help="WIPE the DB and reload from profile.")
    ap.add_argument("--status", action="store_true", help="Print status and exit.")
    args = ap.parse_args()

    if args.reset and DB_PATH.exists():
        DB_PATH.unlink()
        print(f"# wiped {DB_PATH}", file=sys.stderr)

    conn = open_db()
    if args.status:
        status(conn)
        return 0

    profile = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
    i_added, i_refresh = upsert_interests(conn, profile)
    f_added, f_refresh = upsert_facts(conn, profile)

    print(f"# interests: +{i_added} added, {i_refresh} refreshed")
    print(f"# facts: +{f_added} added, {f_refresh} refreshed")
    status(conn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
