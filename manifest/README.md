# manifest/ — install dependency lists

Plain-text files consumed by `install.sh` to bootstrap a fresh Ubuntu 24.04 box to vendor parity.

| File | Consumer | Format |
|---|---|---|
| `apt-packages.txt` | `apt-get install -y $(cat apt-packages.txt \| grep -v '^#')` | one package per line, `#` comments allowed |
| `npm-global.txt` | `npm install -g <each line>` | `<pkg>@<version>` per line |
| `ollama-models.txt` | `ollama pull <each line>` | model tag per line |
| `piper-voices.txt` | download `.onnx` + `.onnx.json` from huggingface | voice name per line |
| `venv-requirements/*.txt` | per-venv `uv pip install -r <file>` | standard pip freeze output |

Versions are pinned to what's running on the L&R box on 2026-05-15. Bumping a version = explicit commit; never silently float.

Repos added by `install.sh` (not via apt):
- NodeSource (Node 20 LTS)
- Cloudflare (cloudflared)
- Tailscale (tailscale)
