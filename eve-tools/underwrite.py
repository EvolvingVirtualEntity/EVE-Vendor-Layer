#!/usr/bin/env python3
"""Simple CRE underwriting helper — 10-year pro forma from a YAML/JSON config.

Input config (YAML or JSON):
    name: "300 S Carlton Ave, Wheaton — BTS Refresh"
    price: 1_800_000        # purchase price $
    closing_costs_pct: 0.02 # % of price (default 0.02)
    noi_y1: 144_000         # Year-1 NOI $
    rent_growth: 0.03       # annual NOI growth
    vacancy: 0.05           # reserved — used only if you pass gross + opex instead
    hold_years: 10
    exit_cap: 0.075         # exit cap rate
    selling_costs_pct: 0.03 # % of exit price (default 0.03)
    loan:
      ltv: 0.65
      rate: 0.065           # annual interest rate
      amort_years: 25
      interest_only_years: 0   # optional

Outputs:
    - Stdout: Markdown pro forma table
    - File: ~/EveBrain/04-Resources/Underwriting/<date>_<slug>.md (or --out)
    - Optional: Google Sheet via Workspace MCP (not in this script; run separately)

Pure stdlib + PyYAML. No numpy_financial — we write our own PMT/IRR.
"""

import argparse
import datetime as dt
import json
import pathlib
import re
import sys

import yaml

DEFAULT_OUT_DIR = pathlib.Path.home() / "EveBrain" / "04-Resources" / "Underwriting"


def slugify(s: str, max_len: int = 60) -> str:
    return (re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower() or "deal")[:max_len]


# --- Financial primitives --------------------------------------------------

def pmt_monthly(principal: float, annual_rate: float, years: int) -> float:
    """Level amortizing payment. Returns a positive number."""
    n = years * 12
    r = annual_rate / 12.0
    if r == 0:
        return principal / n
    return principal * (r * (1 + r) ** n) / ((1 + r) ** n - 1)


def amort_schedule(principal: float, annual_rate: float, amort_years: int,
                   io_years: int = 0) -> list[dict]:
    """Monthly amortisation schedule. Returns list of {month, interest, principal, balance}."""
    r = annual_rate / 12.0
    bal = principal
    schedule: list[dict] = []

    for m in range(1, io_years * 12 + 1):
        interest = bal * r
        schedule.append({"month": m, "interest": interest, "principal": 0.0, "balance": bal})

    remaining_years = amort_years - io_years
    if remaining_years <= 0:
        return schedule
    pmt = pmt_monthly(bal, annual_rate, remaining_years)
    for m in range(io_years * 12 + 1, (io_years + remaining_years) * 12 + 1):
        interest = bal * r
        principal_paid = pmt - interest
        bal = max(0.0, bal - principal_paid)
        schedule.append({"month": m, "interest": interest,
                         "principal": principal_paid, "balance": bal})
    return schedule


def irr(cashflows: list[float], guess: float = 0.1) -> float | None:
    """Newton/bisection IRR. Returns None if no root found in plausible range."""
    def npv(rate: float) -> float:
        return sum(cf / (1 + rate) ** t for t, cf in enumerate(cashflows))

    # Bisect on [-0.99, 10.0]
    lo, hi = -0.99, 10.0
    flo, fhi = npv(lo), npv(hi)
    if flo * fhi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        fmid = npv(mid)
        if abs(fmid) < 1e-6:
            return mid
        if flo * fmid < 0:
            hi = mid
            fhi = fmid
        else:
            lo = mid
            flo = fmid
    return (lo + hi) / 2


# --- Pro forma -------------------------------------------------------------

def load_config(path: pathlib.Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("config must be a mapping")
    return data


def build_pro_forma(cfg: dict) -> dict:
    name = cfg.get("name") or "Untitled Deal"
    price = float(cfg["price"])
    closing_costs_pct = float(cfg.get("closing_costs_pct", 0.02))
    rent_growth = float(cfg.get("rent_growth", 0.02))
    hold_years = int(cfg.get("hold_years", 10))
    exit_cap = float(cfg["exit_cap"])
    selling_costs_pct = float(cfg.get("selling_costs_pct", 0.03))

    # Two modes: (1) noi_y1 + rent_growth → compound growth; (2) noi_schedule → explicit per-year list.
    noi_schedule_cfg = cfg.get("noi_schedule")
    if noi_schedule_cfg:
        noi_schedule = [float(v) for v in noi_schedule_cfg]
        if len(noi_schedule) != hold_years:
            raise ValueError(f"noi_schedule has {len(noi_schedule)} entries but hold_years={hold_years}")
        noi_y1 = noi_schedule[0]
    else:
        noi_y1 = float(cfg["noi_y1"])
        noi_schedule = None

    loan = cfg.get("loan") or {}
    ltv = float(loan.get("ltv", 0))
    rate = float(loan.get("rate", 0))
    amort_years = int(loan.get("amort_years", 25))
    io_years = int(loan.get("interest_only_years", 0))
    principal_override = loan.get("principal")

    if principal_override is not None:
        loan_amount = float(principal_override)
        ltv = loan_amount / price if price else 0.0
    else:
        loan_amount = price * ltv
    equity = price - loan_amount + price * closing_costs_pct
    sched = amort_schedule(loan_amount, rate, amort_years, io_years) if loan_amount else []

    # Year-by-year NOI + debt service + cash flow
    years: list[dict] = []
    noi = noi_y1
    ds_per_year = {
        y: sum(m["interest"] + m["principal"]
               for m in sched[(y - 1) * 12:y * 12])
        for y in range(1, hold_years + 1)
    } if sched else {y: 0.0 for y in range(1, hold_years + 1)}

    for y in range(1, hold_years + 1):
        if noi_schedule is not None:
            noi = noi_schedule[y - 1]
        ds = ds_per_year.get(y, 0.0)
        cfads = noi - ds
        dscr = (noi / ds) if ds > 0 else float("inf")
        years.append({
            "year": y,
            "noi": noi,
            "debt_service": ds,
            "cash_flow": cfads,
            "dscr": dscr,
        })
        if noi_schedule is None:
            noi = noi * (1 + rent_growth)

    # Exit: use NEXT year's NOI over exit cap (forward-looking), then net of loan balance + selling costs.
    # For schedule mode, extrapolate one more year using the implied growth from the last two years of the schedule.
    if noi_schedule is not None and len(noi_schedule) >= 2:
        last, prev = noi_schedule[-1], noi_schedule[-2]
        implied_growth = (last / prev - 1.0) if prev > 0 else rent_growth
        exit_year_noi_forward = last * (1 + implied_growth)
    else:
        exit_year_noi_forward = years[-1]["noi"] * (1 + rent_growth)
    exit_price = exit_year_noi_forward / exit_cap
    selling_costs = exit_price * selling_costs_pct
    remaining_bal = sched[hold_years * 12 - 1]["balance"] if sched and hold_years * 12 <= len(sched) else 0.0
    net_proceeds = exit_price - selling_costs - remaining_bal

    # Cashflows for levered IRR: -equity at t=0, annual CFADS, plus net proceeds at hold year
    cfs = [-equity] + [y["cash_flow"] for y in years]
    cfs[-1] += net_proceeds
    levered_irr = irr(cfs)

    # Unlevered: -(price + closing_costs), annual NOI, exit proceeds (gross of loan)
    unlev_cfs = [-(price + price * closing_costs_pct)] + [y["noi"] for y in years]
    unlev_cfs[-1] += exit_price - selling_costs
    unlevered_irr = irr(unlev_cfs)

    total_equity_cf = sum(y["cash_flow"] for y in years) + net_proceeds
    equity_multiple = total_equity_cf / equity if equity else float("inf")

    # Year-1 metrics
    cap_y1 = noi_y1 / price if price else 0.0
    coc_y1 = (years[0]["cash_flow"] / equity) if equity else 0.0

    return {
        "name": name,
        "price": price,
        "closing_costs_pct": closing_costs_pct,
        "equity": equity,
        "loan_amount": loan_amount,
        "ltv": ltv,
        "rate": rate,
        "amort_years": amort_years,
        "io_years": io_years,
        "hold_years": hold_years,
        "exit_cap": exit_cap,
        "exit_price": exit_price,
        "selling_costs": selling_costs,
        "remaining_loan_balance": remaining_bal,
        "net_proceeds": net_proceeds,
        "years": years,
        "cap_y1": cap_y1,
        "coc_y1": coc_y1,
        "levered_irr": levered_irr,
        "unlevered_irr": unlevered_irr,
        "equity_multiple": equity_multiple,
    }


# --- Render ----------------------------------------------------------------

def fmt_money(v: float) -> str:
    return f"${v:,.0f}"


def fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.2f}%"


def render_markdown(pf: dict) -> str:
    lines: list[str] = []
    today = dt.date.today().isoformat()
    lines.append("---")
    lines.append("type: underwriting")
    lines.append(f"date: {today}")
    lines.append(f"deal: {pf['name']!r}")
    lines.append("---")
    lines.append("")
    lines.append(f"# Underwriting — {pf['name']}")
    lines.append("")
    lines.append("## Capital Stack")
    lines.append(f"- **Purchase Price:** {fmt_money(pf['price'])}")
    lines.append(f"- **Closing Costs:** {fmt_money(pf['price'] * pf['closing_costs_pct'])} ({fmt_pct(pf['closing_costs_pct'])})")
    lines.append(f"- **Loan Amount (LTV {fmt_pct(pf['ltv'])}):** {fmt_money(pf['loan_amount'])}")
    lines.append(f"- **Equity Required:** {fmt_money(pf['equity'])}")
    lines.append("")
    lines.append("## Debt Terms")
    lines.append(f"- **Rate:** {fmt_pct(pf['rate'])}")
    lines.append(f"- **Amortization:** {pf['amort_years']} years"
                 + (f" (first {pf['io_years']} interest-only)" if pf['io_years'] else ""))
    lines.append("")
    lines.append("## Returns Summary")
    lines.append(f"- **Year-1 Cap Rate:** {fmt_pct(pf['cap_y1'])}")
    lines.append(f"- **Year-1 Cash-on-Cash:** {fmt_pct(pf['coc_y1'])}")
    lines.append(f"- **Unlevered IRR ({pf['hold_years']}-yr hold):** {fmt_pct(pf['unlevered_irr'])}")
    lines.append(f"- **Levered IRR ({pf['hold_years']}-yr hold):** {fmt_pct(pf['levered_irr'])}")
    lines.append(f"- **Equity Multiple:** {pf['equity_multiple']:.2f}x")
    lines.append("")
    lines.append("## Exit")
    lines.append(f"- **Exit Cap:** {fmt_pct(pf['exit_cap'])}")
    lines.append(f"- **Exit Price:** {fmt_money(pf['exit_price'])}")
    lines.append(f"- **Selling Costs:** {fmt_money(pf['selling_costs'])}")
    lines.append(f"- **Remaining Loan Balance:** {fmt_money(pf['remaining_loan_balance'])}")
    lines.append(f"- **Net Proceeds to Equity:** {fmt_money(pf['net_proceeds'])}")
    lines.append("")
    lines.append("## Annual Pro Forma")
    lines.append("| Year | NOI | Debt Service | Cash Flow | DSCR |")
    lines.append("|------|-----|--------------|-----------|------|")
    for y in pf["years"]:
        dscr = "—" if y["dscr"] == float("inf") else f"{y['dscr']:.2f}x"
        lines.append(f"| {y['year']} | {fmt_money(y['noi'])} | {fmt_money(y['debt_service'])} | {fmt_money(y['cash_flow'])} | {dscr} |")
    lines.append("")
    return "\n".join(lines) + "\n"


def build_output_path(pf: dict, explicit: str | None) -> pathlib.Path:
    if explicit:
        return pathlib.Path(explicit).expanduser().resolve()
    DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    return DEFAULT_OUT_DIR / f"{today}_{slugify(pf['name'])}.md"


def main() -> int:
    ap = argparse.ArgumentParser(description="CRE underwriting pro forma from YAML/JSON config.")
    ap.add_argument("config", type=pathlib.Path, help="Path to YAML or JSON deal config.")
    ap.add_argument("--out", help="Explicit output Markdown path.")
    ap.add_argument("--print", action="store_true", help="Also print the Markdown to stdout.")
    args = ap.parse_args()

    if not args.config.exists():
        sys.exit(f"error: config {args.config} not found")

    cfg = load_config(args.config)
    pf = build_pro_forma(cfg)
    md = render_markdown(pf)
    out = build_output_path(pf, args.out)
    out.write_text(md, encoding="utf-8")

    if args.print:
        print(md)
    print(out, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
