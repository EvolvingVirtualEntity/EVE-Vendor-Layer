#!/usr/bin/env python3
"""Ask a locally-hosted LLM (Ollama) — private, offline, no API cost.

Usage:
    ask_local.py "summarize this lease in 5 bullets"
    echo "LONG TEXT" | ask_local.py - --system "You extract lease abstracts."
    ask_local.py "..." --model qwen2.5:7b-instruct-q4_K_M
    ask_local.py "..." --json        # ask model to reply in JSON (works with newer Qwen/Llama)
    ask_local.py "..." --no-stream   # return whole response at once

Requires the Ollama server running locally (default port 11434).
"""

import argparse
import json
import sys
import urllib.request
import urllib.error

DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen2.5:7b-instruct-q4_K_M"


def post_json(url: str, payload: dict, stream: bool = True):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    return urllib.request.urlopen(req, timeout=3600)


def stream_chat(host: str, model: str, prompt: str, system: str | None, want_json: bool) -> int:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload: dict = {"model": model, "messages": messages, "stream": True}
    if want_json:
        payload["format"] = "json"

    try:
        resp = post_json(f"{host}/api/chat", payload, stream=True)
    except urllib.error.URLError as e:
        sys.exit(f"error: cannot reach Ollama at {host}: {e}\n(hint: ~/.local/bin/ollama serve)")

    full = []
    for raw in resp:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("message", {}).get("content"):
            chunk = obj["message"]["content"]
            sys.stdout.write(chunk)
            sys.stdout.flush()
            full.append(chunk)
        if obj.get("done"):
            break
    sys.stdout.write("\n")
    return 0


def oneshot_chat(host: str, model: str, prompt: str, system: str | None, want_json: bool) -> int:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload: dict = {"model": model, "messages": messages, "stream": False}
    if want_json:
        payload["format"] = "json"

    try:
        resp = post_json(f"{host}/api/chat", payload, stream=False)
    except urllib.error.URLError as e:
        sys.exit(f"error: cannot reach Ollama at {host}: {e}")
    data = json.loads(resp.read().decode("utf-8"))
    print(data.get("message", {}).get("content", ""))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Call local Ollama LLM.")
    ap.add_argument("prompt", help="Prompt. Use '-' to read from stdin.")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--system", default=None, help="System prompt.")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--json", action="store_true", help="Ask model to reply in valid JSON.")
    ap.add_argument("--no-stream", action="store_true", help="Return complete response rather than streaming.")
    args = ap.parse_args()

    prompt = sys.stdin.read() if args.prompt == "-" else args.prompt
    prompt = prompt.strip()
    if not prompt:
        sys.exit("error: empty prompt")

    if args.no_stream:
        return oneshot_chat(args.host, args.model, prompt, args.system, args.json)
    return stream_chat(args.host, args.model, prompt, args.system, args.json)


if __name__ == "__main__":
    sys.exit(main())
