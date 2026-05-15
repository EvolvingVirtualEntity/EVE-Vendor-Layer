#!/usr/bin/env python3
"""Daily brief — calendar + mail + birthdays for Alex (work + personal) + Eve.

Posts to Alex's WhatsApp DM at 7 AM and 2 PM Pacific via cron (switched
from Google Chat 2026-05-06 per Alex). Falls back to Chat if WhatsApp
bridge is unreachable.

Crontab entry (Pacific local time on the eve box):
    0 7,14 * * * /usr/bin/python3 /home/eve/.local/eve-tools/daily_brief.py \\
        >> /home/eve/.local/eve-tools/cron-brief.log 2>&1
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import sys
import urllib.request
import zoneinfo

sys.path.insert(0, "/home/eve/.local/eve-tools")
from pulse_outreach import credentials_dir, send_chat_message  # noqa: E402

# WhatsApp destination — Alex's DM JID. Bridge accepts either phone or JID.
ALEX_WA_JID = "133324425179144@lid"
WA_BRIDGE_URL = "http://127.0.0.1:8080/api/send"
# Legacy Chat fallback target if WA send fails
SPACE = "spaces/AAQA5xkWJq0"  # EvE chat
# Per-account scope. Alex's personal account is calendar-only by design
# (Gmail/People scopes intentionally not granted — confirmed 2026-05-02).
ACCOUNTS = [
    ("alexander.reich@labrasseurandreich.com", "Alex (work)", {"calendar", "mail"}),
    ("alexander.reich.la@gmail.com", "Alex (personal)", {"calendar"}),
    ("Eve@labrasseurandreich.com", "Eve", {"calendar", "mail"}),
]
PEOPLE_DIR = pathlib.Path.home() / "EveBrain" / "03-People"
PT = zoneinfo.ZoneInfo("America/Los_Angeles")

MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"], start=1)}

BIRTHDAY_RE = re.compile(r"\*\*Birthday:\s*([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})\*\*")

# Google Contacts birthdays surface on the primary calendar as all-day events
# with titles like "Mama's birthday", "Birthday Selah Zen Love", or the
# generic Facebook-style "Happy birthday!". Detect those so we can consolidate
# them into the birthdays bucket instead of letting them double up under the
# calendar section.
BIRTHDAY_EVENT_PATTERNS = (
    re.compile(r"^(.+?)'s\s+birthday\s*$", re.IGNORECASE),
    re.compile(r"^Birthday\s+(.+)$", re.IGNORECASE),
)


def parse_birthday_event(summary: str):
    """Return the celebrant name if summary is a birthday event, else None.

    Generic 'Happy birthday!' (no name) returns the sentinel "_anon_" so the
    caller can still filter the event out of the calendar block without
    inventing a celebrant.
    """
    s = (summary or "").strip()
    if not s:
        return None
    for pat in BIRTHDAY_EVENT_PATTERNS:
        m = pat.match(s)
        if m:
            return m.group(1).strip()
    if s.lower().startswith("happy birthday"):
        return "_anon_"
    return None

AUTOMATED_TOKENS = (
    "no.reply", "noreply", "no-reply", "donotreply",
    "newsletter", "alerts@", "notifications@", "notify@",
    "mailer@", "billing@", "auto@", "unsubscribe@",
    "@jangomail", "resourcesforclients",
)


def load_creds(email: str):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = credentials_dir() / f"{email}.json"
    if not path.exists():
        return None, f"no token file"
    creds = Credentials.from_authorized_user_file(str(path))
    if creds.valid:
        return creds, None
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            path.write_text(creds.to_json())
            return creds, None
        except Exception as exc:
            return None, f"refresh failed ({exc.__class__.__name__})"
    return None, "invalid creds, no refresh token"


def get_events_today(email: str):
    creds, err = load_creds(email)
    if err:
        return None, err
    from googleapiclient.discovery import build

    now = dt.datetime.now(PT)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + dt.timedelta(days=1)
    cal = build("calendar", "v3", credentials=creds, cache_discovery=False)
    try:
        resp = cal.events().list(
            calendarId="primary",
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=25,
        ).execute()
        return resp.get("items", []), None
    except Exception as exc:
        return None, f"calendar error: {exc.__class__.__name__}"


def get_unread_mail(email: str):
    creds, err = load_creds(email)
    if err:
        return None, err
    from googleapiclient.discovery import build

    gm = build("gmail", "v1", credentials=creds, cache_discovery=False)
    try:
        resp = gm.users().messages().list(
            userId="me",
            q="is:unread newer_than:1d -category:promotions -category:social",
            maxResults=15,
        ).execute()
    except Exception as exc:
        return None, f"gmail error: {exc.__class__.__name__}"
    out = []
    for m in resp.get("messages", []):
        try:
            mr = gm.users().messages().get(
                userId="me", id=m["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
        except Exception:
            continue
        headers = {h["name"]: h["value"] for h in mr.get("payload", {}).get("headers", [])}
        out.append({
            "from": headers.get("From", ""),
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
        })
    return out, None


def vault_birthdays_today():
    """Read 03-People/*.md and surface today's birthdays."""
    today = dt.datetime.now(PT)
    md = (today.month, today.day)
    matches = []
    if not PEOPLE_DIR.exists():
        return matches
    for f in PEOPLE_DIR.glob("*.md"):
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        m = BIRTHDAY_RE.search(text)
        if not m:
            continue
        mon_str, day_str, year_str = m.groups()
        if mon_str not in MONTHS:
            continue
        mm = MONTHS[mon_str]
        dd = int(day_str)
        yy = int(year_str)
        if (mm, dd) == md:
            age = today.year - yy
            matches.append((f.stem, age, yy))
    return matches


def is_human(from_field: str) -> bool:
    f = from_field.lower()
    return not any(tok in f for tok in AUTOMATED_TOKENS)


def display_sender(from_field: str) -> str:
    return re.sub(r"\s*<.*?>", "", from_field).strip().strip('"')


def format_event_time(ev) -> str:
    start = ev.get("start", {})
    t = start.get("dateTime") or start.get("date") or ""
    if "T" not in t:
        return "all-day"
    try:
        return dt.datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(PT).strftime("%-I:%M %p")
    except Exception:
        return t


def merge_birthdays(calendars, vault_birthdays):
    """Combine vault- and calendar-detected birthdays and strip birthday
    events out of the calendar lists so they don't appear in both sections.

    Returns (birthdays, filtered_calendars). `birthdays` is a list of
    (name, age_or_None, year_or_None). Vault entries (with age) take
    precedence over calendar entries (no age) for the same name.
    """
    seen = {}
    for name, age, yr in vault_birthdays:
        seen[name.lower()] = (name, age, yr)
    filtered = []
    for entry in calendars:
        label, items, err = entry
        keep = []
        for ev in items:
            celebrant = parse_birthday_event(ev.get("summary", ""))
            if celebrant is None:
                keep.append(ev)
                continue
            # Birthday event — drop from calendar section regardless.
            if celebrant != "_anon_" and celebrant.lower() not in seen:
                seen[celebrant.lower()] = (celebrant, None, None)
        filtered.append((label, keep, err))
    return list(seen.values()), filtered


def format_brief(now_pt, calendars, mails, birthdays) -> str:
    out = [f"*Daily brief — {now_pt.strftime('%a %b %-d, %-I:%M %p')} Pacific*", ""]

    birthdays, calendars = merge_birthdays(calendars, birthdays)

    # Birthdays
    out.append("*birthdays today*")
    if birthdays:
        for name, age, _yr in birthdays:
            if age is not None:
                out.append(f"- *{name}* — turning {age}")
            else:
                out.append(f"- *{name}*")
    else:
        out.append("- none")
    out.append("")

    # Calendar
    out.append("*today's calendar (Pacific)*")
    cal_lines = []
    cal_errs = []
    for label, items, err in calendars:
        if err:
            cal_errs.append(f"- _couldn't read {label} calendar: {err}_")
            continue
        for ev in items:
            cal_lines.append(f"- *{format_event_time(ev)}* — {ev.get('summary', '(no title)')} _({label})_")
    if cal_lines:
        out.extend(cal_lines)
    elif not cal_errs:
        out.append("- nothing scheduled")
    out.extend(cal_errs)
    out.append("")

    # Mail
    out.append("*mail worth surfacing (last 24h, unread, humans only)*")
    mail_lines = []
    mail_errs = []
    skipped = 0
    for label, items, err in mails:
        if err:
            mail_errs.append(f"- _couldn't read {label} mail: {err}_")
            continue
        for m in items:
            if not is_human(m["from"]):
                skipped += 1
                continue
            mail_lines.append(f"- *{display_sender(m['from'])}* → \"{m['subject']}\" _({label})_")
    if mail_lines:
        out.extend(mail_lines)
    elif not mail_errs:
        out.append("- nothing pressing")
    if skipped:
        out.append(f"- _(skipped {skipped} automated)_")
    out.extend(mail_errs)

    return "\n".join(out).rstrip() + "\n\n— Eve"


def main():
    now = dt.datetime.now(PT)
    calendars, mails = [], []
    for email, label, scopes in ACCOUNTS:
        if "calendar" in scopes:
            ev, ev_err = get_events_today(email)
            calendars.append((label, ev or [], ev_err))
        if "mail" in scopes:
            ml, ml_err = get_unread_mail(email)
            mails.append((label, ml or [], ml_err))
    birthdays = vault_birthdays_today()

    brief = format_brief(now, calendars, mails, birthdays)
    print(brief)
    print("---")
    print(f"[{now.isoformat()}] posting to WhatsApp {ALEX_WA_JID}")
    if send_whatsapp(ALEX_WA_JID, brief):
        print("[ok] sent via WhatsApp")
        return
    print(f"[warn] WhatsApp send failed, falling back to Chat {SPACE}")
    send_chat_message(SPACE, brief)
    print("[ok] sent via Chat fallback")


def send_whatsapp(recipient: str, message: str) -> bool:
    """POST to the local whatsmeow bridge. Returns True on success."""
    body = json.dumps({"recipient": recipient, "message": message}).encode()
    req = urllib.request.Request(
        WA_BRIDGE_URL, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        return bool(data.get("success"))
    except Exception as exc:
        print(f"[warn] WA bridge POST failed: {exc}")
        return False


if __name__ == "__main__":
    main()
