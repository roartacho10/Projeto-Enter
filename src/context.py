"""
Which client the pipeline is running for, and where its outputs go.

Prices and the security master are shared across clients and stay in
data/processed; everything a run produces is per-client, under output/<id>.
Selecting a client through the environment keeps every stage a plain script
that can be run alone, which is what the app and run.py rely on.
"""
from __future__ import annotations
from pathlib import Path
import os
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "data" / "reference"
PROC = ROOT / "data" / "processed"

CLIENT_ID = os.environ.get("CLIENT_ID", "ALBERT")
OUT = ROOT / "output" / CLIENT_ID
OUT.mkdir(parents=True, exist_ok=True)


def clients() -> pd.DataFrame:
    return pd.read_csv(REF / "clients.csv", dtype=str).fillna("").set_index("client_id")


def client(cid: str | None = None) -> pd.Series:
    return clients().loc[cid or CLIENT_ID]


def policy_path() -> Path:
    """
    The client's own derived policy when there is one, the shared file when
    there is not. A client with no risk-profile document falls back to generic
    limits rather than silently borrowing another client's.
    """
    own = OUT / "suitability_policy.csv"
    return own if own.exists() else REF / "suitability_policy.csv"


# ---------------------------------------------------------------- period
# The reference month was written into five places across three files, so
# running May meant editing code. It is a property of the data, not of the
# program: clients.csv already declares it per client. PERIOD overrides it for
# a one-off run without touching the registry.
MESES_PT = ["janeiro", "fevereiro", "março", "abril", "maio", "junho",
            "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]


def period_id(cid: str | None = None) -> str:
    """The reference month (YYYY-MM) this run is about."""
    return os.environ.get("PERIOD", "").strip() or str(client(cid)["period"]).strip()


def periods() -> list[str]:
    """Every reference month on the roster - what the shared stages must cover."""
    env = os.environ.get("PERIOD", "").strip()
    if env:
        return [env]
    return sorted({str(p).strip() for p in clients()["period"] if str(p).strip()})


def period_bounds(period: str) -> tuple[str, str]:
    """
    Opening and closing marks of the month, as ISO dates.

    Both are calendar month-ends. Pricing looks up the last quote on or before
    a date, so a month-end that falls on a weekend resolves to the last trading
    day without needing a holiday calendar here.
    """
    p = pd.Period(period, freq="M")
    return str((p - 1).end_time.date()), str(p.end_time.date())


def period_months(period: str, back: int = 1) -> list[str]:
    """YYYYMM of the period and the `back` months before it, oldest first."""
    p = pd.Period(period, freq="M")
    return [(p - i).strftime("%Y%m") for i in range(back, -1, -1)]


def period_label(period: str) -> tuple[str, str]:
    """Month name in Portuguese and the year, for the letter's furniture."""
    p = pd.Period(period, freq="M")
    return MESES_PT[p.month - 1].capitalize(), str(p.year)


def br_date(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{d}/{m}/{y}"
