#!/usr/bin/env python3
"""Eve nightly encrypted backup to Google Drive.

Threat model: protect against fire / theft / total Eve-box loss. The encrypted
archive lives in Eve's own Drive folder. Decryption requires the symmetric
passphrase stored at ~/.config/eve/backup-passphrase (chmod 600). Alex should
ALSO store this passphrase in his password manager, otherwise a recovered
backup is unrecoverable without the box.

Pipeline:
  1. tar -cf the source directories with exclusions
  2. zstd-compress
  3. gpg --symmetric --cipher-algo AES256 with the passphrase
  4. Upload to Drive folder "Eve Backups (encrypted)"
  5. Prune to KEEP_N most-recent backups in that folder

Each archive named: eve-backup-<YYYY-MM-DD>.tar.gz.gpg

Usage:
  backup_to_drive.py                  # nightly run
  backup_to_drive.py --dry-run        # don't upload, don't prune
  backup_to_drive.py --keep 14        # keep 14 days instead of 30
"""

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

EVE_TOOLS = pathlib.Path.home() / ".local" / "eve-tools"
LOG = EVE_TOOLS / "cron-backup.log"
PASSPHRASE_FILE = pathlib.Path.home() / ".config" / "eve" / "backup-passphrase"
CREDS_FILE = pathlib.Path.home() / ".google_workspace_mcp" / "credentials" / "Eve@labrasseurandreich.com.json"
BACKUP_FOLDER_ID = "1sCrGG5S9M_f07FOpaGdYv8lQ_Y4Q_-fd"   # "Eve Backups (encrypted)"
KEEP_N_DEFAULT = 30

SOURCES: list[tuple[str, str]] = [
    # (label, absolute path)
    ("EveBrain", "/home/eve/EveBrain"),
    ("eve-tools", "/home/eve/.local/eve-tools"),
    ("config-eve", "/home/eve/.config/eve"),
    ("claude-memory", "/home/eve/.claude/projects/-home-eve-EveBrain/memory"),
    ("pm2-dump", "/home/eve/.pm2/dump.pm2"),
    ("remote", "/home/eve/remote"),
    ("tools", "/home/eve/tools"),
]

EXCLUDES = [
    "*-venv",
    "node_modules",
    "__pycache__",
    ".git",
    "*.pyc",
    ".cache",
]


def log(msg: str) -> None:
    line = f"[{dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}] {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def get_access_token() -> str:
    creds = json.loads(CREDS_FILE.read_text())
    payload = {
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": creds["refresh_token"],
        "grant_type": "refresh_token",
    }
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=urllib.parse.urlencode(payload).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=15).read())["access_token"]


def tar_and_encrypt(out_path: pathlib.Path) -> None:
    """tar -czf | gpg --symmetric → out_path"""
    if not PASSPHRASE_FILE.exists():
        sys.exit(f"error: passphrase missing at {PASSPHRASE_FILE}")

    # Build tar args
    tar_cmd = ["tar", "--create", "--gzip"]
    for ex in EXCLUDES:
        tar_cmd += [f"--exclude={ex}"]
    for label, path in SOURCES:
        if pathlib.Path(path).exists():
            tar_cmd += ["-C", str(pathlib.Path(path).parent), pathlib.Path(path).name]
        else:
            log(f"WARN: source missing — skipping {path}")

    gpg_cmd = [
        "gpg", "--symmetric", "--batch", "--yes",
        "--cipher-algo", "AES256",
        "--passphrase-file", str(PASSPHRASE_FILE),
        "--output", str(out_path),
    ]

    log(f"  tar: {' '.join(tar_cmd[:5])} ... ({len(SOURCES)} sources)")
    log(f"  gpg: AES256 symmetric → {out_path.name}")

    tar = subprocess.Popen(tar_cmd, stdout=subprocess.PIPE)
    gpg = subprocess.Popen(gpg_cmd, stdin=tar.stdout, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE)
    tar.stdout.close()  # allow tar to receive SIGPIPE
    _, gpg_err = gpg.communicate()
    tar_rc = tar.wait()
    if tar_rc not in (0, 1):  # tar exits 1 for "some files changed during read", which is fine
        sys.exit(f"error: tar exited {tar_rc}")
    if gpg.returncode != 0:
        sys.stderr.write(gpg_err.decode("utf-8", errors="replace"))
        sys.exit(f"error: gpg exited {gpg.returncode}")


def upload_to_drive(local_path: pathlib.Path, access_token: str) -> dict:
    """Multipart upload to Drive."""
    boundary = "eve-backup-boundary"
    metadata = json.dumps({
        "name": local_path.name,
        "parents": [BACKUP_FOLDER_ID],
    }).encode()
    body_parts = [
        f"--{boundary}\r\n".encode(),
        b"Content-Type: application/json; charset=UTF-8\r\n\r\n",
        metadata,
        f"\r\n--{boundary}\r\n".encode(),
        b"Content-Type: application/octet-stream\r\n\r\n",
        local_path.read_bytes(),
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    body = b"".join(body_parts)
    req = urllib.request.Request(
        "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
        data=body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
        },
        method="POST",
    )
    return json.loads(urllib.request.urlopen(req, timeout=600).read())


def list_backups(access_token: str) -> list[dict]:
    q = urllib.parse.quote(f"'{BACKUP_FOLDER_ID}' in parents and trashed = false and name contains 'eve-backup-'")
    url = (
        f"https://www.googleapis.com/drive/v3/files"
        f"?q={q}&fields=files(id,name,createdTime,size)&orderBy=createdTime+desc&pageSize=100"
    )
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read()).get("files", [])


def delete_drive_file(file_id: str, access_token: str) -> None:
    req = urllib.request.Request(
        f"https://www.googleapis.com/drive/v3/files/{file_id}",
        method="DELETE",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    urllib.request.urlopen(req, timeout=30).read()


def main() -> int:
    ap = argparse.ArgumentParser(description="Encrypted nightly backup to Drive.")
    ap.add_argument("--dry-run", action="store_true", help="Make the archive but skip upload + prune.")
    ap.add_argument("--keep", type=int, default=KEEP_N_DEFAULT,
                    help=f"Number of most-recent backups to keep (default {KEEP_N_DEFAULT}).")
    args = ap.parse_args()

    today = dt.date.today().isoformat()
    archive_name = f"eve-backup-{today}.tar.gz.gpg"

    log(f"=== backup start: {archive_name} ===")

    with tempfile.TemporaryDirectory(prefix="eve-backup-") as td:
        archive = pathlib.Path(td) / archive_name
        tar_and_encrypt(archive)
        size_mb = archive.stat().st_size / (1024 * 1024)
        log(f"  archive built: {size_mb:.1f} MB")

        if args.dry_run:
            log("  dry-run: skipping upload + prune")
            log(f"=== backup done (dry-run) ===")
            return 0

        log("  fetching access token...")
        token = get_access_token()

        log("  uploading to Drive...")
        result = upload_to_drive(archive, token)
        log(f"  uploaded: id={result.get('id')} name={result.get('name')}")

        # Prune
        existing = list_backups(token)
        log(f"  total backups in folder: {len(existing)}")
        if len(existing) > args.keep:
            to_prune = existing[args.keep:]
            for f in to_prune:
                log(f"  pruning old backup: {f['name']} ({f['id']})")
                try:
                    delete_drive_file(f["id"], token)
                except Exception as e:
                    log(f"  ! delete failed for {f['name']}: {e}")

    log("=== backup done ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
