#!/usr/bin/env python3
"""Eve's text-to-speech — Piper TTS, local/offline, free.

Usage:
    speak.py "Hello, how are you?"
    echo "long text" | speak.py -
    speak.py "..." --voice de_DE-thorsten-medium
    speak.py "..." --out /path/to/audio.wav

Default voice: en_US-amy-medium (warm American female).
Voice models live at ~/.local/eve-tools/piper-voices/.
Default output dir: ~/eve-audio/YYYY-MM-DD_HHMMSS_slug.wav
"""

import argparse
import datetime as dt
import os
import pathlib
import re
import shutil
import subprocess
import sys

PIPER_VENV_PY = pathlib.Path.home() / ".local" / "eve-tools" / "piper-venv" / "bin" / "python"
VOICES_DIR = pathlib.Path.home() / ".local" / "eve-tools" / "piper-voices"
DEFAULT_VOICE = "en_US-amy-medium"
DEFAULT_OUT_DIR = pathlib.Path.home() / "eve-audio"


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return slug[:max_len] or "audio"


def build_output_path(text: str, explicit: str | None, ext: str) -> pathlib.Path:
    if explicit:
        return pathlib.Path(explicit).expanduser().resolve()
    DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return DEFAULT_OUT_DIR / f"{stamp}_{slugify(text)}.{ext}"


def wav_to_mp3(wav_path: pathlib.Path, mp3_path: pathlib.Path, bitrate: str = "64k") -> None:
    if not shutil.which("ffmpeg"):
        sys.exit("error: ffmpeg not found on PATH (apt install ffmpeg)")
    result = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav_path),
         "-codec:a", "libmp3lame", "-b:a", bitrate, str(mp3_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(f"error: ffmpeg exited {result.returncode}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate speech with Piper TTS.")
    ap.add_argument("text", help="Text to speak. Use '-' to read from stdin.")
    ap.add_argument("--voice", default=DEFAULT_VOICE,
                    help=f"Voice model name in {VOICES_DIR} (default: {DEFAULT_VOICE})")
    ap.add_argument("--out", help="Output WAV path (default: ~/eve-audio/<stamp>_<slug>.wav).")
    ap.add_argument("--length-scale", type=float, default=None,
                    help="Speaking rate. <1 = faster, >1 = slower (default model preset).")
    ap.add_argument("--format", choices=["wav", "mp3"], default="wav",
                    help="Output audio format. mp3 is 3-5x smaller; requires ffmpeg on PATH.")
    ap.add_argument("--bitrate", default="64k",
                    help="MP3 bitrate (only used when --format mp3). Default 64k is fine for speech.")
    args = ap.parse_args()

    text = sys.stdin.read() if args.text == "-" else args.text
    text = text.strip()
    if not text:
        sys.exit("error: empty text")

    model_path = VOICES_DIR / f"{args.voice}.onnx"
    config_path = VOICES_DIR / f"{args.voice}.onnx.json"
    if not model_path.exists():
        sys.exit(f"error: voice model not found: {model_path}")
    if not config_path.exists():
        sys.exit(f"error: voice config not found: {config_path}")

    final_path = build_output_path(text, args.out, args.format)
    wav_path = final_path if args.format == "wav" else final_path.with_suffix(".wav")

    cmd = [
        str(PIPER_VENV_PY), "-m", "piper",
        "-m", str(model_path),
        "-c", str(config_path),
        "-f", str(wav_path),
    ]
    if args.length_scale is not None:
        cmd += ["--length-scale", str(args.length_scale)]

    result = subprocess.run(cmd, input=text, text=True, capture_output=True)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(f"error: piper exited {result.returncode}")

    if args.format == "mp3":
        wav_to_mp3(wav_path, final_path, bitrate=args.bitrate)
        os.unlink(wav_path)

    print(final_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
