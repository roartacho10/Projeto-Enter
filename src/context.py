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
