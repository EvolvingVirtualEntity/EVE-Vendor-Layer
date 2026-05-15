#!/usr/bin/env python3
"""Decide what (if anything) Eve should send to a partner right now.

Combines the cadence model (`cadence_model.yaml`) with the scored items in
`eve-knowledge.db`. Returns the highest-scoring item that:
  (a) Falls within the partner's current active window
  (b) Matches one of the families that window leans toward
  (c) Scores at or above `min_relevance_to_send`
  (d) Has not already been sent to this partner

Usage:
  pulse_recommend.py --partner alex
  pulse_recommend.py --partner shawn --json
  pulse_recommend.py --partner alex --time "2026-04-22T08:30:00-07:00"   # simulate

This is the *recommender* only — Phase 2. It does NOT send anything; that's
Phase 3's job. The output here can be inspected by hand or piped into a
prompt for Eve to gate the actual send.
"""

import argparse
import datetime as dt
import json
import pathlib
import sqlite3
import sys
import zoneinfo

import yaml

EVE_TOOLS = pathlib.Path.home() / ".local" / "eve-tools"
DB_PATH = EVE_TOOLS / "eve-knowledge.db"
CADENCE_FILE = EVE_TOOLS / "cadence_model.yaml"


def load_cadence() -> dict:
    return yaml.safe_load(CADENCE_FILE.read_text(encoding="utf-8"))


def find_active_window(cadence: dict, partner: str, when_local: dt.datetime) -> dict | None:
    p = cadence["partners"][partner]
    h = when_local.hour
    if h in (p.get("quiet_hours") or []):
        return None
    for w in p.get("windows", []):
        if w["start"] <= h < w["end"]:
            return w
    return None


def families_to_tags(cadence: dict, families: list[str]) -> set[str]:
    out: set[str] = set()
    fam_map = cadence.get("families", {})
    for f in families:
        out.update(fam_map.get(f, []))
    return out


def already_sent_today(conn: sqlite3.Connection, partner: str, when_local: dt.datetime) -> int:
    cur = conn.cursor()
    # Items sent today (partner-local-day). Stored times are UTC; do a window check.
    start_local = when_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + dt.timedelta(days=1)
    start_utc = start_local.astimezone(dt.timezone.utc).isoformat()
    end_utc = end_local.astimezone(dt.timezone.utc).isoformat()
    return cur.execute(
        "SELECT COUNT(*) FROM outreach_log WHERE partner=? AND decision='sent' "
        "AND decided_at >= ? AND decided_at < ?",
        (partner, start_utc, end_utc),
    ).fetchone()[0]


def already_offered_to_partner(conn: sqlite3.Connection, partner: str, item_id: int) -> bool:
    cur = conn.cursor()
    return cur.execute(
        "SELECT 1 FROM outreach_log WHERE partner=? AND item_id=? LIMIT 1",
        (partner, item_id),
    ).fetchone() is not None


def recommend(conn: sqlite3.Connection, cadence: dict, partner: str,
              when_local: dt.datetime) -> dict:
    out = {
        "partner": partner,
        "now_local": when_local.isoformat(timespec="minutes"),
        "decision": "skip",
        "reason": None,
        "window": None,
        "candidate": None,
    }

    window = find_active_window(cadence, partner, when_local)
    if window is None:
        out["reason"] = "outside_active_window"
        return out
    out["window"] = window["name"]

    cap = cadence["defaults"].get("per_partner_max_per_day", 5)
    sent_today = already_sent_today(conn, partner, when_local)
    if sent_today >= cap:
        out["reason"] = f"daily_cap_reached ({sent_today}/{cap})"
        return out

    min_score = cadence["defaults"].get("min_relevance_to_send", 0.55)
    family_tags = families_to_tags(cadence, window.get("families", []))
    if not family_tags:
        out["reason"] = "no_families_for_window"
        return out

    # Per-partner family weights (default 1.0 for any unspecified family).
    partner_weights = cadence["partners"][partner].get("family_weights", {}) or {}

    # Pull a generous candidate set — we'll re-rank by effective_score below.
    # Need a wider net than `min_score` because per-partner weights can boost
    # a base-0.50 item up past 0.55 (or push a 0.80 item below).
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT id, relevance_score, source, title, summary, url, tags, published_at "
        "FROM items WHERE status='scored' AND relevance_score >= ? "
        "ORDER BY relevance_score DESC LIMIT 200",
        (min_score * 0.5,),  # generous lower bound; weights might lift these
    ).fetchall()

    candidates: list[dict] = []
    for r in rows:
        item_tags = set(json.loads(r["tags"] or "[]"))
        matched_fams = sorted({
            fname for fname in window["families"]
            if set(cadence["families"].get(fname, [])) & item_tags
        })
        if not matched_fams:
            continue
        if already_offered_to_partner(conn, partner, r["id"]):
            continue
        # Per-partner family weight = max weight across all matched families.
        # (max, not sum, so a single very-relevant family doesn't get drowned.)
        weight = max((partner_weights.get(f, 1.0) for f in matched_fams), default=1.0)
        effective = round(r["relevance_score"] * weight, 4)
        if effective < min_score:
            continue
        candidates.append({
            "row": r,
            "item_tags": sorted(item_tags),
            "matched_fams": matched_fams,
            "weight": weight,
            "effective_score": effective,
        })

    if not candidates:
        out["reason"] = (f"no_eligible_item (window={window['name']}, "
                        f"families={window['families']}, "
                        f"min_score={min_score}, "
                        f"weights={partner_weights or 'default 1.0'})")
        return out

    # Sort by effective score (per-partner weighted), descending.
    candidates.sort(key=lambda c: c["effective_score"], reverse=True)
    top = candidates[0]
    r = top["row"]
    out["decision"] = "send"
    out["reason"] = "match"
    out["candidate"] = {
        "id": r["id"],
        "base_score": r["relevance_score"],
        "partner_weight": top["weight"],
        "effective_score": top["effective_score"],
        "source": r["source"],
        "title": r["title"],
        "url": r["url"],
        "summary": (r["summary"] or "")[:300],
        "published_at": r["published_at"],
        "tags": top["item_tags"],
        "matched_via_families": top["matched_fams"],
    }
    return out


def render_pretty(rec: dict) -> str:
    L: list[str] = []
    L.append(f"# partner: {rec['partner']}  local: {rec['now_local']}")
    L.append(f"# window: {rec['window'] or '(none — quiet)'}")
    L.append(f"# decision: {rec['decision']}")
    if rec["reason"]:
        L.append(f"# reason: {rec['reason']}")
    if rec["candidate"]:
        c = rec["candidate"]
        L.append("")
        L.append(f"  effective score: {c['effective_score']:.3f}  "
                 f"(base {c['base_score']:.3f} × partner-weight {c['partner_weight']:.2f})")
        L.append(f"  source: {c['source']}")
        L.append(f"  title: {c['title']}")
        L.append(f"  url: {c['url']}")
        L.append(f"  published: {c['published_at']}")
        L.append(f"  matched families: {', '.join(c['matched_via_families'])}")
        if c["summary"]:
            L.append(f"  summary: {c['summary']}")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="Recommend whether/what Eve should send right now.")
    ap.add_argument("--partner", required=True, choices=["alex", "shawn"])
    ap.add_argument("--time", help="ISO timestamp (with offset) to simulate. Defaults to now.")
    ap.add_argument("--json", action="store_true", help="JSON output instead of pretty.")
    args = ap.parse_args()

    cadence = load_cadence()
    if args.partner not in cadence["partners"]:
        sys.exit(f"error: partner '{args.partner}' not in cadence_model.yaml")

    tz = zoneinfo.ZoneInfo(cadence["partners"][args.partner]["timezone"])
    if args.time:
        when = dt.datetime.fromisoformat(args.time).astimezone(tz)
    else:
        when = dt.datetime.now(tz)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rec = recommend(conn, cadence, args.partner, when)

    if args.json:
        print(json.dumps(rec, indent=2, default=str))
    else:
        print(render_pretty(rec))
    return 0


if __name__ == "__main__":
    sys.exit(main())
