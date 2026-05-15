#!/usr/bin/env python3
"""Eve nightly local backup to the encrypted USB stick.

Mirrors the same `.tar.gz.gpg` archive that `backup_to_drive.py` builds, but
copies it to the LUKS-encrypted Transcend USB at `/dev/sdb1` instead of (or
in addition to) Drive. Defense in depth: same content, two destinations.

Crontab entry — runs 5 min after the Drive backup so they don't fight for
disk I/O at 03:30:
    35 3 * * * /usr/bin/python3 /home/eve/.local/eve-tools/backup_to_usb.py \\
        >> /home/eve/.local/eve-tools/cron-backup-usb.log 2>&1

One-time setup required (Alex, root):
    1. Generate a keyfile and add it to a LUKS slot:
         install -m 600 /dev/null ~/.config/eve/usb-backup-keyfile
         dd if=/dev/urandom of=~/.config/eve/usb-backup-keyfile bs=64 count=1
         sudo cryptsetup luksAddKey /dev/sdb1 ~/.config/eve/usb-backup-keyfile
       (will prompt for the existing LUKS passphrase to authorize the new slot)
    2. Make a mount point:
         sudo mkdir -p /mnt/eve-backup
         sudo chown eve:eve /mnt/eve-backup
    3. Add narrow sudoers entries (visudo, drop in /etc/sudoers.d/eve-backup):
         eve ALL=(root) NOPASSWD: /usr/sbin/cryptsetup luksOpen /dev/sdb1 eve_backup --key-file /home/eve/.config/eve/usb-backup-keyfile
         eve ALL=(root) NOPASSWD: /usr/sbin/cryptsetup luksClose eve_backup
         eve ALL=(root) NOPASSWD: /usr/bin/mount /dev/mapper/eve_backup /mnt/eve-backup
         eve ALL=(root) NOPASSWD: /usr/bin/umount /mnt/eve-backup

Until those steps are done, this script logs a message and exits 0 (so cron
doesn't error-spam — it just no-ops with a clear log line).
"""
from __future__ import annotations

import datetime as dt
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, "/home/eve/.local/eve-tools")
from backup_to_drive import tar_and_encrypt  # noqa: E402

LOG = pathlib.Path("/home/eve/.local/eve-tools/cron-backup-usb.log")
KEYFILE = pathlib.Path.home() / ".config" / "eve" / "usb-backup-keyfile"
LUKS_DEV = "/dev/sdb1"
MAPPER_NAME = "eve_backup"
MAPPER_PATH = f"/dev/mapper/{MAPPER_NAME}"
MOUNT_POINT = pathlib.Path("/mnt/eve-backup")
KEEP_N = 30


def log(msg: str) -> None:
    line = f"[{dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}] {msg}"
    print(line)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def preflight() -> bool:
    """Return True if the script can proceed; False if not (no error, just no-op)."""
    if not KEYFILE.exists():
        log(f"setup pending — keyfile missing at {KEYFILE}; skipping")
        return False
    if not pathlib.Path(LUKS_DEV).exists():
        log(f"USB not plugged in — {LUKS_DEV} absent; skipping")
        return False
    if not MOUNT_POINT.exists():
        log(f"setup pending — mount point {MOUNT_POINT} doesn't exist; skipping")
        return False
    # Sanity check sudoers by trying a no-op
    rc = subprocess.run(
        ["sudo", "-n", "/usr/sbin/cryptsetup", "--help"],
        capture_output=True, text=True,
    )
    if rc.returncode != 0:
        log("setup pending — sudoers entries not in place yet; skipping")
        return False
    return True


def luks_open() -> None:
    log(f"opening LUKS volume {LUKS_DEV} → {MAPPER_PATH}")
    run([
        "sudo", "-n", "/usr/sbin/cryptsetup", "luksOpen",
        LUKS_DEV, MAPPER_NAME,
        "--key-file", str(KEYFILE),
    ])


def luks_close() -> None:
    if pathlib.Path(MAPPER_PATH).exists():
        log(f"closing LUKS volume {MAPPER_PATH}")
        run(["sudo", "-n", "/usr/sbin/cryptsetup", "luksClose", MAPPER_NAME], check=False)


def mount_usb() -> None:
    log(f"mounting {MAPPER_PATH} at {MOUNT_POINT}")
    run(["sudo", "-n", "/usr/bin/mount", MAPPER_PATH, str(MOUNT_POINT)])


def unmount_usb() -> None:
    log(f"unmounting {MOUNT_POINT}")
    run(["sudo", "-n", "/usr/bin/umount", str(MOUNT_POINT)], check=False)


def prune_old(keep: int = KEEP_N) -> None:
    archives = sorted(MOUNT_POINT.glob("eve-backup-*.tar.gz.gpg"), reverse=True)
    if len(archives) <= keep:
        return
    for old in archives[keep:]:
        log(f"pruning old: {old.name}")
        try:
            old.unlink()
        except Exception as exc:
            log(f"  ! failed to delete {old}: {exc}")


def main() -> int:
    if not preflight():
        return 0

    today = dt.date.today().isoformat()
    archive_name = f"eve-backup-{today}.tar.gz.gpg"
    log(f"=== usb backup start: {archive_name} ===")

    luks_open()
    try:
        mount_usb()
        try:
            with tempfile.TemporaryDirectory(prefix="eve-backup-usb-") as td:
                staged = pathlib.Path(td) / archive_name
                tar_and_encrypt(staged)
                size_mb = staged.stat().st_size / (1024 * 1024)
                log(f"  archive built: {size_mb:.1f} MB")

                target = MOUNT_POINT / archive_name
                log(f"  copying to {target}")
                target.write_bytes(staged.read_bytes())
                log(f"  copy complete")

            prune_old(KEEP_N)
        finally:
            unmount_usb()
    finally:
        luks_close()

    log("=== usb backup done ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
