#!/usr/bin/env python3
"""Plaud → vault ingest pipeline.

For each new Plaud recording:
  list  → download .ogg → Whisper transcribe → Ollama analyze → write vault note

State (which file_ids are already processed) lives in
~/.local/eve-tools/plaud-state/processed.json so reruns are idempotent.

Designed to be safe to invoke from cron; logs go to cron-plaud.log.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

# allow `from plaud_client import ...` when this script is run from cron
sys.path.insert(0, str(Path(__file__).parent))
from plaud_client import PlaudClient  # noqa: E402

CACHE = Path.home() / ".local" / "eve-tools" / "plaud-cache"
STATE = Path.home() / ".local" / "eve-tools" / "plaud-state"
VAULT_INBOX = Path.home() / "EveBrain" / "00-Inbox" / "Plaud"
WHISPER_PY = Path.home() / ".local" / "eve-tools" / "whisper-venv" / "bin" / "python"
TRANSCRIBE_SCRIPT = Path.home() / ".local" / "eve-tools" / "transcribe.py"
ASK_LOCAL = Path.home() / ".local" / "eve-tools" / "ask_local.py"

STATE_FILE = STATE / "processed.json"

# Only audio analysis should leave the box. The transcript itself never goes
# to a 3rd-party LLM in v1 — Ollama is local.
ANALYSIS_PROMPT = """You analyze short voice memos for a real-estate investment partnership (LaBrasseur and Reich, Illinois LLC). The speaker is one of the partners (Alex or Shawn). Read the transcript and reply in compact JSON with these keys:

  summary       : one sentence, what was said
  action_items  : list of strings (imperative voice, may be empty)
  entities      : list of strings — properties, people, companies, places mentioned
  topic         : short tag like "deal-flow", "operations", "personal", "test", or "unclear"

Reply with JSON only, no prose. If the transcript is gibberish or sub-3 words, set topic="unclear" and leave other lists empty.

Transcript:
"""


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"processed_ids": [], "last_run": None}


def save_state(state: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def transcribe(audio: Path, model: str = "large-v3") -> str:
    """Run faster-whisper via the existing transcribe.py helper."""
    proc = subprocess.run(
        [str(WHISPER_PY), str(TRANSCRIBE_SCRIPT), str(audio), "--model", model],
        capture_output=True, text=True, check=True,
    )
    return proc.stdout.strip()


def analyze(transcript: str) -> dict:
    """Run a local Ollama prompt to extract summary/actions/entities. Best-effort."""
    if not transcript or len(transcript.split()) < 3:
        return {"summary": "", "action_items": [], "entities": [], "topic": "unclear"}
    try:
        proc = subprocess.run(
            [str(ASK_LOCAL), ANALYSIS_PROMPT + transcript, "--json", "--no-stream"],
            capture_output=True, text=True, timeout=120, check=True,
        )
        return json.loads(proc.stdout.strip())
    except (subprocess.SubprocessError, json.JSONDecodeError) as e:
        return {"summary": "", "action_items": [], "entities": [], "topic": "unclear",
                "_analyze_error": str(e)}


def slugify(s: str) -> str:
    keep = "abcdefghijklmnopqrstuvwxyz0123456789-_"
    return "".join(c if c.lower() in keep else "-" for c in s).strip("-")[:60]


def vault_note_path(rec: dict) -> Path:
    captured = dt.datetime.fromtimestamp(rec["start_time"] / 1000)
    return VAULT_INBOX / f"{captured:%Y-%m-%d-%H%M}-{rec['id'][:8]}.md"


def write_note(rec: dict, transcript: str, analysis: dict) -> Path:
    captured = dt.datetime.fromtimestamp(rec["start_time"] / 1000)
    note_path = vault_note_path(rec)
    note_path.parent.mkdir(parents=True, exist_ok=True)

    body = f"""---
type: plaud-recording
date: {captured:%Y-%m-%d}
captured_at: {captured.isoformat()}
duration_sec: {rec['duration'] / 1000:.0f}
file_id: {rec['id']}
plaud_serial: {rec.get('serial_number', '')}
ingested_at: {dt.datetime.now().isoformat(timespec='seconds')}
markmemo: {str(rec.get('is_markmemo', False)).lower()}
topic: {analysis.get('topic', 'unknown')}
tags:
  - plaud
  - "{captured:%Y-%m-%d}"
---

# Plaud — {captured:%Y-%m-%d %H:%M} ({rec['duration']/1000:.0f}s)

## Eve's analysis
**Summary:** {analysis.get('summary') or '_(none)_'}

**Action items:**
"""
    for ai in analysis.get("action_items") or []:
        body += f"- {ai}\n"
    if not analysis.get("action_items"):
        body += "- _(none)_\n"
    body += "\n**Entities:** "
    body += ", ".join(analysis.get("entities") or []) or "_(none)_"
    body += "\n\n## Transcript\n\n"
    body += transcript or "_(empty)_"
    body += "\n"

    note_path.write_text(body)
    return note_path


def ingest_one(client: PlaudClient, rec: dict, *, model: str) -> Path:
    fid = rec["id"]
    audio_path = CACHE / f"{fid}.ogg"
    if not audio_path.exists():
        client.download_audio(fid, audio_path)
    transcript = transcribe(audio_path, model=model)
    analysis = analyze(transcript)
    return write_note(rec, transcript, analysis)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20, help="how many recent recordings to consider")
    ap.add_argument("--model", default="large-v3", help="Whisper model")
    ap.add_argument("--force", action="store_true", help="re-ingest even if already processed")
    ap.add_argument("--dry-run", action="store_true", help="list what would be ingested")
    args = ap.parse_args()

    client = PlaudClient.from_env()
    if client.ensure_fresh_wt():
        print(f"[{dt.datetime.now().isoformat(timespec='seconds')}] "
              f"WT was stale — refreshed, new remaining "
              f"{client.wt_seconds_remaining()/3600:.1f}h")
    state = load_state()
    seen = set(state["processed_ids"]) if not args.force else set()

    files = client.list_recordings(limit=args.limit)
    new = [f for f in files if f["id"] not in seen]

    print(f"[{dt.datetime.now().isoformat(timespec='seconds')}] "
          f"{len(files)} total, {len(new)} new, "
          f"WT remaining {client.wt_seconds_remaining()/3600:.1f}h")

    if args.dry_run:
        for rec in new:
            print(f"  WOULD ingest {rec['id'][:8]}  {rec['filename']}  ({rec['duration']/1000:.0f}s)")
        return 0

    for rec in new:
        try:
            note = ingest_one(client, rec, model=args.model)
            state["processed_ids"].append(rec["id"])
            print(f"  ✓ {rec['id'][:8]}  {rec['filename']}  → {note.relative_to(Path.home())}")
            save_state(state)  # save after each so a crash doesn't lose progress
        except Exception as e:
            print(f"  ✗ {rec['id'][:8]}  {rec['filename']}  FAILED: {e}", file=sys.stderr)

    state["last_run"] = dt.datetime.now().isoformat(timespec="seconds")
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
