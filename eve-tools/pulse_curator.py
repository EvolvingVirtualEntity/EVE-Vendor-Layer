#!/usr/bin/env python3
"""Phase 4 — Eve's knowledge-base curator.

Runs daily (cron). Keeps the knowledge DB from getting stale or bloated:

  1. EXPIRE stale RSS items
       Items with status='scored' that are older than ITEM_EXPIRY_DAYS (30)
       and have never been sent → status='expired' so they stop competing.

  2. ROTATE eve_prompts
       An eve_prompts item that was offered/sent to a partner more than
       PROMPT_ROTATION_DAYS (60) ago is made eligible again by deleting
       its outreach_log row for that partner. The item itself stays in the
       DB — this is just a cooldown reset, not a delete.

  3. DECAY transient interests
       Transient interests (type='transient') that have not been
       reinforced in TRANSIENT_DECAY_GRACE_DAYS (7) lose TRANSIENT_DECAY_RATE
       (5%) of their intensity. Below TRANSIENT_MIN_INTENSITY (0.1) they
       are pruned entirely. Stable interests are untouched.

  4. RESCORE
       Triggers a re-score of scored items so freshness-decay in relevance
       is baked in. (Calls relevance_score.py's scoring logic in-process.)

All actions are logged to curator_log with a reason string for later audit.

Usage:
    pulse_curator.py               # run the full curator pass
    pulse_curator.py --dry-run     # report what would happen, don't write
    pulse_curator.py --status      # print current DB + curator stats, no changes
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import relevance_score  # type: ignore  # noqa: E402

DB_PATH = pathlib.Path.home() / ".local" / "eve-tools" / "eve-knowledge.db"

ITEM_EXPIRY_DAYS = 30
PROMPT_ROTATION_DAYS = 60
TRANSIENT_DECAY_GRACE_DAYS = 7
TRANSIENT_DECAY_RATE = 0.05        # 5% per curator pass (multiplicative)
TRANSIENT_MIN_INTENSITY = 0.10     # below this, prune


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(t: dt.datetime) -> str:
    return t.isoformat(timespec="seconds")


def log(conn: sqlite3.Connection, interest_id: int | None, action: str,
        delta: float | None, reason: str) -> None:
    conn.execute(
        "INSERT INTO curator_log (decided_at, interest_id, action, delta, reason) "
        "VALUES (?, ?, ?, ?, ?)",
        (iso(now_utc()), interest_id, action, delta, reason),
    )


def expire_stale_items(conn: sqlite3.Connection, dry_run: bool) -> dict:
    """Expire scored RSS items older than ITEM_EXPIRY_DAYS that were never sent."""
    cutoff = iso(now_utc() - dt.timedelta(days=ITEM_EXPIRY_DAYS))
    cur = conn.cursor()
    # eve_prompts items are excluded — they don't expire by age, they rotate.
    candidates = cur.execute(
        "SELECT id, source, fetched_at FROM items "
        "WHERE status='scored' AND source <> 'eve_prompts' "
        "AND fetched_at < ? "
        "AND id NOT IN (SELECT item_id FROM outreach_log WHERE decision='sent')",
        (cutoff,),
    ).fetchall()
    ids = [r["id"] for r in candidates]
    if ids and not dry_run:
        placeholders = ",".join("?" * len(ids))
        cur.execute(
            f"UPDATE items SET status='expired' WHERE id IN ({placeholders})", ids
        )
        log(conn, None, "pruned", None,
            f"expired {len(ids)} stale items (>{ITEM_EXPIRY_DAYS}d, unsent)")
        conn.commit()
    return {"count": len(ids), "cutoff": cutoff,
            "sample_sources": sorted({r["source"] for r in candidates})[:5]}


def rotate_eve_prompts(conn: sqlite3.Connection, dry_run: bool) -> dict:
    """Reset outreach_log entries for eve_prompts offered > PROMPT_ROTATION_DAYS ago."""
    cutoff = iso(now_utc() - dt.timedelta(days=PROMPT_ROTATION_DAYS))
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT ol.id, ol.partner, ol.item_id "
        "FROM outreach_log ol "
        "JOIN items i ON i.id = ol.item_id "
        "WHERE i.source = 'eve_prompts' AND ol.decided_at < ?",
        (cutoff,),
    ).fetchall()
    log_ids = [r["id"] for r in rows]
    if log_ids and not dry_run:
        placeholders = ",".join("?" * len(log_ids))
        cur.execute(
            f"DELETE FROM outreach_log WHERE id IN ({placeholders})", log_ids
        )
        # Also restore the item status to scored so it's eligible again.
        item_ids = sorted({r["item_id"] for r in rows})
        ph = ",".join("?" * len(item_ids))
        cur.execute(
            f"UPDATE items SET status='scored' WHERE id IN ({ph}) AND status='sent'",
            item_ids,
        )
        log(conn, None, "promoted", None,
            f"rotated {len(log_ids)} eve_prompts offers (>{PROMPT_ROTATION_DAYS}d)")
        conn.commit()
    return {"count": len(log_ids),
            "partners": sorted({r["partner"] for r in rows})}


def decay_transient_interests(conn: sqlite3.Connection, dry_run: bool) -> dict:
    """Decay transient interests unreinforced for TRANSIENT_DECAY_GRACE_DAYS. Prune if below floor."""
    grace_cutoff = iso(now_utc() - dt.timedelta(days=TRANSIENT_DECAY_GRACE_DAYS))
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT id, name, intensity, last_reinforced, last_decayed FROM interests "
        "WHERE type='transient' "
        "AND (last_reinforced IS NULL OR last_reinforced < ?)",
        (grace_cutoff,),
    ).fetchall()

    decayed: list[dict] = []
    pruned: list[dict] = []
    for r in rows:
        new_intensity = round(r["intensity"] * (1.0 - TRANSIENT_DECAY_RATE), 4)
        if new_intensity < TRANSIENT_MIN_INTENSITY:
            pruned.append({"name": r["name"], "was": r["intensity"]})
            if not dry_run:
                cur.execute("DELETE FROM interests WHERE id=?", (r["id"],))
                log(conn, r["id"], "pruned", -r["intensity"],
                    f"transient below floor {TRANSIENT_MIN_INTENSITY}")
        else:
            decayed.append({"name": r["name"], "from": r["intensity"],
                            "to": new_intensity})
            if not dry_run:
                cur.execute(
                    "UPDATE interests SET intensity=?, last_decayed=? WHERE id=?",
                    (new_intensity, iso(now_utc()), r["id"]),
                )
                log(conn, r["id"], "decayed",
                    round(new_intensity - r["intensity"], 4),
                    f"unreinforced >{TRANSIENT_DECAY_GRACE_DAYS}d")
    if (decayed or pruned) and not dry_run:
        conn.commit()
    return {"decayed": decayed, "pruned": pruned}


def rescore_all(conn: sqlite3.Connection, dry_run: bool) -> dict:
    """Delegate to relevance_score.score_all() with rescore=True."""
    if dry_run:
        cur = conn.cursor()
        n = cur.execute("SELECT COUNT(*) FROM items WHERE status='scored'").fetchone()[0]
        return {"would_rescore": n, "dry_run": True}
    stats = relevance_score.score_all(conn, rescore=True)
    return stats


def run(dry_run: bool) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    report = {
        "at": iso(now_utc()),
        "dry_run": dry_run,
        "expire_stale_items": expire_stale_items(conn, dry_run),
        "rotate_eve_prompts": rotate_eve_prompts(conn, dry_run),
        "decay_transient_interests": decay_transient_interests(conn, dry_run),
        "rescore": rescore_all(conn, dry_run),
    }
    return report


def status() -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    counts = {}
    for s in ("new", "scored", "sent", "expired", "skipped"):
        counts[s] = cur.execute("SELECT COUNT(*) FROM items WHERE status=?", (s,)).fetchone()[0]
    n_interests = cur.execute("SELECT COUNT(*) FROM interests").fetchone()[0]
    n_transient = cur.execute("SELECT COUNT(*) FROM interests WHERE type='transient'").fetchone()[0]
    n_outreach = cur.execute("SELECT COUNT(*) FROM outreach_log").fetchone()[0]
    n_curator = cur.execute("SELECT COUNT(*) FROM curator_log").fetchone()[0]
    last_curator = cur.execute(
        "SELECT decided_at, action, reason FROM curator_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return {
        "items_by_status": counts,
        "interests_total": n_interests,
        "interests_transient": n_transient,
        "outreach_log_rows": n_outreach,
        "curator_log_rows": n_curator,
        "last_curator_action": dict(last_curator) if last_curator else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Eve's knowledge-DB curator (Phase 4).")
    ap.add_argument("--dry-run", action="store_true", help="Report only, no writes.")
    ap.add_argument("--status", action="store_true",
                    help="Print DB state and exit.")
    ap.add_argument("--json", action="store_true", help="JSON output.")
    args = ap.parse_args()

    if args.status:
        report = status()
    else:
        report = run(args.dry_run)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
