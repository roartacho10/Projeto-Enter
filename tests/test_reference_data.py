"""
Integrity of the curated reference data.

data/reference is where the hard-won knowledge lives, and it is edited by hand.
These checks are cheap and catch the class of mistake that actually happened:
a category row nobody referenced any more, and an instrument that a position
pointed at without existing in the security master.
"""
from __future__ import annotations
from pathlib import Path

import pandas as pd
import pytest

REF = Path(__file__).resolve().parent.parent / "data" / "reference"
BASES = {"statement_qty", "derive_from_value", "return_only", "value_only"}


@pytest.fixture(scope="module")
def ref():
    return {p.stem: pd.read_csv(p, dtype=str).fillna("") for p in REF.glob("*.csv")}


def test_every_position_has_an_instrument(ref):
    orphans = set(ref["positions"]["instrument_id"]) - set(ref["instruments"]["instrument_id"])
    assert not orphans, f"posicoes apontando para instrumento inexistente: {orphans}"


def test_every_instrument_category_exists(ref):
    unknown = set(ref["instruments"]["risk_category"]) - set(
        ref["risk_categories"]["risk_category"])
    assert not unknown, f"risk_category fora da tabela: {unknown}"


def test_no_unused_risk_category(ref):
    """A row nobody references is stale reference data, not documentation."""
    used = set(ref["instruments"]["risk_category"])
    declared = set(ref["risk_categories"]["risk_category"])
    assert not (declared - used), f"categorias declaradas e nao usadas: {declared - used}"


def test_quantity_basis_is_declared(ref):
    bad = set(ref["positions"]["quantity_basis"]) - BASES
    assert not bad, f"quantity_basis nao declarado em compute_metrics: {bad}"


def test_every_client_has_a_period(ref):
    empty = ref["clients"][ref["clients"]["period"] == ""]["client_id"].tolist()
    assert not empty, f"clientes sem periodo de referencia: {empty}"


def test_profile_tables_cover_every_class(ref):
    classes = set(ref["profile_rating_floor"]["risk_class"])
    for table, col in (("profile_limits", "risk_class"), ("profile_families", "risk_class")):
        missing = classes - set(ref[table][col])
        assert not missing, f"{table} nao cobre {missing}"


def test_limits_matrix_is_complete_and_monotone(ref):
    d = ref["profile_limits"].copy()
    for c in ("max_equity_pct",):
        d[c] = d[c].astype(float)
    horizons = ["Curto", "Medio", "Longo"]
    for rc, g in d.groupby("risk_class"):
        assert set(g["horizon"]) == set(horizons), f"{rc}: matriz incompleta"
        eq = [g[g.horizon == h]["max_equity_pct"].iloc[0] for h in horizons]
        # Longer horizon: more equity tolerated, less idle cash. Declared shape.
        assert eq == sorted(eq), f"{rc}: teto de RV nao cresce com o horizonte: {eq}"


def test_corporate_action_ratios_are_positive(ref):
    r = ref["corporate_actions"]["ratio"].astype(float)
    assert (r > 0).all(), "ratio de evento societario deve ser positivo"


def test_product_cap_is_user_calibrated(ref):
    """Exact basket targets replace the legacy minimum-ticket heuristic."""
    rpol = ref["rebalance_policy"].set_index("param")
    assert float(rpol.loc["MAX_PRODUCT_PCT", "value"]) == 25
    assert "MIN_TICKET_BRL" not in rpol.index


def test_cash_target_is_within_the_idle_limit(ref):
    """
    The rebalance deploys down to TARGET_CASH_PCT; the suitability rule flags
    cash above MAX_IDLE_CASH_PCT. A target above the limit would make the plan
    produce a portfolio its own rules reject.
    """
    alvo = float(ref["rebalance_policy"].set_index("param").loc["TARGET_CASH_PCT", "value"])
    assert alvo == 0
