"""Minimal client for Plaud's reverse-engineered web API.

Auth model:
  UT (user token, ~10mo lifetime)  -> /user/me, /user-app/profile/account/me, /user-app/auth/workspace/token/<id>
  WT (workspace token, ~24h)       -> /file/*, /filetag/, /user-app/profile/workspace/me
  WRT (refresh token, ~30d)        -> emitted alongside each new WT but NOT used for refresh — UT mints WT directly

Auto-refresh: `refresh_workspace_token()` calls
`POST /user-app/auth/workspace/token/<workspace_id>` with the UT and
gets back a fresh WT + WRT. `ensure_fresh_wt()` is the cron-friendly
helper — refreshes if WT has < 1h remaining and writes the new tokens
back to the env file.

All endpoints validated 2026-05-05/06 against api.plaud.ai (us-west-2).
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ENV_PATH = Path.home() / ".config" / "eve" / "plaud.env"


def _load_env(path: Path = ENV_PATH) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"')
    return out


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    raw = token.replace("bearer ", "").replace("Bearer ", "").strip()
    parts = raw.split(".")
    if len(parts) != 3:
        raise ValueError("not a JWT")
    pad = "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(parts[1] + pad))


@dataclass
class PlaudClient:
    user_token: str       # "bearer eyJ..." — for user-scoped calls
    workspace_token: str  # "Bearer eyJ..." — for workspace-scoped calls
    api_domain: str       # "https://api.plaud.ai"
    workspace_id: str     # "ws_..."

    @classmethod
    def from_env(cls, path: Path = ENV_PATH) -> "PlaudClient":
        e = _load_env(path)
        return cls(
            user_token=e["PLAUD_UT"],
            workspace_token=e["PLAUD_WT"],
            api_domain=e["PLAUD_API_DOMAIN"].rstrip("/"),
            workspace_id=e["PLAUD_WORKSPACE_ID"],
        )

    def wt_expires_at(self) -> float:
        """Unix epoch seconds for WT expiry."""
        return float(_decode_jwt_payload(self.workspace_token)["exp"])

    def wt_seconds_remaining(self) -> float:
        return self.wt_expires_at() - time.time()

    def _request(self, path: str, *, token: str, params: dict | None = None) -> dict:
        url = f"{self.api_domain}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": token,
                "edit-from": "web",
                "app-platform": "web",
                "Content-Type": "application/json",
                # Plaud's WAF rejects the default Python-urllib UA with 403.
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        if data.get("status", 0) not in (0, None):
            # -302 = region-switch signal (Plaud redirects to per-user domain).
            # Not handling for now since us-west-2 already uses api.plaud.ai.
            raise RuntimeError(f"Plaud API error: {data.get('status')} {data.get('msg')}")
        return data

    def list_recordings(self, *, limit: int = 50, skip: int = 0) -> list[dict]:
        """Most recent first. Excludes trash."""
        data = self._request(
            "/file/simple/web",
            token=self.workspace_token,
            params={
                "skip": skip,
                "limit": limit,
                "is_trash": 2,
                "sort_by": "start_time",
                "is_desc": "true",
            },
        )
        return data.get("data_file_list", [])

    def get_detail(self, file_id: str) -> dict:
        """Detail JSON for one recording. Returns {} if not yet transcribed."""
        return self._request(f"/file/detail/{file_id}", token=self.workspace_token).get("data", {})

    def get_audio_url(self, file_id: str, *, prefer_opus: bool = True) -> str:
        """Pre-signed S3 URL (~1.5h validity). Plaud serves .ogg regardless of is_opus flag."""
        data = self._request(
            f"/file/temp-url/{file_id}",
            token=self.workspace_token,
            params={"is_opus": "true" if prefer_opus else "false"},
        )
        url = data.get("temp_url") or data.get("temp_url_opus")
        if not url:
            raise RuntimeError(f"no temp_url in response for {file_id}")
        return url

    def download_audio(self, file_id: str, dest: Path) -> Path:
        """Fetch the audio file to disk. Returns the dest path."""
        url = self.get_audio_url(file_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=120) as r, open(dest, "wb") as f:
            while chunk := r.read(64 * 1024):
                f.write(chunk)
        return dest

    def refresh_workspace_token(self) -> dict:
        """Mint a fresh WT (and WRT) using the UT. Returns the response data dict.

        Endpoint discovered 2026-05-06 by Alex from the Plaud web bundle:
          POST /user-app/auth/workspace/token/<workspace_id>
          Authorization: <UT>
          body: {}
        Response keys include: workspace_token, refresh_token, expires_in,
        wt_expires_at, refresh_expires_in, refresh_expires_at, workspace_id,
        member_id, role.
        """
        url = f"{self.api_domain}/user-app/auth/workspace/token/{self.workspace_id}"
        req = urllib.request.Request(
            url,
            method="POST",
            data=b"{}",
            headers={
                "Authorization": self.user_token,
                "edit-from": "web",
                "app-platform": "web",
                "app-language": "en",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = json.loads(r.read())
        if payload.get("status") != 0:
            raise RuntimeError(f"refresh_workspace_token: {payload.get('status')} {payload.get('msg')}")
        data = payload.get("data") or {}
        if not data.get("workspace_token"):
            raise RuntimeError(f"refresh_workspace_token: no workspace_token in response: {payload}")
        # Mutate the live client so subsequent calls use the new WT.
        self.workspace_token = "Bearer " + data["workspace_token"]
        return data

    def ensure_fresh_wt(self, *, min_remaining_seconds: int = 3600,
                         env_path: Path = ENV_PATH) -> bool:
        """If the WT has less than `min_remaining_seconds` left, refresh and
        rewrite the env file. Returns True if a refresh happened."""
        if self.wt_seconds_remaining() > min_remaining_seconds:
            return False
        data = self.refresh_workspace_token()
        _update_env_tokens(env_path, data)
        return True


def _update_env_tokens(path: Path, data: dict) -> None:
    """Rewrite plaud.env in place with the new WT/WRT, preserving other lines.

    `data` is the response from refresh_workspace_token() (keys: workspace_token,
    refresh_token, wt_expires_at*1000, refresh_expires_at*1000).
    """
    new_wt_value = '"Bearer ' + data["workspace_token"] + '"'
    new_wrt_value = '"' + data["refresh_token"] + '"'
    new_wt_exp_ms = str(int(data["wt_expires_at"]) * 1000)
    new_wrt_exp_ms = str(int(data["refresh_expires_at"]) * 1000)

    replacements = {
        "PLAUD_WT": new_wt_value,
        "PLAUD_WT_EXPIRES_AT": new_wt_exp_ms,
        "PLAUD_WRT": new_wrt_value,
        "PLAUD_WRT_EXPIRES_AT": new_wrt_exp_ms,
    }

    lines = path.read_text().splitlines(keepends=True)
    out = []
    seen = set()
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in replacements:
            # Preserve any leading whitespace
            indent = line[:len(line) - len(line.lstrip())]
            out.append(f"{indent}{key}={replacements[key]}\n")
            seen.add(key)
        else:
            out.append(line)
    # Append any keys we didn't see (shouldn't happen but defensive)
    for key, value in replacements.items():
        if key not in seen:
            out.append(f"{key}={value}\n")
    path.write_text("".join(out))


if __name__ == "__main__":
    # smoke test
    c = PlaudClient.from_env()
    remaining_h = c.wt_seconds_remaining() / 3600
    print(f"workspace token expires in {remaining_h:.1f}h")
    files = c.list_recordings(limit=5)
    print(f"{len(files)} recent recordings:")
    for f in files:
        print(f"  {f['filename']}  ({f['duration']/1000:.0f}s)  id={f['id'][:12]}...")
