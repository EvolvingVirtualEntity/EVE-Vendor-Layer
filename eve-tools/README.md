# eve-tools/

Platform-layer scripts. install.sh copies these to `~/.local/eve-tools/` on every customer box. Cron entries point here.

## What's in here

| Script / file | Purpose |
|---|---|
| `ask_local.py` | Ollama wrapper for local LLM queries (sensitive doc work) |
| `assemble-claude.sh` | (at repo root) cat CLAUDE.user.md + CLAUDE.base.md → CLAUDE.md |
| `backup_to_drive.py` | Nightly encrypted (AES-256 + GPG) backup to Google Drive, 30-day rolling |
| `backup_to_usb.py` | Encrypted local backup to USB drive (mirror of Drive backup) |
| `bts_sweep.py` | Build-to-suit deal-sourcing sweep + daily digest |
| `chat_send_audio.py` | Upload audio messages to Google Chat |
| `daily_brief.py` | 7am + 2pm daily brief (WhatsApp + Chat) |
| `deal_status.py` | CRE deal status updates |
| `eve-knowledge-schema.sql` | Schema for the knowledge DB (rows are customer-layer, schema is vendor) |
| `imagegen.py` | Gemini image generation wrapper |
| `interest_init.py` | (re)load interests from `interest_profile.yaml` (customer-supplied) into the knowledge DB |
| `lease_abstract.py` | Lease PDF → structured abstract |
| `news_fetch.py` | RSS / news ingestion (driven by customer-supplied `interest_profile.yaml`) |
| `ocr.py` | Tesseract wrapper for image → text |
| `pdf_extract.py` | PDF text extraction |
| `plaud_client.py` | Plaud cloud API client |
| `plaud_ingest.py` | Hourly pull from Plaud + Whisper transcription + Ollama analysis + vault write |
| `prompt_seed.py` | Internal helper for prompt assembly |
| `pulse_curator.py` | Daily curator pass over news+events |
| `pulse_outreach.py` | (paused) Outreach scheduler |
| `pulse_recommend.py` | Recommend what to send/do, given the cadence model |
| `relevance_score.py` | Score news items against interest profile |
| `research_property.py` | Parcel / zoning / comps research |
| `speak.py` | Piper TTS wrapper |
| `transcribe.py` | Whisper STT wrapper |
| `underwrite.py` | CRE underwriting model |
| `vault_ask.py` | One-shot vault question (uses Chroma RAG) |
| `vault_chat.py` | Interactive vault chat session |
| `vault_index.py` | Build/refresh Chroma index over the vault |
| `web_fetch.py` | HTTP fetch utility |

## Customer-supplied configs (NOT in this repo)

Some scripts read YAML / DB / cache files that live in the *instance layer*, not here:

- `cadence_model.yaml` — outreach pacing + topic families (per-customer)
- `interest_profile.yaml` — RSS sources + interests (per-customer)
- `eve-knowledge.db` — rows are per-customer (schema is in this repo)
- `plaud-cache/`, `plaud-state/` — runtime state (per-customer)
- `vault-chroma/` — RAG index built from the customer's vault

install.sh creates the empty dirs; the dashboard (Phase 2.5) handles populating the configs.

## TODO — sanitization pass before customer #2

The following scripts currently contain hardcoded L&R-specific strings (paths, emails, Chat space IDs, project references). They run fine on the L&R box, but **must be templatized before deploying to any other customer**. Tracked separately; first cleanup wave is the next Phase 2 commit.

- assemble-claude.sh (repo root)
- backup_to_drive.py
- bts_sweep.py
- chat_send_audio.py
- daily_brief.py
- deal_status.py
- lease_abstract.py
- plaud_ingest.py
- pulse_outreach.py
- research_property.py
- underwrite.py
- vault_chat.py
- vault_index.py

The templatization pattern: replace hardcoded strings with environment variables loaded from `~/.config/eve/instance.env` (customer-supplied) or `~/EveBrain/CLAUDE.user.md` parsed values.
