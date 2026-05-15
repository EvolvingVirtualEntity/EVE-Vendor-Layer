#!/usr/bin/env python3
"""Send an audio file (or any file) as an attachment to a Google Chat space.

Uses Eve's OAuth credentials (loaded via pulse_outreach.load_eve_credentials).
Two-step flow per the Chat API:
  1. POST to /upload/v1/{space}/attachments:upload — returns attachmentDataRef
  2. POST to /v1/{space}/messages with attachment[].attachmentDataRef set

Usage:
    chat_send_audio.py --space spaces/XXXX --file /path/to/audio.mp3 \\
        [--text "optional text body"] [--content-type audio/mpeg]
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import pathlib
import sys
import urllib.request

sys.path.insert(0, "/home/eve/.local/eve-tools")
from pulse_outreach import load_eve_credentials  # noqa: E402


def upload_attachment(token: str, space_id: str, file_path: pathlib.Path,
                      content_type: str) -> dict:
    boundary = "eve-attach-boundary"
    metadata = json.dumps({"filename": file_path.name}).encode()
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        b"Content-Type: application/json; charset=UTF-8\r\n\r\n",
        metadata,
        f"\r\n--{boundary}\r\n".encode(),
        f"Content-Type: {content_type}\r\n\r\n".encode(),
        file_path.read_bytes(),
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    url = f"https://chat.googleapis.com/upload/v1/{space_id}/attachments:upload?uploadType=multipart"
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        sys.stderr.write(f"upload HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}\n")
        raise


def send_message_with_attachment(token: str, space_id: str, text: str,
                                 attachment_ref: dict,
                                 content_name: str, content_type: str) -> dict:
    body = {
        "text": text,
        "attachment": [{
            "contentName": content_name,
            "contentType": content_type,
            "attachmentDataRef": attachment_ref,
        }],
    }
    url = f"https://chat.googleapis.com/v1/{space_id}/messages"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        sys.stderr.write(f"message HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}\n")
        raise


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--space", required=True, help="e.g. spaces/AAQA5xkWJq0")
    ap.add_argument("--file", required=True)
    ap.add_argument("--text", default="")
    ap.add_argument("--content-type", default=None,
                    help="MIME type (auto-detected by extension if omitted)")
    args = ap.parse_args()

    file_path = pathlib.Path(args.file).expanduser().resolve()
    if not file_path.exists():
        sys.exit(f"file not found: {file_path}")

    content_type = args.content_type or mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"

    creds = load_eve_credentials()
    token = creds.token

    print(f"uploading {file_path.name} ({content_type}) to {args.space}", file=sys.stderr)
    up = upload_attachment(token, args.space, file_path, content_type)
    ref = up.get("attachmentDataRef")
    if not ref:
        sys.exit(f"no attachmentDataRef in response: {up}")
    print(f"got attachmentDataRef", file=sys.stderr)

    msg = send_message_with_attachment(
        token, args.space, args.text, ref,
        content_name=file_path.name, content_type=content_type,
    )
    print(msg.get("name", json.dumps(msg)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
