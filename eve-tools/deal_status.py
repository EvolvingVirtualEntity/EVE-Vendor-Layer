#!/usr/bin/env python3
"""Weekly digest of the deal pipeline in ~/EveBrain/02-Projects/Deals/.

Walks every `*.md` deal note (skipping `_TEMPLATE.md`), parses the YAML
front-matter, and generates a Markdown digest grouped by status plus
a "needs attention" section for stale deals and deals nearing target-close.

Usage:
    deal_status.py                          # print digest to stdout
    deal_status.py --out digest.md          # write to a specific path
    deal_status.py --save                   # write to ~/EveBrain/01-Daily/<date>_deal-digest.md
    deal_status.py --stale-days 14          # flag deals untouched > N days
"""

import argparse
import datetime as dt
import pathlib
import re
import sys

import yaml

VAULT = pathlib.Path.home() / "EveBrain"
DEALS_DIR = VAULT / "02-Projects" / "Deals"
DAILY_DIR = VAULT / "01-Daily"

FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)

STATUS_ORDER = ["prospect", "active", "loi", "under-contract", "closed", "passed", "unknown"]


def parse_front_matter(text: str) -> dict | None:
    m = FRONT_MATTER_RE.match(text)
    if not m:
        return None
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def iter_deals() -> list[dict]:
    if not DEALS_DIR.exists():
        return []
    out: list[dict] = []
    for p in sorted(DEALS_DIR.glob("*.md")):
        if p.name.startswith("_"):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        fm = parse_front_matter(text)
        if not fm or fm.get("type") != "deal":
            continue
        fm["_path"] = str(p.relative_to(VAULT))
        fm["_mtime"] = dt.date.fromtimestamp(p.stat().st_mtime)
        out.append(fm)
    return out


def days_since(d: dt.date | None) -> int | None:
    if d is None:
        return None
    if isinstance(d, str):
        try:
            d = dt.date.fromisoformat(d)
        except ValueError:
            return None
    return (dt.date.today() - d).days


def fmt_money(v) -> str:
    if v is None or v == 0:
        return "—"
    try:
        return f"${int(float(v)):,}"
    except (ValueError, TypeError):
        return str(v)


def fmt_pct(v) -> str:
    if v is None or v == 0:
        return "—"
    try:
        return f"{float(v) * 100:.2f}%"
    except (ValueError, TypeError):
        return str(v)


def render_digest(deals: list[dict], stale_days: int) -> str:
    today = dt.date.today().isoformat()
    total = len(deals)
    lines: list[str] = []
    lines.append(f"# Deal Pipeline Digest — {today}")
    lines.append("")
    if total == 0:
        lines.append("_No deal notes found in `02-Projects/Deals/`. Copy `_TEMPLATE.md` to start one._")
        return "\n".join(lines) + "\n"

    # Counts by status
    by_status: dict[str, list[dict]] = {s: [] for s in STATUS_ORDER}
    for d in deals:
        key = (d.get("status") or "unknown").lower()
        by_status.setdefault(key, []).append(d)

    lines.append("## Summary")
    for status in STATUS_ORDER:
        n = len(by_status.get(status, []))
        if n:
            lines.append(f"- **{status}**: {n}")
    lines.append(f"- **Total:** {total}")
    lines.append("")

    # Needs attention
    stale = []
    upcoming = []
    for d in deals:
        lt_days = days_since(d.get("last-touch")) or days_since(d["_mtime"])
        if lt_days is not None and lt_days >= stale_days and d.get("status") in ("prospect", "active", "loi", "under-contract"):
            stale.append((lt_days, d))
        tc = d.get("target-close")
        if tc:
            tc_days = days_since(tc)
            if tc_days is not None and tc_days >= -30 and d.get("status") in ("active", "loi", "under-contract"):
                upcoming.append((tc_days, d))

    if stale or upcoming:
        lines.append("## Needs Attention")
        if stale:
            lines.append(f"### Stale (no touch in ≥ {stale_days} days)")
            for days, d in sorted(stale, reverse=True):
                lines.append(f"- **{d.get('deal-name') or d['_path']}** — last touch {days}d ago — "
                             f"_next action:_ {d.get('next-action') or '?'} → [{d['_path']}]")
            lines.append("")
        if upcoming:
            lines.append("### Target-close within 30 days (or already past)")
            for days, d in sorted(upcoming):
                label = f"{-days}d overdue" if days > 0 else f"{-days}d out"
                lines.append(f"- **{d.get('deal-name') or d['_path']}** — target close {d.get('target-close')} ({label}) → [{d['_path']}]")
            lines.append("")

    # Per-status detail
    for status in STATUS_ORDER:
        items = by_status.get(status, [])
        if not items:
            continue
        lines.append(f"## {status.title()} ({len(items)})")
        for d in items:
            name = d.get("deal-name") or d["_path"]
            address = d.get("address") or "—"
            ask = fmt_money(d.get("asking-price"))
            cap = fmt_pct(d.get("ask-cap-rate"))
            next_action = d.get("next-action") or "?"
            last_touch = d.get("last-touch") or d["_mtime"]
            lines.append(f"- **{name}** — {address} — ask {ask} @ {cap} cap · last touch {last_touch}")
            lines.append(f"  - _Next:_ {next_action}")
            lines.append(f"  - [{d['_path']}]")
        lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Weekly deal pipeline digest.")
    ap.add_argument("--out", help="Write digest to this path.")
    ap.add_argument("--save", action="store_true", help="Write to ~/EveBrain/01-Daily/<date>_deal-digest.md.")
    ap.add_argument("--stale-days", type=int, default=14, help="Flag deals untouched for N days (default 14).")
    args = ap.parse_args()

    deals = iter_deals()
    md = render_digest(deals, args.stale_days)

    out_path: pathlib.Path | None = None
    if args.out:
        out_path = pathlib.Path(args.out).expanduser().resolve()
    elif args.save:
        DAILY_DIR.mkdir(parents=True, exist_ok=True)
        out_path = DAILY_DIR / f"{dt.date.today().isoformat()}_deal-digest.md"

    if out_path:
        out_path.write_text(md, encoding="utf-8")
        print(out_path, file=sys.stderr)
    else:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
