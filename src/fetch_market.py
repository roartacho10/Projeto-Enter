"""
L0 market data loader - Enter case.
Fetches equity prices (Yahoo) and BCB SGS macro series into data/raw/.
Run from anywhere: python fetch_market.py
Requires: pip install yfinance requests pandas
"""
from pathlib import Path
from datetime import datetime, timezone
import json
import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent      # .../Projeto Enter
RAW_MKT = ROOT / "data" / "raw" / "market"
RAW_BCB = ROOT / "data" / "raw" / "bcb"
for p in (RAW_MKT, RAW_BCB):
    p.mkdir(parents=True, exist_ok=True)

NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")
manifest = []

# ---------------------------------------------------------------- equities
# Window is deliberately wider than March-April so the last business day of
# each month is always inside it, holidays included.
TICKERS = {
    "LREN3": "LREN3.SA",
    "MRFG3": "MBRF3.SA",  # ex-MRFG3: incorporacao da BRF em set/2025 aposentou o ticker antigo.
                          # Vendors indexam o historico pelo simbolo ATUAL, entao MRFG3.SA retorna 404
                          # mesmo para datas em que MRFG3 era o ticker vigente.
    "AZZA3": "AZZA3.SA",   # ex-ARZZ3, incorporacao do Grupo Soma em 01/08/2024
    "HAPV3": "HAPV3.SA",
    "IBOV":  "^BVSP",      # benchmark
}
START, END = "2025-03-15", "2025-05-06"

for instrument_id, symbol in TICKERS.items():
    t = yf.Ticker(symbol)
    # auto_adjust=False e OBRIGATORIO: o default do yfinance 1.x sobrescreve
    # Close com o preco ajustado sem avisar.
    h = t.history(start=START, end=END, auto_adjust=False)
    if h.empty:
        print(f"!! {instrument_id} ({symbol}): VAZIO - conferir o ticker")
        continue
    h = h.reset_index()
    h.insert(0, "instrument_id", instrument_id)
    h.insert(1, "symbol", symbol)
    h["source"] = "yahoo"
    h["retrieved_at"] = NOW
    out = RAW_MKT / f"{instrument_id}_{START}_{END}.csv"
    h.to_csv(out, index=False, encoding="utf-8")
    manifest.append({"file": out.name, "instrument_id": instrument_id, "symbol": symbol,
                     "source_url": f"yfinance:{symbol}", "rows": len(h), "retrieved_at": NOW})
    div = h.loc[h.get("Dividends", pd.Series(dtype=float)).fillna(0) > 0, ["Date", "Dividends"]]
    print(f"ok {instrument_id:6} {len(h):3} pregoes  {h['Date'].min().date()} -> {h['Date'].max().date()}"
          f"  proventos no periodo: {len(div)}")

# --- prova empirica da relacao de troca ARZZ3 -> AZZA3 (deve ser 1:1)
# ARZZ3.SA retorna 404 (ticker aposentado). AZZA3.SA carrega a serie continua,
# inclusive datas anteriores a 01/08/2024 -> a troca foi 1:1 e a quantidade nao muda.
for instrument_id, symbol, a, b in [("AZZA3_debut", "AZZA3.SA", "2024-07-15", "2024-08-15")]:
    h = yf.Ticker(symbol).history(start=a, end=b, auto_adjust=False)
    if h.empty:
        print(f"-- {instrument_id}: sem historico"); continue
    h = h.reset_index(); h.insert(0, "instrument_id", instrument_id)
    h["source"] = "yahoo"; h["retrieved_at"] = NOW
    out = RAW_MKT / f"{instrument_id}_merger_check.csv"
    h.to_csv(out, index=False, encoding="utf-8")
    manifest.append({"file": out.name, "instrument_id": instrument_id, "symbol": symbol,
                     "source_url": f"yfinance:{symbol}", "rows": len(h), "retrieved_at": NOW})
    print(f"ok {instrument_id:13} {h['Date'].min().date()} -> {h['Date'].max().date()}  ultimo close {h['Close'].iloc[-1]:.2f}")

# ---------------------------------------------------------------- BCB SGS
SERIES = {12: "CDI_diario_pct", 11: "Selic_diaria_pct", 432: "Selic_meta_aa", 433: "IPCA_mensal_pct"}
BASE = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{cod}/dados"
for cod, name in SERIES.items():
    url = BASE.format(cod=cod)
    params = {"formato": "json", "dataInicial": "01/01/2024", "dataFinal": "31/05/2025"}
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    df = pd.DataFrame(r.json())
    df["serie"] = cod; df["nome"] = name; df["source"] = "bcb_sgs"; df["retrieved_at"] = NOW
    out = RAW_BCB / f"sgs_{cod}_{name}.csv"
    df.to_csv(out, index=False, encoding="utf-8")
    manifest.append({"file": out.name, "instrument_id": name, "symbol": f"SGS:{cod}",
                     "source_url": r.url, "rows": len(df), "retrieved_at": NOW})
    print(f"ok SGS {cod:4} {name:18} {len(df):5} pontos")

pd.DataFrame(manifest).to_csv(RAW_MKT.parent / "_manifest.csv", index=False, encoding="utf-8")
print(f"\nmanifesto: {RAW_MKT.parent / '_manifest.csv'}")
