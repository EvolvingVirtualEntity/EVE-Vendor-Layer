#!/usr/bin/env python3
"""Phase 3 — Eve's outreach loop.

Runs on a cron every 15 min. For each partner:
  1. Calls the Phase-2 recommender (pulse_recommend.recommend).
  2. If decision=send and a candidate exists, composes a Chat message:
       - eve_prompts items with a [TOPIC] placeholder are SKIPPED from cron
         (they need Claude-in-the-loop adaptation — handled separately).
       - eve_prompts items without [TOPIC] are sent as-is — they are
         already Eve-voiced conversation seeds.
       - RSS/news items are sent with a short Eve-voiced lead-in + title + URL.
  3. Sends the message via Google Chat API using Eve's stored OAuth creds.
  4. Logs the outcome (sent | skipped | error) to the outreach_log table.

Respects quiet hours, daily cap, and per-partner family weighting — those
are all enforced in the recommender; this script just acts on its output.

Usage:
    pulse_outreach.py                         # all partners, live
    pulse_outreach.py --partner alex          # one partner
    pulse_outreach.py --dry-run               # don't send, don't log — just preview
    pulse_outreach.py --simulate-time TS      # pretend now=TS (ISO8601 w/ offset)

Environment:
    WORKSPACE_MCP_CREDENTIALS_DIR (optional) — where Eve's OAuth JSON lives.
    Default: ~/.google_workspace_mcp/credentials/
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import random
import sqlite3
import sys
import zoneinfo

import yaml

# Local imports — pulse_recommend is a sibling module
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import pulse_recommend  # type: ignore  # noqa: E402

EVE_TOOLS = pathlib.Path.home() / ".local" / "eve-tools"
DB_PATH = EVE_TOOLS / "eve-knowledge.db"
CADENCE_FILE = EVE_TOOLS / "cadence_model.yaml"
EVE_EMAIL = "Eve@labrasseurandreich.com"


def credentials_dir() -> pathlib.Path:
    env = os.getenv("WORKSPACE_MCP_CREDENTIALS_DIR")
    if env:
        return pathlib.Path(os.path.expanduser(env))
    return pathlib.Path.home() / ".google_workspace_mcp" / "credentials"


def load_eve_credentials():
    """Load Eve's OAuth credentials from the MCP credential store and refresh if expired."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = credentials_dir() / f"{EVE_EMAIL}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Eve's credentials not found at {path}. Ensure the Workspace MCP "
            "has been authorized for Eve@labrasseurandreich.com."
        )
    creds = Credentials.from_authorized_user_file(str(path))
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Write refreshed token back so the MCP sees it too.
            path.write_text(creds.to_json())
        else:
            raise RuntimeError(f"Credentials at {path} are invalid and cannot refresh.")
    return creds


def chat_service(creds):
    from googleapiclient.discovery import build
    return build("chat", "v1", credentials=creds, cache_discovery=False)


def send_chat_message(space_id: str, text: str) -> dict:
    """Send a text message to a Chat space. Returns the API response."""
    svc = chat_service(load_eve_credentials())
    return svc.spaces().messages().create(
        parent=space_id,
        body={"text": text},
    ).execute()


# ---------------------------------------------------------------------------
# Message composition
# ---------------------------------------------------------------------------


# Short, Eve-voiced lead-ins for news/RSS items. Picked at random so the
# channel doesn't feel formulaic. Keep them sentence-starters, not full
# templates — the item title should flow after.
NEWS_LEADINS = [
    "Ran across this and thought of you",
    "Small one for your morning",
    "Saw this and flagged it",
    "Worth a peek if you've got a minute",
    "Not urgent, just noteworthy",
    "This caught me",
]


def compose_for_eve_prompt(title: str, summary: str) -> str | None:
    """Return a ready-to-send message for an eve_prompts item, or None to skip.

    Prompts with an unfilled [TOPIC] placeholder need Claude-in-the-loop
    adaptation and are not fit for pure-cron send — we skip and leave them
    for a future adaptation step.
    """
    if "[TOPIC]" in title or "[TOPIC]" in summary:
        return None
    # Seeds are already in Eve's voice — send as-is, sign off lightly.
    return f"{title}\n\n— Eve"


def compose_for_news(title: str, url: str) -> str:
    lead = random.choice(NEWS_LEADINS)
    # Google Chat <URL|label> syntax renders the label as a clickable link and
    # hides the raw URL. Alex's preference (2026-04-22): keep the headline as
    # plain text for readability and put a one-word "→ read" link after it.
    return f"{lead}: {title} <{url}|→ read>\n\n— Eve"


def compose_message(candidate: dict) -> str | None:
    source = candidate.get("source") or ""
    if source == "eve_prompts":
        return compose_for_eve_prompt(candidate["title"], candidate.get("summary", ""))
    # Default: RSS / news item
    return compose_for_news(candidate["title"], candidate["url"])


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------


def log_outreach(conn: sqlite3.Connection, partner: str, item_id: int | None,
                 decision: str, reason: str, chat_message_id: str | None) -> None:
    conn.execute(
        "INSERT INTO outreach_log (partner, item_id, decided_at, decision, "
        "chat_message_id, reason) VALUES (?, ?, ?, ?, ?, ?)",
        (partner, item_id,
         dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
         decision, chat_message_id, reason),
    )
    conn.commit()


def mark_item_sent(conn: sqlite3.Connection, item_id: int) -> None:
    conn.execute("UPDATE items SET status='sent' WHERE id=?", (item_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# Main per-partner loop
# ---------------------------------------------------------------------------


def run_for_partner(conn: sqlite3.Connection, cadence: dict, partner: str,
                    when_local: dt.datetime, dry_run: bool) -> dict:
    rec = pulse_recommend.recommend(conn, cadence, partner, when_local)
    out = {
        "partner": partner,
        "now_local": rec["now_local"],
        "window": rec["window"],
        "recommender_decision": rec["decision"],
        "recommender_reason": rec["reason"],
        "action": "no-op",
        "message_preview": None,
        "chat_message_id": None,
        "candidate_id": None,
    }

    if rec["decision"] != "send" or not rec["candidate"]:
        out["action"] = "skip"
        return out

    candidate = rec["candidate"]
    out["candidate_id"] = candidate["id"]

    space_id = (cadence["partners"][partner] or {}).get("chat_space_id")
    if not space_id:
        reason = "no_space_configured"
        out["action"] = "skip"
        out["recommender_reason"] = reason
        if not dry_run:
            log_outreach(conn, partner, candidate["id"], "skipped", reason, None)
        return out

    message = compose_message(candidate)
    if message is None:
        reason = "needs_adaptation (contains [TOPIC] placeholder)"
        out["action"] = "skip"
        out["recommender_reason"] = reason
        if not dry_run:
            log_outreach(conn, partner, candidate["id"], "skipped", reason, None)
        return out

    out["message_preview"] = message

    if dry_run:
        out["action"] = "would_send"
        return out

    try:
        resp = send_chat_message(space_id, message)
    except Exception as e:
        reason = f"send_failed: {type(e).__name__}: {e}"
        out["action"] = "error"
        out["recommender_reason"] = reason
        log_outreach(conn, partner, candidate["id"], "skipped", reason, None)
        return out

    chat_message_id = resp.get("name")
    out["chat_message_id"] = chat_message_id
    out["action"] = "sent"
    log_outreach(conn, partner, candidate["id"], "sent",
                 f"match: {candidate['source']} eff={candidate['effective_score']:.3f}",
                 chat_message_id)
    mark_item_sent(conn, candidate["id"])
    return out


def run(partners: list[str], dry_run: bool, simulate_time: str | None) -> list[dict]:
    cadence = yaml.safe_load(CADENCE_FILE.read_text(encoding="utf-8"))
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    results = []
    for p in partners:
        if p not in cadence["partners"]:
            results.append({"partner": p, "action": "error",
                            "recommender_reason": "unknown_partner"})
            continue
        tz = zoneinfo.ZoneInfo(cadence["partners"][p]["timezone"])
        if simulate_time:
            when = dt.datetime.fromisoformat(simulate_time).astimezone(tz)
        else:
            when = dt.datetime.now(tz)
        results.append(run_for_partner(conn, cadence, p, when, dry_run))
    return results


def render_pretty(results: list[dict]) -> str:
    L: list[str] = []
    for r in results:
        L.append(f"# partner: {r['partner']}  now_local: {r.get('now_local','?')}  "
                 f"window: {r.get('window') or '—'}")
        L.append(f"# action: {r['action']}  reason: {r.get('recommender_reason') or '—'}")
        if r.get("message_preview"):
            L.append("")
            L.append("  " + r["message_preview"].replace("\n", "\n  "))
            L.append("")
        if r.get("chat_message_id"):
            L.append(f"# chat_message_id: {r['chat_message_id']}")
        L.append("")
    return "\n".join(L).rstrip()


def main() -> int:
    ap = argparse.ArgumentParser(description="Eve's outreach loop (Phase 3).")
    ap.add_argument("--partner", choices=["alex", "shawn"],
                    help="Only run for this partner. Default: all.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Don't send or log — just preview.")
    ap.add_argument("--simulate-time",
                    help="ISO8601 timestamp to treat as 'now' (e.g. for testing).")
    ap.add_argument("--json", action="store_true", help="JSON output.")
    args = ap.parse_args()

    partners = [args.partner] if args.partner else ["alex", "shawn"]
    results = run(partners, args.dry_run, args.simulate_time)

    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        print(render_pretty(results))

    any_error = any(r.get("action") == "error" for r in results)
    return 1 if any_error else 0


if __name__ == "__main__":
    sys.exit(main())
