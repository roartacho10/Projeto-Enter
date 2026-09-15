"""Real production false positives and counterexamples that must stay blocked."""
from pathlib import Path
import re

import pytest

import claims
import verify


@pytest.fixture
def gate(ran, monkeypatch):
    monkeypatch.setattr(claims, "OUT", ran / "output/ALBERT")
    monkeypatch.setattr(claims, "REF", ran / "data/reference")
    monkeypatch.setattr(verify, "OUT", ran / "output/ALBERT")
    return verify.verify


@pytest.mark.parametrize("text", [
    "MRFG3 excede os limites por produto.",
    "A concentração em MRFG3 excedeu o limite.",
    "Concentração em Riza Lotus (46,40% da RF), Brave I (31,00% da RF) e MRFG3 (32,58% da RV) excede os limites por produto.",
])
def test_exceeding_a_limit_is_not_a_price_decline(gate, text):
    issues = gate("Albert, " + text)
    assert not [i for i in issues if i[0] == "blocker"], issues


@pytest.mark.parametrize("verb", ["caiu", "cedeu", "recuou", "desvalorizou"])
def test_real_decline_claims_still_fail_for_a_rising_stock(gate, verb):
    issues = gate(f"Albert, MRFG3 {verb} no mês.")
    assert any(i[1] == "claims_direction" for i in issues), issues


def test_positive_direction_does_not_match_inside_negative_word():
    assert re.search(claims.DOWN, "desvalorizou")
    assert not re.search(claims.UP, "desvalorizou")
    assert not re.search(claims.DOWN, "excede")


def test_short_fund_names_own_their_figures_in_an_enumeration(gate):
    text = ("Albert, os principais destaques positivos foram LREN3 (+19,46%), MRFG3 (+18,94%) "
            "e AZZA3 (+29,96%), além de Constellation (+11,13%) e Truxt (+10,17%).")
    assert not [i for i in gate(text) if i[0] == "blocker"]


@pytest.mark.parametrize("text", [
    "Albert, Constellation (+10,17%) e Truxt (+11,13%) foram destaques.",
    "Albert, Constellation (+10,170%) e Truxt (+11,130%) foram destaques.",
])
def test_swapping_returns_between_short_fund_names_is_still_blocked(gate, text):
    issues = gate(text)
    assert len([i for i in issues if i[1] == "claims_attribution"]) == 2, issues


def test_instrument_names_do_not_match_inside_other_words(gate):
    issues = gate("Albert, a MRFG30 caiu no período.")
    assert not any(i[1] == "claims_direction" for i in issues)


def test_only_exactly_equivalent_number_formats_are_accepted(gate):
    good = gate("Albert, limite de caixa de 0,0% e teto de RV de 25,0%.")
    assert not any(i[0] == "blocker" for i in good), good
    bad = gate("Albert, limite de caixa de 0,001% e teto de RV de 25,001%.")
    assert len([i for i in bad if i[1] == "ungrounded_number"]) == 2, bad


def test_full_report_from_streamlit_passes_with_current_equity_base(gate):
    text = (Path(__file__).parent / "fixtures/streamlit_letter_2025_04.txt").read_text(encoding="utf-8")
    # Preserve the historical regression fixture; only update the retired base.
    text = text.replace("29,51% do investido, acima do teto", "24,15% do patrimônio total, abaixo do teto")
    issues = gate(text)
    assert not [i for i in issues if i[0] == "blocker"], issues
