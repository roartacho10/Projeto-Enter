"""
L1 - Calculation engine.

Reads the canonical price table and the client's positions, and produces the
MetricsPack: the single source of truth for every figure in the letter.
No language model is involved here, and none of these numbers may be
recomputed downstream - they are only quoted.

Run: python src/compute_metrics.py
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
import sys
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from contracts import DataQualityIssue, MetricsPack, PositionMetric  # noqa: E402

from context import CLIENT_ID, OUT, REF, PROC, period_id, period_bounds, br_date  # noqa: E402

CLIENT = CLIENT_ID
PERIOD = period_id()
START, END = period_bounds(PERIOD)
# Policy benchmark for a "moderado" mandate. Declared, not implied - the v1
# quoted a benchmark that existed in no input file.
BENCH_NAME, W_CDI, W_IBOV = "75% CDI + 25% Ibovespa", 0.75, 0.25
RUN_ID = datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")

issues: list[DataQualityIssue] = []
def log(sev, code, msg, iid=None):
    issues.append(DataQualityIssue(run_id=RUN_ID, severity=sev, code=code, message=msg, instrument_id=iid))

con = sqlite3.connect(PROC / "enter.db")
px = pd.read_sql("SELECT * FROM prices", con)
ind = pd.read_sql("SELECT * FROM market_indicators", con)
con.close()
instruments = pd.read_csv(REF / "instruments.csv", dtype={"cnpj": str}).fillna("").set_index("instrument_id")
positions = pd.read_csv(REF / "positions.csv").fillna("")
positions = positions[positions["client_id"] == CLIENT]
if positions.empty:
    # A client on the roster with no holdings is a precondition failure, not a
    # portfolio worth zero. Say which, rather than dividing by it.
    print(f"\nFALHOU: {CLIENT} nao tem nenhuma posicao em data/reference/positions.csv.")
    sys.exit(1)


def price_on(iid: str, day: str) -> float | None:
    """Last available price on or before `day` - robust to holidays."""
    s = px[(px["instrument_id"] == iid) & (px["date"] <= day)].sort_values("date")
    return None if s.empty else float(s.iloc[-1]["price"])


metrics: list[PositionMetric] = []
marked_value = 0.0

for _, p in positions.iterrows():
    iid = p["instrument_id"]
    ins = instruments.loc[iid]
    # statement_value only means something for the value-based bases; a
    # quantity-based position may legitimately leave it blank.
    basis = p["quantity_basis"]
    base_value = float(p["statement_value"]) if str(p["statement_value"]).strip() else 0.0
    qty = price0 = price1 = None
    note = ""

    if basis == "statement_qty":                      # equities: quantity is given
        qty = float(p["quantity"])
        price0, price1 = price_on(iid, START), price_on(iid, END)
        if price0 is None or price1 is None:
            log("blocker", "no_price", f"{iid}: missing price at {START}/{END}", iid); continue
        v0, v1 = qty * price0, qty * price1
        marked_value += v0

    elif basis == "derive_from_value":                # funds: back out the quota count
        anchor = str(p["statement_value_date"])
        qa = price_on(iid, anchor)
        if qa is None:
            log("blocker", "no_anchor_quota", f"{iid}: no quota at {anchor}", iid); continue
        qty = base_value / qa
        price0, price1 = price_on(iid, START), price_on(iid, END)
        if price0 is None or price1 is None:
            log("blocker", "no_price", f"{iid}: missing quota at {START}/{END}", iid); continue
        v0, v1 = qty * price0, qty * price1
        marked_value += v0
        note = f"quota count derived from statement value at {anchor}"

    elif basis == "return_only":                      # quota series does not reach the anchor
        price0, price1 = price_on(iid, START), price_on(iid, END)
        if price0 is None or price1 is None:
            log("blocker", "no_price", f"{iid}: missing quota at {START}/{END}", iid); continue
        v0 = base_value                               # level carried from the statement
        v1 = v0 * (price1 / price0)                   # change is real
        log("warning", "value_carried",
            f"{iid}: return marked to market, position level carried from statement "
            f"of {p['statement_value_date']} - understated", iid)
        note = "return marked; level carried from statement (stale)"

    elif basis == "value_only":                       # matured paper and cash: no return
        v0 = v1 = base_value
        if ins["type"] == "fixed_income":
            log("warning", "matured_instrument",
                f"{iid}: matured before the period, no defensible accrual - carried flat", iid)
        note = "carried flat"
    else:
        log("blocker", "unknown_basis", f"{iid}: unknown quantity_basis '{basis}'", iid); continue

    metrics.append(PositionMetric(
        instrument_id=iid, quantity=qty, price_start=price0, price_end=price1,
        value_start=round(v0, 2), value_end=round(v1, 2),
        return_pct=round((v1 / v0 - 1) * 100, 4) if v0 else 0.0,
        weight_pct=0.0, contribution_pp=0.0, pricing_note=note))

# ---------------------------------------------------------------- aggregates
t0 = sum(m.value_start for m in metrics)
t1 = sum(m.value_end for m in metrics)
cash = sum(m.value_start for m in metrics if instruments.loc[m.instrument_id, "type"] == "cash")
i0 = sum(m.value_start for m in metrics if instruments.loc[m.instrument_id, "type"] != "cash")
i1 = sum(m.value_end for m in metrics if instruments.loc[m.instrument_id, "type"] != "cash")
for m in metrics:
    m.weight_pct = round(m.value_start / t0 * 100, 4)
    m.contribution_pp = round((m.value_end - m.value_start) / t0 * 100, 4)

# ---------------------------------------------------------------- benchmark
cdi = ind[(ind["series_id"] == "12") & (ind["date"] > START) & (ind["date"] <= END)]
if cdi.empty:
    log("blocker", "no_cdi", "CDI series 12 not found for the period")
    cdi_ret = 0.0
else:
    cdi_ret = ((1 + cdi["value"] / 100).prod() - 1) * 100
ib0, ib1 = price_on("IBOV", START), price_on("IBOV", END)
ibov_ret = (ib1 / ib0 - 1) * 100 if ib0 and ib1 else 0.0
bench = W_CDI * cdi_ret + W_IBOV * ibov_ret
inv_ret = (i1 / i0 - 1) * 100

pack = MetricsPack(
    run_id=RUN_ID, client_id=CLIENT, period_start=START, period_end=END, positions=metrics,
    total_value_start=round(t0, 2), total_value_end=round(t1, 2),
    total_return_pct=round((t1 / t0 - 1) * 100, 4),
    invested_value_start=round(i0, 2), invested_value_end=round(i1, 2),
    invested_return_pct=round(inv_ret, 4),
    cash_value=round(cash, 2), cash_pct_of_total=round(cash / t0 * 100, 4),
    benchmark_name=BENCH_NAME, benchmark_return_pct=round(bench, 4),
    excess_return_pp=round(inv_ret - bench, 4),
    cdi_return_pct=round(cdi_ret, 4), ibov_return_pct=round(ibov_ret, 4),
    coverage_pct=round(marked_value / i0 * 100, 2), issues=issues)

(OUT / "metrics_pack.json").write_text(pack.model_dump_json(indent=2), encoding="utf-8")

# ---------------------------------------------------------------- validate
# The golden file is Albert's hand-checked answer key. Other clients have no
# such reference, so they are reported without it rather than silently
# "passing" a check that was never run.
# The answer key belongs to a period: it is a hand-check of one month's
# numbers, so it is named after that month and looked up by it.
golden_path = REF / f"golden_portfolio_{PERIOD}.csv"
if not golden_path.exists():
    print(f"\n(sem gabarito para {CLIENT} em {PERIOD} "
          f"[{golden_path.name}]: validacao numerica nao executada)")
    print(f"metrics_pack.json -> {OUT / 'metrics_pack.json'}")
    raise SystemExit(0)
golden = pd.read_csv(golden_path)
checks, fails = [], 0
def chk(label, got, exp, tol):
    global fails
    ok = abs(got - exp) <= tol
    if not ok: fails += 1
    checks.append((label, got, exp, ok))
for _, g in golden.iterrows():
    gid = g["id"]
    if gid.startswith("__"): continue
    m = next((x for x in metrics if x.instrument_id == gid), None)
    if gid == "FUNDS_BUCKET":
        got0 = sum(x.value_start for x in metrics if instruments.loc[x.instrument_id, "type"] == "fund")
        chk(f"FUNDS_BUCKET valor {br_date(START)}", got0, float(g["value_start"]), 0.05); continue
    if m is None: continue
    chk(f"{gid} valor {br_date(START)}", m.value_start, float(g["value_start"]), 0.05)
    chk(f"{gid} retorno", m.return_pct, float(g["return_pct"]), 0.02)
# The totals were typed into this file AND present in the answer key, so an
# updated key would have been silently ignored here. They are read from it.
_g = golden.set_index("id")


def gold(row: str, col: str):
    return float(_g.loc[row, col]) if row in _g.index else None


for _row, _col, _label, _got, _tol in (
        ("__TOTAL_PATRIMONIO__", "value_start", f"PATRIMONIO {br_date(START)}",
         pack.total_value_start, 0.05),
        ("__TOTAL_PATRIMONIO__", "return_pct", "PATRIMONIO retorno",
         pack.total_return_pct, 0.02),
        ("__TOTAL_INVESTIDO__", "return_pct", "INVESTIDO retorno",
         pack.invested_return_pct, 0.02),
        ("__CDI__", "return_pct", "CDI do periodo", pack.cdi_return_pct, 0.02),
        ("__IBOV__", "return_pct", "IBOV do periodo", pack.ibov_return_pct, 0.02)):
    _exp = gold(_row, _col)
    if _exp is not None:
        chk(_label, _got, _exp, _tol)

print(f"{'posicao':22} {'31/03':>13} {'30/04':>13} {'peso':>7} {'retorno':>9} {'contrib':>9}")
for m in sorted(metrics, key=lambda x: -x.contribution_pp):
    print(f"{m.instrument_id:22} {m.value_start:13,.2f} {m.value_end:13,.2f} "
          f"{m.weight_pct:6.2f}% {m.return_pct:8.2f}% {m.contribution_pp:+8.3f}pp")
print("-" * 80)
print(f"{'PATRIMONIO':22} {pack.total_value_start:13,.2f} {pack.total_value_end:13,.2f} "
      f"{'':7} {pack.total_return_pct:8.2f}%")
print(f"{'  investido':22} {pack.invested_value_start:13,.2f} {pack.invested_value_end:13,.2f} "
      f"{'':7} {pack.invested_return_pct:8.2f}%")
print(f"\nCDI {pack.cdi_return_pct:.2f}%   Ibovespa {pack.ibov_return_pct:.2f}%   "
      f"Benchmark ({pack.benchmark_name}) {pack.benchmark_return_pct:.2f}%")
print(f"Excesso sobre o benchmark: {pack.excess_return_pp:+.2f} pp")
print(f"Caixa: R$ {pack.cash_value:,.2f} ({pack.cash_pct_of_total:.1f}% do patrimonio)")
print(f"Cobertura de marcacao: {pack.coverage_pct:.1f}% do investido")
print(f"\n--- validacao contra {golden_path.name}")
for label, got, exp, ok in checks:
    print(f"  {'OK ' if ok else 'ERRO'} {label:30} obtido={got:>14,.2f}  esperado={exp:>14,.2f}")
print(f"\nmetrics_pack.json -> {OUT / 'metrics_pack.json'}")
print("VALIDACAO OK" if fails == 0 else f"VALIDACAO FALHOU: {fails} divergencias")
