#!/usr/bin/env python3
"""Transcribe an audio file with faster-whisper.

Usage:
    transcribe.py <audio-file> [--model small|base|medium|large-v3] [--lang en|de|auto]
"""
import argparse
import sys
from pathlib import Path

from faster_whisper import WhisperModel


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio", type=Path)
    ap.add_argument("--model", default="large-v3",
                    choices=["tiny", "base", "small", "medium", "large-v3", "distil-large-v3"])
    ap.add_argument("--lang", default=None, help="Language code (en, de, ...) or omit for auto")
    ap.add_argument("--format", default="text", choices=["text", "srt", "vtt", "json"])
    args = ap.parse_args()

    if not args.audio.exists():
        sys.exit(f"File not found: {args.audio}")

    model = WhisperModel(args.model, device="cpu", compute_type="int8")

    segments, info = model.transcribe(
        str(args.audio),
        language=args.lang,
        vad_filter=True,
    )

    print(f"# Detected language: {info.language} (prob {info.language_probability:.2f})",
          file=sys.stderr)
    print(f"# Duration: {info.duration:.1f}s", file=sys.stderr)

    if args.format == "text":
        for seg in segments:
            print(seg.text.strip())
    elif args.format == "json":
        import json
        out = [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segments]
        print(json.dumps({"language": info.language, "duration": info.duration, "segments": out},
                         ensure_ascii=False, indent=2))
    elif args.format == "srt":
        for i, s in enumerate(segments, 1):
            print(f"{i}\n{_ts(s.start)} --> {_ts(s.end)}\n{s.text.strip()}\n")
    elif args.format == "vtt":
        print("WEBVTT\n")
        for s in segments:
            print(f"{_ts(s.start, vtt=True)} --> {_ts(s.end, vtt=True)}\n{s.text.strip()}\n")


def _ts(seconds: float, vtt: bool = False) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    sep = "." if vtt else ","
    return f"{h:02d}:{m:02d}:{int(s):02d}{sep}{int((s - int(s)) * 1000):03d}"


if __name__ == "__main__":
    main()
