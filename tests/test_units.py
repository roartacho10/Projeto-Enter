"""
Pure functions, no pipeline needed. These are the ones that broke in practice.
"""
from __future__ import annotations
from datetime import date

import pandas as pd
import pytest

from context import (MESES_PT, br_date, period_bounds, period_label,  # noqa: E402
                     period_months)


# ---------------------------------------------------------------- period math
@pytest.mark.parametrize("period, start, end", [
    ("2025-04", "2025-03-31", "2025-04-30"),
    ("2025-01", "2024-12-31", "2025-01-31"),   # crosses the year
    ("2024-03", "2024-02-29", "2024-03-31"),   # leap February
    ("2025-03", "2025-02-28", "2025-03-31"),
])
def test_period_bounds(period, start, end):
    assert period_bounds(period) == (start, end)


def test_period_bounds_are_month_ends():
    for p in pd.period_range("2023-01", "2026-12", freq="M").strftime("%Y-%m"):
        a, b = period_bounds(p)
        assert date.fromisoformat(a) < date.fromisoformat(b)
        # the close is the last day of its own month
        assert (date.fromisoformat(b) + pd.Timedelta(days=1)).day == 1


def test_period_months_order_and_span():
    assert period_months("2025-04") == ["202503", "202504"]
    assert period_months("2025-01") == ["202412", "202501"]
    assert period_months("2025-04", 3) == ["202501", "202502", "202503", "202504"]


def test_period_label_is_portuguese():
    assert period_label("2025-04") == ("Abril", "2025")
    assert period_label("2024-12") == ("Dezembro", "2024")
    assert len(MESES_PT) == 12


def test_br_date():
    assert br_date("2025-04-30") == "30/04/2025"


# ------------------------------------------------- allocation bucket by state
def test_bucket_matured_vs_live(ran):
    """
    Idle capital is a STATE, not a product family: a matured bank CD and a live
    one share a risk_category. This test fails on the version that read the
    bucket off the category table.
    """
    import charts
    ins = pd.read_csv(ran / "data" / "reference" / "instruments.csv",
                      dtype=str).fillna("").set_index("instrument_id")
    cats = pd.read_csv(ran / "data" / "reference" / "risk_categories.csv",
                       dtype=str).fillna("").set_index("risk_category")
    end = date(2025, 4, 30)
    assert charts.bucket_of("CDB_C6_SET2024", ins, cats, end) == "Capital ocioso"
    assert charts.bucket_of("CASH_BRL", ins, cats, end) == "Capital ocioso"
    assert charts.bucket_of("RF_POS_A", ins, cats, end) == "Crédito privado e renda fixa"
    assert charts.bucket_of("LREN3", ins, cats, end) == "Renda variável"
