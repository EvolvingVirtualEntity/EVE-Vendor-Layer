# cron/

Crontab template installed by `install.sh`. Universal vendor-layer entries only — customer-layer (dated reminders, customer-specific cadence overrides) is added separately by the dashboard.

## Install

```bash
crontab cron/eve-cron.crontab.template
```

`install.sh` does *not* run this automatically (the dashboard does, after the customer has credentials in place).

## What's included (vendor-layer cron jobs)

| Schedule | Job | Notes |
|---|---|---|
| 7am + 1pm | `news_fetch.py` + `relevance_score.py` | Chained — score always runs post-fetch |
| 3:15am | `pulse_curator.py` | Before backup + before news fetch |
| 8am | `bts_sweep.py` | After news + scoring |
| 3:30am | `backup_to_drive.py` | Nightly encrypted Drive backup, 30-day rolling |
| 3:35am | `backup_to_usb.py` | Mirror to USB, 5min offset to avoid I/O contention |
| 7am + 2pm | `daily_brief.py` | Local TZ |
| 30 mins past, 6am-10pm | `plaud_ingest.py` | Hourly Plaud pull |
| 4am | `eve-update.sh` | Auto-pull vendor updates |

## What's commented out

- `pulse_outreach.py` (every 15 min) — shipped paused; uncomment + tune cadence model when customer is ready to enable

## What's NOT in this template (customer-layer)

- Dated one-shot reminders (`reminder-*.py`) — dashboard creates these per customer
- Customer-specific reminder scripts (e.g., L&R's `shawn-vzw-reminder.sh`)
- Any cron that references customer-specific addresses / spaces / data

## Timezone

The template assumes the host TZ matches whatever the customer wants. Adjust the host TZ during install (`sudo timedatectl set-timezone America/Los_Angeles` etc.) — the cron entries use host-local time, not UTC.
