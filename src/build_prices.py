"""
L0 - Build the canonical price table.

Reads the curated reference files and the raw downloads, and produces ONE
long-format price table plus a data quality log. Loaders differ per source;
the destination schema does not.

Run: python src/build_prices.py
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from contracts import DataQualityIssue  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "data" / "reference"
RAW = ROOT / "data" / "raw"
PROC = ROOT / "data" / "processed"
PROC.mkdir(parents=True, exist_ok=True)

# CVM bulk files: preferred location first, then the folder as downloaded.
CVM_DIRS = [RAW / "cvm", ROOT / "CSVs exportados CVM"]
# The CVM bulk files are ~230 MB and are not versioned. Every run that has them
# writes out the handful of rows the pipeline actually used; a run without them
# reads that extract instead. Same numbers, 30 KB, and the project stays
# runnable on a host that has no access to the originals.
CVM_EXTRACT = RAW / "cvm_extract" / "fund_quotas.csv"
PERIOD_START, PERIOD_END = "2025-03-31", "2025-04-30"
RUN_ID = datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")

issues: list[DataQualityIssue] = []
prices: list[dict] = []


def log(severity, code, message, instrument_id=None):
    issues.append(DataQualityIssue(run_id=RUN_ID, severity=severity, code=code,
                                   message=message, instrument_id=instrument_id))


def cvm_file(name: str) -> Path | None:
    for d in CVM_DIRS:
        p = d / name
        if p.exists():
            return p
    return None


def digits(x) -> str:
    return "".join(ch for ch in str(x) if ch.isdigit())


# ---------------------------------------------------------------- reference
instruments = pd.read_csv(REF / "instruments.csv", dtype={"cnpj": str}).fillna("")
actions = pd.read_csv(REF / "corporate_actions.csv", dtype={"ratio": float}).fillna("")
print(f"[ref] {len(instruments)} instrumentos, {len(actions)} eventos societarios")

# A split/reverse split AFTER the analysis window means the data vendor has
# already back-adjusted the historical series. Divide the published price by
# the ratio to recover what was actually traded on the reference dates.
adj: dict[str, float] = {}
for _, a in actions.iterrows():
    if str(a["event_type"]) in ("reverse_split", "split") and str(a["event_date"]) > PERIOD_END:
        adj[a["instrument_id"]] = float(a["ratio"])
        log("info", "vendor_back_adjustment",
            f"{a['instrument_id']}: vendor prices divided by {a['ratio']} "
            f"({a['event_type']} on {a['event_date']}, after the period)", a["instrument_id"])

# ---------------------------------------------------------------- equities
for _, ins in instruments[instruments["type"].isin(["stock", "index"])].iterrows():
    iid = ins["instrument_id"]
    files = sorted((RAW / "market").glob(f"{iid}_2*.csv"))
    if not files:
        log("blocker", "missing_price_file", f"{iid}: no market file found", iid)
        continue
    d = pd.read_csv(files[-1])
    d["date"] = pd.to_datetime(d["Date"], utc=True).dt.tz_convert("America/Sao_Paulo").dt.date
    factor = adj.get(iid, 1.0)
    for _, r in d.iterrows():
        prices.append({"instrument_id": iid, "date": str(r["date"]),
                       "price": float(r["Close"]) / factor, "source": "yfinance",
                       "retrieved_at": r.get("retrieved_at", ""),
                       "note": f"vendor Close divided by {factor}" if factor != 1 else ""})

cvm_rows_before = len(prices)

# ---------------------------------------------------- funds: informe diario
daily = instruments[(instruments["type"] == "fund") &
                    (instruments["pricing_source"] == "cvm_inf_diario")]
wanted = {digits(r["cnpj"]): r["instrument_id"] for _, r in daily.iterrows()}
for ym in ("202404", "202503", "202504"):
    f = cvm_file(f"inf_diario_fi_{ym}.csv")
    if f is None:
        log("warning", "missing_cvm_file", f"inf_diario_fi_{ym}.csv not found")
        continue
    d = pd.read_csv(f, sep=";", encoding="latin-1", dtype=str,
                    usecols=["CNPJ_FUNDO_CLASSE", "DT_COMPTC", "VL_QUOTA"])
    d["k"] = d["CNPJ_FUNDO_CLASSE"].map(digits)
    d = d[d["k"].isin(wanted)]
    for _, r in d.iterrows():
        prices.append({"instrument_id": wanted[r["k"]], "date": r["DT_COMPTC"],
                       "price": float(r["VL_QUOTA"]), "source": "cvm_inf_diario",
                       "retrieved_at": "", "note": ""})
    print(f"[cvm] inf_diario {ym}: {len(d)} linhas")

# ------------------------------------------------- funds: informe mensal FIDC
fidc = instruments[instruments["pricing_source"] == "cvm_inf_mensal_fidc"]
for _, ins in fidc.iterrows():
    iid, key = ins["instrument_id"], digits(ins["cnpj"])
    for ym in ("202503", "202504"):
        fq, fp = cvm_file(f"inf_mensal_fidc_tab_X_2_{ym}.csv"), cvm_file(f"inf_mensal_fidc_tab_IV_{ym}.csv")
        if fq is None or fp is None:
            log("warning", "missing_cvm_file", f"FIDC tabs for {ym} not found", iid)
            continue
        q = pd.read_csv(fq, sep=";", encoding="latin-1", dtype=str)
        p = pd.read_csv(fp, sep=";", encoding="latin-1", dtype=str)
        q = q[q["CNPJ_FUNDO_CLASSE"].map(digits) == key]
        p = p[p["CNPJ_FUNDO_CLASSE"].map(digits) == key]
        live = q[q["TAB_X_QT_COTA"].astype(float) > 0]
        if len(live) != 1 or p.empty:
            log("blocker", "fidc_class_ambiguous",
                f"{iid} {ym}: expected exactly one live quota class, found {len(live)}", iid)
            continue
        row, pl = live.iloc[0], float(p.iloc[0]["TAB_IV_A_VL_PL"])
        # The published VL_COTA is rounded to 2 decimals, which on a ~R$1.76
        # quota distorts a monthly return by more than the return itself.
        # PL / quota count recovers full precision.
        quota = pl / float(row["TAB_X_QT_COTA"])
        prices.append({"instrument_id": iid, "date": row["DT_COMPTC"], "price": quota,
                       "source": "cvm_inf_mensal_fidc", "retrieved_at": "",
                       "note": f"derived PL/QT_COTA; published VL_COTA={row['TAB_X_VL_COTA']} (2dp)"})
        print(f"[cvm] fidc {iid} {ym}: cota={quota:.6f} (classe '{row['TAB_X_CLASSE_SERIE'].strip()}')")

# --------------------------------------------- offline extract: write or read
cvm_rows = prices[cvm_rows_before:]
if cvm_rows:
    CVM_EXTRACT.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(cvm_rows).to_csv(CVM_EXTRACT, index=False, encoding="utf-8")
    print(f"[cvm] extrato gravado: {len(cvm_rows)} linhas -> {CVM_EXTRACT.name}")
elif CVM_EXTRACT.exists():
    ext = pd.read_csv(CVM_EXTRACT, dtype={"date": str}).fillna("")
    prices.extend(ext.to_dict("records"))
    log("info", "offline_extract",
        f"arquivos volumosos da CVM ausentes; usando o extrato versionado "
        f"({len(ext)} linhas, {CVM_EXTRACT.name})")
    print(f"[cvm] MODO OFFLINE: {len(ext)} linhas lidas do extrato versionado")
else:
    log("blocker", "no_cvm_data",
        "sem arquivos da CVM e sem extrato: os fundos ficam sem preco")

# ---------------------------------------------------------------- indicators
indicators: list[dict] = []
for f in sorted((RAW / "bcb").glob("sgs_*.csv")):
    d = pd.read_csv(f, dtype=str)
    for _, r in d.iterrows():
        indicators.append({"series_id": r["serie"], "name": r["nome"],
                           "date": str(pd.to_datetime(r["data"], format="%d/%m/%Y").date()),
                           "value": float(r["valor"]), "source": "bcb_sgs"})
    print(f"[bcb] {f.name}: {len(d)} pontos")
if not indicators:
    log("blocker", "missing_indicators", "no BCB series found - benchmark cannot be built")

# ------------------------------------------- post-fixed fixed income (CDI)
# Priced by compounding the Bacen CDI series into an index starting at 1.0.
# No invented curve: the return of these instruments is the published CDI.
cdi_acc = instruments[instruments["pricing_source"] == "cdi_accrual"]
if len(cdi_acc):
    cdi = [i for i in indicators if i["series_id"] == "12"]
    if not cdi:
        log("blocker", "no_cdi_series", "serie CDI 12 ausente: renda fixa pos-fixada sem preco")
    else:
        cdi.sort(key=lambda r: r["date"])
        idx, acc = [], 1.0
        for r in cdi:
            acc *= 1 + r["value"] / 100
            idx.append((r["date"], acc))
        for _, ins in cdi_acc.iterrows():
            for d, v in idx:
                prices.append({"instrument_id": ins["instrument_id"], "date": d, "price": v,
                               "source": "bcb_sgs_12_accrual", "retrieved_at": "",
                               "note": "indice acumulado do CDI, base 1,0"})
        print(f"[cdi] {len(cdi_acc)} instrumentos pos-fixados, {len(idx)} datas")

# ---------------------------------------------------------------- validate
px = pd.DataFrame(prices).drop_duplicates(subset=["instrument_id", "date", "source"])
golden = pd.read_csv(REF / "golden_quotas.csv")
ok = fail = 0
for _, g in golden.iterrows():
    m = px[(px["instrument_id"] == g["instrument_id"]) & (px["date"] == g["date"])]
    if m.empty:
        log("blocker", "golden_missing", f"{g['instrument_id']} {g['date']}: not produced", g["instrument_id"])
        fail += 1
        continue
    diff = abs(m.iloc[0]["price"] - float(g["quota"])) / float(g["quota"])
    if diff > 1e-4:
        log("blocker", "golden_mismatch",
            f"{g['instrument_id']} {g['date']}: got {m.iloc[0]['price']:.8f}, expected {g['quota']}",
            g["instrument_id"])
        fail += 1
    else:
        ok += 1

# ---------------------------------------------------------------- persist
db = PROC / "enter.db"
con = sqlite3.connect(db)
con.executescript("""
DROP TABLE IF EXISTS prices; DROP TABLE IF EXISTS market_indicators;
DROP TABLE IF EXISTS data_quality_log;
CREATE TABLE prices (instrument_id TEXT, date TEXT, price REAL, source TEXT,
                     retrieved_at TEXT, note TEXT, PRIMARY KEY (instrument_id, date, source));
CREATE TABLE market_indicators (series_id TEXT, name TEXT, date TEXT, value REAL, source TEXT,
                     PRIMARY KEY (series_id, date));
CREATE TABLE data_quality_log (run_id TEXT, severity TEXT, instrument_id TEXT, code TEXT, message TEXT);
""")
con.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?)",
                [(r["instrument_id"], r["date"], r["price"], r["source"], r["retrieved_at"], r["note"])
                 for r in px.to_dict("records")])
con.executemany("INSERT OR REPLACE INTO market_indicators VALUES (?,?,?,?,?)",
                [(i["series_id"], i["name"], i["date"], i["value"], i["source"]) for i in indicators])
con.executemany("INSERT INTO data_quality_log VALUES (?,?,?,?,?)",
                [(i.run_id, i.severity, i.instrument_id, i.code, i.message) for i in issues])
con.commit(); con.close()
px.to_csv(PROC / "prices.csv", index=False, encoding="utf-8")
pd.DataFrame([i.model_dump() for i in issues]).to_csv(PROC / "data_quality_log.csv", index=False, encoding="utf-8")

print("\n" + "=" * 64)
print(f"run_id            {RUN_ID}")
print(f"precos            {len(px)} linhas, {px['instrument_id'].nunique()} instrumentos")
print(f"indicadores       {len(indicators)} pontos")
print(f"golden quotas     {ok} ok / {fail} falhas  ({len(golden)} esperadas)")
for sev in ("blocker", "warning", "info"):
    n = sum(1 for i in issues if i.severity == sev)
    if n:
        print(f"  {sev:8} {n}")
        for i in [x for x in issues if x.severity == sev][:6]:
            print(f"      [{i.code}] {i.message}")
print(f"\nbanco  {db}")
print("=" * 64)
print("VALIDACAO OK" if fail == 0 else f"VALIDACAO FALHOU: {fail} divergencias")
