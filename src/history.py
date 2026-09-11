"""
Monthly trajectory of the client's positions.

Turns the collected extract into one indexed series per position, plus CDI,
IPCA and Ibovespa as references. Two rules govern the whole module:

  1. A month with no published observation stays empty. Nothing is carried
     forward, interpolated or averaged, so a short series is short and says so.
  2. Quantities are held constant. The client made no contribution or
     withdrawal in the window by assumption, and that assumption is printed on
     the page rather than buried here.

Everything the extract already validated is re-validated: month-end quotas are
compared against the hand-checked answer key on every run.

Run: python src/history.py            (CLIENT_ID picks the client)
"""
from __future__ import annotations
from pathlib import Path
import json
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from context import CLIENT_ID, OUT, ROOT  # noqa: E402
from contracts import HistoryPack, HistoryPoint, HistorySeries  # noqa: E402

REF = ROOT / "data" / "reference"
EXTRACT = ROOT / "data" / "raw" / "history_extract" / "monthly_series.csv"

BASIS = {"cvm_inf_diario": "quota_month_end",
         "yfinance": "close_month_end",
         "cvm_inf_mensal_fidc": "reported_monthly_return",
         "bcb_sgs_12": "compounded_daily_rate",
         "bcb_sgs_433": "published_monthly_rate"}
BENCH = {"IBOV": "Ibovespa", "MACRO_CDI": "CDI", "MACRO_IPCA": "IPCA"}
ASSUMPTION = ("Trajetória calculada com as quantidades da posição atual mantidas constantes. "
              "Não considera aportes, resgates nem proventos reinvestidos no período.")


def fail(msg: str) -> None:
    print(f"\nFALHOU: {msg}")
    sys.exit(1)


if not EXTRACT.exists():
    # The trajectory enriches the report; it does not gate it. A checkout with
    # no extract yet should still produce a letter.
    print(f"[pula] extrato ausente ({EXTRACT.relative_to(ROOT)}).\n"
          f"       Para ter a trajetoria de 24 meses:  python src/fetch_history.py")
    sys.exit(0)

ext = pd.read_csv(EXTRACT, dtype={"month": str, "date": str})
instruments = pd.read_csv(REF / "instruments.csv", dtype={"cnpj": str}).fillna("")
positions = pd.read_csv(REF / "positions.csv")
positions = positions[positions["client_id"] == CLIENT_ID]
if positions.empty:
    fail(f"nenhuma posicao para {CLIENT_ID}")

names = dict(zip(instruments["instrument_id"], instruments["display_name"]))
types = dict(zip(instruments["instrument_id"], instruments["type"]))

WINDOW = sorted(ext["month"].unique())
START, END = WINDOW[0], WINDOW[-1]
ALL_MONTHS = [d.strftime("%Y-%m") for d in pd.period_range(START, END, freq="M").to_timestamp()]

# ---------------------------------------------------------------- validate
# The extract is the input to everything below, so it is checked here too,
# not only where it was written.
golden = pd.read_csv(REF / "golden_quotas.csv", dtype={"date": str})
px = ext[ext["kind"] == "price"]
chk = golden.merge(px, on=["instrument_id", "date"], how="inner")
bad = [(r["instrument_id"], r["date"], r["quota"], r["value"]) for _, r in chk.iterrows()
       if abs(r["quota"] - r["value"]) / r["quota"] > 1e-8]
if bad:
    for iid, d, want, got in bad:
        print(f"  [golden] {iid} {d}: esperado {want}, obtido {got}")
    fail(f"{len(bad)} cotas de fim de mes divergem do gabarito")
print(f"[valida] {len(chk)} cotas de fim de mes conferem com o gabarito")


def build(iid: str, kind: str) -> HistorySeries | None:
    d = ext[ext["instrument_id"] == iid].sort_values("month")
    if d.empty:
        return None
    src = d["source"].iloc[0]
    basis = BASIS.get(src)
    if basis is None:
        fail(f"{iid}: fonte '{src}' sem base declarada")

    pts: list[HistoryPoint] = []
    idx, prev_price = 100.0, None
    for i, (_, r) in enumerate(d.iterrows()):
        price = float(r["value"]) if r["kind"] == "price" else None
        if r["kind"] == "price":
            ret = None if prev_price is None else (price / prev_price - 1) * 100
            prev_price = price
        else:
            # Return-only source: the published figure is the month's own return.
            # It is reported at every point, but the first one cannot be
            # compounded - the index is anchored at that month's close, and the
            # anchor for the month before it was never published.
            ret = float(r["value"])
        if ret is not None and i > 0:
            idx *= 1 + ret / 100
        pts.append(HistoryPoint(month=r["month"], date=r["date"], price=price,
                                return_pct=None if ret is None else round(ret, 4),
                                index=round(idx, 4)))

    covered = [p.month for p in pts]
    missing = [m for m in ALL_MONTHS if m not in covered]
    note = ""
    if missing:
        note = (f"Série disponível de {covered[0]} a {covered[-1]}: "
                f"{len(missing)} dos {len(ALL_MONTHS)} meses da janela não têm publicação.")
    if basis == "reported_monthly_return":
        note = (note + " " if note else "") + (
            f"Fonte publica rentabilidade mensal, não cota: o índice parte de 100 no "
            f"fechamento de {covered[0]} e a rentabilidade desse primeiro mês aparece "
            f"informada, mas não entra no acumulado.")
    return HistorySeries(
        instrument_id=iid, display_name=names.get(iid, iid), kind=kind, basis=basis,
        source=src, months_covered=len(pts), first_month=covered[0], last_month=covered[-1],
        total_return_pct=round(pts[-1].index / pts[0].index * 100 - 100, 4),
        complete=not missing, gap_note=note, points=pts)


series: list[HistorySeries] = []
excluded: list[dict] = []

for _, p in positions.iterrows():
    iid = p["instrument_id"]
    if types.get(iid) == "cash":
        excluded.append({"instrument_id": iid, "display_name": names.get(iid, iid),
                         "reason": "Saldo em conta: não tem cotação."})
        continue
    s = build(iid, "position")
    if s is None:
        why = {"fixed_income": "Instrumento vencido, carregado pelo valor do extrato: "
                               "não há série de preço publicada.",
               "fund": "Sem publicação da CVM no período coletado.",
               "stock": "Sem série do provedor de mercado."}.get(types.get(iid, ""), "Sem série.")
        excluded.append({"instrument_id": iid, "display_name": names.get(iid, iid), "reason": why})
        continue
    series.append(s)

for iid, label in BENCH.items():
    s = build(iid, "benchmark")
    if s is None:
        print(f"[aviso] referencia {label} ausente do extrato")
        continue
    s.display_name = label
    series.append(s)

pack = HistoryPack(client_id=CLIENT_ID, window_start=START, window_end=END,
                   months_requested=len(ALL_MONTHS), assumption=ASSUMPTION,
                   series=series, excluded=excluded)

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "history.json").write_text(
    json.dumps(json.loads(pack.model_dump_json()), ensure_ascii=False, indent=2),
    encoding="utf-8")

# ---------------------------------------------------------------- report
print(f"\n{'=' * 72}")
print(f"{CLIENT_ID}  ·  janela {START} a {END}  ({len(ALL_MONTHS)} meses)")
print("=" * 72)
for s in series:
    tag = "" if s.complete else f"  <- {len(ALL_MONTHS) - s.months_covered} meses sem dado"
    mark = " " if s.kind == "position" else "~"
    print(f" {mark}{s.display_name[:34]:34} {s.months_covered:3}m  "
          f"{s.first_month}->{s.last_month}  {s.total_return_pct:+8.2f}%{tag}")
if excluded:
    print("\n sem trajetoria:")
    for e in excluded:
        print(f"   {e['display_name'][:34]:34} {e['reason']}")
print(f"\n{OUT / 'history.json'}")
