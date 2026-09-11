"""
L0 - Twenty-four months of monthly history.

Same discipline as fetch_market.py, wider window: every figure here is a
published close, a published quota or a published rate. Nothing is
interpolated, and a month that no source covers is simply absent - history.py
reports the hole rather than filling it.

The expensive part is the CVM bulk files: quotas are only published inside the
whole month's informe, so one download per month. This script keeps none of
them. It extracts the handful of rows the funds need, writes them to a small
versioned CSV, and deletes the archive, so the repository stays light and the
pipeline still runs on a host that can reach nothing.

Resumable: months already in the extract are skipped. Interrupt it and run it
again.

    python src/fetch_history.py              # everything missing
    python src/fetch_history.py --no-cvm     # equities and macro only (no bulk downloads)
    python src/fetch_history.py --from 2024-01
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import argparse
import io
import sys
import zipfile

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "data" / "reference"
RAW = ROOT / "data" / "raw"
OUT = RAW / "history_extract" / "monthly_series.csv"
CACHE = RAW / "_cvm_cache"                    # annual archives only, gitignored
CVM_DIRS = [RAW / "cvm", ROOT / "CSVs exportados CVM"]

# 25 month-ends give 24 monthly returns. The window ends where the statement does.
HIST_START, HIST_END = "2023-04", "2025-04"
NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")

CVM_FI = "https://dados.cvm.gov.br/dados/FI/DOC/INF_DIARIO/DADOS"
CVM_FIDC = "https://dados.cvm.gov.br/dados/FIDC/DOC/INF_MENSAL/DADOS"
SGS = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{cod}/dados"

rows: list[dict] = []


def months(a: str, b: str) -> list[str]:
    return [d.strftime("%Y-%m") for d in pd.period_range(a, b, freq="M").to_timestamp()]


def digits(x) -> str:
    return "".join(ch for ch in str(x) if ch.isdigit())


def add(instrument_id, month, date, value, kind, source, note=""):
    rows.append({"instrument_id": instrument_id, "month": month, "date": str(date),
                 "value": float(value), "kind": kind, "source": source,
                 "retrieved_at": NOW, "note": note})


def local_csv(name: str) -> tuple[bytes, str] | None:
    """
    Anything already on disk wins over the network. A file dropped in by hand
    counts whether it was unzipped or not: the loose CSV first, then any zip in
    those folders that contains it. Downloading a month manually should not also
    mean unzipping 50 MB of it.
    """
    for d in CVM_DIRS:
        f = d / name
        if f.exists():
            return f.read_bytes(), f.name
    for d in CVM_DIRS:
        if not d.is_dir():
            continue
        for z in sorted(d.glob("*.zip")):
            try:
                inner = member(z.read_bytes(), name)
            except (zipfile.BadZipFile, OSError):
                continue
            if inner is not None:
                return inner, f"{z.name} -> {name}"
    return None


def get(url: str, timeout=180) -> requests.Response | None:
    """One attempt. A missing file is an answer, not an error to retry around."""
    try:
        r = requests.get(url, timeout=timeout, stream=True)
    except requests.RequestException as e:
        print(f"   !! {url.rsplit('/', 1)[-1]}: {type(e).__name__}")
        return None
    if r.status_code != 200:
        print(f"   -- {url.rsplit('/', 1)[-1]}: HTTP {r.status_code}")
        return None
    return r


def download(url: str) -> bytes | None:
    r = get(url)
    if r is None:
        return None
    buf, mb = io.BytesIO(), 0
    for chunk in r.iter_content(1 << 20):
        buf.write(chunk)
        mb += 1
        if mb % 10 == 0:
            print(f"      {mb} MB", end="\r", flush=True)
    print(f"      {mb} MB baixados   ")
    return buf.getvalue()


def member(blob: bytes, name: str) -> bytes | None:
    """Reads one CSV out of a zip, whatever depth CVM nested it at."""
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for n in z.namelist():
            if n.rsplit("/", 1)[-1].lower() == name.lower():
                return z.read(n)
        # annual archives hold monthly zips
        for n in z.namelist():
            if n.lower().endswith(".zip"):
                inner = member(z.read(n), name)
                if inner is not None:
                    return inner
    return None


def cvm_csv(ym: str, fname: str, monthly_url: str, hist_url: str) -> bytes | None:
    """Local file, then the month's archive, then the year's archive."""
    hit = local_csv(fname)
    if hit is not None:
        blob, where = hit
        print(f"   (local) {where}")
        return blob
    blob = download(monthly_url)
    if blob is not None:
        return member(blob, fname)
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / hist_url.rsplit("/", 1)[-1]
    if not cached.exists():
        print(f"   arquivo mensal indisponivel; tentando o anual {cached.name}")
        blob = download(hist_url)
        if blob is None:
            return None
        cached.write_bytes(blob)
    return member(cached.read_bytes(), fname)


# ---------------------------------------------------------------- arguments
ap = argparse.ArgumentParser()
ap.add_argument("--from", dest="start", default=HIST_START)
ap.add_argument("--to", dest="end", default=HIST_END)
ap.add_argument("--no-cvm", action="store_true", help="pula os arquivos volumosos da CVM")
args = ap.parse_args()
WANT = months(args.start, args.end)

instruments = pd.read_csv(REF / "instruments.csv", dtype={"cnpj": str}).fillna("")
actions = pd.read_csv(REF / "corporate_actions.csv").fillna("")
PERIOD_END = "2025-04-30"
adj = {a["instrument_id"]: float(a["ratio"]) for _, a in actions.iterrows()
       if str(a["event_type"]) in ("split", "reverse_split") and str(a["event_date"]) > PERIOD_END}

have: set[tuple[str, str]] = set()
if OUT.exists():
    prev = pd.read_csv(OUT, dtype=str)
    have = set(zip(prev["instrument_id"], prev["month"]))
    rows.extend(prev.assign(value=prev["value"].astype(float)).to_dict("records"))
    print(f"extrato existente: {len(prev)} linhas, {prev['instrument_id'].nunique()} instrumentos\n")

# ---------------------------------------------------------------- equities
print(f"== acoes e indice  ({WANT[0]} -> {WANT[-1]})")
try:
    import yfinance as yf
except ImportError:
    yf = None
    print("   !! yfinance nao instalado: pip install yfinance")

eq = instruments[instruments["type"].isin(["stock", "index"])] if yf is not None else instruments.iloc[0:0]
start = (pd.Period(WANT[0]) - 1).to_timestamp().strftime("%Y-%m-%d")
end = (pd.Period(WANT[-1]) + 1).to_timestamp().strftime("%Y-%m-%d")
for _, ins in eq.iterrows():
    iid = ins["instrument_id"]
    symbol = ins["ticker_current"] if iid == "IBOV" else f"{ins['ticker_current']}.SA"
    if all((iid, m) in have for m in WANT):
        print(f"   {iid:6} ja no extrato")
        continue
    try:
        h = yf.Ticker(symbol).history(start=start, end=end, auto_adjust=False)
    except Exception as e:                                   # noqa: BLE001
        print(f"   !! {iid} ({symbol}): {type(e).__name__} - {e}")
        continue
    if h.empty:
        print(f"   !! {iid} ({symbol}): VAZIO")
        continue
    h = h.reset_index()
    h["date"] = pd.to_datetime(h["Date"], utc=True).dt.tz_convert("America/Sao_Paulo").dt.date
    h["month"] = pd.to_datetime(h["date"]).dt.strftime("%Y-%m")
    factor = adj.get(iid, 1.0)
    eom = h.sort_values("date").groupby("month").tail(1)
    n = 0
    for _, r in eom.iterrows():
        if r["month"] not in WANT or (iid, r["month"]) in have:
            continue
        add(iid, r["month"], r["date"], float(r["Close"]) / factor, "price", "yfinance",
            f"fechamento do ultimo pregao do mes; dividido por {factor:g}" if factor != 1
            else "fechamento do ultimo pregao do mes")
        n += 1
    print(f"   {iid:6} {symbol:10} {n} meses"
          f"{'  (preco do vendor dividido por %g)' % factor if factor != 1 else ''}")

# ---------------------------------------------------------------- macro
print("\n== series macro (BCB)")
for cod, name, kind in ((12, "CDI", "return_pct"), (433, "IPCA", "return_pct")):
    iid = f"MACRO_{name}"
    if all((iid, m) in have for m in WANT):
        print(f"   {iid:12} ja no extrato")
        continue
    p0 = (pd.Period(WANT[0]) - 1).to_timestamp()
    p1 = (pd.Period(WANT[-1]) + 1).to_timestamp() - pd.Timedelta(days=1)
    try:
        r = requests.get(SGS.format(cod=cod), timeout=90, params={
            "formato": "json", "dataInicial": p0.strftime("%d/%m/%Y"),
            "dataFinal": p1.strftime("%d/%m/%Y")})
        r.raise_for_status()
        d = pd.DataFrame(r.json())
    except Exception as e:                                   # noqa: BLE001
        print(f"   !! SGS {cod}: {type(e).__name__} - {e}")
        continue
    d["date"] = pd.to_datetime(d["data"], format="%d/%m/%Y").dt.date
    d["month"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m")
    d["v"] = d["valor"].astype(float)
    n = 0
    for m, g in d.groupby("month"):
        if m not in WANT or (iid, m) in have:
            continue
        # CDI is published daily: compound it into the month's return.
        v = ((1 + g["v"] / 100).prod() - 1) * 100 if cod == 12 else g["v"].iloc[-1]
        add(iid, m, g["date"].max(), v, kind, f"bcb_sgs_{cod}",
            "CDI diario capitalizado no mes" if cod == 12 else "IPCA do mes")
        n += 1
    print(f"   {iid:12} SGS {cod:4} {n} meses")

# ---------------------------------------------------- funds: informe diario
if args.no_cvm:
    print("\n== fundos: PULADO (--no-cvm)")
else:
    print("\n== fundos: informe diario da CVM  (um arquivo por mes)")
    daily = instruments[(instruments["type"] == "fund") &
                        (instruments["pricing_source"] == "cvm_inf_diario")]
    wanted = {digits(r["cnpj"]): r["instrument_id"] for _, r in daily.iterrows()}
    for ym in WANT:
        tag = ym.replace("-", "")
        if all((iid, ym) in have for iid in wanted.values()):
            print(f"   {ym} ja no extrato")
            continue
        print(f"   {ym}")
        blob = cvm_csv(ym, f"inf_diario_fi_{tag}.csv",
                       f"{CVM_FI}/inf_diario_fi_{tag}.zip",
                       f"{CVM_FI}/HIST/inf_diario_fi_{ym[:4]}.zip")
        if blob is None:
            print(f"   !! {ym}: informe diario indisponivel")
            continue
        head = pd.read_csv(io.BytesIO(blob), sep=";", encoding="latin-1", nrows=0)
        cnpj_col = "CNPJ_FUNDO_CLASSE" if "CNPJ_FUNDO_CLASSE" in head.columns else "CNPJ_FUNDO"
        d = pd.read_csv(io.BytesIO(blob), sep=";", encoding="latin-1", dtype=str,
                        usecols=[cnpj_col, "DT_COMPTC", "VL_QUOTA"])
        d["k"] = d[cnpj_col].map(digits)
        d = d[d["k"].isin(wanted)].dropna(subset=["VL_QUOTA"])
        for k, g in d.groupby("k"):
            iid = wanted[k]
            if (iid, ym) in have:
                continue
            last = g.sort_values("DT_COMPTC").iloc[-1]
            add(iid, ym, last["DT_COMPTC"], float(last["VL_QUOTA"]), "price",
                "cvm_inf_diario", f"cota do ultimo dia util do mes ({cnpj_col})")
        found = d["k"].nunique()
        print(f"      {found}/{len(wanted)} fundos")
        if found < len(wanted):
            missing = [wanted[k] for k in wanted if k not in set(d["k"])]
            print(f"      sem cota em {ym}: {', '.join(missing)}")

    # ------------------------------------------- funds: informe mensal FIDC
    print("\n== fundos: informe mensal FIDC")
    fidc = instruments[instruments["pricing_source"] == "cvm_inf_mensal_fidc"]
    for _, ins in fidc.iterrows():
        iid, key = ins["instrument_id"], digits(ins["cnpj"])
        for ym in WANT:
            tag = ym.replace("-", "")
            if (iid, ym) in have:
                continue
            blob = cvm_csv(ym, f"inf_mensal_fidc_tab_X_3_{tag}.csv",
                           f"{CVM_FIDC}/inf_mensal_fidc_{tag}.zip",
                           f"{CVM_FIDC}/HIST/inf_mensal_fidc_{ym[:4]}.zip")
            if blob is None:
                continue
            d = pd.read_csv(io.BytesIO(blob), sep=";", encoding="latin-1", dtype=str)
            col = "CNPJ_FUNDO_CLASSE" if "CNPJ_FUNDO_CLASSE" in d.columns else "CNPJ_FUNDO"
            d = d[d[col].map(digits) == key]
            if d.empty:
                continue
            # The senior series is the live one; the subordinated class is zeroed.
            sen = d[d["TAB_X_CLASSE_SERIE"].str.contains("Senior", case=False, na=False)]
            pick = sen if len(sen) == 1 else d
            if len(pick) != 1:
                print(f"   !! {iid} {ym}: {len(pick)} classes, ambiguo - pulado")
                continue
            r = pick.iloc[0]
            add(iid, ym, r["DT_COMPTC"], float(str(r["TAB_X_VL_RENTAB_MES"]).replace(",", ".")),
                "return_pct", "cvm_inf_mensal_fidc",
                f"rentabilidade publicada da classe '{r['TAB_X_CLASSE_SERIE'].strip()}'")
            print(f"   {iid} {ym}: {r['TAB_X_VL_RENTAB_MES']}%")

# ---------------------------------------------------------------- persist
if not rows:
    print("\nNada coletado.")
    sys.exit(1)
df = (pd.DataFrame(rows)
      .drop_duplicates(subset=["instrument_id", "month"], keep="last")
      .sort_values(["instrument_id", "month"]))
OUT.parent.mkdir(parents=True, exist_ok=True)
df.to_csv(OUT, index=False, encoding="utf-8")

print("\n" + "=" * 64)
print(f"extrato  {OUT.relative_to(ROOT)}  ({len(df)} linhas, {OUT.stat().st_size/1024:.0f} KB)")
cov = df.groupby("instrument_id")["month"].agg(["count", "min", "max"])
for iid, r in cov.iterrows():
    bar = "#" * r["count"]
    print(f"  {iid:20} {r['count']:3} meses  {r['min']} -> {r['max']}  {bar}")
print("=" * 64)
print(f"{len(WANT)} meses pedidos. Rode de novo para completar o que faltou.")
